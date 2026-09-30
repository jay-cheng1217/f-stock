import json
from datetime import datetime

from backend.services import finnhub_sentiment_service as svc


def test_load_symbol_map_reads_enabled_rows(tmp_path):
    path = tmp_path / "map.csv"
    path.write_text(
        "ticker,name,finnhub_symbol,basis,confidence,enabled,notes\n"
        "2330,TSMC,TSM,adr,high,1,ok\n"
        "9999,Nope,NOPE,adr,low,0,disabled\n",
        encoding="utf-8",
    )

    rows = svc.load_symbol_map(path)

    assert len(rows) == 2
    assert rows[0].ticker == "2330"
    assert rows[0].finnhub_symbol == "TSM"
    assert rows[1].enabled is False


def test_score_raw_payload_shrinks_low_buzz_signal():
    high_conf = svc._score_raw_payload(
        {
            "buzz": {"articlesInLastWeek": 20, "buzz": 1.0},
            "companyNewsScore": 0.9,
            "sectorAverageNewsScore": 0.5,
            "sentiment": {"bullishPercent": 1.0, "bearishPercent": 0.0},
        }
    )
    low_conf = svc._score_raw_payload(
        {
            "buzz": {"articlesInLastWeek": 1, "buzz": 0.1},
            "companyNewsScore": 0.9,
            "sectorAverageNewsScore": 0.5,
            "sentiment": {"bullishPercent": 1.0, "bearishPercent": 0.0},
        }
    )

    assert high_conf["score"] > low_conf["score"] > 50
    assert high_conf["label"] == "positive"


def test_fetch_all_without_token_writes_skipped_snapshot(tmp_path, monkeypatch):
    config = tmp_path / "map.csv"
    data_dir = tmp_path / "data"
    report_dir = tmp_path / "reports"
    latest_json = data_dir / "latest.json"
    latest_md = report_dir / "latest.md"
    config.write_text(
        "ticker,name,finnhub_symbol,basis,confidence,enabled,notes\n"
        "2330,TSMC,TSM,adr,high,1,ok\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(svc, "CONFIG_PATH", config)
    monkeypatch.setattr(svc, "DATA_DIR", data_dir)
    monkeypatch.setattr(svc, "REPORT_DIR", report_dir)
    monkeypatch.setattr(svc, "LATEST_JSON", latest_json)
    monkeypatch.setattr(svc, "LATEST_MD", latest_md)
    monkeypatch.delenv("FINNHUB_TOKEN", raising=False)
    monkeypatch.delenv("FINNHUB_API_KEY", raising=False)
    monkeypatch.setattr(svc, "_read_dotenv_value", lambda name: None)

    payload = svc.fetch_all_sentiments(as_of=datetime(2026, 6, 7, 9, 0, 0))

    assert payload["status"] == "skipped_no_token"
    assert latest_json.exists()
    saved = json.loads(latest_json.read_text(encoding="utf-8"))
    assert saved["records"][0]["status"] == "skipped_no_token"


def test_fetch_provider_error_payload_is_not_ok():
    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"error": "premium access required"}

    class FakeSession:
        def get(self, *args, **kwargs):
            return FakeResponse()

    row = svc.FinnhubSymbolMap(
        ticker="2330",
        name="TSMC",
        finnhub_symbol="TSM",
        basis="adr",
        confidence="high",
        enabled=True,
    )

    payload = svc.fetch_finnhub_news_sentiment(row, "token", session=FakeSession())

    assert payload["status"] == "provider_error"
    assert "premium" in payload["error"]


def test_fetch_news_sentiment_falls_back_to_company_news():
    class SentimentDeniedResponse:
        status_code = 403

        def json(self):
            return {"error": "premium required"}

        def raise_for_status(self):
            return None

    class CompanyNewsResponse:
        status_code = 200

        def json(self):
            return [
                {
                    "headline": "TSMC revenue growth remains strong on AI demand",
                    "summary": "Analysts cite strong AI demand and record expansion.",
                    "datetime": 1,
                    "source": "Test",
                }
            ]

        def raise_for_status(self):
            return None

    class FakeSession:
        def __init__(self):
            self.calls = 0

        def get(self, *args, **kwargs):
            self.calls += 1
            return SentimentDeniedResponse() if self.calls == 1 else CompanyNewsResponse()

    row = svc.FinnhubSymbolMap(
        ticker="2330",
        name="TSMC",
        finnhub_symbol="TSM",
        basis="adr",
        confidence="high",
        enabled=True,
    )
    session = FakeSession()

    payload = svc.fetch_finnhub_news_sentiment(row, "token", session=session)

    assert session.calls == 2
    assert payload["status"] == "ok"
    assert payload["source_endpoint"] == "company-news"
    assert payload["scored"]["score"] > 50
    assert payload["scored"]["source_endpoint"] == "company-news"


def test_maybe_apply_overlay_directly_affects_score_by_default(tmp_path, monkeypatch):
    latest = tmp_path / "latest.json"
    backtest = tmp_path / "backtest.json"
    latest.write_text(
        json.dumps(
            {
                "records": [
                    {
                        "status": "ok",
                        "ticker": "2330",
                        "finnhub_symbol": "TSM",
                        "scored": {
                            "score": 80.0,
                            "label": "positive",
                            "articles_in_last_week": 10,
                            "confidence": 1.0,
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(svc, "LATEST_JSON", latest)
    monkeypatch.setattr(svc, "BACKTEST_LATEST_JSON", backtest)
    monkeypatch.delenv("FINNHUB_NEWS_SENTIMENT_FORCE_SCORING", raising=False)
    monkeypatch.delenv("FINNHUB_NEWS_SENTIMENT_DIRECT_SCORING", raising=False)

    score, detail = svc.maybe_apply_finnhub_overlay(50.0, "local", "2330")

    assert score == 56.0
    assert "direct overlay" in detail


def test_maybe_apply_overlay_can_require_promoted_backtest_when_direct_disabled(tmp_path, monkeypatch):
    latest = tmp_path / "latest.json"
    backtest = tmp_path / "backtest.json"
    latest.write_text(
        json.dumps(
            {
                "records": [
                    {
                        "status": "ok",
                        "ticker": "2330",
                        "finnhub_symbol": "TSM",
                        "scored": {
                            "score": 80.0,
                            "label": "positive",
                            "articles_in_last_week": 10,
                            "confidence": 1.0,
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(svc, "LATEST_JSON", latest)
    monkeypatch.setattr(svc, "BACKTEST_LATEST_JSON", backtest)
    monkeypatch.delenv("FINNHUB_NEWS_SENTIMENT_FORCE_SCORING", raising=False)
    monkeypatch.setenv("FINNHUB_NEWS_SENTIMENT_DIRECT_SCORING", "0")

    assert svc.maybe_apply_finnhub_overlay(50.0, "local", "2330") == (50.0, "local")

    backtest.write_text(json.dumps({"promotion": "promoted"}), encoding="utf-8")
    score, detail = svc.maybe_apply_finnhub_overlay(50.0, "local", "2330")

    assert score == 56.0
    assert "promoted overlay" in detail
