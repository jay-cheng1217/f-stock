"""Freeze official 2026Q2 inputs through canonical fetchers; never publish them."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys

import numpy as np
import pandas as pd
from bs4 import BeautifulSoup

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def receipt(path):
    return {"path": str(path.resolve()), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "bytes": path.stat().st_size}


def compare_and_merge(before, official):
    if before.Ticker.duplicated().any() or official.Ticker.duplicated().any():
        raise ValueError("Duplicate quarterly ticker")
    columns = list(before.columns)
    if set(columns) != set(official.columns):
        raise ValueError(f"Quarterly schema changed: {set(columns) ^ set(official.columns)}")
    official = official[columns]
    old, new = before.set_index("Ticker"), official.set_index("Ticker")
    common = sorted(set(old.index) & set(new.index))
    different = ~(old.loc[common].eq(new.loc[common]) | (old.loc[common].isna() & new.loc[common].isna()))
    changes = []
    for ticker in different.index[different.any(axis=1)]:
        for column in different.columns[different.loc[ticker]]:
            a, b = old.loc[ticker, column], new.loc[ticker, column]
            changes.append({"Ticker": ticker, "column": column, "old": None if pd.isna(a) else a.item() if hasattr(a, "item") else a,
                            "new": None if pd.isna(b) else b.item() if hasattr(b, "item") else b})
    removed = sorted(set(old.index) - set(new.index))
    combined = pd.concat([before[before.Ticker.isin(removed)], official], ignore_index=True).sort_values("Ticker").reset_index(drop=True)
    return combined, {"before_rows": len(before), "official_rows": len(official), "candidate_rows": len(combined),
        "added": sorted(set(new.index) - set(old.index)), "retained_omitted_historical_rows": removed, "existing_changes": changes}


def run(base, out, financial_raw_dir, frozen_manifest=None):
    base, out = base.resolve(), out.resolve()
    if out.exists() or not out.is_relative_to(base / "output") or out == base / "output":
        raise ValueError("Use a new isolated output subdirectory")
    out.mkdir(parents=True)
    frozen_sources = {}
    if frozen_manifest is not None:
        frozen = json.loads(frozen_manifest.read_text(encoding="utf-8"))
        if frozen["quarter"] != "2026Q2":
            raise ValueError("Frozen quarter mismatch")
        frozen_sources = {(e["kind"], e["market"]): e for e in frozen["official_evidence"]}
    os.environ["STOCK_BASE_DIR"] = str(out)
    import twstock
    from scripts import backfill_eps as eps
    from scripts import backfill_balance_sheet as bs

    # These are the actual canonical HTTP fetchers. Keep CA/hostname checking
    # enabled while allowing public-server certificate compatibility on Python 3.14.
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
    for mod in (eps, bs):
        mod.SESSION = VerifiedSession()
        mod.SESSION.mount("https://", Adapter())
    raw_dir = out / "raw"
    raw_dir.mkdir()
    reports, evidence = {}, []
    configurations = [
        ("financial", Path("季報財務/financial_2026Q2.csv"), None, twstock._parse_mops_ratio_tables, twstock._combine_complete_financial_markets, "https://mopsov.twse.com.tw/mops/web/ajax_t163sb06"),
        ("eps", Path("季報財務/eps_2026Q2.csv"), eps.fetch_mops_eps, eps.parse_mops_eps_tables, eps.combine_complete_markets, eps.MOPS_URL),
        ("bs", Path("資產負債/bs_2026Q2.csv"), bs.fetch_balance_sheet, bs.parse_balance_sheet, bs.combine_complete_markets, bs.MOPS_URL),
    ]
    for kind, rel, fetch, parse, combine, url in configurations:
        before_path = base / rel
        before = pd.read_csv(before_path, dtype={"Ticker": str})
        markets = {}
        for typek, market in [("sii", "TWSE"), ("otc", "OTC")]:
            if frozen_sources:
                source = frozen_sources[(kind, market)]
                path = Path(source["path"])
                if receipt(path)["sha256"] != source["sha256"]:
                    raise ValueError("Frozen official HTML changed")
                html = path.read_text(encoding="utf-8")
            elif kind == "financial":
                html = (financial_raw_dir / f"mops_115Q2_{typek}.html").read_text(encoding="utf-8")
            else:
                html = fetch(115, 2, typek)
            if not html:
                raise ValueError(f"No official HTML: {kind}/{market}")
            path = raw_dir / f"{kind}_115Q2_{typek}.html"
            path.write_text(html, encoding="utf-8")
            text = BeautifulSoup(html, "lxml").get_text(" ", strip=True)
            explicit_year = bool(re.search(r"115\s*年(?:度)?\s*第?\s*(?:二|2)\s*季", text[:500]))
            # The canonical EPS/BS endpoint titles omit the year. Keep the actual
            # POST year in evidence rather than inventing a response year field.
            valid_title = explicit_year if kind == "financial" else bool(re.search(r"公司\s*第?\s*(?:二|2)\s*季", text[:100]))
            if not valid_title:
                raise ValueError(f"Official quarter/title mismatch: {kind}/{market}: {text[:150]}")
            frame = parse(html)
            frame["Market"] = market
            markets[market] = frame
            evidence.append({**receipt(path), "kind": kind, "market": market, "url": url,
                             "request": {"year": "115", "season": "2", "TYPEK": typek}, "title": text[:100], "response_year_explicit": explicit_year, "rows": len(frame)})
        official = combine(markets, len(before))
        official["Year"] = 2026
        official["Season"] = 2
        if kind == "bs":
            official["Debt_Ratio"] = (official.Total_Liabilities / official.Total_Assets.replace(0, np.nan) * 100).round(2)
            official["Current_Ratio"] = (official.Current_Assets / official.Current_Liabilities.replace(0, np.nan) * 100).round(2)
        official = official[before.columns]
        candidate, report = compare_and_merge(before, official)
        raw_path = out / "official_only" / rel
        candidate_path = out / "candidate" / rel
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        candidate_path.parent.mkdir(parents=True, exist_ok=True)
        official.to_csv(raw_path, index=False, encoding="utf-8-sig")
        candidate.to_csv(candidate_path, index=False, encoding="utf-8-sig")
        reports[kind] = {**report, "before": receipt(before_path), "official": receipt(raw_path), "candidate": receipt(candidate_path)}
        print(json.dumps({"kind": kind, "before": len(before), "official": len(official), "candidate": len(candidate),
            "added": len(report["added"]), "existing_changes": len(report["existing_changes"]), "retained_omitted": report["retained_omitted_historical_rows"]}, ensure_ascii=True), flush=True)
    result = {"status": "staged_not_promoted", "quarter": "2026Q2", "reports": reports, "official_evidence": evidence,
              "calculation_method": "Unchanged canonical fetch/parse/completeness/derived-ratio functions", "model_or_production_writes": False}
    (out / "manifest.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-dir", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--financial-raw-dir", type=Path, required=True)
    parser.add_argument("--frozen-manifest", type=Path)
    args = parser.parse_args()
    run(args.base_dir, args.output_dir, args.financial_raw_dir, args.frozen_manifest)
