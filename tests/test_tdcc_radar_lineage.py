import ast
import json
from pathlib import Path

import pandas as pd
import pytest

from scripts import tdcc_whale_radar as radar
from scripts import entry_dashboard


def raw_frame(day, tickers=("2330", "2603"), *, whale=60, retail=1):
    return pd.DataFrame([
        {"資料日期": day, "證券代號": ticker, "持股分級": level, "人數": 100,
         "股數": 1000, "占集保庫存數比例%": whale if level == 15 else retail if level <= 9 else 0}
        for ticker in tickers for level in range(1, 16)
    ])


def write_raw(root, day, **kwargs):
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"tdcc_{day}.csv"
    raw_frame(day, **kwargs).to_csv(path, index=False, encoding="utf-8-sig")
    return path


def setup_pair(tmp_path, **kwargs):
    raw = tmp_path / "raw"
    previous = write_raw(raw, "20260828")
    current = write_raw(raw, "20260904", whale=64, retail=.8, **kwargs)
    sector = tmp_path / "sector.csv"
    pd.DataFrame({"Ticker": ["2330", "2603"], "Name": ["台積電", "長榮"],
                  "Sector": ["半導體", "航運"]}).to_csv(sector, index=False)
    return raw, previous, current, sector


def test_refresh_ignores_stale_rolling_snapshot_and_consumer_uses_verified_pair(tmp_path, monkeypatch):
    raw, _, _, sector = setup_pair(tmp_path)
    out = tmp_path / "output"
    (out / radar.SNAP).parent.mkdir(parents=True)
    pd.DataFrame({"ticker": ["2330"], "date": ["20260703"], "whale1000": [10], "retail100": [40]}).to_csv(out / radar.SNAP, index=False)
    report = radar.refresh(input_dir=raw, output_root=out, sector_path=sector)
    frame, loaded = radar.load_radar_artifact(out, not_after="2026-09-04")
    assert report["previous_date"] == "20260828" and loaded["as_of_date"] == "20260904"
    assert frame["whale_delta"].tolist() == [4, 4]
    assert frame["retail_delta"].tolist() == [-1.8, -1.8]
    monkeypatch.setattr(entry_dashboard, "BASE_DIR", out)
    per, rows, day = entry_dashboard.load_whale_deltas(not_after="2026-09-04")
    assert day == "2026-09-04" and len(rows) == 2
    assert per["2330"] == (64, 4, 7.2, -1.8)


def test_named_columns_and_numeric_levels_preserve_original_formula(tmp_path):
    path = write_raw(tmp_path, "20260904")
    frame = pd.read_csv(path)
    frame = frame.sort_values("持股分級", key=lambda s: s.astype(str))
    frame[list(reversed(frame.columns))].to_csv(path, index=False)
    result = radar.load_local_week(path)
    assert result["whale1000"].tolist() == [60, 60]
    assert result["retail100"].tolist() == [9, 9]


@pytest.mark.parametrize("mutation", ["missing_level", "duplicate", "mixed_date", "invalid_pct", "empty"])
def test_partial_or_invalid_raw_never_publishes_a_fresh_derivative(tmp_path, mutation):
    raw, _, current, sector = setup_pair(tmp_path)
    frame = pd.read_csv(current, dtype={"證券代號": str})
    if mutation == "missing_level":
        frame = frame.iloc[1:]
    elif mutation == "duplicate":
        frame = pd.concat([frame, frame.iloc[[0]]])
    elif mutation == "mixed_date":
        frame.loc[0, "資料日期"] = 20260828
    elif mutation == "invalid_pct":
        frame.loc[0, "占集保庫存數比例%"] = float("nan")
    else:
        frame = frame.iloc[:0]
    frame.to_csv(current, index=False)
    out = tmp_path / "out"
    with pytest.raises(radar.TDCCRadarDataError):
        radar.refresh(input_dir=raw, output_root=out, sector_path=sector)
    assert not (out / radar.DELTAS).exists()
    assert json.loads((out / radar.STATUS).read_text())["status"] == "FAILED"


def test_missing_week_refused_but_holiday_thursday_in_adjacent_week_valid(tmp_path):
    write_raw(tmp_path, "20260703")
    write_raw(tmp_path, "20260717")
    with pytest.raises(radar.TDCCRadarDataError, match="adjacent"):
        radar.select_week_pair(tmp_path)
    thursday = write_raw(tmp_path, "20260709")
    previous, current = radar.select_week_pair(tmp_path)
    assert previous == thursday and current.stem == "tdcc_20260717"
    radar.calculate_pair(previous, current)


def test_partial_market_coverage_is_not_a_legal_empty_radar(tmp_path):
    previous = write_raw(tmp_path, "20260828", tickers=("2330", "2603", "1001", "1002", "1003"))
    current = write_raw(tmp_path, "20260904", tickers=("2330",))
    with pytest.raises(radar.TDCCRadarDataError, match="coverage"):
        radar.calculate_pair(previous, current)


def test_valid_zero_hits_and_repeated_catchup_are_successful(tmp_path):
    raw, _, current, sector = setup_pair(tmp_path)
    write_raw(raw, "20260904", whale=61)
    out = tmp_path / "out"
    first = radar.refresh(input_dir=raw, output_root=out, sector_path=sector)
    second = radar.refresh(input_dir=raw, output_root=out, sector_path=sector)
    assert first["named_radar_rows"] == 0 and second["status"] == "OK"
    assert first["outputs"] == second["outputs"]
    assert len(radar.load_radar_artifact(out)[0]) == 2


@pytest.mark.parametrize("problem", ["raw_changed", "derived_changed", "new_week", "future", "manifest_date"])
def test_consumer_rejects_unverifiable_or_stale_or_future_lineage(tmp_path, monkeypatch, problem):
    raw, _, current, sector = setup_pair(tmp_path)
    out = tmp_path / "out"
    radar.refresh(input_dir=raw, output_root=out, sector_path=sector)
    asof = "2026-09-04"
    if problem == "raw_changed":
        current.write_text(current.read_text(encoding="utf-8-sig") + "\n", encoding="utf-8")
    elif problem == "derived_changed":
        (out / radar.DELTAS).write_text("broken", encoding="utf-8")
    elif problem == "new_week":
        write_raw(raw, "20260911")
        asof = "2026-09-11"
    elif problem == "manifest_date":
        manifest = json.loads((out / radar.STATUS).read_text(encoding="utf-8"))
        manifest["sources"]["current"]["date"] = "20260828"
        (out / radar.STATUS).write_text(json.dumps(manifest), encoding="utf-8")
    else:
        asof = "2026-08-28"
    with pytest.raises(radar.TDCCRadarDataError):
        radar.load_radar_artifact(out, not_after=asof)
    monkeypatch.setattr(entry_dashboard, "BASE_DIR", out)
    assert entry_dashboard.load_whale_deltas(not_after=asof) == ({}, [], None)


def test_phase1_and_phase2_both_schedule_independent_derived_catchup():
    tree = ast.parse(Path("scripts/smart_update_auto.py").read_text(encoding="utf-8"))
    for name in ("_run_phase1_tw_data", "_run_phase2_predict"):
        function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
        assert any(isinstance(n, ast.Name) and n.id == "exec_tdcc_whale_refresh" for n in ast.walk(function))


def test_fetch_heals_existing_partial_week_and_bad_response_preserves_good_file(tmp_path, monkeypatch):
    from scripts import fetch_tdcc_weekly as fetch
    write_raw(tmp_path, "20260828")
    current = write_raw(tmp_path, "20260904")
    pd.read_csv(current).iloc[:2].to_csv(current, index=False)
    monkeypatch.setattr(fetch, "TDCC_DIR", str(tmp_path))
    summaries = []
    monkeypatch.setattr(fetch, "rebuild_summary", lambda: summaries.append(True))
    payload = raw_frame("20260904").to_dict("records")
    class Response:
        def raise_for_status(self): pass
        def json(self): return payload
    monkeypatch.setattr(fetch.requests, "get", lambda *_a, **_kw: Response())
    assert fetch.fetch_and_save()
    assert len(pd.read_csv(current)) == 30 and summaries == [True]
    before = radar.sha(current)
    payload[0]["占集保庫存數比例%"] = None
    with pytest.raises(radar.TDCCRadarDataError, match="numeric"):
        fetch.fetch_and_save()
    assert radar.sha(current) == before and summaries == [True]

def test_fetch_stages_in_inherited_acl_dir_not_tempfile(tmp_path, monkeypatch):
    """tempfile.mkdtemp restricts the DACL on Windows; an elevated nightly run then
    publishes raw weeks that non-elevated sessions cannot read (2026-09-07)."""
    import tempfile
    from scripts import fetch_tdcc_weekly as fetch
    write_raw(tmp_path, "20260828")
    monkeypatch.setattr(fetch, "TDCC_DIR", str(tmp_path))
    monkeypatch.setattr(fetch, "rebuild_summary", lambda: None)
    for name in ("mkdtemp", "TemporaryDirectory"):
        monkeypatch.setattr(tempfile, name, lambda *a, **k: pytest.fail(f"tempfile.{name} used for staging"))
    payload = raw_frame("20260904").to_dict("records")
    class Response:
        def raise_for_status(self): pass
        def json(self): return payload
    monkeypatch.setattr(fetch.requests, "get", lambda *_a, **_kw: Response())
    reset_calls = []
    monkeypatch.setattr(fetch, "_restore_inherited_acl", lambda path: reset_calls.append(Path(path).name))
    assert fetch.fetch_and_save()
    assert (tmp_path / "tdcc_20260904.csv").exists()
    assert not [p for p in tmp_path.iterdir() if p.name.startswith(".tdcc_validate_")]
    assert reset_calls == ["tdcc_20260904.csv"]


def test_acl_reset_failure_never_blocks_fetch(tmp_path, monkeypatch):
    from scripts import fetch_tdcc_weekly as fetch
    import subprocess
    monkeypatch.setattr(fetch.os, "name", "nt", raising=False)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(PermissionError("denied")))
    fetch._restore_inherited_acl(tmp_path / "x.csv")  # must not raise
