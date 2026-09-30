from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.routers import my_holdings


def _client(tmp_path, monkeypatch) -> TestClient:
    daily_dir = tmp_path / "daily"
    daily_dir.mkdir()
    monkeypatch.setattr(my_holdings, "MY_HOLDINGS_DB_PATH", str(tmp_path / "my_holdings.db"))
    monkeypatch.setattr(my_holdings, "DAILY_K_DIR", str(daily_dir))
    monkeypatch.setattr(my_holdings, "_load_news_alert", lambda ticker: _neutral_news_alert(ticker))
    my_holdings._build_benchmark_groups.cache_clear()
    app = FastAPI()
    app.include_router(my_holdings.router)
    return TestClient(app)


def _neutral_news_alert(ticker: str) -> dict:
    return {
        "status": "no_recent_news",
        "alert_level": "green",
        "score": 50.0,
        "summary": "近7日無重大公告",
        "negative_count": 0,
        "positive_count": 0,
        "neutral_count": 0,
        "latest_date": None,
        "latest_title": None,
        "rows": [],
    }


def _negative_news_alert(ticker: str) -> dict:
    return {
        "status": "ok",
        "alert_level": "yellow",
        "score": 35.0,
        "summary": "近7日負面重大公告 1 則；最新：公告本公司發生重大訴訟",
        "negative_count": 1,
        "positive_count": 0,
        "neutral_count": 0,
        "latest_date": "2026-05-02",
        "latest_title": "公告本公司發生重大訴訟",
        "rows": [
            {
                "ticker": ticker,
                "date": "2026-05-02",
                "time": "09:00:00",
                "title": "公告本公司發生重大訴訟",
                "sentiment": "negative",
                "sentiment_score": 35.0,
            }
        ],
    }


def _write_daily_k(base_dir, ticker: str, closes: list[float] | None = None, volume: int = 900_000) -> None:
    if closes is None:
        closes = [100.0] * 64 + [110.0]
    start = date(2026, 1, 1)
    rows = [
        {
            "Date": (start + timedelta(days=index)).isoformat(),
            "Open": close,
            "High": close,
            "Low": close,
            "Close": close,
            "Volume": volume,
        }
        for index, close in enumerate(closes)
    ]
    pd.DataFrame(rows).to_csv(base_dir / f"{ticker}.csv", index=False)


def test_my_holdings_crud_summary_and_transaction(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    _write_daily_k(tmp_path / "daily", "2330", closes=[110.0] * 65)

    created = client.post(
        "/api/my_holdings",
        json={"ticker": "2330", "shares": 1.5, "avg_cost": 100, "breakeven_price": 98},
    )
    assert created.status_code == 200
    holding = created.json()["data"]
    assert holding["ticker"] == "2330"
    assert holding["asset_type"] == "stock"
    assert holding["current_value"] == 165000
    assert holding["unrealized_pnl_pct"] == 10

    listed = client.get("/api/my_holdings").json()
    assert listed["summary"]["holding_count"] == 1
    assert listed["summary"]["current_value"] == 165000
    assert listed["summary"]["unrealized_pnl"] == 15000

    traded = client.post(
        f"/api/my_holdings/{holding['id']}/transaction",
        json={"action": "buy", "shares": 0.5, "price": 120, "fee": 20, "tax": 0},
    )
    assert traded.status_code == 200
    assert traded.json()["data"]["shares"] == 2
    assert traded.json()["data"]["avg_cost"] == 105

    patched = client.patch(f"/api/my_holdings/{holding['id']}", json={"note": "core holding"})
    assert patched.status_code == 200
    assert patched.json()["data"]["note"] == "core holding"

    deleted = client.delete(f"/api/my_holdings/{holding['id']}")
    assert deleted.status_code == 200
    assert client.get("/api/my_holdings").json()["summary"]["holding_count"] == 0


def test_review_uses_cash_dividend_adjusted_returns(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    _write_daily_k(tmp_path / "daily", "2330", closes=[100.0] * 64 + [90.0])
    monkeypatch.setattr(my_holdings, "cum_dividend", lambda ticker, after_date, until_date: 10.0)
    monkeypatch.setattr(
        my_holdings,
        "_load_stock_prediction_payload",
        lambda ticker: {"recommendation": "觀望", "pred_return_20d": 0.0, "signal": "hold"},
    )
    monkeypatch.setattr(
        my_holdings,
        "_load_prediction_explain_payload",
        lambda ticker: {"recommendation": "觀望", "domain_warnings": []},
    )

    holding = client.post(
        "/api/my_holdings",
        json={
            "ticker": "2330",
            "shares": 1,
            "avg_cost": 100,
            "breakeven_price": 95,
            "entry_date": "2026-01-01",
        },
    ).json()["data"]
    review = client.get(f"/api/my_holdings/{holding['id']}/review").json()

    assert holding["current_value"] == 90000
    assert holding["unrealized_pnl"] == -10000
    assert holding["distribution_cash_adjustment"] == 10000
    assert holding["total_pnl_with_distributions"] == 0
    assert holding["adjusted_return_pct"] == 0
    assert holding["effective_close_with_distributions"] == 100
    assert review["metrics"]["ret_20d_pct"] == 0
    assert not any(
        reason["code"] in {"STOP_LOSS_10PCT", "BELOW_BREAKEVEN", "CRASH_20D"}
        for reason in review["reasons"]
    )


def test_stock_review_emits_add_and_exit_signals(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    _write_daily_k(tmp_path / "daily", "2330", closes=[105.0] * 65)
    monkeypatch.setattr(
        my_holdings,
        "_load_stock_prediction_payload",
        lambda ticker: {"recommendation": "建議買進", "pred_return_20d": 6.2, "signal": "buy"},
    )
    monkeypatch.setattr(
        my_holdings,
        "_load_prediction_explain_payload",
        lambda ticker: {"recommendation": "建議買進", "domain_warnings": []},
    )

    holding_id = client.post(
        "/api/my_holdings",
        json={"ticker": "2330", "shares": 1, "avg_cost": 100, "breakeven_price": 95},
    ).json()["data"]["id"]

    add_review = client.get(f"/api/my_holdings/{holding_id}/review").json()
    assert add_review["signal"] == "ADD"
    assert add_review["rule_pool"] == "prediction_explain_plus_guardrails"
    assert add_review["prediction"]["pred_return_20d"] == 6.2

    client.patch(f"/api/my_holdings/{holding_id}", json={"breakeven_price": 120})
    exit_review = client.get(f"/api/my_holdings/{holding_id}/review").json()
    assert exit_review["signal"] == "EXIT"
    assert any(reason["code"] == "BELOW_BREAKEVEN" for reason in exit_review["reasons"])


def test_negative_news_upgrades_non_exit_review_to_trim(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    _write_daily_k(tmp_path / "daily", "2330", closes=[105.0] * 65)
    monkeypatch.setattr(my_holdings, "_load_news_alert", _negative_news_alert)
    monkeypatch.setattr(
        my_holdings,
        "_load_stock_prediction_payload",
        lambda ticker: {"recommendation": "建議買進", "pred_return_20d": 6.2, "signal": "buy"},
    )
    monkeypatch.setattr(
        my_holdings,
        "_load_prediction_explain_payload",
        lambda ticker: {"recommendation": "建議買進", "domain_warnings": []},
    )

    holding_id = client.post(
        "/api/my_holdings",
        json={"ticker": "2330", "shares": 1, "avg_cost": 100, "breakeven_price": 95},
    ).json()["data"]["id"]

    listed = client.get("/api/my_holdings").json()["data"][0]
    review = client.get(f"/api/my_holdings/{holding_id}/review").json()

    assert listed["news_alert"]["alert_level"] == "yellow"
    assert review["signal"] == "TRIM"
    assert review["news_alert"]["negative_count"] == 1
    assert any(reason["code"] == "NEWS_NEGATIVE_ALERT" for reason in review["reasons"])


def test_etf_review_uses_rule_subset_and_suppresses_v2(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    _write_daily_k(tmp_path / "daily", "00981A", closes=[20.0] * 65)
    monkeypatch.setattr(
        my_holdings,
        "_load_stock_prediction_payload",
        lambda ticker: (_ for _ in ()).throw(AssertionError("ETF must not call stock prediction")),
    )
    monkeypatch.setattr(
        my_holdings,
        "_load_prediction_explain_payload",
        lambda ticker: (_ for _ in ()).throw(AssertionError("ETF must not call prediction explain")),
    )

    holding_id = client.post(
        "/api/my_holdings",
        json={"ticker": "00981A", "shares": 3, "avg_cost": 20, "breakeven_price": 19.8},
    ).json()["data"]["id"]

    review = client.get(f"/api/my_holdings/{holding_id}/review").json()
    assert review["holding"]["asset_type"] == "etf"
    assert review["holding"]["universe_type"] == "etf_active"
    assert review["review_status"] == "ETF_RULE_SUBSET_ONLY"
    assert review["v2_alpha_model_status"] == "ETF_V2_ALPHA_MODEL_UNSUPPORTED"
    assert review["rule_pool"] == "technical_liquidity_price_event_subset"
    assert review["prediction"] is None
    assert review["prediction_explain"] is None


def test_create_rejects_unknown_stock_ticker_without_daily_k(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    response = client.post(
        "/api/my_holdings",
        json={"ticker": "9999", "shares": 1, "avg_cost": 10},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "ticker not found in stock universe or daily K data"


def test_portfolio_review_flags_sector_top3_and_leveraged_etf_exposure(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    monkeypatch.setattr(
        my_holdings,
        "_SECTOR_LOOKUP",
        {"2330": "半導體業", "2317": "其他電子業", "1305": "塑膠工業"},
    )
    monkeypatch.setattr(
        my_holdings,
        "_load_latest_beta_lookup",
        lambda: {"2330": 1.4, "2317": 1.2, "1305": 0.8},
    )
    for ticker in ["2330", "2317", "1305", "0050", "00878", "00631L"]:
        _write_daily_k(tmp_path / "daily", ticker, closes=[100.0] * 65)

    holdings = [
        ("2330", 1),
        ("2317", 1),
        ("1305", 1),
        ("0050", 1),
        ("00878", 1),
        ("00631L", 2),
    ]
    for ticker, shares in holdings:
        response = client.post(
            "/api/my_holdings",
            json={"ticker": ticker, "shares": shares, "avg_cost": 100},
        )
        assert response.status_code == 200

    review = client.get("/api/my_holdings/portfolio_review?cash=300000").json()

    assert review["risk_level"] == "red"
    assert review["summary"]["holding_value"] == 700000
    assert review["summary"]["cash_ratio_pct"] == 30
    assert review["summary"]["leveraged_inverse_etf_weight_pct"] == 28.57
    assert review["summary"]["top3_weight_pct"] == 57.15
    assert review["summary"]["beta_coverage_pct"] == 42.86
    assert {alert["code"] for alert in review["alerts"]} >= {
        "LEVERAGED_INVERSE_ETF_EXPOSURE_RED",
        "TOP3_CONCENTRATION_YELLOW",
        "BETA_60_EXPOSURE",
    }


def test_portfolio_review_can_emit_green_for_diversified_holdings(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    tickers = [f"10{index:02d}" for index in range(1, 8)]
    monkeypatch.setattr(
        my_holdings,
        "_SECTOR_LOOKUP",
        {ticker: f"sector_{index}" for index, ticker in enumerate(tickers)},
    )
    monkeypatch.setattr(
        my_holdings,
        "_load_latest_beta_lookup",
        lambda: {ticker: 1.0 for ticker in tickers},
    )
    for ticker in tickers:
        _write_daily_k(tmp_path / "daily", ticker, closes=[100.0] * 65)
        assert client.post(
            "/api/my_holdings",
            json={"ticker": ticker, "shares": 1, "avg_cost": 100},
        ).status_code == 200

    review = client.get("/api/my_holdings/portfolio_review").json()

    assert review["risk_level"] == "green"
    assert review["summary"]["top3_weight_pct"] == 42.87
    assert review["summary"]["weighted_beta_60"] == 1
    assert [alert["code"] for alert in review["alerts"]] == ["BETA_60_EXPOSURE"]


def test_portfolio_attribution_reconciles_mixed_stock_etf_portfolio(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    monkeypatch.setattr(
        my_holdings,
        "_SECTOR_LOOKUP",
        {"2330": "半導體業", "2317": "其他電子業", "1305": "塑膠工業"},
    )
    monkeypatch.setattr(
        my_holdings,
        "ETF_TICKERS",
        frozenset({"0050", "00878", "00631L"}),
    )
    monkeypatch.setattr(
        my_holdings,
        "_load_twii_return_for_attribution",
        lambda start, end: {"return_pct": 0.03, "start_date": start, "end_date": end},
    )
    closes = {
        "2330": [100.0] * 9 + [100.0] * 10 + [110.0] * 46,
        "2317": [100.0] * 9 + [100.0] * 10 + [95.0] * 46,
        "1305": [100.0] * 9 + [100.0] * 10 + [101.0] * 46,
        "0050": [100.0] * 9 + [100.0] * 10 + [104.0] * 46,
        "00878": [100.0] * 9 + [100.0] * 10 + [102.0] * 46,
        "00631L": [100.0] * 9 + [100.0] * 10 + [108.0] * 46,
    }
    for ticker, values in closes.items():
        _write_daily_k(tmp_path / "daily", ticker, closes=values, volume=900_000)

    for ticker, shares in [
        ("2330", 1),
        ("2317", 1),
        ("1305", 1),
        ("0050", 1),
        ("00878", 1),
        ("00631L", 1),
    ]:
        assert client.post(
            "/api/my_holdings",
            json={"ticker": ticker, "shares": shares, "avg_cost": 100, "entry_date": "2026-01-01"},
        ).status_code == 200

    response = client.get("/api/my_holdings/portfolio_attribution?start=2026-01-10&end=2026-01-20")
    payload = response.json()

    assert response.status_code == 200
    assert payload["summary"]["holding_count"] == 6
    assert payload["summary"]["reconciliation_pass"] is True
    assert abs(payload["summary"]["reconciliation_error_pct"]) <= 0.5
    assert payload["summary"]["alpha_vs_twii_pct"] is not None
    groups = {row["group"] for row in payload["sector_attribution"]}
    assert {"半導體業", "其他電子業", "塑膠工業", "ETF (passive)", "ETF (leveraged)"}.issubset(groups)
    assert payload["benchmark"]["source"] == "local_traded_value_weighted_stock_sector_plus_etf_type_proxy"


def test_portfolio_attribution_handles_empty_and_excludes_future_entry(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    monkeypatch.setattr(
        my_holdings,
        "_load_twii_return_for_attribution",
        lambda start, end: {"return_pct": 0.0, "start_date": start, "end_date": end},
    )

    empty = client.get("/api/my_holdings/portfolio_attribution?start=2026-01-10&end=2026-01-20").json()
    assert empty["summary"]["holding_count"] == 0
    assert empty["summary"]["reconciliation_pass"] is False

    monkeypatch.setattr(my_holdings, "_SECTOR_LOOKUP", {"2330": "半導體業"})
    _write_daily_k(tmp_path / "daily", "2330", closes=[100.0] * 65)
    assert client.post(
        "/api/my_holdings",
        json={"ticker": "2330", "shares": 1, "avg_cost": 100, "entry_date": "2026-02-01"},
    ).status_code == 200

    payload = client.get("/api/my_holdings/portfolio_attribution?start=2026-01-10&end=2026-01-20").json()
    assert payload["summary"]["holding_count"] == 0
    assert payload["summary"]["excluded_holding_count"] == 1
    assert payload["skipped_holdings"][0]["reason"] == "entry_date_after_window"
