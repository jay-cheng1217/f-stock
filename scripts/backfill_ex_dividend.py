# -*- coding: utf-8 -*-
"""Backfill ex-dividend/ex-rights calendar (TWSE + TPEx) via FinMind.

Output: ml/data/ex_dividend_calendar.csv
Columns: stock_id, date (ex-date), before_price, after_price,
         stock_and_cache_dividend (權值+息值合計, 元/股)

Usage:
    python scripts/backfill_ex_dividend.py --start 2026-01-01 [--end 2026-12-31]
"""
from __future__ import annotations

import argparse
import os
import sys
import time

import pandas as pd
import requests

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_PATH = os.path.join(BASE_DIR, "ml", "data", "ex_dividend_calendar.csv")
FINMIND_URL = "https://api.finmindtrade.com/api/v4/data"


def _load_token() -> str:
    token = os.environ.get("FINMIND_TOKEN", "")
    if token:
        return token
    env_path = os.path.join(BASE_DIR, ".env")
    try:
        with open(env_path, encoding="utf-8-sig") as fh:
            for line in fh:
                if line.strip().startswith("FINMIND_TOKEN="):
                    return line.strip().split("=", 1)[1]
    except OSError:
        pass
    return ""


def fetch_dividend_results(start: str, end: str, token: str) -> pd.DataFrame:
    params = {
        "dataset": "TaiwanStockDividendResult",
        "start_date": start,
        "end_date": end,
        "token": token,
    }
    for attempt in range(3):
        try:
            resp = requests.get(FINMIND_URL, params=params, timeout=120)
            resp.raise_for_status()
            payload = resp.json()
            if payload.get("status") != 200:
                raise RuntimeError(f"FinMind status={payload.get('status')} msg={payload.get('msg')}")
            return pd.DataFrame(payload.get("data") or [])
        except Exception as exc:  # noqa: BLE001
            if attempt == 2:
                raise
            print(f"  retry {attempt + 1}: {exc}")
            time.sleep(5 * (attempt + 1))
    return pd.DataFrame()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2026-01-01")
    ap.add_argument("--end", default=pd.Timestamp.today().strftime("%Y-%m-%d"))
    args = ap.parse_args()

    token = _load_token()
    if not token:
        print("FINMIND_TOKEN missing (.env or env var)")
        return 1

    df = fetch_dividend_results(args.start, args.end, token)
    if df.empty:
        print("no data returned")
        return 1

    keep = ["stock_id", "date", "before_price", "after_price", "stock_and_cache_dividend"]
    df = df[[c for c in keep if c in df.columns]].copy()
    df = df.sort_values(["date", "stock_id"]).drop_duplicates(["stock_id", "date"], keep="last")

    if os.path.exists(OUT_PATH):
        old = pd.read_csv(OUT_PATH, dtype={"stock_id": str})
        df = pd.concat([old, df.astype({"stock_id": str})], ignore_index=True)
        df = df.sort_values(["date", "stock_id"]).drop_duplicates(["stock_id", "date"], keep="last")

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    df.to_csv(OUT_PATH, index=False, encoding="utf-8-sig")
    print(f"ex-dividend calendar -> {OUT_PATH} ({len(df)} rows, {df['date'].min()} ~ {df['date'].max()})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
