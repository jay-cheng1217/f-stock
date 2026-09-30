"""Unified portfolio ledger for combined 20D / T+1 daily signals."""

from __future__ import annotations

import argparse
import glob
import os
import re
import sqlite3
import sys
from dataclasses import dataclass

import numpy as np
import pandas as pd

BASE_DIR = os.environ.get("STOCK_BASE_DIR") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from ml.config import DAILY_K_DIR, MODEL_DIR
from scripts.order_simulation import (
    EXIT_REASON_MISSED_ENTRY_GAP_UP,
    EXIT_REASON_TAKE_PROFIT_MA20,
    EXIT_REASON_TIME_20D,
    EXIT_REASON_TIME_T1,
    FILL_STATUS_FILLED,
    FILL_STATUS_MISSED,
    FILL_STATUS_PENDING,
    POSITION_STATUS_MISSED_ENTRY_GAP_UP,
    simulate_20d_entry,
    simulate_intraday_stop,
    simulate_t1_limit_entry,
)
from scripts.exit_policies import (
    ENTRY_TAG_FUNDAMENTAL_DRIVEN,
    EXIT_POLICY_ASYMMETRIC_V2,
    EXIT_POLICY_MA5_BREAK_PLUS_HWM_8PCT,
    EXIT_REASON_NARRATIVE_DECAY,
    ExitManager,
    classify_entry_tag,
    is_asymmetric_v2_strong_trend,
    is_narrative_decay_exit,
    resolve_exit_policy_name,
    SUPPORTED_EXIT_POLICIES,
)
from scripts.update_paper_portfolio import _coerce_db_value, _sha256, _utc_now_str

DEFAULT_DB_PATH = os.path.join(BASE_DIR, "paper_portfolio_v2_champion.db")
NARRATIVE_DB_PATH = os.path.join(BASE_DIR, "stock.duckdb")
DEFAULT_RULE_VERSION = "unified-v2-order-sim"
PORTFOLIO_START_DATE = "2026-04-09"

SIGNAL_FILE_RE = re.compile(r"^unified_signals_(\d{4}-\d{2}-\d{2})\.csv$")
SIGNAL_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})(?=\.csv$)")

EXIT_POLICY_T1 = "T1"
EXIT_POLICY_20D = "20D"
EXIT_POLICY_DUAL = "20D"

T1_HOLD_DAYS = 1
TWENTY_DAY_HOLD = 20
CRASH_DRAWDOWN_THRESHOLD = -0.10
EARLY_CRASH_DAYS = 5
MA20_TAKE_PROFIT_THRESHOLD = 0.18
TWO_STAGE_RANK_STATUS_KNOWN = "known"
TWO_STAGE_RANK_STATUS_UNKNOWN = "rank_unknown"
ENTRY_SNAPSHOT_CONFIDENCE_EXACT = "exact_snapshot"
ENTRY_SNAPSHOT_CONFIDENCE_BACKFILL = "backfill_signal_row"
ENTRY_SNAPSHOT_CONFIDENCE_PARTIAL_BACKFILL = "partial_backfill"
ENTRY_SNAPSHOT_CONFIDENCE_MISSING_FILE = "missing_signal_file"
ENTRY_SNAPSHOT_CONFIDENCE_MISSING_ROW = "missing_signal_row"
ENTRY_SNAPSHOT_REASON_OK = "OK"
ENTRY_SNAPSHOT_REASON_BACKFILLED = "BACKFILLED_FROM_SIGNAL_FILE"
ENTRY_SNAPSHOT_REASON_MISSING_FILE = "MISSING_SIGNAL_FILE"
ENTRY_SNAPSHOT_REASON_MISSING_ROW = "MISSING_SIGNAL_ROW"
ENTRY_SNAPSHOT_REASON_SHA_MISMATCH = "SIGNAL_SHA_MISMATCH"

ENTRY_SNAPSHOT_COLUMNS: tuple[tuple[str, str], ...] = (
    ("entry_snapshot_id", "TEXT"),
    ("entry_model_version", "TEXT"),
    ("entry_rank_20d", "REAL"),
    ("entry_two_stage_rank", "REAL"),
    ("entry_score", "REAL"),
    ("entry_prob_edge", "REAL"),
    ("entry_risk_adjusted_return_pre_penalty", "REAL"),
    ("entry_leaderboard_score_pre_penalty", "REAL"),
    ("entry_penalty_overlay_total", "REAL"),
    ("entry_guardrail_version", "TEXT"),
    ("entry_allocation_version", "TEXT"),
    ("entry_execution_policy_version", "TEXT"),
    ("entry_snapshot_confidence", "TEXT"),
    ("entry_snapshot_reason_code", "TEXT"),
)


@dataclass
class SyncStats:
    new_runs: int = 0
    skipped_runs: int = 0
    new_positions: int = 0
    upgraded_positions: int = 0
    refreshed_positions: int = 0
    marks_upserted: int = 0
    cash_deployed: float = 0.0


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
        CREATE TABLE IF NOT EXISTS unified_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            prediction_date TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL,
            signals_file_path TEXT NOT NULL,
            signals_sha256 TEXT NOT NULL,
            rule_version TEXT NOT NULL,
            total_candidates INTEGER NOT NULL,
            total_units INTEGER NOT NULL,
            available_cash_before REAL NOT NULL DEFAULT 0.0,
            cash_deployed REAL NOT NULL DEFAULT 0.0,
            new_positions INTEGER NOT NULL DEFAULT 0,
            upgraded_positions INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS unified_positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            opened_by_run_id INTEGER NOT NULL REFERENCES unified_runs(id) ON DELETE CASCADE,
            last_run_id INTEGER NOT NULL REFERENCES unified_runs(id) ON DELETE CASCADE,
            prediction_date TEXT NOT NULL,
            last_signal_date TEXT NOT NULL,
            ticker TEXT NOT NULL,
            sector TEXT,
            entry_signal_type TEXT NOT NULL,
            current_signal_type TEXT NOT NULL,
            target_units INTEGER NOT NULL,
            target_weight REAL NOT NULL,
            planned_entry_ref_price REAL,
            entry_date TEXT,
            entry_price REAL,
            exit_policy TEXT NOT NULL,
            hold_days_target INTEGER NOT NULL,
            upgrade_count INTEGER NOT NULL DEFAULT 0,
            upgraded_at TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            days_observed INTEGER NOT NULL DEFAULT 0,
            last_mark_date TEXT,
            exit_date TEXT,
            exit_price REAL,
            realized_return_pct REAL,
            max_drawdown_pct REAL,
            max_drawdown_date TEXT,
            max_drawdown_low REAL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS unified_marks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            position_id INTEGER NOT NULL REFERENCES unified_positions(id) ON DELETE CASCADE,
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

        CREATE INDEX IF NOT EXISTS idx_unified_positions_status
            ON unified_positions(status, prediction_date);
        CREATE INDEX IF NOT EXISTS idx_unified_positions_ticker
            ON unified_positions(ticker, status);
        CREATE INDEX IF NOT EXISTS idx_unified_marks_position
            ON unified_marks(position_id, mark_date);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_unified_open_ticker
            ON unified_positions(ticker)
            WHERE status IN ('pending', 'open');
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
        ("two_stage_rank_at_entry", "REAL"),
        ("two_stage_rank_status", f"TEXT NOT NULL DEFAULT '{TWO_STAGE_RANK_STATUS_UNKNOWN}'"),
        ("price_vs_ma20_at_entry", "REAL"),
        *ENTRY_SNAPSHOT_COLUMNS,
    ]:
        _ensure_column(conn, "unified_positions", column_name, column_ddl)
    conn.execute(
        """
        UPDATE unified_positions
        SET two_stage_rank_status = CASE
            WHEN two_stage_rank_at_entry IS NOT NULL THEN ?
            ELSE ?
        END
        WHERE two_stage_rank_status IS NULL
           OR two_stage_rank_status = ''
           OR two_stage_rank_status = 'unknown'
           OR two_stage_rank_status = ?
        """,
        (
            TWO_STAGE_RANK_STATUS_KNOWN,
            TWO_STAGE_RANK_STATUS_UNKNOWN,
            TWO_STAGE_RANK_STATUS_UNKNOWN,
        ),
    )
    conn.commit()


def _empty_narrative_row() -> dict[str, object]:
    return {
        "active_labels": None,
        "heat_3d": None,
        "decay_slope": None,
        "top_label": None,
    }


def _load_narrative_features_for_date(date_str: str) -> dict[str, dict[str, object]]:
    """Load shadow narrative features for one date from DuckDB.

    Missing tables/files intentionally fail closed to FUNDAMENTAL_DRIVEN.
    """

    if not os.path.exists(NARRATIVE_DB_PATH):
        return {}
    try:
        import duckdb

        con = duckdb.connect(NARRATIVE_DB_PATH, read_only=True)
        try:
            df = con.execute(
                """
                SELECT ticker, active_labels, heat_3d, decay_slope, top_label
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


def _get_narrative_feature(
    cache: dict[str, dict[str, dict[str, object]]],
    date_str: str,
    ticker: str,
) -> dict[str, object]:
    if date_str not in cache:
        cache[date_str] = _load_narrative_features_for_date(date_str)
    return cache[date_str].get(str(ticker), _empty_narrative_row())


def _entry_tag_for_signal(
    narrative_by_ticker: dict[str, dict[str, object]],
    ticker: str,
) -> tuple[str, float | None]:
    row = narrative_by_ticker.get(str(ticker), _empty_narrative_row())
    return classify_entry_tag(row.get("active_labels"), row.get("heat_3d"))


def _first_numeric(row: dict[str, object] | sqlite3.Row, *keys: str) -> float | None:
    for key in keys:
        try:
            value = row[key]
        except (IndexError, KeyError):
            value = None
        numeric = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
        if pd.notna(numeric) and np.isfinite(float(numeric)):
            return float(numeric)
    return None


def _first_text(row: dict[str, object] | sqlite3.Row, *keys: str) -> str | None:
    for key in keys:
        try:
            value = row[key]
        except (IndexError, KeyError):
            value = None
        if value is None:
            continue
        if isinstance(value, float) and pd.isna(value):
            continue
        text = str(value).strip()
        if text and text.lower() not in {"nan", "none", "null"}:
            return text
    return None


def _basename_text(value: str | None) -> str | None:
    if not value:
        return None
    return os.path.basename(str(value).replace("\\", os.sep))


def _entry_snapshot_id(prediction_date: str, signals_sha256: str | None, ticker: str) -> str:
    snapshot_key = signals_sha256 or "missing-signal-sha"
    return f"{prediction_date}:{snapshot_key}:{ticker}"


def _entry_model_version(row: dict[str, object] | sqlite3.Row) -> str | None:
    parts: list[str] = []
    env_stage1 = _basename_text(os.environ.get("V2_MODEL_META"))
    env_stage2 = _basename_text(os.environ.get("TWO_STAGE_RANKER_META"))
    if env_stage1:
        parts.append(f"stage1_meta={env_stage1}")
    if env_stage2:
        parts.append(f"stage2_meta={env_stage2}")

    base_model_file = _basename_text(_first_text(row, "base_model_file"))
    two_stage_model_file = _basename_text(_first_text(row, "two_stage_model_file"))
    base_model_trained_at = _first_text(row, "base_model_trained_at")
    if base_model_file:
        parts.append(f"base_model={base_model_file}")
    if base_model_trained_at:
        parts.append(f"base_trained_at={base_model_trained_at}")
    if two_stage_model_file:
        parts.append(f"stage2_model={two_stage_model_file}")

    source_20d = _basename_text(_first_text(row, "source_20d_file"))
    if source_20d:
        parts.append(f"source_20d={source_20d}")
    return "|".join(dict.fromkeys(parts)) if parts else None


def _entry_guardrail_version(row: dict[str, object] | sqlite3.Row) -> str:
    monitor_top_n = _first_text(row, "guardrail_monitor_top_n", "guardrail_monitor_top_n_20d")
    loose_mode = _first_text(row, "guardrail_loose_mode", "guardrail_loose_mode_20d")
    intercept_rate = _first_text(
        row,
        "guardrail_intercept_rate_top50",
        "guardrail_intercept_rate_top50_20d",
    )
    pieces = ["guardrail_v1"]
    if monitor_top_n:
        pieces.append(f"monitor_top_n={monitor_top_n}")
    if loose_mode:
        pieces.append(f"loose_mode={loose_mode}")
    if intercept_rate:
        pieces.append(f"intercept_rate_top50={intercept_rate}")
    return "|".join(pieces)


def _entry_allocation_version(row: dict[str, object] | sqlite3.Row) -> str:
    from ml.thresholds import GROUP_CAP_RATIO, SECTOR_CAP_RATIO

    group_cap_applied = _first_text(row, "group_cap_applied", "group_cap_applied_20d")
    return (
        f"sector_cap={SECTOR_CAP_RATIO:.2f}|"
        f"group_cap={GROUP_CAP_RATIO:.2f}|"
        f"group_cap_applied={group_cap_applied or 'unknown'}"
    )


def _entry_execution_policy_version(exit_policy: str, order_type: object | None = None) -> str:
    order_text = str(order_type).strip() if order_type is not None and not pd.isna(order_type) else "pending_fill"
    return f"rule={DEFAULT_RULE_VERSION}|exit_policy={exit_policy}|order_type={order_text}"


def _missing_snapshot_reasons(snapshot: dict[str, object]) -> list[str]:
    required_reason_fields = {
        "entry_snapshot_id": "MISSING_SNAPSHOT_ID",
        "entry_model_version": "MISSING_MODEL_VERSION",
        "entry_rank_20d": "MISSING_RANK_20D",
        "entry_two_stage_rank": "MISSING_TWO_STAGE_RANK",
        "entry_score": "MISSING_ENTRY_SCORE",
        "entry_prob_edge": "MISSING_PROB_EDGE",
        "entry_risk_adjusted_return_pre_penalty": "MISSING_RISK_ADJUSTED_PRE_PENALTY",
        "entry_leaderboard_score_pre_penalty": "MISSING_LEADERBOARD_PRE_PENALTY",
        "entry_penalty_overlay_total": "MISSING_PENALTY_OVERLAY_TOTAL",
        "entry_guardrail_version": "MISSING_GUARDRAIL_VERSION",
        "entry_allocation_version": "MISSING_ALLOCATION_VERSION",
        "entry_execution_policy_version": "MISSING_EXECUTION_POLICY_VERSION",
    }
    reasons: list[str] = []
    for field_name, reason in required_reason_fields.items():
        value = snapshot.get(field_name)
        if value is None:
            reasons.append(reason)
            continue
        if isinstance(value, float) and (pd.isna(value) or not np.isfinite(value)):
            reasons.append(reason)
        elif isinstance(value, str) and not value.strip():
            reasons.append(reason)
    return reasons


def _entry_snapshot_from_signal(
    row: dict[str, object] | sqlite3.Row,
    *,
    prediction_date: str,
    signals_sha256: str | None,
    exit_policy: str,
    confidence: str,
    base_reason_codes: list[str] | None = None,
) -> dict[str, object]:
    ticker = str(row["ticker"])
    snapshot: dict[str, object] = {
        "entry_snapshot_id": _entry_snapshot_id(prediction_date, signals_sha256, ticker),
        "entry_model_version": _entry_model_version(row),
        "entry_rank_20d": _first_numeric(row, "rank_20d"),
        "entry_two_stage_rank": _first_numeric(row, "two_stage_rank", "two_stage_rank_20d"),
        "entry_score": _first_numeric(row, "pred_return_20d", "entry_score", "leaderboard_score"),
        "entry_prob_edge": _first_numeric(row, "prob_edge"),
        "entry_risk_adjusted_return_pre_penalty": _first_numeric(
            row,
            "risk_adjusted_return_pre_penalty",
            "risk_adjusted_return",
        ),
        "entry_leaderboard_score_pre_penalty": _first_numeric(row, "leaderboard_score_pre_penalty"),
        "entry_penalty_overlay_total": _first_numeric(row, "penalty_overlay_total"),
        "entry_guardrail_version": _entry_guardrail_version(row),
        "entry_allocation_version": _entry_allocation_version(row),
        "entry_execution_policy_version": _entry_execution_policy_version(exit_policy, row.get("order_type") if isinstance(row, dict) else None),
        "entry_snapshot_confidence": confidence,
    }
    reason_codes = list(base_reason_codes or [])
    reason_codes.extend(_missing_snapshot_reasons(snapshot))
    if not reason_codes:
        reason_codes = [ENTRY_SNAPSHOT_REASON_OK]
    snapshot["entry_snapshot_reason_code"] = ";".join(dict.fromkeys(reason_codes))
    return snapshot


def _entry_snapshot_missing(
    *,
    prediction_date: str,
    signals_sha256: str | None,
    ticker: str,
    exit_policy: str,
    confidence: str,
    reason_code: str,
) -> dict[str, object]:
    snapshot = {
        "entry_snapshot_id": _entry_snapshot_id(prediction_date, signals_sha256, ticker),
        "entry_model_version": None,
        "entry_rank_20d": None,
        "entry_two_stage_rank": None,
        "entry_score": None,
        "entry_prob_edge": None,
        "entry_risk_adjusted_return_pre_penalty": None,
        "entry_leaderboard_score_pre_penalty": None,
        "entry_penalty_overlay_total": None,
        "entry_guardrail_version": None,
        "entry_allocation_version": None,
        "entry_execution_policy_version": _entry_execution_policy_version(exit_policy),
        "entry_snapshot_confidence": confidence,
    }
    reasons = [reason_code]
    reasons.extend(_missing_snapshot_reasons(snapshot))
    snapshot["entry_snapshot_reason_code"] = ";".join(dict.fromkeys(reasons))
    return snapshot


def _snapshot_update_assignments() -> str:
    return ",\n                ".join(f"{column} = ?" for column, _ in ENTRY_SNAPSHOT_COLUMNS)


def _snapshot_values(snapshot: dict[str, object]) -> list[object]:
    return [_coerce_db_value(snapshot.get(column)) for column, _ in ENTRY_SNAPSHOT_COLUMNS]


def _two_stage_rank_status(rank: object) -> str:
    return (
        TWO_STAGE_RANK_STATUS_KNOWN
        if _first_numeric({"rank": rank}, "rank") is not None
        else TWO_STAGE_RANK_STATUS_UNKNOWN
    )


def _rank_is_known(row: sqlite3.Row | dict[str, object]) -> bool:
    try:
        status = str(row["two_stage_rank_status"] or "")
    except (IndexError, KeyError):
        status = ""
    try:
        rank = row["two_stage_rank_at_entry"]
    except (IndexError, KeyError):
        rank = None
    if status == TWO_STAGE_RANK_STATUS_KNOWN:
        return _two_stage_rank_status(rank) == TWO_STAGE_RANK_STATUS_KNOWN
    if status == TWO_STAGE_RANK_STATUS_UNKNOWN:
        return False
    return _two_stage_rank_status(rank) == TWO_STAGE_RANK_STATUS_KNOWN


def backfill_narrative_entry_tags(
    conn: sqlite3.Connection,
    portfolio_start_date: str = PORTFOLIO_START_DATE,
) -> int:
    """Backfill entry tags for positions created before narrative columns existed."""

    rows = conn.execute(
        """
        SELECT id, ticker, prediction_date
        FROM unified_positions
        WHERE prediction_date >= ?
          AND narrative_heat_at_entry IS NULL
        ORDER BY prediction_date, ticker
        """,
        (portfolio_start_date,),
    ).fetchall()
    if not rows:
        return 0

    cache: dict[str, dict[str, dict[str, object]]] = {}
    updated = 0
    for row in rows:
        pred_date = str(row["prediction_date"])
        if pred_date not in cache:
            cache[pred_date] = _load_narrative_features_for_date(pred_date)
        entry_tag, heat = _entry_tag_for_signal(cache[pred_date], str(row["ticker"]))
        conn.execute(
            """
            UPDATE unified_positions
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


def backfill_entry_snapshots(
    conn: sqlite3.Connection,
    portfolio_start_date: str = PORTFOLIO_START_DATE,
) -> dict[str, int]:
    """Backfill immutable entry snapshots for existing positions.

    Historical rows are deliberately marked as backfill confidence, even when
    their source signal row is found. Only positions opened by the current sync
    path get exact_snapshot at insert time.
    """

    rows = conn.execute(
        """
        SELECT
            p.id,
            p.ticker,
            p.prediction_date,
            p.exit_policy,
            r.signals_file_path,
            r.signals_sha256
        FROM unified_positions p
        LEFT JOIN unified_runs r ON r.id = p.opened_by_run_id
        WHERE p.prediction_date >= ?
          AND (
              p.entry_snapshot_confidence IS NULL
              OR p.entry_snapshot_confidence = ''
              OR p.entry_snapshot_id IS NULL
              OR p.entry_snapshot_reason_code IS NULL
          )
        ORDER BY p.prediction_date, p.ticker
        """,
        (portfolio_start_date,),
    ).fetchall()
    if not rows:
        return {"updated": 0, "missing_file": 0, "missing_row": 0, "partial": 0}

    signal_cache: dict[str, tuple[pd.DataFrame | None, str | None]] = {}
    updated = 0
    missing_file = 0
    missing_row = 0
    partial = 0
    now = _utc_now_str()

    for position in rows:
        prediction_date = str(position["prediction_date"])
        ticker = str(position["ticker"])
        exit_policy = str(position["exit_policy"] or EXIT_POLICY_20D)
        signal_path = _first_text(position, "signals_file_path")
        if not signal_path:
            candidate = os.path.join(MODEL_DIR, f"unified_signals_{prediction_date}.csv")
            signal_path = candidate if os.path.exists(candidate) else None

        signals_sha256 = _first_text(position, "signals_sha256")
        if not signal_path or not os.path.exists(signal_path):
            snapshot = _entry_snapshot_missing(
                prediction_date=prediction_date,
                signals_sha256=signals_sha256,
                ticker=ticker,
                exit_policy=exit_policy,
                confidence=ENTRY_SNAPSHOT_CONFIDENCE_MISSING_FILE,
                reason_code=ENTRY_SNAPSHOT_REASON_MISSING_FILE,
            )
            missing_file += 1
        else:
            signal_path = os.path.abspath(signal_path)
            if signal_path not in signal_cache:
                try:
                    signal_cache[signal_path] = (_sort_signal_df(_load_signal_file(signal_path)), _sha256(signal_path))
                except Exception:
                    signal_cache[signal_path] = (None, None)
            signal_df, actual_sha256 = signal_cache[signal_path]
            if signal_df is None:
                snapshot = _entry_snapshot_missing(
                    prediction_date=prediction_date,
                    signals_sha256=signals_sha256,
                    ticker=ticker,
                    exit_policy=exit_policy,
                    confidence=ENTRY_SNAPSHOT_CONFIDENCE_MISSING_FILE,
                    reason_code=ENTRY_SNAPSHOT_REASON_MISSING_FILE,
                )
                missing_file += 1
            else:
                match = signal_df[signal_df["ticker"].astype(str) == ticker]
                if match.empty:
                    snapshot = _entry_snapshot_missing(
                        prediction_date=prediction_date,
                        signals_sha256=signals_sha256 or actual_sha256,
                        ticker=ticker,
                        exit_policy=exit_policy,
                        confidence=ENTRY_SNAPSHOT_CONFIDENCE_MISSING_ROW,
                        reason_code=ENTRY_SNAPSHOT_REASON_MISSING_ROW,
                    )
                    missing_row += 1
                else:
                    reason_codes = [ENTRY_SNAPSHOT_REASON_BACKFILLED]
                    if signals_sha256 and actual_sha256 and signals_sha256 != actual_sha256:
                        reason_codes.append(ENTRY_SNAPSHOT_REASON_SHA_MISMATCH)
                    snapshot = _entry_snapshot_from_signal(
                        match.iloc[0].to_dict(),
                        prediction_date=prediction_date,
                        signals_sha256=signals_sha256 or actual_sha256,
                        exit_policy=exit_policy,
                        confidence=ENTRY_SNAPSHOT_CONFIDENCE_BACKFILL,
                        base_reason_codes=reason_codes,
                    )
                    if any(code.startswith("MISSING_") for code in str(snapshot["entry_snapshot_reason_code"]).split(";")):
                        snapshot["entry_snapshot_confidence"] = ENTRY_SNAPSHOT_CONFIDENCE_PARTIAL_BACKFILL
                        partial += 1

        conn.execute(
            f"""
            UPDATE unified_positions
            SET {_snapshot_update_assignments()},
                updated_at = ?
            WHERE id = ?
            """,
            (*_snapshot_values(snapshot), now, int(position["id"])),
        )
        updated += 1

    conn.commit()
    return {
        "updated": updated,
        "missing_file": missing_file,
        "missing_row": missing_row,
        "partial": partial,
    }


def _list_signal_files() -> list[str]:
    candidates = glob.glob(os.path.join(MODEL_DIR, "unified_signals_*.csv"))
    return sorted(path for path in candidates if SIGNAL_FILE_RE.match(os.path.basename(path)))


def _signal_date_from_path(path: str) -> str:
    match = SIGNAL_DATE_RE.search(os.path.basename(path))
    if not match:
        raise ValueError(f"Cannot infer signal date from filename: {os.path.basename(path)}")
    return match.group(1)


def _is_supported_prediction_date(
    prediction_date: str,
    portfolio_start_date: str = PORTFOLIO_START_DATE,
) -> bool:
    return prediction_date >= portfolio_start_date


def _resolve_signal_paths(
    signal_files: list[str] | None,
    dates: list[str] | None,
    backfill_all: bool,
    portfolio_start_date: str = PORTFOLIO_START_DATE,
) -> list[str]:
    if signal_files:
        resolved = [os.path.abspath(path) for path in signal_files]
        for path in resolved:
            pred_date = _signal_date_from_path(path)
            if not _is_supported_prediction_date(pred_date, portfolio_start_date):
                raise ValueError(
                    f"Unified portfolio starts at {portfolio_start_date}; {pred_date} is older."
                )
        return resolved

    if dates:
        resolved = []
        for pred_date in dates:
            if not _is_supported_prediction_date(pred_date, portfolio_start_date):
                raise ValueError(
                    f"Unified portfolio starts at {portfolio_start_date}; {pred_date} is older."
                )
            path = os.path.join(MODEL_DIR, f"unified_signals_{pred_date}.csv")
            if not os.path.exists(path):
                raise FileNotFoundError(f"Unified signal file not found: {path}")
            resolved.append(path)
        return resolved

    files = [
        path
        for path in _list_signal_files()
        if _is_supported_prediction_date(_signal_date_from_path(path), portfolio_start_date)
    ]
    if not files:
        raise FileNotFoundError("No unified_signals_YYYY-MM-DD.csv files were found.")
    if backfill_all:
        return files
    return [files[-1]]


def _load_signal_file(signal_path: str) -> pd.DataFrame:
    df = pd.read_csv(signal_path, encoding="utf-8-sig", dtype={"ticker": str})
    required_cols = {
        "prediction_date",
        "ticker",
        "signal_type",
        "target_units",
        "target_weight_ratio",
        "close_ref",
    }
    missing = sorted(required_cols - set(df.columns))
    if missing:
        raise ValueError(
            f"{os.path.basename(signal_path)} is missing columns: {', '.join(missing)}"
        )

    pred_dates = df["prediction_date"].dropna().astype(str).unique().tolist()
    if df.empty and not pred_dates:
        df.attrs["prediction_date"] = _signal_date_from_path(signal_path)
        return df
    if len(pred_dates) != 1:
        raise ValueError(
            f"{os.path.basename(signal_path)} contains multiple prediction_date values: {pred_dates}"
        )
    df.attrs["prediction_date"] = pred_dates[0]

    df["target_units"] = pd.to_numeric(df["target_units"], errors="coerce").fillna(0).astype(np.int16)
    df["target_weight_ratio"] = pd.to_numeric(df["target_weight_ratio"], errors="coerce").fillna(0.0)
    df["route_priority"] = pd.to_numeric(df.get("route_priority"), errors="coerce")
    df["rank_20d"] = pd.to_numeric(df.get("rank_20d"), errors="coerce")
    df["rank_t1"] = pd.to_numeric(df.get("rank_t1"), errors="coerce")
    df["risk_adjusted_return"] = pd.to_numeric(df.get("risk_adjusted_return"), errors="coerce")
    df["t1_score"] = pd.to_numeric(df.get("t1_score"), errors="coerce")
    df["close_ref"] = pd.to_numeric(df.get("close_ref"), errors="coerce")
    df["two_stage_rank"] = pd.to_numeric(df.get("two_stage_rank"), errors="coerce")
    df["two_stage_rank_20d"] = pd.to_numeric(df.get("two_stage_rank_20d"), errors="coerce")
    df["price_vs_ma20"] = pd.to_numeric(df.get("price_vs_ma20"), errors="coerce")
    df["price_vs_ma20_20d"] = pd.to_numeric(df.get("price_vs_ma20_20d"), errors="coerce")
    if "hold_days_target" in df.columns:
        df["hold_days_target"] = pd.to_numeric(df["hold_days_target"], errors="coerce")
    return df


def _exit_policy_for_signal(signal_type: str) -> str:
    return EXIT_POLICY_T1 if signal_type == "T1_only" else EXIT_POLICY_20D


def _hold_days_for_policy(exit_policy: str) -> int:
    return T1_HOLD_DAYS if exit_policy == EXIT_POLICY_T1 else TWENTY_DAY_HOLD


def _resolve_hold_days_target(raw_value: object, exit_policy: str) -> int:
    default_hold_days = _hold_days_for_policy(exit_policy)
    numeric_value = pd.to_numeric(pd.Series([raw_value]), errors="coerce").iloc[0]
    if pd.isna(numeric_value):
        return default_hold_days

    hold_days = int(numeric_value)
    return hold_days if hold_days > 0 else default_hold_days


def _load_price_history(
    ticker: str,
    cache: dict[str, pd.DataFrame | None],
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
        ).sort_values("Date").reset_index(drop=True)
    except Exception:
        cache[ticker] = None
        return None

    # Keep moving averages on the cached frame so replay exit policies can use
    # the exact same daily price history without recomputing per position.
    df["MA5"] = df["Close"].rolling(window=5, min_periods=5).mean()
    df["MA10"] = df["Close"].rolling(window=10, min_periods=10).mean()
    df["MA20"] = df["Close"].rolling(window=20, min_periods=20).mean()
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
        INSERT INTO unified_marks (
            position_id, mark_date, trading_day_number,
            open, high, low, close,
            close_return_pct, intraday_drawdown_pct,
            is_exit_day, created_at
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


def _cum_div_since_entry(ticker: str, entry_date_iso: str, mark_date_iso: str) -> float:
    """進場後累計權值+息值(元/股);EXDIV_STOP_ADJUST=0 可停用(A/B 與緊急回退)。"""
    if os.environ.get("EXDIV_STOP_ADJUST", "1") == "0":
        return 0.0
    try:
        from scripts.exdiv_utils import cum_dividend

        return cum_dividend(ticker, entry_date_iso, mark_date_iso)
    except Exception:  # noqa: BLE001 - 日曆缺失時退回未調整(偏保守,不擋 sync)
        return 0.0


def refresh_open_positions(
    conn: sqlite3.Connection,
    stats: SyncStats,
    exit_policy_name: str = EXIT_POLICY_MA5_BREAK_PLUS_HWM_8PCT,
    portfolio_start_date: str = PORTFOLIO_START_DATE,
) -> None:
    cache: dict[str, pd.DataFrame | None] = {}
    resolved_exit_policy_name = resolve_exit_policy_name(exit_policy_name)
    positions = conn.execute(
        """
        SELECT
            id, ticker, prediction_date, exit_policy, hold_days_target,
            planned_entry_ref_price, entry_tag, narrative_heat_at_entry,
            two_stage_rank_at_entry, two_stage_rank_status, price_vs_ma20_at_entry
        FROM unified_positions
        WHERE status IN ('pending', 'open')
          AND prediction_date >= ?
        ORDER BY prediction_date, ticker
        """,
        (portfolio_start_date,),
    ).fetchall()

    now = _utc_now_str()
    narrative_cache: dict[str, dict[str, dict[str, object]]] = {}
    for position in positions:
        ticker = str(position["ticker"])
        position_id = int(position["id"])
        prediction_date = str(position["prediction_date"])
        exit_policy = str(position["exit_policy"] or EXIT_POLICY_20D)
        entry_tag = str(position["entry_tag"] or ENTRY_TAG_FUNDAMENTAL_DRIVEN)
        hold_days_target = int(position["hold_days_target"] or _hold_days_for_policy(exit_policy))

        price_df = _load_price_history(ticker, cache)
        if price_df is None or price_df.empty:
            continue

        future = price_df[price_df["Date"] > pd.Timestamp(prediction_date)].reset_index(drop=True)
        if future.empty:
            conn.execute(
                """
                UPDATE unified_positions
                SET updated_at = ?, status = 'pending'
                WHERE id = ?
                """,
                (now, position_id),
            )
            continue

        if exit_policy == EXIT_POLICY_T1:
            window = future.head(hold_days_target).copy()
            day1 = window.iloc[0]
            entry_fill = simulate_t1_limit_entry(
                reference_price=position["planned_entry_ref_price"],
                open_price=day1["Open"],
                low_price=day1["Low"],
            )
            if entry_fill.fill_status == FILL_STATUS_MISSED:
                mark_date = day1["Date"].date().isoformat()
                _upsert_mark(
                    conn=conn,
                    position_id=position_id,
                    mark_date=mark_date,
                    trading_day_number=1,
                    open_price=float(day1["Open"]),
                    high_price=float(day1["High"]),
                    low_price=float(day1["Low"]),
                    close_price=float(day1["Close"]),
                    close_return_pct=None,
                    intraday_drawdown_pct=None,
                    is_exit_day=1,
                )
                stats.marks_upserted += 1
                conn.execute(
                    """
                    UPDATE unified_positions
                    SET entry_date = ?,
                        order_type = ?,
                        fill_status = ?,
                        fill_price = NULL,
                        entry_slippage_pct = NULL,
                        days_observed = ?,
                        last_mark_date = ?,
                        exit_date = ?,
                        exit_price = NULL,
                        realized_return_pct = NULL,
                        max_drawdown_pct = NULL,
                        max_drawdown_date = NULL,
                        max_drawdown_low = NULL,
                        exit_reason = ?,
                        status = ?,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        mark_date,
                        entry_fill.order_type,
                        FILL_STATUS_MISSED,
                        1,
                        mark_date,
                        mark_date,
                        EXIT_REASON_MISSED_ENTRY_GAP_UP,
                        POSITION_STATUS_MISSED_ENTRY_GAP_UP,
                        now,
                        position_id,
                    ),
                )
                stats.refreshed_positions += 1
                continue

            entry_price = float(entry_fill.fill_price)

            min_drawdown = float("inf")
            min_drawdown_date = None
            min_drawdown_low = None
            stop_exit_price = None
            exit_reason = None
            stop_day_number = None

            t1_entry_date_iso = window.iloc[0]["Date"].date().isoformat()
            for day_number, (_, mark_row) in enumerate(window.iterrows(), start=1):
                mark_date = mark_row["Date"].date().isoformat()
                low_price = float(mark_row["Low"])
                close_price = float(mark_row["Close"])
                cum_div = _cum_div_since_entry(ticker, t1_entry_date_iso, mark_date)
                intraday_drawdown_pct = (low_price + cum_div) / entry_price - 1.0
                close_return_pct = (close_price + cum_div) / entry_price - 1.0

                if intraday_drawdown_pct < min_drawdown:
                    min_drawdown = intraday_drawdown_pct
                    min_drawdown_date = mark_date
                    min_drawdown_low = low_price

                stop_event = simulate_intraday_stop(
                    entry_price=entry_price,
                    open_price=mark_row["Open"],
                    low_price=mark_row["Low"],
                    dividend_adjust=cum_div,
                )
                if stop_event is not None:
                    stop_day_number = day_number
                    stop_exit_price = stop_event.exit_price
                    exit_reason = stop_event.exit_reason

                is_exit_day = int(
                    stop_event is not None
                    or (day_number == hold_days_target and len(window) >= hold_days_target)
                )
                _upsert_mark(
                    conn=conn,
                    position_id=position_id,
                    mark_date=mark_date,
                    trading_day_number=day_number,
                    open_price=float(mark_row["Open"]),
                    high_price=float(mark_row["High"]),
                    low_price=low_price,
                    close_price=close_price,
                    close_return_pct=close_return_pct,
                    intraday_drawdown_pct=intraday_drawdown_pct,
                    is_exit_day=is_exit_day,
                )
                stats.marks_upserted += 1
                if stop_event is not None:
                    break

            days_observed = int(stop_day_number or len(window))
            last_observed_row = window.iloc[days_observed - 1]
            exit_date = None
            exit_price = None
            realized_return_pct = None
            status = "open"
            if stop_day_number is not None:
                status = "stopped_out"
                exit_date = last_observed_row["Date"].date().isoformat()
                exit_price = float(stop_exit_price)
                realized_return_pct = (
                    exit_price + _cum_div_since_entry(ticker, t1_entry_date_iso, exit_date)
                ) / entry_price - 1.0
            elif len(window) >= hold_days_target:
                status = "closed"
                exit_date = last_observed_row["Date"].date().isoformat()
                exit_price = float(last_observed_row["Close"])
                realized_return_pct = (
                    exit_price + _cum_div_since_entry(ticker, t1_entry_date_iso, exit_date)
                ) / entry_price - 1.0
                exit_reason = EXIT_REASON_TIME_T1

            conn.execute(
                """
                UPDATE unified_positions
                SET entry_date = ?,
                    entry_price = ?,
                    order_type = ?,
                    fill_status = ?,
                    fill_price = ?,
                    entry_slippage_pct = ?,
                    days_observed = ?,
                    last_mark_date = ?,
                    exit_date = ?,
                    exit_price = ?,
                    realized_return_pct = ?,
                    max_drawdown_pct = ?,
                    max_drawdown_date = ?,
                    max_drawdown_low = ?,
                    exit_reason = ?,
                    status = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    day1["Date"].date().isoformat(),
                    entry_price,
                    entry_fill.order_type,
                    FILL_STATUS_FILLED,
                    entry_price,
                    entry_fill.entry_slippage_pct,
                    days_observed,
                    last_observed_row["Date"].date().isoformat(),
                    exit_date,
                    exit_price,
                    realized_return_pct,
                    min_drawdown if np.isfinite(min_drawdown) else None,
                    min_drawdown_date,
                    min_drawdown_low,
                    exit_reason,
                    status,
                    now,
                    position_id,
                ),
            )
            stats.refreshed_positions += 1
            continue

        window = future.head(hold_days_target).copy()
        entry_row = window.iloc[0]
        entry_fill = simulate_20d_entry(
            reference_price=position["planned_entry_ref_price"],
            open_price=entry_row["Open"],
            high_price=entry_row["High"],
        )
        if entry_fill.fill_status != FILL_STATUS_FILLED or entry_fill.fill_price is None:
            continue
        entry_price = float(entry_fill.fill_price)

        breached_stop = False
        stop_day_number = None
        stop_exit_price = None
        take_profit_day_number = None
        policy_exit_day_number = None
        policy_exit_price = None
        exit_reason = None
        min_drawdown = float("inf")
        min_drawdown_date = None
        min_drawdown_low = None
        disable_ma_break_for_position = (
            resolved_exit_policy_name == EXIT_POLICY_ASYMMETRIC_V2
            and _rank_is_known(position)
            and is_asymmetric_v2_strong_trend(
                position["two_stage_rank_at_entry"],
                position["price_vs_ma20_at_entry"],
            )
        )
        exit_manager = ExitManager(
            resolved_exit_policy_name,
            entry_price,
            disable_ma_break_for_position=disable_ma_break_for_position,
        )
        narrative_pending_trigger_date = None

        entry_date_iso = entry_row["Date"].date().isoformat()
        for day_number, (_, mark_row) in enumerate(window.iterrows(), start=1):
            mark_date = mark_row["Date"].date().isoformat()
            open_price = float(mark_row["Open"])
            low_price = float(mark_row["Low"])
            close_price = float(mark_row["Close"])
            cum_div = _cum_div_since_entry(ticker, entry_date_iso, mark_date)
            intraday_drawdown_pct = (low_price + cum_div) / entry_price - 1.0
            close_return_pct = (close_price + cum_div) / entry_price - 1.0
            ma20_value = pd.to_numeric(mark_row.get("MA20"), errors="coerce")
            overheat_pct = None
            if pd.notna(ma20_value) and float(ma20_value) > 0:
                overheat_pct = close_price / float(ma20_value) - 1.0

            policy_exit = None
            if narrative_pending_trigger_date is not None:
                policy_exit_day_number = day_number
                policy_exit_price = open_price
                exit_reason = EXIT_REASON_NARRATIVE_DECAY
                narrative_pending_trigger_date = None
            else:
                policy_exit = exit_manager.before_open(mark_row)
            exposed_drawdown_pct = (
                (open_price + cum_div) / entry_price - 1.0
                if policy_exit is not None or policy_exit_day_number is not None
                else intraday_drawdown_pct
            )
            exposed_drawdown_low = open_price if policy_exit is not None or policy_exit_day_number is not None else low_price
            if exposed_drawdown_pct < min_drawdown:
                min_drawdown = exposed_drawdown_pct
                min_drawdown_date = mark_date
                min_drawdown_low = exposed_drawdown_low

            if exit_reason == EXIT_REASON_NARRATIVE_DECAY:
                stop_event = None
            elif policy_exit is not None:
                policy_exit_day_number = day_number
                policy_exit_price = policy_exit.exit_price
                exit_reason = policy_exit.exit_reason
                stop_event = None
            else:
                stop_event = simulate_intraday_stop(
                    entry_price=entry_price,
                    open_price=mark_row["Open"],
                    low_price=mark_row["Low"],
                    dividend_adjust=cum_div,
                )
                if stop_event is not None:
                    breached_stop = True
                    stop_day_number = day_number
                    stop_exit_price = stop_event.exit_price
                    exit_reason = stop_event.exit_reason
                elif (
                    entry_tag == ENTRY_TAG_FUNDAMENTAL_DRIVEN
                    and
                    exit_manager.enable_ma20_take_profit
                    and overheat_pct is not None
                    and overheat_pct >= MA20_TAKE_PROFIT_THRESHOLD
                ):
                    take_profit_day_number = day_number
                    exit_reason = EXIT_REASON_TAKE_PROFIT_MA20
                elif day_number < hold_days_target:
                    if entry_tag == ENTRY_TAG_FUNDAMENTAL_DRIVEN:
                        exit_manager.after_close(mark_row)
                    else:
                        narrative_row = _get_narrative_feature(narrative_cache, mark_date, ticker)
                        if is_narrative_decay_exit(
                            entry_tag,
                            narrative_row.get("heat_3d"),
                            narrative_row.get("decay_slope"),
                            day_number,
                        ):
                            narrative_pending_trigger_date = mark_date

            is_exit_day = int(
                policy_exit_day_number is not None
                or breached_stop
                or take_profit_day_number is not None
                or (day_number == hold_days_target and len(window) >= hold_days_target)
            )
            _upsert_mark(
                conn=conn,
                position_id=position_id,
                mark_date=mark_date,
                trading_day_number=day_number,
                open_price=float(mark_row["Open"]),
                high_price=float(mark_row["High"]),
                low_price=low_price,
                close_price=close_price,
                close_return_pct=close_return_pct,
                intraday_drawdown_pct=intraday_drawdown_pct,
                is_exit_day=is_exit_day,
            )
            stats.marks_upserted += 1

            if policy_exit_day_number is not None:
                break
            if breached_stop:
                break
            if take_profit_day_number is not None:
                break

        if policy_exit_day_number is not None:
            days_observed = int(policy_exit_day_number)
        elif breached_stop:
            days_observed = int(stop_day_number or len(window))
        elif take_profit_day_number is not None:
            days_observed = int(take_profit_day_number)
        else:
            days_observed = int(len(window))
        if policy_exit_day_number is not None:
            status = "closed"
        elif breached_stop:
            status = "stopped_out"
        elif take_profit_day_number is not None:
            status = "closed"
        elif days_observed >= hold_days_target:
            status = "closed"
        else:
            status = "open"

        exit_date = None
        exit_price = None
        realized_return_pct = None
        if policy_exit_day_number is not None:
            exit_row = window.iloc[policy_exit_day_number - 1]
            exit_date = exit_row["Date"].date().isoformat()
            exit_price = float(policy_exit_price)
            realized_return_pct = exit_price / entry_price - 1.0
        elif status == "stopped_out":
            exit_row = window.iloc[stop_day_number - 1]
            exit_date = exit_row["Date"].date().isoformat()
            exit_price = float(stop_exit_price)
            realized_return_pct = exit_price / entry_price - 1.0
        elif take_profit_day_number is not None:
            exit_row = window.iloc[take_profit_day_number - 1]
            exit_date = exit_row["Date"].date().isoformat()
            exit_price = float(exit_row["Close"])
            realized_return_pct = exit_price / entry_price - 1.0
        elif status == "closed":
            exit_row = window.iloc[hold_days_target - 1]
            exit_date = exit_row["Date"].date().isoformat()
            exit_price = float(exit_row["Close"])
            realized_return_pct = exit_price / entry_price - 1.0
            exit_reason = EXIT_REASON_TIME_20D

        # 除息還原:所有出場分支的已實現報酬加回持有期間權值+息值
        if realized_return_pct is not None and exit_date is not None:
            realized_return_pct += _cum_div_since_entry(ticker, entry_date_iso, exit_date) / entry_price

        conn.execute(
            """
            UPDATE unified_positions
            SET entry_date = ?,
                entry_price = ?,
                order_type = ?,
                fill_status = ?,
                fill_price = ?,
                entry_slippage_pct = ?,
                days_observed = ?,
                last_mark_date = ?,
                exit_date = ?,
                exit_price = ?,
                realized_return_pct = ?,
                max_drawdown_pct = ?,
                max_drawdown_date = ?,
                max_drawdown_low = ?,
                exit_reason = ?,
                status = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                entry_row["Date"].date().isoformat(),
                entry_price,
                entry_fill.order_type,
                FILL_STATUS_FILLED,
                entry_price,
                entry_fill.entry_slippage_pct,
                days_observed,
                window.iloc[min(days_observed - 1, len(window) - 1)]["Date"].date().isoformat(),
                exit_date,
                exit_price,
                realized_return_pct,
                min_drawdown if np.isfinite(min_drawdown) else None,
                min_drawdown_date,
                min_drawdown_low,
                exit_reason,
                status,
                now,
                position_id,
            ),
        )
        stats.refreshed_positions += 1

    conn.commit()


def _load_open_positions(conn: sqlite3.Connection) -> pd.DataFrame:
    return pd.read_sql_query(
        """
        SELECT *
        FROM unified_positions
        WHERE status IN ('pending', 'open')
        ORDER BY ticker
        """,
        conn,
    )


def _sort_signal_df(df: pd.DataFrame) -> pd.DataFrame:
    sort_cols: list[str] = []
    ascending: list[bool] = []
    for col, asc in [
        ("route_priority", False),
        ("target_weight_ratio", False),
        ("risk_adjusted_return", False),
        ("t1_score", False),
        ("rank_20d", True),
        ("rank_t1", True),
        ("ticker", True),
    ]:
        if col in df.columns:
            sort_cols.append(col)
            ascending.append(asc)
    if not sort_cols:
        return df.reset_index(drop=True)
    return df.sort_values(sort_cols, ascending=ascending, na_position="last").reset_index(drop=True)


def _cap_delta_weight(raw_weight: float, target_weight_ratio: object, existing_weight: float = 0.0) -> float:
    """Keep ledger allocations within the signal artifact target weight.

    The signal builder may intentionally leave cash unallocated, such as
    EXPENSIVE_MOMENTUM_RISK capping a name at 3%. The legacy unit allocator is
    still used as the baseline sizing mechanism, then clipped to the explicit
    target weight so the paper book cannot silently overfill capped signals.
    """
    try:
        desired_total = float(target_weight_ratio)
    except (TypeError, ValueError):
        desired_total = 0.0
    if not np.isfinite(desired_total) or desired_total <= 0:
        return max(0.0, raw_weight)

    try:
        current = float(existing_weight)
    except (TypeError, ValueError):
        current = 0.0
    if not np.isfinite(current) or current < 0:
        current = 0.0

    max_additional = max(0.0, desired_total - current)
    return max(0.0, min(float(raw_weight), max_additional))


def lock_signal_run(
    conn: sqlite3.Connection,
    signal_path: str,
    rule_version: str,
    stats: SyncStats,
) -> tuple[int | None, str, bool]:
    signal_df = _sort_signal_df(_load_signal_file(signal_path))
    prediction_date = str(signal_df.attrs.get("prediction_date") or _signal_date_from_path(signal_path))
    existing = conn.execute(
        "SELECT id FROM unified_runs WHERE prediction_date = ?",
        (prediction_date,),
    ).fetchone()
    if existing:
        stats.skipped_runs += 1
        print(f"[unified-paper] {prediction_date} already locked, skip.")
        return int(existing["id"]), prediction_date, False

    open_df = _load_open_positions(conn)
    invested_capital = (
        float(pd.to_numeric(open_df["target_weight"], errors="coerce").fillna(0.0).sum())
        if not open_df.empty
        else 0.0
    )
    available_cash = max(0.0, 1.0 - invested_capital)

    open_lookup = {}
    if not open_df.empty:
        open_df["target_units"] = pd.to_numeric(open_df["target_units"], errors="coerce").fillna(0).astype(int)
        open_df["target_weight"] = pd.to_numeric(open_df["target_weight"], errors="coerce").fillna(0.0)
        for row in open_df.to_dict("records"):
            open_lookup[str(row["ticker"])] = row

    signal_df["delta_units"] = signal_df["ticker"].map(
        lambda ticker: max(
            int(signal_df.loc[signal_df["ticker"] == ticker, "target_units"].iloc[0])
            - int(open_lookup.get(str(ticker), {}).get("target_units", 0) or 0),
            0,
        )
    )
    requested_units = int(signal_df["delta_units"].sum())
    cash_per_unit = available_cash / requested_units if requested_units > 0 and available_cash > 0 else 0.0

    now = _utc_now_str()
    signals_sha256 = _sha256(signal_path)
    cursor = conn.execute(
        """
        INSERT INTO unified_runs (
            prediction_date,
            created_at,
            signals_file_path,
            signals_sha256,
            rule_version,
            total_candidates,
            total_units,
            available_cash_before,
            cash_deployed,
            new_positions,
            upgraded_positions
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            prediction_date,
            now,
            os.path.abspath(signal_path),
            signals_sha256,
            rule_version,
            int(len(signal_df)),
            int(signal_df["target_units"].sum()),
            available_cash,
            0.0,
            0,
            0,
        ),
    )
    run_id = int(cursor.lastrowid)
    stats.new_runs += 1

    run_new_positions = 0
    run_upgrades = 0
    run_cash_deployed = 0.0
    narrative_at_entry = _load_narrative_features_for_date(prediction_date)

    for row in signal_df.to_dict("records"):
        ticker = str(row.get("ticker", ""))
        signal_type = str(row.get("signal_type", ""))
        target_units = int(row.get("target_units", 0) or 0)
        delta_units = int(row.get("delta_units", 0) or 0)
        exit_policy = _exit_policy_for_signal(signal_type)
        hold_days_target = _resolve_hold_days_target(row.get("hold_days_target"), exit_policy)
        entry_tag, narrative_heat_at_entry = _entry_tag_for_signal(narrative_at_entry, ticker)
        two_stage_rank_at_entry = _first_numeric(row, "two_stage_rank", "two_stage_rank_20d")
        two_stage_rank_status = _two_stage_rank_status(two_stage_rank_at_entry)
        price_vs_ma20_at_entry = _first_numeric(row, "price_vs_ma20", "price_vs_ma20_20d")
        entry_snapshot = _entry_snapshot_from_signal(
            row,
            prediction_date=prediction_date,
            signals_sha256=signals_sha256,
            exit_policy=exit_policy,
            confidence=ENTRY_SNAPSHOT_CONFIDENCE_EXACT,
        )

        existing_open = open_lookup.get(ticker)
        existing_weight = float(existing_open.get("target_weight", 0.0) or 0.0) if existing_open else 0.0
        raw_allocated_weight = float(delta_units * cash_per_unit) if delta_units > 0 else 0.0
        allocated_weight = _cap_delta_weight(
            raw_allocated_weight,
            row.get("target_weight_ratio"),
            existing_weight=existing_weight,
        )
        if existing_open:
            conn.execute(
                """
                UPDATE unified_positions
                SET last_run_id = ?,
                    last_signal_date = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (run_id, prediction_date, now, int(existing_open["id"])),
            )

            if delta_units > 0 and allocated_weight > 0:
                conn.execute(
                    """
                    UPDATE unified_positions
                    SET current_signal_type = ?,
                        target_units = ?,
                        target_weight = target_weight + ?,
                        exit_policy = ?,
                        hold_days_target = ?,
                        upgrade_count = upgrade_count + 1,
                        upgraded_at = ?,
                        last_run_id = ?,
                        last_signal_date = ?,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        signal_type,
                        target_units,
                        allocated_weight,
                        exit_policy,
                        hold_days_target,
                        now,
                        run_id,
                        prediction_date,
                        now,
                        int(existing_open["id"]),
                    ),
                )
                run_upgrades += 1
                stats.upgraded_positions += 1
                run_cash_deployed += allocated_weight
                stats.cash_deployed += allocated_weight
            continue

        if allocated_weight <= 0:
            continue

        conn.execute(
            """
            INSERT INTO unified_positions (
                opened_by_run_id,
                last_run_id,
                prediction_date,
                last_signal_date,
                ticker,
                sector,
                entry_signal_type,
                current_signal_type,
                target_units,
                target_weight,
                planned_entry_ref_price,
                entry_tag,
                narrative_heat_at_entry,
                two_stage_rank_at_entry,
                two_stage_rank_status,
                price_vs_ma20_at_entry,
                order_type,
                fill_status,
                exit_policy,
                hold_days_target,
                entry_snapshot_id,
                entry_model_version,
                entry_rank_20d,
                entry_two_stage_rank,
                entry_score,
                entry_prob_edge,
                entry_risk_adjusted_return_pre_penalty,
                entry_leaderboard_score_pre_penalty,
                entry_penalty_overlay_total,
                entry_guardrail_version,
                entry_allocation_version,
                entry_execution_policy_version,
                entry_snapshot_confidence,
                entry_snapshot_reason_code,
                status,
                created_at,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                run_id,
                prediction_date,
                prediction_date,
                ticker,
                _coerce_db_value(row.get("sector")),
                signal_type,
                signal_type,
                target_units,
                allocated_weight,
                _coerce_db_value(row.get("close_ref")),
                entry_tag,
                narrative_heat_at_entry,
                two_stage_rank_at_entry,
                two_stage_rank_status,
                price_vs_ma20_at_entry,
                None,
                FILL_STATUS_PENDING,
                exit_policy,
                hold_days_target,
                *_snapshot_values(entry_snapshot),
                "pending",
                now,
                now,
            ),
        )
        run_new_positions += 1
        stats.new_positions += 1
        run_cash_deployed += allocated_weight
        stats.cash_deployed += allocated_weight

    conn.execute(
        """
        UPDATE unified_runs
        SET cash_deployed = ?,
            new_positions = ?,
            upgraded_positions = ?
        WHERE id = ?
        """,
        (
            run_cash_deployed,
            run_new_positions,
            run_upgrades,
            run_id,
        ),
    )
    conn.commit()
    print(
        f"[unified-paper] Locked {prediction_date}: "
        f"candidates={len(signal_df)} new={run_new_positions} upgrades={run_upgrades} "
        f"cash_before={available_cash:.4f} deployed={run_cash_deployed:.4f}"
    )
    return run_id, prediction_date, True


def summarize_ledger(
    conn: sqlite3.Connection,
    portfolio_start_date: str = PORTFOLIO_START_DATE,
) -> dict[str, object]:
    run_row = conn.execute(
        "SELECT COUNT(*) AS total_runs FROM unified_runs WHERE prediction_date >= ?",
        (portfolio_start_date,),
    ).fetchone()
    position_row = conn.execute(
        """
        SELECT
            COUNT(*) AS total_positions,
            SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) AS pending_positions,
            SUM(CASE WHEN status = 'open' THEN 1 ELSE 0 END) AS open_positions,
            SUM(CASE WHEN status = 'closed' THEN 1 ELSE 0 END) AS closed_positions,
            SUM(CASE WHEN status = 'stopped_out' THEN 1 ELSE 0 END) AS stopped_out_positions,
            SUM(CASE WHEN status = 'missed_entry_gap_up' THEN 1 ELSE 0 END) AS missed_entry_positions,
            SUM(CASE WHEN status IN ('pending', 'open') THEN target_weight ELSE 0 END) AS invested_capital
        FROM unified_positions
        WHERE prediction_date >= ?
        """,
        (portfolio_start_date,),
    ).fetchone()
    closed_row = conn.execute(
        """
        SELECT
            AVG(realized_return_pct) AS avg_realized_return_pct,
            AVG(max_drawdown_pct) AS avg_max_drawdown_pct
        FROM unified_positions
        WHERE status IN ('closed', 'stopped_out')
          AND prediction_date >= ?
        """,
        (portfolio_start_date,),
    ).fetchone()
    mark_row = conn.execute(
        """
        SELECT COUNT(*) AS total_marks
        FROM unified_marks m
        JOIN unified_positions p ON p.id = m.position_id
        WHERE p.prediction_date >= ?
        """,
        (portfolio_start_date,),
    ).fetchone()
    invested_capital = float(position_row["invested_capital"] or 0.0)
    return {
        "total_runs": int(run_row["total_runs"] or 0),
        "total_positions": int(position_row["total_positions"] or 0),
        "pending_positions": int(position_row["pending_positions"] or 0),
        "open_positions": int(position_row["open_positions"] or 0),
        "closed_positions": int(position_row["closed_positions"] or 0),
        "stopped_out_positions": int(position_row["stopped_out_positions"] or 0),
        "missed_entry_positions": int(position_row["missed_entry_positions"] or 0),
        "invested_capital": invested_capital,
        "available_cash": max(0.0, 1.0 - invested_capital),
        "avg_realized_return_pct": _coerce_db_value(closed_row["avg_realized_return_pct"]),
        "avg_max_drawdown_pct": _coerce_db_value(closed_row["avg_max_drawdown_pct"]),
        "total_marks": int(mark_row["total_marks"] or 0),
    }


def sync_unified_portfolio(
    signal_files: list[str] | None = None,
    dates: list[str] | None = None,
    backfill_all: bool = False,
    db_path: str = DEFAULT_DB_PATH,
    rule_version: str = DEFAULT_RULE_VERSION,
    exit_policy_name: str = EXIT_POLICY_MA5_BREAK_PLUS_HWM_8PCT,
    portfolio_start_date: str = PORTFOLIO_START_DATE,
) -> dict[str, object]:
    stats = SyncStats()
    resolved_signal_paths = _resolve_signal_paths(
        signal_files,
        dates,
        backfill_all,
        portfolio_start_date=portfolio_start_date,
    )
    resolved_exit_policy_name = resolve_exit_policy_name(exit_policy_name)

    processed_dates: list[str] = []
    with connect_db(db_path) as conn:
        ensure_schema(conn)
        backfill_narrative_entry_tags(conn, portfolio_start_date=portfolio_start_date)
        entry_snapshot_backfill = backfill_entry_snapshots(conn, portfolio_start_date=portfolio_start_date)
        refresh_open_positions(
            conn,
            stats,
            exit_policy_name=resolved_exit_policy_name,
            portfolio_start_date=portfolio_start_date,
        )
        for signal_path in resolved_signal_paths:
            _, prediction_date, created = lock_signal_run(
                conn=conn,
                signal_path=signal_path,
                rule_version=rule_version,
                stats=stats,
            )
            if created:
                processed_dates.append(prediction_date)
                refresh_open_positions(
                    conn,
                    stats,
                    exit_policy_name=resolved_exit_policy_name,
                    portfolio_start_date=portfolio_start_date,
                )
        summary = summarize_ledger(conn, portfolio_start_date=portfolio_start_date)

    return {
        "db_path": os.path.abspath(db_path),
        "signal_files": [os.path.abspath(path) for path in resolved_signal_paths],
        "processed_dates": processed_dates,
        "new_runs": stats.new_runs,
        "skipped_runs": stats.skipped_runs,
        "new_positions": stats.new_positions,
        "upgraded_positions": stats.upgraded_positions,
        "refreshed_positions": stats.refreshed_positions,
        "marks_upserted": stats.marks_upserted,
        "cash_deployed": stats.cash_deployed,
        "rule_version": rule_version,
        "exit_policy_name": resolved_exit_policy_name,
        "portfolio_start_date": portfolio_start_date,
        "entry_snapshot_backfill": entry_snapshot_backfill,
        "summary": summary,
    }


def main(argv: list[str] | None = None) -> dict[str, object]:
    parser = argparse.ArgumentParser(description="Sync the unified portfolio ledger.")
    parser.add_argument("--signal-file", action="append", help="Specific unified_signals_*.csv path.")
    parser.add_argument("--date", action="append", help="Specific prediction date (YYYY-MM-DD).")
    parser.add_argument("--backfill-all", action="store_true")
    parser.add_argument("--db-path", default=DEFAULT_DB_PATH)
    parser.add_argument(
        "--rule-version",
        default=DEFAULT_RULE_VERSION,
        help="Rule version recorded on each unified run.",
    )
    parser.add_argument(
        "--exit-policy",
        choices=sorted(SUPPORTED_EXIT_POLICIES),
        default=EXIT_POLICY_MA5_BREAK_PLUS_HWM_8PCT,
        help="Exit policy applied when refreshing and extending the unified ledger.",
    )
    parser.add_argument(
        "--portfolio-start-date",
        default=PORTFOLIO_START_DATE,
        help="Earliest prediction date accepted by the ledger.",
    )
    parser.add_argument(
        "--backfill-entry-snapshots-only",
        action="store_true",
        help="Only migrate/backfill entry snapshot columns; do not sync new signal files.",
    )
    args = parser.parse_args(argv)

    if args.backfill_entry_snapshots_only:
        with connect_db(args.db_path) as conn:
            ensure_schema(conn)
            result = backfill_entry_snapshots(conn, portfolio_start_date=args.portfolio_start_date)
            summary = summarize_ledger(conn, portfolio_start_date=args.portfolio_start_date)
        print(
            "[unified-paper] "
            f"entry_snapshot_backfill={result['updated']} "
            f"partial={result['partial']} missing_file={result['missing_file']} "
            f"missing_row={result['missing_row']}"
        )
        return {
            "db_path": os.path.abspath(args.db_path),
            "entry_snapshot_backfill": result,
            "portfolio_start_date": args.portfolio_start_date,
            "summary": summary,
        }

    result = sync_unified_portfolio(
        signal_files=args.signal_file,
        dates=args.date,
        backfill_all=args.backfill_all,
        db_path=args.db_path,
        rule_version=args.rule_version,
        exit_policy_name=args.exit_policy,
        portfolio_start_date=args.portfolio_start_date,
    )
    summary = result["summary"]
    print(
        "[unified-paper] "
        f"runs={summary['total_runs']} pending={summary['pending_positions']} "
        f"open={summary['open_positions']} closed={summary['closed_positions']} "
        f"stopped={summary['stopped_out_positions']} missed={summary['missed_entry_positions']} "
        f"invested={summary['invested_capital']:.4f} "
        f"cash={summary['available_cash']:.4f} "
        f"policy={result['exit_policy_name']}"
    )
    return result


if __name__ == "__main__":
    main()
