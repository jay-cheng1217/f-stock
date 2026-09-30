from __future__ import annotations

import pandas as pd

import backend.services.forecast_service as forecast_service
from backend.services.forecast_service import build_price_forecast


def _daily_frame(days: int = 90) -> pd.DataFrame:
    dates = pd.bdate_range("2026-02-05", periods=days)
    closes = [100.0 + index * 0.35 for index in range(days)]
    return pd.DataFrame(
        {
            "Date": dates,
            "Open": closes,
            "High": [value * 1.01 for value in closes],
            "Low": [value * 0.99 for value in closes],
            "Close": closes,
            "Volume": [1_000_000] * days,
            "MA_20": pd.Series(closes).rolling(20, min_periods=1).mean(),
            "MA_60": pd.Series(closes).rolling(60, min_periods=1).mean(),
            "RSI_14": [55.0] * days,
            "ATR_14": [2.0] * days,
            "VOL_MA_20": [900_000] * days,
        }
    )


def test_build_price_forecast_returns_chart_ready_payload(monkeypatch):
    frame = _daily_frame()
    monkeypatch.setattr(forecast_service, "_read_daily_history", lambda ticker, limit: (frame, "test"))
    monkeypatch.setattr(
        forecast_service,
        "_load_latest_prediction_row",
        lambda ticker: (
            {
                "ticker": ticker,
                "date": "2026-06-05",
                "pred_return_20d": 0.05,
                "recommendation": "BUY",
                "risk_tags": "",
                "beta_60": 0.9,
                "leaderboard_score": 0.1234,
            },
            "predictions_2026-06-05.csv",
        ),
    )
    monkeypatch.setattr(forecast_service, "_load_name_sector_lookup", lambda ticker: ("Test Co", "Tech"))
    monkeypatch.setattr(forecast_service, "score_stock_news_sentiment", lambda ticker, days, limit: (58.0, "ok"))

    payload = build_price_forecast("2330", horizon_days=10)

    assert payload["status"] == "ok"
    assert payload["ticker"] == "2330"
    assert payload["horizon_days"] == 10
    assert payload["expected_return_20d_pct"] == 5.0
    assert payload["market_state"] in {"RISK-ON", "NEUTRAL", "RISK-OFF"}
    assert len(payload["history"]) == 90
    assert len(payload["forecast"]) == 10
    assert {"base", "upside", "downside", "stress"}.issubset(payload["forecast"][0])
    assert payload["risk_score"] >= 0
    assert payload["risk_factors"]
    assert payload["macro_events"]["event_count"] >= 1
    assert payload["risk_metrics"]["macro_event_count"] >= 1
    assert any(factor["label"] == "Macro Event Cluster" for factor in payload["risk_factors"])
    advanced = payload["advanced_signals"]
    assert advanced["rebound_strength"]["scale_max"] == 10
    assert advanced["crash_detector"]["label"] in {"NORMAL", "WATCH", "DANGER", "EXTREME"}
    assert advanced["historical_context"]["status"] in {"ok", "insufficient"}


def test_build_price_forecast_reports_insufficient_history(monkeypatch):
    monkeypatch.setattr(forecast_service, "_read_daily_history", lambda ticker, limit: (pd.DataFrame(), None))

    payload = build_price_forecast("9999")

    assert payload["status"] == "not_found"
    assert payload["ticker"] == "9999"


def test_build_price_forecast_marks_ticker_outside_prediction_universe(monkeypatch):
    frame = _daily_frame()
    monkeypatch.setattr(forecast_service, "_read_daily_history", lambda ticker, limit: (frame, "test"))
    monkeypatch.setattr(
        forecast_service,
        "_load_latest_prediction_row",
        lambda ticker: (None, "predictions_2026-06-08.csv"),
    )
    monkeypatch.setattr(forecast_service, "_load_etf_component_config", lambda ticker: None)
    monkeypatch.setattr(forecast_service, "_load_name_sector_lookup", lambda ticker: ("ETF", "ETF"))
    monkeypatch.setattr(forecast_service, "score_stock_news_sentiment", lambda ticker, days, limit: (50.0, "ok"))

    payload = build_price_forecast("0050", horizon_days=10)

    assert payload["status"] == "ok"
    assert payload["source"]["prediction"] == "predictions_2026-06-08.csv"
    assert payload["source"]["prediction_status"] == "ticker_not_in_prediction_universe"
    assert payload["model"]["prediction_available"] is False
    assert payload["expected_return_source"] == "recent_return_fallback"
    assert any("not in the production prediction universe" in note for note in payload["notes"])


def test_build_price_forecast_uses_etf_component_proxy(monkeypatch, tmp_path):
    frame = _daily_frame()
    prediction_path = tmp_path / "predictions_2026-06-08.csv"
    prediction_path.write_text(
        "\n".join(
            [
                "ticker,pred_return_20d,recommendation,prob_edge",
                "2330,-0.0800,SELL,-0.20",
                "2317,0.0400,BUY,0.10",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(forecast_service, "_read_daily_history", lambda ticker, limit: (frame, "test"))
    monkeypatch.setattr(
        forecast_service,
        "_load_latest_prediction_row",
        lambda ticker: (None, str(prediction_path)),
    )
    monkeypatch.setattr(
        forecast_service,
        "_load_etf_component_config",
        lambda ticker: {
            "name": "Test ETF",
            "method": "proxy_top_holdings_plus_residual",
            "source_label": "test",
            "source_url": "https://example.test/etf",
            "source_as_of": "2026-06-08",
            "weights_basis": "test weights",
            "min_prediction_coverage": 0.40,
            "components": [
                {"ticker": "2330", "name": "TSMC", "weight": 0.50},
                {"ticker": "2317", "name": "Hon Hai", "weight": 0.20},
            ],
        },
    )
    monkeypatch.setattr(forecast_service, "_load_name_sector_lookup", lambda ticker: ("ETF", "ETF"))
    monkeypatch.setattr(forecast_service, "score_stock_news_sentiment", lambda ticker, days, limit: (50.0, "ok"))

    payload = build_price_forecast("0050", horizon_days=10)

    assert payload["status"] == "ok"
    assert payload["source"]["prediction_status"] == "etf_component_proxy"
    assert payload["expected_return_source"] == "etf_component_weighted_prediction"
    assert payload["model"]["prediction_available"] is False
    assert payload["model"]["etf_proxy_available"] is True
    assert payload["etf_forecast_proxy"]["covered_weight"] == 0.7
    assert payload["etf_forecast_proxy"]["components_used"] == 2
    assert payload["etf_forecast_proxy"]["top_component"]["ticker"] == "2330"
    assert any("component-weighted predictions" in note for note in payload["notes"])
