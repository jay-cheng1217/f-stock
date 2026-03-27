"""關鍵價位模組 — 從日K計算支撐/壓力位，產生建議掛單價。

用法：
    from ml.features.key_levels import compute_key_levels
    levels_df = compute_key_levels(tickers, daily_k_dir)
    # 回傳 DataFrame: ticker, support_1, support_2, resistance_1, resistance_2,
    #                  suggested_entry, entry_discount_pct, level_source
"""

from __future__ import annotations

import os
from typing import Sequence

import numpy as np
import pandas as pd

from ml.config import DAILY_K_DIR

# 參數
LOOKBACK_DAYS = 60          # 回看天數
RECENT_DAYS = 20            # 近期低點範圍
MIN_TOUCH_COUNT = 1         # 最少觸及次數算有效支撐
PRICE_CLUSTER_PCT = 0.015   # 價格聚類容差 (1.5%)
ENTRY_BUFFER_PCT = 0.003    # 建議掛單價在支撐上方 0.3%


def _load_daily_k(ticker: str, lookback: int = LOOKBACK_DAYS) -> pd.DataFrame | None:
    """載入個股日K，回傳最近 N 天。"""
    path = os.path.join(DAILY_K_DIR, f"{ticker}.csv")
    if not os.path.exists(path):
        return None
    try:
        df = pd.read_csv(
            path,
            parse_dates=["Date"],
            usecols=["Date", "Open", "High", "Low", "Close", "Volume"],
        )
        df = df.sort_values("Date").tail(lookback).reset_index(drop=True)
        if len(df) < 10:
            return None
        return df
    except Exception:
        return None


def _find_swing_lows(df: pd.DataFrame, window: int = 3) -> pd.Series:
    """找波段低點 — Low 比前後 window 根 K 棒都低的位置。"""
    lows = df["Low"].values
    n = len(lows)
    swing_lows = []
    for i in range(window, n - window):
        if all(lows[i] <= lows[i - j] for j in range(1, window + 1)) and \
           all(lows[i] <= lows[i + j] for j in range(1, window + 1)):
            swing_lows.append(lows[i])
    return pd.Series(swing_lows, dtype=np.float64)


def _find_swing_highs(df: pd.DataFrame, window: int = 3) -> pd.Series:
    """找波段高點。"""
    highs = df["High"].values
    n = len(highs)
    swing_highs = []
    for i in range(window, n - window):
        if all(highs[i] >= highs[i - j] for j in range(1, window + 1)) and \
           all(highs[i] >= highs[i + j] for j in range(1, window + 1)):
            swing_highs.append(highs[i])
    return pd.Series(swing_highs, dtype=np.float64)


def _cluster_levels(prices: pd.Series, close: float, tolerance: float = PRICE_CLUSTER_PCT) -> list[float]:
    """將相近價格聚類，回傳由近到遠排序的代表價位。"""
    if prices.empty:
        return []

    sorted_p = prices.sort_values().values
    clusters: list[list[float]] = []
    current_cluster = [sorted_p[0]]

    for p in sorted_p[1:]:
        if (p - current_cluster[-1]) / current_cluster[-1] <= tolerance:
            current_cluster.append(p)
        else:
            clusters.append(current_cluster)
            current_cluster = [p]
    clusters.append(current_cluster)

    # 代表價位 = 聚類中位數，按觸及次數加權
    representatives = []
    for c in clusters:
        rep = float(np.median(c))
        representatives.append((rep, len(c)))

    # 按離收盤價的距離排序
    representatives.sort(key=lambda x: abs(x[0] - close))
    return [r[0] for r in representatives]


def _compute_ma_supports(df: pd.DataFrame) -> list[tuple[float, str]]:
    """計算均線支撐位。"""
    close = df["Close"].iloc[-1]
    supports = []
    for w, name in [(5, "MA5"), (10, "MA10"), (20, "MA20"), (60, "MA60")]:
        if len(df) >= w:
            ma = df["Close"].rolling(w).mean().iloc[-1]
            if ma < close:  # 只有在收盤價下方的均線才是支撐
                supports.append((float(ma), name))
    return supports


def _compute_volume_profile_support(df: pd.DataFrame, bins: int = 20) -> float | None:
    """量價分析 — 找最近收盤價下方的高成交量密集區。"""
    close = float(df["Close"].iloc[-1])
    below = df[df["Close"] < close]
    if below.empty or len(below) < 5:
        return None

    price_range = (below["Low"].min(), close)
    bin_edges = np.linspace(price_range[0], price_range[1], bins + 1)
    vol_profile = np.zeros(bins)

    for _, row in df.iterrows():
        low, high, vol = row["Low"], row["High"], row["Volume"]
        for i in range(bins):
            if high >= bin_edges[i] and low <= bin_edges[i + 1]:
                vol_profile[i] += vol

    # 找最大量的 bin（在收盤價下方）
    if vol_profile.sum() == 0:
        return None
    max_bin = int(np.argmax(vol_profile))
    return float((bin_edges[max_bin] + bin_edges[max_bin + 1]) / 2)


def compute_single_ticker_levels(ticker: str, current_close: float | None = None) -> dict:
    """計算單一股票的關鍵價位。"""
    df = _load_daily_k(ticker)
    if df is None:
        return _empty_result(ticker)

    close = current_close if current_close is not None else float(df["Close"].iloc[-1])

    # 1. 波段低點支撐
    swing_lows = _find_swing_lows(df, window=2)
    support_levels = _cluster_levels(swing_lows[swing_lows < close], close)

    # 2. 近期絕對低點
    recent = df.tail(RECENT_DAYS)
    recent_low = float(recent["Low"].min())

    # 3. 波段高點壓力
    swing_highs = _find_swing_highs(df, window=2)
    resistance_levels = _cluster_levels(swing_highs[swing_highs > close], close)

    # 4. 均線支撐
    ma_supports = _compute_ma_supports(df)

    # 5. 量價密集區
    vol_support = _compute_volume_profile_support(df)

    # --- 綜合判斷最佳支撐位 ---
    candidates: list[tuple[float, str, float]] = []  # (price, source, score)

    # 波段低點（最重要）
    for i, s in enumerate(support_levels[:3]):
        if s < close:
            # 越近的支撐越好，但不能太近（< 1%沒意義）
            dist_pct = (close - s) / close
            if 0.01 <= dist_pct <= 0.10:
                score = 3.0 - i * 0.5  # 最近的分數最高
                candidates.append((s, "前低支撐", score))

    # 近期最低點
    if recent_low < close:
        dist_pct = (close - recent_low) / close
        if 0.01 <= dist_pct <= 0.10:
            candidates.append((recent_low, f"{RECENT_DAYS}日低點", 2.5))

    # 均線支撐
    for ma_price, ma_name in ma_supports:
        dist_pct = (close - ma_price) / close
        if 0.01 <= dist_pct <= 0.08:
            candidates.append((ma_price, ma_name, 2.0))

    # 量價密集區
    if vol_support is not None and vol_support < close:
        dist_pct = (close - vol_support) / close
        if 0.01 <= dist_pct <= 0.10:
            candidates.append((vol_support, "量價密集區", 1.8))

    # 排序：分數高優先
    candidates.sort(key=lambda x: -x[2])

    # 取前兩個支撐、前兩個壓力
    supports = [(p, src) for p, src, _ in candidates[:2]] if candidates else []
    resistances = [(r, "前高壓力") for r in resistance_levels[:2]] if resistance_levels else []

    # 建議掛單價 = 最佳支撐位上方微幅（讓單容易成交）
    if supports:
        best_support = supports[0][0]
        suggested = _tick_round(best_support * (1 + ENTRY_BUFFER_PCT), close)
        source = supports[0][1]
        discount = (close - suggested) / close
    else:
        suggested = None
        source = None
        discount = None

    return {
        "ticker": ticker,
        "close": close,
        "support_1": supports[0][0] if len(supports) > 0 else None,
        "support_1_src": supports[0][1] if len(supports) > 0 else None,
        "support_2": supports[1][0] if len(supports) > 1 else None,
        "support_2_src": supports[1][1] if len(supports) > 1 else None,
        "resistance_1": resistances[0][0] if len(resistances) > 0 else None,
        "resistance_2": resistances[1][0] if len(resistances) > 1 else None,
        "suggested_entry": suggested,
        "entry_discount_pct": discount,
        "level_source": source,
    }


def _tick_round(price: float, ref_close: float) -> float:
    """依台股升降單位四捨五入。"""
    if ref_close < 10:
        tick = 0.01
    elif ref_close < 50:
        tick = 0.05
    elif ref_close < 100:
        tick = 0.1
    elif ref_close < 500:
        tick = 0.5
    elif ref_close < 1000:
        tick = 1.0
    else:
        tick = 5.0
    return round(round(price / tick) * tick, 2)


def _empty_result(ticker: str) -> dict:
    return {
        "ticker": ticker,
        "close": None,
        "support_1": None, "support_1_src": None,
        "support_2": None, "support_2_src": None,
        "resistance_1": None, "resistance_2": None,
        "suggested_entry": None, "entry_discount_pct": None,
        "level_source": None,
    }


def compute_key_levels(
    tickers: Sequence[str],
    closes: dict[str, float] | None = None,
) -> pd.DataFrame:
    """批次計算關鍵價位。

    Parameters
    ----------
    tickers : 股票代號清單
    closes : {ticker: close} 可選，提供最新收盤價
    """
    rows = []
    for t in tickers:
        c = closes.get(t) if closes else None
        rows.append(compute_single_ticker_levels(t, c))
    return pd.DataFrame(rows)
