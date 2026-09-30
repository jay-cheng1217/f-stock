from types import SimpleNamespace

import pandas as pd
import pytest

import twstock
from scripts import fetch_tdcc_weekly as fetch


def _guard_legacy(monkeypatch):
    def forbidden():
        pytest.fail("Step 9 attempted legacy summary rebuild")
    monkeypatch.setattr(twstock, "_summarize_tdcc", forbidden)


def test_step9_false_is_failure_not_old_summary_success(monkeypatch):
    _guard_legacy(monkeypatch)
    monkeypatch.setattr(fetch, "fetch_and_save", lambda: False)
    with pytest.raises(RuntimeError, match="source refresh failed"):
        twstock.step9_update_tdcc()


def test_step9_exception_propagates_without_old_summary(monkeypatch):
    _guard_legacy(monkeypatch)
    def outage():
        raise OSError("official request failed")
    monkeypatch.setattr(fetch, "fetch_and_save", outage)
    with pytest.raises(OSError, match="official request failed"):
        twstock.step9_update_tdcc()


def test_empty_canonical_response_preserves_existing_summary(tmp_path, monkeypatch):
    _guard_legacy(monkeypatch)
    summary = tmp_path / "tdcc_summary.csv"
    summary.write_bytes(b"old verified summary\n")
    monkeypatch.setattr(fetch, "TDCC_DIR", str(tmp_path))
    monkeypatch.setattr(fetch.requests, "get", lambda *a, **k: SimpleNamespace(
        raise_for_status=lambda: None, json=lambda: []))
    monkeypatch.setattr(fetch, "rebuild_summary", lambda: pytest.fail("empty source rebuilt summary"))
    with pytest.raises(RuntimeError, match="source refresh failed"):
        twstock.step9_update_tdcc()
    assert summary.read_bytes() == b"old verified summary\n"


def test_step9_delegates_each_call_even_same_existing_week(tmp_path, monkeypatch):
    _guard_legacy(monkeypatch)
    monkeypatch.setattr(fetch, "TDCC_DIR", str(tmp_path))
    payload = [{"資料日期": "20260904", "證券代號": "2317", "持股分級": level,
                "人數": 10, "股數": 1000, "占集保庫存數比例%": 1.0} for level in range(1, 16)]
    calls, summaries = [], []
    def get(*args, **kwargs):
        calls.append(args[0])
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: payload)
    monkeypatch.setattr(fetch.requests, "get", get)
    monkeypatch.setattr(fetch, "rebuild_summary", lambda: summaries.append(True))
    assert twstock.step9_update_tdcc() is True
    raw = tmp_path / "tdcc_20260904.csv"
    assert len(pd.read_csv(raw)) == 15
    # Same-week official revision must replace the previous bytes after validation.
    payload[-1]["占集保庫存數比例%"] = 2.0
    assert twstock.step9_update_tdcc() is True
    assert pd.read_csv(raw).iloc[-1]["占集保庫存數比例%"] == 2.0
    assert len(calls) == 2 and summaries == [True, True]
    before = raw.read_bytes()
    payload[-1]["占集保庫存數比例%"] = None
    with pytest.raises(ValueError, match="numeric"):
        twstock.step9_update_tdcc()
    assert len(calls) == 3 and summaries == [True, True]
    assert raw.read_bytes() == before
