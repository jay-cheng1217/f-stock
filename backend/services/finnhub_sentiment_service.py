"""Finnhub news sentiment overlay for Taiwan equity research surfaces.

Finnhub's news-sentiment endpoint is US-company/Premium scoped. This module
therefore treats it as an external ADR/global-sentiment overlay, not as a
replacement for local MOPS/Taiwan news scoring.
"""

from __future__ import annotations

import csv
import json
import os
import shutil
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import requests


BASE_DIR = Path(os.environ.get("STOCK_BASE_DIR") or Path(__file__).resolve().parents[2])
CONFIG_PATH = BASE_DIR / "config" / "finnhub_symbol_map.csv"
DATA_DIR = BASE_DIR / "ml" / "data" / "external_sentiment"
REPORT_DIR = BASE_DIR / "ml" / "reports"
LATEST_JSON = DATA_DIR / "finnhub_news_sentiment_latest.json"
LATEST_MD = REPORT_DIR / "finnhub_news_sentiment_latest.md"
BACKTEST_LATEST_JSON = REPORT_DIR / "finnhub_news_overlay_backtest_latest.json"
FINNHUB_NEWS_SENTIMENT_URL = "https://finnhub.io/api/v1/news-sentiment"
FINNHUB_COMPANY_NEWS_URL = "https://finnhub.io/api/v1/company-news"

POSITIVE_COMPANY_NEWS_KEYWORDS = (
    "beat",
    "beats",
    "upgrade",
    "upgraded",
    "raise",
    "raised",
    "growth",
    "profit",
    "revenue",
    "earnings",
    "rally",
    "gain",
    "gains",
    "bullish",
    "strong",
    "record",
    "expansion",
    "demand",
    "ai",
)

NEGATIVE_COMPANY_NEWS_KEYWORDS = (
    "miss",
    "misses",
    "downgrade",
    "downgraded",
    "cut",
    "decline",
    "declines",
    "fall",
    "falls",
    "lawsuit",
    "investigation",
    "risk",
    "tariff",
    "weak",
    "loss",
    "layoff",
    "ban",
    "sanction",
    "slump",
    "bearish",
)


@dataclass(frozen=True)
class FinnhubSymbolMap:
    ticker: str
    name: str
    finnhub_symbol: str
    basis: str
    confidence: str
    enabled: bool
    notes: str = ""


def _truthy(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on", "y"}


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _read_dotenv_value(name: str) -> str | None:
    env_path = BASE_DIR / ".env"
    if not env_path.exists():
        return None
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            if key.strip() == name:
                return value.strip().strip('"').strip("'")
    except OSError:
        return None
    return None


def get_finnhub_token() -> str | None:
    for name in ("FINNHUB_TOKEN", "FINNHUB_API_KEY"):
        value = os.environ.get(name) or _read_dotenv_value(name)
        if value:
            return value.strip()
    return None


def load_symbol_map(path: Path | None = None) -> list[FinnhubSymbolMap]:
    path = path or CONFIG_PATH
    if not path.exists():
        return []
    rows: list[FinnhubSymbolMap] = []
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            ticker = str(row.get("ticker") or "").strip()
            symbol = str(row.get("finnhub_symbol") or "").strip()
            if not ticker or not symbol:
                continue
            rows.append(
                FinnhubSymbolMap(
                    ticker=ticker,
                    name=str(row.get("name") or "").strip(),
                    finnhub_symbol=symbol,
                    basis=str(row.get("basis") or "adr").strip(),
                    confidence=str(row.get("confidence") or "unknown").strip(),
                    enabled=_truthy(row.get("enabled"), default=True),
                    notes=str(row.get("notes") or "").strip(),
                )
            )
    return rows


def _score_raw_payload(raw: dict[str, Any]) -> dict[str, Any]:
    sentiment = raw.get("sentiment") or {}
    buzz = raw.get("buzz") or {}
    bullish = _safe_float(sentiment.get("bullishPercent"))
    bearish = _safe_float(sentiment.get("bearishPercent"))
    company_score = _safe_float(raw.get("companyNewsScore"), default=0.5)
    sector_score = _safe_float(raw.get("sectorAverageNewsScore"), default=0.5)
    articles = _safe_float(buzz.get("articlesInLastWeek"))
    buzz_ratio = _safe_float(buzz.get("buzz"))

    directional_edge = bullish - bearish
    relative_edge = company_score - sector_score
    raw_score = 50.0 + directional_edge * 35.0 + relative_edge * 20.0

    article_confidence = min(1.0, articles / 5.0) if articles > 0 else 0.0
    buzz_confidence = min(1.0, max(0.0, buzz_ratio))
    confidence = _clamp((article_confidence * 0.7 + buzz_confidence * 0.3), 0.0, 1.0)
    score = 50.0 + (raw_score - 50.0) * confidence
    score = round(_clamp(score, 10.0, 90.0), 1)

    if score >= 60:
        label = "positive"
    elif score <= 40:
        label = "negative"
    else:
        label = "neutral"

    return {
        "score": score,
        "label": label,
        "confidence": round(confidence, 3),
        "bullish_percent": bullish,
        "bearish_percent": bearish,
        "company_news_score": company_score,
        "sector_average_news_score": sector_score,
        "articles_in_last_week": int(articles),
        "buzz": buzz_ratio,
    }


def _score_company_news_items(items: list[dict[str, Any]]) -> dict[str, Any]:
    usable = [item for item in items if str(item.get("headline") or "").strip()]
    article_scores: list[float] = []
    positive_hits = 0
    negative_hits = 0
    for item in usable[:25]:
        text = f"{item.get('headline') or ''} {item.get('summary') or ''}".lower()
        pos = sum(1 for keyword in POSITIVE_COMPANY_NEWS_KEYWORDS if keyword in text)
        neg = sum(1 for keyword in NEGATIVE_COMPANY_NEWS_KEYWORDS if keyword in text)
        positive_hits += pos
        negative_hits += neg
        article_scores.append(_clamp(50.0 + (pos - neg) * 8.0, 20.0, 80.0))

    if not article_scores:
        score = 50.0
    else:
        avg_score = sum(article_scores) / len(article_scores)
        confidence = min(1.0, len(article_scores) / 5.0)
        score = 50.0 + (avg_score - 50.0) * confidence

    score = round(_clamp(score, 10.0, 90.0), 1)
    if score >= 60:
        label = "positive"
    elif score <= 40:
        label = "negative"
    else:
        label = "neutral"

    return {
        "score": score,
        "label": label,
        "confidence": round(min(1.0, len(article_scores) / 5.0), 3),
        "bullish_percent": None,
        "bearish_percent": None,
        "company_news_score": None,
        "sector_average_news_score": None,
        "articles_in_last_week": len(usable),
        "buzz": None,
        "positive_keyword_hits": positive_hits,
        "negative_keyword_hits": negative_hits,
        "source_endpoint": "company-news",
    }


def fetch_finnhub_company_news_overlay(
    mapping: FinnhubSymbolMap,
    token: str,
    *,
    session: requests.Session | None = None,
    timeout_seconds: int = 30,
    days: int = 7,
    fallback_reason: str = "",
) -> dict[str, Any]:
    from datetime import date, timedelta

    client = session or requests.Session()
    today = date.today()
    start = (today - timedelta(days=max(1, int(days)))).isoformat()
    response = client.get(
        FINNHUB_COMPANY_NEWS_URL,
        params={"symbol": mapping.finnhub_symbol, "from": start, "to": today.isoformat(), "token": token},
        timeout=timeout_seconds,
    )
    if response.status_code in {401, 403}:
        return {
            "status": "unauthorized",
            "ticker": mapping.ticker,
            "name": mapping.name,
            "finnhub_symbol": mapping.finnhub_symbol,
            "basis": mapping.basis,
            "confidence": mapping.confidence,
            "http_status": response.status_code,
            "error": "Finnhub company-news token rejected",
            "fallback_reason": fallback_reason,
        }
    if response.status_code == 429:
        return {
            "status": "rate_limited",
            "ticker": mapping.ticker,
            "name": mapping.name,
            "finnhub_symbol": mapping.finnhub_symbol,
            "basis": mapping.basis,
            "confidence": mapping.confidence,
            "http_status": response.status_code,
            "error": "Finnhub company-news rate limit",
            "fallback_reason": fallback_reason,
        }
    response.raise_for_status()
    raw = response.json()
    if isinstance(raw, dict) and (raw.get("error") or raw.get("detail")):
        return {
            "status": "provider_error",
            "ticker": mapping.ticker,
            "name": mapping.name,
            "finnhub_symbol": mapping.finnhub_symbol,
            "basis": mapping.basis,
            "confidence": mapping.confidence,
            "http_status": response.status_code,
            "error": str(raw.get("error") or raw.get("detail")),
            "fallback_reason": fallback_reason,
            "raw": raw,
        }
    if not isinstance(raw, list):
        raw = []
    scored = _score_company_news_items(raw)
    return {
        "status": "ok",
        "ticker": mapping.ticker,
        "name": mapping.name,
        "finnhub_symbol": mapping.finnhub_symbol,
        "basis": mapping.basis,
        "confidence": mapping.confidence,
        "raw": raw[:10],
        "scored": scored,
        "source_endpoint": "company-news",
        "fallback_reason": fallback_reason,
    }


def fetch_finnhub_news_sentiment(
    mapping: FinnhubSymbolMap,
    token: str,
    *,
    session: requests.Session | None = None,
    timeout_seconds: int = 30,
) -> dict[str, Any]:
    client = session or requests.Session()
    response = client.get(
        FINNHUB_NEWS_SENTIMENT_URL,
        params={"symbol": mapping.finnhub_symbol, "token": token},
        timeout=timeout_seconds,
    )
    if response.status_code in {401, 403}:
        return fetch_finnhub_company_news_overlay(
            mapping,
            token,
            session=session,
            timeout_seconds=timeout_seconds,
            fallback_reason="news-sentiment unauthorized_or_non_premium",
        )
    if response.status_code == 429:
        return {
            "status": "rate_limited",
            "ticker": mapping.ticker,
            "name": mapping.name,
            "finnhub_symbol": mapping.finnhub_symbol,
            "basis": mapping.basis,
            "confidence": mapping.confidence,
            "http_status": response.status_code,
            "error": "Finnhub rate limit",
        }
    response.raise_for_status()
    raw = response.json()
    if not isinstance(raw, dict) or not raw:
        return {
            "status": "empty",
            "ticker": mapping.ticker,
            "name": mapping.name,
            "finnhub_symbol": mapping.finnhub_symbol,
            "basis": mapping.basis,
            "confidence": mapping.confidence,
            "raw": raw,
        }
    if raw.get("error") or raw.get("detail"):
        return fetch_finnhub_company_news_overlay(
            mapping,
            token,
            session=session,
            timeout_seconds=timeout_seconds,
            fallback_reason=f"news-sentiment provider_error: {raw.get('error') or raw.get('detail')}",
        )

    scored = _score_raw_payload(raw)
    return {
        "status": "ok",
        "ticker": mapping.ticker,
        "name": mapping.name,
        "finnhub_symbol": mapping.finnhub_symbol,
        "basis": mapping.basis,
        "confidence": mapping.confidence,
        "raw": raw,
        "scored": scored,
        "source_endpoint": "news-sentiment",
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def render_finnhub_sentiment_md(payload: dict[str, Any]) -> str:
    rows = payload.get("records") or []
    lines = [
        "# Finnhub News Sentiment Overlay",
        "",
        f"- Generated at: {payload.get('generated_at')}",
        f"- Status: {payload.get('status')}",
        f"- Mode: {payload.get('mode')}",
        f"- Mapped tickers: {payload.get('mapped_ticker_count', 0)}",
        f"- OK records: {payload.get('ok_count', 0)}",
        "",
        "| ticker | ADR symbol | status | score | label | confidence | articles |",
        "| - | - | - | -: | - | -: | -: |",
    ]
    for row in rows:
        scored = row.get("scored") or {}
        lines.append(
            "| {ticker} | {symbol} | {status} | {score} | {label} | {confidence} | {articles} |".format(
                ticker=row.get("ticker") or "-",
                symbol=row.get("finnhub_symbol") or "-",
                status=row.get("status") or "-",
                score=scored.get("score", "-"),
                label=scored.get("label", "-"),
                confidence=scored.get("confidence", "-"),
                articles=scored.get("articles_in_last_week", "-"),
            )
        )
    lines.extend(
        [
            "",
            "Notes:",
            "- Finnhub news-sentiment is a US-company/Premium endpoint.",
            "- Taiwan ticker mappings are ADR/global sentiment overlays, not local MOPS replacements.",
            "- Production scoring blends OK Finnhub records into local news scores at 20% direct overlay weight.",
        ]
    )
    return "\n".join(lines) + "\n"


def fetch_all_sentiments(
    *,
    tickers: set[str] | None = None,
    session: requests.Session | None = None,
    as_of: datetime | None = None,
) -> dict[str, Any]:
    as_of = as_of or datetime.now()
    token = get_finnhub_token()
    mappings = [row for row in load_symbol_map() if row.enabled]
    if tickers:
        mappings = [row for row in mappings if row.ticker in tickers]

    if not token:
        payload = {
            "status": "skipped_no_token",
            "mode": "advisory_overlay",
            "generated_at": as_of.isoformat(timespec="seconds"),
            "source": "finnhub_news_sentiment",
            "mapped_ticker_count": len(mappings),
            "ok_count": 0,
            "records": [asdict(row) | {"status": "skipped_no_token"} for row in mappings],
        }
    else:
        records = [fetch_finnhub_news_sentiment(row, token, session=session) for row in mappings]
        payload = {
            "status": "success",
            "mode": "advisory_overlay",
            "generated_at": as_of.isoformat(timespec="seconds"),
            "source": "finnhub_news_sentiment",
            "mapped_ticker_count": len(mappings),
            "ok_count": sum(1 for row in records if row.get("status") == "ok"),
            "records": records,
        }

    stamp = as_of.strftime("%Y%m%d")
    daily_json = DATA_DIR / f"finnhub_news_sentiment_{stamp}.json"
    daily_md = REPORT_DIR / f"finnhub_news_sentiment_{stamp}.md"
    _write_json(daily_json, payload)
    _write_json(LATEST_JSON, payload)
    md = render_finnhub_sentiment_md(payload)
    _write_text(daily_md, md)
    _write_text(LATEST_MD, md)
    return payload | {"json_path": str(daily_json), "latest_json_path": str(LATEST_JSON)}


def load_latest_sentiment_payload(path: Path | None = None) -> dict[str, Any] | None:
    path = path or LATEST_JSON
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def latest_record_by_ticker(ticker: str) -> dict[str, Any] | None:
    payload = load_latest_sentiment_payload()
    if not payload:
        return None
    key = str(ticker or "").strip()
    for row in payload.get("records") or []:
        if str(row.get("ticker") or "").strip() == key:
            return row
    return None


def latest_backtest_promoted(path: Path | None = None) -> bool:
    if _truthy(os.environ.get("FINNHUB_NEWS_SENTIMENT_FORCE_SCORING")):
        return True
    path = path or BACKTEST_LATEST_JSON
    if not path.exists():
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return str(payload.get("promotion") or "").lower() == "promoted"


def direct_scoring_enabled() -> bool:
    """Whether Finnhub overlay should directly affect local news sentiment scores."""
    return _truthy(os.environ.get("FINNHUB_NEWS_SENTIMENT_DIRECT_SCORING"), default=True)


def score_ticker_finnhub_sentiment(ticker: str) -> tuple[float, str, dict[str, Any]]:
    row = latest_record_by_ticker(ticker)
    if not row:
        return 50.0, "Finnhub overlay unavailable", {"status": "missing"}
    if row.get("status") != "ok":
        return 50.0, f"Finnhub overlay {row.get('status')}", row
    scored = row.get("scored") or {}
    score = _safe_float(scored.get("score"), default=50.0)
    detail = (
        "Finnhub {symbol} {endpoint}: score={score:.1f}, label={label}, articles={articles}, confidence={confidence}".format(
            symbol=row.get("finnhub_symbol") or "-",
            endpoint=scored.get("source_endpoint") or row.get("source_endpoint") or "news-sentiment",
            score=score,
            label=scored.get("label") or "-",
            articles=scored.get("articles_in_last_week", "-"),
            confidence=scored.get("confidence", "-"),
        )
    )
    return score, detail, row


def maybe_apply_finnhub_overlay(local_score: float, local_detail: str, ticker: str) -> tuple[float, str]:
    if not direct_scoring_enabled() and not latest_backtest_promoted():
        return local_score, local_detail
    overlay_score, overlay_detail, row = score_ticker_finnhub_sentiment(ticker)
    if row.get("status") != "ok":
        return local_score, local_detail
    combined = round(local_score * 0.8 + overlay_score * 0.2, 1)
    mode = "direct" if direct_scoring_enabled() else "promoted"
    return combined, f"{local_detail}; {overlay_detail} (20% {mode} overlay)"


def reset_latest_outputs() -> None:
    """Test helper: remove latest artifacts without touching daily archives."""
    for path in (LATEST_JSON, LATEST_MD):
        if path.exists():
            path.unlink()
    if DATA_DIR.exists() and not any(DATA_DIR.iterdir()):
        shutil.rmtree(DATA_DIR)
