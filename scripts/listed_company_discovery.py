"""Discover missing listed-company CSVs and seed real official current bars.

Seeding never joins predecessor codes or invents pre-listing/zero-volume bars.
History requirements and model eligibility remain owned by the ML pipeline.
"""
from __future__ import annotations

import csv
from datetime import date, datetime
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import ssl
import tempfile
import time

from scripts.latest_bar_coverage import CoverageContractError, parse_official_tape
from scripts.taiwan_trading_calendar import previous_taiwan_trading_day

COMPANY_URLS = {
    "twse": "https://openapi.twse.com.tw/v1/opendata/t187ap03_L",
    "tpex": "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O",
}


def _company_day(value):
    text = str(value).strip()
    if re.fullmatch(r"\d{7}", text):
        return date(int(text[:3]) + 1911, int(text[3:5]), int(text[5:])).isoformat()
    if re.fullmatch(r"\d{8}", text):
        return datetime.strptime(text, "%Y%m%d").date().isoformat()
    raise CoverageContractError(f"Invalid official company date {text!r}")


def company_source_window(target_date, knowledge_date):
    """Accepted roster report-date window for one daily run.

    The exchanges regenerate the company rosters on their own clock: TPEx
    stamps the current calendar day, while TWSE ``t187ap03_L`` still carries
    the previous calendar day during the evening Phase-1 run (observed
    2026-09-07 21:06: TWSE=1150906, TPEx=1150907). A roster no older than the
    trading day before ``target_date`` cannot lose a listing that matters for
    ``target_date`` (a company listing on the target day is discovered on the
    next run through the pending/retry path), so that is the lower bound.
    Anything older, or stamped after ``knowledge_date``, still fails closed.
    """
    earliest = previous_taiwan_trading_day(date.fromisoformat(target_date)).isoformat()
    return earliest, knowledge_date


def parse_company_list(payload, market, *, target_date, knowledge_date, minimum_rows=None):
    names = {"twse": ("公司代號", "公司簡稱", "上市日期", "出表日期"),
             "tpex": ("SecuritiesCompanyCode", "CompanyAbbreviation", "DateOfListing", "Date")}
    if market not in names or not isinstance(payload, list):
        raise CoverageContractError(f"{market}: company source is not a recognized list")
    earliest, latest = company_source_window(target_date, knowledge_date)
    minimum = minimum_rows if minimum_rows is not None else {"twse": 900, "tpex": 700}[market]
    if len(payload) < minimum:
        raise CoverageContractError(f"{market}: partial company list {len(payload)} < {minimum}")
    code, name, listing, source_day = names[market]
    result = {}
    source_dates = set()
    for raw in payload:
        if not isinstance(raw, dict) or not set(names[market]) <= raw.keys():
            raise CoverageContractError(f"{market}: company fields changed")
        ticker = str(raw[code]).strip()
        if ticker in result:
            raise CoverageContractError(f"{market}: duplicate company ticker {ticker}")
        published, listed = _company_day(raw[source_day]), _company_day(raw[listing])
        if not earliest <= published <= latest:
            raise CoverageContractError(
                f"{market}: company source date {published} outside {earliest}..{latest} "
                f"(target {target_date}, knowledge {knowledge_date})")
        source_dates.add(published)
        result[ticker] = {"ticker": ticker, "name": str(raw[name]).strip(), "market": market,
                          "listing_date": listed, "source_date": published}
    if len(source_dates) != 1:
        raise CoverageContractError(f"{market}: mixed company source generations")
    return result


def discover_companies(*, twse_payload, tpex_payload, existing_tickers, target_date,
                       knowledge_date, retired_tickers=(), etf_tickers=(), minimum_rows=None):
    all_companies = {}
    for market, payload in [("twse", twse_payload), ("tpex", tpex_payload)]:
        parsed = parse_company_list(payload, market, target_date=target_date, knowledge_date=knowledge_date,
                                    minimum_rows=(minimum_rows or {}).get(market))
        if set(parsed) & set(all_companies):
            raise CoverageContractError("Company ticker appears in both markets")
        all_companies.update(parsed)
    existing, retired, etfs = set(existing_tickers), set(retired_tickers), set(etf_tickers)
    missing, excluded = [], []
    for ticker, row in sorted(all_companies.items()):
        reason = ("not_four_digit_common_stock" if not re.fullmatch(r"[1-9]\d{3}", ticker)
                  else "tdr_separate_review" if ticker.startswith("91")
                  else "retired_registry" if ticker in retired
                  else "etf_registry" if ticker in etfs
                  else "listing_after_target" if row["listing_date"] > target_date else None)
        if reason:
            excluded.append({**row, "reason": reason})
        elif ticker not in existing:
            missing.append(row)
    return {"target_date": target_date, "knowledge_date": knowledge_date,
            "company_count": len(all_companies), "missing": missing, "excluded": excluded,
            "companies": all_companies}


def _fetch_json(url):
    import requests
    from requests.adapters import HTTPAdapter

    class CompatibleCAAdapter(HTTPAdapter):
        def init_poolmanager(self, *args, **kwargs):
            context = ssl.create_default_context()
            # Python 3.14 enables strict X.509 extension checks by default.
            # Taiwan exchange CA chains need legacy-extension compatibility;
            # CA-chain validation and hostname verification remain enabled.
            context.verify_flags &= ~ssl.VERIFY_X509_STRICT
            kwargs["ssl_context"] = context
            return super().init_poolmanager(*args, **kwargs)

    last_error = None
    with requests.Session() as session:
        session.mount("https://", CompatibleCAAdapter())
        for attempt in range(3):
            try:
                response = session.get(url, timeout=35)
                response.raise_for_status()
                return response.json()
            except (requests.RequestException, ValueError) as exc:
                last_error = exc
                if attempt < 2:
                    time.sleep(attempt + 1)
    raise CoverageContractError(f"Company/tape source failed: {url}") from last_error


def _atomic_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as stream:
        tmp = Path(stream.name)
        json.dump(payload, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    try:
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _seed_csv(path, row, target_date):
    """Install one complete CSV atomically and never overwrite a raced file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8-sig", newline="", dir=path.parent,
                                     prefix=".new-company-", suffix=".tmp", delete=False) as stream:
        tmp = Path(stream.name)
        writer = csv.writer(stream)
        writer.writerow(["Date", "Open", "High", "Low", "Close", "Volume"])
        writer.writerow([target_date, row["open"], row["high"], row["low"], row["close"], int(row["volume"])])
    try:
        os.link(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def discover_and_seed_listed_companies(*, base_dir, target_date, knowledge_date,
                                     retired_tickers=(), etf_tickers=(), fetch_json=None,
                                     minimum_company_rows=None, minimum_tape_rows=None):
    """Daily update entry point; all source contracts precede the first CSV write.

    Existing history is untouched. Missing/no-OHLC companies stay pending in the
    receipt and are retried next run. A pending seed is not a current price.
    """
    from scripts.official_daily_prices import TWSE_HISTORY_URL, TPEX_HISTORY_URL

    root, fetch = Path(base_dir), fetch_json or _fetch_json
    target = date.fromisoformat(target_date)
    daily, logs = root / "日K資料", root / "logs"
    receipt_path = logs / "listed_company_discovery_latest.json"
    report = {"target_date": target_date, "knowledge_date": knowledge_date,
              "status": "failed", "seeded": [], "pending": [], "sources": {}}
    try:
        company_payloads = {market: fetch(url) for market, url in COMPANY_URLS.items()}
        discovery = discover_companies(
            twse_payload=company_payloads["twse"], tpex_payload=company_payloads["tpex"],
            existing_tickers=[p.stem for p in daily.glob("*.csv")], target_date=target_date,
            knowledge_date=knowledge_date, retired_tickers=retired_tickers, etf_tickers=etf_tickers,
            minimum_rows=minimum_company_rows)
        report.update({k: v for k, v in discovery.items() if k != "companies"})
        for market, payload in company_payloads.items():
            encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
            source_dates = sorted({row["source_date"] for row in discovery["companies"].values()
                                   if row["market"] == market})
            report["sources"][market + "_companies"] = {"url": COMPANY_URLS[market],
                "sha256": hashlib.sha256(encoded).hexdigest(), "rows": len(payload),
                "source_date": source_dates[0] if source_dates else None}
        tape = {}
        raw_tapes = {}
        if discovery["missing"]:
            for market, url in [
                ("twse", TWSE_HISTORY_URL.format(date8=target.strftime("%Y%m%d"))),
                ("tpex", TPEX_HISTORY_URL.format(roc_date=f"{target.year - 1911:03d}/{target.month:02d}/{target.day:02d}")),
            ]:
                cache = logs / "official_daily_history_cache" / market / f"{target:%Y%m%d}.json.gz"
                payload = json.loads(gzip.decompress(cache.read_bytes())) if cache.exists() else fetch(url)
                parsed = parse_official_tape(payload, market, target_date,
                                            minimum_rows=(minimum_tape_rows or {}).get(market))
                if set(tape) & set(parsed):
                    raise CoverageContractError("Seed tape ticker appears in both markets")
                tape.update(parsed)
                raw_tapes[market] = payload
                report["sources"][market + "_tape"] = {"url": url, "date": target_date,
                    "sha256": hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()}
            for company in discovery["missing"]:
                row = tape.get(company["ticker"])
                if row and row["state"] == "official_invalid_ohlc":
                    raise CoverageContractError(f"Invalid seed OHLC: {company['ticker']}")
                if row and row["market"] != company["market"]:
                    raise CoverageContractError(f"Seed company/tape market mismatch: {company['ticker']}")
            # Both company lists and both daily tapes are valid before mutation.
            for company in discovery["missing"]:
                ticker = company["ticker"]
                row, path = tape.get(ticker), daily / (ticker + ".csv")
                if row is None or row["state"] != "official_bar" or row["volume"] == 0:
                    reason = "unknown_official_absence" if row is None else (
                        "official_zero_volume" if row["volume"] == 0 else row["state"])
                    report["pending"].append({**company, "reason": reason})
                    continue
                if path.exists():
                    report["pending"].append({**company, "reason": "file_created_concurrently"})
                    continue
                _seed_csv(path, row, target_date)
                report["seeded"].append({**company, "date": target_date, "rows": 1,
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "history_scope": "one real current bar only"})
        report["status"] = "pending" if report["pending"] else "ok"
        snapshot = {"target_date": target_date, "knowledge_date": knowledge_date, "companies": company_payloads,
                    "tapes": raw_tapes}
        _atomic_json(logs / f"listed_company_discovery_sources_{target:%Y%m%d}.json", snapshot)
        return report
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        _atomic_json(receipt_path, report)
