# ==============================================================================
# backfill_balance_sheet.py - 回補 MOPS 資產負債表 (t163sb05)
#
# 提取: 流動資產、資產總計、流動負債、負債總計、股東權益、每股淨值
# 衍生: 負債比、流動比率、ROE、ROA (需搭配季報損益)
#
# 用法: python scripts/backfill_balance_sheet.py [--start-year 2020]
# ==============================================================================

import os
import re
import sys
import io
import time
import random
import argparse
from datetime import date

import pandas as pd
import numpy as np
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import urllib3
from bs4 import BeautifulSoup
from tqdm import tqdm

# Force UTF-8 output on Windows
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")

# --- SSL ---
try:
    import truststore
    truststore.inject_into_ssl()
except Exception:
    pass
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ==============================================================================
BASE_DIR = r"F:\stock"
BS_DIR = os.path.join(BASE_DIR, "資產負債")
os.makedirs(BS_DIR, exist_ok=True)

RATE_LIMIT = 5.0
MAX_RETRIES = 3

MOPS_URL = "https://mopsov.twse.com.tw/mops/web/ajax_t163sb05"
MOPS_REFERER = "https://mopsov.twse.com.tw/mops/web/t163sb05"


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


def fetch_balance_sheet(year_roc, season, typek):
    """從 MOPS 批次抓取資產負債表"""
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
            r = SESSION.post(MOPS_URL, data=payload, headers=headers, timeout=90, verify=False)
            r.raise_for_status()
            r.encoding = "utf-8"
            return r.text
        except Exception as e:
            if attempt < MAX_RETRIES - 1:
                tqdm.write(f"    重試 ({attempt+1}): {e}")
                time.sleep(RATE_LIMIT * 2)
            else:
                tqdm.write(f"    失敗: {e}")
                return None
    return None


def _clean_num(val):
    """清理數值字串"""
    if not val or val in ("--", "-", "N/A", ""):
        return np.nan
    cleaned = val.replace(",", "").replace(" ", "").strip()
    try:
        return float(cleaned)
    except (ValueError, TypeError):
        return np.nan


def parse_balance_sheet(html_text):
    """解析 MOPS 資產負債表 HTML

    一般公司表格 (23 欄):
    [0]代號 [1]名稱 [2]流動資產 [3]非流動資產 [4]資產總計
    [5]流動負債 [6]非流動負債 [7]負債總計 [8]股本
    ...
    [14]歸屬母公司權益 [18]權益總計 [22]每股淨值
    """
    if not html_text:
        return pd.DataFrame()

    soup = BeautifulSoup(html_text, "lxml")
    tables = soup.find_all("table", class_="hasBorder")
    if not tables:
        return pd.DataFrame()

    all_rows = []
    for table in tables:
        trs = table.find_all("tr")
        for tr in trs:
            tds = [td.get_text(strip=True) for td in tr.find_all("td")]
            if len(tds) < 19:
                continue

            ticker = tds[0].replace(",", "").strip()
            if not re.match(r"^\d{4,6}$", ticker):
                continue

            row = {
                "Ticker": ticker,
                "Name": tds[1].strip(),
                "Current_Assets": _clean_num(tds[2]),
                "Total_Assets": _clean_num(tds[4]),
                "Current_Liabilities": _clean_num(tds[5]),
                "Total_Liabilities": _clean_num(tds[7]),
                "Share_Capital": _clean_num(tds[8]),
                "Parent_Equity": _clean_num(tds[14]),
                "Total_Equity": _clean_num(tds[18]),
            }

            # 每股淨值 (可能在不同位置)
            if len(tds) > 22:
                row["Book_Value_Per_Share"] = _clean_num(tds[22])
            elif len(tds) > 19:
                row["Book_Value_Per_Share"] = _clean_num(tds[-1])
            else:
                row["Book_Value_Per_Share"] = np.nan

            all_rows.append(row)

    if all_rows:
        return pd.DataFrame(all_rows)
    return pd.DataFrame()


def get_latest_available_quarter():
    today = date.today()
    y, m = today.year, today.month
    if m >= 11:
        return y, 3
    elif m >= 8:
        return y, 2
    elif m >= 5:
        return y, 1
    elif m >= 3:
        return y - 1, 4
    else:
        return y - 1, 3


def main():
    parser = argparse.ArgumentParser(description="回補 MOPS 資產負債表")
    parser.add_argument("--start-year", type=int, default=2020)
    parser.add_argument("--start-quarter", type=int, default=1)
    args = parser.parse_args()

    end_year, end_q = get_latest_available_quarter()

    print("=" * 60)
    print("回補 MOPS 資產負債表 (t163sb05)")
    print(f"  範圍: {args.start_year}Q{args.start_quarter} ~ {end_year}Q{end_q}")
    print(f"  儲存: {BS_DIR}")
    print("=" * 60)

    quarters = []
    for y in range(args.start_year, end_year + 1):
        for q in range(1, 5):
            if y == args.start_year and q < args.start_quarter:
                continue
            if y == end_year and q > end_q:
                break
            quarters.append((y, q))

    pending = [(y, q) for y, q in quarters
               if not os.path.exists(os.path.join(BS_DIR, f"bs_{y}Q{q}.csv"))]

    print(f"  季度共 {len(quarters)} 個, 待下載 {len(pending)} 個")

    if not pending:
        print("  全部完成!")
        return

    success = 0
    fail = 0

    for y, q in tqdm(pending, desc="資產負債表"):
        filepath = os.path.join(BS_DIR, f"bs_{y}Q{q}.csv")
        if os.path.exists(filepath):
            continue

        roc_year = y - 1911
        all_data = []

        for typek in ["sii", "otc"]:
            market = "TWSE" if typek == "sii" else "OTC"
            html = fetch_balance_sheet(roc_year, q, typek)
            if html:
                df = parse_balance_sheet(html)
                if not df.empty:
                    df["Market"] = market
                    all_data.append(df)
                    tqdm.write(f"    {y}Q{q} ({market}): {len(df)} 筆")
            time.sleep(RATE_LIMIT + random.uniform(0, 3))

        if all_data:
            result = pd.concat(all_data, ignore_index=True)
            result["Year"] = y
            result["Season"] = q

            # 計算衍生比率
            result["Debt_Ratio"] = (
                result["Total_Liabilities"] / result["Total_Assets"].replace(0, np.nan) * 100
            ).round(2)
            result["Current_Ratio"] = (
                result["Current_Assets"] / result["Current_Liabilities"].replace(0, np.nan) * 100
            ).round(2)

            tmp = filepath + ".tmp"
            result.to_csv(tmp, index=False, encoding="utf-8-sig")
            os.replace(tmp, filepath)
            success += 1
        else:
            fail += 1

    print(f"\n完成: 成功 {success}, 失敗 {fail}")


if __name__ == "__main__":
    main()
