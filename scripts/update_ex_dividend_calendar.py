# -*- coding: utf-8 -*-
"""每日更新除權息日曆(TWSE TWT49U 範圍抓取 + TPEx openapi 當日)。

輸出/累積:ml/data/ex_dividend_calendar.csv
- TWSE:/rwd/zh/exRight/TWT49U,一次抓近 10 個日曆日到明日(涵蓋補假日與預告)
- TPEx:/openapi/v1/tpex_exright_daily,僅當日(歷史靠每日累積或 FinMind 補)

用法:
    python scripts/update_ex_dividend_calendar.py            # 每日增量
    python scripts/update_ex_dividend_calendar.py --seed-finmind 3033 8150 ...  # 指定股票歷史回補
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
import time
from datetime import date, timedelta

import pandas as pd
import requests

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CAL_PATH = os.path.join(BASE_DIR, "ml", "data", "ex_dividend_calendar.csv")
FINMIND_URL = "https://api.finmindtrade.com/api/v4/data"


def _get_json(url: str) -> dict | list:
    try:
        resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=40)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.SSLError:
        # TPEx 憑證缺 Subject Key Identifier,Python 3.14 拒絕;照專案慣例退回 curl
        import subprocess
        out = subprocess.run(["curl", "-sL", "--max-time", "40",
                              "-H", "User-Agent: Mozilla/5.0", url],
                             capture_output=True, timeout=60)
        return json.loads(out.stdout.decode("utf-8"))


def _roc_to_iso(roc: str) -> str | None:
    """民國日期('115年07月13日' 或 '1150713')轉 ISO。"""
    digits = "".join(ch for ch in str(roc) if ch.isdigit())
    if len(digits) < 7:
        return None
    y, m, d = int(digits[:-4]) + 1911, int(digits[-4:-2]), int(digits[-2:])
    try:
        return date(y, m, d).isoformat()
    except ValueError:
        return None


def fetch_twse(start: date, end: date) -> list[dict]:
    url = ("https://www.twse.com.tw/rwd/zh/exRight/TWT49U"
           f"?startDate={start:%Y%m%d}&endDate={end:%Y%m%d}&response=json")
    payload = _get_json(url)
    if not isinstance(payload, dict) or "data" not in payload:
        raise RuntimeError("TWSE TWT49U returned an invalid payload")
    rows = []
    for row in payload.get("data") or []:
        iso = _roc_to_iso(row[0])
        if not iso:
            continue
        code = str(row[1]).strip()
        try:
            before = float(str(row[3]).replace(",", ""))
            after = float(str(row[4]).replace(",", ""))
            dv = float(str(row[5]).replace(",", ""))
        except (TypeError, ValueError):
            continue
        rows.append({"stock_id": code, "date": iso, "before_price": before,
                     "after_price": after, "stock_and_cache_dividend": dv})
    return rows


def fetch_tpex(start: date, end: date) -> list[dict]:
    """TPEx 除權息計算結果(bulletin/exDailyQ,支援跨年日期範圍)。"""
    url = ("https://www.tpex.org.tw/www/zh-tw/bulletin/exDailyQ"
           f"?startDate={start:%Y/%m/%d}&endDate={end:%Y/%m/%d}&response=json")
    payload = _get_json(url)
    if not isinstance(payload, dict) or not payload.get("tables"):
        raise RuntimeError("TPEx exDailyQ returned an invalid payload")
    table = payload["tables"][0]
    if not isinstance(table, dict) or "data" not in table:
        raise RuntimeError("TPEx exDailyQ table is missing data")
    rows = []
    for row in table.get("data") or []:
        # 欄位:0=除權息日期(民國) 1=代號 3=前收盤 4=參考價 7=權值+息值
        iso = _roc_to_iso(row[0])
        if not iso:
            continue
        try:
            rows.append({
                "stock_id": str(row[1]).strip(),
                "date": iso,
                "before_price": float(str(row[3]).replace(",", "")),
                "after_price": float(str(row[4]).replace(",", "")),
                "stock_and_cache_dividend": float(str(row[7]).replace(",", "")),
            })
        except (TypeError, ValueError, IndexError):
            continue
    return rows


def fetch_finmind(tickers: list[str]) -> list[dict]:
    token = ""
    try:
        for line in io.open(os.path.join(BASE_DIR, ".env"), encoding="utf-8-sig"):
            if line.startswith("FINMIND_TOKEN="):
                token = line.strip().split("=", 1)[1]
    except OSError:
        pass
    if not token:
        print("  FINMIND_TOKEN missing; skip seed")
        return []
    import requests
    rows = []
    for i, tk in enumerate(tickers):
        try:
            r = requests.get(FINMIND_URL, params={
                "dataset": "TaiwanStockDividendResult", "data_id": tk,
                "start_date": "2026-01-01", "token": token}, timeout=60)
            r.raise_for_status()
            for e in r.json().get("data") or []:
                rows.append({"stock_id": str(e["stock_id"]), "date": str(e["date"])[:10],
                             "before_price": e.get("before_price"), "after_price": e.get("after_price"),
                             "stock_and_cache_dividend": e.get("stock_and_cache_dividend")})
        except Exception as exc:  # noqa: BLE001
            print(f"  finmind {tk} failed: {exc}")
        time.sleep(0.4)
        if (i + 1) % 20 == 0:
            print(f"  seeded {i + 1}/{len(tickers)}")
    return rows


def merge_save(rows: list[dict]) -> None:
    df = pd.DataFrame(rows)
    if os.path.exists(CAL_PATH):
        old = pd.read_csv(CAL_PATH, dtype={"stock_id": str}, encoding="utf-8-sig")
        df = pd.concat([old, df], ignore_index=True) if not df.empty else old
    if df.empty:
        print("  nothing to save")
        return
    df["stock_id"] = df["stock_id"].astype(str)
    df["date"] = df["date"].astype(str).str[:10]
    df = df.drop_duplicates(["stock_id", "date"], keep="last").sort_values(["date", "stock_id"])
    os.makedirs(os.path.dirname(CAL_PATH), exist_ok=True)
    df.to_csv(CAL_PATH, index=False, encoding="utf-8-sig")
    print(f"calendar -> {CAL_PATH} ({len(df)} events, latest {df['date'].max()})")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed-finmind", nargs="*", default=None,
                    help="以 FinMind 回補指定股票 2026 起的除權息事件")
    ap.add_argument("--backfill", nargs=2, metavar=("START", "END"), default=None,
                    help="歷史回補(YYYY-MM-DD YYYY-MM-DD),按年切塊抓 TWSE+TPEx")
    args = ap.parse_args()

    rows: list[dict] = []
    failures: list[str] = []

    def collect(name: str, func, *func_args) -> list[dict]:
        try:
            return func(*func_args)
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{name}: {exc}")
            print(f"  {name} failed: {exc}")
            return []

    if args.seed_finmind is not None:
        rows += fetch_finmind(args.seed_finmind)
    if args.backfill:
        b_start = date.fromisoformat(args.backfill[0])
        b_end = date.fromisoformat(args.backfill[1])
        cur = b_start
        while cur <= b_end:
            chunk_end = min(date(cur.year, 12, 31), b_end)
            tw = collect("TWSE TWT49U", fetch_twse, cur, chunk_end)
            tp = collect("TPEx exDailyQ", fetch_tpex, cur, chunk_end)
            print(f"  {cur.year}: TWSE {len(tw)} + TPEx {len(tp)}")
            rows += tw + tp
            cur = date(cur.year + 1, 1, 1)
            time.sleep(1.5)
    else:
        today = date.today()
        start = today - timedelta(days=10)
        end = today + timedelta(days=1)
        rows += collect("TWSE TWT49U", fetch_twse, start, end)
        rows += collect("TPEx exDailyQ", fetch_tpex, start, end)
    merge_save(rows)
    if failures:
        print(f"  official source failures: {len(failures)}")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
