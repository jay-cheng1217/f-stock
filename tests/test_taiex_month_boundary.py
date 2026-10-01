"""月初首日 TAIEX 報酬指數「本月尚無資料」邊界(2026-10-01 假警報三連發後修復)。"""
import json
from datetime import date

import pandas as pd
import pytest

from scripts import update_taiex_total_return_index as taiex

NO_DATA_PAYLOAD = {"stat": "很抱歉， 沒有符合條件的資料!"}


class _FakeResponse:
    def __init__(self, payload: dict):
        self.content = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    def raise_for_status(self) -> None:
        return None


def _patch_session(monkeypatch, payload: dict) -> None:
    monkeypatch.setattr(
        taiex.SESSION, "get", lambda *a, **k: _FakeResponse(payload)
    )


def test_current_month_no_data_returns_empty(monkeypatch):
    _patch_session(monkeypatch, NO_DATA_PAYLOAD)
    this_month = pd.Timestamp(date.today()).to_period("M").start_time
    frame = taiex._fetch_month(this_month, retries=1)
    assert frame.empty


def test_past_month_no_data_still_raises(monkeypatch):
    _patch_session(monkeypatch, NO_DATA_PAYLOAD)
    past_month = pd.Timestamp(date.today()).to_period("M").start_time - pd.DateOffset(months=2)
    with pytest.raises(RuntimeError, match="MFI94U"):
        taiex._fetch_month(past_month, retries=1)


def test_current_month_other_error_still_raises(monkeypatch):
    _patch_session(monkeypatch, {"stat": "伺服器忙碌中"})
    this_month = pd.Timestamp(date.today()).to_period("M").start_time
    with pytest.raises(RuntimeError, match="MFI94U"):
        taiex._fetch_month(this_month, retries=1)
