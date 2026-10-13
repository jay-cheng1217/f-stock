"""rere 公開標的跟單帳本:進場/出場規則與市場對照。"""
import numpy as np
import pandas as pd

from scripts.rere_public_follow_tracker import build_follow_book, render


def _bars(closes, *, liquid=True, locked_at=None):
    days = pd.bdate_range("2026-07-01", periods=len(closes)).strftime("%Y-%m-%d")
    close = np.array(closes, dtype=float)
    high, low = close * 1.01, close * 0.99
    if locked_at is not None:
        high[locked_at] = low[locked_at] = close[locked_at]
    return pd.DataFrame({"adj": close, "close": close, "high": high, "low": low, "liquid": liquid}, index=days)


def _campaigns(rows):
    return pd.DataFrame(rows, columns=["ticker", "name", "first_public_date", "exit_public_date", "kind", "source"])


def test_entry_is_next_trading_day_close_and_excess_is_vs_liquid_market():
    bars = {"1111": _bars([100, 100, 110, 121]), "2222": _bars([50, 50, 50, 50]),
            "3333": _bars([10, 10, 30, 90], liquid=False)}                       # 不流動,不算進市場
    book = build_follow_book(_campaigns([["1111", "甲", "2026-07-01", "", "campaign", "x"]]), bars)
    r = book.iloc[0]
    assert r.entry_date == "2026-07-02" and r.entry_close == 100 and r.status == "holding"
    assert r.ret_pct == 21.0 and r.market_pct == 10.5 and r.excess_pct == 10.5   # 市場 = (21% + 0%) / 2


def test_locked_limit_up_entry_is_delayed_and_public_exit_is_followed():
    closes = [100, 110, 121, 120, 118, 130, 140]
    bars = {"1111": _bars(closes, locked_at=1), "2222": _bars([50] * 7)}
    book = build_follow_book(_campaigns([["1111", "甲", "2026-07-01", "2026-07-07", "event", "x"]]), bars)
    r = book.iloc[0]
    assert r.entry_date == "2026-07-03" and bool(r.entry_delayed)                 # 7/2 一字鎖漲停買不到
    assert r.status == "exited_public" and r.last_date == "2026-07-08"           # 公開出場 7/7 → 7/8 收盤跟出
    assert r.ret_pct == round((130 / 121 - 1) * 100, 2)


def test_matured_after_hold_and_pending_before_any_bar():
    bars = {"1111": _bars(list(range(100, 170))), "2222": _bars([50] * 70)}
    book = build_follow_book(_campaigns([["1111", "甲", "2026-07-01", "", "campaign", "x"],
                                         ["1111", "乙", "2026-12-31", "", "campaign", "x"],
                                         ["9999", "丙", "2026-07-01", "", "trial", "x"]]), bars, hold=60)
    assert book.status.tolist() == ["matured60", "pending_entry", "no_data"]
    assert book.iloc[0].bars_held == 60 and "ret20_pct" in book.columns
    text = render(book, "2026-10-12")
    assert "不是她的損益" in text and "pending_entry" in text


def test_core_disclosures_are_reported_separately_from_trade_ideas():
    bars = {"1111": _bars([100, 100, 110, 121]), "5292": _bars([200, 200, 210, 220]), "2222": _bars([50, 50, 50, 50])}
    book = build_follow_book(_campaigns([["1111", "甲", "2026-07-01", "", "campaign", "x"],
                                         ["5292", "華懋", "2026-07-01", "", "core", "x"]]), bars)
    text = render(book, "2026-07-06")
    all_line = next(line for line in text.splitlines() if line.startswith("- 全部(不含存股揭露)"))
    core_line = next(line for line in text.splitlines() if line.startswith("- 存股戶揭露(core)"))
    assert ":1 筆" in all_line and ":1 筆" in core_line
