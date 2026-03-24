# ==============================================================================
# backfill_eps.py - 回補季報 EPS 資料 (MOPS 公開資訊觀測站)
#
# 來源: https://mopsov.twse.com.tw/mops/web/ajax_t163sb04
# 使用與 twstock.py 相同的 MOPS POST 格式和 session 模式
#
# 用法: python scripts/backfill_eps.py [--start-year 2020]
# ==============================================================================

import os
import re
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
EPS_DIR = os.path.join(BASE_DIR, "季報財務")
os.makedirs(EPS_DIR, exist_ok=True)

RATE_LIMIT = 5.0  # seconds between requests
MAX_RETRIES = 3

# MOPS EPS 頁面 (t163sb04 = 每股盈餘彙總表)
MOPS_URL = "https://mopsov.twse.com.tw/mops/web/ajax_t163sb04"
MOPS_REFERER = "https://mopsov.twse.com.tw/mops/web/t163sb04"


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
        "Referer": MOPS_REFERER,
    })
    return s


SESSION = build_session()


# ==============================================================================
# MOPS EPS 抓取
# ==============================================================================
def fetch_mops_eps(year_roc, season, typek):
    """
    從 MOPS 批次抓取每股盈餘 (全部公司)
    year_roc: 民國年 (e.g., 113)
    season: 1-4
    typek: 'sii' (上市) or 'otc' (上櫃)
    Returns: HTML text or None
    """
    payload = {
        "encodeURIComponent": "1",
        "step": "2",
        "firstin": "1",
        "off": "1",
        "TYPEK": typek,
        "year": str(year_roc),
        "season": str(season),
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Referer": MOPS_REFERER,
        "Content-Type": "application/x-www-form-urlencoded",
    }

    for attempt in range(MAX_RETRIES):
        try:
            r = SESSION.post(MOPS_URL, data=payload, headers=headers, timeout=60, verify=False)
            r.raise_for_status()
            return r.text
        except Exception as e:
            if attempt < MAX_RETRIES - 1:
                tqdm.write(f"    MOPS {year_roc}Q{season} ({typek}) 重試 ({attempt+1}/{MAX_RETRIES}): {e}")
                time.sleep(RATE_LIMIT * 2)
            else:
                tqdm.write(f"    MOPS {year_roc}Q{season} ({typek}) 失敗: {e}")
                return None

    return None


def parse_mops_eps_tables(html_text):
    """
    解析 MOPS 每股盈餘 HTML 表格
    t163sb04 欄位:
      公司代號, 公司名稱, 基本每股盈餘(元), 稀釋每股盈餘(元), ...
    """
    if not html_text:
        return pd.DataFrame()

    soup = BeautifulSoup(html_text, "lxml")
    tables = soup.find_all("table", class_="hasBorder")
    if not tables:
        return pd.DataFrame()

    all_rows = []
    for table in tables:
        for tr in table.find_all("tr"):
            tds = tr.find_all("td")
            if len(tds) < 4:
                continue
            vals = [td.get_text(strip=True) for td in tds]

            # 第一欄必須是 4-6 位數字的股票代號
            ticker = vals[0].replace(",", "").strip()
            if not re.match(r"^\d{4,6}$", ticker):
                continue

            name = vals[1].strip()

            # 基本每股盈餘 (最後一欄，MOPS t163sb04 格式)
            # 注意: 欄位數因產業而異 (銀行 22 欄, 一般 30 欄)，
            # 但基本每股盈餘固定在最後一欄
            eps_basic_raw = vals[-1] if vals else ""

            try:
                cleaned = eps_basic_raw.replace(",", "").replace("--", "").strip()
                eps_basic = float(cleaned) if cleaned not in ("", "-", "N/A") else None
            except (ValueError, TypeError):
                eps_basic = None

            all_rows.append({
                "Ticker": ticker,
                "Name": name,
                "EPS_Basic": eps_basic,
            })

    if all_rows:
        return pd.DataFrame(all_rows)
    return pd.DataFrame()


# ==============================================================================
# 季度工具
# ==============================================================================
def get_latest_available_quarter():
    """
    根據今天日期判斷最新可用季度
    季報公布時程: Q1->5月, Q2->8月, Q3->11月, Q4->隔年3月底
    """
    today = date.today()
    y = today.year
    m = today.month

    if m >= 11:
        return y, 3
    elif m >= 8:
        return y, 2
    elif m >= 5:
        return y, 1
    elif m >= 3:
        # Q4 部分公司 3月中就公布，嘗試抓取
        return y - 1, 4
    else:
        return y - 1, 3


def generate_quarters(start_year, start_q, end_year, end_q):
    """產生季度列表"""
    quarters = []
    for y in range(start_year, end_year + 1):
        for q in range(1, 5):
            if y == start_year and q < start_q:
                continue
            if y == end_year and q > end_q:
                break
            quarters.append((y, q))
    return quarters


# ==============================================================================
# 主程式
# ==============================================================================
def main():
    parser = argparse.ArgumentParser(description="回補季報 EPS 資料 (MOPS)")
    parser.add_argument("--start-year", type=int, default=2020,
                        help="起始年份 (預設 2020)")
    parser.add_argument("--start-quarter", type=int, default=1,
                        help="起始季度 1-4 (預設 1)")
    args = parser.parse_args()

    end_year, end_q = get_latest_available_quarter()

    print("=" * 60)
    print("回補季報 EPS 資料 (MOPS)")
    print(f"  範圍: {args.start_year}Q{args.start_quarter} ~ {end_year}Q{end_q}")
    print(f"  儲存: {EPS_DIR}")
    print("=" * 60)

    quarters = generate_quarters(args.start_year, args.start_quarter, end_year, end_q)

    # 過濾已下載的季度
    pending_quarters = []
    for y, q in quarters:
        filepath = os.path.join(EPS_DIR, f"eps_{y}Q{q}.csv")
        if not os.path.exists(filepath):
            pending_quarters.append((y, q))

    print(f"  季度共 {len(quarters)} 個, 待下載 {len(pending_quarters)} 個, 已完成 {len(quarters) - len(pending_quarters)} 個")

    if not pending_quarters:
        print("  所有季度皆已下載完畢!")
        return

    success_count = 0
    fail_count = 0
    failed_quarters = []

    for y, q in tqdm(pending_quarters, desc="EPS 季報"):
        filepath = os.path.join(EPS_DIR, f"eps_{y}Q{q}.csv")

        # 再次檢查
        if os.path.exists(filepath):
            continue

        roc_year = y - 1911
        all_data = []

        for typek in ["sii", "otc"]:
            market_label = "TWSE" if typek == "sii" else "OTC"

            html_text = fetch_mops_eps(roc_year, q, typek)
            if html_text:
                df = parse_mops_eps_tables(html_text)
                if not df.empty:
                    df["Market"] = market_label
                    all_data.append(df)
                    tqdm.write(f"    {y}Q{q} ({market_label}): {len(df)} 筆")
                else:
                    tqdm.write(f"    {y}Q{q} ({market_label}): 無資料或解析失敗")
            else:
                tqdm.write(f"    {y}Q{q} ({market_label}): 抓取失敗")

            time.sleep(RATE_LIMIT + random.uniform(0, 3))

        if all_data:
            result = pd.concat(all_data, ignore_index=True)
            result["Year"] = y
            result["Season"] = q

            # 確保欄位順序
            cols = ["Ticker", "Name", "EPS_Basic", "Market", "Year", "Season"]
            result = result[cols]

            tmp_path = filepath + ".tmp"
            result.to_csv(tmp_path, index=False, encoding="utf-8-sig")
            os.replace(tmp_path, filepath)

            success_count += 1
            tqdm.write(f"  {y}Q{q}: {len(result)} 筆已儲存")
        else:
            fail_count += 1
            failed_quarters.append(f"{y}Q{q}")

    # 摘要
    print("\n" + "=" * 60)
    print("回補完成")
    print(f"  成功: {success_count} 季")
    print(f"  失敗: {fail_count} 季")
    if failed_quarters:
        print(f"  失敗季度: {', '.join(failed_quarters[:20])}")
    print("=" * 60)


if __name__ == "__main__":
    main()
