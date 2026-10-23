# -*- coding: utf-8 -*-
"""名單日遇臨時休市(颱風假)的帳本列要標 void(休市不計),不可永遠「待判定」,也不可留著占位假棒算出的進場。
2026-10-23 颱風休市:當天名單 39 列只看名單日 K 棒觸發,永遠不會有 K 棒;2026-07-10 巴威颱風的列有些用了事後被清掉的假棒結算。"""
import datetime as dt

import pandas as pd

from scripts import entry_filter_tracker as tracker


def test_closure_day_rows_are_voided_and_trading_day_rows_are_untouched(tmp_path, monkeypatch):
    monkeypatch.setattr(tracker, "LEDGER", tmp_path / "main.csv")
    monkeypatch.setattr(tracker, "STATIC_LEDGER", tmp_path / "static.json")
    monkeypatch.setattr(tracker, "DAILY", tmp_path)
    closed = {dt.date(2026, 10, 23), dt.date(2026, 7, 10)}
    monkeypatch.setattr(tracker, "is_taiwan_trading_day", lambda d: d not in closed)
    base = {"lane": "main", "state": "go", "zone_low": 10, "zone_high": 11, "stop": 9, "horizon": 20}
    led = tmp_path / "led.csv"
    pd.DataFrame([
        {**base, "trade_date": "2026-10-23", "ticker": "1111", "status": "pending"},
        {**base, "trade_date": "2026-07-10", "ticker": "2222", "status": "stopped",
         "entry_date": "2026-07-10", "entry_price": 10.5, "ret_pct": -13.0},
        {**base, "trade_date": "2026-10-22", "ticker": "3333", "status": "pending"},
    ], columns=tracker.COLS).to_csv(led, index=False)

    tracker.check(ledger=led)
    out = pd.read_csv(led, dtype=str).set_index("ticker")
    assert out.loc["1111", "status"] == "void" and out.loc["2222", "status"] == "void"
    assert "原狀態 stopped" in out.loc["2222", "exit_reason"]          # 原狀態留給稽核
    assert out.loc["3333", "status"] == "pending"                     # 交易日的列照常(沒有 K 棒就仍待判定)
    assert not (tmp_path / "static.json").exists()                    # 非正式帳本不發布 static JSON

    tracker.check(ledger=led)                                         # 重跑不重複改寫
    assert pd.read_csv(led, dtype=str).set_index("ticker").loc["2222", "exit_reason"].count("原狀態") == 1
