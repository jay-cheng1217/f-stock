import sys

from scripts import update_ex_dividend_calendar as updater


def test_daily_update_returns_nonzero_when_official_sources_fail(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["update_ex_dividend_calendar.py"])
    monkeypatch.setattr(
        updater,
        "fetch_twse",
        lambda *args: (_ for _ in ()).throw(RuntimeError("twse down")),
    )
    monkeypatch.setattr(
        updater,
        "fetch_tpex",
        lambda *args: (_ for _ in ()).throw(RuntimeError("tpex down")),
    )
    saved = []
    monkeypatch.setattr(updater, "merge_save", lambda rows: saved.extend(rows))

    assert updater.main() == 2
    assert saved == []


def test_partial_success_is_saved_but_still_reports_degraded(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["update_ex_dividend_calendar.py"])
    event = {
        "stock_id": "2330",
        "date": "2026-07-16",
        "before_price": 1000.0,
        "after_price": 990.0,
        "stock_and_cache_dividend": 10.0,
    }
    monkeypatch.setattr(updater, "fetch_twse", lambda *args: [event])
    monkeypatch.setattr(
        updater,
        "fetch_tpex",
        lambda *args: (_ for _ in ()).throw(RuntimeError("tpex down")),
    )
    saved = []
    monkeypatch.setattr(updater, "merge_save", lambda rows: saved.extend(rows))

    assert updater.main() == 2
    assert saved == [event]


def test_valid_empty_responses_are_success(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["update_ex_dividend_calendar.py"])
    monkeypatch.setattr(updater, "fetch_twse", lambda *args: [])
    monkeypatch.setattr(updater, "fetch_tpex", lambda *args: [])
    monkeypatch.setattr(updater, "merge_save", lambda rows: None)

    assert updater.main() == 0
