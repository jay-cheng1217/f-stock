"""Independent E: fixed daily rows, repaired Q2 baseline, historical BS only."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
import time

import duckdb
import numpy as np
import pandas as pd

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.stage_q2_financial_reconciliation import receipt


def run(base, historical, q2, daily_db, output, *, retained_manifest=None, fixed_raw=None):
    base, output = base.resolve(), output.resolve()
    if output.exists() or not output.is_relative_to(base / "output"):
        raise ValueError("Use a new isolated output directory")
    history = json.loads(historical.read_text(encoding="utf-8"))
    if len(history["reports"]) != 25 or any(not r["status"].startswith("staged") for r in history["reports"]):
        raise ValueError("All 25 historical quarters must have frozen candidates")
    q2_report = json.loads(q2.read_text(encoding="utf-8"))
    retained = {}
    if retained_manifest:
        data = json.loads(retained_manifest.read_text(encoding="utf-8"))
        if len(data["reports"]) != 26 or data["failed_quarters"]:
            raise ValueError("All 26 retained supplements must be verified")
        retained = {(r["year"], r["quarter"]): r for r in data["reports"]}
    output.mkdir(parents=True)
    snapshots = []
    def copy_verified(source, target, expected=None):
        if expected and receipt(source)["sha256"] != expected:
            raise ValueError("Frozen quarterly source changed")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        snapshots.append(receipt(target))
    for report in history["reports"]:
        for branch, key in (("before", "before"), ("after", "candidate")):
            item = (retained[(report["year"], report["quarter"])]["candidate"] if branch == "after" else report["candidate"]) if retained else report[key]
            copy_verified(Path(item["path"]), output / branch / "資產負債" / Path(item["path"]).name, item["sha256"])
    for branch in ("before", "after"):
        item = retained[(2026, 2)]["candidate"] if retained and branch == "after" else q2_report["reports"]["bs"]["candidate"]
        copy_verified(Path(item["path"]), output / branch / "資產負債" / "bs_2026Q2.csv", item["sha256"])
    for path in sorted((base / "季報財務").glob("financial_*.csv")):
        if path.name == "financial_2026Q2.csv":
            item = q2_report["reports"]["financial"]["candidate"]
            copy_verified(Path(item["path"]), output / "financial" / path.name, item["sha256"])
        else:
            copy_verified(path, output / "financial" / path.name)
    if fixed_raw:
        raw = pd.read_csv(fixed_raw, dtype={"Ticker": str}, parse_dates=["Date"])
    else:
        conn = duckdb.connect(str(daily_db), read_only=True)
        try:
            raw = conn.execute('''SELECT _ab_ticker AS Ticker, Date, Close FROM daily_k
                WHERE TRY_CAST(Date AS DATE) <= DATE '2026-09-04'
                QUALIFY ROW_NUMBER() OVER (PARTITION BY _ab_ticker ORDER BY _ab_row DESC)=1
                ORDER BY _ab_ticker''').df()
        finally:
            conn.close()
    raw.Ticker = raw.Ticker.astype(str)
    raw.to_csv(output / "fixed_latest_raw.csv", index=False, encoding="utf-8-sig")
    from ml.features import balance_sheet as module
    original_reader = module._read_quarter_csv
    io_cache = {}
    def memoized_reader(path, *, source, required):
        # Pure I/O memoization, all files are frozen above. Feature formulas and
        # available-date join remain the actual canonical function, unchanged.
        key = (path, source, tuple(sorted(required)))
        if key not in io_cache:
            io_cache[key] = original_reader(path, source=source, required=required)
        return io_cache[key]
    module._read_quarter_csv = memoized_reader
    module._BS_CACHE.clear()
    start = time.monotonic()
    rows, changes = [], []
    try:
        for row in raw.itertuples(index=False):
            daily = pd.DataFrame({"Date": [row.Date], "Close": [row.Close]})
            daily_sha = hashlib.sha256(daily.to_csv(index=False).encode()).hexdigest()
            outcomes = {}
            for branch in ("before", "after"):
                frame = module.compute_balance_sheet_features(daily, row.Ticker, str(output / branch / "資產負債"), str(output / "financial"))
                outcomes[branch] = frame.iloc[-1]
                rows.append({"branch": branch, "Ticker": row.Ticker, "Date": str(row.Date),
                             "raw_frame_sha256": daily_sha,
                             **{key: float(frame.iloc[-1][key]) for key in module.BALANCE_SHEET_FEATURE_COLS}})
            for key in module.BALANCE_SHEET_FEATURE_COLS:
                a, b = outcomes["before"][key], outcomes["after"][key]
                if not ((pd.isna(a) and pd.isna(b)) or a == b):
                    changes.append({"Ticker": row.Ticker, "Date": str(row.Date), "feature": key,
                                    "before": None if pd.isna(a) else float(a), "after": None if pd.isna(b) else float(b)})
    finally:
        module._read_quarter_csv = original_reader
        module._BS_CACHE.clear()
    pd.DataFrame(rows).to_csv(output / "latest_features.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(changes).to_csv(output / "latest_feature_changes.csv", index=False, encoding="utf-8-sig")
    report = {"status": "independent_F_source_feature_comparison_not_model_promotion" if retained else "independent_E_source_feature_comparison_not_model_promotion",
        "baseline": "E: repaired Q2 and 25 historical BS, retained old filings unchanged" if retained else "repaired Q2 BS+financial, prior BS unchanged",
        "alternative": "same baseline plus exact retained official corrections in all 26 quarters" if retained else "same baseline plus 25 historical BS candidates",
        "same_raw_daily_frame": True, "raw_source": str(daily_db.resolve()), "raw_receipt": receipt(output / "fixed_latest_raw.csv"),
        "rows": len(raw), "date_counts": raw.Date.astype(str).value_counts().to_dict(),
        "changed_tickers": sorted({row["Ticker"] for row in changes}), "changed_cells": len(changes),
        "feature_change_counts": pd.Series([row["feature"] for row in changes]).value_counts().to_dict(),
        "changes": changes, "sources": snapshots, "elapsed_seconds": round(time.monotonic() - start, 2),
        "prob_edge": "N/A here; full E fixed-model inference is a separate acceptance",
        "limitations": ["Historical batch headings do not independently prove year, actual request year is retained; retained supplement has individual historical period/value anchors" if retained else "Historical headings do not independently prove year, actual request year is retained",
            "No retraining authorized; source repair is not model training acceptance" if retained else "Some omitted historical filings retain old identity violations; historical model training remains blocked",
            "Knowledge date of current official revisions is 2026-09-06, not historical publication dates"]}
    (output / "manifest.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("status", "rows", "changed_tickers", "changed_cells", "feature_change_counts", "elapsed_seconds")}, ensure_ascii=True), flush=True)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-dir", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--historical-manifest", type=Path, required=True)
    parser.add_argument("--q2-manifest", type=Path, required=True)
    parser.add_argument("--daily-db", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--retained-manifest", type=Path)
    parser.add_argument("--fixed-raw", type=Path)
    args = parser.parse_args()
    run(args.base_dir, args.historical_manifest, args.q2_manifest, args.daily_db, args.output_dir,
        retained_manifest=args.retained_manifest, fixed_raw=args.fixed_raw)
