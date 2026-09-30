from __future__ import annotations

from datetime import datetime

import duckdb
import json

from backend.db.ingest import ensure_narrative_schema
from backend.features.narrative.heat import build_daily_features, get_narrative_labels_for_ticker
from backend.features.narrative.labeler import label_text
from backend.features.narrative.ticker_linker import TickerLinker
from scripts.fetch_news_daily import ArticleMeta, insert_articles


def _article(news_id: str, title: str, published_at: str, source: str = "cnyes") -> ArticleMeta:
    from scripts.fetch_news_daily import _hash_text

    return ArticleMeta(
        news_id=news_id,
        source=source,
        published_at=datetime.fromisoformat(published_at),
        title=title,
        link=f"https://example.test/{news_id}",
        snippet="",
        content_hash=_hash_text(title),
    )


def test_label_text_applies_pm_approved_taxonomy() -> None:
    labels = {item.label: set(item.matched_keywords) for item in label_text("中東戰爭推升油價，PVC報價走升")}

    assert "WAR_PROXY" in labels
    assert "COMMODITY_PRICE" in labels
    assert "PRICE_HIKE" in labels


def test_ticker_linker_prefers_explicit_ticker_then_company_dict() -> None:
    linker = TickerLinker({"1305": {"華夏"}, "6488": {"環球晶"}})

    explicit = linker.link("Factset 最新調查：環球晶(6488-TW)EPS預估上修")
    company = linker.link("中東戰爭推升油價，華夏受惠")

    assert explicit[0].ticker == "6488"
    assert explicit[0].match_method == "ticker_regex"
    assert company[0].ticker == "1305"
    assert company[0].match_method == "company_dict"


def test_ticker_linker_rejects_common_false_positive_patterns() -> None:
    linker = TickerLinker(
        {
            "1227": {"佳格"},
            "1459": {"聯發"},
            "2454": {"聯發科"},
            "5007": {"三星"},
        }
    )

    assert linker.link("AMD單日市值爆增1227億美元") == []
    assert [link.ticker for link in linker.link("聯發科連4漲")] == ["2454"]
    assert linker.link("三星電子決定退出中國家電市場") == []


def test_insert_articles_deduplicates_links_labels_and_builds_features(tmp_path) -> None:
    db_path = tmp_path / "narrative.duckdb"
    conn = duckdb.connect(str(db_path))
    try:
        ensure_narrative_schema(conn)
        linker = TickerLinker({"1305": {"華夏"}, "2317": {"鴻海"}})
        first = _article("cnyes:1", "中東戰爭推升油價，PVC報價走升，華夏受惠", "2026-04-17 10:00:00")
        duplicate = _article("yahoo:1", first.title, "2026-04-17 11:00:00", source="yahoo_rss")
        governance = _article("mops:1", "鴻海配合檢調搜索調查特定員工", "2026-04-17 12:00:00", source="mops")

        stats = insert_articles(conn, [first, duplicate, governance], linker=linker)
        features = build_daily_features(conn, "2026-04-17")

        assert stats["inserted"] == 2
        assert stats["duplicate_skipped"] == 1
        assert set(features["ticker"]) == {"1305", "2317"}

        row_1305 = features.loc[features["ticker"] == "1305"].iloc[0]
        labels_1305 = set(json.loads(row_1305["active_labels"]))
        assert {"WAR_PROXY", "COMMODITY_PRICE", "PRICE_HIKE"}.issubset(labels_1305)
        assert row_1305["heat_3d"] > 0

        row_2317 = features.loc[features["ticker"] == "2317"].iloc[0]
        assert "GOVERNANCE_RISK" in set(json.loads(row_2317["active_labels"]))
    finally:
        conn.close()


def test_low_confidence_links_do_not_enter_daily_features(tmp_path) -> None:
    conn = duckdb.connect(str(tmp_path / "narrative.duckdb"))
    try:
        ensure_narrative_schema(conn)
        now = datetime(2026, 4, 17, 10, 0, 0)
        conn.execute(
            "INSERT INTO news_articles VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ["n1", "cnyes", now, "AI伺服器題材延燒", "https://example.test/n1", "", "h1", now],
        )
        conn.execute(
            "INSERT INTO news_ticker_links VALUES (?, ?, ?, ?, ?)",
            ["n1", "2330", "company_dict", 0.69, now],
        )
        conn.execute(
            "INSERT INTO news_narrative_labels VALUES (?, ?, ?, ?)",
            ["n1", "AI_THEME", "[\"AI\"]", now],
        )
        conn.commit()

        features = build_daily_features(conn, "2026-04-17")

        assert features.empty
    finally:
        conn.close()


def test_get_narrative_labels_for_ticker_returns_latest_feature(tmp_path) -> None:
    conn = duckdb.connect(str(tmp_path / "narrative.duckdb"))
    try:
        ensure_narrative_schema(conn)
        conn.execute(
            "INSERT INTO daily_ticker_narrative_features VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ["2026-04-17", "1305", "[\"WAR_PROXY\"]", 1.0, 1.5, -0.333333, "WAR_PROXY", 0, 1],
        )
        labels = get_narrative_labels_for_ticker("1305", conn=conn)

        assert labels == [
            {
                "label": "WAR_PROXY",
                "date": "2026-04-17",
                "top_label": "WAR_PROXY",
                "heat_3d": 1.0,
                "heat_14d": 1.5,
                "decay_slope": -0.333333,
                "label_age_days": 0,
                "article_count_7d": 1,
            }
        ]
    finally:
        conn.close()
