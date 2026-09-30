"""Stock workbench API."""

from fastapi import APIRouter, Query

from backend.services.stock_workbench_service import build_stock_workbench


router = APIRouter(prefix="/api/workbench", tags=["workbench"])


@router.get("/{ticker}")
def stock_workbench(ticker: str, days: int = Query(240, ge=60, le=1000)):
    """Return advisory technical and ML context for one ticker."""
    return build_stock_workbench(ticker, days=days)
