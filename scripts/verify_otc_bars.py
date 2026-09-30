# -*- coding: utf-8 -*-
"""Reconcile recent local bars to official TWSE/TPEx final OHLCV.

The verifier covers both exchanges and a bounded recent-date window, so a delayed
backfill cannot append a Yahoo-adjusted row behind an official raw-price history.
It fails closed when either official market source is unavailable.

Local rows dated on a non-trading day (or in the future) inside the window are
Yahoo artifacts by definition (2026-09-21: Sunday 9/20 quote bars on 5276/5878);
they are dropped and reported instead of being sent to TWSE as a query date.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from ml.features.technical import compute_classic_technical_indicators
from scripts.official_daily_prices import fetch_official_daily_prices
from scripts.taiwan_trading_calendar import is_taiwan_trading_day


DAILY = BASE_DIR / "日K資料"
LOG = BASE_DIR / "logs"
CACHE = LOG / "official_daily_history_cache"
INDICATOR_COLUMNS = [
    "MA_5", "MA_20", "MA_60", "RSI_6", "RSI_14",
    "BBL_20_2.0", "BBM_20_2.0", "BBU_20_2.0",
    "MACD_12_26_9", "MACDh_12_26_9", "MACDs_12_26_9",
    "K", "D", "ATR_14", "VOL_MA_5", "VOL_MA_20",
]
DROPPED_MARKER = "row_dropped:non_trading_day"


def _is_official_trading_day(day: pd.Timestamp, today: date) -> bool:
    return day.date() <= today and is_taiwan_trading_day(day.date())


def _recent_local_dates(limit: int, today: date) -> list[pd.Timestamp]:
    dates: set[pd.Timestamp] = set()
    for path in DAILY.glob("*.csv"):
        try:
            values = pd.read_csv(path, usecols=["Date"])["Date"].tail(limit + 2)
        except Exception:
            continue
        parsed = pd.to_datetime(values, errors="coerce").dropna().dt.normalize()
        dates.update(d for d in parsed.tolist() if _is_official_trading_day(d, today))
    return sorted(dates)[-limit:]


def _atomic_write(frame: pd.DataFrame, path: Path) -> None:
    temp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    frame.to_csv(temp, index=False, encoding="utf-8-sig", date_format="%Y-%m-%d")
    os.replace(temp, path)


def run(lookback_trading_days: int = 5, today: date | None = None,
        fetch=fetch_official_daily_prices) -> dict:
    today = today or date.today()
    dates = _recent_local_dates(max(1, lookback_trading_days), today)
    if not dates:
        raise RuntimeError("verify_official_bars: no local trading dates")
    window_start = dates[0]
    official = pd.concat(
        [fetch(day, cache_dir=CACHE) for day in dates],
        ignore_index=True,
    )
    official["Date"] = pd.to_datetime(official["Date"]).dt.normalize()

    rows: list[dict] = []
    files_patched = 0
    rows_dropped = 0
    for ticker, group in official.groupby("Ticker", sort=False):
        path = DAILY / f"{ticker}.csv"
        if not path.exists():
            continue
        local = pd.read_csv(path, dtype={"Date": str})
        local["Date"] = pd.to_datetime(local["Date"], errors="coerce").dt.normalize()
        if local["Date"].duplicated().any():
            raise RuntimeError(f"verify_official_bars: duplicate Date in {ticker}.csv")
        changed = False

        bogus = local["Date"].notna() & (local["Date"] >= window_start) & ~local["Date"].apply(
            lambda d: _is_official_trading_day(d, today))
        if bogus.any():
            for d in local.loc[bogus, "Date"]:
                rows.append({"ticker": ticker, "date": d.strftime("%Y-%m-%d"),
                             "changed_columns": DROPPED_MARKER})
            rows_dropped += int(bogus.sum())
            local = local.loc[~bogus].reset_index(drop=True)
            changed = True

        by_date = group.set_index("Date")
        for index in local.index[local["Date"].isin(by_date.index)]:
            date_value = local.at[index, "Date"]
            changed_columns = []
            record = {"ticker": ticker, "date": date_value.strftime("%Y-%m-%d")}
            for column in ("Open", "High", "Low", "Close", "Volume"):
                old = pd.to_numeric(pd.Series([local.at[index, column]]), errors="coerce").iloc[0]
                new = float(by_date.at[date_value, column])
                if pd.isna(old) or float(old) != new:
                    record[f"old_{column.lower()}"] = None if pd.isna(old) else float(old)
                    record[f"new_{column.lower()}"] = new
                    local.at[index, column] = new
                    changed_columns.append(column)
            if changed_columns:
                record["changed_columns"] = ",".join(changed_columns)
                rows.append(record)
                changed = True
        if changed:
            technical = compute_classic_technical_indicators(local, ticker=ticker)
            for column in INDICATOR_COLUMNS:
                if column in technical:
                    local[column] = technical[column]
            local.replace([np.inf, -np.inf], np.nan, inplace=True)
            _atomic_write(local, path)
            files_patched += 1

    LOG.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d")
    if rows:
        pd.DataFrame(rows).to_csv(
            LOG / f"official_bar_repair_{stamp}.csv", index=False, encoding="utf-8-sig"
        )
    return {"dates": len(dates), "files_patched": files_patched,
            "rows_patched": len(rows), "rows_dropped": rows_dropped}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lookback-trading-days", type=int, default=5)
    args = parser.parse_args()

    summary = run(args.lookback_trading_days)
    message = (
        f"{datetime.now():%F %T} verify_official_bars: "
        f"dates={summary['dates']} files_patched={summary['files_patched']} "
        f"rows_patched={summary['rows_patched']} rows_dropped={summary['rows_dropped']}"
    )
    print(message)
    with (LOG / "tpex_bar_verify.log").open("a", encoding="utf-8") as handle:
        handle.write(message + "\n")
    if summary["files_patched"] > 50:
        print(
            f"WARNING: repaired {summary['files_patched']} files; signals generated before "
            "this verifier used non-final or wrong-basis bars"
        )


if __name__ == "__main__":
    main()
