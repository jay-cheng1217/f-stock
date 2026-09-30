from scripts.signal_explainability import explain_signal_row


def test_unified_active_signal_gets_entry_ready_with_warnings():
    row = {
        "signal_type": "20D_only",
        "target_units": 2,
        "rank_20d": 3,
        "pred_return_20d": 0.041,
        "beta_60": 1.45,
        "price_vs_ma60": 0.31,
        "penalty_overlay_reason": "foreign_selling;gap_risk",
    }

    explain = explain_signal_row(row, source="unified")

    assert explain["advisory_only"] is True
    assert explain["action"] == "ENTRY_READY"
    assert explain["action_label"] == "可進場"
    assert "20D rank #3" in explain["drivers"]
    assert any("high beta" in item for item in explain["warnings"])
    assert any("外資賣壓" in item for item in explain["warnings"])


def test_tradability_block_overrides_buy_recommendation():
    row = {
        "signal_type": "20D_only",
        "target_units": 0,
        "recommendation_20d": "建議買進",
        "tradability_blocked": True,
        "tradability_reason": "is_disposition",
        "pred_return_20d": 0.05,
    }

    explain = explain_signal_row(row, source="unified")

    assert explain["action"] == "BLOCKED"
    assert explain["action_label"] == "不進場"
    assert any("is_disposition" in item for item in explain["warnings"])


def test_prediction_leaderboard_row_is_model_candidate_not_gate_decision():
    row = {
        "recommendation": "建議買進",
        "two_stage_rank": 6,
        "historical_win_rate": 63.8,
        "pred_return_20d": 0.032,
        "risk_tags": "Guardrail熔斷",
    }

    explain = explain_signal_row(row, source="prediction")

    assert explain["action"] == "MODEL_CANDIDATE"
    assert explain["action_label"] == "模型候選"
    assert any("20D rank #6" in item for item in explain["drivers"])
    assert any("Guardrail" in item for item in explain["warnings"])
