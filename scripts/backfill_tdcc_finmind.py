"""FinMind 回補 TDCC 集保股權分散歷史資料

使用 FinMind backer API，一次拿所有股票，按週五逐週下載。
下載完成後自動重建 tdcc_summary.csv。

用法:
    python scripts/backfill_tdcc_finmind.py --token YOUR_TOKEN
    python scripts/backfill_tdcc_finmind.py --token YOUR_TOKEN --start-date 2020-01-01
"""

import os
import sys
import io
import time
import argparse
from datetime import date, datetime, timedelta

import pandas as pd
import requests
from tqdm import tqdm

# Force UTF-8 output on Windows
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")

BASE_DIR = r"F:\stock"
TDCC_DIR = os.path.join(BASE_DIR, "集保分散")
os.makedirs(TDCC_DIR, exist_ok=True)

FINMIND_URL = "https://api.finmindtrade.com/api/v4/data"
RATE_LIMIT = 0.5  # seconds between requests (backer tier)

# FinMind level string → integer (TDCC 標準 1-17)
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
    """下載單週所有股票的集保資料，date_str = 'YYYY-MM-DD'（週五）"""
    try:
        r = requests.get(
            FINMIND_URL,
            params={"dataset": "TaiwanStockHoldingSharesPer", "start_date": date_str, "token": token},
            timeout=60,
        )
        data = r.json()
        if data.get("status") != 200 or not data.get("data"):
            return None

        df = pd.DataFrame(data["data"])
        if df.empty:
            return None

        # 過濾掉 total / 外資法人（只保留 level 1-15）
        df["Level"] = df["HoldingSharesLevel"].map(LEVEL_MAP)
        df = df.dropna(subset=["Level"])
        df["Level"] = df["Level"].astype(int)
        df = df[df["Level"].between(1, 15)]

        # 轉為標準格式
        date_8 = date_str.replace("-", "")
        out = pd.DataFrame({
            "資料日期": date_8,
            "證券代號": df["stock_id"].astype(str).str.strip(),
            "持股分級": df["Level"],
            "人數": pd.to_numeric(df["people"], errors="coerce").fillna(0).astype(int),
            "股數": pd.to_numeric(df["unit"], errors="coerce").fillna(0).astype(int),
            "占集保庫存數比例%": pd.to_numeric(df["percent"], errors="coerce").fillna(0.0),
        })
        return out

    except Exception as e:
        tqdm.write(f"  fetch {date_str} 失敗: {e}")
        return None


def generate_fridays(start: date, end: date) -> list[date]:
    fridays = []
    d = start
    while d.weekday() != 4:
        d += timedelta(days=1)
    while d <= end:
        fridays.append(d)
        d += timedelta(days=7)
    return fridays


def rebuild_summary():
    """重建 tdcc_summary.csv"""
    print("\n重建 tdcc_summary.csv ...")
    files = sorted(f for f in os.listdir(TDCC_DIR)
                   if f.startswith("tdcc_") and f.endswith(".csv") and f != "tdcc_summary.csv")
    if not files:
        print("  無資料")
        return

    all_rows = []
    for fn in tqdm(files, desc="彙整"):
        fp = os.path.join(TDCC_DIR, fn)
        try:
            df = pd.read_csv(fp, encoding="utf-8-sig", dtype=str)
            if len(df.columns) < 6:
                continue
            # 欄位對應
            df.columns = ["Date", "Ticker", "Level", "Holders", "Shares", "Pct"][:len(df.columns)]
            for c in ["Level", "Holders", "Shares", "Pct"]:
                df[c] = pd.to_numeric(df[c], errors="coerce")
            df = df.dropna(subset=["Level"])
            df["Level"] = df["Level"].astype(int)
            df = df[df["Level"].between(1, 15)]

            for (dt, ticker), g in df.groupby(["Date", "Ticker"], sort=False):
                retail = float(g.loc[g["Level"].between(1, 11), "Pct"].sum())
                whale = float(g.loc[g["Level"].between(12, 15), "Pct"].sum())
                holders = int(g["Holders"].sum())
                all_rows.append({"Date": str(dt), "Ticker": str(ticker).strip(),
                                  "Retail_Pct": round(retail, 2), "Whale_Pct": round(whale, 2),
                                  "Total_Holders": holders})
        except Exception as e:
            tqdm.write(f"  略過 {fn}: {e}")

    if all_rows:
        summary = pd.DataFrame(all_rows)
        summary = summary.drop_duplicates(["Date", "Ticker"], keep="last")
        summary = summary.sort_values(["Ticker", "Date"]).reset_index(drop=True)
        out = os.path.join(TDCC_DIR, "tdcc_summary.csv")
        summary.to_csv(out, index=False, encoding="utf-8-sig")
        print(f"  完成: {len(summary):,} 筆 → {out}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--token", required=True)
    parser.add_argument("--start-date", default="2020-01-01")
    parser.add_argument("--end-date", default=None)
    args = parser.parse_args()

    start = datetime.strptime(args.start_date, "%Y-%m-%d").date()
    end = datetime.strptime(args.end_date, "%Y-%m-%d").date() if args.end_date else date.today()

    fridays = generate_fridays(start, end)
    pending = [d for d in fridays
               if not os.path.exists(os.path.join(TDCC_DIR, f"tdcc_{d.strftime('%Y%m%d')}.csv"))]

    print(f"週五總計 {len(fridays)} 天，已有 {len(fridays)-len(pending)} 天，待下載 {len(pending)} 天")

    ok = fail = 0
    for d in tqdm(pending, desc="TDCC 回補"):
        date_str = d.strftime("%Y-%m-%d")
        df = fetch_one_week(date_str, args.token)
        if df is not None and not df.empty:
            path = os.path.join(TDCC_DIR, f"tdcc_{d.strftime('%Y%m%d')}.csv")
            tmp = path + ".tmp"
            df.to_csv(tmp, index=False, encoding="utf-8-sig")
            os.replace(tmp, path)
            ok += 1
        else:
            tqdm.write(f"  {date_str} 無資料（假日或非交易週）")
            fail += 1
        time.sleep(RATE_LIMIT)

    print(f"\n下載完成：成功 {ok}，無資料 {fail}")
    rebuild_summary()


if __name__ == "__main__":
    main()
