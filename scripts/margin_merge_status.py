import os
import json
import pandas as pd
from datetime import datetime, timedelta
from tqdm import tqdm

# =========================
# 本機路徑設定
# =========================
# 來源：清理後的融資融券總表
CLEANED_DATA_DIR = r'F:\股票分析\清理後資料'
# 目的地：所有個股日K CSV 的資料夾（檔名＝股票代碼，例如 2330.csv）
DAILY_K_DIR = r'F:\股票分析\日K資料'

MARGIN_DATA_FILE = os.path.join(CLEANED_DATA_DIR, 'cleaned_all_margin_data.csv')
# 追蹤上次合併到哪一天（用於增量）
MERGE_STATUS_FILE = os.path.join(CLEANED_DATA_DIR, 'margin_merge_status.json')

# 檢查路徑
if not os.path.exists(MARGIN_DATA_FILE):
    raise SystemExit(f"錯誤：找不到清理後總表：{MARGIN_DATA_FILE}")
if not os.path.isdir(DAILY_K_DIR):
    raise SystemExit(f"錯誤：找不到日K資料夾：{DAILY_K_DIR}")

# =========================
# 載入清理後總表（所有日期）
# =========================
print("步驟 1/3：載入清理後的融資融券總表…")
try:
    all_margin = pd.read_csv(
        MARGIN_DATA_FILE,
        dtype={'Ticker': str, 'Date': str},
        encoding='utf-8-sig'
    )
    print(f"載入完成，共 {len(all_margin):,} 筆。")
except Exception as e:
    raise SystemExit(f"讀取失敗：{e}")

# =========================
# 決定增量範圍（從 last_merged_date+1 起）
# =========================
start_date_for_merge = None
try:
    with open(MERGE_STATUS_FILE, 'r', encoding='utf-8') as f:
        status = json.load(f)
        last_merged_date_str = status.get('last_merged_date')
        if last_merged_date_str:
            last_merged_date = datetime.strptime(last_merged_date_str, '%Y-%m-%d').date()
            start_date_for_merge = (last_merged_date + timedelta(days=1)).strftime('%Y-%m-%d')
            print(f"偵測到前次合併至：{last_merged_date_str}，本次將自 {start_date_for_merge} 起增量。")
except FileNotFoundError:
    print("未找到狀態檔（首次執行）：將進行完整合併。")
except Exception as e:
    print(f"狀態檔讀取失敗：{e}，為保險將進行完整合併。")

if start_date_for_merge:
    data_to_merge = all_margin[all_margin['Date'] >= start_date_for_merge].copy()
else:
    data_to_merge = all_margin.copy()

if data_to_merge.empty:
    print("✅ 已是最新，無需合併。")
    raise SystemExit()

# 預先依 Ticker 分組，合併時更快
grp = data_to_merge.groupby('Ticker')
tickers_to_merge = set(grp.groups.keys())

# =========================
# 合併到個股日K
# =========================
print(f"步驟 2/3：準備合併到日K資料（{len(tickers_to_merge)} 檔股票涉及增量）…")

all_stock_files = [f for f in os.listdir(DAILY_K_DIR) if f.lower().endswith('.csv')]
updated_files = 0
skipped_files = 0
missing_files = 0

for filename in tqdm(all_stock_files, desc="合併至日K檔"):
    ticker = os.path.splitext(filename)[0]
    if ticker not in tickers_to_merge:
        skipped_files += 1
        continue

    daily_k_path = os.path.join(DAILY_K_DIR, filename)
    if not os.path.exists(daily_k_path):
        missing_files += 1
        continue

    try:
        df_daily = pd.read_csv(daily_k_path, dtype={'Date': str}, encoding='utf-8-sig')
        if 'Date' not in df_daily.columns:
            # 嘗試大小寫或其他誤差
            candidate = [c for c in df_daily.columns if c.lower() == 'date']
            if candidate:
                df_daily.rename(columns={candidate[0]: 'Date'}, inplace=True)
            else:
                # 無日期欄，跳過
                continue

        # 確保要寫入的欄位存在
        for col in ['Margin_Balance', 'Short_Balance']:
            if col not in df_daily.columns:
                df_daily[col] = 0

        stock_margin_data = grp.get_group(ticker).copy()
        # 對齊欄位
        stock_margin_data = stock_margin_data[['Date', 'Ticker', 'Margin_Balance', 'Short_Balance']]

        # 用 Date 當 key 更新
        df_daily_idx = df_daily.set_index('Date')
        stock_idx = stock_margin_data.set_index('Date')[['Margin_Balance', 'Short_Balance']]

        # 只更新已有日期的交集；若你想把不存在的日期也補進日K，需先決定如何補 OHLCV
        df_daily_idx.update(stock_idx)

        df_daily_out = df_daily_idx.reset_index()
        df_daily_out.to_csv(daily_k_path, index=False, encoding='utf-8-sig')
        updated_files += 1
    except Exception:
        # 若單檔有問題，略過以不中斷整體流程
        continue

# =========================
# 更新狀態檔 & 總結
# =========================
latest_date_in_merge = data_to_merge['Date'].max()
try:
    with open(MERGE_STATUS_FILE, 'w', encoding='utf-8') as f:
        json.dump({'last_merged_date': latest_date_in_merge}, f, ensure_ascii=False)
    status_msg = f"狀態已更新至：{latest_date_in_merge}"
except Exception as e:
    status_msg = f"⚠️ 狀態更新失敗：{e}"

print("\n" + "="*60)
print("✅ 合併完成（本機版）")
print("-" * 60)
print(f"涉及增量的股票數：{len(tickers_to_merge)}")
print(f"成功更新檔案數：{updated_files}")
print(f"略過（無增量）的檔案數：{skipped_files}")
print(f"找不到對應日K檔的股票數：{missing_files}")
print(status_msg)
print("="*60)
