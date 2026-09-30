"""Fetch metadata-only narrative news into DuckDB shadow tables.

No full article body is stored. The pipeline keeps title/link/snippet/hash metadata
so narrative features can be researched without redistributing copyrighted text.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Iterable

import duckdb
import pandas as pd
import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

BASE_DIR = os.environ.get("STOCK_BASE_DIR") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

try:
    import truststore

    truststore.inject_into_ssl()
except Exception:
    pass

from backend.config import DUCKDB_PATH
from backend.db.ingest import ensure_narrative_schema
from backend.features.narrative.labeler import label_text
from backend.features.narrative.ticker_linker import TickerLinker, load_aliases_from_conn
from backend.features.narrative.heat import build_daily_features


REPORT_DIR = os.path.join(BASE_DIR, "ml", "reports")
os.makedirs(REPORT_DIR, exist_ok=True)

CNYES_URL = "https://api.cnyes.com/media/api/v1/newslist/category/tw_stock"
YAHOO_RSS_URLS = [
    "https://tw.stock.yahoo.com/rss?category=tw-market",
]
LTN_RSS_URLS = [
    "https://news.ltn.com.tw/rss/business.xml",
]
MOPS_URL = "https://mopsov.twse.com.tw/mops/web/ajax_t05sr01_1"


@dataclass(frozen=True)
class ArticleMeta:
    news_id: str
    source: str
    published_at: datetime
    title: str
    link: str
    snippet: str
    content_hash: str
    ticker_hint: str | None = None


def build_session() -> requests.Session:
    session = requests.Session()
    retries = Retry(
        total=3,
        backoff_factor=1.0,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "POST"],
    )
    session.mount("https://", HTTPAdapter(max_retries=retries))
    session.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json,text/xml,application/xml,text/html",
        }
    )
    return session


def _now_naive() -> datetime:
    return datetime.now().replace(microsecond=0)


def _normalize_title(title: str) -> str:
    text = re.sub(r"\s+", "", str(title or "")).strip().lower()
    return text[:240]


def _hash_text(title: str, link: str = "") -> str:
    key = _normalize_title(title) or str(link or "").strip().lower()
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _article_id(source: str, *parts: object) -> str:
    raw = "|".join(str(part or "") for part in parts)
    return f"{source}:{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:24]}"


def _snippet(value: object, limit: int = 200) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit]


def _to_naive(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(microsecond=0)
    return dt.astimezone(timezone(timedelta(hours=8))).replace(tzinfo=None, microsecond=0)


def fetch_cnyes(
    session: requests.Session,
    start: date,
    end: date,
    limit: int = 30,
    rate_limit_sec: float = 1.0,
    max_pages: int | None = None,
) -> list[ArticleMeta]:
    start_dt = datetime.combine(start, datetime.min.time())
    end_dt = datetime.combine(end + timedelta(days=1), datetime.min.time()) - timedelta(seconds=1)
    articles: list[ArticleMeta] = []
    page = 1
    while True:
        params = {
            "startAt": int(start_dt.timestamp()),
            "endAt": int(end_dt.timestamp()),
            "limit": int(limit),
            "page": page,
        }
        response = session.get(CNYES_URL, params=params, timeout=45)
        response.raise_for_status()
        payload = response.json()
        items = payload.get("items") or {}
        data = items.get("data") or []
        for item in data:
            news_id = str(item.get("newsId") or item.get("id") or "")
            title = str(item.get("title") or "").strip()
            if not news_id or not title:
                continue
            published_at = datetime.fromtimestamp(int(item.get("publishAt") or 0))
            link = f"https://news.cnyes.com/news/id/{news_id}"
            snippet = _snippet(item.get("categoryName") or item.get("market") or "")
            articles.append(
                ArticleMeta(
                    news_id=f"cnyes:{news_id}",
                    source="cnyes",
                    published_at=published_at,
                    title=title,
                    link=link,
                    snippet=snippet,
                    content_hash=_hash_text(title, link),
                )
            )
        last_page = int(items.get("last_page") or page)
        if page >= last_page or (max_pages is not None and page >= max_pages):
            break
        page += 1
        time.sleep(max(float(rate_limit_sec), 1.0) + random.uniform(0, 0.2))
    return articles


def _parse_rss_date(value: str | None) -> datetime:
    if not value:
        return _now_naive()
    try:
        return _to_naive(parsedate_to_datetime(value))
    except Exception:
        try:
            return pd.to_datetime(value).to_pydatetime().replace(tzinfo=None)
        except Exception:
            return _now_naive()


def fetch_rss(session: requests.Session, source: str, urls: Iterable[str]) -> list[ArticleMeta]:
    articles: list[ArticleMeta] = []
    for url in urls:
        response = session.get(url, timeout=45)
        response.raise_for_status()
        root = ET.fromstring(response.content)
        for item in root.findall(".//item"):
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            if not title:
                continue
            published_at = _parse_rss_date(item.findtext("pubDate"))
            articles.append(
                ArticleMeta(
                    news_id=_article_id(source, title, link, published_at.isoformat()),
                    source=source,
                    published_at=published_at,
                    title=title,
                    link=link,
                    snippet=_snippet(item.findtext("description") or ""),
                    content_hash=_hash_text(title, link),
                )
            )
    return articles


def _roc_to_ad_date(value: str) -> str:
    parts = str(value or "").split("/")
    if len(parts) != 3:
        return ""
    return f"{int(parts[0]) + 1911:04d}-{int(parts[1]):02d}-{int(parts[2]):02d}"


def fetch_mops(session: requests.Session) -> list[ArticleMeta]:
    today = date.today()
    payload_base = {
        "encodeURIComponent": "1",
        "step": "2",
        "firstin": "1",
        "off": "1",
        "year": str(today.year - 1911),
        "month": f"{today.month:02d}",
    }
    articles: list[ArticleMeta] = []
    for typek, market in (("sii", "listed"), ("otc", "otc")):
        response = session.post(MOPS_URL, data={**payload_base, "TYPEK": typek}, timeout=60)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "lxml")
        for table in soup.find_all("table", class_="hasBorder"):
            for tr in table.find_all("tr"):
                cells = [td.get_text(strip=True) for td in tr.find_all("td")]
                if len(cells) < 5:
                    continue
                ticker = str(cells[0]).strip()
                if not ticker or not ticker[0].isdigit():
                    continue
                name = str(cells[1]).strip()
                ad_date = _roc_to_ad_date(str(cells[2]).strip())
                time_str = str(cells[3]).strip() or "00:00:00"
                title = str(cells[4]).strip()
                try:
                    published_at = datetime.fromisoformat(f"{ad_date} {time_str}")
                except ValueError:
                    published_at = _now_naive()
                articles.append(
                    ArticleMeta(
                        news_id=_article_id("mops", ticker, ad_date, time_str, title),
                        source="mops",
                        published_at=published_at,
                        title=title,
                        link="https://mopsov.twse.com.tw/mops/web/t05sr01_1",
                        snippet=_snippet(f"{ticker} {name} {market}"),
                        content_hash=_hash_text(f"{ticker}:{ad_date}:{time_str}:{title}"),
                        ticker_hint=ticker,
                    )
                )
        time.sleep(1.0 + random.uniform(0, 0.2))
    return articles


def insert_articles(
    conn: duckdb.DuckDBPyConnection,
    articles: list[ArticleMeta],
    linker: TickerLinker | None = None,
) -> dict[str, int]:
    ensure_narrative_schema(conn)
    linker = linker or TickerLinker(load_aliases_from_conn(conn))
    fetched_at = _now_naive()
    existing_ids = {
        row[0]
        for row in conn.execute("SELECT news_id FROM news_articles").fetchall()
        if row[0]
    }
    existing_hashes = {
        row[0]
        for row in conn.execute("SELECT content_hash FROM news_articles").fetchall()
        if row[0]
    }
    inserted_ids: list[str] = []
    skipped_duplicates = 0
    for article in articles:
        if article.news_id in existing_ids or article.content_hash in existing_hashes:
            skipped_duplicates += 1
            continue
        conn.execute(
            """
            INSERT INTO news_articles (
                news_id, source, published_at, title, link, snippet, content_hash, fetched_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                article.news_id,
                article.source,
                article.published_at,
                article.title,
                article.link,
                article.snippet,
                article.content_hash,
                fetched_at,
            ],
        )
        existing_ids.add(article.news_id)
        existing_hashes.add(article.content_hash)
        inserted_ids.append(article.news_id)

        links = linker.link(article.title, article.snippet, ticker_hint=article.ticker_hint)
        for link in links:
            conn.execute(
                """
                INSERT INTO news_ticker_links (
                    news_id, ticker, match_method, confidence, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                [article.news_id, link.ticker, link.match_method, link.confidence, fetched_at],
            )

        for label in label_text(article.title, article.snippet):
            conn.execute(
                """
                INSERT INTO news_narrative_labels (
                    news_id, label, matched_keywords, created_at
                ) VALUES (?, ?, ?, ?)
                """,
                [article.news_id, label.label, label.matched_keywords_json(), fetched_at],
            )

    conn.commit()
    return {
        "fetched": len(articles),
        "inserted": len(inserted_ids),
        "duplicate_skipped": skipped_duplicates,
        "inserted_ids": len(inserted_ids),
    }


def rebuild_derived_tables(
    conn: duckdb.DuckDBPyConnection,
    linker: TickerLinker | None = None,
) -> dict[str, int]:
    """Rebuild links and labels from stored metadata after linker/taxonomy changes."""
    ensure_narrative_schema(conn)
    linker = linker or TickerLinker(load_aliases_from_conn(conn))
    articles = conn.execute(
        "SELECT news_id, title, snippet FROM news_articles ORDER BY published_at"
    ).fetchall()
    conn.execute("DELETE FROM news_ticker_links")
    conn.execute("DELETE FROM news_narrative_labels")
    conn.execute("DELETE FROM daily_ticker_narrative_features")
    now = _now_naive()
    link_rows = 0
    label_rows = 0
    for news_id, title, snippet in articles:
        for link in linker.link(str(title or ""), str(snippet or "")):
            conn.execute(
                """
                INSERT INTO news_ticker_links (
                    news_id, ticker, match_method, confidence, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                [news_id, link.ticker, link.match_method, link.confidence, now],
            )
            link_rows += 1
        for label in label_text(str(title or ""), str(snippet or "")):
            conn.execute(
                """
                INSERT INTO news_narrative_labels (
                    news_id, label, matched_keywords, created_at
                ) VALUES (?, ?, ?, ?)
                """,
                [news_id, label.label, label.matched_keywords_json(), now],
            )
            label_rows += 1
    conn.commit()
    return {"articles": len(articles), "link_rows": link_rows, "label_rows": label_rows}


def write_unresolved_audit(conn: duckdb.DuckDBPyConnection, as_of: date) -> str:
    ensure_narrative_schema(conn)
    path = os.path.join(REPORT_DIR, f"narrative_unresolved_{as_of.strftime('%Y%m%d')}.csv")
    df = conn.execute(
        """
        SELECT a.news_id, a.source, a.published_at, a.title, a.link
        FROM news_articles a
        LEFT JOIN news_ticker_links l
          ON a.news_id = l.news_id AND l.confidence >= 0.7
        WHERE CAST(a.published_at AS DATE) = ?
          AND l.news_id IS NULL
        ORDER BY a.published_at DESC
        """,
        [as_of.isoformat()],
    ).fetchdf()
    df.to_csv(path, index=False, encoding="utf-8-sig")
    return path


def fetch_sources(args: argparse.Namespace) -> list[ArticleMeta]:
    session = build_session()
    sources = set(args.source)
    articles: list[ArticleMeta] = []
    end = date.fromisoformat(args.end_date) if args.end_date else date.today()
    start = date.fromisoformat(args.start_date) if args.start_date else end - timedelta(days=2)

    if "all" in sources or "mops" in sources:
        articles.extend(fetch_mops(session))
    if "all" in sources or "cnyes" in sources:
        articles.extend(fetch_cnyes(session, start, end, limit=args.limit, rate_limit_sec=args.rate_limit_sec, max_pages=args.max_pages))
    if "all" in sources or "yahoo_rss" in sources:
        articles.extend(fetch_rss(session, "yahoo_rss", YAHOO_RSS_URLS))
    if "all" in sources or "ltn_rss" in sources:
        articles.extend(fetch_rss(session, "ltn_rss", LTN_RSS_URLS))
    return articles


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch metadata-only narrative news into DuckDB.")
    parser.add_argument("--source", action="append", choices=["all", "mops", "cnyes", "yahoo_rss", "ltn_rss"], default=None)
    parser.add_argument("--start-date", default=None)
    parser.add_argument("--end-date", default=None)
    parser.add_argument("--as-of-date", default=None)
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument("--max-pages", type=int, default=None)
    parser.add_argument("--rate-limit-sec", type=float, default=1.0)
    parser.add_argument("--run-report", action="store_true")
    parser.add_argument("--skip-fetch", action="store_true")
    parser.add_argument("--rebuild-derived", action="store_true")
    args = parser.parse_args()
    args.source = args.source or ["all"]

    articles = [] if args.skip_fetch else fetch_sources(args)
    as_of = date.fromisoformat(args.as_of_date) if args.as_of_date else date.today()
    conn = duckdb.connect(DUCKDB_PATH)
    try:
        stats = insert_articles(conn, articles)
        derived_stats = rebuild_derived_tables(conn) if args.rebuild_derived else None
        features = build_daily_features(conn, as_of)
        unresolved_path = write_unresolved_audit(conn, as_of)
    finally:
        conn.close()

    print(
        json.dumps(
            {
                "status": "ok",
                "stats": stats,
                "derived_stats": derived_stats,
                "feature_rows": int(len(features)),
                "unresolved_path": unresolved_path,
            },
            ensure_ascii=False,
        )
    )

    if args.run_report:
        from scripts.build_narrative_shadow_report import main as report_main

        return int(report_main(["--date", as_of.isoformat()]) or 0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
