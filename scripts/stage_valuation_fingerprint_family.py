"""Reconcile the frozen, bounded valuation fingerprint findings with both markets.

Records exact response bytes and uses production parsers. Never writes production
sources; a separate feature comparison and reviewed promotion are required.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import io
import json
from pathlib import Path
import re
import ssl
import sys
import time

import numpy as np
import pandas as pd
import requests
from requests.adapters import HTTPAdapter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import backfill_valuation as producer
from scripts.valuation_source_contract import NUMERIC_COLUMNS

SCREEN = ROOT / "ml/reports/research/data_layer_consistency_20260906/valuation_market_fingerprint_screen.json"
OUT = ROOT / "output/valuation_fingerprint_family_20260906"


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


class CertificateAdapter(HTTPAdapter):
    def init_poolmanager(self, *args, **kwargs):
        context = ssl.create_default_context()
        context.verify_flags &= ~ssl.VERIFY_X509_STRICT
        kwargs["ssl_context"] = context
        return super().init_poolmanager(*args, **kwargs)


class RecordedSession:
    def __init__(self):
        self.session = requests.Session()
        self.session.mount("https://", CertificateAdapter())
        self.day = None
        self.records = []
        self.last_start = 0
        self.run_id = datetime.now().strftime("%H%M%S_%f")

    def get(self, url, **kwargs):
        pause = 3 - (time.monotonic() - self.last_start)
        if pause > 0:
            time.sleep(pause)
        self.last_start = time.monotonic()
        kwargs["verify"] = True
        response = self.session.get(url, **kwargs)
        market = "TWSE" if "www.twse.com.tw" in url else "TPEx"
        raw = OUT / "raw" / f"{self.day}_{market}_{self.run_id}_{len(self.records)}.json"
        raw.write_bytes(response.content)
        record = {"date": self.day, "market": market, "url": response.url,
                  "http_status": response.status_code, "raw_path": str(raw), "raw_sha256": sha(raw),
                  "knowledge_time": datetime.now().astimezone().isoformat()}
        self.records.append(record)
        save(raw.with_suffix(".receipt.json"), record)
        return response


def compare_frames(before, candidate):
    keys = ["Ticker", "Market"]
    if before.duplicated(keys).any() or candidate.duplicated(keys).any():
        raise ValueError("Duplicate same-market source key")
    old, new = before.set_index(keys).sort_index(), candidate.set_index(keys).sort_index()
    common = old.index.intersection(new.index)
    delta = []
    for column in NUMERIC_COLUMNS + ["Name"]:
        equal = old.loc[common, column].eq(new.loc[common, column]) | (
            old.loc[common, column].isna() & new.loc[common, column].isna())
        for ticker, market in common[~equal]:
            a, b = old.at[(ticker, market), column], new.at[(ticker, market), column]
            delta.append({"ticker": ticker, "market": market, "column": column,
                          "old": None if pd.isna(a) else str(a) if column == "Name" else float(a),
                          "new": None if pd.isna(b) else str(b) if column == "Name" else float(b)})
    return {"before_rows": len(old), "official_rows": len(new),
            "added_keys": [list(k) for k in new.index.difference(old.index)],
            "removed_keys": [list(k) for k in old.index.difference(new.index)],
            "changed_numeric_cells": sum(c["column"] != "Name" for c in delta),
            "changed_name_cells": sum(c["column"] == "Name" for c in delta), "cells": delta}


def numeric_repair_bundle(files):
    """Keep historical names for existing keys; current aliases do not prove PIT names."""
    directory = OUT / "values_candidate"
    directory.mkdir(exist_ok=True)
    numeric_files, skipped = [], []
    for item in files:
        source = ROOT / item["target"]
        before = pd.read_csv(OUT / "before" / source.name, dtype={"Ticker": str})
        official = pd.read_csv(item["source"], dtype={"Ticker": str})
        old_names = before.set_index(["Ticker", "Market"]).Name
        candidate = official.set_index(["Ticker", "Market"])
        common = candidate.index.intersection(old_names.index)
        candidate.loc[common, "Name"] = old_names.loc[common]
        candidate = candidate.reset_index()[before.columns]
        delta = compare_frames(before, candidate)
        if not delta["changed_numeric_cells"] and not delta["added_keys"] and not delta["removed_keys"]:
            skipped.append(source.name)
            continue
        path = directory / source.name
        candidate.to_csv(path, index=False, encoding="utf-8-sig")
        pd.testing.assert_frame_equal(candidate, pd.read_csv(path, dtype={"Ticker": str}), check_exact=True)
        numeric_files.append({**item, "source": str(path), "after_sha256": sha(path)})
    return numeric_files, skipped


def main():
    OUT.mkdir(exist_ok=True)
    for folder in ("raw", "before", "candidate", "receipts"):
        (OUT / folder).mkdir(exist_ok=True)
    screen = json.loads(SCREEN.read_text(encoding="utf-8"))
    days = {m["date"] for group in screen["current_cross_date_identical_market_groups"] for m in group["members"]}
    days |= {row["date"] for row in screen["duplicate_ticker_findings"] if re.fullmatch(r"\d{8}", row["date"])}
    if any(not re.fullmatch(r"\d{8}", day) for day in days) or len(days) > 60:
        raise ValueError("Findings exceed the locked source scope")
    manifest_by_day = {r["date"]: r for r in screen["source_manifest"]}
    plan = {"scope": "Existing exact market-fingerprint matches and duplicate ticker findings only",
            "screen_sha256": sha(SCREEN), "days": sorted(days), "source_before": [manifest_by_day[d] for d in sorted(days)]}
    plan_path = OUT / "plan.json"
    if plan_path.exists():
        if json.loads(plan_path.read_text(encoding="utf-8")) != plan:
            raise ValueError("Frozen scope changed")
    else:
        save(plan_path, plan)
    for source in plan["source_before"]:
        if sha(source["source_path"]) != source["sha256"]:
            raise ValueError(f"Source changed before reconciliation: {source['date']}")
    session = RecordedSession()
    producer.SESSION = session
    summaries, files, errors = [], [], []
    for index, day in enumerate(sorted(days), 1):
        prior_failure = None
        receipt_path = OUT / "receipts" / f"{day}.json"
        if receipt_path.exists():
            entry = json.loads(receipt_path.read_text(encoding="utf-8"))
            if entry["status"] == "VERIFIED":
                if any(sha(r["raw_path"]) != r["raw_sha256"] for r in entry["sources"]):
                    raise ValueError("Verified source receipt changed")
                if entry.get("file"):
                    if sha(entry["file"]["source"]) != entry["file"]["after_sha256"]:
                        raise ValueError("Candidate changed")
                    files.append(entry["file"])
                summaries.append(entry)
                continue
            history = OUT / "receipts" / "failed_attempts"
            history.mkdir(exist_ok=True)
            save(history / f"{day}_{session.run_id}.json", entry)
            prior_failure = entry
        session.day = day
        session.records = []
        source = Path(manifest_by_day[day]["source_path"])
        original = source.read_bytes()
        (OUT / "before" / source.name).write_bytes(original)
        before = pd.read_csv(io.BytesIO(original), dtype={"Ticker": str})
        try:
            if prior_failure and 'Unable to parse string "null"' in prior_failure.get("error", ""):
                # The saved 200 response already proves this requested date.
                # Re-parse its newly recognized explicit unknown marker without
                # another HTTP query. Preserve the original failed receipt.
                replayed = {}
                for market, label in (("TWSE", "上市"), ("TPEx", "上櫃")):
                    record = next(r for r in reversed(prior_failure["sources"]) if r["market"] == market and r["http_status"] == 200)
                    if sha(record["raw_path"]) != record["raw_sha256"]:
                        raise ValueError("Failed source replay bytes changed")
                    frame = producer.parse_official_valuation(json.loads(Path(record["raw_path"]).read_text(encoding="utf-8-sig")), label, day)
                    producer._validate_valuation_df(frame, f"Replay {market}/{day}")
                    replayed[market] = producer.SourceResult("ok", frame)
                    session.records.append(dict(record, replay="recognized_literal_null_marker"))
                twse, tpex = replayed["TWSE"], replayed["TPEx"]
            else:
                twse = producer.fetch_twse_valuation(day)
                tpex = producer.fetch_tpex_valuation(datetime.strptime(day, "%Y%m%d").date())
            candidate = producer.combine_complete_sources(twse, tpex)[before.columns]
            if candidate is None or candidate.empty:
                raise ValueError("Canonical producer did not return a complete day")
            delta = compare_frames(before, candidate)
            changed = bool(delta["cells"] or delta["added_keys"] or delta["removed_keys"])
            item = None
            if changed:
                target = OUT / "candidate" / source.name
                candidate.to_csv(target, index=False, encoding="utf-8-sig")
                pd.testing.assert_frame_equal(candidate, pd.read_csv(target, dtype={"Ticker": str}), check_exact=True)
                item = {"source": str(target), "target": source.relative_to(ROOT).as_posix(),
                        "before_sha256": sha(source), "after_sha256": sha(target)}
                files.append(item)
            entry = {"status": "VERIFIED", "date": day, **delta, "file": item, "sources": session.records}
            save(receipt_path, entry)
            summaries.append(entry)
            print(json.dumps({"done": index, "total": len(days), "date": day, "changed": changed,
                              "numeric_cells": delta["changed_numeric_cells"], "added_keys": len(delta["added_keys"]),
                              "removed_keys": len(delta["removed_keys"])}), flush=True)
        except Exception as exc:
            entry = {"status": "FAILED", "date": day, "error": str(exc), "sources": session.records}
            save(receipt_path, entry)
            errors.append(entry)
            print(json.dumps({"date": day, "error": str(exc)}), flush=True)
    if any(sha(r["source_path"]) != r["sha256"] for r in plan["source_before"]):
        raise ValueError("Production source changed during reconciliation")
    official_candidate_files = files
    skipped_name_only = []
    if not errors:
        files, skipped_name_only = numeric_repair_bundle(files)
    result = {"status": "STAGED_SOURCE_RECONCILIATION_COMPLETE" if not errors else "INCOMPLETE",
              "plan_sha256": sha(plan_path), "date_count": len(days), "changed_files": len(files),
              "numeric_changed_cells": sum(r["changed_numeric_cells"] for r in summaries),
              "summaries": summaries, "errors": errors, "files": files,
              "official_candidate_files": official_candidate_files, "name_only_dates_not_promoted": skipped_name_only,
              "historical_name_policy": "Retain original Name for common ticker/market keys; current official aliases cannot certify historical names. Newly restored keys use the official response name with current knowledge time.",
              "promotion": "NOT_RUN; latest feature A/B and reviewed promotion still required"}
    save(OUT / "reconciliation.json", result)
    print(json.dumps({k: v for k, v in result.items() if k not in {"summaries", "errors", "files"}}, ensure_ascii=False), flush=True)
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
