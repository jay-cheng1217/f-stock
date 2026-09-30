"""Audit MARKET_REGIME_CAUTION Top-N pruning.

This research-only script keeps non-market gates unchanged and simulates
alternative CAUTION rank limits after the existing liquidity/overheat/beta gates.
It does not mutate production gates, model files, or paper ledgers.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
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
DEFAULT_OUTPUT_STEM = "market_regime_caution_audit_20260409_20260430"

REASON_MARKET_CAUTION = "MARKET_REGIME_CAUTION"


@dataclass(frozen=True)
class CautionVariant:
    name: str
    caution_rank_limit: int | None
    description: str


VARIANTS = [
    CautionVariant("top10_production", 10, "Current CAUTION production behavior."),
    CautionVariant("top15", 15, "Relax CAUTION pruning to Top15."),
    CautionVariant("top20", 20, "Relax CAUTION pruning to Top20."),
    CautionVariant("top30_no_prune", None, "Do not rank-prune 20D names during CAUTION; keep other gates unchanged."),
]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit MARKET_REGIME_CAUTION rank pruning.")
    parser.add_argument("--start", default=DEFAULT_START)
    parser.add_argument("--end", default=DEFAULT_END)
    parser.add_argument("--mark-date", default=DEFAULT_MARK_DATE)
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


def _list_dated_files(pattern: re.Pattern[str], glob_pattern: str) -> dict[str, Path]:
    rows: dict[str, Path] = {}
    for path in Path(MODEL_DIR).glob(glob_pattern):
        match = pattern.match(path.name)
        if match:
            rows[match.group(1)] = path
    return rows


def available_signal_dates(start: str, end: str) -> list[str]:
    predictions = _list_dated_files(PRED_RE, "predictions_*.csv")
    signals = _list_dated_files(SIGNAL_RE, "unified_signals_*.csv")
    return [date for date in sorted(set(predictions) & set(signals)) if start <= date <= end]


def read_signal(date_value: str) -> pd.DataFrame:
    path = Path(MODEL_DIR) / f"unified_signals_{date_value}.csv"
    df = pd.read_csv(path, encoding="utf-8-sig", dtype={"ticker": str})
    df["ticker"] = df["ticker"].astype(str).str.upper()
    if "sector" not in df.columns:
        df["sector"] = "UNKNOWN"
    df["sector"] = df["sector"].fillna("UNKNOWN").astype(str)
    for column in [
        "target_units",
        "target_weight_ratio",
        "rank_20d",
        "market_regime_twii_close",
        "market_regime_twii_ma20",
        "market_regime_twii_ma60",
    ]:
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


def is_caution_day(rank20: pd.DataFrame) -> bool:
    state = first_nonempty(rank20, "market_regime_state")
    action = first_nonempty(rank20, "market_regime_action")
    return state.upper() == "CAUTION" or "LIMIT_20D_TOP10" in action


def rank20_with_reasons(date_value: str) -> pd.DataFrame:
    signal = read_signal(date_value)
    ranked = signal.loc[signal["rank_20d"].notna()].copy()
    ranked["prediction_date"] = date_value
    ranked["rank_20d"] = pd.to_numeric(ranked["rank_20d"], errors="coerce")
    ranked["reason_set"] = ranked.get("tradability_reason", pd.Series("", index=ranked.index)).apply(split_reasons)
    ranked["is_current_active"] = pd.to_numeric(ranked.get("target_units"), errors="coerce").fillna(0).gt(0)
    return ranked.sort_values(["rank_20d", "ticker"]).reset_index(drop=True)


def pre_market_pool(rank20: pd.DataFrame) -> pd.DataFrame:
    """Rows that passed non-market gates; market caution may still have blocked them."""

    def has_only_market_reason(reason_set: set[str]) -> bool:
        return len(set(reason_set) - {REASON_MARKET_CAUTION}) == 0

    return rank20.loc[rank20["reason_set"].apply(has_only_market_reason)].copy()


def simulate_variant_for_date(rank20: pd.DataFrame, variant: CautionVariant) -> pd.DataFrame:
    pool = pre_market_pool(rank20)
    if is_caution_day(rank20) and variant.caution_rank_limit is not None:
        selected = pool.loc[pd.to_numeric(pool["rank_20d"], errors="coerce").le(variant.caution_rank_limit)].copy()
    else:
        selected = pool.copy()
    selected["variant"] = variant.name
    selected["caution_rank_limit"] = variant.caution_rank_limit
    selected["rescued_from_market_caution"] = selected["reason_set"].apply(lambda reasons: REASON_MARKET_CAUTION in reasons)
    selected["variant_weight"] = 0.0 if selected.empty else 1.0 / float(len(selected))
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


def build_daily_simulation(signal_dates: list[str], mark_date: str) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    price_cache: dict[str, pd.DataFrame | None] = {}
    daily_rows: list[dict[str, Any]] = []
    holding_rows: list[dict[str, Any]] = []
    sector_rows: list[dict[str, Any]] = []

    for date_value in signal_dates:
        rank20 = rank20_with_reasons(date_value)
        pool = pre_market_pool(rank20)
        caution = is_caution_day(rank20)
        twii = _twii_return(date_value, mark_date)
        rank20_counts = rank20.groupby("sector").size().to_dict()
        pool_counts = pool.groupby("sector").size().to_dict() if not pool.empty else {}
        for variant in VARIANTS:
            selected = simulate_variant_for_date(rank20, variant)
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
                    contribution = 0.0
                    missing_returns += 1
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
                        "tradability_reason": row.get("tradability_reason"),
                        "rescued_from_market_caution": bool(row.get("rescued_from_market_caution")),
                        "market_regime_state": first_nonempty(rank20, "market_regime_state"),
                        "market_regime_action": first_nonempty(rank20, "market_regime_action"),
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
                    "is_caution_day": caution,
                    "caution_rank_limit": variant.caution_rank_limit,
                    "rank20_count": int(len(rank20)),
                    "pre_market_pool_count": int(len(pool)),
                    "selected_count": int(len(selected)),
                    "rescued_market_caution_count": int(selected["rescued_from_market_caution"].sum()) if not selected.empty else 0,
                    "gross_invested_weight": float(selected["variant_weight"].sum()) if not selected.empty else 0.0,
                    "basket_return_pct": weighted_return,
                    "twii_return_pct": twii,
                    "basket_alpha_vs_twii_pct": None if twii is None else weighted_return - twii,
                    "max_name_weight": float(selected["variant_weight"].max()) if not selected.empty else 0.0,
                    "max_sector": max_sector,
                    "max_sector_weight": float(sector_weights.iloc[0]) if not sector_weights.empty else 0.0,
                    "missing_return_rows": missing_returns,
                    "market_regime_state": first_nonempty(rank20, "market_regime_state"),
                    "market_regime_action": first_nonempty(rank20, "market_regime_action"),
                }
            )

            selected_counts = selected.groupby("sector").size().to_dict() if not selected.empty else {}
            selected_weights = selected.groupby("sector")["variant_weight"].sum().to_dict() if not selected.empty else {}
            for sector in sorted(set(rank20_counts) | set(pool_counts) | set(selected_counts)):
                before = int(rank20_counts.get(sector, 0))
                pool_count = int(pool_counts.get(sector, 0))
                after = int(selected_counts.get(sector, 0))
                sector_rows.append(
                    {
                        "prediction_date": date_value,
                        "variant": variant.name,
                        "sector": sector,
                        "rank20_count": before,
                        "pre_market_pool_count": pool_count,
                        "selected_count": after,
                        "rank20_to_selected_survival": None if before <= 0 else after / float(before),
                        "pool_to_selected_survival": None if pool_count <= 0 else after / float(pool_count),
                        "selected_weight": float(selected_weights.get(sector, 0.0)),
                    }
                )
    return pd.DataFrame(daily_rows), pd.DataFrame(holding_rows), pd.DataFrame(sector_rows)


def build_blocked_return_rows(signal_dates: list[str], mark_date: str) -> pd.DataFrame:
    price_cache: dict[str, pd.DataFrame | None] = {}
    rows: list[dict[str, Any]] = []
    for date_value in signal_dates:
        rank20 = rank20_with_reasons(date_value)
        blocked = rank20.loc[
            ~rank20["is_current_active"]
            & rank20["reason_set"].apply(lambda reasons: REASON_MARKET_CAUTION in reasons)
        ].copy()
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
        caution_group = group.loc[group["is_caution_day"].astype(bool)]
        rows.append(
            {
                "variant": variant,
                "days": int(len(group)),
                "caution_days": int(group["is_caution_day"].astype(bool).sum()),
                "mean_selected_count": float(selected.mean()) if len(selected) else None,
                "median_selected_count": float(selected.median()) if len(selected) else None,
                "min_selected_count": int(selected.min()) if len(selected) else None,
                "mean_selected_count_caution_days": float(caution_group["selected_count"].mean()) if not caution_group.empty else None,
                "mean_basket_return_pct": float(basket.mean()) if len(basket) else None,
                "compound_basket_return_pct": float((1.0 + basket).prod() - 1.0) if len(basket) else None,
                "mean_alpha_vs_twii_pct": float(alpha.mean()) if len(alpha) else None,
                "mean_max_name_weight": float(group["max_name_weight"].mean()),
                "max_name_weight": float(group["max_name_weight"].max()),
                "mean_max_sector_weight": float(group["max_sector_weight"].mean()),
                "max_sector_weight": float(group["max_sector_weight"].max()),
                "total_rescued_market_caution_count": int(group["rescued_market_caution_count"].sum()),
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
    by_sector = [{"sector": sector, **summarize(group)} for sector, group in blocked_df.groupby("sector")]
    return overall, pd.DataFrame(by_sector).sort_values("count", ascending=False)


def build_payload(
    *,
    args: argparse.Namespace,
    signal_dates: list[str],
    daily_df: pd.DataFrame,
    sector_df: pd.DataFrame,
    variant_summary: pd.DataFrame,
    blocked_df: pd.DataFrame,
    blocked_summary: pd.DataFrame,
    blocked_by_sector: pd.DataFrame,
) -> dict[str, Any]:
    best_alpha = variant_summary.sort_values("mean_alpha_vs_twii_pct", ascending=False).iloc[0]
    broadest = variant_summary.sort_values("mean_selected_count", ascending=False).iloc[0]
    current = variant_summary.loc[variant_summary["variant"].eq("top10_production")].iloc[0]
    blocked_overall = blocked_summary.iloc[0].to_dict() if not blocked_summary.empty else {}
    full_20d_count = int(blocked_overall.get("full_20d_available_count") or 0)
    recommendation = (
        "Do not change production yet. The audit shows the Top10 CAUTION gate is a major active-breadth throttle, "
        "but the 20D ex-post window has not matured for the blocked rows. Treat Top15/Top20/Top30 as research "
        "counterfactuals until full 20D outcomes and a fixed-snapshot A/B are available."
    )
    return _json_safe(
        {
            "summary": {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "signal_window": {"start": args.start, "end": args.end, "mark_date": args.mark_date},
                "signal_days": len(signal_dates),
                "variants": [variant.__dict__ for variant in VARIANTS],
                "production_path_note": (
                    "scripts/build_unified_signals.py applies MARKET_REGIME_CAUTION after liquidity, "
                    "OVERHEAT_RISK, beta, and alpha-win gates. This audit changes only the CAUTION rank "
                    "limit and keeps non-market gates unchanged."
                ),
                "current_production": current.to_dict(),
                "best_mean_alpha_variant": best_alpha["variant"],
                "broadest_variant": broadest["variant"],
                "market_caution_blocked_count": int(len(blocked_df)),
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
            "blocked_return_by_sector": blocked_by_sector.to_dict(orient="records"),
        }
    )


def write_markdown(path: Path, payload: dict[str, Any]) -> None:
    summary = payload["summary"]
    current = summary["current_production"]
    blocked = summary["blocked_return_summary"]
    lines = [
        "# MARKET_REGIME_CAUTION Top-N Audit",
        "",
        "## Scope",
        "",
        f"- Generated at: {summary['generated_at']}",
        f"- Signal window: {summary['signal_window']['start']} to {summary['signal_window']['end']} ({summary['signal_days']} canonical days)",
        f"- Mark date: {summary['signal_window']['mark_date']}",
        "- Research-only: no production gate, model, sector cap, entry filter, or paper ledger changes.",
        "",
        "## Implementation Reality Check",
        "",
        "- Production path: `scripts/build_unified_signals.py::_apply_market_regime_gate()`.",
        "- Current CAUTION behavior: keep `rank_20d <= 10`; block the rest with `MARKET_REGIME_CAUTION`.",
        "- This audit changes only that rank limit. Liquidity, OVERHEAT_RISK, beta, alpha-win, and disposition gates stay unchanged.",
        "",
        "## Key Findings",
        "",
        f"- MARKET_REGIME_CAUTION blocked rows audited: {summary['market_caution_blocked_count']}",
        f"- Current production mean selected count: {current['mean_selected_count']:.2f}; caution-day mean selected: {current['mean_selected_count_caution_days']:.2f}",
        f"- Current production mean alpha vs TWII: {_pct(current['mean_alpha_vs_twii_pct'])}",
        f"- Best same-window mean alpha variant: {summary['best_mean_alpha_variant']}",
        f"- Broadest active-breadth variant: {summary['broadest_variant']}",
        f"- Full 20D blocked-return maturity: {blocked.get('full_20d_available_count', 0)} / {blocked.get('count', 0)} rows",
        f"- Interim blocked mark-to-date median return: {_pct(blocked.get('partial_median_return_pct'))}; positive rate: {_plain_pct(blocked.get('partial_positive_rate'))}",
        "",
        "## Rank-Limit Counterfactual Summary",
        "",
        "| variant | mean selected | caution-day selected | min selected | mean return | compound return | mean alpha vs TWII | rescued rows | max name | max sector |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in payload["variant_summary"]:
        lines.append(
            f"| {row['variant']} | {row['mean_selected_count']:.2f} | {row['mean_selected_count_caution_days']:.2f} | "
            f"{row['min_selected_count']} | {_pct(row['mean_basket_return_pct'])} | {_pct(row['compound_basket_return_pct'])} | "
            f"{_pct(row['mean_alpha_vs_twii_pct'])} | {row['total_rescued_market_caution_count']} | "
            f"{_plain_pct(row['max_name_weight'])} | {_plain_pct(row['max_sector_weight'])} |"
        )

    lines.extend(
        [
            "",
            "## Daily Counterfactual",
            "",
            "| date | variant | action | pre-market pool | selected | rescued | return | alpha | max sector | max name |",
            "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- | ---: |",
        ]
    )
    for row in payload["daily"]:
        lines.append(
            f"| {row['prediction_date']} | {row['variant']} | {row['market_regime_action']} | "
            f"{row['pre_market_pool_count']} | {row['selected_count']} | {row['rescued_market_caution_count']} | "
            f"{_pct(row['basket_return_pct'])} | {_pct(row['basket_alpha_vs_twii_pct'])} | "
            f"{row['max_sector']} {_plain_pct(row['max_sector_weight'])} | {_plain_pct(row['max_name_weight'])} |"
        )

    lines.extend(
        [
            "",
            "## MARKET_REGIME_CAUTION Blocked Return Diagnostics",
            "",
            "These rows are actual inactive `rank_20d` rows whose `tradability_reason` contains `MARKET_REGIME_CAUTION`.",
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
            "## Recommendation",
            "",
            summary["recommendation"],
            "",
            "## Notes",
            "",
            "- `pre_market_pool_count` is the pool that passed non-market gates before CAUTION rank pruning.",
            "- Same-window basket returns use next-trading-day open to mark-date close and 100% equal-weight among selected rows. They are diagnostics, not a ledger replay.",
            f"- {summary['full_20d_maturity_note']}",
            "- A production market-regime change still requires a fixed-snapshot A/B closure artifact before promotion.",
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
    blocked_df.to_csv(report_dir / f"{output_stem}_blocked_returns.csv", index=False, encoding="utf-8-sig")
    blocked_summary.to_csv(report_dir / f"{output_stem}_blocked_returns_summary.csv", index=False, encoding="utf-8-sig")
    blocked_by_sector.to_csv(report_dir / f"{output_stem}_blocked_returns_by_sector.csv", index=False, encoding="utf-8-sig")
    (report_dir / f"{output_stem}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    write_markdown(report_dir / f"{output_stem}.md", payload)
    print(f"[market-regime-caution] wrote {report_dir / f'{output_stem}.md'}")
    print(f"[market-regime-caution] wrote {report_dir / f'{output_stem}.json'}")
    print(f"[market-regime-caution] wrote {len(blocked_df)} MARKET_REGIME_CAUTION blocked rows")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    signal_dates = available_signal_dates(args.start, args.end)
    if not signal_dates:
        raise FileNotFoundError(f"No overlapping prediction/unified signal dates found for {args.start} to {args.end}")
    daily_df, holdings_df, sector_df = build_daily_simulation(signal_dates, args.mark_date)
    variant_summary = summarize_variants(daily_df)
    blocked_df = build_blocked_return_rows(signal_dates, args.mark_date)
    blocked_summary, blocked_by_sector = summarize_blocked_returns(blocked_df)
    payload = build_payload(
        args=args,
        signal_dates=signal_dates,
        daily_df=daily_df,
        sector_df=sector_df,
        variant_summary=variant_summary,
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
        blocked_df=blocked_df,
        blocked_summary=blocked_summary,
        blocked_by_sector=blocked_by_sector,
    )
    print(
        "[market-regime-caution] "
        f"days={len(signal_dates)} "
        f"blocked={len(blocked_df)} "
        f"full20={payload['summary']['blocked_return_summary'].get('full_20d_available_count', 0)} "
        f"best_alpha={payload['summary']['best_mean_alpha_variant']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
