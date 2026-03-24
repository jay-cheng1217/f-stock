"""抓取 TWSE/TPEx 股票產業分類對應表

來源: TWSE ISIN 證券代號查詢 (strMode=2 上市, strMode=4 上櫃)
產出: ml/data/sector_mapping.csv
欄位: Ticker, Name, Sector, Market

用法: python scripts/fetch_sector_mapping.py
"""

import os
import re
import time
import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import urllib3
from bs4 import BeautifulSoup

try:
    import truststore
    truststore.inject_into_ssl()
except Exception:
    pass
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BASE_DIR = r"F:\stock"
OUTPUT_DIR = os.path.join(BASE_DIR, "ml", "data")
os.makedirs(OUTPUT_DIR, exist_ok=True)
OUTPUT_PATH = os.path.join(OUTPUT_DIR, "sector_mapping.csv")


def build_session():
    s = requests.Session()
    retries = Retry(
        total=5, backoff_factor=1.0,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "POST"],
    )
    s.mount("https://", HTTPAdapter(max_retries=retries))
    s.mount("http://", HTTPAdapter(max_retries=retries))
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    })
    return s


SESSION = build_session()


def fetch_isin_listing(mode: int, market_name: str) -> list[dict]:
    """從 TWSE ISIN 證券代號查詢取得股票清單 (含產業別)

    strMode=2: 上市
    strMode=4: 上櫃

    HTML 表格欄位:
      有價證券代號及名稱 | ISIN Code | 上市日 | 市場別 | 產業別 | CFICode | 備註
    """
    url = f"https://isin.twse.com.tw/isin/C_public.jsp?strMode={mode}"
    print(f"  抓取 {market_name} 股票清單 (strMode={mode})...")

    r = SESSION.get(url, timeout=60, verify=False)
    if r.status_code != 200:
        print(f"    HTTP {r.status_code}")
        return []

    # 嘗試多種編碼
    for enc in ["cp950", "big5", "utf-8", "big5hkscs"]:
        try:
            text = r.content.decode(enc)
            break
        except (UnicodeDecodeError, LookupError):
            continue
    else:
        text = r.content.decode("cp950", errors="replace")

    soup = BeautifulSoup(text, "lxml")
    table = soup.find("table", class_="h4")
    if not table:
        print("    找不到表格")
        return []

    records = []
    in_stock_section = False

    for tr in table.find_all("tr"):
        tds = tr.find_all("td")

        # 區段標題 (colspan=7): 「股票」、「ETF」、「特別股」等
        if len(tds) == 1 and tds[0].get("colspan"):
            section = tds[0].get_text(strip=True)
            in_stock_section = "股票" in section
            continue

        if not in_stock_section:
            continue

        if len(tds) < 5:
            continue

        # 欄位: 代號及名稱 | ISIN | 上市日 | 市場別 | 產業別 | CFI | 備註
        code_name = tds[0].get_text(strip=True)
        sector = tds[4].get_text(strip=True) if len(tds) > 4 else ""

        # 解析「1101　台泥」格式
        match = re.match(r"^(\d{4})\s+(.+)$", code_name)
        if not match:
            continue

        ticker = match.group(1)
        name = match.group(2).strip()

        if not sector:
            continue

        records.append({
            "Ticker": ticker,
            "Name": name,
            "Sector": sector,
            "Market": market_name,
        })

    print(f"    取得 {len(records)} 檔 {market_name} 股票")
    return records


def main():
    print("=" * 60)
    print("  建立股票產業分類對應表")
    print("=" * 60)

    all_records = []

    # 上市 (strMode=2)
    records = fetch_isin_listing(2, "上市")
    all_records.extend(records)

    time.sleep(3)

    # 上櫃 (strMode=4)
    records = fetch_isin_listing(4, "上櫃")
    all_records.extend(records)

    if not all_records:
        print("\n  [錯誤] 無法取得任何產業分類資料")
        return

    df = pd.DataFrame(all_records)
    df = df.drop_duplicates(subset=["Ticker"], keep="first")

    # 統計
    print(f"\n  總計: {len(df)} 檔股票")
    print(f"  產業別數: {df['Sector'].nunique()}")
    print(f"\n  產業分佈 (前 15):")
    for sector, count in df["Sector"].value_counts().head(15).items():
        print(f"    {sector}: {count}")

    # 和現有日K資料比對
    kline_dir = os.path.join(BASE_DIR, "日K資料")
    if os.path.isdir(kline_dir):
        kline_tickers = set(
            os.path.splitext(f)[0]
            for f in os.listdir(kline_dir)
            if f.endswith(".csv") and os.path.splitext(f)[0].isdigit()
        )
        matched = len(kline_tickers & set(df["Ticker"]))
        print(f"\n  日K資料 {len(kline_tickers)} 檔中，{matched} 檔有產業分類 ({matched/len(kline_tickers)*100:.1f}%)")

    # 儲存
    df.to_csv(OUTPUT_PATH, index=False, encoding="utf-8-sig")
    print(f"\n  已儲存: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
