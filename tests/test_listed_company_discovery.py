import json
from pathlib import Path

import pytest

from scripts.latest_bar_coverage import CoverageContractError
from scripts.listed_company_discovery import COMPANY_URLS, discover_companies, discover_and_seed_listed_companies
from scripts.stage_new_stock_sources import extract_rows, stage


def companies(market, ticker, listing="20260903", published="1150905"):
    keys = (["公司代號", "公司簡稱", "上市日期", "出表日期"] if market == "twse"
            else ["SecuritiesCompanyCode", "CompanyAbbreviation", "DateOfListing", "Date"])
    return [dict(zip(keys, [ticker, "fixture", listing, published]))]


def quotes(market, ticker, prices=(80, 81, 75, 75.5), volume=1000):
    fields = (["證券代號", "證券名稱", "開盤價", "最高價", "最低價", "收盤價", "成交股數"]
              if market == "twse" else ["代號", "名稱", "開盤", "最高", "最低", "收盤", "成交股數"])
    return {"stat": "ok", "date": "20260904", "tables": [
        {"fields": fields, "data": [[ticker, "fixture", *prices, volume]]}]}


def inputs():
    return dict(twse_payload=companies("twse", "2330"), tpex_payload=companies("tpex", "3718"),
                existing_tickers=[], target_date="2026-09-04", knowledge_date="2026-09-06",
                minimum_rows={"twse": 1, "tpex": 1})


@pytest.mark.parametrize("failure", ["empty", "wrong_schema", "old", "future", "duplicate", "cross_market"])
def test_company_contract_fails_closed(failure):
    args = inputs()
    if failure == "empty": args["tpex_payload"] = []
    if failure == "wrong_schema": args["tpex_payload"][0].pop("DateOfListing")
    if failure == "old": args["tpex_payload"][0]["Date"] = "1150902"  # older than prior trading day 09-03
    if failure == "future": args["tpex_payload"][0]["Date"] = "1150907"
    if failure == "duplicate": args["tpex_payload"] *= 2
    if failure == "cross_market": args["tpex_payload"][0]["SecuritiesCompanyCode"] = "2330"
    with pytest.raises(CoverageContractError):
        discover_companies(**args)


@pytest.mark.parametrize("published,accepted", [
    ("1150907", True),   # same-day roster (TPEx behaviour)
    ("1150906", True),   # Sunday report date: TWSE t187ap03_L lags one calendar day on Monday evening
    ("1150904", True),   # previous trading day (Friday) is the lower bound
    ("1150903", False),  # older than the previous trading day
    ("1150908", False),  # stamped after knowledge_date
])
def test_company_source_date_window_allows_exchange_report_lag(published, accepted):
    args = inputs()
    args.update(target_date="2026-09-07", knowledge_date="2026-09-07",
                twse_payload=companies("twse", "2330", published=published),
                tpex_payload=companies("tpex", "3718", published="1150907"))
    if accepted:
        assert discover_companies(**args)["company_count"] == 2
    else:
        with pytest.raises(CoverageContractError, match="company source date"):
            discover_companies(**args)


def test_retired_etf_future_listing_and_tdr_boundaries():
    args = inputs()
    args["twse_payload"] += companies("twse", "9103") + companies("twse", "9911", listing="20260907")
    result = discover_companies(**args, retired_tickers=["2330"], etf_tickers=["3718"])
    assert result["missing"] == []
    assert {r["reason"] for r in result["excluded"]} == {
        "retired_registry", "etf_registry", "listing_after_target", "tdr_separate_review"}


def run_seed(tmp_path, overrides=None):
    payloads = {COMPANY_URLS["twse"]: companies("twse", "2330"),
                COMPANY_URLS["tpex"]: companies("tpex", "3718"),
                "twse_tape": quotes("twse", "2330"), "tpex_tape": quotes("tpex", "3718")}
    payloads.update(overrides or {})
    def fetch(url):
        value = payloads[url] if url in payloads else payloads["tpex_tape" if "tpex.org.tw" in url else "twse_tape"]
        if isinstance(value, Exception):
            raise value
        return value
    return discover_and_seed_listed_companies(base_dir=tmp_path, target_date="2026-09-04",
        knowledge_date="2026-09-06", fetch_json=fetch,
        minimum_company_rows={"twse": 1, "tpex": 1}, minimum_tape_rows={"twse": 1, "tpex": 1})


def test_real_seed_never_overwrites_or_splices_old_code(tmp_path):
    daily = tmp_path / "日K資料"
    daily.mkdir()
    old = daily / "5371.csv"
    old.write_text("Date,Close\n2026-08-21,83.5\n", encoding="utf-8")
    before = old.read_bytes()
    result = run_seed(tmp_path)
    assert {r["ticker"] for r in result["seeded"]} == {"2330", "3718"}
    assert old.read_bytes() == before
    assert (daily / "3718.csv").read_text(encoding="utf-8-sig").splitlines() == [
        "Date,Open,High,Low,Close,Volume", "2026-09-04,80.0,81.0,75.0,75.5,1000"]
    assert run_seed(tmp_path)["seeded"] == []


@pytest.mark.parametrize("override", [
    {COMPANY_URLS["tpex"]: RuntimeError("official source failed")},
    {"tpex_tape": {"stat": "error"}},
    {"tpex_tape": quotes("tpex", "3718", prices=(80, 70, 75, 75.5))},
])
def test_any_source_failure_prevents_all_csv_seeding(tmp_path, override):
    with pytest.raises((RuntimeError, CoverageContractError)):
        run_seed(tmp_path, override)
    assert not list((tmp_path / "日K資料").glob("*.csv"))
    receipt = json.loads((tmp_path / "logs/listed_company_discovery_latest.json").read_text(encoding="utf-8"))
    assert receipt["status"] == "failed"
    assert receipt["seeded"] == []


@pytest.mark.parametrize("prices,volume", [(("--",) * 4, 0), (("--",) * 4, 218), ((80, 81, 75, 75.5), 0)])
def test_no_trade_or_no_ohlc_remains_pending_then_retries(tmp_path, prices, volume):
    result = run_seed(tmp_path, {"tpex_tape": quotes("tpex", "3718", prices, volume)})
    assert result["status"] == "pending"
    assert not (tmp_path / "日K資料/3718.csv").exists()
    assert result["pending"][0]["ticker"] == "3718"
    assert run_seed(tmp_path)["seeded"][0]["ticker"] == "3718"


def test_history_extraction_respects_listing_and_unadjusted_values():
    company = {"ticker": "3718", "listing_date_raw": "20260903"}
    tape = {"3718": {"state": "official_bar", "open": 80, "high": 80.5, "low": 75.2,
                     "close": 75.5, "volume": 4129000}}
    assert extract_rows(tape, [company], "2026-09-02") == ([], [])
    bars, absent = extract_rows(tape, [company], "2026-09-04")
    assert bars[0]["Close"] == 75.5
    assert bars[0]["Volume"] == 4129000
    assert absent == []


def test_history_stage_rejects_production_target(tmp_path):
    with pytest.raises(ValueError, match="named subdirectory"):
        stage(base_dir=tmp_path, output_dir=tmp_path / "日K資料",
              candidates_path=Path("never-read"), target_date="2026-09-04")
