"""Forecast API for advisory scenario charts."""

from fastapi import APIRouter, Query

from backend.services.forecast_service import build_price_forecast


router = APIRouter(prefix="/api/forecast", tags=["forecast"])


@router.get("/{ticker}")
def price_forecast(ticker: str, horizon: int = Query(20, ge=5, le=60)):
    """Return advisory price-scenario forecast and risk score for one ticker."""
    return build_price_forecast(ticker, horizon_days=horizon)
