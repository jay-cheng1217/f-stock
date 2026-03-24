# ==============================================================================
# 【最終萬用版｜本地 Windows】下載上櫃(TPEx)法人資料腳本
# - 自動判斷並解析「近期 aaData」與「歷史 tables」兩種 JSON 格式
# - 本地儲存：F:\股票分析\法人快取
# - SSL 相容：優先使用 OS 憑證庫 (truststore)；退而求其次 certifi-win32；最後才關驗證
# - 穩定性：Requests Session + Retry + UA + Timeout
# ==============================================================================

import os
import time
import datetime
import pandas as pd
import requests
from tqdm import tqdm
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import urllib3
pd.options.mode.chained_assignment = None

# -----------------------------
# 0) SSL 信任鏈相容處理（優先使用 OS trust store）
# -----------------------------
ssl_mode = "default"
try:
    import truststore
    truststore.inject_into_ssl()
    ssl_mode = "truststore"
except Exception:
    try:
        import certifi_win32  # 將 Windows 受信任根憑證補到 certifi
        ssl_mode = "certifi_win32"
    except Exception:
        ssl_mode = "default"

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
try:
    import certifi
    CERT_BUNDLE = certifi.where()
except Exception:
    CERT_BUNDLE = None

# -----------------------------
# 1) 參數設定（本地路徑）
# -----------------------------
FUND_CACHE_DIR = r'F:\股票分析\法人快取'
os.makedirs(FUND_CACHE_DIR, exist_ok=True)

START_DATE = '2025-10-15'
END_DATE = datetime.date.today().strftime('%Y-%m-%d')

# -----------------------------
# 2) 建立全域 Session（重試/UA/Timeout）
# -----------------------------
def build_session() -> requests.Session:
    s = requests.Session()
    retries = Retry(
        total=3,
        backoff_factor=0.6,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
        raise_on_status=False,
    )
    s.mount("https://", HTTPAdapter(max_retries=retries))
    s.headers.update({
        'User-Agent': ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                       'AppleWebKit/537.36 (KHTML, like Gecko) '
                       'Chrome/122.0.0.0 Safari/537.36'),
        'Referer': 'https://www.tpex.org.tw/zh-tw/mainboard/trading/major-institutional/detail/day.html',
    })
    return s

SESSION = build_session()

def safe_get(url, params=None, timeout=15):
    """
    先用已注入的 truststore / certifi-win32 嘗試；
    若失敗再指定 certifi bundle；
    再失敗最後以 verify=False（印警告）避免整批中斷。
    """
    # 1) 預設（已注入 truststore / certifi-win32 時通常可過）
    try:
        r = SESSION.get(url, params=params, timeout=timeout)
        r.raise_for_status()
        return r
    except Exception as e1:
        # 2) 指定 certifi bundle 再試
        if CERT_BUNDLE:
            try:
                r = SESSION.get(url, params=params, timeout=timeout, verify=CERT_BUNDLE)
                r.raise_for_status()
                return r
            except Exception as e2:
                last_err = e2
        else:
            last_err = e1
        # 3) 最後手段：關閉驗證
        print(f"[WARN] SSL 驗證失敗（{type(last_err).__name__}），改用 verify=False。")
        r = SESSION.get(url, params=params, timeout=timeout, verify=False)
        r.raise_for_status()
        return r

# -----------------------------
# 3) 萬用版抓取與解析
# -----------------------------
def fetch_fund_tpex_universal_csv(date_str, cache=True):
    """
    抓取櫃買(TPEx)三大法人買賣超資料。
    - 自動解析 'aaData'（近期）與 'tables'（歷史）兩種 JSON。
    - 儲存至 F:\股票分析\法人快取\fund_tpex_YYYYMMDD.csv
    """
    try:
        roc_year = int(date_str[:4]) - 1911
        tpex_date_str = f"{roc_year}/{date_str[5:7]}/{date_str[8:]}"
    except ValueError:
        return

    date8 = date_str.replace('-', '')
    cache_path = os.path.join(FUND_CACHE_DIR, f'fund_tpex_{date8}.csv')
    if cache and os.path.exists(cache_path):
        return

    url = 'https://www.tpex.org.tw/web/stock/3insti/daily_trade/3itrade_hedge_result.php'
    params = {'l': 'zh-tw', 'd': tpex_date_str, '_': str(int(time.time() * 1000))}

    try:
        resp = safe_get(url, params=params, timeout=20)
        data = resp.json()

        df = None
        # 近期格式：aaData
        if isinstance(data, dict) and data.get('aaData'):
            tmp = pd.DataFrame(data['aaData'])
            # 0:代號, 4:外資買賣超, 7:投信買賣超, 8:自營商買賣超(合計)
            df = tmp[[0, 4, 7, 8]].copy()
            df.columns = ['Ticker', 'Foreign_BuySell', 'Trust_BuySell', 'Dealer_BuySell']

        # 歷史格式：tables[0].data
        elif isinstance(data, dict) and data.get('tables') and data['tables'][0].get('data'):
            tmp = pd.DataFrame(data['tables'][0]['data'])
            # 0:代號, 4:外資, 7:投信, 10:自營(自營), 13:自營(避險)
            sel = tmp[[0, 4, 7, 10, 13]].copy()
            sel.columns = ['Ticker', 'Foreign_BuySell', 'Trust_BuySell', 'Dealer_Prop_BuySell', 'Dealer_Hedge_BuySell']
            # 數值清洗與合併
            for c in ['Foreign_BuySell', 'Trust_BuySell', 'Dealer_Prop_BuySell', 'Dealer_Hedge_BuySell']:
                sel[c] = pd.to_numeric(sel[c].astype(str).str.replace(',', ''), errors='coerce').fillna(0)
            sel['Dealer_BuySell'] = sel['Dealer_Prop_BuySell'] + sel['Dealer_Hedge_BuySell']
            df = sel[['Ticker', 'Foreign_BuySell', 'Trust_BuySell', 'Dealer_BuySell']]

        else:
            # 假日或無資料
            return

        # 儲存
        df['Date'] = date_str
        for col in ['Foreign_BuySell', 'Trust_BuySell', 'Dealer_BuySell']:
            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0).astype(int)

        df.to_csv(cache_path, index=False, encoding='utf-8-sig')

    except Exception as _:
        # 任何錯誤直接略過，維持批次不中斷
        return

# -----------------------------
# 4) 執行流程
# -----------------------------
print(f"[SSL] mode: {ssl_mode}")
print(f"\n準備下載上櫃(TPEx)法人資料，範圍：{START_DATE} 至 {END_DATE}")
print(f"資料將儲存至: {FUND_CACHE_DIR}\n")

date_range = pd.date_range(start=START_DATE, end=END_DATE, freq='D')

saved, skipped = 0, 0
for single_date in tqdm(date_range, desc="正在下載每日上櫃法人資料"):
    date_str = single_date.strftime('%Y-%m-%d')
    before = len(os.listdir(FUND_CACHE_DIR))
    fetch_fund_tpex_universal_csv(date_str, cache=True)
    after = len(os.listdir(FUND_CACHE_DIR))
    if after > before:
        saved += 1
    else:
        skipped += 1
    # 禮貌性間隔，避免過於頻繁
    time.sleep(0.25)

print("\n" + "="*56)
print(f"✅ 完成！新增 {saved} 檔，略過/無資料 {skipped} 天。")
print("="*56)
