"""News service backed by daily-crawled MOPS announcement CSV files."""

from __future__ import annotations

import glob
import os
from collections import Counter
from datetime import datetime, timedelta
from functools import lru_cache
from typing import Any

import pandas as pd

from backend.services.finnhub_sentiment_service import maybe_apply_finnhub_overlay


REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
NEWS_DIR = os.path.join(REPO_ROOT, "新聞資料")

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
    "增資完成",
    "上修",
    "成長",
)

NEGATIVE_KEYWORDS = (
    "虧損",
    "減損",
    "訴訟",
    "裁罰",
    "違約",
    "終止",
    "解約",
    "停工",
    "停業",
    "下市",
    "重整",
    "破產",
    "火災",
    "爆炸",
    "減資",
    "撤銷",
)


def _empty_news_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=["Ticker", "Name", "Date", "Time", "Title", "Market"])


def _classify_title_sentiment(title: str) -> tuple[str, float]:
    text = str(title or "").strip()
    if not text:
        return "neutral", 50.0

    positive_hits = sum(1 for keyword in POSITIVE_KEYWORDS if keyword in text)
    negative_hits = sum(1 for keyword in NEGATIVE_KEYWORDS if keyword in text)

    if positive_hits > negative_hits:
        return "positive", min(80.0, 55.0 + positive_hits * 10.0)
    if negative_hits > positive_hits:
        return "negative", max(20.0, 45.0 - negative_hits * 10.0)
    return "neutral", 50.0


def _load_announcements_uncached() -> pd.DataFrame:
    csv_files = sorted(glob.glob(os.path.join(NEWS_DIR, "announcements_*.csv")), reverse=True)
    if not csv_files:
        return _empty_news_frame()

    frames: list[pd.DataFrame] = []
    for csv_path in csv_files[:12]:
        try:
            df = pd.read_csv(csv_path, encoding="utf-8-sig", dtype={"Ticker": str})
        except Exception:
            continue
        if df.empty:
            continue
        for column in ("Ticker", "Name", "Date", "Time", "Title", "Market"):
            if column not in df.columns:
                df[column] = ""
        frames.append(df[["Ticker", "Name", "Date", "Time", "Title", "Market"]].copy())

    if not frames:
        return _empty_news_frame()

    data = pd.concat(frames, ignore_index=True)
    data["Ticker"] = data["Ticker"].astype(str).str.strip()
    data["Name"] = data["Name"].fillna("").astype(str).str.strip()
    data["Date"] = data["Date"].fillna("").astype(str).str.strip()
    data["Time"] = data["Time"].fillna("").astype(str).str.strip()
    data["Title"] = data["Title"].fillna("").astype(str).str.strip()
    data["Market"] = data["Market"].fillna("").astype(str).str.strip()
    data["_ts"] = pd.to_datetime(
        data["Date"] + " " + data["Time"].replace("", "00:00:00"),
        errors="coerce",
    )
    data = data.sort_values(["_ts", "Ticker"], ascending=[False, True], na_position="last")
    return data


@lru_cache(maxsize=1)
def _load_announcements() -> pd.DataFrame:
    """Load announcement CSVs once per process for dashboard/read API reuse."""
    return _load_announcements_uncached()


def _serialize_rows(df: pd.DataFrame, limit: int) -> list[dict[str, Any]]:
    if df.empty:
        return []

    rows: list[dict[str, Any]] = []
    for _, row in df.head(limit).iterrows():
        sentiment, sentiment_score = _classify_title_sentiment(row.get("Title", ""))
        rows.append(
            {
                "ticker": str(row.get("Ticker", "")),
                "name": str(row.get("Name", "")),
                "date": str(row.get("Date", "")),
                "time": str(row.get("Time", "")),
                "title": str(row.get("Title", "")),
                "market": str(row.get("Market", "")),
                "sentiment": sentiment,
                "sentiment_score": round(float(sentiment_score), 1),
            }
        )
    return rows


def _recent_rows(rows: list[dict[str, Any]], days: int, limit: int) -> list[dict[str, Any]]:
    cutoff = (datetime.now() - timedelta(days=max(int(days), 1))).date()
    recent: list[dict[str, Any]] = []
    for row in rows:
        try:
            row_date = datetime.strptime(str(row.get("date", "")), "%Y-%m-%d").date()
        except ValueError:
            continue
        if row_date >= cutoff:
            recent.append(row)
        if len(recent) >= max(int(limit), 1):
            break
    return recent


def get_latest_news(n: int = 20) -> list[dict[str, Any]]:
    """Return latest market announcements across all stocks."""
    df = _load_announcements()
    return _serialize_rows(df, max(int(n or 20), 1))


def get_stock_news(ticker: str, n: int = 10) -> list[dict[str, Any]]:
    """Return recent announcements for a specific stock ticker."""
    key = str(ticker or "").strip()
    if not key:
        return []

    df = _load_announcements()
    if df.empty:
        return []

    matched = df[df["Ticker"].astype(str) == key].copy()
    return _serialize_rows(matched, max(int(n or 10), 1))


def score_stock_news_sentiment(ticker: str, days: int = 3, limit: int = 5) -> tuple[float, str]:
    """Return a conservative announcement-derived sentiment score for scoring_service."""
    rows = get_stock_news(ticker, max(limit * 3, 15))
    if not rows:
        return 50.0, f"近{days}日無重大公告"

    recent = _recent_rows(rows, days=days, limit=limit)

    if not recent:
        return 50.0, f"近{days}日無重大公告"

    scores = [float(row.get("sentiment_score", 50.0)) for row in recent]
    avg_score = round(sum(scores) / len(scores), 1) if scores else 50.0
    counts = Counter(str(row.get("sentiment", "neutral")) for row in recent)

    detail_parts: list[str] = []
    if counts.get("positive"):
        detail_parts.append(f"近{days}日偏正面{counts['positive']}則")
    if counts.get("negative"):
        detail_parts.append(f"偏負面{counts['negative']}則")
    if counts.get("neutral") and not detail_parts:
        detail_parts.append(f"近{days}日中性公告{counts['neutral']}則")

    latest_title = str(recent[0].get("title", "")).strip()
    if latest_title:
        detail_parts.append(f"最新：{latest_title[:28]}")

    final_detail = "、".join(detail_parts) if detail_parts else f"近{days}日無重大公告"
    return maybe_apply_finnhub_overlay(avg_score, final_detail, ticker)


def get_stock_news_alert(ticker: str, days: int = 7, limit: int = 5) -> dict[str, Any]:
    """Return a structured per-ticker news alert for holding review surfaces."""
    rows = get_stock_news(ticker, max(limit * 3, 15))
    recent = _recent_rows(rows, days=days, limit=limit)
    if not recent:
        return {
            "status": "no_recent_news",
            "alert_level": "green",
            "score": 50.0,
            "summary": f"近{days}日無重大公告",
            "negative_count": 0,
            "positive_count": 0,
            "neutral_count": 0,
            "latest_date": None,
            "latest_title": None,
            "rows": [],
        }

    scores = [float(row.get("sentiment_score", 50.0)) for row in recent]
    avg_score = round(sum(scores) / len(scores), 1) if scores else 50.0
    counts = Counter(str(row.get("sentiment", "neutral")) for row in recent)
    negative_count = int(counts.get("negative", 0))
    positive_count = int(counts.get("positive", 0))
    neutral_count = int(counts.get("neutral", 0))
    latest = recent[0]

    alert_level = "yellow" if negative_count > 0 or avg_score < 45.0 else "green"
    if negative_count:
        summary = f"近{days}日負面重大公告 {negative_count} 則"
    elif positive_count:
        summary = f"近{days}日正面重大公告 {positive_count} 則"
    else:
        summary = f"近{days}日中性公告 {neutral_count} 則"

    latest_title = str(latest.get("title", "")).strip()
    if latest_title:
        summary = f"{summary}；最新：{latest_title[:32]}"

    return {
        "status": "ok",
        "alert_level": alert_level,
        "score": avg_score,
        "summary": summary,
        "negative_count": negative_count,
        "positive_count": positive_count,
        "neutral_count": neutral_count,
        "latest_date": latest.get("date"),
        "latest_title": latest.get("title"),
        "rows": recent,
    }
