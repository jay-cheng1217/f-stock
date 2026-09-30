import copy
import hashlib
import json
from datetime import date

import pandas as pd
import pytest

from scripts import fetch_disposition as fetch
from scripts.disposition_contract import (
    DispositionContractError, evaluate_disposition_snapshot, load_corrections,
    load_disposition_gate, resolve_disposition_periods, validate_periods,
)


def _period(stock_id="3008", start="2026-09-03", end="2026-09-09", source="TWSE"):
    return {"stock_id": stock_id, "period_start": start, "period_end": end,
            "source": source, "announcement_date": "2026-09-02"}


def _report(count=1):
    return {"status": "OK", "effective_date": "2026-09-05", "fetched_at": "2026-09-05T11:00:00+08:00",
            "disposition_coverage_end": "2026-09-07",
            "source_counts": {"OfficialDisposition": count}, "missing_sources": [],
            "source_status": {"TWSE": {"status": "OK", "rows": count},
                              "TPEx": {"status": "OK", "rows": 0}},
            "unsupported_sources": ["attention", "full_delivery", "suspended"]}


def _evaluate(periods, report=None, **kwargs):
    return evaluate_disposition_snapshot(pd.DataFrame(periods), report or _report(len(periods)),
        target_date="2026-09-07", required_as_of="2026-09-04", knowledge_as_of="2026-09-06", **kwargs)


def _twse_table():
    return {"stat": "OK", "fields": ["編號", "公布日期", "證券代號", "證券名稱", "累計", "處置條件", "處置起迄時間"],
            "data": [[1, "115/09/02", "3008", "test", 1, "test", "115/09/03～115/09/09"]],
            "count": 1, "total": 1}


def _correction_periods():
    return pd.DataFrame([{"stock_id": row["stock_id"], "period_start": row["period_start"],
                          "period_end": row["original_period_end"], "source": row["source"],
                          "announcement_date": row["announcement_date"]}
                         for row in load_corrections()["corrections"]])


def test_named_fields_survive_column_reordering_and_keep_publication():
    table = _twse_table()
    table["fields"] = list(reversed(table["fields"]))
    table["data"] = [list(reversed(table["data"][0]))]
    assert fetch._records_from_twse(table) == [_period()]


@pytest.mark.parametrize("mutation", [
    lambda table: table["fields"].__setitem__(2, "wrong field"),
    lambda table: table["data"][0].pop(),
    lambda table: table["data"][0].__setitem__(6, "broken date"),
    lambda table: table["data"][0].__setitem__(6, "115/09/09~115/09/03"),
    lambda table: table["data"][0].__setitem__(1, "115/09/10"),
    lambda table: table.__setitem__("total", 2),
])
def test_bad_schema_partial_or_invalid_rows_fail_loud(mutation):
    table = _twse_table()
    mutation(table)
    with pytest.raises((RuntimeError, DispositionContractError)):
        fetch._records_from_twse(table)


def test_twse_count_counts_securities_not_announcements():
    table = _twse_table()
    table["data"].append([2, "115/08/10", "3008", "test", 1, "test", "115/08/11~115/08/17"])
    table["total"] = 2
    assert len(fetch._records_from_twse(table)) == 2


def test_empty_requires_valid_schema_and_explicit_zero_count():
    table = _twse_table()
    table.update(data=[], count=0, total=0)
    assert fetch._records_from_twse(table) == []
    del table["count"]
    del table["total"]
    with pytest.raises(RuntimeError, match="explicit zero"):
        fetch._records_from_twse(table)
    with pytest.raises(DispositionContractError, match="missing columns"):
        validate_periods(pd.DataFrame())


def test_tpex_status_and_schema_are_both_required():
    table = _twse_table()
    table["fields"][-1] = "處置起訖時間"
    payload = {"stat": "ok", "tables": [table]}
    assert fetch._records_from_tpex(payload)[0]["source"] == "TPEx"
    payload["stat"] = "failed"
    with pytest.raises(RuntimeError):
        fetch._records_from_tpex(payload)


def test_echoed_query_range_must_match():
    fetch._verify_query_range({"title": "公布處置 (115/08/01 至 115/09/05)"}, "2026-08-01", "2026-09-05", "TWSE")
    # Live TWSE title (2026-09-07) appends a "資訊更新日期" line; it is not part of the range.
    fetch._verify_query_range(
        {"title": "公布處置有價證券資訊 (113/09/07 至 115/09/08)\n資訊更新日期：115/09/07"},
        "2024-09-07", "2026-09-08", "TWSE")
    with pytest.raises(RuntimeError, match="response date range"):
        fetch._verify_query_range({"title": "公布處置有價證券資訊\n資訊更新日期：115/09/07"}, "2024-09-07", "2026-09-08", "TWSE")
    with pytest.raises(RuntimeError, match="response date range"):
        fetch._verify_query_range({"title": "公布處置 (115/08/01 至 115/09/04)"}, "2026-08-01", "2026-09-05", "TWSE")
    with pytest.raises(RuntimeError, match="response date range"):
        fetch._verify_query_range({"date": "20260801~20260904"}, "2026-08-01", "2026-09-05", "TPEx")


def test_one_market_failure_retries_and_returns_no_partial_dataset(monkeypatch):
    calls = []
    monkeypatch.setattr(fetch, "_fetch_twse_periods", lambda *args: [_period()])
    def fail(*args):
        calls.append(args)
        raise RuntimeError("source offline")
    monkeypatch.setattr(fetch, "_fetch_tpex_periods", fail)
    periods, error, retries, sources = fetch.fetch_disposition_periods(
        start_date="2026-09-01", end_date="2026-09-05", max_retries=2, retry_delay_seconds=0)
    assert periods.empty and "TPEx" in error and len(calls) == 2
    assert retries == 2 and sources["TWSE"]["status"] == "OK"


def test_next_session_coverage_includes_announced_future_but_not_future_knowledge(monkeypatch):
    observed = []
    announced = _period("2455", "2026-09-07", "2026-09-15")
    announced["announcement_date"] = "2026-09-04"
    not_yet_known = _period("2330", "2026-09-08", "2026-09-14")
    not_yet_known["announcement_date"] = "2026-09-07"
    def twse(start, end, timeout):
        observed.append(end)
        return [announced, not_yet_known]
    monkeypatch.setattr(fetch, "_fetch_twse_periods", twse)
    monkeypatch.setattr(fetch, "_fetch_tpex_periods", lambda *args: [])
    periods, error, _, _ = fetch.fetch_disposition_periods(
        start_date="2026-09-01", end_date="2026-09-05", max_retries=1)
    assert observed == ["2026-09-07"] and error is None
    assert periods.stock_id.tolist() == ["2455"]


def _redirect_outputs(monkeypatch, tmp_path):
    periods = tmp_path / "ml/data/disposition_periods.csv"
    active = tmp_path / "disposition_active.csv"
    report = tmp_path / "ml/reports/special_stock_status_latest.json"
    periods.parent.mkdir(parents=True)
    report.parent.mkdir(parents=True)
    for attr, path in [("PERIODS_PATH", periods), ("OUTPUT_PATH", active), ("STATUS_REPORT_PATH", report)]:
        monkeypatch.setattr(fetch, attr, str(path))
    pd.DataFrame([_period()]).to_csv(periods, index=False)
    pd.DataFrame([_period()]).to_csv(active, index=False)
    report.write_text(json.dumps(_report()), encoding="utf-8")
    return periods, active, report


def test_fetch_failure_preserves_both_csvs_and_last_complete_date(monkeypatch, tmp_path):
    periods, active, _ = _redirect_outputs(monkeypatch, tmp_path)
    original = [periods.read_bytes(), active.read_bytes()]
    monkeypatch.setattr(fetch, "fetch_disposition_periods", lambda **kwargs: (
        pd.DataFrame([_period()]), "partial source failure", 1,
        {"TWSE": {"status": "OK", "rows": 1}, "TPEx": {"status": "FAILED"}}))
    report = fetch.update_disposition_status()
    assert [periods.read_bytes(), active.read_bytes()] == original
    assert report["status"] == "STALE" and report["missing_sources"] == ["TPEx"]
    assert report["last_successful_effective_date"] == "2026-09-05"


def test_partial_content_cannot_replace_complete_history(monkeypatch, tmp_path):
    periods, active, report_path = _redirect_outputs(monkeypatch, tmp_path)
    previous = _report()
    previous["source_status"]["TWSE"]["rows"] = 100
    report_path.write_text(json.dumps(previous), encoding="utf-8")
    original = periods.read_bytes()
    monkeypatch.setattr(fetch, "fetch_disposition_periods", lambda **kwargs: (
        pd.DataFrame([_period()]), None, 0, _report()["source_status"]))
    report = fetch.update_disposition_status()
    assert report["status"] == "STALE" and "partial-content" in report["error_message"]
    assert periods.read_bytes() == original


def test_all_15_official_corrections_cutoff_and_idempotence():
    raw = _correction_periods()
    before = resolve_disposition_periods(raw, knowledge_as_of="2026-08-07T17:59:59+08:00")
    after = resolve_disposition_periods(raw, knowledge_as_of="2026-08-07T18:00:00+08:00")
    assert len(after) == 15 and before["correction_id"].eq("").all()
    expected = {row["stock_id"]: row["corrected_period_end"] for row in load_corrections()["corrections"]}
    assert dict(zip(after.stock_id, after.period_end)) == expected
    assert raw.set_index("stock_id").period_end.equals(before.set_index("stock_id").period_end.reindex(raw.stock_id))
    pd.testing.assert_frame_equal(after, resolve_disposition_periods(after, knowledge_as_of="2026-08-10"))
    restored = resolve_disposition_periods(after, knowledge_as_of="2026-08-07T17:00:00+08:00")
    assert dict(zip(restored.stock_id, restored.period_end)) == dict(zip(raw.stock_id, raw.period_end))


def test_duplicate_correction_version_is_rejected_and_duplicate_period_is_collapsed():
    raw = _correction_periods()
    corrections = load_corrections()
    corrections["corrections"].append(copy.deepcopy(corrections["corrections"][0]))
    with pytest.raises(DispositionContractError, match="duplicate"):
        resolve_disposition_periods(raw, knowledge_as_of="2026-08-10", corrections=corrections)
    corrected = raw.iloc[[0]].copy()
    corrected["period_end"] = "2026-08-07"
    assert len(resolve_disposition_periods(pd.concat([raw, corrected]), knowledge_as_of="2026-08-10")) == 15


def test_gate_uses_target_period_not_any_future_period():
    result = _evaluate([_period(), _period("2330", "2026-09-08", "2026-09-14")])
    assert result["complete"] and result["blocked_tickers"] == ["3008"]
    assert result["scope"] == "disposition_only" and "attention" in result["unsupported_sources"]


@pytest.mark.parametrize("change", [
    {"status": "DEGRADED"}, {"status": "FAILED"}, {"source_status": {"TWSE": {"status": "OK"}}},
    {"effective_date": "2026-09-03"}, {"fetched_at": "2026-09-07T12:00:00+08:00"},
    {"source_counts": {"OfficialDisposition": 2}}, {"query_end_date": "2026-09-03"},
    {"disposition_coverage_end": None}, {"disposition_coverage_end": "2026-09-05"},
])
def test_gate_unknown_is_distinct_from_an_empty_veto_set(change):
    report = _report()
    report.update(change)
    result = _evaluate([_period()], report)
    assert result["status"] == "UNKNOWN" and not result["complete"] and result["reasons"]


def test_current_and_future_intervals_for_same_code_are_preserved():
    periods = pd.DataFrame([_period(), _period(start="2026-09-08", end="2026-09-14")])
    assert len(fetch._active_from_periods(periods, date(2026, 9, 5))) == 2


def test_generation_hash_prevents_mixed_metadata_and_csv(monkeypatch, tmp_path):
    periods, _, report_path = _redirect_outputs(monkeypatch, tmp_path)
    report = _report()
    report["output_sha256"] = {"disposition_periods": hashlib.sha256(periods.read_bytes()).hexdigest()}
    report_path.write_text(json.dumps(report), encoding="utf-8")
    kwargs = dict(target_date="2026-09-07", required_as_of="2026-09-04", knowledge_as_of="2026-09-06")
    assert load_disposition_gate(tmp_path, **kwargs)["complete"]
    periods.write_text(periods.read_text().replace("3008", "2330"), encoding="utf-8")
    result = load_disposition_gate(tmp_path, **kwargs)
    assert result["status"] == "UNKNOWN" and "generation" in result["reasons"][0]


def test_success_preserves_raw_history_and_writes_closed_state_before_generation(monkeypatch, tmp_path):
    periods, _, _ = _redirect_outputs(monkeypatch, tmp_path)
    raw = _correction_periods()
    sources = {"TWSE": {"status": "OK", "rows": 15}, "TPEx": {"status": "OK", "rows": 0}}
    monkeypatch.setattr(fetch, "fetch_disposition_periods", lambda **kwargs: (raw, None, 0, sources))
    states = []
    original_writer = fetch._atomic_write_json
    def capture(path, payload):
        states.append(payload["status"])
        original_writer(path, payload)
    monkeypatch.setattr(fetch, "_atomic_write_json", capture)
    report = fetch.update_disposition_status()
    assert states == ["UPDATING", "OK"]
    stored = pd.read_csv(periods, dtype=str)
    assert stored.loc[stored.stock_id == "8046", "period_end"].iloc[0] == "2026-08-18"
    assert report["output_sha256"]["disposition_periods"] == hashlib.sha256(periods.read_bytes()).hexdigest()


def test_current_release_radar_respects_corrected_release_date_and_stock_scope():
    from scripts.disposition_release_radar import recent_releases
    raw = _correction_periods()
    early = recent_releases(raw, knowledge_as_of="2026-08-07T17:00:00+08:00")
    after = recent_releases(raw, knowledge_as_of="2026-08-12T10:00:00+08:00")
    assert early.empty
    assert len(after) == 10 and after.stock_id.str.len().eq(4).all()
    assert after.loc[after.stock_id == "8046", "period_end"].iloc[0] == pd.Timestamp("2026-08-11")


def test_failed_partial_response_keeps_original_complete_count_for_next_retry(monkeypatch, tmp_path):
    _, _, report_path = _redirect_outputs(monkeypatch, tmp_path)
    previous = _report()
    previous["source_status"]["TWSE"]["rows"] = 100
    report_path.write_text(json.dumps(previous), encoding="utf-8")
    monkeypatch.setattr(fetch, "fetch_disposition_periods", lambda **kwargs: (
        pd.DataFrame([_period()]), None, 0, _report()["source_status"]))
    first = fetch.update_disposition_status()
    second = fetch.update_disposition_status()
    assert first["status"] == second["status"] == "STALE"
    assert second["last_complete_source_status"]["TWSE"]["rows"] == 100


def test_release_radar_does_not_label_a_second_active_disposition_as_released():
    from scripts.disposition_release_radar import recent_releases
    rows = [_period(end="2026-09-04"), _period(start="2026-09-07", end="2026-09-11")]
    assert recent_releases(pd.DataFrame(rows), knowledge_as_of="2026-09-08").empty


def test_roc_date_accepts_twse_same_day_asterisk_marker():
    assert fetch._roc_date_to_iso("*115/09/08") == "2026-09-08"
    assert fetch._roc_date_to_iso("115/09/08") == "2026-09-08"
    assert fetch._roc_date_to_iso("*") is None
