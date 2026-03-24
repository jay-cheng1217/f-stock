# ==============================================================================
# 0. 匯入函式庫
# ==============================================================================
import os
import re
import html
import pandas as pd
import shutil
import time
import random
from bs4 import BeautifulSoup
from datetime import datetime
from dateutil.relativedelta import relativedelta
from tqdm.auto import tqdm
import requests # <--- 主要變更點：改用 requests
import importlib

# ==============================================================================
# 1. 參數設定
# ==============================================================================
DAILY_K_DIR = r'F:\股票分析\日K資料'
SAVE_DIR = r'F:\股票分析\月營收'
os.makedirs(SAVE_DIR, exist_ok=True)

LIMIT_TICKERS = None
OVERALL_END_DATE = datetime.today().replace(day=1) - relativedelta(days=1)
DEFAULT_START_DATE = datetime(2020, 1, 1)

RETRY_MAX = 3
RANDOM_DELAY_RANGE = (2.0, 5.0) 

FAIL_LOG_PATH = os.path.join(SAVE_DIR, "fetch_failures.log")

# ==============================================================================
# 2. 工具與解析函式 (內容不變)
# ==============================================================================

# ========= 憑證注入 =========
def enable_truststore():
    """嘗試注入 truststore 以使用 Windows 系統憑證。"""
    try:
        truststore = importlib.import_module("truststore")
        truststore.inject_into_ssl()
        print("[truststore] 已成功注入 Windows 系統憑證。")
        return True
    except Exception as e:
        print(f"[truststore] 注入失敗：{e}。將繼續使用 Python 預設憑證。")
        return False

def normalize_date_col(df: pd.DataFrame) -> pd.DataFrame:
    if 'Date' not in df.columns: return df
    def _norm(x):
        if pd.isna(x): return x
        s = str(x).strip()
        m = re.match(r'^\s*(\d{4})[-/](\d{1,2})\s*$', s)
        if m: return f"{m.group(1)}-{int(m.group(2)):02d}"
        return s if re.match(r'^\d{4}-\d{2}$', s) else s
    df['Date'] = df['Date'].apply(_norm)
    return df
    
def read_existing_months(csv_path: str) -> set[str]:
    if not os.path.exists(csv_path): return set()
    try:
        df = pd.read_csv(csv_path, dtype={'Date': str}, usecols=['Date'])
        df = normalize_date_col(df)
        return set(df['Date'].dropna().tolist())
    except (FileNotFoundError, pd.errors.EmptyDataError, ValueError):
        return set()

def roc_to_ad(roc_year: int) -> int: return roc_year + 1911

def extract_inner_html(full_html: str) -> str | None:
    soup = BeautifulSoup(full_html, "lxml")
    candidates = soup.select('form[name^=autoRunScript] input[name^=run]')
    for inp in candidates:
        val = (inp.get("value") or "").strip()
        if not val: continue
        raw = html.unescape(val)
        m = re.search(r"innerHTML\s*=\s*['\"](.+?)['\"];", raw, re.DOTALL)
        if not m: continue
        inner = m.group(1).replace(r"\/", "/").replace(r"\n", "\n").replace(r"\t", "\t").replace(r"\'", "'").replace(r'\"', '"')
        inner = html.unescape(inner)
        if "<table" in inner: return inner
    return None

def parse_revenue_data(html_text: str, source_ym: tuple[int, int]) -> dict | None:
    soup = BeautifulSoup(html_text, "lxml")
    main_tbl = soup.find("table", class_="hasBorder")
    if not main_tbl or "查無資料" in main_tbl.get_text(): return None
    try:
        company_name = None
        comp_name_tag = soup.find('td', class_='compName')
        if comp_name_tag and comp_name_tag.b and ')' in comp_name_tag.b.get_text():
            company_name = comp_name_tag.b.text.split(')')[1].split('公司提供')[0].strip()
        items = {}
        for tr in main_tbl.select("tr"):
            th, td = tr.find("th"), tr.find("td")
            if not (th and td): continue
            key = th.get_text(strip=True)
            val_str = td.get_text(strip=True).replace(",", "").replace("\xa0", "").replace("&nbsp;", "")
            try: val = float(val_str) if val_str else None
            except ValueError: val = None
            if key in items: items[f"累計_{key}"] = val
            else: items[key] = val
        if items.get("本月") is not None:
            return {'Date': f"{source_ym[0]}-{source_ym[1]:02d}", 'Name': company_name, 'Monthly_Revenue': items.get("本月"), 'Cumulative_Revenue': items.get("本年累計"), 'YoY_pct_change': items.get("增減百分比"), 'Cumulative_YoY_pct_change': items.get("累計_增減百分比")}
        return None
    except Exception: return None

def find_company_name_from_list(html_text: str, ticker: str) -> str | None:
    soup = BeautifulSoup(html_text, "lxml")
    tbl = soup.find("table", class_="hasBorder")
    if not tbl: return None
    for tr in tbl.select("tr")[1:]:
        tds = tr.find_all("td")
        if len(tds) >= 2 and tds[0].get_text(strip=True) == ticker:
            return tds[1].get_text(strip=True)
    return None

def month_range(start_dt: datetime, end_dt: datetime):
    cur = start_dt.replace(day=1)
    last = end_dt.replace(day=1)
    while cur <= last: yield (cur.year, cur.month); cur += relativedelta(months=1)

def build_ticker_list():
    if not os.path.isdir(DAILY_K_DIR):
        print(f"錯誤：找不到日K資料夾 '{DAILY_K_DIR}'")
        return []
    stems = [os.path.splitext(f)[0] for f in os.listdir(DAILY_K_DIR) if f.endswith('.csv')]
    numeric, skipped = [], []
    for s in stems: (numeric if s.isdigit() else skipped).append(s)
    if skipped: print(f"已略過非純數字代號 ({len(skipped)} 檔): {', '.join(sorted(skipped))}")
    return sorted(numeric)

# ==============================================================================
# 3. 同步抓取核心函式 (requests 版本)
# ==============================================================================
def fetch_month_sync(session: requests.Session, ticker: str, year: int, month: int):
    api_url = "https://mopsov.twse.com.tw/mops/web/ajax_t05st10_ifrs"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/96.0.4664.110 Safari/537.36", "Referer": "https://mopsov.twse.com.tw/mops/web/t05st10_ifrs"}
    roc_year = year - 1911
    
    for attempt in range(RETRY_MAX):
        try:
            delay = random.uniform(RANDOM_DELAY_RANGE[0], RANDOM_DELAY_RANGE[1])
            time.sleep(delay)
            
            list_payload = {"encodeURIComponent": "1", "step": "1", "firstin": "1", "off": "1", "queryName": "co_id", "inpuType": "co_id", "TYPEK": "all", "isnew": "false", "co_id": ticker, "year": str(roc_year), "month": f"{month:02d}"}
            # requests 會自動處理 redirects
            r1 = session.post(api_url, data=list_payload, headers=headers, timeout=30)
            r1.raise_for_status()

            soup1 = BeautifulSoup(r1.text, "lxml")
            form = soup1.select_one("form#t05st10_ifrs_form")
            detail_payload = list_payload.copy()
            detail_payload.update({"step": "2", "yearmonth": f"{roc_year}{month:02d}"})
            if form:
                for inp in form.select("input[name]"):
                    name, value = inp.get("name"), (inp.get("value") or "").strip()
                    if name and name not in detail_payload: detail_payload[name] = value

            r2 = session.post(api_url, data=detail_payload, headers=headers, timeout=30)
            r2.raise_for_status()

            parsed = parse_revenue_data(r2.text, (year, month))
            if not parsed:
                inner_html = extract_inner_html(r2.text)
                if inner_html: parsed = parse_revenue_data(inner_html, (year, month))
            
            return parsed

        except requests.exceptions.RequestException as e:
            if attempt >= RETRY_MAX - 1:
                tqdm.write(f"   -> 請求失敗 (T:{ticker} M:{year}-{month:02d}): {e}")
                return None
            else:
                tqdm.write(f"   -> 請求失敗，5秒後重試... (T:{ticker} M:{year}-{month:02d})")
                time.sleep(5)
    return None

# ==============================================================================
# 4. 主流程
# ==============================================================================
def main_incremental_update():
    enable_truststore()
    print("-" * 50)

    print("1. 正在準備任務 (增量更新模式)...")
    all_tickers = build_ticker_list()
    if not all_tickers: return

    if isinstance(LIMIT_TICKERS, int) and LIMIT_TICKERS > 0:
        all_tickers = all_tickers[:LIMIT_TICKERS]
    
    all_months_to_have = list(month_range(DEFAULT_START_DATE, OVERALL_END_DATE))
    
    print(f"\n2. 開始遍歷 {len(all_tickers)} 支股票，檢查並補齊缺失月份...")
    global_failed_tasks, touched_files, total_new_rows = [], 0, 0
    
    # ★★★ 主要變更點：改用 requests.Session() ★★★
    with requests.Session() as session:
        for ticker in tqdm(all_tickers, desc="整體進度"):
            final_path = os.path.join(SAVE_DIR, f"revenue_{ticker}.csv")
            
            existing_months_set = read_existing_months(final_path)
            months_to_fetch = [
                (y, m) for y, m in all_months_to_have 
                if f"{y}-{m:02d}" not in existing_months_set
            ]

            if not months_to_fetch:
                continue

            tqdm.write(f"\n--- 正在處理 Ticker: {ticker} (發現 {len(months_to_fetch)} 個缺失月份) ---")
            new_rows = []

            for year, month in tqdm(months_to_fetch, desc=f"補齊 {ticker} 資料", leave=False):
                res = fetch_month_sync(session, ticker, year, month)
                if res:
                    new_rows.append(res)
                else:
                    global_failed_tasks.append((ticker, year, month))
            
            if not new_rows:
                tqdm.write(f"-> 警告：Ticker {ticker} 本次未能補齊任何資料。")
                continue

            df_new = pd.DataFrame(new_rows)
            if os.path.exists(final_path) and os.path.getsize(final_path) > 0:
                try:
                    df_old = pd.read_csv(final_path, dtype={'Date': str})
                    df_out = pd.concat([df_old, df_new], ignore_index=True)
                except (pd.errors.EmptyDataError, FileNotFoundError):
                    df_out = df_new
            else:
                df_out = df_new

            df_out = normalize_date_col(df_out).drop_duplicates(subset=['Date'], keep='last').sort_values('Date', ascending=True)
            tmp_path = f"{final_path}.tmp"
            df_out.to_csv(tmp_path, index=False, encoding='utf-8-sig')
            shutil.move(tmp_path, final_path)
            
            tqdm.write(f"-> ✅ 已更新/儲存檔案：revenue_{ticker}.csv (新增 {len(new_rows)} 筆，總計 {len(df_out)} 筆)")
            touched_files += 1; total_new_rows += len(new_rows)

    with open(FAIL_LOG_PATH, "w", encoding="utf-8") as f:
        f.write(f"# 下載失敗紀錄 ({datetime.now().isoformat(timespec='seconds')})\n# Ticker,Year,Month\n")
        for ticker, year, month in global_failed_tasks: f.write(f"{ticker},{year},{month}\n")

    print("\n" + "="*50)
    print("✅ 所有股票增量更新完成！")
    print(f"🧾 本次共更新了 {touched_files} 檔，總計新增 {total_new_rows} 筆資料。")
    if global_failed_tasks: print(f"⚠️ 未能成功下載 {len(global_failed_tasks)} 筆月份資料，詳見：\n   {FAIL_LOG_PATH}")
    print("="*50)

# ==============================================================================
# 5. 執行
# ==============================================================================
if __name__ == '__main__':
    main_incremental_update()