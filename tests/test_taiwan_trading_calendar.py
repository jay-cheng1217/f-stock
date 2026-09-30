from datetime import date

from scripts.smart_update_auto import _prev_trading_day, is_weekday
from scripts.taiwan_trading_calendar import (
    is_taiwan_trading_day,
    iter_taiwan_trading_days,
    next_taiwan_trading_day,
    previous_taiwan_trading_day,
)
from twstock import _find_missing_dates


def test_labor_day_2026_is_not_trading_day():
    assert not is_taiwan_trading_day(date(2026, 5, 1))
    assert is_weekday(date(2026, 5, 1))


def test_previous_trading_day_skips_labor_day_and_weekend():
    assert previous_taiwan_trading_day(date(2026, 5, 2)) == date(2026, 4, 30)
    assert _prev_trading_day(date(2026, 5, 2)) == date(2026, 4, 30)


def test_next_trading_day_skips_labor_day_and_weekend():
    assert next_taiwan_trading_day(date(2026, 4, 30)) == date(2026, 5, 4)


def test_iter_trading_days_excludes_market_holidays():
    assert list(iter_taiwan_trading_days(date(2026, 4, 30), date(2026, 5, 4))) == [
        date(2026, 4, 30),
        date(2026, 5, 4),
    ]


def test_twstock_missing_dates_excludes_market_holidays(tmp_path):
    missing = _find_missing_dates(
        tmp_path,
        prefix="fund_",
        suffix=".csv",
        end_date=date(2026, 5, 4),
        lookback_days=4,
    )

    assert date(2026, 5, 1) not in missing
    assert missing == [date(2026, 4, 30), date(2026, 5, 4)]
