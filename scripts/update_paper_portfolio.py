"""Paper portfolio ledger for daily Top 30 selections."""

from __future__ import annotations

import argparse
import glob
import hashlib
import os
import re
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime

import numpy as np
import pandas as pd

BASE_DIR = os.environ.get("STOCK_BASE_DIR") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from ml.config import DAILY_K_DIR, MODEL_DIR
from ml.predict import apply_sector_cap, _sort_prediction_df
from ml.universe import filter_out_etfs_df
from scripts.order_simulation import (
    EXIT_REASON_TIME_20D,
    FILL_STATUS_FILLED,
    FILL_STATUS_PENDING,
    simulate_20d_entry,
    simulate_intraday_stop,
)
from scripts.exit_policies import ENTRY_TAG_FUNDAMENTAL_DRIVEN, classify_entry_tag

DEFAULT_DB_PATH = os.path.join(BASE_DIR, "paper_portfolio.db")
NARRATIVE_DB_PATH = os.path.join(BASE_DIR, "stock.duckdb")
DEFAULT_RULE_VERSION = "v2.4-chip-momentum-entry-filter"
DEFAULT_TOP_N = 30
PORTFOLIO_START_DATE = "2026-03-18"
HOLD_DAYS = 20
FRICTION = 0.004  # round-trip friction, same as backtest
EARLY_CRASH_DAYS = 5
CRASH_DRAWDOWN_THRESHOLD = -0.10
ENABLE_STOP_LOSS = True

PREDICTION_FILE_RE = re.compile(r"^predictions_\d{4}-\d{2}-\d{2}\.csv$")
PREDICTION_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})(?=\.csv$)")


@dataclass
class SyncStats:
    new_runs: int = 0
    skipped_runs: int = 0
    new_positions: int = 0
    refreshed_positions: int = 0
    marks_upserted: int = 0


def connect_db(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _ensure_column(conn: sqlite3.Connection, table_name: str, column_name: str, column_ddl: str) -> None:
    existing = {
        str(row["name"])
        for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    }
    if column_name not in existing:
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_ddl}")


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS portfolio_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            prediction_date TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL,
            prediction_path TEXT NOT NULL,
            prediction_sha256 TEXT NOT NULL,
            rule_version TEXT NOT NULL,
            top_n INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS portfolio_positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL REFERENCES portfolio_runs(id) ON DELETE CASCADE,
            prediction_date TEXT NOT NULL,
            selection_rank INTEGER NOT NULL,
            ticker TEXT NOT NULL,
            stock_name TEXT,
            sector TEXT,
            recommendation TEXT,
            signal TEXT,
            selection_close REAL,
            pred_return_20d REAL,
            up_prob REAL,
            flat_prob REAL,
            down_prob REAL,
            prob_edge REAL,
            risk_adjusted_return REAL,
            price_vs_ma20 REAL,
            inst_net_10d REAL,
            risk_tags TEXT,
            entry_date TEXT,
            entry_open REAL,
            days_observed INTEGER NOT NULL DEFAULT 0,
            last_mark_date TEXT,
            exit_date TEXT,
            exit_close REAL,
            realized_return_pct REAL,
            max_drawdown_pct REAL,
            max_drawdown_date TEXT,
            max_drawdown_low REAL,
            breached_10pct_drawdown INTEGER NOT NULL DEFAULT 0,
            breached_10pct_first5d INTEGER NOT NULL DEFAULT 0,
            first_10pct_breach_date TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            hold_days_target INTEGER NOT NULL DEFAULT 20,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(run_id, selection_rank),
            UNIQUE(run_id, ticker)
        );

        CREATE TABLE IF NOT EXISTS portfolio_marks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            position_id INTEGER NOT NULL REFERENCES portfolio_positions(id) ON DELETE CASCADE,
            mark_date TEXT NOT NULL,
            trading_day_number INTEGER NOT NULL,
            open REAL NOT NULL,
            high REAL NOT NULL,
            low REAL NOT NULL,
            close REAL NOT NULL,
            close_return_pct REAL,
            intraday_drawdown_pct REAL,
            is_exit_day INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            UNIQUE(position_id, mark_date)
        );

        CREATE INDEX IF NOT EXISTS idx_portfolio_positions_status
            ON portfolio_positions(status, prediction_date);
        CREATE INDEX IF NOT EXISTS idx_portfolio_positions_ticker
            ON portfolio_positions(ticker, prediction_date);
        CREATE INDEX IF NOT EXISTS idx_portfolio_marks_position
            ON portfolio_marks(position_id, mark_date);

        CREATE VIEW IF NOT EXISTS portfolio_latest_run AS
        SELECT
            p.*,
            r.rule_version,
            r.prediction_path,
            r.prediction_sha256
        FROM portfolio_positions p
        JOIN portfolio_runs r ON r.id = p.run_id
        WHERE p.prediction_date = (
            SELECT MAX(prediction_date) FROM portfolio_runs
        );
        """
    )
    for column_name, column_ddl in [
        ("order_type", "TEXT"),
        ("fill_status", "TEXT"),
        ("fill_price", "REAL"),
        ("entry_slippage_pct", "REAL"),
        ("exit_reason", "TEXT"),
        ("entry_tag", f"TEXT NOT NULL DEFAULT '{ENTRY_TAG_FUNDAMENTAL_DRIVEN}'"),
        ("narrative_heat_at_entry", "REAL"),
    ]:
        _ensure_column(conn, "portfolio_positions", column_name, column_ddl)
    conn.commit()


def _load_narrative_features_for_date(date_str: str) -> dict[str, dict[str, object]]:
    if not os.path.exists(NARRATIVE_DB_PATH):
        return {}
    try:
        import duckdb

        con = duckdb.connect(NARRATIVE_DB_PATH, read_only=True)
        try:
            df = con.execute(
                """
                SELECT ticker, active_labels, heat_3d
                FROM daily_ticker_narrative_features
                WHERE date = ?
                """,
                [date_str],
            ).fetchdf()
        finally:
            con.close()
    except Exception:
        return {}
    if df.empty:
        return {}
    df["ticker"] = df["ticker"].astype(str)
    return {str(row["ticker"]): row for row in df.to_dict("records")}


def _entry_tag_for_prediction(
    narrative_by_ticker: dict[str, dict[str, object]],
    ticker: str,
) -> tuple[str, float | None]:
    row = narrative_by_ticker.get(str(ticker), {})
    return classify_entry_tag(row.get("active_labels"), row.get("heat_3d"))


def backfill_narrative_entry_tags(conn: sqlite3.Connection) -> int:
    rows = conn.execute(
        """
        SELECT id, ticker, prediction_date
        FROM portfolio_positions
        WHERE prediction_date >= ?
          AND narrative_heat_at_entry IS NULL
        ORDER BY prediction_date, ticker
        """,
        (PORTFOLIO_START_DATE,),
    ).fetchall()
    if not rows:
        return 0

    cache: dict[str, dict[str, dict[str, object]]] = {}
    updated = 0
    for row in rows:
        pred_date = str(row["prediction_date"])
        if pred_date not in cache:
            cache[pred_date] = _load_narrative_features_for_date(pred_date)
        entry_tag, heat = _entry_tag_for_prediction(cache[pred_date], str(row["ticker"]))
        conn.execute(
            """
            UPDATE portfolio_positions
            SET entry_tag = ?,
                narrative_heat_at_entry = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (entry_tag, heat, _utc_now_str(), int(row["id"])),
        )
        updated += 1
    conn.commit()
    return updated


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
    candidates = glob.glob(os.path.join(MODEL_DIR, "predictions_*.csv"))
    files = [
        path for path in candidates
        if PREDICTION_FILE_RE.match(os.path.basename(path))
    ]
    return sorted(files)


def _prediction_date_from_path(path: str) -> str:
    basename = os.path.basename(path)
    match = PREDICTION_DATE_RE.search(basename)
    if not match:
        raise ValueError(f"Cannot infer prediction date from filename: {basename}")
    return match.group(1)


def _is_supported_prediction_date(prediction_date: str) -> bool:
    return prediction_date >= PORTFOLIO_START_DATE


def _resolve_prediction_paths(
    prediction_file: str | None,
    backfill_all: bool,
) -> list[str]:
    if prediction_file:
        resolved = os.path.abspath(prediction_file)
        prediction_date = _prediction_date_from_path(resolved)
        if not _is_supported_prediction_date(prediction_date):
            raise ValueError(
                f"Paper portfolio starts at {PORTFOLIO_START_DATE}; "
                f"{prediction_date} is older."
            )
        return [resolved]

    files = [
        path
        for path in _list_prediction_files()
        if _is_supported_prediction_date(_prediction_date_from_path(path))
    ]
    if not files:
        raise FileNotFoundError("No predictions_YYYY-MM-DD.csv files were found.")

    if backfill_all:
        return files
    return [files[-1]]


def _load_prediction_file(prediction_path: str) -> pd.DataFrame:
    df = pd.read_csv(prediction_path, encoding="utf-8-sig", dtype={"ticker": str})
    required_cols = {"ticker", "date", "close"}
    missing = sorted(required_cols - set(df.columns))
    if missing:
        raise ValueError(
            f"{os.path.basename(prediction_path)} is missing columns: {', '.join(missing)}"
        )

    if "up_prob" in df.columns and "down_prob" in df.columns and "prob_edge" not in df.columns:
        df["prob_edge"] = df["up_prob"] - df["down_prob"]

    if (
        "pred_return_20d" in df.columns
        and "prob_edge" in df.columns
        and "risk_adjusted_return" not in df.columns
    ):
        df["risk_adjusted_return"] = (
            pd.to_numeric(df["pred_return_20d"], errors="coerce")
            * np.maximum(pd.to_numeric(df["prob_edge"], errors="coerce"), 0.0)
        )

    return df


def _build_leaderboard(pred_df: pd.DataFrame, top_n: int) -> pd.DataFrame:
    pred_df = filter_out_etfs_df(pred_df)
    if {"pred_return_20d", "recommendation"} <= set(pred_df.columns):
        leaderboard = apply_sector_cap(pred_df, top_n=top_n)
    else:
        leaderboard = _sort_prediction_df(pred_df).head(top_n).copy()

    leaderboard = leaderboard.reset_index(drop=True).copy()
    leaderboard["selection_rank"] = np.arange(1, len(leaderboard) + 1)
    return leaderboard


def lock_prediction_run(
    conn: sqlite3.Connection,
    prediction_path: str,
    top_n: int,
    rule_version: str,
    stats: SyncStats,
) -> tuple[int, str, bool]:
    pred_df = _load_prediction_file(prediction_path)
    prediction_date = str(pred_df["date"].iloc[0])
    existing = conn.execute(
        "SELECT id FROM portfolio_runs WHERE prediction_date = ?",
        (prediction_date,),
    ).fetchone()
    if existing:
        stats.skipped_runs += 1
        print(f"[paper] {prediction_date} already locked, skip snapshot.")
        return int(existing["id"]), prediction_date, False

    leaderboard = _build_leaderboard(pred_df, top_n=top_n)

    now = _utc_now_str()
    cursor = conn.execute(
        """
        INSERT INTO portfolio_runs (
            prediction_date,
            created_at,
            prediction_path,
            prediction_sha256,
            rule_version,
            top_n
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            prediction_date,
            now,
            os.path.abspath(prediction_path),
            _sha256(prediction_path),
            rule_version,
            int(top_n),
        ),
    )
    run_id = int(cursor.lastrowid)
    stats.new_runs += 1
    narrative_at_entry = _load_narrative_features_for_date(prediction_date)

    for row in leaderboard.to_dict("records"):
        entry_tag, narrative_heat_at_entry = _entry_tag_for_prediction(
            narrative_at_entry,
            str(row.get("ticker", "")),
        )
        conn.execute(
            """
            INSERT INTO portfolio_positions (
                run_id,
                prediction_date,
                selection_rank,
                ticker,
                stock_name,
                sector,
                recommendation,
                signal,
                selection_close,
                pred_return_20d,
                up_prob,
                flat_prob,
                down_prob,
                prob_edge,
                risk_adjusted_return,
                price_vs_ma20,
                inst_net_10d,
                risk_tags,
                entry_tag,
                narrative_heat_at_entry,
                fill_status,
                status,
                hold_days_target,
                created_at,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                prediction_date,
                int(row["selection_rank"]),
                str(row.get("ticker", "")),
                _coerce_db_value(row.get("name")),
                _coerce_db_value(row.get("sector")),
                _coerce_db_value(row.get("recommendation")),
                _coerce_db_value(row.get("signal")),
                _coerce_db_value(row.get("close")),
                _coerce_db_value(row.get("pred_return_20d")),
                _coerce_db_value(row.get("up_prob")),
                _coerce_db_value(row.get("flat_prob")),
                _coerce_db_value(row.get("down_prob")),
                _coerce_db_value(row.get("prob_edge")),
                _coerce_db_value(row.get("risk_adjusted_return")),
                _coerce_db_value(row.get("price_vs_ma20")),
                _coerce_db_value(row.get("inst_net_10d")),
                _coerce_db_value(row.get("risk_tags")),
                entry_tag,
                narrative_heat_at_entry,
                FILL_STATUS_PENDING,
                "pending",
                HOLD_DAYS,
                now,
                now,
            ),
        )
        stats.new_positions += 1

    conn.commit()
    print(f"[paper] Locked {len(leaderboard)} names for {prediction_date}.")
    return run_id, prediction_date, True


def _load_price_history(
    ticker: str,
    cache: dict[str, pd.DataFrame],
) -> pd.DataFrame | None:
    if ticker in cache:
        return cache[ticker]

    csv_path = os.path.join(DAILY_K_DIR, f"{ticker}.csv")
    if not os.path.exists(csv_path):
        cache[ticker] = None
        return None

    try:
        df = pd.read_csv(
            csv_path,
            parse_dates=["Date"],
            usecols=["Date", "Open", "High", "Low", "Close"],
        )
    except Exception:
        cache[ticker] = None
        return None

    df = df.sort_values("Date").reset_index(drop=True)
    cache[ticker] = df
    return df


def _upsert_mark(
    conn: sqlite3.Connection,
    position_id: int,
    mark_date: str,
    trading_day_number: int,
    open_price: float,
    high_price: float,
    low_price: float,
    close_price: float,
    close_return_pct: float,
    intraday_drawdown_pct: float,
    is_exit_day: int,
) -> None:
    conn.execute(
        """
        INSERT INTO portfolio_marks (
            position_id,
            mark_date,
            trading_day_number,
            open,
            high,
            low,
            close,
            close_return_pct,
            intraday_drawdown_pct,
            is_exit_day,
            created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(position_id, mark_date) DO UPDATE SET
            trading_day_number = excluded.trading_day_number,
            open = excluded.open,
            high = excluded.high,
            low = excluded.low,
            close = excluded.close,
            close_return_pct = excluded.close_return_pct,
            intraday_drawdown_pct = excluded.intraday_drawdown_pct,
            is_exit_day = excluded.is_exit_day
        """,
        (
            position_id,
            mark_date,
            trading_day_number,
            open_price,
            high_price,
            low_price,
            close_price,
            close_return_pct,
            intraday_drawdown_pct,
            is_exit_day,
            _utc_now_str(),
        ),
    )


def refresh_open_positions(
    conn: sqlite3.Connection,
    stats: SyncStats,
    hold_days: int = HOLD_DAYS,
    prediction_dates: set[str] | None = None,
) -> None:
    cache: dict[str, pd.DataFrame | None] = {}
    query = """
        SELECT id, ticker, prediction_date, hold_days_target, selection_close
        FROM portfolio_positions
        WHERE status IN ('pending', 'open')
          AND prediction_date >= ?
    """
    params: list[str] = [PORTFOLIO_START_DATE]
    if prediction_dates:
        filtered_dates = sorted(
            date for date in prediction_dates if _is_supported_prediction_date(date)
        )
        if not filtered_dates:
            return
        placeholders = ", ".join("?" for _ in filtered_dates)
        query += f" AND prediction_date IN ({placeholders})"
        params.extend(filtered_dates)
    query += " ORDER BY prediction_date, selection_rank"
    positions = conn.execute(query, params).fetchall()

    now = _utc_now_str()
    for position in positions:
        ticker = str(position["ticker"])
        position_id = int(position["id"])
        selection_date = str(position["prediction_date"])
        hold_days_target = int(position["hold_days_target"] or hold_days)
        price_df = _load_price_history(ticker, cache)
        if price_df is None or price_df.empty:
            continue

        future = price_df[price_df["Date"] > pd.Timestamp(selection_date)].reset_index(drop=True)
        if future.empty:
            conn.execute(
                """
                UPDATE portfolio_positions
                SET updated_at = ?, status = 'pending'
                WHERE id = ?
                """,
                (now, position_id),
            )
            continue

        window = future.head(hold_days_target).copy()
        entry_row = window.iloc[0]
        entry_fill = simulate_20d_entry(
            reference_price=position["selection_close"],
            open_price=entry_row["Open"],
            high_price=entry_row["High"],
        )
        if entry_fill.fill_status != FILL_STATUS_FILLED or entry_fill.fill_price is None:
            continue
        entry_cost = float(entry_fill.fill_price)

        breached_10pct_first5d = 0
        first_10pct_breach_date = None
        min_drawdown = float("inf")
        min_drawdown_date = None
        min_drawdown_low = None
        stopped_out = False        # 強制停損旗標
        stop_day_number = None
        stop_exit_price = None
        exit_reason = None

        for day_number, mark_row in enumerate(window.itertuples(index=False), start=1):
            mark_date = mark_row.Date.date().isoformat()
            low_price = float(mark_row.Low)
            close_price = float(mark_row.Close)
            intraday_drawdown_pct = low_price / entry_cost - 1.0
            close_return_pct = close_price / entry_cost - 1.0
            if intraday_drawdown_pct < min_drawdown:
                min_drawdown = intraday_drawdown_pct
                min_drawdown_date = mark_date
                min_drawdown_low = low_price

            if intraday_drawdown_pct <= CRASH_DRAWDOWN_THRESHOLD and first_10pct_breach_date is None:
                first_10pct_breach_date = mark_date
            if day_number <= EARLY_CRASH_DAYS and intraday_drawdown_pct <= CRASH_DRAWDOWN_THRESHOLD:
                breached_10pct_first5d = 1

            stop_event = (
                simulate_intraday_stop(entry_cost, mark_row.Open, mark_row.Low)
                if ENABLE_STOP_LOSS
                else None
            )
            if stop_event is not None:
                stopped_out = True
                stop_day_number = day_number
                stop_exit_price = stop_event.exit_price
                exit_reason = stop_event.exit_reason

            is_exit_day = int(
                stopped_out
                or (day_number == hold_days_target and len(window) >= hold_days_target)
            )
            _upsert_mark(
                conn=conn,
                position_id=position_id,
                mark_date=mark_date,
                trading_day_number=day_number,
                open_price=float(mark_row.Open),
                high_price=float(mark_row.High),
                low_price=low_price,
                close_price=close_price,
                close_return_pct=close_return_pct,
                intraday_drawdown_pct=intraday_drawdown_pct,
                is_exit_day=is_exit_day,
            )
            stats.marks_upserted += 1

            # 停損觸發後不再繼續記錄後續交易日
            if stopped_out:
                break

        days_observed = int(stop_day_number if stopped_out else len(window))
        if stopped_out:
            status = "stopped_out"
        elif days_observed >= hold_days_target:
            status = "closed"
        else:
            status = "open"
        exit_date = None
        exit_close = None
        realized_return_pct = None
        if status == "stopped_out":
            stop_row = window.iloc[stop_day_number - 1]
            exit_date = stop_row["Date"].date().isoformat()
            exit_close = float(stop_exit_price)
            realized_return_pct = exit_close / entry_cost - 1.0 - FRICTION
        elif status == "closed":
            exit_row = window.iloc[hold_days_target - 1]
            exit_date = exit_row["Date"].date().isoformat()
            exit_close = float(exit_row["Close"])
            realized_return_pct = exit_close / entry_cost - 1.0 - FRICTION
            exit_reason = EXIT_REASON_TIME_20D

        conn.execute(
            """
            UPDATE portfolio_positions
            SET entry_date = ?,
                entry_open = ?,
                order_type = ?,
                fill_status = ?,
                fill_price = ?,
                entry_slippage_pct = ?,
                days_observed = ?,
                last_mark_date = ?,
                exit_date = ?,
                exit_close = ?,
                realized_return_pct = ?,
                max_drawdown_pct = ?,
                max_drawdown_date = ?,
                max_drawdown_low = ?,
                breached_10pct_drawdown = ?,
                breached_10pct_first5d = ?,
                first_10pct_breach_date = ?,
                exit_reason = ?,
                status = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                entry_row["Date"].date().isoformat(),
                entry_cost,
                entry_fill.order_type,
                FILL_STATUS_FILLED,
                entry_cost,
                entry_fill.entry_slippage_pct,
                days_observed,
                window.iloc[min(days_observed - 1, len(window) - 1)]["Date"].date().isoformat(),
                exit_date,
                exit_close,
                realized_return_pct,
                min_drawdown if np.isfinite(min_drawdown) else None,
                min_drawdown_date,
                min_drawdown_low,
                int(min_drawdown <= CRASH_DRAWDOWN_THRESHOLD),
                breached_10pct_first5d,
                first_10pct_breach_date,
                exit_reason,
                status,
                now,
                position_id,
            ),
        )
        stats.refreshed_positions += 1

    conn.commit()


def summarize_ledger(conn: sqlite3.Connection) -> dict[str, object]:
    run_row = conn.execute(
        "SELECT COUNT(*) AS total_runs FROM portfolio_runs WHERE prediction_date >= ?",
        (PORTFOLIO_START_DATE,),
    ).fetchone()
    position_row = conn.execute(
        """
        SELECT
            COUNT(*) AS total_positions,
            SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) AS pending_positions,
            SUM(CASE WHEN status = 'open' THEN 1 ELSE 0 END) AS open_positions,
            SUM(CASE WHEN status = 'closed' THEN 1 ELSE 0 END) AS closed_positions,
            SUM(CASE WHEN status = 'stopped_out' THEN 1 ELSE 0 END) AS stopped_out_positions
        FROM portfolio_positions
        WHERE prediction_date >= ?
        """
        ,
        (PORTFOLIO_START_DATE,),
    ).fetchone()
    mark_row = conn.execute(
        """
        SELECT COUNT(*) AS total_marks
        FROM portfolio_marks m
        JOIN portfolio_positions p ON p.id = m.position_id
        WHERE p.prediction_date >= ?
        """,
        (PORTFOLIO_START_DATE,),
    ).fetchone()
    observed_row = conn.execute(
        """
        SELECT
            COUNT(*) AS observed_positions,
            AVG(max_drawdown_pct) AS avg_max_drawdown_pct,
            SUM(CASE WHEN breached_10pct_drawdown = 1 THEN 1 ELSE 0 END) AS breach_10pct_count,
            SUM(CASE WHEN breached_10pct_first5d = 1 THEN 1 ELSE 0 END) AS breach_10pct_first5d_count
        FROM portfolio_positions
        WHERE days_observed > 0
          AND prediction_date >= ?
        """
        ,
        (PORTFOLIO_START_DATE,),
    ).fetchone()
    closed_row = conn.execute(
        """
        SELECT
            AVG(realized_return_pct) AS avg_realized_return_pct,
            AVG(max_drawdown_pct) AS avg_closed_max_drawdown_pct
        FROM portfolio_positions
        WHERE status IN ('closed', 'stopped_out')
          AND prediction_date >= ?
        """
        ,
        (PORTFOLIO_START_DATE,),
    ).fetchone()
    stopped_row = conn.execute(
        """
        SELECT
            AVG(realized_return_pct) AS avg_stopped_return_pct,
            COUNT(*) AS stopped_count
        FROM portfolio_positions
        WHERE status = 'stopped_out'
          AND prediction_date >= ?
        """,
        (PORTFOLIO_START_DATE,),
    ).fetchone()
    return {
        "total_runs": int(run_row["total_runs"] or 0),
        "total_positions": int(position_row["total_positions"] or 0),
        "pending_positions": int(position_row["pending_positions"] or 0),
        "open_positions": int(position_row["open_positions"] or 0),
        "closed_positions": int(position_row["closed_positions"] or 0),
        "stopped_out_positions": int(position_row["stopped_out_positions"] or 0),
        "total_marks": int(mark_row["total_marks"] or 0),
        "observed_positions": int(observed_row["observed_positions"] or 0),
        "avg_max_drawdown_pct": _coerce_db_value(observed_row["avg_max_drawdown_pct"]),
        "breach_10pct_count": int(observed_row["breach_10pct_count"] or 0),
        "breach_10pct_first5d_count": int(observed_row["breach_10pct_first5d_count"] or 0),
        "avg_realized_return_pct": _coerce_db_value(closed_row["avg_realized_return_pct"]),
        "avg_closed_max_drawdown_pct": _coerce_db_value(closed_row["avg_closed_max_drawdown_pct"]),
        "avg_stopped_return_pct": _coerce_db_value(stopped_row["avg_stopped_return_pct"]),
        "stopped_out_count": int(stopped_row["stopped_count"] or 0),
    }


def sync_paper_portfolio(
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
        backfill_narrative_entry_tags(conn)
        if not backfill_all:
            refresh_open_positions(conn, stats)
        new_prediction_dates: set[str] = set()
        for path in prediction_paths:
            _, prediction_date, inserted = lock_prediction_run(
                conn=conn,
                prediction_path=path,
                top_n=top_n,
                rule_version=rule_version,
                stats=stats,
            )
            if inserted:
                new_prediction_dates.add(prediction_date)
        if backfill_all:
            refresh_open_positions(conn, stats)
        elif new_prediction_dates:
            refresh_open_positions(conn, stats, prediction_dates=new_prediction_dates)
        summary = summarize_ledger(conn)

    result = {
        "db_path": os.path.abspath(db_path),
        "prediction_files": prediction_paths,
        "new_runs": stats.new_runs,
        "skipped_runs": stats.skipped_runs,
        "new_positions": stats.new_positions,
        "refreshed_positions": stats.refreshed_positions,
        "marks_upserted": stats.marks_upserted,
        "summary": summary,
    }
    return result


def _print_result(result: dict[str, object]) -> None:
    summary = result["summary"]
    print("[paper] Ledger synced:")
    print(f"  db_path: {result['db_path']}")
    print(
        "  new_runs/skipped_runs/new_positions: "
        f"{result['new_runs']}/{result['skipped_runs']}/{result['new_positions']}"
    )
    print(
        "  refreshed_positions/marks_upserted: "
        f"{result['refreshed_positions']}/{result['marks_upserted']}"
    )
    print(
        "  runs/positions/marks: "
        f"{summary['total_runs']}/{summary['total_positions']}/{summary['total_marks']}"
    )
    print(
        "  pending/open/closed/stopped_out: "
        f"{summary['pending_positions']}/{summary['open_positions']}"
        f"/{summary['closed_positions']}/{summary['stopped_out_positions']}"
    )
    print(
        "  observed_breaches(10pct/all, first5d): "
        f"{summary['breach_10pct_count']}/{summary['breach_10pct_first5d_count']}"
    )
    avg_max_dd = summary["avg_max_drawdown_pct"]
    if avg_max_dd is not None:
        print(f"  avg_observed_max_drawdown: {avg_max_dd:+.2%}")
    avg_realized = summary["avg_realized_return_pct"]
    if avg_realized is not None:
        print(f"  avg_closed_return: {avg_realized:+.2%}")
    stopped_count = summary.get("stopped_out_count", 0)
    if stopped_count > 0:
        avg_stopped = summary.get("avg_stopped_return_pct")
        print(f"  stopped_out: {stopped_count} positions, avg_return: {avg_stopped:+.2%}" if avg_stopped is not None else f"  stopped_out: {stopped_count} positions")


def main(argv: list[str] | None = None) -> dict[str, object]:
    parser = argparse.ArgumentParser(description="Sync the paper portfolio ledger.")
    parser.add_argument(
        "--prediction-file",
        help="Path to a specific predictions_YYYY-MM-DD.csv file.",
    )
    parser.add_argument(
        "--backfill-all",
        action="store_true",
        help="Lock every predictions_YYYY-MM-DD.csv file under ml/models.",
    )
    parser.add_argument(
        "--db-path",
        default=DEFAULT_DB_PATH,
        help="SQLite database path. Default: %(default)s",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=DEFAULT_TOP_N,
        help="Top N names to lock. Default: %(default)s",
    )
    args = parser.parse_args(argv)

    result = sync_paper_portfolio(
        prediction_file=args.prediction_file,
        backfill_all=args.backfill_all,
        db_path=args.db_path,
        top_n=args.top,
    )
    _print_result(result)
    return result


if __name__ == "__main__":
    main()
