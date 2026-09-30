from __future__ import annotations

import sqlite3

import pandas as pd
import scripts.agent_arena as arena

from scripts.agent_arena import (
    AGENTS,
    MAX_BUY_OPEN_GAP_PCT,
    OPEN_FILL_SLIPPAGE_PCT,
    STARTING_CAPITAL,
    _admission_verdict,
    _build_sizing_plan,
    _connect_arena,
    _exit_policy_explanation_zh,
    _exit_reason_zh,
    _execution_policy_config,
    _execution_policy_for_spec,
    _fill_pending_orders_at_open,
    _lookup_stock_name,
    _lot_summary,
    _open_fill_price,
    _open_gap_pct,
    _quantize_tw_lot_trade,
    _queue_pending_order,
    _sizing_policy_for_spec,
    _write_backtests,
    backfill_agent_competition,
    export_latest_status,
    score_agent_candidates,
)


def test_admission_requires_positive_backtest() -> None:
    status, reason = _admission_verdict(10.0, 0.3, 120, -25.0)
    assert status == "ADMITTED"
    assert "profitable" in reason

    status, reason = _admission_verdict(-1.0, 0.3, 120, -25.0)
    assert status == "BENCH_FAILED"
    assert "total return" in reason


def test_score_agent_candidates_sorts_without_future_columns() -> None:
    spec = next(item for item in AGENTS if item.agent_id == "dual_ma_trend")
    df = pd.DataFrame(
        [
            {
                "ticker": "1111",
                "avg_turnover_20m": 30,
                "close_px": 50,
                "volume": 200_000,
                "price_vs_ma20": 0.08,
                "price_vs_ma60": 0.18,
                "ret_20": 0.12,
                "ret_60": 0.25,
                "macd_hist": 1.0,
            },
            {
                "ticker": "2222",
                "avg_turnover_20m": 30,
                "close_px": 50,
                "volume": 200_000,
                "price_vs_ma20": 0.01,
                "price_vs_ma60": 0.02,
                "ret_20": 0.03,
                "ret_60": 0.05,
                "macd_hist": 0.1,
            },
        ]
    )

    scored = score_agent_candidates(df, spec)

    assert scored["ticker"].tolist() == ["1111", "2222"]
    assert "future_return_20" not in scored.columns
    assert scored["reason"].notna().all()


def test_consensus_pullback_addon_requires_cross_agent_consensus(monkeypatch) -> None:
    spec = next(item for item in AGENTS if item.agent_id == "consensus_pullback_addon")
    base_specs = tuple(
        item
        for item in AGENTS
        if item.agent_id in {"dual_ma_trend", "volume_breakout", "risk_parity_momentum"}
    )
    monkeypatch.setattr(arena, "AGENTS", (*base_specs, spec))
    df = pd.DataFrame(
        [
            {
                "ticker": "1111",
                "avg_turnover_20m": 80,
                "close_px": 50,
                "volume": 250_000,
                "price_vs_ma20": -0.035,
                "price_vs_ma60": 0.055,
                "ret_10": 0.015,
                "ret_20": 0.040,
                "ret_60": 0.120,
                "vol_20": 0.026,
                "vol_60": 0.030,
                "vol_ratio": 1.20,
                "close_to_high_60": -0.045,
                "inst_5_norm": 0.002,
                "macd_hist": 0.8,
            },
            {
                "ticker": "2222",
                "avg_turnover_20m": 80,
                "close_px": 50,
                "volume": 250_000,
                "price_vs_ma20": -0.115,
                "price_vs_ma60": -0.085,
                "ret_10": -0.090,
                "ret_20": -0.130,
                "ret_60": -0.050,
                "vol_20": 0.045,
                "vol_60": 0.050,
                "vol_ratio": 1.40,
                "close_to_high_60": -0.240,
                "inst_5_norm": -0.004,
                "macd_hist": -0.2,
            },
            {
                "ticker": "3333",
                "avg_turnover_20m": 80,
                "close_px": 50,
                "volume": 250_000,
                "price_vs_ma20": 0.065,
                "price_vs_ma60": 0.120,
                "ret_10": 0.040,
                "ret_20": 0.090,
                "ret_60": 0.200,
                "vol_20": 0.080,
                "vol_60": 0.030,
                "vol_ratio": 1.50,
                "close_to_high_60": 0.000,
                "inst_5_norm": 0.002,
                "macd_hist": 0.9,
            },
        ]
    )

    scored = score_agent_candidates(df, spec)

    assert scored["ticker"].tolist() == ["1111"]
    assert scored.iloc[0]["meta_votes"] == 3
    assert scored.iloc[0]["tdcc_votes"] == 0
    assert scored.iloc[0]["consensus_total_votes"] == 3
    assert "cross-agent meta consensus" in scored.iloc[0]["reason"]


def test_consensus_pullback_addon_allows_two_models_plus_tdcc_vote(monkeypatch) -> None:
    spec = next(item for item in AGENTS if item.agent_id == "consensus_pullback_addon")
    base_specs = tuple(
        item
        for item in AGENTS
        if item.agent_id in {"dual_ma_trend", "volume_breakout"}
    )
    monkeypatch.setattr(arena, "AGENTS", (*base_specs, spec))
    base_row = {
        "ticker": "1111",
        "avg_turnover_20m": 80,
        "close_px": 50,
        "volume": 250_000,
        "price_vs_ma20": 0.025,
        "price_vs_ma60": 0.055,
        "ret_10": 0.015,
        "ret_20": 0.040,
        "ret_60": 0.120,
        "vol_20": 0.026,
        "vol_60": 0.030,
        "vol_ratio": 1.20,
        "close_to_high_60": -0.045,
        "inst_5_norm": 0.002,
        "macd_hist": 0.8,
    }

    no_tdcc = score_agent_candidates(pd.DataFrame([base_row]), spec)

    with_tdcc = score_agent_candidates(
        pd.DataFrame(
            [
                {
                    **base_row,
                    "whale_pct_chg": 0.45,
                    "retail_pct_chg": -0.30,
                    "whale_trend_4w": 0.18,
                    "whale_trend_8w": 0.11,
                    "whale_acc_weeks": 3,
                    "whale_retail_diverge": 0.75,
                }
            ]
        ),
        spec,
    )

    assert no_tdcc.empty
    assert with_tdcc["ticker"].tolist() == ["1111"]
    assert with_tdcc.iloc[0]["meta_votes"] == 2
    assert with_tdcc.iloc[0]["tdcc_votes"] == 1
    assert with_tdcc.iloc[0]["consensus_total_votes"] == 3
    assert "TDCC vote" in with_tdcc.iloc[0]["reason"]


def test_open_fill_helpers_apply_gap_and_slippage() -> None:
    gap = _open_gap_pct(102.5, 100.0)
    assert round(gap or 0.0, 4) == 2.5
    assert gap > MAX_BUY_OPEN_GAP_PCT

    buy_fill = _open_fill_price(100.0, "BUY")
    sell_fill = _open_fill_price(100.0, "SELL")
    assert round(buy_fill, 6) == round(100.0 * (1.0 + OPEN_FILL_SLIPPAGE_PCT / 100.0), 6)
    assert round(sell_fill, 6) == round(100.0 * (1.0 - OPEN_FILL_SLIPPAGE_PCT / 100.0), 6)


def test_agent_sizing_policies_are_strategy_specific() -> None:
    policies = {_sizing_policy_for_spec(spec) for spec in AGENTS}

    assert len(policies) >= 8
    assert _sizing_policy_for_spec(next(item for item in AGENTS if item.agent_id == "high_beta_momentum")) == "high_beta_capped"
    assert _sizing_policy_for_spec(next(item for item in AGENTS if item.agent_id == "risk_parity_momentum")) == "inverse_volatility"
    assert _sizing_policy_for_spec(next(item for item in AGENTS if item.agent_id == "consensus_pullback_addon")) == "meta_consensus_scaled"


def test_agent_execution_policies_are_strategy_specific() -> None:
    assert _execution_policy_for_spec(next(item for item in AGENTS if item.agent_id == "low_vol_quality_proxy")) == "conservative_open_confirm"
    assert _execution_policy_for_spec(next(item for item in AGENTS if item.agent_id == "high_beta_momentum")) == "momentum_relaxed_open"
    assert _execution_policy_for_spec(next(item for item in AGENTS if item.agent_id == "consensus_pullback_addon")) == "consensus_relaxed_open"
    assert _execution_policy_config("momentum_relaxed_open")["max_buy_open_gap_pct"] == 3.5


def test_sizing_plan_varies_notional_by_policy() -> None:
    spec = next(item for item in AGENTS if item.agent_id == "risk_parity_momentum")
    rows = pd.DataFrame(
        [
            {
                "ticker": "1111",
                "score": 2.0,
                "vol_20": 0.012,
                "ret_20": 0.08,
                "price_vs_ma20": 0.03,
                "price_vs_ma60": 0.08,
                "inst_5_norm": 0.001,
                "close_to_high_60": 0.02,
                "vol_ratio": 1.1,
            },
            {
                "ticker": "2222",
                "score": 2.0,
                "vol_20": 0.045,
                "ret_20": 0.08,
                "price_vs_ma20": 0.03,
                "price_vs_ma60": 0.08,
                "inst_5_norm": 0.001,
                "close_to_high_60": 0.02,
                "vol_ratio": 1.1,
            },
        ]
    )

    plan = _build_sizing_plan(spec, rows, 60_000.0)

    assert len(plan) == 2
    assert plan[0]["sizing_policy"] == "inverse_volatility"
    assert plan[0]["target_notional"] > plan[1]["target_notional"]
    assert "inverse vol_20" in plan[0]["sizing_reason"]


def test_tw_lot_quantization_prefers_board_lots_then_odd_lots() -> None:
    board_lot = _quantize_tw_lot_trade(120_000.0, 50.0)
    odd_lot = _quantize_tw_lot_trade(10_000.0, 80.0)

    assert board_lot["board_lots"] == 2
    assert board_lot["odd_lot_shares"] == 0
    assert board_lot["board_lot_mode"] == "board_lot"
    assert odd_lot["board_lots"] == 0
    assert odd_lot["odd_lot_shares"] == 125
    assert odd_lot["board_lot_mode"] == "odd_lot_fallback"


def test_legacy_fractional_lot_summary_uses_tw_lot_language() -> None:
    summary = _lot_summary({"shares": 1028.16})

    assert summary["board_lots"] == 1
    assert summary["odd_lot_shares"] == 28
    assert summary["board_lot_mode"] == "legacy_fractional"
    assert summary["lot_summary"] == "1張 + 28股 legacy"


def test_agent_detail_supports_stock_names_and_zh_exit_rules() -> None:
    assert _lookup_stock_name("1101") == "台泥"
    assert "硬停損" in _exit_policy_explanation_zh("flow_decay", 10)
    assert "法人流向轉弱" in _exit_policy_explanation_zh("flow_decay", 10)
    assert _exit_reason_zh("flow_decay:flow_reversal") == "籌碼反轉：法人流向轉弱。"


def test_pending_buy_skips_open_high_close_low(tmp_path) -> None:
    db_path = tmp_path / "arena.db"
    spec = AGENTS[0]
    conn = _connect_arena(db_path)
    try:
        queued = _queue_pending_order(
            conn,
            agent_id=spec.agent_id,
            signal_date="2026-06-01",
            side="BUY",
            ticker="1234",
            target_notional=10_000.0,
            signal_close=100.0,
            signal_score=1.0,
            signal_reason="unit-test",
            exit_policy=spec.exit_policy,
        )
        assert queued
        cash, closed, filled, skipped = _fill_pending_orders_at_open(
            conn,
            spec=spec,
            as_of_date="2026-06-02",
            latest_rows={"1234": pd.Series({"open_px": 101.0, "close_px": 99.0})},
            cash=STARTING_CAPITAL,
        )

        assert cash == STARTING_CAPITAL
        assert closed == 0
        assert filled == 0
        assert skipped == 1
        order = conn.execute("SELECT status, skipped_reason FROM arena_pending_orders").fetchone()
        assert order["status"] == "SKIPPED"
        assert order["skipped_reason"] == "open_fade_no_follow_through"
        assert conn.execute("SELECT COUNT(*) FROM arena_positions").fetchone()[0] == 0
    finally:
        conn.close()


def test_conservative_execution_skips_open_fade_even_above_signal(tmp_path) -> None:
    db_path = tmp_path / "arena.db"
    spec = next(item for item in AGENTS if item.agent_id == "low_vol_quality_proxy")
    conn = _connect_arena(db_path)
    try:
        _queue_pending_order(
            conn,
            agent_id=spec.agent_id,
            signal_date="2026-06-01",
            side="BUY",
            ticker="1234",
            target_notional=10_000.0,
            signal_close=100.0,
            signal_score=1.0,
            signal_reason="unit-test",
            exit_policy=spec.exit_policy,
            max_open_gap_pct=float(_execution_policy_config(_execution_policy_for_spec(spec))["max_buy_open_gap_pct"]),
        )
        _cash, _closed, filled, skipped = _fill_pending_orders_at_open(
            conn,
            spec=spec,
            as_of_date="2026-06-02",
            latest_rows={"1234": pd.Series({"open_px": 101.0, "close_px": 100.5})},
            cash=STARTING_CAPITAL,
        )

        assert filled == 0
        assert skipped == 1
        order = conn.execute("SELECT status, skipped_reason FROM arena_pending_orders").fetchone()
        assert order["status"] == "SKIPPED"
        assert order["skipped_reason"] == "open_fade_no_follow_through"
    finally:
        conn.close()


def test_relaxed_execution_allows_partial_open_fade_above_signal(tmp_path) -> None:
    db_path = tmp_path / "arena.db"
    spec = next(item for item in AGENTS if item.agent_id == "consensus_pullback_addon")
    policy_config = _execution_policy_config(_execution_policy_for_spec(spec))
    conn = _connect_arena(db_path)
    try:
        _queue_pending_order(
            conn,
            agent_id=spec.agent_id,
            signal_date="2026-06-01",
            side="BUY",
            ticker="1234",
            target_notional=20_000.0,
            signal_close=100.0,
            signal_score=1.0,
            signal_reason="unit-test",
            exit_policy=spec.exit_policy,
            max_open_gap_pct=float(policy_config["max_buy_open_gap_pct"]),
        )
        _cash, _closed, filled, skipped = _fill_pending_orders_at_open(
            conn,
            spec=spec,
            as_of_date="2026-06-02",
            latest_rows={"1234": pd.Series({"open_px": 103.0, "close_px": 101.0})},
            cash=STARTING_CAPITAL,
        )

        assert filled == 0
        assert skipped == 0
        order = conn.execute("SELECT status, target_notional, skipped_reason FROM arena_pending_orders").fetchone()
        assert order["status"] == "READY_OPEN"
        assert round(float(order["target_notional"]), 2) == 10_000.0
        assert "partial_open_fade_0.50x" in order["skipped_reason"]
    finally:
        conn.close()


def test_pending_buy_requires_follow_through_before_next_open_fill(tmp_path) -> None:
    db_path = tmp_path / "arena.db"
    spec = AGENTS[0]
    conn = _connect_arena(db_path)
    try:
        _queue_pending_order(
            conn,
            agent_id=spec.agent_id,
            signal_date="2026-06-01",
            side="BUY",
            ticker="1234",
            target_notional=10_000.0,
            signal_close=100.0,
            signal_score=1.0,
            signal_reason="unit-test",
            exit_policy=spec.exit_policy,
        )
        cash, _closed, filled, skipped = _fill_pending_orders_at_open(
            conn,
            spec=spec,
            as_of_date="2026-06-02",
            latest_rows={"1234": pd.Series({"open_px": 100.5, "close_px": 102.0})},
            cash=STARTING_CAPITAL,
        )

        assert filled == 0
        assert skipped == 0
        order = conn.execute("SELECT status, confirmed_date, confirmed_close FROM arena_pending_orders").fetchone()
        assert order["status"] == "READY_OPEN"
        assert order["confirmed_date"] == "2026-06-02"
        assert order["confirmed_close"] == 102.0
        assert conn.execute("SELECT COUNT(*) FROM arena_positions").fetchone()[0] == 0

        cash, _closed, filled, skipped = _fill_pending_orders_at_open(
            conn,
            spec=spec,
            as_of_date="2026-06-03",
            latest_rows={"1234": pd.Series({"open_px": 102.4, "close_px": 103.0})},
            cash=cash,
        )

        assert filled == 1
        assert skipped == 0
        order = conn.execute("SELECT status, filled_date FROM arena_pending_orders").fetchone()
        assert order["status"] == "FILLED"
        assert order["filled_date"] == "2026-06-03"
        assert conn.execute("SELECT COUNT(*) FROM arena_positions WHERE status='OPEN'").fetchone()[0] == 1
    finally:
        conn.close()


def test_pending_buy_records_tw_lot_sizing_metadata(tmp_path) -> None:
    db_path = tmp_path / "arena.db"
    spec = AGENTS[0]
    conn = _connect_arena(db_path)
    try:
        _queue_pending_order(
            conn,
            agent_id=spec.agent_id,
            signal_date="2026-06-01",
            side="BUY",
            ticker="1234",
            target_notional=120_000.0,
            target_weight=0.4,
            sizing_policy="conviction_momentum",
            sizing_reason="unit-test sizing",
            board_lot_mode="pending_open_quantize",
            signal_close=50.0,
            signal_score=1.0,
            signal_reason="unit-test",
            exit_policy=spec.exit_policy,
        )
        _fill_pending_orders_at_open(
            conn,
            spec=spec,
            as_of_date="2026-06-02",
            latest_rows={"1234": pd.Series({"open_px": 50.0, "close_px": 51.0})},
            cash=STARTING_CAPITAL,
        )
        cash, _closed, filled, skipped = _fill_pending_orders_at_open(
            conn,
            spec=spec,
            as_of_date="2026-06-03",
            latest_rows={"1234": pd.Series({"open_px": 50.0, "close_px": 51.0})},
            cash=STARTING_CAPITAL,
        )

        assert filled == 1
        assert skipped == 0
        assert cash < STARTING_CAPITAL
        position = conn.execute("SELECT * FROM arena_positions WHERE status='OPEN'").fetchone()
        assert position["board_lots"] == 2
        assert position["odd_lot_shares"] == 0
        assert position["board_lot_mode"] == "board_lot"
        assert position["sizing_policy"] == "conviction_momentum"
        assert position["sizing_reason"] == "unit-test sizing"
        assert _lot_summary(position)["lot_summary"] == "2張"
    finally:
        conn.close()


def test_pending_buy_uses_odd_lot_fallback_below_one_board_lot(tmp_path) -> None:
    db_path = tmp_path / "arena.db"
    spec = AGENTS[0]
    conn = _connect_arena(db_path)
    try:
        _queue_pending_order(
            conn,
            agent_id=spec.agent_id,
            signal_date="2026-06-01",
            side="BUY",
            ticker="1234",
            target_notional=10_000.0,
            target_weight=0.033,
            sizing_policy="rebound_probe",
            sizing_reason="unit-test odd lot",
            signal_close=80.0,
            signal_score=1.0,
            signal_reason="unit-test",
            exit_policy=spec.exit_policy,
        )
        _fill_pending_orders_at_open(
            conn,
            spec=spec,
            as_of_date="2026-06-02",
            latest_rows={"1234": pd.Series({"open_px": 80.0, "close_px": 81.0})},
            cash=STARTING_CAPITAL,
        )
        _cash, _closed, filled, skipped = _fill_pending_orders_at_open(
            conn,
            spec=spec,
            as_of_date="2026-06-03",
            latest_rows={"1234": pd.Series({"open_px": 80.0, "close_px": 81.0})},
            cash=STARTING_CAPITAL,
        )

        assert filled == 1
        assert skipped == 0
        position = conn.execute("SELECT * FROM arena_positions WHERE status='OPEN'").fetchone()
        assert position["board_lots"] == 0
        assert 0 < position["odd_lot_shares"] < 1000
        assert position["board_lot_mode"] == "odd_lot_fallback"
        assert _lot_summary(position)["lot_summary"].endswith("股")
    finally:
        conn.close()


def test_backfill_agent_competition_replays_missing_live_days(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "arena.db"
    monkeypatch.setattr(arena, "LATEST_JSON_PATH", tmp_path / "agent_arena_latest.json")
    spec = AGENTS[0]
    other = AGENTS[1]
    conn = _connect_arena(db_path)
    try:
        _write_backtests(
            conn,
            [
                {
                    "agent_id": spec.agent_id,
                    "name": spec.name,
                    "style": spec.style,
                    "source_family": spec.source_family,
                    "source_repos": spec.source_repos,
                    "thesis": spec.thesis,
                    "exit_policy": spec.exit_policy,
                    "sizing_policy": _sizing_policy_for_spec(spec),
                    "admission_status": "ADMITTED",
                    "admission_reason": "unit test",
                    "total_return_pct": 10.0,
                    "max_drawdown_pct": -5.0,
                    "trade_count": 100,
                    "ending_equity": STARTING_CAPITAL * 1.1,
                }
            ],
        )
        for dt in ("2026-06-02", "2026-06-03", "2026-06-04"):
            conn.execute(
                """
                INSERT INTO arena_daily_equity(
                    agent_id, dt, cash, market_value, equity, daily_return_pct,
                    open_positions, closed_positions, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (other.agent_id, dt, STARTING_CAPITAL, 0.0, STARTING_CAPITAL, 0.0, 0, 0, "unit"),
            )
        conn.commit()
    finally:
        conn.close()

    feature_df = pd.DataFrame(
        [
            {
                "ticker": "1234",
                "dt": pd.Timestamp("2026-06-02"),
                "open_px": 100.0,
                "close_px": 100.0,
                "volume": 200_000,
                "ret_5": 0.02,
                "ret_10": 0.03,
                "ret_20": 0.04,
                "ret_60": 0.08,
                "vol_20": 0.02,
                "vol_60": 0.025,
                "vol_ratio": 1.2,
                "close_to_high_60": -0.02,
                "price_vs_ma20": 0.01,
                "price_vs_ma60": 0.05,
                "inst_5_norm": 0.001,
                "rsi14": 55.0,
                "next_date": pd.Timestamp("2026-06-03"),
            },
            {
                "ticker": "1234",
                "dt": pd.Timestamp("2026-06-03"),
                "open_px": 100.0,
                "close_px": 101.0,
                "volume": 200_000,
                "ret_5": 0.02,
                "ret_10": 0.03,
                "ret_20": 0.04,
                "ret_60": 0.08,
                "vol_20": 0.02,
                "vol_60": 0.025,
                "vol_ratio": 1.2,
                "close_to_high_60": -0.02,
                "price_vs_ma20": 0.01,
                "price_vs_ma60": 0.05,
                "inst_5_norm": 0.001,
                "rsi14": 55.0,
                "next_date": pd.Timestamp("2026-06-04"),
            },
            {
                "ticker": "1234",
                "dt": pd.Timestamp("2026-06-04"),
                "open_px": 101.0,
                "close_px": 102.0,
                "volume": 200_000,
                "ret_5": 0.02,
                "ret_10": 0.03,
                "ret_20": 0.04,
                "ret_60": 0.08,
                "vol_20": 0.02,
                "vol_60": 0.025,
                "vol_ratio": 1.2,
                "close_to_high_60": -0.02,
                "price_vs_ma20": 0.01,
                "price_vs_ma60": 0.05,
                "inst_5_norm": 0.001,
                "rsi14": 55.0,
                "next_date": pd.NaT,
            },
        ]
    )

    monkeypatch.setattr(arena, "_load_feature_frame", lambda **_kwargs: feature_df.copy())
    monkeypatch.setattr(
        arena,
        "score_agent_candidates",
        lambda df, _spec: df.assign(score=1.0, reason="unit backfill").sort_values("ticker"),
    )

    payload = backfill_agent_competition(spec.agent_id, db_path=db_path, duckdb_path=tmp_path / "unused.duckdb")

    assert payload["backfill"]["replayed_days"] == 3
    conn = _connect_arena(db_path)
    try:
        daily_rows = conn.execute(
            "SELECT dt, open_positions FROM arena_daily_equity WHERE agent_id=? ORDER BY dt",
            (spec.agent_id,),
        ).fetchall()
        assert [row["dt"] for row in daily_rows] == ["2026-06-02", "2026-06-03", "2026-06-04"]
        assert daily_rows[-1]["open_positions"] == 1
        statuses = [
            row["status"]
            for row in conn.execute(
                "SELECT status FROM arena_pending_orders WHERE agent_id=? ORDER BY id",
                (spec.agent_id,),
            ).fetchall()
        ]
        assert "FILLED" in statuses
    finally:
        conn.close()


def test_export_latest_status_handles_backtests_without_daily_rows(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(arena, "LATEST_JSON_PATH", tmp_path / "agent_arena_latest.json")
    db_path = tmp_path / "arena.db"
    conn = _connect_arena(db_path)
    try:
        _write_backtests(
            conn,
            [
                {
                    "agent_id": AGENTS[0].agent_id,
                    "name": AGENTS[0].name,
                    "style": AGENTS[0].style,
                    "source_family": AGENTS[0].source_family,
                    "source_repos": AGENTS[0].source_repos,
                    "thesis": AGENTS[0].thesis,
                    "admission_status": "ADMITTED",
                    "admission_reason": "ok",
                    "total_return_pct": 12.3,
                    "max_drawdown_pct": -8.0,
                    "trade_count": 100,
                    "ending_equity": STARTING_CAPITAL * 1.123,
                }
            ],
        )
    finally:
        conn.close()

    payload = export_latest_status(db_path=db_path)

    assert payload["status"] == "ok"
    assert payload["standings"] == []
    assert payload["backtests"][0]["admission_status"] == "ADMITTED"
    assert payload["watchlist_count"] == len(AGENTS) - 1
    assert payload["source_library_count"] > 60
    assert any(item["status"] == "CONVERTED" for item in payload["source_library"])
    assert payload["pending_orders"] == []
    assert payload["order_events"] == []

    with sqlite3.connect(db_path) as check_conn:
        assert check_conn.execute("SELECT COUNT(*) FROM agent_backtests").fetchone()[0] == 1


def test_export_latest_status_reranks_standings_by_latest_paper_equity(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(arena, "LATEST_JSON_PATH", tmp_path / "agent_arena_latest.json")
    db_path = tmp_path / "arena.db"
    specs = AGENTS[:3]
    conn = _connect_arena(db_path)
    try:
        _write_backtests(
            conn,
            [
                {
                    "agent_id": spec.agent_id,
                    "name": spec.name,
                    "style": spec.style,
                    "source_family": spec.source_family,
                    "source_repos": spec.source_repos,
                    "thesis": spec.thesis,
                    "admission_status": "ADMITTED",
                    "admission_reason": "ok",
                    "total_return_pct": 10.0 + index,
                    "max_drawdown_pct": -10.0,
                    "trade_count": 100,
                    "ending_equity": STARTING_CAPITAL * (1.1 + index / 100.0),
                }
                for index, spec in enumerate(specs)
            ],
        )
        rows = [
            (specs[0].agent_id, 305_000.0, 0.10),
            (specs[1].agent_id, 299_000.0, 2.00),
            (specs[2].agent_id, 310_000.0, -1.00),
        ]
        for agent_id, equity, daily_return_pct in rows:
            conn.execute(
                """
                INSERT INTO arena_daily_equity(
                    agent_id, dt, cash, market_value, equity, daily_return_pct,
                    open_positions, closed_positions, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    agent_id,
                    "2026-06-03",
                    equity,
                    0.0,
                    equity,
                    daily_return_pct,
                    0,
                    0,
                    "2026-06-03T00:00:00Z",
                ),
            )
        conn.commit()
    finally:
        conn.close()

    payload = export_latest_status(db_path=db_path)

    standings = payload["standings"]
    assert [row["agent_id"] for row in standings] == [
        specs[2].agent_id,
        specs[0].agent_id,
        specs[1].agent_id,
    ]
    assert [row["competition_rank"] for row in standings] == [1, 2, 3]
    assert standings[0]["rank_basis"] == "latest_paper_equity_desc"
    daily_ranks = {row["agent_id"]: row["daily_return_rank"] for row in standings}
    assert daily_ranks == {
        specs[1].agent_id: 1,
        specs[0].agent_id: 2,
        specs[2].agent_id: 3,
    }
    assert payload["leader"]["agent_id"] == specs[2].agent_id
