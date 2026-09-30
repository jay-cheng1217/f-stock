"""Read-only final CSV/DB/snapshot/prediction/entry consistency acceptance."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import sys

import numpy as np
import pandas as pd
from bs4 import BeautifulSoup

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

OHLCV = ["Open", "High", "Low", "Close", "Volume"]


def file_receipt(path):
    path = Path(path)
    digest = hashlib.sha256()
    before = path.stat()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(2**20), b""):
            digest.update(chunk)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError(f"File changed while reading: {path}")
    return {"path": str(path.resolve()), "sha256": digest.hexdigest(), "bytes": after.st_size, "mtime_ns": after.st_mtime_ns}


def identity_frame(frame):
    frame = frame.copy()
    date_fields = [name for name in ("Date", "date", "source_date") if name in frame]
    if len(date_fields) > 1:
        primary = pd.to_datetime(frame[date_fields[0]], errors="coerce")
        if any(not primary.eq(pd.to_datetime(frame[name], errors="coerce")).all() for name in date_fields[1:]):
            raise ValueError("Date/date/source_date identities disagree")
    if "Ticker" not in frame and "ticker" in frame:
        frame = frame.rename(columns={"ticker": "Ticker"})
    if "Date" not in frame and date_fields:
        frame = frame.rename(columns={date_fields[0]: "Date"})
    if not {"Ticker", "Date"}.issubset(frame.columns):
        raise ValueError("Ticker/Date identity columns missing")
    frame["Ticker"] = frame.Ticker.astype(str).str.strip()
    frame["Date"] = pd.to_datetime(frame.Date, errors="coerce").dt.strftime("%Y-%m-%d")
    if frame.Date.isna().any() or not frame.Ticker.str.fullmatch(r"\d{4,6}").all():
        raise ValueError("Invalid ticker/date identity")
    if frame.duplicated(["Ticker", "Date"]).any():
        raise ValueError("Duplicate ticker/date identity")
    return frame


def compare_exact(expected, actual, columns, *, require_same_tickers=True):
    expected, actual = identity_frame(expected), identity_frame(actual)
    if expected.Ticker.duplicated().any() or actual.Ticker.duplicated().any():
        raise ValueError("Expected one latest row per ticker")
    left = expected.set_index("Ticker")
    right = actual.set_index("Ticker")
    missing = sorted(set(left.index) - set(right.index))
    extra = sorted(set(right.index) - set(left.index))
    common = sorted(set(left.index) & set(right.index))
    changes = []
    for ticker in common:
        for column in ["Date", *columns]:
            if column not in left or column not in right:
                raise ValueError(f"Comparison column missing: {column}")
            a, b = left.loc[ticker, column], right.loc[ticker, column]
            if column == "Date":
                equal = a == b
            else:
                a, b = float(a), float(b)
                equal = np.isfinite(a) and np.isfinite(b) and a == b
            if not equal:
                changes.append({"Ticker": ticker, "column": column,
                                "expected": str(a), "actual": str(b)})
    return {"expected_rows": len(expected), "actual_rows": len(actual),
            "missing_tickers": missing, "extra_tickers": extra, "mismatch_count": len(changes),
            "mismatches": changes, "ok": not changes and not missing and (not require_same_tickers or not extra)}


def load_prediction_for_audit(path, label, report):
    """Use certified bytes while keeping each product's own date contract."""
    path = Path(path)
    lineage_key = label + "_prediction_lineage"
    report["checks"][lineage_key] = False
    before = file_receipt(path)
    from ml.prediction_provenance import load_prediction_csv
    manifest_path = Path(str(path) + ".manifest.json")
    manifest_before = file_receipt(manifest_path) if manifest_path.is_file() else None
    loaded = load_prediction_csv(path, expected_model_slot=label,
                                read_csv_kwargs={"dtype": {"ticker": str, "Ticker": str}, "float_precision": "round_trip"})
    if loaded is None:
        raise ValueError(f"{label} prediction lineage rejected or absent")
    frame, certificate = loaded
    manifest_after = file_receipt(manifest_path)
    if manifest_before != manifest_after:
        raise ValueError(f"{label} prediction manifest changed during validation")
    report[lineage_key] = certificate
    report[label + "_prediction_manifest_receipt"] = manifest_after
    after = file_receipt(path)
    if before != after:
        raise ValueError(f"{label} prediction CSV changed during validation")
    report["checks"][lineage_key] = True
    return frame, after


def scan_daily_sources(root, as_of, snapshot=None):
    from ml.universe import _read_retired_tickers
    retired = _read_retired_tickers(root / "config/retired_tickers.csv")
    requests = {} if snapshot is None else dict(zip(snapshot.Ticker, snapshot.Date))
    paths = sorted(p for p in (root / "日K資料").glob("*.csv") if p.stem.isdigit() and p.stem not in retired)
    if not paths:
        raise ValueError("No accepted numeric source daily CSVs")
    latest, keyed, files, errors = [], [], [], []
    source_rows, duplicate_rows = 0, 0
    for path in paths:
        before = file_receipt(path)
        try:
            frame = pd.read_csv(path, usecols=["Date", *OHLCV], dtype={"Date": str}, float_precision="round_trip")
            frame["Date"] = pd.to_datetime(frame.Date, errors="coerce").dt.strftime("%Y-%m-%d")
            if frame.empty or frame.Date.isna().any():
                raise ValueError("Empty source or invalid dates")
            duplicate_rows += int(frame.duplicated("Date").sum())
            source_rows += len(frame)
            last = frame.sort_values("Date").iloc[-1]
            if last.Date > as_of:
                raise ValueError(f"Source newer than requested close: {last.Date}")
            latest.append({"Ticker": path.stem, **last.to_dict()})
            if path.stem in requests:
                selected = frame.loc[frame.Date.eq(requests[path.stem])]
                if len(selected) != 1:
                    raise ValueError(f"Snapshot own-date source key has {len(selected)} rows: {requests[path.stem]}")
                keyed.append({"Ticker": path.stem, **selected.iloc[0].to_dict()})
        except Exception as exc:
            errors.append({"Ticker": path.stem, "error": str(exc)})
        if path.stat().st_size != before["bytes"] or path.stat().st_mtime_ns != before["mtime_ns"]:
            raise ValueError(f"Source changed during scan: {path}")
        files.append(before)
    return {"latest": pd.DataFrame(latest), "snapshot_keys": pd.DataFrame(keyed),
            "files": files, "source_files": len(paths), "source_rows": source_rows,
            "duplicate_rows": duplicate_rows, "errors": errors, "retired_count": len(retired)}


def audit_database(cursor, sources):
    from backend.db.integrity import read_ingest_consistency
    integrity = read_ingest_consistency(cursor)
    latest = cursor.execute('''SELECT Ticker, Date, Open, High, Low, Close, Volume FROM daily_k
        QUALIFY ROW_NUMBER() OVER (PARTITION BY Ticker ORDER BY TRY_CAST(Date AS DATE) DESC)=1
        ORDER BY Ticker''').df()
    duplicates = cursor.execute('''SELECT COALESCE(SUM(n-1),0) FROM
        (SELECT Ticker, Date, COUNT(*) n FROM daily_k GROUP BY Ticker,Date HAVING COUNT(*)>1)''').fetchone()[0]
    daily_rows = cursor.execute("SELECT COUNT(*) FROM daily_k").fetchone()[0]
    stock_list_rows = cursor.execute("SELECT COUNT(*),COUNT(DISTINCT Ticker) FROM stock_list").fetchone()
    latest_comparison = compare_exact(sources["latest"], latest, OHLCV)
    checks = {"five_ingest_generations_and_counts": integrity["ok"],
              "stock_list_unique": stock_list_rows[0] == stock_list_rows[1] == len(latest),
              "source_db_daily_rows_equal": daily_rows == sources["source_rows"],
              "daily_duplicate_zero": int(duplicates) == 0,
              "latest_source_db_ohlcv_exact": latest_comparison["ok"]}
    return {"checks": checks, "ingest_integrity": integrity, "daily_rows": int(daily_rows),
            "daily_duplicate_rows": int(duplicates), "stock_list_rows": int(stock_list_rows[0]),
            "latest_comparison": latest_comparison,
            "financial_max_period": cursor.execute("SELECT MAX(CAST(Year AS BIGINT)*10+CAST(Season AS BIGINT)) FROM financials").fetchone()[0],
            "twii_latest_date": str(cursor.execute("SELECT MAX(Date) FROM indices WHERE Index_Name='TWII'").fetchone()[0])}


def audit_entry_artifacts(entry_path, html_path, sources, as_of, trade_date):
    from scripts.entry_artifact_lineage import load_entry_artifact
    manifest_path = Path(str(entry_path) + ".manifest.json")
    before = file_receipt(entry_path)
    manifest_before = file_receipt(manifest_path) if manifest_path.is_file() else None
    plan = load_entry_artifact(entry_path)
    if before != file_receipt(entry_path) or manifest_before != file_receipt(manifest_path):
        raise ValueError("Canonical entry plan or certificate changed during validation")
    html_before = file_receipt(html_path)
    soup = BeautifulSoup(html_path.read_text(encoding="utf-8"), "lxml")
    block = soup.find("script", id="data", type="application/json")
    if block is None:
        raise ValueError("Dashboard JSON data block missing")
    html = json.loads(block.string or block.get_text())
    rows, shown = plan.get("rows"), html.get("rows")
    if not isinstance(rows, list) or not isinstance(shown, list):
        raise ValueError("Candidate rows schema invalid")
    def key(row):
        return (str(row.get("stock", "")).split()[0], str(row.get("priority", "")),
                str(row.get("source_date", "")), str(row.get("model_source_date", "")))
    missing = list((Counter(map(key, rows)) - Counter(map(key, shown))).elements())
    extra = list((Counter(map(key, shown)) - Counter(map(key, rows))).elements())
    latest = sources["latest"].set_index("Ticker")
    source_errors = []
    for row in rows:
        ticker = str(row.get("stock", "")).split()[0]
        source_date = row.get("source_date")
        if ticker not in latest.index or source_date != latest.loc[ticker, "Date"]:
            source_errors.append({"Ticker": ticker, "source_date": source_date, "error": "candidate own source date differs from source latest"})
        if source_date != as_of and not row.get("stale") and row.get("data_status") == "OK":
            source_errors.append({"Ticker": ticker, "error": "older source falsely labeled current OK"})
    if load_entry_artifact(entry_path) != plan or before != file_receipt(entry_path) or manifest_before != file_receipt(manifest_path):
        raise ValueError("Canonical entry plan or certificate changed during audit")
    if html_before != file_receipt(html_path):
        raise ValueError("Entry HTML changed during audit")
    checks = {"entry_plan_lineage": True,
              "entry_source_date": plan.get("as_of_date") == as_of and plan.get("model_source_date") == as_of,
              "entry_trade_date": plan.get("trade_date") == trade_date,
              "entry_model_filename_date": as_of in str(plan.get("model_source", "")),
              "entry_source_keys": not source_errors,
              "html_trade_date": html.get("meta", {}).get("trade_date") == trade_date,
              "html_every_candidate_exact_identity": not missing and not extra}
    return {"checks": checks, "plan_rows": len(rows), "html_rows": len(shown),
            "missing_html_rows": missing, "extra_html_rows": extra, "source_errors": source_errors,
            "entry_receipt": before, "entry_manifest_receipt": manifest_before,
            "html_receipt": html_before}


def run(args):
    root = args.root.resolve()
    output = args.output.resolve()
    if not output.is_relative_to(root / "output") or output.exists() or output.with_suffix(".md").exists():
        raise ValueError("Use a new report filename under the specified root/output")
    report = {"status": "FAIL", "stage": args.stage, "as_of": args.as_of, "trade_date": args.trade_date,
              "started_at": datetime.now().astimezone().isoformat(), "checks": {},
              "scope": "Read-only source/DB/output consistency. No fetch, ingest, model, ledger or HTML writes.",
              "limitations": ["Older per-company dates are preserved and reported, not automatically certified as official halts",
                              "This is current-snapshot consistency, not historical announcement-time availability or strategy performance"]}
    cursor = None
    db_signature = None
    try:
        if os.environ.get("STOCK_BASE_DIR") not in (None, str(root)):
            raise ValueError("STOCK_BASE_DIR conflicts with audit root")
        os.environ["STOCK_BASE_DIR"] = str(root)
        from scripts.taiwan_trading_calendar import previous_taiwan_trading_day
        report["checks"]["source_is_previous_trade_close"] = previous_taiwan_trading_day(pd.Timestamp(args.trade_date).date()).isoformat() == args.as_of
        snapshot = None
        if args.stage in {"outputs", "all"}:
            from ml.snapshot_lineage import load_snapshot
            snapshot = load_snapshot(args.snapshot)
            report["checks"]["certified_snapshot_lineage"] = snapshot is not None
            if snapshot is None:
                raise ValueError("Canonical snapshot lineage rejected or absent")
            snapshot = identity_frame(snapshot)
            report["checks"]["snapshot_has_expected_close_date"] = not snapshot.empty and snapshot.Date.max() == args.as_of
            report["snapshot_receipt"] = file_receipt(args.snapshot)
            report["snapshot_manifest_receipt"] = file_receipt(str(args.snapshot) + ".manifest.json")
        sources = scan_daily_sources(root, args.as_of, snapshot)
        report["sources"] = {k: v for k, v in sources.items() if k not in {"latest", "snapshot_keys"}}
        report["checks"].update(source_errors_zero=not sources["errors"], source_duplicates_zero=sources["duplicate_rows"] == 0,
                                   source_latest_market_date=sources["latest"].Date.max() == args.as_of)
        report["actual_source_date_counts"] = sources["latest"].Date.value_counts().to_dict()
        report["older_source_rows"] = sources["latest"].loc[sources["latest"].Date.lt(args.as_of), ["Ticker", "Date"]].to_dict("records")
        if args.stage in {"db", "all"}:
            from backend.db import engine
            requested_db = args.db.resolve()
            if Path(engine.DUCKDB_PATH).resolve() != requested_db:
                if engine._conn is not None or engine._snapshot_conn is not None:
                    raise ValueError("Existing shared engine connection belongs to another DB")
                engine.DUCKDB_PATH = str(requested_db)
            parent = engine.get_conn(read_only=True)
            if parent is engine._snapshot_conn:
                raise ValueError("Fallback DB snapshot cannot certify the intended current DB")
            cursor = parent.cursor()
            stat = requested_db.stat()
            db_signature = (stat.st_size, stat.st_mtime_ns)
            database = audit_database(cursor, sources)
            database["path"] = str(requested_db)
            database["bytes"], database["mtime_ns"] = db_signature
            report["database"] = database
            report["checks"].update(database["checks"])
            report["checks"]["twii_source_date"] = str(database["twii_latest_date"])[:10] == args.as_of
        if snapshot is not None:
            keyed = compare_exact(snapshot, sources["snapshot_keys"], ["Close", "Volume"])
            latest = sources["latest"].loc[sources["latest"].Ticker.isin(snapshot.Ticker)]
            current = compare_exact(snapshot, latest, ["Close", "Volume"])
            report["snapshot_source_key_comparison"] = keyed
            report["snapshot_source_latest_comparison"] = current
            report["checks"].update(snapshot_source_own_date_exact=keyed["ok"], snapshot_source_latest_exact=current["ok"])
            report["prediction_comparisons"] = {}
            for label, path in (("production", args.predictions), ("dataA", args.dataa)):
                frame, receipt = load_prediction_for_audit(path, label, report)
                normalized = identity_frame(frame)
                fields = ["Close"] if "Close" in normalized else []
                comparison = compare_exact(snapshot, normalized, fields)
                report["prediction_comparisons"][label] = {**comparison, "receipt": receipt}
                report["checks"][label + "_snapshot_identities"] = comparison["ok"]
                report["checks"][label + "_artifact_date"] = args.as_of in path.name
            report["checks"]["entry_plan_lineage"] = False
            entry = audit_entry_artifacts(args.entry, args.html, sources, args.as_of, args.trade_date)
            report["entry"] = entry
            report["checks"].update(entry["checks"])
        unchanged = all(Path(item["path"]).stat().st_size == item["bytes"] and
                        Path(item["path"]).stat().st_mtime_ns == item["mtime_ns"] for item in sources["files"])
        report["checks"]["source_files_stable_through_audit"] = unchanged
        from ml.universe import _read_retired_tickers
        retired_now = _read_retired_tickers(root / "config/retired_tickers.csv")
        current_paths = sorted(str(p.resolve()) for p in (root / "日K資料").glob("*.csv")
                               if p.stem.isdigit() and p.stem not in retired_now)
        report["checks"]["source_file_set_stable_through_audit"] = current_paths == sorted(item["path"] for item in sources["files"])
        if db_signature is not None:
            stat = args.db.stat()
            report["checks"]["db_file_stable_through_audit"] = db_signature == (stat.st_size, stat.st_mtime_ns)
        report["status"] = "PASS" if report["checks"] and all(report["checks"].values()) else "FAIL"
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        report["checks"]["audit_completed"] = False
    finally:
        if cursor is not None:
            cursor.close()
    report["completed_at"] = datetime.now().astimezone().isoformat()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    failed = [k for k, value in report["checks"].items() if not value]
    output.with_suffix(".md").write_text(
        f"# Formal consistency acceptance: {report['status']}\n\nStage: {args.stage}. Source close: {args.as_of}; plan trade date: {args.trade_date}.\n\n"
        f"Checks: {len(report['checks'])}; failed: {', '.join(failed) or 'none'}.\n\n"
        f"{report.get('error', '')}\n\nActual counts, exact mismatches, older source dates and SHA receipts are in {output.name}. "
        "Read-only acceptance; no ingestion, model calculation, publication or strategy-performance conclusion.\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "stage": args.stage, "failed_checks": failed,
                      "error": report.get("error"), "report": str(output)}, ensure_ascii=True))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--stage", choices=["db", "outputs", "all"], required=True)
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--trade-date", required=True)
    parser.add_argument("--output", type=Path, required=True)
    for name in ("db", "snapshot", "predictions", "dataa", "entry", "html"):
        parser.add_argument("--" + name, type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    args.db = args.db or root / "stock.duckdb"
    args.snapshot = args.snapshot or root / "ml/models/snapshot_cache.pkl"
    args.predictions = args.predictions or root / f"ml/models/predictions_{args.as_of}.csv"
    args.dataa = args.dataa or root / f"ml/models/dataA_predictions_{args.as_of}.csv"
    args.entry = args.entry or root / f"logs/entry_list_{args.trade_date.replace('-', '')}.json"
    args.html = args.html or root / "ml/reports/entry_dashboard_latest.html"
    return 0 if run(args)["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
