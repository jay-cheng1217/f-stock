# ===========================================
# 台股 融資融券（上市/上櫃）- 本機下載 + 增量清理 + 合併輸出
# 目錄：
#   原始日檔：F:\股票分析\原始融資資料
#   清理總表：F:\股票分析\清理後資料\cleaned_all_margin_data.csv
# ===========================================
import os
import time
import json
import pandas as pd
from datetime import datetime, timedelta, date
import requests
from urllib3.util.retry import Retry
from requests.adapters import HTTPAdapter

# --- (可選) 使用系統信任庫，解決 SSL 憑證問題 ---
try:
    import truststore
    truststore.inject_into_ssl()
except Exception:
    pass

# ===========================================
# 1) 參數設定
# ===========================================
# 【開關】True：全量重建（刪舊合併表，清理所有日檔）；False：只清理缺的日期（增量）
FORCE_REFRESH = False

RAW_MARGIN_DIR = r'F:\股票分析\原始融資資料'
CLEANED_DATA_DIR = r'F:\股票分析\清理後資料'
os.makedirs(RAW_MARGIN_DIR, exist_ok=True)
os.makedirs(CLEANED_DATA_DIR, exist_ok=True)

FINAL_CLEANED_FILE_PATH = os.path.join(CLEANED_DATA_DIR, 'cleaned_all_margin_data.csv')

def get_previous_trading_day():
    d = date.today() - timedelta(days=1)
    while d.weekday() >= 5:  # 5=Sat, 6=Sun
        d -= timedelta(days=1)
    return d

START_DATE = date(2025, 9, 1)
END_DATE = get_previous_trading_day()

# requests Session with retry
session = requests.Session()
session.headers.update({
    'User-Agent': 'Mozilla/5.0',
    'Referer': 'https://www.twse.com.tw/'
})
session.mount('https://', HTTPAdapter(max_retries=Retry(
    total=5, backoff_factor=1.2,
    status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods=['GET']
)))

# ===========================================
# 2) 下載功能
# ===========================================
def download_twse_margin(d: date, save_path: str):
    """下載「上市」融資融券（CSV）"""
    if os.path.exists(save_path):
        return "exists"
    date_str = d.strftime('%Y%m%d')
    url = f'https://www.twse.com.tw/exchangeReport/MI_MARGN?response=csv&date={date_str}&selectType=ALL'
    try:
        r = session.get(url, timeout=20)
        txt = r.text.strip()
        if r.status_code == 200 and len(txt) > 500:
            with open(save_path, 'w', encoding='utf-8') as f:
                f.write(txt)
            return "ok"
        return "no_data"
    except Exception as e:
        return f"error:{e}"

def download_tpex_margin(d: date, save_path: str):
    """下載「上櫃」融資融券（JSON）"""
    if os.path.exists(save_path):
        return "exists"
    roc_year = d.year - 1911
    tpex_date_str = f"{roc_year}/{d.strftime('%m/%d')}"
    url = 'https://www.tpex.org.tw/web/stock/margin_trading/margin_balance/margin_bal_result.php'
    params = {'l': 'zh-tw', 'd': tpex_date_str, '_': str(int(time.time() * 1000))}
    headers = {
        'User-Agent': 'Mozilla/5.0',
        'Referer': 'https://www.tpex.org.tw/web/stock/margin_trading/margin_balance/margin_bal.htm',
    }
    try:
        r = session.get(url, params=params, headers=headers, timeout=20)
        r.raise_for_status()
        txt = r.text.strip()
        if txt:
            with open(save_path, 'w', encoding='utf-8-sig') as f:
                f.write(txt)
            return "ok"
        return "no_data"
    except Exception as e:
        return f"error:{e}"

# ===========================================
# 3) 清理與標準化
# ===========================================
TARGET_COLUMNS = ['Date', 'Ticker', 'Margin_Balance', 'Short_Balance']

def clean_twse_margin_df(file_path: str, date_str: str) -> pd.DataFrame:
    """清理「上市」CSV，容錯解析欄位"""
    try:
        if not os.path.exists(file_path) or os.path.getsize(file_path) < 200:
            return pd.DataFrame(columns=TARGET_COLUMNS)

        # 找到表頭開端
        start_line = None
        with open(file_path, 'r', encoding='utf-8') as f:
            for i, line in enumerate(f):
                if '代號' in line and '名稱' in line:
                    start_line = i
                    break
        if start_line is None:
            return pd.DataFrame(columns=TARGET_COLUMNS)

        df = pd.read_csv(file_path, encoding='utf-8', skiprows=start_line)
        df.columns = df.columns.str.strip()

        required_cols = ['代號', '今日餘額', '今日餘額.1']
        if not all(col in df.columns for col in required_cols):
            return pd.DataFrame(columns=TARGET_COLUMNS)

        df['代號'] = (
            df['代號'].astype(str)
            .str.replace('=', '', regex=False)
            .str.replace('"', '', regex=False)
            .str.strip()
        )
        df = df[df['代號'].str.match(r'^\d{4,6}$', na=False)]
        df = df[required_cols].copy()
        df.columns = ['Ticker', 'Margin_Balance', 'Short_Balance']
        df['Date'] = date_str

        for col in ['Margin_Balance', 'Short_Balance']:
            df[col] = pd.to_numeric(df[col].astype(str).str.replace(',', ''), errors='coerce').fillna(0).astype(int)

        return df[TARGET_COLUMNS]
    except Exception:
        return pd.DataFrame(columns=TARGET_COLUMNS)

def clean_tpex_margin_df(file_path: str, date_str: str) -> pd.DataFrame:
    """清理「上櫃」JSON，兼容不同欄位版型"""
    try:
        if not os.path.exists(file_path):
            return pd.DataFrame(columns=TARGET_COLUMNS)
        with open(file_path, 'r', encoding='utf-8-sig') as f:
            content = f.read()
        if not content.strip():
            return pd.DataFrame(columns=TARGET_COLUMNS)

        data = json.loads(content)
        if 'tables' in data and data.get('tables') and 'data' in data['tables'][0] and data['tables'][0].get('data'):
            df_raw = pd.DataFrame(data['tables'][0]['data'])
            df_selected = df_raw.iloc[:, [0, 6, 14]]
        elif 'aaData' in data and data.get('aaData'):
            df_raw = pd.DataFrame(data['aaData'])
            df_selected = df_raw.iloc[:, [0, 6, 12]]
        else:
            return pd.DataFrame(columns=TARGET_COLUMNS)

        df_selected.columns = ['Ticker', 'Margin_Balance', 'Short_Balance']
        df_selected['Date'] = date_str

        for col in ['Margin_Balance', 'Short_Balance']:
            df_selected[col] = pd.to_numeric(df_selected[col].astype(str).str.replace(',', ''), errors='coerce').fillna(0).astype(int)

        return df_selected[TARGET_COLUMNS]
    except Exception:
        return pd.DataFrame(columns=TARGET_COLUMNS)

# ===========================================
# 4) 主流程（下載 + 增量清理 + 合併）
# ===========================================
dates = pd.date_range(start=START_DATE, end=END_DATE)
print(f"\n步驟 1/3: 下載原始檔（{START_DATE:%Y-%m-%d} → {END_DATE:%Y-%m-%d}）...")

exist_cnt = ok_cnt = nodata_cnt = err_cnt = 0
for d in dates:
    d_date = d.date()
    ymd = d_date.strftime('%Y%m%d')

    twse_path = os.path.join(RAW_MARGIN_DIR, f'raw_margin_twse_{ymd}.csv')
    tpex_path = os.path.join(RAW_MARGIN_DIR, f'raw_margin_tpex_{ymd}.json')

    r1 = download_twse_margin(d_date, twse_path)
    r2 = download_tpex_margin(d_date, tpex_path)

    for r in (r1, r2):
        if r == "exists": exist_cnt += 1
        elif r == "ok": ok_cnt += 1
        elif r == "no_data": nodata_cnt += 1
        elif isinstance(r, str) and r.startswith("error:"): err_cnt += 1

    time.sleep(0.35)

print("原始資料下載檢查完成。")

# --- 讀入舊的清理後總表（若存在） ---
df_old = None
old_dates_set = set()
if not FORCE_REFRESH and os.path.exists(FINAL_CLEANED_FILE_PATH):
    try:
        usecols = ['Date', 'Ticker', 'Margin_Balance', 'Short_Balance']
        df_old = pd.read_csv(FINAL_CLEANED_FILE_PATH, usecols=usecols)
        if 'Date' in df_old.columns and not df_old.empty:
            old_dates_set = set(df_old['Date'].astype(str).unique())
    except Exception:
        # 如果讀舊表失敗，當作沒有舊資料，轉全量
        df_old = None
        old_dates_set = set()

# --- 決定要清理的日期（增量！） ---
if FORCE_REFRESH or df_old is None:
    dates_to_clean = [d.strftime('%Y-%m-%d') for d in dates]  # 全量
    mode = "全量重建" if FORCE_REFRESH else "首次建表/全量清理"
else:
    # 只清理「清理後總表中沒有的日期」（含新天數與歷史缺漏）
    dates_to_clean = [d.strftime('%Y-%m-%d') for d in dates if d.strftime('%Y-%m-%d') not in old_dates_set]
    mode = "增量清理" if dates_to_clean else "無需清理（已最新）"

print(f"\n步驟 2/3: 清理模式：{mode}")
print(f"需清理天數：{len(dates_to_clean)} 天")

all_new_cleaned = []
total_twse_new = 0
total_tpex_new = 0

for iso in dates_to_clean:
    ymd = iso.replace('-', '')
    twse_file = os.path.join(RAW_MARGIN_DIR, f'raw_margin_twse_{ymd}.csv')
    tpex_file = os.path.join(RAW_MARGIN_DIR, f'raw_margin_tpex_{ymd}.json')

    df_twse = clean_twse_margin_df(twse_file, iso)
    df_tpex = clean_tpex_margin_df(tpex_file, iso)

    total_twse_new += len(df_twse)
    total_tpex_new += len(df_tpex)

    all_new_cleaned.append(df_twse)
    all_new_cleaned.append(df_tpex)

# --- 合併輸出 ---
print("\n步驟 3/3: 合併輸出...")
if df_old is None:
    base = pd.DataFrame(columns=TARGET_COLUMNS)
else:
    base = df_old.copy()

if all_new_cleaned:
    df_new = pd.concat(all_new_cleaned, ignore_index=True)
else:
    df_new = pd.DataFrame(columns=TARGET_COLUMNS)

out = pd.concat([base, df_new], ignore_index=True)
if not out.empty:
    out.drop_duplicates(subset=['Date', 'Ticker'], keep='last', inplace=True)
    out.sort_values(by=['Ticker', 'Date'], inplace=True)

out.to_csv(FINAL_CLEANED_FILE_PATH, index=False, encoding='utf-8-sig')

# --- 總結 ---
print("\n" + "="*70)
print("✅ 完成：下載 + 增量清理 + 合併輸出（本機版）")
print("-" * 70)
print(f"下載統計：已存在 {exist_cnt}、成功 {ok_cnt}、空檔 {nodata_cnt}、錯誤 {err_cnt}")
print(f"清理模式：{mode}")
print(f"本次清理天數：{len(dates_to_clean)}")
print(f"本次新增筆數（上市）：{total_twse_new}，（上櫃）：{total_tpex_new}，合計：{total_twse_new + total_tpex_new}")
print(f"最終總筆數：{len(out)}")
if out.empty:
    print("⚠️ 警示：最終輸出為空，請檢查來源檔或清理邏輯。")
else:
    print(f"最後日期（資料範圍）：{out['Date'].min()} → {out['Date'].max()}")
print(f"輸出檔案：{FINAL_CLEANED_FILE_PATH}")
print("="*70)
