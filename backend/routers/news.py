"""News API routes."""

from fastapi import APIRouter, Query

from backend.services.news_service import get_latest_news, get_stock_news
from backend.services.tw_news_intel_service import (
    build_news_intel_dashboard,
    build_ticker_news_intel,
)

router = APIRouter(prefix="/api/news", tags=["news"])


@router.get("/latest")
def latest_news(n: int = Query(20, ge=1, le=100)):
    """Return latest local MOPS announcements."""
    return {"data": get_latest_news(n)}


@router.get("/intel")
def news_intel_dashboard(
    scope: str = Query("top", pattern="^(top|holdings)$"),
    limit: int = Query(6, ge=1, le=20),
    days: int = Query(7, ge=1, le=30),
    include_web: bool = Query(True),
):
    """Taiwan-stock news intelligence dashboard for candidates or holdings."""
    return build_news_intel_dashboard(scope=scope, limit=limit, days=days, include_web=include_web)


@router.get("/intel/{ticker}")
def ticker_news_intel(
    ticker: str,
    name: str | None = Query(None),
    limit: int = Query(8, ge=1, le=20),
    days: int = Query(7, ge=1, le=30),
    include_web: bool = Query(True),
):
    """Taiwan-stock news intelligence for one ticker."""
    return build_ticker_news_intel(ticker=ticker, name=name, limit=limit, days=days, include_web=include_web)


@router.get("/{ticker}")
def stock_news(ticker: str, n: int = Query(10, ge=1, le=50)):
    """Return local MOPS announcements for a ticker."""
    return {"ticker": ticker, "data": get_stock_news(ticker, n)}
