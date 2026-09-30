"""Compare canonical latest valuation features from a single frozen source set.

Both branches share actual ticker-local daily dates from the frozen union DB.
Only the verified valuation-day bundle differs. Complete per-ticker histories
are retained; no feature formula, unknown value or historical date is invented.
"""
from pathlib import Path
import hashlib
import io
import json
import sys
import time

import duckdb
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ml.features import valuation
from ml.universe import filter_stock_universe
from scripts.stage_valuation_fingerprint_family import OUT, sha, save

DB = ROOT / "output/pipeline_acceptance_20260906/workspace/ab_union.duckdb"


def latest_features(group, day, cache_key):
    # This is exactly the canonical loader's concat output restricted to ticker.
    # The function applies its own sorting, rolling, casts, NaNs and as-of merge.
    valuation._VALUATION_CACHE[cache_key] = group
    return valuation.compute_valuation_features(pd.DataFrame({"Date": [day]}), str(group.Ticker.iloc[0]), cache_key)


def main():
    started = time.monotonic()
    result = json.loads((OUT / "reconciliation.json").read_text(encoding="utf-8"))
    if result["status"] != "STAGED_SOURCE_RECONCILIATION_COMPLETE":
        raise ValueError("Bounded official source reconciliation is incomplete")
    target = OUT / "feature_ab_v2"
    target.mkdir(exist_ok=False)
    # Path is fixed by the frozen A/B manifest, never a writable production DB.
    if not DB.is_file():
        raise ValueError(f"Frozen daily DB missing: {DB}")
    db_hash = sha(DB)
    with duckdb.connect(str(DB), read_only=True) as conn:
        daily = conn.execute('SELECT _ab_ticker AS Ticker, max(Date) AS Date FROM daily_k GROUP BY _ab_ticker ORDER BY _ab_ticker').fetchdf()
    if len(daily) != 2031 or daily.Ticker.duplicated().any():
        raise ValueError("Unexpected frozen source universe")
    daily.Ticker = daily.Ticker.astype(str)
    daily.Date = pd.to_datetime(daily.Date)
    daily.to_csv(target / "daily_dates.csv", index=False)
    replacement = {Path(r["target"]).name: r for r in result["files"]}
    branches, source_manifest = [[], []], []
    for path in sorted((ROOT / "估值資料").glob("valuation_*.csv")):
        raw = path.read_bytes()
        before_sha = hashlib.sha256(raw).hexdigest()
        before = pd.read_csv(io.BytesIO(raw), dtype={"Ticker": str})
        if not {"Ticker", "PE_Ratio", "PB_Ratio", "Dividend_Yield"}.issubset(before):
            raise ValueError(f"Invalid frozen source schema: {path}")
        after = before.copy()
        entry = {"path": str(path), "sha256": before_sha}
        if path.name in replacement:
            item = replacement[path.name]
            if before_sha != item["before_sha256"] or sha(item["source"]) != item["after_sha256"]:
                raise ValueError("Verified replacement no longer matches")
            after = pd.read_csv(item["source"], dtype={"Ticker": str})
            entry.update(candidate=item["source"], after_sha256=item["after_sha256"])
        for i, frame in enumerate((before, after)):
            frame = frame[frame.Ticker.isin(daily.Ticker)].copy()
            frame["val_date"] = pd.Timestamp(path.stem[-8:])
            branches[i].append(frame)
        source_manifest.append(entry)
    group_maps = [dict(tuple(pd.concat(frames, ignore_index=True).groupby("Ticker", sort=False))) for frames in branches]
    rows = []
    stock_tickers = set(filter_stock_universe(daily.Ticker))
    for count, row in enumerate(daily.itertuples(index=False), 1):
        values = []
        for branch, groups in zip(("before", "after"), group_maps):
            history = groups.get(row.Ticker)
            if history is None or history.empty:
                values.append(np.full(len(valuation.VALUATION_FEATURE_COLS), np.nan))
                continue
            data = latest_features(history, row.Date, str(target / branch))
            values.append(data[valuation.VALUATION_FEATURE_COLS].iloc[-1].to_numpy(float))
        equal = np.isclose(values[0], values[1], atol=0, rtol=0, equal_nan=True)
        item = {"ticker": row.Ticker, "date": row.Date.date().isoformat(), "stock_universe": row.Ticker in stock_tickers,
                "changed_cells": int((~equal).sum())}
        for i, col in enumerate(valuation.VALUATION_FEATURE_COLS):
            item["before_" + col] = None if np.isnan(values[0][i]) else float(values[0][i])
            item["after_" + col] = None if np.isnan(values[1][i]) else float(values[1][i])
        rows.append(item)
        if count % 200 == 0:
            print(json.dumps({"tickers": count, "total": len(daily), "seconds": round(time.monotonic() - started, 1)}), flush=True)
    changed_sources = [r["path"] for r in source_manifest if sha(r["path"]) != r["sha256"]]
    if changed_sources or sha(DB) != db_hash:
        raise ValueError("Source changed during feature comparison")
    pd.DataFrame(rows).to_csv(target / "latest_features.csv", index=False)
    payload = {"status": "FEATURE_AB_COMPLETE", "tickers": len(daily), "feature_count": len(valuation.VALUATION_FEATURE_COLS),
               "compared_cells": len(daily) * len(valuation.VALUATION_FEATURE_COLS), "changed_cells": sum(r["changed_cells"] for r in rows),
               "changed_tickers": [r for r in rows if r["changed_cells"]], "raw_daily_db_sha256": db_hash,
               "daily_dates_sha256": sha(target / "daily_dates.csv"), "source_manifest": source_manifest,
               "candidate_bundle_sha256": sha(OUT / "reconciliation.json"), "feature_function_sha256": sha(ROOT / "ml/features/valuation.py"),
               "seconds": round(time.monotonic() - started, 2),
               "scope": "Same frozen actual daily dates, full valuation history, exact canonical five-feature function; no model/return backtest or source publication"}
    save(target / "manifest.json", payload)
    plan = {"label": "valuation_fingerprint_family_20260906", "evidence": [
        {"path": str(p), "sha256": sha(p)} for p in (OUT / "plan.json", OUT / "reconciliation.json", target / "manifest.json", target / "latest_features.csv")],
        "files": result["files"]}
    save(OUT / "promotion_plan.json", plan)
    print(json.dumps({k: v for k, v in payload.items() if k != "source_manifest"}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
