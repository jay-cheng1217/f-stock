from __future__ import annotations

import inspect

import duckdb
import pandas as pd

from scripts import update_unified_portfolio as unified
from scripts import update_paper_portfolio as legacy_paper
from scripts.exit_policies import (
    ENTRY_TAG_FUNDAMENTAL_DRIVEN,
    ENTRY_TAG_NARRATIVE_DRIVEN,
    EXIT_POLICY_ASYMMETRIC_V2,
    EXIT_POLICY_MA5_BREAK_PLUS_HWM_8PCT,
    EXIT_POLICY_STOP_ONLY_20D,
    EXIT_REASON_NARRATIVE_DECAY,
    REGIME_EXIT_VARIANT_20D_RETURN_8PCT,
    REGIME_EXIT_VARIANT_MA20_3PCT,
    REGIME_EXIT_VARIANT_MA20_5PCT,
    REGIME_EXIT_VARIANT_MA20_5PCT_HWM,
    REGIME_EXIT_VARIANT_NONE,
    ExitManager,
    classify_entry_tag,
    is_regime_exit_overlay_active,
    is_asymmetric_v2_below_ma5_stop_only,
    is_asymmetric_v2_group_b_stop_only,
    is_asymmetric_v2_strong_trend,
    is_narrative_decay_exit,
    resolve_regime_exit_route,
    resolve_regime_exit_variant,
)


def test_classify_entry_tag_and_decay_thresholds() -> None:
    tag, heat = classify_entry_tag('["AI_THEME"]', 0.31)
    assert tag == ENTRY_TAG_NARRATIVE_DRIVEN
    assert heat == 0.31

    tag, _ = classify_entry_tag("[]", 0.90)
    assert tag == ENTRY_TAG_FUNDAMENTAL_DRIVEN

    assert is_narrative_decay_exit(ENTRY_TAG_NARRATIVE_DRIVEN, 0.19, -0.31, 5)
    assert not is_narrative_decay_exit(ENTRY_TAG_NARRATIVE_DRIVEN, 0.19, -0.31, 4)
    assert not is_narrative_decay_exit(ENTRY_TAG_FUNDAMENTAL_DRIVEN, 0.19, -0.31, 5)


def test_ma_break_policy_is_enabled_by_default_but_can_be_disabled() -> None:
    mark = pd.Series({"Date": "2026-05-08", "Open": 99.0, "Close": 96.0, "MA5": 97.0})
    next_mark = pd.Series({"Date": "2026-05-11", "Open": 95.0, "Close": 94.0, "MA5": 96.0})

    enabled = ExitManager(EXIT_POLICY_MA5_BREAK_PLUS_HWM_8PCT, entry_price=100.0)
    enabled.after_close(mark)
    decision = enabled.before_open(next_mark)
    assert decision is not None
    assert decision.exit_reason == "MA5_BREAK"
    assert decision.exit_price == 95.0

    disabled = ExitManager(
        EXIT_POLICY_MA5_BREAK_PLUS_HWM_8PCT,
        entry_price=100.0,
        enable_ma_break_exits=False,
    )
    disabled.after_close(mark)
    assert disabled.before_open(next_mark) is None


def test_asymmetric_v2_strong_trend_disables_ma_break_only_for_qualified_positions() -> None:
    assert is_asymmetric_v2_strong_trend(10, 0.051)
    assert not is_asymmetric_v2_strong_trend(11, 0.051)
    assert not is_asymmetric_v2_strong_trend(10, 0.05)
    assert not is_asymmetric_v2_strong_trend(None, 0.20)

    mark = pd.Series({"Date": "2026-05-08", "Open": 99.0, "Close": 96.0, "MA5": 97.0})
    next_mark = pd.Series({"Date": "2026-05-11", "Open": 95.0, "Close": 94.0, "MA5": 96.0})

    strong = ExitManager(
        EXIT_POLICY_ASYMMETRIC_V2,
        entry_price=100.0,
        disable_ma_break_for_position=True,
    )
    strong.after_close(mark)
    assert strong.before_open(next_mark) is None

    normal = ExitManager(
        EXIT_POLICY_ASYMMETRIC_V2,
        entry_price=100.0,
        disable_ma_break_for_position=False,
    )
    normal.after_close(mark)
    decision = normal.before_open(next_mark)
    assert decision is not None
    assert decision.exit_reason == "MA5_BREAK"


def test_asymmetric_v2_below_ma5_stop_only_predicate() -> None:
    assert is_asymmetric_v2_below_ma5_stop_only(-0.001)
    assert not is_asymmetric_v2_below_ma5_stop_only(0.0)
    assert not is_asymmetric_v2_below_ma5_stop_only(0.01)
    assert not is_asymmetric_v2_below_ma5_stop_only(None)


def test_asymmetric_v2_group_b_stop_only_predicate() -> None:
    assert is_asymmetric_v2_group_b_stop_only(-0.001)
    assert is_asymmetric_v2_group_b_stop_only(-0.049)
    assert not is_asymmetric_v2_group_b_stop_only(-0.05)
    assert not is_asymmetric_v2_group_b_stop_only(-0.051)
    assert not is_asymmetric_v2_group_b_stop_only(0.0)
    assert not is_asymmetric_v2_group_b_stop_only(None)


def test_regime_exit_variant_resolution_and_routes(monkeypatch) -> None:
    monkeypatch.delenv("REGIME_EXIT_VARIANT", raising=False)
    assert resolve_regime_exit_variant() == REGIME_EXIT_VARIANT_NONE
    assert resolve_regime_exit_variant("off") == REGIME_EXIT_VARIANT_NONE

    monkeypatch.setenv("REGIME_EXIT_VARIANT", REGIME_EXIT_VARIANT_MA20_3PCT)
    assert resolve_regime_exit_variant() == REGIME_EXIT_VARIANT_MA20_3PCT
    assert is_regime_exit_overlay_active(REGIME_EXIT_VARIANT_MA20_3PCT, 0.031, 0.00)
    assert not is_regime_exit_overlay_active(REGIME_EXIT_VARIANT_MA20_3PCT, 0.030, 0.20)
    assert is_regime_exit_overlay_active(REGIME_EXIT_VARIANT_MA20_5PCT, 0.051, 0.00)
    assert is_regime_exit_overlay_active(REGIME_EXIT_VARIANT_20D_RETURN_8PCT, 0.00, 0.081)

    policy, disable_ma, reason = resolve_regime_exit_route(
        REGIME_EXIT_VARIANT_MA20_5PCT,
        base_strong_trend_no_ma5=True,
        twii_price_vs_ma20=0.06,
        twii_return_20d=0.00,
    )
    assert policy == EXIT_POLICY_STOP_ONLY_20D
    assert not disable_ma
    assert reason == "REGIME_MA20_5PCT"

    policy, disable_ma, reason = resolve_regime_exit_route(
        REGIME_EXIT_VARIANT_MA20_5PCT_HWM,
        base_strong_trend_no_ma5=False,
        twii_price_vs_ma20=0.06,
        twii_return_20d=0.00,
    )
    assert policy == EXIT_POLICY_ASYMMETRIC_V2
    assert disable_ma
    assert reason == "REGIME_MA20_5PCT_HWM"

    policy, disable_ma, reason = resolve_regime_exit_route(
        REGIME_EXIT_VARIANT_MA20_5PCT,
        base_strong_trend_no_ma5=True,
        twii_price_vs_ma20=0.01,
        twii_return_20d=0.00,
    )
    assert policy == EXIT_POLICY_ASYMMETRIC_V2
    assert disable_ma
    assert reason == "BASE_STRONG_TREND_NO_MA5"


def test_unified_defaults_to_ma5_exit_policy_after_stoponly_cl3_rollback() -> None:
    assert (
        inspect.signature(unified.refresh_open_positions).parameters["exit_policy_name"].default
        == EXIT_POLICY_MA5_BREAK_PLUS_HWM_8PCT
    )
    assert (
        inspect.signature(unified.sync_unified_portfolio).parameters["exit_policy_name"].default
        == EXIT_POLICY_MA5_BREAK_PLUS_HWM_8PCT
    )


def test_unified_schema_has_narrative_entry_columns(tmp_path) -> None:
    conn = unified.connect_db(str(tmp_path / "portfolio.db"))
    try:
        unified.ensure_schema(conn)
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(unified_positions)").fetchall()}
    finally:
        conn.close()

    assert {
        "entry_tag",
        "narrative_heat_at_entry",
        "two_stage_rank_at_entry",
        "two_stage_rank_status",
        "price_vs_ma20_at_entry",
    } <= cols


def test_legacy_paper_schema_has_narrative_entry_columns(tmp_path) -> None:
    conn = legacy_paper.connect_db(str(tmp_path / "paper.db"))
    try:
        legacy_paper.ensure_schema(conn)
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(portfolio_positions)").fetchall()}
    finally:
        conn.close()

    assert {"entry_tag", "narrative_heat_at_entry"} <= cols


def test_unified_locks_asymmetric_v2_entry_context(tmp_path) -> None:
    signal_path = tmp_path / "unified_signals_2026-05-06.csv"
    pd.DataFrame(
        [
            {
                "prediction_date": "2026-05-06",
                "ticker": "9999",
                "signal_type": "20D_only",
                "target_units": 1,
                "target_weight_ratio": 1.0,
                "close_ref": 100.0,
                "sector": "半導體業",
                "risk_adjusted_return": 0.10,
                "rank_20d": 3,
                "two_stage_rank": 7,
                "price_vs_ma20": 0.08,
            },
            {
                "prediction_date": "2026-05-06",
                "ticker": "8888",
                "signal_type": "20D_only",
                "target_units": 1,
                "target_weight_ratio": 1.0,
                "close_ref": 100.0,
                "sector": "??擃平",
                "risk_adjusted_return": 0.08,
                "rank_20d": 4,
                "price_vs_ma20": 0.06,
            }
        ]
    ).to_csv(signal_path, index=False)

    conn = unified.connect_db(str(tmp_path / "portfolio.db"))
    try:
        unified.ensure_schema(conn)
        stats = unified.SyncStats()
        unified.lock_signal_run(conn, str(signal_path), "test", stats)
        rows = conn.execute(
            """
            SELECT ticker, two_stage_rank_at_entry, two_stage_rank_status, price_vs_ma20_at_entry
            FROM unified_positions
            ORDER BY ticker
            """
        ).fetchall()
    finally:
        conn.close()

    by_ticker = {row["ticker"]: row for row in rows}
    assert by_ticker["9999"]["two_stage_rank_at_entry"] == 7
    assert by_ticker["9999"]["two_stage_rank_status"] == unified.TWO_STAGE_RANK_STATUS_KNOWN
    assert by_ticker["9999"]["price_vs_ma20_at_entry"] == 0.08
    assert by_ticker["8888"]["two_stage_rank_at_entry"] is None
    assert by_ticker["8888"]["two_stage_rank_status"] == unified.TWO_STAGE_RANK_STATUS_UNKNOWN


def test_narrative_driven_position_exits_on_decay_instead_of_ma_break(tmp_path, monkeypatch) -> None:
    daily_dir = tmp_path / "daily"
    daily_dir.mkdir()
    pd.DataFrame(
        [
            ("2026-05-07", 100.0, 101.0, 99.0, 100.0),
            ("2026-05-08", 100.0, 101.0, 99.0, 99.0),
            ("2026-05-11", 100.0, 101.0, 99.0, 98.0),
            ("2026-05-12", 100.0, 101.0, 99.0, 97.0),
            ("2026-05-13", 100.0, 101.0, 99.0, 96.0),
            ("2026-05-14", 101.0, 102.0, 100.0, 101.0),
        ],
        columns=["Date", "Open", "High", "Low", "Close"],
    ).to_csv(daily_dir / "9999.csv", index=False)

    narrative_db = tmp_path / "stock.duckdb"
    con = duckdb.connect(str(narrative_db))
    try:
        con.execute(
            """
            CREATE TABLE daily_ticker_narrative_features (
                date DATE,
                ticker TEXT,
                active_labels TEXT,
                heat_3d REAL,
                heat_14d REAL,
                decay_slope REAL,
                top_label TEXT,
                label_age_days INTEGER,
                article_count_7d INTEGER
            )
            """
        )
        con.execute(
            """
            INSERT INTO daily_ticker_narrative_features VALUES
            ('2026-05-06', '9999', '["AI_THEME"]', 0.50, 0.60, 0.00, 'AI_THEME', 1, 2),
            ('2026-05-13', '9999', '["AI_THEME"]', 0.10, 0.60, -0.40, 'AI_THEME', 6, 1)
            """
        )
    finally:
        con.close()

    monkeypatch.setattr(unified, "DAILY_K_DIR", str(daily_dir))
    monkeypatch.setattr(unified, "NARRATIVE_DB_PATH", str(narrative_db))

    signal_path = tmp_path / "unified_signals_2026-05-06.csv"
    pd.DataFrame(
        [
            {
                "prediction_date": "2026-05-06",
                "ticker": "9999",
                "signal_type": "20D_only",
                "target_units": 1,
                "target_weight_ratio": 1.0,
                "close_ref": 100.0,
                "sector": "測試",
                "risk_adjusted_return": 0.10,
                "rank_20d": 1,
            }
        ]
    ).to_csv(signal_path, index=False)

    conn = unified.connect_db(str(tmp_path / "portfolio.db"))
    try:
        unified.ensure_schema(conn)
        stats = unified.SyncStats()
        unified.lock_signal_run(conn, str(signal_path), "test", stats)
        row = conn.execute("SELECT entry_tag, narrative_heat_at_entry FROM unified_positions").fetchone()
        assert row["entry_tag"] == ENTRY_TAG_NARRATIVE_DRIVEN
        assert row["narrative_heat_at_entry"] == 0.5

        unified.refresh_open_positions(conn, stats, exit_policy_name=EXIT_POLICY_MA5_BREAK_PLUS_HWM_8PCT)
        exit_row = conn.execute(
            "SELECT status, exit_date, exit_reason, days_observed FROM unified_positions"
        ).fetchone()
    finally:
        conn.close()

    assert exit_row["status"] == "closed"
    assert exit_row["exit_date"] == "2026-05-14"
    assert exit_row["exit_reason"] == EXIT_REASON_NARRATIVE_DECAY
    assert exit_row["days_observed"] == 6
