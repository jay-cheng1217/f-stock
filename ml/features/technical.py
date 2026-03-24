"""技術面特徵工程模組.

直接由 OHLCV 重算技術指標，避免增量更新後最新列尚未回填指標時
造成推論與解釋誤判為資料不足。
"""

import warnings

import pandas as pd
import numpy as np


def _rma(series: pd.Series, period: int) -> pd.Series:
    """Wilder's RMA，供 RSI / ATR 使用。"""
    s = series.copy()
    sma = s.rolling(period, min_periods=period).mean()
    out = pd.Series(index=s.index, dtype="float64")
    first = sma.first_valid_index()
    if first is None:
        return out

    out.loc[first] = sma.loc[first]
    alpha = 1.0 / period
    start = s.index.get_loc(first) + 1
    for i in range(start, len(s)):
        out.iloc[i] = out.iloc[i - 1] + alpha * (s.iloc[i] - out.iloc[i - 1])
    return out


def _rsi_wilder(close: pd.Series, period: int) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = _rma(gain, period)
    avg_loss = _rma(loss, period)
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _atr_wilder(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat(
        [
            (high - low).abs(),
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return _rma(tr, period)


def _stoch_kd(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    k_period: int = 9,
    k_smooth: int = 3,
    d_period: int = 3,
) -> tuple[pd.Series, pd.Series]:
    lowest = low.rolling(k_period, min_periods=k_period).min()
    highest = high.rolling(k_period, min_periods=k_period).max()
    rsv = (close - lowest) / (highest - lowest)
    rsv = (rsv.replace([np.inf, -np.inf], np.nan) * 100).clip(0, 100)
    k = rsv.rolling(k_smooth, min_periods=k_smooth).mean()
    d = k.rolling(d_period, min_periods=d_period).mean()
    return k, d


def compute_technical_features(df: pd.DataFrame) -> pd.DataFrame:
    """從單檔股票日K資料計算技術面特徵。"""
    warnings.filterwarnings("ignore", category=pd.errors.PerformanceWarning)
    out = df.copy()

    for col in ["Open", "High", "Low", "Close", "Volume"]:
        if col not in out.columns:
            out[col] = np.nan
        out[col] = pd.to_numeric(out[col], errors="coerce")

    close = out["Close"]
    high = out["High"]
    low = out["Low"]
    open_ = out["Open"]
    volume = out["Volume"]

    out["MA_5"] = close.rolling(5, min_periods=5).mean()
    out["MA_20"] = close.rolling(20, min_periods=20).mean()
    out["MA_60"] = close.rolling(60, min_periods=60).mean()

    out["RSI_6"] = _rsi_wilder(close, 6)
    out["RSI_14"] = _rsi_wilder(close, 14)

    ma20 = close.rolling(20, min_periods=20).mean()
    std20 = close.rolling(20, min_periods=20).std()
    out["BBM_20_2.0"] = ma20
    out["BBU_20_2.0"] = ma20 + 2 * std20
    out["BBL_20_2.0"] = ma20 - 2 * std20

    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    macd_signal = macd.ewm(span=9, adjust=False).mean()
    out["MACD_12_26_9"] = macd
    out["MACDs_12_26_9"] = macd_signal
    out["MACDh_12_26_9"] = macd - macd_signal

    k, d = _stoch_kd(high, low, close)
    out["K"] = k
    out["D"] = d
    out["ATR_14"] = _atr_wilder(high, low, close)

    out["VOL_MA_5"] = volume.rolling(5, min_periods=5).mean()
    out["VOL_MA_20"] = volume.rolling(20, min_periods=20).mean()

    out["price_vs_ma5"] = ((close - out["MA_5"]) / out["MA_5"]).astype(np.float32)
    out["price_vs_ma20"] = ((close - out["MA_20"]) / out["MA_20"]).astype(np.float32)
    out["price_vs_ma60"] = ((close - out["MA_60"]) / out["MA_60"]).astype(np.float32)

    bb_width = out["BBU_20_2.0"] - out["BBL_20_2.0"]
    out["bb_position"] = (
        (close - out["BBL_20_2.0"]) / bb_width.replace(0, np.nan)
    ).astype(np.float32)

    out["ma_slope_5"] = (out["MA_5"].diff(5) / out["MA_5"].shift(5)).astype(np.float32)
    out["ma_slope_20"] = (out["MA_20"].diff(5) / out["MA_20"].shift(5)).astype(np.float32)

    out["vol_ratio_5_20"] = (
        out["VOL_MA_5"] / out["VOL_MA_20"].replace(0, np.nan)
    ).astype(np.float32)

    for period in [1, 3, 5, 10, 20]:
        out[f"return_{period}d"] = close.pct_change(period).astype(np.float32)

    daily_ret = close.pct_change()
    out["volatility_5d"] = daily_ret.rolling(5, min_periods=3).std().astype(np.float32)
    out["volatility_20d"] = daily_ret.rolling(20, min_periods=10).std().astype(np.float32)

    out["high_low_range"] = ((high - low) / close.replace(0, np.nan)).astype(np.float32)
    out["gap_pct"] = (
        (open_ - close.shift(1)) / close.shift(1).replace(0, np.nan)
    ).astype(np.float32)

    out["rsi_6"] = out["RSI_6"].astype(np.float32)
    out["rsi_14"] = out["RSI_14"].astype(np.float32)
    out["macd_hist"] = out["MACDh_12_26_9"].astype(np.float32)
    out["kd_k"] = out["K"].astype(np.float32)
    out["kd_d"] = out["D"].astype(np.float32)
    out["atr_14"] = out["ATR_14"].astype(np.float32)
    # ATR% (ATR/股價)：正規化波動度，讓高價股與低價股在同一天平比較
    out["atr_pct"] = np.where(
        close > 0, (out["ATR_14"] / close).astype(np.float32), np.nan
    )

    # --- 均線支撐/反彈事件型特徵 ---
    prev_vs_ma5 = out["price_vs_ma5"].shift(1)
    prev_vs_ma20 = out["price_vs_ma20"].shift(1)

    # MA5 反彈：前一天貼近或跌破 MA5，今天收回（從下方回到上方）
    out["ma5_bounce"] = (
        (prev_vs_ma5 < 0.01) & (out["price_vs_ma5"] > 0)
    ).astype(np.float32)

    # MA20 反彈：同理
    out["ma20_bounce"] = (
        (prev_vs_ma20 < 0.01) & (out["price_vs_ma20"] > 0)
    ).astype(np.float32)

    # 均線支撐測試：最近5天內曾觸及 MA5/MA20 但未跌破
    touched_ma5 = (out["price_vs_ma5"].abs() < 0.015).rolling(5, min_periods=1).max()
    out["ma5_support_test"] = (
        touched_ma5 * (out["price_vs_ma5"] > 0)
    ).astype(np.float32)

    touched_ma20 = (out["price_vs_ma20"].abs() < 0.015).rolling(5, min_periods=1).max()
    out["ma20_support_test"] = (
        touched_ma20 * (out["price_vs_ma20"] > 0)
    ).astype(np.float32)

    # 均線多頭排列：MA5 > MA20 > MA60
    out["ma_bullish_align"] = (
        (out["MA_5"] > out["MA_20"]) & (out["MA_20"] > out["MA_60"])
    ).astype(np.float32)

    # 均線金叉/死叉：MA5 穿越 MA20
    ma5_above_ma20 = out["MA_5"] > out["MA_20"]
    out["ma_golden_cross"] = (
        ma5_above_ma20 & ~ma5_above_ma20.shift(1, fill_value=False)
    ).astype(np.float32)
    out["ma_death_cross"] = (
        ~ma5_above_ma20 & ma5_above_ma20.shift(1, fill_value=True)
    ).astype(np.float32)

    # MACD 翻正/翻負
    macd_pos = out["MACDh_12_26_9"] > 0
    out["macd_turn_positive"] = (
        macd_pos & ~macd_pos.shift(1, fill_value=False)
    ).astype(np.float32)

    # KD 黃金交叉 (K 由下穿上 D)
    k_above_d = out["K"] > out["D"]
    out["kd_golden_cross"] = (
        k_above_d & ~k_above_d.shift(1, fill_value=False)
    ).astype(np.float32)

    # RSI 超賣反彈 (RSI14 從 <30 回升到 >30)
    out["rsi_oversold_bounce"] = (
        (out["RSI_14"] > 30) & (out["RSI_14"].shift(1) <= 30)
    ).astype(np.float32)

    # === 量價關係 ===

    # OBV 斜率 (趨勢方向)
    obv_sign = np.where(close > close.shift(1), 1, np.where(close < close.shift(1), -1, 0))
    obv = (volume * obv_sign).cumsum()
    out["obv_slope_20"] = (
        pd.Series(obv, index=out.index).rolling(20, min_periods=10).apply(
            lambda x: np.polyfit(range(len(x)), x, 1)[0] if len(x) > 1 else 0, raw=True
        )
    ).astype(np.float32)

    # CMF (Chaikin Money Flow)
    mfm = ((close - low) - (high - close)) / (high - low).replace(0, np.nan)
    mfv = mfm * volume
    out["cmf_20"] = (
        mfv.rolling(20, min_periods=10).sum() / volume.rolling(20, min_periods=10).sum()
    ).astype(np.float32)

    # 量突破 z-score
    vol_mean20 = volume.rolling(20, min_periods=10).mean()
    vol_std20 = volume.rolling(20, min_periods=10).std()
    out["vol_zscore"] = ((volume - vol_mean20) / vol_std20.replace(0, np.nan)).astype(np.float32)

    # === K線型態 ===

    body = (close - open_).abs()
    hl_range = (high - low).replace(0, np.nan)

    # 實體比例 (十字線偵測)
    out["body_ratio"] = (body / hl_range).astype(np.float32)

    # 上影線比例
    upper_shadow = high - pd.concat([close, open_], axis=1).max(axis=1)
    out["upper_shadow_ratio"] = (upper_shadow / hl_range).astype(np.float32)

    # 下影線比例
    lower_shadow = pd.concat([close, open_], axis=1).min(axis=1) - low
    out["lower_shadow_ratio"] = (lower_shadow / hl_range).astype(np.float32)

    # 吞噬型態
    prev_body_top = pd.concat([close.shift(1), open_.shift(1)], axis=1).max(axis=1)
    prev_body_bot = pd.concat([close.shift(1), open_.shift(1)], axis=1).min(axis=1)
    is_red = close > open_
    is_green_prev = close.shift(1) < open_.shift(1)
    out["bullish_engulf"] = (
        is_red & is_green_prev & (open_ < prev_body_bot) & (close > prev_body_top)
    ).astype(np.float32)
    out["bearish_engulf"] = (
        ~is_red & ~is_green_prev & (open_ > prev_body_top) & (close < prev_body_bot)
    ).astype(np.float32)

    # === 趨勢強度 ===

    # ADX (Average Directional Index)
    plus_dm = (high - high.shift(1)).clip(lower=0)
    minus_dm = (low.shift(1) - low).clip(lower=0)
    # 當 +DM > -DM 時才算 +DM，否則歸零；反之亦然
    plus_dm = np.where(plus_dm > minus_dm, plus_dm, 0)
    minus_dm_vals = np.where(pd.Series(minus_dm) > pd.Series(plus_dm).shift(0), minus_dm, 0)
    plus_dm = pd.Series(plus_dm, index=out.index).astype(float)
    minus_dm = pd.Series(minus_dm_vals, index=out.index).astype(float)
    atr14 = out["ATR_14"].replace(0, np.nan)
    plus_di = 100 * _rma(plus_dm, 14) / atr14
    minus_di = 100 * _rma(minus_dm, 14) / atr14
    dx = (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan) * 100
    out["adx_14"] = _rma(dx, 14).astype(np.float32)
    out["di_diff"] = (plus_di - minus_di).astype(np.float32)

    # === 支撐/壓力 ===

    # 近 N 日高低點距離
    for n in [20, 60]:
        rolling_high = high.rolling(n, min_periods=n).max()
        rolling_low = low.rolling(n, min_periods=n).min()
        out[f"dist_to_high_{n}d"] = ((rolling_high - close) / close.replace(0, np.nan)).astype(np.float32)
        out[f"dist_to_low_{n}d"] = ((close - rolling_low) / close.replace(0, np.nan)).astype(np.float32)

    # 52週高低相對位置
    high_252 = high.rolling(252, min_periods=120).max()
    low_252 = low.rolling(252, min_periods=120).min()
    out["position_52w"] = (
        (close - low_252) / (high_252 - low_252).replace(0, np.nan)
    ).astype(np.float32)

    # === 動能與乖離 ===

    # 中長期動能
    out["return_60d"] = close.pct_change(60).astype(np.float32)
    out["momentum_accel"] = (out["return_20d"] - out["return_60d"]).astype(np.float32)

    # 波動率收縮 (布林帶寬度)
    out["bb_width"] = (bb_width / close.replace(0, np.nan)).astype(np.float32)
    bb_width_ma = out["bb_width"].rolling(20, min_periods=10).mean()
    out["vol_contraction"] = (out["bb_width"] < bb_width_ma * 0.8).astype(np.float32)

    # 上漲天數比 (趨勢一致性)
    is_up = (close > close.shift(1)).astype(float)
    out["up_day_ratio_20"] = is_up.rolling(20, min_periods=10).mean().astype(np.float32)

    # Williams %R
    for n in [14]:
        highest_n = high.rolling(n, min_periods=n).max()
        lowest_n = low.rolling(n, min_periods=n).min()
        out[f"williams_r_{n}"] = (
            (highest_n - close) / (highest_n - lowest_n).replace(0, np.nan) * -100
        ).astype(np.float32)

    # ================================================================
    # === 第二波擴充特徵 ===
    # ================================================================

    # --- CCI (Commodity Channel Index) ---
    for n in [14, 20]:
        tp = (high + low + close) / 3
        tp_ma = tp.rolling(n, min_periods=n).mean()
        tp_md = tp.rolling(n, min_periods=n).apply(lambda x: np.abs(x - x.mean()).mean(), raw=True)
        out[f"cci_{n}"] = ((tp - tp_ma) / (0.015 * tp_md).replace(0, np.nan)).astype(np.float32)

    # --- MFI (Money Flow Index) ---
    tp_mfi = (high + low + close) / 3
    raw_mf = tp_mfi * volume
    tp_diff = tp_mfi.diff()
    pos_mf = np.where(tp_diff > 0, raw_mf, 0)
    neg_mf = np.where(tp_diff < 0, raw_mf, 0)
    pos_mf_sum = pd.Series(pos_mf, index=out.index).rolling(14, min_periods=14).sum()
    neg_mf_sum = pd.Series(neg_mf, index=out.index).rolling(14, min_periods=14).sum()
    mf_ratio = pos_mf_sum / neg_mf_sum.replace(0, np.nan)
    out["mfi_14"] = (100 - 100 / (1 + mf_ratio)).astype(np.float32)

    # --- Stochastic RSI ---
    rsi14 = out["RSI_14"]
    rsi_min = rsi14.rolling(14, min_periods=14).min()
    rsi_max = rsi14.rolling(14, min_periods=14).max()
    stoch_rsi = (rsi14 - rsi_min) / (rsi_max - rsi_min).replace(0, np.nan)
    out["stoch_rsi_k"] = stoch_rsi.rolling(3, min_periods=1).mean().astype(np.float32)
    out["stoch_rsi_d"] = out["stoch_rsi_k"].rolling(3, min_periods=1).mean().astype(np.float32)

    # --- PSY 心理線 ---
    for n in [12, 24]:
        out[f"psy_{n}"] = is_up.rolling(n, min_periods=n).mean().astype(np.float32)

    # --- ROC (Rate of Change) ---
    for n in [5, 10, 20]:
        out[f"roc_{n}"] = (close.pct_change(n) * 100).astype(np.float32)

    # --- Ichimoku Cloud (一目均衡表) ---
    tenkan_high = high.rolling(9, min_periods=9).max()
    tenkan_low = low.rolling(9, min_periods=9).min()
    out["ichimoku_tenkan"] = ((tenkan_high + tenkan_low) / 2).astype(np.float32)

    kijun_high = high.rolling(26, min_periods=26).max()
    kijun_low = low.rolling(26, min_periods=26).min()
    out["ichimoku_kijun"] = ((kijun_high + kijun_low) / 2).astype(np.float32)

    senkou_a = (out["ichimoku_tenkan"] + out["ichimoku_kijun"]) / 2
    senkou_b_high = high.rolling(52, min_periods=52).max()
    senkou_b_low = low.rolling(52, min_periods=52).min()
    senkou_b = (senkou_b_high + senkou_b_low) / 2

    # 價格相對雲帶位置
    cloud_top = pd.concat([senkou_a, senkou_b], axis=1).max(axis=1)
    cloud_bot = pd.concat([senkou_a, senkou_b], axis=1).min(axis=1)
    cloud_width = (cloud_top - cloud_bot).replace(0, np.nan)
    out["ichimoku_cloud_pos"] = ((close - cloud_bot) / cloud_width).astype(np.float32)

    # 轉換線 vs 基準線 (趨勢信號)
    out["ichimoku_tk_diff"] = (
        (out["ichimoku_tenkan"] - out["ichimoku_kijun"]) / close.replace(0, np.nan)
    ).astype(np.float32)

    # 雲帶厚度 (趨勢強度)
    out["ichimoku_cloud_width"] = (cloud_width / close.replace(0, np.nan)).astype(np.float32)

    # --- Aroon ---
    for n in [25]:
        aroon_up = high.rolling(n, min_periods=n).apply(lambda x: x.argmax() / (n - 1) * 100, raw=True)
        aroon_down = low.rolling(n, min_periods=n).apply(lambda x: x.argmin() / (n - 1) * 100, raw=True)
        out["aroon_up"] = aroon_up.astype(np.float32)
        out["aroon_down"] = aroon_down.astype(np.float32)
        out["aroon_osc"] = (aroon_up - aroon_down).astype(np.float32)

    # --- TRIX ---
    ema1 = close.ewm(span=15, adjust=False).mean()
    ema2 = ema1.ewm(span=15, adjust=False).mean()
    ema3 = ema2.ewm(span=15, adjust=False).mean()
    out["trix"] = (ema3.pct_change() * 10000).astype(np.float32)  # basis points

    # --- Elder Ray (Bull/Bear Power) ---
    ema13 = close.ewm(span=13, adjust=False).mean()
    out["elder_bull"] = ((high - ema13) / close.replace(0, np.nan)).astype(np.float32)
    out["elder_bear"] = ((low - ema13) / close.replace(0, np.nan)).astype(np.float32)

    # --- Force Index ---
    force_raw = close.diff() * volume
    out["force_index_13"] = (
        force_raw.ewm(span=13, adjust=False).mean() / volume.rolling(20).mean().replace(0, np.nan)
    ).astype(np.float32)

    # --- Parabolic SAR 方向 (簡化版) ---
    # 使用 ADX + DI 方向判斷趨勢，SAR 方向用高低點近似
    sar_up = (close > close.shift(1)) & (close.shift(1) > close.shift(2))
    sar_down = (close < close.shift(1)) & (close.shift(1) < close.shift(2))
    out["trend_direction"] = (sar_up.astype(float) - sar_down.astype(float)).astype(np.float32)

    # --- Linear Regression Slope & R² ---
    for n in [20]:
        x_vals = np.arange(n, dtype=float)
        x_mean = x_vals.mean()
        x_var = ((x_vals - x_mean) ** 2).sum()

        def _lr_slope(y):
            if len(y) < n:
                return np.nan
            y_mean = y.mean()
            slope = ((x_vals - x_mean) * (y - y_mean)).sum() / x_var
            return slope

        def _lr_r2(y):
            if len(y) < n:
                return np.nan
            y_mean = y.mean()
            slope = ((x_vals - x_mean) * (y - y_mean)).sum() / x_var
            intercept = y_mean - slope * x_mean
            y_pred = slope * x_vals + intercept
            ss_res = ((y - y_pred) ** 2).sum()
            ss_tot = ((y - y_mean) ** 2).sum()
            return 1 - ss_res / ss_tot if ss_tot > 0 else 0

        out[f"lr_slope_{n}"] = close.rolling(n, min_periods=n).apply(_lr_slope, raw=True).astype(np.float32)
        out[f"lr_r2_{n}"] = close.rolling(n, min_periods=n).apply(_lr_r2, raw=True).astype(np.float32)

    # --- MACD 背離偵測 ---
    # 價格新高但 MACD 未新高 → 頂背離
    price_high_20 = close.rolling(20, min_periods=10).max()
    macd_high_20 = out["MACDh_12_26_9"].rolling(20, min_periods=10).max()
    out["macd_bearish_div"] = (
        (close >= price_high_20 * 0.99) &
        (out["MACDh_12_26_9"] < macd_high_20 * 0.7) &
        (out["MACDh_12_26_9"] > 0)
    ).astype(np.float32)

    # 價格新低但 MACD 未新低 → 底背離
    price_low_20 = close.rolling(20, min_periods=10).min()
    macd_low_20 = out["MACDh_12_26_9"].rolling(20, min_periods=10).min()
    out["macd_bullish_div"] = (
        (close <= price_low_20 * 1.01) &
        (out["MACDh_12_26_9"] > macd_low_20 * 0.7) &
        (out["MACDh_12_26_9"] < 0)
    ).astype(np.float32)

    # --- RSI 背離偵測 ---
    rsi_high_20 = rsi14.rolling(20, min_periods=10).max()
    out["rsi_bearish_div"] = (
        (close >= price_high_20 * 0.99) &
        (rsi14 < rsi_high_20 - 10) &
        (rsi14 > 50)
    ).astype(np.float32)

    rsi_low_20 = rsi14.rolling(20, min_periods=10).min()
    out["rsi_bullish_div"] = (
        (close <= price_low_20 * 1.01) &
        (rsi14 > rsi_low_20 + 10) &
        (rsi14 < 50)
    ).astype(np.float32)

    # --- 更多 K 線型態 ---

    # 十字星 (Doji) - 實體 < 10% 日振幅
    out["doji"] = (out["body_ratio"] < 0.1).astype(np.float32)

    # 錘子線 (Hammer) - 下影線 > 實體2倍, 上影線短
    out["hammer"] = (
        (out["lower_shadow_ratio"] > 0.6) &
        (out["upper_shadow_ratio"] < 0.1) &
        (out["body_ratio"] < 0.3)
    ).astype(np.float32)

    # 吊人線 (Hanging Man) - 同錘子但在高位
    out["hanging_man"] = (
        out["hammer"].astype(bool) &
        (close > out["MA_20"])
    ).astype(np.float32)

    # 射擊之星 (Shooting Star)
    out["shooting_star"] = (
        (out["upper_shadow_ratio"] > 0.6) &
        (out["lower_shadow_ratio"] < 0.1) &
        (out["body_ratio"] < 0.3)
    ).astype(np.float32)

    # 晨星 (Morning Star, 簡化: 前天大跌 + 昨天十字 + 今天大漲)
    prev2_red = (close.shift(2) < open_.shift(2)) & (out["return_1d"].shift(2) < -0.01)
    prev1_doji = out["body_ratio"].shift(1) < 0.15
    today_green = (close > open_) & (out["return_1d"] > 0.01)
    out["morning_star"] = (prev2_red & prev1_doji & today_green).astype(np.float32)

    # 暮星 (Evening Star)
    prev2_green = (close.shift(2) > open_.shift(2)) & (out["return_1d"].shift(2) > 0.01)
    today_red = (close < open_) & (out["return_1d"] < -0.01)
    out["evening_star"] = (prev2_green & prev1_doji & today_red).astype(np.float32)

    # 三白兵 (Three White Soldiers)
    c1_up = (close.shift(2) > open_.shift(2)) & (out["return_1d"].shift(2) > 0)
    c2_up = (close.shift(1) > open_.shift(1)) & (out["return_1d"].shift(1) > 0)
    c3_up = (close > open_) & (out["return_1d"] > 0)
    out["three_white_soldiers"] = (c1_up & c2_up & c3_up).astype(np.float32)

    # 三黑鴉 (Three Black Crows)
    c1_dn = (close.shift(2) < open_.shift(2)) & (out["return_1d"].shift(2) < 0)
    c2_dn = (close.shift(1) < open_.shift(1)) & (out["return_1d"].shift(1) < 0)
    c3_dn = (close < open_) & (out["return_1d"] < 0)
    out["three_black_crows"] = (c1_dn & c2_dn & c3_dn).astype(np.float32)

    # --- Donchian Channel Breakout ---
    dc_high_20 = high.rolling(20, min_periods=20).max()
    dc_low_20 = low.rolling(20, min_periods=20).min()
    dc_range = (dc_high_20 - dc_low_20).replace(0, np.nan)
    out["donchian_pos"] = ((close - dc_low_20) / dc_range).astype(np.float32)
    out["donchian_breakout_up"] = (close >= dc_high_20).astype(np.float32)
    out["donchian_breakout_dn"] = (close <= dc_low_20).astype(np.float32)

    # --- Ultimate Oscillator ---
    bp = close - pd.concat([low, close.shift(1)], axis=1).min(axis=1)
    tr_uo = pd.concat(
        [(high - low).abs(), (high - close.shift(1)).abs(), (low - close.shift(1)).abs()],
        axis=1,
    ).max(axis=1)
    avg7 = bp.rolling(7, min_periods=7).sum() / tr_uo.rolling(7, min_periods=7).sum()
    avg14 = bp.rolling(14, min_periods=14).sum() / tr_uo.rolling(14, min_periods=14).sum()
    avg28 = bp.rolling(28, min_periods=28).sum() / tr_uo.rolling(28, min_periods=28).sum()
    out["ultimate_osc"] = ((4 * avg7 + 2 * avg14 + avg28) / 7 * 100).astype(np.float32)

    # --- 連漲連跌天數 ---
    is_up_int = (close > close.shift(1)).astype(int)
    is_dn_int = (close < close.shift(1)).astype(int)
    up_streak = is_up_int.copy()
    dn_streak = is_dn_int.copy()
    for i in range(1, len(up_streak)):
        if up_streak.iloc[i] == 1:
            up_streak.iloc[i] = up_streak.iloc[i - 1] + 1
        if dn_streak.iloc[i] == 1:
            dn_streak.iloc[i] = dn_streak.iloc[i - 1] + 1
    out["up_streak"] = up_streak.astype(np.float32)
    out["down_streak"] = dn_streak.astype(np.float32)

    # --- Keltner Channel 位置 ---
    kc_mid = close.ewm(span=20, adjust=False).mean()
    kc_upper = kc_mid + 1.5 * out["ATR_14"]
    kc_lower = kc_mid - 1.5 * out["ATR_14"]
    kc_range = (kc_upper - kc_lower).replace(0, np.nan)
    out["keltner_pos"] = ((close - kc_lower) / kc_range).astype(np.float32)

    # --- Squeeze 指標 (BB 收窄到 Keltner 內) ---
    out["squeeze"] = (
        (out["BBL_20_2.0"] > kc_lower) & (out["BBU_20_2.0"] < kc_upper)
    ).astype(np.float32)

    # --- MA10 均線 (10日線距離) ---
    ma10 = close.rolling(10, min_periods=10).mean()
    out["price_vs_ma10"] = ((close - ma10) / ma10.replace(0, np.nan)).astype(np.float32)

    # --- 價量背離 (價漲量縮 / 價跌量增) ---
    price_up = out["return_5d"] > 0.02
    vol_shrink = out["vol_ratio_5_20"] < 0.8
    out["price_vol_diverge"] = (
        (price_up & vol_shrink) | ((~price_up) & (~vol_shrink))
    ).astype(np.float32)

    out = out.copy()
    return out.replace([np.inf, -np.inf], np.nan)


# 本模組產出的特徵欄位名稱
TECHNICAL_FEATURE_COLS = [
    # 均線距離
    "price_vs_ma5", "price_vs_ma10", "price_vs_ma20", "price_vs_ma60",
    "bb_position",
    "ma_slope_5", "ma_slope_20",
    # 量
    "vol_ratio_5_20",
    # 報酬與波動
    "return_1d", "return_3d", "return_5d", "return_10d", "return_20d",
    "volatility_5d", "volatility_20d",
    "high_low_range", "gap_pct",
    # 傳統指標
    "rsi_6", "rsi_14", "macd_hist", "kd_k", "kd_d", "atr_14", "atr_pct",
    # 均線事件
    "ma5_bounce", "ma20_bounce",
    "ma5_support_test", "ma20_support_test",
    "ma_bullish_align", "ma_golden_cross", "ma_death_cross",
    # 指標交叉/反轉
    "macd_turn_positive", "kd_golden_cross", "rsi_oversold_bounce",
    # 量價關係
    "obv_slope_20", "cmf_20", "vol_zscore",
    # K線型態 (基礎)
    "body_ratio", "upper_shadow_ratio", "lower_shadow_ratio",
    "bullish_engulf", "bearish_engulf",
    # 趨勢強度
    "adx_14", "di_diff",
    # 支撐/壓力
    "dist_to_high_20d", "dist_to_low_20d",
    "dist_to_high_60d", "dist_to_low_60d",
    "position_52w",
    # 動能與乖離
    "return_60d", "momentum_accel",
    "bb_width", "vol_contraction",
    "up_day_ratio_20", "williams_r_14",
    # --- 第二波擴充 ---
    # 振盪指標
    "cci_14", "cci_20",
    "mfi_14",
    "stoch_rsi_k", "stoch_rsi_d",
    "psy_12", "psy_24",
    "roc_5", "roc_10", "roc_20",
    "ultimate_osc",
    # 一目均衡表
    "ichimoku_cloud_pos", "ichimoku_tk_diff", "ichimoku_cloud_width",
    # Aroon
    "aroon_up", "aroon_down", "aroon_osc",
    # 趨勢
    "trix",
    "trend_direction",
    "lr_slope_20", "lr_r2_20",
    # 量價進階
    "elder_bull", "elder_bear",
    "force_index_13",
    "price_vol_diverge",
    # 背離偵測
    "macd_bearish_div", "macd_bullish_div",
    "rsi_bearish_div", "rsi_bullish_div",
    # K線型態 (進階)
    "doji", "hammer", "hanging_man", "shooting_star",
    "morning_star", "evening_star",
    "three_white_soldiers", "three_black_crows",
    # 通道
    "donchian_pos", "donchian_breakout_up", "donchian_breakout_dn",
    "keltner_pos", "squeeze",
    # 連漲連跌
    "up_streak", "down_streak",
]
