"""預測 Pipeline：載入模型 → 產生今日預測

支援 v1 (分類) 和 v2 (迴歸) 兩種模型格式。
v2 模型額外提供：三層次動能解析、買賣建議。
"""
import json
import glob
import os
import sys

import numpy as np
import pandas as pd
import lightgbm as lgb

from ml.config import MODEL_DIR, TARGET_CLASSES, FORWARD_DAYS
from ml.dataset import build_latest_snapshot, apply_snapshot_zscore
from ml.features.entry import ENTRY_INFO_COLS
from ml.features.sector import load_sector_mapping
from ml.model_selection import get_slot_label, resolve_base_meta_path

# === 處置股名單（每日更新）===
from ml.config import BASE_DIR as _ML_BASE_DIR
_DISPOSITION_PATH = os.path.join(_ML_BASE_DIR, "disposition_active.csv")


def _load_disposition_set() -> set:
    """載入目前處置中的股票代號集合"""
    from datetime import date
    try:
        if not os.path.exists(_DISPOSITION_PATH):
            return set()
        df = pd.read_csv(_DISPOSITION_PATH, dtype=str)
        if df.empty or "stock_id" not in df.columns:
            return set()
        today = date.today()
        if "period_end" in df.columns:
            df["period_end"] = pd.to_datetime(df["period_end"], errors="coerce").dt.date
            df = df[df["period_end"] >= today]
        return set(df["stock_id"].str.strip())
    except Exception:
        return set()

# === 產業對應表（用於組合防呆）===
_SECTOR_DF = load_sector_mapping()
_SECTOR_LOOKUP = {}
if _SECTOR_DF is not None:
    _SECTOR_LOOKUP = dict(zip(_SECTOR_DF["Ticker"], _SECTOR_DF["Sector"]))

# 單一產業佔 Top N 的上限比例（從 30% 收緊至 20%，實戰驗證電子類過度集中）
SECTOR_CAP_RATIO = 0.20
BUY_PROB_EDGE_MIN = 0.0
STRONG_BUY_PROB_EDGE_MIN = 0.05
RECOMMENDATION_OVERHEAT_THRESHOLD = 0.18
STRONG_BUY_MAX_INST_SELL_PCT = 20.0
TOP30_EXCLUDE_INST_SELL_PCT = 30.0
LEADERBOARD_SOFT_PROB_WEIGHT = 0.06
LEADERBOARD_TURNAROUND_WEIGHT = 0.12
LEADERBOARD_TURNAROUND_CAP_RATIO = 0.04
PROB_EDGE_CLIP_LOW = -0.15
PROB_EDGE_CLIP_HIGH = 0.30
RISK_ADJUST_BASE = 0.35
RISK_ADJUST_MIN_SCALE = 0.15
RISK_ADJUST_MAX_SCALE = 0.65


def _safe_print(text: str) -> None:
    """Avoid Windows console encoding crashes on symbols like warning icons."""
    try:
        print(text)
    except UnicodeEncodeError:
        encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
        safe_text = str(text).encode(encoding, errors="replace").decode(encoding, errors="replace")
        print(safe_text)


def _turnaround_recovery_score(snapshot: pd.DataFrame) -> pd.Series:
    """Score recovery signals for loss-making turnaround candidates."""
    idx = snapshot.index

    def _col(name: str, default: float = 0.0) -> pd.Series:
        if name not in snapshot.columns:
            return pd.Series(default, index=idx, dtype=np.float32)
        return snapshot[name].fillna(default)

    profit_recovery = (
        (_col("eps_qoq") > 0)
        | (_col("eps_momentum") > 0.05)
        | (_col("eps_yoy") > 0.10)
    )
    demand_recovery = (
        (_col("revenue_yoy_latest") > 0.15)
        | (_col("revenue_yoy_3m_avg") > 0.10)
        | (_col("revenue_yoy_momentum") > 0.03)
    )
    margin_recovery = (
        (_col("margin_trend") > 0.03)
        | ((_col("operating_margin_latest", -1.0) > -0.08) & (_col("gross_margin_latest") > 0.20))
    )

    return (
        profit_recovery.astype(np.int8)
        + demand_recovery.astype(np.int8)
        + margin_recovery.astype(np.int8)
    )


def _turnaround_recovery_mask(snapshot: pd.DataFrame) -> pd.Series:
    """Identify loss-making names that are already showing recovery signals."""
    return _turnaround_recovery_score(snapshot) >= 2


def _chip_support_score(snapshot: pd.DataFrame) -> pd.Series:
    """Score supportive accumulation signals that help turnarounds surface."""
    idx = snapshot.index
    score = pd.Series(0, index=idx, dtype=np.int8)

    if "chip_diverge_bull" in snapshot.columns:
        score = score + (snapshot["chip_diverge_bull"].fillna(0) >= 1.0).astype(np.int8)
    if "whale_pct_chg" in snapshot.columns:
        score = score + (snapshot["whale_pct_chg"].fillna(0) > 0).astype(np.int8)
    if "retail_pct_chg" in snapshot.columns:
        score = score + (snapshot["retail_pct_chg"].fillna(0) < 0).astype(np.int8)
    if "price_vs_inst_cost" in snapshot.columns:
        near_cost = snapshot["price_vs_inst_cost"].fillna(np.inf).between(0, 0.05)
        score = score + near_cost.astype(np.int8)
    if "inst_buy_ratio_20d" in snapshot.columns:
        score = score + (snapshot["inst_buy_ratio_20d"].fillna(0) > 0.03).astype(np.int8)
    if "inst_total_20d_norm" in snapshot.columns:
        score = score + (snapshot["inst_total_20d_norm"].fillna(0) > 0.05).astype(np.int8)

    return score


def _prediction_sort_column(df: pd.DataFrame) -> str:
    if "leaderboard_score" in df.columns:
        return "leaderboard_score"
    if "risk_adjusted_return" in df.columns:
        return "risk_adjusted_return"
    if "pred_return_20d" in df.columns:
        return "pred_return_20d"
    return "up_prob"


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


def _sort_prediction_df(df: pd.DataFrame) -> pd.DataFrame:
    sort_col = _prediction_sort_column(df)
    sorted_df = df.copy()
    sort_keys: list[str] = []
    ascending: list[bool] = []

    if "recommendation" in sorted_df.columns:
        sorted_df["_recommendation_rank"] = (
            sorted_df["recommendation"].map(_recommendation_rank).astype(np.int8)
        )
        sort_keys.append("_recommendation_rank")
        ascending.append(False)

    sort_keys.append(sort_col)
    ascending.append(False)

    if "pred_return_20d" in sorted_df.columns and sort_col != "pred_return_20d":
        sort_keys.append("pred_return_20d")
        ascending.append(False)
    if "ticker" in sorted_df.columns:
        sort_keys.append("ticker")
        ascending.append(True)

    sorted_df = sorted_df.sort_values(sort_keys, ascending=ascending).reset_index(drop=True)
    return sorted_df.drop(columns=["_recommendation_rank"], errors="ignore")


def _institutional_sell_pressure(df: pd.DataFrame) -> pd.Series:
    idx = df.index
    signed_ratio = pd.Series(np.nan, index=idx, dtype=np.float32)

    if {"inst_net_10d", "inst_vol_pct_10d"} <= set(df.columns):
        net = pd.to_numeric(df["inst_net_10d"], errors="coerce").fillna(0.0)
        vol_pct = pd.to_numeric(df["inst_vol_pct_10d"], errors="coerce").abs() / 100.0
        signed_ratio = np.sign(net).astype(np.float32) * vol_pct.astype(np.float32)
    elif "inst_buy_ratio_20d" in df.columns:
        signed_ratio = pd.to_numeric(df["inst_buy_ratio_20d"], errors="coerce").astype(np.float32)

    return signed_ratio


# === 特徵 → 三層次動能解析映射 ===
# Layer 1: 籌碼與波動引擎 — 技術面 + 籌碼面（模型主要驅動力）
# Layer 2: 總經與大盤環境 — 系統性風險評估
# Layer 3: 基本面防禦網 — 下檔風險檢驗（排雷用）
_L1 = "籌碼與波動引擎"
_L2 = "總經與大盤環境"
_L3 = "基本面防禦網"

_DIMENSION_MAP = {
    # === Layer 1: 籌碼與波動引擎 ===
    # 波動率
    "atr_pct": _L1, "atr_pct_rank": _L1, "atr_14": _L1,
    "bb_width": _L1, "bb_position": _L1,
    "volatility_5d": _L1, "volatility_20d": _L1,
    "vol_contraction": _L1, "vol_contraction_ratio": _L1,
    "high_low_range": _L1, "keltner_pos": _L1, "squeeze": _L1,
    # 成交量
    "vol_ratio_5_20": _L1, "vol_zscore": _L1,
    "obv_slope_20": _L1, "cmf_20": _L1,
    "volume_surprise": _L1, "turnover_rate": _L1,
    "gap_pct": _L1, "gap_freq_10d": _L1,
    # 動量
    "return_1d": _L1, "return_3d": _L1, "return_5d": _L1,
    "return_10d": _L1, "return_20d": _L1, "return_60d": _L1,
    "momentum_accel": _L1, "mom_20d": _L1,
    "roc_5": _L1, "roc_10": _L1, "roc_20": _L1,
    # 趨勢
    "ma_slope_5": _L1, "ma_slope_20": _L1,
    "trend_direction": _L1, "lr_slope_20": _L1, "lr_r2_20": _L1,
    "adx_14": _L1, "di_diff": _L1,
    # 均線
    "price_vs_ma5": _L1, "price_vs_ma10": _L1,
    "price_vs_ma20": _L1, "price_vs_ma60": _L1,
    "ma_bullish_align": _L1, "ma_golden_cross": _L1, "ma_death_cross": _L1,
    "ma5_bounce": _L1, "ma20_bounce": _L1,
    "ma5_support_test": _L1, "ma20_support_test": _L1,
    "macd_turn_positive": _L1, "kd_golden_cross": _L1, "rsi_oversold_bounce": _L1,
    # 技術指標
    "rsi_6": _L1, "rsi_14": _L1, "macd_hist": _L1,
    "kd_k": _L1, "kd_d": _L1,
    "williams_r_14": _L1, "cci_14": _L1, "cci_20": _L1,
    "mfi_14": _L1, "stoch_rsi_k": _L1, "stoch_rsi_d": _L1,
    "psy_12": _L1, "psy_24": _L1, "ultimate_osc": _L1,
    "ichimoku_cloud_pos": _L1, "ichimoku_tk_diff": _L1, "ichimoku_cloud_width": _L1,
    "aroon_up": _L1, "aroon_down": _L1, "aroon_osc": _L1,
    "trix": _L1,
    "elder_bull": _L1, "elder_bear": _L1, "force_index_13": _L1,
    # 價格型態
    "dist_to_high_20d": _L1, "dist_to_low_20d": _L1,
    "dist_to_high_60d": _L1, "dist_to_low_60d": _L1,
    "dist_from_20d_high": _L1, "dist_from_20d_low": _L1,
    "position_52w": _L1, "up_day_ratio_20": _L1,
    "up_streak": _L1, "down_streak": _L1, "consecutive_days": _L1,
    # K線型態
    "body_ratio": _L1, "upper_shadow_ratio": _L1, "lower_shadow_ratio": _L1,
    "bullish_engulf": _L1, "bearish_engulf": _L1,
    "doji": _L1, "hammer": _L1, "hanging_man": _L1,
    "shooting_star": _L1, "morning_star": _L1, "evening_star": _L1,
    "three_white_soldiers": _L1, "three_black_crows": _L1,
    "upper_wick_ratio": _L1, "lower_wick_ratio": _L1, "upper_wick_5d_avg": _L1,
    "donchian_pos": _L1, "donchian_breakout_up": _L1, "donchian_breakout_dn": _L1,
    # 背離
    "price_vol_diverge": _L1, "price_vol_divergence": _L1,
    "macd_bearish_div": _L1, "macd_bullish_div": _L1,
    "rsi_bearish_div": _L1, "rsi_bullish_div": _L1,
    # 進場因子
    "entry_score": _L1, "phase": _L1, "price_vs_poc_20d": _L1,
    # 法人籌碼
    "foreign_cumsum_1d": _L1, "foreign_cumsum_3d": _L1,
    "foreign_cumsum_5d": _L1, "foreign_cumsum_10d": _L1, "foreign_cumsum_20d": _L1,
    "trust_cumsum_1d": _L1, "trust_cumsum_3d": _L1,
    "trust_cumsum_5d": _L1, "trust_cumsum_10d": _L1, "trust_cumsum_20d": _L1,
    "dealer_cumsum_1d": _L1, "dealer_cumsum_3d": _L1,
    "dealer_cumsum_5d": _L1, "dealer_cumsum_10d": _L1,
    "inst_total_5d": _L1, "inst_total_10d": _L1, "inst_total_20d": _L1,
    "foreign_trust_sync": _L1,
    "chip_diverge_bear": _L1, "chip_diverge_bull": _L1,
    "margin_change_5d": _L1, "margin_change_10d": _L1,
    "short_change_5d": _L1, "short_change_10d": _L1,
    "margin_short_ratio": _L1,
    "foreign_reversal_buy": _L1, "foreign_buy_streak": _L1,
    "trust_reversal_buy": _L1, "trust_buy_streak": _L1,
    "foreign_cumsum_1d_norm": _L1, "foreign_cumsum_3d_norm": _L1,
    "foreign_cumsum_5d_norm": _L1, "foreign_cumsum_10d_norm": _L1, "foreign_cumsum_20d_norm": _L1,
    "trust_cumsum_1d_norm": _L1, "trust_cumsum_3d_norm": _L1,
    "trust_cumsum_5d_norm": _L1, "trust_cumsum_10d_norm": _L1, "trust_cumsum_20d_norm": _L1,
    "dealer_cumsum_1d_norm": _L1, "dealer_cumsum_3d_norm": _L1,
    "dealer_cumsum_5d_norm": _L1, "dealer_cumsum_10d_norm": _L1,
    "inst_total_5d_norm": _L1, "inst_total_10d_norm": _L1, "inst_total_20d_norm": _L1,
    "whale_pct": _L1, "whale_pct_chg": _L1,
    "retail_pct": _L1, "retail_pct_chg": _L1,
    "holders_chg_pct": _L1, "whale_retail_ratio": _L1, "whale_trend_4w": _L1,
    "price_vs_inst_cost": _L1, "inst_accumulation": _L1, "inst_buy_ratio_20d": _L1,
    # === Layer 2: 總經與大盤環境 ===
    "twii_return_5d": _L2, "twii_return_20d": _L2,
    "vix_percentile_60d": _L2, "vix_change_5d": _L2, "vix_ma20_ratio": _L2,
    "sox_return_5d": _L2, "usdtwd_change_5d": _L2,
    "sector_return_rank": _L2, "sector_avg_return_5d": _L2,
    "sector_avg_return_20d": _L2, "sector_relative_return_5d": _L2,
    "sector_relative_return_20d": _L2, "sector_momentum_5d": _L2,
    "sector_momentum_20d": _L2, "sector_breadth": _L2, "sector_id": _L2,
    "ann_count_7d": _L2, "ann_count_30d": _L2,
    "ann_surprise": _L2, "has_ann_30d": _L2,
    # === Layer 3: 基本面防禦網 ===
    "revenue_yoy_latest": _L3, "revenue_yoy_3m_avg": _L3,
    "revenue_yoy_momentum": _L3, "revenue_cumulative_yoy": _L3,
    "gross_margin_latest": _L3, "operating_margin_latest": _L3,
    "net_margin_latest": _L3, "margin_trend": _L3,
    "gross_margin_trend": _L3, "revenue_qoq": _L3, "revenue_yoy_q": _L3,
    "margin_spread": _L3, "tax_effect": _L3, "margin_yoy_change": _L3,
    "revenue_log_scale": _L3,
    "pe_ratio": _L3, "pb_ratio": _L3, "dividend_yield": _L3,
    "pe_percentile_60d": _L3, "pb_change_20d": _L3,
    "eps_basic": _L3, "eps_ttm": _L3, "eps_yoy": _L3,
    "eps_qoq": _L3, "eps_momentum": _L3,
    "debt_ratio": _L3, "current_ratio": _L3,
    "roe_annualized": _L3, "roa_annualized": _L3,
    "book_value_per_share": _L3, "equity_ratio": _L3,
    "debt_ratio_trend": _L3, "roe_trend": _L3,
}


def load_selected_model(slot: str = "production", meta_path: str | None = None):
    """載入指定 slot 的 base 分類模型。"""
    resolved_meta_path = resolve_base_meta_path(slot=slot, explicit_meta_path=meta_path)
    if not resolved_meta_path:
        raise FileNotFoundError(f"找不到 slot={slot} 的 base model metadata")

    with open(resolved_meta_path, "r", encoding="utf-8") as f:
        meta = json.load(f)

    meta["meta_path"] = resolved_meta_path
    meta["model_slot"] = slot
    meta["model_label"] = get_slot_label(slot)

    model = lgb.Booster(model_file=meta["model_file"])
    return model, meta


def load_latest_model():
    """相容舊呼叫點，實際上載入 production slot。"""
    return load_selected_model(slot="production")


def _model_needs_zscore(meta: dict) -> bool:
    """判斷模型是否需要 z-score 輸入（V3+ 訓練自帶截面標準化）。"""
    # 明確標記
    if meta.get("cross_sectional_zscore"):
        return True
    # V2 excess return 模型使用 build_dataset() 訓練，自動帶 z-score
    version = meta.get("model_version", "")
    if "excess" in version:
        return True
    return False


def load_v2_model():
    """載入 v2 迴歸模型 (如果存在)"""
    v2_files = sorted(glob.glob(os.path.join(MODEL_DIR, "lgbm_v2_*_meta.json")))
    if not v2_files:
        return None, None

    meta_path = v2_files[-1]
    with open(meta_path, "r", encoding="utf-8") as f:
        meta = json.load(f)

    model = lgb.Booster(model_file=meta["model_file"])
    return model, meta


SNAPSHOT_CACHE_PATH = os.path.join(MODEL_DIR, "snapshot_cache.pkl")


def _compute_dimension_scores(model, X, feature_cols):
    """用 SHAP-like leaf prediction 計算三層次動能分數

    用 pred_contrib=True 取得每個特徵的貢獻度，
    再按三層次加總。
    """
    contribs = model.predict(X, pred_contrib=True)

    dim_names = [_L1, _L2, _L3]
    dim_scores = {d: np.zeros(len(X)) for d in dim_names}

    for i, col in enumerate(feature_cols):
        dim = _DIMENSION_MAP.get(col, _L1)
        dim_scores[dim] += contribs[:, i]

    return dim_scores


def _apply_recommendation_rules(pred_df: pd.DataFrame, snapshot: pd.DataFrame) -> pd.DataFrame:
    pred_df = pred_df.copy()
    pred_df["prob_edge"] = pred_df["up_prob"] - pred_df["down_prob"]
    if "risk_tags" in pred_df.columns:
        pred_df["risk_tags"] = pred_df["risk_tags"].fillna("").astype(str)
    else:
        pred_df["risk_tags"] = ""

    pct_rank = pred_df["pred_return_20d"].rank(pct=True)
    pred_df["recommendation"] = pd.cut(
        pct_rank,
        bins=[0, 0.10, 0.30, 0.70, 0.90, 1.0],
        labels=["強力賣出", "建議賣出", "觀望", "建議買進", "強力買進"],
        include_lowest=True,
    ).astype(str)

    if "atr_pct" in snapshot.columns:
        atr = snapshot["atr_pct"].fillna(0.05)
        atr_cap = (atr * 10).clip(lower=0.10)
        over_cap = pred_df["pred_return_20d"].abs() > atr_cap
        if over_cap.any():
            sign = pred_df.loc[over_cap, "pred_return_20d"].apply(lambda x: 1 if x > 0 else -1)
            pred_df.loc[over_cap, "pred_return_20d"] = sign * atr_cap[over_cap]
            pred_df.loc[over_cap, "risk_tags"] += "⚠️預測報酬超出波動上限 "

    positive_return = pred_df["pred_return_20d"] > 0
    pred_df.loc[positive_return, "recommendation"] = "建議買進"
    non_positive_buy = (~positive_return) & pred_df["recommendation"].isin(["強力買進", "建議買進"])
    pred_df.loc[non_positive_buy, "recommendation"] = "觀望"

    overbought = pd.Series(False, index=pred_df.index)
    oversold = pd.Series(False, index=pred_df.index)
    price_ok = pd.Series(True, index=pred_df.index)
    if "price_vs_ma20" in snapshot.columns:
        overbought = snapshot["price_vs_ma20"] > RECOMMENDATION_OVERHEAT_THRESHOLD
        oversold = snapshot["price_vs_ma20"] < -RECOMMENDATION_OVERHEAT_THRESHOLD
        price_ok = snapshot["price_vs_ma20"].fillna(np.inf) <= RECOMMENDATION_OVERHEAT_THRESHOLD
        pred_df.loc[overbought, "risk_tags"] += "⚠️短線偏熱，追價風險高 "
        pred_df.loc[oversold, "risk_tags"] += "⚠️乖離過大(超跌) "

    inst_sell_ratio = _institutional_sell_pressure(pred_df)
    heavy_sell = inst_sell_ratio <= -(STRONG_BUY_MAX_INST_SELL_PCT / 100.0)
    top30_excluded = inst_sell_ratio <= -(TOP30_EXCLUDE_INST_SELL_PCT / 100.0)
    inst_ok = inst_sell_ratio.fillna(0.0) > -(STRONG_BUY_MAX_INST_SELL_PCT / 100.0)
    pred_df.loc[heavy_sell, "risk_tags"] += (
        f"⚠️法人10日賣壓占量偏高（>{STRONG_BUY_MAX_INST_SELL_PCT:.0f}%） "
    )
    pred_df.loc[top30_excluded, "risk_tags"] += (
        f"⚠️法人10日賣壓占量過高（>{TOP30_EXCLUDE_INST_SELL_PCT:.0f}%），不列入Top30 "
    )

    low_vol = pd.Series(False, index=pred_df.index)
    if "VOL_MA_5" in snapshot.columns:
        low_vol = low_vol | (snapshot["VOL_MA_5"] < 500_000)
    if "Volume" in snapshot.columns:
        low_vol = low_vol | (snapshot["Volume"] < 500_000)
    pred_df.loc[low_vol, "risk_tags"] += "⚠️流動性不足 "

    crash = pd.Series(False, index=pred_df.index)
    if "return_20d" in snapshot.columns:
        crash = snapshot["return_20d"] < -0.40
        pred_df.loc[crash, "risk_tags"] += "⚠️異常暴跌 "

    extreme_pred = pred_df["pred_return_20d"].abs() > 0.30
    pred_df.loc[extreme_pred, "risk_tags"] += "⚠️預測值異常 "

    turnaround = _turnaround_recovery_mask(snapshot)
    op_loss = pd.Series(False, index=pred_df.index)
    if "operating_margin_latest" in snapshot.columns:
        op_loss = snapshot["operating_margin_latest"] < 0
        pred_df.loc[op_loss & (~turnaround), "risk_tags"] += "⚠️本業虧損 "
        pred_df.loc[op_loss & turnaround, "risk_tags"] += "⚠️本業仍虧損但營運修復中 "

    chip_bear = pd.Series(False, index=pred_df.index)
    if "chip_diverge_bear" in snapshot.columns:
        chip_bear = snapshot["chip_diverge_bear"] >= 1.0
        pred_df.loc[chip_bear, "risk_tags"] += "⚠️籌碼頂部背離 "

    chip_bull = pd.Series(False, index=pred_df.index)
    if "chip_diverge_bull" in snapshot.columns:
        chip_bull = snapshot["chip_diverge_bull"] >= 1.0
        pred_df.loc[chip_bull, "risk_tags"] += "💡底部吸籌訊號 "

    high_vol_gamble = positive_return & (pred_df["prob_edge"] <= BUY_PROB_EDGE_MIN)
    pred_df.loc[high_vol_gamble, "risk_tags"] += "⚠️期望值高但勝率偏低，屬高波動博弈 "

    weak_edge = positive_return & (pred_df["prob_edge"] > BUY_PROB_EDGE_MIN) & (
        pred_df["prob_edge"] < STRONG_BUY_PROB_EDGE_MIN
    )
    pred_df.loc[weak_edge, "risk_tags"] += "⚠️勝率優勢未達強力買進門檻 "

    strong_buy = positive_return & (pred_df["prob_edge"] >= STRONG_BUY_PROB_EDGE_MIN)
    strong_buy = strong_buy & price_ok & inst_ok & (~low_vol) & (~crash) & (~chip_bear) & (~op_loss)
    pred_df.loc[strong_buy, "recommendation"] = "強力買進"

    # 處置股硬擋（最優先）：改分盤交易，流動性極差
    disposition_set = _load_disposition_set()
    if disposition_set and "ticker" in pred_df.columns:
        is_disposition = pred_df["ticker"].isin(disposition_set)
        pred_df.loc[is_disposition, "risk_tags"] += "⚠️處置股（分盤交易）"
    else:
        is_disposition = pd.Series(False, index=pred_df.index)

    buy_labels = ["強力買進", "建議買進"]
    pred_df.loc[is_disposition, "recommendation"] = "觀望（處置股）"
    pred_df.loc[low_vol & pred_df["recommendation"].isin(buy_labels), "recommendation"] = "觀望（流動性不足）"
    pred_df.loc[crash & pred_df["recommendation"].isin(buy_labels), "recommendation"] = "觀望（異常暴跌）"
    pred_df.loc[extreme_pred & pred_df["recommendation"].isin(buy_labels), "recommendation"] = "觀望（預測值異常）"
    pred_df.loc[chip_bear & pred_df["recommendation"].isin(buy_labels), "recommendation"] = "觀望（籌碼頂部背離）"
    pred_df.loc[(op_loss & (~turnaround)) & pred_df["recommendation"].isin(buy_labels), "recommendation"] = "觀望（基本面警示）"

    confidence_scale = (
        RISK_ADJUST_BASE
        + pred_df["prob_edge"].clip(lower=PROB_EDGE_CLIP_LOW, upper=PROB_EDGE_CLIP_HIGH)
    ).clip(lower=RISK_ADJUST_MIN_SCALE, upper=RISK_ADJUST_MAX_SCALE)
    pred_df["risk_adjusted_return"] = pred_df["pred_return_20d"] * confidence_scale
    turnaround_score = _turnaround_recovery_score(snapshot).astype(np.float32)
    chip_support_score = _chip_support_score(snapshot).astype(np.float32)
    soft_prob_boost = (
        pred_df["pred_return_20d"].clip(lower=0)
        * (pred_df["up_prob"].clip(lower=0) + 0.5 * pred_df["flat_prob"].clip(lower=0))
        * LEADERBOARD_SOFT_PROB_WEIGHT
    )
    turnaround_raw = (
        pred_df["pred_return_20d"].clip(lower=0)
        * (turnaround_score / 3.0)
        * (chip_support_score / 6.0)
        * LEADERBOARD_TURNAROUND_WEIGHT
    )
    turnaround_boost = turnaround_raw.clip(
        upper=pred_df["pred_return_20d"].clip(lower=0) * LEADERBOARD_TURNAROUND_CAP_RATIO
    )
    pred_df["leaderboard_score"] = (
        pred_df["risk_adjusted_return"].fillna(0)
        + soft_prob_boost
        + turnaround_boost
    )
    pred_df["recommendation_rank"] = pred_df["recommendation"].map(_recommendation_rank).astype(np.int8)
    pred_df["risk_tags"] = pred_df["risk_tags"].str.strip()
    return pred_df


def predict_all(
    top_n: int = 30,
    model_slot: str = "production",
    model_meta_path: str | None = None,
    save_snapshot: bool = True,
):
    """對所有股票產生預測，回傳排名。"""
    model, meta = load_selected_model(slot=model_slot, meta_path=model_meta_path)
    feature_cols = meta["feature_columns"]

    # 嘗試載入 v2 迴歸模型
    v2_model, v2_meta = load_v2_model()

    snapshot = build_latest_snapshot(verbose=False)

    if save_snapshot:
        tmp_path = SNAPSHOT_CACHE_PATH + ".tmp"
        snapshot.to_pickle(tmp_path)
        os.replace(tmp_path, SNAPSHOT_CACHE_PATH)
        print(f"Snapshot 已快取: {SNAPSHOT_CACHE_PATH} ({len(snapshot):,} 筆)")

    # V3+ 模型需要 z-score，保留 raw snapshot 給舊模型用
    snapshot_zs = None  # lazy: 只在需要時計算

    def _get_zscore_snapshot():
        nonlocal snapshot_zs
        if snapshot_zs is None:
            snapshot_zs = apply_snapshot_zscore(snapshot)
        return snapshot_zs

    # --- base 分類模型預測 ---
    v1_snap = _get_zscore_snapshot() if _model_needs_zscore(meta) else snapshot
    X_v1 = pd.DataFrame(
        {
            col: v1_snap[col].values if col in v1_snap.columns else np.full(len(v1_snap), np.nan)
            for col in feature_cols
        }
    )
    proba = model.predict(X_v1.values)

    keep_cols = ["ticker", "Date", "Close"]
    extra_cols = ENTRY_INFO_COLS + [
        "entry_score",
        "phase",
        "dist_to_high_20d",
        "dist_to_high_60d",
        "price_vs_ma20",
        "price_vs_ma60",
        "position_52w",
    ]
    for col in extra_cols:
        if col in snapshot.columns:
            keep_cols.append(col)

    pred_df = snapshot[keep_cols].copy()
    pred_df["date"] = pred_df["Date"].dt.strftime("%Y-%m-%d")
    pred_df["close"] = pred_df["Close"]
    pred_df["up_prob"] = proba[:, 2]
    pred_df["flat_prob"] = proba[:, 1]
    pred_df["down_prob"] = proba[:, 0]
    pred_df["signal"] = [TARGET_CLASSES[int(i)] for i in np.argmax(proba, axis=1)]
    pred_df["model_slot"] = meta.get("model_slot", model_slot)
    pred_df["model_label"] = meta.get("model_label", get_slot_label(model_slot))
    pred_df["base_model_file"] = os.path.basename(meta["model_file"])
    pred_df["base_model_trained_at"] = meta.get("trained_at")

    # --- v2 迴歸模型 (如果有) ---
    if v2_model is not None and v2_meta is not None:
        v2_cols = v2_meta["feature_columns"]
        v2_snap = _get_zscore_snapshot() if _model_needs_zscore(v2_meta) else snapshot
        X_v2 = pd.DataFrame(
            {
                col: v2_snap[col].values if col in v2_snap.columns else np.full(len(v2_snap), np.nan)
                for col in v2_cols
            }
        )
        pred_df["pred_return_20d"] = v2_model.predict(X_v2.values)

        dim_scores = _compute_dimension_scores(v2_model, X_v2.values, v2_cols)
        for dim_name, scores in dim_scores.items():
            pred_df[f"dim_{dim_name}"] = scores

        pred_df = _apply_recommendation_rules(pred_df, snapshot)

        _tracking_path = os.path.join(MODEL_DIR, "..", "reports", "prediction_tracking.json")
        try:
            if os.path.exists(_tracking_path):
                with open(_tracking_path, "r", encoding="utf-8") as _f:
                    _tracking = json.load(_f)
                _rec_hist = _tracking.get("summary", {}).get("v2", {}).get(
                    "recommendation_historical", {}
                )
                if _rec_hist:
                    pred_df["historical_win_rate"] = pred_df["recommendation"].map(
                        lambda r: _rec_hist.get(str(r), {}).get("historical_win_rate")
                    )
                    pred_df["historical_avg_return"] = pred_df["recommendation"].map(
                        lambda r: _rec_hist.get(str(r), {}).get("avg_actual_return")
                    )
        except Exception:
            pass

        pred_df["market_return_median"] = pred_df["pred_return_20d"].median()
        pred_df["market_return_q25"] = pred_df["pred_return_20d"].quantile(0.25)
        pred_df["market_return_q75"] = pred_df["pred_return_20d"].quantile(0.75)
        med = pred_df["pred_return_20d"].median()
        if med < -0.03:
            pred_df["market_sentiment"] = "空頭"
        elif med < 0:
            pred_df["market_sentiment"] = "偏空"
        elif med < 0.03:
            pred_df["market_sentiment"] = "偏多"
        else:
            pred_df["market_sentiment"] = "多頭"

    pred_df["sector"] = pred_df["ticker"].map(_SECTOR_LOOKUP).fillna("其他")

    out_cols = ["ticker", "date", "close", "up_prob", "flat_prob", "down_prob", "signal"]
    for col in extra_cols:
        if col in pred_df.columns:
            out_cols.append(col)
    for col in [
        "model_slot",
        "model_label",
        "base_model_file",
        "base_model_trained_at",
        "pred_return_20d",
        "prob_edge",
        "risk_adjusted_return",
        "leaderboard_score",
        "recommendation",
        "risk_tags",
        "sector",
        f"dim_{_L1}",
        f"dim_{_L2}",
        f"dim_{_L3}",
        "historical_win_rate",
        "historical_avg_return",
        "market_return_median",
        "market_return_q25",
        "market_return_q75",
        "market_sentiment",
    ]:
        if col in pred_df.columns:
            out_cols.append(col)

    pred_df = pred_df[out_cols]
    pred_df = _sort_prediction_df(pred_df)
    return pred_df, meta


def apply_sector_cap(df: pd.DataFrame, top_n: int = 30,
                     cap_ratio: float = SECTOR_CAP_RATIO) -> pd.DataFrame:
    """產業集中度上限：單一產業不超過 cap_ratio 比例。

    演算法：
    1. 依榜單排序分數取 Top N
    2. 若某產業超過上限，從該產業排名最低的開始移除
    3. 由下一個不超限的股票遞補
    4. 重複直到所有產業都在上限內

    回傳：套用產業上限後的 Top N DataFrame
    """
    if "sector" not in df.columns:
        return df.head(top_n)

    max_per_sector = max(1, int(top_n * cap_ratio))
    sort_col = _prediction_sort_column(df)
    if sort_col not in df.columns:
        return df.head(top_n)
    sorted_df = _sort_prediction_df(df)

    # 只從買進池中選取，並排除相對法人賣壓過高的標的
    buy_recs = ["強力買進", "建議買進"]
    eligible = sorted_df[sorted_df["recommendation"].isin(buy_recs)].copy()
    inst_sell_ratio = _institutional_sell_pressure(eligible)
    eligible = eligible[
        inst_sell_ratio.fillna(0.0) > -(TOP30_EXCLUDE_INST_SELL_PCT / 100.0)
    ].copy()

    selected = []
    sector_count = {}
    replaced_sectors = {}  # 記錄被替換的產業 → 數量

    for _, row in eligible.iterrows():
        sector = row["sector"]
        current = sector_count.get(sector, 0)
        if current < max_per_sector:
            selected.append(row)
            sector_count[sector] = current + 1
        else:
            replaced_sectors[sector] = replaced_sectors.get(sector, 0) + 1
        if len(selected) >= top_n:
            break

    result = pd.DataFrame(selected)

    # 標記被產業上限遞補的股票
    if replaced_sectors:
        replaced_info = ", ".join(f"{k}(-{v})" for k, v in replaced_sectors.items())
        print(f"  產業集中度調整: {replaced_info}")

    return result.reset_index(drop=True)


def run_prediction(
    top_n: int = 30,
    model_slot: str = "production",
    model_meta_path: str | None = None,
    output_prefix: str = "predictions",
    output_path: str | None = None,
    save_snapshot: bool = True,
):
    """執行預測並印出結果。"""
    print("=" * 60)
    print(f"  台股預測（v1 分類 + v2 迴歸）")
    print("=" * 60)

    pred_df, meta = predict_all(
        top_n=top_n,
        model_slot=model_slot,
        model_meta_path=model_meta_path,
        save_snapshot=save_snapshot,
    )

    print(f"\nbase 模型: {os.path.basename(meta['model_file'])}")
    print(f"slot: {meta.get('model_slot', model_slot)} ({meta.get('model_label', get_slot_label(model_slot))})")
    has_v2 = "pred_return_20d" in pred_df.columns

    if has_v2:
        print("v2 模型: 已載入 (20天迴歸)")

    # 信號分佈
    if has_v2:
        rec_counts = pred_df["recommendation"].value_counts()
        print(f"\n建議分佈: {dict(rec_counts)}")

    # Top N — 套用產業集中度上限
    print(f"\n{'='*60}")
    print(f"  Top {top_n} 建議買進（產業上限 {int(SECTOR_CAP_RATIO*100)}%）")
    print(f"{'='*60}")
    if has_v2:
        top = apply_sector_cap(pred_df, top_n=top_n)
    else:
        top = pred_df.head(top_n)
    for _, row in top.iterrows():
        line = f"  {row['ticker']:>6s}  收盤:{row['close']:>8.1f}"
        if has_v2:
            line += f"  預估20d:{row['pred_return_20d']:+.1%}"
            line += f"  建議:{row['recommendation']}"
            risk = row.get("risk_tags", "")
            if risk:
                line += f"  {risk}"
            line += f"  [{row.get('sector','?')}]"
        else:
            line += f"  UP:{row['up_prob']:.1%}"
        _safe_print(line)

    # 儲存
    if output_path is None:
        out_path = os.path.join(MODEL_DIR, f"{output_prefix}_{pred_df['date'].iloc[0]}.csv")
    else:
        out_path = output_path
    pred_df.to_csv(out_path, index=False, encoding="utf-8-sig")
    print(f"\n完整預測已儲存: {out_path}")

    return {
        "pred_df": pred_df,
        "meta": meta,
        "output_path": out_path,
    }


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="台股預測")
    parser.add_argument("--top", type=int, default=30, help="顯示前 N 名")
    parser.add_argument("--model-slot", default="production", help="production 或 shadow")
    parser.add_argument("--model-meta", default=None, help="明確指定 base model meta path")
    parser.add_argument("--output-prefix", default="predictions", help="輸出檔名前綴")
    parser.add_argument("--output-path", default=None, help="明確指定輸出 CSV 路徑")
    parser.add_argument("--no-save-snapshot", action="store_true", help="不要覆寫 snapshot_cache.pkl")
    args = parser.parse_args()
    run_prediction(
        top_n=args.top,
        model_slot=args.model_slot,
        model_meta_path=args.model_meta,
        output_prefix=args.output_prefix,
        output_path=args.output_path,
        save_snapshot=not args.no_save_snapshot,
    )
