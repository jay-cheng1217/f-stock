# ==============================================================================
# backfill_announcements.py - 回補 MOPS 重大訊息公告資料
#
# 來源: https://mopsov.twse.com.tw/mops/web/ajax_t05sr01_1
#
# 每月抓取一次，儲存所有上市櫃公司的重大訊息公告記錄。
# 用途: 計算消息面特徵 (公告頻率、異常公告量)
#
# 用法: python scripts/backfill_announcements.py [--start-year 2020]
# ==============================================================================

import os
import time
import random
import argparse
from datetime import datetime, date

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import urllib3
from bs4 import BeautifulSoup
from tqdm import tqdm

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
BASE_DIR = r"F:\stock"
NEWS_DIR = os.path.join(BASE_DIR, "新聞資料")
os.makedirs(NEWS_DIR, exist_ok=True)

RATE_LIMIT = 5.0
MAX_RETRIES = 3


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


# ==============================================================================
# 抓取重大訊息
# ==============================================================================
def fetch_announcements(year_roc, month, typek):
    """
    抓取 MOPS 重大訊息 (單月、單市場)
    year_roc: 民國年 (e.g. 114)
    month: 1-12
    typek: 'sii' (上市) or 'otc' (上櫃)
    Returns: list of dicts or None
    """
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

    for attempt in range(MAX_RETRIES):
        try:
            r = SESSION.post(url, data=payload, timeout=60, verify=False)
            if r.status_code != 200:
                time.sleep(RATE_LIMIT * 2)
                continue

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
                    # 過濾非股票代碼
                    if not ticker or not ticker[0].isdigit():
                        continue

                    name = tds[1].strip()
                    date_str = tds[2].strip()  # ROC format: 114/03/15
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
            if attempt < MAX_RETRIES - 1:
                tqdm.write(f"  MOPS {year_roc}/{month:02d} {typek} retry ({attempt+1}): {e}")
                time.sleep(RATE_LIMIT * 3)
            else:
                tqdm.write(f"  MOPS {year_roc}/{month:02d} {typek} failed: {e}")
                return None

    return None


# ==============================================================================
# 主程式
# ==============================================================================
def main():
    parser = argparse.ArgumentParser(description="回補 MOPS 重大訊息公告")
    parser.add_argument("--start-year", type=int, default=2020, help="起始年 (預設 2020)")
    parser.add_argument("--end-year", type=int, default=None, help="結束年 (預設今年)")
    args = parser.parse_args()

    end_year = args.end_year or date.today().year
    end_month = date.today().month if end_year == date.today().year else 12

    print("=" * 60)
    print("回補 MOPS 重大訊息公告")
    print(f"  範圍: {args.start_year}-01 ~ {end_year}-{end_month:02d}")
    print(f"  儲存: {NEWS_DIR}")
    print("=" * 60)

    # 產生月份列表
    months = []
    for y in range(args.start_year, end_year + 1):
        m_end = end_month if y == end_year else 12
        for m in range(1, m_end + 1):
            months.append((y, m))

    # 過濾已下載的月份
    pending = []
    for y, m in months:
        fpath = os.path.join(NEWS_DIR, f"announcements_{y}{m:02d}.csv")
        if not os.path.exists(fpath):
            pending.append((y, m))

    print(f"  總月數: {len(months)}, 待下載: {len(pending)}, 已完成: {len(months) - len(pending)}")

    if not pending:
        print("  全部完成!")
        return

    success = 0
    fail = 0

    for y, m in tqdm(pending, desc="重大訊息"):
        year_roc = y - 1911
        all_records = []

        # 上市
        records = fetch_announcements(year_roc, m, "sii")
        if records:
            all_records.extend(records)
        time.sleep(RATE_LIMIT + random.uniform(0, 2))

        # 上櫃
        records = fetch_announcements(year_roc, m, "otc")
        if records:
            all_records.extend(records)
        time.sleep(RATE_LIMIT + random.uniform(0, 2))

        fpath = os.path.join(NEWS_DIR, f"announcements_{y}{m:02d}.csv")
        if all_records:
            df = pd.DataFrame(all_records)
            df = df[["Ticker", "Name", "Date", "Time", "Title", "Market"]]
            tmp = fpath + ".tmp"
            df.to_csv(tmp, index=False, encoding="utf-8-sig")
            os.replace(tmp, fpath)
            success += 1
            tqdm.write(f"  {y}-{m:02d}: {len(df)} 則公告")
        else:
            # 空月份也存空檔，避免重複查詢
            pd.DataFrame(columns=["Ticker", "Name", "Date", "Time", "Title", "Market"]).to_csv(
                fpath, index=False, encoding="utf-8-sig"
            )
            success += 1

    print(f"\n完成: 成功 {success}, 失敗 {fail}")


if __name__ == "__main__":
    main()
