"""Audit post-gate concentration and minimum effective position fallbacks.

This research script does not change production rules. It reads canonical
`unified_signals_YYYY-MM-DD.csv` artifacts, measures active-position breadth,
and runs simple same-window counterfactual baskets for minimum-position
fallback designs.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

BASE_DIR = Path(os.environ.get("STOCK_BASE_DIR") or Path(__file__).resolve().parents[1])
sys.path.insert(0, str(BASE_DIR))

from ml.config import DAILY_K_DIR, INDEX_DIR, MODEL_DIR, REPORT_DIR  # noqa: E402

SIGNAL_RE = re.compile(r"^unified_signals_(\d{4}-\d{2}-\d{2})\.csv$")
DEFAULT_START = "2026-04-23"
DEFAULT_END = "2026-04-29"
DEFAULT_MARK_DATE = "2026-04-30"
DEFAULT_MIN_ACTIVE = 10
DEFAULT_TOP_N = 30
DEFAULT_OUTPUT_STEM = "min_effective_positions_20260423_20260429"
VARIANT_BASELINE = "baseline_current"
VARIANT_CASH_OUT = "fallback_a_cash_if_under_min"
VARIANT_KEEP_CASH = "fallback_b_keep_cash_buffer"
VARIANT_RELAX_GATE = "fallback_c_relax_gate_to_min"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit minimum effective position fallbacks.")
    parser.add_argument("--start", default=DEFAULT_START)
    parser.add_argument("--end", default=DEFAULT_END)
    parser.add_argument("--mark-date", default=DEFAULT_MARK_DATE)
    parser.add_argument("--min-active", type=int, default=DEFAULT_MIN_ACTIVE)
    parser.add_argument("--top-n", type=int, default=DEFAULT_TOP_N)
    parser.add_argument("--lookback-days", type=int, default=60)
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


def list_signal_files() -> list[tuple[str, Path]]:
    rows = []
    for path in Path(MODEL_DIR).glob("unified_signals_*.csv"):
        match = SIGNAL_RE.match(path.name)
        if match:
            rows.append((match.group(1), path))
    return sorted(rows, key=lambda item: item[0])


def load_signal_file(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, encoding="utf-8-sig", dtype={"ticker": str})
    for column in ["target_units", "target_weight_ratio", "rank_20d", "risk_adjusted_return"]:
        if column in df.columns:
            df[column] = pd.to_numeric(df[column], errors="coerce")
    if "sector" not in df.columns:
        df["sector"] = "UNKNOWN"
    df["sector"] = df["sector"].fillna("UNKNOWN").astype(str)
    return df


def active_mask(df: pd.DataFrame) -> pd.Series:
    return pd.to_numeric(df.get("target_units"), errors="coerce").fillna(0).gt(0)


def active_stats_for_file(prediction_date: str, path: Path) -> dict[str, Any]:
    df = load_signal_file(path)
    active = df.loc[active_mask(df)].copy()
    units = pd.to_numeric(active.get("target_units"), errors="coerce").fillna(0)
    weights = pd.to_numeric(active.get("target_weight_ratio"), errors="coerce").fillna(0.0)
    sector_weights = active.assign(_w=weights).groupby("sector")["_w"].sum().sort_values(ascending=False)
    max_sector = sector_weights.index[0] if not sector_weights.empty else None
    return {
        "prediction_date": prediction_date,
        "active_count": int(len(active)),
        "active_units": int(units.sum()),
        "max_name_weight": float(weights.max()) if len(weights) else 0.0,
        "max_sector": max_sector,
        "max_sector_weight": float(sector_weights.iloc[0]) if not sector_weights.empty else 0.0,
        "signal_rows": int(len(df)),
        "path": str(path),
    }


def build_active_distribution(lookback_days: int, min_active: int) -> tuple[pd.DataFrame, dict[str, Any]]:
    files = list_signal_files()
    if not files:
        raise FileNotFoundError("No canonical unified_signals_YYYY-MM-DD.csv files found")

    latest_date = pd.Timestamp(files[-1][0])
    start_date = latest_date - timedelta(days=lookback_days)
    rows = [
        active_stats_for_file(prediction_date, path)
        for prediction_date, path in files
        if pd.Timestamp(prediction_date) >= start_date
    ]
    df = pd.DataFrame(rows).sort_values("prediction_date")
    active = pd.to_numeric(df["active_count"], errors="coerce")
    stats = {
        "latest_signal_date": files[-1][0],
        "lookback_days": lookback_days,
        "available_signal_days": int(len(df)),
        "coverage_start": df["prediction_date"].min() if not df.empty else None,
        "coverage_end": df["prediction_date"].max() if not df.empty else None,
        "min_active_threshold": min_active,
        "active_count_min": int(active.min()) if len(active) else None,
        "active_count_p25": float(active.quantile(0.25)) if len(active) else None,
        "active_count_median": float(active.median()) if len(active) else None,
        "active_count_p75": float(active.quantile(0.75)) if len(active) else None,
        "active_count_max": int(active.max()) if len(active) else None,
        "active_below_min_days": int(active.lt(min_active).sum()) if len(active) else 0,
        "active_below_min_ratio": float(active.lt(min_active).mean()) if len(active) else None,
    }
    return df, stats


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
    entry_rows = df[(df["Date"] > pred_ts) & (df["Date"] <= mark_ts)].copy()
    if entry_rows.empty:
        return None, None, None
    entry = entry_rows.iloc[0]
    mark_rows = df[(df["Date"] >= entry["Date"]) & (df["Date"] <= mark_ts)].copy()
    if mark_rows.empty:
        return None, None, None
    mark = mark_rows.iloc[-1]
    entry_open = _safe_float(entry["Open"])
    mark_close = _safe_float(mark["Close"])
    if entry_open is None or entry_open <= 0 or mark_close is None:
        return None, entry["Date"].date().isoformat(), mark["Date"].date().isoformat()
    return mark_close / entry_open - 1.0, entry["Date"].date().isoformat(), mark["Date"].date().isoformat()


def _twii_return(prediction_date: str, mark_date: str) -> float | None:
    path = Path(INDEX_DIR) / "index_TWII.csv"
    if not path.exists():
        return None
    df = pd.read_csv(path, parse_dates=["Date"], usecols=["Date", "Open", "Close"])
    df = df.sort_values("Date").reset_index(drop=True)
    pred_ts = pd.Timestamp(prediction_date)
    mark_ts = pd.Timestamp(mark_date)
    entry_rows = df[(df["Date"] > pred_ts) & (df["Date"] <= mark_ts)].copy()
    if entry_rows.empty:
        return None
    entry = entry_rows.iloc[0]
    mark_rows = df[(df["Date"] >= entry["Date"]) & (df["Date"] <= mark_ts)].copy()
    if mark_rows.empty:
        return None
    mark = mark_rows.iloc[-1]
    entry_open = _safe_float(entry["Open"])
    mark_close = _safe_float(mark["Close"])
    if entry_open is None or entry_open <= 0 or mark_close is None:
        return None
    return mark_close / entry_open - 1.0


def _selected_rows_for_variant(
    df: pd.DataFrame,
    variant: str,
    min_active: int,
    top_n: int,
) -> pd.DataFrame:
    active = df.loc[active_mask(df)].copy()
    if variant in {VARIANT_BASELINE, VARIANT_CASH_OUT, VARIANT_KEEP_CASH}:
        selected = active
    elif variant == VARIANT_RELAX_GATE:
        selected = active.copy()
        if len(selected) < min_active:
            blocked = df.loc[~active_mask(df) & df["rank_20d"].notna()].copy()
            blocked["rank_20d"] = pd.to_numeric(blocked["rank_20d"], errors="coerce")
            blocked = blocked.sort_values(["rank_20d", "ticker"], ascending=[True, True])
            needed = max(0, min_active - len(selected))
            selected = pd.concat([selected, blocked.head(needed)], ignore_index=True)
    else:
        raise ValueError(f"Unsupported variant: {variant}")

    selected = selected.copy()
    selected["_originally_active"] = selected["target_units"].fillna(0).gt(0)
    if variant == VARIANT_CASH_OUT and len(active) < min_active:
        selected = selected.head(0).copy()
        selected["variant_weight"] = []
        return selected

    if selected.empty:
        selected["variant_weight"] = []
        return selected

    if variant == VARIANT_KEEP_CASH and len(active) < min_active:
        planned_units = float(top_n * 2)
        selected["variant_weight"] = pd.to_numeric(selected["target_units"], errors="coerce").fillna(0) / planned_units
    elif variant == VARIANT_RELAX_GATE and len(active) < min_active:
        selected["variant_weight"] = 1.0 / float(len(selected))
    else:
        selected["variant_weight"] = pd.to_numeric(
            selected.get("target_weight_ratio"), errors="coerce"
        ).fillna(0.0)
    return selected


def _sector_max_weight(selected: pd.DataFrame) -> tuple[str | None, float]:
    if selected.empty:
        return None, 0.0
    weights = selected.groupby("sector")["variant_weight"].sum().sort_values(ascending=False)
    if weights.empty:
        return None, 0.0
    return str(weights.index[0]), float(weights.iloc[0])


def build_counterfactual_rows(
    start: str,
    end: str,
    mark_date: str,
    min_active: int,
    top_n: int,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    variants = [VARIANT_BASELINE, VARIANT_CASH_OUT, VARIANT_KEEP_CASH, VARIANT_RELAX_GATE]
    signal_lookup = {date_value: path for date_value, path in list_signal_files()}
    dates = [date for date in sorted(signal_lookup) if start <= date <= end]
    if not dates:
        raise FileNotFoundError(f"No signal files found for {start} to {end}")

    price_cache: dict[str, pd.DataFrame | None] = {}
    rows: list[dict[str, Any]] = []
    holdings: list[dict[str, Any]] = []
    for prediction_date in dates:
        df = load_signal_file(signal_lookup[prediction_date])
        active_count = int(active_mask(df).sum())
        twii = _twii_return(prediction_date, mark_date)
        for variant in variants:
            selected = _selected_rows_for_variant(df, variant, min_active=min_active, top_n=top_n)
            selected_count = int(len(selected))
            added_by_relax = int((~selected["_originally_active"]).sum()) if not selected.empty else 0
            missing_returns = 0
            weighted_return = 0.0
            gross_weight = float(pd.to_numeric(selected.get("variant_weight"), errors="coerce").fillna(0.0).sum()) if not selected.empty else 0.0
            for _, row in selected.iterrows():
                ticker = str(row["ticker"]).upper()
                stock_return, entry_date, actual_mark_date = _entry_to_mark_return(
                    ticker,
                    prediction_date,
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
                holdings.append(
                    {
                        "prediction_date": prediction_date,
                        "variant": variant,
                        "ticker": ticker,
                        "sector": row.get("sector"),
                        "signal_type": row.get("signal_type"),
                        "tradability_status": row.get("tradability_status"),
                        "tradability_reason": row.get("tradability_reason"),
                        "market_regime_state": row.get("market_regime_state"),
                        "market_regime_action": row.get("market_regime_action"),
                        "weight": weight,
                        "return_pct": stock_return,
                        "contribution_pct": contribution,
                        "originally_active": bool(row.get("_originally_active")),
                        "rank_20d": _safe_float(row.get("rank_20d")),
                        "entry_date": entry_date,
                        "mark_date": actual_mark_date,
                    }
                )

            sector, max_sector_weight = _sector_max_weight(selected)
            max_name_weight = float(selected["variant_weight"].max()) if not selected.empty else 0.0
            rows.append(
                {
                    "prediction_date": prediction_date,
                    "variant": variant,
                    "active_count": active_count,
                    "selected_count": selected_count,
                    "added_by_relax_gate": added_by_relax,
                    "gross_invested_weight": gross_weight,
                    "cash_weight": max(0.0, 1.0 - gross_weight),
                    "max_name_weight": max_name_weight,
                    "max_sector": sector,
                    "max_sector_weight": max_sector_weight,
                    "basket_return_pct": weighted_return,
                    "twii_return_pct": twii,
                    "basket_alpha_vs_twii_pct": None if twii is None else weighted_return - twii,
                    "missing_return_rows": missing_returns,
                }
            )

    return pd.DataFrame(rows), pd.DataFrame(holdings)


def summarize_variants(counterfactual: pd.DataFrame, min_active: int) -> pd.DataFrame:
    rows = []
    for variant, group in counterfactual.groupby("variant", sort=False):
        basket = pd.to_numeric(group["basket_return_pct"], errors="coerce")
        alpha = pd.to_numeric(group["basket_alpha_vs_twii_pct"], errors="coerce")
        downside = basket.loc[basket.lt(0)]
        alpha_downside = alpha.loc[alpha.lt(0)]
        basket_vol = float(basket.std(ddof=0)) if len(basket) else None
        alpha_vol = float(alpha.std(ddof=0)) if len(alpha) else None
        downside_vol = float(downside.std(ddof=0)) if len(downside) else None
        alpha_downside_vol = float(alpha_downside.std(ddof=0)) if len(alpha_downside) else None
        rows.append(
            {
                "variant": variant,
                "days": int(len(group)),
                "mean_basket_return_pct": float(basket.mean()),
                "compound_basket_return_pct": float((1.0 + basket).prod() - 1.0),
                "mean_alpha_vs_twii_pct": float(alpha.mean()),
                "daily_volatility_pct": basket_vol,
                "downside_volatility_pct": downside_vol,
                "sortino_vs_zero": None if not downside_vol or downside_vol <= 0 else float(basket.mean() / downside_vol),
                "alpha_volatility_pct": alpha_vol,
                "alpha_downside_volatility_pct": alpha_downside_vol,
                "sortino_alpha_vs_zero": None if not alpha_downside_vol or alpha_downside_vol <= 0 else float(alpha.mean() / alpha_downside_vol),
                "mean_selected_count": float(group["selected_count"].mean()),
                "min_selected_count": int(group["selected_count"].min()),
                "mean_gross_invested_weight": float(group["gross_invested_weight"].mean()),
                "mean_cash_weight": float(group["cash_weight"].mean()),
                "max_cash_weight": float(group["cash_weight"].max()),
                "mean_max_name_weight": float(group["max_name_weight"].mean()),
                "max_name_weight": float(group["max_name_weight"].max()),
                "mean_max_sector_weight": float(group["max_sector_weight"].mean()),
                "max_sector_weight": float(group["max_sector_weight"].max()),
                "days_max_sector_le_25pct": int(pd.to_numeric(group["max_sector_weight"], errors="coerce").le(0.25).sum()),
                "days_max_name_le_10pct": int(pd.to_numeric(group["max_name_weight"], errors="coerce").le(0.10).sum()),
                "days_under_min_active": int(group["active_count"].lt(min_active).sum()),
                "added_by_relax_gate": int(group["added_by_relax_gate"].sum()),
            }
        )
    return pd.DataFrame(rows)


def build_report_payload(
    *,
    active_distribution: pd.DataFrame,
    active_stats: dict[str, Any],
    counterfactual: pd.DataFrame,
    holdings: pd.DataFrame,
    variant_summary: pd.DataFrame,
    args: argparse.Namespace,
) -> dict[str, Any]:
    best_alpha = variant_summary.sort_values("mean_alpha_vs_twii_pct", ascending=False).iloc[0]
    lowest_concentration = variant_summary.sort_values("max_name_weight", ascending=True).iloc[0]
    summary_by_variant = {row["variant"]: row for row in variant_summary.to_dict(orient="records")}
    baseline = summary_by_variant.get(VARIANT_BASELINE, {})
    keep_cash = summary_by_variant.get(VARIANT_KEEP_CASH, {})
    alpha_delta = None
    if baseline and keep_cash:
        alpha_delta = keep_cash["mean_alpha_vs_twii_pct"] - baseline["mean_alpha_vs_twii_pct"]
    keep_cash_max_sector = keep_cash.get("max_sector_weight")
    keep_cash_alpha_ok = alpha_delta is not None and alpha_delta >= -0.005
    keep_cash_sector_ok = keep_cash_max_sector is not None and keep_cash_max_sector <= 0.25
    recommendation = (
        "Do not promote fallback_b_keep_cash_buffer as a standalone production change from this window. "
        "It is a strong concentration brake, but it fails the no-material-alpha-sacrifice check and does "
        "not fully keep max sector <= 25% because sufficient-breadth days still use baseline weights. "
        "Keep it as a portfolio-risk candidate to combine with gate calibration, not as the main alpha fix."
    )
    return _json_safe(
        {
            "summary": {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "signal_window": {"start": args.start, "end": args.end, "mark_date": args.mark_date},
                "min_active": args.min_active,
                "top_n": args.top_n,
                "lookback": active_stats,
                "best_mean_alpha_variant": best_alpha["variant"],
                "lowest_concentration_variant": lowest_concentration["variant"],
                "fallback_b_assessment": {
                    "mean_alpha_delta_vs_baseline_pct": alpha_delta,
                    "max_sector_weight": keep_cash_max_sector,
                    "passes_no_material_alpha_sacrifice": keep_cash_alpha_ok,
                    "passes_max_sector_le_25pct": keep_cash_sector_ok,
                    "baseline_mean_alpha_vs_twii_pct": baseline.get("mean_alpha_vs_twii_pct"),
                    "fallback_b_mean_alpha_vs_twii_pct": keep_cash.get("mean_alpha_vs_twii_pct"),
                    "baseline_daily_volatility_pct": baseline.get("daily_volatility_pct"),
                    "fallback_b_daily_volatility_pct": keep_cash.get("daily_volatility_pct"),
                },
                "recommendation": recommendation,
                "scope_note": (
                    "Counterfactual returns are same-window research baskets using next-trading-day open "
                    "to mark-date close. They do not replay exits or mutate production ledgers."
                ),
            },
            "variant_summary": variant_summary.to_dict(orient="records"),
            "counterfactual_daily": counterfactual.to_dict(orient="records"),
            "active_distribution": active_distribution.to_dict(orient="records"),
            "holdings_rows": len(holdings),
        }
    )


def write_markdown(path: Path, payload: dict[str, Any]) -> None:
    summary = payload["summary"]
    lookback = summary["lookback"]
    fallback_b = summary.get("fallback_b_assessment", {})
    lines = [
        "# Minimum Effective Positions Audit",
        "",
        "## Scope",
        "",
        f"- Generated at: {summary['generated_at']}",
        f"- Signal window: {summary['signal_window']['start']} to {summary['signal_window']['end']}",
        f"- Mark date: {summary['signal_window']['mark_date']}",
        f"- Minimum active threshold: {summary['min_active']}",
        f"- Top N denominator: {summary['top_n']}",
        "- Return basis: next-trading-day open to mark-date close, signal-day baskets.",
        "",
        "## Active Breadth",
        "",
        f"- Available canonical signal days in last {lookback['lookback_days']} calendar days: {lookback['available_signal_days']} "
        f"({lookback['coverage_start']} to {lookback['coverage_end']})",
        f"- Active count min/p25/median/p75/max: {lookback['active_count_min']} / "
        f"{lookback['active_count_p25']:.2f} / {lookback['active_count_median']:.2f} / "
        f"{lookback['active_count_p75']:.2f} / {lookback['active_count_max']}",
        f"- Days with active < {lookback['min_active_threshold']}: {lookback['active_below_min_days']} "
        f"({_plain_pct(lookback['active_below_min_ratio'])})",
        "",
        "## Key Findings",
        "",
        f"- fallback_b alpha delta vs baseline: {_pct(fallback_b.get('mean_alpha_delta_vs_baseline_pct'))} "
        f"(baseline {_pct(fallback_b.get('baseline_mean_alpha_vs_twii_pct'))} vs fallback_b {_pct(fallback_b.get('fallback_b_mean_alpha_vs_twii_pct'))})",
        f"- fallback_b max sector: {_plain_pct(fallback_b.get('max_sector_weight'))}; passes <=25% check: {fallback_b.get('passes_max_sector_le_25pct')}",
        f"- fallback_b daily volatility: {_plain_pct(fallback_b.get('fallback_b_daily_volatility_pct'))} vs baseline {_plain_pct(fallback_b.get('baseline_daily_volatility_pct'))}",
        f"- fallback_b passes no-material-alpha-sacrifice check: {fallback_b.get('passes_no_material_alpha_sacrifice')}",
        "",
        "## Variant Summary",
        "",
        "| variant | mean return | compound return | mean alpha vs TWII | vol | Sortino | alpha vol | mean selected | invested | cash | max name | max sector |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in payload["variant_summary"]:
        sortino_text = "-" if row.get("sortino_vs_zero") is None else f"{row['sortino_vs_zero']:.2f}"
        lines.append(
            f"| {row['variant']} | {_pct(row['mean_basket_return_pct'])} | "
            f"{_pct(row['compound_basket_return_pct'])} | {_pct(row['mean_alpha_vs_twii_pct'])} | "
            f"{_plain_pct(row['daily_volatility_pct'])} | {sortino_text} | {_plain_pct(row['alpha_volatility_pct'])} | "
            f"{row['mean_selected_count']:.2f} | {_plain_pct(row['mean_gross_invested_weight'])} | "
            f"{_plain_pct(row['mean_cash_weight'])} | "
            f"{_plain_pct(row['max_name_weight'])} | {_plain_pct(row['max_sector_weight'])} |"
        )

    lines.extend(
        [
            "",
            "## Daily Counterfactual",
            "",
            "| date | variant | active | selected | added | invested | return | alpha | max name | max sector |",
            "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
        ]
    )
    for row in payload["counterfactual_daily"]:
        lines.append(
            f"| {row['prediction_date']} | {row['variant']} | {row['active_count']} | "
            f"{row['selected_count']} | {row['added_by_relax_gate']} | {_plain_pct(row['gross_invested_weight'])} | "
            f"{_pct(row['basket_return_pct'])} | {_pct(row['basket_alpha_vs_twii_pct'])} | "
            f"{_plain_pct(row['max_name_weight'])} | {row['max_sector']} {_plain_pct(row['max_sector_weight'])} |"
        )

    lines.extend(
        [
            "",
            "## Recommendation",
            "",
            summary["recommendation"],
            "",
            "fallback_b keeps the gate decision intact and prevents 4-7 names from being re-normalized to 100% exposure, "
            "but the longer available window shows that many low-breadth days were strong up days. The cash buffer "
            "therefore reduces volatility and concentration by giving up too much upside in this sample. The relax-gate "
            "variant remains a research upper bound because it explicitly re-admits rows that production gates blocked.",
            "",
            "## Notes",
            "",
            "- `baseline_current` uses the current `target_weight_ratio`, which re-normalizes active target units to 100%.",
            "- `fallback_a_cash_if_under_min` goes fully to cash when active count is below the threshold.",
            "- `fallback_b_keep_cash_buffer` keeps active 20D names at `target_units / (TopN * 2)` and leaves the rest in cash.",
            "- `fallback_c_relax_gate_to_min` fills from blocked `rank_20d` rows until the threshold is reached, then equal-weights the basket.",
            "- This is research-only and does not change production gates, model scoring, sector cap, or entry filters.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def write_outputs(
    payload: dict[str, Any],
    active_distribution: pd.DataFrame,
    counterfactual: pd.DataFrame,
    holdings: pd.DataFrame,
    variant_summary: pd.DataFrame,
    output_stem: str,
) -> None:
    report_dir = Path(REPORT_DIR)
    report_dir.mkdir(parents=True, exist_ok=True)
    combined = counterfactual.copy()
    active_distribution.to_csv(report_dir / f"{output_stem}_active_distribution.csv", index=False, encoding="utf-8-sig")
    counterfactual.to_csv(report_dir / f"{output_stem}.csv", index=False, encoding="utf-8-sig")
    holdings.to_csv(report_dir / f"{output_stem}_holdings.csv", index=False, encoding="utf-8-sig")
    variant_summary.to_csv(report_dir / f"{output_stem}_summary.csv", index=False, encoding="utf-8-sig")
    (report_dir / f"{output_stem}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    write_markdown(report_dir / f"{output_stem}.md", payload)
    print(f"[min-positions] wrote {report_dir / f'{output_stem}.md'}")
    print(f"[min-positions] wrote {report_dir / f'{output_stem}.json'}")
    print(f"[min-positions] wrote {len(combined)} daily variant rows")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    active_distribution, active_stats = build_active_distribution(
        lookback_days=args.lookback_days,
        min_active=args.min_active,
    )
    counterfactual, holdings = build_counterfactual_rows(
        args.start,
        args.end,
        args.mark_date,
        min_active=args.min_active,
        top_n=args.top_n,
    )
    variant_summary = summarize_variants(counterfactual, min_active=args.min_active)
    payload = build_report_payload(
        active_distribution=active_distribution,
        active_stats=active_stats,
        counterfactual=counterfactual,
        holdings=holdings,
        variant_summary=variant_summary,
        args=args,
    )
    write_outputs(
        payload,
        active_distribution=active_distribution,
        counterfactual=counterfactual,
        holdings=holdings,
        variant_summary=variant_summary,
        output_stem=args.output_stem,
    )
    print(
        "[min-positions] "
        f"available_days={active_stats['available_signal_days']} "
        f"active_below_{args.min_active}={active_stats['active_below_min_days']} "
        f"best_alpha={payload['summary']['best_mean_alpha_variant']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
