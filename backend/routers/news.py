"""新聞 API (Phase 2F 完整實作)."""

from fastapi import APIRouter, Query
from backend.services.news_service import get_latest_news, get_stock_news

router = APIRouter(prefix="/api/news", tags=["news"])


@router.get("/latest")
def latest_news(n: int = Query(20, ge=1, le=100)):
    """最新台股新聞."""
    return {"data": get_latest_news(n)}


@router.get("/{ticker}")
def stock_news(ticker: str, n: int = Query(10, ge=1, le=50)):
    """個股新聞."""
    return {"ticker": ticker, "data": get_stock_news(ticker, n)}
