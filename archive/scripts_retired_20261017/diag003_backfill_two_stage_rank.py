"""DIAG-003: backfill two_stage_rank_at_entry and explicit rank status."""

from __future__ import annotations

import argparse
import sys
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ml.config import REPORT_DIR
from scripts.update_unified_portfolio import (
    DEFAULT_DB_PATH,
    TWO_STAGE_RANK_STATUS_KNOWN,
    TWO_STAGE_RANK_STATUS_UNKNOWN,
    connect_db,
    ensure_schema,
)


def _safe_float(value: Any) -> float | None:
    try:
        number = float(value)
    except Exception:
        return None
    if not np.isfinite(number):
        return None
    return number


def _load_signal_lookup(signal_file: Path) -> dict[str, dict[str, Any]]:
    df = pd.read_csv(signal_file, encoding="utf-8-sig", dtype={"ticker": str})
    lookup: dict[str, dict[str, Any]] = {}
    for row in df.to_dict("records"):
        ticker = str(row.get("ticker", "")).strip()
        lookup[ticker] = row
    return lookup


def _fetch_positions(conn: sqlite3.Connection, prediction_date: str) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT id, ticker, prediction_date, status, two_stage_rank_at_entry,
               two_stage_rank_status, price_vs_ma20_at_entry
        FROM unified_positions
        WHERE prediction_date = ?
        ORDER BY ticker
        """,
        (prediction_date,),
    ).fetchall()


def run(args: argparse.Namespace) -> dict[str, str]:
    signal_file = Path(args.signal_file)
    if not signal_file.exists():
        raise FileNotFoundError(signal_file)
    signal_lookup = _load_signal_lookup(signal_file)
    report_rows: list[dict[str, Any]] = []

    with connect_db(args.db_path) as conn:
        ensure_schema(conn)
        before_rows = _fetch_positions(conn, args.prediction_date)
        for position in before_rows:
            ticker = str(position["ticker"])
            signal = signal_lookup.get(ticker, {})
            signal_rank = _safe_float(signal.get("two_stage_rank"))
            signal_price_vs_ma20 = _safe_float(signal.get("price_vs_ma20_20d"))
            before_rank = _safe_float(position["two_stage_rank_at_entry"])
            before_status = str(position["two_stage_rank_status"] or "")
            new_rank = before_rank if before_rank is not None else signal_rank
            new_status = (
                TWO_STAGE_RANK_STATUS_KNOWN
                if new_rank is not None
                else TWO_STAGE_RANK_STATUS_UNKNOWN
            )
            new_price_vs_ma20 = (
                _safe_float(position["price_vs_ma20_at_entry"])
                if _safe_float(position["price_vs_ma20_at_entry"]) is not None
                else signal_price_vs_ma20
            )
            conn.execute(
                """
                UPDATE unified_positions
                SET two_stage_rank_at_entry = ?,
                    two_stage_rank_status = ?,
                    price_vs_ma20_at_entry = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    new_rank,
                    new_status,
                    new_price_vs_ma20,
                    datetime.now(UTC).isoformat(),
                    int(position["id"]),
                ),
            )
            report_rows.append(
                {
                    "ticker": ticker,
                    "status": position["status"],
                    "before_rank": before_rank,
                    "signal_rank": signal_rank,
                    "after_rank": new_rank,
                    "before_status": before_status,
                    "after_status": new_status,
                    "price_vs_ma20_at_entry": new_price_vs_ma20,
                    "strong_trend_candidate": bool(
                        new_status == TWO_STAGE_RANK_STATUS_KNOWN
                        and new_rank is not None
                        and new_rank <= 10
                        and new_price_vs_ma20 is not None
                        and new_price_vs_ma20 > 0.05
                    ),
                }
            )
        conn.commit()

    report = pd.DataFrame(report_rows)
    report_dir = Path(REPORT_DIR)
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = args.output_date or datetime.now().strftime("%Y%m%d")
    csv_path = report_dir / f"diag003_two_stage_rank_backfill_{stamp}.csv"
    md_path = report_dir / f"diag003_two_stage_rank_backfill_{stamp}.md"
    report.to_csv(csv_path, index=False, encoding="utf-8-sig")
    lines = [
        "# DIAG-003 Two-Stage Rank Backfill",
        "",
        f"- Generated at: `{datetime.now().isoformat(timespec='seconds')}`",
        f"- DB: `{args.db_path}`",
        f"- Prediction date: `{args.prediction_date}`",
        f"- Signal file: `{signal_file}`",
        f"- Positions scanned: `{len(report)}`",
        f"- Known after backfill: `{int((report['after_status'] == TWO_STAGE_RANK_STATUS_KNOWN).sum()) if not report.empty else 0}`",
        f"- Rank unknown after backfill: `{int((report['after_status'] == TWO_STAGE_RANK_STATUS_UNKNOWN).sum()) if not report.empty else 0}`",
        f"- Strong-trend no-MA5 candidates: `{int(report['strong_trend_candidate'].sum()) if not report.empty else 0}`",
        "",
        "## Rows",
        "",
        "| Ticker | Status | Before rank | Signal rank | After rank | After status | price_vs_ma20 | Strong trend candidate |",
        "|---|---|---:|---:|---:|---|---:|:---:|",
    ]
    for row in report.to_dict("records"):
        lines.append(
            f"| {row['ticker']} | {row['status']} | {row['before_rank']} | {row['signal_rank']} | "
            f"{row['after_rank']} | {row['after_status']} | {row['price_vs_ma20_at_entry']} | "
            f"{'YES' if row['strong_trend_candidate'] else 'NO'} |"
        )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(report.to_string(index=False))
    print(f"[diag003] wrote {md_path}")
    return {"csv": str(csv_path), "md": str(md_path)}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", default=DEFAULT_DB_PATH)
    parser.add_argument("--prediction-date", default="2026-05-08")
    parser.add_argument(
        "--signal-file",
        default="ml/models/unified_signals_2026-05-08.csv",
        help="Use the current promoted signal by default. Pass archive/nightly path to audit the historical artifact.",
    )
    parser.add_argument("--output-date", default=None)
    return parser.parse_args()


if __name__ == "__main__":
    run(parse_args())
