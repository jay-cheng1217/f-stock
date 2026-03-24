"""Dataset builder for T+1 momentum / next-day strength research."""

from __future__ import annotations

import glob
import os

import numpy as np
import pandas as pd
from tqdm import tqdm

from ml.config import (
    BASE_DIR,
    DAILY_K_DIR,
    INDEX_DIR,
    MIN_AVG_VOLUME,
    MIN_HISTORY_DAYS,
    MIN_PRICE,
)
from ml.features.institutional import compute_institutional_features
from ml.features.sector import compute_sector_features
from ml.features.technical import compute_technical_features
from ml.target_t1 import T1_LABEL_COLUMNS, compute_t1_targets

FOREIGN_DIR = os.path.join(BASE_DIR, "外資持股")

T1_CONTEXT_COLUMNS = [
    "ticker",
    "Date",
    "Open",
    "High",
    "Low",
    "Close",
    "Volume",
]

T1_MARKET_FEATURE_COLUMNS = [
    "twii_return_1d",
    "twii_return_5d",
    "gspc_return_1d",
    "gspc_return_5d",
    "sox_return_1d",
    "sox_return_5d",
    "vix_level",
    "vix_return_1d",
    "vix_return_5d",
    "usdtwd_return_1d",
    "usdtwd_return_5d",
]

T1_BASE_FEATURE_COLUMNS = [
    "price_vs_ma5",
    "price_vs_ma20",
    "price_vs_ma60",
    "return_1d",
    "return_3d",
    "return_5d",
    "volatility_5d",
    "volatility_20d",
    "gap_pct",
    "high_low_range",
    "vol_ratio_5_20",
    "vol_zscore",
    "atr_pct",
    "bb_position",
    "rsi_6",
    "rsi_14",
    "macd_hist",
    "kd_k",
    "kd_d",
    "body_ratio",
    "upper_shadow_ratio",
    "lower_shadow_ratio",
    "foreign_cumsum_1d_norm",
    "trust_cumsum_1d_norm",
    "dealer_cumsum_1d_norm",
    "foreign_cumsum_3d_norm",
    "trust_cumsum_3d_norm",
    "inst_total_5d_norm",
    "margin_change_5d",
    "short_change_5d",
    "margin_short_ratio",
    "turnover_rate",
    "chip_diverge_bear",
    "chip_diverge_bull",
]

T1_CUSTOM_FEATURE_COLUMNS = [
    "t1_body_signed",
    "t1_close_location",
    "t1_open_location",
    "t1_upper_wick_pct",
    "t1_lower_wick_pct",
    "t1_range_pct",
    "t1_range_expansion_20d",
    "t1_volume_burst_5d",
    "t1_volume_burst_20d",
    "t1_volume_change_1d",
    "t1_inst_net_ratio_1d",
    "t1_foreign_ratio_1d",
    "t1_trust_ratio_1d",
    "t1_dealer_ratio_1d",
    "t1_turnover_zscore_20",
    "t1_gap_above_prev_high",
    "t1_close_above_prev_high",
    "t1_breakout_20d",
    "t1_distance_from_prev_high",
    "t1_distance_from_prev_low",
    "t1_up_streak",
    "t1_strong_close_flag",
    "t1_long_upper_shadow_flag",
]

T1_SECTOR_FEATURE_COLUMNS = [
    "sector_return_rank",
    "sector_avg_return_5d",
    "sector_relative_return_5d",
    "sector_momentum_5d",
    "sector_breadth",
    "sector_id",
]

T1_FEATURE_COLUMNS = (
    T1_BASE_FEATURE_COLUMNS
    + T1_CUSTOM_FEATURE_COLUMNS
    + T1_MARKET_FEATURE_COLUMNS
    + T1_SECTOR_FEATURE_COLUMNS
)

_ISSUED_SHARES_CACHE: dict[str, float] | None = None
_MARKET_FEATURES_CACHE: pd.DataFrame | None = None


def _list_daily_tickers() -> list[str]:
    return sorted(
        [
            os.path.splitext(name)[0]
            for name in os.listdir(DAILY_K_DIR)
            if name.endswith(".csv") and os.path.splitext(name)[0].isdigit()
        ]
    )


def _load_issued_shares() -> dict[str, float]:
    if not os.path.isdir(FOREIGN_DIR):
        return {}
    files = sorted(glob.glob(os.path.join(FOREIGN_DIR, "*.csv")))
    if not files:
        return {}
    try:
        df = pd.read_csv(files[-1], dtype={"Ticker": str})
        return dict(zip(df["Ticker"], df["Issued_Shares"]))
    except Exception:
        return {}


def _get_issued_shares() -> dict[str, float]:
    global _ISSUED_SHARES_CACHE
    if _ISSUED_SHARES_CACHE is None:
        _ISSUED_SHARES_CACHE = _load_issued_shares()
    return _ISSUED_SHARES_CACHE


def _load_index_close(filename: str) -> pd.DataFrame | None:
    path = os.path.join(INDEX_DIR, filename)
    if not os.path.exists(path):
        return None
    try:
        df = pd.read_csv(path, parse_dates=["Date"])
    except Exception:
        return None
    if "Close" not in df.columns:
        return None
    df = df[["Date", "Close"]].dropna(subset=["Close"]).sort_values("Date")
    return df.reset_index(drop=True)


def _build_market_features() -> pd.DataFrame:
    global _MARKET_FEATURES_CACHE
    if _MARKET_FEATURES_CACHE is not None:
        return _MARKET_FEATURES_CACHE

    merged: pd.DataFrame | None = None
    specs = [
        ("index_TWII.csv", "twii", False),
        ("index_GSPC.csv", "gspc", False),
        ("index_SOX.csv", "sox", False),
        ("index_VIX.csv", "vix", True),
        ("index_USDTWDX.csv", "usdtwd", False),
    ]

    for filename, prefix, keep_level in specs:
        idx = _load_index_close(filename)
        if idx is None or idx.empty:
            continue
        idx = idx.copy()
        if keep_level:
            idx[f"{prefix}_level"] = pd.to_numeric(idx["Close"], errors="coerce").astype(np.float32)
        idx[f"{prefix}_return_1d"] = idx["Close"].pct_change(1).astype(np.float32)
        idx[f"{prefix}_return_5d"] = idx["Close"].pct_change(5).astype(np.float32)
        cols = ["Date"] + [col for col in idx.columns if col.startswith(prefix)]
        feat = idx[cols]
        merged = (
            feat
            if merged is None
            else pd.merge_asof(
                merged.sort_values("Date"),
                feat.sort_values("Date"),
                on="Date",
                direction="backward",
            )
        )

    if merged is None:
        merged = pd.DataFrame(columns=["Date"] + T1_MARKET_FEATURE_COLUMNS)

    _MARKET_FEATURES_CACHE = merged
    return merged


def _merge_market_features(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["Date"] = pd.to_datetime(out["Date"])
    market_df = _build_market_features()
    if market_df.empty:
        for col in T1_MARKET_FEATURE_COLUMNS:
            out[col] = np.nan
        return out

    out = pd.merge_asof(
        out.sort_values("Date"),
        market_df.sort_values("Date"),
        on="Date",
        direction="backward",
    )
    for col in T1_MARKET_FEATURE_COLUMNS:
        if col not in out.columns:
            out[col] = np.nan
    return out


def _add_turnover_rate(df: pd.DataFrame, ticker: str) -> pd.DataFrame:
    out = df.copy()
    shares = _get_issued_shares().get(str(ticker))
    if not shares or shares <= 0 or "Volume" not in out.columns:
        out["turnover_rate"] = np.nan
        return out

    lots_outstanding = shares / 1000.0
    out["turnover_rate"] = np.where(
        lots_outstanding > 0,
        (pd.to_numeric(out["Volume"], errors="coerce") / 1000.0) / lots_outstanding * 100.0,
        np.nan,
    ).astype(np.float32)
    return out


def _clip_float(series: pd.Series, lower: float | None = None, upper: float | None = None) -> pd.Series:
    out = pd.to_numeric(series, errors="coerce")
    if lower is not None or upper is not None:
        out = out.clip(lower=lower, upper=upper)
    return out.astype(np.float32)


def _add_t1_short_features(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()

    open_ = pd.to_numeric(out["Open"], errors="coerce")
    high = pd.to_numeric(out["High"], errors="coerce")
    low = pd.to_numeric(out["Low"], errors="coerce")
    close = pd.to_numeric(out["Close"], errors="coerce")
    volume = pd.to_numeric(out["Volume"], errors="coerce")

    prev_high = high.shift(1)
    prev_low = low.shift(1)
    day_range = (high - low).replace(0, np.nan)
    body = close - open_
    upper_wick = high - pd.concat([open_, close], axis=1).max(axis=1)
    lower_wick = pd.concat([open_, close], axis=1).min(axis=1) - low

    out["t1_body_signed"] = _clip_float(body / open_.replace(0, np.nan), -0.30, 0.30)
    out["t1_close_location"] = _clip_float((close - low) / day_range, 0.0, 1.0)
    out["t1_open_location"] = _clip_float((open_ - low) / day_range, 0.0, 1.0)
    out["t1_upper_wick_pct"] = _clip_float(upper_wick / close.replace(0, np.nan), 0.0, 0.30)
    out["t1_lower_wick_pct"] = _clip_float(lower_wick / close.replace(0, np.nan), 0.0, 0.30)
    out["t1_range_pct"] = _clip_float(day_range / close.replace(0, np.nan), 0.0, 0.50)

    range_base = out["t1_range_pct"].rolling(20, min_periods=5).mean()
    out["t1_range_expansion_20d"] = _clip_float(
        out["t1_range_pct"] / range_base.replace(0, np.nan),
        0.0,
        10.0,
    )

    vol_ma5 = volume.rolling(5, min_periods=3).mean()
    vol_ma20 = volume.rolling(20, min_periods=5).mean()
    out["t1_volume_burst_5d"] = _clip_float(volume / vol_ma5.replace(0, np.nan), 0.0, 20.0)
    out["t1_volume_burst_20d"] = _clip_float(volume / vol_ma20.replace(0, np.nan), 0.0, 20.0)
    out["t1_volume_change_1d"] = _clip_float(volume.pct_change(), -0.95, 20.0)

    foreign = pd.to_numeric(out.get("Foreign_BuySell", 0.0), errors="coerce")
    trust = pd.to_numeric(out.get("Trust_BuySell", 0.0), errors="coerce")
    dealer = pd.to_numeric(out.get("Dealer_BuySell", 0.0), errors="coerce")
    volume_safe = volume.replace(0, np.nan)
    inst_total = foreign + trust + dealer
    out["t1_inst_net_ratio_1d"] = _clip_float(inst_total / volume_safe, -1.0, 1.0)
    out["t1_foreign_ratio_1d"] = _clip_float(foreign / volume_safe, -1.0, 1.0)
    out["t1_trust_ratio_1d"] = _clip_float(trust / volume_safe, -1.0, 1.0)
    out["t1_dealer_ratio_1d"] = _clip_float(dealer / volume_safe, -1.0, 1.0)

    turnover_mean = pd.to_numeric(out.get("turnover_rate"), errors="coerce").rolling(20, min_periods=5).mean()
    turnover_std = pd.to_numeric(out.get("turnover_rate"), errors="coerce").rolling(20, min_periods=5).std()
    out["t1_turnover_zscore_20"] = _clip_float(
        (pd.to_numeric(out.get("turnover_rate"), errors="coerce") - turnover_mean)
        / turnover_std.replace(0, np.nan),
        -10.0,
        10.0,
    )

    out["t1_gap_above_prev_high"] = (open_ > prev_high).fillna(False).astype(np.float32)
    out["t1_close_above_prev_high"] = (close > prev_high).fillna(False).astype(np.float32)
    breakout_high = high.shift(1).rolling(20, min_periods=5).max()
    out["t1_breakout_20d"] = _clip_float(close / breakout_high.replace(0, np.nan) - 1.0, -0.50, 0.50)
    out["t1_distance_from_prev_high"] = _clip_float(close / prev_high.replace(0, np.nan) - 1.0, -0.50, 0.50)
    out["t1_distance_from_prev_low"] = _clip_float(close / prev_low.replace(0, np.nan) - 1.0, -0.50, 1.00)

    is_up = (close.diff() > 0).fillna(False)
    streak_group = (~is_up).cumsum()
    out["t1_up_streak"] = is_up.groupby(streak_group).cumsum().astype(np.float32)
    out["t1_strong_close_flag"] = (out["t1_close_location"] >= 0.80).fillna(False).astype(np.float32)
    out["t1_long_upper_shadow_flag"] = (out["upper_shadow_ratio"] >= 0.40).fillna(False).astype(np.float32)

    return out


def load_single_stock_t1(ticker: str) -> pd.DataFrame | None:
    """Load one stock and compute T+1 labels + short-horizon features."""
    path = os.path.join(DAILY_K_DIR, f"{ticker}.csv")
    if not os.path.exists(path):
        return None

    df = pd.read_csv(path, dtype={"Date": str})
    df["Date"] = pd.to_datetime(df["Date"])
    df = df.sort_values("Date").reset_index(drop=True)

    if len(df) < MIN_HISTORY_DAYS:
        return None

    avg_vol = pd.to_numeric(df["Volume"], errors="coerce").tail(60).mean()
    last_price = pd.to_numeric(df["Close"], errors="coerce").iloc[-1]
    if avg_vol < MIN_AVG_VOLUME or last_price < MIN_PRICE:
        return None

    df = compute_t1_targets(df)
    df = compute_technical_features(df)
    df = compute_institutional_features(df)
    df = _add_turnover_rate(df, ticker)
    df = _merge_market_features(df)
    df = _add_t1_short_features(df)
    df["ticker"] = str(ticker)
    return df


def _finalize_frame(df: pd.DataFrame) -> pd.DataFrame:
    keep = T1_CONTEXT_COLUMNS + T1_FEATURE_COLUMNS + T1_LABEL_COLUMNS
    existing = [col for col in keep if col in df.columns]
    out = df.loc[:, existing].copy()
    float_cols = out.select_dtypes(include=["float64"]).columns
    if len(float_cols) > 0:
        out[float_cols] = out[float_cols].astype(np.float32)
    return out


def build_t1_dataset(max_stocks: int = 0, verbose: bool = True) -> pd.DataFrame:
    """Build a historical training dataset for T+1 momentum models."""
    tickers = _list_daily_tickers()
    if max_stocks > 0:
        tickers = tickers[:max_stocks]

    frames = []
    iterator = tqdm(tickers, desc="Build T+1 dataset") if verbose else tickers
    for ticker in iterator:
        df = load_single_stock_t1(ticker)
        if df is not None and not df.empty:
            frames.append(df)

    if not frames:
        raise ValueError("No eligible stocks were available for the T+1 dataset.")

    dataset = pd.concat(frames, ignore_index=True)
    dataset = compute_sector_features(dataset)
    dataset = dataset.dropna(subset=["t1_high_return", "t1_hit_3pct"]).copy()
    dataset["t1_hit_3pct"] = dataset["t1_hit_3pct"].astype(np.int8)
    dataset = _finalize_frame(dataset)

    if verbose:
        print(f"  rows: {len(dataset):,}")
        print(f"  tickers: {dataset['ticker'].nunique():,}")
        print(
            "  hit_3pct rate: "
            f"{dataset['t1_hit_3pct'].mean() * 100:.2f}%"
        )
    return dataset


def build_latest_t1_snapshot(max_stocks: int = 0, verbose: bool = True) -> pd.DataFrame:
    """Build the latest cross-sectional snapshot for next-day inference."""
    tickers = _list_daily_tickers()
    if max_stocks > 0:
        tickers = tickers[:max_stocks]

    rows = []
    iterator = tqdm(tickers, desc="Build T+1 snapshot") if verbose else tickers
    for ticker in iterator:
        df = load_single_stock_t1(ticker)
        if df is None or df.empty:
            continue
        rows.append(df.iloc[[-1]].copy())

    if not rows:
        raise ValueError("No eligible stocks were available for the T+1 snapshot.")

    snapshot = pd.concat(rows, ignore_index=True)
    snapshot = compute_sector_features(snapshot)
    snapshot = _finalize_frame(snapshot)

    if verbose:
        print(f"  rows: {len(snapshot):,}")
        print(f"  tickers: {snapshot['ticker'].nunique():,}")
    return snapshot

