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

from backend.services.macro_event_service import build_macro_strategy_context, macro_stock_adjustment
from ml.config import (
    DAILY_K_DIR,
    INDEX_DIR,
    MODEL_DIR,
    TARGET_CLASSES,
    FORWARD_DAYS,
    TWO_STAGE_CANDIDATE_SIZE,
    TWO_STAGE_LABEL_BINS,
    TWO_STAGE_TOP_N,
)
from ml.dataset import build_latest_snapshot, apply_snapshot_zscore
from ml.features.entry import ENTRY_INFO_COLS
from ml.features.group import OTHER_GROUP_CODE, annotate_group_columns, is_capped_group
from ml.features.sector import load_sector_mapping
from ml.model_selection import get_slot_label, resolve_base_meta_path
from ml.two_stage_model import TwoStageModel, TwoStagePredictionConfig
from ml.chipk import load_latest_chipk_main_force
from ml.entry_shortwave import SHORTWAVE_OUTPUT_COLS, compute_shortwave_overlay

# === 處置股名單（每日更新）===
from ml.config import BASE_DIR as _ML_BASE_DIR
_DISPOSITION_PATH = os.path.join(_ML_BASE_DIR, "disposition_active.csv")

# (mtime_ns, today_isoformat) -> set；mtime 或日期變動才重讀 CSV
_DISPOSITION_CACHE: dict[tuple[int, str], set[str]] = {}


def _load_disposition_set() -> set[str]:
    """載入目前處置中的股票代號集合（依 CSV mtime + 今日日期 cache）。"""
    from datetime import date

    if not os.path.exists(_DISPOSITION_PATH):
        return set()

    try:
        mtime_ns = os.stat(_DISPOSITION_PATH).st_mtime_ns
    except OSError:
        return set()

    today_iso = date.today().isoformat()
    key = (mtime_ns, today_iso)
    cached = _DISPOSITION_CACHE.get(key)
    if cached is not None:
        return cached

    try:
        df = pd.read_csv(_DISPOSITION_PATH, dtype=str)
        if df.empty or "stock_id" not in df.columns:
            result: set[str] = set()
        else:
            if "period_end" in df.columns:
                df["period_end"] = pd.to_datetime(df["period_end"], errors="coerce").dt.date
                df = df[df["period_end"] >= date.today()]
            result = set(df["stock_id"].str.strip())
    except Exception:
        result = set()

    # 只保留最新一組，避免 cache 隨日期累積
    _DISPOSITION_CACHE.clear()
    _DISPOSITION_CACHE[key] = result
    return result

# === 產業對應表（用於組合防呆）===
_SECTOR_DF = load_sector_mapping()
_SECTOR_LOOKUP = {}
if _SECTOR_DF is not None:
    _SECTOR_LOOKUP = dict(zip(_SECTOR_DF["Ticker"], _SECTOR_DF["Sector"]))

# === 推薦/防呆閾值 ===
# 集中於 ml/thresholds.py 作為單一來源；app.py 也從同一處 import，
# 避免歷史上 RECOMMENDATION_OVERHEAT_THRESHOLD 在兩處 drift (0.18 vs 0.30)。
from ml.thresholds import (  # noqa: E402  常數匯入，保持原符號可用
    SECTOR_CAP_RATIO,
    SOFT_BUY_PROB_EDGE_MIN,
    BUY_PROB_EDGE_MIN,
    STRONG_BUY_PROB_EDGE_MIN,
    STRONG_BUY_PRED_RET_MIN,
    V2_OVERRIDE_PRED_RET_MIN,
    SELL_STRONG_PROB_EDGE_MAX,
    SELL_STRONG_PRED_RET_MAX,
    DUAL_TRACK_VETO_PROB_EDGE_MAX,
    DUAL_TRACK_VETO_DOWN_PROB_MIN,
    RECOMMENDATION_OVERHEAT_THRESHOLD,
    STRONG_BUY_MAX_INST_SELL_PCT,
    TOP30_EXCLUDE_INST_SELL_PCT,
    TURNAROUND_MARGIN_FLOOR,
    TURNAROUND_GROSS_MARGIN_MIN,
    DISPOSITION_OVERRIDE_MIN_VOLUME,
    CHIP_DIVERGE_WARN_THRESHOLD,
    CHIP_DIVERGE_BLOCK_THRESHOLD,
    GUARDRAIL_MONITOR_TOP_N,
    GUARDRAIL_INTERCEPT_RATE_LIMIT,
    GUARDRAIL_DUAL_TRACK_RATE_LIMIT,
    GROUP_CAP_RATIO,
    LEADERBOARD_SOFT_PROB_WEIGHT,
    LEADERBOARD_TURNAROUND_WEIGHT,
    LEADERBOARD_TURNAROUND_CAP_RATIO,
    PROB_EDGE_CLIP_LOW,
    PROB_EDGE_CLIP_HIGH,
    RISK_ADJUST_BASE,
    RISK_ADJUST_MIN_SCALE,
    RISK_ADJUST_MAX_SCALE,
    VOLUME_SHARES_PER_LOT,
    BETA_LOOKBACK_DAYS,
    BETA_MIN_OBSERVATIONS,
)

PENALTY_CONTEXT_COLS = [
    "gap_pct",
    "gap_freq_10d",
    "gap_up_avg_5d",
    "foreign_cumsum_20d_raw",
    "foreign_cumsum_20d_norm_raw",
    "foreign_cumsum_10d_raw",
    "foreign_cumsum_10d_norm_raw",
    "inst_buy_ratio_20d",
    "roa_annualized",
    "roe_annualized",
    "operating_margin_latest",
    "net_margin_latest",
    "gross_margin_latest",
    # REQ-002 / RD-2026-0506-001：高估值動能股 gate 需要 pe_ratio。
    "pe_ratio",
]

MACRO_STRATEGY_COLS = [
    "macro_event_risk_score",
    "macro_event_risk_level",
    "macro_event_count",
    "macro_event_next_title",
    "macro_event_nearest_days",
    "macro_market_sentiment_score",
    "macro_market_sentiment_label",
    "macro_strategy_pressure_score",
    "macro_strategy_action",
    "macro_stock_penalty_return",
    "macro_stock_penalty_multiplier",
    "macro_stock_action",
    "risk_adjusted_return_pre_macro",
    "leaderboard_score_pre_macro",
]

VALIDATED_CHIP_MOMENTUM_GATE_ENV = "VALIDATED_CHIP_MOMENTUM_GATE_ENABLED"
VALIDATED_CHIP_MOMENTUM_TOP_N_ENV = "VALIDATED_CHIP_MOMENTUM_TOP_N"
VALIDATED_CHIP_MOMENTUM_MIN_OPEN_ENV = "VALIDATED_CHIP_MOMENTUM_MIN_OPEN"
VALIDATED_CHIP_MOMENTUM_MIN_VOLUME_ENV = "VALIDATED_CHIP_MOMENTUM_MIN_VOLUME"
VALIDATED_CHIP_MOMENTUM_MIN_AMOUNT_ENV = "VALIDATED_CHIP_MOMENTUM_MIN_AMOUNT"
VALIDATED_CHIP_MOMENTUM_GATE_VERSION = "v1_chip_momentum_top5_20260611"
VALIDATED_CHIP_MOMENTUM_COMPONENTS = [
    "inst_total_20d_norm",
    "foreign_cumsum_20d_norm",
    "trust_cumsum_20d_norm",
    "inst_buy_ratio_20d",
    "return_20d",
]
VALIDATED_CHIP_MOMENTUM_LIQUIDITY_COLS = ["Open", "Volume", "AMOUNT_MA_20"]
VALIDATED_CHIP_MOMENTUM_RAW_FEATURE_COLS = [
    *VALIDATED_CHIP_MOMENTUM_LIQUIDITY_COLS,
    *VALIDATED_CHIP_MOMENTUM_COMPONENTS,
]
VALIDATED_CHIP_MOMENTUM_DIAGNOSTIC_COLS = [
    "validated_chip_momentum_gate_enabled",
    "validated_chip_momentum_gate_version",
    "validated_chip_momentum_gate_top_n",
    "validated_chip_momentum_min_open",
    "validated_chip_momentum_min_volume",
    "validated_chip_momentum_min_amount",
    "validated_chip_momentum_gate_error",
    "validated_chip_momentum_score",
    "validated_chip_momentum_rank",
    "validated_chip_momentum_liquidity_pass",
    "validated_chip_momentum_gate_pass",
]


def _safe_print(text: str) -> None:
    """Avoid Windows console encoding crashes on symbols like warning icons."""
    try:
        print(text)
    except UnicodeEncodeError:
        encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
        safe_text = str(text).encode(encoding, errors="replace").decode(encoding, errors="replace")
        print(safe_text)


def _dedupe_column_order(columns: list[str]) -> list[str]:
    """Preserve first occurrence order while preventing duplicate DataFrame columns."""
    seen: set[str] = set()
    deduped: list[str] = []
    for column in columns:
        if column in seen:
            continue
        seen.add(column)
        deduped.append(column)
    return deduped


def _column_as_series(df: pd.DataFrame, column: str) -> pd.Series:
    """Return one Series even when upstream assembly produced duplicate columns."""
    values = df[column]
    if isinstance(values, pd.DataFrame):
        return values.iloc[:, -1]
    return values


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


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

    avg_5d_amount = _snapshot_numeric(snapshot, "AMOUNT_MA_5")
    avg_20d_amount = _snapshot_numeric(snapshot, "AMOUNT_MA_20")
    avg_5d_amount = avg_5d_amount.where(avg_5d_amount.notna(), avg_5d_volume_shares * close)
    avg_20d_amount = avg_20d_amount.where(avg_20d_amount.notna(), avg_20d_volume_shares * close)

    out["volume_today"] = (volume_today_shares / VOLUME_SHARES_PER_LOT).astype(np.float32)
    out["avg_5d_volume"] = (avg_5d_volume_shares / VOLUME_SHARES_PER_LOT).astype(np.float32)
    out["avg_20d_volume"] = (avg_20d_volume_shares / VOLUME_SHARES_PER_LOT).astype(np.float32)
    out["avg_5d_amount"] = avg_5d_amount.astype(np.float64)
    out["avg_20d_amount"] = avg_20d_amount.astype(np.float64)
    return out


def _load_twii_returns() -> pd.DataFrame:
    index_path = os.path.join(INDEX_DIR, "index_TWII.csv")
    if not os.path.exists(index_path):
        return pd.DataFrame(columns=["Date", "twii_return_1d"])
    try:
        twii = pd.read_csv(index_path, usecols=["Date", "Close"])
    except Exception:
        return pd.DataFrame(columns=["Date", "twii_return_1d"])

    twii["Date"] = pd.to_datetime(twii["Date"], errors="coerce")
    twii["Close"] = pd.to_numeric(twii["Close"], errors="coerce")
    twii = twii.dropna(subset=["Date", "Close"]).sort_values("Date")
    twii["twii_return_1d"] = twii["Close"].pct_change()
    return twii[["Date", "twii_return_1d"]].dropna()


def _compute_beta_60_for_ticker(
    ticker: str,
    prediction_date: pd.Timestamp,
    twii_returns: pd.DataFrame,
) -> float:
    stock_path = os.path.join(DAILY_K_DIR, f"{ticker}.csv")
    if not os.path.exists(stock_path) or twii_returns.empty:
        return np.nan
    try:
        stock = pd.read_csv(stock_path, usecols=["Date", "Close"])
    except Exception:
        return np.nan

    stock["Date"] = pd.to_datetime(stock["Date"], errors="coerce")
    stock["Close"] = pd.to_numeric(stock["Close"], errors="coerce")
    stock = stock.dropna(subset=["Date", "Close"]).sort_values("Date")
    stock["stock_return_1d"] = stock["Close"].pct_change()
    merged = stock[["Date", "stock_return_1d"]].merge(twii_returns, on="Date", how="inner")
    window = merged[merged["Date"] <= prediction_date].dropna().tail(BETA_LOOKBACK_DAYS)
    if len(window) < BETA_MIN_OBSERVATIONS:
        return np.nan

    market_var = float(window["twii_return_1d"].var())
    if not np.isfinite(market_var) or market_var <= 0:
        return np.nan
    beta = float(window["stock_return_1d"].cov(window["twii_return_1d"]) / market_var)
    return beta if np.isfinite(beta) else np.nan


def _attach_beta_artifacts(pred_df: pd.DataFrame) -> pd.DataFrame:
    """Attach rolling beta context for downstream gates, not model inference."""
    out = pred_df.copy()
    if out.empty or "ticker" not in out.columns or "date" not in out.columns:
        out["beta_60"] = np.nan
        return out

    prediction_date = pd.to_datetime(out["date"].iloc[0], errors="coerce")
    if pd.isna(prediction_date):
        out["beta_60"] = np.nan
        return out

    twii_returns = _load_twii_returns()
    beta_cache: dict[str, float] = {}
    for ticker in out["ticker"].astype(str).str.strip().unique():
        beta_cache[ticker] = _compute_beta_60_for_ticker(ticker, prediction_date, twii_returns)
    out["beta_60"] = out["ticker"].astype(str).str.strip().map(beta_cache).astype(np.float32)
    return out


def _raw_context_for_ticker(ticker: str, prediction_date: pd.Timestamp) -> dict[str, float]:
    stock_path = os.path.join(DAILY_K_DIR, f"{ticker}.csv")
    if not os.path.exists(stock_path):
        return {}
    try:
        stock = pd.read_csv(
            stock_path,
            usecols=lambda col: col in {"Date", "Open", "Close", "Volume", "Foreign_BuySell"},
        )
    except Exception:
        return {}

    stock["Date"] = pd.to_datetime(stock["Date"], errors="coerce")
    stock = stock.dropna(subset=["Date"]).sort_values("Date")
    stock = stock[stock["Date"] <= prediction_date].copy()
    if stock.empty:
        return {}

    close = pd.to_numeric(stock.get("Close"), errors="coerce")
    open_ = pd.to_numeric(stock.get("Open"), errors="coerce")
    prev_close = close.shift(1).replace(0, np.nan)
    gap = open_ / prev_close - 1.0
    positive_gap = gap.clip(lower=0)

    if "Foreign_BuySell" in stock.columns:
        foreign = pd.to_numeric(stock["Foreign_BuySell"], errors="coerce").fillna(0.0)
    else:
        foreign = pd.Series(0.0, index=stock.index, dtype="float64")
    if "Volume" in stock.columns:
        volume = pd.to_numeric(stock["Volume"], errors="coerce").fillna(0.0)
    else:
        volume = pd.Series(0.0, index=stock.index, dtype="float64")

    def _window_sum(series: pd.Series, window: int) -> float:
        value = float(series.tail(window).sum())
        return value if np.isfinite(value) else np.nan

    def _window_norm(series: pd.Series, window: int) -> float:
        vol_sum = float(volume.tail(window).sum())
        if not np.isfinite(vol_sum) or vol_sum <= 0:
            return np.nan
        value = float(series.tail(window).sum() / vol_sum)
        return value if np.isfinite(value) else np.nan

    latest_gap = float(gap.iloc[-1]) if len(gap) else np.nan
    avg_gap_up_5d = float(positive_gap.tail(5).mean()) if len(positive_gap) else np.nan
    return {
        "gap_pct": latest_gap if np.isfinite(latest_gap) else np.nan,
        "gap_up_avg_5d": avg_gap_up_5d if np.isfinite(avg_gap_up_5d) else np.nan,
        "foreign_cumsum_20d_raw": _window_sum(foreign, 20),
        "foreign_cumsum_20d_norm_raw": _window_norm(foreign, 20),
        "foreign_cumsum_10d_raw": _window_sum(foreign, 10),
        "foreign_cumsum_10d_norm_raw": _window_norm(foreign, 10),
    }


def _attach_penalty_context_artifacts(pred_df: pd.DataFrame, snapshot: pd.DataFrame) -> pd.DataFrame:
    """Attach white-box penalty context for downstream overlay ranking."""
    out = pred_df.copy()
    if out.empty:
        for col in PENALTY_CONTEXT_COLS:
            out[col] = np.nan
        return out

    for col in [
        "gap_pct",
        "gap_freq_10d",
        "inst_buy_ratio_20d",
        "roa_annualized",
        "roe_annualized",
        "operating_margin_latest",
        "net_margin_latest",
        "gross_margin_latest",
        "pe_ratio",
    ]:
        if col in snapshot.columns:
            values = pd.to_numeric(snapshot[col], errors="coerce")
            out[col] = pd.Series(values.to_numpy(), index=out.index, dtype="float64")
        elif col not in out.columns:
            out[col] = np.nan

    if "ticker" not in out.columns or "date" not in out.columns:
        for col in [
            "gap_up_avg_5d",
            "foreign_cumsum_20d_raw",
            "foreign_cumsum_20d_norm_raw",
            "foreign_cumsum_10d_raw",
            "foreign_cumsum_10d_norm_raw",
        ]:
            out[col] = np.nan
        return out

    raw_cache: dict[tuple[str, str], dict[str, float]] = {}
    raw_rows: list[dict[str, float]] = []
    for row in out[["ticker", "date"]].itertuples(index=False):
        ticker = str(row.ticker).strip()
        date_text = str(row.date)
        key = (ticker, date_text)
        if key not in raw_cache:
            prediction_date = pd.to_datetime(date_text, errors="coerce")
            raw_cache[key] = (
                _raw_context_for_ticker(ticker, prediction_date)
                if not pd.isna(prediction_date)
                else {}
            )
        raw_rows.append(raw_cache[key])

    raw_df = pd.DataFrame(raw_rows, index=out.index)
    for col in [
        "gap_up_avg_5d",
        "foreign_cumsum_20d_raw",
        "foreign_cumsum_20d_norm_raw",
        "foreign_cumsum_10d_raw",
        "foreign_cumsum_10d_norm_raw",
    ]:
        if col in raw_df.columns:
            out[col] = pd.to_numeric(raw_df[col], errors="coerce").astype(np.float32)
        elif col not in out.columns:
            out[col] = np.nan

    return out


def _append_risk_tag(existing: object, tag: str) -> str:
    left = str(existing or "").strip()
    if not left:
        return tag
    if tag in {part.strip() for part in left.replace(",", " ").split()}:
        return left
    return f"{left} {tag}".strip()


def _attach_macro_strategy_artifacts(pred_df: pd.DataFrame) -> pd.DataFrame:
    """Attach daily macro context and apply transparent ranking penalties."""
    out = pred_df.copy()
    if out.empty:
        for col in MACRO_STRATEGY_COLS:
            out[col] = np.nan
        return out

    as_of = None
    if "date" in out.columns:
        dates = out["date"].dropna().astype(str)
        if not dates.empty:
            as_of = dates.iloc[0][:10]
    try:
        context = build_macro_strategy_context(as_of=as_of, horizon_days=20)
    except Exception:
        context = {
            "events": {"risk_score": 0.0, "risk_level": "none", "event_count": 0, "next_event": None},
            "market_sentiment": {"score": 50.0, "label": "neutral"},
            "strategy": {"macro_pressure_score": 0.0, "action": "normal"},
            "next_event": None,
        }

    events = context.get("events", {}) if isinstance(context, dict) else {}
    sentiment = context.get("market_sentiment", {}) if isinstance(context, dict) else {}
    strategy = context.get("strategy", {}) if isinstance(context, dict) else {}
    next_event = context.get("next_event") or events.get("next_event") or {}
    pressure = float(strategy.get("macro_pressure_score") or 0.0)

    beta = pd.to_numeric(out.get("beta_60", pd.Series(np.nan, index=out.index)), errors="coerce")
    gap = pd.to_numeric(out.get("gap_pct", pd.Series(np.nan, index=out.index)), errors="coerce")
    ma20 = pd.to_numeric(out.get("price_vs_ma20", pd.Series(np.nan, index=out.index)), errors="coerce")

    adjustments = [
        macro_stock_adjustment(
            beta_60=beta.loc[idx],
            gap_pct=gap.loc[idx],
            price_vs_ma20=ma20.loc[idx],
            context=context,
        )
        for idx in out.index
    ]
    adj_df = pd.DataFrame(adjustments, index=out.index)

    out["macro_event_risk_score"] = float(events.get("risk_score") or 0.0)
    out["macro_event_risk_level"] = str(events.get("risk_level") or "none")
    out["macro_event_count"] = int(events.get("event_count") or 0)
    out["macro_event_next_title"] = str(next_event.get("title") or "")
    out["macro_event_nearest_days"] = next_event.get("days_until")
    out["macro_market_sentiment_score"] = float(sentiment.get("score") or 50.0)
    out["macro_market_sentiment_label"] = str(sentiment.get("label") or "neutral")
    out["macro_strategy_pressure_score"] = pressure
    out["macro_strategy_action"] = str(strategy.get("action") or "normal")
    out["macro_stock_penalty_return"] = pd.to_numeric(
        adj_df.get("macro_stock_penalty_return", 0.0),
        errors="coerce",
    ).fillna(0.0)
    out["macro_stock_penalty_multiplier"] = pd.to_numeric(
        adj_df.get("macro_stock_penalty_multiplier", 1.0),
        errors="coerce",
    ).fillna(1.0)
    out["macro_stock_action"] = adj_df.get("macro_stock_action", "normal").fillna("normal").astype(str)

    penalty = pd.to_numeric(out["macro_stock_penalty_return"], errors="coerce").fillna(0.0)
    if "risk_adjusted_return" in out.columns:
        out["risk_adjusted_return_pre_macro"] = pd.to_numeric(out["risk_adjusted_return"], errors="coerce")
        out["risk_adjusted_return"] = out["risk_adjusted_return_pre_macro"] - penalty
    else:
        out["risk_adjusted_return_pre_macro"] = np.nan

    if "leaderboard_score" in out.columns:
        out["leaderboard_score_pre_macro"] = pd.to_numeric(out["leaderboard_score"], errors="coerce")
        out["leaderboard_score"] = out["leaderboard_score_pre_macro"] - penalty
    else:
        out["leaderboard_score_pre_macro"] = np.nan

    if pressure >= 25:
        if "risk_tags" not in out.columns:
            out["risk_tags"] = ""
        out["risk_tags"] = out["risk_tags"].map(lambda value: _append_risk_tag(value, "MACRO_EVENT_RISK"))

    return out


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
        | ((_col("operating_margin_latest", -1.0) > TURNAROUND_MARGIN_FLOOR) & (_col("gross_margin_latest") > TURNAROUND_GROSS_MARGIN_MIN))
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


def _validated_chip_momentum_gate_enabled() -> bool:
    return _env_bool(VALIDATED_CHIP_MOMENTUM_GATE_ENV, default=True)


def _validated_chip_momentum_top_n() -> int:
    return max(1, _env_int(VALIDATED_CHIP_MOMENTUM_TOP_N_ENV, 5))


def _validated_chip_momentum_min_open() -> float:
    return _env_float(VALIDATED_CHIP_MOMENTUM_MIN_OPEN_ENV, 10.0)


def _validated_chip_momentum_min_volume() -> float:
    return _env_float(VALIDATED_CHIP_MOMENTUM_MIN_VOLUME_ENV, 250_000.0)


def _validated_chip_momentum_min_amount() -> float:
    return _env_float(VALIDATED_CHIP_MOMENTUM_MIN_AMOUNT_ENV, 20_000_000.0)


def attach_validated_chip_momentum_gate(df: pd.DataFrame) -> pd.DataFrame:
    """Attach diagnostics for the independently validated chip-momentum entry model."""
    out = df.copy()
    index = out.index
    enabled = _validated_chip_momentum_gate_enabled()
    top_n = _validated_chip_momentum_top_n()
    min_open = _validated_chip_momentum_min_open()
    min_volume = _validated_chip_momentum_min_volume()
    min_amount = _validated_chip_momentum_min_amount()

    out["validated_chip_momentum_gate_enabled"] = bool(enabled)
    out["validated_chip_momentum_gate_version"] = VALIDATED_CHIP_MOMENTUM_GATE_VERSION
    out["validated_chip_momentum_gate_top_n"] = int(top_n)
    out["validated_chip_momentum_min_open"] = float(min_open)
    out["validated_chip_momentum_min_volume"] = float(min_volume)
    out["validated_chip_momentum_min_amount"] = float(min_amount)
    out["validated_chip_momentum_gate_error"] = ""
    out["validated_chip_momentum_score"] = np.nan
    out["validated_chip_momentum_rank"] = np.nan
    out["validated_chip_momentum_liquidity_pass"] = False
    out["validated_chip_momentum_gate_pass"] = True

    required = set(VALIDATED_CHIP_MOMENTUM_COMPONENTS + VALIDATED_CHIP_MOMENTUM_LIQUIDITY_COLS)
    missing = sorted(required - set(out.columns))
    if missing:
        out["validated_chip_momentum_gate_error"] = "missing_columns: " + ",".join(missing)
        return out

    score = pd.Series(0.0, index=index, dtype="float64")
    for component in VALIDATED_CHIP_MOMENTUM_COMPONENTS:
        values = pd.to_numeric(_column_as_series(out, component), errors="coerce")
        score = score + values.rank(pct=True, ascending=True).fillna(0.5)
    score = score / float(len(VALIDATED_CHIP_MOMENTUM_COMPONENTS))
    out["validated_chip_momentum_score"] = score

    liquidity_pass = (
        pd.to_numeric(_column_as_series(out, "Open"), errors="coerce").ge(min_open)
        & pd.to_numeric(_column_as_series(out, "Volume"), errors="coerce").ge(min_volume)
        & pd.to_numeric(_column_as_series(out, "AMOUNT_MA_20"), errors="coerce").ge(min_amount)
    ).fillna(False)
    out["validated_chip_momentum_liquidity_pass"] = liquidity_pass.astype(bool)

    eligible = liquidity_pass & score.notna()
    if eligible.any():
        ticker_values = (
            _column_as_series(out, "ticker").astype(str)
            if "ticker" in out.columns
            else pd.Series("", index=index)
        )
        ranking_frame = pd.DataFrame(
            {
                "score": score.loc[eligible],
                "ticker": ticker_values.loc[eligible],
            },
            index=out.index[eligible],
        )
        ordered_index = ranking_frame.sort_values(
            ["score", "ticker"],
            ascending=[False, True],
            na_position="last",
        ).index
        ranks = pd.Series(np.nan, index=index, dtype="float64")
        ranks.loc[ordered_index] = np.arange(1, len(ordered_index) + 1, dtype=float)
        out["validated_chip_momentum_rank"] = ranks
        out["validated_chip_momentum_gate_pass"] = ranks.le(float(top_n)).fillna(False)
    else:
        out["validated_chip_momentum_gate_pass"] = False

    return out


def apply_validated_chip_momentum_entry_filter(df: pd.DataFrame) -> pd.DataFrame:
    """Filter the 20D entry pool to the verified chip-momentum Top-N model."""
    out = attach_validated_chip_momentum_gate(df)
    if not _validated_chip_momentum_gate_enabled():
        return out
    if out["validated_chip_momentum_gate_error"].fillna("").astype(str).str.strip().ne("").any():
        return out
    return out.loc[out["validated_chip_momentum_gate_pass"].fillna(False).astype(bool)].copy()


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


def _resolve_v2_meta_path():
    meta_path = os.environ.get("V2_MODEL_META", "").strip()
    if not meta_path:
        v2_files = sorted(glob.glob(os.path.join(MODEL_DIR, "lgbm_v2_*_meta.json")))
        if not v2_files:
            return None
        meta_path = v2_files[-1]
    return meta_path


def load_v2_model():
    """載入 v2 迴歸模型 (如果存在)"""
    meta_path = _resolve_v2_meta_path()
    if not meta_path:
        return None, None
    with open(meta_path, "r", encoding="utf-8") as f:
        meta = json.load(f)

    model = lgb.Booster(model_file=meta["model_file"])
    return model, meta


def _resolve_alpha_meta_path():
    enabled = os.environ.get("ENABLE_ALPHA_CLASSIFIER_RESEARCH", "").strip().lower()
    if enabled not in {"1", "true", "yes", "on"}:
        return None

    alpha_files = sorted(glob.glob(os.path.join(MODEL_DIR, "lgbm_alpha_cls_v2_*_meta.json")))
    if not alpha_files:
        return None
    return alpha_files[-1]


def load_v2_alpha_classifier():
    """Load optional V2 20D alpha win-rate classifier."""
    meta_path = _resolve_alpha_meta_path()
    if not meta_path:
        return None, None
    with open(meta_path, "r", encoding="utf-8") as f:
        meta = json.load(f)

    model = lgb.Booster(model_file=meta["model_file"])
    return model, meta


def _resolve_ranker_meta_path():
    enabled = os.environ.get("TWO_STAGE_RANKER_ENABLED", "").strip().lower()
    if enabled not in {"1", "true", "yes", "on"}:
        return None

    meta_path = os.environ.get("TWO_STAGE_RANKER_META", "").strip()
    if not meta_path:
        ranker_files = sorted(glob.glob(os.path.join(MODEL_DIR, "lgbm_two_stage_ranker_*_meta.json")))
        if not ranker_files:
            return None
        meta_path = ranker_files[-1]
    return meta_path


def load_two_stage_ranker():
    """Load optional AUDIT-002 Stage 2 ranker using the existing feature flag."""
    meta_path = _resolve_ranker_meta_path()
    if not meta_path:
        return None, None

    with open(meta_path, "r", encoding="utf-8") as f:
        meta = json.load(f)
    model = lgb.Booster(model_file=meta["model_file"])
    meta["candidate_size"] = int(meta.get("candidate_size", TWO_STAGE_CANDIDATE_SIZE))
    meta["label_bins"] = int(meta.get("label_bins", TWO_STAGE_LABEL_BINS))
    meta["top_n"] = int(meta.get("top_n", TWO_STAGE_TOP_N))
    return model, meta


def _prediction_model_paths(model_slot, model_meta_path):
    """Resolve the same paths used by loaders, before any weights are loaded."""
    paths = {"base": resolve_base_meta_path(slot=model_slot, explicit_meta_path=model_meta_path),
             "v2": _resolve_v2_meta_path(), "alpha": _resolve_alpha_meta_path(),
             "ranker": _resolve_ranker_meta_path()}
    models = {}
    for role, path in paths.items():
        if path:
            with open(path, "r", encoding="utf-8") as handle:
                metadata = json.load(handle)
            models[role] = {"meta_path": os.path.abspath(path),
                            "model_path": os.path.abspath(metadata["model_file"])}
    return models


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


def _finalize_guardrail_metrics(
    pred_df: pd.DataFrame,
    *,
    intercept_rate: float,
    dual_track_fail_rate: float,
    loose_mode: bool,
    trigger_reason: str,
    top_n: int,
) -> pd.DataFrame:
    """Attach market-level guardrail metrics for monitoring and downstream alerts."""
    pred_df = pred_df.copy()
    pred_df["guardrail_loose_mode"] = np.int8(1 if loose_mode else 0)
    pred_df["guardrail_intercept_rate_top50"] = float(intercept_rate)
    pred_df["guardrail_dual_track_fail_rate_top50"] = float(dual_track_fail_rate)
    pred_df["guardrail_monitor_top_n"] = np.int16(top_n)
    if "guardrail_trigger_reason" not in pred_df.columns:
        pred_df["guardrail_trigger_reason"] = ""
    pred_df["guardrail_trigger_reason"] = pred_df["guardrail_trigger_reason"].fillna("").astype(str)
    if trigger_reason:
        market_reason = f"MARKET_GUARDRAIL_CIRCUIT:{trigger_reason}"
        pred_df["guardrail_trigger_reason"] = np.where(
            pred_df["guardrail_trigger_reason"].str.strip().ne(""),
            pred_df["guardrail_trigger_reason"] + ";" + market_reason,
            market_reason,
        )
    return pred_df


def _set_guardrail_reason(
    pred_df: pd.DataFrame,
    mask: pd.Series,
    reason: str,
) -> None:
    if not mask.any():
        return
    if "guardrail_trigger_reason" not in pred_df.columns:
        pred_df["guardrail_trigger_reason"] = ""
    existing = pred_df.loc[mask, "guardrail_trigger_reason"].fillna("").astype(str)
    pred_df.loc[mask, "guardrail_trigger_reason"] = [
        f"{left};{reason}" if left.strip() else reason
        for left in existing.tolist()
    ]


def _compute_guardrail_metrics(pred_df: pd.DataFrame) -> tuple[float, float, int]:
    """Measure top-of-book guardrail saturation on the V2 Top 50 slice."""
    if pred_df.empty or "pred_return_20d" not in pred_df.columns:
        return 0.0, 0.0, 0

    top_slice = pred_df.nlargest(GUARDRAIL_MONITOR_TOP_N, "pred_return_20d")
    if top_slice.empty:
        return 0.0, 0.0, 0

    intercept_rate = float(top_slice["guardrail_blocked"].astype(float).mean())
    dual_track_fail_rate = float(top_slice["guardrail_dual_track_conflict"].astype(float).mean())
    return intercept_rate, dual_track_fail_rate, int(len(top_slice))


def _apply_recommendation_rules_core(
    pred_df: pd.DataFrame,
    snapshot: pd.DataFrame,
    *,
    loose_mode: bool = False,
) -> pd.DataFrame:
    """Apply guardrail logic once. Wrapper may rerun in loose mode after circuit-breaker checks."""
    pred_df = pred_df.copy()
    pred_df["guardrail_trigger_reason"] = ""
    pred_df["prob_edge"] = pred_df["up_prob"] - pred_df["down_prob"]
    if "risk_tags" in pred_df.columns:
        pred_df["risk_tags"] = pred_df["risk_tags"].fillna("").astype(str)
    else:
        pred_df["risk_tags"] = ""

    # --- Step 1: ATR 天花板 ---
    if "atr_pct" in snapshot.columns:
        atr = snapshot["atr_pct"].fillna(0.05)
        atr_cap = (atr * 10).clip(lower=0.10)
        over_cap = pred_df["pred_return_20d"].abs() > atr_cap
        if over_cap.any():
            sign = pred_df.loc[over_cap, "pred_return_20d"].apply(lambda x: 1 if x > 0 else -1)
            pred_df.loc[over_cap, "pred_return_20d"] = sign * atr_cap[over_cap]
            pred_df.loc[over_cap, "risk_tags"] += "⚠️預測報酬超出波動上限 "

    # --- Step 2: V1+V2 雙軌同意判定 ---
    v1_up_signal = pred_df["signal"].eq("UP")
    v1_down_signal = pred_df["signal"].eq("DOWN")
    v1_hard_buy_ok = v1_up_signal & (pred_df["prob_edge"] >= BUY_PROB_EDGE_MIN)
    v1_ok_buy = pred_df["prob_edge"] > SOFT_BUY_PROB_EDGE_MIN
    v1_strong_bull = v1_up_signal & (pred_df["prob_edge"] >= STRONG_BUY_PROB_EDGE_MIN)
    v2_bull = pred_df["pred_return_20d"] > 0
    v2_strong_bull = pred_df["pred_return_20d"] >= STRONG_BUY_PRED_RET_MIN
    v2_override_buy = pred_df["pred_return_20d"] >= V2_OVERRIDE_PRED_RET_MIN

    v1_bear = v1_down_signal & (pred_df["prob_edge"] < 0)
    v2_bear = pred_df["pred_return_20d"] < 0
    v1_strong_bear = v1_down_signal & (pred_df["prob_edge"] <= SELL_STRONG_PROB_EDGE_MAX)
    v2_strong_bear = pred_df["pred_return_20d"] <= SELL_STRONG_PRED_RET_MAX
    v1_veto = (
        v1_down_signal
        & (pred_df["prob_edge"] <= DUAL_TRACK_VETO_PROB_EDGE_MAX)
        & (pred_df["down_prob"] >= DUAL_TRACK_VETO_DOWN_PROB_MIN)
    )

    pred_df["recommendation"] = "觀望"

    buy_candidate = v2_bull & (v1_ok_buy | v2_override_buy | loose_mode)
    both_bull = v1_ok_buy & v2_bull
    pred_df.loc[buy_candidate, "recommendation"] = "建議買進"

    both_bear = v1_bear & v2_bear
    pred_df.loc[both_bear, "recommendation"] = "建議賣出"

    both_strong_bear = v1_strong_bear & v2_strong_bear
    pred_df.loc[both_strong_bear, "recommendation"] = "強力賣出"

    pred_df["v1_buy_ok"] = v1_ok_buy.astype(np.int8)
    pred_df["v1_hard_buy_ok"] = v1_hard_buy_ok.astype(np.int8)
    pred_df["v1_veto"] = v1_veto.astype(np.int8)
    pred_df["v2_buy_ok"] = v2_bull.astype(np.int8)
    pred_df["v2_override_buy"] = v2_override_buy.astype(np.int8)
    pred_df["dual_track_buy"] = both_bull.astype(np.int8)
    pred_df["v2_only_bull"] = (v2_bull & (~v1_ok_buy)).astype(np.int8)

    conflict_up = (~v1_ok_buy) & v2_bull
    conflict_down = v1_ok_buy & (~v2_bull)
    pred_df["guardrail_dual_track_conflict"] = conflict_up.astype(np.int8)
    pred_df.loc[conflict_up & (~v1_veto), "risk_tags"] += "⚠️V1偏空但V2看多（分歧） "
    pred_df.loc[conflict_up & v1_veto, "risk_tags"] += "⚠️V1強烈看空但V2看多（分歧） "
    pred_df.loc[conflict_down, "risk_tags"] += "⚠️V1看多但V2看空（分歧） "
    pred_df.loc[(~v1_ok_buy) & v2_override_buy, "risk_tags"] += "⚠️V2強勢看多，改採降倉通過 "
    pred_df.loc[loose_mode & conflict_up, "risk_tags"] += "⚠️Guardrail熔斷：已暫停V1否決權 "

    # --- Step 3: 風險標籤收集 ---
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
    liquidity_proxy = pd.Series(0.0, index=pred_df.index, dtype=np.float32)
    if "VOL_MA_5" in snapshot.columns:
        liquidity_proxy = snapshot["VOL_MA_5"].fillna(liquidity_proxy)
        low_vol = low_vol | (snapshot["VOL_MA_5"] < 500_000)
    if "Volume" in snapshot.columns:
        liquidity_proxy = snapshot["Volume"].fillna(liquidity_proxy)
        low_vol = low_vol | (snapshot["Volume"] < 500_000)
    pred_df.loc[low_vol, "risk_tags"] += "⚠️流動性不足 "
    disposition_liquid = liquidity_proxy >= DISPOSITION_OVERRIDE_MIN_VOLUME

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

    chip_bear_warn = pd.Series(False, index=pred_df.index)
    chip_bear_block = pd.Series(False, index=pred_df.index)
    if "chip_diverge_bear" in snapshot.columns:
        chip_bear_warn = snapshot["chip_diverge_bear"] >= CHIP_DIVERGE_WARN_THRESHOLD
        chip_bear_block = snapshot["chip_diverge_bear"] >= CHIP_DIVERGE_BLOCK_THRESHOLD
        pred_df.loc[chip_bear_warn, "risk_tags"] += "⚠️籌碼頂部背離 "

    chip_bull = pd.Series(False, index=pred_df.index)
    if "chip_diverge_bull" in snapshot.columns:
        chip_bull = snapshot["chip_diverge_bull"] >= 1.0
        pred_df.loc[chip_bull, "risk_tags"] += "💡底部吸籌訊號 "

    disposition_set = _load_disposition_set()
    if disposition_set and "ticker" in pred_df.columns:
        is_disposition = pred_df["ticker"].isin(disposition_set)
        pred_df.loc[is_disposition, "risk_tags"] += "⚠️處置股（分盤交易） "
    else:
        is_disposition = pd.Series(False, index=pred_df.index)
    disposition_override = (
        is_disposition
        & v2_override_buy
        & disposition_liquid
        & (~op_loss)
        & (~crash)
    )
    pred_df["disposition_override"] = disposition_override.astype(np.int8)
    pred_df.loc[disposition_override, "risk_tags"] += "💡處置股但高強度放行 "

    weak_edge = both_bull & (~v1_hard_buy_ok)
    pred_df.loc[weak_edge, "risk_tags"] += "⚠️勝率優勢未達強力買進門檻 "

    # --- Step 4: 強力買進升級 ---
    strong_buy = (
        both_bull
        & v1_strong_bull
        & v2_strong_bull
        & price_ok
        & inst_ok
        & (~low_vol)
        & (~crash)
        & (~chip_bear_block)
        & (~op_loss)
        & (~is_disposition)
    )
    pred_df.loc[strong_buy, "recommendation"] = "強力買進"

    # --- Step 5: 硬擋降級 ---
    buy_labels = ["強力買進", "建議買進"]
    disposition_block = is_disposition & (~disposition_override)
    _set_guardrail_reason(pred_df, disposition_block, "DISPOSITION_PERIOD")
    pred_df.loc[disposition_block, "recommendation"] = "觀望（處置股）"

    low_vol_block = low_vol & pred_df["recommendation"].isin(buy_labels)
    _set_guardrail_reason(pred_df, low_vol_block, "LIQUIDITY_GUARD")
    pred_df.loc[low_vol_block, "recommendation"] = "觀望（流動性不足）"

    crash_block = crash & pred_df["recommendation"].isin(buy_labels)
    _set_guardrail_reason(pred_df, crash_block, "CRASH_GUARD")
    pred_df.loc[crash_block, "recommendation"] = "觀望（異常暴跌）"

    extreme_pred_block = extreme_pred & pred_df["recommendation"].isin(buy_labels)
    _set_guardrail_reason(pred_df, extreme_pred_block, "EXTREME_PREDICTION_GUARD")
    pred_df.loc[extreme_pred_block, "recommendation"] = "觀望（預測值異常）"

    chip_bear_blocked = chip_bear_block & pred_df["recommendation"].isin(buy_labels)
    _set_guardrail_reason(pred_df, chip_bear_blocked, "CHIP_DIVERGENCE_GUARD")
    pred_df.loc[chip_bear_blocked, "recommendation"] = "觀望（籌碼頂部背離）"

    op_loss_block = (op_loss & (~turnaround)) & pred_df["recommendation"].isin(buy_labels)
    _set_guardrail_reason(pred_df, op_loss_block, "FUNDAMENTAL_GUARD")
    pred_df.loc[op_loss_block, "recommendation"] = "觀望（基本面警示）"
    pred_df["guardrail_blocked"] = pred_df["recommendation"].astype(str).str.startswith("觀望").astype(np.int8)
    no_reason_block = pred_df["guardrail_blocked"].astype(bool) & pred_df["guardrail_trigger_reason"].str.strip().eq("")
    _set_guardrail_reason(pred_df, no_reason_block, "NO_BUY_SIGNAL_OR_LOW_EDGE")

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


def _apply_recommendation_rules(pred_df: pd.DataFrame, snapshot: pd.DataFrame) -> pd.DataFrame:
    """Dynamic guardrail wrapper with a circuit breaker for production incidents."""
    strict_df = _apply_recommendation_rules_core(pred_df, snapshot, loose_mode=False)
    intercept_rate, dual_track_fail_rate, top_n = _compute_guardrail_metrics(strict_df)

    trigger_reasons = []
    if intercept_rate > GUARDRAIL_INTERCEPT_RATE_LIMIT:
        trigger_reasons.append(
            f"Top{top_n}攔截率{intercept_rate:.1%}>{GUARDRAIL_INTERCEPT_RATE_LIMIT:.0%}"
        )
    if dual_track_fail_rate > GUARDRAIL_DUAL_TRACK_RATE_LIMIT:
        trigger_reasons.append(
            f"Top{top_n}雙軌分歧{dual_track_fail_rate:.1%}>{GUARDRAIL_DUAL_TRACK_RATE_LIMIT:.0%}"
        )

    if trigger_reasons:
        loose_df = _apply_recommendation_rules_core(pred_df, snapshot, loose_mode=True)
        return _finalize_guardrail_metrics(
            loose_df,
            intercept_rate=intercept_rate,
            dual_track_fail_rate=dual_track_fail_rate,
            loose_mode=True,
            trigger_reason="; ".join(trigger_reasons),
            top_n=top_n,
        )

    return _finalize_guardrail_metrics(
        strict_df,
        intercept_rate=intercept_rate,
        dual_track_fail_rate=dual_track_fail_rate,
        loose_mode=False,
        trigger_reason="",
        top_n=top_n,
    )


def _apply_two_stage_ranking(
    pred_df: pd.DataFrame,
    feature_frame: pd.DataFrame,
    feature_cols: list[str],
    ranker_model,
    ranker_meta: dict,
) -> pd.DataFrame:
    """Attach optional Two-Stage ranker fields and rerank leaderboard scores."""
    if pred_df.empty or "pred_return_20d" not in pred_df.columns:
        return pred_df

    out = pred_df.copy()
    candidate_size = int(ranker_meta.get("candidate_size", TWO_STAGE_CANDIDATE_SIZE))
    label_bins = int(ranker_meta.get("label_bins", TWO_STAGE_LABEL_BINS))
    top_n = int(ranker_meta.get("top_n", TWO_STAGE_TOP_N))

    inference_frame = feature_frame.copy()
    inference_frame["ticker"] = out["ticker"].astype(str).values
    inference_frame["stage1_score"] = pd.to_numeric(out["pred_return_20d"], errors="coerce").values

    two_stage = TwoStageModel(
        stage1_model=None,
        stage2_model=ranker_model,
        feature_cols=feature_cols,
        config=TwoStagePredictionConfig(candidate_size=candidate_size, top_n=top_n),
    )
    ranked = two_stage.predict(inference_frame, precomputed_stage1_score_col="stage1_score")

    out["two_stage_enabled"] = np.int8(1)
    out["two_stage_candidate_size"] = np.int16(candidate_size)
    out["two_stage_label_bins"] = np.int16(label_bins)
    out["two_stage_model_file"] = os.path.basename(ranker_meta.get("model_file", ""))
    out["two_stage_score"] = np.nan
    out["two_stage_rank"] = np.nan
    out["two_stage_candidate"] = np.int8(0)
    out["leaderboard_score_before_two_stage"] = out.get("leaderboard_score", np.nan)

    if ranked.empty:
        out["leaderboard_score"] = -np.inf
        return out

    score_by_ticker = ranked.set_index("ticker")["stage2_score"]
    rank_by_ticker = ranked.set_index("ticker")["two_stage_rank"]
    candidate_tickers = set(
        inference_frame.nlargest(candidate_size, "stage1_score")["ticker"].astype(str)
    )
    ticker_key = out["ticker"].astype(str)
    out["two_stage_candidate"] = ticker_key.isin(candidate_tickers).astype(np.int8)
    out["two_stage_score"] = ticker_key.map(score_by_ticker)
    out["two_stage_rank"] = ticker_key.map(rank_by_ticker)
    out["leaderboard_score"] = pd.to_numeric(out["two_stage_score"], errors="coerce").fillna(-np.inf)
    return out


def predict_all(
    top_n: int = 30,
    model_slot: str = "production",
    model_meta_path: str | None = None,
    save_snapshot: bool = True,
    capture_provenance: bool = False,
):
    """對所有股票產生預測，回傳排名。"""
    prediction_run = None
    if capture_provenance:
        from ml.prediction_provenance import capture_run, bind_snapshot, assert_run_unchanged
        prediction_paths = _prediction_model_paths(model_slot, model_meta_path)
        prediction_run = capture_run(model_slot, model_paths=prediction_paths,
                                     context={"top_n": top_n, "explicit_meta_path": model_meta_path,
                                              "environment": {key: os.environ.get(key) for key in (
                                                  "V2_MODEL_META", "ENABLE_ALPHA_CLASSIFIER_RESEARCH",
                                                  "TWO_STAGE_RANKER_ENABLED", "TWO_STAGE_RANKER_META",
                                                  VALIDATED_CHIP_MOMENTUM_GATE_ENV,
                                                  VALIDATED_CHIP_MOMENTUM_TOP_N_ENV,
                                                  VALIDATED_CHIP_MOMENTUM_MIN_OPEN_ENV,
                                                  VALIDATED_CHIP_MOMENTUM_MIN_VOLUME_ENV,
                                                  VALIDATED_CHIP_MOMENTUM_MIN_AMOUNT_ENV)}})
    model, meta = load_selected_model(slot=model_slot, meta_path=model_meta_path)
    feature_cols = meta["feature_columns"]

    # 嘗試載入 v2 迴歸模型
    v2_model, v2_meta = load_v2_model()
    alpha_model, alpha_meta = load_v2_alpha_classifier()
    two_stage_ranker, two_stage_meta = load_two_stage_ranker()
    if prediction_run is not None:
        if _prediction_model_paths(model_slot, model_meta_path) != prediction_paths:
            raise RuntimeError("Prediction model selection changed during load")
        for role, loaded_meta in (("base", meta), ("v2", v2_meta), ("alpha", alpha_meta), ("ranker", two_stage_meta)):
            if loaded_meta is not None and os.path.abspath(loaded_meta["model_file"]) != prediction_paths[role]["model_path"]:
                raise RuntimeError("Loaded model differs from captured input")
        assert_run_unchanged(prediction_run)

    from ml.snapshot_lineage import (
        source_state, clear_source_memo_caches, save_snapshot as save_verified_snapshot,
    )
    snapshot_sources = prediction_run["sources"] if prediction_run is not None else (source_state(include_hashes=True) if save_snapshot else None)
    if save_snapshot or prediction_run is not None:
        clear_source_memo_caches()
    if prediction_run is not None:
        # The output sector lookup is initialized at module import. Refresh its
        # source IO before certifying a run in an already-imported process.
        global _SECTOR_DF, _SECTOR_LOOKUP
        _SECTOR_DF = load_sector_mapping()
        _SECTOR_LOOKUP = {} if _SECTOR_DF is None else dict(zip(_SECTOR_DF["Ticker"], _SECTOR_DF["Sector"]))
    snapshot = build_latest_snapshot(verbose=False)

    snapshot_manifest = None
    if save_snapshot:
        snapshot_manifest = save_verified_snapshot(snapshot, SNAPSHOT_CACHE_PATH, snapshot_sources)
        print(f"Snapshot 已快取: {SNAPSHOT_CACHE_PATH} ({len(snapshot):,} 筆)")
    if prediction_run is not None:
        prediction_run = bind_snapshot(prediction_run, snapshot, snapshot_manifest=snapshot_manifest)

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
        "MA_5",
        "MA_20",
        "MA_60",
        "price_vs_ma5",
        "dist_to_high_20d",
        "dist_to_high_60d",
        "price_vs_ma20",
        "price_vs_ma60",
        "ma_slope_5",
        "ma_slope_20",
        "vol_ratio",
        "return_1d",
        "return_5d",
        "rsi_14",
        "macd_hist",
        "macd_turn_positive",
        "macd_bearish_div",
        "macd_bullish_div",
        "kd_k",
        "kd_d",
        "position_52w",
        *VALIDATED_CHIP_MOMENTUM_RAW_FEATURE_COLS,
    ]
    for col in extra_cols:
        if col in snapshot.columns and col not in keep_cols:
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
        if two_stage_ranker is not None and two_stage_meta is not None:
            pred_df = _apply_two_stage_ranking(
                pred_df,
                X_v2,
                v2_cols,
                two_stage_ranker,
                two_stage_meta,
            )

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

    if alpha_model is not None and alpha_meta is not None:
        alpha_cols = alpha_meta["feature_columns"]
        alpha_snap = _get_zscore_snapshot() if _model_needs_zscore(alpha_meta) else snapshot
        X_alpha = pd.DataFrame(
            {
                col: alpha_snap[col].values if col in alpha_snap.columns else np.full(len(alpha_snap), np.nan)
                for col in alpha_cols
            }
        )
        pred_df["alpha_win_prob_20d"] = alpha_model.predict(X_alpha.values)
        alpha_prob = pd.to_numeric(pred_df["alpha_win_prob_20d"], errors="coerce")
        pred_df["alpha_win_prob_percentile"] = alpha_prob.rank(pct=True, method="average")
        pred_df["alpha_win_prob_rank"] = alpha_prob.rank(
            ascending=False,
            method="min",
        )
        pred_df["alpha_classifier_model_file"] = os.path.basename(alpha_meta["model_file"])
        pred_df["alpha_classifier_threshold"] = float(
            alpha_meta.get("default_gate_threshold", 0.52)
        )

    pred_df["sector"] = pred_df["ticker"].map(_SECTOR_LOOKUP).fillna("其他")
    pred_df = _attach_liquidity_artifacts(pred_df, snapshot)
    pred_df = _attach_beta_artifacts(pred_df)
    pred_df = _attach_penalty_context_artifacts(pred_df, snapshot)
    pred_df = _attach_macro_strategy_artifacts(pred_df)
    try:
        chipk_df, _chipk_meta = load_latest_chipk_main_force()
    except Exception:
        chipk_df = pd.DataFrame()
    pred_df = compute_shortwave_overlay(pred_df, snapshot=snapshot, chipk_df=chipk_df)

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
        "v1_buy_ok",
        "v1_hard_buy_ok",
        "v1_veto",
        "v2_buy_ok",
        "v2_override_buy",
        "dual_track_buy",
        "v2_only_bull",
        "disposition_override",
        "guardrail_blocked",
        "guardrail_loose_mode",
        "guardrail_intercept_rate_top50",
        "guardrail_dual_track_fail_rate_top50",
        "guardrail_monitor_top_n",
        "guardrail_trigger_reason",
        "risk_adjusted_return",
        "leaderboard_score",
        "leaderboard_score_before_two_stage",
        "two_stage_enabled",
        "two_stage_candidate",
        "two_stage_candidate_size",
        "two_stage_label_bins",
        "two_stage_rank",
        "two_stage_score",
        "two_stage_model_file",
        "alpha_win_prob_20d",
        "alpha_win_prob_percentile",
        "alpha_win_prob_rank",
        "alpha_classifier_model_file",
        "alpha_classifier_threshold",
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
        *MACRO_STRATEGY_COLS,
        *SHORTWAVE_OUTPUT_COLS,
        "volume_today",
        "avg_5d_volume",
        "avg_20d_volume",
        "avg_5d_amount",
        "avg_20d_amount",
        "beta_60",
        *PENALTY_CONTEXT_COLS,
    ]:
        if col in pred_df.columns:
            out_cols.append(col)

    pred_df = pred_df[_dedupe_column_order(out_cols)]
    pred_df = _sort_prediction_df(pred_df)
    if prediction_run is not None:
        assert_run_unchanged(prediction_run)
        meta["_prediction_provenance"] = prediction_run
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
    df = apply_validated_chip_momentum_entry_filter(df)
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
    if result.empty:
        return eligible.head(0).copy().reset_index(drop=True)

    # 標記被產業上限遞補的股票
    if replaced_sectors:
        replaced_info = ", ".join(f"{k}(-{v})" for k, v in replaced_sectors.items())
        print(f"  產業集中度調整: {replaced_info}")

    return result.reset_index(drop=True)


def apply_group_cap(
    df: pd.DataFrame,
    top_n: int = 30,
    cap_ratio: float = GROUP_CAP_RATIO,
    initial_df: pd.DataFrame | None = None,
    sector_cap_ratio: float = SECTOR_CAP_RATIO,
) -> pd.DataFrame:
    """Supply-chain/theme group cap layered after sector cap.

    OTHER/unmapped names are intentionally uncapped. When ``initial_df`` is
    provided, it is treated as the sector-capped seed selection; over-cap group
    names are removed from the lowest-ranked rows and filled from the same
    sorted eligible pool while preserving sector and group caps.
    """

    if df.empty:
        out = df.head(0).copy()
        out["group_code"] = pd.Series(dtype=object)
        out["group_name"] = pd.Series(dtype=object)
        out["group_cap_applied"] = pd.Series(dtype=bool)
        return out

    df = apply_validated_chip_momentum_entry_filter(df)
    if initial_df is not None and {"ticker"} <= set(df.columns) and {"ticker"} <= set(initial_df.columns):
        allowed_tickers = set(df["ticker"].astype(str))
        initial_df = initial_df.loc[initial_df["ticker"].astype(str).isin(allowed_tickers)].copy()

    sort_col = _prediction_sort_column(df)
    if sort_col not in df.columns:
        out = annotate_group_columns(df.head(top_n).copy()).reset_index(drop=True)
        out["group_cap_applied"] = False
        return out

    sorted_df = annotate_group_columns(_sort_prediction_df(df).copy()).reset_index(drop=True)
    sorted_df["_group_pool_order"] = np.arange(len(sorted_df), dtype=np.int32)

    buy_recs = ["強力買進", "建議買進"]
    eligible = sorted_df[sorted_df["recommendation"].isin(buy_recs)].copy()
    inst_sell_ratio = _institutional_sell_pressure(eligible)
    eligible = eligible[
        inst_sell_ratio.fillna(0.0) > -(TOP30_EXCLUDE_INST_SELL_PCT / 100.0)
    ].copy()

    if eligible.empty:
        out = annotate_group_columns(df.head(0).copy()).reset_index(drop=True)
        out["group_cap_applied"] = False
        return out

    max_per_group = max(1, int(top_n * cap_ratio))
    max_per_sector = max(1, int(top_n * sector_cap_ratio))
    eligible_by_ticker = eligible.drop_duplicates(subset=["ticker"], keep="first").set_index("ticker")

    if initial_df is None:
        seed = eligible.head(0).copy()
    else:
        seed = annotate_group_columns(initial_df.copy())
        seed = seed.merge(
            eligible[["ticker", "_group_pool_order"]],
            on="ticker",
            how="left",
            suffixes=("", "_eligible"),
        )
        missing_order = seed["_group_pool_order"].isna()
        if missing_order.any():
            seed.loc[missing_order, "_group_pool_order"] = np.arange(
                len(eligible), len(eligible) + int(missing_order.sum())
            )
        seed = seed.sort_values("_group_pool_order").reset_index(drop=True)

    selected: list[pd.Series] = [row for _, row in seed.iterrows()]
    removed_tickers: set[str] = set()
    cap_triggered = False

    while True:
        capped_counts: dict[str, int] = {}
        for row in selected:
            code = row.get("group_code", OTHER_GROUP_CODE)
            if is_capped_group(code):
                capped_counts[str(code)] = capped_counts.get(str(code), 0) + 1
        over_groups = {code for code, count in capped_counts.items() if count > max_per_group}
        if not over_groups:
            break
        cap_triggered = True
        for idx in range(len(selected) - 1, -1, -1):
            row = selected[idx]
            code = str(row.get("group_code", OTHER_GROUP_CODE))
            if code in over_groups:
                removed_tickers.add(str(row.get("ticker")))
                selected.pop(idx)
                break

    selected_tickers = {str(row.get("ticker")) for row in selected}

    def _counts(rows: list[pd.Series]) -> tuple[dict[str, int], dict[str, int]]:
        sector_count: dict[str, int] = {}
        group_count: dict[str, int] = {}
        for item in rows:
            sector = str(item.get("sector", item.get("Sector", "其他")))
            sector_count[sector] = sector_count.get(sector, 0) + 1
            code = str(item.get("group_code", OTHER_GROUP_CODE))
            if is_capped_group(code):
                group_count[code] = group_count.get(code, 0) + 1
        return sector_count, group_count

    sector_count, group_count = _counts(selected)
    replacement_tickers: set[str] = set()
    for _, candidate in eligible.iterrows():
        ticker = str(candidate.get("ticker"))
        if ticker in selected_tickers:
            continue
        sector = str(candidate.get("sector", candidate.get("Sector", "其他")))
        if sector_count.get(sector, 0) >= max_per_sector:
            continue
        code = str(candidate.get("group_code", OTHER_GROUP_CODE))
        if is_capped_group(code) and group_count.get(code, 0) >= max_per_group:
            continue
        selected.append(candidate)
        selected_tickers.add(ticker)
        replacement_tickers.add(ticker)
        sector_count[sector] = sector_count.get(sector, 0) + 1
        if is_capped_group(code):
            group_count[code] = group_count.get(code, 0) + 1
        if len(selected) >= top_n:
            break

    if not selected:
        out = eligible.head(0).copy()
    else:
        out = pd.DataFrame([row.to_dict() for row in selected]).sort_values("_group_pool_order")
    out = out.drop(columns=[col for col in ["_group_pool_order"] if col in out.columns])
    out["group_cap_applied"] = out["ticker"].astype(str).isin(replacement_tickers)

    if cap_triggered:
        removed_count = len(removed_tickers)
        replacement_count = len(replacement_tickers)
        print(f"  族群集中度調整: removed={removed_count}, replacements={replacement_count}")

    return out.head(top_n).reset_index(drop=True)


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
        capture_provenance=True,
    )
    prediction_run = meta.pop("_prediction_provenance")

    print(f"\nbase 模型: {os.path.basename(meta['model_file'])}")
    print(f"slot: {meta.get('model_slot', model_slot)} ({meta.get('model_label', get_slot_label(model_slot))})")
    has_v2 = "pred_return_20d" in pred_df.columns

    if has_v2:
        print("v2 模型: 已載入 (20天迴歸)")

    # 信號分佈
    if has_v2:
        rec_counts = pred_df["recommendation"].value_counts()
        print(f"\n建議分佈: {dict(rec_counts)}")
        if "guardrail_loose_mode" in pred_df.columns and int(pred_df["guardrail_loose_mode"].iloc[0]) == 1:
            intercept_rate = float(pred_df["guardrail_intercept_rate_top50"].iloc[0])
            dual_track_rate = float(pred_df["guardrail_dual_track_fail_rate_top50"].iloc[0])
            reason = str(pred_df["guardrail_trigger_reason"].iloc[0] or "")
            print(
                "WARN Guardrail 熔斷已啟動: "
                f"Top50 攔截率={intercept_rate:.1%}, "
                f"雙軌分歧率={dual_track_rate:.1%}"
            )
            if reason:
                print(f"   觸發原因: {reason}")

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
    from ml.prediction_provenance import publish_prediction_csv
    publish_prediction_csv(pred_df, out_path, prediction_run)
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
