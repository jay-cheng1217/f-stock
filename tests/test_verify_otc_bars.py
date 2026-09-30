from datetime import date

import pandas as pd
import pytest

import twstock
from scripts import verify_otc_bars

TODAY = date(2026, 9, 21)  # Monday; 2026-09-18 is Friday, 2026-09-20 is Sunday


def bar(day, close=18.0, volume=1000.0):
    return {"Date": day, "Open": close, "High": close, "Low": close, "Close": close, "Volume": volume}


def test_drop_fake_flat_bars_removes_non_trading_day_and_future_rows():
    frame = pd.DataFrame([
        bar("2026-09-18"),
        bar("2026-09-20", volume=2006.0),
        bar("2026-09-22"),
        bar("2026-09-17", volume=0.0),
    ])
    kept = twstock._drop_fake_flat_bars(frame, today=TODAY)
    assert kept["Date"].tolist() == ["2026-09-18"]


def test_verifier_drops_sunday_bar_and_never_queries_official_for_it(tmp_path, monkeypatch):
    daily = tmp_path / "daily"
    daily.mkdir()
    monkeypatch.setattr(verify_otc_bars, "DAILY", daily)
    monkeypatch.setattr(verify_otc_bars, "LOG", tmp_path / "logs")
    monkeypatch.setattr(verify_otc_bars, "CACHE", tmp_path / "logs" / "cache")

    days = ["2026-09-14", "2026-09-15", "2026-09-16", "2026-09-17", "2026-09-18"]
    rows = [bar(d) for d in days] + [bar("2026-09-20", volume=2006.0)]
    pd.DataFrame(rows).to_csv(daily / "5276.csv", index=False, encoding="utf-8-sig")

    queried = []

    def fetch(day, cache_dir=None):
        queried.append(day.date())
        return pd.DataFrame([{"Ticker": "5276", **bar(day.strftime("%Y-%m-%d"))}])

    summary = verify_otc_bars.run(lookback_trading_days=5, today=TODAY, fetch=fetch)

    assert date(2026, 9, 20) not in queried
    assert queried[-1] == date(2026, 9, 18)
    assert summary["rows_dropped"] == 1 and summary["files_patched"] == 1
    written = pd.read_csv(daily / "5276.csv")
    assert "2026-09-20" not in written["Date"].tolist()
    assert written["Date"].tolist()[-1] == "2026-09-18"
    repair = pd.read_csv(next((tmp_path / "logs").glob("official_bar_repair_*.csv")))
    assert repair["changed_columns"].tolist() == [verify_otc_bars.DROPPED_MARKER]
