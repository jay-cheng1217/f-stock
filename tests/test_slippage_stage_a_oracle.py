import math

import pandas as pd

from scripts.slippage_stage_a_oracle import analyze_stage_a, prepare_slippage_frame


def test_stage_a_slippage_oracle_splits_bad_and_good_chases() -> None:
    positions = pd.DataFrame(
        [
            {
                "id": 1,
                "ticker": "1111",
                "target_weight": 0.10,
                "entry_slippage_pct": 0.03,
                "order_type": "INTRADAY_CHASE",
                "realized_return_pct": -0.05,
            },
            {
                "id": 2,
                "ticker": "2222",
                "target_weight": 0.20,
                "entry_slippage_pct": 0.04,
                "order_type": "INTRADAY_CHASE",
                "realized_return_pct": 0.10,
            },
            {
                "id": 3,
                "ticker": "3333",
                "target_weight": 0.10,
                "entry_slippage_pct": -0.01,
                "order_type": "OPEN",
                "realized_return_pct": 0.02,
            },
        ]
    )

    summary, by_order, thresholds = analyze_stage_a(positions, thresholds=(0.02,))

    assert math.isclose(summary["oracle_slippage_only_upper_bound_pct"], 0.011, rel_tol=1e-9)
    assert math.isclose(summary["price_improvement_offset_pct"], 0.001, rel_tol=1e-9)
    assert summary["stage_b_recommendation"] == "ALLOW_STAGE_B_DESIGN"
    row = thresholds.iloc[0]
    assert row["trade_count"] == 2
    assert math.isclose(row["slippage_saved_pct"], 0.011, rel_tol=1e-9)
    assert math.isclose(row["avoided_bad_chase_pct"], 0.005, rel_tol=1e-9)
    assert math.isclose(row["missed_good_chase_pct"], 0.020, rel_tol=1e-9)


def test_prepare_slippage_frame_uses_latest_mark_for_open_positions() -> None:
    positions = pd.DataFrame(
        [
            {
                "id": 1,
                "ticker": "1111",
                "target_weight": 0.10,
                "entry_slippage_pct": 0.02,
                "order_type": "OPEN",
                "realized_return_pct": None,
                "latest_close_return_pct": 0.06,
            },
        ]
    )

    frame = prepare_slippage_frame(positions)

    assert math.isclose(frame.iloc[0]["effective_return_pct"], 0.06, rel_tol=1e-9)
    assert math.isclose(frame.iloc[0]["weighted_effective_return_pct"], 0.006, rel_tol=1e-9)

