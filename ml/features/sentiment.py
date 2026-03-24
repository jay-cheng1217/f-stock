"""消息面/情緒代理特徵工程模組

從價量微觀結構中提取市場情緒代理指標，
作為消息面分析的量化替代。

特徵邏輯:
- 異常量比: 當日成交量 vs 過去 20 日均量的偏離
- 連續漲跌天數: 多空方向持續力
- 跳空缺口頻率: 近期跳空事件密度 (overnight news proxy)
- 量價背離: 價漲量縮 / 價跌量增的背離信號
- 大戶散戶動向: 融資融券餘額變化趨勢 (if available)
"""

import pandas as pd
import numpy as np


def compute_sentiment_features(df: pd.DataFrame) -> pd.DataFrame:
    """從價量資料計算市場情緒代理特徵。

    Parameters
    ----------
    df : pd.DataFrame
        單檔股票日K資料，需包含 Open, High, Low, Close, Volume 欄位。

    Returns
    -------
    pd.DataFrame
        原始欄位 + 情緒代理特徵欄位
    """
    out = df.copy()

    close = out["Close"]
    volume = out["Volume"].astype(np.float64)
    daily_ret = close.pct_change()

    # --- 異常量比 (volume surprise) ---
    vol_ma20 = volume.rolling(20, min_periods=10).mean()
    out["volume_surprise"] = (
        (volume / vol_ma20.replace(0, np.nan) - 1.0)
    ).astype(np.float32)

    # --- 連續漲跌天數 ---
    # 正值=連漲天數, 負值=連跌天數
    sign = np.sign(daily_ret).fillna(0)
    streaks = []
    streak = 0
    for s in sign:
        if s == 0:
            streak = 0
        elif streak == 0 or np.sign(streak) == s:
            streak += int(s)
        else:
            streak = int(s)
        streaks.append(streak)
    out["consecutive_days"] = np.array(streaks, dtype=np.float32)

    # --- 近 10 日跳空缺口頻率 ---
    gap = (out["Open"] - close.shift(1)) / close.shift(1).replace(0, np.nan)
    significant_gap = (gap.abs() > 0.01).astype(np.float32)
    out["gap_freq_10d"] = (
        significant_gap.rolling(10, min_periods=5).sum().astype(np.float32)
    )

    # --- 量價背離指標 ---
    # 正值: 價漲量縮 (bearish divergence)
    # 負值: 價跌量增 (也是 bearish)
    # 計算方式: 5日報酬方向 vs 5日量變化方向的不一致程度
    ret_5d = close.pct_change(5)
    vol_change_5d = volume.pct_change(5)
    out["price_vol_divergence"] = np.where(
        ret_5d * vol_change_5d < 0,  # 方向不同 = 背離
        (-ret_5d * vol_change_5d.abs()).astype(np.float64),
        0.0,
    ).astype(np.float32)

    # --- 上影線比例 (賣壓指標) ---
    body = (close - out["Open"]).abs()
    wick_upper = out["High"] - pd.concat([close, out["Open"]], axis=1).max(axis=1)
    full_range = (out["High"] - out["Low"]).replace(0, np.nan)
    out["upper_wick_ratio"] = (wick_upper / full_range).astype(np.float32)

    # --- 下影線比例 (買盤支撐) ---
    wick_lower = pd.concat([close, out["Open"]], axis=1).min(axis=1) - out["Low"]
    out["lower_wick_ratio"] = (wick_lower / full_range).astype(np.float32)

    # --- 5 日平均上影線 (持續賣壓) ---
    out["upper_wick_5d_avg"] = (
        out["upper_wick_ratio"].rolling(5, min_periods=3).mean().astype(np.float32)
    )

    # --- 近 20 日最高價距離 (離場風險) ---
    rolling_high_20 = out["High"].rolling(20, min_periods=10).max()
    out["dist_from_20d_high"] = (
        (close - rolling_high_20) / rolling_high_20.replace(0, np.nan)
    ).astype(np.float32)

    # --- 近 20 日最低價距離 (反彈空間) ---
    rolling_low_20 = out["Low"].rolling(20, min_periods=10).min()
    out["dist_from_20d_low"] = (
        (close - rolling_low_20) / rolling_low_20.replace(0, np.nan)
    ).astype(np.float32)

    return out


# 本模組產出的特徵欄位名稱
SENTIMENT_FEATURE_COLS = [
    "volume_surprise",
    "consecutive_days",
    "gap_freq_10d",
    "price_vol_divergence",
    "upper_wick_ratio",
    "lower_wick_ratio",
    "upper_wick_5d_avg",
    "dist_from_20d_high",
    "dist_from_20d_low",
]
