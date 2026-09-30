import numpy as np
import pandas as pd

from scripts.tquant_reference_sleeves import (
    _annotate_cross_section_ranks,
    _annotate_votes,
    _point_in_time_features,
    calc_aroon,
    calc_baz,
    calc_expanded_momentum_score,
    calc_price_action_structure,
    compute_factor_validation,
)


def test_tquant_momentum_scores_are_finite_for_clean_uptrend():
    close = pd.Series(np.linspace(50.0, 80.0, 140))

    assert np.isfinite(calc_baz(close))
    assert calc_expanded_momentum_score(close) > 0


def test_price_action_structure_requires_higher_low_and_compression():
    close = pd.Series(
        [
            100,
            101,
            102,
            103,
            104,
            105,
            106,
            107,
            108,
            109,
            110,
            111,
            112,
            113,
            114,
            115,
            116,
            117,
            118,
            119,
            120,
            121,
            122,
            123,
            124,
        ],
        dtype=float,
    )
    high = close * 1.01
    low = close * 0.99
    high.iloc[-5:] = close.iloc[-5:] * 1.005
    low.iloc[-5:] = close.iloc[-5:] * 0.995
    frame = pd.DataFrame({"Close": close, "High": high, "Low": low})

    passed, detail = calc_price_action_structure(frame)

    assert passed is True
    assert detail["close_to_20d_high"] > 0.88
    assert detail["higher_low_10d"] == 1.0
    assert detail["range_compression"] == 1.0


def test_aroon_detects_recent_high_and_old_low():
    high = pd.Series(list(range(100, 125)), dtype=float)
    low = pd.Series(list(range(50, 75)), dtype=float)

    up, down = calc_aroon(high, low, window=25)

    assert up == 100.0
    assert down == 4.0


def test_point_in_time_features_add_teJ_technical_and_financial_votes(monkeypatch):
    dates = pd.date_range("2026-04-01", periods=30, freq="B")
    close = pd.Series(np.linspace(50.0, 60.0, len(dates)))
    frame = pd.DataFrame(
        {
            "ticker": ["1234"] * len(dates),
            "date": dates.strftime("%Y-%m-%d"),
            "Open": close - 0.2,
            "High": close + 0.5,
            "Low": close - 0.5,
            "Close": close,
            "Volume": 1000,
            "Foreign_BuySell": 10,
            "Trust_BuySell": 5,
            "Dealer_BuySell": 0,
            "Margin_Balance": 1000,
            "Short_Balance": 0,
            "RSI_14": list(np.linspace(25, 34, len(dates))),
            "K": [45.0] * 29 + [18.0],
            "D": [40.0] * 30,
        }
    )
    monkeypatch.setattr(
        "scripts.tquant_reference_sleeves._latest_financial_features",
        lambda ticker, signal_date: {
            "financial_data_status": "ok",
            "financial_available_lag_days": 75,
            "financial_year": 2025,
            "financial_season": 4,
            "tquant_gross_margin_pct": 0.4,
            "tquant_net_margin_pct": 0.12,
            "tquant_gross_margin_delta_yoy": 0.02,
            "tquant_net_margin_delta_yoy": 0.01,
            "tquant_financial_quality_vote": True,
            "tquant_financial_turnaround_vote": True,
            "tquant_margin_deterioration_risk": False,
        },
    )
    monkeypatch.setattr(
        "scripts.tquant_reference_sleeves._latest_valuation_features",
        lambda ticker, signal_date: {
            "valuation_data_status": "ok",
            "valuation_date": "20260501",
            "tquant_pe_ratio": 15.0,
            "tquant_pb_ratio": 1.2,
            "tquant_dividend_yield": 3.5,
            "tquant_value_vote": True,
            "tquant_valuation_risk": False,
        },
    )

    features = _point_in_time_features("1234", "2026-05-12", frame)

    assert features["tquant_kd_reversal_vote"] is True
    assert features["tquant_rsi_reversal_vote"] is True
    assert features["tquant_aroon_trend_vote"] is True
    assert features["tquant_financial_quality_vote"] is True
    assert features["tquant_value_vote"] is True


def test_tquant_votes_classify_strong_and_reject():
    frame = pd.DataFrame(
        {
            "signal_date": ["2026-05-01", "2026-05-01"],
            "tquant_vam_21d": [4.0, -1.0],
            "tquant_mrat_21_200": [0.12, -0.05],
            "tquant_baz": [0.8, -0.5],
            "tquant_expanded_momentum": [30.0, -10.0],
            "revenue_yoy_3m_avg": [0.3, -0.1],
            "tquant_price_structure_vote": [True, False],
            "tquant_revenue_vote": [True, False],
            "tquant_institutional_vote": [True, False],
            "tquant_financing_crowding_risk": [False, True],
            "pred_return_20d": [0.08, 0.02],
            "prob_edge": [-0.05, -0.30],
            "beta_60_20d": [1.0, 1.8],
            "gap_pct_20d": [0.0, 0.10],
            "penalty_overlay_reason": ["", "gap_risk"],
            "futures_settlement_phase": ["normal", "settlement_day"],
            "futures_settlement_action": ["", "CAP"],
        }
    )

    ranked = _annotate_cross_section_ranks(frame)
    out = _annotate_votes(ranked)

    assert out.loc[0, "tquant_consensus"] == "strong"
    assert out.loc[0, "tquant_support_votes"] >= 6
    assert out.loc[1, "tquant_consensus"] == "reject"
    assert out.loc[1, "tquant_risk_flags"] >= 3


def test_factor_validation_reports_positive_rank_ic_for_monotonic_score():
    rows = []
    for signal_date in ["2026-05-01", "2026-05-02"]:
        for i in range(6):
            rows.append(
                {
                    "signal_date": signal_date,
                    "ticker": f"{1100+i}",
                    "sector_20d": "tech",
                    "avg_20d_amount_20d": 1_000_000,
                    "tquant_reference_score": i,
                    "tquant_technical_votes": i,
                    "tquant_fundamental_votes": i // 2,
                    "tquant_support_votes": i,
                    "tquant_risk_flags": 5 - i,
                    "pred_return_20d": i / 100,
                    "prob_edge": i / 10,
                    "return_to_mark": i / 100,
                }
            )
    report = compute_factor_validation(pd.DataFrame(rows))
    metric = {item["factor"]: item for item in report["metrics"]}["reference_score"]

    assert report["status"] == "ok"
    assert metric["rank_ic_mean"] > 0.9
    assert metric["top_bottom_spread"] > 0
