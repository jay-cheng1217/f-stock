from datetime import datetime

from twstock import _resolve_revenue_update_window


def test_revenue_window_skips_before_publish_window():
    should_run, start_date, end_date, reason = _resolve_revenue_update_window(
        datetime(2026, 5, 2, 23, 30)
    )

    assert should_run is False
    assert start_date is None
    assert end_date.date().isoformat() == "2026-04-30"
    assert reason == "outside_monthly_publish_window"


def test_revenue_window_runs_during_publish_window():
    should_run, start_date, end_date, reason = _resolve_revenue_update_window(
        datetime(2026, 5, 11, 23, 30)
    )

    assert should_run is True
    assert start_date.date().isoformat() == "2026-03-01"
    assert end_date.date().isoformat() == "2026-04-30"
    assert reason == "monthly_publish_window"


def test_revenue_window_retry_overrides_calendar():
    should_run, start_date, end_date, reason = _resolve_revenue_update_window(
        datetime(2026, 5, 2, 23, 30),
        retry_only=True,
    )

    assert should_run is True
    assert start_date.date().isoformat() == "2025-09-01"
    assert end_date.date().isoformat() == "2026-04-30"
    assert reason == "retry"
