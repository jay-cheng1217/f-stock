"""Exact-date source enrichment for isolated new-company history batches."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path
import re
import sys

import pandas as pd

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.latest_bar_coverage import CoverageContractError

FUND_COLS = ["Foreign_BuySell", "Trust_BuySell", "Dealer_BuySell"]
MARGIN_COLS = ["Margin_Balance", "Short_Balance"]
KEY = ["Ticker", "Date"]
OHLCV = ["Open", "High", "Low", "Close", "Volume"]


def checked_source(frame, columns, source):
    if not set(KEY + columns) <= set(frame.columns):
        raise CoverageContractError(f"Source schema mismatch: {source}")
    result = frame[KEY + columns].copy()
    result["Ticker"] = result.Ticker.astype(str).str.strip()
    result["Date"] = pd.to_datetime(result.Date, errors="raise").dt.strftime("%Y-%m-%d")
    for col in columns:
        # Unknown remains NaN, never converted to an observed zero.
        raw = result[col].astype(str).str.strip().str.replace(",", "", regex=False)
        raw = raw.replace({"": None, "--": None, "---": None, "nan": None, "None": None})
        result[col] = pd.to_numeric(raw, errors="raise")
    duplicates = result.duplicated(KEY, keep=False)
    if duplicates.any():
        conflicts = result[duplicates].groupby(KEY, dropna=False)[columns].nunique(dropna=False).gt(1).any(axis=1)
        if conflicts.any():
            raise CoverageContractError(f"Conflicting ticker/date source values: {source}")
        result = result.drop_duplicates(KEY)
    return result


def exact_enrich(bars, institutional, margin):
    if bars.duplicated(KEY).any():
        raise CoverageContractError("Duplicate staged OHLCV keys")
    original = bars[KEY + OHLCV].reset_index(drop=True)
    out = bars.merge(checked_source(institutional, FUND_COLS, "institutional"), on=KEY, how="left", validate="one_to_one")
    out = out.merge(checked_source(margin, MARGIN_COLS, "margin"), on=KEY, how="left", validate="one_to_one")
    pd.testing.assert_frame_equal(original, out[KEY + OHLCV].reset_index(drop=True))
    return out


def parse_raw_margin(path, market, day):
    """Use canonical balance columns, retaining invalid/missing as unknown."""
    if market == "tpex":
        obj = json.loads(path.read_text(encoding="utf-8-sig"))
        if obj.get("date") != day.replace("-", ""):
            raise CoverageContractError(f"TPEx margin source date mismatch: {path}")
        tables = obj.get("tables") or []
        if tables:
            table = tables[0]
            fields = [str(f).strip() for f in table.get("fields", [])]
            needed = ["代號", "資餘額", "券餘額"]
            if not set(needed) <= set(fields):
                raise CoverageContractError(f"TPEx margin named fields missing: {path}")
            raw = pd.DataFrame(table.get("data", []), columns=fields)[needed]
        else:
            raise CoverageContractError(f"Unrecognized TPEx margin historical schema: {path}")
    else:
        text = path.read_text(encoding="utf-8-sig")
        year, month, dom = map(int, day.split("-"))
        if not re.search(rf"{year - 1911}年0?{month}月0?{dom}日", text[:150]):
            raise CoverageContractError(f"TWSE margin source date mismatch: {path}")
        lines = text.splitlines()
        start = next((i for i, line in enumerate(lines) if "代號" in line and "名稱" in line), None)
        if start is None:
            raise CoverageContractError(f"TWSE margin header missing: {path}")
        raw = pd.read_csv(io.StringIO("\n".join(lines[start:])), dtype=str)
        raw.columns = raw.columns.str.strip()
        needed = ["代號", "今日餘額", "今日餘額.1"]
        if not set(needed) <= set(raw.columns):
            raise CoverageContractError(f"TWSE margin balance fields missing: {path}")
        raw = raw[needed].copy()
        raw["代號"] = raw["代號"].astype(str).str.replace("=", "", regex=False).str.replace('"', "", regex=False).str.strip()
        raw = raw[raw["代號"].str.fullmatch(r"\d{4,6}", na=False)]
    raw.columns = ["Ticker", *MARGIN_COLS]
    raw["Date"] = day
    if len(raw) < 50:
        raise CoverageContractError(f"Partial raw margin source: {path}")
    return checked_source(raw, MARGIN_COLS, str(path))


def enrich_stage(base_dir, source_dir, output_dir, raw_overrides=None):
    from ml.config import MIN_HISTORY_DAYS, MIN_AVG_VOLUME, MIN_PRICE

    base, source, out = Path(base_dir).resolve(), Path(source_dir).resolve(), Path(output_dir).resolve()
    if not out.is_relative_to(base / "output") or out == base / "output" or out.exists():
        raise ValueError("Use a new isolated output subdirectory")
    manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    frames = []
    for entry in manifest["outputs"]:
        path = Path(entry["path"])
        if hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"]:
            raise CoverageContractError(f"Staged OHLCV hash changed: {path}")
        frame = pd.read_csv(path)
        frame.insert(0, "Ticker", entry["ticker"])
        frames.append(frame)
    bars = pd.concat(frames, ignore_index=True)
    tickers = set(bars.Ticker)
    receipts, errors, funds = [], [], []

    def receipt(path):
        raw = path.read_bytes()
        receipts.append({"path": str(path), "sha256": hashlib.sha256(raw).hexdigest(), "size": len(raw), "mtime_ns": path.stat().st_mtime_ns})

    for path in sorted((base / "法人快取").glob("*.csv")):
        frame = pd.read_csv(path, dtype={"Ticker": str, "Date": str})
        selected = checked_source(frame[frame.Ticker.isin(tickers)], FUND_COLS, str(path))
        if not selected.empty:
            expected = path.stem[-8:]
            if set(selected.Date.str.replace("-", "", regex=False)) != {expected}:
                raise CoverageContractError(f"Institutional filename/date mismatch: {path}")
            receipt(path)
            funds.append(selected)
    institutional = checked_source(pd.concat(funds, ignore_index=True), FUND_COLS, "institutional all caches")
    margin_path = base / "清理後資料/cleaned_all_margin_data.csv"
    margin = pd.read_csv(margin_path, dtype={"Ticker": str, "Date": str})
    margin = checked_source(margin[margin.Ticker.isin(tickers)], MARGIN_COLS, str(margin_path))
    receipt(margin_path)
    current = bars[KEY].merge(margin, on=KEY, how="left", validate="one_to_one")
    missing_dates = sorted(current.loc[current[MARGIN_COLS].isna().any(axis=1), "Date"].unique())
    raw_frames = []
    for day in missing_dates:
        for market in ["twse", "tpex"]:
            path = base / "原始融資資料" / f"raw_margin_{market}_{day.replace('-', '')}.{'csv' if market == 'twse' else 'json'}"
            if raw_overrides is not None:
                replacement = Path(raw_overrides) / path.name
                if replacement.exists():
                    path = replacement
            try:
                raw = parse_raw_margin(path, market, day)
                receipt(path)
                raw_frames.append(raw[raw.Ticker.isin(tickers)])
            except (OSError, ValueError) as exc:
                errors.append({"path": str(path), "error": str(exc)})
    raw_margin = checked_source(pd.concat(raw_frames, ignore_index=True), MARGIN_COLS, "raw margin") if raw_frames else pd.DataFrame(columns=KEY + MARGIN_COLS)
    overlap = margin.merge(raw_margin, on=KEY, suffixes=("_merged", "_raw"))
    mismatches = []
    for col in MARGIN_COLS:
        bad = overlap[col + "_merged"].notna() & overlap[col + "_raw"].notna() & overlap[col + "_merged"].ne(overlap[col + "_raw"])
        if bad.any():
            mismatches.extend(overlap.loc[bad, KEY].assign(column=col).to_dict("records"))
    if mismatches:
        raise CoverageContractError(f"Raw/merged margin conflicts: {mismatches[:5]}")
    margin = margin.set_index(KEY).combine_first(raw_margin.set_index(KEY)).reset_index()
    enriched = exact_enrich(bars, institutional, margin)
    out.mkdir(parents=True)
    daily = out / "daily_k"
    daily.mkdir()
    outputs = []
    for ticker, frame in enriched.groupby("Ticker", sort=True):
        frame = frame.sort_values("Date")
        path = daily / (ticker + ".csv")
        frame.drop(columns="Ticker").to_csv(path, index=False, encoding="utf-8-sig")
        eligible = len(frame) >= MIN_HISTORY_DAYS and frame.Volume.tail(60).mean() >= MIN_AVG_VOLUME and frame.Close.iloc[-1] >= MIN_PRICE
        outputs.append({"ticker": ticker, "rows": len(frame), "path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                        "eligibility_gate_only": bool(eligible), "nonnull": frame[FUND_COLS + MARGIN_COLS].notna().sum().astype(int).to_dict(),
                        "last60_missing": frame.tail(60)[FUND_COLS + MARGIN_COLS].isna().sum().astype(int).to_dict(),
                        "latest_missing": [c for c in FUND_COLS + MARGIN_COLS if pd.isna(frame.iloc[-1][c])]})
    enriched.to_csv(out / "all_84_company_bars_enriched.csv", index=False, encoding="utf-8-sig")
    result = {"schema_version": 1, "status": "enriched_with_explicit_unknowns", "source_stage": str(source),
              "companies": len(outputs), "rows": len(enriched), "ohlcv_value_or_key_changes": 0,
              "source_errors": errors, "raw_merged_conflicts": mismatches, "raw_margin_rows": len(raw_margin),
              "nonnull": enriched[FUND_COLS + MARGIN_COLS].notna().sum().astype(int).to_dict(),
              "unknown": enriched[FUND_COLS + MARGIN_COLS].isna().sum().astype(int).to_dict(),
              "eligible_count": sum(r["eligibility_gate_only"] for r in outputs), "outputs": outputs, "sources": receipts,
              "limitations": ["Institutional values are canonical normalized caches, not a new raw exchange reconciliation.",
                  "No missing institutional or margin values were zero/forward filled.", "Source absence does not by itself prove margin ineligibility."]}
    (out / "manifest.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k not in ["outputs", "sources", "source_errors"]}, ensure_ascii=True))
    print("source_errors", len(errors))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-dir", default=Path(__file__).resolve().parents[1], type=Path)
    parser.add_argument("--source-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--raw-overrides", type=Path)
    args = parser.parse_args()
    result = enrich_stage(args.base_dir, args.source_dir, args.output_dir, args.raw_overrides)
    raise SystemExit(1 if result["source_errors"] else 0)
