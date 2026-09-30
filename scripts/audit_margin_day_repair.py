"""Read-only source/feature impact audit; stage exact margin-day repair candidates."""
from __future__ import annotations

import argparse
from collections import Counter
import csv
import hashlib
import io
import json
from pathlib import Path
import sys
import time

import numpy as np
import pandas as pd

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.enrich_new_stock_sources import MARGIN_COLS, checked_source, parse_raw_margin
from scripts.latest_bar_coverage import CoverageContractError


def receipt(path, raw=None):
    raw = path.read_bytes() if raw is None else raw
    return {"path": str(path), "sha256": hashlib.sha256(raw).hexdigest(),
            "size": len(raw), "mtime_ns": path.stat().st_mtime_ns}


def same(a, b):
    return (pd.isna(a) and pd.isna(b)) or a == b


def scalar(value):
    return None if pd.isna(value) else float(value)


def parse_complete_twse_day(path, day):
    # First validate the official date/header/credible numeric security coverage.
    parse_raw_margin(path, "twse", day)
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    start = next(i for i, line in enumerate(lines) if "代號" in line and "名稱" in line)
    raw = pd.read_csv(io.StringIO("\n".join(lines[start:])), dtype=str)
    raw.columns = raw.columns.str.strip()
    raw = raw[["代號", "今日餘額", "今日餘額.1"]].copy()
    raw.columns = ["Ticker", *MARGIN_COLS]
    raw.Ticker = raw.Ticker.astype(str).str.replace("=", "", regex=False).str.replace('"', "", regex=False).str.strip()
    raw = raw[raw.Ticker.str.fullmatch(r"\d{4,6}[A-Z]?", na=False)].copy()
    raw["Date"] = day
    return checked_source(raw, MARGIN_COLS, "complete TWSE official day")


def classify_value(old, new):
    if pd.isna(old):
        return "unknown_to_observed_zero" if new == 0 else "unknown_to_observed_nonzero"
    if same(old, new):
        return "already_equal"
    return "incorrect_zero" if old == 0 else "incorrect_nonzero"


def patch_daily_bytes(raw, patch):
    """Return a patched frame; never write. Reject stale bytes or old cell values."""
    if hashlib.sha256(raw).hexdigest() != patch["source_sha256"]:
        raise CoverageContractError("Stale daily CSV source hash")
    frame = pd.read_csv(io.BytesIO(raw), dtype={"Date": str})
    if not {"Date", *MARGIN_COLS} <= set(frame.columns):
        raise CoverageContractError("Daily margin schema missing")
    mask = frame.Date.eq(patch["Date"])
    if mask.sum() != 1:
        raise CoverageContractError("Repair must match exactly one existing price bar")
    idx = frame.index[mask][0]
    out = frame.copy()
    for col in MARGIN_COLS:
        old = np.nan if patch["old"][col] is None else patch["old"][col]
        if not same(frame.loc[idx, col], old):
            raise CoverageContractError("Stale daily CSV old cell value")
        new = patch["new"][col]
        if new is None or not np.isfinite(new) or new < 0:
            raise CoverageContractError("Official balance must be observed, finite and nonnegative")
        out.loc[idx, col] = new
    pd.testing.assert_frame_equal(frame.drop(columns=MARGIN_COLS), out.drop(columns=MARGIN_COLS))
    return out


def materialize_daily_patch(raw, patch):
    """Preserve all other lines and non-margin cell text, including float precision."""
    expected = patch_daily_bytes(raw, patch)
    bom = b"\xef\xbb\xbf" if raw.startswith(b"\xef\xbb\xbf") else b""
    lines = raw[len(bom):].splitlines(keepends=True)
    columns = next(csv.reader([lines[0].decode("utf-8").rstrip("\r\n")]))
    if columns[0] != "Date":
        raise CoverageContractError("Physical daily patch requires Date as first column")
    prefixes = [(patch["Date"] + ",").encode(), ('"' + patch["Date"] + '",').encode()]
    matches = [i for i, line in enumerate(lines[1:], 1) if any(line.startswith(p) for p in prefixes)]
    if len(matches) != 1:
        raise CoverageContractError("Physical patch must match one line")
    idx = matches[0]
    line = lines[idx]
    ending = "\r\n" if line.endswith(b"\r\n") else ("\n" if line.endswith(b"\n") else "")
    cells = next(csv.reader([line.decode("utf-8").rstrip("\r\n")]))
    if len(cells) != len(columns):
        raise CoverageContractError("Physical daily row width mismatch")
    for col in MARGIN_COLS:
        cells[columns.index(col)] = format(patch["new"][col], ".15g")
    stream = io.StringIO(newline="")
    csv.writer(stream, lineterminator=ending).writerow(cells)
    lines[idx] = stream.getvalue().encode("utf-8")
    result = bom + b"".join(lines)
    actual = pd.read_csv(io.BytesIO(result), dtype={"Date": str})
    pd.testing.assert_frame_equal(expected, actual, check_dtype=False, check_exact=True)
    return result


def materialize_candidates(base, audit_dir, evidence_paths):
    """Stage physical CSV candidates and a generic verified_source_promotion plan."""
    base, audit_dir = Path(base).resolve(), Path(audit_dir).resolve()
    daily = audit_dir / "daily_k"
    if not audit_dir.is_relative_to(base / "output") or daily.exists():
        raise ValueError("Use an audited isolated output directory without daily candidates")
    report = json.loads((audit_dir / "validation.json").read_text(encoding="utf-8"))
    daily.mkdir()
    files = []
    for count, patch in enumerate(report["daily_csv"]["patches"], 1):
        target = base / "日K資料" / (patch["Ticker"] + ".csv")
        candidate = daily / target.name
        candidate.write_bytes(materialize_daily_patch(target.read_bytes(), patch))
        files.append({"source": str(candidate), "target": target.relative_to(base).as_posix(),
                      "before_sha256": patch["source_sha256"], "after_sha256": receipt(candidate)["sha256"]})
        if count % 200 == 0:
            print(json.dumps({"materialized_daily_candidates": count}), flush=True)
    original_receipts = {Path(r["path"]).name: r for r in report["source_receipts"]}
    # Two raw receipts have the same filename, so select the actual production path.
    original_raw = next(r for r in report["source_receipts"] if Path(r["path"]).parent == base / "原始融資資料" and Path(r["path"]).suffix == ".json")
    replacement_raw = next(r for r in report["source_receipts"] if Path(r["path"]).suffix == ".json" and Path(r["path"]).parent != base / "原始融資資料")
    merged_before = original_receipts["cleaned_all_margin_data.csv"]
    for source, target, before, after in [
        (replacement_raw["path"], original_raw["path"], original_raw["sha256"], replacement_raw["sha256"]),
        (str(audit_dir / "cleaned_all_margin_data.csv"), merged_before["path"], merged_before["sha256"], receipt(audit_dir / "cleaned_all_margin_data.csv")["sha256"]),
    ]:
        files.append({"source": str(Path(source).resolve()), "target": Path(target).relative_to(base).as_posix(),
                      "before_sha256": before, "after_sha256": after})
    plan = {"label": "margin_day_20260320", "evidence": [receipt(Path(p)) for p in evidence_paths], "files": files,
            "scope": "Only 2026-03-20 official margin/short balance cells and full dual-market day; no model/DB promotion"}
    plan_path = audit_dir / "promotion_plan.json"
    plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return plan_path


def replace_merged_day(frame, replacement, day):
    """Replace a whole verified dual-market day, retaining string security codes."""
    if not frame.Ticker.map(lambda v: isinstance(v, str)).all():
        raise CoverageContractError("Ticker identifiers must already be strings")
    replacement = checked_source(replacement, MARGIN_COLS, "dual-market day")
    if set(replacement.Date) != {day} or replacement[MARGIN_COLS].isna().any().any():
        raise CoverageContractError("Invalid replacement date or unknown official balances")
    untouched = frame[frame.Date.ne(day)].copy()
    out = pd.concat([untouched, replacement[frame.columns]], ignore_index=True)
    pd.testing.assert_frame_equal(untouched.reset_index(drop=True), out.iloc[:len(untouched)].reset_index(drop=True))
    return out


def audit(base, day, tpex_raw, output):
    from ml.dataset import _asof_eligibility_mask
    from ml.features.institutional import compute_institutional_features, INSTITUTIONAL_FEATURE_COLS

    started = time.monotonic()
    base, output = base.resolve(), output.resolve()
    if output.exists() or not output.is_relative_to(base / "output") or output == base / "output":
        raise ValueError("Use a new isolated output subdirectory")
    output.mkdir(parents=True)
    sources = []
    twse_path = base / "原始融資資料" / f"raw_margin_twse_{day.replace('-', '')}.csv"
    old_tpex = base / "原始融資資料" / f"raw_margin_tpex_{day.replace('-', '')}.json"
    official = {}
    for market, path in [("twse", twse_path), ("tpex", tpex_raw)]:
        official[market] = parse_complete_twse_day(path, day) if market == "twse" else parse_raw_margin(path, market, day)
        sources.append(receipt(path))
    sources.append(receipt(old_tpex))
    replacement = pd.concat(official.values(), ignore_index=True)
    if replacement.duplicated(["Ticker", "Date"]).any():
        raise CoverageContractError("Dual-market duplicate identifier/date")
    replacement.to_csv(output / "replacement_merged_day.csv", index=False, encoding="utf-8-sig")
    merged_path = base / "清理後資料/cleaned_all_margin_data.csv"
    merged_raw = merged_path.read_bytes()
    sources.append(receipt(merged_path, merged_raw))
    merged = pd.read_csv(io.BytesIO(merged_raw), dtype={"Ticker": str, "Date": str})
    current_day = merged[merged.Date.eq(day)].copy()
    merged_after = replace_merged_day(merged, replacement, day)
    merged_after.to_csv(output / "cleaned_all_margin_data.csv", index=False, encoding="utf-8-sig")
    old_by_key = current_day.set_index("Ticker")
    official_by_key = replacement.set_index("Ticker")
    merged_changes, merged_classes = [], Counter()
    for ticker, row in official_by_key.iterrows():
        if ticker not in old_by_key.index:
            merged_classes["missing_row"] += 1
            merged_changes.append({"Ticker": ticker, "Date": day, "kind": "missing_row",
                                   "new": {c: scalar(row[c]) for c in MARGIN_COLS}})
            continue
        old = old_by_key.loc[ticker]
        for col in MARGIN_COLS:
            kind = classify_value(old[col], row[col])
            merged_classes[kind] += 1
            if kind != "already_equal":
                merged_changes.append({"Ticker": ticker, "Date": day, "column": col, "kind": kind,
                                       "old": scalar(old[col]), "new": scalar(row[col])})
    removed = sorted(set(current_day.Ticker) - set(replacement.Ticker))
    # Quantify possible historic leading-zero collisions. Equality is evidence of
    # risk, not a complete raw-source reconciliation of every historical day.
    leading = sorted(t for t in replacement.Ticker if t.startswith("0") and t.isdigit())
    collision_inventory = []
    historical_groups = dict(tuple(merged[merged.Date.ge("2020-01-01")].groupby("Ticker")))
    for ticker in leading:
        alias = str(int(ticker))
        hist = historical_groups.get(alias)
        if hist is not None and len(hist):
            collision_inventory.append({"official_ticker": ticker, "stripped_alias": alias,
                "rows_requiring_raw_reconciliation": len(hist), "first": hist.Date.min(),
                "last": hist.Date.max(), "alias_is_current_official_security": alias in official_by_key.index})
    source_classes, cell_classes = Counter(), Counter()
    date_changes, feature_changes = Counter(), Counter()
    patches, ticker_receipts, changed_records, missing = [], [], [], []
    latest_changed, asof_changed, eligible_changed = [], [], Counter()
    market_classes = {market: Counter() for market in official}
    current_date = "2026-09-04"
    all_official = pd.concat([frame.assign(market=market) for market, frame in official.items()], ignore_index=True)
    for num, row in enumerate(all_official.to_dict("records"), 1):
        ticker = row["Ticker"]
        market = row["market"]
        path = base / "日K資料" / f"{ticker}.csv"
        if not path.exists():
            source_classes["no_csv"] += 1
            market_classes[market]["no_csv"] += 1
            missing.append({"Ticker": ticker, "market": market, "kind": "no_csv"})
            continue
        raw = path.read_bytes()
        rec = receipt(path, raw)
        frame = pd.read_csv(io.BytesIO(raw), dtype={"Date": str})
        if frame.Date.duplicated().any() or not frame.Date.is_monotonic_increasing:
            raise CoverageContractError(f"Invalid daily key ordering: {ticker}")
        selected = frame[frame.Date.eq(day)]
        if selected.empty:
            source_classes["no_price_bar"] += 1
            market_classes[market]["no_price_bar"] += 1
            missing.append({"Ticker": ticker, "market": market, "kind": "no_price_bar", "last": frame.Date.max()})
            ticker_receipts.append(rec)
            continue
        if len(selected) != 1:
            raise CoverageContractError(f"Duplicate daily date: {ticker}")
        if not set(MARGIN_COLS) <= set(frame.columns):
            source_classes["margin_schema_missing"] += 1
            market_classes[market]["margin_schema_missing"] += 1
            missing.append({"Ticker": ticker, "market": market, "kind": "margin_schema_missing",
                            "columns": [c for c in MARGIN_COLS if c not in frame.columns]})
            ticker_receipts.append(rec)
            continue
        old = selected.iloc[0]
        patch = {"Ticker": ticker, "Date": day, "source_sha256": rec["sha256"],
                 "old": {c: scalar(old[c]) for c in MARGIN_COLS},
                 "new": {c: scalar(row[c]) for c in MARGIN_COLS}}
        for col in MARGIN_COLS:
            cell_classes[classify_value(old[col], row[col])] += 1
        if all(same(old[c], row[c]) for c in MARGIN_COLS):
            source_classes["already_equal"] += 1
            market_classes[market]["already_equal"] += 1
            ticker_receipts.append(rec)
            continue
        source_classes["repairable_existing_bar"] += 1
        market_classes[market]["repairable_existing_bar"] += 1
        after = patch_daily_bytes(raw, patch)
        patches.append(patch)
        # Both branches use this one byte-frozen source frame; only two cells differ.
        before_features = compute_institutional_features(frame)
        after_features = compute_institutional_features(after)
        cols = [c for c in INSTITUTIONAL_FEATURE_COLS if c in before_features.columns]
        a, b = before_features[cols], after_features[cols]
        different = ~(a.eq(b) | (a.isna() & b.isna()))
        rows = different.any(axis=1)
        for idx in different.index[rows]:
            changed_cols = different.columns[different.loc[idx]].tolist()
            feature_changes.update(changed_cols)
            date_changes[frame.loc[idx, "Date"]] += 1
            changed_records.append({"Ticker": ticker, "Date": frame.loc[idx, "Date"], "features": changed_cols})
        eligible = _asof_eligibility_mask(frame)
        eligible_changed[ticker] = int((rows & eligible).sum())
        if rows.iloc[-1]:
            latest_changed.append({"Ticker": ticker, "Date": frame.Date.iloc[-1]})
        if (rows & frame.Date.eq(current_date)).any():
            asof_changed.append(ticker)
        rec.update({"ticker": ticker, "rows": len(frame), "last_date": frame.Date.iloc[-1],
                    "historical_changed_rows": int(rows.sum()), "canonical_asof_eligible_changed_rows": int((rows & eligible).sum()),
                    "latest_feature_cell_deltas": int(different.iloc[-1].sum()),
                    "has_20260904_bar": bool(frame.Date.eq(current_date).any())})
        ticker_receipts.append(rec)
        if num % 100 == 0:
            print(json.dumps({"processed": num, "elapsed_seconds": round(time.monotonic() - started, 2)}), flush=True)
    result = {"schema_version": 1, "status": "staged_not_promoted", "date": day,
        "official_rows": {k: len(v) for k, v in official.items()}, "source_receipts": sources,
        "merged": {"before_rows": len(merged), "after_rows": len(merged_after),
            "before_day_rows": len(current_day), "after_day_rows": len(replacement),
            "cell_and_missing_row_classification": dict(merged_classes), "changes": merged_changes,
            "removed_wrong_keys": removed, "other_date_value_changes": 0},
        "daily_csv": {"scope": "every official TWSE/TPEx margin security on target date, including ETF codes",
            "classification": dict(source_classes), "by_market": {m: dict(c) for m, c in market_classes.items()}, "cell_classification": dict(cell_classes), "unpatched": missing,
            "patches": patches, "source_receipts": ticker_receipts, "nonmargin_value_changes": 0},
        "feature_ab": {"function": "ml.features.institutional.compute_institutional_features",
            "input": "one byte-frozen actual daily CSV frame per ticker for both branches",
            "changed_historical_ticker_dates": sum(date_changes.values()),
            "changed_historical_tickers": sum(n > 0 for n in (r.get("historical_changed_rows", 0) for r in ticker_receipts)),
            "changes_by_date": dict(sorted(date_changes.items())), "changes_by_feature": dict(feature_changes),
            "canonical_asof_eligible_changed_ticker_dates": sum(eligible_changed.values()),
            "actual_latest_changed_tickers": latest_changed, "asof": current_date,
            "asof_changed_tickers": asof_changed, "prob_edge": "N/A: no model prediction run; source/feature impact only"},
        "historical_alias_inventory": collision_inventory,
        "limitations": ["Historic alias counts are candidates, not confirmed raw/CSV value discrepancies.",
            "No production CSV, raw cache, merged table, DB, model, ledger or service was changed.",
            "Raw source repair changes historical research/training inputs; old model training is not retroactively corrected.",
            "This is canonical institutional-feature replay, not full model probability A/B or a new backtest."],
        "elapsed_seconds": round(time.monotonic() - started, 3)}
    (output / "manifest.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output / "feature_changed_rows.json").write_text(json.dumps(changed_records, ensure_ascii=False) + "\n", encoding="utf-8")
    result["candidate_receipts"] = [receipt(output / name) for name in ["replacement_merged_day.csv", "cleaned_all_margin_data.csv", "manifest.json", "feature_changed_rows.json"]]
    (output / "validation.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"daily": dict(source_classes), "cells": dict(cell_classes), "feature_ab": result["feature_ab"],
        "merged_counts": {k: v for k, v in result["merged"].items() if k not in ["changes", "removed_wrong_keys"]},
        "elapsed_seconds": result["elapsed_seconds"]}, ensure_ascii=True), flush=True)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-dir", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--day", required=True)
    parser.add_argument("--tpex-raw", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    audit(args.base_dir, args.day, args.tpex_raw, args.output_dir)
