from datetime import datetime

from backend.services import tw_news_intel_service as tw
from backend.services.tw_news_intel_service import RawNewsItem, build_news_intel_dashboard, build_ticker_news_intel, score_news_item


def test_score_news_item_marks_direct_negative_mops_as_red() -> None:
    item = RawNewsItem(
        source_type="mops",
        provider="MOPS",
        dimension="announcements",
        title="2330 台積電 因違規遭裁罰",
        source="MOPS",
        published_date=datetime.now().date().isoformat(),
        date_status="fresh",
    )

    scored = score_news_item(item, ticker="2330", name="台積電")

    assert scored["risk_level"] == "red"
    assert scored["sentiment"] == "negative"
    assert scored["relevance_category"] == "direct_stock_news"
    assert "risk_keyword" in scored["relevance_reasons"]


def test_build_ticker_news_intel_uses_local_mops_without_web(monkeypatch) -> None:
    today = datetime.now().date().isoformat()

    monkeypatch.setattr(tw, "_configured_providers", lambda: [])
    monkeypatch.setattr(tw, "_lookup_stock_name", lambda ticker: "台積電")
    monkeypatch.setattr(
        tw,
        "get_stock_news",
        lambda ticker, n: [
            {
                "date": today,
                "time": "08:30:00",
                "title": "2330 台積電 營收創高",
                "market": "上市",
            }
        ],
    )

    payload = build_ticker_news_intel("2330", days=7, limit=5, include_web=True)
    report = payload["report"]

    assert payload["status"] == "success"
    assert report["web_search_status"] == "no_provider_configured"
    assert report["items"][0]["source_type"] == "mops"
    assert report["items"][0]["sentiment"] == "positive"


def test_build_dashboard_uses_scope_targets_and_summarizes(monkeypatch) -> None:
    today = datetime.now().date().isoformat()

    monkeypatch.setattr(tw, "_configured_providers", lambda: [])
    monkeypatch.setattr(tw, "_targets_for_scope", lambda scope, limit: ([{"ticker": "2454", "name": "聯發科"}], "mock.csv"))
    monkeypatch.setattr(
        tw,
        "get_stock_news",
        lambda ticker, n: [
            {
                "date": today,
                "time": "09:00:00",
                "title": "2454 聯發科 法說 展望成長",
                "market": "上市",
            }
        ],
    )

    payload = build_news_intel_dashboard(scope="top", days=7, limit=3, include_web=False)

    assert payload["summary"]["ticker_count"] == 1
    assert payload["summary"]["item_count"] == 1
    assert payload["summary"]["source_counts"]["mops"] == 1
    assert payload["reports"][0]["ticker"] == "2454"


def test_dashboard_web_budget_exhaustion_keeps_mops_report(monkeypatch) -> None:
    today = datetime.now().date().isoformat()

    monkeypatch.setattr(tw, "WEB_BUDGET_SECONDS", -1.0)
    monkeypatch.setattr(tw, "_configured_providers", lambda: [tw.WebProvider(name="Fake", kind="fake", credential="x")])
    monkeypatch.setattr(tw, "_targets_for_scope", lambda scope, limit: ([{"ticker": "2330", "name": "台積電"}], "mock.csv"))
    monkeypatch.setattr(
        tw,
        "get_stock_news",
        lambda ticker, n: [
            {
                "date": today,
                "time": "09:00:00",
                "title": "2330 台積電 本公司受邀參加法說會",
                "market": "上市",
            }
        ],
    )

    payload = build_news_intel_dashboard(scope="top", days=7, limit=1, include_web=True)

    assert payload["reports"][0]["web_search_status"] == "budget_exhausted"
    assert payload["summary"]["item_count"] == 1
    assert payload["reports"][0]["items"][0]["source_type"] == "mops"
