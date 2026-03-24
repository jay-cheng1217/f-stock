"""評分 API."""

from fastapi import APIRouter, Query
from backend.services.scoring_service import score_stock
from backend.services.ranking_service import get_ranking

router = APIRouter(prefix="/api/scoring", tags=["scoring"])


@router.get("/{ticker}")
def get_score(ticker: str):
    """四面向評分 + 中文說明."""
    return score_stock(ticker)


@router.get("/top/list")
def top_scores(n: int = Query(20, ge=1, le=100)):
    """綜合排行前N名."""
    return get_ranking("score", n)
