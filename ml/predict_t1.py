"""Live T+1 prediction utilities for next-day momentum signals."""

from __future__ import annotations

import glob
import json
import os
import sqlite3
import sys
from datetime import datetime
from typing import Any

import lightgbm as lgb
import numpy as np
import pandas as pd

from ml.config import MODEL_DIR
from ml.dataset_t1 import build_latest_t1_snapshot
from ml.features.key_levels import compute_key_levels
from ml.features.sector import load_sector_mapping
from ml.t1_production_gate import evaluate_t1_production_gate

T1_LONG_UPPER_WICK_CUTOFF = 0.04
T1_BREAKOUT_STRETCH_CUTOFF = 0.08
T1_VOLUME_BURST_MIN = 1.20
T1_INST_RATIO_MIN = 0.03
T1_SCORE_VOLUME_WEIGHT = 0.03
T1_SCORE_STRONG_CLOSE_WEIGHT = 0.03
T1_SCORE_BREAKOUT_WEIGHT = 0.02
T1_SCORE_INST_WEIGHT = 0.03
T1_SCORE_UPPER_WICK_PENALTY = 0.04

# --- 乖離率追高警告 ---
T1_MA5_OVEREXTEND_PCT = 0.10    # MA5 正乖離 >10% → 短線過熱（P99 水位才10.4%）
T1_MA5_PULLBACK_PCT = 0.03      # MA5 乖離 <3% → 回測均線，動能進場點

# --- 實戰防護常數 ---
T1_MIN_AVG_AMOUNT = 10_000_000       # 5日均成交額門檻 (1000萬台幣)
T1_MARKET_CIRCUIT_BREAKER = -0.015   # 大盤跌 > 1.5% 熔斷，不出手
T1_MDD_HALT_PCT = 0.10              # 帳本 MDD > 10% 熔斷
T1_CONSECUTIVE_LOSS_HALT = 5         # 連續 5 日虧損熔斷
T1_PORTFOLIO_DB = os.path.join(os.path.dirname(os.path.dirname(__file__)), "paper_portfolio_t1.db")
T1_LEDGER_RESTART_DATE = "2026-04-17"  # 1D direction model clean start
VOLUME_SHARES_PER_LOT = 1000.0

T1_SNAPSHOT_CACHE_PATH = os.path.join(MODEL_DIR, "snapshot_t1_cache.pkl")

_SECTOR_DF = load_sector_mapping()
_SECTOR_LOOKUP: dict[str, str] = {}
if _SECTOR_DF is not None:
    _SECTOR_LOOKUP = dict(zip(_SECTOR_DF["Ticker"], _SECTOR_DF["Sector"]))


def _safe_print(text: str) -> None:
    try:
        print(text)
    except UnicodeEncodeError:
        encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
        safe_text = str(text).encode(encoding, errors="replace").decode(
            encoding,
            errors="replace",
        )
        print(safe_text)


def _snapshot_numeric(snapshot: pd.DataFrame, col: str) -> pd.Series:
    if col in snapshot.columns:
        return pd.to_numeric(snapshot[col], errors="coerce")
    return pd.Series(np.nan, index=snapshot.index, dtype="float64")


def _attach_liquidity_artifacts(pred_df: pd.DataFrame, snapshot: pd.DataFrame) -> pd.DataFrame:
    """Attach liquidity context for downstream gates.

    Volume artifacts are in board lots (1 lot = 1,000 shares). Amount artifacts
    are estimated TWD traded value.
    """
    out = pred_df.copy()
    close = pd.to_numeric(out["close"], errors="coerce")
    volume_today_shares = _snapshot_numeric(snapshot, "Volume")
    avg_5d_volume_shares = _snapshot_numeric(snapshot, "VOL_MA_5")
    avg_20d_volume_shares = _snapshot_numeric(snapshot, "VOL_MA_20")

    if avg_5d_volume_shares.isna().all() and "t1_volume_burst_5d" in snapshot.columns:
        vol_burst = _snapshot_numeric(snapshot, "t1_volume_burst_5d").fillna(1.0).clip(lower=0.01)
        avg_5d_volume_shares = volume_today_shares / vol_burst

    avg_5d_amount = _snapshot_numeric(snapshot, "AMOUNT_MA_5")
    avg_20d_amount = _snapshot_numeric(snapshot, "AMOUNT_MA_20")
    avg_5d_amount = avg_5d_amount.where(avg_5d_amount.notna(), avg_5d_volume_shares * close)
    avg_20d_amount = avg_20d_amount.where(avg_20d_amount.notna(), avg_20d_volume_shares * close)

    out["avg_5d_volume"] = (avg_5d_volume_shares / VOLUME_SHARES_PER_LOT).astype(np.float32)
    out["avg_20d_volume"] = (avg_20d_volume_shares / VOLUME_SHARES_PER_LOT).astype(np.float32)
    out["avg_5d_amount"] = avg_5d_amount.astype(np.float64)
    out["avg_20d_amount"] = avg_20d_amount.astype(np.float64)
    return out


def _check_portfolio_mdd_halt() -> tuple[bool, str]:
    """檢查帳本是否觸發 MDD 或連續虧損熔斷。"""
    if not os.path.exists(T1_PORTFOLIO_DB):
        return False, ""
    try:
        conn = sqlite3.connect(T1_PORTFOLIO_DB)
        df = pd.read_sql_query(
            "SELECT prediction_date, realized_return_pct "
            "FROM t1_positions WHERE status = 'closed' AND realized_return_pct IS NOT NULL "
            "AND prediction_date >= ? "
            "ORDER BY prediction_date",
            conn,
            params=(T1_LEDGER_RESTART_DATE,),
        )
        conn.close()
    except Exception:
        return False, ""

    if df.empty or len(df) < 3:
        return False, ""

    # 每日平均報酬
    daily = df.groupby("prediction_date")["realized_return_pct"].mean()

    # 連續虧損檢查
    last_n = daily.tail(T1_CONSECUTIVE_LOSS_HALT)
    if len(last_n) >= T1_CONSECUTIVE_LOSS_HALT and (last_n < 0).all():
        return True, f"連續{T1_CONSECUTIVE_LOSS_HALT}日虧損"

    # MDD 檢查
    equity = (1 + daily).cumprod()
    peak = equity.cummax()
    drawdown = (equity - peak) / peak
    mdd = drawdown.min()
    if mdd < -T1_MDD_HALT_PCT:
        return True, f"MDD={mdd:.1%}"

    return False, ""


def _find_latest_20d_prediction(pred_date: str) -> str | None:
    """Find the 20D prediction CSV matching or closest to pred_date."""
    pattern = os.path.join(MODEL_DIR, "predictions_*.csv")
    candidates = [f for f in sorted(glob.glob(pattern)) if "t1" not in os.path.basename(f)]
    if not candidates:
        return None
    # Prefer exact date match, fallback to latest
    for f in reversed(candidates):
        basename = os.path.basename(f)
        if pred_date in basename:
            return f
    return candidates[-1]


def load_latest_t1_model() -> tuple[lgb.Booster, dict[str, Any]]:
    """Load the latest saved T+1 classifier."""
    meta_files = sorted(glob.glob(os.path.join(MODEL_DIR, "lgbm_t1_*_meta.json")))
    if not meta_files:
        raise FileNotFoundError(
            "尚未找到 T+1 模型。請先執行 scripts/train_t1_backtest.py 建立第一版模型。"
        )

    meta_path = meta_files[-1]
    with open(meta_path, "r", encoding="utf-8") as fh:
        meta = json.load(fh)

    model = lgb.Booster(model_file=meta["model_file"])
    meta["meta_path"] = meta_path
    return model, meta


def _extract_trade_rules(meta: dict[str, Any]) -> dict[str, Any]:
    trade_rules = meta.get("trade_rules", {}) or {}
    selection = trade_rules.get("selection", {}) or {}

    stop_loss = trade_rules.get("stop_loss")
    if stop_loss in ("", 0):
        stop_loss = None

    return {
        "top_n": int(selection.get("top_n", 10) or 10),
        "min_prob": float(selection.get("min_prob", 0.60) or 0.60),
        "take_profit": float(tp) if (tp := trade_rules.get("take_profit")) else 0.05,
        "stop_loss": float(stop_loss) if stop_loss is not None else None,
        "friction": float(trade_rules.get("friction", 0.004) or 0.004),
        "ambiguous_fill": str(trade_rules.get("ambiguous_fill", "close")),
    }


# V3: 已改用 ranking-based selection，不再需要動態機率門檻


def _append_tag(
    tags: pd.Series,
    mask: pd.Series,
    text: str,
) -> pd.Series:
    if mask.any():
        tags.loc[mask] = tags.loc[mask] + text + " "
    return tags


def _build_t1_setup_tags(snapshot: pd.DataFrame) -> pd.Series:
    setup_tags = pd.Series("", index=snapshot.index, dtype=object)

    if "t1_volume_burst_5d" in snapshot.columns:
        setup_tags = _append_tag(
            setup_tags,
            snapshot["t1_volume_burst_5d"].fillna(0) >= T1_VOLUME_BURST_MIN,
            "爆量",
        )
    if "t1_strong_close_flag" in snapshot.columns:
        setup_tags = _append_tag(
            setup_tags,
            snapshot["t1_strong_close_flag"].fillna(0) >= 1.0,
            "強收",
        )
    if "t1_gap_above_prev_high" in snapshot.columns:
        setup_tags = _append_tag(
            setup_tags,
            snapshot["t1_gap_above_prev_high"].fillna(0) >= 1.0,
            "跳空",
        )
    if "t1_breakout_20d" in snapshot.columns:
        setup_tags = _append_tag(
            setup_tags,
            snapshot["t1_breakout_20d"].fillna(-1) > 0,
            "突破",
        )
    if "t1_inst_net_ratio_1d" in snapshot.columns:
        setup_tags = _append_tag(
            setup_tags,
            snapshot["t1_inst_net_ratio_1d"].fillna(0) >= T1_INST_RATIO_MIN,
            "法人偏多",
        )

    return setup_tags.str.strip()


def _build_t1_risk_tags(snapshot: pd.DataFrame) -> pd.Series:
    risk_tags = pd.Series("", index=snapshot.index, dtype=object)

    if "t1_long_upper_shadow_flag" in snapshot.columns:
        risk_tags = _append_tag(
            risk_tags,
            snapshot["t1_long_upper_shadow_flag"].fillna(0) >= 1.0,
            "長上影",
        )
    if "t1_upper_wick_pct" in snapshot.columns:
        risk_tags = _append_tag(
            risk_tags,
            snapshot["t1_upper_wick_pct"].fillna(0) >= T1_LONG_UPPER_WICK_CUTOFF,
            "追價風險",
        )
    if "t1_close_location" in snapshot.columns:
        risk_tags = _append_tag(
            risk_tags,
            snapshot["t1_close_location"].fillna(0.5) <= 0.35,
            "收盤偏弱",
        )
    if "t1_volume_burst_5d" in snapshot.columns:
        risk_tags = _append_tag(
            risk_tags,
            snapshot["t1_volume_burst_5d"].fillna(0) < 0.90,
            "量能不足",
        )
    if "t1_breakout_20d" in snapshot.columns:
        risk_tags = _append_tag(
            risk_tags,
            snapshot["t1_breakout_20d"].fillna(0) >= T1_BREAKOUT_STRETCH_CUTOFF,
            "突破過熱",
        )
    if "t1_inst_net_ratio_1d" in snapshot.columns:
        risk_tags = _append_tag(
            risk_tags,
            snapshot["t1_inst_net_ratio_1d"].fillna(0) <= -T1_INST_RATIO_MIN,
            "法人反向",
        )

    return risk_tags.str.strip()


def _compute_t1_score(snapshot: pd.DataFrame, hit_prob: pd.Series) -> pd.Series:
    score = pd.to_numeric(hit_prob, errors="coerce").fillna(0.0).astype(np.float32)

    if "t1_volume_burst_5d" in snapshot.columns:
        score = score + (
            snapshot["t1_volume_burst_5d"].fillna(1.0).clip(lower=1.0, upper=3.0) - 1.0
        ) * T1_SCORE_VOLUME_WEIGHT
    if "t1_strong_close_flag" in snapshot.columns:
        score = score + snapshot["t1_strong_close_flag"].fillna(0).clip(0, 1) * T1_SCORE_STRONG_CLOSE_WEIGHT
    if "t1_breakout_20d" in snapshot.columns:
        score = score + snapshot["t1_breakout_20d"].fillna(0).clip(lower=0, upper=0.10) * T1_SCORE_BREAKOUT_WEIGHT
    if "t1_inst_net_ratio_1d" in snapshot.columns:
        score = score + snapshot["t1_inst_net_ratio_1d"].fillna(0).clip(lower=0, upper=0.10) * T1_SCORE_INST_WEIGHT
    if "t1_upper_wick_pct" in snapshot.columns:
        score = score - snapshot["t1_upper_wick_pct"].fillna(0).clip(lower=0, upper=0.08) * T1_SCORE_UPPER_WICK_PENALTY

    return score.clip(lower=0.0, upper=1.2).astype(np.float32)


def _recommendation_rank(value: object) -> int:
    text = str(value or "")
    if text == "強力買進":
        return 2
    if text == "建議買進":
        return 1
    if text.startswith("觀望"):
        return 0
    if text == "建議賣出":
        return -1
    if text == "強力賣出":
        return -2
    return 0


def sort_t1_prediction_df(df: pd.DataFrame) -> pd.DataFrame:
    sorted_df = df.copy()
    sort_keys: list[str] = []
    ascending: list[bool] = []

    if "recommendation" in sorted_df.columns:
        sorted_df["_recommendation_rank"] = (
            sorted_df["recommendation"].map(_recommendation_rank).astype(np.int8)
        )
        sort_keys.append("_recommendation_rank")
        ascending.append(False)

    if "t1_score" in sorted_df.columns:
        sort_keys.append("t1_score")
        ascending.append(False)
    if "hit_prob_3pct" in sorted_df.columns:
        sort_keys.append("hit_prob_3pct")
        ascending.append(False)
    if "ticker" in sorted_df.columns:
        sort_keys.append("ticker")
        ascending.append(True)

    sorted_df = sorted_df.sort_values(sort_keys, ascending=ascending).reset_index(drop=True)
    return sorted_df.drop(columns=["_recommendation_rank"], errors="ignore")


def build_live_t1_prediction_df(
    snapshot: pd.DataFrame,
    model: lgb.Booster,
    meta: dict[str, Any],
) -> pd.DataFrame:
    feature_cols = meta["feature_columns"]
    X = pd.DataFrame(
        {
            col: snapshot[col].values if col in snapshot.columns else np.full(len(snapshot), np.nan)
            for col in feature_cols
        }
    )
    hit_prob = np.clip(model.predict(X.values), 1e-6, 1.0 - 1e-6)
    trade_rules = _extract_trade_rules(meta)

    pred_df = snapshot[["ticker", "Date", "Close"]].copy()
    pred_df["date"] = pred_df["Date"].dt.strftime("%Y-%m-%d")
    pred_df["close"] = pd.to_numeric(pred_df["Close"], errors="coerce")
    pred_df["hit_prob_3pct"] = hit_prob.astype(np.float32)
    pred_df["pred_label"] = (pred_df["hit_prob_3pct"] >= 0.5).astype(np.int8)
    pred_df["t1_score"] = _compute_t1_score(snapshot, pred_df["hit_prob_3pct"])
    pred_df["recommendation"] = "觀望"

    # V3 ranking-based: 推薦等級由截面排名決定，不依賴絕對機率門檻
    # （miscalibrated 模型的 prob 值無意義，只有相對排序可信）
    score_rank = pred_df["t1_score"].rank(ascending=False, method="min")
    total = len(pred_df)
    # Top 2% = 強力買進, Top 10% = 建議買進, 其餘觀望
    strong_mask = score_rank <= max(1, int(total * 0.02))
    buy_mask = (~strong_mask) & (score_rank <= max(5, int(total * 0.10)))

    pred_df.loc[buy_mask, "recommendation"] = "建議買進"
    pred_df.loc[strong_mask, "recommendation"] = "強力買進"

    pred_df["setup_tags"] = _build_t1_setup_tags(snapshot)
    pred_df["risk_tags"] = _build_t1_risk_tags(snapshot)
    pred_df["sector"] = pred_df["ticker"].map(_SECTOR_LOOKUP).fillna("其他")

    for col in [
        "t1_volume_burst_5d",
        "t1_inst_net_ratio_1d",
        "t1_breakout_20d",
        "t1_close_location",
        "t1_upper_wick_pct",
        "t1_strong_close_flag",
        "t1_long_upper_shadow_flag",
        "t1_gap_above_prev_high",
    ]:
        if col in snapshot.columns:
            pred_df[col] = pd.to_numeric(snapshot[col], errors="coerce")

    pred_df["take_profit"] = trade_rules["take_profit"]
    pred_df["stop_loss"] = trade_rules["stop_loss"]
    pred_df["friction"] = trade_rules["friction"]
    pred_df["selected_for_trade"] = False
    pred_df["selection_rank"] = pd.Series(pd.NA, index=pred_df.index, dtype="Int64")
    pred_df["position_weight"] = np.nan
    pred_df["veto_reason"] = ""
    production_gate = evaluate_t1_production_gate()
    gate_closed = bool(production_gate.get("closed"))
    gate_reason = str(production_gate.get("reason") or "")
    pred_df["production_gate_status"] = str(production_gate.get("status") or "UNKNOWN")
    pred_df["production_gate_reason"] = gate_reason

    # --- [防護0] MDD / 連續虧損熔斷 ---
    mdd_halted, mdd_reason = _check_portfolio_mdd_halt()

    # --- [防護1] 大盤熔斷 ---
    twii_ret = snapshot["twii_return_1d"].iloc[0] if "twii_return_1d" in snapshot.columns else 0.0
    market_halted = (twii_ret <= T1_MARKET_CIRCUIT_BREAKER) if pd.notna(twii_ret) else False

    # --- [防護2] 流動性 artifact：成交量用張，成交額用台幣估算 ---
    pred_df = _attach_liquidity_artifacts(pred_df, snapshot)

    # --- [防護3] 載入 20D 預測 ---
    d20_pred_file = _find_latest_20d_prediction(pred_df["date"].iloc[0] if not pred_df.empty else "")
    d20_map = None
    if d20_pred_file is not None:
        d20_df = pd.read_csv(d20_pred_file, dtype={"ticker": str}, usecols=["ticker", "signal", "pred_return_20d"])
        d20_map = d20_df.set_index("ticker")

    pred_df = sort_t1_prediction_df(pred_df)

    # sort 後 index 已重置，用 pred_df 欄位重建 mask
    liquidity_ok = pred_df["avg_5d_amount"] >= T1_MIN_AVG_AMOUNT

    d20_veto = pd.Series(False, index=pred_df.index)
    if d20_pred_file is not None:
        matched_signal = pred_df["ticker"].map(d20_map["signal"]).fillna("")
        matched_return = pred_df["ticker"].map(d20_map["pred_return_20d"]).fillna(0.0)
        d20_veto = (matched_signal == "DOWN") & (matched_return < 0)

    # 標記否決原因
    pred_df.loc[~liquidity_ok, "veto_reason"] = pred_df.loc[~liquidity_ok, "veto_reason"] + "流動性不足 "
    pred_df.loc[d20_veto, "veto_reason"] = pred_df.loc[d20_veto, "veto_reason"] + "20D看空 "

    if gate_closed:
        pred_df["veto_reason"] = f"T1_GATE_CLOSED({gate_reason}) " + pred_df["veto_reason"]
        pred_df["recommendation"] = "觀望"
    elif mdd_halted:
        # MDD 熔斷：全數不選
        pred_df["veto_reason"] = f"帳本熔斷({mdd_reason}) " + pred_df["veto_reason"]
    elif market_halted:
        # 大盤熔斷：全數不選
        pred_df["veto_reason"] = f"大盤熔斷({twii_ret:.2%}) " + pred_df["veto_reason"]
    else:
        # V3 ranking-based selection: 不依賴絕對機率門檻
        # 只用硬過濾（流動性 + 20D否決），再從過濾後取 Top N
        eligible_mask = liquidity_ok & (~d20_veto)
        eligible = pred_df[eligible_mask].copy()
        selected = eligible.head(trade_rules["top_n"]).copy()

        if not selected.empty:
            pred_df.loc[selected.index, "selected_for_trade"] = True
            pred_df.loc[selected.index, "selection_rank"] = pd.Series(
                range(1, len(selected) + 1),
                index=selected.index,
                dtype="Int64",
            )
            # 被選中但原本是觀望的，升級為建議買進
            selected_watch = selected.index[
                pred_df.loc[selected.index, "recommendation"].astype(str).str.startswith("觀望")
            ]
            if len(selected_watch) > 0:
                pred_df.loc[selected_watch, "recommendation"] = "建議買進"

            # --- [防護4] 波動度風險平價部位 (用 ticker 對應，避免 index 錯位) ---
            if "atr_pct" in snapshot.columns:
                atr_map = snapshot.set_index("ticker")["atr_pct"]
                sel_atr = pd.to_numeric(
                    selected["ticker"].map(atr_map), errors="coerce"
                ).fillna(0.03).clip(lower=0.005)
            else:
                sel_atr = pd.Series(0.03, index=selected.index)
            inv_atr = 1.0 / sel_atr
            weights = inv_atr / inv_atr.sum()
            pred_df.loc[selected.index, "position_weight"] = weights.values

    out_cols = [
        "ticker",
        "date",
        "close",
        "hit_prob_3pct",
        "pred_label",
        "t1_score",
        "recommendation",
        "setup_tags",
        "risk_tags",
        "sector",
        "selected_for_trade",
        "selection_rank",
        "take_profit",
        "stop_loss",
        "friction",
        "position_weight",
        "avg_5d_volume",
        "avg_20d_volume",
        "avg_5d_amount",
        "avg_20d_amount",
        "veto_reason",
        "production_gate_status",
        "production_gate_reason",
    ]
    out_cols.extend(
        col
        for col in [
            "t1_volume_burst_5d",
            "t1_inst_net_ratio_1d",
            "t1_breakout_20d",
            "t1_close_location",
            "t1_upper_wick_pct",
            "t1_strong_close_flag",
            "t1_long_upper_shadow_flag",
            "t1_gap_above_prev_high",
            "support_1",
            "support_1_src",
            "support_2",
            "support_2_src",
            "resistance_1",
            "resistance_2",
            "suggested_entry",
            "entry_discount_pct",
            "level_source",
            "support_strength",
            "entry_note",
            "trade_route",
            "ma5_bias",
        ]
        if col in pred_df.columns
    )
    return pred_df.loc[:, out_cols]


def predict_t1_all(save_csv: bool = True, verbose: bool = True) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Generate the latest T+1 predictions from the newest saved classifier."""
    model, meta = load_latest_t1_model()
    snapshot = build_latest_t1_snapshot(verbose=verbose)

    tmp_snapshot_path = T1_SNAPSHOT_CACHE_PATH + ".tmp"
    snapshot.to_pickle(tmp_snapshot_path)
    os.replace(tmp_snapshot_path, T1_SNAPSHOT_CACHE_PATH)

    pred_df = build_live_t1_prediction_df(snapshot=snapshot, model=model, meta=meta)
    pred_date = str(pred_df["date"].iloc[0]) if not pred_df.empty else datetime.now().strftime("%Y-%m-%d")

    # --- 關鍵價位：計算支撐/壓力/建議掛單價（selected + top 30）---
    selected_tickers = pred_df.loc[
        pred_df["selected_for_trade"].fillna(False).astype(bool), "ticker"
    ].tolist()
    # 一律對 top 30 都算（selected 可能不在 top 30 裡，用 union）
    top30_tickers = pred_df.head(30)["ticker"].tolist()
    selected_tickers = list(dict.fromkeys(selected_tickers + top30_tickers))  # 保序去重
    if selected_tickers:
        closes_map = dict(zip(pred_df["ticker"], pred_df["close"]))
        levels = compute_key_levels(selected_tickers, closes_map)
        level_cols = [
            "support_1", "support_1_src", "support_2", "support_2_src",
            "resistance_1", "resistance_2",
            "suggested_entry", "entry_discount_pct", "level_source",
            "support_strength", "entry_note",
        ]
        levels_indexed = levels.set_index("ticker")[level_cols]
        for col in level_cols:
            pred_df[col] = pred_df["ticker"].map(
                levels_indexed[col].to_dict()
            )
        if verbose:
            with_entry = levels["suggested_entry"].notna().sum()
            _safe_print(f"Key levels computed for {len(selected_tickers)} selected, {with_entry} with suggested entry")

    # --- MA5 乖離率（全量計算，不限 top 30）---
    ma5_bias_map = dict(zip(
        snapshot["ticker"].astype(str),
        pd.to_numeric(snapshot.get("price_vs_ma5"), errors="coerce").fillna(0),
    ))
    pred_df["ma5_bias"] = pred_df["ticker"].map(ma5_bias_map).fillna(0)

    # --- trade_route 分流（全量，不限 top 30）---
    entry = pd.to_numeric(pred_df.get("suggested_entry"), errors="coerce")
    has_entry = entry.notna()

    # A路線(動能進場): MA5 乖離率適中 → 均線附近可直接進
    # B路線(等待低接): 乖離過大需等回測，或有掛單價且折價空間大
    pred_df["trade_route"] = ""
    ma5_ok = pred_df["ma5_bias"] <= T1_MA5_OVEREXTEND_PCT  # 乖離不過大
    ma5_pullback = pred_df["ma5_bias"] <= T1_MA5_PULLBACK_PCT  # 貼近均線

    pred_df.loc[ma5_ok, "trade_route"] = "A_動能"
    pred_df.loc[ma5_pullback, "trade_route"] = "A_動能回測"

    # 有掛單價且折價空間大 → B路線等待低接
    discount = pd.to_numeric(pred_df.get("entry_discount_pct"), errors="coerce").fillna(0).abs()
    b_route = has_entry & (discount >= 0.05)  # 折價 ≥5%
    pred_df.loc[b_route, "trade_route"] = "B_等待低接"

    # --- 風險標籤：MA5 正乖離過大 → 短線過熱 ---
    if "risk_tags" in pred_df.columns:
        overextended = pred_df["ma5_bias"] > T1_MA5_OVEREXTEND_PCT
        if overextended.any():
            bias_str = pred_df.loc[overextended, "ma5_bias"].apply(
                lambda x: f"均線乖離+{x:.0%}"
            )
            pred_df.loc[overextended, "risk_tags"] = (
                pred_df.loc[overextended, "risk_tags"].fillna("") + " " + bias_str
            ).str.strip()
            # 過熱的股票不適合A路線動能，改為等待回測
            pred_df.loc[overextended, "trade_route"] = "B_等待回測"

    if save_csv:
        output_path = os.path.join(MODEL_DIR, f"predictions_t1_{pred_date}.csv")
        tmp_path = output_path + ".tmp"
        pred_df.to_csv(tmp_path, index=False, encoding="utf-8-sig")
        os.replace(tmp_path, output_path)
        meta["prediction_file"] = output_path
        if verbose:
            _safe_print(f"T+1 predictions saved to {output_path}")

    return pred_df, meta


def load_latest_t1_predictions_csv() -> pd.DataFrame | None:
    pred_files = sorted(glob.glob(os.path.join(MODEL_DIR, "predictions_t1_*.csv")))
    if not pred_files:
        return None
    df = pd.read_csv(pred_files[-1], dtype={"ticker": str})
    return sort_t1_prediction_df(df)


def main() -> int:
    try:
        pred_df, meta = predict_t1_all(save_csv=True, verbose=True)
    except FileNotFoundError as exc:
        _safe_print(f"SKIP: {exc}")
        return 0

    trade_rules = _extract_trade_rules(meta)
    selected_count = int(pred_df["selected_for_trade"].fillna(False).sum())
    tp_text = "off" if trade_rules["take_profit"] is None else f"{trade_rules['take_profit']:.1%}"
    sl_text = "off" if trade_rules["stop_loss"] is None else f"{trade_rules['stop_loss']:.1%}"

    # 統計各防護機制攔截數量
    veto_col = pred_df.get("veto_reason", pd.Series("", index=pred_df.index)).fillna("")
    liq_vetoed = int(veto_col.str.contains("流動性不足").sum())
    d20_vetoed = int(veto_col.str.contains("20D看空").sum())
    mkt_halted = int(veto_col.str.contains("大盤熔斷").sum()) > 0
    mdd_halted = int(veto_col.str.contains("帳本熔斷").sum()) > 0

    _safe_print(
        "T+1 live signal summary: "
        f"{len(pred_df):,} names | "
        f"selected={selected_count} | "
        f"tp={tp_text} | "
        f"sl={sl_text}"
    )
    _safe_print(
        f"  Filters: liquidity_vetoed={liq_vetoed} | d20_vetoed={d20_vetoed} | "
        f"market_halted={'YES' if mkt_halted else 'no'} | "
        f"mdd_halted={'YES' if mdd_halted else 'no'}"
    )
    if "production_gate_status" in pred_df.columns and not pred_df.empty:
        gate_status = str(pred_df["production_gate_status"].iloc[0])
        if gate_status == "CLOSED":
            gate_reason = str(pred_df.get("production_gate_reason", pd.Series([""])).iloc[0])
            _safe_print(f"  Production gate: CLOSED | {gate_reason}")
    if selected_count > 0:
        sel = pred_df[pred_df["selected_for_trade"] == True]
        weights = sel["position_weight"]
        if weights.notna().any():
            _safe_print(f"  Position weights: min={weights.min():.1%} max={weights.max():.1%} (risk-parity)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
