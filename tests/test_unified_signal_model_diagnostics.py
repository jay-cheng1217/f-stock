from __future__ import annotations

import pandas as pd

from scripts.build_unified_signals import (
    SIGNAL_TYPE_20D_ONLY,
    _attach_public_market_context_metadata,
    _attach_selection_model_diagnostics,
)


def test_unified_signal_diagnostics_mark_tquant_override(monkeypatch):
    def fake_quality(**kwargs):
        return {
            "status": "ok",
            "generated_at": "2026-06-03T08:00:00",
            "preflight_pass": True,
            "selection_usage_mode": "advisory_metadata_only",
            "model_feature_readiness": "not_ready",
            "model_feature_gap": "latest_snapshot_only_no_point_in_time_training_panel",
            "required_failures": [],
            "stale_datasets": [],
            "missing_field_datasets": [],
        }

    monkeypatch.setattr("scripts.build_unified_signals.evaluate_public_market_context", fake_quality)
    frame = pd.DataFrame(
        [
            {
                "ticker": "2451",
                "signal_type": SIGNAL_TYPE_20D_ONLY,
                "rank_20d": 1,
                "rank_t1": pd.NA,
                "prob_edge": -0.18,
                "entry_benchmark_prob_edge_floor": -0.10,
                "entry_benchmark_guard_pass": "False",
                "tquant_reference_gate_enabled": "True",
                "tquant_reference_gate_pass": "True",
            }
        ]
    )

    out = _attach_public_market_context_metadata(frame, prediction_date="2026-06-01")
    out = _attach_selection_model_diagnostics(out)
    row = out.iloc[0]

    assert row["public_market_context_status"] == "ok"
    assert row["public_market_context_time_basis"] == "latest_snapshot_not_point_in_time"
    assert row["selection_model_primary_layer"] == "tquant_reference_gate"
    assert row["selection_model_alignment"] == "tquant_override_entry_benchmark"
    assert bool(row["selection_model_weak_ml_edge_under_tquant"])
    assert "weak_ml_edge_under_tquant" in row["selection_model_data_gap"]
    assert "entry benchmark remains advisory" in row["selection_model_reason"]
