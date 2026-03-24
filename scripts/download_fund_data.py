import os
import pandas as pd
import requests
from io import StringIO
import datetime
from tqdm import tqdm
import glob
import truststore
truststore.inject_into_ssl()


# ============================================================
# 1. 設定本地資料夾路徑
# ============================================================
K_DIR = r'F:\股票分析\日K資料'         # 日K資料
法人快取DIR = r'F:\股票分析\法人快取'  # 法人快取資料
os.makedirs(法人快取DIR, exist_ok=True)

START_DATE = '2025-10-15'

# ============================================================
# 2. 定義下載函式
# ============================================================
def fetch_fund_csv(date_str, cache=True):
    cache_path = os.path.join(法人快取DIR, f'fund_{date_str}.csv')
    if cache and os.path.exists(cache_path):
        return
    url = f'https://www.twse.com.tw/fund/T86?response=csv&date={date_str}&selectType=ALL'
    r = requests.get(url)
    raw = r.text

    # 找header
    lines = [line for line in raw.split('\n') if '證券代號' in line or '證券名稱' in line]
    if not lines:
        return
    header_line = lines[0]
    content = []
    header_found = False
    for line in raw.split('\n'):
        if header_found:
            content.append(line)
        elif line.strip() == header_line.strip():
            header_found = True
            content.append(line)
    raw2 = '\n'.join(content)
    try:
        df = pd.read_csv(StringIO(raw2))
        df.columns = [c.replace('\ufeff', '').strip() for c in df.columns]
        if '證券代號' not in df.columns:
            return
        df = df[df['證券代號'].astype(str).str.isnumeric()]
        df = df.rename(columns={
            '證券代號': 'Ticker',
            '外陸資買賣超股數(不含外資自營商)': 'Foreign_BuySell',
            '投信買賣超股數': 'Trust_BuySell',
            '自營商買賣超股數': 'Dealer_BuySell',
        })
        df = df[['Ticker', 'Foreign_BuySell', 'Trust_BuySell', 'Dealer_BuySell']]
        df['Date'] = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"
        for col in ['Foreign_BuySell', 'Trust_BuySell', 'Dealer_BuySell']:
            df[col] = pd.to_numeric(df[col].astype(str).str.replace(',', ''), errors='coerce').fillna(0)
        df.to_csv(cache_path, index=False, encoding='utf-8-sig')
    except Exception:
        pass

# ============================================================
# 3. 統計所有日K有的日期
# ============================================================
csv_files = sorted(glob.glob(os.path.join(K_DIR, '*.csv')))
all_dates = set()
for f in tqdm(csv_files, desc='統計csv日期'):
    df = pd.read_csv(f, dtype={'Date': str})
    date_list = [str(d) for d in df['Date'].unique() if isinstance(d, str) and len(d) == 10 and d >= START_DATE]
    all_dates |= set(date_list)
all_dates = sorted(list(all_dates))

# ============================================================
# 4. 下載法人快取資料
# ============================================================
for date_str in tqdm(all_dates, desc='下載法人快取'):
    date8 = date_str.replace('-', '')
    fetch_fund_csv(date8, cache=True)

print('【法人資料快取已完成】')
