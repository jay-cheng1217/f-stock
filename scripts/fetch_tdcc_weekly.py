"""每週下載 TDCC 集保股權分散表 (Open Data API)

TDCC Open Data 只提供最新一週的資料，無法歷史回補。
此腳本每週執行，累積快照到 集保分散/ 目錄。

用法: python scripts/fetch_tdcc_weekly.py
"""

import os
import sys
import io
import requests
import urllib3
import pandas as pd

# Force UTF-8 output on Windows
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BASE_DIR = r"F:\stock"
TDCC_DIR = os.path.join(BASE_DIR, "集保分散")
os.makedirs(TDCC_DIR, exist_ok=True)

TDCC_API = "https://openapi.tdcc.com.tw/v1/opendata/1-5"


def fetch_and_save():
    """下載最新 TDCC 集保資料並存檔"""
    print("下載 TDCC 集保股權分散表...", flush=True)

    r = requests.get(TDCC_API, timeout=60, verify=False)
    r.raise_for_status()
    data = r.json()

    if not data:
        print("  API 回傳空資料")
        return False

    df = pd.DataFrame(data)
    df.columns = [c.replace("\ufeff", "") for c in df.columns]

    data_date = str(df["資料日期"].iloc[0]).strip()
    print(f"  資料日期: {data_date}, 筆數: {len(df)}", flush=True)

    # 檢查是否已下載過
    raw_path = os.path.join(TDCC_DIR, f"tdcc_{data_date}.csv")
    if os.path.exists(raw_path):
        print(f"  已存在: {raw_path}, 跳過")
        return True

    # 儲存原始資料
    df.to_csv(raw_path, index=False, encoding="utf-8-sig")
    print(f"  原始資料: {raw_path}", flush=True)

    # 建立 summary (大戶/散戶持股比)
    df["股數"] = pd.to_numeric(df["股數"].str.replace(",", ""), errors="coerce")
    df["人數"] = pd.to_numeric(df["人數"].str.replace(",", ""), errors="coerce")
    df["占集保庫存數比例%"] = pd.to_numeric(df["占集保庫存數比例%"], errors="coerce")
    df["持股分級"] = pd.to_numeric(df["持股分級"], errors="coerce")

    summary_rows = []
    for ticker in df["證券代號"].unique():
        sub = df[df["證券代號"] == ticker]
        if sub.empty:
            continue
        whale = sub[sub["持股分級"] >= 12]
        retail = sub[sub["持股分級"] <= 5]
        summary_rows.append({
            "Date": pd.to_datetime(data_date),
            "Ticker": ticker,
            "whale_pct": whale["占集保庫存數比例%"].sum(),
            "retail_pct": retail["占集保庫存數比例%"].sum(),
            "total_holders": sub["人數"].sum(),
        })

    new_summary = pd.DataFrame(summary_rows)

    # 合併到 summary
    summary_path = os.path.join(TDCC_DIR, "tdcc_summary.csv")
    if os.path.exists(summary_path):
        old = pd.read_csv(summary_path, dtype={"Ticker": str})
        old["Date"] = pd.to_datetime(old["Date"])
        combined = pd.concat([old, new_summary]).drop_duplicates(
            ["Date", "Ticker"], keep="last"
        )
    else:
        combined = new_summary

    combined.to_csv(summary_path, index=False, encoding="utf-8-sig")
    print(
        f"  Summary: {len(combined)} 筆, "
        f"{combined['Ticker'].nunique()} 檔, "
        f"{combined['Date'].nunique()} 週",
        flush=True,
    )
    return True


if __name__ == "__main__":
    try:
        fetch_and_save()
    except Exception as e:
        print(f"  [錯誤] {e}")
        sys.exit(1)
