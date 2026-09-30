"""Stage historical BS through canonical fetch/parser; never write production data."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import time

import numpy as np
import pandas as pd

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.quarterly_source_contract import validate_html_period, validate_quarter_frame
from scripts.stage_q2_financial_reconciliation import compare_and_merge, receipt


def identity_inventory(frame):
    values = frame[["Total_Assets", "Total_Liabilities", "Total_Equity"]].apply(pd.to_numeric, errors="coerce")
    known = values.notna().all(axis=1)
    bad = known & (values.Total_Assets - values.Total_Liabilities - values.Total_Equity).abs().gt(2)
    return {"known_rows": int(known.sum()), "unknown_rows": int((~known).sum()),
            "violation_tickers": frame.loc[bad, "Ticker"].tolist()}


def stage(base, output, *, limit=None):
    base, output = base.resolve(), output.resolve()
    if not output.is_relative_to(base / "output") or output == base / "output":
        raise ValueError("Use an isolated repository output subdirectory")
    output.mkdir(parents=True, exist_ok=True)
    os.environ["STOCK_BASE_DIR"] = str(output / "runtime")
    from scripts import backfill_balance_sheet as bs
    # Use the actual canonical fetch function; keep CA/hostname verification on.
    import requests
    import ssl
    from requests.adapters import HTTPAdapter
    class VerifiedSession(requests.Session):
        def request(self, *args, **kwargs):
            kwargs["verify"] = True
            return super().request(*args, **kwargs)
    class Adapter(HTTPAdapter):
        def init_poolmanager(self, *args, **kwargs):
            context = ssl.create_default_context()
            context.verify_flags &= ~ssl.VERIFY_X509_STRICT
            kwargs["ssl_context"] = context
            return super().init_poolmanager(*args, **kwargs)
    bs.SESSION = VerifiedSession()
    bs.SESSION.mount("https://", Adapter())
    quarters = [(year, q) for year in range(2020, 2027) for q in range(1, 5) if (year, q) <= (2026, 1)]
    if limit:
        quarters = quarters[:limit]
    reports = []
    for year, quarter in quarters:
        name = f"bs_{year}Q{quarter}.csv"
        report_path = output / f"bs_{year}Q{quarter}_report.json"
        if report_path.exists():
            previous = json.loads(report_path.read_text(encoding="utf-8"))
            if previous.get("status", "").startswith("staged"):
                for item in [previous["before"], previous["official"], previous["candidate"], *previous["sources"]]:
                    if receipt(Path(item["path"]))["sha256"] != item["sha256"]:
                        raise ValueError("Frozen historical stage changed")
                reports.append(previous)
                continue
        report = {"year": year, "quarter": quarter, "status": "failed", "sources": []}
        try:
            source = base / "資產負債" / name
            before_path = output / "before" / "資產負債" / name
            before_path.parent.mkdir(parents=True, exist_ok=True)
            if not before_path.exists():
                before_path.write_bytes(source.read_bytes())
            before = pd.read_csv(before_path, dtype={"Ticker": str})
            report["before"] = receipt(before_path)
            report["before_identity"] = identity_inventory(before)
            frames = {}
            for typek, market in (("sii", "TWSE"), ("otc", "OTC")):
                raw_path = output / "raw" / f"bs_{year}Q{quarter}_{typek}.html"
                raw_path.parent.mkdir(parents=True, exist_ok=True)
                knowledge = datetime.now(timezone.utc).isoformat()
                if raw_path.exists():
                    html = raw_path.read_text(encoding="utf-8")
                    knowledge = "cached_response_initial_fetch_at_file_mtime; see mtime_ns"
                else:
                    html = bs.fetch_balance_sheet(year - 1911, quarter, typek)
                    if not html:
                        raise ValueError(f"Official source failed: {year}Q{quarter} {market}")
                    raw_path.write_text(html, encoding="utf-8")
                    time.sleep(2)
                proof = validate_html_period(html, "bs", year, quarter, market)
                frame = bs.parse_balance_sheet(html)
                frame["Market"] = market
                frames[market] = frame
                report["sources"].append({**receipt(raw_path), **proof, "request_TYPEK": typek,
                    "year_evidence": "request_only_no_response_year", "knowledge_time_utc": knowledge,
                    "mtime_ns": raw_path.stat().st_mtime_ns, "url": bs.MOPS_URL, "rows": len(frame)})
            official = bs.combine_complete_markets(frames, len(before))
            official["Year"], official["Season"] = year, quarter
            official["Debt_Ratio"] = (official.Total_Liabilities / official.Total_Assets.replace(0, np.nan) * 100).round(2)
            official["Current_Ratio"] = (official.Current_Assets / official.Current_Liabilities.replace(0, np.nan) * 100).round(2)
            validate_quarter_frame(official, "bs", year, quarter)
            missing = sorted(set(official.columns) - set(before.columns))
            extra = sorted(set(before.columns) - set(official.columns))
            if extra or set(missing) - {"Name"}:
                raise ValueError(f"Unresolved old schema: missing={missing}, extra={extra}")
            for column in missing:
                before[column] = np.nan
            candidate, changes = compare_and_merge(before, official)
            report.update(changes)
            report["added_metadata_columns"] = missing
            report["official_identity"] = identity_inventory(official)
            report["candidate_identity"] = identity_inventory(candidate)
            omitted = candidate[candidate.Ticker.isin(changes["retained_omitted_historical_rows"])]
            report["retained_identity"] = identity_inventory(omitted)
            report["retained_missing_name"] = omitted.loc[omitted.Name.isna(), "Ticker"].tolist()
            for kind, frame in (("official", official), ("candidate", candidate)):
                path = output / kind / "資產負債" / name
                path.parent.mkdir(parents=True, exist_ok=True)
                frame.to_csv(path, index=False, encoding="utf-8-sig")
                report[kind] = receipt(path)
            report["status"] = ("staged_blocked_retained_source_defects" if report["candidate_identity"]["violation_tickers"] or report["retained_missing_name"]
                                else "staged_request_year_only_not_promoted")
        except Exception as exc:
            report["error"] = str(exc)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        reports.append(report)
        print(json.dumps({k: report.get(k) for k in ("year", "quarter", "status", "official_rows", "candidate_rows", "error")}, ensure_ascii=True), flush=True)
    result = {"status": "staging_complete" if all(r["status"].startswith("staged") for r in reports) else "staging_has_failures",
              "scope": "2020Q1-2026Q1; Q2 frozen C unchanged", "reports": reports,
              "production_writes": False, "model_changes": False,
              "year_proof_limitation": "MOPS headings only prove quarter/market, year is actual request parameter"}
    (output / "manifest.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-dir", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    report = stage(args.base_dir, args.output_dir, limit=args.limit)
    raise SystemExit(0 if report["status"] == "staging_complete" else 1)
