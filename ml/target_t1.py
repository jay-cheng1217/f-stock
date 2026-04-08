"""T+1 target definitions for next-day momentum models.

V3: All return targets are now excess returns (stock - TWII) to force the model
to learn stock-selection alpha rather than market beta.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

T1_HIGH_HIT_THRESHOLD = 0.03

T1_LABEL_COLUMNS = [
    "t1_open_return",
    "t1_close_return",
    "t1_high_return",
    "t1_low_return",
    "t1_hit_3pct",
    "t1_close_positive",
    "t1_open_to_close_return",
    "t1_open_to_close_positive",
    "t1_high_from_open",
    "t1_low_from_open",
    "t1_open_next_close_return",
    "t1_open_next_close_positive",
]


def compute_t1_targets(
    df: pd.DataFrame,
    high_hit_threshold: float = T1_HIGH_HIT_THRESHOLD,
    twii_df: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Add next-day excess return labels for T+1 momentum research.

    All return labels are now **excess returns** (stock return - TWII return)
    so the model must learn to pick stocks that outperform the market,
    not just ride market beta.

    Parameters
    ----------
    df:
        Daily OHLCV frame sorted by date.
    high_hit_threshold:
        Threshold for the binary "tomorrow can rally at least X% above market" label.
    twii_df:
        TWII index DataFrame with Date and twii_close columns.
        If None, falls back to absolute returns.
    """
    out = df.copy()

    close = pd.to_numeric(out["Close"], errors="coerce").replace(0, np.nan)
    next_open = pd.to_numeric(out["Open"], errors="coerce").shift(-1)
    next_close = pd.to_numeric(out["Close"], errors="coerce").shift(-1)
    next_high = pd.to_numeric(out["High"], errors="coerce").shift(-1)
    next_low = pd.to_numeric(out["Low"], errors="coerce").shift(-1)

    # Compute raw absolute returns first
    raw_close_return = (next_close / close - 1.0)

    # Compute TWII next-day return for excess calculation
    twii_next_return = pd.Series(0.0, index=out.index, dtype=np.float32)
    if twii_df is not None and "twii_close" in twii_df.columns:
        twii = twii_df[["Date", "twii_close"]].copy()
        twii["twii_next_return"] = (
            twii["twii_close"].shift(-1) / twii["twii_close"] - 1.0
        ).astype(np.float32)
        out = out.merge(twii[["Date", "twii_next_return"]], on="Date", how="left")
        twii_next_return = out["twii_next_return"].fillna(0.0)
        out.drop(columns=["twii_next_return"], inplace=True)

    # All returns are now excess (stock - market)
    out["t1_open_return"] = ((next_open / close - 1.0) - twii_next_return).astype(np.float32)
    out["t1_close_return"] = (raw_close_return - twii_next_return).astype(np.float32)
    out["t1_high_return"] = ((next_high / close - 1.0) - twii_next_return).astype(np.float32)
    out["t1_low_return"] = ((next_low / close - 1.0) - twii_next_return).astype(np.float32)

    hit_mask = out["t1_high_return"].notna()
    out["t1_hit_3pct"] = np.where(
        hit_mask,
        (out["t1_high_return"] >= high_hit_threshold).astype(np.int8),
        np.nan,
    )

    # close-to-close positive: did next day's close beat market?
    close_mask = out["t1_close_return"].notna()
    out["t1_close_positive"] = np.where(
        close_mask,
        (out["t1_close_return"] > 0).astype(np.int8),
        np.nan,
    )

    # open-to-close excess return
    next_open_safe = next_open.replace(0, np.nan)
    out["t1_open_to_close_return"] = (
        (next_close / next_open_safe - 1.0) - twii_next_return
    ).astype(np.float32)

    otc_mask = out["t1_open_to_close_return"].notna()
    out["t1_open_to_close_positive"] = np.where(
        otc_mask,
        (out["t1_open_to_close_return"] > 0).astype(np.int8),
        np.nan,
    )

    # high/low relative to next open (excess)
    out["t1_high_from_open"] = (
        (next_high / next_open_safe - 1.0) - twii_next_return
    ).astype(np.float32)
    out["t1_low_from_open"] = (
        (next_low / next_open_safe - 1.0) - twii_next_return
    ).astype(np.float32)

    # open-to-next-close excess return
    today_open = pd.to_numeric(out["Open"], errors="coerce").replace(0, np.nan)
    out["t1_open_next_close_return"] = (
        (next_close / today_open - 1.0) - twii_next_return
    ).astype(np.float32)

    onc_mask = out["t1_open_next_close_return"].notna()
    out["t1_open_next_close_positive"] = np.where(
        onc_mask,
        (out["t1_open_next_close_return"] > 0).astype(np.int8),
        np.nan,
    )

    return out
