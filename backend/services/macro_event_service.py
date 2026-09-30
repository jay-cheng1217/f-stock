"""Macro/policy event context for forecast and stock-selection risk.

The calendar is intentionally explicit and source-labeled. It does not fetch
the internet during production prediction; daily jobs recalculate proximity,
market sentiment, and stock-level penalties from local market data.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd


BASE_DIR = Path(__file__).resolve().parents[2]
INDEX_DIR = BASE_DIR / "大盤指數"
REPORT_DIR = BASE_DIR / "ml" / "reports"
EXTERNAL_SENTIMENT_DIR = BASE_DIR / "ml" / "data" / "external_sentiment"
MACRO_STRATEGY_CONTEXT_JSON = REPORT_DIR / "macro_strategy_context_latest.json"
MACRO_STRATEGY_CONTEXT_MD = REPORT_DIR / "macro_strategy_context_latest.md"

MACRO_EVENT_VERSION = "2026-06-08_strategy_calendar_v2"
MACRO_STRATEGY_VERSION = "macro_strategy_context_v1"
REGIME_ANCHOR_VERSION = "2026-06-08_user_regime_anchors_v1"


@dataclass(frozen=True)
class MacroEvent:
    date: str
    local_time: str | None
    title: str
    region: str
    category: str
    impact: str
    base_risk: float
    risk_bias: str
    source_label: str
    source_url: str
    confidence: str
    notes: str


@dataclass(frozen=True)
class RegimeAnchor:
    date: str
    local_time: str | None
    title: str
    stance: str
    net_score: float
    source_label: str
    confidence: str
    notes: str


STRATEGY_REGIME_ANCHORS: tuple[RegimeAnchor, ...] = (
    RegimeAnchor(
        date="2026-06-08",
        local_time=None,
        title="User-provided market regime anchor",
        stance="risk_off",
        net_score=-3.0,
        source_label="user_provided_regime_watch",
        confidence="user_research_signal",
        notes="Screenshot supplied by user: bearish / risk-off / net -3.",
    ),
    RegimeAnchor(
        date="2026-06-15",
        local_time=None,
        title="User-provided market regime anchor",
        stance="risk_on",
        net_score=6.0,
        source_label="user_provided_regime_watch",
        confidence="user_research_signal",
        notes="Screenshot supplied by user: bullish / risk-on / net +6.",
    ),
    RegimeAnchor(
        date="2026-06-30",
        local_time=None,
        title="User-provided market regime anchor",
        stance="risk_off",
        net_score=-4.0,
        source_label="user_provided_regime_watch",
        confidence="user_research_signal",
        notes="Screenshot supplied by user: bearish / risk-off / net -4.",
    ),
)


def _parse_date(value: str | date | datetime | None) -> date:
    if value is None:
        return datetime.now().date()
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.fromisoformat(str(value)[:10]).date()


def _third_weekday(year: int, month: int, weekday: int) -> date:
    current = date(year, month, 1)
    while current.weekday() != weekday:
        current += timedelta(days=1)
    return current + timedelta(days=14)


def _us_release_time_taipei(value: str) -> str:
    """Return Taipei time for standard 08:30 ET US data releases in 2026."""
    event_date = _parse_date(value)
    if event_date < date(2026, 3, 8) or event_date >= date(2026, 11, 1):
        return "21:30"
    return "20:30"


def _event_window(days_until: int) -> str:
    if days_until < 0:
        return "past"
    if days_until == 0:
        return "today"
    if days_until == 1:
        return "tomorrow"
    if days_until <= 3:
        return "within_3d"
    if days_until <= 7:
        return "within_7d"
    return "within_horizon"


def _proximity_multiplier(days_until: int) -> float:
    if days_until < 0:
        return 0.0
    if days_until == 0:
        return 1.0
    if days_until == 1:
        return 0.85
    if days_until <= 3:
        return 0.65
    if days_until <= 7:
        return 0.38
    return 0.18


def _confidence_multiplier(confidence: str) -> float:
    key = str(confidence or "").lower()
    if key == "official_rule":
        return 0.95
    if key == "official_calendar_plus_rule":
        return 0.85
    if key.startswith("official"):
        return 1.0
    if key == "unverified_watch":
        return 0.35
    return 0.65


def _regime_anchor_confidence_multiplier(confidence: str) -> float:
    key = str(confidence or "").lower()
    if key == "user_research_signal":
        return 0.55
    if key == "validated_research_signal":
        return 0.75
    return 0.45


def _risk_level(score: float) -> str:
    if score >= 45:
        return "high"
    if score >= 24:
        return "medium"
    if score > 0:
        return "low"
    return "none"


def _strategy_action(score: float, event_score: float, sentiment_score: float) -> str:
    if score >= 65:
        return "risk_off_reduce_or_block_high_beta"
    if score >= 45 or event_score >= 45:
        return "risk_control_reduce_high_beta"
    if score >= 25 or sentiment_score <= 45:
        return "watch_position_size"
    return "normal"


def _regime_anchor_pressure_delta(anchor: RegimeAnchor, days_until: int) -> float:
    magnitude = min(abs(float(anchor.net_score)) * 2.0, 18.0)
    signed_direction = -1.0 if anchor.stance == "risk_on" or anchor.net_score > 0 else 1.0
    risk_on_haircut = 0.85 if signed_direction < 0 else 1.0
    return (
        signed_direction
        * magnitude
        * _proximity_multiplier(days_until)
        * _regime_anchor_confidence_multiplier(anchor.confidence)
        * risk_on_haircut
    )


def _build_seeded_events() -> tuple[MacroEvent, ...]:
    events: list[MacroEvent] = []

    cpi_releases = [
        ("2026-01-13", "December 2025"),
        ("2026-02-13", "January 2026"),
        ("2026-03-11", "February 2026"),
        ("2026-04-10", "March 2026"),
        ("2026-05-12", "April 2026"),
        ("2026-06-10", "May 2026"),
        ("2026-07-14", "June 2026"),
        ("2026-08-12", "July 2026"),
        ("2026-09-11", "August 2026"),
        ("2026-10-14", "September 2026"),
        ("2026-11-10", "October 2026"),
        ("2026-12-10", "November 2026"),
    ]
    for release_date, reference_month in cpi_releases:
        events.append(
            MacroEvent(
                date=release_date,
                local_time=_us_release_time_taipei(release_date),
                title=f"US CPI for {reference_month}",
                region="US",
                category="inflation",
                impact="high",
                base_risk=23.0,
                risk_bias="rates_usd_tech_valuation",
                source_label="BLS CPI release schedule",
                source_url="https://www.bls.gov/schedule/news_release/cpi.htm",
                confidence="official",
                notes="BLS release time is 08:30 ET, converted to Asia/Taipei.",
            )
        )

    ppi_releases = [
        ("2026-01-14", "November 2025"),
        ("2026-01-30", "December 2025"),
        ("2026-02-27", "January 2026"),
        ("2026-03-18", "February 2026"),
        ("2026-04-14", "March 2026"),
        ("2026-05-13", "April 2026"),
        ("2026-06-11", "May 2026"),
        ("2026-07-15", "June 2026"),
        ("2026-08-13", "July 2026"),
        ("2026-09-10", "August 2026"),
        ("2026-10-15", "September 2026"),
        ("2026-11-13", "October 2026"),
        ("2026-12-15", "November 2026"),
    ]
    for release_date, reference_month in ppi_releases:
        events.append(
            MacroEvent(
                date=release_date,
                local_time=_us_release_time_taipei(release_date),
                title=f"US PPI for {reference_month}",
                region="US",
                category="inflation",
                impact="high",
                base_risk=18.0,
                risk_bias="rates_input_costs_semiconductor_cycle",
                source_label="BLS PPI release schedule",
                source_url="https://www.bls.gov/schedule/news_release/ppi.htm",
                confidence="official",
                notes="BLS release time is 08:30 ET, converted to Asia/Taipei.",
            )
        )

    fomc_decisions_tw = [
        ("2026-01-29", "January 27-28, 2026"),
        ("2026-03-19", "March 17-18, 2026"),
        ("2026-04-30", "April 28-29, 2026"),
        ("2026-06-18", "June 16-17, 2026"),
        ("2026-07-30", "July 28-29, 2026"),
        ("2026-09-17", "September 15-16, 2026"),
        ("2026-10-29", "October 27-28, 2026"),
        ("2026-12-10", "December 8-9, 2026"),
        ("2027-01-28", "January 26-27, 2027"),
        ("2027-03-18", "March 16-17, 2027"),
        ("2027-04-29", "April 27-28, 2027"),
        ("2027-06-10", "June 8-9, 2027"),
        ("2027-07-29", "July 27-28, 2027"),
        ("2027-09-16", "September 14-15, 2027"),
        ("2027-10-28", "October 26-27, 2027"),
        ("2027-12-09", "December 7-8, 2027"),
    ]
    for release_date, meeting_label in fomc_decisions_tw:
        local_time = "03:00" if release_date[5:7] in {"01", "12"} else "02:00"
        events.append(
            MacroEvent(
                date=release_date,
                local_time=local_time,
                title="FOMC policy decision and statement",
                region="US",
                category="central_bank",
                impact="very_high",
                base_risk=26.0,
                risk_bias="rates_usd_growth_multiple",
                source_label="Federal Reserve FOMC calendar",
                source_url="https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm",
                confidence="official",
                notes=f"Fed meeting {meeting_label}; 14:00 ET decision converted to Asia/Taipei.",
            )
        )

    for year in (2026, 2027):
        for month in range(1, 13):
            settlement = _third_weekday(year, month, 2)
            events.append(
                MacroEvent(
                    date=settlement.isoformat(),
                    local_time="13:30",
                    title="TAIEX futures/options monthly settlement",
                    region="TW",
                    category="derivatives_settlement",
                    impact="high",
                    base_risk=21.0,
                    risk_bias="taiwan_index_rebalance_intraday_volatility",
                    source_label="TAIFEX TAIEX futures specs",
                    source_url="https://www.taifex.com.tw/enl/eng2/tX",
                    confidence="official_rule",
                    notes="TAIFEX TAIEX futures last trading day is the third Wednesday of the delivery month.",
                )
            )

    for year in (2026, 2027):
        for month in (3, 6, 9, 12):
            expiry = _third_weekday(year, month, 4)
            title = "US quarterly options/futures expiration watch"
            notes = "Standard quarterly expiration watch for the third Friday cycle."
            if expiry == date(2026, 6, 19):
                notes = "June 19 is Juneteenth in 2026; listed US options markets are closed, so pressure may pull forward to June 18."
            events.append(
                MacroEvent(
                    date=expiry.isoformat(),
                    local_time=None,
                    title=title,
                    region="US",
                    category="derivatives_expiration",
                    impact="medium",
                    base_risk=11.0,
                    risk_bias="us_liquidity_dealer_gamma_global_risk",
                    source_label="Cboe hours and holiday calendar",
                    source_url="https://www.cboe.com/en/about/hours/us-options/",
                    confidence="official_calendar_plus_rule",
                    notes=notes,
                )
            )

    for release_date, meeting_label in [
        ("2026-06-16", "June 15-16, 2026"),
    ]:
        events.append(
            MacroEvent(
                date=release_date,
                local_time=None,
                title="Bank of Japan monetary policy decision",
                region="JP",
                category="central_bank",
                impact="high",
                base_risk=17.0,
                risk_bias="jpy_rates_exporters_asia_risk",
                source_label="BOJ release schedule",
                source_url="https://www.boj.or.jp/en/about/calendar/",
                confidence="official",
                notes=f"BOJ lists the {meeting_label} Monetary Policy Meeting and related release schedule.",
            )
        )

    events.append(
        MacroEvent(
            date="2026-06-12",
            local_time=None,
            title="SpaceX IPO watch",
            region="US",
            category="ipo_liquidity",
            impact="medium",
            base_risk=7.0,
            risk_bias="risk_appetite_growth_equity_liquidity",
            source_label="SEC EDGAR verification required",
            source_url="https://www.sec.gov/edgar/search/",
            confidence="unverified_watch",
            notes="No official IPO pricing record is assumed; keep as watch-only until an SEC filing or exchange notice exists.",
        )
    )

    deduped: dict[tuple[str, str, str], MacroEvent] = {}
    for event in events:
        deduped[(event.date, event.title, event.region)] = event
    return tuple(sorted(deduped.values(), key=lambda item: (item.date, item.local_time or "", item.title)))


SEEDED_EVENTS: tuple[MacroEvent, ...] = _build_seeded_events()


def _safe_float(value: Any, default: float = math.nan) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if math.isfinite(out) else default


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _read_index_series(name: str, as_of: date) -> dict[str, Any]:
    path = INDEX_DIR / f"index_{name}.csv"
    if not path.exists():
        return {"status": "missing", "path": str(path)}
    try:
        df = pd.read_csv(path)
    except Exception as exc:
        return {"status": "error", "path": str(path), "error": str(exc)}
    if df.empty or "Date" not in df.columns or "Close" not in df.columns:
        return {"status": "empty", "path": str(path)}
    df = df.copy()
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce").dt.date
    df["Close"] = pd.to_numeric(df["Close"], errors="coerce")
    df = df[df["Date"].notna() & df["Close"].notna()]
    df = df[df["Date"] <= as_of].sort_values("Date")
    if df.empty:
        return {"status": "no_asof_data", "path": str(path)}

    close = float(df["Close"].iloc[-1])
    out: dict[str, Any] = {
        "status": "ok",
        "date": df["Date"].iloc[-1].isoformat(),
        "close": round(close, 4),
    }
    for lookback in (1, 3, 5):
        if len(df) > lookback:
            prev = float(df["Close"].iloc[-lookback - 1])
            out[f"ret_{lookback}d"] = round((close / prev - 1.0) * 100.0, 3) if prev else None
    return out


def _load_finnhub_sentiment() -> dict[str, Any]:
    path = EXTERNAL_SENTIMENT_DIR / "finnhub_news_sentiment_latest.json"
    if not path.exists():
        return {"status": "missing", "path": str(path)}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"status": "error", "path": str(path), "error": str(exc)}
    scores: list[float] = []
    labels: list[str] = []
    for record in payload.get("records", []) if isinstance(payload, dict) else []:
        sentiment = record.get("sentiment") if isinstance(record, dict) else None
        if not isinstance(sentiment, dict):
            continue
        score = _safe_float(sentiment.get("score"))
        if math.isfinite(score):
            scores.append(score)
        label = str(sentiment.get("label") or "").strip()
        if label:
            labels.append(label)
    if not scores:
        return {
            "status": "no_scores",
            "path": str(path),
            "generated_at": payload.get("generated_at") if isinstance(payload, dict) else None,
        }
    avg_score = sum(scores) / len(scores)
    return {
        "status": "ok",
        "path": str(path),
        "generated_at": payload.get("generated_at"),
        "score": round(avg_score, 2),
        "record_count": len(scores),
        "labels": labels,
    }


def _sentiment_label(score: float) -> str:
    if score <= 40:
        return "risk_off"
    if score <= 50:
        return "cautious"
    if score >= 60:
        return "risk_on"
    return "neutral"


def build_market_sentiment_context(as_of: str | date | datetime | None = None) -> dict[str, Any]:
    base_date = _parse_date(as_of)
    metrics = {name: _read_index_series(name, base_date) for name in ("VIX", "GSPC", "SOX", "TWII", "USDTWDX")}
    drivers: list[str] = []
    score = 50.0

    vix = metrics.get("VIX", {})
    vix_close = _safe_float(vix.get("close"))
    vix_ret_1d = _safe_float(vix.get("ret_1d"), 0.0)
    if math.isfinite(vix_close):
        if vix_close >= 25:
            score -= 18
            drivers.append(f"VIX elevated {vix_close:.2f}")
        elif vix_close >= 20:
            score -= 12
            drivers.append(f"VIX above 20 at {vix_close:.2f}")
        elif vix_close >= 16:
            score -= 5
            drivers.append(f"VIX moderate {vix_close:.2f}")
        elif vix_close < 14:
            score += 6
            drivers.append(f"VIX calm {vix_close:.2f}")
    if vix_ret_1d >= 15:
        score -= 8
        drivers.append(f"VIX 1D spike {vix_ret_1d:.1f}%")
    elif vix_ret_1d <= -10:
        score += 4
        drivers.append(f"VIX 1D relief {vix_ret_1d:.1f}%")

    gspc_ret_3d = _safe_float(metrics.get("GSPC", {}).get("ret_3d"), 0.0)
    if gspc_ret_3d <= -2:
        score -= 8
        drivers.append(f"S&P 500 3D weak {gspc_ret_3d:.1f}%")
    elif gspc_ret_3d >= 1.5:
        score += 6
        drivers.append(f"S&P 500 3D firm {gspc_ret_3d:.1f}%")

    sox_ret_3d = _safe_float(metrics.get("SOX", {}).get("ret_3d"), 0.0)
    if sox_ret_3d <= -4:
        score -= 10
        drivers.append(f"SOX 3D weak {sox_ret_3d:.1f}%")
    elif sox_ret_3d >= 2:
        score += 8
        drivers.append(f"SOX 3D firm {sox_ret_3d:.1f}%")

    twii_ret_5d = _safe_float(metrics.get("TWII", {}).get("ret_5d"), 0.0)
    if twii_ret_5d <= -3:
        score -= 10
        drivers.append(f"TWII 5D weak {twii_ret_5d:.1f}%")
    elif twii_ret_5d >= 2:
        score += 8
        drivers.append(f"TWII 5D firm {twii_ret_5d:.1f}%")

    usdtwd_ret_5d = _safe_float(metrics.get("USDTWDX", {}).get("ret_5d"), 0.0)
    if usdtwd_ret_5d >= 1:
        score -= 5
        drivers.append(f"USD/TWD 5D TWD weakness {usdtwd_ret_5d:.1f}%")
    elif usdtwd_ret_5d <= -1:
        score += 4
        drivers.append(f"USD/TWD 5D TWD strength {usdtwd_ret_5d:.1f}%")

    finnhub = _load_finnhub_sentiment()
    if finnhub.get("status") == "ok":
        news_score = _safe_float(finnhub.get("score"), 50.0)
        news_delta = _clamp((news_score - 50.0) * 0.25, -8.0, 8.0)
        score += news_delta
        drivers.append(f"Finnhub mapped-news sentiment {news_score:.1f}")

    final_score = round(_clamp(score, 0.0, 100.0), 1)
    return {
        "as_of": base_date.isoformat(),
        "score": final_score,
        "label": _sentiment_label(final_score),
        "drivers": drivers,
        "metrics": metrics,
        "finnhub_news_sentiment": finnhub,
        "method": "local VIX/SPX/SOX/TWII/USD-TWD plus latest mapped Finnhub news sentiment",
    }


def build_macro_event_context(
    as_of: str | date | datetime | None = None,
    horizon_days: int = 20,
) -> dict[str, Any]:
    base_date = _parse_date(as_of)
    horizon = max(1, min(int(horizon_days or 20), 120))
    rows: list[dict[str, Any]] = []
    total_risk = 0.0

    for event in SEEDED_EVENTS:
        event_date = _parse_date(event.date)
        days_until = (event_date - base_date).days
        if days_until < 0 or days_until > horizon:
            continue
        row = asdict(event)
        event_score = event.base_risk * _proximity_multiplier(days_until) * _confidence_multiplier(event.confidence)
        row.update(
            {
                "days_until": days_until,
                "window": _event_window(days_until),
                "event_risk_score": round(event_score, 2),
            }
        )
        total_risk += event_score
        rows.append(row)

    rows.sort(key=lambda item: (item["date"], item.get("local_time") or "", item["title"]))
    score = round(min(total_risk, 100.0), 1)
    return {
        "version": MACRO_EVENT_VERSION,
        "as_of": base_date.isoformat(),
        "horizon_days": horizon,
        "risk_score": score,
        "risk_level": _risk_level(score),
        "events": rows,
        "event_count": len(rows),
        "official_event_count": sum(1 for row in rows if str(row.get("confidence", "")).startswith("official")),
        "unverified_watch_count": sum(1 for row in rows if row.get("confidence") == "unverified_watch"),
        "next_event": rows[0] if rows else None,
        "method": "seeded official-source calendar with proximity-weighted advisory risk",
    }


def build_regime_anchor_context(
    as_of: str | date | datetime | None = None,
    horizon_days: int = 30,
) -> dict[str, Any]:
    base_date = _parse_date(as_of)
    horizon = max(1, min(int(horizon_days or 30), 120))
    rows: list[dict[str, Any]] = []
    total_delta = 0.0

    for anchor in STRATEGY_REGIME_ANCHORS:
        anchor_date = _parse_date(anchor.date)
        days_until = (anchor_date - base_date).days
        if days_until < 0 or days_until > horizon:
            continue
        delta = _regime_anchor_pressure_delta(anchor, days_until)
        row = asdict(anchor)
        row.update(
            {
                "days_until": days_until,
                "window": _event_window(days_until),
                "pressure_delta": round(delta, 2),
                "strategy_use": "market_regime_watch_not_official_macro_event",
            }
        )
        total_delta += delta
        rows.append(row)

    rows.sort(key=lambda item: (item["date"], item.get("local_time") or "", item["stance"]))
    return {
        "version": REGIME_ANCHOR_VERSION,
        "as_of": base_date.isoformat(),
        "horizon_days": horizon,
        "pressure_delta": round(total_delta, 1),
        "anchors": rows,
        "anchor_count": len(rows),
        "risk_on_count": sum(1 for row in rows if row.get("stance") == "risk_on"),
        "risk_off_count": sum(1 for row in rows if row.get("stance") == "risk_off"),
        "method": "user/research regime anchors with lower confidence than official macro events",
    }


def build_macro_strategy_context(
    as_of: str | date | datetime | None = None,
    horizon_days: int = 20,
) -> dict[str, Any]:
    events = build_macro_event_context(as_of=as_of, horizon_days=horizon_days)
    sentiment = build_market_sentiment_context(as_of=events["as_of"])
    regime_anchors = build_regime_anchor_context(
        as_of=events["as_of"],
        horizon_days=max(int(horizon_days or 20), 30),
    )
    event_score = _safe_float(events.get("risk_score"), 0.0)
    sentiment_score = _safe_float(sentiment.get("score"), 50.0)
    sentiment_drag = max(0.0, 50.0 - sentiment_score) * 0.7
    anchor_delta = _safe_float(regime_anchors.get("pressure_delta"), 0.0)
    pressure = round(_clamp(event_score + sentiment_drag + anchor_delta, 0.0, 100.0), 1)
    action = _strategy_action(pressure, event_score, sentiment_score)
    return {
        "version": MACRO_STRATEGY_VERSION,
        "as_of": events["as_of"],
        "horizon_days": events["horizon_days"],
        "events": events,
        "market_sentiment": sentiment,
        "regime_anchors": regime_anchors,
        "strategy": {
            "macro_pressure_score": pressure,
            "regime_anchor_pressure_delta": anchor_delta,
            "action": action,
            "entry_policy": "rank_and_weight_adjustment_no_blanket_block",
            "notes": (
                "Macro events reduce ranking/position size for high-beta, hot, or gap-risk names; "
                "they do not globally block all entries."
            ),
        },
        "next_event": events.get("next_event"),
    }


def macro_stock_adjustment(
    *,
    beta_60: Any = None,
    gap_pct: Any = None,
    price_vs_ma20: Any = None,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    context = context or build_macro_strategy_context()
    pressure = _safe_float(context.get("strategy", {}).get("macro_pressure_score"), 0.0)
    pressure_ratio = _clamp(pressure / 100.0, 0.0, 1.0)
    beta = _safe_float(beta_60, 1.0)
    if not math.isfinite(beta):
        beta = 1.0
    gap = max(0.0, _safe_float(gap_pct, 0.0))
    ma20 = max(0.0, _safe_float(price_vs_ma20, 0.0))

    base_penalty = pressure_ratio * 0.006
    beta_penalty = max(0.0, beta - 1.0) * pressure_ratio * 0.045
    gap_penalty = gap * pressure_ratio * 0.35
    heat_penalty = ma20 * pressure_ratio * 0.12
    total_penalty = _clamp(base_penalty + beta_penalty + gap_penalty + heat_penalty, 0.0, 0.09)
    multiplier = _clamp(1.0 - total_penalty, 0.75, 1.0)

    if pressure >= 65 and beta >= 1.5:
        action = "quarter_or_half_size_high_beta"
        multiplier = min(multiplier, 0.50)
    elif pressure >= 45 and beta >= 1.25:
        action = "half_size_high_beta"
        multiplier = min(multiplier, 0.75)
    elif pressure >= 25:
        action = "size_down_if_hot_or_gap"
    else:
        action = "normal"

    return {
        "macro_stock_penalty_return": round(total_penalty, 6),
        "macro_stock_penalty_multiplier": round(multiplier, 4),
        "macro_stock_action": action,
    }


def write_macro_strategy_context(
    as_of: str | date | datetime | None = None,
    horizon_days: int = 20,
    report_dir: str | Path = REPORT_DIR,
) -> dict[str, Any]:
    payload = build_macro_strategy_context(as_of=as_of, horizon_days=horizon_days)
    target_dir = Path(report_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    json_path = target_dir / MACRO_STRATEGY_CONTEXT_JSON.name
    md_path = target_dir / MACRO_STRATEGY_CONTEXT_MD.name

    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    event_rows = payload["events"].get("events", [])
    sentiment = payload["market_sentiment"]
    regime_anchors = payload.get("regime_anchors", {})
    strategy = payload["strategy"]
    lines = [
        "# Macro Strategy Context",
        "",
        f"- as_of: {payload['as_of']}",
        f"- horizon_days: {payload['horizon_days']}",
        f"- macro_pressure_score: {strategy['macro_pressure_score']}",
        f"- strategy_action: {strategy['action']}",
        f"- event_risk_score: {payload['events']['risk_score']} ({payload['events']['risk_level']})",
        f"- market_sentiment: {sentiment['score']} ({sentiment['label']})",
        f"- regime_anchor_pressure_delta: {strategy.get('regime_anchor_pressure_delta', 0.0)}",
        "",
        "## Upcoming Events",
        "",
        "| date | days | event | impact | confidence | event_risk |",
        "| --- | ---: | --- | --- | --- | ---: |",
    ]
    for event in event_rows[:20]:
        lines.append(
            "| {date} | {days_until} | {title} | {impact} | {confidence} | {event_risk_score} |".format(
                **event
            )
        )
    lines.extend(
        [
            "",
            "## Regime Anchors",
            "",
            "| date | days | stance | net_score | pressure_delta | confidence |",
            "| --- | ---: | --- | ---: | ---: | --- |",
        ]
    )
    for anchor in regime_anchors.get("anchors", []):
        lines.append(
            "| {date} | {days_until} | {stance} | {net_score} | {pressure_delta} | {confidence} |".format(
                **anchor
            )
        )
    lines.extend(
        [
            "",
            "## Market Sentiment Drivers",
            "",
        ]
    )
    for driver in sentiment.get("drivers", []):
        lines.append(f"- {driver}")
    lines.extend(
        [
            "",
            "## Production Use",
            "",
            "- 20D prediction ranking subtracts per-stock macro penalty from risk_adjusted_return and leaderboard_score.",
            "- Unified signals cap target_weight_ratio for high-beta/hot/gap-risk names when macro pressure is elevated.",
            "- This is not a blanket hard gate; macro context must not erase the whole entry list by itself.",
        ]
    )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    result = dict(payload)
    result["paths"] = {"json": str(json_path), "md": str(md_path)}
    return result
