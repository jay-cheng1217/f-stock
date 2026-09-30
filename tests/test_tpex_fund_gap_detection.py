from __future__ import annotations

from datetime import date

import twstock


def test_fund_gap_detects_tpex_only_missing(tmp_path, monkeypatch):
    target = date(2026, 6, 22)
    monkeypatch.setattr(twstock, "iter_taiwan_trading_days", lambda start, end: [target])
    (tmp_path / "fund_20260622.csv").write_text("Ticker,Date\n2330,2026-06-22\n", encoding="utf-8")

    missing = twstock._find_missing_fund_dates(tmp_path, target, lookback_days=3)

    assert missing == [target]


def test_fund_gap_passes_when_both_markets_exist(tmp_path, monkeypatch):
    target = date(2026, 6, 22)
    monkeypatch.setattr(twstock, "iter_taiwan_trading_days", lambda start, end: [target])
    (tmp_path / "fund_20260622.csv").write_text("Ticker,Date\n2330,2026-06-22\n", encoding="utf-8")
    (tmp_path / "fund_tpex_20260622.csv").write_text("Ticker,Date\n3013,2026-06-22\n", encoding="utf-8")

    missing = twstock._find_missing_fund_dates(tmp_path, target, lookback_days=3)

    assert missing == []
