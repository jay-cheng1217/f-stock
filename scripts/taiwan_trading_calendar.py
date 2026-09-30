"""Taiwan market trading-day helpers.

The built-in holiday set is based on the TWSE 115-year (2026) market
holiday schedule:
https://www.twse.com.tw/holidaySchedule/holidaySchedule?queryYear=115&response=html
"""
from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta
from typing import Iterator

# 臨時休市覆蓋檔（颱風假等非年度行事曆休市）；由 freshness gate 自動偵測寫入，也可手動編輯
ADHOC_CLOSURES_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "config",
    "adhoc_market_closures.json",
)


TAIWAN_MARKET_HOLIDAYS = {
    date(2026, 1, 1),
    date(2026, 2, 12),
    date(2026, 2, 13),
    date(2026, 2, 16),
    date(2026, 2, 17),
    date(2026, 2, 18),
    date(2026, 2, 19),
    date(2026, 2, 20),
    date(2026, 2, 27),
    date(2026, 4, 3),
    date(2026, 4, 6),
    date(2026, 5, 1),
    date(2026, 6, 19),
    date(2026, 9, 25),
    date(2026, 9, 28),
    date(2026, 10, 9),
    date(2026, 10, 26),
    date(2026, 12, 25),
}


def _load_adhoc_closures() -> set[date]:
    try:
        with open(ADHOC_CLOSURES_PATH, encoding="utf-8") as fh:
            payload = json.load(fh)
    except (OSError, ValueError):
        return set()
    result: set[date] = set()
    for entry in payload.get("closures", []):
        raw = entry.get("date") if isinstance(entry, dict) else entry
        try:
            result.add(datetime.fromisoformat(str(raw)[:10]).date())
        except ValueError:
            continue
    return result


def add_adhoc_closure(day: date, reason: str) -> None:
    """Persist an unscheduled market closure and apply it to the in-process calendar."""
    try:
        with open(ADHOC_CLOSURES_PATH, encoding="utf-8") as fh:
            payload = json.load(fh)
    except (OSError, ValueError):
        payload = {"closures": []}
    existing = {str(e.get("date") if isinstance(e, dict) else e)[:10] for e in payload.get("closures", [])}
    if day.isoformat() not in existing:
        payload.setdefault("closures", []).append(
            {"date": day.isoformat(), "reason": reason, "recorded_at": datetime.now().isoformat(timespec="seconds")}
        )
        os.makedirs(os.path.dirname(ADHOC_CLOSURES_PATH), exist_ok=True)
        with open(ADHOC_CLOSURES_PATH, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
    TAIWAN_MARKET_HOLIDAYS.add(day)


TAIWAN_MARKET_HOLIDAYS |= _load_adhoc_closures()


def _as_date(value: date | datetime) -> date:
    if isinstance(value, datetime):
        return value.date()
    return value


def is_taiwan_trading_day(value: date | datetime) -> bool:
    day = _as_date(value)
    return day.weekday() < 5 and day not in TAIWAN_MARKET_HOLIDAYS


def previous_taiwan_trading_day(value: date | datetime) -> date:
    day = _as_date(value) - timedelta(days=1)
    while not is_taiwan_trading_day(day):
        day -= timedelta(days=1)
    return day


def next_taiwan_trading_day(value: date | datetime) -> date:
    day = _as_date(value) + timedelta(days=1)
    while not is_taiwan_trading_day(day):
        day += timedelta(days=1)
    return day


def iter_taiwan_trading_days(start: date | datetime, end: date | datetime) -> Iterator[date]:
    day = _as_date(start)
    end_day = _as_date(end)
    while day <= end_day:
        if is_taiwan_trading_day(day):
            yield day
        day += timedelta(days=1)
