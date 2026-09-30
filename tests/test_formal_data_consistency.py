import json
from pathlib import Path
from types import SimpleNamespace

import duckdb
import pandas as pd
import pytest

from scripts import audit_formal_data_consistency as audit


def frame():
    return pd.DataFrame({"Ticker": ["2330", "5371"], "Date": ["2026-09-04", "2026-09-02"],
                         "Open": [100.0, 40.0], "High": [102.0, 42.0], "Low": [99.0, 39.0],
                         "Close": [101.0, 41.0], "Volume": [1000000, 500000]})


def test_exact_comparison_preserves_own_older_date_and_detects_one_ulp():
    source = frame()
    assert audit.compare_exact(source, source.copy(), audit.OHLCV)["ok"]
    import numpy as np
    changed = source.copy()
    changed.loc[0, "Close"] = np.nextafter(101.0, float("inf"))
    result = audit.compare_exact(source, changed, audit.OHLCV)
    assert not result["ok"] and result["mismatch_count"] == 1
    changed = source.copy()
    changed.loc[1, "Date"] = "2026-09-04"
    assert not audit.compare_exact(source, changed, audit.OHLCV)["ok"]


def test_duplicate_or_conflicting_prediction_date_is_not_silently_normalized():
    source = frame()
    with pytest.raises(ValueError, match="Duplicate"):
        audit.identity_frame(pd.concat([source, source.iloc[:1]]))
    source["date"] = "2026-09-04"
    with pytest.raises(ValueError, match="disagree"):
        audit.identity_frame(source)


def test_dataa_source_date_is_legal_and_conflicts_fail():
    source = frame()
    dataa = source[["Ticker", "Date"]].rename(columns={"Ticker": "ticker", "Date": "source_date"})
    assert audit.compare_exact(source, dataa, [])["ok"]
    dataa["date"] = dataa.source_date
    assert audit.compare_exact(source, dataa, [])["ok"]
    dataa.loc[1, "date"] = "2026-09-04"
    with pytest.raises(ValueError, match="disagree"):
        audit.identity_frame(dataa)


def test_csv_scan_reads_counts_retirement_and_own_date_keys(tmp_path):
    daily = tmp_path / "日K資料"
    daily.mkdir()
    config = tmp_path / "config"
    config.mkdir()
    (config / "retired_tickers.csv").write_text("ticker\n9999\n", encoding="utf-8")
    for row in frame().to_dict("records"):
        ticker = row.pop("Ticker")
        pd.DataFrame([row]).to_csv(daily / f"{ticker}.csv", index=False)
    (daily / "9999.csv").write_text("invalid retired file", encoding="utf-8")
    result = audit.scan_daily_sources(tmp_path, "2026-09-04", frame())
    assert result["source_files"] == 2 and result["source_rows"] == 2
    assert result["duplicate_rows"] == 0 and not result["errors"]
    assert result["latest"].set_index("Ticker").loc["5371", "Date"] == "2026-09-02"
    assert audit.compare_exact(frame(), result["snapshot_keys"], audit.OHLCV)["ok"]


def create_database(path, *, mismatch=False):
    conn = duckdb.connect(str(path))
    daily = frame()
    conn.register("fixture_daily", daily)
    conn.execute("CREATE TABLE daily_k AS SELECT * FROM fixture_daily")
    conn.execute("CREATE TABLE stock_list AS SELECT Ticker, Date AS Last_Date FROM daily_k")
    conn.execute("CREATE TABLE revenue AS SELECT '2330' Ticker, '2026-07' Date")
    conn.execute('CREATE TABLE financials AS SELECT \'2330\' AS Ticker, 2026 AS "Year", 2 AS Season')
    conn.execute("CREATE TABLE tdcc AS SELECT '2330' Ticker, '20260904' Date")
    conn.execute("CREATE TABLE indices AS SELECT 'TWII' Index_Name, '2026-09-04' Date")
    conn.execute("CREATE TABLE ingest_meta(table_name VARCHAR, rows BIGINT, generation_id VARCHAR)")
    for table in ("daily_k", "revenue", "financials", "tdcc", "indices"):
        count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        conn.execute("INSERT INTO ingest_meta VALUES (?,?,?)", [table, count, "g1"])
    if mismatch:
        conn.execute("UPDATE daily_k SET Close=999 WHERE Ticker='2330'")
        conn.execute("UPDATE ingest_meta SET generation_id='g0' WHERE table_name='tdcc'")
    conn.close()


@pytest.mark.parametrize("mismatch", [False, True])
def test_actual_read_only_duckdb_generation_and_prices(tmp_path, mismatch):
    path = tmp_path / "stock.duckdb"
    create_database(path, mismatch=mismatch)
    before = audit.file_receipt(path)
    conn = duckdb.connect(str(path), read_only=True)
    try:
        result = audit.audit_database(conn, {"latest": frame(), "source_rows": 2})
    finally:
        conn.close()
    assert all(result["checks"].values()) is (not mismatch)
    assert audit.file_receipt(path)["sha256"] == before["sha256"]


def test_html_must_contain_each_dated_candidate_in_payload(tmp_path, monkeypatch):
    row = {"stock": "2330 台積電", "priority": "1", "source_date": "2026-09-04",
           "model_source_date": "2026-09-04", "data_status": "OK"}
    plan = {"trade_date": "2026-09-07", "as_of_date": "2026-09-04", "model_source_date": "2026-09-04",
            "model_source": "dataA_predictions_2026-09-04.csv", "rows": [row]}
    entry = tmp_path / "entry.json"
    entry.write_text(json.dumps(plan), encoding="utf-8")
    Path(str(entry) + ".manifest.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr("scripts.entry_artifact_lineage.load_entry_artifact", lambda path: json.loads(path.read_text(encoding="utf-8")))
    html = tmp_path / "dashboard.html"
    payload = {"meta": {"trade_date": "2026-09-07"}, "rows": [row]}
    html.write_text('<script id="data" type="application/json">' + json.dumps(payload) + '</script>', encoding="utf-8")
    result = audit.audit_entry_artifacts(entry, html, {"latest": frame()}, "2026-09-04", "2026-09-07")
    assert all(result["checks"].values())
    payload["rows"] = []
    html.write_text('2330<script id="data" type="application/json">' + json.dumps(payload) + '</script>', encoding="utf-8")
    result = audit.audit_entry_artifacts(entry, html, {"latest": frame()}, "2026-09-04", "2026-09-07")
    assert not result["checks"]["html_every_candidate_exact_identity"]


def test_cli_failure_reports_fail_and_returns_nonzero_without_database(tmp_path, monkeypatch):
    monkeypatch.delenv("STOCK_BASE_DIR", raising=False)
    monkeypatch.setattr("sys.argv", ["audit", "--root", str(tmp_path), "--stage", "db",
        "--as-of", "2026-09-04", "--trade-date", "2026-09-07", "--output", str(tmp_path / "output/test.json")])
    assert audit.main() == 1
    report = json.loads((tmp_path / "output/test.json").read_text(encoding="utf-8"))
    assert report["status"] == "FAIL" and report["checks"]["audit_completed"] is False
    assert not (tmp_path / "stock.duckdb").exists()
