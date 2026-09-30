"""Build a unified daily signal artifact from 20D and T+1 predictions.

This script is the single source of truth for the next refactor stage:
    1. Read the exact 20D production prediction CSV for a date.
    2. Reconstruct the same 20D Top-30 candidate pool used by paper_portfolio.
    3. Read the exact T+1 prediction CSV for the same date unless disabled for research backfills.
    4. Keep only T+1 rows with selected_for_trade=True.
    5. Outer-join on ticker and assign a MECE signal_type:
       Dual / 20D_only / T1_only.
    6. Apply tradability gates, including disposition, liquidity floors, and overheat risk.
    7. Map signal_type to default target_units: 3 / 2 / 1 / 0.
    8. Cap high-valuation momentum target weights.
    9. Write unified_signals_YYYY-MM-DD.csv atomically.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from functools import lru_cache

import numpy as np
import pandas as pd
import yaml

BASE_DIR = os.environ.get("STOCK_BASE_DIR") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from ml.config import DAILY_K_DIR, MODEL_DIR, REPORT_DIR
from backend.services.macro_event_service import build_macro_strategy_context, macro_stock_adjustment
from ml.futures_settlement import nearest_monthly_settlement_window
from ml.market_regime import (
    ACTION_BLOCK,
    ACTION_LIMIT,
    STATE_CAUTION,
    STATE_CLOSED,
    evaluate_market_regime,
)
from ml.features.group import annotate_group_columns
from ml.predict import (
    VALIDATED_CHIP_MOMENTUM_COMPONENTS,
    VALIDATED_CHIP_MOMENTUM_DIAGNOSTIC_COLS,
    apply_group_cap,
    apply_sector_cap,
    apply_validated_chip_momentum_entry_filter,
)
from ml.entry_shortwave import SHORTWAVE_OUTPUT_COLS
from ml.predict_t1 import sort_t1_prediction_df
from ml.thresholds import (
    EXPENSIVE_MOMENTUM_MA60_THRESHOLD,
    EXPENSIVE_MOMENTUM_PE_THRESHOLD,
    EXPENSIVE_MOMENTUM_WEIGHT_CAP,
    ENTRY_MA5_MIN_PCT,
    OVERHEAT_DEFAULT_THRESHOLD,
)
from ml.t1_production_gate import (
    combine_block_reasons,
    d20_hard_risk_reason,
    evaluate_t1_production_gate,
    t1_hard_risk_reason,
)
from ml.universe import filter_out_etfs_df
from scripts.taiwan_trading_calendar import next_taiwan_trading_day
from scripts.tquant_reference_sleeves import (
    DEFAULT_DB_PATH as TQUANT_REFERENCE_DB_PATH,
    _annotate_cross_section_ranks as _tquant_annotate_cross_section_ranks,
    _annotate_votes as _tquant_annotate_votes,
    _latest_revenue_features as _tquant_latest_revenue_features,
    _load_price_history as _tquant_load_price_history,
    _point_in_time_features as _tquant_point_in_time_features,
)
from scripts.public_market_context_quality import (
    compact_public_market_context_columns,
    evaluate_public_market_context,
)

UNIFIED_OUTPUT_PREFIX = "unified_signals"
DEFAULT_TOP_N_20D = 30
SHORTWAVE_20D_COLUMNS = [f"{column}_20d" for column in SHORTWAVE_OUTPUT_COLS]
SHORTWAVE_NUMERIC_COLUMNS = {
    "shortwave_score",
    "shortwave_score_pre_chipk",
    "shortwave_preferred_hold_days",
    "shortwave_entry_zone_low",
    "shortwave_entry_zone_high",
    "shortwave_friend_combo_score",
    "shortwave_friend_combo_match",
    "shortwave_model_combo_support",
    "shortwave_invalid_level",
    "shortwave_ma_score",
    "shortwave_volume_score",
    "shortwave_momentum_score",
    "shortwave_chipk_score",
    "shortwave_chipk_used_in_production",
    "leaderboard_score_before_shortwave",
    "chipk_main_force_1d",
    "chipk_main_force_5d",
    "chipk_main_force_20d",
    "chipk_main_force_score",
    "tactical_score",
    "tactical_entry_limit",
    "tactical_entry_zone_low",
    "tactical_entry_zone_high",
    "tactical_stop_loss",
    "tactical_take_profit_2p",
    "tactical_take_profit_4p",
    "tactical_max_hold_days",
    "swing_score",
    "swing_entry_zone_low",
    "swing_entry_zone_high",
    "swing_preferred_hold_days",
}
SHORTWAVE_BOOL_COLUMNS = {
    "shortwave_rerank_enabled",
    "chipk_history_available",
}


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


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _coerce_bool_series(value: pd.Series | object, index: pd.Index, default: bool = False) -> pd.Series:
    if not isinstance(value, pd.Series):
        value = pd.Series(default, index=index)
    out = value.reindex(index).copy()
    if out.dtype == bool:
        return out.fillna(default).astype(bool)
    text = out.astype(str).str.strip().str.lower()
    truthy = text.isin({"1", "true", "yes", "y", "on"})
    falsy = text.isin({"0", "false", "no", "n", "off", "", "nan", "none", "<na>"})
    result = pd.Series(default, index=index, dtype=bool)
    result.loc[truthy] = True
    result.loc[falsy] = False
    return result

SIGNAL_TYPE_DUAL = "Dual"
SIGNAL_TYPE_20D_ONLY = "20D_only"
SIGNAL_TYPE_T1_ONLY = "T1_only"
SIGNAL_TYPE_NONE = "NONE"

TARGET_UNITS_MAP = {
    SIGNAL_TYPE_DUAL: 3,
    SIGNAL_TYPE_20D_ONLY: 2,
    SIGNAL_TYPE_T1_ONLY: 1,
    SIGNAL_TYPE_NONE: 0,
}

ROUTE_PRIORITY_MAP = {
    SIGNAL_TYPE_DUAL: 3,
    SIGNAL_TYPE_20D_ONLY: 2,
    SIGNAL_TYPE_T1_ONLY: 1,
    SIGNAL_TYPE_NONE: 0,
}

PRED20_RE = re.compile(r"^predictions_(\d{4}-\d{2}-\d{2})\.csv$")
PRED20_EXPLICIT_RE = re.compile(
    r"^predictions(?!_t1_)(?:_[A-Za-z0-9][A-Za-z0-9_-]*)?_(\d{4}-\d{2}-\d{2})\.csv$"
)
PRED_T1_RE = re.compile(r"^predictions_t1_(\d{4}-\d{2}-\d{2})\.csv$")
DISPOSITION_PERIODS_PATH = os.path.join(BASE_DIR, "ml", "data", "disposition_periods.csv")
SPECIAL_STATUS_REPORT_PATH = os.path.join(REPORT_DIR, "special_stock_status_latest.json")
TRADABILITY_STATUS_OPEN = "OPEN"
TRADABILITY_STATUS_BLOCKED = "BLOCKED"
TRADABILITY_REASON_DISPOSITION = "DISPOSITION_PERIOD"
TRADABILITY_REASON_LOW_LIQUIDITY = "LOW_LIQUIDITY"
TRADABILITY_REASON_T1_LIQUIDITY_BLOCKED = "T1_LIQUIDITY_BLOCKED"
TRADABILITY_REASON_OVERHEAT_RISK = "OVERHEAT_RISK"
TRADABILITY_REASON_HIGH_BETA_RISK = "HIGH_BETA_RISK"
TRADABILITY_REASON_EXPENSIVE_MOMENTUM_RISK = "EXPENSIVE_MOMENTUM_RISK"
TRADABILITY_REASON_MISSING_RISK_CONTEXT = "MISSING_RISK_CONTEXT"
TRADABILITY_REASON_MARKET_REGIME_CAUTION = "MARKET_REGIME_CAUTION"
TRADABILITY_REASON_MARKET_REGIME_CLOSED = "MARKET_REGIME_CLOSED"
TRADABILITY_REASON_FUTURES_SETTLEMENT_WINDOW = "FUTURES_SETTLEMENT_WINDOW"
TRADABILITY_REASON_MACRO_EVENT_RISK = "MACRO_EVENT_RISK"
TRADABILITY_REASON_LOW_ALPHA_WIN_PROB = "LOW_ALPHA_WIN_PROB"
TRADABILITY_REASON_ENTRY_MA5_WEAK = "ENTRY_MA5_WEAK"
TRADABILITY_REASON_MISSING_ENTRY_MA5_CONTEXT = "MISSING_ENTRY_MA5_CONTEXT"
TRADABILITY_REASON_ENTRY_BENCHMARK_GUARD = "ENTRY_BENCHMARK_GUARD"
TRADABILITY_REASON_TQUANT_REFERENCE_GATE = "TQUANT_REFERENCE_GATE"
D20_MIN_AVG_20D_VOLUME = 500.0
D20_MIN_AVG_20D_AMOUNT = 20_000_000.0
T1_MIN_AVG_5D_VOLUME = 1000.0
T1_MIN_AVG_5D_AMOUNT = 30_000_000.0
MAX_PRICE_VS_MA60 = OVERHEAT_DEFAULT_THRESHOLD  # Default/fallback Champion unified OVERHEAT_RISK gate.
MAX_BETA_60 = 1.80
SECTOR_THRESHOLDS_PATH = os.environ.get(
    "SECTOR_THRESHOLDS_PATH",
    os.path.join(BASE_DIR, "config", "sector_thresholds.yaml"),
)
SECTOR_OVERHEAT_THRESHOLDS_ENV = "SECTOR_OVERHEAT_THRESHOLDS_ENABLED"
ENTRY_MA5_GUARD_ENV = "ENTRY_MA5_GUARD_ENABLED"
ENTRY_BENCHMARK_GUARD_ENV = "ENTRY_BENCHMARK_GUARD_ENABLED"
ENTRY_BENCHMARK_GUARD_VERSION = "v1_prob_edge_or_strong_consensus"
TQUANT_REFERENCE_GATE_ENV = "TQUANT_REFERENCE_GATE_ENABLED"
TQUANT_REFERENCE_GATE_VERSION = "v2_tej_factor_validated_consensus_gate"
TQUANT_REFERENCE_GATE_MIN_CONSENSUS_ENV = "TQUANT_REFERENCE_GATE_MIN_CONSENSUS"
PUBLIC_MARKET_CONTEXT_COLUMNS = [
    "public_market_context_status",
    "public_market_context_generated_at",
    "public_market_context_preflight_pass",
    "public_market_context_usage_mode",
    "public_market_context_time_basis",
    "public_market_context_model_feature_readiness",
    "public_market_context_model_feature_gap",
    "public_market_context_stale_count",
    "public_market_context_missing_field_count",
    "public_market_context_required_failures",
    "public_market_context_stale_datasets",
    "public_market_context_missing_field_datasets",
]
MACRO_STRATEGY_COLUMNS = [
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
    "macro_event_weight_multiplier",
    "target_weight_ratio_before_macro_event_cap",
]
SELECTION_MODEL_DIAGNOSTIC_COLUMNS = [
    "selection_model_primary_layer",
    "selection_model_alignment",
    "selection_model_weak_ml_edge_under_tquant",
    "selection_model_data_gap",
    "selection_model_reason",
]
VALUATION_DIR = os.path.join(BASE_DIR, "\u4f30\u503c\u8cc7\u6599")
CAUTION_MAX_RANK_20D = 10
ALPHA_WIN_GATE_MODE = os.environ.get("ALPHA_WIN_GATE_MODE", "disabled").strip().lower()
ALPHA_WIN_PROB_MIN = _env_float("ALPHA_WIN_PROB_MIN", 0.52)
ALPHA_WIN_PROB_PERCENTILE_MIN = _env_float("ALPHA_WIN_PROB_PERCENTILE_MIN", 0.90)
ALPHA_WIN_PROB_TOP_N = _env_int("ALPHA_WIN_PROB_TOP_N", 10)
ENTRY_BENCHMARK_PROB_EDGE_FLOOR = _env_float("ENTRY_BENCHMARK_PROB_EDGE_FLOOR", -0.10)
ENTRY_BENCHMARK_STRONG_VOTE_MIN = _env_int("ENTRY_BENCHMARK_STRONG_VOTE_MIN", 5)
FUTURES_SETTLEMENT_BASE_MULTIPLIER = _env_float("FUTURES_SETTLEMENT_BASE_MULTIPLIER", 0.50)
FUTURES_SETTLEMENT_HIGH_BETA_MULTIPLIER = _env_float("FUTURES_SETTLEMENT_HIGH_BETA_MULTIPLIER", 0.25)
FUTURES_SETTLEMENT_HIGH_BETA_MIN = _env_float("FUTURES_SETTLEMENT_HIGH_BETA_MIN", 1.25)
PENALTY_OVERLAY_DEFAULT_VERSION = "v1"
PENALTY_OVERLAY_VERSIONS = {"v1", "tuned"}
PENALTY_OVERLAY_REQUIRED_COLS = [
    "pred_return_20d",
    "beta_60",
    "gap_pct",
    "gap_up_avg_5d",
    "foreign_cumsum_20d_raw",
    "foreign_cumsum_20d_norm_raw",
    "roa_annualized",
]
PENALTY_OVERLAY_TUNED_REQUIRED_COLS = ["roe_annualized"]

GUARDRAIL_RECOMMENDATION_REASON_MAP = {
    "流動性不足": "LIQUIDITY_GUARD",
    "處置股": "DISPOSITION_PERIOD",
    "異常暴跌": "CRASH_GUARD",
    "預測值異常": "EXTREME_PREDICTION_GUARD",
    "籌碼頂部背離": "CHIP_DIVERGENCE_GUARD",
    "基本面警示": "FUNDAMENTAL_GUARD",
}
GUARDRAIL_FALLBACK_REASON = "NO_BUY_SIGNAL_OR_LOW_EDGE"


@dataclass
class UnifiedBuildResult:
    output_path: str
    prediction_date: str
    total_names: int
    dual_count: int
    d20_only_count: int
    t1_only_count: int
    total_units: int
    production_gate_status: str
    production_gate_reason: str
    downgraded_dual_count: int
    tradability_blocked_count: int


def _list_prediction_files(pattern: re.Pattern[str]) -> list[str]:
    candidates = []
    for name in os.listdir(MODEL_DIR):
        if pattern.match(name):
            candidates.append(os.path.join(MODEL_DIR, name))
    return sorted(candidates)


def _extract_date_from_filename(path: str, pattern: re.Pattern[str]) -> str:
    match = pattern.match(os.path.basename(path))
    if not match:
        raise ValueError(f"Cannot infer date from filename: {os.path.basename(path)}")
    return match.group(1)


def _resolve_prediction_20d_path(prediction_path: str | None, pred_date: str | None) -> str:
    if prediction_path:
        resolved = os.path.abspath(prediction_path)
        if not PRED20_EXPLICIT_RE.match(os.path.basename(resolved)):
            raise ValueError(f"Not a 20D prediction file: {resolved}")
        actual_date = _extract_date_from_filename(resolved, PRED20_EXPLICIT_RE)
        if pred_date and actual_date != pred_date:
            raise ValueError(f"20D path date mismatch: expected {pred_date}, got {actual_date}")
        return resolved

    files = _list_prediction_files(PRED20_RE)
    if not files:
        raise FileNotFoundError("No predictions_YYYY-MM-DD.csv files found.")

    if pred_date is None:
        return files[-1]

    target = os.path.join(MODEL_DIR, f"predictions_{pred_date}.csv")
    if not os.path.exists(target):
        raise FileNotFoundError(f"20D prediction for {pred_date} not found at {target}")
    return target


def _resolve_prediction_t1_path(prediction_path: str | None, pred_date: str | None) -> str:
    if prediction_path:
        resolved = os.path.abspath(prediction_path)
        if not PRED_T1_RE.match(os.path.basename(resolved)):
            raise ValueError(f"Not a T+1 prediction file: {resolved}")
        actual_date = _extract_date_from_filename(resolved, PRED_T1_RE)
        if pred_date and actual_date != pred_date:
            raise ValueError(f"T+1 path date mismatch: expected {pred_date}, got {actual_date}")
        return resolved

    files = _list_prediction_files(PRED_T1_RE)
    if not files:
        raise FileNotFoundError("No predictions_t1_YYYY-MM-DD.csv files found.")

    if pred_date is None:
        return files[-1]

    target = os.path.join(MODEL_DIR, f"predictions_t1_{pred_date}.csv")
    if not os.path.exists(target):
        raise FileNotFoundError(f"T+1 prediction for {pred_date} not found at {target}")
    return target


def _load_csv(path: str, expected_date: str | None = None, label: str = "prediction") -> pd.DataFrame:
    df = pd.read_csv(path, encoding="utf-8-sig", dtype={"ticker": str})
    before = len(df)
    df = filter_out_etfs_df(df, "ticker")
    dropped = before - len(df)
    if dropped:
        print(f"[unified] dropped {dropped} ETF rows from {label} source")
    if expected_date is None or "date" not in df.columns:
        return df

    date_series = df["date"].astype(str)
    filtered = df.loc[date_series == expected_date].copy()
    if filtered.empty:
        raise ValueError(
            f"{label} file {os.path.basename(path)} contains no rows for expected date {expected_date}"
        )
    return filtered


def _coalesce_columns(df: pd.DataFrame, preferred: str, fallback: str) -> pd.Series:
    preferred_series = (
        df[preferred] if preferred in df.columns else pd.Series(pd.NA, index=df.index, dtype="object")
    )
    fallback_series = (
        df[fallback] if fallback in df.columns else pd.Series(pd.NA, index=df.index, dtype="object")
    )
    return preferred_series.where(preferred_series.notna(), fallback_series)


def _coalesce_many(df: pd.DataFrame, *columns: str) -> pd.Series:
    out = pd.Series(pd.NA, index=df.index, dtype="object")
    for column in columns:
        if column not in df.columns:
            continue
        series = df[column]
        out = out.where(out.notna(), series)
    return out


def _numeric_series(df: pd.DataFrame, column: str) -> pd.Series:
    if column in df.columns:
        return pd.to_numeric(df[column], errors="coerce")
    return pd.Series(np.nan, index=df.index, dtype="float64")


def _shortwave_20d_payload(df: pd.DataFrame) -> dict[str, pd.Series]:
    payload: dict[str, pd.Series] = {}
    for column in SHORTWAVE_OUTPUT_COLS:
        series = (
            df[column]
            if column in df.columns
            else pd.Series(pd.NA, index=df.index, dtype="object")
        )
        output_column = f"{column}_20d"
        if column in SHORTWAVE_NUMERIC_COLUMNS:
            payload[output_column] = pd.to_numeric(series, errors="coerce")
        elif column in SHORTWAVE_BOOL_COLUMNS:
            payload[output_column] = series.where(series.notna(), False).astype(bool)
        else:
            payload[output_column] = series
    return payload


def _infer_guardrail_reason_from_recommendation(value: object) -> str:
    text = "" if pd.isna(value) else str(value)
    for keyword, reason in GUARDRAIL_RECOMMENDATION_REASON_MAP.items():
        if keyword in text:
            return reason
    if text.startswith("觀望"):
        return GUARDRAIL_FALLBACK_REASON
    return ""


def _normalize_guardrail_reason_series(
    blocked: pd.Series,
    reason: pd.Series | None,
    recommendation: pd.Series | None = None,
) -> pd.Series:
    blocked_bool = pd.to_numeric(blocked, errors="coerce").fillna(0).astype(bool)
    if reason is None:
        out = pd.Series("", index=blocked.index, dtype="object")
    else:
        out = reason.reindex(blocked.index).fillna("").astype(str)
        out = out.mask(out.str.lower().isin({"nan", "none", "null"}), "")
    missing = blocked_bool & out.str.strip().eq("")
    if missing.any() and recommendation is not None:
        inferred = recommendation.reindex(blocked.index).map(_infer_guardrail_reason_from_recommendation)
        out = out.where(~missing, inferred.fillna(""))
        missing = blocked_bool & out.str.strip().eq("")
    out = out.where(~missing, GUARDRAIL_FALLBACK_REASON)
    return out


@lru_cache(maxsize=4096)
def _price_vs_ma5_context(ticker: str, prediction_date: str) -> float:
    path = os.path.join(DAILY_K_DIR, f"{str(ticker).zfill(4)}.csv")
    if not os.path.exists(path):
        return float("nan")
    try:
        prices = pd.read_csv(path, dtype={"Date": str})
    except Exception:
        return float("nan")
    if "Date" not in prices.columns or "Close" not in prices.columns:
        return float("nan")
    prices["Date"] = pd.to_datetime(prices["Date"], errors="coerce")
    prices["Close"] = pd.to_numeric(prices["Close"], errors="coerce")
    prices = prices.dropna(subset=["Date"]).sort_values("Date").reset_index(drop=True)
    if prices.empty:
        return float("nan")
    if "MA_5" in prices.columns:
        prices["ma5_for_entry_gate"] = pd.to_numeric(prices["MA_5"], errors="coerce")
        prices["ma5_for_entry_gate"] = prices["ma5_for_entry_gate"].fillna(
            prices["Close"].rolling(window=5).mean()
        )
    else:
        prices["ma5_for_entry_gate"] = prices["Close"].rolling(window=5).mean()
    rows = prices.loc[prices["Date"] <= pd.Timestamp(prediction_date)]
    if rows.empty:
        return float("nan")
    latest = rows.iloc[-1]
    close = latest.get("Close")
    ma5 = latest.get("ma5_for_entry_gate")
    if pd.isna(close) or pd.isna(ma5) or float(ma5) == 0:
        return float("nan")
    return float(close) / float(ma5) - 1.0


def _entry_price_vs_ma5_series(df: pd.DataFrame) -> pd.Series:
    if "price_vs_ma5" in df.columns:
        series = pd.to_numeric(df["price_vs_ma5"], errors="coerce")
        if not series.isna().any() or not {"ticker", "date"} <= set(df.columns):
            return series
        fallback = pd.Series(
            [
                _price_vs_ma5_context(str(ticker), str(prediction_date))
                for ticker, prediction_date in zip(df["ticker"], df["date"], strict=False)
            ],
            index=df.index,
            dtype="float64",
        )
        return series.where(series.notna(), fallback)
    if not {"ticker", "date"} <= set(df.columns):
        return pd.Series(np.nan, index=df.index, dtype="float64")
    values = [
        _price_vs_ma5_context(str(ticker), str(prediction_date))
        for ticker, prediction_date in zip(df["ticker"], df["date"], strict=False)
    ]
    return pd.Series(values, index=df.index, dtype="float64")


@lru_cache(maxsize=8)
def _load_sector_threshold_config(path: str = SECTOR_THRESHOLDS_PATH) -> dict[str, object]:
    default_threshold = float(OVERHEAT_DEFAULT_THRESHOLD)
    if not os.path.exists(path):
        return {"default_threshold": default_threshold, "groups": {}}

    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    configured_default = raw.get("default_threshold", default_threshold)
    try:
        default_threshold = float(configured_default)
    except (TypeError, ValueError):
        default_threshold = float(OVERHEAT_DEFAULT_THRESHOLD)
    if not np.isfinite(default_threshold) or default_threshold <= 0:
        default_threshold = float(OVERHEAT_DEFAULT_THRESHOLD)

    groups = raw.get("groups") or {}
    if not isinstance(groups, dict):
        groups = {}

    high_risk_groups = {"petrochemical", "steel_metal", "shipping"}
    normalized: dict[str, dict[str, object]] = {}
    for name, cfg in groups.items():
        if not isinstance(cfg, dict):
            continue
        try:
            threshold = float(cfg.get("threshold", default_threshold))
        except (TypeError, ValueError):
            threshold = default_threshold
        if not np.isfinite(threshold) or threshold <= 0:
            threshold = default_threshold
        if str(name) in high_risk_groups and threshold > default_threshold:
            raise ValueError(
                f"High-risk sector group {name} cannot loosen above default {default_threshold:.2f}."
            )
        keywords = [
            str(keyword).strip()
            for keyword in (cfg.get("keywords") or [])
            if str(keyword).strip()
        ]
        normalized[str(name)] = {
            "label": str(cfg.get("label") or name),
            "threshold": threshold,
            "keywords": keywords,
        }

    return {"default_threshold": default_threshold, "groups": normalized}


def _sector_overheat_context(sector_values: pd.Series) -> pd.DataFrame:
    config = _load_sector_threshold_config()
    default_threshold = float(config.get("default_threshold", OVERHEAT_DEFAULT_THRESHOLD))
    groups = config.get("groups") if isinstance(config.get("groups"), dict) else {}
    rows: list[dict[str, object]] = []

    for raw_sector in sector_values.fillna("").astype(str):
        sector_text = raw_sector.strip()
        threshold = default_threshold
        group_name = "fallback"
        group_label = "未分類 / 其他"
        for name, cfg in groups.items():
            keywords = cfg.get("keywords") or []
            if any(str(keyword) in sector_text for keyword in keywords):
                threshold = float(cfg.get("threshold", default_threshold))
                group_name = str(name)
                group_label = str(cfg.get("label") or name)
                break
        rows.append(
            {
                "overheat_threshold": threshold,
                "overheat_threshold_group": group_name,
                "overheat_threshold_label": group_label,
            }
        )

    return pd.DataFrame(rows, index=sector_values.index)


def _sector_overheat_thresholds_enabled() -> bool:
    value = os.environ.get(SECTOR_OVERHEAT_THRESHOLDS_ENV, "").strip().lower()
    return value in {"1", "true", "yes", "on", "shadow"}


def _entry_ma5_guard_enabled() -> bool:
    value = os.environ.get(ENTRY_MA5_GUARD_ENV, "").strip().lower()
    return value in {"1", "true", "yes", "on", "shadow"}


def _entry_benchmark_guard_enabled() -> bool:
    return _env_bool(ENTRY_BENCHMARK_GUARD_ENV, default=True)


def _tquant_reference_gate_enabled() -> bool:
    return _env_bool(TQUANT_REFERENCE_GATE_ENV, default=True)


def _tquant_reference_gate_min_consensus() -> str:
    raw = os.environ.get(TQUANT_REFERENCE_GATE_MIN_CONSENSUS_ENV, "strong").strip().lower()
    return "strong" if raw == "strong" else "watch"


def _default_overheat_context(sector_values: pd.Series) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "overheat_threshold": [OVERHEAT_DEFAULT_THRESHOLD] * len(sector_values),
            "overheat_threshold_group": ["production_default"] * len(sector_values),
            "overheat_threshold_label": ["Production default"] * len(sector_values),
        },
        index=sector_values.index,
    )


def _next_business_day(value: str) -> str:
    ts = pd.Timestamp(value)
    return next_taiwan_trading_day(ts.date()).isoformat()


def _load_special_status_report() -> dict[str, object]:
    if not os.path.exists(SPECIAL_STATUS_REPORT_PATH):
        return {
            "status": "MISSING",
            "gate_action": "CLOSED_FOR_T1_AND_DUAL",
            "error_message": "special_stock_status_latest.json missing",
        }
    try:
        with open(SPECIAL_STATUS_REPORT_PATH, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception as exc:
        return {
            "status": "FAILED",
            "gate_action": "CLOSED_FOR_T1_AND_DUAL",
            "error_message": str(exc),
        }


def _load_disposition_periods(path: str = DISPOSITION_PERIODS_PATH) -> pd.DataFrame:
    if not os.path.exists(path):
        return pd.DataFrame(columns=["stock_id", "period_start", "period_end"])
    df = pd.read_csv(path, encoding="utf-8-sig", dtype={"stock_id": str})
    required = {"stock_id", "period_start", "period_end"}
    missing = sorted(required - set(df.columns))
    if missing:
        raise ValueError(f"Disposition periods missing columns: {', '.join(missing)}")
    out = df.loc[:, ["stock_id", "period_start", "period_end"]].copy()
    out["stock_id"] = out["stock_id"].astype(str).str.strip()
    out["period_start"] = pd.to_datetime(out["period_start"], errors="coerce")
    out["period_end"] = pd.to_datetime(out["period_end"], errors="coerce")
    out = out.dropna(subset=["stock_id", "period_start", "period_end"])
    out = out[out["stock_id"].ne("")]
    return out


def _disposition_set_for_entry_date(entry_date: str) -> set[str]:
    periods = _load_disposition_periods()
    if periods.empty:
        return set()
    check_date = pd.Timestamp(entry_date)
    active = periods[
        (periods["period_start"] <= check_date)
        & (periods["period_end"] >= check_date)
    ]
    return set(active["stock_id"].astype(str).str.strip())


def _selected_for_trade_mask(series: pd.Series) -> pd.Series:
    if pd.api.types.is_bool_dtype(series):
        return series.fillna(False).astype(bool)
    text = series.fillna(False).astype(str).str.strip().str.lower()
    return text.isin({"1", "true", "yes"})


def _alpha_win_gate_mode() -> str:
    mode = ALPHA_WIN_GATE_MODE
    if mode in {"off", "none", "disabled", "disable", "false", "0"}:
        return "disabled"
    if mode in {"percentile", "pct", "pr", "pr90"}:
        return "percentile"
    if mode in {"top_n", "topn", "rank"}:
        return "top_n"
    return "absolute"


def _alpha_win_gate_threshold() -> float:
    mode = _alpha_win_gate_mode()
    if mode == "percentile":
        return ALPHA_WIN_PROB_PERCENTILE_MIN
    if mode == "top_n":
        return float(ALPHA_WIN_PROB_TOP_N)
    if mode == "disabled":
        return np.nan
    return ALPHA_WIN_PROB_MIN


def _ensure_alpha_win_rank_columns(
    df: pd.DataFrame,
    prob_col: str,
    percentile_col: str,
    rank_col: str,
) -> pd.DataFrame:
    if prob_col not in df.columns:
        return df

    out = df.copy()
    alpha_prob = pd.to_numeric(out[prob_col], errors="coerce")
    if percentile_col not in out.columns:
        out[percentile_col] = alpha_prob.rank(pct=True, method="average")
    if rank_col not in out.columns:
        out[rank_col] = alpha_prob.rank(ascending=False, method="min")
    return out


def _alpha_win_gate_pass_mask(
    df: pd.DataFrame,
    prob_col: str,
    percentile_col: str,
    rank_col: str,
) -> pd.Series:
    idx = df.index
    mode = _alpha_win_gate_mode()
    if mode == "disabled":
        return pd.Series(True, index=idx)
    if prob_col not in df.columns:
        return pd.Series(True, index=idx)

    enriched = _ensure_alpha_win_rank_columns(df, prob_col, percentile_col, rank_col)
    if mode == "percentile":
        percentile = pd.to_numeric(enriched.get(percentile_col), errors="coerce")
        return percentile.notna() & percentile.ge(ALPHA_WIN_PROB_PERCENTILE_MIN)
    if mode == "top_n":
        rank = pd.to_numeric(enriched.get(rank_col), errors="coerce")
        return rank.notna() & rank.le(ALPHA_WIN_PROB_TOP_N)

    alpha_prob = pd.to_numeric(enriched.get(prob_col), errors="coerce")
    return alpha_prob.notna() & alpha_prob.ge(ALPHA_WIN_PROB_MIN)


def _filter_20d_prediction_pool_by_alpha_gate(pred20_df: pd.DataFrame) -> pd.DataFrame:
    if pred20_df.empty or "alpha_win_prob_20d" not in pred20_df.columns:
        return pred20_df

    pred20_df = _ensure_alpha_win_rank_columns(
        pred20_df,
        prob_col="alpha_win_prob_20d",
        percentile_col="alpha_win_prob_percentile",
        rank_col="alpha_win_prob_rank",
    )
    keep = _alpha_win_gate_pass_mask(
        pred20_df,
        prob_col="alpha_win_prob_20d",
        percentile_col="alpha_win_prob_percentile",
        rank_col="alpha_win_prob_rank",
    )
    return pred20_df.loc[keep].copy()


def _load_valuation_pe_ratios(prediction_date: str) -> dict[str, float]:
    """Load PE ratios for legacy prediction artifacts missing `pe_ratio`.

    New prediction artifacts include `pe_ratio` directly. This fallback keeps
    same-date unified rebuilds auditable for artifacts produced before REQ-002
    added valuation context to `ml/predict.py`.
    """
    date_key = str(prediction_date).replace("-", "")
    path = os.path.join(VALUATION_DIR, f"valuation_{date_key}.csv")
    if not os.path.exists(path):
        return {}
    try:
        df = pd.read_csv(path, encoding="utf-8-sig", dtype={"Ticker": str})
    except Exception:
        return {}
    if not {"Ticker", "PE_Ratio"}.issubset(df.columns):
        return {}
    tickers = df["Ticker"].astype(str).str.strip().str.upper()
    pe = pd.to_numeric(df["PE_Ratio"], errors="coerce")
    return {
        ticker: float(value)
        for ticker, value in zip(tickers, pe)
        if ticker and pd.notna(value) and np.isfinite(float(value))
    }


def _attach_pe_ratio_context(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        if "pe_ratio" not in df.columns:
            df["pe_ratio"] = pd.Series(dtype="float64")
        return df

    out = df.copy()
    if "pe_ratio" in out.columns and pd.to_numeric(out["pe_ratio"], errors="coerce").notna().any():
        out["pe_ratio"] = pd.to_numeric(out["pe_ratio"], errors="coerce")
        return out

    out["pe_ratio"] = np.nan
    if "date" not in out.columns or "ticker" not in out.columns:
        return out
    dates = out["date"].dropna().astype(str).unique().tolist()
    if len(dates) != 1:
        return out
    pe_map = _load_valuation_pe_ratios(dates[0])
    if not pe_map:
        return out
    out["pe_ratio"] = out["ticker"].astype(str).str.strip().str.upper().map(pe_map)
    return out


def _overlay_reason(row: pd.Series) -> str:
    reasons: list[str] = []
    if float(row.get("penalty_foreign_selling", 0.0) or 0.0) > 0:
        reasons.append("foreign_selling")
    if float(row.get("penalty_gap_risk", 0.0) or 0.0) > 0:
        reasons.append("gap_risk")
    if float(row.get("penalty_quality", 0.0) or 0.0) > 0:
        reasons.append("quality")
    if float(row.get("penalty_beta", 0.0) or 0.0) > 0:
        reasons.append("beta")
    if bool(row.get("penalty_quality_cap_applied", False)):
        reasons.append("quality_cap")
    if bool(row.get("penalty_hard_block", False)):
        reasons.append("hard_block")
    return ";".join(reasons)


def _resolve_penalty_overlay_version(version: str | None) -> str:
    resolved = (version or PENALTY_OVERLAY_DEFAULT_VERSION).strip().lower()
    if resolved not in PENALTY_OVERLAY_VERSIONS:
        raise ValueError(
            f"Unsupported penalty overlay version: {version}. "
            f"Expected one of: {', '.join(sorted(PENALTY_OVERLAY_VERSIONS))}"
        )
    return resolved


def apply_penalty_overlay(
    pred20_df: pd.DataFrame,
    version: str = PENALTY_OVERLAY_DEFAULT_VERSION,
) -> pd.DataFrame:
    """Apply a white-box penalty overlay to the 20D artifact ranking score."""
    resolved_version = _resolve_penalty_overlay_version(version)
    required_cols = list(PENALTY_OVERLAY_REQUIRED_COLS)
    if resolved_version == "tuned":
        required_cols.extend(PENALTY_OVERLAY_TUNED_REQUIRED_COLS)
    missing = [col for col in required_cols if col not in pred20_df.columns]
    if missing:
        raise ValueError(
            "Penalty overlay requires prediction artifact columns: "
            + ", ".join(missing)
            + ". Regenerate or backfill an enriched predictions_* artifact first."
        )

    out = pred20_df.copy()
    idx = out.index

    pred_return = _numeric_series(out, "pred_return_20d")
    risk_adjusted = _numeric_series(out, "risk_adjusted_return")
    base_score = pred_return.where(pred_return.notna(), risk_adjusted).fillna(0.0)

    foreign_raw = _numeric_series(out, "foreign_cumsum_20d_raw")
    foreign_norm = _numeric_series(out, "foreign_cumsum_20d_norm_raw")
    if resolved_version == "tuned":
        foreign_pressure = (foreign_raw < -1_000_000) | (foreign_norm < -0.01)
    else:
        foreign_pressure = (foreign_raw < 0) | (foreign_norm < 0)
    foreign_penalty = pd.Series(0.0, index=idx, dtype="float64")
    foreign_penalty = foreign_penalty.mask(foreign_pressure, 0.005)
    foreign_penalty = foreign_penalty.mask(
        (foreign_raw < -20_000_000) | (foreign_norm < -0.04),
        0.015,
    )
    foreign_penalty = foreign_penalty.mask(
        (foreign_raw < -50_000_000) | (foreign_norm < -0.08),
        0.020,
    )

    gap_now = _numeric_series(out, "gap_pct").clip(lower=0.0)
    gap_avg = _numeric_series(out, "gap_up_avg_5d").clip(lower=0.0)
    gap_risk = pd.concat([gap_now, gap_avg], axis=1).max(axis=1).fillna(0.0)
    gap_penalty = pd.Series(0.0, index=idx, dtype="float64")
    gap_penalty = gap_penalty.mask(gap_risk > 0.010, 0.005)
    gap_penalty = gap_penalty.mask(gap_risk > 0.020, 0.010)
    gap_penalty = gap_penalty.mask(gap_risk > 0.040, 0.015)

    roa = _numeric_series(out, "roa_annualized")
    quality_penalty = pd.Series(0.0, index=idx, dtype="float64")
    quality_penalty = quality_penalty.mask(roa < 0.0, 0.015)
    quality_penalty = quality_penalty.mask(roa < -1.0, 0.020)

    beta = _numeric_series(out, "beta_60")
    beta_penalty = pd.Series(0.0, index=idx, dtype="float64")
    beta_penalty = beta_penalty.mask((beta > 1.50) & foreign_pressure, 0.005)
    beta_penalty = beta_penalty.mask(beta > 1.80, 0.010)
    beta_penalty = beta_penalty.mask(beta > 2.00, 0.015)

    raw_total_penalty = foreign_penalty + gap_penalty + quality_penalty + beta_penalty
    if resolved_version == "tuned":
        roe = _numeric_series(out, "roe_annualized")
        quality_cap = (roa > 5.0) & (roe > 10.0)
        total_penalty = raw_total_penalty.where(~quality_cap, raw_total_penalty.clip(upper=0.025))
        quality_cap_applied = quality_cap & raw_total_penalty.gt(total_penalty)
    else:
        total_penalty = raw_total_penalty
        quality_cap_applied = pd.Series(False, index=idx)
    hard_block = (
        (roa < -1.0)
        & ((foreign_raw < -20_000_000) | (foreign_norm < -0.04))
        & (beta > 1.50)
    )

    out["penalty_overlay_enabled"] = True
    out["penalty_overlay_version"] = resolved_version
    out["risk_adjusted_return_pre_penalty"] = risk_adjusted
    out["leaderboard_score_pre_penalty"] = _numeric_series(out, "leaderboard_score")
    out["penalty_foreign_selling"] = foreign_penalty.astype(np.float32)
    out["penalty_gap_risk"] = gap_penalty.astype(np.float32)
    out["penalty_quality"] = quality_penalty.astype(np.float32)
    out["penalty_beta"] = beta_penalty.astype(np.float32)
    out["penalty_overlay_total"] = total_penalty.astype(np.float32)
    out["penalty_overlay_total_before_cap"] = raw_total_penalty.astype(np.float32)
    out["penalty_quality_cap_applied"] = quality_cap_applied.fillna(False).astype(bool)
    out["penalty_overlay_score"] = (base_score - total_penalty).astype(np.float32)
    out["penalty_hard_block"] = hard_block.fillna(False).astype(bool)
    out["penalty_overlay_reason"] = out.apply(_overlay_reason, axis=1)

    out["risk_adjusted_return"] = out["penalty_overlay_score"]
    if "leaderboard_score" in out.columns:
        out["leaderboard_score"] = out["penalty_overlay_score"]
    return out


def _apply_penalty_overlay_sector_cap(df: pd.DataFrame, top_n: int) -> pd.DataFrame:
    if "penalty_overlay_score" not in df.columns:
        return df.head(top_n)

    buy_recs = {"強力買進", "建議買進"}
    if "recommendation" in df.columns:
        eligible = df[df["recommendation"].isin(buy_recs)].copy()
    else:
        eligible = df.copy()
    if eligible.empty:
        return eligible.reset_index(drop=True)

    sort_keys = ["penalty_overlay_score", "pred_return_20d"]
    ascending = [False, False]
    if "ticker" in eligible.columns:
        sort_keys.append("ticker")
        ascending.append(True)
    eligible = eligible.sort_values(sort_keys, ascending=ascending, na_position="last")

    if "sector" not in eligible.columns:
        return eligible.head(top_n).reset_index(drop=True)

    max_per_sector = max(1, int(top_n * 0.20))
    selected = []
    sector_count: dict[object, int] = {}
    for _, row in eligible.iterrows():
        sector = row["sector"]
        current = sector_count.get(sector, 0)
        if current >= max_per_sector:
            continue
        selected.append(row)
        sector_count[sector] = current + 1
        if len(selected) >= top_n:
            break

    if not selected:
        return eligible.head(0).copy().reset_index(drop=True)
    return pd.DataFrame(selected).reset_index(drop=True)


def _build_20d_candidates(
    pred20_df: pd.DataFrame,
    top_n: int,
    *,
    penalty_overlay: bool = False,
    penalty_overlay_version: str = PENALTY_OVERLAY_DEFAULT_VERSION,
) -> pd.DataFrame:
    required_cols = {"ticker", "date", "close"}
    missing = sorted(required_cols - set(pred20_df.columns))
    if missing:
        raise ValueError(f"20D prediction missing columns: {', '.join(missing)}")

    pred20_df = _filter_20d_prediction_pool_by_alpha_gate(pred20_df)
    pred20_df = apply_validated_chip_momentum_entry_filter(pred20_df)
    if penalty_overlay:
        pred20_df = apply_penalty_overlay(pred20_df, version=penalty_overlay_version)
        pred20_df = pred20_df.loc[~pred20_df["penalty_hard_block"].fillna(False)].copy()
        sector_capped_top20 = _apply_penalty_overlay_sector_cap(pred20_df, top_n=top_n)
    else:
        sector_capped_top20 = apply_sector_cap(pred20_df, top_n=top_n)
    top20 = apply_group_cap(
        pred20_df,
        top_n=top_n,
        initial_df=sector_capped_top20,
    ).reset_index(drop=True).copy()
    top20 = _attach_pe_ratio_context(top20)
    if top20.empty:
        return pd.DataFrame(
            columns=[
                "ticker",
                "prediction_date",
                "rank_20d",
                "recommendation_20d",
                "signal_20d",
                "pred_return_20d",
                *SHORTWAVE_20D_COLUMNS,
                "risk_adjusted_return",
                "risk_adjusted_return_pre_penalty",
                "leaderboard_score_pre_penalty",
                "penalty_overlay_enabled",
                "penalty_overlay_version",
                "penalty_overlay_score",
                "penalty_overlay_total",
                "penalty_overlay_total_before_cap",
                "penalty_foreign_selling",
                "penalty_gap_risk",
                "penalty_quality",
                "penalty_beta",
                "penalty_quality_cap_applied",
                "penalty_hard_block",
                "penalty_overlay_reason",
                "guardrail_blocked_20d",
                "guardrail_trigger_reason_20d",
                "guardrail_loose_mode_20d",
                "guardrail_intercept_rate_top50_20d",
                "guardrail_dual_track_fail_rate_top50_20d",
                "guardrail_monitor_top_n_20d",
                "alpha_win_prob_20d",
                "alpha_win_prob_percentile_20d",
                "alpha_win_prob_rank_20d",
                *VALIDATED_CHIP_MOMENTUM_DIAGNOSTIC_COLS,
                "prob_edge",
                "up_prob",
                "flat_prob",
                "down_prob",
                "close_20d",
                "open_20d",
                "raw_volume_20d",
                "amount_ma20_raw_20d",
                "volume_today_20d",
                "avg_5d_volume_20d",
                "avg_20d_volume_20d",
                "avg_5d_amount_20d",
                "avg_20d_amount_20d",
                "price_vs_ma60_20d",
                "price_vs_ma5_20d",
                "beta_60_20d",
                "pe_ratio_20d",
                "gap_pct_20d",
                "gap_up_avg_5d_20d",
                "foreign_cumsum_20d_raw_20d",
                "foreign_cumsum_20d_norm_raw_20d",
                *[f"{column}_20d" for column in VALIDATED_CHIP_MOMENTUM_COMPONENTS],
                "roa_annualized_20d",
                "roe_annualized_20d",
                "sector_20d",
                "group_code_20d",
                "group_name_20d",
                "group_cap_applied_20d",
                "risk_tags_20d",
                "two_stage_rank_20d",
                "price_vs_ma20_20d",
            ]
    )

    top20["rank_20d"] = np.arange(1, len(top20) + 1)
    raw_guardrail_blocked = (
        top20["guardrail_blocked"]
        if "guardrail_blocked" in top20.columns
        else pd.Series(0, index=top20.index, dtype="int8")
    )
    raw_guardrail_reason = (
        top20["guardrail_trigger_reason"]
        if "guardrail_trigger_reason" in top20.columns
        else pd.Series("", index=top20.index, dtype="object")
    )
    raw_recommendation = (
        top20["recommendation"]
        if "recommendation" in top20.columns
        else pd.Series("", index=top20.index, dtype="object")
    )
    guardrail_blocked_20d = pd.to_numeric(raw_guardrail_blocked, errors="coerce").fillna(0)
    guardrail_reason_20d = _normalize_guardrail_reason_series(
        guardrail_blocked_20d,
        raw_guardrail_reason,
        raw_recommendation,
    )
    return pd.DataFrame(
        {
            "ticker": top20["ticker"].astype(str),
            "prediction_date": top20["date"].astype(str),
            "rank_20d": top20["rank_20d"].astype("Int64"),
            "recommendation_20d": top20.get("recommendation"),
            "signal_20d": top20.get("signal"),
            "pred_return_20d": pd.to_numeric(top20.get("pred_return_20d"), errors="coerce"),
            **_shortwave_20d_payload(top20),
            "risk_adjusted_return": pd.to_numeric(top20.get("risk_adjusted_return"), errors="coerce"),
            "risk_adjusted_return_pre_penalty": pd.to_numeric(
                top20.get("risk_adjusted_return_pre_penalty"),
                errors="coerce",
            ),
            "leaderboard_score_pre_penalty": pd.to_numeric(
                top20.get("leaderboard_score_pre_penalty"),
                errors="coerce",
            ),
            "penalty_overlay_enabled": bool(penalty_overlay),
            "penalty_overlay_version": top20.get("penalty_overlay_version"),
            "penalty_overlay_score": pd.to_numeric(top20.get("penalty_overlay_score"), errors="coerce"),
            "penalty_overlay_total": pd.to_numeric(top20.get("penalty_overlay_total"), errors="coerce"),
            "penalty_overlay_total_before_cap": pd.to_numeric(
                top20.get("penalty_overlay_total_before_cap"),
                errors="coerce",
            ),
            "penalty_foreign_selling": pd.to_numeric(top20.get("penalty_foreign_selling"), errors="coerce"),
            "penalty_gap_risk": pd.to_numeric(top20.get("penalty_gap_risk"), errors="coerce"),
            "penalty_quality": pd.to_numeric(top20.get("penalty_quality"), errors="coerce"),
            "penalty_beta": pd.to_numeric(top20.get("penalty_beta"), errors="coerce"),
            "penalty_quality_cap_applied": top20.get("penalty_quality_cap_applied", False),
            "penalty_hard_block": top20.get("penalty_hard_block", False),
            "penalty_overlay_reason": top20.get("penalty_overlay_reason"),
            "guardrail_blocked_20d": guardrail_blocked_20d,
            "guardrail_trigger_reason_20d": guardrail_reason_20d,
            "guardrail_loose_mode_20d": pd.to_numeric(top20.get("guardrail_loose_mode"), errors="coerce"),
            "guardrail_intercept_rate_top50_20d": pd.to_numeric(
                top20.get("guardrail_intercept_rate_top50"),
                errors="coerce",
            ),
            "guardrail_dual_track_fail_rate_top50_20d": pd.to_numeric(
                top20.get("guardrail_dual_track_fail_rate_top50"),
                errors="coerce",
            ),
            "guardrail_monitor_top_n_20d": pd.to_numeric(top20.get("guardrail_monitor_top_n"), errors="coerce"),
            "alpha_win_prob_20d": pd.to_numeric(top20.get("alpha_win_prob_20d"), errors="coerce"),
            "alpha_win_prob_percentile_20d": pd.to_numeric(
                top20.get("alpha_win_prob_percentile"),
                errors="coerce",
            ),
            "alpha_win_prob_rank_20d": pd.to_numeric(top20.get("alpha_win_prob_rank"), errors="coerce"),
            "validated_chip_momentum_gate_enabled": top20.get("validated_chip_momentum_gate_enabled"),
            "validated_chip_momentum_gate_version": top20.get("validated_chip_momentum_gate_version"),
            "validated_chip_momentum_gate_top_n": pd.to_numeric(
                top20.get("validated_chip_momentum_gate_top_n"),
                errors="coerce",
            ),
            "validated_chip_momentum_min_open": pd.to_numeric(
                top20.get("validated_chip_momentum_min_open"),
                errors="coerce",
            ),
            "validated_chip_momentum_min_volume": pd.to_numeric(
                top20.get("validated_chip_momentum_min_volume"),
                errors="coerce",
            ),
            "validated_chip_momentum_min_amount": pd.to_numeric(
                top20.get("validated_chip_momentum_min_amount"),
                errors="coerce",
            ),
            "validated_chip_momentum_gate_error": top20.get("validated_chip_momentum_gate_error"),
            "validated_chip_momentum_score": pd.to_numeric(
                top20.get("validated_chip_momentum_score"),
                errors="coerce",
            ),
            "validated_chip_momentum_rank": pd.to_numeric(
                top20.get("validated_chip_momentum_rank"),
                errors="coerce",
            ),
            "validated_chip_momentum_liquidity_pass": top20.get(
                "validated_chip_momentum_liquidity_pass",
                False,
            ),
            "validated_chip_momentum_gate_pass": top20.get("validated_chip_momentum_gate_pass", False),
            "prob_edge": pd.to_numeric(top20.get("prob_edge"), errors="coerce"),
            "up_prob": pd.to_numeric(top20.get("up_prob"), errors="coerce"),
            "flat_prob": pd.to_numeric(top20.get("flat_prob"), errors="coerce"),
            "down_prob": pd.to_numeric(top20.get("down_prob"), errors="coerce"),
            "close_20d": pd.to_numeric(top20.get("close"), errors="coerce"),
            "open_20d": pd.to_numeric(top20.get("Open"), errors="coerce"),
            "raw_volume_20d": pd.to_numeric(top20.get("Volume"), errors="coerce"),
            "amount_ma20_raw_20d": pd.to_numeric(top20.get("AMOUNT_MA_20"), errors="coerce"),
            "volume_today_20d": pd.to_numeric(top20.get("volume_today"), errors="coerce"),
            "avg_5d_volume_20d": pd.to_numeric(top20.get("avg_5d_volume"), errors="coerce"),
            "avg_20d_volume_20d": pd.to_numeric(top20.get("avg_20d_volume"), errors="coerce"),
            "avg_5d_amount_20d": pd.to_numeric(top20.get("avg_5d_amount"), errors="coerce"),
            "avg_20d_amount_20d": pd.to_numeric(top20.get("avg_20d_amount"), errors="coerce"),
            "price_vs_ma60_20d": pd.to_numeric(top20.get("price_vs_ma60"), errors="coerce"),
            "price_vs_ma5_20d": _entry_price_vs_ma5_series(top20),
            "beta_60_20d": pd.to_numeric(top20.get("beta_60"), errors="coerce"),
            "pe_ratio_20d": pd.to_numeric(top20.get("pe_ratio"), errors="coerce"),
            "gap_pct_20d": pd.to_numeric(top20.get("gap_pct"), errors="coerce"),
            "gap_up_avg_5d_20d": pd.to_numeric(top20.get("gap_up_avg_5d"), errors="coerce"),
            "foreign_cumsum_20d_raw_20d": pd.to_numeric(
                top20.get("foreign_cumsum_20d_raw"),
                errors="coerce",
            ),
            "foreign_cumsum_20d_norm_raw_20d": pd.to_numeric(
                top20.get("foreign_cumsum_20d_norm_raw"),
                errors="coerce",
            ),
            **{
                f"{column}_20d": pd.to_numeric(top20.get(column), errors="coerce")
                for column in VALIDATED_CHIP_MOMENTUM_COMPONENTS
            },
            "roa_annualized_20d": pd.to_numeric(top20.get("roa_annualized"), errors="coerce"),
            "roe_annualized_20d": pd.to_numeric(top20.get("roe_annualized"), errors="coerce"),
            "sector_20d": top20.get("sector"),
            "group_code_20d": top20.get("group_code"),
            "group_name_20d": top20.get("group_name"),
            "group_cap_applied_20d": top20.get("group_cap_applied", False),
            "risk_tags_20d": top20.get("risk_tags"),
            "two_stage_rank_20d": pd.to_numeric(top20.get("two_stage_rank"), errors="coerce"),
            "price_vs_ma20_20d": pd.to_numeric(top20.get("price_vs_ma20"), errors="coerce"),
        }
    )


def _empty_t1_candidates() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "ticker",
            "prediction_date",
            "rank_t1",
            "recommendation_t1",
            "hit_prob_3pct",
            "t1_score",
            "take_profit",
            "stop_loss",
            "position_weight_t1",
            "close_t1",
            "avg_5d_volume_t1",
            "avg_20d_volume_t1",
            "avg_5d_amount_t1",
            "avg_20d_amount_t1",
            "price_vs_ma60_t1",
            "beta_60_t1",
            "sector_t1",
            "group_code_t1",
            "group_name_t1",
            "setup_tags_t1",
            "risk_tags_t1",
            "veto_reason_t1",
        ]
    )


def _build_t1_candidates(pred_t1_df: pd.DataFrame, production_gate: dict[str, object]) -> pd.DataFrame:
    required_cols = {"ticker", "date", "selected_for_trade"}
    missing = sorted(required_cols - set(pred_t1_df.columns))
    if missing:
        raise ValueError(f"T+1 prediction missing columns: {', '.join(missing)}")

    if bool(production_gate.get("closed")):
        return _empty_t1_candidates()

    selected = pred_t1_df.loc[_selected_for_trade_mask(pred_t1_df["selected_for_trade"])].copy()
    if selected.empty:
        return _empty_t1_candidates()

    if "selection_rank" not in selected.columns or selected["selection_rank"].isna().all():
        selected = sort_t1_prediction_df(selected)
        selected["selection_rank"] = pd.Series(range(1, len(selected) + 1), index=selected.index, dtype="Int64")

    selected = selected.sort_values(
        ["selection_rank", "t1_score", "ticker"],
        ascending=[True, False, True],
        na_position="last",
    ).reset_index(drop=True)
    selected = annotate_group_columns(selected)

    return pd.DataFrame(
        {
            "ticker": selected["ticker"].astype(str),
            "prediction_date": selected["date"].astype(str),
            "rank_t1": pd.to_numeric(selected.get("selection_rank"), errors="coerce").astype("Int64"),
            "recommendation_t1": selected.get("recommendation"),
            "hit_prob_3pct": pd.to_numeric(selected.get("hit_prob_3pct"), errors="coerce"),
            "t1_score": pd.to_numeric(selected.get("t1_score"), errors="coerce"),
            "take_profit": pd.to_numeric(selected.get("take_profit"), errors="coerce"),
            "stop_loss": pd.to_numeric(selected.get("stop_loss"), errors="coerce"),
            "position_weight_t1": pd.to_numeric(selected.get("position_weight"), errors="coerce"),
            "close_t1": pd.to_numeric(selected.get("close"), errors="coerce"),
            "avg_5d_volume_t1": pd.to_numeric(selected.get("avg_5d_volume"), errors="coerce"),
            "avg_20d_volume_t1": pd.to_numeric(selected.get("avg_20d_volume"), errors="coerce"),
            "avg_5d_amount_t1": pd.to_numeric(selected.get("avg_5d_amount"), errors="coerce"),
            "avg_20d_amount_t1": pd.to_numeric(selected.get("avg_20d_amount"), errors="coerce"),
            "price_vs_ma60_t1": pd.to_numeric(selected.get("price_vs_ma60"), errors="coerce"),
            "beta_60_t1": pd.to_numeric(selected.get("beta_60"), errors="coerce"),
            "sector_t1": selected.get("sector"),
            "group_code_t1": selected.get("group_code"),
            "group_name_t1": selected.get("group_name"),
            "setup_tags_t1": selected.get("setup_tags"),
            "risk_tags_t1": selected.get("risk_tags"),
            "veto_reason_t1": selected.get("veto_reason"),
        }
    )


def _evaluate_20d_production_gate() -> dict[str, object]:
    """Return the production gate state for 20D signals.

    T+1 monitor/backtest health is intentionally scoped to T+1 candidate
    promotion. 20D production eligibility is controlled by the 20D ranking,
    tradability, market-regime, and guardrail layers below; stale or failing
    T+1 metrics must not globally close 20D production signals.
    """

    return {
        "status": "OPEN",
        "closed": False,
        "action": "allow_20d",
        "reason": "",
        "reasons": [],
        "checks": {},
    }


def _validate_prediction_dates(pred20_df: pd.DataFrame, pred_t1_df: pd.DataFrame) -> str:
    pred20_dates = pred20_df["date"].dropna().astype(str).unique().tolist()
    pred_t1_dates = pred_t1_df["date"].dropna().astype(str).unique().tolist()

    if len(pred20_dates) != 1:
        raise ValueError(f"20D prediction contains multiple dates: {pred20_dates}")
    if len(pred_t1_dates) != 1:
        raise ValueError(f"T+1 prediction contains multiple dates: {pred_t1_dates}")
    if pred20_dates[0] != pred_t1_dates[0]:
        raise ValueError(
            f"Prediction date mismatch: 20D={pred20_dates[0]} vs T+1={pred_t1_dates[0]}"
        )
    return pred20_dates[0]


def _apply_mece_signal_type(unified: pd.DataFrame) -> pd.DataFrame:
    in_20d = unified["rank_20d"].notna()
    in_t1 = unified["rank_t1"].notna()

    conditions = [
        in_20d & in_t1,
        in_20d & (~in_t1),
        (~in_20d) & in_t1,
    ]
    choices = [
        SIGNAL_TYPE_DUAL,
        SIGNAL_TYPE_20D_ONLY,
        SIGNAL_TYPE_T1_ONLY,
    ]
    unified["signal_type"] = np.select(conditions, choices, default="ERROR")
    if (unified["signal_type"] == "ERROR").any():
        bad_rows = unified.loc[unified["signal_type"] == "ERROR", ["ticker", "rank_20d", "rank_t1"]]
        raise ValueError(f"Non-MECE signal assignment detected:\n{bad_rows.to_string(index=False)}")

    return unified


def _append_block_reason(df: pd.DataFrame, mask: pd.Series, reason: pd.Series) -> None:
    if not mask.any():
        return
    existing = df.loc[mask, "t1_block_reason"].fillna("").astype(str)
    addition = reason.loc[mask].fillna("").astype(str)
    combined = [
        combine_block_reasons(left, right)
        for left, right in zip(existing.tolist(), addition.tolist())
    ]
    df.loc[mask, "t1_block_reason"] = combined


def _append_tradability_reason(df: pd.DataFrame, mask: pd.Series, reason: str) -> None:
    if not mask.any():
        return
    existing = df.loc[mask, "tradability_reason"].fillna("").astype(str)
    combined = [combine_block_reasons(left, reason) for left in existing.tolist()]
    df.loc[mask, "tradability_reason"] = combined


def _apply_t1_risk_blocks(unified: pd.DataFrame) -> pd.DataFrame:
    if unified.empty:
        return unified

    unified = unified.copy()
    unified["t1_block_reason"] = ""
    has_t1 = unified["rank_t1"].notna()

    t1_reasons = unified.get("risk_tags_t1", pd.Series("", index=unified.index)).map(t1_hard_risk_reason)
    dual_d20_reasons = unified.get("risk_tags_20d", pd.Series("", index=unified.index)).map(d20_hard_risk_reason)
    t1_block = has_t1 & t1_reasons.astype(str).ne("")
    dual_block = has_t1 & unified["signal_type"].eq(SIGNAL_TYPE_DUAL) & dual_d20_reasons.astype(str).ne("")

    _append_block_reason(unified, t1_block, t1_reasons)
    _append_block_reason(unified, dual_block, dual_d20_reasons)

    blocked_t1 = unified["t1_block_reason"].fillna("").astype(str).ne("")
    drop_t1_only = blocked_t1 & unified["rank_20d"].isna()
    downgrade_to_20d = blocked_t1 & unified["rank_20d"].notna()

    unified.loc[downgrade_to_20d, "signal_type"] = SIGNAL_TYPE_20D_ONLY
    if drop_t1_only.any():
        unified = unified.loc[~drop_t1_only].copy()

    return unified.reset_index(drop=True)


def _assign_target_fields(unified: pd.DataFrame) -> pd.DataFrame:
    if unified.empty:
        for column in ["target_units", "route_priority", "target_weight_ratio"]:
            if column not in unified.columns:
                unified[column] = []
        return unified

    unified["target_units"] = unified["signal_type"].map(TARGET_UNITS_MAP).astype(np.int16)
    unified["route_priority"] = unified["signal_type"].map(ROUTE_PRIORITY_MAP).astype(np.int8)
    total_units = int(unified["target_units"].sum())
    unified["target_weight_ratio"] = (
        unified["target_units"].astype(np.float64) / total_units if total_units > 0 else 0.0
    )
    return unified


def _apply_expensive_momentum_gate(unified: pd.DataFrame) -> pd.DataFrame:
    """Cap high-valuation momentum names without fully blocking the signal.

    REQ-002 deliberately differs from OVERHEAT_RISK: high PE + strong MA60
    momentum should stay visible to the model/user, but the production target
    weight is capped and downstream ledgers leave the remainder in cash.
    """
    if unified.empty:
        for column in [
            "expensive_momentum_risk",
            "expensive_momentum_pe_ratio",
            "expensive_momentum_price_vs_ma60",
            "target_weight_ratio_before_expensive_momentum_cap",
        ]:
            if column not in unified.columns:
                unified[column] = []
        return unified

    pe_ratio = pd.to_numeric(
        _coalesce_many(unified, "pe_ratio_20d", "pe_ratio"),
        errors="coerce",
    )
    price_vs_ma60 = pd.to_numeric(
        _coalesce_many(unified, "price_vs_ma60_20d", "price_vs_ma60_t1", "price_vs_ma60"),
        errors="coerce",
    )
    target_weight = pd.to_numeric(unified.get("target_weight_ratio"), errors="coerce").fillna(0.0)
    active = pd.to_numeric(unified.get("target_units"), errors="coerce").fillna(0).gt(0)
    trigger = (
        active
        & pe_ratio.gt(EXPENSIVE_MOMENTUM_PE_THRESHOLD)
        & price_vs_ma60.gt(EXPENSIVE_MOMENTUM_MA60_THRESHOLD)
    )

    unified["expensive_momentum_risk"] = trigger.fillna(False).astype(bool)
    unified["expensive_momentum_pe_ratio"] = pe_ratio
    unified["expensive_momentum_price_vs_ma60"] = price_vs_ma60
    unified["target_weight_ratio_before_expensive_momentum_cap"] = target_weight

    if trigger.any():
        unified.loc[trigger, "target_weight_ratio"] = target_weight.loc[trigger].clip(
            upper=EXPENSIVE_MOMENTUM_WEIGHT_CAP
        )
        _append_tradability_reason(unified, trigger, TRADABILITY_REASON_EXPENSIVE_MOMENTUM_RISK)

    return unified


def _apply_futures_settlement_overlay(unified: pd.DataFrame, prediction_date: str) -> pd.DataFrame:
    """Reduce new-entry sizing near monthly TAIFEX index settlement windows."""
    columns = [
        "futures_settlement_window",
        "futures_settlement_phase",
        "futures_settlement_date",
        "futures_settlement_action",
        "futures_settlement_weight_multiplier",
        "target_weight_ratio_before_futures_settlement_cap",
    ]
    if unified.empty:
        for column in columns:
            if column not in unified.columns:
                unified[column] = []
        return unified

    entry_date = str(unified.get("tradability_check_date", pd.Series([_next_business_day(prediction_date)])).iloc[0])
    window = nearest_monthly_settlement_window(entry_date)
    target_weight = pd.to_numeric(unified.get("target_weight_ratio"), errors="coerce").fillna(0.0)

    unified["futures_settlement_window"] = bool(window.active)
    unified["futures_settlement_phase"] = window.phase
    unified["futures_settlement_date"] = window.settlement_date
    unified["futures_settlement_action"] = ""
    unified["futures_settlement_weight_multiplier"] = 1.0
    unified["target_weight_ratio_before_futures_settlement_cap"] = target_weight

    if not window.active:
        return unified

    active = pd.to_numeric(unified.get("target_units"), errors="coerce").fillna(0).gt(0)
    beta = pd.to_numeric(
        _coalesce_many(unified, "beta_60_20d", "beta_60_t1", "beta_60"),
        errors="coerce",
    )
    high_beta = active & beta.ge(FUTURES_SETTLEMENT_HIGH_BETA_MIN)
    normal_beta = active & ~high_beta
    multipliers = pd.Series(1.0, index=unified.index, dtype=float)
    multipliers.loc[normal_beta] = FUTURES_SETTLEMENT_BASE_MULTIPLIER
    multipliers.loc[high_beta] = FUTURES_SETTLEMENT_HIGH_BETA_MULTIPLIER

    overlay = active & multipliers.lt(1.0)
    if not overlay.any():
        return unified

    unified.loc[overlay, "target_weight_ratio"] = target_weight.loc[overlay] * multipliers.loc[overlay]
    unified.loc[overlay, "futures_settlement_weight_multiplier"] = multipliers.loc[overlay]
    unified.loc[normal_beta, "futures_settlement_action"] = "half_size_no_chase"
    unified.loc[high_beta, "futures_settlement_action"] = "quarter_size_high_beta_no_chase"
    _append_tradability_reason(unified, overlay, TRADABILITY_REASON_FUTURES_SETTLEMENT_WINDOW)
    return unified


def _apply_macro_strategy_overlay(unified: pd.DataFrame, prediction_date: str) -> pd.DataFrame:
    """Cap sizing near macro/policy events without blanketing the entry list."""
    if unified.empty:
        for column in MACRO_STRATEGY_COLUMNS:
            if column not in unified.columns:
                unified[column] = []
        return unified

    unified = unified.copy()
    try:
        context = build_macro_strategy_context(as_of=prediction_date, horizon_days=20)
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

    beta = pd.to_numeric(_coalesce_many(unified, "beta_60_20d", "beta_60_t1", "beta_60"), errors="coerce")
    gap = pd.to_numeric(_coalesce_many(unified, "gap_pct_20d", "gap_pct"), errors="coerce")
    ma20 = pd.to_numeric(
        _coalesce_many(unified, "price_vs_ma20_20d", "price_vs_ma20_t1", "price_vs_ma20"),
        errors="coerce",
    )
    target_weight = pd.to_numeric(unified.get("target_weight_ratio"), errors="coerce").fillna(0.0)
    active = pd.to_numeric(unified.get("target_units"), errors="coerce").fillna(0).gt(0)

    adjustments = [
        macro_stock_adjustment(
            beta_60=beta.loc[idx],
            gap_pct=gap.loc[idx],
            price_vs_ma20=ma20.loc[idx],
            context=context,
        )
        for idx in unified.index
    ]
    adj_df = pd.DataFrame(adjustments, index=unified.index)

    unified["macro_event_risk_score"] = float(events.get("risk_score") or 0.0)
    unified["macro_event_risk_level"] = str(events.get("risk_level") or "none")
    unified["macro_event_count"] = int(events.get("event_count") or 0)
    unified["macro_event_next_title"] = str(next_event.get("title") or "")
    unified["macro_event_nearest_days"] = next_event.get("days_until")
    unified["macro_market_sentiment_score"] = float(sentiment.get("score") or 50.0)
    unified["macro_market_sentiment_label"] = str(sentiment.get("label") or "neutral")
    unified["macro_strategy_pressure_score"] = pressure
    unified["macro_strategy_action"] = str(strategy.get("action") or "normal")
    unified["macro_stock_penalty_return"] = pd.to_numeric(
        adj_df.get("macro_stock_penalty_return", 0.0),
        errors="coerce",
    ).fillna(0.0)
    unified["macro_stock_penalty_multiplier"] = pd.to_numeric(
        adj_df.get("macro_stock_penalty_multiplier", 1.0),
        errors="coerce",
    ).fillna(1.0)
    unified["macro_stock_action"] = adj_df.get("macro_stock_action", "normal").fillna("normal").astype(str)
    unified["macro_event_weight_multiplier"] = 1.0
    unified["target_weight_ratio_before_macro_event_cap"] = target_weight

    if pressure < 25:
        return unified

    multipliers = pd.to_numeric(unified["macro_stock_penalty_multiplier"], errors="coerce").fillna(1.0)
    capped = active & multipliers.lt(1.0)
    if not capped.any():
        return unified

    unified.loc[capped, "target_weight_ratio"] = target_weight.loc[capped] * multipliers.loc[capped]
    unified.loc[capped, "macro_event_weight_multiplier"] = multipliers.loc[capped]
    _append_tradability_reason(unified, capped, TRADABILITY_REASON_MACRO_EVENT_RISK)
    return unified


def _apply_liquidity_gate(unified: pd.DataFrame) -> pd.DataFrame:
    if unified.empty:
        return unified

    has_20d = unified["rank_20d"].notna()
    has_t1 = unified["rank_t1"].notna()

    base_avg_20d_volume = pd.to_numeric(
        _coalesce_columns(unified, "avg_20d_volume_20d", "avg_20d_volume_t1"),
        errors="coerce",
    )
    base_avg_20d_amount = pd.to_numeric(
        _coalesce_columns(unified, "avg_20d_amount_20d", "avg_20d_amount_t1"),
        errors="coerce",
    )
    t1_avg_5d_volume = _numeric_series(unified, "avg_5d_volume_t1")
    t1_avg_5d_amount = _numeric_series(unified, "avg_5d_amount_t1")

    base_liquidity_block = (
        base_avg_20d_volume.isna()
        | base_avg_20d_amount.isna()
        | (base_avg_20d_volume < D20_MIN_AVG_20D_VOLUME)
        | (base_avg_20d_amount < D20_MIN_AVG_20D_AMOUNT)
    )
    t1_liquidity_block = has_t1 & (
        t1_avg_5d_volume.isna()
        | t1_avg_5d_amount.isna()
        | (t1_avg_5d_volume < T1_MIN_AVG_5D_VOLUME)
        | (t1_avg_5d_amount < T1_MIN_AVG_5D_AMOUNT)
    )

    full_block = unified["signal_type"].ne(SIGNAL_TYPE_NONE) & base_liquidity_block
    if full_block.any():
        unified.loc[full_block, "tradability_status"] = TRADABILITY_STATUS_BLOCKED
        unified.loc[full_block, "tradability_blocked"] = True
        unified.loc[full_block, "signal_type"] = SIGNAL_TYPE_NONE
        _append_tradability_reason(unified, full_block, TRADABILITY_REASON_LOW_LIQUIDITY)

    t1_only_block = (
        (~full_block)
        & t1_liquidity_block
        & unified["signal_type"].eq(SIGNAL_TYPE_T1_ONLY)
    )
    if t1_only_block.any():
        unified.loc[t1_only_block, "tradability_status"] = TRADABILITY_STATUS_BLOCKED
        unified.loc[t1_only_block, "tradability_blocked"] = True
        unified.loc[t1_only_block, "signal_type"] = SIGNAL_TYPE_NONE
        _append_tradability_reason(unified, t1_only_block, TRADABILITY_REASON_T1_LIQUIDITY_BLOCKED)

    dual_downgrade = (
        (~full_block)
        & t1_liquidity_block
        & has_20d
        & unified["signal_type"].eq(SIGNAL_TYPE_DUAL)
    )
    if dual_downgrade.any():
        unified.loc[dual_downgrade, "signal_type"] = SIGNAL_TYPE_20D_ONLY
        _append_tradability_reason(unified, dual_downgrade, TRADABILITY_REASON_T1_LIQUIDITY_BLOCKED)

    return unified


def _apply_overheat_risk_gate(unified: pd.DataFrame) -> pd.DataFrame:
    """Apply the Champion unified production overheat/beta gate.

    This is the paper-book / unified-signals production path:
    - price_vs_ma60 above the sector-conditioned threshold appends
      OVERHEAT_RISK and blocks the row.
    - beta_60 > 1.80 appends HIGH_BETA_RISK and blocks the row.

    Nightly Champion enables the REQ-004 sector thresholds through
    SECTOR_OVERHEAT_THRESHOLDS_ENABLED=1. Keeping the flag explicit preserves a
    rollback path to the previous flat 40% threshold.
    It is intentionally distinct from the recommendation/explain guardrail in
    ml/thresholds.py, where |price_vs_ma20| > 30% only governs recommendation
    downgrades and UI risk wording. Audit reports must name which path they
    are calibrating.
    """
    if unified.empty:
        for column in ["overheat_threshold", "overheat_threshold_group", "overheat_threshold_label"]:
            if column not in unified.columns:
                unified[column] = []
        return unified

    active = unified["signal_type"].ne(SIGNAL_TYPE_NONE)
    price_vs_ma60 = pd.to_numeric(
        _coalesce_columns(unified, "price_vs_ma60_20d", "price_vs_ma60_t1"),
        errors="coerce",
    )
    beta_60 = pd.to_numeric(
        _coalesce_columns(unified, "beta_60_20d", "beta_60_t1"),
        errors="coerce",
    )
    sector = _coalesce_columns(unified, "sector_20d", "sector_t1")
    overheat_context = (
        _sector_overheat_context(sector)
        if _sector_overheat_thresholds_enabled()
        else _default_overheat_context(sector)
    )
    unified["overheat_threshold"] = overheat_context["overheat_threshold"]
    unified["overheat_threshold_group"] = overheat_context["overheat_threshold_group"]
    unified["overheat_threshold_label"] = overheat_context["overheat_threshold_label"]

    missing_context = active & (price_vs_ma60.isna() | beta_60.isna())
    overheat_block = active & price_vs_ma60.gt(
        pd.to_numeric(unified["overheat_threshold"], errors="coerce").fillna(MAX_PRICE_VS_MA60)
    )
    high_beta_block = active & beta_60.gt(MAX_BETA_60)
    full_block = missing_context | overheat_block | high_beta_block

    if not full_block.any():
        return unified

    unified.loc[full_block, "tradability_status"] = TRADABILITY_STATUS_BLOCKED
    unified.loc[full_block, "tradability_blocked"] = True
    unified.loc[full_block, "signal_type"] = SIGNAL_TYPE_NONE
    _append_tradability_reason(unified, missing_context, TRADABILITY_REASON_MISSING_RISK_CONTEXT)
    _append_tradability_reason(unified, overheat_block, TRADABILITY_REASON_OVERHEAT_RISK)
    _append_tradability_reason(unified, high_beta_block, TRADABILITY_REASON_HIGH_BETA_RISK)
    return unified


def _apply_ma5_entry_guard(unified: pd.DataFrame) -> pd.DataFrame:
    """Block active 20D entries that are already too far below MA5."""
    if unified.empty:
        return unified

    active = unified["signal_type"].ne(SIGNAL_TYPE_NONE)
    has_20d = unified["rank_20d"].notna()
    price_vs_ma5 = pd.to_numeric(_coalesce_columns(unified, "price_vs_ma5_20d", "price_vs_ma5_t1"), errors="coerce")

    missing_context = active & has_20d & price_vs_ma5.isna()
    weak_entry = active & has_20d & price_vs_ma5.lt(ENTRY_MA5_MIN_PCT)
    full_block = missing_context | weak_entry
    if not full_block.any():
        return unified

    unified.loc[full_block, "tradability_status"] = TRADABILITY_STATUS_BLOCKED
    unified.loc[full_block, "tradability_blocked"] = True
    unified.loc[full_block, "signal_type"] = SIGNAL_TYPE_NONE
    _append_tradability_reason(unified, missing_context, TRADABILITY_REASON_MISSING_ENTRY_MA5_CONTEXT)
    _append_tradability_reason(unified, weak_entry, TRADABILITY_REASON_ENTRY_MA5_WEAK)
    return unified


def _apply_entry_benchmark_guard(unified: pd.DataFrame, enforce: bool = True) -> pd.DataFrame:
    """Block weak 20D entries unless edge or transparent consensus is strong.

    This guard is derived from the offline entry-filter benchmark layer. It only
    uses ex-ante fields already present before entry: trend, foreign-flow, gap,
    quality, beta, and ``prob_edge``. It does not use realized returns,
    attribution residual labels, or post-entry outcomes.
    """
    columns = [
        "entry_benchmark_guard_enabled",
        "entry_benchmark_guard_enforced",
        "entry_benchmark_guard_version",
        "entry_benchmark_prob_edge_floor",
        "entry_benchmark_strong_vote_min",
        "entry_benchmark_trend_vote",
        "entry_benchmark_flow_vote",
        "entry_benchmark_gap_vote",
        "entry_benchmark_quality_vote",
        "entry_benchmark_risk_vote",
        "entry_benchmark_edge_vote",
        "entry_benchmark_votes",
        "entry_benchmark_consensus",
        "entry_benchmark_guard_pass",
    ]
    if unified.empty:
        for column in columns:
            if column not in unified.columns:
                unified[column] = []
        return unified

    enabled = _entry_benchmark_guard_enabled()
    enforced = bool(enabled and enforce)
    edge_floor = float(ENTRY_BENCHMARK_PROB_EDGE_FLOOR)
    strong_vote_min = int(ENTRY_BENCHMARK_STRONG_VOTE_MIN)

    price_vs_ma20 = pd.to_numeric(_coalesce_many(unified, "price_vs_ma20_20d", "price_vs_ma20"), errors="coerce")
    price_vs_ma60 = pd.to_numeric(_coalesce_many(unified, "price_vs_ma60_20d", "price_vs_ma60"), errors="coerce")
    price_vs_ma5 = pd.to_numeric(_coalesce_many(unified, "price_vs_ma5_20d", "price_vs_ma5"), errors="coerce")
    beta_60 = pd.to_numeric(_coalesce_many(unified, "beta_60_20d", "beta_60"), errors="coerce")
    gap_pct = pd.to_numeric(_coalesce_many(unified, "gap_pct_20d", "gap_pct"), errors="coerce")
    foreign_flow = pd.to_numeric(
        _coalesce_many(unified, "foreign_cumsum_20d_raw_20d", "foreign_cumsum_20d_raw"),
        errors="coerce",
    )
    prob_edge = pd.to_numeric(unified.get("prob_edge", pd.Series(np.nan, index=unified.index)), errors="coerce")
    penalty_reason = unified.get("penalty_overlay_reason", pd.Series("", index=unified.index)).fillna("").astype(str)

    trend_vote = (price_vs_ma20 > 0) & (price_vs_ma60 > 0) & (price_vs_ma5 >= -0.02)
    flow_vote = (foreign_flow > 0) | ~penalty_reason.str.contains("foreign_selling", na=False)
    gap_vote = (gap_pct <= 0.03) & ~penalty_reason.str.contains("gap_risk", na=False)
    quality_vote = ~penalty_reason.str.contains("quality", na=False)
    risk_vote = beta_60.fillna(0) <= 1.25
    edge_vote = prob_edge >= edge_floor
    vote_frame = pd.DataFrame(
        {
            "trend": trend_vote.fillna(False),
            "flow": flow_vote.fillna(False),
            "gap": gap_vote.fillna(False),
            "quality": quality_vote.fillna(False),
            "risk": risk_vote.fillna(False),
            "edge": edge_vote.fillna(False),
        },
        index=unified.index,
    )
    votes = vote_frame.sum(axis=1).astype(np.int8)
    consensus = pd.Series("reject", index=unified.index, dtype="object")
    consensus.loc[votes.between(3, strong_vote_min - 1)] = "watch"
    consensus.loc[votes >= strong_vote_min] = "strong"
    pass_guard = (prob_edge >= edge_floor) | (votes >= strong_vote_min)

    unified["entry_benchmark_guard_enabled"] = bool(enabled)
    unified["entry_benchmark_guard_enforced"] = enforced
    unified["entry_benchmark_guard_version"] = ENTRY_BENCHMARK_GUARD_VERSION
    unified["entry_benchmark_prob_edge_floor"] = edge_floor
    unified["entry_benchmark_strong_vote_min"] = strong_vote_min
    unified["entry_benchmark_trend_vote"] = vote_frame["trend"].astype(bool)
    unified["entry_benchmark_flow_vote"] = vote_frame["flow"].astype(bool)
    unified["entry_benchmark_gap_vote"] = vote_frame["gap"].astype(bool)
    unified["entry_benchmark_quality_vote"] = vote_frame["quality"].astype(bool)
    unified["entry_benchmark_risk_vote"] = vote_frame["risk"].astype(bool)
    unified["entry_benchmark_edge_vote"] = vote_frame["edge"].astype(bool)
    unified["entry_benchmark_votes"] = votes
    unified["entry_benchmark_consensus"] = consensus
    unified["entry_benchmark_guard_pass"] = pass_guard.fillna(False).astype(bool)

    if not enforced:
        return unified

    active_20d = unified["signal_type"].ne(SIGNAL_TYPE_NONE) & unified["rank_20d"].notna()
    has_context = prob_edge.notna()
    fail_guard = active_20d & has_context & ~pass_guard.fillna(False)
    if not fail_guard.any():
        return unified

    rank_t1 = unified["rank_t1"] if "rank_t1" in unified.columns else pd.Series(pd.NA, index=unified.index)
    dual_downgrade = fail_guard & unified["signal_type"].eq(SIGNAL_TYPE_DUAL) & rank_t1.notna()
    full_block = fail_guard & ~dual_downgrade
    if dual_downgrade.any():
        unified.loc[dual_downgrade, "signal_type"] = SIGNAL_TYPE_T1_ONLY
        _append_tradability_reason(unified, dual_downgrade, TRADABILITY_REASON_ENTRY_BENCHMARK_GUARD)
    if full_block.any():
        unified.loc[full_block, "tradability_status"] = TRADABILITY_STATUS_BLOCKED
        unified.loc[full_block, "tradability_blocked"] = True
        unified.loc[full_block, "signal_type"] = SIGNAL_TYPE_NONE
        _append_tradability_reason(unified, full_block, TRADABILITY_REASON_ENTRY_BENCHMARK_GUARD)
    return unified


TQUANT_REFERENCE_COLUMNS = [
    "tquant_reference_gate_enabled",
    "tquant_reference_gate_version",
    "tquant_reference_gate_min_consensus",
    "tquant_reference_gate_error",
    "tquant_reference_gate_pass",
    "tquant_consensus",
    "tquant_support_votes",
    "tquant_risk_flags",
    "tquant_support_reasons",
    "tquant_risk_reasons",
    "tquant_data_status",
    "tquant_vam_21d",
    "tquant_vam_pct_rank",
    "tquant_ret_21d",
    "tquant_vol_21d",
    "tquant_mrat_21_200",
    "tquant_mrat_pct_rank",
    "tquant_baz",
    "tquant_baz_pct_rank",
    "tquant_expanded_momentum",
    "tquant_expanded_momentum_pct_rank",
    "tquant_price_structure_vote",
    "tquant_close_to_20d_high",
    "tquant_higher_low_10d",
    "tquant_range_compression",
    "tquant_k_value",
    "tquant_d_value",
    "tquant_kd_reversal_vote",
    "tquant_kd_overheat_risk",
    "tquant_rsi_14",
    "tquant_rsi_slope_3d",
    "tquant_rsi_reversal_vote",
    "tquant_aroon_up_25",
    "tquant_aroon_down_25",
    "tquant_aroon_up_pct_rank",
    "tquant_aroon_trend_vote",
    "tquant_revenue_pct_rank",
    "revenue_data_status",
    "revenue_yoy_latest",
    "revenue_yoy_3m_avg",
    "revenue_yoy_momentum",
    "tquant_revenue_vote",
    "financial_data_status",
    "financial_available_lag_days",
    "financial_year",
    "financial_season",
    "tquant_gross_margin_pct",
    "tquant_gross_margin_pct_rank",
    "tquant_net_margin_pct",
    "tquant_net_margin_pct_rank",
    "tquant_gross_margin_delta_yoy",
    "tquant_net_margin_delta_yoy",
    "tquant_financial_quality_vote",
    "tquant_financial_turnaround_vote",
    "tquant_margin_deterioration_risk",
    "valuation_data_status",
    "valuation_date",
    "tquant_pe_ratio",
    "tquant_pb_ratio",
    "tquant_dividend_yield",
    "tquant_value_vote",
    "tquant_valuation_risk",
    "tquant_inst_total_5d",
    "tquant_inst_total_20d",
    "tquant_inst_total_20d_norm",
    "tquant_institutional_vote",
    "tquant_margin_change_5d",
    "tquant_price_return_20d",
    "tquant_financing_crowding_risk",
    "tquant_settlement_risk",
    "tquant_high_beta_risk",
    "tquant_gap_risk",
    "tquant_sector_crowding_risk",
    "tquant_vam_vote",
    "tquant_mrat_vote",
    "tquant_baz_vote",
    "tquant_expanded_momentum_vote",
    "tquant_technical_votes",
    "tquant_fundamental_votes",
    "tquant_reference_score",
    "tquant_ml_edge_vote",
]


def _empty_tquant_reference_columns(unified: pd.DataFrame) -> pd.DataFrame:
    for column in TQUANT_REFERENCE_COLUMNS:
        if column not in unified.columns:
            unified[column] = []
    return unified


def _build_tquant_reference_frame(unified: pd.DataFrame, prediction_date: str) -> pd.DataFrame:
    """Build TQuant-inspired votes for the current unified candidate frame."""
    if unified.empty:
        return pd.DataFrame(index=unified.index)

    tickers = sorted(unified["ticker"].dropna().astype(str).str.zfill(4).unique())
    lookback_start = (pd.Timestamp(prediction_date) - pd.Timedelta(days=430)).strftime("%Y-%m-%d")
    prices = _tquant_load_price_history(
        tickers,
        lookback_start,
        prediction_date,
        db_path=TQUANT_REFERENCE_DB_PATH,
    )
    rows: list[dict[str, object]] = []
    for _, row in unified.iterrows():
        ticker = str(row.get("ticker", "")).strip().zfill(4)
        features = _tquant_point_in_time_features(ticker, prediction_date, prices)
        features.update(_tquant_latest_revenue_features(ticker, prediction_date))
        rows.append(features)

    feature_df = pd.DataFrame(rows, index=unified.index)
    annotated = pd.concat([unified.copy(), feature_df], axis=1)
    annotated["signal_date"] = prediction_date
    entry_date = str(annotated.get("tradability_check_date", pd.Series([_next_business_day(prediction_date)])).iloc[0])
    window = nearest_monthly_settlement_window(entry_date)
    annotated["futures_settlement_window"] = bool(window.active)
    annotated["futures_settlement_phase"] = window.phase
    annotated["futures_settlement_action"] = "risk_window" if window.active else ""
    annotated = _tquant_annotate_cross_section_ranks(annotated)
    annotated = _tquant_annotate_votes(annotated)
    return annotated


def _tquant_reference_pass_mask(unified: pd.DataFrame, min_consensus: str) -> pd.Series:
    consensus = unified.get("tquant_consensus", pd.Series("reject", index=unified.index)).fillna("reject").astype(str)
    if min_consensus == "strong":
        return consensus.eq("strong")
    return consensus.isin(["strong", "watch"])


def _apply_tquant_reference_gate(unified: pd.DataFrame, prediction_date: str) -> pd.DataFrame:
    """Apply the production TQuant reference gate to the 20D entry leg.

    User direction on 2026-05-31 was to promote the TQuant reference layer
    directly to production. The gate blocks 20D entries with `reject`
    consensus; Dual signals are downgraded to T1-only when the T1 leg exists.
    """
    if unified.empty:
        return _empty_tquant_reference_columns(unified)

    unified = unified.copy()
    enabled = _tquant_reference_gate_enabled()
    min_consensus = _tquant_reference_gate_min_consensus()
    unified["tquant_reference_gate_enabled"] = bool(enabled)
    unified["tquant_reference_gate_version"] = TQUANT_REFERENCE_GATE_VERSION
    unified["tquant_reference_gate_min_consensus"] = min_consensus
    unified["tquant_reference_gate_error"] = ""

    try:
        annotated = _build_tquant_reference_frame(unified, prediction_date)
    except Exception as exc:  # pragma: no cover - defensive pipeline reliability path
        unified["tquant_reference_gate_error"] = f"{type(exc).__name__}: {exc}"
        unified["tquant_reference_gate_pass"] = True
        for column in TQUANT_REFERENCE_COLUMNS:
            if column not in unified.columns:
                unified[column] = pd.NA
        unified["tquant_reference_gate_enabled"] = bool(enabled)
        unified["tquant_reference_gate_version"] = TQUANT_REFERENCE_GATE_VERSION
        unified["tquant_reference_gate_min_consensus"] = min_consensus
        return unified

    for column in TQUANT_REFERENCE_COLUMNS:
        if column in {
            "tquant_reference_gate_enabled",
            "tquant_reference_gate_version",
            "tquant_reference_gate_min_consensus",
            "tquant_reference_gate_error",
            "tquant_reference_gate_pass",
        }:
            continue
        unified[column] = annotated[column] if column in annotated.columns else pd.NA

    pass_mask = _tquant_reference_pass_mask(unified, min_consensus)
    unified["tquant_reference_gate_pass"] = pass_mask.fillna(False).astype(bool)

    if not enabled:
        return unified

    active_20d = unified["signal_type"].ne(SIGNAL_TYPE_NONE) & unified["rank_20d"].notna()
    fail_gate = active_20d & ~pass_mask.fillna(False)
    if not fail_gate.any():
        return unified

    rank_t1 = unified["rank_t1"] if "rank_t1" in unified.columns else pd.Series(pd.NA, index=unified.index)
    dual_downgrade = fail_gate & unified["signal_type"].eq(SIGNAL_TYPE_DUAL) & rank_t1.notna()
    full_block = fail_gate & ~dual_downgrade
    if dual_downgrade.any():
        unified.loc[dual_downgrade, "signal_type"] = SIGNAL_TYPE_T1_ONLY
        _append_tradability_reason(unified, dual_downgrade, TRADABILITY_REASON_TQUANT_REFERENCE_GATE)
    if full_block.any():
        unified.loc[full_block, "tradability_status"] = TRADABILITY_STATUS_BLOCKED
        unified.loc[full_block, "tradability_blocked"] = True
        unified.loc[full_block, "signal_type"] = SIGNAL_TYPE_NONE
        _append_tradability_reason(unified, full_block, TRADABILITY_REASON_TQUANT_REFERENCE_GATE)
    return unified


def _apply_alpha_win_prob_gate(unified: pd.DataFrame) -> pd.DataFrame:
    unified["alpha_win_gate_mode"] = _alpha_win_gate_mode()
    unified["alpha_win_gate_threshold"] = _alpha_win_gate_threshold()

    if unified.empty or _alpha_win_gate_mode() == "disabled" or "alpha_win_prob_20d" not in unified.columns:
        return unified

    if not pd.to_numeric(unified["alpha_win_prob_20d"], errors="coerce").notna().any():
        return unified

    active = unified["signal_type"].ne(SIGNAL_TYPE_NONE)
    has_20d = unified["rank_20d"].notna()
    pass_alpha = _alpha_win_gate_pass_mask(
        unified,
        prob_col="alpha_win_prob_20d",
        percentile_col="alpha_win_prob_percentile_20d",
        rank_col="alpha_win_prob_rank_20d",
    )
    low_alpha = active & has_20d & ~pass_alpha
    if not low_alpha.any():
        return unified

    dual_downgrade = low_alpha & unified["signal_type"].eq(SIGNAL_TYPE_DUAL) & unified["rank_t1"].notna()
    full_block = low_alpha & ~dual_downgrade

    if dual_downgrade.any():
        unified.loc[dual_downgrade, "signal_type"] = SIGNAL_TYPE_T1_ONLY
        _append_tradability_reason(unified, dual_downgrade, TRADABILITY_REASON_LOW_ALPHA_WIN_PROB)

    if full_block.any():
        unified.loc[full_block, "tradability_status"] = TRADABILITY_STATUS_BLOCKED
        unified.loc[full_block, "tradability_blocked"] = True
        unified.loc[full_block, "signal_type"] = SIGNAL_TYPE_NONE
        _append_tradability_reason(unified, full_block, TRADABILITY_REASON_LOW_ALPHA_WIN_PROB)

    return unified


def _apply_market_regime_gate(unified: pd.DataFrame, prediction_date: str) -> pd.DataFrame:
    unified = unified.copy()
    try:
        report = evaluate_market_regime(as_of_date=prediction_date)
    except Exception as exc:
        report = {
            "state": STATE_CLOSED,
            "action": ACTION_BLOCK,
            "reason": f"market_regime_error:{exc}",
            "twii": {},
            "breadth": {},
        }

    state = str(report.get("state") or STATE_CLOSED).upper()
    action = str(report.get("action") or ACTION_BLOCK)
    reason = str(report.get("reason") or "")
    twii = report.get("twii", {}) or {}
    breadth = report.get("breadth", {}) or {}

    unified["market_regime_state"] = state
    unified["market_regime_action"] = action
    unified["market_regime_reason"] = reason
    unified["market_regime_breadth_ma20"] = pd.to_numeric(
        pd.Series([breadth.get("ma20_ratio")] * len(unified), index=unified.index),
        errors="coerce",
    )
    unified["market_regime_twii_close"] = pd.to_numeric(
        pd.Series([twii.get("close")] * len(unified), index=unified.index),
        errors="coerce",
    )
    unified["market_regime_twii_ma20"] = pd.to_numeric(
        pd.Series([twii.get("ma20")] * len(unified), index=unified.index),
        errors="coerce",
    )
    unified["market_regime_twii_ma60"] = pd.to_numeric(
        pd.Series([twii.get("ma60")] * len(unified), index=unified.index),
        errors="coerce",
    )

    active = unified["signal_type"].ne(SIGNAL_TYPE_NONE)
    if state == STATE_CLOSED or action == ACTION_BLOCK:
        block = active
        if block.any():
            unified.loc[block, "tradability_status"] = TRADABILITY_STATUS_BLOCKED
            unified.loc[block, "tradability_blocked"] = True
            unified.loc[block, "signal_type"] = SIGNAL_TYPE_NONE
            _append_tradability_reason(unified, block, TRADABILITY_REASON_MARKET_REGIME_CLOSED)
        return unified

    if state == STATE_CAUTION or action == ACTION_LIMIT:
        rank_20d = pd.to_numeric(unified.get("rank_20d"), errors="coerce")
        keep = rank_20d.notna() & rank_20d.le(CAUTION_MAX_RANK_20D)
        block = active & ~keep
        downgrade_dual = active & keep & unified["signal_type"].eq(SIGNAL_TYPE_DUAL)
        if block.any():
            unified.loc[block, "tradability_status"] = TRADABILITY_STATUS_BLOCKED
            unified.loc[block, "tradability_blocked"] = True
            unified.loc[block, "signal_type"] = SIGNAL_TYPE_NONE
            _append_tradability_reason(unified, block, TRADABILITY_REASON_MARKET_REGIME_CAUTION)
        if downgrade_dual.any():
            unified.loc[downgrade_dual, "signal_type"] = SIGNAL_TYPE_20D_ONLY
            _append_tradability_reason(
                unified,
                downgrade_dual,
                TRADABILITY_REASON_MARKET_REGIME_CAUTION,
            )
        return unified

    return unified


def _apply_tradability_gate(unified: pd.DataFrame, prediction_date: str) -> pd.DataFrame:
    unified = unified.copy()
    entry_date = _next_business_day(prediction_date)
    report = _load_special_status_report()
    data_status = str(report.get("status") or "UNKNOWN").upper()
    gate_action = str(report.get("gate_action") or "")

    unified["signal_type_before_tradability"] = unified["signal_type"]
    unified["tradability_check_date"] = entry_date
    unified["tradability_data_status"] = data_status
    unified["tradability_gate_action"] = gate_action
    unified["tradability_status"] = TRADABILITY_STATUS_OPEN
    unified["tradability_blocked"] = False
    unified["tradability_reason"] = ""
    unified["is_disposition"] = False
    unified["is_attention"] = False
    unified["is_full_delivery"] = False
    unified["is_suspended"] = False

    unified = _apply_liquidity_gate(unified)
    if _entry_ma5_guard_enabled():
        unified = _apply_ma5_entry_guard(unified)
    unified = _apply_overheat_risk_gate(unified)
    unified = _apply_alpha_win_prob_gate(unified)
    unified = _apply_market_regime_gate(unified, prediction_date=prediction_date)
    tquant_reference_gate_enabled = _tquant_reference_gate_enabled()
    unified = _apply_entry_benchmark_guard(unified, enforce=not tquant_reference_gate_enabled)
    unified = _apply_tquant_reference_gate(unified, prediction_date=prediction_date)

    disposition_set = _disposition_set_for_entry_date(entry_date)
    if not disposition_set:
        return unified

    is_disposition = unified["ticker"].astype(str).str.strip().isin(disposition_set)
    if not is_disposition.any():
        return unified

    unified.loc[is_disposition, "is_disposition"] = True
    unified.loc[is_disposition, "tradability_status"] = TRADABILITY_STATUS_BLOCKED
    unified.loc[is_disposition, "tradability_blocked"] = True
    unified.loc[is_disposition, "signal_type"] = SIGNAL_TYPE_NONE
    _append_tradability_reason(unified, is_disposition, TRADABILITY_REASON_DISPOSITION)
    return unified


def _attach_public_market_context_metadata(unified: pd.DataFrame, prediction_date: str) -> pd.DataFrame:
    """Attach latest public-context quality metadata without changing gates."""
    out = unified.copy()
    try:
        quality = evaluate_public_market_context(as_of_date=prediction_date)
        flat = compact_public_market_context_columns(quality)
    except Exception as exc:  # pragma: no cover - defensive reporting path
        flat = {
            "public_market_context_status": "error",
            "public_market_context_generated_at": None,
            "public_market_context_preflight_pass": False,
            "public_market_context_usage_mode": "unavailable",
            "public_market_context_time_basis": "latest_snapshot_not_point_in_time",
            "public_market_context_model_feature_readiness": "not_ready",
            "public_market_context_model_feature_gap": f"{type(exc).__name__}: {exc}",
            "public_market_context_stale_count": 0,
            "public_market_context_missing_field_count": 0,
            "public_market_context_required_failures": "",
            "public_market_context_stale_datasets": "",
            "public_market_context_missing_field_datasets": "",
        }
    for column in PUBLIC_MARKET_CONTEXT_COLUMNS:
        out[column] = flat.get(column)
    return out


def _attach_selection_model_diagnostics(unified: pd.DataFrame) -> pd.DataFrame:
    """Explain which model layer selected each row and where advisory gaps remain."""
    out = unified.copy()
    index = out.index
    signal_type = out.get("signal_type", pd.Series(SIGNAL_TYPE_NONE, index=index)).fillna(SIGNAL_TYPE_NONE).astype(str)
    rank_20d = pd.to_numeric(out.get("rank_20d", pd.Series(np.nan, index=index)), errors="coerce")
    rank_t1 = pd.to_numeric(out.get("rank_t1", pd.Series(np.nan, index=index)), errors="coerce")
    prob_edge = pd.to_numeric(out.get("prob_edge", pd.Series(np.nan, index=index)), errors="coerce")
    edge_floor = pd.to_numeric(
        out.get("entry_benchmark_prob_edge_floor", pd.Series(ENTRY_BENCHMARK_PROB_EDGE_FLOOR, index=index)),
        errors="coerce",
    ).fillna(ENTRY_BENCHMARK_PROB_EDGE_FLOOR)
    entry_pass = _coerce_bool_series(out.get("entry_benchmark_guard_pass"), index)
    tquant_enabled = _coerce_bool_series(out.get("tquant_reference_gate_enabled"), index)
    tquant_pass = _coerce_bool_series(out.get("tquant_reference_gate_pass"), index)

    has_20d = rank_20d.notna()
    has_t1 = rank_t1.notna()
    active = signal_type.ne(SIGNAL_TYPE_NONE)
    weak_ml_edge_under_tquant = active & has_20d & tquant_enabled & tquant_pass & prob_edge.lt(edge_floor)

    primary = pd.Series("none", index=index, dtype="object")
    primary.loc[has_t1 & ~has_20d] = "t1_model"
    primary.loc[has_20d & ~tquant_enabled] = "entry_benchmark_guard"
    primary.loc[has_20d & tquant_enabled] = "tquant_reference_gate"

    alignment = pd.Series("blocked_or_none", index=index, dtype="object")
    alignment.loc[active & has_t1 & ~has_20d] = "t1_only"
    alignment.loc[active & has_20d & ~tquant_enabled & entry_pass] = "entry_benchmark_pass"
    alignment.loc[active & has_20d & tquant_enabled & tquant_pass & entry_pass] = "tquant_and_entry_benchmark_aligned"
    alignment.loc[active & has_20d & tquant_enabled & tquant_pass & ~entry_pass] = "tquant_override_entry_benchmark"
    alignment.loc[has_20d & tquant_enabled & ~tquant_pass] = "tquant_reject"

    data_gap = pd.Series("", index=index, dtype="object")
    data_gap.loc[weak_ml_edge_under_tquant] = "weak_ml_edge_under_tquant"
    public_gap = out.get(
        "public_market_context_model_feature_gap",
        pd.Series("", index=index, dtype="object"),
    ).fillna("").astype(str)
    data_gap = [
        combine_block_reasons(left, right)
        for left, right in zip(data_gap.tolist(), public_gap.tolist())
    ]

    reason = pd.Series("No active selection.", index=index, dtype="object")
    reason.loc[alignment.eq("t1_only")] = "Selected by T+1 model only."
    reason.loc[alignment.eq("entry_benchmark_pass")] = "20D selected with entry benchmark support."
    reason.loc[alignment.eq("tquant_and_entry_benchmark_aligned")] = (
        "20D selected with TQuant reference support and entry benchmark confirmation."
    )
    reason.loc[alignment.eq("tquant_override_entry_benchmark")] = (
        "20D selected by TQuant reference gate; entry benchmark remains advisory and did not confirm."
    )
    reason.loc[alignment.eq("tquant_reject")] = (
        "20D candidate rejected or downgraded by TQuant reference gate."
    )

    out["selection_model_primary_layer"] = primary
    out["selection_model_alignment"] = alignment
    out["selection_model_weak_ml_edge_under_tquant"] = weak_ml_edge_under_tquant.fillna(False).astype(bool)
    out["selection_model_data_gap"] = data_gap
    out["selection_model_reason"] = reason
    return out


def _finalize_unified_df(
    unified: pd.DataFrame,
    prediction_date: str,
    source_20d_path: str,
    source_t1_path: str,
    production_gate: dict[str, object],
) -> pd.DataFrame:
    if unified.empty:
        empty_cols = [
            "prediction_date",
            "ticker",
            "signal_type",
            "signal_type_before_gate",
            "signal_type_before_tradability",
            "target_units",
            "target_weight_ratio",
            "target_weight_ratio_before_futures_settlement_cap",
            "futures_settlement_window",
            "futures_settlement_phase",
            "futures_settlement_date",
            "futures_settlement_action",
            "futures_settlement_weight_multiplier",
            *MACRO_STRATEGY_COLUMNS,
            "route_priority",
            "rank_20d",
            "rank_t1",
            "recommendation_20d",
            "recommendation_t1",
            "signal_20d",
            "pred_return_20d",
            *SHORTWAVE_OUTPUT_COLS,
            *SHORTWAVE_20D_COLUMNS,
            "risk_adjusted_return",
            "risk_adjusted_return_pre_penalty",
            "leaderboard_score_pre_penalty",
            "penalty_overlay_enabled",
            "penalty_overlay_version",
            "penalty_overlay_score",
            "penalty_overlay_total",
            "penalty_overlay_total_before_cap",
            "penalty_foreign_selling",
            "penalty_gap_risk",
            "penalty_quality",
            "penalty_beta",
            "penalty_quality_cap_applied",
            "penalty_hard_block",
            "penalty_overlay_reason",
            "guardrail_blocked",
            "guardrail_trigger_reason",
            "guardrail_loose_mode",
            "guardrail_intercept_rate_top50",
            "guardrail_dual_track_fail_rate_top50",
            "guardrail_monitor_top_n",
            "guardrail_blocked_20d",
            "guardrail_trigger_reason_20d",
            "guardrail_loose_mode_20d",
            "guardrail_intercept_rate_top50_20d",
            "guardrail_dual_track_fail_rate_top50_20d",
            "guardrail_monitor_top_n_20d",
            "alpha_win_prob_20d",
            "alpha_win_prob_percentile_20d",
            "alpha_win_prob_rank_20d",
            "alpha_win_gate_mode",
            "alpha_win_gate_threshold",
            *VALIDATED_CHIP_MOMENTUM_DIAGNOSTIC_COLS,
            "entry_benchmark_guard_enabled",
            "entry_benchmark_guard_enforced",
            "entry_benchmark_guard_version",
            "entry_benchmark_prob_edge_floor",
            "entry_benchmark_strong_vote_min",
            "entry_benchmark_trend_vote",
            "entry_benchmark_flow_vote",
            "entry_benchmark_gap_vote",
            "entry_benchmark_quality_vote",
            "entry_benchmark_risk_vote",
            "entry_benchmark_edge_vote",
            "entry_benchmark_votes",
            "entry_benchmark_consensus",
            "entry_benchmark_guard_pass",
            *TQUANT_REFERENCE_COLUMNS,
            "prob_edge",
            "up_prob",
            "flat_prob",
            "down_prob",
            "hit_prob_3pct",
            "t1_score",
            "take_profit",
            "stop_loss",
            "close_20d",
            "close_t1",
            "close_ref",
            "open_20d",
            "raw_volume_20d",
            "amount_ma20_raw_20d",
            "volume_today",
            "avg_5d_volume",
            "avg_20d_volume",
            "avg_5d_amount",
            "avg_20d_amount",
            "price_vs_ma60",
            "price_vs_ma5",
            "price_vs_ma20",
            "beta_60",
            "two_stage_rank",
            "volume_today_20d",
            "avg_5d_volume_20d",
            "avg_20d_volume_20d",
            "avg_5d_amount_20d",
            "avg_20d_amount_20d",
            "price_vs_ma60_20d",
            "price_vs_ma5_20d",
            "price_vs_ma20_20d",
            "beta_60_20d",
            "two_stage_rank_20d",
            "pe_ratio_20d",
            "gap_pct_20d",
            "gap_up_avg_5d_20d",
            "foreign_cumsum_20d_raw_20d",
            "foreign_cumsum_20d_norm_raw_20d",
            *[f"{column}_20d" for column in VALIDATED_CHIP_MOMENTUM_COMPONENTS],
            "roa_annualized_20d",
            "roe_annualized_20d",
            "avg_5d_volume_t1",
            "avg_20d_volume_t1",
            "avg_5d_amount_t1",
            "avg_20d_amount_t1",
            "price_vs_ma60_t1",
            "beta_60_t1",
            "sector",
            "sector_20d",
            "sector_t1",
            "setup_tags_t1",
            "risk_tags_20d",
            "risk_tags_t1",
            "veto_reason_t1",
            "t1_block_reason",
            "market_regime_state",
            "market_regime_action",
            "market_regime_reason",
            "market_regime_breadth_ma20",
            "market_regime_twii_close",
            "market_regime_twii_ma20",
            "market_regime_twii_ma60",
            "overheat_threshold",
            "overheat_threshold_group",
            "overheat_threshold_label",
            "tradability_check_date",
            "tradability_data_status",
            "tradability_gate_action",
            "tradability_status",
            "tradability_blocked",
            "tradability_reason",
            "expensive_momentum_risk",
            "expensive_momentum_pe_ratio",
            "expensive_momentum_price_vs_ma60",
            "target_weight_ratio_before_expensive_momentum_cap",
            "is_disposition",
            "is_attention",
            "is_full_delivery",
            "is_suspended",
            "production_gate_status",
            "production_gate_reason",
            *PUBLIC_MARKET_CONTEXT_COLUMNS,
            *SELECTION_MODEL_DIAGNOSTIC_COLUMNS,
            "source_20d_file",
            "source_t1_file",
        ]
        return pd.DataFrame(columns=empty_cols)

    unified = _apply_mece_signal_type(unified)
    unified["signal_type_before_gate"] = unified["signal_type"]
    unified = _apply_t1_risk_blocks(unified)
    unified = _apply_tradability_gate(unified, prediction_date=prediction_date)
    unified = _assign_target_fields(unified)
    unified = _apply_expensive_momentum_gate(unified)
    unified = _apply_futures_settlement_overlay(unified, prediction_date=prediction_date)
    unified = _apply_macro_strategy_overlay(unified, prediction_date=prediction_date)

    unified["prediction_date"] = prediction_date
    unified["close_ref"] = _coalesce_columns(unified, "close_20d", "close_t1")
    unified["volume_today"] = _coalesce_columns(unified, "volume_today_20d", "volume_today_t1")
    unified["avg_5d_volume"] = _coalesce_columns(unified, "avg_5d_volume_20d", "avg_5d_volume_t1")
    unified["avg_20d_volume"] = _coalesce_columns(unified, "avg_20d_volume_20d", "avg_20d_volume_t1")
    unified["avg_5d_amount"] = _coalesce_columns(unified, "avg_5d_amount_20d", "avg_5d_amount_t1")
    unified["avg_20d_amount"] = _coalesce_columns(unified, "avg_20d_amount_20d", "avg_20d_amount_t1")
    unified["price_vs_ma60"] = _coalesce_columns(unified, "price_vs_ma60_20d", "price_vs_ma60_t1")
    unified["price_vs_ma5"] = _coalesce_columns(unified, "price_vs_ma5_20d", "price_vs_ma5_t1")
    unified["price_vs_ma20"] = _coalesce_columns(unified, "price_vs_ma20_20d", "price_vs_ma20_t1")
    unified["beta_60"] = _coalesce_columns(unified, "beta_60_20d", "beta_60_t1")
    unified["two_stage_rank"] = _coalesce_columns(unified, "two_stage_rank_20d", "two_stage_rank_t1")
    unified["pe_ratio"] = _coalesce_many(unified, "pe_ratio_20d", "pe_ratio")
    unified["sector"] = _coalesce_columns(unified, "sector_20d", "sector_t1")
    unified["group_code"] = _coalesce_columns(unified, "group_code_20d", "group_code_t1")
    unified["group_name"] = _coalesce_columns(unified, "group_name_20d", "group_name_t1")
    unified["group_code"] = unified["group_code"].fillna("OTHER")
    unified["group_name"] = unified["group_name"].fillna("Other / uncapped")
    unified["group_cap_applied"] = (
        _coalesce_many(unified, "group_cap_applied_20d", "group_cap_applied")
        .fillna(False)
        .astype(bool)
    )
    unified["guardrail_blocked"] = pd.to_numeric(
        _coalesce_many(unified, "guardrail_blocked_20d", "guardrail_blocked"),
        errors="coerce",
    ).fillna(0).astype(np.int8)
    unified["guardrail_trigger_reason"] = _normalize_guardrail_reason_series(
        unified["guardrail_blocked"],
        _coalesce_many(unified, "guardrail_trigger_reason_20d", "guardrail_trigger_reason"),
        _coalesce_many(unified, "recommendation_20d", "recommendation_t1"),
    )
    unified["guardrail_loose_mode"] = pd.to_numeric(
        _coalesce_many(unified, "guardrail_loose_mode_20d", "guardrail_loose_mode"),
        errors="coerce",
    )
    unified["guardrail_intercept_rate_top50"] = pd.to_numeric(
        _coalesce_many(unified, "guardrail_intercept_rate_top50_20d", "guardrail_intercept_rate_top50"),
        errors="coerce",
    )
    unified["guardrail_dual_track_fail_rate_top50"] = pd.to_numeric(
        _coalesce_many(unified, "guardrail_dual_track_fail_rate_top50_20d", "guardrail_dual_track_fail_rate_top50"),
        errors="coerce",
    )
    unified["guardrail_monitor_top_n"] = pd.to_numeric(
        _coalesce_many(unified, "guardrail_monitor_top_n_20d", "guardrail_monitor_top_n"),
        errors="coerce",
    )
    for column in SHORTWAVE_OUTPUT_COLS:
        unified[column] = _coalesce_many(unified, f"{column}_20d", column)
    unified["production_gate_status"] = str(production_gate.get("status") or "UNKNOWN")
    unified["production_gate_reason"] = str(production_gate.get("reason") or "")
    unified["source_20d_file"] = os.path.basename(source_20d_path)
    unified["source_t1_file"] = os.path.basename(source_t1_path)
    unified = _attach_public_market_context_metadata(unified, prediction_date=prediction_date)
    unified = _attach_selection_model_diagnostics(unified)

    for sort_col in ["route_priority", "risk_adjusted_return", "t1_score", "rank_20d", "rank_t1", "ticker"]:
        if sort_col not in unified.columns:
            unified[sort_col] = pd.NA
    unified = unified.sort_values(
        [
            "route_priority",
            "risk_adjusted_return",
            "t1_score",
            "rank_20d",
            "rank_t1",
            "ticker",
        ],
        ascending=[False, False, False, True, True, True],
        na_position="last",
    ).reset_index(drop=True)

    ordered_cols = [
        "prediction_date",
        "ticker",
        "signal_type",
        "signal_type_before_gate",
        "signal_type_before_tradability",
        "target_units",
        "target_weight_ratio",
        "target_weight_ratio_before_futures_settlement_cap",
        "futures_settlement_window",
        "futures_settlement_phase",
        "futures_settlement_date",
        "futures_settlement_action",
        "futures_settlement_weight_multiplier",
        *MACRO_STRATEGY_COLUMNS,
        "route_priority",
        "rank_20d",
        "rank_t1",
        "recommendation_20d",
        "recommendation_t1",
        "signal_20d",
        "pred_return_20d",
        *SHORTWAVE_OUTPUT_COLS,
        *SHORTWAVE_20D_COLUMNS,
        "risk_adjusted_return",
        "risk_adjusted_return_pre_penalty",
        "leaderboard_score_pre_penalty",
        "penalty_overlay_enabled",
        "penalty_overlay_version",
        "penalty_overlay_score",
        "penalty_overlay_total",
        "penalty_overlay_total_before_cap",
        "penalty_foreign_selling",
        "penalty_gap_risk",
        "penalty_quality",
        "penalty_beta",
        "penalty_quality_cap_applied",
        "penalty_hard_block",
        "penalty_overlay_reason",
        "guardrail_blocked",
        "guardrail_trigger_reason",
        "guardrail_loose_mode",
        "guardrail_intercept_rate_top50",
        "guardrail_dual_track_fail_rate_top50",
        "guardrail_monitor_top_n",
        "guardrail_blocked_20d",
        "guardrail_trigger_reason_20d",
        "guardrail_loose_mode_20d",
        "guardrail_intercept_rate_top50_20d",
        "guardrail_dual_track_fail_rate_top50_20d",
        "guardrail_monitor_top_n_20d",
        "alpha_win_prob_20d",
        "alpha_win_prob_percentile_20d",
        "alpha_win_prob_rank_20d",
        "alpha_win_gate_mode",
        "alpha_win_gate_threshold",
        *VALIDATED_CHIP_MOMENTUM_DIAGNOSTIC_COLS,
        "entry_benchmark_guard_enabled",
        "entry_benchmark_guard_enforced",
        "entry_benchmark_guard_version",
        "entry_benchmark_prob_edge_floor",
        "entry_benchmark_strong_vote_min",
        "entry_benchmark_trend_vote",
        "entry_benchmark_flow_vote",
        "entry_benchmark_gap_vote",
        "entry_benchmark_quality_vote",
        "entry_benchmark_risk_vote",
        "entry_benchmark_edge_vote",
        "entry_benchmark_votes",
        "entry_benchmark_consensus",
        "entry_benchmark_guard_pass",
        *TQUANT_REFERENCE_COLUMNS,
        "prob_edge",
        "up_prob",
        "flat_prob",
        "down_prob",
        "hit_prob_3pct",
        "t1_score",
        "take_profit",
        "stop_loss",
        "close_20d",
        "close_t1",
        "close_ref",
        "open_20d",
        "raw_volume_20d",
        "amount_ma20_raw_20d",
        "volume_today",
        "avg_5d_volume",
        "avg_20d_volume",
        "avg_5d_amount",
        "avg_20d_amount",
        "price_vs_ma60",
        "price_vs_ma5",
        "price_vs_ma20",
        "beta_60",
        "two_stage_rank",
        "volume_today_20d",
        "avg_5d_volume_20d",
        "avg_20d_volume_20d",
        "avg_5d_amount_20d",
        "avg_20d_amount_20d",
        "price_vs_ma60_20d",
        "price_vs_ma5_20d",
        "price_vs_ma20_20d",
        "beta_60_20d",
        "two_stage_rank_20d",
        "pe_ratio_20d",
        "pe_ratio",
        "gap_pct_20d",
        "gap_up_avg_5d_20d",
        "foreign_cumsum_20d_raw_20d",
        "foreign_cumsum_20d_norm_raw_20d",
        *[f"{column}_20d" for column in VALIDATED_CHIP_MOMENTUM_COMPONENTS],
        "roa_annualized_20d",
        "roe_annualized_20d",
        "avg_5d_volume_t1",
        "avg_20d_volume_t1",
        "avg_5d_amount_t1",
        "avg_20d_amount_t1",
        "price_vs_ma60_t1",
        "beta_60_t1",
        "sector",
        "sector_20d",
        "sector_t1",
        "group_code",
        "group_name",
        "group_cap_applied",
        "group_code_20d",
        "group_name_20d",
        "group_cap_applied_20d",
        "group_code_t1",
        "group_name_t1",
        "setup_tags_t1",
        "risk_tags_20d",
        "risk_tags_t1",
        "veto_reason_t1",
        "t1_block_reason",
        "market_regime_state",
        "market_regime_action",
        "market_regime_reason",
        "market_regime_breadth_ma20",
        "market_regime_twii_close",
        "market_regime_twii_ma20",
        "market_regime_twii_ma60",
        "overheat_threshold",
        "overheat_threshold_group",
        "overheat_threshold_label",
        "tradability_check_date",
        "tradability_data_status",
        "tradability_gate_action",
        "tradability_status",
        "tradability_blocked",
        "tradability_reason",
        "expensive_momentum_risk",
        "expensive_momentum_pe_ratio",
        "expensive_momentum_price_vs_ma60",
        "target_weight_ratio_before_expensive_momentum_cap",
        "is_disposition",
        "is_attention",
        "is_full_delivery",
        "is_suspended",
        "production_gate_status",
        "production_gate_reason",
        *PUBLIC_MARKET_CONTEXT_COLUMNS,
        *SELECTION_MODEL_DIAGNOSTIC_COLUMNS,
        "source_20d_file",
        "source_t1_file",
    ]
    for column in ordered_cols:
        if column not in unified.columns:
            unified[column] = pd.NA
    return unified.loc[:, ordered_cols]


def build_unified_signals(
    pred_date: str | None = None,
    prediction_20d_path: str | None = None,
    prediction_t1_path: str | None = None,
    top_n_20d: int = DEFAULT_TOP_N_20D,
    output_prefix: str = UNIFIED_OUTPUT_PREFIX,
    penalty_overlay: bool = False,
    penalty_overlay_version: str = PENALTY_OVERLAY_DEFAULT_VERSION,
    disable_t1: bool = False,
    verbose: bool = True,
) -> UnifiedBuildResult:
    """Build and atomically persist the daily unified signal artifact."""
    source_20d_path = _resolve_prediction_20d_path(prediction_20d_path, pred_date)
    resolved_date = pred_date or _extract_date_from_filename(source_20d_path, PRED20_EXPLICIT_RE)
    pred20_df = _load_csv(source_20d_path, expected_date=resolved_date, label="20D")
    if disable_t1:
        pred20_dates = pred20_df["date"].dropna().astype(str).unique().tolist()
        if len(pred20_dates) != 1:
            raise ValueError(f"20D prediction contains multiple dates: {pred20_dates}")
        prediction_date = pred20_dates[0]
        source_t1_path = "T1_DISABLED"
        production_gate = _evaluate_20d_production_gate()
        t1_candidates = _empty_t1_candidates()
    else:
        source_t1_path = _resolve_prediction_t1_path(prediction_t1_path, resolved_date)
        pred_t1_df = _load_csv(source_t1_path, expected_date=resolved_date, label="T+1")
        prediction_date = _validate_prediction_dates(pred20_df, pred_t1_df)
        production_gate = _evaluate_20d_production_gate()
        t1_production_gate = evaluate_t1_production_gate()
        t1_candidates = _build_t1_candidates(pred_t1_df, production_gate=t1_production_gate)

    d20_candidates = _build_20d_candidates(
        pred20_df,
        top_n=top_n_20d,
        penalty_overlay=penalty_overlay,
        penalty_overlay_version=penalty_overlay_version,
    )

    unified = d20_candidates.merge(
        t1_candidates,
        on="ticker",
        how="outer",
        suffixes=("_20d", "_t1"),
    )
    unified = _finalize_unified_df(
        unified=unified,
        prediction_date=prediction_date,
        source_20d_path=source_20d_path,
        source_t1_path=source_t1_path,
        production_gate=production_gate,
    )

    output_path = os.path.join(MODEL_DIR, f"{output_prefix}_{prediction_date}.csv")
    tmp_path = output_path + ".tmp"
    unified.to_csv(tmp_path, index=False, encoding="utf-8-sig")
    os.replace(tmp_path, output_path)

    dual_count = int((unified["signal_type"] == SIGNAL_TYPE_DUAL).sum()) if not unified.empty else 0
    d20_only_count = int((unified["signal_type"] == SIGNAL_TYPE_20D_ONLY).sum()) if not unified.empty else 0
    t1_only_count = int((unified["signal_type"] == SIGNAL_TYPE_T1_ONLY).sum()) if not unified.empty else 0
    total_units = int(unified["target_units"].sum()) if not unified.empty else 0
    downgraded_dual_count = int(
        (
            unified.get("signal_type_before_gate", pd.Series(dtype=object)).eq(SIGNAL_TYPE_DUAL)
            & unified.get("signal_type", pd.Series(dtype=object)).eq(SIGNAL_TYPE_20D_ONLY)
        ).sum()
    ) if not unified.empty else 0
    tradability_blocked_count = int(
        unified.get("tradability_blocked", pd.Series(dtype=bool)).fillna(False).astype(bool).sum()
    ) if not unified.empty else 0

    if verbose:
        print(f"[unified] 20D source: {os.path.basename(source_20d_path)}")
        if disable_t1:
            print("[unified] T+1 source: disabled (20D-only production mode)")
        else:
            print(f"[unified] T+1 source: {os.path.basename(source_t1_path)}")
        if penalty_overlay:
            print(f"[unified] penalty overlay: enabled ({penalty_overlay_version})")
        print(
            "[unified] production gate: "
            f"{production_gate.get('status')} | {production_gate.get('reason') or 'ok'}"
        )
        print(f"[unified] wrote {os.path.basename(output_path)} with {len(unified)} names")
        print(
            f"[unified] counts: Dual={dual_count}, "
            f"20D_only={d20_only_count}, T1_only={t1_only_count}, "
            f"total_units={total_units}, downgraded_dual={downgraded_dual_count}, "
            f"tradability_blocked={tradability_blocked_count}"
        )

    return UnifiedBuildResult(
        output_path=output_path,
        prediction_date=prediction_date,
        total_names=len(unified),
        dual_count=dual_count,
        d20_only_count=d20_only_count,
        t1_only_count=t1_only_count,
        total_units=total_units,
        production_gate_status=str(production_gate.get("status") or "UNKNOWN"),
        production_gate_reason=str(production_gate.get("reason") or ""),
        downgraded_dual_count=downgraded_dual_count,
        tradability_blocked_count=tradability_blocked_count,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Build unified daily signals from 20D and T+1 outputs.")
    parser.add_argument("--date", default=None, help="Prediction date in YYYY-MM-DD.")
    parser.add_argument("--prediction-20d", default=None, help="Explicit 20D predictions_YYYY-MM-DD.csv path.")
    parser.add_argument("--prediction-t1", default=None, help="Explicit predictions_t1_YYYY-MM-DD.csv path.")
    parser.add_argument("--top-n-20d", type=int, default=DEFAULT_TOP_N_20D, help="Top-N 20D pool size.")
    parser.add_argument("--output-prefix", default=UNIFIED_OUTPUT_PREFIX, help="Output filename prefix.")
    parser.add_argument("--penalty-overlay", action="store_true", help="Use penalty overlay net score for 20D ranking.")
    parser.add_argument(
        "--disable-t1",
        action="store_true",
        help="Build a 20D-only production-capable unified artifact without requiring predictions_t1_YYYY-MM-DD.csv.",
    )
    parser.add_argument(
        "--penalty-overlay-version",
        choices=sorted(PENALTY_OVERLAY_VERSIONS),
        default=PENALTY_OVERLAY_DEFAULT_VERSION,
        help="Penalty overlay rule version. v1 is the champion/default; tuned is research-only.",
    )
    args = parser.parse_args()

    build_unified_signals(
        pred_date=args.date,
        prediction_20d_path=args.prediction_20d,
        prediction_t1_path=args.prediction_t1,
        top_n_20d=args.top_n_20d,
        output_prefix=args.output_prefix,
        penalty_overlay=args.penalty_overlay,
        penalty_overlay_version=args.penalty_overlay_version,
        disable_t1=args.disable_t1,
        verbose=True,
    )


if __name__ == "__main__":
    main()
