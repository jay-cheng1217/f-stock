# -*- coding: utf-8 -*-
"""Reconcile local daily OHLCV to official TWSE/TPEx regular-market history.

This is the RETRAIN-03 L2 repair tool. It is deliberately fail-closed:

* every local trading date in the requested window must be fetched from both exchanges;
* source files are changed only with ``--apply`` and a verified external backup path;
* regular-market rows absent from the official tape are removed (for example, pre-listing
  emerging-market rows and fake suspension/holiday bars);
* non-OHLCV source fields are preserved, then classic technical columns are recomputed
  from the shared company-action-continuous price view.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from functools import partial
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from ml.config import DAILY_K_DIR
from ml.features.technical import (
    CLASSIC_INDICATOR_COLUMNS,
    compute_classic_technical_indicators,
)
from scripts.official_daily_prices import (
    OFFICIAL_COLUMNS,
    fetch_official_daily_prices,
    normalize_official_ticker,
)


DEFAULT_CACHE = BASE_DIR / "logs" / "official_daily_history_cache"
DEFAULT_STAGE = BASE_DIR / "logs" / "retrain03_official_prices.duckdb"

PRICE_COLUMNS = ["Open", "High", "Low", "Close", "Volume"]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_backup(source_dir: Path, backup_dir: Path) -> dict:
    """Verify that the external backup contains every current source filename."""
    if not backup_dir.is_dir() or backup_dir.resolve() == source_dir.resolve():
        raise RuntimeError(f"invalid backup directory: {backup_dir}")
    source_files = sorted(source_dir.glob("*.csv"))
    missing = [path.name for path in source_files if not (backup_dir / path.name).exists()]
    if missing:
        raise RuntimeError(f"backup missing {len(missing)} source files; sample={missing[:5]}")
    return {
        "source_files": len(source_files),
        "backup_files": len(list(backup_dir.glob("*.csv"))),
        "sample_source_sha256": _sha256(source_files[0]) if source_files else None,
        "sample_backup_sha256": _sha256(backup_dir / source_files[0].name) if source_files else None,
    }


def collect_local_dates(source_dir: Path, start: pd.Timestamp, end: pd.Timestamp) -> list[str]:
    dates: set[str] = set()
    for path in source_dir.glob("*.csv"):
        if not (path.stem.isdigit() and len(path.stem) == 4):
            continue
        try:
            frame = pd.read_csv(path, usecols=["Date"], dtype={"Date": str})
        except Exception:
            continue
        parsed = pd.to_datetime(frame["Date"], errors="coerce").dt.normalize()
        parsed = parsed.loc[parsed.between(start, end)]
        dates.update(parsed.dt.strftime("%Y-%m-%d"))
    return sorted(dates)


def stage_official_history(
    dates: list[str],
    *,
    cache_dir: Path,
    stage_path: Path,
    workers: int,
) -> dict:
    stage_path.parent.mkdir(parents=True, exist_ok=True)
    if stage_path.exists():
        stage_path.unlink()
    conn = duckdb.connect(str(stage_path))
    conn.execute(
        """
        CREATE TABLE official (
            Ticker VARCHAR,
            Date DATE,
            Open DOUBLE,
            High DOUBLE,
            Low DOUBLE,
            Close DOUBLE,
            Volume DOUBLE,
            Market VARCHAR
        )
        """
    )

    market_rows = {"TWSE": 0, "TPEX": 0}
    completed = 0
    try:
        fetch = partial(fetch_official_daily_prices, cache_dir=cache_dir)
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            # Python 3.14 buffersize keeps the queue bounded. A failed day therefore
            # stops after only the in-flight requests, rather than waiting for 1,500
            # already-submitted jobs during executor shutdown.
            for day, frame in zip(
                dates,
                pool.map(fetch, dates, buffersize=max(2, workers * 2)),
                strict=True,
            ):
                counts = frame.groupby("Market").size().to_dict()
                if counts.get("TWSE", 0) < 500 or counts.get("TPEX", 0) < 300:
                    raise RuntimeError(
                        f"official source coverage too small on {day}: {counts}"
                    )
                conn.register("day_frame", frame)
                conn.execute("INSERT INTO official SELECT * FROM day_frame")
                conn.unregister("day_frame")
                for market, count in counts.items():
                    market_rows[market] = market_rows.get(market, 0) + int(count)
                completed += 1
                if completed % 25 == 0 or completed == len(dates):
                    print(f"official history: {completed}/{len(dates)} trading days")
        duplicate_count = conn.execute(
            """
            SELECT COUNT(*) FROM (
                SELECT Ticker, Date, COUNT(*) n
                FROM official GROUP BY Ticker, Date HAVING n > 1
            )
            """
        ).fetchone()[0]
        if duplicate_count:
            raise RuntimeError(f"official stage contains {duplicate_count} duplicate keys")
        total = conn.execute("SELECT COUNT(*) FROM official").fetchone()[0]
        return {"dates": completed, "rows": int(total), "market_rows": market_rows}
    finally:
        conn.close()


def reconcile_ticker_frame(
    local: pd.DataFrame,
    official: pd.DataFrame,
    *,
    ticker: str,
    start: pd.Timestamp,
    end: pd.Timestamp,
    remove_unofficial_rows: bool = True,
) -> tuple[pd.DataFrame, dict]:
    """Reconcile one ticker while preserving every retained non-price cell."""
    out = local.copy()
    out["Date"] = pd.to_datetime(out["Date"], errors="coerce").dt.normalize()
    if out["Date"].isna().any():
        raise RuntimeError(f"{ticker}: invalid local Date rows")
    if out["Date"].duplicated().any():
        raise RuntimeError(f"{ticker}: duplicate local Date rows")

    off = official.copy()
    off["Date"] = pd.to_datetime(off["Date"], errors="coerce").dt.normalize()
    if off["Date"].duplicated().any():
        raise RuntimeError(f"{ticker}: duplicate official Date rows")
    off = off.set_index("Date")

    in_window = out["Date"].between(start, end)
    official_dates = set(off.index)
    remove_mask = in_window & ~out["Date"].isin(official_dates)
    removed_dates = out.loc[remove_mask, "Date"].dt.strftime("%Y-%m-%d").tolist()
    if remove_unofficial_rows and official_dates:
        out = out.loc[~remove_mask].copy()
    else:
        removed_dates = []

    before_nonprice = out.drop(columns=[c for c in PRICE_COLUMNS if c in out.columns]).copy()
    matched = out["Date"].isin(official_dates) & out["Date"].between(start, end)
    matched_index = out.index[matched.to_numpy()]
    row_changed = pd.Series(False, index=matched_index)
    changed_cells = 0
    matched_dates = out.loc[matched_index, "Date"]
    for column in PRICE_COLUMNS:
        if column not in out.columns:
            out[column] = np.nan
        official_values = matched_dates.map(off[column]).astype(float)
        local_values = pd.to_numeric(out.loc[matched_index, column], errors="coerce")
        different = local_values.isna() | local_values.ne(official_values)
        changed_cells += int(different.sum())
        row_changed |= different
        out.loc[matched_index, column] = official_values.to_numpy()
    changed_rows = int(row_changed.sum())
    unchanged_rows = int(len(row_changed) - changed_rows)

    after_nonprice = out.drop(columns=[c for c in PRICE_COLUMNS if c in out.columns]).copy()
    pd.testing.assert_frame_equal(before_nonprice, after_nonprice, check_exact=True)

    local_dates = set(out["Date"])
    added_dates = [
        date for date in off.index
        if start <= date <= end and date not in local_dates
    ]
    if added_dates:
        added = pd.DataFrame(np.nan, index=range(len(added_dates)), columns=out.columns)
        added["Date"] = added_dates
        for column in PRICE_COLUMNS:
            if column not in added.columns:
                added[column] = np.nan
            added[column] = off.loc[added_dates, column].to_numpy(dtype=float)
        out = pd.concat([out, added], ignore_index=True)

    out = out.sort_values("Date").reset_index(drop=True)
    return out, {
        "ticker": ticker,
        "matched_rows": int(len(matched_index)),
        "changed_rows": changed_rows,
        "unchanged_rows": unchanged_rows,
        "changed_price_cells": changed_cells,
        "removed_rows": len(removed_dates),
        "removed_dates_sample": removed_dates[:10],
        "added_rows": len(added_dates),
        "added_dates_sample": [date.strftime("%Y-%m-%d") for date in added_dates[:10]],
    }


def _atomic_write(frame: pd.DataFrame, path: Path) -> None:
    temp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    frame.to_csv(temp, index=False, encoding="utf-8-sig", date_format="%Y-%m-%d")
    os.replace(temp, path)


def apply_reconciliation(
    source_dir: Path,
    stage_path: Path,
    *,
    start: pd.Timestamp,
    end: pd.Timestamp,
    apply: bool,
) -> tuple[list[dict], dict]:
    conn = duckdb.connect(str(stage_path), read_only=True)
    details: list[dict] = []
    uncovered: list[str] = []
    missing_local_official = 0
    files_written = 0
    try:
        paths = sorted(source_dir.glob("*.csv"))
        for path_number, path in enumerate(paths, start=1):
            ticker = path.stem
            if normalize_official_ticker(ticker) is None:
                continue
            local = pd.read_csv(path, dtype={"Date": str})
            official = conn.execute(
                """
                SELECT Date, Open, High, Low, Close, Volume
                FROM official WHERE Ticker = ? ORDER BY Date
                """,
                [ticker],
            ).fetchdf()
            if official.empty:
                has_window = pd.to_datetime(local["Date"], errors="coerce").between(start, end).any()
                if has_window:
                    uncovered.append(ticker)
                continue
            repaired, stats = reconcile_ticker_frame(
                local, official, ticker=ticker, start=start, end=end
            )
            missing_local_official += stats["added_rows"]

            if apply:
                # Recompute only the indicators persisted on disk. The full ML catalog
                # is calculated later by dataset.py from the same adjusted price view.
                technical = compute_classic_technical_indicators(repaired, ticker=ticker)
                for column in CLASSIC_INDICATOR_COLUMNS:
                    if column in technical.columns:
                        repaired[column] = technical[column]
            stats["rows_before"] = len(local)
            stats["rows_after"] = len(repaired)
            details.append(stats)
            if apply and (
                stats["changed_rows"] or stats["removed_rows"] or stats["added_rows"] or any(
                    column in repaired.columns for column in CLASSIC_INDICATOR_COLUMNS
                )
            ):
                _atomic_write(repaired, path)
                files_written += 1
            if path_number % 100 == 0 or path_number == len(paths):
                print(f"local reconciliation: {path_number}/{len(paths)} files")
    finally:
        conn.close()
    return details, {
        "uncovered_tickers": uncovered,
        "uncovered_count": len(uncovered),
        "missing_local_official_rows": missing_local_official,
        "added_official_rows": int(sum(row.get("added_rows", 0) for row in details)),
        "files_written": files_written,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2020-01-01")
    parser.add_argument("--end", default=None)
    parser.add_argument("--cache-dir", default=str(DEFAULT_CACHE))
    parser.add_argument("--stage-path", default=str(DEFAULT_STAGE))
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--backup-path", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--report", default=None)
    args = parser.parse_args()

    source_dir = Path(DAILY_K_DIR)
    start = pd.Timestamp(args.start).normalize()
    if args.end:
        end = pd.Timestamp(args.end).normalize()
    else:
        latest = []
        for path in source_dir.glob("*.csv"):
            try:
                dates = pd.read_csv(path, usecols=["Date"])["Date"]
                latest.append(pd.to_datetime(dates, errors="coerce").max())
            except Exception:
                continue
        end = max(date for date in latest if pd.notna(date)).normalize()

    backup = validate_backup(source_dir, Path(args.backup_path))
    if backup["sample_source_sha256"] != backup["sample_backup_sha256"]:
        raise RuntimeError("backup verification failed: sample SHA-256 mismatch")
    dates = collect_local_dates(source_dir, start, end)
    if not dates:
        raise RuntimeError("no local trading dates in requested window")
    print(f"RETRAIN-03 official reconciliation: {dates[0]}..{dates[-1]} ({len(dates)} dates)")
    stage = stage_official_history(
        dates,
        cache_dir=Path(args.cache_dir),
        stage_path=Path(args.stage_path),
        workers=args.workers,
    )
    details, coverage = apply_reconciliation(
        source_dir,
        Path(args.stage_path),
        start=start,
        end=end,
        apply=args.apply,
    )
    totals = {
        key: int(sum(row.get(key, 0) for row in details))
        for key in (
            "matched_rows", "changed_rows", "unchanged_rows", "changed_price_cells",
            "removed_rows", "added_rows", "rows_before", "rows_after",
        )
    }
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "mode": "apply" if args.apply else "dry_run",
        "window": {"start": str(start.date()), "end": str(end.date())},
        "backup_path": str(Path(args.backup_path).resolve()),
        "backup_verification": backup,
        "official_stage": stage,
        "totals": totals,
        "coverage": coverage,
        "ticker_details": details,
    }
    report = Path(args.report) if args.report else (
        BASE_DIR / "ml" / "reports" / f"retrain03_official_ohlc_repair_{datetime.now():%Y%m%d}.json"
    )
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"report": str(report), "totals": totals, "coverage": coverage}, ensure_ascii=False))


if __name__ == "__main__":
    main()
