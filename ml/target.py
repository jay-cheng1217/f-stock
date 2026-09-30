"""目標變數建構：計算超額報酬（相對大盤）與分類標籤

Canonical 20D trade spec (single source of truth):
  Entry = Open[t+1], Exit = Close[t+20]
  trade_return_20d = Close[t+20] / Open[t+1] - 1  (absolute)
  trade_excess_return_20d = trade_return_20d - twii_20d_return  (alpha)
"""
import pandas as pd
import numpy as np
from ml.config import FORWARD_DAYS, UP_THRESHOLD, DOWN_THRESHOLD
from ml.corporate_actions import action_window_factor

V2_FORWARD_DAYS = 20
MAX_TARGET_GAP_DAYS = 30


def _continuous_forward_window(dates: pd.Series, periods: int) -> pd.Series:
    """Return True when a row-based forward window stays in one trading era."""
    normalized = pd.to_datetime(dates, errors="coerce")
    segment = normalized.diff().dt.days.gt(MAX_TARGET_GAP_DAYS).cumsum()
    return normalized.notna() & normalized.shift(-periods).notna() & segment.eq(
        segment.shift(-periods)
    )


def compute_target(
    df: pd.DataFrame,
    twii_df: pd.DataFrame = None,
    *,
    ticker: str | None = None,
    exdiv_df: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """計算前瞻報酬、分類標籤、可交易口徑 20D target。

    Args:
        df: 含 Date, Open, Close 的 DataFrame (單支股票)
        twii_df: 含 Date, twii_close 的價格指數 DataFrame；若另含
            twii_total_return_close，excess 類標籤採雙邊含息口徑。
        ticker: 股票代碼，用於套用官方公司行動比例因子。
        exdiv_df: 可選的公司行動 fixture；正式執行使用官方日曆快取。
    Returns:
        df 加上 forward_return, excess_return, target,
        forward_return_20d, excess_return_20d,
        trade_return_20d, trade_excess_return_20d 欄位
    """
    df = df.copy()
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")

    close = pd.to_numeric(df["Close"], errors="coerce")
    next_open = pd.to_numeric(df["Open"], errors="coerce").shift(-1)
    next_open_safe = next_open.replace(0, np.nan)
    close_at_5 = close.shift(-FORWARD_DAYS)
    close_at_20 = close.shift(-V2_FORWARD_DAYS)

    raw_forward_5d = close_at_5 / close - 1
    raw_forward_20d = close_at_20 / close - 1
    raw_trade_20d = close_at_20 / next_open_safe - 1
    valid_forward_5d = _continuous_forward_window(df["Date"], FORWARD_DAYS)
    valid_forward_20d = _continuous_forward_window(df["Date"], V2_FORWARD_DAYS)
    raw_forward_5d = raw_forward_5d.where(valid_forward_5d)
    raw_forward_20d = raw_forward_20d.where(valid_forward_20d)
    raw_trade_20d = raw_trade_20d.where(valid_forward_20d)

    # close[t] 持有口徑：(t, t+N]；Open[t+1] 交易口徑：(t+1, t+20]。
    forward_factor_5d = action_window_factor(
        df["Date"], ticker, 0, FORWARD_DAYS, exdiv_df
    )
    forward_factor_20d = action_window_factor(
        df["Date"], ticker, 0, V2_FORWARD_DAYS, exdiv_df
    )
    trade_factor_20d = action_window_factor(
        df["Date"], ticker, 1, V2_FORWARD_DAYS, exdiv_df
    )

    # 個股前瞻報酬（V1: 5日, V2: 20日）採官方 before/after 比例還原。
    df["forward_return"] = (
        close_at_5 / close * forward_factor_5d - 1
    ).where(valid_forward_5d)
    df["forward_return_20d"] = (
        close_at_20 / close * forward_factor_20d - 1
    ).where(valid_forward_20d)

    # === Canonical 20D trade return: Open[t+1] → Close[t+20] ===
    df["trade_return_20d"] = (
        close_at_20 / next_open_safe * trade_factor_20d - 1
    ).where(valid_forward_20d).astype(np.float32)

    # 計算大盤前瞻報酬並合併
    twii_20d_ret = pd.Series(0.0, index=df.index, dtype=np.float32)
    if twii_df is not None:
        twii = twii_df.copy()
        twii["Date"] = pd.to_datetime(twii["Date"], errors="coerce")
        price_index = pd.to_numeric(twii["twii_close"], errors="coerce")
        has_total_return = (
            "twii_total_return_close" in twii.columns
            and pd.to_numeric(twii["twii_total_return_close"], errors="coerce").notna().any()
        )
        benchmark = (
            pd.to_numeric(twii["twii_total_return_close"], errors="coerce")
            if has_total_return
            else price_index
        )
        twii["twii_forward_return"] = (
            benchmark.shift(-FORWARD_DAYS) / benchmark - 1
        )
        twii["twii_forward_return_20d"] = (
            benchmark.shift(-V2_FORWARD_DAYS) / benchmark - 1
        )
        df = df.merge(
            twii[["Date", "twii_forward_return", "twii_forward_return_20d"]],
            on="Date", how="left",
        )
        # 若官方報酬指數尚未備妥，excess 類標籤維持雙邊未還原；
        # 禁止把含息個股減未含息價格指數，避免新增殖利率偏差。
        raw_forward_5d_aligned = pd.Series(raw_forward_5d.to_numpy(), index=df.index)
        raw_forward_20d_aligned = pd.Series(raw_forward_20d.to_numpy(), index=df.index)
        raw_trade_20d_aligned = pd.Series(raw_trade_20d.to_numpy(), index=df.index)
        stock_5d_for_excess = (
            df["forward_return"] if has_total_return else raw_forward_5d_aligned
        )
        stock_20d_for_excess = (
            df["forward_return_20d"] if has_total_return else raw_forward_20d_aligned
        )
        trade_20d_for_excess = (
            df["trade_return_20d"] if has_total_return else raw_trade_20d_aligned
        )
        df["excess_return"] = stock_5d_for_excess - df["twii_forward_return"]
        df["excess_return_20d"] = stock_20d_for_excess - df["twii_forward_return_20d"]
        twii_20d_ret = df["twii_forward_return_20d"].fillna(0.0)
        df.drop(columns=["twii_forward_return", "twii_forward_return_20d"], inplace=True)
    else:
        df["excess_return"] = df["forward_return"]
        df["excess_return_20d"] = df["forward_return_20d"]

    # === Trade excess return: alpha portion of the tradeable return ===
    trade_for_excess = trade_20d_for_excess if twii_df is not None else df["trade_return_20d"]
    df["trade_excess_return_20d"] = (trade_for_excess - twii_20d_ret).astype(np.float32)

    # 以超額報酬分類（V1 用）
    conditions = [
        df["excess_return"] <= DOWN_THRESHOLD,
        df["excess_return"] >= UP_THRESHOLD,
    ]
    choices = [0, 2]  # 0=DOWN, 2=UP
    df["target"] = np.select(conditions, choices, default=1)  # 1=FLAT

    df.loc[df["forward_return"].isna(), "target"] = np.nan
    return df
