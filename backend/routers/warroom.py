from __future__ import annotations

import json
import os

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from backend.services.warroom_service import (
    CHAMPION_DB_PATH,
    DASHBOARD_SUMMARY_PATH,
    LATEST_PREDICTION_RE,
    LATEST_UNIFIED_RE,
    PORTFOLIO_COMPARE_PATH,
    SHADOW_DB_PATH,
    UNIFIED_SIGNALS_FEED_PATH,
    build_dashboard_summary,
    build_portfolio_champion_vs_shadow,
    build_unified_signals_latest_feed,
    _latest_csv_path,
)

router = APIRouter(tags=["warroom"])


def _cached_unified_feed_is_fresh(payload: dict) -> bool:
    latest_signal_path = _latest_csv_path(LATEST_UNIFIED_RE)
    latest_prediction_path = _latest_csv_path(LATEST_PREDICTION_RE)
    try:
        from ml.chipk import find_latest_chipk_snapshot_path

        latest_chipk_path_obj = find_latest_chipk_snapshot_path()
        latest_chipk_path = str(latest_chipk_path_obj) if latest_chipk_path_obj else None
    except Exception:
        latest_chipk_path = None
    builder_code = getattr(build_unified_signals_latest_feed, "__code__", None)
    builder_path = getattr(builder_code, "co_filename", None)

    if latest_signal_path:
        cached_path = payload.get("source_signal_path")
        if not cached_path:
            return False
        if os.path.abspath(str(cached_path)) != os.path.abspath(latest_signal_path):
            return False
    return _cache_is_newer_than_sources(
        UNIFIED_SIGNALS_FEED_PATH,
        [latest_signal_path, latest_prediction_path, latest_chipk_path, builder_path, __file__],
    )


def _load_or_build(path: str, builder, freshness_check=None):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                payload = json.load(fh)
            if freshness_check is None or freshness_check(payload):
                return payload
        except Exception:
            pass
    payload = builder()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
    except Exception:
        pass
    return payload


def _cache_is_newer_than_sources(cache_path: str, source_paths: list[str | None]) -> bool:
    if not os.path.exists(cache_path):
        return False
    existing_sources = [path for path in source_paths if path and os.path.exists(path)]
    if not existing_sources:
        return True
    cache_mtime = os.path.getmtime(cache_path)
    latest_source_mtime = max(os.path.getmtime(path) for path in existing_sources)
    return cache_mtime >= latest_source_mtime


@router.get("/api/warroom/dashboard_summary.json")
def get_dashboard_summary():
    return JSONResponse(
        content=_load_or_build(
            DASHBOARD_SUMMARY_PATH,
            build_dashboard_summary,
            lambda payload: _cache_is_newer_than_sources(
                DASHBOARD_SUMMARY_PATH,
                [CHAMPION_DB_PATH, SHADOW_DB_PATH],
            ),
        )
    )


@router.get("/api/warroom/portfolio_champion_vs_shadow.json")
def get_portfolio_compare():
    return JSONResponse(
        content=_load_or_build(
            PORTFOLIO_COMPARE_PATH,
            build_portfolio_champion_vs_shadow,
            lambda payload: _cache_is_newer_than_sources(
                PORTFOLIO_COMPARE_PATH,
                [CHAMPION_DB_PATH, SHADOW_DB_PATH],
            ),
        )
    )


@router.get("/api/warroom/unified_signals_latest.json")
def get_unified_signals_latest():
    return JSONResponse(
        content=_load_or_build(
            UNIFIED_SIGNALS_FEED_PATH,
            build_unified_signals_latest_feed,
            _cached_unified_feed_is_fresh,
        )
    )
