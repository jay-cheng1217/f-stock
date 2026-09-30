from copy import deepcopy

import pytest

from scripts.latest_bar_coverage import CoverageContractError, build_latest_bar_coverage, load_local_latest_dates


def tape(market, ticker, prices=(10, 11, 9, 10), volume=1000):
    fields = (["證券代號", "證券名稱", "開盤價", "最高價", "最低價", "收盤價", "成交股數"]
              if market == "twse" else ["代號", "名稱", "開盤 ", "最高 ", "最低", "收盤 ", "成交股數  "])
    return {"stat": "ok", "date": "20260904", "tables": [
        {"fields": fields, "data": [[ticker, "fixture", *prices, volume]]}]}


def event(**overrides):
    return {"id": "6129-capital", "ticker": "6129", "status": "suspended",
            "effective_from": "2026-09-03", "effective_until": "2026-09-14",
            "available_on": "2026-08-20", "source_url": "https://www.tpex.org.tw/notice",
            "source_sha256": "a" * 64, **overrides}


def run(rows, **kwargs):
    args = dict(target_date="2026-09-04", knowledge_date="2026-09-06",
                twse_payload=tape("twse", "2330"), tpex_payload=tape("tpex", "6488"),
                layer_dates=rows, event_registry={"schema_version": 1, "events": []},
                minimum_rows={"twse": 1, "tpex": 1})
    args.update(kwargs)
    return build_latest_bar_coverage(**args)


def test_real_source_gap_is_not_silently_exempted():
    result = run([{"ticker": "2330", "csv_date": "2026-09-03"}])
    assert result["status"] == "fail"
    assert result["counts"] == {"source_gap": 1}


@pytest.mark.parametrize("volume,classification", [(0, "official_no_ohlc_zero_volume"),
    (218, "official_no_ohlc_reported_volume")])
def test_missing_ohlc_is_preserved_without_inventing_price(volume, classification):
    result = run([{"ticker": "2330", "csv_date": "2026-09-03", "db_date": "2026-09-03"}],
                 twse_payload=tape("twse", "2330", ("--",) * 4, volume))
    row = result["rows"][0]
    assert result["status"] == "ok"
    assert row["classification"] == classification
    assert row["source"]["close"] is None
    assert row["csv_date"] == "2026-09-03"


def test_no_ohlc_does_not_hide_existing_db_lag_or_fake_csv_bar():
    result = run([{"ticker": "2330", "csv_date": "2026-09-04", "db_date": "2026-09-02"}],
                 twse_payload=tape("twse", "2330", ("--",) * 4, 0))
    assert result["status"] == "fail"
    assert result["rows"][0]["layer_issues"] == ["csv_bar_without_valid_official_ohlc", "db_lag"]


def test_downstream_lags_and_ml_absence_are_distinct():
    result = run([{"ticker": "2330", "csv_date": "2026-09-04", "db_date": "2026-09-03",
                   "ml_date": "2026-09-02"}, {"ticker": "6488", "csv_date": "2026-09-04",
                   "ml_date": None, "ml_expected": True}])
    assert result["rows"][0]["layer_issues"] == ["db_lag", "ml_lag"]
    assert result["rows"][1]["layer_issues"] == ["ml_missing_expected_row"]


def test_suspension_keeps_old_ml_date_visible():
    result = run([{"ticker": "6129", "csv_date": "2026-09-02", "ml_date": "2026-09-02"}],
                 event_registry={"schema_version": 1, "events": [event()]})
    assert result["rows"][0]["classification"] == "official_suspended"
    assert result["rows"][0]["ml_row_current"] is False
    assert result["rows"][0]["ml_date"] == "2026-09-02"


@pytest.mark.parametrize("overrides,expected", [
    ({"available_on": "2026-09-07"}, "unknown_official_absence"),
    ({"effective_from": "2026-09-05"}, "unknown_official_absence"),
    ({"effective_until": "2026-09-04"}, "unknown_official_absence"),
    ({"effective_from": "2026-09-04"}, "official_suspended"),
])
def test_event_publication_and_effective_boundaries(overrides, expected):
    result = run([{"ticker": "6129", "csv_date": "2026-09-02"}],
                 event_registry={"schema_version": 1, "events": [event(**overrides)]})
    assert result["rows"][0]["classification"] == expected


@pytest.mark.parametrize("failure", ["failed", "date", "schema", "partial", "duplicate", "width"])
def test_source_contract_fails_closed(failure):
    payload = tape("twse", "2330")
    if failure == "failed": payload["stat"] = "error"
    if failure == "date": payload["date"] = "20260903"
    if failure == "schema": payload["tables"][0]["fields"][2] = "unexpected"
    if failure == "partial": payload["tables"][0]["data"] = []
    if failure == "duplicate": payload["tables"][0]["data"] *= 2
    if failure == "width": payload["tables"][0]["data"][0].pop()
    with pytest.raises(CoverageContractError):
        run([], twse_payload=payload)


def test_missing_market_cross_market_duplicate_and_event_conflict():
    with pytest.raises(CoverageContractError): run([], tpex_payload=None)
    with pytest.raises(CoverageContractError): run([], tpex_payload=tape("tpex", "2330"))
    result = run([{"ticker": "2330", "csv_date": "2026-09-04"}],
                 event_registry={"schema_version": 1, "events": [event(ticker="2330")]})
    assert result["status"] == "fail"
    assert result["counts"] == {"official_event_conflict": 1}


@pytest.mark.parametrize("mutation", ["duplicate", "overlap", "reverse", "bad_hash"])
def test_event_contract_rejects_conflicting_revisions(mutation):
    events = [event()]
    if mutation == "duplicate": events.append(deepcopy(events[0]))
    if mutation == "overlap": events.append(event(id="second", effective_from="2026-09-04"))
    if mutation == "reverse": events[0]["effective_until"] = "2026-09-02"
    if mutation == "bad_hash": events[0]["source_sha256"] = "missing"
    with pytest.raises(CoverageContractError):
        run([], event_registry={"schema_version": 1, "events": events})


def test_absent_symbol_is_unknown_not_automatically_retired():
    result = run([{"ticker": "9999", "csv_date": "2026-09-02"}])
    assert result["status"] == "warn"
    assert result["unknown_tickers"] == ["9999"]


def test_reordered_official_fields_are_resolved_by_name():
    payload = tape("twse", "2330")
    payload["tables"][0]["fields"].reverse()
    payload["tables"][0]["data"][0].reverse()
    result = run([{"ticker": "2330", "csv_date": "2026-09-04"}], twse_payload=payload)
    assert result["rows"][0]["source"]["close"] == 10


def test_local_max_date_cache_invalidates_on_file_change(tmp_path):
    daily = tmp_path / "daily"
    daily.mkdir()
    path = daily / "2330.csv"
    path.write_text("Date,Close\n2026-09-04,10\n2026-09-02,9\n", encoding="utf-8")
    assert load_local_latest_dates(daily) == [{"ticker": "2330", "csv_date": "2026-09-04"}]
    path.write_text("Date,Close\n2026-09-07,11\n", encoding="utf-8")
    assert load_local_latest_dates(daily) == [{"ticker": "2330", "csv_date": "2026-09-07"}]


def test_local_dates_reject_empty_csv_and_respect_retired_effective_date(tmp_path):
    daily = tmp_path / "daily"
    daily.mkdir()
    (daily / "2330.csv").write_text("Date,Close\n2026-09-04,10\n", encoding="utf-8")
    (daily / "5371.csv").write_text("Date,Close\n2026-08-21,80\n", encoding="utf-8")
    registry = tmp_path / "retired.csv"
    registry.write_text("ticker,retired_as_of\n5371,2026-09-03\n", encoding="utf-8")
    assert len(load_local_latest_dates(daily, retired_path=registry, as_of="2026-09-02")) == 2
    assert len(load_local_latest_dates(daily, retired_path=registry, as_of="2026-09-04")) == 1
    (daily / "2330.csv").write_text("Date,Close\n", encoding="utf-8")
    with pytest.raises(CoverageContractError):
        load_local_latest_dates(daily)
