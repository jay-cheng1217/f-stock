"""Market-regime percentile features for model training and inference.

All features are computed from information available before the row's trading
date. For a stock row dated T, the joined regime values use market data through
T-1, then express that value as a rolling 252-trading-day percentile.
"""

from __future__ import annotations

import glob
import os
from functools import lru_cache

import numpy as np
import pandas as pd

from ml.config import DAILY_K_DIR, INDEX_DIR, REGIME_FEATURE_COLS

PERCENTILE_WINDOW = 252
BREATH_WINDOW = 20


def _rolling_percentile(series: pd.Series, window: int = PERCENTILE_WINDOW) -> pd.Series:
    """Return the percentile rank of the latest value inside each trailing window."""

    def _pct(values: np.ndarray) -> float:
        values = values[~np.isnan(values)]
        if len(values) == 0:
            return np.nan
        return float((values <= values[-1]).mean())

    return series.rolling(window, min_periods=20).apply(_pct, raw=True).astype(np.float32)


def _load_taiex(index_dir: str) -> pd.DataFrame:
    path = os.path.join(index_dir, "index_TWII.csv")
    if not os.path.exists(path):
        return pd.DataFrame(columns=["Date", *REGIME_FEATURE_COLS])
    taiex = pd.read_csv(path, dtype={"Date": str})
    taiex["Date"] = pd.to_datetime(taiex["Date"])
    taiex["Close"] = pd.to_numeric(taiex["Close"], errors="coerce")
    return taiex.sort_values("Date").reset_index(drop=True)


def _build_market_breadth(daily_k_dir: str) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for path in glob.glob(os.path.join(daily_k_dir, "*.csv")):
        ticker = os.path.splitext(os.path.basename(path))[0]
        if not ticker.isdigit():
            continue
        try:
            df = pd.read_csv(path, usecols=["Date", "Close"], dtype={"Date": str})
        except Exception:
            continue
        df["Date"] = pd.to_datetime(df["Date"])
        df["Close"] = pd.to_numeric(df["Close"], errors="coerce")
        df = df.sort_values("Date")
        df["is_up"] = df["Close"].pct_change() > 0
        frames.append(df[["Date", "is_up"]])

    if not frames:
        return pd.DataFrame(columns=["Date", "market_breadth_20d_pct"])

    breadth = pd.concat(frames, ignore_index=True)
    daily = (
        breadth.groupby("Date")["is_up"]
        .mean()
        .sort_index()
        .rename("breadth_ratio")
        .reset_index()
    )
    # Shift once: feature for date T can only use breadth observed through T-1.
    daily["breadth_20d_lagged"] = daily["breadth_ratio"].rolling(
        BREATH_WINDOW,
        min_periods=5,
    ).mean().shift(1)
    daily["market_breadth_20d_pct"] = _rolling_percentile(daily["breadth_20d_lagged"])
    return daily[["Date", "market_breadth_20d_pct"]]


@lru_cache(maxsize=4)
def build_market_regime_features(
    index_dir: str = INDEX_DIR,
    daily_k_dir: str = DAILY_K_DIR,
) -> pd.DataFrame:
    """Build daily T-1 market-regime percentile features."""

    taiex = _load_taiex(index_dir)
    if taiex.empty:
        return pd.DataFrame(columns=["Date", *REGIME_FEATURE_COLS])

    close = taiex["Close"]
    raw_20d = close.pct_change(20).shift(1)
    ma60 = close.rolling(60, min_periods=30).mean()
    raw_vs_ma60 = (close / ma60.replace(0, np.nan) - 1).shift(1)

    out = taiex[["Date"]].copy()
    out["taiex_20d_return_pct"] = _rolling_percentile(raw_20d)
    out["taiex_vs_ma60_pct"] = _rolling_percentile(raw_vs_ma60)

    breadth = _build_market_breadth(daily_k_dir)
    if not breadth.empty:
        out = pd.merge_asof(
            out.sort_values("Date"),
            breadth.sort_values("Date"),
            on="Date",
            direction="backward",
        )
    else:
        out["market_breadth_20d_pct"] = np.nan

    for col in REGIME_FEATURE_COLS:
        if col not in out.columns:
            out[col] = np.nan
        out[col] = pd.to_numeric(out[col], errors="coerce").clip(0.0, 1.0).astype(np.float32)

    return out[["Date", *REGIME_FEATURE_COLS]].sort_values("Date").reset_index(drop=True)


def add_market_regime_features(
    frame: pd.DataFrame,
    *,
    date_col: str = "Date",
    index_dir: str = INDEX_DIR,
    daily_k_dir: str = DAILY_K_DIR,
) -> pd.DataFrame:
    """Join T-1 regime percentile features onto a stock/date frame."""

    out = frame.copy()
    out[date_col] = pd.to_datetime(out[date_col])
    out = out.drop(columns=[c for c in REGIME_FEATURE_COLS if c in out.columns])
    regime = build_market_regime_features(index_dir=index_dir, daily_k_dir=daily_k_dir)
    if regime.empty:
        for col in REGIME_FEATURE_COLS:
            out[col] = np.nan
        return out

    out = pd.merge_asof(
        out.sort_values(date_col),
        regime.sort_values("Date").rename(columns={"Date": date_col}),
        on=date_col,
        direction="backward",
    ).sort_index()

    for col in REGIME_FEATURE_COLS:
        if col not in out.columns:
            out[col] = np.nan

    return out
