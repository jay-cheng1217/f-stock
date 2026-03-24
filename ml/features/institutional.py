"""法人籌碼面特徵工程模組

從日K資料中的三大法人買賣超、融資融券餘額
衍生籌碼面特徵。
"""

import pandas as pd
import numpy as np


def compute_institutional_features(df: pd.DataFrame) -> pd.DataFrame:
    """從單檔股票日K資料計算籌碼面特徵。

    Parameters
    ----------
    df : pd.DataFrame
        單檔股票日K資料，需包含 Foreign_BuySell, Trust_BuySell,
        Dealer_BuySell, Margin_Balance, Short_Balance 等欄位。

    Returns
    -------
    pd.DataFrame
        原始欄位 + 新增籌碼面特徵欄位
    """
    out = df.copy()

    # 補齊可能缺少的欄位
    for col in ["Foreign_BuySell", "Trust_BuySell", "Dealer_BuySell",
                 "Margin_Balance", "Short_Balance"]:
        if col not in out.columns:
            out[col] = 0.0

    # Margin / short data often arrives after the latest daily bar.
    # Carry forward a single missing row so snapshot features do not drop to NaN
    # just because the financing feed lags the price feed by one trading day.
    out[["Margin_Balance", "Short_Balance"]] = (
        out[["Margin_Balance", "Short_Balance"]].ffill(limit=1)
    )

    # --- 成交量 (用於法人買賣超正規化) ---
    volume = out["Volume"].astype(np.float64) if "Volume" in out.columns else pd.Series(0.0, index=out.index)

    # --- 外資累積買賣超 ---
    for w in [1, 3, 5, 10, 20]:
        raw = out["Foreign_BuySell"].rolling(w, min_periods=1).sum()
        out[f"foreign_cumsum_{w}d"] = raw.astype(np.float32)
        # 正規化: 除以同期成交量，讓大小型股可比較
        vol_sum = volume.rolling(w, min_periods=1).sum()
        out[f"foreign_cumsum_{w}d_norm"] = np.where(
            vol_sum > 0, (raw / vol_sum).astype(np.float32), np.nan
        )

    # --- 投信累積買賣超 ---
    for w in [1, 3, 5, 10, 20]:
        raw = out["Trust_BuySell"].rolling(w, min_periods=1).sum()
        out[f"trust_cumsum_{w}d"] = raw.astype(np.float32)
        vol_sum = volume.rolling(w, min_periods=1).sum()
        out[f"trust_cumsum_{w}d_norm"] = np.where(
            vol_sum > 0, (raw / vol_sum).astype(np.float32), np.nan
        )

    # --- 自營商累積買賣超 ---
    for w in [1, 3, 5, 10]:
        raw = out["Dealer_BuySell"].rolling(w, min_periods=1).sum()
        out[f"dealer_cumsum_{w}d"] = raw.astype(np.float32)
        vol_sum = volume.rolling(w, min_periods=1).sum()
        out[f"dealer_cumsum_{w}d_norm"] = np.where(
            vol_sum > 0, (raw / vol_sum).astype(np.float32), np.nan
        )

    # --- 三大法人合計累積 ---
    inst_total = out["Foreign_BuySell"] + out["Trust_BuySell"] + out["Dealer_BuySell"]
    for w in [5, 10, 20]:
        raw = inst_total.rolling(w, min_periods=1).sum()
        out[f"inst_total_{w}d"] = raw.astype(np.float32)
        vol_sum = volume.rolling(w, min_periods=1).sum()
        out[f"inst_total_{w}d_norm"] = np.where(
            vol_sum > 0, (raw / vol_sum).astype(np.float32), np.nan
        )

    # --- 外資投信同步買超 ---
    sync = ((out["Foreign_BuySell"] > 0) & (out["Trust_BuySell"] > 0)).astype(np.float32)
    out["foreign_trust_sync"] = sync.rolling(5, min_periods=1).sum().astype(np.float32)

    # --- 融資變化率 ---
    for w in [5, 10]:
        out[f"margin_change_{w}d"] = (
            out["Margin_Balance"].pct_change(w, fill_method=None).astype(np.float32)
        )

    # --- 融券變化率 ---
    for w in [5, 10]:
        out[f"short_change_{w}d"] = (
            out["Short_Balance"].pct_change(w, fill_method=None).astype(np.float32)
        )

    # --- 融資券比 ---
    # 只在融券餘額 > 0 時才有意義；否則為 NaN (LightGBM 原生處理)
    margin = out["Margin_Balance"].astype(np.float64)
    short = out["Short_Balance"].astype(np.float64)
    out["margin_short_ratio"] = np.where(
        short > 0, (margin / short).astype(np.float32), np.nan
    )

    # --- 外資轉向信號 (從賣轉買) ---
    foreign = out["Foreign_BuySell"]
    prev_foreign_neg = foreign.shift(1) < 0
    out["foreign_reversal_buy"] = (
        prev_foreign_neg & (foreign > 0)
    ).astype(np.float32)

    # 外資連續買超天數 (向量化：cumsum group trick)
    is_foreign_buy = foreign > 0
    foreign_group = (~is_foreign_buy).cumsum()
    out["foreign_buy_streak"] = is_foreign_buy.groupby(foreign_group).cumsum().astype(np.float32)

    # --- 投信轉向 ---
    trust = out["Trust_BuySell"]
    out["trust_reversal_buy"] = (
        (trust.shift(1) < 0) & (trust > 0)
    ).astype(np.float32)

    # 投信連續買超天數 (向量化)
    is_trust_buy = trust > 0
    trust_group = (~is_trust_buy).cumsum()
    out["trust_buy_streak"] = is_trust_buy.groupby(trust_group).cumsum().astype(np.float32)

    # =================================================================
    # 籌碼背離指標 (Chip Divergence)
    # 價格趨勢 vs 法人行為 vs 散戶融資 的三維背離
    # =================================================================
    close = out["Close"].astype(np.float64) if "Close" in out.columns else None
    if close is not None:
        ret_20d = close / close.shift(20) - 1  # 20日報酬

        # --- 法人合計 20 日正規化淨買賣 ---
        inst_norm_20d = out.get("inst_total_20d_norm",
                               pd.Series(np.nan, index=out.index))

        # --- 融資 5 日變化率 ---
        margin_chg_5d = out.get("margin_change_5d",
                                pd.Series(np.nan, index=out.index))

        # --- 頂部籌碼背離 (Bearish) ---
        # 股價漲 + 法人賣 + 散戶融資追買 = 主力出貨割韭菜
        # 連續量化分數：各分項 clip 到 [0, 1] 後相乘
        price_heat = (ret_20d / 0.10).clip(0, 2)           # 漲 10% = 1.0, 20% = 2.0
        inst_sell = (-inst_norm_20d / 0.05).clip(0, 2)      # 法人賣超佔量 5% = 1.0
        margin_chase = (margin_chg_5d / 0.05).clip(0, 2)    # 融資增 5% = 1.0
        out["chip_diverge_bear"] = (
            price_heat * inst_sell * margin_chase
        ).astype(np.float32)

        # --- 底部籌碼背離 (Bullish) ---
        # 股價跌 + 法人買 + 散戶融資斷頭 = 法人暗中吃貨
        price_weak = (-ret_20d / 0.10).clip(0, 2)           # 跌 10% = 1.0
        inst_buy = (inst_norm_20d / 0.05).clip(0, 2)        # 法人買超佔量 5% = 1.0
        margin_panic = (-margin_chg_5d / 0.05).clip(0, 2)   # 融資減 5% = 1.0
        out["chip_diverge_bull"] = (
            price_weak * inst_buy * margin_panic
        ).astype(np.float32)
    else:
        out["chip_diverge_bear"] = np.nan
        out["chip_diverge_bull"] = np.nan

    return out


# 本模組產出的特徵欄位名稱
INSTITUTIONAL_FEATURE_COLS = [
    # 外資累積買賣超 (1d/3d/5d/10d/20d)
    "foreign_cumsum_1d", "foreign_cumsum_3d",
    "foreign_cumsum_5d", "foreign_cumsum_10d", "foreign_cumsum_20d",
    # 投信累積買賣超
    "trust_cumsum_1d", "trust_cumsum_3d",
    "trust_cumsum_5d", "trust_cumsum_10d", "trust_cumsum_20d",
    # 自營商累積買賣超
    "dealer_cumsum_1d", "dealer_cumsum_3d",
    "dealer_cumsum_5d", "dealer_cumsum_10d",
    # 三大法人合計
    "inst_total_5d", "inst_total_10d", "inst_total_20d",
    # 正規化版 (除以成交量，大小型股可比較)
    "foreign_cumsum_1d_norm", "foreign_cumsum_3d_norm",
    "foreign_cumsum_5d_norm", "foreign_cumsum_10d_norm", "foreign_cumsum_20d_norm",
    "trust_cumsum_1d_norm", "trust_cumsum_3d_norm",
    "trust_cumsum_5d_norm", "trust_cumsum_10d_norm", "trust_cumsum_20d_norm",
    "dealer_cumsum_1d_norm", "dealer_cumsum_3d_norm",
    "dealer_cumsum_5d_norm", "dealer_cumsum_10d_norm",
    "inst_total_5d_norm", "inst_total_10d_norm", "inst_total_20d_norm",
    "foreign_trust_sync",
    "margin_change_5d", "margin_change_10d",
    "short_change_5d", "short_change_10d",
    "margin_short_ratio",
    # 法人轉向事件與連續天數
    "foreign_reversal_buy", "foreign_buy_streak",
    "trust_reversal_buy", "trust_buy_streak",
    # 周轉率 (在 dataset.py 計算，需外部 issued_shares)
    "turnover_rate",
    # 籌碼背離
    "chip_diverge_bear",  # 頂部：漲+法人賣+散戶融資追 → 越大越危險
    "chip_diverge_bull",  # 底部：跌+法人買+散戶斷頭 → 越大越有機會
]
