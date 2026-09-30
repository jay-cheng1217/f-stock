"""Taiwan equity news intelligence service.

This service adapts the reusable parts from ZhuLinsen/daily_stock_analysis:
multi-provider search fallback, freshness filtering, relevance scoring, and
dashboard-shaped report output. It is intentionally read-only and does not feed
production entry/exit gates.
"""

from __future__ import annotations

import glob
import hashlib
import json
import os
import re
import sqlite3
import time
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import pandas as pd
import requests

from backend.config import BASE_DIR
from backend.services.news_service import get_stock_news
from backend.services.finnhub_sentiment_service import score_ticker_finnhub_sentiment
from ml.config import MODEL_DIR, REPORT_DIR


DIMENSIONS: dict[str, dict[str, str]] = {
    "latest_news": {
        "label": "最新新聞",
        "query": "{name} {ticker} 台股 最新 新聞",
    },
    "announcements": {
        "label": "重大訊息",
        "query": "{name} {ticker} 重大訊息 公告 法說 交易所 公開資訊觀測站",
    },
    "risk_check": {
        "label": "風險事件",
        "query": "{name} {ticker} 風險 裁罰 虧損 下修 處置 注意 停工 違約",
    },
    "earnings_revenue": {
        "label": "營收獲利",
        "query": "{name} {ticker} 營收 獲利 EPS 毛利 法說 展望",
    },
    "industry_supply_chain": {
        "label": "產業供應鏈",
        "query": "{name} {ticker} 產業 供應鏈 訂單 客戶 競爭",
    },
    "institutional_flow": {
        "label": "外資籌碼",
        "query": "{name} {ticker} 外資 投信 買超 賣超 籌碼",
    },
}

NEGATIVE_KEYWORDS = (
    "虧損",
    "減損",
    "訴訟",
    "裁罰",
    "下修",
    "違約",
    "停工",
    "停業",
    "下市",
    "重整",
    "破產",
    "火災",
    "爆炸",
    "減資",
    "處分",
    "警示",
    "注意",
    "處置",
    "全額交割",
    "內線",
    "調查",
    "違規",
    "利空",
    "賣壓",
    "外資賣超",
    "投信賣超",
)

POSITIVE_KEYWORDS = (
    "得標",
    "中標",
    "簽約",
    "合作",
    "策略聯盟",
    "接單",
    "訂單",
    "獲利",
    "盈餘",
    "股利",
    "配息",
    "買回庫藏股",
    "上修",
    "成長",
    "法說",
    "營收創高",
    "外資買超",
    "投信買超",
)

MACRO_KEYWORDS = (
    "台股",
    "台指期",
    "結算",
    "加權指數",
    "櫃買",
    "美股",
    "那斯達克",
    "費半",
    "匯率",
    "利率",
    "升息",
    "降息",
)

SECTOR_KEYWORDS = (
    "半導體",
    "電子",
    "AI",
    "伺服器",
    "PCB",
    "散熱",
    "航運",
    "金融",
    "生技",
    "電動車",
    "供應鏈",
    "產業",
)

SIGNAL_FILE_RE = re.compile(r"^unified_signals_\d{4}-\d{2}-\d{2}\.csv$")
SEARCH_TIMEOUT_SECONDS = float(os.environ.get("TW_NEWS_INTEL_TIMEOUT_SECONDS", "8"))
SEARCH_CACHE_TTL_SECONDS = int(os.environ.get("TW_NEWS_INTEL_CACHE_TTL_SECONDS", "600"))
WEB_BUDGET_SECONDS = float(os.environ.get("TW_NEWS_INTEL_WEB_BUDGET_SECONDS", "20"))
DEFAULT_BATCH_WEB_DIMENSIONS = tuple(
    item.strip()
    for item in os.environ.get("TW_NEWS_INTEL_BATCH_DIMENSIONS", "latest_news,risk_check,earnings_revenue").split(",")
    if item.strip()
)

_SEARCH_CACHE: dict[str, tuple[float, list[dict[str, Any]]]] = {}


@dataclass(frozen=True)
class WebProvider:
    name: str
    kind: str
    credential: str
    base_url: str | None = None


@dataclass(frozen=True)
class RawNewsItem:
    source_type: str
    provider: str
    dimension: str
    title: str
    snippet: str = ""
    url: str = ""
    source: str = ""
    published_date: str | None = None
    date_status: str = "unknown"


def _split_env(*names: str) -> list[str]:
    values: list[str] = []
    for name in names:
        raw = os.environ.get(name, "")
        for item in re.split(r"[,\n;]+", raw):
            text = item.strip()
            if text:
                values.append(text)
    return values


def configured_provider_status() -> list[dict[str, Any]]:
    """Return search provider availability without exposing secrets."""
    providers = _configured_providers()
    names = [provider.name for provider in providers]
    return [
        {"name": "Tavily", "available": "Tavily" in names},
        {"name": "Brave", "available": "Brave" in names},
        {"name": "SerpAPI", "available": "SerpAPI" in names},
        {"name": "SearXNG", "available": "SearXNG" in names},
    ]


def _configured_providers() -> list[WebProvider]:
    providers: list[WebProvider] = []
    for key in _split_env("TAVILY_API_KEYS", "TAVILY_API_KEY"):
        providers.append(WebProvider(name="Tavily", kind="tavily", credential=key))
    for key in _split_env("BRAVE_API_KEYS", "BRAVE_API_KEY"):
        providers.append(WebProvider(name="Brave", kind="brave", credential=key))
    for key in _split_env("SERPAPI_API_KEYS", "SERPAPI_API_KEY"):
        providers.append(WebProvider(name="SerpAPI", kind="serpapi", credential=key))
    for base_url in _split_env("SEARXNG_BASE_URLS", "SEARXNG_BASE_URL"):
        providers.append(
            WebProvider(
                name="SearXNG",
                kind="searxng",
                credential="",
                base_url=base_url.rstrip("/"),
            )
        )
    return providers


def _domain(url: str) -> str:
    try:
        parsed = urlparse(url)
        return parsed.netloc.replace("www.", "") or "web"
    except Exception:
        return "web"


def _cache_key(provider: WebProvider, query: str, days: int, max_results: int) -> str:
    raw = f"{provider.kind}|{provider.base_url or ''}|{query}|{days}|{max_results}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _parse_date(value: Any) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None

    lowered = text.lower()
    now = datetime.now().date()
    if "今天" in text or "today" in lowered:
        return now
    if "昨天" in text or "yesterday" in lowered:
        return now - timedelta(days=1)

    hour_match = re.search(r"(\d+)\s*(?:小時|hour)", lowered)
    if hour_match:
        return now
    day_match = re.search(r"(\d+)\s*(?:天|day)", lowered)
    if day_match:
        return now - timedelta(days=int(day_match.group(1)))

    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y%m%d", "%d %b %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(text[: max(len(text), len(fmt))], fmt).date()
        except ValueError:
            continue

    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _date_status(published_date: Any, days: int) -> str:
    parsed = _parse_date(published_date)
    if parsed is None:
        return "unknown"
    if parsed >= datetime.now().date() - timedelta(days=max(1, int(days))):
        return "fresh"
    return "stale"


def _search_tavily(provider: WebProvider, query: str, days: int, max_results: int) -> list[dict[str, Any]]:
    response = requests.post(
        "https://api.tavily.com/search",
        json={
            "api_key": provider.credential,
            "query": query,
            "search_depth": "advanced",
            "topic": "news",
            "days": days,
            "max_results": max_results,
            "include_answer": False,
            "include_raw_content": False,
        },
        timeout=SEARCH_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    data = response.json()
    return [
        {
            "title": item.get("title") or "",
            "snippet": item.get("content") or item.get("snippet") or "",
            "url": item.get("url") or "",
            "published_date": item.get("published_date") or item.get("publishedDate"),
            "source": _domain(item.get("url") or ""),
        }
        for item in data.get("results", [])
    ]


def _search_brave(provider: WebProvider, query: str, days: int, max_results: int) -> list[dict[str, Any]]:
    response = requests.get(
        "https://api.search.brave.com/res/v1/web/search",
        headers={"X-Subscription-Token": provider.credential, "Accept": "application/json"},
        params={
            "q": query,
            "count": max_results,
            "country": "tw",
            "search_lang": "zh-hant",
            "safesearch": "moderate",
            "freshness": "pd" if days <= 1 else "pw" if days <= 7 else "pm",
        },
        timeout=SEARCH_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    data = response.json()
    items = (data.get("web") or {}).get("results") or []
    return [
        {
            "title": item.get("title") or "",
            "snippet": item.get("description") or "",
            "url": item.get("url") or "",
            "published_date": item.get("age"),
            "source": _domain(item.get("url") or ""),
        }
        for item in items
    ]


def _search_serpapi(provider: WebProvider, query: str, days: int, max_results: int) -> list[dict[str, Any]]:
    tbs = "qdr:d" if days <= 1 else "qdr:w" if days <= 7 else "qdr:m"
    response = requests.get(
        "https://serpapi.com/search.json",
        params={
            "engine": "google",
            "q": query,
            "api_key": provider.credential,
            "google_domain": "google.com.tw",
            "hl": "zh-tw",
            "gl": "tw",
            "num": max_results,
            "tbs": tbs,
        },
        timeout=SEARCH_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    data = response.json()
    items = data.get("news_results") or data.get("organic_results") or []
    return [
        {
            "title": item.get("title") or "",
            "snippet": item.get("snippet") or "",
            "url": item.get("link") or "",
            "published_date": item.get("date"),
            "source": item.get("source") or _domain(item.get("link") or ""),
        }
        for item in items
    ]


def _search_searxng(provider: WebProvider, query: str, days: int, max_results: int) -> list[dict[str, Any]]:
    if not provider.base_url:
        return []
    response = requests.get(
        f"{provider.base_url}/search",
        params={
            "q": query,
            "format": "json",
            "categories": "news,general",
            "language": "zh-TW",
            "time_range": "day" if days <= 1 else "week" if days <= 7 else "month",
        },
        timeout=SEARCH_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    data = response.json()
    return [
        {
            "title": item.get("title") or "",
            "snippet": item.get("content") or "",
            "url": item.get("url") or "",
            "published_date": item.get("publishedDate") or item.get("date"),
            "source": item.get("engine") or _domain(item.get("url") or ""),
        }
        for item in (data.get("results") or [])[:max_results]
    ]


def _provider_search(provider: WebProvider, query: str, days: int, max_results: int) -> list[dict[str, Any]]:
    key = _cache_key(provider, query, days, max_results)
    cached = _SEARCH_CACHE.get(key)
    if cached and time.time() - cached[0] <= SEARCH_CACHE_TTL_SECONDS:
        return cached[1]

    if provider.kind == "tavily":
        rows = _search_tavily(provider, query, days, max_results)
    elif provider.kind == "brave":
        rows = _search_brave(provider, query, days, max_results)
    elif provider.kind == "serpapi":
        rows = _search_serpapi(provider, query, days, max_results)
    elif provider.kind == "searxng":
        rows = _search_searxng(provider, query, days, max_results)
    else:
        rows = []

    _SEARCH_CACHE[key] = (time.time(), rows)
    return rows


def _web_search_dimension(
    ticker: str,
    name: str,
    dimension: str,
    days: int,
    max_results: int,
) -> list[RawNewsItem]:
    providers = _configured_providers()
    if not providers:
        return []

    template = DIMENSIONS[dimension]["query"]
    query = template.format(ticker=ticker, name=name or ticker)
    for provider in providers:
        try:
            rows = _provider_search(provider, query, days, max_results=max_results)
        except Exception:
            continue
        if not rows:
            continue
        items: list[RawNewsItem] = []
        for row in rows:
            published_date = row.get("published_date")
            status = _date_status(published_date, days)
            if status == "stale":
                continue
            items.append(
                RawNewsItem(
                    source_type="web",
                    provider=provider.name,
                    dimension=dimension,
                    title=str(row.get("title") or "").strip(),
                    snippet=str(row.get("snippet") or "").strip(),
                    url=str(row.get("url") or "").strip(),
                    source=str(row.get("source") or "").strip() or _domain(str(row.get("url") or "")),
                    published_date=str(published_date).strip() if published_date else None,
                    date_status=status,
                )
            )
        if items:
            return items
    return []


def _mops_items(ticker: str, days: int, limit: int) -> list[RawNewsItem]:
    rows = get_stock_news(ticker, max(limit * 3, 15))
    items: list[RawNewsItem] = []
    for row in rows:
        published_date = str(row.get("date") or "").strip() or None
        status = _date_status(published_date, days)
        if status == "stale":
            continue
        title = str(row.get("title") or "").strip()
        if not title:
            continue
        items.append(
            RawNewsItem(
                source_type="mops",
                provider="MOPS",
                dimension="announcements",
                title=title,
                snippet=str(row.get("market") or "").strip(),
                url="",
                source="MOPS",
                published_date=published_date,
                date_status=status,
            )
        )
    return items[:limit]


def score_news_item(item: RawNewsItem | dict[str, Any], ticker: str, name: str | None = None) -> dict[str, Any]:
    """Score one normalized news item for directness, sentiment, and risk."""
    if not isinstance(item, RawNewsItem):
        item = RawNewsItem(**item)

    ticker_text = str(ticker or "").strip()
    name_text = str(name or "").strip()
    haystack = f"{item.title} {item.snippet}".lower()
    direct_hits = []
    if ticker_text and ticker_text.lower() in haystack:
        direct_hits.append("ticker")
    if name_text and name_text.lower() in haystack:
        direct_hits.append("name")
    if item.source_type == "mops":
        direct_hits.append("mops_ticker_link")

    negative_hits = [keyword for keyword in NEGATIVE_KEYWORDS if keyword.lower() in haystack]
    positive_hits = [keyword for keyword in POSITIVE_KEYWORDS if keyword.lower() in haystack]
    macro_hits = [keyword for keyword in MACRO_KEYWORDS if keyword.lower() in haystack]
    sector_hits = [keyword for keyword in SECTOR_KEYWORDS if keyword.lower() in haystack]

    score = 25
    reasons: list[str] = []
    if direct_hits:
        score += 45
        reasons.append("direct_stock_match")
    if item.source_type == "mops":
        score += 18
        reasons.append("mops_announcement")
    if item.dimension in {"risk_check", "announcements", "earnings_revenue"}:
        score += 8
    if negative_hits:
        score += min(18, len(negative_hits) * 6)
        reasons.append("risk_keyword")
    if positive_hits:
        score += min(10, len(positive_hits) * 3)
        reasons.append("positive_keyword")
    if sector_hits and not direct_hits:
        score += 14
        reasons.append("sector_context")
    if macro_hits and not direct_hits:
        score += 8
        reasons.append("macro_context")
    if item.date_status == "unknown" and item.source_type == "web":
        score -= 8
        reasons.append("unknown_publish_date")

    if direct_hits:
        relevance_category = "direct_stock_news"
    elif sector_hits:
        relevance_category = "sector_related_news"
    elif macro_hits:
        relevance_category = "macro_market_news"
    else:
        relevance_category = "general_market_news"

    sentiment = "neutral"
    if len(negative_hits) > len(positive_hits):
        sentiment = "negative"
    elif len(positive_hits) > len(negative_hits):
        sentiment = "positive"

    risk_level = "green"
    if negative_hits and (direct_hits or item.source_type == "mops"):
        risk_level = "red"
    elif negative_hits or (item.dimension == "risk_check" and item.date_status == "unknown"):
        risk_level = "yellow"
    elif item.date_status == "unknown" and direct_hits:
        risk_level = "yellow"

    payload = asdict(item)
    payload.update(
        {
            "ticker": ticker_text,
            "name": name_text,
            "dimension_label": DIMENSIONS.get(item.dimension, {}).get("label", item.dimension),
            "relevance_score": max(0, min(100, int(score))),
            "relevance_category": relevance_category,
            "relevance_reasons": reasons,
            "sentiment": sentiment,
            "risk_level": risk_level,
            "matched_keywords": {
                "negative": negative_hits[:5],
                "positive": positive_hits[:5],
                "macro": macro_hits[:5],
                "sector": sector_hits[:5],
            },
        }
    )
    return payload


def _dedupe_and_rank(items: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for item in items:
        title = str(item.get("title") or "").strip().lower()
        url = str(item.get("url") or "").strip().lower()
        key = url or title
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    risk_order = {"red": 0, "yellow": 1, "green": 2}
    deduped.sort(
        key=lambda row: (
            risk_order.get(str(row.get("risk_level") or "green"), 3),
            -float(row.get("relevance_score") or 0),
            str(row.get("published_date") or ""),
        )
    )
    return deduped[: max(1, int(limit))]


def _lookup_stock_name(ticker: str) -> str:
    try:
        from backend.db.engine import query_df

        df = query_df("SELECT Name FROM stock_list WHERE Ticker = ? LIMIT 1", [ticker])
        if not df.empty:
            value = str(df.iloc[0].get("Name") or "").strip()
            if value:
                return value
    except Exception:
        pass
    return ""


def _load_latest_signal_targets(limit: int) -> tuple[list[dict[str, str]], str | None]:
    candidates = [
        path
        for path in sorted(glob.glob(os.path.join(MODEL_DIR, "unified_signals_*.csv")))
        if SIGNAL_FILE_RE.match(os.path.basename(path))
    ]
    if not candidates:
        return [], None
    path = candidates[-1]
    try:
        df = pd.read_csv(path, dtype={"ticker": str})
    except Exception:
        return [], path
    if df.empty or "ticker" not in df.columns:
        return [], path
    sort_columns = [column for column in ("rank_20d", "two_stage_rank", "risk_adjusted_return") if column in df.columns]
    if sort_columns:
        ascending = [True if "rank" in column else False for column in sort_columns]
        df = df.sort_values(sort_columns, ascending=ascending, na_position="last")
    rows: list[dict[str, str]] = []
    for _, row in df.head(max(1, limit)).iterrows():
        ticker = str(row.get("ticker") or "").strip()
        if not ticker:
            continue
        rows.append(
            {
                "ticker": ticker,
                "name": _lookup_stock_name(ticker),
                "source": os.path.basename(path),
            }
        )
    return rows, path


def _load_holding_targets(limit: int) -> tuple[list[dict[str, str]], str | None]:
    db_path = Path(BASE_DIR) / "my_holdings.db"
    if not db_path.exists():
        return [], str(db_path)
    try:
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT ticker FROM holdings ORDER BY ticker, id LIMIT ?", (max(1, limit),)).fetchall()
    except sqlite3.Error:
        return [], str(db_path)
    targets = []
    for row in rows:
        ticker = str(row["ticker"] or "").strip()
        if ticker:
            targets.append({"ticker": ticker, "name": _lookup_stock_name(ticker), "source": "my_holdings.db"})
    return targets, str(db_path)


def _report_for_ticker(
    ticker: str,
    name: str | None,
    days: int,
    limit: int,
    include_web: bool,
    web_dimensions: tuple[str, ...] | None = None,
    deadline_monotonic: float | None = None,
) -> dict[str, Any]:
    clean_ticker = str(ticker or "").strip()
    clean_name = str(name or "").strip() or _lookup_stock_name(clean_ticker)
    days = max(1, min(int(days), 30))
    limit = max(1, min(int(limit), 20))

    raw_items = _mops_items(clean_ticker, days=days, limit=max(limit, 5))
    web_search_status = "disabled"
    providers_configured = bool(_configured_providers())
    if include_web and not providers_configured:
        web_search_status = "no_provider_configured"
    elif include_web and providers_configured:
        web_search_status = "enabled"
        dimensions = web_dimensions or tuple(DIMENSIONS)
        per_dimension_limit = max(2, min(5, limit))
        for dimension in dimensions:
            if dimension not in DIMENSIONS:
                continue
            if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
                web_search_status = "budget_exhausted"
                break
            raw_items.extend(
                _web_search_dimension(
                    clean_ticker,
                    clean_name or clean_ticker,
                    dimension,
                    days=days,
                    max_results=per_dimension_limit,
                )
            )

    scored = [score_news_item(item, clean_ticker, clean_name) for item in raw_items if item.title]
    rows = _dedupe_and_rank(scored, limit=limit)
    risk_counts = Counter(str(row.get("risk_level") or "green") for row in rows)
    sentiment_counts = Counter(str(row.get("sentiment") or "neutral") for row in rows)
    dimension_counts = Counter(str(row.get("dimension") or "unknown") for row in rows)
    finnhub_score, finnhub_detail, finnhub_record = score_ticker_finnhub_sentiment(clean_ticker)

    verdict = "green"
    if risk_counts.get("red", 0):
        verdict = "red"
    elif risk_counts.get("yellow", 0):
        verdict = "yellow"

    return {
        "ticker": clean_ticker,
        "name": clean_name,
        "days": days,
        "limit": limit,
        "include_web": bool(include_web),
        "web_provider_status": configured_provider_status(),
        "web_search_status": web_search_status,
        "verdict": verdict,
        "risk_counts": dict(risk_counts),
        "sentiment_counts": dict(sentiment_counts),
        "dimension_counts": dict(dimension_counts),
        "external_sentiment": {
            "provider": "finnhub",
            "status": finnhub_record.get("status"),
            "score": finnhub_score,
            "detail": finnhub_detail,
            "finnhub_symbol": finnhub_record.get("finnhub_symbol"),
        },
        "items": rows,
    }


def build_ticker_news_intel(
    ticker: str,
    name: str | None = None,
    days: int = 7,
    limit: int = 8,
    include_web: bool = True,
) -> dict[str, Any]:
    """Build a single-stock news intelligence report."""
    deadline = time.monotonic() + WEB_BUDGET_SECONDS if include_web else None
    return {
        "status": "success",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "mode": "ticker",
        "report": _report_for_ticker(
            ticker=ticker,
            name=name,
            days=days,
            limit=limit,
            include_web=include_web,
            deadline_monotonic=deadline,
        ),
    }


def _targets_for_scope(scope: str, limit: int) -> tuple[list[dict[str, str]], str | None]:
    normalized = str(scope or "top").strip().lower()
    if normalized == "holdings":
        return _load_holding_targets(limit)
    return _load_latest_signal_targets(limit)


def _dashboard_summary(reports: list[dict[str, Any]]) -> dict[str, Any]:
    risk_counts: Counter[str] = Counter()
    source_counts: Counter[str] = Counter()
    dimension_counts: Counter[str] = Counter()
    total_items = 0
    for report in reports:
        risk_counts.update(report.get("risk_counts") or {})
        for item in report.get("items") or []:
            total_items += 1
            source_counts[str(item.get("source_type") or "unknown")] += 1
            dimension_counts[str(item.get("dimension") or "unknown")] += 1
    return {
        "ticker_count": len(reports),
        "item_count": total_items,
        "risk_counts": dict(risk_counts),
        "source_counts": dict(source_counts),
        "dimension_counts": dict(dimension_counts),
    }


def build_news_intel_dashboard(
    scope: str = "top",
    limit: int = 6,
    days: int = 7,
    include_web: bool = True,
) -> dict[str, Any]:
    """Build a batch dashboard for latest candidates or personal holdings."""
    limit = max(1, min(int(limit), 20))
    days = max(1, min(int(days), 30))
    targets, source_path = _targets_for_scope(scope, limit)
    deadline = time.monotonic() + WEB_BUDGET_SECONDS if include_web else None
    reports = [
        _report_for_ticker(
            ticker=target["ticker"],
            name=target.get("name"),
            days=days,
            limit=5,
            include_web=include_web,
            web_dimensions=DEFAULT_BATCH_WEB_DIMENSIONS,
            deadline_monotonic=deadline,
        )
        for target in targets[:limit]
    ]
    return {
        "status": "success",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "mode": "dashboard",
        "scope": scope,
        "source_path": source_path,
        "days": days,
        "limit": limit,
        "include_web": bool(include_web),
        "web_provider_status": configured_provider_status(),
        "summary": _dashboard_summary(reports),
        "reports": reports,
    }


def _markdown_for_payload(payload: dict[str, Any]) -> str:
    lines = [
        "# Taiwan News Intelligence",
        "",
        f"- Generated at: {payload.get('generated_at')}",
        f"- Mode: {payload.get('mode')}",
    ]
    if payload.get("mode") == "dashboard":
        summary = payload.get("summary") or {}
        lines.extend(
            [
                f"- Scope: {payload.get('scope')}",
                f"- Source: {payload.get('source_path') or '-'}",
                f"- Tickers: {summary.get('ticker_count', 0)}",
                f"- Items: {summary.get('item_count', 0)}",
                "",
            ]
        )
        reports = payload.get("reports") or []
    else:
        reports = [payload.get("report") or {}]
        lines.append("")

    for report in reports:
        ticker = report.get("ticker") or "-"
        name = report.get("name") or ""
        lines.extend(
            [
                f"## {ticker} {name}".strip(),
                "",
                f"- Verdict: {report.get('verdict')}",
                f"- Web search: {report.get('web_search_status')}",
                f"- External sentiment: {(report.get('external_sentiment') or {}).get('detail') or '-'}",
                "",
                "| Risk | Dimension | Date | Source | Title |",
                "|:--|:--|:--|:--|:--|",
            ]
        )
        for item in report.get("items") or []:
            title = str(item.get("title") or "").replace("|", " ")
            url = str(item.get("url") or "").strip()
            title_cell = f"[{title}]({url})" if url else title
            lines.append(
                "| {risk} | {dimension} | {date} | {source} | {title} |".format(
                    risk=item.get("risk_level") or "-",
                    dimension=item.get("dimension_label") or item.get("dimension") or "-",
                    date=item.get("published_date") or item.get("date_status") or "-",
                    source=item.get("source") or item.get("provider") or "-",
                    title=title_cell,
                )
            )
        if not report.get("items"):
            lines.append("| - | - | - | - | no recent intelligence |")
        lines.append("")
    return "\n".join(lines)


def write_news_intel_artifacts(payload: dict[str, Any], as_of: str | None = None) -> dict[str, str]:
    """Write JSON and Markdown reports under ml/reports."""
    report_dir = Path(REPORT_DIR)
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = as_of or datetime.now().strftime("%Y%m%d_%H%M%S")
    prefix = f"tw_news_intel_{stamp}"
    json_path = report_dir / f"{prefix}.json"
    md_path = report_dir / f"{prefix}.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(_markdown_for_payload(payload), encoding="utf-8")
    return {"json": str(json_path), "md": str(md_path)}
