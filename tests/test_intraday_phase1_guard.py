from datetime import datetime

from scripts import smart_update_auto as sua

TRADING_DAY = datetime(2026, 9, 30)   # Wednesday, regular session
HOLIDAY = datetime(2026, 9, 28)       # Teacher's Day closure


def _at(day: datetime, hour: int, minute: int) -> datetime:
    return day.replace(hour=hour, minute=minute)


def test_blocks_phase1_during_market_hours():
    assert sua._intraday_phase1_blocked(_at(TRADING_DAY, 11, 52), "1", False)
    assert sua._intraday_phase1_blocked(_at(TRADING_DAY, 8, 30), "all", False)
    assert sua._intraday_phase1_blocked(_at(TRADING_DAY, 14, 29), "1", False)


def test_allows_scheduled_evening_and_morning_runs():
    assert sua._intraday_phase1_blocked(_at(TRADING_DAY, 19, 30), "1", False) is None
    assert sua._intraday_phase1_blocked(_at(TRADING_DAY, 14, 30), "1", False) is None
    assert sua._intraday_phase1_blocked(_at(TRADING_DAY, 7, 0), "2", False) is None


def test_phase2_never_blocked_by_intraday_guard():
    assert sua._intraday_phase1_blocked(_at(TRADING_DAY, 11, 0), "2", False) is None


def test_non_trading_day_not_blocked():
    assert sua._intraday_phase1_blocked(_at(HOLIDAY, 11, 0), "1", False) is None


def test_explicit_allow_flag_overrides():
    assert sua._intraday_phase1_blocked(_at(TRADING_DAY, 11, 0), "1", True) is None


def test_main_refuses_before_taking_lock(monkeypatch):
    calls = []
    monkeypatch.setattr(sua, "_intraday_phase1_blocked", lambda now, phase, allow: "blocked")
    monkeypatch.setattr(sua, "acquire_lock", lambda *a, **k: calls.append("lock") or (None, None))
    assert sua.main(["--phase", "1"]) == 2
    assert calls == []
