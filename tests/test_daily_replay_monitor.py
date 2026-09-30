from __future__ import annotations

import pandas as pd

from scripts.daily_replay_monitor import _summarize_subset


def test_summarize_subset_handles_empty_frame_without_observation_columns() -> None:
    summary = _summarize_subset(pd.DataFrame())

    assert summary["count"] == 0
    assert summary["observed_count"] == 0
    assert summary["avg_partial_return"] is None
    assert summary["mdd_alert_count"] == 0


def test_summarize_subset_handles_unobserved_positions_without_partial_return() -> None:
    positions = pd.DataFrame(
        [
            {"ticker": "2330", "pred_return_20d": 0.02},
            {"ticker": "2454", "pred_return_20d": 0.04},
        ]
    )

    summary = _summarize_subset(positions)

    assert summary["count"] == 2
    assert summary["observed_count"] == 0
    assert summary["avg_pred_return_20d"] == 0.03
    assert summary["avg_partial_return"] is None
