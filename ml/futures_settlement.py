"""TAIFEX domestic equity index settlement window helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from scripts.taiwan_trading_calendar import next_taiwan_trading_day, previous_taiwan_trading_day


PHASE_NONE = ""
PHASE_PRE_SETTLEMENT = "pre_settlement_day"
PHASE_SETTLEMENT = "settlement_day"


@dataclass(frozen=True)
class SettlementWindow:
    active: bool
    phase: str = PHASE_NONE
    settlement_date: str = ""
    days_to_settlement: int | None = None


def _as_date(value: str | date | datetime) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.strptime(str(value), "%Y-%m-%d").date()


def monthly_equity_index_settlement_day(year: int, month: int) -> date:
    """Return monthly TX/MTX/TMF/TXO-style settlement day.

    Domestic equity index futures/options normally expire on the third
    Wednesday. If that day is a Taiwan market holiday, use the next Taiwan
    trading day, matching the 2026 February holiday adjustment.
    """

    day = date(year, month, 1)
    while day.weekday() != 2:
        day += timedelta(days=1)
    third_wednesday = day + timedelta(days=14)
    return next_taiwan_trading_day(third_wednesday - timedelta(days=1))


def nearest_monthly_settlement_window(entry_date: str | date | datetime) -> SettlementWindow:
    """Flag the settlement day and the trading day immediately before it."""

    entry = _as_date(entry_date)
    settlement = monthly_equity_index_settlement_day(entry.year, entry.month)
    if entry > settlement and entry.month == 12:
        next_month_settlement = monthly_equity_index_settlement_day(entry.year + 1, 1)
    elif entry > settlement:
        next_month_settlement = monthly_equity_index_settlement_day(entry.year, entry.month + 1)
    else:
        next_month_settlement = settlement

    previous_trade_day = previous_taiwan_trading_day(next_month_settlement)
    if entry == next_month_settlement:
        return SettlementWindow(
            active=True,
            phase=PHASE_SETTLEMENT,
            settlement_date=next_month_settlement.isoformat(),
            days_to_settlement=0,
        )
    if entry == previous_trade_day:
        return SettlementWindow(
            active=True,
            phase=PHASE_PRE_SETTLEMENT,
            settlement_date=next_month_settlement.isoformat(),
            days_to_settlement=1,
        )
    return SettlementWindow(
        active=False,
        settlement_date=next_month_settlement.isoformat(),
        days_to_settlement=(next_month_settlement - entry).days,
    )
