"""Supplement exact retained historical keys from official all-company filings."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
import time

import numpy as np
import pandas as pd
from bs4 import BeautifulSoup

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.stage_q2_financial_reconciliation import compare_and_merge, receipt
from scripts.stage_bs_historical_reconciliation import identity_inventory
from scripts.quarterly_source_contract import validate_quarter_frame


def parse_individual_anchor(html, year, quarter):
    soup = BeautifulSoup(html, "lxml")
    text = re.sub(r"\s+", "", soup.get_text())
    if f"民國{year-1911}年第{quarter}季" not in text:
        raise ValueError("individual official statement year/quarter mismatch")
    end = {1: "03月31日", 2: "06月30日", 3: "09月30日", 4: "12月31日"}[quarter]
    if f"{year-1911}年{end}" not in text:
        raise ValueError("individual official statement accounting date mismatch")
    aliases = {"Total_Assets": {"資產總額", "資產總計"},
               "Total_Liabilities": {"負債總額", "負債總計"},
               "Total_Equity": {"權益總額", "權益總計"}}
    values = {}
    for tr in soup.find_all("tr"):
        cells = [re.sub(r"\s+", "", x.get_text()) for x in tr.find_all(["th", "td"], recursive=False)]
        if len(cells) < 2:
            continue
        for key, names in aliases.items():
            if cells[0] in names:
                if key in values:
                    raise ValueError("ambiguous individual accounting item")
                values[key] = float(cells[1].replace(",", ""))
    if len(values) != 3 or abs(values["Total_Assets"] - values["Total_Liabilities"] - values["Total_Equity"]) > 2:
        raise ValueError("individual accounting identity/schema failure")
    return values


def run(base, historical, q2, output):
    base, output = base.resolve(), output.resolve()
    if not output.is_relative_to(base / "output") or output == base / "output":
        raise ValueError("Use isolated output subdirectory")
    output.mkdir(parents=True, exist_ok=True)
    from scripts import backfill_balance_sheet as bs
    history = json.loads(historical.read_text(encoding="utf-8"))
    q2_data = json.loads(q2.read_text(encoding="utf-8"))["reports"]["bs"]
    inputs = [(r["year"], r["quarter"], r["candidate"], r["retained_omitted_historical_rows"]) for r in history["reports"]]
    inputs.append((2026, 2, q2_data["candidate"], q2_data["retained_omitted_historical_rows"]))
    reports = []
    for year, quarter, source, omitted in inputs:
        if receipt(Path(source["path"]))["sha256"] != source["sha256"]:
            raise ValueError("frozen input changed")
        result_path = output / f"bs_{year}Q{quarter}_report.json"
        if result_path.exists():
            previous = json.loads(result_path.read_text(encoding="utf-8"))
            if previous.get("status") == "staged_retained_verified":
                for frozen in [previous["candidate"], previous["all_source"], *previous["individual_anchors"]]:
                    if receipt(Path(frozen["path"]))["sha256"] != frozen["sha256"]:
                        raise ValueError("Frozen retained stage changed")
                reports.append(previous)
                continue
        report = {"year": year, "quarter": quarter, "source": source, "requested_retained_tickers": omitted,
                  "status": "failed", "knowledge_time_utc": datetime.now(timezone.utc).isoformat()}
        try:
            before = pd.read_csv(source["path"], dtype={"Ticker": str})
            raw = output / "raw" / f"all_{year}Q{quarter}.html"
            raw.parent.mkdir(parents=True, exist_ok=True)
            payload = {"encodeURIComponent": "1", "step": "2", "firstin": "1", "off": "1",
                       "TYPEK": "all", "year": str(year - 1911), "season": str(quarter)}
            if not raw.exists():
                response = bs.SESSION.post(bs.MOPS_URL, data=payload, timeout=60)
                response.raise_for_status()
                raw.write_text(response.text, encoding="utf-8")
                time.sleep(1)
            html = raw.read_text(encoding="utf-8")
            titles = [re.sub(r"\s+", "", h.get_text()) for h in BeautifulSoup(html, "lxml").find_all("h2")]
            if titles != ["第" + "一二三四"[quarter-1] + "季資料"]:
                raise ValueError("all-company response quarter heading mismatch")
            all_frame = bs.parse_balance_sheet(html)
            retained = all_frame[all_frame.Ticker.isin(omitted)].copy()
            report["all_source"] = {**receipt(raw), "request": payload, "url": bs.MOPS_URL,
                                    "response_year_explicit": False, "rows": len(all_frame), "title": titles[0]}
            missing = sorted(set(omitted) - set(retained.Ticker))
            report["still_missing_official"] = missing
            anchors = []
            # The old-market batch omits these companies. Obtain an explicit
            # historical year/quarter source for the retained current accounts.
            # One retained company anchors each quarter, plus each 2020Q1 defect.
            checks = {next((ticker for ticker in ("5371", "2867", "3454", "4130")
                            if ticker in set(retained.Ticker)), retained.Ticker.iloc[0])}
            if (year, quarter) == (2020, 1):
                checks.update(set(retained.Ticker) & {"3454", "4130", "5371"})
            for ticker in sorted(checks):
                individual = output / "raw" / f"individual_{ticker}_{year}Q{quarter}.html"
                request = {"encodeURIComponent": "1", "step": "1", "firstin": "1", "off": "1", "TYPEK": "all",
                           "isnew": "false", "co_id": ticker, "year": str(year-1911), "season": f"{quarter:02d}"}
                url = "https://mopsov.twse.com.tw/mops/web/ajax_t164sb03"
                if not individual.exists():
                    response = bs.SESSION.post(url, data=request, timeout=60)
                    response.raise_for_status()
                    individual.write_text(response.text, encoding="utf-8")
                    time.sleep(1)
                individual_html = individual.read_text(encoding="utf-8")
                unavailable_text = BeautifulSoup(individual_html, "lxml").get_text(" ", strip=True)
                if "不繼續公開發行" in unavailable_text or "查無資料" in unavailable_text:
                    report.setdefault("unavailable_individual_sources", []).append({**receipt(individual),
                        "request": request, "url": url, "source_message": unavailable_text[:300]})
                    continue
                values = parse_individual_anchor(individual_html, year, quarter)
                row = retained.set_index("Ticker").loc[ticker]
                for field, value in values.items():
                    if value != row[field]:
                        raise ValueError(f"retained individual/all source mismatch: {ticker}/{field}")
                anchors.append({**receipt(individual), "request": request, "url": url, "values": values,
                                "response_year_explicit": True})
            report["individual_anchors"] = anchors
            if not anchors:
                raise ValueError("No independently dated individual source could corroborate this quarter")
            retained["Market"] = retained.Ticker.map(before.set_index("Ticker").Market)
            retained["Year"], retained["Season"] = year, quarter
            retained["Debt_Ratio"] = (retained.Total_Liabilities / retained.Total_Assets.replace(0, np.nan) * 100).round(2)
            retained["Current_Ratio"] = (retained.Current_Assets / retained.Current_Liabilities.replace(0, np.nan) * 100).round(2)
            # Exact old keys only; the additional unlisted/all-company rows do
            # not enter the project universe or any model source table.
            candidate, diff = compare_and_merge(before, retained)
            report["changes"] = diff["existing_changes"]
            report["candidate_identity"] = identity_inventory(candidate)
            validate_quarter_frame(candidate, "bs", year, quarter)
            target = output / "candidate" / "資產負債" / f"bs_{year}Q{quarter}.csv"
            target.parent.mkdir(parents=True, exist_ok=True)
            candidate.to_csv(target, index=False, encoding="utf-8-sig")
            report["candidate"] = receipt(target)
            report["status"] = "staged_retained_verified" if not missing else "staged_remaining_unknown_keys"
        except Exception as exc:
            report["error"] = str(exc)
        result_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        reports.append(report)
        print(json.dumps({k: report.get(k) for k in ("year", "quarter", "status", "still_missing_official", "error")}, ensure_ascii=True), flush=True)
    summary = {"status": "staged_not_promoted", "reports": reports,
               "failed_quarters": [f'{r["year"]}Q{r["quarter"]}' for r in reports if r["status"] == "failed"],
               "scope": "only exact previously-filed retained keys; frozen C and E source files unchanged"}
    (output / "manifest.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-dir", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--historical-manifest", type=Path, required=True)
    parser.add_argument("--q2-manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    result = run(args.base_dir, args.historical_manifest, args.q2_manifest, args.output_dir)
    raise SystemExit(bool(result["failed_quarters"]))
