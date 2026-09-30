from __future__ import annotations

import os
from pathlib import Path

import pandas as pd

from ml.chipk import annotate_chipk_main_force_diagnosis, load_latest_chipk_main_force
from ml.entry_shortwave import compute_shortwave_overlay
from scripts import build_unified_signals as bus


def test_load_latest_chipk_main_force_reads_cp950_snapshot(tmp_path, monkeypatch):
    chart_dir = tmp_path / "AppViewer" / "profile" / "UBSKChart"
    (chart_dir / "System").mkdir(parents=True)
    (chart_dir / "System" / "date.txt").write_text("20260618", encoding="cp950")
    content = "\n".join(
        [
            "股票代號^股票名稱^收盤價^漲跌^漲跌幅^成交量^漲跌停^主力動向1日^主力動向5日^主力動向20日",
            "2330^台積電^2385.00^-15.00^-0.63^30059^0^5^2^4",
            "3550^聯穎^33.90^3.05^9.89^4661^1^1^2^2",
        ]
    )
    (chart_dir / "2056757959.txt").write_text(content, encoding="cp950")
    monkeypatch.setenv("CMONEY_APPVIEWER_DIR", str(tmp_path / "AppViewer"))

    df, meta = load_latest_chipk_main_force()

    assert meta.status == "ok"
    assert meta.asof_date == "2026-06-18"
    assert meta.row_count == 2
    assert meta.history_available is False
    row = df.set_index("ticker").loc["3550"]
    assert row["chipk_main_force_1d"] == 1
    assert row["chipk_main_force_score"] > df.set_index("ticker").loc["2330", "chipk_main_force_score"]


def test_shortwave_overlay_can_disable_rerank_for_rollback(monkeypatch):
    monkeypatch.delenv("SHORTWAVE_PROD_RERANK_ENABLED", raising=False)
    pred = pd.DataFrame(
        {
            "ticker": ["2330", "3550"],
            "recommendation": ["建議買進", "建議買進"],
            "leaderboard_score": [0.10, 0.20],
            "close": [100.0, 80.0],
        }
    )
    snapshot = pd.DataFrame(
        {
            "price_vs_ma5": [0.02, -0.10],
            "price_vs_ma20": [0.03, -0.12],
            "price_vs_ma60": [0.04, -0.02],
            "ma_slope_5": [0.01, -0.01],
            "vol_ratio": [0.6, 4.2],
            "return_1d": [0.01, -0.03],
            "return_5d": [0.01, -0.08],
            "macd_hist": [1.0, -1.0],
            "macd_hist_delta_3d": [0.2, -0.1],
            "macd_turn_positive": [1, 0],
            "macd_bearish_div": [0, 1],
            "macd_bullish_div": [0, 0],
            "rsi_14": [58.0, 84.0],
            "kd_k": [55.0, 80.0],
            "kd_d": [45.0, 82.0],
            "MA_5": [100.0, 90.0],
            "MA_20": [98.0, 95.0],
        },
        index=pred.index,
    )

    out = compute_shortwave_overlay(pred, snapshot=snapshot, chipk_df=None, rerank_enabled=False)

    assert "shortwave_score" in out.columns
    assert out.loc[0, "shortwave_action"] == "NORMAL_ENTRY"
    assert out.loc[0, "shortwave_friend_combo_match"] == 1
    assert "friend_combo_primary" in out.loc[0, "shortwave_strategy_tags"]
    assert out.loc[0, "shortwave_missing_strategy_tags"] == ""
    assert "normal_entry_allowed" in out.loc[0, "shortwave_entry_strategy_advice"]
    assert out.loc[0, "shortwave_entry_zone_low"] == 97.0
    assert out.loc[0, "shortwave_entry_zone_status"] == "in_entry_zone"
    assert out.loc[1, "shortwave_action"] == "BLOCK"
    assert out["leaderboard_score"].tolist() == [0.10, 0.20]
    assert out["shortwave_rerank_enabled"].eq(False).all()
    assert out["shortwave_risk_flags"].str.contains("chipk_snapshot_not_historical_backtested").all()


def test_shortwave_overlay_can_rerank_when_explicitly_enabled(monkeypatch):
    monkeypatch.setenv("SHORTWAVE_ALLOW_NO_CHIPK_HISTORY", "1")
    pred = pd.DataFrame(
        {
            "ticker": ["2330"],
            "recommendation": ["建議買進"],
            "leaderboard_score": [1.0],
            "close": [100.0],
        }
    )
    snapshot = pd.DataFrame(
        {
            "price_vs_ma5": [0.02],
            "price_vs_ma20": [0.03],
            "price_vs_ma60": [0.04],
            "ma_slope_5": [0.01],
            "vol_ratio": [0.6],
            "return_1d": [0.01],
            "return_5d": [0.01],
            "macd_hist": [1.0],
            "macd_hist_delta_3d": [0.2],
            "macd_turn_positive": [1],
            "rsi_14": [55.0],
            "kd_k": [60.0],
            "kd_d": [50.0],
        },
        index=pred.index,
    )
    chipk = pd.DataFrame(
        {
            "ticker": ["2330"],
            "chipk_asof_date": ["2026-06-18"],
            "chipk_main_force_1d": [1],
            "chipk_main_force_5d": [1],
            "chipk_main_force_20d": [1],
            "chipk_main_force_score": [1.0],
            "chipk_history_available": [False],
            "chipk_score_version": ["test"],
            "chipk_score_orientation": ["lower_is_stronger_assumption"],
        }
    )

    out = compute_shortwave_overlay(pred, snapshot=snapshot, chipk_df=chipk, rerank_enabled=True)

    assert out.loc[0, "shortwave_chipk_used_in_production"] == 1
    assert out.loc[0, "shortwave_friend_combo_match"] == 1
    assert "friend_combo_primary" in out.loc[0, "shortwave_strategy_tags"]
    assert out.loc[0, "shortwave_missing_strategy_tags"] == ""
    assert out.loc[0, "leaderboard_score"] > 1.0
    assert out.loc[0, "leaderboard_score_before_shortwave"] == 1.0


def test_shortwave_overlay_tolerates_partial_chipk_snapshot(monkeypatch):
    pred = pd.DataFrame(
        {
            "ticker": ["2330"],
            "recommendation": ["BUY"],
            "leaderboard_score": [1.0],
            "close": [100.0],
        }
    )
    snapshot = pd.DataFrame(
        {
            "price_vs_ma5": [0.02],
            "price_vs_ma20": [0.03],
            "price_vs_ma60": [0.04],
            "volume_today": [5000],
            "avg_20d_volume": [10000],
            "return_1d": [0.01],
            "return_5d": [0.01],
            "macd_hist": [1.0],
            "macd_hist_delta_3d": [0.2],
            "macd_turn_positive": [1],
            "rsi_14": [55.0],
            "kd_k": [60.0],
            "kd_d": [50.0],
        },
        index=pred.index,
    )
    chipk = pd.DataFrame({"ticker": ["2330"], "chipk_asof_date": ["2026-06-22"]})

    out = compute_shortwave_overlay(pred, snapshot=snapshot, chipk_df=chipk, rerank_enabled=False)

    assert "chipk_main_force_score" in out.columns
    assert pd.isna(out.loc[0, "chipk_main_force_score"])
    assert out.loc[0, "shortwave_friend_combo_score"] == 1.0
    assert out.loc[0, "shortwave_entry_zone_status"] == "in_entry_zone"


def test_shortwave_overlay_reports_missing_leg_and_entry_advice():
    pred = pd.DataFrame(
        {
            "ticker": ["4909"],
            "close": [100.0],
            "recommendation": ["建議買進"],
            "leaderboard_score": [1.0],
        }
    )
    snapshot = pd.DataFrame(
        {
            "price_vs_ma5": [0.18],
            "price_vs_ma20": [0.12],
            "price_vs_ma60": [0.04],
            "volume_today": [5000],
            "avg_20d_volume": [10000],
            "return_1d": [0.01],
            "return_5d": [0.01],
            "macd_hist": [1.0],
            "macd_hist_delta_3d": [0.2],
            "macd_turn_positive": [1],
            "rsi_14": [55.0],
            "kd_k": [60.0],
            "kd_d": [50.0],
        },
        index=pred.index,
    )

    out = compute_shortwave_overlay(pred, snapshot=snapshot, chipk_df=None, rerank_enabled=False)

    assert out.loc[0, "shortwave_action"] == "SMALL_ENTRY"
    assert out.loc[0, "shortwave_friend_combo_score"] == 0.75
    assert out.loc[0, "shortwave_missing_strategy_tags"] == "missing_friend_pullback_support"
    assert out.loc[0, "shortwave_entry_zone_status"] == "wait_pullback_to_zone"
    assert 91.3 < out.loc[0, "shortwave_entry_zone_low"] < 91.4
    assert 91.5 < out.loc[0, "shortwave_entry_zone_high"] < 91.6
    assert "small_entry_only" in out.loc[0, "shortwave_entry_strategy_advice"]
    assert "trigger_if_price_in_entry_zone=91.35-91.53" in out.loc[0, "shortwave_entry_strategy_advice"]
    assert "missing_friend_pullback_support" in out.loc[0, "shortwave_entry_strategy_advice"]


def test_shortwave_overlay_adds_ma60_tactical_bounce_strategy():
    pred = pd.DataFrame(
        {
            "ticker": ["3013"],
            "close": [112.0],
            "recommendation": ["BUY"],
            "leaderboard_score": [1.0],
        }
    )
    snapshot = pd.DataFrame(
        {
            "close": [112.0],
            "Low": [107.5],
            "volume_today": [3_500_000],
            "avg_20d_volume": [7_330_000],
            "return_1d": [0.014],
            "price_vs_ma5": [112.0 / 109.7 - 1.0],
            "price_vs_ma20": [112.0 / 116.23 - 1.0],
            "price_vs_ma60": [112.0 / 108.78 - 1.0],
            "MA_5": [109.7],
            "MA_20": [116.23],
            "MA_60": [108.78],
            "RSI_14": [47.8],
            "MACDh_12_26_9": [-2.03],
            "macd_hist_delta_3d": [-0.1],
            "K": [19.1],
            "D": [12.7],
        },
        index=pred.index,
    )

    out = compute_shortwave_overlay(pred, snapshot=snapshot, chipk_df=None, rerank_enabled=False)

    assert out.loc[0, "tactical_action"] == "TACTICAL_WAIT_PULLBACK"
    assert out.loc[0, "tactical_entry_zone_low"] == round(108.78 * 0.99, 4)
    assert out.loc[0, "tactical_entry_zone_high"] == round(108.78 * 1.01, 4)
    assert out.loc[0, "tactical_stop_loss"] == round(108.78 * 0.98, 4)
    assert out.loc[0, "tactical_take_profit_2p"] == round(108.78 * 1.02, 4)
    assert out.loc[0, "tactical_max_hold_days"] == 3
    assert "small_size_only_backtest_negative" in out.loc[0, "tactical_entry_strategy_advice"]
    assert out.loc[0, "swing_action"] == out.loc[0, "shortwave_action"]


def test_shortwave_overlay_adds_momentum_continuation_strategy():
    pred = pd.DataFrame(
        {
            "ticker": ["6182"],
            "close": [116.0],
            "recommendation": ["強力賣出"],
            "leaderboard_score": [0.2],
            "is_disposition": [0],
            "is_attention": [0],
            "is_full_delivery": [0],
            "is_suspended": [0],
        }
    )
    snapshot = pd.DataFrame(
        {
            "close": [116.0],
            "Volume": [45_000],
            "VOL_MA_20": [42_000],
            "return_1d": [0.013],
            "return_5d": [0.045],
            "price_vs_ma5": [116.0 / 116.2 - 1.0],
            "price_vs_ma20": [116.0 / 97.1 - 1.0],
            "price_vs_ma60": [116.0 / 63.2 - 1.0],
            "MA_5": [116.2],
            "MA_20": [97.1],
            "MA_60": [63.2],
            "RSI_14": [70.0],
            "MACDh_12_26_9": [5.0],
            "macd_hist_delta_3d": [0.3],
            "K": [80.0],
            "D": [70.0],
            "beta_60": [1.88],
        },
        index=pred.index,
    )

    out = compute_shortwave_overlay(pred, snapshot=snapshot, chipk_df=None, rerank_enabled=False)

    assert out.loc[0, "momentum_action"] == "MOMENTUM_LIMIT_BUY"
    assert out.loc[0, "momentum_entry_zone_low"] == round(116.0 * 0.985, 4)
    assert out.loc[0, "momentum_entry_zone_high"] == round(116.0 * 1.015, 4)
    assert out.loc[0, "momentum_stop_loss"] == round(max(116.2 * 0.965, 116.0 * 0.94), 4)
    assert out.loc[0, "momentum_take_profit_3p"] == round(116.0 * 1.03, 4)
    assert "confirm_chipk_1d_5d_main_force" in out.loc[0, "momentum_entry_strategy_advice"]


def test_tactical_no_trade_suppresses_stale_ma60_zone():
    pred = pd.DataFrame(
        {
            "ticker": ["6182"],
            "close": [116.0],
            "recommendation": ["強力賣出"],
            "leaderboard_score": [0.2],
        }
    )
    snapshot = pd.DataFrame(
        {
            "close": [116.0],
            "Low": [114.0],
            "Volume": [45_000_000],
            "VOL_MA_20": [42_000_000],
            "return_1d": [0.013],
            "price_vs_ma5": [116.0 / 116.2 - 1.0],
            "price_vs_ma20": [116.0 / 97.1 - 1.0],
            "price_vs_ma60": [116.0 / 63.2 - 1.0],
            "MA_5": [116.2],
            "MA_20": [97.1],
            "MA_60": [63.2],
            "RSI_14": [70.0],
            "MACDh_12_26_9": [5.0],
            "macd_hist_delta_3d": [0.3],
            "K": [80.0],
            "D": [70.0],
        },
        index=pred.index,
    )

    out = compute_shortwave_overlay(pred, snapshot=snapshot, chipk_df=None, rerank_enabled=False)

    assert out.loc[0, "tactical_action"] == "TACTICAL_NO_TRADE"
    assert pd.isna(out.loc[0, "tactical_entry_zone_low"])
    assert pd.isna(out.loc[0, "tactical_entry_limit"])
    assert "do_not_use_stale_support_zone" in out.loc[0, "tactical_entry_strategy_advice"]


def test_chipk_source_date_after_prediction_is_aligned_for_prediction(monkeypatch):
    monkeypatch.setenv("SHORTWAVE_ALLOW_NO_CHIPK_HISTORY", "1")
    pred = pd.DataFrame(
        {
            "ticker": ["2351"],
            "date": ["2026-06-22"],
            "recommendation": ["BUY"],
            "leaderboard_score": [1.0],
        }
    )
    snapshot = pd.DataFrame(
        {
            "price_vs_ma5": [0.02],
            "price_vs_ma20": [0.03],
            "price_vs_ma60": [0.04],
            "ma_slope_5": [0.01],
            "vol_ratio": [1.1],
            "return_1d": [0.01],
            "return_5d": [0.02],
            "macd_hist": [1.0],
            "macd_turn_positive": [1],
            "rsi_14": [55.0],
            "kd_k": [60.0],
            "kd_d": [50.0],
        },
        index=pred.index,
    )
    chipk = pd.DataFrame(
        {
            "ticker": ["2351"],
            "chipk_asof_date": ["2026-06-23"],
            "chipk_main_force_1d": [3],
            "chipk_main_force_5d": [4],
            "chipk_main_force_20d": [3],
            "chipk_main_force_score": [0.4125],
            "chipk_history_available": [False],
            "chipk_score_version": ["test"],
            "chipk_score_orientation": ["lower_is_stronger_assumption"],
        }
    )

    out = compute_shortwave_overlay(pred, snapshot=snapshot, chipk_df=chipk, rerank_enabled=True)

    assert out.loc[0, "chipk_asof_date"] == "2026-06-22"
    assert out.loc[0, "chipk_source_snapshot_date"] == "2026-06-23"
    assert out.loc[0, "chipk_date_alignment"] == "source_after_prediction_adjusted"
    assert out.loc[0, "shortwave_chipk_used_in_production"] == 1


def test_stale_chipk_snapshot_is_not_used_for_rerank(monkeypatch):
    monkeypatch.setenv("SHORTWAVE_ALLOW_NO_CHIPK_HISTORY", "1")
    pred = pd.DataFrame(
        {
            "ticker": ["2351"],
            "date": ["2026-06-22"],
            "recommendation": ["BUY"],
            "leaderboard_score": [1.0],
        }
    )
    snapshot = pd.DataFrame(
        {
            "price_vs_ma5": [0.02],
            "price_vs_ma20": [0.03],
            "price_vs_ma60": [0.04],
            "ma_slope_5": [0.01],
            "vol_ratio": [1.1],
            "return_1d": [0.01],
            "return_5d": [0.02],
            "macd_hist": [1.0],
            "macd_turn_positive": [1],
            "rsi_14": [55.0],
            "kd_k": [60.0],
            "kd_d": [50.0],
        },
        index=pred.index,
    )
    chipk = pd.DataFrame(
        {
            "ticker": ["2351"],
            "chipk_asof_date": ["2026-06-18"],
            "chipk_main_force_1d": [5],
            "chipk_main_force_5d": [4],
            "chipk_main_force_20d": [4],
            "chipk_main_force_score": [0.1875],
            "chipk_history_available": [False],
            "chipk_score_version": ["test"],
            "chipk_score_orientation": ["lower_is_stronger_assumption"],
        }
    )

    out = compute_shortwave_overlay(pred, snapshot=snapshot, chipk_df=chipk, rerank_enabled=True)

    assert out.loc[0, "chipk_date_alignment"] == "stale_before_prediction"
    assert out.loc[0, "shortwave_chipk_used_in_production"] == 0
    assert "chipk_snapshot_stale" in out.loc[0, "shortwave_risk_flags"]


def test_unified_20d_candidates_preserve_shortwave_columns(monkeypatch):
    monkeypatch.setattr(bus, "apply_validated_chip_momentum_entry_filter", lambda df: df)
    monkeypatch.setattr(bus, "apply_sector_cap", lambda df, top_n: df.head(top_n))
    monkeypatch.setattr(
        bus,
        "apply_group_cap",
        lambda df, top_n, initial_df=None: (initial_df if initial_df is not None else df).head(top_n),
    )
    monkeypatch.setattr(bus, "_attach_pe_ratio_context", lambda df: df)
    pred = pd.DataFrame(
        [
            {
                "ticker": "3550",
                "date": "2026-06-18",
                "close": 33.9,
                "recommendation": "BUY",
                "pred_return_20d": 0.08,
                "risk_adjusted_return": 0.08,
                "leaderboard_score": 0.08,
                "shortwave_score": 0.72,
                "shortwave_action": "SMALL_ENTRY",
                "shortwave_reason": "ma_support;volume_confirm",
                "shortwave_strategy_tags": "friend_combo_developing;ml_buy_signal",
                "shortwave_missing_strategy_tags": "missing_friend_pullback_support",
                "shortwave_entry_strategy_advice": "small_entry_only; missing=missing_friend_pullback_support",
                "shortwave_watch_note": "developing_watch",
                "shortwave_preferred_hold_days": 60,
                "shortwave_friend_combo_score": 0.75,
                "shortwave_friend_combo_match": 0,
                "shortwave_model_combo_support": 3,
                "shortwave_rerank_enabled": False,
                "chipk_main_force_score": 0.66,
                "chipk_history_available": False,
                "tactical_action": "TACTICAL_LIMIT_BUY",
                "tactical_score": 0.87,
                "tactical_entry_zone_low": 108.81,
                "tactical_entry_zone_high": 111.01,
                "tactical_stop_loss": 107.71,
                "tactical_take_profit_2p": 112.11,
                "swing_action": "SMALL_ENTRY",
                "swing_score": 0.72,
            }
        ]
    )

    out = bus._build_20d_candidates(pred, top_n=1)

    assert out.loc[0, "shortwave_score_20d"] == 0.72
    assert out.loc[0, "shortwave_action_20d"] == "SMALL_ENTRY"
    assert out.loc[0, "shortwave_reason_20d"] == "ma_support;volume_confirm"
    assert out.loc[0, "shortwave_strategy_tags_20d"] == "friend_combo_developing;ml_buy_signal"
    assert out.loc[0, "shortwave_missing_strategy_tags_20d"] == "missing_friend_pullback_support"
    assert out.loc[0, "shortwave_entry_strategy_advice_20d"] == "small_entry_only; missing=missing_friend_pullback_support"
    assert out.loc[0, "shortwave_watch_note_20d"] == "developing_watch"
    assert out.loc[0, "shortwave_preferred_hold_days_20d"] == 60
    assert out.loc[0, "shortwave_friend_combo_score_20d"] == 0.75
    assert out.loc[0, "shortwave_friend_combo_match_20d"] == 0
    assert out.loc[0, "shortwave_model_combo_support_20d"] == 3
    assert out.loc[0, "chipk_main_force_score_20d"] == 0.66
    assert out.loc[0, "tactical_action_20d"] == "TACTICAL_LIMIT_BUY"
    assert out.loc[0, "tactical_score_20d"] == 0.87
    assert out.loc[0, "tactical_entry_zone_low_20d"] == 108.81
    assert out.loc[0, "tactical_take_profit_2p_20d"] == 112.11
    assert out.loc[0, "swing_action_20d"] == "SMALL_ENTRY"


def test_chipk_main_force_diagnosis_detects_chase_trap():
    frame = pd.DataFrame(
        [
            {
                "ticker": "1111",
                "chipk_main_force_1d": 1,
                "chipk_main_force_5d": 5,
                "chipk_main_force_20d": 5,
                "chipk_main_force_score": 0.25,
            },
            {
                "ticker": "2222",
                "chipk_main_force_1d": 1,
                "chipk_main_force_5d": 1,
                "chipk_main_force_20d": 1,
                "chipk_main_force_score": 1.0,
            },
            {
                "ticker": "3333",
                "chipk_main_force_1d": 5,
                "chipk_main_force_5d": 1,
                "chipk_main_force_20d": 1,
                "chipk_main_force_score": 0.70,
            },
        ]
    )

    out = annotate_chipk_main_force_diagnosis(frame).set_index("ticker")

    assert out.loc["1111", "chipk_main_force_pattern"] == "short_term_chase_trap"
    assert out.loc["1111", "chipk_entry_alignment"] == "DO_NOT_CHASE"
    assert bool(out.loc["1111", "chipk_trap_risk"]) is True
    assert out.loc["2222", "chipk_main_force_pattern"] == "sustained_accumulation"
    assert out.loc["2222", "chipk_entry_alignment"] == "CONFIRM_ENTRY"
    assert out.loc["3333", "chipk_main_force_pattern"] == "constructive_pullback"
    assert out.loc["3333", "chipk_entry_alignment"] == "WAIT_FOR_TURN"
