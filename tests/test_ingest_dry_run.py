# -*- coding: utf-8 -*-
"""匯入預演:正式匯入程式 + 記憶體資料庫;要抓到「下一次排程匯入會失敗」的資料狀態。"""
import json

import pytest

from backend.db import ingest
from scripts import ingest_dry_run as dr
from scripts import preflight_check as pf


@pytest.fixture
def data_dirs(tmp_path, monkeypatch):
    d = {k: tmp_path / k for k in ("日K資料", "月營收", "季報財務", "集保分散", "大盤指數")}
    for p in d.values():
        p.mkdir()
    (d["日K資料"] / "2330.csv").write_text(
        "Date,Open,High,Low,Close,Volume\n2026-10-15,1,1,1,1000,100\n2026-10-16,1,1,1,1010,100\n", encoding="utf-8")
    (d["月營收"] / "revenue_2330.csv").write_text(
        "﻿Date,Name,Monthly_Revenue,Cumulative_Revenue,YoY_pct_change,Cumulative_YoY_pct_change\n"
        "2026-08,台積電,100.0,800.0,1.0,1.0\n2026-09,台積電,110.0,910.0,2.0,1.5\n", encoding="utf-8")
    (d["季報財務"] / "financial_2026Q2.csv").write_text(
        "Ticker,Name,Year,Season,Operating_Margin_Pct\n2330,台積電,2026,2,45.0\n", encoding="utf-8")
    (d["集保分散"] / "tdcc_summary.csv").write_text(
        "Date,Ticker,Retail_Pct,Whale_Pct,Total_Holders\n20261016,2330,10.0,85.0,1000\n", encoding="utf-8")
    (d["大盤指數"] / "index_TWII.csv").write_text(
        "Date,Open,High,Low,Close,Volume\n2026-10-16,1,1,1,23000,1\n", encoding="utf-8")
    (tmp_path / "ml" / "data").mkdir(parents=True)
    (tmp_path / "ml" / "data" / "sector_mapping.csv").write_text("Ticker,Name\n2330,台積電\n", encoding="utf-8")
    for attr, key in (("DAILY_K_DIR", "日K資料"), ("REVENUE_DIR", "月營收"), ("FINANCIAL_DIR", "季報財務"),
                      ("TDCC_DIR", "集保分散"), ("INDEX_DIR", "大盤指數")):
        monkeypatch.setattr(ingest, attr, str(d[key]))
    monkeypatch.setattr(ingest, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(ingest, "load_retired_tickers", lambda: set())
    return d


def _by_table(results):
    return {r["table"]: r for r in results}


def test_clean_data_passes_every_table_and_restores_the_connection(data_dirs):
    original = ingest.get_conn
    res = _by_table(dr.dry_run({}))
    assert ingest.get_conn is original                                   # 正式連線函式一定還原
    assert set(res) == {"daily_k", "revenue", "financials", "tdcc", "indices", "stock_list"}
    assert all(r["ok"] for r in res.values())
    assert res["revenue"]["rows"] == 2 and res["revenue"]["freshness"] == "2026-09"
    assert res["financials"]["freshness"] == "20262" and res["revenue"]["dotted_ticker_rows"] == 0


def test_row_shrink_below_70_percent_of_last_ingest_fails_like_production(data_dirs):
    res = _by_table(dr.dry_run({"revenue": (1000, None)}))
    assert not res["revenue"]["ok"] and "70%" in res["revenue"]["error"]
    assert res["daily_k"]["ok"]                                          # 其他表不受影響


def test_freshness_regression_fails(data_dirs):
    res = _by_table(dr.dry_run({"daily_k": (2, "2026-10-20")}))
    assert not res["daily_k"]["ok"] and "regressed" in res["daily_k"]["error"]
    assert "stock_list" not in res                                       # 日K沒過就不建股票清單


def test_float_ticker_column_is_caught_as_dotted_code(data_dirs):
    (data_dirs["季報財務"] / "financial_2026Q2.csv").write_text(
        "Ticker,Name,Year,Season,Operating_Margin_Pct\n2330,台積電,2026,2,45.0\n1441.0,大東,2026,2,3.0\n",
        encoding="utf-8")
    res = _by_table(dr.dry_run({}))
    assert not res["financials"]["ok"] and "小數點" in res["financials"]["error"]


def test_baseline_falls_back_to_the_latest_pipeline_log(tmp_path):
    (tmp_path / "smart_update_auto_stdio_20261016.log").write_bytes(
        "[ingest] \xe5\xae\x8c\xe6\x88\x90! 172.9s ".encode("latin-1", "replace")
        + b"{'daily_k': 3038103, 'revenue': 65882, 'financials': 47336, 'tdcc': 931067, 'indices': 10192}\n")
    base, source = dr.baseline_from_logs(tmp_path)
    assert source == "log:smart_update_auto_stdio_20261016.log"
    assert base["revenue"] == (65882, None) and base["daily_k"][0] == 3038103


def test_main_writes_the_report_preflight_reads(data_dirs, tmp_path, monkeypatch):
    monkeypatch.setattr(dr, "production_baseline", lambda db, logs: ({}, "test"))
    out = tmp_path / "report.json"
    assert dr.main(report_path=out) == 0
    rep = json.loads(out.read_text(encoding="utf-8"))
    assert rep["verdict"] == "PASS" and rep["finished_ts"] > 0 and rep["baseline_source"] == "test"


def test_preflight_flags_data_changed_after_last_ingest_and_last_dry_run():
    newest = {"月營收": 1_000_000.0, "日K資料": 500.0}
    assert pf.pending_ingest(newest, db_mtime=900.0, dry_run=None) == ["月營收"]
    assert pf.pending_ingest(newest, db_mtime=900.0, dry_run={"verdict": "PASS", "finished_ts": 1_000_100.0}) == []
    # 預演 FAIL 不算數;預演之後又改資料也要再提醒
    assert pf.pending_ingest(newest, db_mtime=900.0, dry_run={"verdict": "FAIL", "finished_ts": 2e6}) == ["月營收"]
    assert pf.pending_ingest({"月營收": 3e6}, db_mtime=900.0, dry_run={"verdict": "PASS", "finished_ts": 2e6}) == ["月營收"]
