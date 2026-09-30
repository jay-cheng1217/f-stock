from __future__ import annotations

import pandas as pd

from scripts import entry_filter_tracker as tracker


def _setup_ledger(tmp_path, monkeypatch, *, second_close: float) -> None:
    ledger = tmp_path / "entry_filter_ledger.csv"
    daily = tmp_path / "daily"
    daily.mkdir()
    monkeypatch.setattr(tracker, "LEDGER", ledger)
    monkeypatch.setattr(tracker, "DAILY", daily)
    monkeypatch.setattr(tracker, "STATIC_LEDGER", tmp_path / "entry_filter_ledger.json")
    pd.DataFrame(
        [
            {
                "trade_date": "2026-06-01",
                "ticker": "2330",
                "name": "台積電",
                "lane": "main",
                "state": "small",
                "zone_low": 99.0,
                "zone_high": 101.0,
                "stop": 95.0,
                "horizon": 20,
                "status": "holding",
                "entry_date": "2026-06-01",
                "entry_price": 100.0,
            }
        ]
    ).to_csv(ledger, index=False, encoding="utf-8-sig")
    pd.DataFrame(
        [
            {"Date": "2026-06-01", "Open": 100.0, "High": 101.0, "Low": 99.0, "Close": 100.0},
            {"Date": "2026-06-02", "Open": second_close, "High": second_close, "Low": second_close, "Close": second_close},
        ]
    ).to_csv(daily / "2330.csv", index=False)


def test_cash_dividend_gap_does_not_create_false_stop(tmp_path, monkeypatch):
    _setup_ledger(tmp_path, monkeypatch, second_close=90.0)
    monkeypatch.setattr(tracker, "cum_dividend", lambda ticker, after_date, until_date: 10.0)

    tracker.check()

    row = pd.read_csv(tracker.LEDGER, dtype={"ticker": str}).iloc[0]
    assert row["status"] == "holding"
    assert row["last_close"] == 90.0
    assert row["last_adjusted_close"] == 100.0
    assert row["cum_dividend_per_share"] == 10.0
    assert row["ret_pct"] == 0.0


def test_real_loss_still_triggers_stop_without_distribution(tmp_path, monkeypatch):
    _setup_ledger(tmp_path, monkeypatch, second_close=90.0)
    monkeypatch.setattr(tracker, "cum_dividend", lambda ticker, after_date, until_date: 0.0)

    tracker.check()

    row = pd.read_csv(tracker.LEDGER, dtype={"ticker": str}).iloc[0]
    assert row["status"] == "stopped"
    assert row["exit_price"] == 90.0
    assert row["exit_adjusted_price"] == 90.0
    assert row["ret_pct"] == -10.0
