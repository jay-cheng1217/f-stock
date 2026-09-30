"""Macro/policy event calendar API."""

from fastapi import APIRouter, Query

from backend.services.macro_event_service import build_macro_event_context, build_macro_strategy_context


router = APIRouter(prefix="/api/macro-events", tags=["macro-events"])


@router.get("/upcoming")
def upcoming_macro_events(
    as_of: str | None = Query(None),
    horizon: int = Query(20, ge=1, le=90),
):
    """Return sourced macro/policy events relevant to advisory forecast risk."""
    return build_macro_event_context(as_of=as_of, horizon_days=horizon)


@router.get("/strategy")
def macro_strategy_context(
    as_of: str | None = Query(None),
    horizon: int = Query(20, ge=1, le=120),
):
    """Return macro event plus market-sentiment context used by selection overlays."""
    return build_macro_strategy_context(as_of=as_of, horizon_days=horizon)
