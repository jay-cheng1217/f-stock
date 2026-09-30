# -*- coding: utf-8 -*-
"""Audit RETRAIN-03 official OHLC repair and action-continuous features."""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from ml.corporate_actions import (  # noqa: E402
    backward_adjustment_multiplier,
    load_price_continuity_calendar,
)
from ml.features.technical import (  # noqa: E402
    MAX_TECHNICAL_GAP_DAYS,
    compute_technical_features,
)


DEFAULT_SOURCE = BASE_DIR / "日K資料"
DEFAULT_BACKUP = Path(r"F:\stock_backups\日K資料_20260717_RETRAIN03")
DEFAULT_REPORT = BASE_DIR / "ml" / "reports" / f"retrain03_price_integrity_{date.today():%Y%m%d}"

KNOWN_BREAKPOINTS = [
    ("0050", "2025-06-18", "ETF split"),
    ("8093", "2025-10-16", "wrong-basis local bar"),
    ("5228", "2021-02-22", "pre-listing non-regular row"),
    ("6546", "2020-11-09", "pre-listing non-regular row"),
]


def _load_price_columns(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path, low_memory=False)
    required = ["Date", "Open", "High", "Low", "Close", "Volume"]
    missing = [column for column in required if column not in frame]
    if missing:
        raise ValueError(f"{path} missing OHLCV columns: {missing}")
    frame["Date"] = pd.to_datetime(frame["Date"], errors="coerce").dt.normalize()
    for column in required[1:]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame.sort_values("Date", kind="stable").reset_index(drop=True)


def _action_maps(calendar: pd.DataFrame) -> tuple[dict[str, pd.DataFrame], dict[tuple[str, pd.Timestamp], float]]:
    groups = {
        str(ticker): group.copy()
        for ticker, group in calendar.groupby("stock_id", sort=False)
    }
    event_factor = {
        (str(row.stock_id), pd.Timestamp(row.date).normalize()): float(row.factor)
        for row in calendar.itertuples(index=False)
    }
    return groups, event_factor


def scan_tree(
    root: Path,
    *,
    calendar_groups: dict[str, pd.DataFrame],
    event_factor: dict[tuple[str, pd.Timestamp], float],
    threshold: float,
    start_date: pd.Timestamp,
) -> dict[str, Any]:
    metrics: dict[str, Any] = {
        "root": str(root),
        "files": 0,
        "rows": 0,
        "rows_since_start": 0,
        "duplicate_ticker_dates": 0,
        "ohlc_invariant_violations": 0,
        "nonpositive_ohlc_rows": 0,
        "nonpositive_ohlc_rows_since_start": 0,
        "raw_extreme_jumps": 0,
        "adjusted_extreme_jumps": 0,
        "raw_action_extreme_jumps": 0,
        "adjusted_action_extreme_jumps": 0,
        "adjusted_long_gap_extreme_jumps": 0,
        "long_gap_reset_rows": 0,
        "new_adjusted_extreme_from_raw": 0,
    }
    extreme_rows: list[dict[str, Any]] = []
    extreme_keys: set[str] = set()
    raw_extreme_keys: set[str] = set()
    adjusted_long_gap_extreme_keys: set[str] = set()

    for path in sorted(root.glob("*.csv")):
        ticker = path.stem
        frame = _load_price_columns(path)
        metrics["files"] += 1
        metrics["rows"] += int(len(frame))
        metrics["duplicate_ticker_dates"] += int(frame["Date"].duplicated().sum())

        valid = frame.dropna(subset=["Date", "Open", "High", "Low", "Close"]).copy()
        min_oc = valid[["Open", "Close"]].min(axis=1)
        max_oc = valid[["Open", "Close"]].max(axis=1)
        invalid_ohlc = valid["Low"].gt(min_oc) | valid["High"].lt(max_oc) | valid["Low"].gt(valid["High"])
        metrics["ohlc_invariant_violations"] += int(invalid_ohlc.sum())
        metrics["nonpositive_ohlc_rows"] += int(valid[["Open", "High", "Low", "Close"]].le(0).any(axis=1).sum())

        scoped = frame.loc[frame["Date"].ge(start_date)].copy()
        metrics["rows_since_start"] += int(len(scoped))
        scoped_valid = scoped.dropna(subset=["Open", "High", "Low", "Close"])
        metrics["nonpositive_ohlc_rows_since_start"] += int(
            scoped_valid[["Open", "High", "Low", "Close"]].le(0).any(axis=1).sum()
        )
        if len(scoped) < 2:
            continue
        gap_days = scoped["Date"].diff().dt.days
        long_gap_reset = gap_days.gt(MAX_TECHNICAL_GAP_DAYS).fillna(False)
        metrics["long_gap_reset_rows"] += int(long_gap_reset.sum())
        ticker_calendar = calendar_groups.get(ticker)
        if ticker_calendar is None:
            ticker_calendar = pd.DataFrame(columns=["stock_id", "date", "factor"])
        multiplier = backward_adjustment_multiplier(scoped["Date"], ticker, ticker_calendar)
        raw_close = scoped["Close"]
        adjusted_close = raw_close * multiplier
        raw_return = raw_close.pct_change(fill_method=None)
        adjusted_return = adjusted_close.pct_change(fill_method=None)
        raw_extreme = raw_return.abs().gt(threshold)
        adjusted_extreme = adjusted_return.abs().gt(threshold)
        metrics["raw_extreme_jumps"] += int(raw_extreme.sum())
        metrics["adjusted_extreme_jumps"] += int(adjusted_extreme.sum())
        metrics["new_adjusted_extreme_from_raw"] += int((adjusted_extreme & ~raw_extreme).sum())

        for idx in scoped.index[raw_extreme | adjusted_extreme]:
            day = pd.Timestamp(scoped.at[idx, "Date"]).normalize()
            key = f"{ticker}:{day:%Y-%m-%d}"
            has_action = (ticker, day) in event_factor
            if raw_extreme.at[idx]:
                raw_extreme_keys.add(key)
                metrics["raw_action_extreme_jumps"] += int(has_action)
            if adjusted_extreme.at[idx]:
                extreme_keys.add(key)
                metrics["adjusted_action_extreme_jumps"] += int(has_action)
                if bool(long_gap_reset.at[idx]):
                    adjusted_long_gap_extreme_keys.add(key)
                    metrics["adjusted_long_gap_extreme_jumps"] += 1
            prior_position = scoped.index.get_loc(idx) - 1
            prior_close = float(scoped.iloc[prior_position]["Close"]) if prior_position >= 0 else None
            extreme_rows.append(
                {
                    "key": key,
                    "ticker": ticker,
                    "date": day.strftime("%Y-%m-%d"),
                    "prior_close": prior_close,
                    "close": float(scoped.at[idx, "Close"]),
                    "raw_return": float(raw_return.at[idx]) if pd.notna(raw_return.at[idx]) else None,
                    "adjusted_return": float(adjusted_return.at[idx]) if pd.notna(adjusted_return.at[idx]) else None,
                    "has_official_action": has_action,
                    "price_factor": event_factor.get((ticker, day)),
                    "calendar_gap_days": int(gap_days.at[idx]) if pd.notna(gap_days.at[idx]) else None,
                    "technical_reset": bool(long_gap_reset.at[idx]),
                }
            )

    extreme_rows.sort(key=lambda row: abs(row.get("adjusted_return") or 0), reverse=True)
    metrics["adjusted_extreme_keys"] = sorted(extreme_keys)
    metrics["raw_extreme_keys"] = sorted(raw_extreme_keys)
    metrics["adjusted_long_gap_extreme_keys"] = sorted(adjusted_long_gap_extreme_keys)
    metrics["technical_effective_adjusted_extreme_jumps"] = (
        metrics["adjusted_extreme_jumps"] - metrics["adjusted_long_gap_extreme_jumps"]
    )
    metrics["extreme_rows"] = extreme_rows[:200]
    return metrics


def _technical_samples(source: Path, calendar: pd.DataFrame) -> list[dict[str, Any]]:
    samples = [
        ("0050", "2025-06-18"),
        ("8093", "2026-01-12"),
        ("2429", "2024-07-02"),
        ("3293", "2024-07-24"),
        ("3073", "2021-02-19"),
        ("8101", "2024-11-19"),
    ]
    output: list[dict[str, Any]] = []
    for ticker, event_date in samples:
        path = source / f"{ticker}.csv"
        if not path.exists():
            continue
        frame = _load_price_columns(path)
        event = pd.Timestamp(event_date)
        position = frame.index[frame["Date"].ge(event)]
        if position.empty:
            continue
        start = max(0, int(position[0]) - 90)
        end = min(len(frame), int(position[0]) + 30)
        window = frame.iloc[start:end].reset_index(drop=True)
        features = compute_technical_features(window, ticker=ticker, action_calendar=calendar)
        observed = pd.Timestamp(frame.loc[int(position[0]), "Date"])
        row_index = features.index[features["Date"].eq(observed)][0]
        previous_date = window["Date"].shift(1).iloc[row_index]
        output.append(
            {
                "ticker": ticker,
                "scheduled_date": event_date,
                "observed_date": observed.strftime("%Y-%m-%d"),
                "calendar_gap_days": (
                    int((observed - previous_date).days) if pd.notna(previous_date) else None
                ),
                "raw_return_1d": float(window["Close"].pct_change(fill_method=None).iloc[row_index]),
                "adjusted_return_1d": float(features.iloc[row_index]["return_1d"]),
                "ma20": float(features.iloc[row_index]["MA_20"]),
                "rsi14": float(features.iloc[row_index]["RSI_14"]),
                "atr14": float(features.iloc[row_index]["ATR_14"]),
            }
        )
    return output


def _known_breakpoint_checks(
    source: Path,
    backup: Path,
    calendar: pd.DataFrame,
    threshold: float,
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for ticker, event_date, mechanism in KNOWN_BREAKPOINTS:
        day = pd.Timestamp(event_date)
        source_frame = _load_price_columns(source / f"{ticker}.csv")
        backup_frame = _load_price_columns(backup / f"{ticker}.csv")

        backup_positions = backup_frame.index[backup_frame["Date"].eq(day)]
        backup_return = None
        if len(backup_positions) and int(backup_positions[0]) > 0:
            position = int(backup_positions[0])
            backup_return = (
                float(backup_frame.loc[position, "Close"])
                / float(backup_frame.loc[position - 1, "Close"])
                - 1.0
            )

        source_positions = source_frame.index[source_frame["Date"].eq(day)]
        if not len(source_positions):
            output.append(
                {
                    "ticker": ticker,
                    "date": event_date,
                    "mechanism": mechanism,
                    "backup_raw_return": backup_return,
                    "current_raw_return": None,
                    "current_adjusted_return": None,
                    "status": "removed_absent_official_regular_tape",
                }
            )
            continue

        position = int(source_positions[0])
        start = max(0, position - 90)
        window = source_frame.iloc[start:position + 1].reset_index(drop=True)
        features = compute_technical_features(window, ticker=ticker, action_calendar=calendar)
        current_raw_return = (
            float(window.iloc[-1]["Close"]) / float(window.iloc[-2]["Close"]) - 1.0
            if len(window) > 1
            else None
        )
        current_adjusted_return = features.iloc[-1]["return_1d"]
        current_adjusted_return = (
            float(current_adjusted_return) if pd.notna(current_adjusted_return) else None
        )
        resolved = (
            current_adjusted_return is not None
            and abs(current_adjusted_return) <= threshold
        )
        output.append(
            {
                "ticker": ticker,
                "date": event_date,
                "mechanism": mechanism,
                "backup_raw_return": backup_return,
                "current_raw_return": current_raw_return,
                "current_adjusted_return": current_adjusted_return,
                "status": "resolved" if resolved else "unresolved",
            }
        )
    return output


def _no_event_exact_check(source: Path, calendar: pd.DataFrame) -> dict[str, Any]:
    event_keys = set(zip(calendar["stock_id"].astype(str), pd.to_datetime(calendar["date"])))
    for path in sorted(source.glob("*.csv")):
        ticker = path.stem
        frame = _load_price_columns(path)
        frame = (
            frame.loc[frame["Date"].ge(pd.Timestamp("2020-01-01"))]
            .head(100)
            .reset_index(drop=True)
        )
        if len(frame) < 90:
            continue
        if any((ticker, day) in event_keys for day in frame["Date"]):
            continue
        legacy = compute_technical_features(frame)
        candidate = compute_technical_features(frame, ticker=ticker, action_calendar=calendar)
        try:
            pd.testing.assert_frame_equal(legacy, candidate, check_exact=True)
        except AssertionError as exc:
            return {"passed": False, "ticker": ticker, "error": str(exc)[:500]}
        return {"passed": True, "ticker": ticker, "rows": len(frame)}
    return {"passed": False, "error": "no eligible no-event sample found"}


def _repair_reports() -> list[dict[str, Any]]:
    reports: list[dict[str, Any]] = []
    for path in sorted((BASE_DIR / "ml" / "reports").glob("retrain03_official_ohlc_repair_20260717*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        reports.append(
            {
                "path": str(path),
                "mode": payload.get("mode"),
                "window": payload.get("window"),
                "backup_path": payload.get("backup_path"),
                "backup_verification": payload.get("backup_verification"),
                "official_stage": payload.get("official_stage"),
                "totals": payload.get("totals"),
                "coverage": payload.get("coverage"),
                "ticker_detail_count": len(payload.get("ticker_details") or []),
            }
        )
    return reports


def _format_metric(value: Any, spec: str, *, reset_label: str = "reset") -> str:
    if value is None:
        return reset_label
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return reset_label
    if not np.isfinite(numeric):
        return reset_label
    return format(numeric, spec)


def build_markdown(report: dict[str, Any]) -> str:
    current = report["current"]
    backup = report["backup"]
    lines = [
        "# RETRAIN-03 official OHLC and price-continuity audit",
        "",
        f"- Verdict: **{report['verdict']}**",
        f"- Source: `{current['root']}`",
        f"- Backup: `{backup['root']}`",
        f"- Corporate-action rows: {report['corporate_action_rows']:,}",
        f"- Scan start: `{report['scan_start']}`; extreme threshold: `{report['extreme_threshold']:.0%}`",
        "",
        "## Integrity",
        "",
        "| Check | Current | Backup |",
        "|---|---:|---:|",
        f"| CSV files | {current['files']:,} | {backup['files']:,} |",
        f"| Total rows | {current['rows']:,} | {backup['rows']:,} |",
        f"| Duplicate ticker/date | {current['duplicate_ticker_dates']:,} | {backup['duplicate_ticker_dates']:,} |",
        f"| OHLC invariant violations | {current['ohlc_invariant_violations']:,} | {backup['ohlc_invariant_violations']:,} |",
        f"| Non-positive OHLC rows (all history) | {current['nonpositive_ohlc_rows']:,} | {backup['nonpositive_ohlc_rows']:,} |",
        f"| Non-positive OHLC rows since scan start | {current['nonpositive_ohlc_rows_since_start']:,} | {backup['nonpositive_ohlc_rows_since_start']:,} |",
        f"| Raw >35% jumps | {current['raw_extreme_jumps']:,} | {backup['raw_extreme_jumps']:,} |",
        f"| Action-adjusted sequence >35% jumps (before suspension reset) | {current['adjusted_extreme_jumps']:,} | {backup['adjusted_extreme_jumps']:,} |",
        f"| Adjusted action-day >35% jumps | {current['adjusted_action_extreme_jumps']:,} | {backup['adjusted_action_extreme_jumps']:,} |",
        f"| Adjusted >35% jumps after >{MAX_TECHNICAL_GAP_DAYS}d suspension | {current['adjusted_long_gap_extreme_jumps']:,} | {backup['adjusted_long_gap_extreme_jumps']:,} |",
        f"| Technical-effective adjusted >35% jumps | {current['technical_effective_adjusted_extreme_jumps']:,} | {backup['technical_effective_adjusted_extreme_jumps']:,} |",
        "",
        "## Regression checks",
        "",
        f"- No-event feature frame exact: `{report['no_event_exact']}`",
        f"- New adjusted extremes versus backup: `{len(report['new_adjusted_extremes_vs_backup'])}`",
        f"- New unexplained adjusted extremes: `{len(report['new_unexplained_extremes_vs_backup'])}`",
        f"- Current action-day adjusted extremes: `{current['adjusted_action_extreme_jumps']}`",
        f"- Historical non-positive rows before scan start: "
        f"`{current['nonpositive_ohlc_rows'] - current['nonpositive_ohlc_rows_since_start']}` "
        "(reported for follow-up; outside this ticket's 2020+ repair scope)",
        "",
        "## Event samples",
        "",
        "| Ticker | Scheduled | Observed | Gap days | Raw 1D | Adjusted 1D | MA20 | RSI14 | ATR14 |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in report["technical_samples"]:
        lines.append(
            f"| {row['ticker']} | {row['scheduled_date']} | {row['observed_date']} | "
            f"{row['calendar_gap_days']} | {_format_metric(row['raw_return_1d'], '+.2%')} | "
            f"{_format_metric(row['adjusted_return_1d'], '+.2%')} | "
            f"{_format_metric(row['ma20'], '.2f')} | "
            f"{_format_metric(row['rsi14'], '.2f')} | "
            f"{_format_metric(row['atr14'], '.2f')} |"
        )
    lines.extend(
        [
            "",
            "## Ticket-named breakpoint closure",
            "",
            "| Ticker | Date | Mechanism | Backup raw | Current raw | Current adjusted | Status |",
            "|---|---|---|---:|---:|---:|---|",
        ]
    )
    for row in report["known_breakpoint_checks"]:
        lines.append(
            f"| {row['ticker']} | {row['date']} | {row['mechanism']} | "
            f"{_format_metric(row['backup_raw_return'], '+.2%', reset_label='-')} | "
            f"{_format_metric(row['current_raw_return'], '+.2%', reset_label='-')} | "
            f"{_format_metric(row['current_adjusted_return'], '+.2%', reset_label='-')} | "
            f"{row['status']} |"
        )
    lines.extend(["", "## Remaining adjusted extremes", ""])
    remaining = current["extreme_rows"][:30]
    if not remaining:
        lines.append("None.")
    else:
        lines.extend([
            "| Ticker | Date | Raw | Adjusted | Gap days | Reset | Official action | Factor |",
            "|---|---|---:|---:|---:|---|---|---:|",
        ])
        for row in remaining:
            if abs(row.get("adjusted_return") or 0) <= report["extreme_threshold"]:
                continue
            factor = row.get("price_factor")
            factor_text = f"{factor:.6f}" if factor is not None else "-"
            lines.append(
                f"| {row['ticker']} | {row['date']} | {row['raw_return']:+.2%} | "
                f"{row['adjusted_return']:+.2%} | {row['calendar_gap_days']} | "
                f"{row['technical_reset']} | {row['has_official_action']} | {factor_text} |"
            )
    lines.extend([
        "",
        "## Method",
        "",
        "- Raw OHLCV remains exchange-reported and unadjusted.",
        "- Training labels use official economic reference factors.",
        "- Technical indicators use official opening/starting-trade basis factors.",
        "- No model training, model selection, scheduler promotion, or protected artifact change was performed.",
        "",
    ])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--backup", type=Path, default=DEFAULT_BACKUP)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--start", default="2020-01-01")
    parser.add_argument("--threshold", type=float, default=0.35)
    args = parser.parse_args()

    if not args.source.is_dir() or not args.backup.is_dir():
        raise FileNotFoundError(f"source/backup missing: {args.source}, {args.backup}")
    calendar = load_price_continuity_calendar()
    calendar_groups, event_factor = _action_maps(calendar)
    current = scan_tree(
        args.source,
        calendar_groups=calendar_groups,
        event_factor=event_factor,
        threshold=args.threshold,
        start_date=pd.Timestamp(args.start),
    )
    backup = scan_tree(
        args.backup,
        calendar_groups=calendar_groups,
        event_factor=event_factor,
        threshold=args.threshold,
        start_date=pd.Timestamp(args.start),
    )
    new_extremes = sorted(
        set(current["adjusted_extreme_keys"]) - set(backup["adjusted_extreme_keys"])
    )
    new_unexplained_extremes = sorted(
        set(new_extremes) - set(current["adjusted_long_gap_extreme_keys"])
    )
    no_event = _no_event_exact_check(args.source, calendar)
    technical_samples = _technical_samples(args.source, calendar)
    known_breakpoints = _known_breakpoint_checks(
        args.source, args.backup, calendar, args.threshold
    )

    hard_failures = []
    if current["duplicate_ticker_dates"]:
        hard_failures.append("duplicate_ticker_dates")
    if current["ohlc_invariant_violations"]:
        hard_failures.append("ohlc_invariant_violations")
    if current["nonpositive_ohlc_rows_since_start"]:
        hard_failures.append("nonpositive_ohlc_rows_since_start")
    if current["adjusted_action_extreme_jumps"]:
        hard_failures.append("unresolved_action_day_extremes")
    if not no_event.get("passed"):
        hard_failures.append("no_event_regression")
    if new_unexplained_extremes:
        hard_failures.append("new_unexplained_adjusted_extremes")
    if any(row["status"] == "unresolved" for row in known_breakpoints):
        hard_failures.append("ticket_named_breakpoint_unresolved")

    report = {
        "ticket": "RETRAIN-03",
        "verdict": "PASS" if not hard_failures else "REVIEW_REQUIRED",
        "hard_failures": hard_failures,
        "scan_start": args.start,
        "extreme_threshold": args.threshold,
        "corporate_action_rows": len(calendar),
        "current": current,
        "backup": backup,
        "new_adjusted_extremes_vs_backup": new_extremes,
        "new_unexplained_extremes_vs_backup": new_unexplained_extremes,
        "no_event_exact": no_event,
        "technical_samples": technical_samples,
        "known_breakpoint_checks": known_breakpoints,
        "official_repair_reports": _repair_reports(),
        "retraining_started": False,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.with_suffix(".json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    args.report.with_suffix(".md").write_text(build_markdown(report), encoding="utf-8")
    print(json.dumps({
        "verdict": report["verdict"],
        "hard_failures": hard_failures,
        "current_raw_extremes": current["raw_extreme_jumps"],
        "current_adjusted_extremes": current["adjusted_extreme_jumps"],
        "current_adjusted_action_extremes": current["adjusted_action_extreme_jumps"],
        "new_extremes_vs_backup": len(new_extremes),
        "new_unexplained_extremes_vs_backup": len(new_unexplained_extremes),
        "nonpositive_since_start": current["nonpositive_ohlc_rows_since_start"],
        "report": str(args.report),
    }, ensure_ascii=False, indent=2))
    return 0 if not hard_failures else 2


if __name__ == "__main__":
    raise SystemExit(main())
