"""Backfill TDCC holding distribution data from FinMind.

The script writes raw weekly ``tdcc_YYYYMMDD.csv`` files and rebuilds
``tdcc_summary.csv`` from all raw files using the same schema normalizer as
``scripts/backfill_tdcc.py``. Rebuilding from raw files avoids the historical
summary column drift where ticker values were accidentally written into the
Date column.
"""
from __future__ import annotations

import argparse
import io
import os
import sys
import time
from datetime import date, datetime, timedelta

import pandas as pd
import requests
from tqdm import tqdm

if sys.platform == "win32":
    def _ensure_utf8_stream(stream):
        try:
            stream.reconfigure(encoding="utf-8")
            return stream
        except Exception:
            pass
        buffer = getattr(stream, "buffer", None)
        if buffer is None:
            return stream
        try:
            return io.TextIOWrapper(buffer, encoding="utf-8")
        except Exception:
            return stream

    sys.stdout = _ensure_utf8_stream(sys.stdout)
    sys.stderr = _ensure_utf8_stream(sys.stderr)

BASE_DIR = os.environ.get("STOCK_BASE_DIR") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from scripts.backfill_tdcc import _standardize_tdcc_columns_v2

TDCC_DIR = os.path.join(BASE_DIR, "集保分散")
os.makedirs(TDCC_DIR, exist_ok=True)

FINMIND_URL = "https://api.finmindtrade.com/api/v4/data"
RATE_LIMIT = 0.5

LEVEL_MAP = {
    "1-999": 1,
    "1,000-5,000": 2,
    "5,001-10,000": 3,
    "10,001-15,000": 4,
    "15,001-20,000": 5,
    "20,001-30,000": 6,
    "30,001-40,000": 7,
    "40,001-50,000": 8,
    "50,001-100,000": 9,
    "100,001-200,000": 10,
    "200,001-400,000": 11,
    "400,001-600,000": 12,
    "600,001-800,000": 13,
    "800,001-1,000,000": 14,
    "more than 1,000,001": 15,
    "total": 17,
}


def fetch_one_week(date_str: str, token: str) -> pd.DataFrame | None:
    """Fetch one Friday from FinMind and return the raw repository schema."""
    try:
        response = requests.get(
            FINMIND_URL,
            params={
                "dataset": "TaiwanStockHoldingSharesPer",
                "start_date": date_str,
                "token": token,
            },
            timeout=60,
        )
        data = response.json()
        if data.get("status") != 200 or not data.get("data"):
            return None

        df = pd.DataFrame(data["data"])
        if df.empty:
            return None

        df["Level"] = df["HoldingSharesLevel"].map(LEVEL_MAP)
        df = df.dropna(subset=["Level"])
        df["Level"] = df["Level"].astype(int)
        df = df[df["Level"].between(1, 15)]

        date_8 = date_str.replace("-", "")
        return pd.DataFrame(
            {
                "資料日期": date_8,
                "證券代號": df["stock_id"].astype(str).str.strip(),
                "持股分級": df["Level"],
                "人數": pd.to_numeric(df["people"], errors="coerce").fillna(0).astype(int),
                "股數": pd.to_numeric(df["unit"], errors="coerce").fillna(0).astype(int),
                "占集保庫存數比例%": pd.to_numeric(df["percent"], errors="coerce").fillna(0.0),
            }
        )
    except Exception as exc:
        tqdm.write(f"  fetch {date_str} failed: {exc}")
        return None


def generate_fridays(start: date, end: date) -> list[date]:
    fridays = []
    current = start
    while current.weekday() != 4:
        current += timedelta(days=1)
    while current <= end:
        fridays.append(current)
        current += timedelta(days=7)
    return fridays


def rebuild_summary() -> None:
    """Rebuild tdcc_summary.csv from all raw TDCC files."""
    files = sorted(
        f
        for f in os.listdir(TDCC_DIR)
        if f.startswith("tdcc_") and f.endswith(".csv") and f != "tdcc_summary.csv"
    )
    if not files:
        print("No TDCC raw files found.")
        return

    rows = []
    skipped = 0
    for filename in tqdm(files, desc="Rebuild TDCC"):
        path = os.path.join(TDCC_DIR, filename)
        try:
            raw_df = pd.read_csv(path, encoding="utf-8-sig", dtype=str)
            df = _standardize_tdcc_columns_v2(raw_df)
            if df.empty:
                skipped += 1
                continue

            df = df[df["Level"].between(1, 15)]
            for (dt, ticker), group in df.groupby(["Date", "Ticker"], sort=False):
                rows.append(
                    {
                        "Date": str(dt).strip(),
                        "Ticker": str(ticker).strip(),
                        "Retail_Pct": round(float(group.loc[group["Level"].between(1, 11), "Pct"].sum()), 2),
                        "Whale_Pct": round(float(group.loc[group["Level"].between(12, 15), "Pct"].sum()), 2),
                        "Total_Holders": int(group["Holders"].sum()),
                    }
                )
        except Exception as exc:
            skipped += 1
            tqdm.write(f"  skipped {filename}: {exc}")

    if not rows:
        print("No valid TDCC rows to summarize.")
        return

    summary = pd.DataFrame(rows)
    summary["Date"] = summary["Date"].astype(str).str.strip()
    summary["Ticker"] = summary["Ticker"].astype(str).str.strip()
    summary = summary[summary["Date"].str.fullmatch(r"\d{8}")]
    summary = summary[summary["Ticker"].str.len() > 0]
    summary = summary.drop_duplicates(["Date", "Ticker"], keep="last")
    summary = summary.sort_values(["Ticker", "Date"]).reset_index(drop=True)

    output_path = os.path.join(TDCC_DIR, "tdcc_summary.csv")
    summary.to_csv(output_path, index=False, encoding="utf-8-sig")
    print(f"Summary rows: {len(summary):,} -> {output_path}")
    if skipped:
        print(f"Skipped raw files: {skipped}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill TDCC data from FinMind.")
    parser.add_argument("--token", required=True)
    parser.add_argument("--start-date", default="2020-01-01")
    parser.add_argument("--end-date", default=None)
    args = parser.parse_args()

    start = datetime.strptime(args.start_date, "%Y-%m-%d").date()
    end = datetime.strptime(args.end_date, "%Y-%m-%d").date() if args.end_date else date.today()
    fridays = generate_fridays(start, end)
    pending = [
        day
        for day in fridays
        if not os.path.exists(os.path.join(TDCC_DIR, f"tdcc_{day.strftime('%Y%m%d')}.csv"))
    ]

    print(f"Fridays: {len(fridays)}; existing: {len(fridays) - len(pending)}; pending: {len(pending)}")

    ok = fail = 0
    for day in tqdm(pending, desc="Fetch TDCC"):
        date_str = day.strftime("%Y-%m-%d")
        df = fetch_one_week(date_str, args.token)
        if df is not None and not df.empty:
            path = os.path.join(TDCC_DIR, f"tdcc_{day.strftime('%Y%m%d')}.csv")
            tmp = path + ".tmp"
            df.to_csv(tmp, index=False, encoding="utf-8-sig")
            os.replace(tmp, path)
            ok += 1
        else:
            tqdm.write(f"  no data for {date_str}")
            fail += 1
        time.sleep(RATE_LIMIT)

    print(f"Fetched: {ok}; failed/empty: {fail}")
    rebuild_summary()


if __name__ == "__main__":
    main()
