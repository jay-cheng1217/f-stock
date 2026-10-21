# -*- coding: utf-8 -*-
"""開盤 gap 提示只能用「今天」的即時報價;盤前拿到前一交易日報價時要誠實回報,不可把昨天日盤漲跌標成夜盤。

2026-10-21:名單 07:00 產生時,證交所即時加權仍是 10/20 的資料(z=10/20 收 46292.93、y=10/19 收 46822),
舊版算出 −1.1% 寫成「跳空下殺日」,實際當天開高約 1.5%;10/14~10/21 六份名單都是這樣。"""
import datetime as dt

import requests

from scripts import generate_entry_candidates as gen


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def _quote(monkeypatch, msg):
    monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp({"msgArray": [msg]}))


def test_premarket_quote_from_the_previous_session_is_not_reported_as_a_gap(monkeypatch):
    _quote(monkeypatch, {"d": "20261020", "t": "13:33:00", "z": "46292.93", "y": "46822.43"})
    out = gen._night_session_gap(today=dt.date(2026, 10, 21))
    assert out["source"] == "stale" and out["gap_pct"] is None
    assert "跳空" not in out["hint"] and "20261020" in out["hint"]


def test_same_day_quote_still_reports_the_opening_gap(monkeypatch):
    _quote(monkeypatch, {"d": "20261021", "t": "09:05:00", "z": "46995.02", "y": "46292.93"})
    out = gen._night_session_gap(today=dt.date(2026, 10, 21))
    assert out["source"] == "twii_realtime" and out["gap_pct"] == round((46995.02 / 46292.93 - 1) * 100, 2)
    assert "跳空開高日" in out["hint"]


def test_missing_quote_date_is_unknown_not_a_gap(monkeypatch):
    _quote(monkeypatch, {"z": "46995.02", "y": "46292.93"})
    out = gen._night_session_gap(today=dt.date(2026, 10, 21))
    assert out["gap_pct"] is None and "未知" in out["hint"]


def test_non_trading_day_returns_none(monkeypatch):
    _quote(monkeypatch, {"d": "20261024", "z": "1", "y": "1"})
    assert gen._night_session_gap(today=dt.date(2026, 10, 24)) is None   # 週六
