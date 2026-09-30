"""Reproduce disposition corrections and source-coverage A/B without production writes."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import pandas as pd
import requests

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
from scripts.disposition_contract import load_corrections, resolve_disposition_periods
from scripts.fetch_disposition import _fetch_twse_periods, _fetch_tpex_periods


def _hash(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _blocked(periods: pd.DataFrame, ticker: str, day: str) -> bool:
    rows = periods[periods.stock_id == ticker]
    return bool(((rows.period_start <= day) & (rows.period_end >= day)).any())


def run(output_dir: Path, *, online: bool) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = [BASE / "ml/data/disposition_periods.csv", BASE / "disposition_active.csv",
             BASE / "ml/reports/special_stock_status_latest.json", BASE / "config/disposition_corrections.json"]
    hashes = {str(path.relative_to(BASE)): _hash(path.read_bytes()) for path in paths}
    periods = pd.read_csv(paths[0], dtype=str)
    raw_hash = _hash(periods.to_csv(index=False).encode())
    revised = resolve_disposition_periods(periods, knowledge_as_of="2026-09-06")
    before = resolve_disposition_periods(periods, knowledge_as_of="2026-08-07T17:59:59+08:00")
    joins = periods.merge(revised, on=["stock_id", "period_start"], suffixes=("_old", "_new"))
    changes = joins[joins.period_end_old != joins.period_end_new].copy()
    changes.to_csv(output_dir / "disposition_corrected_periods.csv", index=False, encoding="utf-8-sig")

    # Use one shared raw frame for both branches; DB errors are not empty data.
    raw_sql = "SELECT CAST(Ticker AS VARCHAR) AS ticker, CAST(Date AS DATE) AS date, Close AS close FROM daily_k WHERE Date BETWEEN '2026-08-01' AND '2026-08-31' ORDER BY Ticker, Date"
    db_error = None
    api_hashes = {}
    missing_price_tickers = []
    try:
        from backend.db.engine import query_df, close_conn
        try:
            frame = query_df(raw_sql)
        finally:
            close_conn()
        frame_source = "DuckDB daily_k via backend.db.engine.query_df, one shared frame"
    except Exception as exc:
        db_error = f"{type(exc).__name__}: {exc}"
        db_path = BASE / "stock.duckdb"
        db_signature = (db_path.stat().st_size, db_path.stat().st_mtime_ns)
        rows = []
        for ticker in sorted(changes.stock_id.unique()):
            response = requests.get(f"http://127.0.0.1:8001/api/stocks/{ticker}/daily", params={"days": 1000}, timeout=30)
            response.raise_for_status()
            payload = response.json()
            if payload.get("ticker") != ticker or not isinstance(payload.get("data"), list):
                raise RuntimeError(f"invalid read-only DuckDB API response: {ticker}")
            api_hashes[ticker] = _hash(json.dumps(payload, sort_keys=True).encode())
            if not payload["data"]:
                missing_price_tickers.append(ticker)
                continue
            bars = pd.DataFrame(payload["data"])
            if not {"Date", "Close", "Ticker"}.issubset(bars.columns) or not bars.Ticker.eq(ticker).all():
                raise RuntimeError(f"invalid raw DuckDB rows: {ticker}")
            bars["Date"] = pd.to_datetime(bars.Date).dt.strftime("%Y-%m-%d")
            bars = bars[(bars.Date >= "2026-08-01") & (bars.Date <= "2026-08-31")]
            bars = bars.rename(columns={"Date": "date", "Close": "close"})
            bars["ticker"] = ticker
            rows.append(bars[["ticker", "date", "close"]])
        frame = pd.concat(rows, ignore_index=True).sort_values(["ticker", "date"])
        if (db_path.stat().st_size, db_path.stat().st_mtime_ns) != db_signature:
            raise RuntimeError("DuckDB changed while freezing the API raw frame")
        frame_source = "DuckDB daily_k through same-process read-only /api/stocks/{ticker}/daily; fetched once and frozen"
    frame["date"] = pd.to_datetime(frame["date"]).dt.strftime("%Y-%m-%d")
    frame = frame[frame.ticker.isin(changes.stock_id.unique())].reset_index(drop=True)
    frame_hash = _hash(frame.to_csv(index=False).encode())
    frame.to_csv(output_dir / "disposition_fixed_raw_frame.csv", index=False)
    checks = []
    for day, bars in frame.groupby("date"):
        known = resolve_disposition_periods(periods, knowledge_as_of=day)
        for row in bars.itertuples(index=False):
            checks.append({"ticker": row.ticker, "date": day,
                           "old_blocked": _blocked(periods, row.ticker, day),
                           "new_blocked": _blocked(known, row.ticker, day)})
    decisions = pd.DataFrame(checks)
    flips = decisions[decisions.old_blocked != decisions.new_blocked]
    flips.to_csv(output_dir / "disposition_eligibility_flips.csv", index=False)

    source_diff = None
    if online:
        baseline, candidate = [], []
        for fetcher in (_fetch_twse_periods, _fetch_tpex_periods):
            baseline.extend(fetcher("2026-08-01", "2026-09-05", 30))
            candidate.extend(fetcher("2026-08-01", "2026-09-07", 30))
        baseline = [row for row in baseline if row["announcement_date"] <= "2026-09-05"]
        candidate = [row for row in candidate if row["announcement_date"] <= "2026-09-05"]
        columns = ["stock_id", "period_start", "period_end", "source", "announcement_date"]
        a, b = pd.DataFrame(baseline)[columns], pd.DataFrame(candidate)[columns]
        added = b.merge(a, on=columns, how="left", indicator=True).query("_merge == 'left_only'").drop(columns="_merge")
        a.to_csv(output_dir / "disposition_source_before.csv", index=False)
        b.to_csv(output_dir / "disposition_source_after.csv", index=False)
        source_diff = {"before_rows": len(a), "after_rows": len(b), "added": added.to_dict("records"),
                       "before_sha256": _hash(a.to_csv(index=False).encode()),
                       "after_sha256": _hash(b.to_csv(index=False).encode()),
                       "known_end": "2026-09-05", "old_coverage_end": "2026-09-05", "new_coverage_end": "2026-09-07"}
    result = {"method_lock": "docs/METHOD_disposition_contract_20260906.md", "snapshot_hashes": hashes,
              "raw_period_frame_sha256": raw_hash, "raw_period_rows": len(periods),
              "corrected_rows": len(changes), "corrected_security_count": changes.stock_id.nunique(),
              "pre_publication_period_changes": int((before.set_index(["stock_id", "period_start"]).period_end !=
                  periods.set_index(["stock_id", "period_start"]).period_end.reindex(before.set_index(["stock_id", "period_start"]).index)).sum()),
              "raw_frame_source": frame_source, "duckdb_error": db_error, "raw_frame_sql": raw_sql,
              "api_response_sha256": api_hashes, "missing_price_tickers": missing_price_tickers,
              "raw_frame_sha256": frame_hash, "raw_frame_rows": len(frame),
              "eligibility_flips": len(flips), "flip_security_count": flips.ticker.nunique(),
              "flips_before_publication": int((flips.date < "2026-08-07").sum()),
              "flip_counts_by_ticker": flips.groupby("ticker").size().to_dict(),
              "source_coverage_comparison": source_diff,
              "prob_edge_delta": "N/A: no prediction computation or model input change",
              "performance_claim": "Eligibility backtest only; no strategy return/MDD superiority claim.",
              "legacy_history_limitation": "Three-column raw history lacks announcement dates; only the correction known-at boundary is established.",
              "production_sources_unchanged": all(_hash(path.read_bytes()) == hashes[str(path.relative_to(BASE))] for path in paths)}
    (output_dir / "disposition_audit.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--online", action="store_true", help="Read official endpoints to compare next-session coverage")
    args = parser.parse_args()
    print(json.dumps(run(args.output_dir, online=args.online), ensure_ascii=False, indent=2))
