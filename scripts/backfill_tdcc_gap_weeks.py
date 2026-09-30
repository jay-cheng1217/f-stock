# -*- coding: utf-8 -*-
"""補齊 TDCC 歷史缺週（需 FinMind 付費等級 token）。

為什麼需要這支腳本（2026-07-19 查明）：
`backfill_tdcc_finmind.py` 的 `generate_fridays()` **只產生週五日期**。但 TDCC 週快照的
資料日 = 該週最後營業日，遇週五休市時會落在**週四**。2026-03-24 的付費批次回補抓到 279 週，
卻有 43 週拿不到——其中 29 週正是「週五休市、資料在週四」，腳本從未問過那些日期。

本腳本修正兩個缺陷：
1. **逐日探測**：每個缺週依序試 週五→四→三→二→一，抓到即止
2. **驗證實際資料日**：`start_date=end_date` 鎖定單日，並比對回傳的 `date` 欄，
   絕不把「請求日期」蓋在別週的資料上（原腳本會產生錯誤日期的髒檔）
3. 覆蓋率不足（<1000 檔）不寫檔；逐週回報成敗

用法：
    python scripts/backfill_tdcc_gap_weeks.py             # 用 .env 的 FINMIND_TOKEN
    python scripts/backfill_tdcc_gap_weeks.py --dry-run   # 只列缺週不抓
"""
from __future__ import annotations

import argparse
import glob
import io
import os
import sys
import time
from datetime import date, timedelta

import pandas as pd
import requests

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TDCC_DIR = os.path.join(BASE_DIR, "集保分散")
FINMIND_URL = "https://api.finmindtrade.com/api/v4/data"
MIN_TICKERS = 1000  # 覆蓋率下限,低於此視為殘缺不寫檔

LEVEL_MAP = {
    "1-999": 1, "1,000-5,000": 2, "5,001-10,000": 3, "10,001-15,000": 4,
    "15,001-20,000": 5, "20,001-30,000": 6, "30,001-40,000": 7,
    "40,001-50,000": 8, "50,001-100,000": 9, "100,001-200,000": 10,
    "200,001-400,000": 11, "400,001-600,000": 12, "600,001-800,000": 13,
    "800,001-1,000,000": 14, "more than 1,000,001": 15,
}


def _token() -> str:
    tok = os.environ.get("FINMIND_TOKEN", "")
    if tok:
        return tok
    try:
        for line in io.open(os.path.join(BASE_DIR, ".env"), encoding="utf-8-sig"):
            if line.startswith("FINMIND_TOKEN="):
                return line.strip().split("=", 1)[1]
    except OSError:
        pass
    return ""


def find_gap_weeks() -> list[date]:
    """以現有週檔的間隔推出缺週（間隔 >10 天即視為有洞）。"""
    have = sorted(os.path.basename(p)[5:13] for p in glob.glob(os.path.join(TDCC_DIR, "tdcc_2*.csv")))
    days = [date(int(s[:4]), int(s[4:6]), int(s[6:8])) for s in have]
    gaps: list[date] = []
    for a, b in zip(days, days[1:]):
        if (b - a).days > 10:
            cur = a + timedelta(days=7)
            while cur < b - timedelta(days=3):
                gaps.append(cur)
                cur += timedelta(days=7)
    return gaps


def fetch_exact(day: date, token: str) -> pd.DataFrame | None:
    """鎖定單一日期抓取；回傳的資料日必須等於 day,否則視為無資料。"""
    try:
        resp = requests.get(FINMIND_URL, params={
            "dataset": "TaiwanStockHoldingSharesPer",
            "start_date": day.isoformat(),
            "end_date": day.isoformat(),
            "token": token,
        }, timeout=90)
        payload = resp.json()
    except Exception as exc:  # noqa: BLE001
        print(f"    {day} 請求失敗: {str(exc)[:60]}")
        return None
    if payload.get("status") != 200:
        msg = str(payload.get("msg") or "")
        if "level" in msg.lower():
            raise SystemExit(
                f"FinMind token 等級不足（{msg[:60]}）。本腳本需付費等級,請更新 .env 的 FINMIND_TOKEN。"
            )
        return None
    data = payload.get("data") or []
    if not data:
        return None
    df = pd.DataFrame(data)
    # 關鍵防呆:只留真正屬於 day 的列,避免把鄰週資料存成本週
    df = df[df["date"] == day.isoformat()]
    if df.empty:
        return None
    df["Level"] = df["HoldingSharesLevel"].map(LEVEL_MAP)
    df = df.dropna(subset=["Level"])
    df = df[df["Level"].astype(int).between(1, 15)]
    if df.empty or df["stock_id"].nunique() < MIN_TICKERS:
        print(f"    {day} 僅 {df['stock_id'].nunique()} 檔,低於下限 {MIN_TICKERS},不寫檔")
        return None
    return pd.DataFrame({
        "資料日期": day.strftime("%Y%m%d"),
        "證券代號": df["stock_id"].astype(str).str.strip(),
        "持股分級": df["Level"].astype(int),
        "人數": pd.to_numeric(df["people"], errors="coerce").fillna(0).astype(int),
        "股數": pd.to_numeric(df["unit"], errors="coerce").fillna(0).astype(int),
        "占集保庫存數比例%": pd.to_numeric(df["percent"], errors="coerce").fillna(0.0),
    })


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只列出缺週不抓取")
    ap.add_argument("--probe-days", type=int, default=5, help="每週往前探測幾天(五→一)")
    args = ap.parse_args()

    gaps = find_gap_weeks()
    print(f"偵測到缺週: {len(gaps)} 週")
    if args.dry_run:
        for g in gaps:
            print(f"  {g}")
        return 0

    token = _token()
    if not token:
        print("FINMIND_TOKEN 未設定（.env 或環境變數）")
        return 1

    ok = failed = 0
    for week in gaps:
        got = False
        for back in range(args.probe_days):  # 週五→四→三→二→一
            day = week - timedelta(days=back)
            path = os.path.join(TDCC_DIR, f"tdcc_{day:%Y%m%d}.csv")
            if os.path.exists(path):
                got = True
                break
            frame = fetch_exact(day, token)
            time.sleep(0.6)
            if frame is not None:
                tmp = path + ".tmp"
                frame.to_csv(tmp, index=False, encoding="utf-8-sig")
                os.replace(tmp, path)
                print(f"  {week} 週 → 補到實際資料日 {day} ({frame['證券代號'].nunique():,} 檔)")
                ok += 1
                got = True
                break
        if not got:
            print(f"  {week} 週 → 五~一 全無資料")
            failed += 1

    print(f"\n補齊 {ok} 週, 無資料 {failed} 週")
    if ok:
        print("請接著重建摘要: python twstock.py --step 9")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.exit(main())
