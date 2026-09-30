"""T+1 target definitions for next-day momentum models.

V3: All return targets are now excess returns (stock - TWII) to force the model
to learn stock-selection alpha rather than market beta.

V4: 3-day horizon labels (t1_*_3d). Canonical spec:
  entry = Open[t+1], hold = 3 days, exit = Close[t+3],
  hit = max(High[t+1:t+3]) / Open[t+1] >= 1.03.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

T1_HIGH_HIT_THRESHOLD = 0.03
T1_HORIZON_DAYS = 3

T1_LABEL_COLUMNS = [
    "t1_open_return",
    "t1_close_return",
    "t1_high_return",
    "t1_low_return",
    "t1_hit_3pct",
    "t1_excess_positive",
    "t1_close_positive",
    "t1_open_to_close_return",
    "t1_open_to_close_excess_positive",
    "t1_high_from_open",
    "t1_low_from_open",
    "t1_open_next_close_return",
    "t1_open_next_close_excess_positive",
    # Absolute trade columns: Open[t+1] → Close[t+1], no TWII adjustment
    "t1_trade_return",
    "t1_trade_positive",
    "t1_trade_high",
    "t1_trade_low",
    # 3-day horizon labels (all based on entry = Open[t+1])
    "t1_high_return_3d",
    "t1_low_return_3d",
    "t1_hit_3pct_3d",
    "t1_close_return_3d",
    "t1_close_positive_3d",
    "t1_close_ge_1pct_3d",
    "t1_open_close_return_3d",
    "t1_open_close_positive_3d",
]


def compute_t1_targets(
    df: pd.DataFrame,
    high_hit_threshold: float = T1_HIGH_HIT_THRESHOLD,
    twii_df: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Add next-day and 3-day excess return labels for T+1 momentum research.

    All return labels are now **excess returns** (stock return - TWII return)
    so the model must learn to pick stocks that outperform the market,
    not just ride market beta.

    Parameters
    ----------
    df:
        Daily OHLCV frame sorted by date.
    high_hit_threshold:
        Threshold for the binary "can rally at least X% above market" label.
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

    raw_close_return = (next_close / close - 1.0)

    # Compute TWII next-day return for excess calculation
    twii_next_return = pd.Series(0.0, index=out.index, dtype=np.float32)
    twii_3d_return = pd.Series(0.0, index=out.index, dtype=np.float32)
    if twii_df is not None and "twii_close" in twii_df.columns:
        twii = twii_df[["Date", "twii_close"]].copy()
        twii["twii_next_return"] = (
            twii["twii_close"].shift(-1) / twii["twii_close"] - 1.0
        ).astype(np.float32)
        twii["twii_3d_return"] = (
            twii["twii_close"].shift(-T1_HORIZON_DAYS) / twii["twii_close"] - 1.0
        ).astype(np.float32)
        out = out.merge(
            twii[["Date", "twii_next_return", "twii_3d_return"]],
            on="Date", how="left",
        )
        twii_next_return = out["twii_next_return"].fillna(0.0)
        twii_3d_return = out["twii_3d_return"].fillna(0.0)
        out.drop(columns=["twii_next_return", "twii_3d_return"], inplace=True)

    # === T+1 (next-day) labels ===
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

    close_mask = out["t1_close_return"].notna()
    out["t1_excess_positive"] = np.where(
        close_mask,
        (out["t1_close_return"] > 0).astype(np.int8),
        np.nan,
    )

    raw_close_mask = raw_close_return.notna()
    out["t1_close_positive"] = np.where(
        raw_close_mask,
        (raw_close_return > 0).astype(np.int8),
        np.nan,
    )

    next_open_safe = next_open.replace(0, np.nan)
    out["t1_open_to_close_return"] = (
        (next_close / next_open_safe - 1.0) - twii_next_return
    ).astype(np.float32)

    otc_mask = out["t1_open_to_close_return"].notna()
    out["t1_open_to_close_excess_positive"] = np.where(
        otc_mask,
        (out["t1_open_to_close_return"] > 0).astype(np.int8),
        np.nan,
    )

    out["t1_high_from_open"] = (
        (next_high / next_open_safe - 1.0) - twii_next_return
    ).astype(np.float32)
    out["t1_low_from_open"] = (
        (next_low / next_open_safe - 1.0) - twii_next_return
    ).astype(np.float32)

    today_open = pd.to_numeric(out["Open"], errors="coerce").replace(0, np.nan)
    out["t1_open_next_close_return"] = (
        (next_close / today_open - 1.0) - twii_next_return
    ).astype(np.float32)

    onc_mask = out["t1_open_next_close_return"].notna()
    out["t1_open_next_close_excess_positive"] = np.where(
        onc_mask,
        (out["t1_open_next_close_return"] > 0).astype(np.int8),
        np.nan,
    )

    # === Absolute trade columns (canonical 1-day spec) ===
    # Entry = Open[t+1], Exit = Close[t+1], NO benchmark adjustment.
    # These are what the trader actually earns/risks.
    raw_otc = next_close / next_open_safe - 1.0
    out["t1_trade_return"] = raw_otc.astype(np.float32)
    out["t1_trade_high"] = (next_high / next_open_safe - 1.0).astype(np.float32)
    out["t1_trade_low"] = (next_low / next_open_safe - 1.0).astype(np.float32)

    trade_mask = out["t1_trade_return"].notna()
    out["t1_trade_positive"] = np.where(
        trade_mask,
        (out["t1_trade_return"] > 0).astype(np.int8),
        np.nan,
    )

    # === 3-day horizon labels ===
    # Canonical spec (single source of truth for all downstream systems):
    #   entry_price  = Open[t+1]
    #   hold period  = T+1, T+2, T+3
    #   exit_price   = Close[t+3]
    #   hit_3pct     = max(High[t+1:t+3]) / entry_price >= 1.03
    #   pnl          = exit_price / entry_price - 1
    high_series = pd.to_numeric(out["High"], errors="coerce")
    low_series = pd.to_numeric(out["Low"], errors="coerce")
    close_series = pd.to_numeric(out["Close"], errors="coerce")

    entry_price_3d = next_open.replace(0, np.nan)  # Open[t+1]
    close_at_3d = close_series.shift(-T1_HORIZON_DAYS)  # Close[t+3]

    rolling_3d_high = pd.concat(
        [high_series.shift(-d) for d in range(1, T1_HORIZON_DAYS + 1)],
        axis=1,
    ).max(axis=1)

    rolling_3d_low = pd.concat(
        [low_series.shift(-d) for d in range(1, T1_HORIZON_DAYS + 1)],
        axis=1,
    ).min(axis=1)

    # Max upside / max downside from entry over 3 days (absolute)
    out["t1_high_return_3d"] = (rolling_3d_high / entry_price_3d - 1.0).astype(np.float32)
    out["t1_low_return_3d"] = (rolling_3d_low / entry_price_3d - 1.0).astype(np.float32)

    # Hit 3% within 3 days from entry price
    hit_3d_mask = out["t1_high_return_3d"].notna()
    out["t1_hit_3pct_3d"] = np.where(
        hit_3d_mask,
        (out["t1_high_return_3d"] >= high_hit_threshold).astype(np.int8),
        np.nan,
    )

    # PnL: Open[t+1] → Close[t+3] (absolute)
    raw_pnl_3d = close_at_3d / entry_price_3d - 1.0
    out["t1_close_return_3d"] = raw_pnl_3d.astype(np.float32)

    close_3d_mask = out["t1_close_return_3d"].notna()
    out["t1_close_positive_3d"] = np.where(
        close_3d_mask,
        (out["t1_close_return_3d"] > 0).astype(np.int8),
        np.nan,
    )

    out["t1_close_ge_1pct_3d"] = np.where(
        close_3d_mask,
        (out["t1_close_return_3d"] >= 0.01).astype(np.int8),
        np.nan,
    )

    # Excess PnL: Open[t+1] → Close[t+3] minus TWII 3d return
    out["t1_open_close_return_3d"] = (raw_pnl_3d - twii_3d_return).astype(np.float32)

    oc3d_mask = out["t1_open_close_return_3d"].notna()
    out["t1_open_close_positive_3d"] = np.where(
        oc3d_mask,
        (out["t1_open_close_return_3d"] > 0).astype(np.int8),
        np.nan,
    )

    return out
