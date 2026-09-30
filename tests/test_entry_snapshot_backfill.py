import sqlite3
from pathlib import Path

import pandas as pd

from scripts import update_unified_portfolio as u


def _insert_run(conn: sqlite3.Connection, signal_path: Path, prediction_date: str) -> int:
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
            "2026-05-15 00:00:00",
            str(signal_path),
            u._sha256(str(signal_path)),
            u.DEFAULT_RULE_VERSION,
            1,
            1,
            1.0,
            0.1,
            1,
            0,
        ),
    )
    return int(cursor.lastrowid)


def _insert_position(
    conn: sqlite3.Connection,
    *,
    run_id: int,
    prediction_date: str,
    ticker: str,
) -> None:
    conn.execute(
        """
        INSERT INTO unified_positions (
            opened_by_run_id,
            last_run_id,
            prediction_date,
            last_signal_date,
            ticker,
            entry_signal_type,
            current_signal_type,
            target_units,
            target_weight,
            exit_policy,
            hold_days_target,
            status,
            created_at,
            updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            run_id,
            prediction_date,
            prediction_date,
            ticker,
            "20D_only",
            "20D_only",
            1,
            0.1,
            u.EXIT_POLICY_20D,
            20,
            "pending",
            "2026-05-15 00:00:00",
            "2026-05-15 00:00:00",
        ),
    )


def _write_signal(path: Path, *, prediction_date: str, ticker: str = "2330") -> None:
    df = pd.DataFrame(
        [
            {
                "prediction_date": prediction_date,
                "ticker": ticker,
                "signal_type": "20D_only",
                "target_units": 1,
                "target_weight_ratio": 0.1,
                "close_ref": 100.0,
                "rank_20d": 3,
                "two_stage_rank": 2,
                "pred_return_20d": 0.123,
                "prob_edge": 0.045,
                "risk_adjusted_return_pre_penalty": 0.067,
                "leaderboard_score_pre_penalty": 0.5,
                "penalty_overlay_total": 0.02,
                "source_20d_file": f"predictions_{prediction_date}.csv",
                "group_cap_applied": False,
                "guardrail_monitor_top_n": 50,
                "guardrail_loose_mode": False,
            }
        ]
    )
    df.to_csv(path, index=False, encoding="utf-8-sig")


def test_ensure_schema_adds_entry_snapshot_columns(tmp_path: Path) -> None:
    db_path = tmp_path / "portfolio.db"
    with u.connect_db(str(db_path)) as conn:
        u.ensure_schema(conn)
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(unified_positions)").fetchall()}

    expected = {column for column, _ in u.ENTRY_SNAPSHOT_COLUMNS}
    assert expected.issubset(columns)


def test_backfill_entry_snapshot_marks_historical_rows_as_backfill(tmp_path: Path) -> None:
    prediction_date = "2026-05-13"
    signal_path = tmp_path / f"unified_signals_{prediction_date}.csv"
    _write_signal(signal_path, prediction_date=prediction_date)

    db_path = tmp_path / "portfolio.db"
    with u.connect_db(str(db_path)) as conn:
        u.ensure_schema(conn)
        run_id = _insert_run(conn, signal_path, prediction_date)
        _insert_position(conn, run_id=run_id, prediction_date=prediction_date, ticker="2330")
        conn.commit()

        result = u.backfill_entry_snapshots(conn, portfolio_start_date="2026-05-01")
        row = conn.execute(
            """
            SELECT entry_snapshot_id,
                   entry_snapshot_confidence,
                   entry_snapshot_reason_code,
                   entry_rank_20d,
                   entry_two_stage_rank,
                   entry_score,
                   entry_prob_edge
            FROM unified_positions
            WHERE ticker = '2330'
            """
        ).fetchone()

    assert result["updated"] == 1
    assert row["entry_snapshot_id"].startswith(f"{prediction_date}:{u._sha256(str(signal_path))}:2330")
    assert row["entry_snapshot_confidence"] == u.ENTRY_SNAPSHOT_CONFIDENCE_BACKFILL
    assert u.ENTRY_SNAPSHOT_REASON_BACKFILLED in row["entry_snapshot_reason_code"]
    assert row["entry_rank_20d"] == 3
    assert row["entry_two_stage_rank"] == 2
    assert row["entry_score"] == 0.123
    assert row["entry_prob_edge"] == 0.045


def test_new_signal_run_records_exact_entry_snapshot(tmp_path: Path) -> None:
    prediction_date = "2026-05-13"
    signal_path = tmp_path / f"unified_signals_{prediction_date}.csv"
    _write_signal(signal_path, prediction_date=prediction_date)

    db_path = tmp_path / "portfolio.db"
    with u.connect_db(str(db_path)) as conn:
        u.ensure_schema(conn)
        stats = u.SyncStats()

        run_id, locked_date, created = u.lock_signal_run(
            conn=conn,
            signal_path=str(signal_path),
            rule_version=u.DEFAULT_RULE_VERSION,
            stats=stats,
        )
        row = conn.execute(
            """
            SELECT entry_snapshot_confidence,
                   entry_snapshot_reason_code,
                   entry_snapshot_id,
                   entry_rank_20d,
                   entry_two_stage_rank
            FROM unified_positions
            WHERE ticker = '2330'
            """
        ).fetchone()

    assert run_id is not None
    assert locked_date == prediction_date
    assert created is True
    assert row["entry_snapshot_confidence"] == u.ENTRY_SNAPSHOT_CONFIDENCE_EXACT
    assert row["entry_snapshot_reason_code"] == u.ENTRY_SNAPSHOT_REASON_OK
    assert row["entry_snapshot_id"].startswith(f"{prediction_date}:{u._sha256(str(signal_path))}:2330")
    assert row["entry_rank_20d"] == 3
    assert row["entry_two_stage_rank"] == 2


def test_backfill_entry_snapshot_records_missing_signal_row_reason(tmp_path: Path) -> None:
    prediction_date = "2026-05-13"
    signal_path = tmp_path / f"unified_signals_{prediction_date}.csv"
    _write_signal(signal_path, prediction_date=prediction_date, ticker="2330")

    db_path = tmp_path / "portfolio.db"
    with u.connect_db(str(db_path)) as conn:
        u.ensure_schema(conn)
        run_id = _insert_run(conn, signal_path, prediction_date)
        _insert_position(conn, run_id=run_id, prediction_date=prediction_date, ticker="2317")
        conn.commit()

        result = u.backfill_entry_snapshots(conn, portfolio_start_date="2026-05-01")
        row = conn.execute(
            """
            SELECT entry_snapshot_confidence, entry_snapshot_reason_code
            FROM unified_positions
            WHERE ticker = '2317'
            """
        ).fetchone()

    assert result["missing_row"] == 1
    assert row["entry_snapshot_confidence"] == u.ENTRY_SNAPSHOT_CONFIDENCE_MISSING_ROW
    assert u.ENTRY_SNAPSHOT_REASON_MISSING_ROW in row["entry_snapshot_reason_code"]
