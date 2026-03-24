"""每日從 FinMind 抓取處置股名單，輸出 disposition_active.csv

處置股：改為分盤撮合（每20分鐘一次），流動性極差，系統強制觀望。
執行時機：每日盤後，daily_pipeline.py 自動呼叫。
"""
import os
import sys
import io
from datetime import date, datetime, timedelta

import pandas as pd
import requests

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")

BASE_DIR = r"F:\stock"
OUTPUT_PATH = os.path.join(BASE_DIR, "disposition_active.csv")

FINMIND_URL = "https://api.finmindtrade.com/api/v4/data"


def _get_token() -> str:
    env_path = os.path.join(BASE_DIR, ".env")
    if os.path.exists(env_path):
        for line in open(env_path).readlines():
            if line.startswith("FINMIND_TOKEN="):
                return line.split("=", 1)[1].strip()
    return os.environ.get("FINMIND_TOKEN", "")


def fetch_active_dispositions() -> pd.DataFrame:
    """抓取目前處置中的股票（period_end >= 今天）"""
    token = _get_token()
    if not token:
        print("  [警告] 找不到 FINMIND_TOKEN，跳過處置股更新")
        return pd.DataFrame(columns=["stock_id", "period_start", "period_end"])

    today = date.today()
    # 查詢近2年，確保抓到所有活躍處置期
    start = (today - timedelta(days=730)).strftime("%Y-%m-%d")
    end = today.strftime("%Y-%m-%d")

    try:
        r = requests.get(
            FINMIND_URL,
            params={
                "dataset": "TaiwanStockDispositionSecuritiesPeriod",
                "start_date": start,
                "end_date": end,
                "token": token,
            },
            timeout=30,
        )
        data = r.json()
        if data.get("status") != 200 or not data.get("data"):
            print(f"  [警告] 處置股 API 回傳: {data.get('msg', '無資料')}")
            return pd.DataFrame(columns=["stock_id", "period_start", "period_end"])

        df = pd.DataFrame(data["data"])
        df["period_start"] = pd.to_datetime(df["period_start"]).dt.date
        df["period_end"] = pd.to_datetime(df["period_end"]).dt.date

        # 只保留目前仍在處置期間的股票
        active = df[df["period_end"] >= today][["stock_id", "period_start", "period_end"]].copy()
        active = active.drop_duplicates("stock_id", keep="last")
        return active

    except Exception as e:
        print(f"  [錯誤] 抓取處置股失敗: {e}")
        return pd.DataFrame(columns=["stock_id", "period_start", "period_end"])


def main():
    print("更新處置股名單...")
    df = fetch_active_dispositions()

    if df.empty:
        # 保留舊檔，只更新時間戳記
        if os.path.exists(OUTPUT_PATH):
            print("  無新資料，保留舊檔")
        else:
            df.to_csv(OUTPUT_PATH, index=False, encoding="utf-8-sig")
            print("  建立空白處置股檔案")
        return

    df.to_csv(OUTPUT_PATH, index=False, encoding="utf-8-sig")
    print(f"  目前處置股: {len(df)} 支 → {OUTPUT_PATH}")
    if not df.empty:
        for _, row in df.iterrows():
            print(f"    {row['stock_id']}  處置至 {row['period_end']}")


if __name__ == "__main__":
    main()
