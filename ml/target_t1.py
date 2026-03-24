"""T+1 target definitions for next-day momentum models."""

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
]


def compute_t1_targets(
    df: pd.DataFrame,
    high_hit_threshold: float = T1_HIGH_HIT_THRESHOLD,
) -> pd.DataFrame:
    """Add next-day return labels for T+1 momentum research.

    Labels are always anchored to today's close so the model can be trained on
    end-of-day inputs and answer practical questions such as:
    - How far can the stock stretch at tomorrow's high?
    - Does the name have enough next-day upside to clear trading friction?

    Parameters
    ----------
    df:
        Daily OHLCV frame sorted by date.
    high_hit_threshold:
        Threshold for the binary "tomorrow can rally at least X%" label.

    Returns
    -------
    pd.DataFrame
        Original frame plus:
        - ``t1_open_return``: next open vs today's close
        - ``t1_close_return``: next close vs today's close
        - ``t1_high_return``: next high vs today's close
        - ``t1_low_return``: next low vs today's close
        - ``t1_hit_3pct``: 1 when ``t1_high_return`` >= threshold, else 0
    """
    out = df.copy()

    close = pd.to_numeric(out["Close"], errors="coerce").replace(0, np.nan)
    next_open = pd.to_numeric(out["Open"], errors="coerce").shift(-1)
    next_close = pd.to_numeric(out["Close"], errors="coerce").shift(-1)
    next_high = pd.to_numeric(out["High"], errors="coerce").shift(-1)
    next_low = pd.to_numeric(out["Low"], errors="coerce").shift(-1)

    out["t1_open_return"] = (next_open / close - 1.0).astype(np.float32)
    out["t1_close_return"] = (next_close / close - 1.0).astype(np.float32)
    out["t1_high_return"] = (next_high / close - 1.0).astype(np.float32)
    out["t1_low_return"] = (next_low / close - 1.0).astype(np.float32)

    hit_mask = out["t1_high_return"].notna()
    out["t1_hit_3pct"] = np.where(
        hit_mask,
        (out["t1_high_return"] >= high_hit_threshold).astype(np.int8),
        np.nan,
    )

    return out
