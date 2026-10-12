"""2026-10-12:官方終止上市櫃的股票不得再進即時推論;排程每天跑官方K棒覆蓋稽核並告警。"""
import json
from datetime import date

import pytest

import ml.dataset as dataset
import ml.universe as universe


def _registry(tmp_path, events):
    path = tmp_path / "trading_status_events.json"
    path.write_text(json.dumps({"schema_version": 1, "events": events}, ensure_ascii=False), encoding="utf-8")
    return path


def _event(ticker, status, start, available, until=None):
    event = {"id": f"{ticker}-{status}", "ticker": ticker, "status": status, "effective_from": start,
             "available_on": available, "source_url": "https://example.invalid", "source_sha256": "0" * 64}
    if until:
        event["effective_until"] = until
    return event


def test_terminated_needs_effective_and_known_dates(tmp_path):
    path = _registry(tmp_path, [_event("5371", "suspended", "2026-08-24", "2026-08-04", "2026-09-03"),
                                _event("5371", "terminated", "2026-09-03", "2026-09-06"),
                                _event("1589", "suspended", "2026-04-07", "2026-04-01")])
    assert universe.terminated_tickers_as_of(date(2026, 9, 2), path) == {}
    assert universe.terminated_tickers_as_of(date(2026, 9, 5), path) == {}        # 生效但當時還不知道
    assert set(universe.terminated_tickers_as_of(date(2026, 9, 6), path)) == {"5371"}
    assert set(universe.active_trading_status(date(2026, 8, 25), "suspended", path)) == {"5371", "1589"}
    assert set(universe.active_trading_status(date(2026, 9, 3), "suspended", path)) == {"1589"}   # 結束日不含


def test_malformed_registry_fails_loudly(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"schema_version": 2, "events": []}), encoding="utf-8")
    with pytest.raises(ValueError):
        universe.load_trading_status_events(bad)
    path = _registry(tmp_path, [_event("5371", "delisted", "2026-09-03", "2026-09-06")])
    with pytest.raises(ValueError):
        universe.load_trading_status_events(path)
    assert universe.load_trading_status_events(tmp_path / "missing.json") == []


def test_real_registry_terminated_set():
    active = universe.terminated_tickers_as_of(date(2026, 10, 12))
    assert {"5371", "4130", "2867"} <= set(active)
    assert not {"1589", "4804"} & set(active)          # 長期停牌但尚未終止,不在第 1 項範圍
    assert "5371" not in universe.terminated_tickers_as_of(date(2026, 9, 2))


def test_live_snapshot_universe_drops_only_terminated(tmp_path, monkeypatch):
    path = _registry(tmp_path, [_event("5371", "terminated", "2026-09-03", "2026-09-06"),
                                _event("1589", "suspended", "2026-04-07", "2026-04-01")])
    monkeypatch.setattr(universe, "TRADING_STATUS_EVENTS_PATH", path)
    monkeypatch.setattr(dataset, "_list_daily_tickers", lambda: ["1589", "2330", "5371"])
    assert dataset._latest_snapshot_tickers(date(2026, 10, 12)) == ["1589", "2330"]
    assert dataset._latest_snapshot_tickers(date(2026, 9, 1)) == ["1589", "2330", "5371"]


def test_eligibility_reports_official_termination(tmp_path, monkeypatch):
    path = _registry(tmp_path, [_event("5371", "terminated", "2026-09-03", "2026-09-06")])
    monkeypatch.setattr(universe, "TRADING_STATUS_EVENTS_PATH", path)
    info = dataset.diagnose_stock_eligibility("5371")
    assert info["reason_code"] == "official_terminated" and "2026-09-03" in info["message"]


def test_capital_reduction_halts_are_explained_by_official_resumption_rows():
    import pandas as pd
    from scripts.latest_bar_coverage_runtime import explain_capital_reduction_halts

    result = {"status": "warn", "error_tickers": [], "unknown_tickers": ["6173", "8183", "9999"],
              "counts": {"unknown_official_absence": 3},
              "rows": [{"ticker": "6173", "classification": "unknown_official_absence", "csv_date": "2026-10-06"},
                       {"ticker": "8183", "classification": "unknown_official_absence", "csv_date": "2026-09-22"},
                       {"ticker": "9999", "classification": "unknown_official_absence", "csv_date": "2026-10-06"}]}
    actions = pd.DataFrame([
        {"stock_id": "6173", "date": "2026-10-19", "event_type": "capital_reduction", "before_price": 284.0,
         "official_source": "https://www.tpex.org.tw/www/zh-tw/bulletin/revivt"},
        {"stock_id": "9999", "date": "2026-10-19", "event_type": "capital_reduction", "before_price": 50.0,
         "official_source": "x"},                                               # 停牌前收盤對不上 → 不認定
        {"stock_id": "8183", "date": "2026-07-09", "event_type": "ex_right_dividend", "before_price": 33.8,
         "official_source": "x"}])
    out = explain_capital_reduction_halts(result, actions, {"6173": 284.0, "8183": 28.2, "9999": 40.0})
    assert out["unknown_tickers"] == ["8183", "9999"] and out["explained_by_capital_reduction"] == ["6173"]
    row = out["rows"][0]
    assert row["classification"] == "official_capital_reduction_halt"
    assert row["event"]["scheduled_resumption"] == "2026-10-19"
    assert out["counts"] == {"official_capital_reduction_halt": 1, "unknown_official_absence": 2}
    assert out["status"] == "warn"


def test_scheduled_coverage_audit_warns_and_never_raises(tmp_path, monkeypatch):
    import duckdb
    import scripts.latest_bar_coverage_runtime as runtime
    import scripts.smart_update_auto as sua

    class _Conn:
        def close(self):
            pass

    monkeypatch.setattr(duckdb, "connect", lambda *a, **k: _Conn())
    monkeypatch.setattr(sua, "LATEST_BAR_COVERAGE_REPORT", str(tmp_path / "coverage.json"))
    monkeypatch.setitem(sua.RUN_CONTEXT, "latest_bar_coverage_warning", None)
    monkeypatch.setattr(runtime, "build_stale_bar_coverage", lambda *a, **k: {
        "status": "warn", "counts": {"unknown_official_absence": 2}, "unknown_tickers": ["6173", "8183"],
        "error_tickers": [], "rows": []})
    result = sua._run_latest_bar_coverage("Phase-2")
    assert result["status"] == "warn" and (tmp_path / "coverage.json").exists()
    assert "6173" in sua.RUN_CONTEXT["latest_bar_coverage_warning"]
    assert any("6173" in w for w in sua._runtime_degraded_steps())

    def _boom(*a, **k):
        raise FileNotFoundError("official tape cache missing")

    monkeypatch.setattr(runtime, "build_stale_bar_coverage", _boom)
    assert sua._run_latest_bar_coverage("Phase-1") is None
    assert "latest_bar_coverage_failed" in sua.RUN_CONTEXT["latest_bar_coverage_warning"]


def _rows(dates):
    import pandas as pd
    return pd.DataFrame({"ticker": [f"{1000 + i}" for i in range(len(dates))], "Date": pd.to_datetime(dates),
                         "atr_pct": 0.02})


def test_stale_latest_rows_are_dropped_from_live_snapshot():
    snap = _rows(["2026-10-08"] * 40 + ["2026-10-06"])
    kept = dataset._drop_stale_latest_rows(snap, verbose=False)
    assert len(kept) == 40 and "1040" not in set(kept["ticker"])
    fresh = _rows(["2026-10-08"] * 5)
    assert len(dataset._drop_stale_latest_rows(fresh, verbose=False)) == 5


def test_half_ingested_snapshot_fails_closed():
    snap = _rows(["2026-10-08"] * 30 + ["2026-10-07"] * 10)      # 25% 落後 = 疑似只匯入一半
    with pytest.raises(ValueError, match="拒絕建立快照"):
        dataset._drop_stale_latest_rows(snap, verbose=False)


def test_build_latest_snapshot_applies_the_stale_row_filter(monkeypatch):
    import pandas as pd
    frames = {f"{2000 + i}": "2026-10-08" for i in range(30)}
    frames["6173"] = "2026-10-06"
    monkeypatch.setattr(dataset, "_latest_snapshot_tickers", lambda as_of=None: sorted(frames))
    monkeypatch.setattr(dataset, "_load_twii", lambda: None)
    monkeypatch.setattr(dataset, "load_single_stock", lambda tk, **kw: pd.DataFrame(
        {"ticker": [tk, tk], "Date": pd.to_datetime(["2026-09-01", frames[tk]]), "atr_pct": [0.02, 0.02]}))
    for name in ("_apply_percentile_ranking", "compute_sector_features", "compute_industry_features",
                 "add_market_regime_features", "_winsorize_features"):
        monkeypatch.setattr(dataset, name, lambda df: df)
    monkeypatch.setattr(dataset, "get_available_features", lambda: set())
    snap = dataset.build_latest_snapshot(verbose=False)
    assert "6173" not in set(snap["ticker"]) and len(snap) == 30
