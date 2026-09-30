"""每日抓取 MOPS 重大訊息公告 — 用於消息面特徵

MOPS API (t05sr01_1) 不支援歷史月份查詢，
只能抓取「當下最新」的公告頁面。
因此改為每日執行，將當天新公告追加到對應月份檔案。

用法: python scripts/fetch_daily_news.py
"""

import os
import time
import random
from datetime import date

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import urllib3
from bs4 import BeautifulSoup

# --- SSL ---
try:
    import truststore
    truststore.inject_into_ssl()
except Exception:
    pass
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ==============================================================================
# 設定
# ==============================================================================
BASE_DIR = os.environ.get("STOCK_BASE_DIR") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NEWS_DIR = os.path.join(BASE_DIR, "新聞資料")
os.makedirs(NEWS_DIR, exist_ok=True)

RATE_LIMIT = 5.0


def build_session():
    s = requests.Session()
    retries = Retry(
        total=5, backoff_factor=1.0,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "POST"],
    )
    s.mount("https://", HTTPAdapter(max_retries=retries))
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://mopsov.twse.com.tw/mops/web/t05sr01_1",
    })
    return s


SESSION = build_session()


def fetch_latest_announcements(typek):
    """抓取 MOPS 最新重大訊息 (上市/上櫃)

    API 不論帶什麼年月參數都回傳同一頁最新公告，
    因此直接用當前年月查詢即可。
    """
    today = date.today()
    year_roc = today.year - 1911
    month = today.month

    url = "https://mopsov.twse.com.tw/mops/web/ajax_t05sr01_1"
    payload = {
        "encodeURIComponent": "1",
        "step": "2",
        "firstin": "1",
        "off": "1",
        "TYPEK": typek,
        "year": str(year_roc),
        "month": f"{month:02d}",
    }

    try:
        r = SESSION.post(url, data=payload, timeout=60, verify=False)
        if r.status_code != 200:
            print(f"  MOPS {typek} HTTP {r.status_code}")
            return []

        soup = BeautifulSoup(r.text, "lxml")
        tables = soup.find_all("table", class_="hasBorder")
        if not tables:
            return []

        records = []
        for table in tables:
            for tr in table.find_all("tr"):
                tds = [td.get_text(strip=True) for td in tr.find_all("td")]
                if len(tds) < 4:
                    continue

                ticker = tds[0].strip()
                if not ticker or not ticker[0].isdigit():
                    continue

                name = tds[1].strip()
                date_str = tds[2].strip()  # ROC: 115/03/16
                time_str = tds[3].strip()
                title = tds[4].strip() if len(tds) > 4 else ""

                # 轉換民國日期
                try:
                    parts = date_str.split("/")
                    ad_year = int(parts[0]) + 1911
                    ad_date = f"{ad_year}-{parts[1]}-{parts[2]}"
                except (ValueError, IndexError):
                    ad_date = ""

                records.append({
                    "Ticker": ticker,
                    "Name": name,
                    "Date": ad_date,
                    "Time": time_str,
                    "Title": title,
                    "Market": "上市" if typek == "sii" else "上櫃",
                })

        return records

    except Exception as e:
        print(f"  MOPS {typek} 失敗: {e}")
        return []


def main():
    print("=" * 60)
    print("  抓取 MOPS 最新重大訊息公告")
    print(f"  日期: {date.today()}")
    print("=" * 60)

    all_records = []

    # 上市
    print("  抓取上市 (sii)...")
    records = fetch_latest_announcements("sii")
    if records:
        all_records.extend(records)
        print(f"    取得 {len(records)} 則")
    else:
        print("    無資料")

    time.sleep(RATE_LIMIT + random.uniform(0, 2))

    # 上櫃
    print("  抓取上櫃 (otc)...")
    records = fetch_latest_announcements("otc")
    if records:
        all_records.extend(records)
        print(f"    取得 {len(records)} 則")
    else:
        print("    無資料")

    if not all_records:
        print("\n  今日無新公告")
        return

    df_new = pd.DataFrame(all_records)
    df_new = df_new[["Ticker", "Name", "Date", "Time", "Title", "Market"]]

    # 按實際日期分組，追加到對應月份檔案
    df_new["_ym"] = pd.to_datetime(df_new["Date"], errors="coerce").dt.strftime("%Y%m")
    total_added = 0

    for ym, group in df_new.groupby("_ym"):
        if pd.isna(ym):
            continue

        fpath = os.path.join(NEWS_DIR, f"announcements_{ym}.csv")
        group_out = group.drop(columns=["_ym"])

        old_count = 0
        if os.path.exists(fpath):
            df_old = pd.read_csv(fpath, encoding="utf-8-sig")
            old_count = len(df_old)
            # 去重: 同 Ticker + Date + Time 視為同一則
            combined = pd.concat([df_old, group_out], ignore_index=True)
            combined = combined.drop_duplicates(
                subset=["Ticker", "Date", "Time"], keep="last"
            )
        else:
            combined = group_out

        combined.to_csv(fpath, index=False, encoding="utf-8-sig")
        new_count = len(combined) - old_count
        total_added += max(new_count, 0)
        print(f"  {ym}: 檔案共 {len(combined)} 則公告")

    print(f"\n  完成: 新增 {total_added} 則公告")


if __name__ == "__main__":
    main()
