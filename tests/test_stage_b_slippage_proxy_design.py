import math

import pandas as pd

from scripts.stage_b_slippage_proxy_design import (
    EX_ANTE_INPUT_COLUMNS,
    FORBIDDEN_PROXY_INPUT_COLUMNS,
    _bootstrap_avoid_miss_delta_ci,
    _threshold_row,
    analyze_stage_b_proxy,
    prepare_stage_b_features,
)


def _sample_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "id": 1,
                "ticker": "1111",
                "entry_date": "2026-05-01",
                "order_type": "INTRADAY_CHASE",
                "target_weight": 0.10,
                "target_units": 2,
                "planned_entry_ref_price": 100.0,
                "price_vs_ma20_at_entry": 0.08,
                "entry_rank_20d": 3,
                "entry_score": 0.20,
                "entry_prob_edge": 0.10,
                "entry_slippage_pct": 0.05,
                "positive_slippage_pct": 0.05,
                "price_improvement_pct": 0.0,
                "weighted_slippage_cost_pct": 0.005,
                "weighted_price_improvement_pct": 0.0,
                "effective_return_pct": -0.04,
                "weighted_effective_return_pct": -0.004,
            },
            {
                "id": 2,
                "ticker": "2222",
                "entry_date": "2026-05-01",
                "order_type": "OPEN",
                "target_weight": 0.10,
                "target_units": 2,
                "planned_entry_ref_price": 80.0,
                "price_vs_ma20_at_entry": -0.02,
                "entry_rank_20d": 5,
                "entry_score": 0.15,
                "entry_prob_edge": 0.02,
                "entry_slippage_pct": -0.01,
                "positive_slippage_pct": 0.0,
                "price_improvement_pct": 0.01,
                "weighted_slippage_cost_pct": 0.0,
                "weighted_price_improvement_pct": 0.001,
                "effective_return_pct": 0.06,
                "weighted_effective_return_pct": 0.006,
            },
            {
                "id": 3,
                "ticker": "6419",
                "entry_date": "2026-05-12",
                "order_type": "INTRADAY_CHASE",
                "target_weight": 0.12,
                "target_units": 2,
                "planned_entry_ref_price": 137.0,
                "price_vs_ma20_at_entry": -0.003,
                "entry_rank_20d": 3,
                "entry_score": 0.04,
                "entry_prob_edge": 0.09,
                "entry_slippage_pct": 0.0985,
                "positive_slippage_pct": 0.0985,
                "price_improvement_pct": 0.0,
                "weighted_slippage_cost_pct": 0.01182,
                "weighted_price_improvement_pct": 0.0,
                "effective_return_pct": 0.29,
                "weighted_effective_return_pct": 0.0348,
            },
        ]
    )


def test_ex_ante_inputs_exclude_forbidden_post_entry_fields() -> None:
    assert EX_ANTE_INPUT_COLUMNS.isdisjoint(FORBIDDEN_PROXY_INPUT_COLUMNS)
    assert "entry_slippage_pct" not in EX_ANTE_INPUT_COLUMNS
    assert "b4_dominance_flag" not in EX_ANTE_INPUT_COLUMNS
    assert "cross_window_sign_stability" not in EX_ANTE_INPUT_COLUMNS


def test_prepare_stage_b_features_adds_proxy_scores_and_liquidity_bucket() -> None:
    scored = prepare_stage_b_features(_sample_frame())

    assert "chase_intensity_proxy" in scored.columns
    assert "hybrid_rule_proxy" in scored.columns
    assert "liquidity_bucket_proxy" in scored.columns
    assert scored.loc[scored["ticker"] == "1111", "chase_intensity_proxy"].iloc[0] > scored.loc[
        scored["ticker"] == "2222", "chase_intensity_proxy"
    ].iloc[0]


def test_threshold_row_reports_avoided_bad_and_missed_good_separately() -> None:
    scored = prepare_stage_b_features(_sample_frame())
    row = _threshold_row(scored, "chase_intensity_proxy", 0.60)

    assert row["trade_count"] >= 1
    assert row["slippage_saved_pct"] > 0
    assert row["avoided_bad_chase_pct"] >= 0
    assert row["missed_good_chase_pct"] >= 0
    assert math.isclose(
        row["avoid_miss_delta_pct"],
        row["avoided_bad_chase_pct"] - row["missed_good_chase_pct"],
        rel_tol=1e-12,
    )
    assert math.isclose(
        row["net_skip_oracle_pct"],
        row["slippage_saved_pct"] + row["avoided_bad_chase_pct"] - row["missed_good_chase_pct"],
        rel_tol=1e-12,
    )


def test_bootstrap_avoid_miss_delta_ci_reports_positive_lower_when_clear() -> None:
    scored = prepare_stage_b_features(
        pd.DataFrame(
            [
                {
                    "ticker": f"A{i:02d}",
                    "entry_date": "2026-05-01",
                    "order_type": "INTRADAY_CHASE",
                    "target_weight": 0.10,
                    "target_units": 2,
                    "planned_entry_ref_price": 100.0,
                    "price_vs_ma20_at_entry": 0.10,
                    "entry_score": 0.20,
                    "entry_prob_edge": 0.10,
                    "entry_slippage_pct": 0.05,
                    "weighted_slippage_cost_pct": 0.005,
                    "effective_return_pct": -0.04,
                    "weighted_effective_return_pct": -0.004,
                }
                for i in range(12)
            ]
            + [
                {
                    "ticker": f"B{i:02d}",
                    "entry_date": "2026-05-01",
                    "order_type": "OPEN",
                    "target_weight": 0.01,
                    "target_units": 1,
                    "planned_entry_ref_price": 20.0,
                    "price_vs_ma20_at_entry": -0.02,
                    "entry_score": 0.01,
                    "entry_prob_edge": 0.00,
                    "entry_slippage_pct": -0.01,
                    "weighted_slippage_cost_pct": 0.0,
                    "effective_return_pct": 0.00,
                    "weighted_effective_return_pct": 0.0,
                }
                for i in range(12)
            ]
        )
    )
    cutoff = float(scored["chase_intensity_proxy"].quantile(0.60))

    ci = _bootstrap_avoid_miss_delta_ci(scored, "chase_intensity_proxy", cutoff, rounds=100)

    assert ci["avoid_miss_bootstrap_90_lower_pct"] > 0


def test_analyze_stage_b_proxy_includes_6419_case_and_rd006_boundary() -> None:
    results = analyze_stage_b_proxy(_sample_frame())

    assert results["summary"]["production_change"] is False
    assert "NAV/B4" in results["summary"]["rd006_boundary"]
    assert "pm_condition_1" in results["summary"]
    assert results["case_6419"]
    assert {row["candidate"] for row in results["case_6419"]} >= {"chase_intensity_proxy", "hybrid_rule_proxy"}
