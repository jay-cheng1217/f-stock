"""月營收特徵工程模組

從月營收資料衍生營收成長動能特徵，
並透過 point-in-time join 對齊至日K資料時間軸。
營收資料通常在 M+1 月 10 號前後公布，
故以此為基準進行時間對齊。
"""

import pandas as pd
import numpy as np


def compute_revenue_features(
    daily_df: pd.DataFrame,
    revenue_df: pd.DataFrame,
) -> pd.DataFrame:
    """將月營收特徵合併至日K資料。

    Parameters
    ----------
    daily_df : pd.DataFrame
        單檔股票日K資料，需包含 Date 欄位 (datetime 或 str)。
    revenue_df : pd.DataFrame
        單檔股票月營收資料，欄位:
        Date (YYYY-MM), Name, Monthly_Revenue, Cumulative_Revenue,
        YoY_pct_change, Cumulative_YoY_pct_change。

    Returns
    -------
    pd.DataFrame
        daily_df + 營收特徵欄位 (forward-fill)
    """
    out = daily_df.copy()
    out["Date"] = pd.to_datetime(out["Date"])

    rev = revenue_df.copy()
    # 營收月份 → 可使用日期 (M+1 月 11 日為保守估計)
    rev["rev_month"] = pd.to_datetime(rev["Date"] + "-01")
    rev = rev.sort_values("rev_month").reset_index(drop=True)
    rev["available_date"] = rev["rev_month"] + pd.DateOffset(months=1, days=10)

    # --- 原始資料為百分比數值 (93.58 = 93.58%)，統一轉小數 (0.9358) ---
    yoy_decimal = rev["YoY_pct_change"] / 100.0
    cum_yoy_decimal = rev["Cumulative_YoY_pct_change"] / 100.0

    # --- 營收年增率 (最新月) ---
    rev["revenue_yoy_latest"] = yoy_decimal.astype(np.float32)

    # --- 近三月平均年增率 ---
    rev["revenue_yoy_3m_avg"] = (
        yoy_decimal.rolling(3, min_periods=1).mean().astype(np.float32)
    )

    # --- 年增率動能 (最新 - 三個月前) ---
    rev["revenue_yoy_momentum"] = (
        yoy_decimal - yoy_decimal.shift(3)
    ).astype(np.float32)

    # --- 累計年增率 ---
    rev["revenue_cumulative_yoy"] = cum_yoy_decimal.astype(np.float32)

    # 取需要的欄位
    feature_cols = [
        "available_date",
        "revenue_yoy_latest",
        "revenue_yoy_3m_avg",
        "revenue_yoy_momentum",
        "revenue_cumulative_yoy",
    ]
    rev_features = rev[feature_cols].copy()

    # point-in-time merge: 使用 merge_asof 以日K日期對齊最近可用的營收
    out = out.sort_values("Date").reset_index(drop=True)
    rev_features = rev_features.sort_values("available_date").reset_index(drop=True)

    out = pd.merge_asof(
        out,
        rev_features,
        left_on="Date",
        right_on="available_date",
        direction="backward",
    )

    # 移除輔助欄位
    out.drop(columns=["available_date"], inplace=True, errors="ignore")

    return out


# 本模組產出的特徵欄位名稱
REVENUE_FEATURE_COLS = [
    "revenue_yoy_latest",
    "revenue_yoy_3m_avg",
    "revenue_yoy_momentum",
    "revenue_cumulative_yoy",
]
