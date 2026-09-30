from __future__ import annotations

import pandas as pd

from scripts.build_unified_signals import (
    SIGNAL_TYPE_20D_ONLY,
    SIGNAL_TYPE_NONE,
    TRADABILITY_REASON_ENTRY_BENCHMARK_GUARD,
    TRADABILITY_STATUS_OPEN,
    _apply_entry_benchmark_guard,
)


def _base_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "ticker": "1111",
                "signal_type": SIGNAL_TYPE_20D_ONLY,
                "rank_20d": 1,
                "rank_t1": None,
                "prob_edge": -0.20,
                "price_vs_ma20_20d": -0.03,
                "price_vs_ma60_20d": 0.10,
                "price_vs_ma5_20d": -0.04,
                "beta_60_20d": 1.60,
                "gap_pct_20d": 0.08,
                "foreign_cumsum_20d_raw_20d": -1000.0,
                "penalty_overlay_reason": "foreign_selling;gap_risk;quality",
                "tradability_status": TRADABILITY_STATUS_OPEN,
                "tradability_blocked": False,
                "tradability_reason": "",
            },
            {
                "ticker": "2222",
                "signal_type": SIGNAL_TYPE_20D_ONLY,
                "rank_20d": 2,
                "rank_t1": None,
                "prob_edge": -0.05,
                "price_vs_ma20_20d": -0.03,
                "price_vs_ma60_20d": 0.10,
                "price_vs_ma5_20d": -0.04,
                "beta_60_20d": 1.60,
                "gap_pct_20d": 0.08,
                "foreign_cumsum_20d_raw_20d": -1000.0,
                "penalty_overlay_reason": "foreign_selling;gap_risk;quality",
                "tradability_status": TRADABILITY_STATUS_OPEN,
                "tradability_blocked": False,
                "tradability_reason": "",
            },
            {
                "ticker": "3333",
                "signal_type": SIGNAL_TYPE_20D_ONLY,
                "rank_20d": 3,
                "rank_t1": None,
                "prob_edge": -0.20,
                "price_vs_ma20_20d": 0.04,
                "price_vs_ma60_20d": 0.12,
                "price_vs_ma5_20d": 0.01,
                "beta_60_20d": 0.90,
                "gap_pct_20d": 0.01,
                "foreign_cumsum_20d_raw_20d": 1000.0,
                "penalty_overlay_reason": "",
                "tradability_status": TRADABILITY_STATUS_OPEN,
                "tradability_blocked": False,
                "tradability_reason": "",
            },
        ]
    )


def test_entry_benchmark_guard_blocks_weak_edge_and_weak_consensus(monkeypatch):
    monkeypatch.delenv("ENTRY_BENCHMARK_GUARD_ENABLED", raising=False)
    out = _apply_entry_benchmark_guard(_base_df())
    by_ticker = {row["ticker"]: row for row in out.to_dict("records")}

    assert by_ticker["1111"]["signal_type"] == SIGNAL_TYPE_NONE
    assert bool(by_ticker["1111"]["tradability_blocked"])
    assert TRADABILITY_REASON_ENTRY_BENCHMARK_GUARD in by_ticker["1111"]["tradability_reason"]
    assert by_ticker["1111"]["entry_benchmark_guard_pass"] is False

    assert by_ticker["2222"]["signal_type"] == SIGNAL_TYPE_20D_ONLY
    assert not bool(by_ticker["2222"]["tradability_blocked"])
    assert by_ticker["2222"]["entry_benchmark_edge_vote"] is True

    assert by_ticker["3333"]["signal_type"] == SIGNAL_TYPE_20D_ONLY
    assert not bool(by_ticker["3333"]["tradability_blocked"])
    assert by_ticker["3333"]["entry_benchmark_votes"] == 5
    assert by_ticker["3333"]["entry_benchmark_consensus"] == "strong"
    assert out["entry_benchmark_guard_enforced"].eq(True).all()


def test_entry_benchmark_guard_can_be_disabled(monkeypatch):
    monkeypatch.setenv("ENTRY_BENCHMARK_GUARD_ENABLED", "0")
    out = _apply_entry_benchmark_guard(_base_df())

    assert out["signal_type"].tolist() == [SIGNAL_TYPE_20D_ONLY] * 3
    assert not out["tradability_blocked"].astype(bool).any()
    assert out["entry_benchmark_guard_enabled"].eq(False).all()
    assert out["entry_benchmark_guard_enforced"].eq(False).all()


def test_entry_benchmark_guard_can_run_advisory_without_blocking(monkeypatch):
    monkeypatch.delenv("ENTRY_BENCHMARK_GUARD_ENABLED", raising=False)
    out = _apply_entry_benchmark_guard(_base_df(), enforce=False)

    assert out["signal_type"].tolist() == [SIGNAL_TYPE_20D_ONLY] * 3
    assert not out["tradability_blocked"].astype(bool).any()
    assert out["entry_benchmark_guard_enabled"].eq(True).all()
    assert out["entry_benchmark_guard_enforced"].eq(False).all()
    assert not bool(out.loc[out["ticker"].eq("1111"), "entry_benchmark_guard_pass"].iloc[0])
