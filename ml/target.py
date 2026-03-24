"""目標變數建構：計算超額報酬（相對大盤）與分類標籤"""
import pandas as pd
import numpy as np
from ml.config import FORWARD_DAYS, UP_THRESHOLD, DOWN_THRESHOLD


def compute_target(df: pd.DataFrame, twii_df: pd.DataFrame = None) -> pd.DataFrame:
    """計算 N 日前瞻超額報酬並分為 UP/FLAT/DOWN 三類

    超額報酬 = 個股報酬 - 大盤(TWII)報酬
    可區分真正跑贏大盤的股票，避免大盤齊漲時所有股票都被標為 UP。

    Args:
        df: 含 Date, Close 的 DataFrame (單支股票)
        twii_df: 含 Date, twii_close 的大盤指數 DataFrame
    Returns:
        df 加上 forward_return, excess_return, target 欄位
    """
    df = df.copy()

    # 個股前瞻報酬
    df["forward_return"] = df["Close"].shift(-FORWARD_DAYS) / df["Close"] - 1

    # 計算大盤前瞻報酬並合併
    if twii_df is not None:
        twii = twii_df.copy()
        twii["twii_forward_return"] = (
            twii["twii_close"].shift(-FORWARD_DAYS) / twii["twii_close"] - 1
        )
        df = df.merge(twii[["Date", "twii_forward_return"]], on="Date", how="left")
        # 超額報酬 = 個股報酬 - 大盤報酬
        df["excess_return"] = df["forward_return"] - df["twii_forward_return"]
        df.drop(columns=["twii_forward_return"], inplace=True)
    else:
        # 無大盤資料時退回絕對報酬
        df["excess_return"] = df["forward_return"]

    # 以超額報酬分類
    conditions = [
        df["excess_return"] <= DOWN_THRESHOLD,
        df["excess_return"] >= UP_THRESHOLD,
    ]
    choices = [0, 2]  # 0=DOWN, 2=UP
    df["target"] = np.select(conditions, choices, default=1)  # 1=FLAT

    # 最後 FORWARD_DAYS 天沒有前瞻報酬，target 設 NaN
    df.loc[df["forward_return"].isna(), "target"] = np.nan
    return df
