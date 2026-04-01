"""Paper portfolio ledger for T+1 next-day momentum selections."""

from __future__ import annotations

import argparse
import glob
import hashlib
import logging
import os
import re
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime

import numpy as np
import pandas as pd

BASE_DIR = r"F:\stock"
sys.path.insert(0, BASE_DIR)

from ml.config import DAILY_K_DIR, MODEL_DIR

DEFAULT_DB_PATH = os.path.join(BASE_DIR, "paper_portfolio_t1.db")
DEFAULT_RULE_VERSION = "t1-v1"
DEFAULT_TOP_N = 10
PORTFOLIO_START_DATE = "2026-03-23"
HOLD_DAYS = 1

PREDICTION_FILE_RE = re.compile(r"^predictions_t1_\d{4}-\d{2}-\d{2}\.csv$")


@dataclass
class SyncStats:
    new_runs: int = 0
    skipped_runs: int = 0
    new_positions: int = 0
    refreshed_positions: int = 0


def connect_db(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS t1_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            prediction_date TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL,
            prediction_path TEXT NOT NULL,
            prediction_sha256 TEXT NOT NULL,
            rule_version TEXT NOT NULL,
            top_n INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS t1_positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL REFERENCES t1_runs(id) ON DELETE CASCADE,
            prediction_date TEXT NOT NULL,
            selection_rank INTEGER NOT NULL,
            ticker TEXT NOT NULL,
            sector TEXT,
            recommendation TEXT,
            setup_tags TEXT,
            risk_tags TEXT,
            selection_close REAL,
            hit_prob REAL,
            t1_score REAL,
            take_profit REAL,
            stop_loss REAL,
            entry_date TEXT,
            entry_open REAL,
            exit_close REAL,
            realized_return_pct REAL,
            intraday_high REAL,
            intraday_low REAL,
            max_intraday_gain_pct REAL,
            max_intraday_drawdown_pct REAL,
            hit_3pct INTEGER,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(run_id, selection_rank),
            UNIQUE(run_id, ticker)
        );

        CREATE INDEX IF NOT EXISTS idx_t1_positions_status
            ON t1_positions(status, prediction_date);
        """
    )
    conn.commit()


def _utc_now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _coerce_db_value(value):
    if value is None:
        return None
    if isinstance(value, np.generic):
        value = value.item()
    if pd.isna(value):
        return None
    return value


def _sha256(path: str) -> str:
    hasher = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _list_prediction_files() -> list[str]:
    candidates = glob.glob(os.path.join(MODEL_DIR, "predictions_t1_*.csv"))
    files = [
        path for path in candidates
        if PREDICTION_FILE_RE.match(os.path.basename(path))
    ]
    return sorted(files)


def _prediction_date_from_path(path: str) -> str:
    basename = os.path.basename(path)
    return basename.replace("predictions_t1_", "").replace(".csv", "")


def _resolve_prediction_paths(
    prediction_file: str | None,
    backfill_all: bool,
) -> list[str]:
    if prediction_file:
        return [os.path.abspath(prediction_file)]

    files = [
        path for path in _list_prediction_files()
        if _prediction_date_from_path(path) >= PORTFOLIO_START_DATE
    ]
    if not files:
        raise FileNotFoundError("No predictions_t1_*.csv files found.")

    if backfill_all:
        return files
    return [files[-1]]


def lock_prediction_run(
    conn: sqlite3.Connection,
    prediction_path: str,
    top_n: int,
    rule_version: str,
    stats: SyncStats,
) -> tuple[int, str, bool]:
    df = pd.read_csv(prediction_path, encoding="utf-8-sig", dtype={"ticker": str})
    prediction_date = str(df["date"].iloc[0])

    existing = conn.execute(
        "SELECT id FROM t1_runs WHERE prediction_date = ?",
        (prediction_date,),
    ).fetchone()
    if existing:
        stats.skipped_runs += 1
        print(f"[t1-paper] {prediction_date} already locked, skip.")
        return int(existing["id"]), prediction_date, False

    # Filter to selected_for_trade only
    if "selected_for_trade" in df.columns:
        selected = df[df["selected_for_trade"] == True].copy()
    else:
        selected = df.copy()
    selected = selected.head(top_n).reset_index(drop=True)
    if selected.empty:
        logging.getLogger(__name__).info("No selected trades in %s — skip (normal when market is weak)", os.path.basename(prediction_path))
        return None, prediction_date, False

    now = _utc_now_str()
    cursor = conn.execute(
        """
        INSERT INTO t1_runs (
            prediction_date, created_at, prediction_path,
            prediction_sha256, rule_version, top_n
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (prediction_date, now, os.path.abspath(prediction_path),
         _sha256(prediction_path), rule_version, int(top_n)),
    )
    run_id = int(cursor.lastrowid)
    stats.new_runs += 1

    for idx, row in selected.iterrows():
        rank = int(row.get("selection_rank", idx + 1))
        conn.execute(
            """
            INSERT INTO t1_positions (
                run_id, prediction_date, selection_rank, ticker,
                sector, recommendation, setup_tags, risk_tags,
                selection_close, hit_prob, t1_score,
                take_profit, stop_loss,
                status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id, prediction_date, rank,
                str(row.get("ticker", "")),
                _coerce_db_value(row.get("sector")),
                _coerce_db_value(row.get("recommendation")),
                _coerce_db_value(row.get("setup_tags")),
                _coerce_db_value(row.get("risk_tags")),
                _coerce_db_value(row.get("close")),
                _coerce_db_value(row.get("hit_prob_3pct")),
                _coerce_db_value(row.get("t1_score")),
                _coerce_db_value(row.get("take_profit")),
                _coerce_db_value(row.get("stop_loss")),
                "pending", now, now,
            ),
        )
        stats.new_positions += 1

    conn.commit()
    print(f"[t1-paper] Locked {len(selected)} names for {prediction_date}.")
    return run_id, prediction_date, True


def refresh_open_positions(conn: sqlite3.Connection, stats: SyncStats) -> None:
    """Mark T+1 positions using next-day price data (open→close, 1-day hold)."""
    cache: dict[str, pd.DataFrame | None] = {}
    positions = conn.execute(
        """
        SELECT id, ticker, prediction_date
        FROM t1_positions
        WHERE status IN ('pending', 'open')
          AND prediction_date >= ?
        ORDER BY prediction_date, selection_rank
        """,
        (PORTFOLIO_START_DATE,),
    ).fetchall()

    now = _utc_now_str()
    for pos in positions:
        ticker = str(pos["ticker"])
        position_id = int(pos["id"])
        pred_date = str(pos["prediction_date"])

        if ticker not in cache:
            csv_path = os.path.join(DAILY_K_DIR, f"{ticker}.csv")
            if os.path.exists(csv_path):
                try:
                    pdf = pd.read_csv(
                        csv_path, parse_dates=["Date"],
                        usecols=["Date", "Open", "High", "Low", "Close"],
                    ).sort_values("Date").reset_index(drop=True)
                    cache[ticker] = pdf
                except Exception:
                    cache[ticker] = None
            else:
                cache[ticker] = None

        price_df = cache[ticker]
        if price_df is None or price_df.empty:
            continue

        # Next trading day after prediction_date
        future = price_df[price_df["Date"] > pd.Timestamp(pred_date)].reset_index(drop=True)
        if future.empty:
            continue

        day1 = future.iloc[0]
        entry_open = float(day1["Open"])
        if not np.isfinite(entry_open) or entry_open <= 0:
            continue

        entry_date = day1["Date"].date().isoformat()
        exit_close = float(day1["Close"])
        intraday_high = float(day1["High"])
        intraday_low = float(day1["Low"])
        realized_return_pct = exit_close / entry_open - 1.0
        max_gain = intraday_high / entry_open - 1.0
        max_dd = intraday_low / entry_open - 1.0
        hit_3pct = int(max_gain >= 0.03)

        conn.execute(
            """
            UPDATE t1_positions
            SET entry_date = ?,
                entry_open = ?,
                exit_close = ?,
                realized_return_pct = ?,
                intraday_high = ?,
                intraday_low = ?,
                max_intraday_gain_pct = ?,
                max_intraday_drawdown_pct = ?,
                hit_3pct = ?,
                status = 'closed',
                updated_at = ?
            WHERE id = ?
            """,
            (
                entry_date, entry_open, exit_close,
                realized_return_pct, intraday_high, intraday_low,
                max_gain, max_dd, hit_3pct, now, position_id,
            ),
        )
        stats.refreshed_positions += 1

    conn.commit()


def summarize_ledger(conn: sqlite3.Connection) -> dict[str, object]:
    run_row = conn.execute(
        "SELECT COUNT(*) AS total FROM t1_runs WHERE prediction_date >= ?",
        (PORTFOLIO_START_DATE,),
    ).fetchone()
    pos_row = conn.execute(
        """
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) AS pending,
            SUM(CASE WHEN status = 'closed' THEN 1 ELSE 0 END) AS closed,
            SUM(CASE WHEN status = 'closed' AND hit_3pct = 1 THEN 1 ELSE 0 END) AS hit_count
        FROM t1_positions
        WHERE prediction_date >= ?
        """,
        (PORTFOLIO_START_DATE,),
    ).fetchone()
    closed_row = conn.execute(
        """
        SELECT
            AVG(realized_return_pct) AS avg_return,
            AVG(max_intraday_drawdown_pct) AS avg_dd,
            AVG(max_intraday_gain_pct) AS avg_gain
        FROM t1_positions
        WHERE status = 'closed' AND prediction_date >= ?
        """,
        (PORTFOLIO_START_DATE,),
    ).fetchone()
    total_closed = int(pos_row["closed"] or 0)
    hit_count = int(pos_row["hit_count"] or 0)
    return {
        "total_runs": int(run_row["total"] or 0),
        "total_positions": int(pos_row["total"] or 0),
        "pending_positions": int(pos_row["pending"] or 0),
        "closed_positions": total_closed,
        "hit_count": hit_count,
        "hit_rate": hit_count / total_closed if total_closed > 0 else None,
        "avg_realized_return_pct": _coerce_db_value(closed_row["avg_return"]),
        "avg_max_dd_pct": _coerce_db_value(closed_row["avg_dd"]),
        "avg_max_gain_pct": _coerce_db_value(closed_row["avg_gain"]),
    }


def sync_paper_portfolio_t1(
    prediction_file: str | None = None,
    backfill_all: bool = False,
    db_path: str = DEFAULT_DB_PATH,
    top_n: int = DEFAULT_TOP_N,
    rule_version: str = DEFAULT_RULE_VERSION,
) -> dict[str, object]:
    stats = SyncStats()
    prediction_paths = _resolve_prediction_paths(prediction_file, backfill_all)

    with connect_db(db_path) as conn:
        ensure_schema(conn)
        refresh_open_positions(conn, stats)
        for path in prediction_paths:
            lock_prediction_run(conn, path, top_n, rule_version, stats)
        # Refresh again for newly locked positions
        if stats.new_runs > 0:
            refresh_open_positions(conn, stats)
        summary = summarize_ledger(conn)

    result = {
        "db_path": os.path.abspath(db_path),
        "prediction_files": prediction_paths,
        "new_runs": stats.new_runs,
        "skipped_runs": stats.skipped_runs,
        "new_positions": stats.new_positions,
        "refreshed_positions": stats.refreshed_positions,
        "summary": summary,
    }
    return result


def main(argv: list[str] | None = None) -> dict[str, object]:
    parser = argparse.ArgumentParser(description="Sync the T+1 paper portfolio ledger.")
    parser.add_argument("--prediction-file", help="Specific predictions_t1_*.csv path.")
    parser.add_argument("--backfill-all", action="store_true")
    parser.add_argument("--db-path", default=DEFAULT_DB_PATH)
    parser.add_argument("--top", type=int, default=DEFAULT_TOP_N)
    args = parser.parse_args(argv)

    result = sync_paper_portfolio_t1(
        prediction_file=args.prediction_file,
        backfill_all=args.backfill_all,
        db_path=args.db_path,
        top_n=args.top,
    )
    summary = result["summary"]
    print(f"[t1-paper] Synced: runs={summary['total_runs']} "
          f"pending={summary['pending_positions']} closed={summary['closed_positions']} "
          f"hit={summary['hit_count']}/{summary['closed_positions']} "
          f"avg_return={summary['avg_realized_return_pct']}")
    return result


if __name__ == "__main__":
    main()
