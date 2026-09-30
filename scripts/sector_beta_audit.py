"""Sector beta exposure audit for V2 Champion paper picks.

The audit is research-only. It compares the Champion paper ledger's sector
weights with a sector benchmark built from local data, then writes
CSV/Markdown/JSON reports for attribution review.
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))

from ml.config import DAILY_K_DIR, INDEX_DIR, REPORT_DIR  # noqa: E402

DEFAULT_DB_PATH = BASE_DIR / "paper_portfolio_v2_champion.db"
DEFAULT_ATTRIBUTION_PATH = Path(REPORT_DIR) / "champion_week1_attribution.csv"
DEFAULT_SECTOR_MAPPING_PATH = BASE_DIR / "ml" / "data" / "sector_mapping.csv"
DEFAULT_START = "2026-04-23"
DEFAULT_END = "2026-04-29"
ACTIVE_WEIGHT_ALERT = 0.05
DISPERSION_ALERT = 0.05
BENCHMARK_EQUAL_WEIGHT = "local_equal_weight_sector_proxy"
BENCHMARK_MARKET_CAP_INDEX = "market_cap_sector_index"
LOCAL_TURNOVER_PROXY = "local_turnover_value_weighted_sector_proxy"


@dataclass(frozen=True)
class AuditPaths:
    db_path: Path
    attribution_path: Path
    sector_mapping_path: Path
    report_dir: Path


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit Champion sector exposure against local sector return proxies."
    )
    parser.add_argument("--start", default=DEFAULT_START)
    parser.add_argument("--end", default=DEFAULT_END)
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--attribution-path", default=str(DEFAULT_ATTRIBUTION_PATH))
    parser.add_argument("--sector-mapping-path", default=str(DEFAULT_SECTOR_MAPPING_PATH))
    parser.add_argument("--report-dir", default=REPORT_DIR)
    parser.add_argument(
        "--benchmark-market",
        default="all",
        help="Sector mapping Market filter, e.g. all or 上市.",
    )
    parser.add_argument(
        "--benchmark-mode",
        choices=[BENCHMARK_EQUAL_WEIGHT, BENCHMARK_MARKET_CAP_INDEX],
        default=BENCHMARK_MARKET_CAP_INDEX,
        help=(
            "Sector benchmark mode. market_cap_sector_index tries local official "
            "sector index files first, then falls back to local traded-value weighting."
        ),
    )
    parser.add_argument(
        "--active-weight-alert",
        type=float,
        default=ACTIVE_WEIGHT_ALERT,
        help="Absolute active sector weight threshold used for alert flags.",
    )
    parser.add_argument(
        "--dispersion-alert",
        type=float,
        default=DISPERSION_ALERT,
        help="Within-sector stock return dispersion threshold used for alert flags.",
    )
    return parser.parse_args(argv)


def _safe_float(value: Any) -> float | None:
    try:
        if pd.isna(value):
            return None
        result = float(value)
    except Exception:
        return None
    if not math.isfinite(result):
        return None
    return result


def _pct(value: Any, digits: int = 2) -> str:
    result = _safe_float(value)
    if result is None:
        return "-"
    return f"{result * 100:+.{digits}f}%"


def _num(value: Any, digits: int = 2) -> str:
    result = _safe_float(value)
    if result is None:
        return "-"
    return f"{result:.{digits}f}"


def _sanitize_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _sanitize_json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_sanitize_json(item) for item in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        number = float(value)
        return number if math.isfinite(number) else None
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if pd.isna(value):
        return None
    return value


def _load_champion_positions(db_path: Path, start: str, end: str) -> pd.DataFrame:
    query = """
        SELECT
            p.id AS position_id,
            p.prediction_date AS pred_date,
            p.ticker,
            p.sector,
            p.status,
            p.target_weight,
            p.entry_date,
            p.entry_price,
            p.exit_date,
            p.exit_price,
            p.exit_reason,
            p.realized_return_pct,
            p.max_drawdown_pct
        FROM unified_positions p
        WHERE p.prediction_date BETWEEN ? AND ?
        ORDER BY p.prediction_date, p.ticker
    """
    with sqlite3.connect(db_path) as con:
        positions = pd.read_sql_query(query, con, params=(start, end), dtype={"ticker": str})
    if positions.empty:
        return positions

    positions["ticker"] = positions["ticker"].astype(str).str.zfill(4)
    positions["target_weight"] = pd.to_numeric(positions["target_weight"], errors="coerce").fillna(0.0)
    total_weight = float(positions["target_weight"].clip(lower=0.0).sum())
    if total_weight > 0:
        positions["audit_weight"] = positions["target_weight"].clip(lower=0.0) / total_weight
    else:
        positions["audit_weight"] = 1.0 / len(positions)
    return positions


def _load_week1_attribution(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    attribution = pd.read_csv(path, encoding="utf-8-sig", dtype={"ticker": str, "pred_date": str})
    if "ticker" in attribution.columns:
        attribution["ticker"] = attribution["ticker"].astype(str).str.zfill(4)
    for column in [
        "realized_pct",
        "hold_to_end_pct",
        "twii_same_window_pct",
        "alpha_if_held_pct",
        "excess_during_hold_pct",
        "post_exit_pct",
    ]:
        if column in attribution.columns:
            attribution[column] = pd.to_numeric(attribution[column], errors="coerce") / 100.0
    return attribution


def _merge_attribution(positions: pd.DataFrame, attribution: pd.DataFrame) -> pd.DataFrame:
    if positions.empty or attribution.empty:
        result = positions.copy()
        result["hold_to_end_pct"] = np.nan
        result["alpha_if_held_pct"] = np.nan
        return result

    keep_columns = [
        column
        for column in [
            "pred_date",
            "ticker",
            "realized_pct",
            "hold_to_end_pct",
            "twii_same_window_pct",
            "alpha_if_held_pct",
            "excess_during_hold_pct",
            "post_exit_pct",
        ]
        if column in attribution.columns
    ]
    result = positions.merge(attribution[keep_columns], on=["pred_date", "ticker"], how="left")
    return result


def _load_sector_mapping(path: Path, market: str) -> pd.DataFrame:
    mapping = pd.read_csv(path, encoding="utf-8-sig", dtype={"Ticker": str})
    mapping["Ticker"] = mapping["Ticker"].astype(str).str.zfill(4)
    mapping["Sector"] = mapping["Sector"].fillna("unknown").astype(str)
    if market.lower() != "all" and "Market" in mapping.columns:
        mapping = mapping[mapping["Market"].astype(str) == market].copy()
    return mapping


def _load_window_stats(ticker: str, start: str, end: str) -> dict[str, Any] | None:
    path = Path(DAILY_K_DIR) / f"{ticker}.csv"
    if not path.exists():
        return None
    try:
        df = pd.read_csv(path, dtype={"Date": str})
    except Exception:
        return None
    keep = [column for column in ["Date", "Close", "Volume"] if column in df.columns]
    df = df[keep].copy()
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    df["Close"] = pd.to_numeric(df["Close"], errors="coerce")
    if "Volume" in df.columns:
        df["Volume"] = pd.to_numeric(df["Volume"], errors="coerce")
    else:
        df["Volume"] = np.nan
    df = df.dropna(subset=["Date", "Close"]).sort_values("Date")
    if df.empty:
        return None
    window = df[(df["Date"] >= pd.Timestamp(start)) & (df["Date"] <= pd.Timestamp(end))]
    if len(window) < 2:
        return None
    first = float(window.iloc[0]["Close"])
    last = float(window.iloc[-1]["Close"])
    if first <= 0:
        return None
    window = window.copy()
    window["traded_value"] = window["Close"] * window["Volume"].fillna(0.0)
    traded_value = float(window["traded_value"].replace([np.inf, -np.inf], np.nan).dropna().mean())
    if not math.isfinite(traded_value) or traded_value <= 0:
        traded_value = first
    return {
        "ticker": ticker,
        "window_return_pct": last / first - 1.0,
        "start_close": first,
        "end_close": last,
        "avg_traded_value": traded_value,
    }


def _load_twii_return(start: str, end: str) -> dict[str, Any]:
    path = Path(INDEX_DIR) / "index_TWII.csv"
    if not path.exists():
        return {"return_pct": None, "reason": f"missing {path}"}
    df = pd.read_csv(path, usecols=["Date", "Close"])
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    df["Close"] = pd.to_numeric(df["Close"], errors="coerce")
    df = df.dropna(subset=["Date", "Close"]).sort_values("Date")
    window = df[(df["Date"] >= pd.Timestamp(start)) & (df["Date"] <= pd.Timestamp(end))]
    if len(window) < 2:
        return {"return_pct": None, "reason": "insufficient TWII rows"}
    first = float(window.iloc[0]["Close"])
    last = float(window.iloc[-1]["Close"])
    return {
        "start_date": window.iloc[0]["Date"].date().isoformat(),
        "end_date": window.iloc[-1]["Date"].date().isoformat(),
        "start_close": first,
        "end_close": last,
        "return_pct": last / first - 1.0 if first > 0 else None,
        "reason": None,
    }


def _weighted_mean(values: pd.Series, weights: pd.Series) -> float | None:
    clean = pd.DataFrame({"value": values, "weight": weights}).dropna()
    clean = clean[np.isfinite(clean["value"]) & np.isfinite(clean["weight"]) & (clean["weight"] > 0)]
    if clean.empty:
        return None
    return float((clean["value"] * clean["weight"]).sum() / clean["weight"].sum())


def _official_sector_index_files() -> list[Path]:
    index_dir = Path(INDEX_DIR)
    if not index_dir.exists():
        return []
    patterns = [
        "sector_index_*.csv",
        "index_sector_*.csv",
        "index_TSE_*.csv",
        "tse_sector_*.csv",
    ]
    files: list[Path] = []
    for pattern in patterns:
        files.extend(index_dir.glob(pattern))
    return sorted(set(files))


def _build_local_proxy_benchmark(
    mapping: pd.DataFrame,
    start: str,
    end: str,
    weight_mode: str,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in mapping.itertuples(index=False):
        ticker = str(item.Ticker).zfill(4)
        sector = str(item.Sector)
        stats = _load_window_stats(ticker, start, end)
        if stats is None:
            continue
        weight_basis = 1.0 if weight_mode == BENCHMARK_EQUAL_WEIGHT else stats["avg_traded_value"]
        rows.append(
            {
                "ticker": ticker,
                "sector": sector,
                "window_return_pct": stats["window_return_pct"],
                "benchmark_weight_basis": weight_basis,
            }
        )

    universe = pd.DataFrame(rows)
    metadata = {
        "official_sector_index_files_found": [str(path) for path in _official_sector_index_files()],
        "fallback_reason": None,
        "actual_benchmark_source": weight_mode,
        "weight_basis": "equal_weight" if weight_mode == BENCHMARK_EQUAL_WEIGHT else "avg_close_times_volume",
    }
    if universe.empty:
        empty = pd.DataFrame(
            columns=["sector", "benchmark_weight", "sector_return_pct", "benchmark_tickers"]
        )
        return empty, metadata

    total_basis = float(universe["benchmark_weight_basis"].sum())
    if total_basis <= 0:
        universe["benchmark_weight_basis"] = 1.0
        total_basis = float(universe["benchmark_weight_basis"].sum())
        metadata["weight_basis"] = "equal_weight_fallback"

    grouped_rows = []
    for sector, group in universe.groupby("sector", dropna=False):
        sector_basis = float(group["benchmark_weight_basis"].sum())
        grouped_rows.append(
            {
                "sector": str(sector) if pd.notna(sector) else "unknown",
                "benchmark_tickers": int(group["ticker"].nunique()),
                "benchmark_weight_basis": sector_basis,
                "sector_return_pct": _weighted_mean(
                    group["window_return_pct"],
                    group["benchmark_weight_basis"],
                ),
                "benchmark_weight": sector_basis / total_basis,
            }
        )
    return pd.DataFrame(grouped_rows), metadata


def _build_benchmark(
    mapping: pd.DataFrame,
    start: str,
    end: str,
    benchmark_mode: str,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    if benchmark_mode == BENCHMARK_EQUAL_WEIGHT:
        return _build_local_proxy_benchmark(mapping, start, end, BENCHMARK_EQUAL_WEIGHT)

    official_files = _official_sector_index_files()
    if official_files:
        # The repo does not yet have a stable official sector-index schema.
        # Keep this explicit so future data can be wired in without silently
        # treating unknown files as trustworthy.
        benchmark, metadata = _build_local_proxy_benchmark(mapping, start, end, LOCAL_TURNOVER_PROXY)
        metadata["requested_benchmark_mode"] = benchmark_mode
        metadata["fallback_reason"] = (
            "official sector index files are present but no supported parser "
            "schema is implemented yet"
        )
        return benchmark, metadata

    benchmark, metadata = _build_local_proxy_benchmark(mapping, start, end, LOCAL_TURNOVER_PROXY)
    metadata["requested_benchmark_mode"] = benchmark_mode
    metadata["fallback_reason"] = (
        "no local official TSE sector index files found; using avg Close*Volume "
        "as a transparent liquidity/size proxy because shares outstanding are "
        "not available in local daily K data"
    )
    return benchmark, metadata


def _weighted_average(df: pd.DataFrame, value_col: str, weight_col: str) -> float | None:
    if value_col not in df.columns or weight_col not in df.columns:
        return None
    values = pd.to_numeric(df[value_col], errors="coerce")
    weights = pd.to_numeric(df[weight_col], errors="coerce")
    clean = pd.DataFrame({"value": values, "weight": weights}).dropna()
    clean = clean[np.isfinite(clean["value"]) & np.isfinite(clean["weight"]) & (clean["weight"] > 0)]
    if clean.empty:
        return None
    return float((clean["value"] * clean["weight"]).sum() / clean["weight"].sum())


def _champion_by_sector(positions: pd.DataFrame) -> pd.DataFrame:
    if positions.empty:
        return pd.DataFrame()

    grouped_rows = []
    for sector, group in positions.groupby("sector", dropna=False):
        group = group.copy()
        sector_name = str(sector) if pd.notna(sector) else "unknown"
        weight = float(group["audit_weight"].sum())
        realized = _weighted_average(group, "realized_pct", "audit_weight")
        hold_to_end = _weighted_average(group, "hold_to_end_pct", "audit_weight")
        alpha_if_held = _weighted_average(group, "alpha_if_held_pct", "audit_weight")
        hold_values = pd.to_numeric(group.get("hold_to_end_pct"), errors="coerce").dropna()
        if hold_values.empty:
            hold_min = None
            hold_max = None
            hold_dispersion = None
        else:
            hold_min = float(hold_values.min())
            hold_max = float(hold_values.max())
            hold_dispersion = hold_max - hold_min
        grouped_rows.append(
            {
                "sector": sector_name,
                "champion_positions": int(len(group)),
                "champion_unique_tickers": int(group["ticker"].nunique()),
                "champion_weight": weight,
                "champion_realized_return_pct": realized,
                "champion_hold_to_end_return_pct": hold_to_end,
                "champion_alpha_if_held_pct": alpha_if_held,
                "champion_hold_min_return_pct": hold_min,
                "champion_hold_max_return_pct": hold_max,
                "champion_hold_dispersion_pct": hold_dispersion,
                "ma5_break_positions": int((group.get("exit_reason", "") == "MA5_BREAK").sum()),
                "stopped_positions": int((group.get("status", "") == "stopped_out").sum()),
                "open_positions": int((group.get("status", "") == "open").sum()),
            }
        )
    return pd.DataFrame(grouped_rows)


def _build_attribution(
    positions: pd.DataFrame,
    benchmark: pd.DataFrame,
    benchmark_return: float | None,
    twii_return: float | None,
    active_weight_alert: float,
    dispersion_alert: float,
) -> pd.DataFrame:
    champion = _champion_by_sector(positions)
    sectors = sorted(set(champion.get("sector", [])) | set(benchmark.get("sector", [])))
    if not sectors:
        return pd.DataFrame()

    merged = pd.DataFrame({"sector": sectors})
    if not champion.empty:
        merged = merged.merge(champion, on="sector", how="left")
    if not benchmark.empty:
        merged = merged.merge(benchmark, on="sector", how="left")

    merged["champion_positions"] = merged["champion_positions"].fillna(0).astype(int)
    merged["champion_unique_tickers"] = merged["champion_unique_tickers"].fillna(0).astype(int)
    merged["champion_weight"] = pd.to_numeric(merged["champion_weight"], errors="coerce").fillna(0.0)
    merged["benchmark_weight"] = pd.to_numeric(merged["benchmark_weight"], errors="coerce").fillna(0.0)
    merged["active_weight"] = merged["champion_weight"] - merged["benchmark_weight"]

    for column in [
        "sector_return_pct",
        "champion_realized_return_pct",
        "champion_hold_to_end_return_pct",
        "champion_alpha_if_held_pct",
        "champion_hold_min_return_pct",
        "champion_hold_max_return_pct",
        "champion_hold_dispersion_pct",
    ]:
        if column in merged.columns:
            merged[column] = pd.to_numeric(merged[column], errors="coerce")

    benchmark_return = float(benchmark_return) if benchmark_return is not None else 0.0
    merged["allocation_effect_pct"] = merged["active_weight"] * (
        merged["sector_return_pct"].fillna(benchmark_return) - benchmark_return
    )
    merged["selection_effect_pct"] = merged["champion_weight"] * (
        merged["champion_hold_to_end_return_pct"].fillna(merged["sector_return_pct"])
        - merged["sector_return_pct"].fillna(benchmark_return)
    )
    merged["total_effect_pct"] = merged["allocation_effect_pct"] + merged["selection_effect_pct"]
    merged["direct_champion_contribution_pct"] = (
        merged["champion_weight"] * merged["champion_hold_to_end_return_pct"].fillna(0.0)
    )
    merged["benchmark_contribution_pct"] = merged["benchmark_weight"] * merged["sector_return_pct"].fillna(0.0)
    merged["sector_return_vs_benchmark_pct"] = merged["sector_return_pct"] - benchmark_return
    merged["sector_return_vs_twii_pct"] = (
        merged["sector_return_pct"] - float(twii_return)
        if twii_return is not None
        else np.nan
    )
    merged["flag"] = ""
    merged["dispersion_flag"] = ""

    underweight = (
        (merged["active_weight"] <= -abs(active_weight_alert))
        & (merged["sector_return_pct"].fillna(-np.inf) > benchmark_return)
    )
    overweight_negative = (
        (merged["active_weight"] >= abs(active_weight_alert))
        & (merged["sector_return_pct"].fillna(0.0) < 0)
    )
    overweight_lagging = (
        (merged["active_weight"] >= abs(active_weight_alert))
        & (merged["sector_return_pct"].fillna(benchmark_return) < benchmark_return)
    )
    merged.loc[underweight, "flag"] = "UNDERWEIGHT_HIGH_RETURN"
    merged.loc[overweight_negative, "flag"] = "OVERWEIGHT_NEGATIVE_RETURN"
    merged.loc[
        overweight_lagging & (merged["flag"] == ""),
        "flag",
    ] = "OVERWEIGHT_LAGGING_RETURN"
    high_dispersion = (
        merged["champion_positions"].fillna(0).astype(int) >= 2
    ) & (merged["champion_hold_dispersion_pct"].fillna(0.0) > abs(dispersion_alert))
    merged.loc[high_dispersion, "dispersion_flag"] = "HIGH_WITHIN_SECTOR_DISPERSION"
    merged = merged.sort_values(
        ["champion_weight", "benchmark_weight", "sector_return_pct"],
        ascending=[False, False, False],
    ).reset_index(drop=True)
    return merged


def _build_reconciliation(attribution: pd.DataFrame, twii_return: float | None) -> dict[str, Any]:
    if attribution.empty:
        return {
            "champion_return_pct": None,
            "benchmark_return_pct": None,
            "champion_vs_benchmark_alpha_pct": None,
            "allocation_effect_total_pct": None,
            "selection_effect_total_pct": None,
            "allocation_plus_selection_pct": None,
            "reconciliation_error_pct": None,
            "reconciliation_pass": False,
            "twii_alpha_pct": None,
            "twii_scale_residual_pct": None,
        }

    champion_return = float(attribution["direct_champion_contribution_pct"].fillna(0.0).sum())
    benchmark_return = float(attribution["benchmark_contribution_pct"].fillna(0.0).sum())
    allocation = float(attribution["allocation_effect_pct"].fillna(0.0).sum())
    selection = float(attribution["selection_effect_pct"].fillna(0.0).sum())
    total = allocation + selection
    alpha = champion_return - benchmark_return
    error = total - alpha
    twii_alpha = champion_return - float(twii_return) if twii_return is not None else None
    twii_scale_residual = twii_alpha - alpha if twii_alpha is not None else None
    return {
        "champion_return_pct": champion_return,
        "benchmark_return_pct": benchmark_return,
        "champion_vs_benchmark_alpha_pct": alpha,
        "allocation_effect_total_pct": allocation,
        "selection_effect_total_pct": selection,
        "allocation_plus_selection_pct": total,
        "reconciliation_error_pct": error,
        "reconciliation_pass": abs(error) <= 0.005,
        "twii_alpha_pct": twii_alpha,
        "twii_scale_residual_pct": twii_scale_residual,
    }


def _write_markdown(
    path: Path,
    *,
    summary: dict[str, Any],
    attribution: pd.DataFrame,
    alerts: list[dict[str, Any]],
    dispersion_alerts: list[dict[str, Any]],
) -> None:
    rows = attribution[
        [
            "sector",
            "champion_positions",
            "champion_weight",
            "benchmark_weight",
            "active_weight",
            "sector_return_pct",
            "champion_hold_to_end_return_pct",
            "champion_hold_dispersion_pct",
            "allocation_effect_pct",
            "selection_effect_pct",
            "flag",
            "dispersion_flag",
        ]
    ].head(20)
    reconciliation = summary["reconciliation"]

    lines = [
        "# Sector Beta Exposure Audit",
        "",
        "## Scope",
        "",
        f"- Generated at: {summary['generated_at']}",
        f"- Window: {summary['start']} to {summary['end']}",
        f"- Champion positions: {summary['champion_positions']}",
        f"- Benchmark universe: {summary['benchmark_tickers']} local tickers",
        f"- Benchmark mode: `{summary['benchmark_mode']}`",
        f"- Benchmark source: `{summary['actual_benchmark_source']}`",
        f"- TWII return: {_pct(summary['twii_return_pct'])}",
        "",
        "## Key Findings",
        "",
        f"- Champion top sectors: {summary['champion_top_sectors'] or '-'}",
        f"- Benchmark leading sectors: {summary['benchmark_leading_sectors'] or '-'}",
        f"- Active-weight alerts: {len(alerts)} sectors above threshold",
        f"- Within-sector dispersion alerts: {len(dispersion_alerts)} sectors above threshold",
        f"- Champion vs benchmark alpha: {_pct(reconciliation['champion_vs_benchmark_alpha_pct'])}",
        f"- Allocation + selection: {_pct(reconciliation['allocation_plus_selection_pct'])}",
        f"- Reconciliation error: {_pct(reconciliation['reconciliation_error_pct'])}",
        f"- TWII scale residual: {_pct(reconciliation['twii_scale_residual_pct'])}",
        "",
        "## Reconciliation",
        "",
        "| metric | value |",
        "| --- | ---: |",
        f"| Champion return basis | {_pct(reconciliation['champion_return_pct'])} |",
        f"| Benchmark return | {_pct(reconciliation['benchmark_return_pct'])} |",
        f"| Champion vs benchmark alpha | {_pct(reconciliation['champion_vs_benchmark_alpha_pct'])} |",
        f"| Allocation effect | {_pct(reconciliation['allocation_effect_total_pct'])} |",
        f"| Selection effect | {_pct(reconciliation['selection_effect_total_pct'])} |",
        f"| Allocation + selection | {_pct(reconciliation['allocation_plus_selection_pct'])} |",
        f"| Reconciliation error | {_pct(reconciliation['reconciliation_error_pct'])} |",
        f"| TWII alpha on same return basis | {_pct(reconciliation['twii_alpha_pct'])} |",
        f"| TWII scale residual | {_pct(reconciliation['twii_scale_residual_pct'])} |",
        "",
        "## Sector Attribution",
        "",
        "| sector | picks | champion wt | benchmark wt | active wt | sector ret | champion held | dispersion | allocation | selection | flag | dispersion flag |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |",
    ]
    for item in rows.itertuples(index=False):
        lines.append(
            "| "
            f"{item.sector} | "
            f"{int(item.champion_positions)} | "
            f"{_pct(item.champion_weight)} | "
            f"{_pct(item.benchmark_weight)} | "
            f"{_pct(item.active_weight)} | "
            f"{_pct(item.sector_return_pct)} | "
            f"{_pct(item.champion_hold_to_end_return_pct)} | "
            f"{_pct(item.champion_hold_dispersion_pct)} | "
            f"{_pct(item.allocation_effect_pct)} | "
            f"{_pct(item.selection_effect_pct)} | "
            f"{item.flag or ''} | "
            f"{item.dispersion_flag or ''} |"
        )

    if alerts:
        lines.extend(["", "## Alerts", ""])
        for alert in alerts:
            lines.append(
                "- "
                f"{alert['flag']}: {alert['sector']} "
                f"active_weight={_pct(alert['active_weight'])}, "
                f"sector_return={_pct(alert['sector_return_pct'])}"
            )

    if dispersion_alerts:
        lines.extend(["", "## Dispersion Alerts", ""])
        for alert in dispersion_alerts:
            lines.append(
                "- "
                f"{alert['sector']}: dispersion={_pct(alert['champion_hold_dispersion_pct'])}, "
                f"min={_pct(alert['champion_hold_min_return_pct'])}, "
                f"max={_pct(alert['champion_hold_max_return_pct'])}"
            )

    lines.extend(
        [
            "",
            "## Notes",
            "",
            "- Champion return basis is `hold_to_end_pct` from the week1 attribution file, so exit timing is intentionally separated from sector selection.",
            "- `market_cap_sector_index` tries local official sector index files first. In this run it falls back to local traded-value weighting because official sector index files and shares-outstanding data are not available locally.",
            "- `TWII scale residual` is the part of Champion-vs-TWII alpha not explained by this local sector benchmark scale.",
            "- This report is research-only and does not change sector caps, entry filters, or production exits.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def run_audit(args: argparse.Namespace) -> dict[str, Any]:
    paths = AuditPaths(
        db_path=Path(args.db_path),
        attribution_path=Path(args.attribution_path),
        sector_mapping_path=Path(args.sector_mapping_path),
        report_dir=Path(args.report_dir),
    )
    paths.report_dir.mkdir(parents=True, exist_ok=True)

    positions = _load_champion_positions(paths.db_path, args.start, args.end)
    attribution_source = _load_week1_attribution(paths.attribution_path)
    positions = _merge_attribution(positions, attribution_source)
    mapping = _load_sector_mapping(paths.sector_mapping_path, args.benchmark_market)
    benchmark, benchmark_metadata = _build_benchmark(
        mapping,
        args.start,
        args.end,
        args.benchmark_mode,
    )
    twii = _load_twii_return(args.start, args.end)
    twii_return = _safe_float(twii.get("return_pct"))
    benchmark_return = (
        float((benchmark["benchmark_weight"] * benchmark["sector_return_pct"]).fillna(0.0).sum())
        if not benchmark.empty
        else None
    )
    decomposition = _build_attribution(
        positions,
        benchmark,
        benchmark_return,
        twii_return,
        active_weight_alert=args.active_weight_alert,
        dispersion_alert=args.dispersion_alert,
    )
    reconciliation = _build_reconciliation(decomposition, twii_return)

    alerts = (
        decomposition[decomposition["flag"].astype(str) != ""]
        .sort_values("active_weight", key=lambda s: s.abs(), ascending=False)
        .to_dict(orient="records")
        if not decomposition.empty
        else []
    )
    dispersion_alerts = (
        decomposition[decomposition["dispersion_flag"].astype(str) != ""]
        .sort_values("champion_hold_dispersion_pct", ascending=False)
        .to_dict(orient="records")
        if not decomposition.empty
        else []
    )

    benchmark_tickers = int(benchmark["benchmark_tickers"].sum()) if not benchmark.empty else 0
    champion_top = decomposition[decomposition["champion_weight"] > 0].head(5)
    leading = decomposition.dropna(subset=["sector_return_pct"]).sort_values(
        "sector_return_pct",
        ascending=False,
    ).head(5)
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "start": args.start,
        "end": args.end,
        "db_path": str(paths.db_path),
        "attribution_path": str(paths.attribution_path),
        "benchmark_mode": f"{args.benchmark_mode}:{args.benchmark_market}",
        "requested_benchmark_mode": args.benchmark_mode,
        "actual_benchmark_source": benchmark_metadata.get("actual_benchmark_source"),
        "benchmark_weight_basis": benchmark_metadata.get("weight_basis"),
        "benchmark_fallback_reason": benchmark_metadata.get("fallback_reason"),
        "benchmark_metadata": benchmark_metadata,
        "champion_positions": int(len(positions)),
        "champion_total_weight": float(positions["target_weight"].sum()) if not positions.empty else 0.0,
        "benchmark_tickers": benchmark_tickers,
        "twii": twii,
        "twii_return_pct": twii_return,
        "benchmark_return_pct": benchmark_return,
        "reconciliation": reconciliation,
        "active_weight_alert": float(args.active_weight_alert),
        "dispersion_alert": float(args.dispersion_alert),
        "champion_top_sectors": ", ".join(
            f"{row.sector} {_pct(row.champion_weight)}" for row in champion_top.itertuples(index=False)
        ),
        "benchmark_leading_sectors": ", ".join(
            f"{row.sector} {_pct(row.sector_return_pct)}" for row in leading.itertuples(index=False)
        ),
    }

    suffix = f"{args.start.replace('-', '')}_{args.end.replace('-', '')}"
    csv_path = paths.report_dir / f"sector_beta_audit_{suffix}.csv"
    md_path = paths.report_dir / f"sector_beta_audit_{suffix}.md"
    json_path = paths.report_dir / f"sector_beta_audit_{suffix}.json"

    decomposition.to_csv(csv_path, index=False, encoding="utf-8-sig")
    _write_markdown(
        md_path,
        summary=summary,
        attribution=decomposition,
        alerts=alerts,
        dispersion_alerts=dispersion_alerts,
    )
    payload = {
        "summary": summary,
        "sector_alpha_decomposition": decomposition.to_dict(orient="records"),
        "alerts": alerts,
        "dispersion_alerts": dispersion_alerts,
    }
    json_path.write_text(
        json.dumps(_sanitize_json(payload), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return {
        "summary": summary,
        "csv": str(csv_path),
        "markdown": str(md_path),
        "json": str(json_path),
        "alerts": alerts,
    }


def main(argv: list[str] | None = None) -> dict[str, Any]:
    args = _parse_args(argv)
    result = run_audit(args)
    summary = result["summary"]
    print(
        "[sector-beta-audit] "
        f"positions={summary['champion_positions']} "
        f"benchmark_tickers={summary['benchmark_tickers']} "
        f"twii={_pct(summary['twii_return_pct'])} "
        f"benchmark={_pct(summary['benchmark_return_pct'])} "
        f"alerts={len(result['alerts'])}"
    )
    print(f"[sector-beta-audit] report={result['markdown']}")
    print(f"[sector-beta-audit] csv={result['csv']}")
    print(f"[sector-beta-audit] json={result['json']}")
    return result


if __name__ == "__main__":
    main()
