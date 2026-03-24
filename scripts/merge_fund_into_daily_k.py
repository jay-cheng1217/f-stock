# ==============================================================================
# 本地版：將「法人快取」合併到「日K資料」
# - 日K資料夾：F:\股票分析\日K資料
# - 法人快取：F:\股票分析\法人快取
# - 只更新欄位：Foreign_BuySell / Trust_BuySell / Dealer_BuySell
# ==============================================================================
import os
from datetime import datetime, timedelta
import pandas as pd
from tqdm import tqdm


def load_market_lookup():
    db_path = r'F:\stock\stock.duckdb'
    if not os.path.exists(db_path):
        return {}
    try:
        import duckdb

        con = duckdb.connect(db_path, read_only=True)
        rows = con.execute(
            """
            WITH ranked AS (
                SELECT
                    Ticker,
                    Market,
                    row_number() OVER (
                        PARTITION BY Ticker
                        ORDER BY Year DESC, Season DESC
                    ) AS rn
                FROM financials
                WHERE Market IN ('TWSE', 'OTC')
            )
            SELECT Ticker, Market
            FROM ranked
            WHERE rn = 1
            """
        ).fetchall()
        con.close()
        return {str(ticker): str(market) for ticker, market in rows if ticker and market}
    except Exception:
        return {}


def fund_cache_dates_by_market(cache_dir: str):
    markets = {'TWSE': set(), 'OTC': set()}
    for name in os.listdir(cache_dir):
        if not name.endswith('.csv'):
            continue
        if name.startswith('fund_tpex_'):
            date8 = name[len('fund_tpex_'):-4]
            if len(date8) == 8 and date8.isdigit():
                markets['OTC'].add(f'{date8[:4]}-{date8[4:6]}-{date8[6:]}')
        elif name.startswith('fund_'):
            date8 = name[len('fund_'):-4]
            if len(date8) == 8 and date8.isdigit():
                markets['TWSE'].add(f'{date8[:4]}-{date8[4:6]}-{date8[6:]}')
    return markets


def fill_missing_fund_values(df_daily_idx: pd.DataFrame, ticker: str, market_lookup: dict, cache_dates_by_market: dict):
    market = market_lookup.get(ticker)
    if market not in cache_dates_by_market:
        return 0

    covered_dates = cache_dates_by_market[market]
    if not covered_dates:
        return 0

    cols = ['Foreign_BuySell', 'Trust_BuySell', 'Dealer_BuySell']
    covered_mask = df_daily_idx.index.isin(covered_dates)
    if not covered_mask.any():
        return 0

    filled = 0
    for col in cols:
        missing_mask = covered_mask & df_daily_idx[col].isna()
        if missing_mask.any():
            filled += int(missing_mask.sum())
            df_daily_idx.loc[missing_mask, col] = 0.0
    return filled

# -----------------------------
# 1) 設定本地資料夾路徑
# -----------------------------
DATA_DIR = r'F:\股票分析\日K資料'
FUND_CACHE_DIR = r'F:\股票分析\法人快取'

if not (os.path.isdir(DATA_DIR) and os.path.isdir(FUND_CACHE_DIR)):
    print(f"錯誤：請確認資料夾存在：\n  日K資料 => {DATA_DIR}\n  法人快取 => {FUND_CACHE_DIR}")
    raise SystemExit(1)

# 可調整日期範圍（僅用於掃描快取檔名；若要全讀可直接掃資料夾）
START_DATE = datetime(2025, 9, 1)
END_DATE = datetime.now()

# -----------------------------
# 2) 輔助：讀取某天的快取檔（TWSE / TPEx）
# -----------------------------
def read_fund_cache_for_date(cache_dir: str, date_obj: datetime):
    """回傳該日的所有法人快取 DataFrame（list），若無則空清單。"""
    out = []
    date8 = date_obj.strftime('%Y%m%d')

    twse = os.path.join(cache_dir, f'fund_{date8}.csv')
    tpex = os.path.join(cache_dir, f'fund_tpex_{date8}.csv')

    for p in (twse, tpex):
        if os.path.exists(p):
            try:
                df = pd.read_csv(p, dtype={'Ticker': str, 'Date': str}, encoding='utf-8-sig')
                # 僅保留需要欄位（若有其他欄位一併保留也不影響，但合併時只用到三大法人）
                needed = ['Ticker', 'Date', 'Foreign_BuySell', 'Trust_BuySell', 'Dealer_BuySell']
                missing = [c for c in needed if c not in df.columns]
                if missing:
                    # 跳過欄位不完整的檔案
                    continue
                out.append(df[needed].copy())
            except Exception:
                # 單檔出錯無妨，略過
                pass
    return out

# -----------------------------
# 3) 預先載入與整合所有法人資料
# -----------------------------
print("步驟 1/3: 正在預先載入所有法人快取資料...")
date_range = pd.date_range(start=START_DATE, end=END_DATE, freq='D')

all_fund_dfs = []
for d in tqdm(date_range, desc="讀取法人快取檔"):
    all_fund_dfs.extend(read_fund_cache_for_date(FUND_CACHE_DIR, d))

# 若想直接掃資料夾（不依日期範圍），可改用：
# import glob
# for p in glob.glob(os.path.join(FUND_CACHE_DIR, "fund_*.csv")) + glob.glob(os.path.join(FUND_CACHE_DIR, "fund_tpex_*.csv")):
#     try:
#         df = pd.read_csv(p, dtype={'Ticker': str, 'Date': str}, encoding='utf-8-sig')
#         needed = ['Ticker', 'Date', 'Foreign_BuySell', 'Trust_BuySell', 'Dealer_BuySell']
#         if all(c in df.columns for c in needed):
#             all_fund_dfs.append(df[needed].copy())
#     except Exception:
#         pass

if not all_fund_dfs:
    print("錯誤：在指定日期範圍內找不到任何法人快取資料。")
    raise SystemExit(1)

# 合併成一張大表
all_fund_data = pd.concat(all_fund_dfs, ignore_index=True)

# 清理：同一 Ticker 同一 Date 若有多筆，取最後一筆（通常來源資料不會重複，但保險）
all_fund_data = (
    all_fund_data
    .sort_values(['Ticker', 'Date'])
    .drop_duplicates(subset=['Ticker', 'Date'], keep='last')
)

# 轉型，避免字串干擾
for col in ['Foreign_BuySell', 'Trust_BuySell', 'Dealer_BuySell']:
    all_fund_data.loc[:, col] = pd.to_numeric(all_fund_data[col], errors='coerce').fillna(0)

# 以 Ticker 分組，提高存取效能
grouped_fund = all_fund_data.groupby('Ticker', sort=False)
market_lookup = load_market_lookup()
cache_dates_by_market = fund_cache_dates_by_market(FUND_CACHE_DIR)
print(f"法人資料載入完成，共 {len(all_fund_data):,} 筆記錄，股票數：{len(grouped_fund):,} 支。")

# -----------------------------
# 4) 以股票為單位，高效合併
# -----------------------------
all_stock_files = [f for f in os.listdir(DATA_DIR) if f.lower().endswith('.csv')]
print(f"\n步驟 2/3: 準備更新 {len(all_stock_files):,} 支股票的日K資料...")

updated = 0
skipped = 0
zero_filled_cells = 0

for filename in tqdm(all_stock_files, desc="更新日K檔"):
    ticker = os.path.splitext(filename)[0]
    path = os.path.join(DATA_DIR, filename)

    # 只有這支股票在法人表中才處理
    has_fund_rows = ticker in grouped_fund.groups
    if (not has_fund_rows) and (ticker not in market_lookup):
        skipped += 1
        continue

    try:
        df_daily = pd.read_csv(path, dtype={'Date': str}, encoding='utf-8-sig')

        # 確保目標欄位存在
        for col in ['Foreign_BuySell', 'Trust_BuySell', 'Dealer_BuySell']:
            if col not in df_daily.columns:
                df_daily.loc[:, col] = 0.0

        # 取該股票的法人資料，並按 Date 去重（保險）
        # 以 Date 為索引進行對齊更新
        df_daily_idx = df_daily.set_index('Date')

        # pandas.DataFrame.update：只會覆蓋相同 index/column 的值，且不會用 NaN 覆蓋有值
        if has_fund_rows:
            sfd = grouped_fund.get_group(ticker).copy()
            sfd = (
                sfd.sort_values('Date')
                   .drop_duplicates(subset=['Date'], keep='last')
                   .set_index('Date')
            )
            sfd = sfd[['Foreign_BuySell', 'Trust_BuySell', 'Dealer_BuySell']]
            df_daily_idx.update(sfd)
        zero_filled_cells += fill_missing_fund_values(df_daily_idx, ticker, market_lookup, cache_dates_by_market)

        # 寫回檔案
        df_out = df_daily_idx.reset_index()
        df_out.to_csv(path, index=False, encoding='utf-8-sig')
        updated += 1

    except Exception:
        # 單檔錯誤跳過，避免整批中斷
        skipped += 1
        continue

print("\n步驟 3/3: 完成所有資料合併！")
print("\n" + "="*56)
print(f"✅ 合併完成：更新 {updated:,} 檔，跳過 {skipped:,} 檔。")
print("="*56)
