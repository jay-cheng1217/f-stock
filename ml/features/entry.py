"""進場信號特徵工程模組 v2

基於台股 39 萬筆回測驗證的量化因子，取代 v1 的主觀規則。
核心因子 (IC20 = Information Coefficient with 20-day forward return):
1. 均值回歸: price_vs_ma60  IC=-0.058 (股價低於 MA60 越多 → 後續漲越多)
2. 短期超賣: mom_20d         IC=-0.045 (20日動量為負 → 反彈機會大)
3. 逆向法人: inst_buy_ratio  IC=-0.028 (法人買越多 → 報酬越差)
4. 低波動:   volatility_20d  IC=-0.029 (低波動 → 報酬更好)
5. 年高動量: near_52w_high   IC=+0.030 (接近年高 → 延續上漲)

組合信號 IC20 = +0.068，8+ 分組合 20 日報酬 +3.00%，勝率 58%。
"""

import numpy as np
import pandas as pd


def compute_entry_features(df: pd.DataFrame) -> pd.DataFrame:
    """計算進場信號特徵。

    Parameters
    ----------
    df : pd.DataFrame
        單檔股票日K資料，需含 OHLCV + 法人欄位。

    Returns
    -------
    pd.DataFrame
        原始欄位 + 進場信號特徵
    """
    out = df.copy()
    close = out["Close"].astype(np.float64)
    high = out["High"].astype(np.float64)
    low = out["Low"].astype(np.float64)
    volume = out["Volume"].astype(np.float64)

    for col in ["Foreign_BuySell", "Trust_BuySell", "Dealer_BuySell"]:
        if col not in out.columns:
            out[col] = 0.0

    foreign = out["Foreign_BuySell"].astype(np.float64)
    trust = out["Trust_BuySell"].astype(np.float64)
    inst_net = foreign + trust

    # =========================================================
    # 1. 法人成本 (保留供前端顯示)
    # =========================================================
    typical_price = (high + low + close) / 3.0

    _abs_net = inst_net.abs()
    _ic = (typical_price * _abs_net).rolling(10, min_periods=1).sum()
    _iv = _abs_net.rolling(10, min_periods=1).sum()
    out["inst_cost_10d"] = np.where(_iv > 0, _ic / _iv, np.nan)

    inst_cost = pd.Series(out["inst_cost_10d"], dtype=np.float64)
    out["price_vs_inst_cost"] = np.where(
        inst_cost > 0, (close - inst_cost) / inst_cost, np.nan
    )

    # 法人 10 日累計淨買超 (張) — 正=淨買, 負=淨賣
    out["inst_net_10d"] = inst_net.rolling(10, min_periods=1).sum()
    # 法人部位佔成交量比例 (%) — 判斷法人影響力
    vol_10d = volume.rolling(10, min_periods=1).sum()
    out["inst_vol_pct_10d"] = np.where(
        vol_10d > 0,
        _abs_net.rolling(10, min_periods=1).sum() / vol_10d * 100,
        np.nan,
    )

    # =========================================================
    # 2. 核心因子計算
    # =========================================================
    ma20 = close.rolling(20, min_periods=5).mean()
    ma60 = close.rolling(60, min_periods=20).mean()

    # 均值回歸因子 (IC=-0.058)
    out["price_vs_ma20"] = (close - ma20) / ma20
    out["price_vs_ma60"] = (close - ma60) / ma60

    # 短期動量 (IC=-0.045)
    out["mom_20d"] = close.pct_change(20)

    # 波動率 (IC=-0.029)
    ret = close.pct_change()
    out["volatility_20d"] = ret.rolling(20, min_periods=5).std()

    # 法人買超比例 (IC=-0.028, 逆向指標)
    # 加權版: 淨買超金額佔成交量比例的 20 日均值，反映量的差異
    inst_vol_ratio = np.where(volume > 0, inst_net / volume, 0.0)
    inst_vol_ratio_s = pd.Series(inst_vol_ratio, index=out.index)
    out["inst_buy_ratio_20d"] = inst_vol_ratio_s.rolling(20, min_periods=5).mean()

    # 52 週位置 (IC=+0.030 near high)
    high_252 = high.rolling(252, min_periods=60).max()
    low_252 = low.rolling(252, min_periods=60).min()
    out["position_52w"] = (close - low_252) / (high_252 - low_252).replace(0, np.nan)

    # 距離 20 日高低點 (供前端顯示)
    out["dist_to_high_20d"] = close / high.rolling(20, min_periods=5).max() - 1
    out["dist_to_high_60d"] = close / high.rolling(60, min_periods=20).max() - 1

    # =========================================================
    # 3. 狀態辨識 (基於因子，非主觀規則)
    # =========================================================
    out["phase"] = _detect_regime(out)

    # =========================================================
    # 4. 組合信號評分 (0~10, 基於回測驗證)
    # =========================================================
    out["entry_score"] = _factor_score(out)

    # =========================================================
    # 5. 保留向下相容的舊欄位
    # =========================================================
    # vol_contraction_ratio (用於模型訓練特徵)
    atr_5 = _atr(high, low, close, 5)
    atr_20 = _atr(high, low, close, 20)
    out["vol_contraction_ratio"] = np.where(
        atr_20 > 0, atr_5 / atr_20, np.nan
    )

    # inst_accumulation (簡化版，保留介面)
    out["inst_accumulation"] = np.where(
        out["inst_buy_ratio_20d"] > 0.6, 1.0,
        np.where(out["inst_buy_ratio_20d"] > 0.4, 0.5, 0.0)
    )

    # volume profile (保留供前端)
    for window in [20, 60]:
        poc, va_low, va_high = _volume_profile(close, low, high, volume, window)
        out[f"poc_{window}d"] = poc
        out[f"va_low_{window}d"] = va_low
        out[f"va_high_{window}d"] = va_high

    out["price_vs_poc_20d"] = np.where(
        pd.Series(out["poc_20d"], dtype=np.float64) > 0,
        (close - pd.Series(out["poc_20d"], dtype=np.float64)) / pd.Series(out["poc_20d"], dtype=np.float64),
        np.nan,
    )

    # entry_price (不再建議具體價格，填 NaN)
    out["entry_price_low"] = np.nan
    out["entry_price_high"] = np.nan

    return out


def _atr(high, low, close, period):
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(period, min_periods=1).mean()


def _detect_regime(df):
    """狀態辨識 — 基於因子研究結果

    0 = 超賣低檔 (oversold): 偏離 MA60 > 5%, 短期動量為負
    1 = 盤整等待 (neutral):  無明顯方向
    2 = 強勢延續 (momentum): 接近年高，趨勢向上
    3 = 過熱警示 (overheated): 偏離 MA60 過大 (>15%)，波動率高
    """
    close = df["Close"].astype(np.float64)
    n = len(df)
    regime = np.full(n, np.nan)

    pma60 = df.get("price_vs_ma60", pd.Series(np.nan, index=df.index))
    pma20 = df.get("price_vs_ma20", pd.Series(np.nan, index=df.index))
    mom20 = df.get("mom_20d", pd.Series(np.nan, index=df.index))
    vol20 = df.get("volatility_20d", pd.Series(np.nan, index=df.index))
    pos52 = df.get("position_52w", pd.Series(np.nan, index=df.index))

    for i in range(60, n):
        # 核心因子任一為 NaN → 資料不足，不強行判斷 regime
        pm60_raw = pma60.iloc[i]
        pm20_raw = pma20.iloc[i]
        m20_raw = mom20.iloc[i]
        v20_raw = vol20.iloc[i]
        p52_raw = pos52.iloc[i]
        if np.isnan(pm60_raw) or np.isnan(m20_raw):
            continue  # 保持 NaN，不假裝知道
        pm60 = pm60_raw
        pm20 = pm20_raw if not np.isnan(pm20_raw) else 0
        m20 = m20_raw
        v20 = v20_raw if not np.isnan(v20_raw) else 0.02
        p52 = p52_raw if not np.isnan(p52_raw) else 0.5

        # 過熱: 遠離 MA60 + 高波動
        if pm60 > 0.15 and v20 > 0.025:
            regime[i] = 3
        # 過熱: 短期漲太多
        elif pm20 > 0.10 and m20 > 0.15:
            regime[i] = 3
        # 超賣: 低於 MA60 + 動量為負
        elif pm60 < -0.05 and m20 < -0.03:
            regime[i] = 0
        # 超賣: 嚴重偏離
        elif pm60 < -0.10:
            regime[i] = 0
        # 強勢: 接近年高 + 趨勢向上
        elif p52 > 0.80 and pm20 > 0:
            regime[i] = 2
        # 強勢: 穩定上升
        elif pm60 > 0.05 and m20 > 0.03 and v20 < 0.025:
            regime[i] = 2
        # 預設盤整
        else:
            regime[i] = 1

    return regime


def _factor_score(df):
    """組合信號評分 (0~10)

    基於 39 萬筆回測驗證的因子權重:
    - 均值回歸 (0~3): 股價低於 MA60 越多 → 反彈空間越大
    - 短期超賣 (0~2): 20日動量為負 → 超賣反彈
    - 逆向法人 (0~2): 法人買超比例低 → 反向買入時機
    - 低波動   (0~1.5): 波動率低 → 報酬更穩
    - 52週位置 (0~1.5): 接近年高(動量) 或年低(超賣)

    IC20 = +0.068, 8+分組勝率 58%, 報酬 +3.00%
    """
    close = df["Close"].astype(np.float64)
    n = len(close)
    score = np.zeros(n, dtype=np.float32)

    pma60 = df.get("price_vs_ma60", pd.Series(np.nan, index=df.index))
    mom20 = df.get("mom_20d", pd.Series(np.nan, index=df.index))
    ibr = df.get("inst_buy_ratio_20d", pd.Series(np.nan, index=df.index))
    vol20 = df.get("volatility_20d", pd.Series(np.nan, index=df.index))
    pos52 = df.get("position_52w", pd.Series(np.nan, index=df.index))

    for i in range(60, n):
        s = 0.0

        # --- 均值回歸 (0~3) ---
        pm = pma60.iloc[i]
        if not np.isnan(pm):
            if pm < -0.15:
                s += 3.0
            elif pm < -0.10:
                s += 2.5
            elif pm < -0.05:
                s += 2.0
            elif pm < 0:
                s += 1.0

        # --- 短期超賣 (0~2) ---
        m = mom20.iloc[i]
        if not np.isnan(m):
            if m < -0.15:
                s += 2.0
            elif m < -0.10:
                s += 1.5
            elif m < -0.05:
                s += 1.0
            elif m < 0:
                s += 0.5

        # --- 逆向法人 (0~2) ---
        ib = ibr.iloc[i]
        if not np.isnan(ib):
            if ib < 0.3:
                s += 2.0
            elif ib < 0.4:
                s += 1.5
            elif ib < 0.5:
                s += 1.0
            elif ib > 0.7:
                s -= 1.0  # 扣分：法人過度集中買

        # --- 低波動 (0~1.5) ---
        v = vol20.iloc[i]
        if not np.isnan(v):
            if v < 0.015:
                s += 1.5
            elif v < 0.020:
                s += 1.0
            elif v < 0.025:
                s += 0.5

        # --- 52週位置 (0~1.5) ---
        p = pos52.iloc[i]
        if not np.isnan(p):
            if p > 0.90:
                s += 1.5  # 動量延續
            elif p > 0.80:
                s += 1.0
            elif p < 0.10:
                s += 1.5  # 超賣反彈
            elif p < 0.20:
                s += 1.0

        score[i] = max(0.0, s)

    return score


def _volume_profile(close, low, high, volume, window):
    """量能密集帶 (Volume Profile)"""
    n = len(close)
    poc = np.full(n, np.nan)
    va_low_arr = np.full(n, np.nan)
    va_high_arr = np.full(n, np.nan)
    n_bins = 20

    for i in range(window, n):
        start = i - window
        c = close.iloc[start:i].values
        l = low.iloc[start:i].values
        h = high.iloc[start:i].values
        v = volume.iloc[start:i].values

        price_min = np.nanmin(l)
        price_max = np.nanmax(h)
        if price_max <= price_min or np.isnan(price_min):
            continue

        bin_edges = np.linspace(price_min, price_max, n_bins + 1)
        bin_vol = np.zeros(n_bins)

        for j in range(len(c)):
            if np.isnan(v[j]) or v[j] <= 0:
                continue
            mid = (h[j] + l[j]) / 2.0
            idx = int((mid - price_min) / (price_max - price_min) * (n_bins - 1))
            idx = max(0, min(n_bins - 1, idx))
            bin_vol[idx] += v[j]

        if bin_vol.sum() == 0:
            continue

        poc_idx = np.argmax(bin_vol)
        poc[i] = (bin_edges[poc_idx] + bin_edges[poc_idx + 1]) / 2.0

        total_vol = bin_vol.sum()
        target = total_vol * 0.70
        accum = bin_vol[poc_idx]
        lo_idx, hi_idx = poc_idx, poc_idx

        while accum < target and (lo_idx > 0 or hi_idx < n_bins - 1):
            expand_lo = bin_vol[lo_idx - 1] if lo_idx > 0 else 0
            expand_hi = bin_vol[hi_idx + 1] if hi_idx < n_bins - 1 else 0
            if expand_lo >= expand_hi and lo_idx > 0:
                lo_idx -= 1
                accum += bin_vol[lo_idx]
            elif hi_idx < n_bins - 1:
                hi_idx += 1
                accum += bin_vol[hi_idx]
            else:
                lo_idx -= 1
                accum += bin_vol[lo_idx]

        va_low_arr[i] = bin_edges[lo_idx]
        va_high_arr[i] = bin_edges[hi_idx + 1]

    return poc, va_low_arr, va_high_arr


# 模組輸出的特徵欄位（用於模型訓練）
ENTRY_FEATURE_COLS = [
    "price_vs_inst_cost",
    "price_vs_poc_20d",
    "vol_contraction_ratio",
    "inst_accumulation",
    "entry_score",
    "phase",
    # v2 新增因子
    "price_vs_ma60",
    "mom_20d",
    "inst_buy_ratio_20d",
    "volatility_20d",
]

# 輔助欄位（不用於訓練，但用於預測輸出）
ENTRY_INFO_COLS = [
    "inst_cost_10d",
    "inst_net_10d",        # 法人10日累計淨買超(張)
    "inst_vol_pct_10d",    # 法人佔成交量比例(%)
    "poc_20d", "poc_60d",
    "va_low_20d", "va_high_20d",
    "va_low_60d", "va_high_60d",
    "entry_price_low", "entry_price_high",
]
