from __future__ import annotations

import pandas as pd

import scripts.build_unified_signals as build_unified_signals
from ml.thresholds import EXPENSIVE_MOMENTUM_WEIGHT_CAP
from scripts.build_unified_signals import (
    SIGNAL_TYPE_20D_ONLY,
    TRADABILITY_REASON_EXPENSIVE_MOMENTUM_RISK,
    _apply_expensive_momentum_gate,
    _assign_target_fields,
    _build_20d_candidates,
    _finalize_unified_df,
)
from scripts.update_unified_portfolio import SyncStats, connect_db, ensure_schema, lock_signal_run


def test_expensive_momentum_gate_caps_weight_and_appends_reason() -> None:
    df = pd.DataFrame(
        [
            {
                "ticker": "3131",
                "signal_type": SIGNAL_TYPE_20D_ONLY,
                "pe_ratio_20d": 63.98,
                "price_vs_ma60_20d": 0.28,
                "tradability_reason": "",
            },
            {
                "ticker": "1305",
                "signal_type": SIGNAL_TYPE_20D_ONLY,
                "pe_ratio_20d": float("nan"),
                "price_vs_ma60_20d": 0.32,
                "tradability_reason": "",
            },
        ]
    )

    out = _apply_expensive_momentum_gate(_assign_target_fields(df))

    trigger = out.loc[out["ticker"] == "3131"].iloc[0]
    safe = out.loc[out["ticker"] == "1305"].iloc[0]
    assert bool(trigger["expensive_momentum_risk"]) is True
    assert trigger["target_weight_ratio"] == EXPENSIVE_MOMENTUM_WEIGHT_CAP
    assert TRADABILITY_REASON_EXPENSIVE_MOMENTUM_RISK in trigger["tradability_reason"]
    assert bool(safe["expensive_momentum_risk"]) is False
    assert safe["target_weight_ratio"] > EXPENSIVE_MOMENTUM_WEIGHT_CAP


def test_build_20d_candidates_backfills_pe_ratio_from_same_date_valuation(tmp_path, monkeypatch) -> None:
    valuation_dir = tmp_path / "valuation"
    valuation_dir.mkdir()
    pd.DataFrame({"Ticker": ["3131"], "PE_Ratio": [63.98]}).to_csv(
        valuation_dir / "valuation_20260505.csv",
        index=False,
    )
    monkeypatch.setattr(build_unified_signals, "VALUATION_DIR", str(valuation_dir))

    pred = pd.DataFrame(
        [
            {
                "ticker": "3131",
                "date": "2026-05-05",
                "close": 2910.0,
                "recommendation": "建議買進",
                "pred_return_20d": 0.10,
                "risk_adjusted_return": 0.10,
                "leaderboard_score": 0.10,
                "sector": "OTHER",
                "price_vs_ma60": 0.28,
                "beta_60": 0.5,
            },
            {
                "ticker": "1305",
                "date": "2026-05-05",
                "close": 14.3,
                "recommendation": "建議買進",
                "pred_return_20d": 0.09,
                "risk_adjusted_return": 0.09,
                "leaderboard_score": 0.09,
                "sector": "PLASTIC",
                "price_vs_ma60": 0.30,
                "beta_60": 0.5,
            },
        ]
    )

    out = _build_20d_candidates(pred, top_n=2)

    pe_3131 = out.loc[out["ticker"] == "3131", "pe_ratio_20d"].iloc[0]
    pe_1305 = out.loc[out["ticker"] == "1305", "pe_ratio_20d"].iloc[0]
    assert pe_3131 > 50
    assert pd.isna(pe_1305)


def test_build_20d_candidates_backfills_legacy_guardrail_reason() -> None:
    pred = pd.DataFrame(
        [
            {
                "ticker": "9999",
                "date": "2026-04-30",
                "close": 100.0,
                "recommendation": "建議買進",
                "signal": "UP",
                "pred_return_20d": 0.12,
                "risk_adjusted_return": 0.12,
                "leaderboard_score": 0.12,
                "guardrail_blocked": 1,
                "guardrail_trigger_reason": float("nan"),
                "sector": "半導體業",
                "price_vs_ma60": 0.10,
                "beta_60": 0.8,
            }
        ]
    )

    out = _build_20d_candidates(pred, top_n=1)

    assert out.loc[0, "guardrail_blocked_20d"] == 1
    assert out.loc[0, "guardrail_trigger_reason_20d"] == "NO_BUY_SIGNAL_OR_LOW_EDGE"


def test_finalize_unified_persists_guardrail_columns() -> None:
    unified = pd.DataFrame(
        [
            {
                "ticker": "9999",
                "rank_20d": 1,
                "rank_t1": pd.NA,
                "t1_score": pd.NA,
                "recommendation_20d": "建議買進",
                "signal_20d": "BUY",
                "pred_return_20d": 0.08,
                "risk_adjusted_return": 0.07,
                "guardrail_blocked_20d": 1,
                "guardrail_trigger_reason_20d": "LIQUIDITY_GUARD",
                "guardrail_loose_mode_20d": 0,
                "guardrail_intercept_rate_top50_20d": 0.12,
                "guardrail_dual_track_fail_rate_top50_20d": 0.34,
                "guardrail_monitor_top_n_20d": 50,
                "avg_20d_volume_20d": 10_000,
                "avg_20d_amount_20d": 50_000_000,
                "avg_5d_volume_20d": 10_000,
                "avg_5d_amount_20d": 50_000_000,
                "price_vs_ma60_20d": 0.10,
                "price_vs_ma5_20d": 0.02,
                "price_vs_ma20_20d": 0.04,
                "beta_60_20d": 1.0,
                "sector_20d": "半導體業",
                "risk_tags_20d": "",
                "two_stage_rank_20d": 1,
            }
        ]
    )

    out = _finalize_unified_df(
        unified,
        prediction_date="2026-05-08",
        source_20d_path="predictions_2026-05-08.csv",
        source_t1_path="predictions_t1_2026-05-08.csv",
        production_gate={"status": "ALLOW", "reason": ""},
    )
    row = out.iloc[0]

    assert row["guardrail_blocked"] == 1
    assert row["guardrail_trigger_reason"] == "LIQUIDITY_GUARD"
    assert row["guardrail_loose_mode"] == 0
    assert row["guardrail_intercept_rate_top50"] == 0.12
    assert row["guardrail_dual_track_fail_rate_top50"] == 0.34
    assert row["guardrail_monitor_top_n"] == 50


def test_finalize_unified_backfills_empty_guardrail_reason_from_recommendation() -> None:
    unified = pd.DataFrame(
        [
            {
                "ticker": "9999",
                "rank_20d": 1,
                "rank_t1": pd.NA,
                "t1_score": pd.NA,
                "recommendation_20d": "觀望（處置股）",
                "signal_20d": "UP",
                "pred_return_20d": 0.08,
                "risk_adjusted_return": 0.07,
                "guardrail_blocked_20d": 1,
                "guardrail_trigger_reason_20d": "",
                "avg_20d_volume_20d": 10_000,
                "avg_20d_amount_20d": 50_000_000,
                "avg_5d_volume_20d": 10_000,
                "avg_5d_amount_20d": 50_000_000,
                "price_vs_ma60_20d": 0.10,
                "price_vs_ma5_20d": 0.02,
                "price_vs_ma20_20d": 0.04,
                "beta_60_20d": 1.0,
                "sector_20d": "半導體業",
                "risk_tags_20d": "",
                "two_stage_rank_20d": 1,
            }
        ]
    )

    out = _finalize_unified_df(
        unified,
        prediction_date="2026-04-30",
        source_20d_path="predictions_2026-04-30.csv",
        source_t1_path="T1_DISABLED",
        production_gate={"status": "DISABLED", "reason": "20d_only_research_backfill"},
    )

    assert out.iloc[0]["guardrail_trigger_reason"] == "DISPOSITION_PERIOD"


def test_unified_portfolio_respects_signal_target_weight_cap(tmp_path) -> None:
    signal_path = tmp_path / "unified_signals_2026-05-05.csv"
    pd.DataFrame(
        [
            {
                "prediction_date": "2026-05-05",
                "ticker": "3131",
                "signal_type": SIGNAL_TYPE_20D_ONLY,
                "target_units": 2,
                "target_weight_ratio": EXPENSIVE_MOMENTUM_WEIGHT_CAP,
                "route_priority": 2,
                "rank_20d": 1,
                "risk_adjusted_return": 0.10,
                "close_ref": 2910.0,
                "sector": "OTHER",
            },
            {
                "prediction_date": "2026-05-05",
                "ticker": "2330",
                "signal_type": SIGNAL_TYPE_20D_ONLY,
                "target_units": 2,
                "target_weight_ratio": 0.97,
                "route_priority": 2,
                "rank_20d": 2,
                "risk_adjusted_return": 0.09,
                "close_ref": 900.0,
                "sector": "SEMICONDUCTOR",
            },
        ]
    ).to_csv(signal_path, index=False)

    db_path = tmp_path / "portfolio.db"
    conn = connect_db(str(db_path))
    try:
        ensure_schema(conn)
        _, _, created = lock_signal_run(
            conn,
            str(signal_path),
            rule_version="test",
            stats=SyncStats(),
        )
        assert created is True
        rows = pd.read_sql_query(
            "SELECT ticker, target_weight FROM unified_positions ORDER BY ticker",
            conn,
        )
    finally:
        conn.close()

    weights = dict(zip(rows["ticker"], rows["target_weight"]))
    assert weights["3131"] == EXPENSIVE_MOMENTUM_WEIGHT_CAP
    assert weights["2330"] > EXPENSIVE_MOMENTUM_WEIGHT_CAP


def test_unified_portfolio_locks_empty_signal_run(tmp_path) -> None:
    signal_path = tmp_path / "unified_signals_2026-05-07.csv"
    pd.DataFrame(
        columns=[
            "prediction_date",
            "ticker",
            "signal_type",
            "target_units",
            "target_weight_ratio",
            "close_ref",
        ]
    ).to_csv(signal_path, index=False)

    db_path = tmp_path / "portfolio.db"
    conn = connect_db(str(db_path))
    try:
        ensure_schema(conn)
        stats = SyncStats()
        run_id, locked_date, created = lock_signal_run(
            conn,
            str(signal_path),
            rule_version="test",
            stats=stats,
        )
        row = conn.execute(
            """
            SELECT prediction_date, total_candidates, total_units,
                   new_positions, upgraded_positions, cash_deployed
            FROM unified_runs
            """
        ).fetchone()
    finally:
        conn.close()

    assert run_id is not None
    assert locked_date == "2026-05-07"
    assert created is True
    assert stats.new_runs == 1
    assert row["prediction_date"] == "2026-05-07"
    assert row["total_candidates"] == 0
    assert row["total_units"] == 0
    assert row["new_positions"] == 0
    assert row["upgraded_positions"] == 0
    assert row["cash_deployed"] == 0.0


def test_cap_delta_weight_does_not_upgrade_existing_over_cap_position(tmp_path) -> None:
    signal_path = tmp_path / "unified_signals_2026-05-06.csv"
    pd.DataFrame(
        [
            {
                "prediction_date": "2026-05-06",
                "ticker": "3131",
                "signal_type": SIGNAL_TYPE_20D_ONLY,
                "target_units": 2,
                "target_weight_ratio": EXPENSIVE_MOMENTUM_WEIGHT_CAP,
                "route_priority": 2,
                "rank_20d": 1,
                "risk_adjusted_return": 0.10,
                "close_ref": 2910.0,
                "sector": "OTHER",
            },
        ]
    ).to_csv(signal_path, index=False)

    db_path = tmp_path / "portfolio.db"
    conn = connect_db(str(db_path))
    try:
        ensure_schema(conn)
        now = "2026-05-06T00:00:00Z"
        conn.execute(
            """
            INSERT INTO unified_runs (
                prediction_date, created_at, signals_file_path, signals_sha256,
                rule_version, total_candidates, total_units, available_cash_before,
                cash_deployed, new_positions, upgraded_positions
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("2026-05-05", now, "seed.csv", "seed", "test", 1, 2, 1.0, 0.10, 1, 0),
        )
        run_id = int(conn.execute("SELECT id FROM unified_runs").fetchone()[0])
        conn.execute(
            """
            INSERT INTO unified_positions (
                opened_by_run_id, last_run_id, prediction_date, last_signal_date,
                ticker, sector, entry_signal_type, current_signal_type, target_units,
                target_weight, planned_entry_ref_price, exit_policy, hold_days_target,
                status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                run_id,
                "2026-05-05",
                "2026-05-05",
                "3131",
                "OTHER",
                SIGNAL_TYPE_20D_ONLY,
                SIGNAL_TYPE_20D_ONLY,
                2,
                0.10,
                2910.0,
                "20D",
                20,
                "pending",
                now,
                now,
            ),
        )
        conn.commit()

        lock_signal_run(
            conn,
            str(signal_path),
            rule_version="test",
            stats=SyncStats(),
        )
        weight = conn.execute(
            "SELECT target_weight FROM unified_positions WHERE ticker = '3131'"
        ).fetchone()[0]
    finally:
        conn.close()

    assert weight == 0.10
