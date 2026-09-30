"""Fetch bounded official historical gaps into a guarded research staging area.

No production CSV, DuckDB, model or ledger is changed. Raw responses and exact
request/error metadata are preserved even when the source cannot supply data.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[1]
STAGE = ROOT / "output/historical_gaps_20260906"


def checked_root():
    assert ROOT == Path(os.environ["STOCK_ACCEPTANCE_ROOT"]).resolve()
    sys.path.insert(0, str(ROOT))
    from scripts.pipeline_acceptance_guard import _INSTALLED
    assert _INSTALLED is not None and _INSTALLED.root == ROOT
    assert Path.cwd().resolve().is_relative_to(ROOT)


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+"\n", encoding="utf-8")


def fetch(label, url, *, post=None):
    import requests
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    raw = STAGE / "raw" / f"{label}.txt"
    meta_path = STAGE / "raw" / f"{label}.json"
    raw.parent.mkdir(parents=True, exist_ok=True)
    meta = {"label": label, "url": url, "method": "POST_QUERY" if post else "GET",
            "request_body": post, "fetched_at": datetime.now().isoformat()}
    try:
        session = requests.Session()
        session.headers.update({"User-Agent": "Mozilla/5.0", "Referer": "https://www.tdcc.com.tw/portal/zh/smWeb/qryStock" if "tdcc" in url else "https://www.twse.com.tw/"})
        response = session.post(url, json=post, timeout=25, verify=False) if post else session.get(url, timeout=25, verify=False)
        raw.write_bytes(response.content)
        meta.update(http_status=response.status_code, final_url=response.url, bytes=len(response.content), transport="requests")
        response.raise_for_status()
        try:
            payload = response.json()
            meta["json_type"] = type(payload).__name__
            if isinstance(payload, dict):
                meta["response_keys"] = list(payload)
                meta["response_meta"] = {k: v for k, v in payload.items() if not isinstance(v, (list, dict))}
                meta["fields"] = payload.get("fields")
                tables = payload.get("tables", [])
                meta["tables"] = [{"title": t.get("title"), "fields": t.get("fields"), "rows": len(t.get("data", []))} for t in tables if isinstance(t, dict)]
                meta["data_rows"] = len(payload.get("data", payload.get("aaData", [])))
                meta["sample"] = payload.get("data", payload.get("aaData", []))[:2]
                if tables and isinstance(tables[0], dict):
                    meta["sample"] = tables[0].get("data", [])[:2]
            elif isinstance(payload, list):
                meta["data_rows"] = len(payload)
                meta["sample"] = payload[:1]
        except ValueError:
            meta["json_type"] = "non_json"
            meta["sample"] = response.text[:250]
    except Exception as exc:
        meta["error"] = f"{type(exc).__name__}: {exc}"
        # Existing repository convention for TPEx certificate incompatibility.
        # Curl is GET only and validated by the installed guard.
        if post is None:
            try:
                completed = subprocess.run(["curl.exe", "-k", "-L", "-sS", "--max-time", "25", url], capture_output=True, check=True)
                raw.write_bytes(completed.stdout)
                payload = json.loads(completed.stdout.decode("utf-8-sig"))
                meta.update(transport="curl_fallback", bytes=len(completed.stdout), json_type=type(payload).__name__)
                if isinstance(payload, dict):
                    meta["response_keys"] = list(payload)
                    meta["response_meta"] = {k: v for k, v in payload.items() if not isinstance(v, (list, dict))}
                    meta["fields"] = payload.get("fields")
                    meta["tables"] = [{"title": t.get("title"), "fields": t.get("fields"), "rows": len(t.get("data", []))} for t in payload.get("tables", []) if isinstance(t, dict)]
            except Exception as fallback_exc:
                meta["fallback_error"] = f"{type(fallback_exc).__name__}: {fallback_exc}"
    if raw.exists():
        meta["sha256"] = hashlib.sha256(raw.read_bytes()).hexdigest()
    write_json(meta_path, meta)
    print(json.dumps(meta, ensure_ascii=False), flush=True)
    return meta


def probe():
    checked_root()
    requests_to_run = []
    for date in ("20220915", "20220916", "20220919", "20260807"):
        dt = datetime.strptime(date, "%Y%m%d")
        requests_to_run.extend([
            (f"foreign_{date}_TWSE", "https://www.twse.com.tw/rwd/zh/fund/MI_QFIIS?"+urlencode({"response":"json","date":date,"selectType":"ALLBUT0999"}), None),
            (f"foreign_{date}_TPEx", "https://www.tpex.org.tw/web/stock/3insti/qfii/qfii_result.php?"+urlencode({"l":"zh-tw","d":f"{dt.year-1911}/{dt:%m/%d}","t":"D"}), None),
        ])
    requests_to_run.append(("tdcc_history_page", "https://www.tdcc.com.tw/portal/zh/smWeb/qryStock", None))
    # Read query only; use last observed session, not a holiday Friday.
    for date in ("20251009", "20251023", "20260226"):
        requests_to_run.append((f"tdcc_{date}", "https://www.tdcc.com.tw/server/web/StockQuery/queryStockCustStat",
                                {"SqlMethod":"StockNo", "StockNo":"", "StartDate":date, "EndDate":date}))
    results = []
    for label, url, body in requests_to_run:
        results.append(fetch(label, url, post=body))
        time.sleep(1.0)
    write_json(STAGE / "probe_manifest.json", results)


def validate_foreign_payload(payload, market, requested_date):
    """Require response date and named fields; preserve explicit official unknowns."""
    import numpy as np
    import pandas as pd
    if str(payload.get("date")) != requested_date:
        raise ValueError("foreign source response date differs from request")
    if str(payload.get("stat", "")).casefold() != "ok":
        raise ValueError("foreign source did not return OK")
    if market == "TWSE":
        fields, rows = payload.get("fields", []), payload.get("data", [])
        wanted = {"Ticker":"證券代號", "Issued_Shares":"發行股數",
                  "Foreign_Held":"全體外資及陸資持有股數", "Foreign_Pct":"全體外資及陸資持股比率"}
    else:
        tables = payload.get("tables", [])
        if not tables:
            raise ValueError("TPEx missing named table")
        fields, rows = tables[0].get("fields", []), tables[0].get("data", [])
        wanted = {"Ticker":"代號", "Issued_Shares":"發行股數(A)",
                  "Foreign_Held":"僑外資及陸資持有股數(C)", "Foreign_Pct":"僑外資及陸資持股比率(E=C/A)"}
    if not rows or not all(name in fields for name in wanted.values()):
        raise ValueError(f"{market} missing required named fields or rows")
    if any(len(row) != len(fields) for row in rows):
        raise ValueError("Ragged foreign response")
    source = pd.DataFrame(rows, columns=fields)
    frame = pd.DataFrame({key: source[value] for key,value in wanted.items()})
    frame["Ticker"] = frame["Ticker"].astype(str).str.strip()
    frame = frame[frame.Ticker.str.match(r"^\d[0-9A-Z]*$")].copy()
    if frame.Ticker.duplicated().any():
        raise ValueError("Duplicate source ticker")
    for column in ("Issued_Shares", "Foreign_Held", "Foreign_Pct"):
        values = frame[column].astype(str).str.strip().str.replace(",", "", regex=False).str.replace("%", "", regex=False)
        unknown = values.isin(["", "-", "--", "N/A", "None", "nan"])
        frame[column] = pd.to_numeric(values.where(~unknown), errors="raise")
        if np.isinf(frame[column]).any():
            raise ValueError("Nonfinite foreign numeric value")
    finite = frame[["Issued_Shares","Foreign_Held","Foreign_Pct"]].notna().all(axis=1)
    complete = frame.loc[finite]
    if ((frame.Issued_Shares.dropna() <= 0).any() or (frame.Foreign_Held.dropna() < 0).any()
        or not frame.Foreign_Pct.dropna().between(0,100).all()):
        raise ValueError("Foreign numeric range violation")
    if ((complete.Issued_Shares <= 0) | (complete.Foreign_Held < 0) |
        (complete.Foreign_Held > complete.Issued_Shares) |
        ~complete.Foreign_Pct.between(0,100)).any():
        raise ValueError("Foreign numeric range violation")
    residual = (complete.Foreign_Held / complete.Issued_Shares * 100 - complete.Foreign_Pct).abs()
    if (residual > 0.03).any():
        raise ValueError("Foreign shares/percentage unit or field mismatch")
    return frame.reset_index(drop=True), {"rows":len(frame), "market":market,
        "unknown_rows":int((~finite).sum()), "unknown_tickers":frame.loc[~finite,"Ticker"].tolist(),
        "max_share_percentage_residual_pp":float(residual.max()) if len(residual) else None,
        "numeric_unit":"shares (named official fields; both markets)", "fields":fields}


def stage_foreign():
    checked_root()
    import pandas as pd
    output = []
    for day in ("20220915", "20220916", "20220919", "20260807"):
        frames, checks = [], []
        for market in ("TWSE", "TPEx"):
            raw = STAGE / f"raw/foreign_{day}_{market}.txt"
            frame, check = validate_foreign_payload(json.loads(raw.read_text(encoding="utf-8-sig")), market, day)
            if len(frame) < 650:
                raise ValueError(f"Partial {day} {market}: {len(frame)}")
            check["raw_sha256"] = hashlib.sha256(raw.read_bytes()).hexdigest()
            frames.append(frame)
            checks.append(check)
        combined = pd.concat(frames, ignore_index=True).sort_values("Ticker").reset_index(drop=True)
        assert len(combined) >= 1500 and not combined.Ticker.duplicated().any()
        old = ROOT / "外資持股" / f"{day}.csv"
        old_hash = hashlib.sha256(old.read_bytes()).hexdigest() if old.exists() else None
        surrounding = sorted((ROOT / "外資持股").glob("*.csv"))
        neighbors = [p for p in surrounding if p.stem < day][-1:] + [p for p in surrounding if p.stem > day][:1]
        coverage = []
        for neighbor in neighbors:
            old_frame = pd.read_csv(neighbor, dtype={"Ticker":str})
            overlap = len(set(old_frame.Ticker)&set(combined.Ticker))/len(set(old_frame.Ticker))
            assert overlap >= 0.80, f"Neighbor coverage insufficient {day}"
            coverage.append({"date":neighbor.stem,"rows":len(old_frame),"ticker_overlap_ratio":overlap,
                             "sha256":hashlib.sha256(neighbor.read_bytes()).hexdigest()})
        target = STAGE / "candidates/外資持股" / f"{day}.csv"
        target.parent.mkdir(parents=True,exist_ok=True)
        combined.to_csv(target,index=False,encoding="utf-8-sig")
        assert (hashlib.sha256(old.read_bytes()).hexdigest() if old.exists() else None) == old_hash
        output.append({"date":day,"status":"VALIDATED_STAGING_ONLY","rows":len(combined),
            "target":str(target),"sha256":hashlib.sha256(target.read_bytes()).hexdigest(),
            "source_before_path":str(old),"source_before_sha256":old_hash,
            "source_before_rows":len(pd.read_csv(old)) if old.exists() else 0,
            "market_validation":checks,"neighbor_coverage":coverage})
    write_json(STAGE/"foreign_candidates.json",output)
    print(json.dumps(output,ensure_ascii=False),flush=True)


def tdcc_sample():
    """Probe real dated security queries using the public form and its session."""
    checked_root()
    import requests
    from bs4 import BeautifulSoup
    session = requests.Session()
    session.headers.update({"User-Agent":"Mozilla/5.0"})
    url = "https://www.tdcc.com.tw/portal/zh/smWeb/qryStock"
    results = []
    for date in ("20251009", "20251023", "20260226"):
        response = session.get(url,timeout=25)
        response.raise_for_status()
        soup = BeautifulSoup(response.text,"html.parser")
        form = soup.find("form",id="form1")
        values = {node["name"]:node.get("value","") for node in form.select("input[type=hidden][name]")}
        choices = [node.get("value") for node in form.select("select[name=scaDate] option")]
        assert date in choices, "Requested date not published by TDCC"
        values.update(scaDate=date,sqlMethod="StockNo",stockNo="2330",stockName="")
        response = session.post(url,data=values,timeout=25)
        response.raise_for_status()
        target = STAGE/f"raw/tdcc_public_2330_{date}.html"
        target.write_bytes(response.content)
        parsed = BeautifulSoup(response.text,"html.parser")
        tables = [[" ".join(row.stripped_strings) for row in table.select("tr")] for table in parsed.select("table")]
        result = {"date":date,"ticker":"2330","http_status":response.status_code,
                  "path":str(target),"sha256":hashlib.sha256(response.content).hexdigest(),"tables":tables,
                  "scope":"Single-security accessibility probe, not full-universe raw replacement"}
        results.append(result)
        print(json.dumps(result,ensure_ascii=False),flush=True)
        time.sleep(2)
    write_json(STAGE/"tdcc_public_samples.json",results)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["probe", "stage-foreign", "tdcc-sample"])
    args = parser.parse_args()
    if args.mode == "probe":
        probe()
    elif args.mode == "stage-foreign":
        stage_foreign()
    elif args.mode == "tdcc-sample":
        tdcc_sample()
