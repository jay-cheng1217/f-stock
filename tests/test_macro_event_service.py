from __future__ import annotations

from backend.services.macro_event_service import (
    build_macro_event_context,
    build_macro_strategy_context,
    build_regime_anchor_context,
    macro_stock_adjustment,
)


def test_macro_event_context_includes_june_policy_cluster():
    payload = build_macro_event_context("2026-06-08", horizon_days=20)

    assert payload["as_of"] == "2026-06-08"
    assert payload["event_count"] >= 6
    assert payload["risk_level"] in {"medium", "high"}

    titles = [event["title"] for event in payload["events"]]
    assert "US CPI for May 2026" in titles
    assert "US PPI for May 2026" in titles
    assert any("FOMC" in title for title in titles)
    assert any("TAIEX" in title for title in titles)

    spacex = next(event for event in payload["events"] if "SpaceX" in event["title"])
    assert spacex["confidence"] == "unverified_watch"
    assert spacex["event_risk_score"] < 5.0


def test_macro_event_context_filters_past_events():
    payload = build_macro_event_context("2026-06-18", horizon_days=5)

    titles = [event["title"] for event in payload["events"]]
    assert "US CPI for May 2026" not in titles
    assert "FOMC policy decision and statement" in titles
    assert payload["next_event"]["date"] == "2026-06-18"


def test_macro_event_context_extends_beyond_june():
    payload = build_macro_event_context("2026-07-10", horizon_days=20)

    titles = [event["title"] for event in payload["events"]]
    assert "US CPI for June 2026" in titles
    assert "US PPI for June 2026" in titles
    assert any("TAIEX" in title for title in titles)


def test_macro_strategy_context_includes_market_sentiment():
    payload = build_macro_strategy_context("2026-06-08", horizon_days=20)

    assert payload["strategy"]["entry_policy"] == "rank_and_weight_adjustment_no_blanket_block"
    assert 0 <= payload["strategy"]["macro_pressure_score"] <= 100
    assert payload["market_sentiment"]["label"] in {"risk_off", "cautious", "neutral", "risk_on"}
    assert isinstance(payload["market_sentiment"]["metrics"], dict)
    assert payload["regime_anchors"]["anchor_count"] >= 1
    assert "regime_anchor_pressure_delta" in payload["strategy"]


def test_regime_anchor_context_places_user_dates_as_strategy_anchors():
    payload = build_regime_anchor_context("2026-06-08", horizon_days=30)

    anchors = {row["date"]: row for row in payload["anchors"]}
    assert {"2026-06-08", "2026-06-15", "2026-06-30"}.issubset(anchors)
    assert anchors["2026-06-08"]["stance"] == "risk_off"
    assert anchors["2026-06-08"]["pressure_delta"] > 0
    assert anchors["2026-06-15"]["stance"] == "risk_on"
    assert anchors["2026-06-15"]["pressure_delta"] < 0
    assert anchors["2026-06-30"]["stance"] == "risk_off"


def test_macro_stock_adjustment_penalizes_high_beta_more():
    context = {
        "strategy": {"macro_pressure_score": 60.0},
    }
    low_beta = macro_stock_adjustment(beta_60=0.8, gap_pct=0.0, price_vs_ma20=0.0, context=context)
    high_beta = macro_stock_adjustment(beta_60=1.6, gap_pct=0.0, price_vs_ma20=0.0, context=context)

    assert high_beta["macro_stock_penalty_return"] > low_beta["macro_stock_penalty_return"]
    assert high_beta["macro_stock_penalty_multiplier"] < low_beta["macro_stock_penalty_multiplier"]
