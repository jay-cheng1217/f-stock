from __future__ import annotations

import pandas as pd

import ml.predict as predict_module
from scripts import build_unified_signals as unified_module


def _macro_context() -> dict:
    return {
        "events": {
            "risk_score": 58.0,
            "risk_level": "high",
            "event_count": 3,
            "next_event": {"title": "US CPI for June 2026", "days_until": 2},
        },
        "market_sentiment": {"score": 43.0, "label": "cautious"},
        "strategy": {"macro_pressure_score": 62.0, "action": "risk_control_reduce_high_beta"},
        "next_event": {"title": "US CPI for June 2026", "days_until": 2},
    }


def test_prediction_macro_overlay_adjusts_ranking_scores(monkeypatch):
    monkeypatch.setattr(predict_module, "build_macro_strategy_context", lambda as_of=None, horizon_days=20: _macro_context())
    df = pd.DataFrame(
        {
            "ticker": ["1111", "2222"],
            "date": ["2026-07-13", "2026-07-13"],
            "risk_adjusted_return": [0.08, 0.08],
            "leaderboard_score": [0.10, 0.10],
            "beta_60": [0.8, 1.6],
            "gap_pct": [0.0, 0.02],
            "price_vs_ma20": [0.0, 0.08],
            "risk_tags": ["", ""],
        }
    )

    out = predict_module._attach_macro_strategy_artifacts(df)

    assert out["macro_strategy_pressure_score"].iloc[0] == 62.0
    assert out.loc[1, "macro_stock_penalty_return"] > out.loc[0, "macro_stock_penalty_return"]
    assert out.loc[1, "leaderboard_score"] < out.loc[1, "leaderboard_score_pre_macro"]
    assert out["risk_tags"].str.contains("MACRO_EVENT_RISK").all()


def test_unified_macro_overlay_caps_weight_without_blocking(monkeypatch):
    monkeypatch.setattr(unified_module, "build_macro_strategy_context", lambda as_of=None, horizon_days=20: _macro_context())
    df = pd.DataFrame(
        {
            "ticker": ["1111", "2222"],
            "signal_type": [unified_module.SIGNAL_TYPE_20D_ONLY, unified_module.SIGNAL_TYPE_20D_ONLY],
            "target_units": [2, 2],
            "target_weight_ratio": [0.5, 0.5],
            "beta_60_20d": [0.9, 1.7],
            "gap_pct_20d": [0.0, 0.02],
            "price_vs_ma20_20d": [0.0, 0.08],
            "tradability_reason": ["", ""],
        }
    )

    out = unified_module._apply_macro_strategy_overlay(df, prediction_date="2026-07-13")

    assert out.loc[1, "target_weight_ratio"] < out.loc[0, "target_weight_ratio"]
    assert out.loc[1, "signal_type"] == unified_module.SIGNAL_TYPE_20D_ONLY
    assert out["tradability_reason"].str.contains(unified_module.TRADABILITY_REASON_MACRO_EVENT_RISK).all()
