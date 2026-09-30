"""Daily research watch runner for V2 exit overlay reports.

This is intentionally research-only. It refreshes a stable latest report and
archives compact dated snapshots so the production dashboard and trading
pipeline remain untouched.
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
from argparse import Namespace
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))

from ml.config import REPORT_DIR
from scripts.v2_exit_overlay_backtest import (
    DEFAULT_FRICTION,
    DEFAULT_MAX_HOLD_DAYS,
    DEFAULT_TOP_N,
    run_backtest,
)

DEFAULT_START_DATE = "2026-03-11"
DEFAULT_OUTPUT_PREFIX = "v2_exit_overlay_watch_latest"
DEFAULT_ARCHIVE_PREFIX = "v2_exit_overlay_watch"
DEFAULT_RETENTION_DAYS = 60

COMPACT_SUFFIXES = [
    ".md",
    ".json",
    "_audit.md",
    "_audit.csv",
    "_summary.csv",
    "_signal_returns.csv",
    "_equity_curve.csv",
    "_by_rank.csv",
    "_by_sector.csv",
]
TRADE_SUFFIX = "_trades.csv"


def _parse_date(value: str | None) -> date:
    if value:
        return date.fromisoformat(value)
    return datetime.now().date()


def _copy_archive(
    *,
    report_dir: Path,
    output_prefix: str,
    archive_dir: Path,
    archive_prefix: str,
    archive_date: date,
    archive_trades: bool,
) -> tuple[list[Path], list[Path]]:
    archive_dir.mkdir(parents=True, exist_ok=True)
    suffixes = list(COMPACT_SUFFIXES)
    if archive_trades:
        suffixes.append(TRADE_SUFFIX)

    copied: list[Path] = []
    missing: list[Path] = []
    archive_stamp = archive_date.isoformat()
    for suffix in suffixes:
        source = report_dir / f"{output_prefix}{suffix}"
        target = archive_dir / f"{archive_prefix}_{archive_stamp}{suffix}"
        if not source.exists():
            missing.append(source)
            continue
        shutil.copy2(source, target)
        copied.append(target)
    return copied, missing


def _cleanup_old_archives(
    *,
    archive_dir: Path,
    archive_prefix: str,
    archive_date: date,
    retention_days: int,
) -> list[Path]:
    if retention_days <= 0 or not archive_dir.exists():
        return []

    cutoff = archive_date - timedelta(days=retention_days)
    pattern = re.compile(rf"^{re.escape(archive_prefix)}_(\d{{4}}-\d{{2}}-\d{{2}})")
    removed: list[Path] = []
    for path in archive_dir.glob(f"{archive_prefix}_*"):
        match = pattern.match(path.name)
        if not match:
            continue
        try:
            snapshot_date = date.fromisoformat(match.group(1))
        except ValueError:
            continue
        if snapshot_date < cutoff:
            path.unlink()
            removed.append(path)
    return removed


def _backtest_args(args: argparse.Namespace) -> Namespace:
    return Namespace(
        start_date=args.start_date,
        end_date=args.end_date,
        top_n=args.top_n,
        max_hold_days=args.max_hold_days,
        friction=args.friction,
        include_open_mtm=not args.closed_only,
        ambiguous_policy=args.ambiguous_policy,
        output_dir=args.output_dir,
        output_prefix=args.output_prefix,
    )


def run_watch(args: argparse.Namespace) -> dict[str, Any]:
    report_dir = Path(args.output_dir)
    archive_date = _parse_date(args.archive_date)

    metadata = run_backtest(_backtest_args(args))
    copied: list[Path] = []
    missing: list[Path] = []
    removed: list[Path] = []

    if not args.no_archive:
        copied, missing = _copy_archive(
            report_dir=report_dir,
            output_prefix=args.output_prefix,
            archive_dir=Path(args.archive_dir),
            archive_prefix=args.archive_prefix,
            archive_date=archive_date,
            archive_trades=args.archive_trades,
        )
        removed = _cleanup_old_archives(
            archive_dir=Path(args.archive_dir),
            archive_prefix=args.archive_prefix,
            archive_date=archive_date,
            retention_days=args.retention_days,
        )

    print("\nWatch outputs:")
    print(f"  latest report: {report_dir / (args.output_prefix + '.md')}")
    print(f"  latest audit:  {report_dir / (args.output_prefix + '_audit.md')}")
    print(f"  archive date:  {archive_date.isoformat()}")
    print(f"  archived:      {len(copied)} files")
    print(f"  missing:       {len(missing)} files")
    print(f"  removed old:   {len(removed)} files")

    return {
        "metadata": metadata,
        "archive_date": archive_date.isoformat(),
        "copied": [str(path) for path in copied],
        "missing": [str(path) for path in missing],
        "removed": [str(path) for path in removed],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Refresh daily V2 exit overlay research watch.")
    parser.add_argument("--start-date", default=DEFAULT_START_DATE)
    parser.add_argument("--end-date", default=None)
    parser.add_argument("--top-n", type=int, default=DEFAULT_TOP_N)
    parser.add_argument("--max-hold-days", type=int, default=DEFAULT_MAX_HOLD_DAYS)
    parser.add_argument("--friction", type=float, default=DEFAULT_FRICTION)
    parser.add_argument(
        "--ambiguous-policy",
        choices=["stop_first", "target_first", "close"],
        default="stop_first",
    )
    parser.add_argument("--closed-only", action="store_true", help="Exclude open mark-to-market trades.")
    parser.add_argument("--output-dir", default=REPORT_DIR)
    parser.add_argument("--output-prefix", default=DEFAULT_OUTPUT_PREFIX)
    parser.add_argument("--archive-dir", default=str(Path(REPORT_DIR) / "archive"))
    parser.add_argument("--archive-prefix", default=DEFAULT_ARCHIVE_PREFIX)
    parser.add_argument("--archive-date", default=None, help="Snapshot date YYYY-MM-DD. Default: today.")
    parser.add_argument("--archive-trades", action="store_true", help="Also archive the large trade detail CSV.")
    parser.add_argument("--no-archive", action="store_true")
    parser.add_argument("--retention-days", type=int, default=DEFAULT_RETENTION_DAYS)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    run_watch(args)


if __name__ == "__main__":
    main()
