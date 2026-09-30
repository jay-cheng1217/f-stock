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
from ml.features.tdcc import TDCC_FEATURE_COLS, compute_tdcc_features
from ml.features.technical import compute_technical_features
from ml.target_t1 import T1_LABEL_COLUMNS, compute_t1_targets
from ml.universe import filter_stock_universe

FOREIGN_DIR = os.path.join(BASE_DIR, "外資持股")
TDCC_SUMMARY_PATH = os.path.join(BASE_DIR, "集保分散", "tdcc_summary.csv")


def _load_twii_t1() -> pd.DataFrame | None:
    """Load TWII index for excess return calculation."""
    twii_path = os.path.join(INDEX_DIR, "index_TWII.csv")
    if not os.path.exists(twii_path):
        return None
    df = pd.read_csv(twii_path, dtype={"Date": str})
    df["Date"] = pd.to_datetime(df["Date"])
    df = df.sort_values("Date").reset_index(drop=True)
    return df[["Date", "Close"]].rename(columns={"Close": "twii_close"})


# Module-level TWII cache (loaded once on first use)
_TWII_CACHE: pd.DataFrame | None = None


def _get_twii() -> pd.DataFrame | None:
    global _TWII_CACHE
    if _TWII_CACHE is None:
        _TWII_CACHE = _load_twii_t1()
    return _TWII_CACHE

T1_CONTEXT_COLUMNS = [
    "ticker",
    "Date",
    "Open",
    "High",
    "Low",
    "Close",
    "Volume",
    "VOL_MA_5",
    "VOL_MA_20",
    "AMOUNT_MA_5",
    "AMOUNT_MA_20",
    "atr_pct",        # 保留供 risk parity 部位計算（非模型特徵）
]

T1_MARKET_FEATURE_COLUMNS = [
    # V3: Target 已改超額報酬，大盤絕對特徵大幅削減
    # 移除 twii_return_1d, sox_return_1d — 這些是 beta 信號，
    # 預測超額報酬時模型不應直接看大盤漲跌。
    # 只保留體制型（相對/二元）特徵。
    "vix_percentile_60d",  # 相對恐慌度（百分位，非絕對值）
    "twii_above_ma20",     # 趨勢方向（二元）
    "twii_volatility_20d", # 波動度體制（影響截面分散度）
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
    "atr_pct_rank",  # 截面百分位，取代絕對 atr_pct 避免波動度獨大
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

T1_GAP_CONTINUATION_COLUMNS = [
    # A. Intraday direction (today & recent)
    "t1_intraday_return",
    "t1_intraday_return_ma3",
    "t1_intraday_return_ma5",
    "t1_intraday_win_rate_5d",
    "t1_intraday_win_rate_20d",
    # B. Gap behavior history
    "t1_gap_size_zscore",
    "t1_gap_continuation_rate_20d",
    "t1_gap_reversal_rate_20d",
    "t1_gap_fill_rate_20d",
    # C. Open position / overnight decomposition
    "t1_overnight_share_5d",
    "t1_open_vs_prev_range",
    "t1_gap_vs_atr",
    # D. K-bar continuation
    "t1_close_strength",
    "t1_consec_intraday_up",
    "t1_prev_day_reversal",
    # E. Volume-gap interaction
    "t1_gap_volume_confirm",
    "t1_volume_price_diverge",
]

T1_MARKET_INTRADAY_COLUMNS = [
    "twii_intraday_return",
    "twii_intraday_ma5",
    "twii_gap_reversal_rate_20d",
]

T1_SECTOR_FEATURE_COLUMNS = [
    "sector_return_rank",
    "sector_avg_return_5d",
    "sector_relative_return_5d",
    "sector_momentum_5d",
    "sector_breadth",
    "sector_id",
]

# 集保分散特徵：挑對 T+1 短線最有預測力的 4 個
# （完整 8 個中，retail_pct/whale_pct 絕對值變動慢，對次日預測貢獻低）
T1_TDCC_FEATURE_COLUMNS = [
    "whale_pct_chg",        # 大戶週增減持（最直接的籌碼訊號）
    "retail_pct_chg",       # 散戶週增減持（反向指標）
    "whale_trend_4w",       # 4 週大戶趨勢（持續吸籌 vs 出貨）
    "retail_capitulation",  # 散戶投降指標（底部訊號）
]

# Gap/continuation features disabled: backtest showed they add noise, not signal.
# AUC degraded 0.551→0.539 when included. Keep code for future research.
# To re-enable: add T1_GAP_CONTINUATION_COLUMNS + T1_MARKET_INTRADAY_COLUMNS
T1_FEATURE_COLUMNS = (
    T1_BASE_FEATURE_COLUMNS
    + T1_CUSTOM_FEATURE_COLUMNS
    + T1_MARKET_FEATURE_COLUMNS
    + T1_SECTOR_FEATURE_COLUMNS
    + T1_TDCC_FEATURE_COLUMNS
)

_ISSUED_SHARES_CACHE: dict[str, float] | None = None
_MARKET_FEATURES_CACHE: pd.DataFrame | None = None


def _list_daily_tickers() -> list[str]:
    tickers = [
        os.path.splitext(name)[0]
        for name in os.listdir(DAILY_K_DIR)
        if name.endswith(".csv") and os.path.splitext(name)[0].isdigit()
    ]
    return sorted(filter_stock_universe(tickers))


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


def _load_index_ohlc(filename: str) -> pd.DataFrame | None:
    path = os.path.join(INDEX_DIR, filename)
    if not os.path.exists(path):
        return None
    try:
        df = pd.read_csv(path, parse_dates=["Date"])
    except Exception:
        return None
    needed = {"Date", "Open", "High", "Low", "Close"}
    if not needed.issubset(df.columns):
        return None
    df = df[list(needed)].dropna(subset=["Close", "Open"]).sort_values("Date")
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
        close = pd.to_numeric(idx["Close"], errors="coerce")
        if keep_level:
            # 用 60 日滾動百分位取代絕對值，避免 regime-specific overfitting
            idx[f"{prefix}_percentile_60d"] = close.rolling(60, min_periods=20).apply(
                lambda x: (x[-1] >= x[:-1]).mean() if len(x) > 1 else 0.5, raw=True
            ).astype(np.float32)
        idx[f"{prefix}_return_1d"] = close.pct_change(1).astype(np.float32)
        idx[f"{prefix}_return_5d"] = close.pct_change(5).astype(np.float32)

        # Market regime features for specific indices
        if prefix in ("twii", "gspc", "sox"):
            ma5 = close.rolling(5, min_periods=3).mean()
            ma20 = close.rolling(20, min_periods=10).mean()
            ma60 = close.rolling(60, min_periods=30).mean()
            idx[f"{prefix}_above_ma20"] = (close > ma20).astype(np.float32)
            if prefix == "twii":
                idx[f"{prefix}_above_ma5"] = (close > ma5).astype(np.float32)
                idx[f"{prefix}_above_ma60"] = (close > ma60).astype(np.float32)
                idx[f"{prefix}_ma5_slope"] = (ma5.pct_change(3) * 100).clip(-5, 5).astype(np.float32)
                idx[f"{prefix}_ma20_slope"] = (ma20.pct_change(5) * 100).clip(-5, 5).astype(np.float32)
                idx[f"{prefix}_volatility_20d"] = close.pct_change().rolling(20, min_periods=10).std().astype(np.float32)

        if prefix == "vix":
            vix_ma20 = close.rolling(20, min_periods=10).mean()
            idx["vix_ma20_ratio"] = (close / vix_ma20.replace(0, np.nan)).clip(0.5, 2.0).astype(np.float32)

        cols = ["Date"] + [col for col in idx.columns if col.startswith(prefix) or col == "vix_ma20_ratio"]
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

    # -- TWII intraday regime features --
    twii_ohlc = _load_index_ohlc("index_TWII.csv")
    if twii_ohlc is not None and not twii_ohlc.empty:
        tc = pd.to_numeric(twii_ohlc["Close"], errors="coerce")
        to = pd.to_numeric(twii_ohlc["Open"], errors="coerce").replace(0, np.nan)
        tpc = tc.shift(1).replace(0, np.nan)

        twii_intraday = (tc - to) / to
        twii_ohlc["twii_intraday_return"] = twii_intraday.astype(np.float32)
        twii_ohlc["twii_intraday_ma5"] = twii_intraday.rolling(5, min_periods=3).mean().astype(np.float32)

        twii_gap = (to - tpc) / tpc
        twii_gap_up = (twii_gap > 0.001).astype(float)
        twii_gap_up_reverse = ((twii_gap > 0.001) & (tc < to)).astype(float)
        twii_gap_up_count = twii_gap_up.rolling(20, min_periods=5).sum().replace(0, np.nan)
        twii_ohlc["twii_gap_reversal_rate_20d"] = (
            twii_gap_up_reverse.rolling(20, min_periods=5).sum() / twii_gap_up_count
        ).astype(np.float32)

        twii_intra_feat = twii_ohlc[["Date"] + T1_MARKET_INTRADAY_COLUMNS]
        if merged is not None:
            merged = pd.merge_asof(
                merged.sort_values("Date"),
                twii_intra_feat.sort_values("Date"),
                on="Date",
                direction="backward",
            )
        else:
            merged = twii_intra_feat

    if merged is None:
        merged = pd.DataFrame(columns=["Date"] + T1_MARKET_FEATURE_COLUMNS + T1_MARKET_INTRADAY_COLUMNS)

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
    for col in T1_MARKET_FEATURE_COLUMNS + T1_MARKET_INTRADAY_COLUMNS:
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


def _add_t1_gap_continuation_features(df: pd.DataFrame) -> pd.DataFrame:
    """Gap behavior / intraday continuation features for Open→Close prediction."""
    out = df.copy()

    open_ = pd.to_numeric(out["Open"], errors="coerce")
    high = pd.to_numeric(out["High"], errors="coerce")
    low = pd.to_numeric(out["Low"], errors="coerce")
    close = pd.to_numeric(out["Close"], errors="coerce")
    volume = pd.to_numeric(out["Volume"], errors="coerce")
    prev_close = close.shift(1)
    prev_high = high.shift(1)
    prev_low = low.shift(1)

    open_safe = open_.replace(0, np.nan)
    prev_close_safe = prev_close.replace(0, np.nan)

    # -- A. Intraday direction (today & recent) --
    intraday_ret = (close - open_) / open_safe
    out["t1_intraday_return"] = _clip_float(intraday_ret, -0.15, 0.15)
    out["t1_intraday_return_ma3"] = _clip_float(
        intraday_ret.rolling(3, min_periods=2).mean(), -0.10, 0.10
    )
    out["t1_intraday_return_ma5"] = _clip_float(
        intraday_ret.rolling(5, min_periods=3).mean(), -0.10, 0.10
    )
    intraday_up = (close > open_).astype(float)
    out["t1_intraday_win_rate_5d"] = intraday_up.rolling(5, min_periods=3).mean().astype(np.float32)
    out["t1_intraday_win_rate_20d"] = intraday_up.rolling(20, min_periods=10).mean().astype(np.float32)

    # -- B. Gap behavior history --
    gap = (open_ - prev_close) / prev_close_safe
    gap_mean_20 = gap.rolling(20, min_periods=10).mean()
    gap_std_20 = gap.rolling(20, min_periods=10).std().replace(0, np.nan)
    out["t1_gap_size_zscore"] = _clip_float((gap - gap_mean_20) / gap_std_20, -5, 5)

    gap_up = (gap > 0.001).astype(float)
    gap_up_and_continue = ((gap > 0.001) & (close > open_)).astype(float)
    gap_up_and_reverse = ((gap > 0.001) & (close < open_)).astype(float)
    gap_up_and_fill = ((gap > 0.001) & (low <= prev_close)).astype(float)

    gap_up_count_20 = gap_up.rolling(20, min_periods=5).sum().replace(0, np.nan)
    out["t1_gap_continuation_rate_20d"] = (
        gap_up_and_continue.rolling(20, min_periods=5).sum() / gap_up_count_20
    ).astype(np.float32)
    out["t1_gap_reversal_rate_20d"] = (
        gap_up_and_reverse.rolling(20, min_periods=5).sum() / gap_up_count_20
    ).astype(np.float32)
    out["t1_gap_fill_rate_20d"] = (
        gap_up_and_fill.rolling(20, min_periods=5).sum() / gap_up_count_20
    ).astype(np.float32)

    # -- C. Open position / overnight decomposition --
    total_ret = close / prev_close_safe - 1.0
    overnight_ret = open_ / prev_close_safe - 1.0
    total_abs_5d = total_ret.abs().rolling(5, min_periods=3).sum().replace(0, np.nan)
    overnight_abs_5d = overnight_ret.abs().rolling(5, min_periods=3).sum()
    out["t1_overnight_share_5d"] = _clip_float(overnight_abs_5d / total_abs_5d, 0, 1)

    prev_range = (prev_high - prev_low).replace(0, np.nan)
    out["t1_open_vs_prev_range"] = _clip_float(
        (open_ - prev_low) / prev_range, -1.0, 2.0
    )

    atr_pct = pd.to_numeric(out.get("atr_pct"), errors="coerce").replace(0, np.nan)
    out["t1_gap_vs_atr"] = _clip_float(gap / atr_pct, -5.0, 5.0)

    # -- D. K-bar continuation --
    hl_range = (high - low).replace(0, np.nan)
    out["t1_close_strength"] = _clip_float((close - open_) / hl_range, -1.0, 1.0)

    is_intraday_up = (close > open_).astype(float)
    intraday_streak_group = (is_intraday_up != is_intraday_up.shift(1)).cumsum()
    out["t1_consec_intraday_up"] = (
        is_intraday_up.groupby(intraday_streak_group).cumsum()
        * is_intraday_up
    ).astype(np.float32)

    prev_gap_up = (gap.shift(0) > 0.001)
    prev_intraday_down = (close.shift(0) < open_.shift(0))
    out["t1_prev_day_reversal"] = (prev_gap_up & prev_intraday_down).astype(np.float32)

    # -- E. Volume-gap interaction --
    vol_ma20 = volume.rolling(20, min_periods=10).mean().replace(0, np.nan)
    vol_ratio = volume / vol_ma20
    gap_vol_raw = pd.Series(
        np.where(gap > 0.001, vol_ratio, 0.0), index=out.index
    )
    out["t1_gap_volume_confirm"] = _clip_float(gap_vol_raw, 0.0, 10.0)

    ret_sign = np.sign(close - prev_close)
    vol_change_sign = np.sign(volume - volume.shift(1))
    out["t1_volume_price_diverge"] = (ret_sign != vol_change_sign).astype(np.float32)

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

    df = compute_t1_targets(df, twii_df=_get_twii())
    df = compute_technical_features(df, ticker=ticker)
    df = compute_institutional_features(df)
    df = compute_tdcc_features(df, ticker, TDCC_SUMMARY_PATH)
    df = _add_turnover_rate(df, ticker)
    df = _merge_market_features(df)
    df = _add_t1_short_features(df)
    # Gap features computed but not in T1_FEATURE_COLUMNS (disabled after backtest)
    df = _add_t1_gap_continuation_features(df)
    df["ticker"] = str(ticker)
    return df


# Binary/event features that should NOT be z-scored in T1
_T1_ZSCORE_SKIP = {
    "atr_pct_rank", "vix_percentile_60d",
    "twii_above_ma20", "twii_volatility_20d",
    "t1_strong_close_flag", "t1_long_upper_shadow_flag",
    "chip_diverge_bear", "chip_diverge_bull",
    "t1_intraday_win_rate_5d", "t1_intraday_win_rate_20d",
    "t1_gap_continuation_rate_20d", "t1_gap_reversal_rate_20d",
    "t1_gap_fill_rate_20d", "t1_overnight_share_5d",
    "t1_prev_day_reversal", "t1_volume_price_diverge",
    "twii_gap_reversal_rate_20d",
}

_T1_WINSORIZE_Q_LOW = 0.01
_T1_WINSORIZE_Q_HIGH = 0.99
_T1_WINSORIZE_MIN_GROUP_SIZE = 50


def _t1_continuous_feature_columns(dataset: pd.DataFrame) -> list[str]:
    return [
        c for c in T1_FEATURE_COLUMNS
        if c in dataset.columns and c not in _T1_ZSCORE_SKIP
    ]


def _winsorize_t1_cross_section(dataset: pd.DataFrame) -> pd.DataFrame:
    """Clip per-day cross-sectional outliers before z-score normalization."""
    cols_to_clip = _t1_continuous_feature_columns(dataset)
    if not cols_to_clip:
        return dataset

    def _winsorize_series(series: pd.Series) -> pd.Series:
        clean = pd.to_numeric(series, errors="coerce").replace([np.inf, -np.inf], np.nan)
        valid = clean.dropna()
        if len(valid) < _T1_WINSORIZE_MIN_GROUP_SIZE:
            return clean
        lower = valid.quantile(_T1_WINSORIZE_Q_LOW)
        upper = valid.quantile(_T1_WINSORIZE_Q_HIGH)
        return clean.clip(lower=lower, upper=upper)

    grouped = dataset.groupby("Date", group_keys=False)
    for col in cols_to_clip:
        dataset[col] = grouped[col].transform(_winsorize_series).astype(np.float32)
    return dataset


def _apply_t1_cross_sectional_zscore(dataset: pd.DataFrame) -> pd.DataFrame:
    """Cross-sectional z-score for T1 continuous features."""
    cols_to_zscore = _t1_continuous_feature_columns(dataset)
    if not cols_to_zscore:
        return dataset

    grouped = dataset.groupby("Date")[cols_to_zscore]
    means = grouped.transform("mean")
    stds = grouped.transform("std").replace(0, np.nan)
    dataset[cols_to_zscore] = ((dataset[cols_to_zscore] - means) / stds).astype(np.float32)
    return dataset


def _finalize_frame(df: pd.DataFrame) -> pd.DataFrame:
    keep = list(dict.fromkeys(T1_CONTEXT_COLUMNS + T1_FEATURE_COLUMNS + T1_LABEL_COLUMNS))
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
    # atr_pct 截面百分位：同一天內跨股票排序，削弱波動度絕對值的宰制力
    if "atr_pct" in dataset.columns:
        dataset["atr_pct_rank"] = dataset.groupby("Date")["atr_pct"].rank(pct=True).astype(np.float32)
    else:
        dataset["atr_pct_rank"] = np.nan

    # Cross-sectional z-score 已關閉：T+1 使用絕對報酬 target，
    # 不需截面標準化，避免壓縮 prob 分布導致模型失去區分度
    # dataset = _winsorize_t1_cross_section(dataset)
    # dataset = _apply_t1_cross_sectional_zscore(dataset)

    dataset = dataset.dropna(subset=[
        "t1_high_return", "t1_hit_3pct", "t1_excess_positive", "t1_close_positive",
        "t1_trade_positive", "t1_trade_return", "t1_trade_high", "t1_trade_low",
        "t1_high_return_3d", "t1_low_return_3d", "t1_hit_3pct_3d",
        "t1_close_positive_3d", "t1_close_ge_1pct_3d", "t1_open_close_positive_3d",
    ]).copy()
    dataset["t1_hit_3pct"] = dataset["t1_hit_3pct"].astype(np.int8)
    dataset["t1_excess_positive"] = dataset["t1_excess_positive"].astype(np.int8)
    dataset["t1_close_positive"] = dataset["t1_close_positive"].astype(np.int8)
    dataset["t1_trade_positive"] = dataset["t1_trade_positive"].astype(np.int8)
    dataset["t1_hit_3pct_3d"] = dataset["t1_hit_3pct_3d"].astype(np.int8)
    dataset["t1_close_positive_3d"] = dataset["t1_close_positive_3d"].astype(np.int8)
    dataset["t1_close_ge_1pct_3d"] = dataset["t1_close_ge_1pct_3d"].astype(np.int8)
    dataset["t1_open_close_positive_3d"] = dataset["t1_open_close_positive_3d"].astype(np.int8)
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

    # 只保留最新交易日的截面。停牌/最後交易日落後的股票(例:6176 停在 2026-08-12,
    # 其餘 1156 檔已到 08-18)不該混進「當日快照」——否則會用過期特徵當今日選股,
    # 且因 predict_t1 的 pred_date 取排序後第一列,停牌股的舊日期會污染整個預測檔名
    # (2026-08 事故:T+1 預測凍結在 08-12,連帶 unified signals / V4 shadow 全失敗)。
    latest_date = snapshot["Date"].max()
    stale_mask = snapshot["Date"] != latest_date
    if stale_mask.any():
        dropped = snapshot.loc[stale_mask, "ticker"].tolist()
        if verbose:
            print(f"  剔除 {len(dropped)} 檔非最新交易日({str(latest_date)[:10]})的停牌/落後股: {dropped[:10]}")
        snapshot = snapshot.loc[~stale_mask].reset_index(drop=True)

    snapshot = compute_sector_features(snapshot)
    # atr_pct 截面百分位（snapshot 只有一天，直接 rank）
    if "atr_pct" in snapshot.columns:
        snapshot["atr_pct_rank"] = snapshot["atr_pct"].rank(pct=True).astype(np.float32)
    else:
        snapshot["atr_pct_rank"] = np.nan

    # Cross-sectional z-score 已關閉（與 build_t1_dataset 同步）
    # snapshot = _winsorize_t1_cross_section(snapshot)
    # snapshot = _apply_t1_cross_sectional_zscore(snapshot)

    snapshot = _finalize_frame(snapshot)

    if verbose:
        print(f"  rows: {len(snapshot):,}")
        print(f"  tickers: {snapshot['ticker'].nunique():,}")
    return snapshot
