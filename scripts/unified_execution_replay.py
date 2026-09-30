"""Replay unified signals through the execution-adjusted ledger.

This script is intentionally read-only for production state: it builds a
temporary unified portfolio database, replays existing unified signal artifacts,
and writes audit reports under ml/reports.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))

from ml.config import INDEX_DIR, MODEL_DIR, REPORT_DIR
from ml.benchmark_contract import build_trade_alpha_benchmark_contract
from scripts.exit_policies import EXIT_POLICY_BASELINE_20D, SUPPORTED_EXIT_POLICIES
from scripts.update_unified_portfolio import (
    PORTFOLIO_START_DATE,
    TWENTY_DAY_HOLD,
    sync_unified_portfolio,
)

SIGNAL_FILE_RE = re.compile(r"^unified_signals_(\d{4}-\d{2}-\d{2})\.csv$")
SIGNAL_FILE_EXPLICIT_RE = re.compile(
    r"^unified_signals(?:_[A-Za-z0-9][A-Za-z0-9_-]*)?_(\d{4}-\d{2}-\d{2})\.csv$"
)
LATEST_JSON = "unified_execution_replay_latest.json"
LATEST_MD = "unified_execution_replay_latest.md"
LATEST_POSITIONS_CSV = "unified_execution_replay_positions_latest.csv"

CLOSED_STATUSES = {"closed", "stopped_out"}
FILLED_STATUSES = {"open", "closed", "stopped_out"}


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Replay unified signal artifacts through order simulation."
    )
    parser.add_argument("--start-date", default=PORTFOLIO_START_DATE)
    parser.add_argument("--end-date")
    parser.add_argument("--signal-file", action="append")
    parser.add_argument("--output-dir", default=REPORT_DIR)
    parser.add_argument("--db-path")
    parser.add_argument("--keep-db", action="store_true")
    parser.add_argument(
        "--exit-policy",
        choices=sorted(SUPPORTED_EXIT_POLICIES),
        default=EXIT_POLICY_BASELINE_20D,
        help="Research exit policy applied to 20D positions during replay.",
    )
    return parser.parse_args(argv)


def _signal_date(path: str | Path, explicit: bool = False) -> str:
    pattern = SIGNAL_FILE_EXPLICIT_RE if explicit else SIGNAL_FILE_RE
    match = pattern.match(Path(path).name)
    if not match:
        raise ValueError(f"Cannot infer signal date from {path}")
    return match.group(1)


def _resolve_signal_files(
    signal_files: list[str] | None,
    start_date: str,
    end_date: str | None,
) -> list[str]:
    if signal_files:
        candidates = [str(Path(path).resolve()) for path in signal_files]
        key_func = lambda path: _signal_date(path, explicit=True)
    else:
        model_dir = Path(MODEL_DIR)
        candidates = [
            str(path.resolve())
            for path in model_dir.glob("unified_signals_*.csv")
            if SIGNAL_FILE_RE.match(path.name)
        ]
        key_func = _signal_date

    resolved = []
    for path in sorted(candidates, key=key_func):
        date_value = key_func(path)
        if date_value < start_date:
            continue
        if end_date and date_value > end_date:
            continue
        resolved.append(path)

    if not resolved:
        raise FileNotFoundError(
            f"No unified signal files found for {start_date} to {end_date or 'latest'}."
        )
    return resolved


def _read_frame(conn: sqlite3.Connection, query: str) -> pd.DataFrame:
    return pd.read_sql_query(query, conn)


def _load_replay_tables(
    db_path: str,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    with sqlite3.connect(db_path) as conn:
        positions = _read_frame(
            conn,
            """
            SELECT
                p.*,
                r.signals_file_path,
                r.signals_sha256,
                r.rule_version
            FROM unified_positions p
            JOIN unified_runs r ON r.id = p.opened_by_run_id
            ORDER BY p.prediction_date, p.ticker
            """,
        )
        runs = _read_frame(
            conn,
            """
            SELECT *
            FROM unified_runs
            ORDER BY prediction_date
            """,
        )
        latest_marks = _read_frame(
            conn,
            """
            SELECT m.position_id, m.mark_date, m.close
            FROM unified_marks m
            JOIN (
                SELECT position_id, MAX(mark_date) AS mark_date
                FROM unified_marks
                GROUP BY position_id
            ) latest
              ON latest.position_id = m.position_id
             AND latest.mark_date = m.mark_date
            """,
        )
        marks = _read_frame(
            conn,
            """
            SELECT m.*
            FROM unified_marks m
            JOIN unified_positions p ON p.id = m.position_id
            ORDER BY m.position_id, m.trading_day_number
            """,
        )
    return positions, runs, latest_marks, marks


def _counts(series: pd.Series) -> dict[str, int]:
    if series.empty:
        return {}
    return {
        str(key): int(value)
        for key, value in series.fillna("NULL").astype(str).value_counts().sort_index().items()
    }


def _weighted_average(values: pd.Series, weights: pd.Series) -> float | None:
    clean = pd.DataFrame({"value": values, "weight": weights}).dropna()
    clean = clean[np.isfinite(clean["value"]) & np.isfinite(clean["weight"]) & (clean["weight"] > 0)]
    if clean.empty:
        return None
    weight_sum = float(clean["weight"].sum())
    if weight_sum <= 0:
        return None
    return float((clean["value"] * clean["weight"]).sum() / weight_sum)


def _max_drawdown(equity: pd.Series) -> float | None:
    if equity.empty:
        return None
    peak = equity.cummax()
    drawdown = equity / peak - 1.0
    value = float(drawdown.min())
    return value if math.isfinite(value) else None


def _compound_return(daily_returns: pd.Series) -> tuple[float | None, float | None]:
    if daily_returns.empty:
        return None, None
    equity = (1.0 + daily_returns).cumprod()
    total_return = float(equity.iloc[-1] - 1.0)
    return total_return, _max_drawdown(equity)


def _load_twii_return(start_date: str | None, end_date: str | None) -> dict[str, Any]:
    if not start_date or not end_date:
        return {
            "start_date": start_date,
            "end_date": end_date,
            "return_pct": None,
            "reason": "missing replay date range",
        }

    index_path = Path(INDEX_DIR) / "index_TWII.csv"
    if not index_path.exists():
        return {
            "start_date": start_date,
            "end_date": end_date,
            "return_pct": None,
            "reason": f"index file not found: {index_path}",
        }

    index_df = pd.read_csv(index_path)
    if "Date" not in index_df.columns or "Close" not in index_df.columns:
        return {
            "start_date": start_date,
            "end_date": end_date,
            "return_pct": None,
            "reason": "index_TWII.csv missing Date or Close",
        }

    index_df["Date"] = pd.to_datetime(index_df["Date"], errors="coerce")
    index_df["Close"] = pd.to_numeric(index_df["Close"], errors="coerce")
    index_df = index_df.dropna(subset=["Date", "Close"]).sort_values("Date")
    start_ts = pd.to_datetime(start_date)
    end_ts = pd.to_datetime(end_date)
    window = index_df[(index_df["Date"] >= start_ts) & (index_df["Date"] <= end_ts)]
    if len(window) < 2:
        return {
            "start_date": start_date,
            "end_date": end_date,
            "return_pct": None,
            "reason": "insufficient TWII rows in replay window",
        }

    start_row = window.iloc[0]
    end_row = window.iloc[-1]
    start_close = float(start_row["Close"])
    end_close = float(end_row["Close"])
    return_pct = end_close / start_close - 1.0 if start_close > 0 else None
    return {
        "start_date": start_row["Date"].date().isoformat(),
        "end_date": end_row["Date"].date().isoformat(),
        "start_close": start_close,
        "end_close": end_close,
        "return_pct": return_pct,
        "reason": None,
    }


def _prepare_position_returns(
    positions: pd.DataFrame,
    latest_marks: pd.DataFrame,
    marks: pd.DataFrame,
) -> pd.DataFrame:
    result = positions.copy()
    if result.empty:
        result["effective_return_pct"] = np.nan
        result["return_source"] = pd.Series(dtype="object")
        result["max_close_return_pct"] = np.nan
        result["max_profit_close_return_pct"] = np.nan
        result["profit_giveback_pct"] = np.nan
        result["profit_giveback_ratio"] = np.nan
        return result

    for column in [
        "target_weight",
        "entry_price",
        "realized_return_pct",
        "entry_slippage_pct",
        "days_observed",
    ]:
        if column in result.columns:
            result[column] = pd.to_numeric(result[column], errors="coerce")

    if not latest_marks.empty:
        mark_df = latest_marks.rename(
            columns={"position_id": "id", "mark_date": "latest_mark_date", "close": "latest_mark_close"}
        )
        result = result.merge(mark_df, on="id", how="left")
        result["latest_mark_close"] = pd.to_numeric(result["latest_mark_close"], errors="coerce")
    else:
        result["latest_mark_date"] = None
        result["latest_mark_close"] = np.nan

    result["unrealized_return_pct"] = np.nan
    open_mask = (result["status"] == "open") & result["entry_price"].gt(0)
    result.loc[open_mask, "unrealized_return_pct"] = (
        result.loc[open_mask, "latest_mark_close"] / result.loc[open_mask, "entry_price"] - 1.0
    )

    result["effective_return_pct"] = np.nan
    result["return_source"] = None
    closed_mask = result["status"].isin(CLOSED_STATUSES) & result["realized_return_pct"].notna()
    result.loc[closed_mask, "effective_return_pct"] = result.loc[closed_mask, "realized_return_pct"]
    result.loc[closed_mask, "return_source"] = "realized"
    result.loc[open_mask, "effective_return_pct"] = result.loc[open_mask, "unrealized_return_pct"]
    result.loc[open_mask, "return_source"] = "mark_to_market"

    result["max_close_return_pct"] = np.nan
    if not marks.empty and "close_return_pct" in marks.columns:
        mark_returns = marks[["position_id", "close_return_pct"]].copy()
        mark_returns["close_return_pct"] = pd.to_numeric(
            mark_returns["close_return_pct"],
            errors="coerce",
        )
        peak_returns = (
            mark_returns.dropna(subset=["close_return_pct"])
            .groupby("position_id")["close_return_pct"]
            .max()
            .rename("max_close_return_pct")
            .reset_index()
            .rename(columns={"position_id": "id"})
        )
        result = result.merge(peak_returns, on="id", how="left", suffixes=("", "_peak"))
        result["max_close_return_pct"] = result["max_close_return_pct_peak"].combine_first(
            result["max_close_return_pct"]
        )
        result = result.drop(columns=["max_close_return_pct_peak"])

    result["max_profit_close_return_pct"] = result["max_close_return_pct"].clip(lower=0.0)
    result["profit_giveback_pct"] = np.nan
    has_profit_peak = result["max_profit_close_return_pct"].gt(0) & result["effective_return_pct"].notna()
    result.loc[has_profit_peak, "profit_giveback_pct"] = (
        result.loc[has_profit_peak, "max_profit_close_return_pct"]
        - result.loc[has_profit_peak, "effective_return_pct"]
    ).clip(lower=0.0)
    result["profit_giveback_ratio"] = np.nan
    result.loc[has_profit_peak, "profit_giveback_ratio"] = (
        result.loc[has_profit_peak, "profit_giveback_pct"]
        / result.loc[has_profit_peak, "max_profit_close_return_pct"]
    )
    return result


def _build_daily_exit_curve(positions: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    realized = positions[
        positions["status"].isin(CLOSED_STATUSES)
        & positions["realized_return_pct"].notna()
        & positions["exit_date"].notna()
    ].copy()
    if realized.empty:
        return pd.DataFrame(), {
            "exit_day_compounded_return_pct": None,
            "exit_day_max_drawdown_pct": None,
            "exit_day_count": 0,
        }

    realized["exit_date"] = pd.to_datetime(realized["exit_date"], errors="coerce")
    realized = realized.dropna(subset=["exit_date"])
    rows: list[dict[str, Any]] = []
    for exit_date, group in realized.groupby(realized["exit_date"].dt.date):
        weighted = _weighted_average(group["realized_return_pct"], group["target_weight"])
        average = float(group["realized_return_pct"].mean())
        rows.append(
            {
                "exit_date": exit_date.isoformat(),
                "position_count": int(len(group)),
                "avg_return_pct": average,
                "weighted_return_pct": weighted if weighted is not None else average,
            }
        )

    daily = pd.DataFrame(rows).sort_values("exit_date")
    total_return, max_dd = _compound_return(daily["weighted_return_pct"])
    return daily, {
        "exit_day_compounded_return_pct": total_return,
        "exit_day_max_drawdown_pct": max_dd,
        "exit_day_count": int(len(daily)),
    }


def _summarize_positions(
    positions: pd.DataFrame,
    runs: pd.DataFrame,
    latest_marks: pd.DataFrame,
    marks: pd.DataFrame,
    exit_policy_name: str,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    positions = _prepare_position_returns(positions, latest_marks, marks)
    daily_curve, daily_summary = _build_daily_exit_curve(positions)

    closed = positions[
        positions["status"].isin(CLOSED_STATUSES) & positions["realized_return_pct"].notna()
    ].copy()
    filled = positions[
        positions["status"].isin(FILLED_STATUSES) & positions["effective_return_pct"].notna()
    ].copy()
    open_positions = positions[positions["status"] == "open"].copy()

    if not runs.empty:
        signal_start = str(runs["prediction_date"].min())
        signal_end = str(runs["prediction_date"].max())
    else:
        signal_start = None
        signal_end = None

    filled_dates = pd.concat(
        [
            pd.to_datetime(filled.get("entry_date"), errors="coerce"),
            pd.to_datetime(filled.get("exit_date"), errors="coerce"),
            pd.to_datetime(filled.get("latest_mark_date"), errors="coerce"),
        ],
        ignore_index=True,
    ).dropna()
    replay_start = filled_dates.min().date().isoformat() if not filled_dates.empty else signal_start
    replay_end = filled_dates.max().date().isoformat() if not filled_dates.empty else signal_end

    twii = _load_twii_return(replay_start, replay_end)
    mtm_weighted = _weighted_average(filled["effective_return_pct"], filled["target_weight"])
    realized_weighted = _weighted_average(closed["realized_return_pct"], closed["target_weight"])
    twii_return = twii.get("return_pct")
    giveback_sample = filled[
        filled["profit_giveback_ratio"].notna()
        & filled["max_profit_close_return_pct"].gt(0)
    ].copy()
    capital_weighted_hold_days = _weighted_average(
        filled.get("days_observed", pd.Series(dtype="float64")),
        filled.get("target_weight", pd.Series(dtype="float64")),
    )

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "exit_policy": exit_policy_name,
        "signal_start": signal_start,
        "signal_end": signal_end,
        "replay_start": replay_start,
        "replay_end": replay_end,
        "benchmark_contract": build_trade_alpha_benchmark_contract(
            start_date=replay_start,
            end_date=replay_end,
        ),
        "signal_file_count": int(len(runs)),
        "position_count": int(len(positions)),
        "status_counts": _counts(positions.get("status", pd.Series(dtype="object"))),
        "entry_signal_type_counts": _counts(
            positions.get("entry_signal_type", pd.Series(dtype="object"))
        ),
        "current_signal_type_counts": _counts(
            positions.get("current_signal_type", pd.Series(dtype="object"))
        ),
        "fill_status_counts": _counts(positions.get("fill_status", pd.Series(dtype="object"))),
        "order_type_counts": _counts(positions.get("order_type", pd.Series(dtype="object"))),
        "exit_reason_counts": _counts(positions.get("exit_reason", pd.Series(dtype="object"))),
        "filled_position_count": int(len(filled)),
        "closed_position_count": int(len(closed)),
        "open_position_count": int(len(open_positions)),
        "missed_entry_count": int((positions["status"] == "missed_entry_gap_up").sum()) if not positions.empty else 0,
        "avg_entry_slippage_pct": (
            float(filled["entry_slippage_pct"].mean())
            if not filled.empty and filled["entry_slippage_pct"].notna().any()
            else None
        ),
        "avg_filled_hold_days": (
            float(filled["days_observed"].mean())
            if not filled.empty and filled["days_observed"].notna().any()
            else None
        ),
        "capital_weighted_hold_days": capital_weighted_hold_days,
        "capital_turnover_20d_multiple": (
            TWENTY_DAY_HOLD / capital_weighted_hold_days
            if capital_weighted_hold_days is not None and capital_weighted_hold_days > 0
            else None
        ),
        "profit_giveback_sample_count": int(len(giveback_sample)),
        "avg_profit_giveback_pct": (
            float(giveback_sample["profit_giveback_pct"].mean())
            if not giveback_sample.empty
            else None
        ),
        "avg_profit_giveback_ratio_pct": (
            float(giveback_sample["profit_giveback_ratio"].mean())
            if not giveback_sample.empty
            else None
        ),
        "capital_weighted_profit_giveback_pct": _weighted_average(
            giveback_sample.get("profit_giveback_pct", pd.Series(dtype="float64")),
            giveback_sample.get("target_weight", pd.Series(dtype="float64")),
        ),
        "realized_avg_return_pct": (
            float(closed["realized_return_pct"].mean()) if not closed.empty else None
        ),
        "realized_median_return_pct": (
            float(closed["realized_return_pct"].median()) if not closed.empty else None
        ),
        "realized_win_rate_pct": (
            float((closed["realized_return_pct"] > 0).mean()) if not closed.empty else None
        ),
        "realized_capital_weighted_return_pct": realized_weighted,
        "mtm_capital_weighted_return_pct": mtm_weighted,
        "mtm_cash_return_contribution_pct": (
            float((filled["target_weight"] * filled["effective_return_pct"]).sum())
            if not filled.empty
            else None
        ),
        "open_avg_unrealized_return_pct": (
            float(open_positions["unrealized_return_pct"].mean())
            if not open_positions.empty and open_positions["unrealized_return_pct"].notna().any()
            else None
        ),
        "open_capital_weighted_unrealized_return_pct": _weighted_average(
            open_positions.get("unrealized_return_pct", pd.Series(dtype="float64")),
            open_positions.get("target_weight", pd.Series(dtype="float64")),
        ),
        "twii": twii,
        "realized_alpha_vs_twii_pct": (
            realized_weighted - twii_return
            if realized_weighted is not None and twii_return is not None
            else None
        ),
        "mtm_alpha_vs_twii_pct": (
            mtm_weighted - twii_return
            if mtm_weighted is not None and twii_return is not None
            else None
        ),
        **daily_summary,
    }
    return positions, daily_curve, summary


def _pct(value: Any, digits: int = 2) -> str:
    if value is None:
        return "n/a"
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return "n/a"
    if not math.isfinite(numeric):
        return "n/a"
    return f"{numeric * 100:.{digits}f}%"


def _num(value: Any, digits: int = 4) -> str:
    if value is None:
        return "n/a"
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return "n/a"
    if not math.isfinite(numeric):
        return "n/a"
    return f"{numeric:.{digits}f}"


def _table_from_counts(counts: dict[str, int]) -> list[str]:
    if not counts:
        return ["| value | count |", "| --- | ---: |", "| n/a | 0 |"]
    rows = ["| value | count |", "| --- | ---: |"]
    rows.extend(f"| {key} | {value} |" for key, value in counts.items())
    return rows


def _sample_table(df: pd.DataFrame, columns: list[str], limit: int = 10) -> list[str]:
    rows = [f"| {' | '.join(columns)} |", f"| {' | '.join(['---'] * len(columns))} |"]
    if df.empty:
        rows.append(f"| {' | '.join(['n/a'] * len(columns))} |")
        return rows
    sample = df.head(limit)
    for _, row in sample.iterrows():
        values = []
        for column in columns:
            value = row.get(column)
            if isinstance(value, float):
                values.append(_pct(value) if "pct" in column else _num(value))
            elif pd.isna(value):
                values.append("")
            else:
                values.append(str(value))
        rows.append(f"| {' | '.join(values)} |")
    return rows


def _write_markdown(
    output_path: Path,
    summary: dict[str, Any],
    positions: pd.DataFrame,
    daily_curve: pd.DataFrame,
    signal_files: list[str],
    db_path: str | None,
) -> None:
    stopped = positions[positions["status"] == "stopped_out"].copy()
    stopped = stopped.sort_values("realized_return_pct", na_position="last")
    missed = positions[positions["status"] == "missed_entry_gap_up"].copy()
    worst = positions[
        positions["effective_return_pct"].notna()
    ].sort_values("effective_return_pct", na_position="last")

    lines = [
        "# Unified Execution Replay Latest",
        "",
        "This is an execution-adjusted replay built from existing unified signal artifacts.",
        "It uses a temporary ledger and does not mutate production portfolio state.",
        "",
        "## Scope",
        "",
        f"- Generated at: {summary['generated_at']}",
        f"- Signal window: {summary['signal_start']} to {summary['signal_end']}",
        f"- Replay mark window: {summary['replay_start']} to {summary['replay_end']}",
        f"- Exit policy: `{summary['exit_policy']}`",
        f"- Signal files: {len(signal_files)}",
        f"- Temp database: {db_path or 'deleted after replay'}",
        "",
        "## Key Metrics",
        "",
        "| metric | value |",
        "| --- | ---: |",
        f"| Benchmark | {summary['benchmark_contract']['benchmark_label']} |",
        f"| Positions | {summary['position_count']} |",
        f"| Filled positions | {summary['filled_position_count']} |",
        f"| Closed/stopped positions | {summary['closed_position_count']} |",
        f"| Open positions | {summary['open_position_count']} |",
        f"| Missed entries | {summary['missed_entry_count']} |",
        f"| Avg entry slippage | {_pct(summary['avg_entry_slippage_pct'])} |",
        f"| Avg filled hold days | {_num(summary['avg_filled_hold_days'], 2)} |",
        f"| Capital-weighted hold days | {_num(summary['capital_weighted_hold_days'], 2)} |",
        f"| Capital turnover vs 20D | {_num(summary['capital_turnover_20d_multiple'], 2)}x |",
        f"| Avg profit giveback | {_pct(summary['avg_profit_giveback_pct'])} |",
        f"| Avg profit giveback ratio | {_pct(summary['avg_profit_giveback_ratio_pct'])} |",
        f"| Capital-weighted profit giveback | {_pct(summary['capital_weighted_profit_giveback_pct'])} |",
        f"| Realized avg return | {_pct(summary['realized_avg_return_pct'])} |",
        f"| Realized median return | {_pct(summary['realized_median_return_pct'])} |",
        f"| Realized win rate | {_pct(summary['realized_win_rate_pct'])} |",
        f"| Realized capital-weighted return | {_pct(summary['realized_capital_weighted_return_pct'])} |",
        f"| MTM capital-weighted return | {_pct(summary['mtm_capital_weighted_return_pct'])} |",
        f"| MTM cash return contribution | {_pct(summary['mtm_cash_return_contribution_pct'])} |",
        f"| TWII same-window return | {_pct(summary['twii'].get('return_pct'))} |",
        f"| Realized alpha vs TWII | {_pct(summary['realized_alpha_vs_twii_pct'])} |",
        f"| MTM alpha vs TWII | {_pct(summary['mtm_alpha_vs_twii_pct'])} |",
        f"| Exit-day compounded return | {_pct(summary['exit_day_compounded_return_pct'])} |",
        f"| Exit-day max drawdown | {_pct(summary['exit_day_max_drawdown_pct'])} |",
        "",
        "## Status Counts",
        "",
        *_table_from_counts(summary["status_counts"]),
        "",
        "## Entry Signal Counts",
        "",
        *_table_from_counts(summary["entry_signal_type_counts"]),
        "",
        "## Current Signal Counts",
        "",
        *_table_from_counts(summary["current_signal_type_counts"]),
        "",
        "## Fill Status Counts",
        "",
        *_table_from_counts(summary["fill_status_counts"]),
        "",
        "## Order Type Counts",
        "",
        *_table_from_counts(summary["order_type_counts"]),
        "",
        "## Exit Reason Counts",
        "",
        *_table_from_counts(summary["exit_reason_counts"]),
        "",
        "## Stopped Positions",
        "",
        *_sample_table(
            stopped,
            [
                "prediction_date",
                "ticker",
                "entry_signal_type",
                "exit_reason",
                "entry_slippage_pct",
                "realized_return_pct",
            ],
        ),
        "",
        "## Missed Entries",
        "",
        *_sample_table(
            missed,
            [
                "prediction_date",
                "ticker",
                "entry_signal_type",
                "order_type",
                "exit_reason",
            ],
        ),
        "",
        "## Worst Effective Returns",
        "",
        *_sample_table(
            worst,
            [
                "prediction_date",
                "ticker",
                "entry_signal_type",
                "status",
                "exit_reason",
                "effective_return_pct",
            ],
        ),
        "",
        "## Exit-Day Curve",
        "",
        *_sample_table(
            daily_curve,
            ["exit_date", "position_count", "weighted_return_pct", "avg_return_pct"],
            limit=20,
        ),
        "",
        "## Caveats",
        "",
        "- This replay is based on the unified signal artifacts currently on disk.",
        "- Older artifacts can contain signal mixes produced before later production gates.",
        "- Open 20D positions are marked to the latest available local K data.",
        "- MTM return is capital-weighted across filled positions, not a brokerage NAV curve.",
        "- TWII comparison is close-to-close over the replay mark window.",
        "",
    ]
    output_path.write_text("\n".join(lines), encoding="utf-8")


def _sanitize_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _sanitize_json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_sanitize_json(item) for item in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        value = float(value)
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (pd.Timestamp,)):
        return value.isoformat()
    if pd.isna(value):
        return None
    return value


def run_replay(args: argparse.Namespace) -> dict[str, Any]:
    signal_files = _resolve_signal_files(args.signal_file, args.start_date, args.end_date)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    temp_dir: str | None = None
    if args.db_path:
        db_path = str(Path(args.db_path).resolve())
    else:
        temp_dir = tempfile.mkdtemp(prefix="unified_execution_replay_")
        db_path = str(Path(temp_dir) / "unified_execution_replay.db")

    replay_result = sync_unified_portfolio(
        signal_files=signal_files,
        backfill_all=False,
        db_path=db_path,
        rule_version=f"unified-v2-order-sim-replay:{args.exit_policy}",
        exit_policy_name=args.exit_policy,
        portfolio_start_date=args.start_date,
    )

    positions, runs, latest_marks, marks = _load_replay_tables(db_path)
    positions, daily_curve, summary = _summarize_positions(
        positions,
        runs,
        latest_marks,
        marks,
        exit_policy_name=args.exit_policy,
    )

    output_positions = output_dir / LATEST_POSITIONS_CSV
    output_json = output_dir / LATEST_JSON
    output_md = output_dir / LATEST_MD

    positions.to_csv(output_positions, index=False, encoding="utf-8-sig")

    report_payload = {
        "summary": summary,
        "replay_result": replay_result,
        "signal_files": signal_files,
        "positions_csv": str(output_positions),
        "daily_exit_curve": daily_curve.to_dict(orient="records"),
        "exit_policy": args.exit_policy,
        "db_path": db_path if args.keep_db or args.db_path else None,
    }
    output_json.write_text(
        json.dumps(_sanitize_json(report_payload), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _write_markdown(
        output_md,
        summary=summary,
        positions=positions,
        daily_curve=daily_curve,
        signal_files=signal_files,
        db_path=db_path if args.keep_db or args.db_path else None,
    )

    if temp_dir and not args.keep_db:
        shutil.rmtree(temp_dir, ignore_errors=True)

    return {
        "summary": summary,
        "json": str(output_json),
        "markdown": str(output_md),
        "positions_csv": str(output_positions),
        "db_path": db_path if args.keep_db or args.db_path else None,
    }


def main(argv: list[str] | None = None) -> dict[str, Any]:
    args = _parse_args(argv)
    result = run_replay(args)
    summary = result["summary"]
    print(
        "[unified-replay] "
        f"signals={summary['signal_file_count']} positions={summary['position_count']} "
        f"filled={summary['filled_position_count']} closed={summary['closed_position_count']} "
        f"open={summary['open_position_count']} missed={summary['missed_entry_count']} "
        f"exit_policy={summary['exit_policy']} "
        f"mtm={_pct(summary['mtm_capital_weighted_return_pct'])} "
        f"twii={_pct(summary['twii'].get('return_pct'))} "
        f"alpha={_pct(summary['mtm_alpha_vs_twii_pct'])}"
    )
    print(f"[unified-replay] report={result['markdown']}")
    print(f"[unified-replay] positions={result['positions_csv']}")
    return result


if __name__ == "__main__":
    main()
