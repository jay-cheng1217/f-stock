import pandas as pd

from backend.services import warroom_service


def test_unified_signal_feed_surfaces_shortwave_candidates_when_selected_is_empty(tmp_path, monkeypatch):
    signal_path = tmp_path / "unified_signals_2026-06-23.csv"
    pd.DataFrame(
        columns=[
            "ticker",
            "prediction_date",
            "target_units",
            "target_weight_ratio",
            "close_ref",
            "rank_20d",
            "pred_return_20d",
            "penalty_overlay_score",
            "tradability_blocked",
            "tradability_reason",
        ]
    ).to_csv(signal_path, index=False, encoding="utf-8-sig")

    prediction_path = tmp_path / "predictions_2026-06-23.csv"
    pd.DataFrame(
        [
            {
                "date": "2026-06-23",
                "ticker": "8086",
                "sector": "Semiconductor",
                "recommendation": "建議買進",
                "rank_20d": 7,
                "close": 159.5,
                "pred_return_20d": 0.007,
                "leaderboard_score_before_shortwave": 0.41,
                "penalty_hard_block": False,
                "shortwave_action": "SMALL_ENTRY",
                "shortwave_score": 3.0,
                "shortwave_strategy_tags": "friend_logic_pullback;model_combo",
                "shortwave_missing_strategy_tags": "volume_quiet",
                "shortwave_entry_strategy_advice": "盤中回落到支撐區才小量試單",
                "shortwave_entry_zone_low": 157.81,
                "shortwave_entry_zone_high": 164.45,
                "shortwave_entry_zone_status": "in_entry_zone",
                "shortwave_entry_zone_note": "支撐回落區",
                "shortwave_preferred_hold_days": 60,
                "shortwave_model_combo_support": 1,
                "shortwave_friend_combo_score": 2,
                "tactical_action": "TACTICAL_NO_TRADE",
            },
            {
                "date": "2026-06-23",
                "ticker": "3013",
                "sector": "Computer",
                "recommendation": "WATCH",
                "rank_20d": 88,
                "close": 109.5,
                "pred_return_20d": -0.01,
                "leaderboard_score_before_shortwave": 0.11,
                "penalty_hard_block": False,
                "shortwave_action": "OBSERVE",
                "shortwave_score": 0.21,
                "shortwave_strategy_tags": "",
                "shortwave_missing_strategy_tags": "missing_ml_buy_signal",
                "shortwave_entry_strategy_advice": "observe_only",
                "tactical_action": "TACTICAL_LIMIT_BUY",
                "tactical_score": 0.87,
                "tactical_entry_zone_low": 108.81,
                "tactical_entry_zone_high": 111.01,
                "tactical_stop_loss": 107.71,
                "tactical_take_profit_2p": 112.11,
                "tactical_take_profit_4p": 114.31,
                "tactical_max_hold_days": 3,
                "tactical_entry_strategy_advice": "short_trade_advisory",
                "tactical_reason": "ma60_support_bounce_setup",
                "swing_action": "OBSERVE",
                "swing_score": 0.21,
            },
            {
                "date": "2026-06-23",
                "ticker": "6182",
                "sector": "Semiconductor",
                "recommendation": "SELL",
                "rank_20d": 120,
                "close": 116.0,
                "pred_return_20d": -0.08,
                "leaderboard_score_before_shortwave": 0.07,
                "penalty_hard_block": False,
                "shortwave_action": "EXIT_BIAS",
                "shortwave_score": 0.62,
                "shortwave_strategy_tags": "",
                "shortwave_missing_strategy_tags": "missing_friend_pullback_support",
                "shortwave_entry_strategy_advice": "swing_inactive",
                "tactical_action": "TACTICAL_NO_TRADE",
                "momentum_action": "MOMENTUM_LIMIT_BUY",
                "momentum_score": 0.84,
                "momentum_reason": "momentum_continuation_v1_20260625;trend_stack;macd_positive",
                "momentum_missing_strategy_tags": "",
                "momentum_entry_strategy_advice": "limit_buy_if_price_near_ma5",
                "momentum_entry_limit": 116.0,
                "momentum_entry_zone_low": 115.62,
                "momentum_entry_zone_high": 117.74,
                "momentum_stop_loss": 112.13,
                "momentum_take_profit_3p": 119.48,
                "momentum_take_profit_6p": 122.96,
                "momentum_max_hold_days": 5,
            },
        ]
    ).to_csv(prediction_path, index=False, encoding="utf-8-sig")

    def fake_latest_path(prefix):
        if prefix == warroom_service.LATEST_UNIFIED_RE:
            return str(signal_path)
        if prefix == warroom_service.LATEST_PREDICTION_RE:
            return str(prediction_path)
        return None

    monkeypatch.setattr(warroom_service, "_latest_csv_path", fake_latest_path)
    monkeypatch.setattr(warroom_service, "_load_name_lookup", lambda: {"8086": "宏捷科", "3013": "晟銘電"})
    monkeypatch.setattr(warroom_service, "apply_penalty_overlay", lambda df, version="v1": df)
    monkeypatch.setattr(
        warroom_service,
        "_momentum_signal_age_for_ticker",
        lambda ticker, prediction_date: {
            "momentum_first_signal_date": "2026-06-23",
            "momentum_setup_age_days": 0,
            "momentum_signal_phase": "fresh_setup",
        },
    )

    payload = warroom_service.build_unified_signals_latest_feed()

    assert payload["prediction_date"] == "2026-06-23"
    assert payload["selected_count"] == 0
    assert payload["shortwave_candidate_count"] == 3
    assert payload["tactical_candidate_count"] == 1
    assert payload["swing_candidate_count"] == 1
    assert payload["momentum_candidate_count"] == 1
    candidate = [row for row in payload["shortwave_candidates"] if row["ticker"] == "8086"][0]
    assert candidate["ticker"] == "8086"
    assert candidate["name"] == "宏捷科"
    assert candidate["signal_type"] == "SHORTWAVE_CANDIDATE"
    assert candidate["explain_action_label"] == "波段小倉"
    assert candidate["shortwave_entry_zone_low"] == 157.81
    assert candidate["shortwave_entry_zone_high"] == 164.45
    assert candidate["tradability_reason"] == "盤中回落到支撐區才小量試單"
    tactical = [row for row in payload["shortwave_candidates"] if row["ticker"] == "3013"][0]
    assert tactical["name"] == "晟銘電"
    assert tactical["strategy_lane"] == "tactical"
    assert tactical["explain_action_label"] == "短打觀察"
    assert tactical["tactical_entry_zone_low"] == 108.81
    assert tactical["tactical_take_profit_2p"] == 112.11
    momentum = [row for row in payload["shortwave_candidates"] if row["ticker"] == "6182"][0]
    assert momentum["strategy_lane"] == "momentum"
    assert momentum["explain_action_label"] == "支撐區觀察"
    assert momentum["momentum_entry_zone_low"] == 115.62
    assert momentum["momentum_take_profit_3p"] == 119.48


def test_shortwave_candidate_feed_marks_momentum_no_chase_when_live_price_already_chased(tmp_path):
    prediction_path = tmp_path / "predictions_2026-06-24.csv"
    pd.DataFrame(
        [
            {
                "date": "2026-06-24",
                "ticker": "6182",
                "recommendation": "SELL",
                "close": 116.0,
                "pred_return_20d": -0.08,
                "leaderboard_score_before_shortwave": 0.07,
                "shortwave_action": "EXIT_BIAS",
                "tactical_action": "TACTICAL_NO_TRADE",
                "momentum_action": "MOMENTUM_LIMIT_BUY",
                "momentum_score": 0.94,
                "momentum_entry_zone_low": 114.26,
                "momentum_entry_limit": 116.0,
                "momentum_entry_zone_high": 117.74,
                "momentum_entry_strategy_advice": "momentum_continuation_advisory",
                "latest_price": 126.5,
            }
        ]
    ).to_csv(prediction_path, index=False, encoding="utf-8-sig")

    rows = warroom_service._shortwave_candidate_rows(str(prediction_path), {"6182": "合晶"})

    assert len(rows) == 1
    assert rows.iloc[0]["momentum_action"] == "MOMENTUM_MISSED_NO_CHASE"
    assert rows.iloc[0]["entry_review_state"] == "missed_no_chase"
    assert rows.iloc[0]["entry_review_label"] == "已錯過不追"


def test_momentum_age_guard_drops_already_active_signal(monkeypatch):
    frame = pd.DataFrame(
        [
            {
                "date": "2026-06-24",
                "ticker": "6182",
                "shortwave_action": "EXIT_BIAS",
                "tactical_action": "TACTICAL_NO_TRADE",
                "momentum_action": "MOMENTUM_LIMIT_BUY",
                "momentum_entry_zone_high": 117.74,
            }
        ]
    )

    monkeypatch.setattr(
        warroom_service,
        "_momentum_signal_age_for_ticker",
        lambda ticker, prediction_date: {
            "momentum_first_signal_date": "2026-06-23",
            "momentum_setup_age_days": 1,
            "momentum_signal_phase": "active_follow_through",
        },
    )

    out = warroom_service._apply_momentum_signal_age_guard(frame)

    assert out.loc[0, "momentum_action"] == "MOMENTUM_ACTIVE_NO_NEW_ENTRY"
    assert out.loc[0, "momentum_first_signal_date"] == "2026-06-23"
    assert "active_follow_through_not_new_entry" in out.loc[0, "momentum_entry_strategy_advice"]


def test_momentum_age_guard_fails_closed_when_fresh_setup_unverified(monkeypatch):
    frame = pd.DataFrame(
        [
            {
                "date": "2026-06-24",
                "ticker": "2375",
                "shortwave_action": "EXIT_BIAS",
                "tactical_action": "TACTICAL_NO_TRADE",
                "momentum_action": "MOMENTUM_LIMIT_BUY",
                "momentum_entry_zone_high": 214.67,
            }
        ]
    )

    monkeypatch.setattr(warroom_service, "_momentum_signal_age_for_ticker", lambda ticker, prediction_date: None)

    out = warroom_service._apply_momentum_signal_age_guard(frame)

    assert out.loc[0, "momentum_action"] == "MOMENTUM_REVIEW_NO_NEW_ENTRY"
    assert out.loc[0, "momentum_signal_phase"] == "fresh_setup_unverified"
    assert "do_not_new_enter" in out.loc[0, "momentum_entry_strategy_advice"]
