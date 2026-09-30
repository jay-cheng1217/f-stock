"""Stage audited identifier repair; preserve price bars and all other fields."""
from pathlib import Path
import csv
import hashlib
import io
import json
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.audit_margin_identifier_history import compare, digest
from scripts.enrich_new_stock_sources import MARGIN_COLS
from ml.features.institutional import compute_institutional_features, INSTITUTIONAL_FEATURE_COLS
from ml.universe import filter_stock_universe


def patch_cells(raw, deltas):
    before = pd.read_csv(io.BytesIO(raw), dtype={"Date": str})
    if before.Date.duplicated().any() or deltas.duplicated(["Date", "column"]).any():
        raise ValueError("Ambiguous repair key")
    if not set(deltas.column).issubset(MARGIN_COLS) or not set(deltas.Date).issubset(before.Date):
        raise ValueError("Repair touches non-margin field or absent price bar")
    expected = before.copy().set_index("Date", drop=False)
    updates = {}
    for row in deltas.itertuples(index=False):
        old = expected.at[row.Date, row.column]
        if not ((pd.isna(old) and pd.isna(row.old)) or old == row.old):
            raise ValueError("Original cell changed")
        if not np.isfinite(row.new) or row.new < 0:
            raise ValueError("Official balance must be observed and nonnegative")
        expected.at[row.Date, row.column] = row.new
        updates.setdefault(row.Date, {})[row.column] = row.new
    bom = b"\xef\xbb\xbf" if raw.startswith(b"\xef\xbb\xbf") else b""
    lines = raw[len(bom):].splitlines(keepends=True)
    header = next(csv.reader([lines[0].decode("utf-8").rstrip("\r\n")]))
    if header[0] != "Date":
        raise ValueError("Date must be the first physical column")
    changed_rows = set()
    for idx, line in enumerate(lines[1:], 1):
        day = line.split(b",", 1)[0].decode("utf-8").strip('"')
        if day not in updates:
            continue
        ending = "\r\n" if line.endswith(b"\r\n") else "\n" if line.endswith(b"\n") else ""
        cells = next(csv.reader([line.decode("utf-8").rstrip("\r\n")]))
        if len(cells) != len(header):
            raise ValueError("Malformed physical CSV row")
        for col, value in updates[day].items():
            cells[header.index(col)] = format(value, ".15g")
        writer = io.StringIO(newline="")
        csv.writer(writer, lineterminator=ending).writerow(cells)
        lines[idx] = writer.getvalue().encode("utf-8")
        changed_rows.add(day)
    if changed_rows != set(updates):
        raise ValueError("Physical repair did not match every target day")
    candidate = bom + b"".join(lines)
    after = pd.read_csv(io.BytesIO(candidate), dtype={"Date": str})
    pd.testing.assert_frame_equal(expected.reset_index(drop=True), after, check_exact=True, check_dtype=False)
    pd.testing.assert_frame_equal(before.drop(columns=MARGIN_COLS), after.drop(columns=MARGIN_COLS), check_exact=True)
    return candidate, before, after


def main():
    audit = ROOT / "output/margin_identifier_history_20260906"
    report_path = audit / "report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if report["status"] != "AUDIT_COMPLETE":
        raise ValueError("Read-only audit did not complete")
    if any(digest(Path(r["path"])) != r["sha256"] for r in report["inputs"]):
        raise ValueError("Audited production inputs changed")
    out = ROOT / "output/margin_identifier_history_staged_20260906"
    if out.exists():
        raise ValueError("Evidence stage already exists")
    (out / "daily_k").mkdir(parents=True)
    changes = pd.read_csv(audit / "cell_changes.csv", dtype={"Ticker": str, "Date": str})
    official = pd.read_csv(audit / "official_selected.csv", dtype={"Ticker": str, "Date": str})
    files, feature_ab = [], []
    for ticker, delta in changes[changes.scope.eq("daily_csv")].groupby("Ticker"):
        target = ROOT / "日K資料" / f"{ticker}.csv"
        candidate, before, after = patch_cells(target.read_bytes(), delta)
        path = out / "daily_k" / target.name
        path.write_bytes(candidate)
        a, b = compute_institutional_features(before), compute_institutional_features(after)
        columns = [col for col in INSTITUTIONAL_FEATURE_COLS if col in a]
        different = ~(a[columns].eq(b[columns]) | (a[columns].isna() & b[columns].isna()))
        stock_universe = bool(filter_stock_universe([ticker]))
        feature_ab.append({"ticker": ticker, "stock_universe": stock_universe, "latest_date": before.Date.iloc[-1],
                           "columns_compared": len(columns), "latest_changed_cells": int(different.iloc[-1].sum()),
                           "historical_changed_rows": int(different.any(axis=1).sum()),
                           "last_affected_date": before.loc[different.any(axis=1), "Date"].max() if different.any(axis=None) else None})
        files.append({"source": str(path), "target": target.relative_to(ROOT).as_posix(),
                      "before_sha256": digest(target), "after_sha256": digest(path)})
    merged_path = ROOT / "清理後資料/cleaned_all_margin_data.csv"
    merged = pd.read_csv(merged_path, dtype={"Ticker": str, "Date": str})
    mask = merged.Ticker.isin(report["tickers"]) & merged.Date.isin(official.Date.unique())
    untouched = merged.loc[~mask]
    merged_after = pd.concat([untouched, official[merged.columns]], ignore_index=True)
    if merged_after.duplicated(["Ticker", "Date"]).any():
        raise ValueError("Merged candidate has duplicate keys")
    candidate = out / merged_path.name
    merged_after.to_csv(candidate, index=False, encoding="utf-8-sig")
    reread = pd.read_csv(candidate, dtype={"Ticker": str, "Date": str})
    pd.testing.assert_frame_equal(untouched.reset_index(drop=True), reread.iloc[:len(untouched)].reset_index(drop=True), check_exact=True)
    check, delta = compare(reread.loc[reread.Ticker.isin(report["tickers"]) & reread.Date.isin(official.Date.unique())], official, "candidate")
    if delta or check["local_rows_without_official_key"] or check["official_rows_without_local_key"]:
        raise ValueError("Candidate table differs from audited official keys/values")
    files.append({"source": str(candidate), "target": merged_path.relative_to(ROOT).as_posix(),
                  "before_sha256": digest(merged_path), "after_sha256": digest(candidate)})
    affected = [r for r in feature_ab if r["stock_universe"] and r["latest_changed_cells"]]
    result = {"status": "STAGED_NOT_PROMOTED", "daily_files": len(feature_ab), "daily_changed_cells": int(changes.scope.eq("daily_csv").sum()),
              "merged_before_rows": len(merged), "merged_after_rows": len(merged_after),
              "feature_ab": feature_ab, "latest_stock_universe_changes": affected,
              "non_margin_value_changes": 0, "merged_unrelated_value_changes": 0,
              "scope": "Only audited identifier keys and existing observed price bars; no training or strategy changes"}
    result_path = out / "validation.json"
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    plan = {"label": "margin_identifier_history_20260906", "evidence": [
        {"path": str(p), "sha256": digest(p)} for p in (report_path, audit / "cell_changes.csv", audit / "official_selected.csv", result_path)], "files": files}
    (out / "promotion_plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"files": len(files), "latest_stock_changes": affected, "output": str(out)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
