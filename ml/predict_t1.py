"""Live T+1 prediction utilities for next-day momentum signals."""

from __future__ import annotations

import glob
import json
import os
import sys
from datetime import datetime
from typing import Any

import lightgbm as lgb
import numpy as np
import pandas as pd

from ml.config import MODEL_DIR
from ml.dataset_t1 import build_latest_t1_snapshot
from ml.features.sector import load_sector_mapping

T1_STRONG_BUY_PROB = 0.70
T1_BUY_PROB = 0.55
T1_WATCH_PROB = 0.50
T1_LONG_UPPER_WICK_CUTOFF = 0.04
T1_BREAKOUT_STRETCH_CUTOFF = 0.08
T1_VOLUME_BURST_MIN = 1.20
T1_INST_RATIO_MIN = 0.03
T1_SCORE_VOLUME_WEIGHT = 0.03
T1_SCORE_STRONG_CLOSE_WEIGHT = 0.03
T1_SCORE_BREAKOUT_WEIGHT = 0.02
T1_SCORE_INST_WEIGHT = 0.03
T1_SCORE_UPPER_WICK_PENALTY = 0.04

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

    buy_mask = pred_df["hit_prob_3pct"] >= max(T1_BUY_PROB, trade_rules["min_prob"])
    strong_mask = (
        (pred_df["hit_prob_3pct"] >= T1_STRONG_BUY_PROB)
        & (snapshot.get("t1_volume_burst_5d", pd.Series(0, index=snapshot.index)).fillna(0) >= T1_VOLUME_BURST_MIN)
        & (snapshot.get("t1_strong_close_flag", pd.Series(0, index=snapshot.index)).fillna(0) >= 1.0)
        & (snapshot.get("t1_upper_wick_pct", pd.Series(0, index=snapshot.index)).fillna(0) < T1_LONG_UPPER_WICK_CUTOFF)
    )
    weak_mask = pred_df["hit_prob_3pct"] < T1_WATCH_PROB

    pred_df.loc[buy_mask, "recommendation"] = "建議買進"
    pred_df.loc[strong_mask, "recommendation"] = "強力買進"
    pred_df.loc[weak_mask, "recommendation"] = "觀望（勝率不足）"

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

    pred_df = sort_t1_prediction_df(pred_df)

    eligible = pred_df[pred_df["hit_prob_3pct"] >= trade_rules["min_prob"]].copy()
    selected = eligible.head(trade_rules["top_n"]).copy()
    if not selected.empty:
        pred_df.loc[selected.index, "selected_for_trade"] = True
        pred_df.loc[selected.index, "selection_rank"] = pd.Series(
            range(1, len(selected) + 1),
            index=selected.index,
            dtype="Int64",
        )
        selected_watch = selected.index[
            pred_df.loc[selected.index, "recommendation"].astype(str).str.startswith("觀望")
        ]
        if len(selected_watch) > 0:
            pred_df.loc[selected_watch, "recommendation"] = "建議買進"

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
    _safe_print(
        "T+1 live signal summary: "
        f"{len(pred_df):,} names | "
        f"selected={selected_count} | "
        f"tp={tp_text} | "
        f"sl={sl_text}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
