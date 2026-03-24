# ==============================================================================
# 台股日K資料 技術指標批次計算（本地端版）
# ==============================================================================

import os
import pandas as pd
import numpy as np
from tqdm import tqdm

# ==============================================================================
# 1. 設定資料夾路徑
# ==============================================================================
DATA_DIR = r'F:\股票分析\日K資料'   # ⚠️ 注意前面加 r 以防止反斜線出錯

if not os.path.isdir(DATA_DIR):
    raise FileNotFoundError(f"❌ 找不到資料夾：{DATA_DIR}")
print(f"準備處理資料夾：{DATA_DIR}")

# ==============================================================================
# 2. 技術指標計算函式
# ==============================================================================
def rma(series: pd.Series, period: int) -> pd.Series:
    """Wilder's RMA（常用於 RSI、ATR）"""
    s = series.copy()
    sma = s.rolling(period, min_periods=period).mean()
    r = pd.Series(index=s.index, dtype='float64')
    first = sma.first_valid_index()
    if first is None:
        return r
    r.loc[first] = sma.loc[first]
    alpha = 1.0 / period
    for i in range(s.index.get_loc(first) + 1, len(s)):
        prev = r.iloc[i-1]
        val = s.iloc[i]
        r.iloc[i] = prev + alpha * (val - prev)
    return r

def rsi_wilder(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = rma(gain, period)
    avg_loss = rma(loss, period)
    rs = avg_gain / (avg_loss.replace(0, np.nan))
    return 100 - (100 / (1 + rs))

def atr_wilder(high, low, close, period=14):
    prev_close = close.shift(1)
    tr = pd.concat([
        (high - low).abs(),
        (high - prev_close).abs(),
        (low - prev_close).abs()
    ], axis=1).max(axis=1)
    return rma(tr, period)

def stoch_kd(high, low, close, k_period=9, k_smooth=3, d_period=3):
    lowest = low.rolling(k_period, min_periods=k_period).min()
    highest = high.rolling(k_period, min_periods=k_period).max()
    rsv = (close - lowest) / (highest - lowest)
    rsv = (rsv.replace([np.inf, -np.inf], np.nan) * 100).clip(0, 100)
    k = rsv.rolling(k_smooth, min_periods=k_smooth).mean()
    d = k.rolling(d_period, min_periods=d_period).mean()
    return k, d

# ==============================================================================
# 3. 主程式
# ==============================================================================
csv_files = [f for f in os.listdir(DATA_DIR) if f.lower().endswith('.csv')]
if not csv_files:
    print("⚠️ 資料夾中沒有 CSV 檔案。")

count_processed = 0
count_skipped = 0
MIN_ROWS = 60  # 最少筆數

for filename in tqdm(csv_files, desc="正在處理檔案"):
    file_path = os.path.join(DATA_DIR, filename)
    ticker = os.path.splitext(filename)[0]
    try:
        df = pd.read_csv(file_path)

        # 必要欄位檢查
        needed = ["Date", "Open", "High", "Low", "Close", "Volume"]
        if not all(col in df.columns for col in needed):
            print(f"⚠️ {ticker}: 欄位不完整，跳過。")
            count_skipped += 1
            continue

        # 日期排序
        df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
        df = df.dropna(subset=["Date"]).sort_values("Date").reset_index(drop=True)

        # 確保數值格式
        df[["Open", "High", "Low", "Close", "Volume"]] = df[
            ["Open", "High", "Low", "Close", "Volume"]
        ].apply(pd.to_numeric, errors="coerce").ffill().bfill()

        if len(df) < MIN_ROWS:
            print(f"⚠️ {ticker}: 資料過少（{len(df)}筆），跳過。")
            count_skipped += 1
            continue

        # 刪除舊欄位
        drop_cols = [
            'MA_5','MA_20','MA_60',
            'RSI_6','RSI_14',
            'BBL_20_2.0','BBM_20_2.0','BBU_20_2.0',
            'MACD_12_26_9','MACDh_12_26_9','MACDs_12_26_9',
            'K','D','ATR_14','VOL_MA_5','VOL_MA_20'
        ]
        df.drop(columns=[c for c in drop_cols if c in df.columns], errors='ignore', inplace=True)

        # --- 技術指標計算 ---
        c_open, c_high, c_low, c_close, c_vol = df["Open"], df["High"], df["Low"], df["Close"], df["Volume"]

        # 移動平均線
        df["MA_5"]  = c_close.rolling(5).mean()
        df["MA_20"] = c_close.rolling(20).mean()
        df["MA_60"] = c_close.rolling(60).mean()

        # RSI
        df["RSI_6"]  = rsi_wilder(c_close, 6)
        df["RSI_14"] = rsi_wilder(c_close, 14)

        # 布林通道
        ma20 = c_close.rolling(20).mean()
        std20 = c_close.rolling(20).std()
        df["BBM_20_2.0"] = ma20
        df["BBU_20_2.0"] = ma20 + 2 * std20
        df["BBL_20_2.0"] = ma20 - 2 * std20

        # MACD
        ema12 = c_close.ewm(span=12, adjust=False).mean()
        ema26 = c_close.ewm(span=26, adjust=False).mean()
        macd = ema12 - ema26
        signal = macd.ewm(span=9, adjust=False).mean()
        df["MACD_12_26_9"] = macd
        df["MACDs_12_26_9"] = signal
        df["MACDh_12_26_9"] = macd - signal

        # KD (9,3,3)
        k, d = stoch_kd(c_high, c_low, c_close)
        df["K"] = k
        df["D"] = d

        # ATR
        df["ATR_14"] = atr_wilder(c_high, c_low, c_close)

        # 成交量均線
        df["VOL_MA_5"] = c_vol.rolling(5).mean()
        df["VOL_MA_20"] = c_vol.rolling(20).mean()

        # 移除 inf
        df.replace([np.inf, -np.inf], np.nan, inplace=True)

        # 寫回
        df.to_csv(file_path, index=False)
        count_processed += 1

    except Exception as e:
        print(f"❌ {ticker} 發生錯誤: {e}")
        count_skipped += 1

print("\n" + "="*60)
print(f"✅ 計算完成！成功：{count_processed} 檔，跳過：{count_skipped} 檔")
print("="*60)
