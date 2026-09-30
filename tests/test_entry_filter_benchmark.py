from __future__ import annotations

import math

import pandas as pd

from scripts.entry_filter_benchmark import add_simple_benchmark_votes, compute_report


def test_simple_benchmark_votes_are_transparent_and_additive():
    df = pd.DataFrame(
        {
            "ticker": ["2330", "3550", "5475"],
            "price_vs_ma20_20d": [0.05, -0.04, 0.08],
            "price_vs_ma60_20d": [0.12, 0.03, 0.15],
            "price_vs_ma5_20d": [0.01, -0.05, -0.01],
            "beta_60_20d": [0.95, 0.80, 1.60],
            "gap_pct_20d": [0.01, 0.02, 0.08],
            "foreign_cumsum_20d_raw_20d": [1000.0, -500.0, -200.0],
            "penalty_overlay_reason": ["", "foreign_selling;quality", "foreign_selling;gap_risk;quality"],
            "prob_edge": [-0.05, -0.08, -0.20],
        }
    )

    result = add_simple_benchmark_votes(df)

    assert result.loc[0, "benchmark_votes"] == 6
    assert result.loc[0, "benchmark_consensus"] == "strong"
    assert result.loc[1, "benchmark_votes"] == 3
    assert result.loc[1, "benchmark_consensus"] == "watch"
    assert result.loc[2, "benchmark_votes"] == 1
    assert result.loc[2, "benchmark_consensus"] == "reject"


def test_report_fails_when_ml_selected_loses_to_counterfactuals(monkeypatch):
    monkeypatch.setattr(
        "scripts.entry_filter_benchmark._twii_open_to_mark_by_entry",
        lambda candidates, *, db_path: {},
    )
    candidates = pd.DataFrame(
        {
            "signal_date": ["2026-05-25"] * 4,
            "entry_date": ["2026-05-26"] * 4,
            "mark_date": ["2026-05-29"] * 4,
            "ticker": ["1111", "2222", "3333", "4444"],
            "selected_by_ml": [True, True, False, False],
            "benchmark_consensus": ["reject", "reject", "strong", "strong"],
            "benchmark_votes": [1, 2, 6, 5],
            "return_to_mark": [-0.10, -0.05, 0.04, 0.06],
            "pred_return_20d": [0.05, 0.04, 0.01, 0.02],
            "prob_edge": [-0.20, -0.18, -0.02, 0.01],
            "rank_20d": [1, 2, 3, 4],
            "penalty_overlay_reason": ["foreign_selling;gap_risk", "gap_risk", "", ""],
            "has_return": [True] * 4,
        }
    )

    report = compute_report(candidates)

    assert report["status"] == "fail"
    assert "ml_selected_did_not_beat_blocked_counterfactual" in report["failures"]
    assert "ml_selected_did_not_beat_simple_consensus_strong" in report["failures"]
    assert "ml_selected_win_rate_below_45pct" in report["failures"]
    assert "ml_selected_contains_consensus_reject_names" in report["warnings"]
    assert math.isclose(report["selected_minus_blocked"], -0.125, rel_tol=1e-9)
    assert math.isclose(report["selected_minus_simple_consensus_strong"], -0.125, rel_tol=1e-9)


def test_proposed_shadow_guard_uses_edge_or_strong_consensus(monkeypatch):
    monkeypatch.setattr(
        "scripts.entry_filter_benchmark._twii_open_to_mark_by_entry",
        lambda candidates, *, db_path: {},
    )
    candidates = pd.DataFrame(
        {
            "signal_date": ["2026-05-25"] * 5,
            "entry_date": ["2026-05-26"] * 5,
            "mark_date": ["2026-05-29"] * 5,
            "ticker": ["1111", "2222", "3333", "4444", "5555"],
            "selected_by_ml": [True, True, True, True, False],
            "benchmark_consensus": ["reject", "strong", "watch", "strong", "strong"],
            "benchmark_votes": [1, 5, 3, 6, 6],
            "return_to_mark": [-0.10, 0.06, 0.03, -0.02, 0.01],
            "pred_return_20d": [0.05, 0.01, 0.02, 0.03, 0.01],
            "prob_edge": [-0.20, -0.18, -0.08, -0.11, -0.02],
            "rank_20d": [1, 2, 3, 4, 5],
            "penalty_overlay_reason": ["foreign_selling;gap_risk", "", "", "gap_risk", ""],
            "has_return": [True] * 5,
        }
    )

    report = compute_report(candidates)
    proposed = report["proposed_shadow_guard"]

    assert proposed["basket"] == "shadow_guard_prob_edge_or_strong_consensus"
    # Keeps selected rows 2222 and 4444 by strong consensus, and 3333 by edge.
    assert proposed["kept_selected"] == 3
    assert proposed["dropped_selected"] == 1
    assert math.isclose(proposed["avg_return"], (0.06 + 0.03 - 0.02) / 3, rel_tol=1e-9)
    assert proposed["avg_return_improvement_vs_current"] > 0
