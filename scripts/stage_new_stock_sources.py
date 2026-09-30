"""Extract missing-company official OHLCV into isolated output; never promote."""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
from pathlib import Path
import sys
from datetime import datetime

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.latest_bar_coverage import CoverageContractError, parse_official_tape


def extract_rows(tape, companies, day):
    """Return real official bars at/after listing plus explicit missing evidence."""
    bars, absent = [], []
    for company in companies:
        ticker = company["ticker"]
        listing = datetime.strptime(company["listing_date_raw"], "%Y%m%d").date().isoformat()
        if day < listing:
            continue
        row = tape.get(ticker)
        if row is None or row["state"] != "official_bar" or row["volume"] == 0:
            state = "absent_from_tape" if row is None else (
                "official_zero_volume" if row["volume"] == 0 else row["state"])
            absent.append({"ticker": ticker, "date": day, "state": state})
            continue
        bars.append({"Ticker": ticker, "Date": day, "Open": row["open"], "High": row["high"],
                     "Low": row["low"], "Close": row["close"], "Volume": int(row["volume"])})
    return bars, absent


def stage(*, base_dir, output_dir, candidates_path, target_date):
    base, out = Path(base_dir).resolve(), Path(output_dir).resolve()
    if not out.is_relative_to(base / "output") or out == base / "output":
        raise ValueError("Staging must be a named subdirectory of repository output/")
    if out.exists():
        raise ValueError("Use a new output directory; never overwrite an existing stage")
    candidates = json.loads(Path(candidates_path).read_text(encoding="utf-8"))["rows"]
    companies = [r for r in candidates if r.get("security_scope") == "listed_company"
                 and r.get("company_metadata_found") and not r.get("is_etf") and not r.get("is_retired")]
    if not companies or len({r["ticker"] for r in companies}) != len(companies):
        raise CoverageContractError("Missing or duplicated company candidates")
    source = base / "logs/official_daily_history_cache"
    dates = sorted({p.name[:8] for p in (source / "twse").glob("*.json.gz")
                    if p.name[:8] <= target_date.replace("-", "")}
                   | {p.name[:8] for p in (source / "tpex").glob("*.json.gz")
                      if p.name[:8] <= target_date.replace("-", "")})
    rows, absent, errors, receipts = [], [], [], []
    for i, day8 in enumerate(dates):
        day = datetime.strptime(day8, "%Y%m%d").date().isoformat()
        tape = {}
        try:
            for market in ["twse", "tpex"]:
                path = source / market / (day8 + ".json.gz")
                stat = path.stat()
                raw = path.read_bytes()
                parsed = parse_official_tape(json.loads(gzip.decompress(raw)), market, day)
                if set(tape) & set(parsed):
                    raise CoverageContractError("Cross-market duplicated ticker")
                tape.update(parsed)
                if (path.stat().st_mtime_ns, path.stat().st_size) != (stat.st_mtime_ns, stat.st_size):
                    raise CoverageContractError("Tape changed while reading")
                receipts.append({"path": str(path.relative_to(base)), "sha256": hashlib.sha256(raw).hexdigest(),
                                 "size": len(raw), "mtime_ns": stat.st_mtime_ns, "rows": len(parsed), "date": day})
            actual, missing = extract_rows(tape, companies, day)
            rows.extend(actual)
            absent.extend(missing)
        except (CoverageContractError, ValueError, OSError) as exc:
            errors.append({"date": day, "error": str(exc)})
        if (i + 1) % 200 == 0:
            print(f"validated {i + 1}/{len(dates)} tape pairs; bars={len(rows)}, source_errors={len(errors)}", flush=True)
    out.mkdir(parents=True)
    daily = out / "daily_k"
    daily.mkdir()
    fields = ["Date", "Open", "High", "Low", "Close", "Volume"]
    outputs = []
    for company in companies:
        ticker = company["ticker"]
        selected = [r for r in rows if r["Ticker"] == ticker]
        path = daily / (ticker + ".csv")
        if selected:
            with path.open("w", encoding="utf-8-sig", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
                writer.writeheader()
                writer.writerows(selected)
        outputs.append({"ticker": ticker, "listing_date_raw": company["listing_date_raw"],
                        "rows": len(selected), "first_date": selected[0]["Date"] if selected else None,
                        "last_date": selected[-1]["Date"] if selected else None,
                        "sha256": hashlib.sha256(path.read_bytes()).hexdigest() if selected else None,
                        "path": str(path), "size": path.stat().st_size if selected else 0})
    manifest = {"schema_version": 1, "target_date": target_date, "status": "fail" if errors else "staged",
                "scope": "Official unadjusted source OHLCV only; no feature/model eligibility or promotion",
                "companies": len(companies), "rows": len(rows), "tape_pairs": len(dates),
                "source_errors": errors, "outputs": outputs, "sources": receipts,
                "no_bar_evidence": absent, "excluded": [r["ticker"] for r in candidates if r not in companies]}
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in manifest.items() if k not in {"sources", "outputs", "no_bar_evidence"}}, ensure_ascii=True))
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-dir", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--target-date", required=True)
    args = parser.parse_args()
    result = stage(base_dir=args.base_dir, output_dir=args.output_dir,
                   candidates_path=args.candidates, target_date=args.target_date)
    return 1 if result["source_errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
