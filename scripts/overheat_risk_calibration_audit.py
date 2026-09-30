"""Calibrate the V2 unified OVERHEAT_RISK gate.

This is a research-only audit. It reads canonical `unified_signals` artifacts
and asks whether the production overheat gate is killing too much signal supply
in trending regimes. It does not mutate model files, ledgers, or production
thresholds.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

BASE_DIR = Path(os.environ.get("STOCK_BASE_DIR") or Path(__file__).resolve().parents[1])
sys.path.insert(0, str(BASE_DIR))

from ml.config import DAILY_K_DIR, INDEX_DIR, MODEL_DIR, REPORT_DIR  # noqa: E402

PRED_RE = re.compile(r"^predictions_(\d{4}-\d{2}-\d{2})\.csv$")
SIGNAL_RE = re.compile(r"^unified_signals_(\d{4}-\d{2}-\d{2})\.csv$")

DEFAULT_START = "2026-04-09"
DEFAULT_END = "2026-04-30"
DEFAULT_MARK_DATE = "2026-04-30"
DEFAULT_LOOKBACK_DAYS = 60
DEFAULT_OUTPUT_STEM = "overheat_risk_calibration_20260409_20260430"

REASON_OVERHEAT = "OVERHEAT_RISK"
REASON_MARKET_CAUTION = "MARKET_REGIME_CAUTION"


@dataclass(frozen=True)
class ThresholdVariant:
    name: str
    metric: str
    fixed_threshold: float | None
    description: str


VARIANTS = [
    ThresholdVariant(
        name="ma60_30_strict_proxy",
        metric="price_vs_ma60_used",
        fixed_threshold=0.30,
        description="Strict proxy using MA60 at 30%; useful because CLAUDE/recommendation docs mention 30%, but this is stricter than production.",
    ),
    ThresholdVariant(
        name="ma60_35",
        metric="price_vs_ma60_used",
        fixed_threshold=0.35,
        description="Intermediate MA60 threshold.",
    ),
    ThresholdVariant(
        name="ma60_40_production",
        metric="price_vs_ma60_used",
        fixed_threshold=0.40,
        description="Current unified-signals production OVERHEAT_RISK threshold.",
    ),
    ThresholdVariant(
        name="ma60_regime_adaptive_50_30_25",
        metric="price_vs_ma60_used",
        fixed_threshold=None,
        description="Adaptive MA60 threshold: trending 50%, ranging 30%, CAUTION 25%.",
    ),
]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit OVERHEAT_RISK threshold calibration.")
    parser.add_argument("--start", default=DEFAULT_START)
    parser.add_argument("--end", default=DEFAULT_END)
    parser.add_argument("--mark-date", default=DEFAULT_MARK_DATE)
    parser.add_argument("--lookback-days", type=int, default=DEFAULT_LOOKBACK_DAYS)
    parser.add_argument("--output-stem", default=DEFAULT_OUTPUT_STEM)
    return parser.parse_args(argv)


def _safe_float(value: Any) -> float | None:
    try:
        if pd.isna(value):
            return None
        number = float(value)
    except Exception:
        return None
    return number if math.isfinite(number) else None


def _pct(value: Any, digits: int = 2) -> str:
    number = _safe_float(value)
    if number is None:
        return "-"
    return f"{number * 100:+.{digits}f}%"


def _plain_pct(value: Any, digits: int = 2) -> str:
    number = _safe_float(value)
    if number is None:
        return "-"
    return f"{number * 100:.{digits}f}%"


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        number = float(value)
        return number if math.isfinite(number) else None
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    return value


def _list_dated_files(directory: Path, pattern: re.Pattern[str], glob_pattern: str) -> dict[str, Path]:
    rows: dict[str, Path] = {}
    for path in directory.glob(glob_pattern):
        match = pattern.match(path.name)
        if match:
            rows[match.group(1)] = path
    return rows


def available_signal_dates(start: str, end: str) -> list[str]:
    predictions = _list_dated_files(Path(MODEL_DIR), PRED_RE, "predictions_*.csv")
    signals = _list_dated_files(Path(MODEL_DIR), SIGNAL_RE, "unified_signals_*.csv")
    return [date for date in sorted(set(predictions) & set(signals)) if start <= date <= end]


def available_prediction_dates(end: str, lookback_days: int) -> list[str]:
    predictions = _list_dated_files(Path(MODEL_DIR), PRED_RE, "predictions_*.csv")
    start = (pd.Timestamp(end) - timedelta(days=lookback_days)).date().isoformat()
    return [date for date in sorted(predictions) if start <= date <= end]


def read_prediction(date_value: str) -> pd.DataFrame:
    path = Path(MODEL_DIR) / f"predictions_{date_value}.csv"
    df = pd.read_csv(path, encoding="utf-8-sig", dtype={"ticker": str})
    df["ticker"] = df["ticker"].astype(str).str.upper()
    if "sector" not in df.columns:
        df["sector"] = "UNKNOWN"
    df["sector"] = df["sector"].fillna("UNKNOWN").astype(str)
    for column in ["price_vs_ma20", "price_vs_ma60", "beta_60", "pred_return_20d", "leaderboard_score", "prob_edge"]:
        if column in df.columns:
            df[column] = pd.to_numeric(df[column], errors="coerce")
    return df


def read_signal(date_value: str) -> pd.DataFrame:
    path = Path(MODEL_DIR) / f"unified_signals_{date_value}.csv"
    df = pd.read_csv(path, encoding="utf-8-sig", dtype={"ticker": str})
    df["ticker"] = df["ticker"].astype(str).str.upper()
    if "sector" not in df.columns:
        df["sector"] = "UNKNOWN"
    df["sector"] = df["sector"].fillna("UNKNOWN").astype(str)
    numeric_cols = [
        "target_units",
        "target_weight_ratio",
        "rank_20d",
        "price_vs_ma60",
        "price_vs_ma60_20d",
        "beta_60",
        "beta_60_20d",
        "market_regime_twii_close",
        "market_regime_twii_ma20",
        "market_regime_twii_ma60",
    ]
    for column in numeric_cols:
        if column in df.columns:
            df[column] = pd.to_numeric(df[column], errors="coerce")
    return df


def split_reasons(reason_text: Any) -> set[str]:
    if reason_text is None or pd.isna(reason_text):
        return set()
    return {part.strip() for part in str(reason_text).split(";") if part.strip()}


def first_nonempty(df: pd.DataFrame, column: str, default: str = "UNKNOWN") -> str:
    if column not in df.columns:
        return default
    values = df[column].dropna().astype(str)
    values = values[values.ne("")]
    return values.iloc[0] if not values.empty else default


def coalesce_numeric(df: pd.DataFrame, columns: list[str]) -> pd.Series:
    out = pd.Series(np.nan, index=df.index, dtype="float64")
    for column in columns:
        if column in df.columns:
            out = out.combine_first(pd.to_numeric(df[column], errors="coerce"))
    return out


def enrich_rank20(date_value: str) -> pd.DataFrame:
    signal = read_signal(date_value)
    pred = read_prediction(date_value)
    ranked = signal.loc[signal["rank_20d"].notna()].copy()
    ranked["rank_20d"] = pd.to_numeric(ranked["rank_20d"], errors="coerce")
    pred_cols = [
        column
        for column in [
            "ticker",
            "price_vs_ma20",
            "price_vs_ma60",
            "beta_60",
            "pred_return_20d",
            "leaderboard_score",
            "prob_edge",
            "sector",
        ]
        if column in pred.columns
    ]
    ranked = ranked.merge(
        pred[pred_cols].rename(
            columns={
                "price_vs_ma20": "price_vs_ma20_pred",
                "price_vs_ma60": "price_vs_ma60_pred",
                "beta_60": "beta_60_pred",
                "sector": "sector_pred",
            }
        ),
        on="ticker",
        how="left",
    )
    ranked["prediction_date"] = date_value
    ranked["sector"] = ranked["sector"].fillna(ranked.get("sector_pred")).fillna("UNKNOWN").astype(str)
    ranked["price_vs_ma20_used"] = coalesce_numeric(ranked, ["price_vs_ma20_pred"])
    ranked["price_vs_ma60_used"] = coalesce_numeric(
        ranked,
        ["price_vs_ma60_20d", "price_vs_ma60", "price_vs_ma60_pred"],
    )
    ranked["beta_60_used"] = coalesce_numeric(ranked, ["beta_60_20d", "beta_60", "beta_60_pred"])
    ranked["is_current_active"] = pd.to_numeric(ranked.get("target_units"), errors="coerce").fillna(0).gt(0)
    ranked["reason_set"] = ranked.get("tradability_reason", pd.Series("", index=ranked.index)).apply(split_reasons)
    return ranked.sort_values(["rank_20d", "ticker"]).reset_index(drop=True)


def market_context(rank20: pd.DataFrame) -> dict[str, Any]:
    close = _safe_float(first_numeric_value(rank20, "market_regime_twii_close"))
    ma20 = _safe_float(first_numeric_value(rank20, "market_regime_twii_ma20"))
    ma60 = _safe_float(first_numeric_value(rank20, "market_regime_twii_ma60"))
    action = first_nonempty(rank20, "market_regime_action")
    state = first_nonempty(rank20, "market_regime_state")
    is_caution = state.upper() == "CAUTION" or "LIMIT_20D_TOP10" in action
    is_trending = bool(
        (close is not None)
        and (ma20 is not None)
        and (ma60 is not None)
        and close > ma20
        and ma20 > ma60
    )
    if is_caution:
        adaptive_threshold = 0.25
        regime_bucket = "caution"
    elif is_trending:
        adaptive_threshold = 0.50
        regime_bucket = "trending"
    else:
        adaptive_threshold = 0.30
        regime_bucket = "ranging"
    return {
        "market_regime_state": state,
        "market_regime_action": action,
        "twii_close": close,
        "twii_ma20": ma20,
        "twii_ma60": ma60,
        "regime_bucket": regime_bucket,
        "adaptive_threshold": adaptive_threshold,
        "is_caution_limit": is_caution,
    }


def first_numeric_value(df: pd.DataFrame, column: str) -> float | None:
    if column not in df.columns:
        return None
    values = pd.to_numeric(df[column], errors="coerce").dropna()
    return None if values.empty else float(values.iloc[0])


def threshold_for_variant(variant: ThresholdVariant, context: dict[str, Any]) -> float:
    return float(context["adaptive_threshold"] if variant.fixed_threshold is None else variant.fixed_threshold)


def simulate_variant_for_date(rank20: pd.DataFrame, variant: ThresholdVariant) -> pd.DataFrame:
    context = market_context(rank20)
    threshold = threshold_for_variant(variant, context)
    metric_values = pd.to_numeric(rank20[variant.metric], errors="coerce")
    has_metric = metric_values.notna()
    overheat_pass = has_metric & metric_values.le(threshold)
    market_pass = pd.Series(True, index=rank20.index)
    if context["is_caution_limit"]:
        market_pass = pd.to_numeric(rank20["rank_20d"], errors="coerce").le(10)

    def has_other_block(reason_set: set[str]) -> bool:
        remaining = set(reason_set) - {REASON_OVERHEAT, REASON_MARKET_CAUTION}
        return bool(remaining)

    other_block = rank20["reason_set"].apply(has_other_block)
    selected = rank20.loc[overheat_pass & market_pass & ~other_block].copy()
    selected["variant"] = variant.name
    selected["overheat_metric"] = variant.metric
    selected["overheat_threshold"] = threshold
    selected["regime_bucket"] = context["regime_bucket"]
    selected["variant_weight"] = 0.0 if selected.empty else 1.0 / float(len(selected))
    selected["rescued_from_overheat"] = selected["reason_set"].apply(lambda reasons: REASON_OVERHEAT in reasons)
    return selected


def _load_price_history(ticker: str, cache: dict[str, pd.DataFrame | None]) -> pd.DataFrame | None:
    ticker = str(ticker).upper()
    if ticker in cache:
        return cache[ticker]
    path = Path(DAILY_K_DIR) / f"{ticker}.csv"
    if not path.exists():
        cache[ticker] = None
        return None
    try:
        df = pd.read_csv(path, parse_dates=["Date"], usecols=["Date", "Open", "Close"])
    except Exception:
        cache[ticker] = None
        return None
    df = df.sort_values("Date").reset_index(drop=True)
    cache[ticker] = df
    return df


def _entry_to_mark_return(
    ticker: str,
    prediction_date: str,
    mark_date: str,
    cache: dict[str, pd.DataFrame | None],
) -> tuple[float | None, str | None, str | None]:
    df = _load_price_history(ticker, cache)
    if df is None or df.empty:
        return None, None, None
    pred_ts = pd.Timestamp(prediction_date)
    mark_ts = pd.Timestamp(mark_date)
    entry_rows = df[(df["Date"] > pred_ts) & (df["Date"] <= mark_ts)]
    if entry_rows.empty:
        return None, None, None
    entry = entry_rows.iloc[0]
    mark_rows = df[(df["Date"] >= entry["Date"]) & (df["Date"] <= mark_ts)]
    if mark_rows.empty:
        return None, entry["Date"].date().isoformat(), None
    mark = mark_rows.iloc[-1]
    entry_open = _safe_float(entry["Open"])
    mark_close = _safe_float(mark["Close"])
    if entry_open is None or entry_open <= 0 or mark_close is None:
        return None, entry["Date"].date().isoformat(), mark["Date"].date().isoformat()
    return mark_close / entry_open - 1.0, entry["Date"].date().isoformat(), mark["Date"].date().isoformat()


def _entry_to_forward_return(
    ticker: str,
    prediction_date: str,
    holding_days: int,
    cache: dict[str, pd.DataFrame | None],
) -> tuple[float | None, str | None, str | None, bool]:
    df = _load_price_history(ticker, cache)
    if df is None or df.empty:
        return None, None, None, False
    pred_ts = pd.Timestamp(prediction_date)
    entry_rows = df[df["Date"] > pred_ts]
    if entry_rows.empty:
        return None, None, None, False
    entry = entry_rows.iloc[0]
    forward_rows = df[df["Date"] >= entry["Date"]].reset_index(drop=True)
    if len(forward_rows) < holding_days:
        return None, entry["Date"].date().isoformat(), None, False
    exit_row = forward_rows.iloc[holding_days - 1]
    entry_open = _safe_float(entry["Open"])
    exit_close = _safe_float(exit_row["Close"])
    if entry_open is None or entry_open <= 0 or exit_close is None:
        return None, entry["Date"].date().isoformat(), exit_row["Date"].date().isoformat(), False
    return (
        exit_close / entry_open - 1.0,
        entry["Date"].date().isoformat(),
        exit_row["Date"].date().isoformat(),
        True,
    )


def _twii_return(prediction_date: str, mark_date: str) -> float | None:
    path = Path(INDEX_DIR) / "index_TWII.csv"
    if not path.exists():
        return None
    df = pd.read_csv(path, parse_dates=["Date"], usecols=["Date", "Open", "Close"])
    df = df.sort_values("Date").reset_index(drop=True)
    pred_ts = pd.Timestamp(prediction_date)
    mark_ts = pd.Timestamp(mark_date)
    entry_rows = df[(df["Date"] > pred_ts) & (df["Date"] <= mark_ts)]
    if entry_rows.empty:
        return None
    entry = entry_rows.iloc[0]
    mark_rows = df[(df["Date"] >= entry["Date"]) & (df["Date"] <= mark_ts)]
    if mark_rows.empty:
        return None
    mark = mark_rows.iloc[-1]
    entry_open = _safe_float(entry["Open"])
    mark_close = _safe_float(mark["Close"])
    if entry_open is None or entry_open <= 0 or mark_close is None:
        return None
    return mark_close / entry_open - 1.0


def build_price_distribution(prediction_dates: list[str]) -> tuple[pd.DataFrame, pd.DataFrame]:
    frames = []
    for date_value in prediction_dates:
        pred = read_prediction(date_value)
        pred["prediction_date"] = date_value
        frames.append(pred)
    all_pred = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if all_pred.empty:
        return all_pred, pd.DataFrame()
    rows: list[dict[str, Any]] = []
    for metric in ["price_vs_ma20", "price_vs_ma60"]:
        if metric not in all_pred.columns:
            continue
        for sector, group in all_pred.groupby("sector"):
            values = pd.to_numeric(group[metric], errors="coerce").dropna()
            if values.empty:
                continue
            rows.append(
                {
                    "metric": metric,
                    "sector": sector,
                    "count": int(len(values)),
                    "p50": float(values.quantile(0.50)),
                    "p75": float(values.quantile(0.75)),
                    "p90": float(values.quantile(0.90)),
                    "p95": float(values.quantile(0.95)),
                    "gt_30_pct": float(values.gt(0.30).mean()),
                    "gt_35_pct": float(values.gt(0.35).mean()),
                    "gt_40_pct": float(values.gt(0.40).mean()),
                }
            )
    return all_pred, pd.DataFrame(rows)


def build_daily_simulation(
    signal_dates: list[str],
    mark_date: str,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    price_cache: dict[str, pd.DataFrame | None] = {}
    daily_rows: list[dict[str, Any]] = []
    holding_rows: list[dict[str, Any]] = []
    sector_rows: list[dict[str, Any]] = []

    for date_value in signal_dates:
        rank20 = enrich_rank20(date_value)
        context = market_context(rank20)
        twii = _twii_return(date_value, mark_date)
        rank20_counts = rank20.groupby("sector").size().to_dict()
        for variant in VARIANTS:
            selected = simulate_variant_for_date(rank20, variant)
            threshold = threshold_for_variant(variant, context)
            weighted_return = 0.0
            missing_returns = 0
            for _, row in selected.iterrows():
                stock_return, entry_date, actual_mark_date = _entry_to_mark_return(
                    str(row["ticker"]),
                    date_value,
                    mark_date,
                    price_cache,
                )
                weight = float(row.get("variant_weight") or 0.0)
                if stock_return is None:
                    missing_returns += 1
                    contribution = 0.0
                else:
                    contribution = weight * stock_return
                    weighted_return += contribution
                holding_rows.append(
                    {
                        "prediction_date": date_value,
                        "variant": variant.name,
                        "ticker": row["ticker"],
                        "sector": row.get("sector"),
                        "rank_20d": _safe_float(row.get("rank_20d")),
                        "weight": weight,
                        "return_to_mark_pct": stock_return,
                        "contribution_pct": contribution,
                        "entry_date": entry_date,
                        "mark_date": actual_mark_date,
                        "price_vs_ma60_used": _safe_float(row.get("price_vs_ma60_used")),
                        "price_vs_ma20_used": _safe_float(row.get("price_vs_ma20_used")),
                        "beta_60_used": _safe_float(row.get("beta_60_used")),
                        "rescued_from_overheat": bool(row.get("rescued_from_overheat")),
                        "market_regime_state": context["market_regime_state"],
                        "market_regime_action": context["market_regime_action"],
                        "regime_bucket": context["regime_bucket"],
                        "threshold": threshold,
                    }
                )

            sector_weights = (
                selected.groupby("sector")["variant_weight"].sum().sort_values(ascending=False)
                if not selected.empty
                else pd.Series(dtype="float64")
            )
            max_sector = None if sector_weights.empty else str(sector_weights.index[0])
            daily_rows.append(
                {
                    "prediction_date": date_value,
                    "variant": variant.name,
                    "threshold": threshold,
                    "regime_bucket": context["regime_bucket"],
                    "market_regime_state": context["market_regime_state"],
                    "market_regime_action": context["market_regime_action"],
                    "rank20_count": int(len(rank20)),
                    "selected_count": int(len(selected)),
                    "rescued_overheat_count": int(selected["rescued_from_overheat"].sum()) if not selected.empty else 0,
                    "gross_invested_weight": float(selected["variant_weight"].sum()) if not selected.empty else 0.0,
                    "basket_return_pct": weighted_return,
                    "twii_return_pct": twii,
                    "basket_alpha_vs_twii_pct": None if twii is None else weighted_return - twii,
                    "max_name_weight": float(selected["variant_weight"].max()) if not selected.empty else 0.0,
                    "max_sector": max_sector,
                    "max_sector_weight": float(sector_weights.iloc[0]) if not sector_weights.empty else 0.0,
                    "selected_price_vs_ma60_max": float(pd.to_numeric(selected.get("price_vs_ma60_used"), errors="coerce").max()) if not selected.empty else None,
                    "selected_price_vs_ma60_p95": float(pd.to_numeric(selected.get("price_vs_ma60_used"), errors="coerce").quantile(0.95)) if not selected.empty else None,
                    "missing_return_rows": missing_returns,
                }
            )

            selected_counts = selected.groupby("sector").size().to_dict() if not selected.empty else {}
            selected_weights = selected.groupby("sector")["variant_weight"].sum().to_dict() if not selected.empty else {}
            for sector in sorted(set(rank20_counts) | set(selected_counts)):
                before = int(rank20_counts.get(sector, 0))
                after = int(selected_counts.get(sector, 0))
                sector_rows.append(
                    {
                        "prediction_date": date_value,
                        "variant": variant.name,
                        "sector": sector,
                        "rank20_count": before,
                        "selected_count": after,
                        "survival_rate": None if before <= 0 else after / float(before),
                        "kill_rate": None if before <= 0 else 1.0 - after / float(before),
                        "selected_weight": float(selected_weights.get(sector, 0.0)),
                    }
                )

    return pd.DataFrame(daily_rows), pd.DataFrame(holding_rows), pd.DataFrame(sector_rows)


def build_blocked_return_rows(signal_dates: list[str], mark_date: str) -> pd.DataFrame:
    price_cache: dict[str, pd.DataFrame | None] = {}
    rows: list[dict[str, Any]] = []
    for date_value in signal_dates:
        rank20 = enrich_rank20(date_value)
        blocked = rank20.loc[rank20["reason_set"].apply(lambda reasons: REASON_OVERHEAT in reasons)].copy()
        for _, row in blocked.iterrows():
            partial_return, entry_date, actual_mark_date = _entry_to_mark_return(
                str(row["ticker"]),
                date_value,
                mark_date,
                price_cache,
            )
            return_20d, entry_20d, exit_20d, full_available = _entry_to_forward_return(
                str(row["ticker"]),
                date_value,
                20,
                price_cache,
            )
            rows.append(
                {
                    "prediction_date": date_value,
                    "ticker": row["ticker"],
                    "sector": row.get("sector"),
                    "rank_20d": _safe_float(row.get("rank_20d")),
                    "tradability_reason": row.get("tradability_reason"),
                    "price_vs_ma60_used": _safe_float(row.get("price_vs_ma60_used")),
                    "price_vs_ma20_used": _safe_float(row.get("price_vs_ma20_used")),
                    "beta_60_used": _safe_float(row.get("beta_60_used")),
                    "return_to_mark_pct": partial_return,
                    "entry_date": entry_date or entry_20d,
                    "mark_date": actual_mark_date,
                    "return_20d_pct": return_20d,
                    "return_20d_exit_date": exit_20d,
                    "full_20d_available": bool(full_available),
                }
            )
    return pd.DataFrame(rows)


def summarize_variants(daily_df: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for variant, group in daily_df.groupby("variant", sort=False):
        basket = pd.to_numeric(group["basket_return_pct"], errors="coerce")
        alpha = pd.to_numeric(group["basket_alpha_vs_twii_pct"], errors="coerce")
        selected = pd.to_numeric(group["selected_count"], errors="coerce")
        rows.append(
            {
                "variant": variant,
                "days": int(len(group)),
                "mean_selected_count": float(selected.mean()) if len(selected) else None,
                "min_selected_count": int(selected.min()) if len(selected) else None,
                "median_selected_count": float(selected.median()) if len(selected) else None,
                "mean_basket_return_pct": float(basket.mean()) if len(basket) else None,
                "compound_basket_return_pct": float((1.0 + basket).prod() - 1.0) if len(basket) else None,
                "mean_alpha_vs_twii_pct": float(alpha.mean()) if len(alpha) else None,
                "mean_max_name_weight": float(group["max_name_weight"].mean()),
                "max_name_weight": float(group["max_name_weight"].max()),
                "mean_max_sector_weight": float(group["max_sector_weight"].mean()),
                "max_sector_weight": float(group["max_sector_weight"].max()),
                "total_rescued_overheat_count": int(group["rescued_overheat_count"].sum()),
                "mean_selected_price_vs_ma60_max": float(pd.to_numeric(group["selected_price_vs_ma60_max"], errors="coerce").mean()),
                "max_selected_price_vs_ma60": float(pd.to_numeric(group["selected_price_vs_ma60_max"], errors="coerce").max()),
            }
        )
    return pd.DataFrame(rows)


def summarize_blocked_returns(blocked_df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    if blocked_df.empty:
        return pd.DataFrame(), pd.DataFrame()

    def summarize(group: pd.DataFrame) -> dict[str, Any]:
        partial = pd.to_numeric(group["return_to_mark_pct"], errors="coerce").dropna()
        full = pd.to_numeric(group.loc[group["full_20d_available"], "return_20d_pct"], errors="coerce").dropna()
        return {
            "count": int(len(group)),
            "partial_available_count": int(len(partial)),
            "partial_median_return_pct": float(partial.median()) if not partial.empty else None,
            "partial_mean_return_pct": float(partial.mean()) if not partial.empty else None,
            "partial_positive_rate": float(partial.gt(0).mean()) if not partial.empty else None,
            "full_20d_available_count": int(len(full)),
            "full_20d_median_return_pct": float(full.median()) if not full.empty else None,
            "full_20d_mean_return_pct": float(full.mean()) if not full.empty else None,
            "full_20d_positive_rate": float(full.gt(0).mean()) if not full.empty else None,
        }

    overall = pd.DataFrame([{"group": "ALL", **summarize(blocked_df)}])
    by_sector_rows = []
    for sector, group in blocked_df.groupby("sector"):
        by_sector_rows.append({"sector": sector, **summarize(group)})
    return overall, pd.DataFrame(by_sector_rows).sort_values("count", ascending=False)


def build_payload(
    *,
    args: argparse.Namespace,
    signal_dates: list[str],
    prediction_dates: list[str],
    daily_df: pd.DataFrame,
    sector_df: pd.DataFrame,
    variant_summary: pd.DataFrame,
    price_distribution_summary: pd.DataFrame,
    blocked_df: pd.DataFrame,
    blocked_summary: pd.DataFrame,
    blocked_by_sector: pd.DataFrame,
) -> dict[str, Any]:
    best_alpha = variant_summary.sort_values("mean_alpha_vs_twii_pct", ascending=False).iloc[0]
    broadest = variant_summary.sort_values("mean_selected_count", ascending=False).iloc[0]
    current = variant_summary.loc[variant_summary["variant"].eq("ma60_40_production")].iloc[0]
    blocked_overall = blocked_summary.iloc[0].to_dict() if not blocked_summary.empty else {}
    full_20d_count = int(blocked_overall.get("full_20d_available_count") or 0)
    recommendation = (
        "Do not change production yet. The calibration evidence should be interpreted as threshold research only; "
        "the full 20D ex-post window has not matured for the 2026-04-09 to 2026-04-30 blocked rows. "
        "Among same-window variants, the MA60 40% production setting remains the clean baseline; "
        "MA60 35%/30% are stricter and worsen active breadth, while the adaptive rule must be re-tested after full 20D outcomes mature."
    )
    if full_20d_count > 0 and (blocked_overall.get("full_20d_median_return_pct") or 0) > 0:
        recommendation = (
            "Keep production unchanged until a fixed-snapshot A/B is run, but prioritize a looser or adaptive "
            "OVERHEAT_RISK proposal: the available full-20D blocked-row median is positive, suggesting false kills."
        )

    return _json_safe(
        {
            "summary": {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "signal_window": {"start": args.start, "end": args.end, "mark_date": args.mark_date},
                "prediction_distribution_window": {
                    "lookback_days": args.lookback_days,
                    "available_days": len(prediction_dates),
                    "start": prediction_dates[0] if prediction_dates else None,
                    "end": prediction_dates[-1] if prediction_dates else None,
                },
                "signal_days": len(signal_dates),
                "variants": [variant.__dict__ for variant in VARIANTS],
                "production_path_note": (
                    "CLAUDE/recommendation guardrail documents price_vs_ma20 > 30%, but the unified "
                    "Champion OVERHEAT_RISK gate currently uses price_vs_ma60 > 40% in "
                    "scripts/build_unified_signals.py. This audit calibrates the unified production path."
                ),
                "current_production": current.to_dict(),
                "best_mean_alpha_variant": best_alpha["variant"],
                "broadest_variant": broadest["variant"],
                "overheat_blocked_count": int(len(blocked_df)),
                "blocked_return_summary": blocked_overall,
                "full_20d_maturity_note": (
                    "Full 20D ex-post returns are only populated when 20 trading bars exist after entry. "
                    f"As of mark date {args.mark_date}, available full-20D rows = {full_20d_count}; "
                    "use partial mark-to-date rows as interim diagnostics, not closure evidence."
                ),
                "recommendation": recommendation,
            },
            "variant_summary": variant_summary.to_dict(orient="records"),
            "daily": daily_df.to_dict(orient="records"),
            "sector_survival": sector_df.to_dict(orient="records"),
            "price_distribution_summary": price_distribution_summary.to_dict(orient="records"),
            "blocked_return_by_sector": blocked_by_sector.to_dict(orient="records"),
        }
    )


def write_markdown(path: Path, payload: dict[str, Any]) -> None:
    summary = payload["summary"]
    current = summary["current_production"]
    blocked = summary["blocked_return_summary"]
    lines = [
        "# OVERHEAT_RISK Calibration Audit",
        "",
        "## Scope",
        "",
        f"- Generated at: {summary['generated_at']}",
        f"- Signal window: {summary['signal_window']['start']} to {summary['signal_window']['end']} ({summary['signal_days']} canonical days)",
        f"- Mark date: {summary['signal_window']['mark_date']}",
        f"- Prediction distribution window: {summary['prediction_distribution_window']['start']} to {summary['prediction_distribution_window']['end']} "
        f"({summary['prediction_distribution_window']['available_days']} available days in last {summary['prediction_distribution_window']['lookback_days']} calendar days)",
        "- Research-only: no production model, gate, sector cap, entry filter, or paper ledger changes.",
        "",
        "## Implementation Reality Check",
        "",
        "- `ml/thresholds.py` / recommendation guardrail documents `price_vs_ma20 > 30%` as the CLAUDE rule #10 downgrade path.",
        "- `scripts/build_unified_signals.py` production `OVERHEAT_RISK` uses `price_vs_ma60 > 40%` inside `_apply_overheat_risk_gate()`.",
        "- This report therefore treats `ma60_40_production` as the true current baseline. The 30% row is a strict proxy, not the unified production baseline.",
        "",
        "## Key Findings",
        "",
        f"- OVERHEAT_RISK rows audited: {summary['overheat_blocked_count']}",
        f"- Current production mean selected count: {current['mean_selected_count']:.2f}; min selected count: {current['min_selected_count']}",
        f"- Current production mean alpha vs TWII: {_pct(current['mean_alpha_vs_twii_pct'])}",
        f"- Best same-window mean alpha variant: {summary['best_mean_alpha_variant']}",
        f"- Broadest active-breadth variant: {summary['broadest_variant']}",
        f"- Full 20D blocked-return maturity: {blocked.get('full_20d_available_count', 0)} / {blocked.get('count', 0)} rows",
        f"- Interim blocked mark-to-date median return: {_pct(blocked.get('partial_median_return_pct'))}; positive rate: {_plain_pct(blocked.get('partial_positive_rate'))}",
        "",
        "## Threshold Counterfactual Summary",
        "",
        "| variant | mean selected | min selected | mean return | compound return | mean alpha vs TWII | rescued overheat | max name | max sector | max selected MA60 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in payload["variant_summary"]:
        lines.append(
            f"| {row['variant']} | {row['mean_selected_count']:.2f} | {row['min_selected_count']} | "
            f"{_pct(row['mean_basket_return_pct'])} | {_pct(row['compound_basket_return_pct'])} | "
            f"{_pct(row['mean_alpha_vs_twii_pct'])} | {row['total_rescued_overheat_count']} | "
            f"{_plain_pct(row['max_name_weight'])} | {_plain_pct(row['max_sector_weight'])} | "
            f"{_plain_pct(row['max_selected_price_vs_ma60'])} |"
        )

    lines.extend(
        [
            "",
            "## Daily Counterfactual",
            "",
            "| date | variant | regime | threshold | selected | rescued | return | alpha | max sector | max name |",
            "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- | ---: |",
        ]
    )
    for row in payload["daily"]:
        lines.append(
            f"| {row['prediction_date']} | {row['variant']} | {row['regime_bucket']} | "
            f"{_plain_pct(row['threshold'])} | {row['selected_count']} | {row['rescued_overheat_count']} | "
            f"{_pct(row['basket_return_pct'])} | {_pct(row['basket_alpha_vs_twii_pct'])} | "
            f"{row['max_sector']} {_plain_pct(row['max_sector_weight'])} | {_plain_pct(row['max_name_weight'])} |"
        )

    lines.extend(
        [
            "",
            "## OVERHEAT_RISK Blocked Return Diagnostics",
            "",
            "These rows are actual `rank_20d` rows whose `tradability_reason` contains `OVERHEAT_RISK`.",
            "",
            "| group | count | partial median | partial mean | partial positive | full 20D rows | full 20D median |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
            f"| ALL | {blocked.get('count', 0)} | {_pct(blocked.get('partial_median_return_pct'))} | "
            f"{_pct(blocked.get('partial_mean_return_pct'))} | {_plain_pct(blocked.get('partial_positive_rate'))} | "
            f"{blocked.get('full_20d_available_count', 0)} | {_pct(blocked.get('full_20d_median_return_pct'))} |",
            "",
            "By sector:",
            "",
            "| sector | count | partial median | partial positive | full 20D rows |",
            "| --- | ---: | ---: | ---: | ---: |",
        ]
    )
    for row in payload["blocked_return_by_sector"]:
        lines.append(
            f"| {row['sector']} | {row['count']} | {_pct(row['partial_median_return_pct'])} | "
            f"{_plain_pct(row['partial_positive_rate'])} | {row['full_20d_available_count']} |"
        )

    lines.extend(
        [
            "",
            "## Price Divergence Distribution",
            "",
            "| metric | sector | count | p50 | p75 | p90 | p95 | >30% | >35% | >40% |",
            "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    focus_sectors = {"半導體業", "通信網路業", "電子零組件業", "其他電子業"}
    for row in payload["price_distribution_summary"]:
        if row["sector"] not in focus_sectors:
            continue
        lines.append(
            f"| {row['metric']} | {row['sector']} | {row['count']} | {_pct(row['p50'])} | "
            f"{_pct(row['p75'])} | {_pct(row['p90'])} | {_pct(row['p95'])} | "
            f"{_plain_pct(row['gt_30_pct'])} | {_plain_pct(row['gt_35_pct'])} | {_plain_pct(row['gt_40_pct'])} |"
        )

    lines.extend(
        [
            "",
            "## Recommendation",
            "",
            summary["recommendation"],
            "",
            "## Notes",
            "",
            "- Threshold variants are simulated after rank20 selection and existing non-overheat gates; market-regime CAUTION Top10 pruning is kept unchanged.",
            "- `rescued_overheat_count` means rows whose historical artifact had `OVERHEAT_RISK` but pass the simulated threshold and all other unchanged gates.",
            "- Same-window basket returns use next-trading-day open to mark-date close and 100% equal-weight among selected rows. They are diagnostics, not a ledger replay.",
            f"- {summary['full_20d_maturity_note']}",
            "- A production threshold change still requires a fixed-snapshot A/B closure artifact before promotion.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def write_outputs(
    *,
    output_stem: str,
    payload: dict[str, Any],
    daily_df: pd.DataFrame,
    holdings_df: pd.DataFrame,
    sector_df: pd.DataFrame,
    variant_summary: pd.DataFrame,
    price_distribution_rows: pd.DataFrame,
    price_distribution_summary: pd.DataFrame,
    blocked_df: pd.DataFrame,
    blocked_summary: pd.DataFrame,
    blocked_by_sector: pd.DataFrame,
) -> None:
    report_dir = Path(REPORT_DIR)
    report_dir.mkdir(parents=True, exist_ok=True)
    daily_df.to_csv(report_dir / f"{output_stem}_daily.csv", index=False, encoding="utf-8-sig")
    holdings_df.to_csv(report_dir / f"{output_stem}_holdings.csv", index=False, encoding="utf-8-sig")
    sector_df.to_csv(report_dir / f"{output_stem}_sector_survival.csv", index=False, encoding="utf-8-sig")
    variant_summary.to_csv(report_dir / f"{output_stem}_summary.csv", index=False, encoding="utf-8-sig")
    price_distribution_summary.to_csv(report_dir / f"{output_stem}_price_distribution_summary.csv", index=False, encoding="utf-8-sig")
    blocked_df.to_csv(report_dir / f"{output_stem}_blocked_returns.csv", index=False, encoding="utf-8-sig")
    blocked_summary.to_csv(report_dir / f"{output_stem}_blocked_returns_summary.csv", index=False, encoding="utf-8-sig")
    blocked_by_sector.to_csv(report_dir / f"{output_stem}_blocked_returns_by_sector.csv", index=False, encoding="utf-8-sig")
    (report_dir / f"{output_stem}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    write_markdown(report_dir / f"{output_stem}.md", payload)
    print(f"[overheat-calibration] wrote {report_dir / f'{output_stem}.md'}")
    print(f"[overheat-calibration] wrote {report_dir / f'{output_stem}.json'}")
    print(f"[overheat-calibration] wrote {len(blocked_df)} OVERHEAT_RISK blocked rows")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    signal_dates = available_signal_dates(args.start, args.end)
    if not signal_dates:
        raise FileNotFoundError(f"No overlapping prediction/unified signal dates found for {args.start} to {args.end}")
    prediction_dates = available_prediction_dates(args.end, args.lookback_days)
    price_distribution_rows, price_distribution_summary = build_price_distribution(prediction_dates)
    daily_df, holdings_df, sector_df = build_daily_simulation(signal_dates, args.mark_date)
    variant_summary = summarize_variants(daily_df)
    blocked_df = build_blocked_return_rows(signal_dates, args.mark_date)
    blocked_summary, blocked_by_sector = summarize_blocked_returns(blocked_df)
    payload = build_payload(
        args=args,
        signal_dates=signal_dates,
        prediction_dates=prediction_dates,
        daily_df=daily_df,
        sector_df=sector_df,
        variant_summary=variant_summary,
        price_distribution_summary=price_distribution_summary,
        blocked_df=blocked_df,
        blocked_summary=blocked_summary,
        blocked_by_sector=blocked_by_sector,
    )
    write_outputs(
        output_stem=args.output_stem,
        payload=payload,
        daily_df=daily_df,
        holdings_df=holdings_df,
        sector_df=sector_df,
        variant_summary=variant_summary,
        price_distribution_rows=price_distribution_rows,
        price_distribution_summary=price_distribution_summary,
        blocked_df=blocked_df,
        blocked_summary=blocked_summary,
        blocked_by_sector=blocked_by_sector,
    )
    print(
        "[overheat-calibration] "
        f"days={len(signal_dates)} "
        f"blocked={len(blocked_df)} "
        f"full20={payload['summary']['blocked_return_summary'].get('full_20d_available_count', 0)} "
        f"best_alpha={payload['summary']['best_mean_alpha_variant']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
