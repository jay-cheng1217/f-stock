"""Reconcile suspected leading-zero aliases against stored official daily sources.

Read-only for production; output is a separate local evidence directory. An
absent official row is recorded as absence and is never replaced by zero.
"""
from pathlib import Path
import hashlib
import json
import sys
import time

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.audit_margin_day_repair import parse_complete_twse_day
from scripts.enrich_new_stock_sources import parse_raw_margin, MARGIN_COLS


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def compare(frame, official, scope):
    if frame.duplicated(["Ticker", "Date"]).any():
        raise ValueError(f"Duplicate identifier/date in {scope}")
    left = frame.set_index(["Ticker", "Date"])[MARGIN_COLS]
    right = official.set_index(["Ticker", "Date"])[MARGIN_COLS]
    keys = left.index.intersection(right.index)
    a, b = left.loc[keys], right.loc[keys]
    changes = []
    for col in MARGIN_COLS:
        wrong = ~(a[col].eq(b[col]) | (a[col].isna() & b[col].isna()))
        for ticker, day in a.index[wrong]:
            old, new = a.loc[(ticker, day), col], b.loc[(ticker, day), col]
            changes.append({"scope": scope, "Ticker": ticker, "Date": day, "column": col,
                            "old": None if pd.isna(old) else float(old),
                            "new": None if pd.isna(new) else float(new)})
    return {"scope": scope, "local_rows": len(left), "official_rows": len(right),
            "paired_rows": len(keys), "different_cells": len(changes),
            "local_rows_without_official_key": len(left.index.difference(right.index)),
            "official_rows_without_local_key": len(right.index.difference(left.index))}, changes


def main():
    out = ROOT / "output/margin_identifier_history_20260906"
    if out.exists():
        raise ValueError("Evidence directory already exists")
    out.mkdir(parents=True)
    prior = json.loads((ROOT / "output/margin_20260320_repair_full_v2_20260906/validation.json").read_text(encoding="utf-8"))
    suspects = prior["historical_alias_inventory"]
    tickers = {r[k] for r in suspects for k in ("official_ticker", "stripped_alias")}
    merged_path = ROOT / "清理後資料/cleaned_all_margin_data.csv"
    merged = pd.read_csv(merged_path, dtype={"Ticker": str, "Date": str})
    merged = merged[merged.Ticker.isin(tickers) & merged.Date.ge("2020-01-01")]
    days = sorted(merged.Date.unique())
    frames, inputs, errors = [], [{"path": str(merged_path), "sha256": digest(merged_path)}], []
    started = time.monotonic()
    for index, day in enumerate(days, 1):
        sources = []
        for market, suffix in (("twse", "csv"), ("tpex", "json")):
            path = ROOT / "原始融資資料" / f"raw_margin_{market}_{day.replace('-', '')}.{suffix}"
            try:
                before_hash = digest(path)
                frame = parse_complete_twse_day(path, day) if market == "twse" else parse_raw_margin(path, market, day)
                if digest(path) != before_hash:
                    raise ValueError("Source changed during audit")
                inputs.append({"path": str(path), "sha256": before_hash})
                sources.append(frame[frame.Ticker.isin(tickers)])
            except Exception as exc:
                errors.append({"day": day, "market": market, "error": str(exc)})
        if len(sources) == 2:
            current = pd.concat(sources, ignore_index=True)
            if current.duplicated(["Ticker", "Date"]).any():
                raise ValueError("Official dual-market key collision")
            frames.append(current)
        if index % 100 == 0:
            print(json.dumps({"days": index, "total": len(days), "errors": len(errors),
                              "elapsed_seconds": round(time.monotonic() - started, 1)}), flush=True)
    official = pd.concat(frames, ignore_index=True)
    official.to_csv(out / "official_selected.csv", index=False)
    summary, changes = compare(merged, official, "merged_table")
    summaries = [summary]
    missing_schema = []
    for ticker in sorted(tickers):
        path = ROOT / "日K資料" / f"{ticker}.csv"
        if not path.exists():
            continue
        inputs.append({"path": str(path), "sha256": digest(path)})
        frame = pd.read_csv(path, dtype={"Date": str})
        if not set(MARGIN_COLS).issubset(frame):
            missing_schema.append(ticker)
            continue
        frame["Ticker"] = ticker
        frame = frame[frame.Date.ge("2020-01-01") & frame.Date.isin(days)]
        item, delta = compare(frame, official[official.Ticker.eq(ticker)], "daily_csv")
        item["ticker"] = ticker
        summaries.append(item)
        changes.extend(delta)
    pd.DataFrame(changes).to_csv(out / "cell_changes.csv", index=False)
    changed_inputs = [r["path"] for r in inputs if digest(Path(r["path"])) != r["sha256"]]
    report = {"status": "AUDIT_COMPLETE" if not errors and not changed_inputs else "INCOMPLETE",
              "scope": "Suspected zero-stripped identifier keys and their real counterparts, 2020 onward; no production writes",
              "days": len(days), "tickers": sorted(tickers), "summaries": summaries,
              "official_errors": errors, "changed_inputs": changed_inputs,
              "daily_missing_margin_schema": missing_schema, "inputs": inputs,
              "changed_cell_count": len(changes), "elapsed_seconds": round(time.monotonic() - started, 1)}
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "cells": len(changes), "errors": len(errors), "output": str(out)}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
