"""Offline TQuant-inspired reference sleeves for entry selection.

The script ports the reusable ideas from tejtw/TQuant-Lab into a small,
read-only benchmark layer for this repository:

- VAM: volatility-adjusted 21D momentum.
- MRAT/MAD: MA21 / MA200 distance with price-action structure checks.
- BAZ: multi-timescale MACD response score from the LambdaMART example.
- Expanded momentum: log-price slope weighted by R-squared.
- Revenue, institutional, financing, and settlement-risk sleeves using
  already-available local Taiwan equity data.

It does not mutate production gates, model files, schedulers, databases, or
paper portfolios.  Use it to decide whether a Champion recommendation has
independent strategy support.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd

BASE_DIR = Path(os.environ.get("STOCK_BASE_DIR") or Path(__file__).resolve().parents[1])
sys.path.insert(0, str(BASE_DIR))

from ml.config import MODEL_DIR, REPORT_DIR  # noqa: E402

DEFAULT_DB_PATH = BASE_DIR / "stock.duckdb"
DEFAULT_OUTPUT_PREFIX = "tquant_reference_sleeves_latest"
SIGNAL_RE = re.compile(r"^unified_signals_(\d{4}-\d{2}-\d{2})\.csv$")
BAZ_PARAMS = ((8, 24), (16, 48), (32, 96))
VALUATION_DIR = BASE_DIR / "估值資料"
FINANCIAL_AVAILABLE_LAG_DAYS = 75


@dataclass(frozen=True)
class BasketMetric:
    basket: str
    names: int
    avg_return: float | None
    median_return: float | None
    win_rate: float | None
    avg_pred_return_20d: float | None
    avg_support_votes: float | None
    avg_risk_flags: float | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "basket": self.basket,
            "names": self.names,
            "avg_return": self.avg_return,
            "median_return": self.median_return,
            "win_rate": self.win_rate,
            "avg_pred_return_20d": self.avg_pred_return_20d,
            "avg_tquant_support_votes": self.avg_support_votes,
            "avg_tquant_risk_flags": self.avg_risk_flags,
        }


def _fmt_pct(value: float | None, digits: int = 2) -> str:
    if value is None or pd.isna(value):
        return "-"
    return f"{float(value) * 100:+.{digits}f}%"


def _fmt_float(value: float | None, digits: int = 2) -> str:
    if value is None or pd.isna(value):
        return "-"
    return f"{float(value):.{digits}f}"


def _coerce_date(value: str | pd.Timestamp) -> pd.Timestamp:
    result = pd.Timestamp(value).normalize()
    if pd.isna(result):
        raise ValueError(f"Invalid date: {value}")
    return result


def _numeric_series(df: pd.DataFrame, columns: list[str], default: float = np.nan) -> pd.Series:
    for column in columns:
        if column in df.columns:
            return pd.to_numeric(df[column], errors="coerce")
    return pd.Series(default, index=df.index, dtype="float64")


def _text_series(df: pd.DataFrame, columns: list[str]) -> pd.Series:
    for column in columns:
        if column in df.columns:
            return df[column].fillna("").astype(str)
    return pd.Series("", index=df.index, dtype="object")


def _signal_paths_between(start_date: str, end_date: str, model_dir: str | Path = MODEL_DIR) -> list[Path]:
    start = _coerce_date(start_date)
    end = _coerce_date(end_date)
    paths: list[tuple[pd.Timestamp, Path]] = []
    for path in Path(model_dir).glob("unified_signals_*.csv"):
        match = SIGNAL_RE.match(path.name)
        if not match:
            continue
        signal_date = _coerce_date(match.group(1))
        if start <= signal_date <= end:
            paths.append((signal_date, path))
    return [path for _, path in sorted(paths)]


def _read_signal(path: Path) -> pd.DataFrame:
    match = SIGNAL_RE.match(path.name)
    if not match:
        raise ValueError(f"Not a unified signal file: {path}")
    df = pd.read_csv(path, encoding="utf-8-sig", dtype={"ticker": str})
    if df.empty:
        return df
    df["ticker"] = df["ticker"].astype(str).str.strip().str.zfill(4)
    df["signal_date"] = match.group(1)
    return df


def _trading_dates_between(
    start_date: str,
    end_date: str,
    *,
    db_path: str | Path = DEFAULT_DB_PATH,
) -> list[str]:
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        dates = con.execute(
            """
            SELECT DISTINCT Date::DATE AS trading_date
            FROM daily_k
            WHERE Date BETWEEN ? AND ?
            ORDER BY trading_date
            """,
            [start_date, end_date],
        ).fetchall()
    finally:
        con.close()
    return [str(row[0]) for row in dates]


def _latest_price_date(db_path: str | Path = DEFAULT_DB_PATH) -> str:
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        row = con.execute("SELECT MAX(Date)::DATE FROM daily_k").fetchone()
    finally:
        con.close()
    if not row or row[0] is None:
        raise RuntimeError("daily_k has no price dates")
    return str(row[0])


def _next_trading_date(signal_date: str, trading_dates: list[str]) -> str | None:
    for trading_date in trading_dates:
        if trading_date > signal_date:
            return trading_date
    return None


def _horizon_mark_date(entry_date: str, trading_dates: list[str], horizon_days: int) -> str | None:
    try:
        idx = trading_dates.index(entry_date)
    except ValueError:
        return None
    mark_idx = idx + horizon_days - 1
    if mark_idx >= len(trading_dates):
        return None
    return trading_dates[mark_idx]


def _ewm_last(values: np.ndarray, span: int) -> np.ndarray:
    alpha = 2.0 / (span + 1)
    out = np.empty_like(values, dtype="float64")
    out[0] = values[0]
    for i in range(1, len(values)):
        out[i] = alpha * values[i] + (1 - alpha) * out[i - 1]
    return out


def _baz_response(x: float) -> float:
    return float(x * math.exp(-(x**2) / 4.0) / 0.89)


def _single_baz(close: np.ndarray, fast: int, slow: int) -> float:
    if len(close) < slow + 10:
        return np.nan
    macd = _ewm_last(close, fast)[-1] - _ewm_last(close, slow)[-1]
    vol = float(np.nanstd(close[-min(63, len(close)) :]))
    if vol <= 1e-8 or pd.isna(vol):
        return np.nan
    return _baz_response(float(macd / vol))


def calc_baz(close: pd.Series) -> float:
    values = pd.to_numeric(close, errors="coerce").dropna().to_numpy(dtype="float64")
    if len(values) < 40:
        return np.nan
    scores = [_single_baz(values, fast, slow) for fast, slow in BAZ_PARAMS]
    valid = [score for score in scores if not pd.isna(score)]
    return float(np.mean(valid)) if valid else np.nan


def calc_expanded_momentum_score(close: pd.Series, window: int = 20) -> float:
    values = pd.to_numeric(close, errors="coerce").dropna().tail(window).to_numpy(dtype="float64")
    if len(values) < window or np.any(values <= 0):
        return np.nan
    x = np.arange(len(values), dtype="float64")
    y = np.log(values)
    x_mean = x.mean()
    y_mean = y.mean()
    x_var = float(((x - x_mean) ** 2).sum())
    if x_var <= 0:
        return np.nan
    slope = float(((x - x_mean) * (y - y_mean)).sum() / x_var)
    intercept = y_mean - slope * x_mean
    y_pred = intercept + slope * x
    ss_res = float(((y - y_pred) ** 2).sum())
    ss_tot = float(((y - y_mean) ** 2).sum())
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0
    annualized_slope = (math.exp(slope * 252) - 1.0) * 100.0
    return float(annualized_slope * max(r2, 0.0))


def calc_price_action_structure(price_history: pd.DataFrame) -> tuple[bool, dict[str, float | None]]:
    if len(price_history) < 22:
        return False, {
            "close_to_20d_high": None,
            "higher_low_10d": None,
            "range_compression": None,
        }

    hist = price_history.tail(25).copy()
    close = pd.to_numeric(hist["Close"], errors="coerce")
    high = pd.to_numeric(hist["High"], errors="coerce")
    low = pd.to_numeric(hist["Low"], errors="coerce")

    close_to_20d_high = float(close.iloc[-1] / high.tail(20).max()) if high.tail(20).max() else np.nan
    recent_low = float(close.tail(10).min())
    previous_low = float(close.iloc[-20:-10].min())
    higher_low = recent_low > previous_low

    range_pct = (high - low) / close.replace(0, np.nan)
    recent_range = float(range_pct.tail(5).mean())
    previous_range = float(range_pct.iloc[-15:-5].mean())
    compression = recent_range < previous_range if not pd.isna(previous_range) else False

    passed = close_to_20d_high > 0.88 and higher_low and compression
    return bool(passed), {
        "close_to_20d_high": close_to_20d_high,
        "higher_low_10d": float(higher_low),
        "range_compression": float(compression),
    }


def calc_aroon(high: pd.Series, low: pd.Series, window: int = 25) -> tuple[float, float]:
    """Return latest Aroon Up/Down using the TEJ/TQuant manual convention."""

    high_values = pd.to_numeric(high, errors="coerce").dropna().tail(window).to_numpy(dtype="float64")
    low_values = pd.to_numeric(low, errors="coerce").dropna().tail(window).to_numpy(dtype="float64")
    if len(high_values) < window or len(low_values) < window:
        return np.nan, np.nan

    high_pos = int(np.nanargmax(high_values))
    low_pos = int(np.nanargmin(low_values))
    periods_since_high = window - 1 - high_pos
    periods_since_low = window - 1 - low_pos
    aroon_up = (window - periods_since_high) / window * 100.0
    aroon_down = (window - periods_since_low) / window * 100.0
    return float(aroon_up), float(aroon_down)


def _quarter_end(year: int, season: int) -> pd.Timestamp:
    if season == 1:
        return pd.Timestamp(year=year, month=3, day=31)
    if season == 2:
        return pd.Timestamp(year=year, month=6, day=30)
    if season == 3:
        return pd.Timestamp(year=year, month=9, day=30)
    return pd.Timestamp(year=year, month=12, day=31)


def _empty_financial_features(status: str) -> dict[str, Any]:
    return {
        "financial_data_status": status,
        "financial_available_lag_days": FINANCIAL_AVAILABLE_LAG_DAYS,
        "financial_year": np.nan,
        "financial_season": np.nan,
        "tquant_gross_margin_pct": np.nan,
        "tquant_net_margin_pct": np.nan,
        "tquant_gross_margin_delta_yoy": np.nan,
        "tquant_net_margin_delta_yoy": np.nan,
        "tquant_financial_quality_vote": False,
        "tquant_financial_turnaround_vote": False,
        "tquant_margin_deterioration_risk": False,
    }


@lru_cache(maxsize=8192)
def _latest_financial_features(
    ticker: str,
    signal_date: str,
    db_path: str | Path = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    """Conservative point-in-time financial snapshot.

    The local financials table has fiscal year/season but no announcement date.
    To avoid look-ahead leakage, a quarter is treated as available only after a
    fixed 75-day lag from quarter end.
    """

    clean_ticker = str(ticker).strip().zfill(4)
    try:
        con = duckdb.connect(str(db_path), read_only=True)
        try:
            fin = con.execute(
                """
                SELECT
                  LPAD(CAST(Ticker AS VARCHAR), 4, '0') AS ticker,
                  Year,
                  Season,
                  Gross_Margin_Pct,
                  Net_Margin_Pct,
                  Revenue_M
                FROM financials
                WHERE LPAD(CAST(Ticker AS VARCHAR), 4, '0') = ?
                ORDER BY Year, Season
                """,
                [clean_ticker],
            ).fetchdf()
        finally:
            con.close()
    except Exception:
        return _empty_financial_features("read_error")

    if fin.empty:
        return _empty_financial_features("missing")

    fin = fin.copy()
    fin["Year"] = pd.to_numeric(fin["Year"], errors="coerce")
    fin["Season"] = pd.to_numeric(fin["Season"], errors="coerce")
    fin = fin.dropna(subset=["Year", "Season"])
    if fin.empty:
        return _empty_financial_features("invalid")

    fin["quarter_end"] = [
        _quarter_end(int(year), int(season)) for year, season in zip(fin["Year"], fin["Season"])
    ]
    fin["available_date"] = fin["quarter_end"] + pd.Timedelta(days=FINANCIAL_AVAILABLE_LAG_DAYS)
    fin["gross_margin"] = pd.to_numeric(fin["Gross_Margin_Pct"], errors="coerce") / 100.0
    fin["net_margin"] = pd.to_numeric(fin["Net_Margin_Pct"], errors="coerce") / 100.0

    available = fin[fin["available_date"] <= pd.Timestamp(signal_date)].sort_values("available_date")
    if available.empty:
        return _empty_financial_features("not_yet_available")

    latest = available.iloc[-1]
    same_season_prior = available[
        (available["Season"] == latest["Season"]) & (available["Year"] == latest["Year"] - 1)
    ]
    prior = same_season_prior.iloc[-1] if not same_season_prior.empty else None

    gross = float(latest["gross_margin"]) if not pd.isna(latest["gross_margin"]) else np.nan
    net = float(latest["net_margin"]) if not pd.isna(latest["net_margin"]) else np.nan
    gross_delta = (
        gross - float(prior["gross_margin"])
        if prior is not None and not pd.isna(gross) and not pd.isna(prior["gross_margin"])
        else np.nan
    )
    net_delta = (
        net - float(prior["net_margin"])
        if prior is not None and not pd.isna(net) and not pd.isna(prior["net_margin"])
        else np.nan
    )

    quality_vote = (
        not pd.isna(gross)
        and not pd.isna(net)
        and gross >= 0.15
        and net > 0
        and (pd.isna(gross_delta) or gross_delta >= -0.03)
    )
    turnaround_vote = (
        not pd.isna(gross_delta)
        and not pd.isna(net_delta)
        and gross_delta > 0
        and net_delta > 0
    )
    margin_risk = (
        (not pd.isna(gross_delta) and gross_delta <= -0.05)
        or (not pd.isna(net_delta) and net_delta <= -0.05)
        or (not pd.isna(net) and net < 0)
    )

    return {
        "financial_data_status": "ok",
        "financial_available_lag_days": FINANCIAL_AVAILABLE_LAG_DAYS,
        "financial_year": int(latest["Year"]),
        "financial_season": int(latest["Season"]),
        "tquant_gross_margin_pct": gross,
        "tquant_net_margin_pct": net,
        "tquant_gross_margin_delta_yoy": gross_delta,
        "tquant_net_margin_delta_yoy": net_delta,
        "tquant_financial_quality_vote": bool(quality_vote),
        "tquant_financial_turnaround_vote": bool(turnaround_vote),
        "tquant_margin_deterioration_risk": bool(margin_risk),
    }


@lru_cache(maxsize=128)
def _valuation_frame_for_signal_date(signal_date: str) -> tuple[str | None, pd.DataFrame]:
    if not VALUATION_DIR.exists():
        return None, pd.DataFrame()
    target = pd.Timestamp(signal_date).strftime("%Y%m%d")
    candidates: list[tuple[str, Path]] = []
    for path in VALUATION_DIR.glob("valuation_*.csv"):
        match = re.match(r"valuation_(\d{8})\.csv$", path.name)
        if match and match.group(1) <= target:
            candidates.append((match.group(1), path))
    if not candidates:
        return None, pd.DataFrame()
    date_key, path = sorted(candidates)[-1]
    try:
        frame = pd.read_csv(path, encoding="utf-8-sig", dtype={"Ticker": str})
    except Exception:
        return date_key, pd.DataFrame()
    if frame.empty:
        return date_key, frame
    frame["Ticker"] = frame["Ticker"].astype(str).str.strip().str.zfill(4)
    return date_key, frame


def _latest_valuation_features(ticker: str, signal_date: str) -> dict[str, Any]:
    date_key, frame = _valuation_frame_for_signal_date(signal_date)
    if date_key is None:
        return {
            "valuation_data_status": "missing",
            "valuation_date": "",
            "tquant_pe_ratio": np.nan,
            "tquant_pb_ratio": np.nan,
            "tquant_dividend_yield": np.nan,
            "tquant_value_vote": False,
            "tquant_valuation_risk": False,
        }
    if frame.empty:
        return {
            "valuation_data_status": "read_error",
            "valuation_date": date_key,
            "tquant_pe_ratio": np.nan,
            "tquant_pb_ratio": np.nan,
            "tquant_dividend_yield": np.nan,
            "tquant_value_vote": False,
            "tquant_valuation_risk": False,
        }

    row = frame[frame["Ticker"].eq(str(ticker).strip().zfill(4))]
    if row.empty:
        return {
            "valuation_data_status": "ticker_missing",
            "valuation_date": date_key,
            "tquant_pe_ratio": np.nan,
            "tquant_pb_ratio": np.nan,
            "tquant_dividend_yield": np.nan,
            "tquant_value_vote": False,
            "tquant_valuation_risk": False,
        }

    record = row.iloc[0]
    pe = pd.to_numeric(pd.Series([record.get("PE_Ratio")]), errors="coerce").iloc[0]
    pb = pd.to_numeric(pd.Series([record.get("PB_Ratio")]), errors="coerce").iloc[0]
    div_yield = pd.to_numeric(pd.Series([record.get("Dividend_Yield")]), errors="coerce").iloc[0]

    pe_ok = not pd.isna(pe) and 0 < float(pe) <= 25
    pb_ok = not pd.isna(pb) and 0 < float(pb) <= 2.5
    yield_ok = not pd.isna(div_yield) and float(div_yield) >= 3.0
    value_vote = (pe_ok and pb_ok) or (pb_ok and yield_ok)
    valuation_risk = (not pd.isna(pe) and float(pe) <= 0) or (
        not pd.isna(pe) and not pd.isna(pb) and float(pe) > 60 and float(pb) > 8
    )

    return {
        "valuation_data_status": "ok",
        "valuation_date": date_key,
        "tquant_pe_ratio": float(pe) if not pd.isna(pe) else np.nan,
        "tquant_pb_ratio": float(pb) if not pd.isna(pb) else np.nan,
        "tquant_dividend_yield": float(div_yield) if not pd.isna(div_yield) else np.nan,
        "tquant_value_vote": bool(value_vote),
        "tquant_valuation_risk": bool(valuation_risk),
    }


def _load_price_history(
    tickers: list[str],
    start_date: str,
    end_date: str,
    *,
    db_path: str | Path = DEFAULT_DB_PATH,
) -> pd.DataFrame:
    if not tickers:
        return pd.DataFrame()
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        prices = con.execute(
            """
            SELECT
              Ticker AS ticker,
              Date::DATE AS date,
              Open,
              High,
              Low,
              Close,
              Volume,
              Foreign_BuySell,
              Trust_BuySell,
              Dealer_BuySell,
              Margin_Balance,
              Short_Balance,
              RSI_14,
              K,
              D
            FROM daily_k
            WHERE Date BETWEEN ? AND ?
              AND Ticker IN (SELECT unnest(?))
            ORDER BY ticker, date
            """,
            [start_date, end_date, tickers],
        ).fetchdf()
    finally:
        con.close()
    if prices.empty:
        return prices
    prices["ticker"] = prices["ticker"].astype(str).str.zfill(4)
    prices["date"] = prices["date"].astype(str)
    return prices


def _latest_revenue_features(ticker: str, signal_date: str) -> dict[str, Any]:
    path = BASE_DIR / "月營收" / f"revenue_{ticker}.csv"
    if not path.exists():
        return {
            "revenue_data_status": "missing",
            "revenue_yoy_latest": np.nan,
            "revenue_yoy_3m_avg": np.nan,
            "revenue_yoy_momentum": np.nan,
            "tquant_revenue_vote": False,
        }
    try:
        rev = pd.read_csv(path)
    except Exception:
        return {
            "revenue_data_status": "read_error",
            "revenue_yoy_latest": np.nan,
            "revenue_yoy_3m_avg": np.nan,
            "revenue_yoy_momentum": np.nan,
            "tquant_revenue_vote": False,
        }

    if rev.empty or "Date" not in rev or "YoY_pct_change" not in rev:
        return {
            "revenue_data_status": "invalid",
            "revenue_yoy_latest": np.nan,
            "revenue_yoy_3m_avg": np.nan,
            "revenue_yoy_momentum": np.nan,
            "tquant_revenue_vote": False,
        }

    rev = rev.copy()
    rev["rev_month"] = pd.to_datetime(rev["Date"].astype(str) + "-01", errors="coerce")
    rev["available_date"] = rev["rev_month"] + pd.DateOffset(months=1, days=10)
    rev["yoy"] = pd.to_numeric(rev["YoY_pct_change"], errors="coerce") / 100.0
    rev = rev[rev["available_date"] <= pd.Timestamp(signal_date)].sort_values("available_date")
    if rev.empty:
        return {
            "revenue_data_status": "not_yet_available",
            "revenue_yoy_latest": np.nan,
            "revenue_yoy_3m_avg": np.nan,
            "revenue_yoy_momentum": np.nan,
            "tquant_revenue_vote": False,
        }

    yoy_latest = float(rev["yoy"].iloc[-1])
    yoy_3m = float(rev["yoy"].tail(3).mean())
    yoy_mom = float(yoy_latest - rev["yoy"].iloc[-4]) if len(rev) >= 4 else np.nan
    vote = yoy_latest > 0 and yoy_3m > 0 and (pd.isna(yoy_mom) or yoy_mom >= -0.05)
    return {
        "revenue_data_status": "ok",
        "revenue_yoy_latest": yoy_latest,
        "revenue_yoy_3m_avg": yoy_3m,
        "revenue_yoy_momentum": yoy_mom,
        "tquant_revenue_vote": bool(vote),
    }


def _point_in_time_features(
    ticker: str,
    signal_date: str,
    price_history: pd.DataFrame,
) -> dict[str, Any]:
    hist = price_history[
        (price_history["ticker"] == ticker) & (price_history["date"] <= signal_date)
    ].sort_values("date")
    if hist.empty:
        features = {
            "tquant_data_status": "missing_price_history",
            "tquant_vam_21d": np.nan,
            "tquant_ret_21d": np.nan,
            "tquant_vol_21d": np.nan,
            "tquant_mrat_21_200": np.nan,
            "tquant_baz": np.nan,
            "tquant_expanded_momentum": np.nan,
            "tquant_price_structure_vote": False,
            "tquant_close_to_20d_high": np.nan,
            "tquant_higher_low_10d": np.nan,
            "tquant_range_compression": np.nan,
            "tquant_k_value": np.nan,
            "tquant_d_value": np.nan,
            "tquant_kd_reversal_vote": False,
            "tquant_kd_overheat_risk": False,
            "tquant_rsi_14": np.nan,
            "tquant_rsi_slope_3d": np.nan,
            "tquant_rsi_reversal_vote": False,
            "tquant_aroon_up_25": np.nan,
            "tquant_aroon_down_25": np.nan,
            "tquant_aroon_trend_vote": False,
            "tquant_inst_total_5d": np.nan,
            "tquant_inst_total_20d": np.nan,
            "tquant_inst_total_20d_norm": np.nan,
            "tquant_margin_change_5d": np.nan,
            "tquant_price_return_20d": np.nan,
            "tquant_financing_crowding_risk": False,
            "tquant_institutional_vote": False,
        }
        features.update(_latest_financial_features(ticker, signal_date))
        features.update(_latest_valuation_features(ticker, signal_date))
        return features

    close = pd.to_numeric(hist["Close"], errors="coerce")
    high = pd.to_numeric(hist["High"], errors="coerce")
    low = pd.to_numeric(hist["Low"], errors="coerce")
    volume = pd.to_numeric(hist["Volume"], errors="coerce")
    daily_ret = close.pct_change()

    ret_21 = close.iloc[-1] / close.iloc[-22] - 1.0 if len(close) >= 22 and close.iloc[-22] else np.nan
    price_return_20d = float(close.iloc[-1] / close.iloc[-21] - 1.0) if len(close) >= 21 and close.iloc[-21] else np.nan
    vol_21 = float(daily_ret.tail(21).std()) if len(daily_ret.dropna()) >= 10 else np.nan
    vam = float(ret_21 / vol_21) if vol_21 and not pd.isna(ret_21) and vol_21 > 1e-8 else np.nan

    ma21 = close.rolling(21, min_periods=21).mean().iloc[-1] if len(close) >= 21 else np.nan
    ma200 = close.rolling(200, min_periods=180).mean().iloc[-1] if len(close) >= 180 else np.nan
    mrat = float(ma21 / ma200 - 1.0) if ma200 and not pd.isna(ma21) and not pd.isna(ma200) else np.nan

    structure_vote, structure_detail = calc_price_action_structure(hist)
    baz = calc_baz(close)
    expanded = calc_expanded_momentum_score(close)
    aroon_up, aroon_down = calc_aroon(high, low)

    k_value_series = pd.to_numeric(hist["K"] if "K" in hist else pd.Series(np.nan, index=hist.index), errors="coerce")
    d_value_series = pd.to_numeric(hist["D"] if "D" in hist else pd.Series(np.nan, index=hist.index), errors="coerce")
    rsi_series = pd.to_numeric(
        hist["RSI_14"] if "RSI_14" in hist else pd.Series(np.nan, index=hist.index),
        errors="coerce",
    )
    k_value = float(k_value_series.iloc[-1]) if len(k_value_series.dropna()) else np.nan
    d_value = float(d_value_series.iloc[-1]) if len(d_value_series.dropna()) else np.nan
    prev_k = float(k_value_series.iloc[-2]) if len(k_value_series.dropna()) >= 2 else np.nan
    prev_d = float(d_value_series.iloc[-2]) if len(d_value_series.dropna()) >= 2 else np.nan
    rsi_14 = float(rsi_series.iloc[-1]) if len(rsi_series.dropna()) else np.nan
    rsi_slope_3d = float(rsi_series.iloc[-1] - rsi_series.iloc[-4]) if len(rsi_series.dropna()) >= 4 else np.nan

    kd_cross_up = (
        not pd.isna(k_value)
        and not pd.isna(d_value)
        and not pd.isna(prev_k)
        and not pd.isna(prev_d)
        and prev_k <= prev_d
        and k_value > d_value
    )
    kd_reversal_vote = (not pd.isna(k_value) and k_value <= 20) or (
        kd_cross_up and not pd.isna(k_value) and k_value <= 50
    )
    kd_overheat_risk = not pd.isna(k_value) and k_value >= 85 and (
        pd.isna(d_value) or k_value <= d_value or (not pd.isna(price_return_20d) and price_return_20d > 0.10)
    )
    rsi_reversal_vote = (
        not pd.isna(rsi_14)
        and not pd.isna(rsi_slope_3d)
        and rsi_slope_3d > 0
        and ((rsi_14 <= 35) or (45 <= rsi_14 <= 65 and not pd.isna(mrat) and mrat > 0))
    )
    aroon_trend_vote = (
        not pd.isna(aroon_up)
        and not pd.isna(aroon_down)
        and aroon_up > 80
        and aroon_down < 45
    )

    foreign = pd.to_numeric(hist.get("Foreign_BuySell", 0), errors="coerce").fillna(0)
    trust = pd.to_numeric(hist.get("Trust_BuySell", 0), errors="coerce").fillna(0)
    dealer = pd.to_numeric(hist.get("Dealer_BuySell", 0), errors="coerce").fillna(0)
    inst_total = foreign + trust + dealer
    volume_sum_20 = float(volume.tail(20).sum()) if len(volume) else np.nan
    inst_total_5d = float(inst_total.tail(5).sum())
    inst_total_20d = float(inst_total.tail(20).sum())
    inst_norm_20d = inst_total_20d / volume_sum_20 if volume_sum_20 and volume_sum_20 > 0 else np.nan
    institutional_vote = (inst_total_5d > 0 and inst_total_20d >= 0) or (
        not pd.isna(inst_norm_20d) and inst_norm_20d > 0.02
    )

    margin = pd.to_numeric(hist.get("Margin_Balance", np.nan), errors="coerce").ffill()
    margin_change_5d = float(margin.iloc[-1] / margin.iloc[-6] - 1.0) if len(margin.dropna()) >= 6 and margin.iloc[-6] else np.nan
    financing_crowding = (
        not pd.isna(margin_change_5d)
        and not pd.isna(price_return_20d)
        and margin_change_5d > 0.05
        and price_return_20d > 0.10
    )

    features = {
        "tquant_data_status": "ok",
        "tquant_vam_21d": vam,
        "tquant_ret_21d": ret_21,
        "tquant_vol_21d": vol_21,
        "tquant_mrat_21_200": mrat,
        "tquant_baz": baz,
        "tquant_expanded_momentum": expanded,
        "tquant_price_structure_vote": bool(structure_vote),
        "tquant_close_to_20d_high": structure_detail["close_to_20d_high"],
        "tquant_higher_low_10d": structure_detail["higher_low_10d"],
        "tquant_range_compression": structure_detail["range_compression"],
        "tquant_k_value": k_value,
        "tquant_d_value": d_value,
        "tquant_kd_reversal_vote": bool(kd_reversal_vote),
        "tquant_kd_overheat_risk": bool(kd_overheat_risk),
        "tquant_rsi_14": rsi_14,
        "tquant_rsi_slope_3d": rsi_slope_3d,
        "tquant_rsi_reversal_vote": bool(rsi_reversal_vote),
        "tquant_aroon_up_25": aroon_up,
        "tquant_aroon_down_25": aroon_down,
        "tquant_aroon_trend_vote": bool(aroon_trend_vote),
        "tquant_inst_total_5d": inst_total_5d,
        "tquant_inst_total_20d": inst_total_20d,
        "tquant_inst_total_20d_norm": inst_norm_20d,
        "tquant_institutional_vote": bool(institutional_vote),
        "tquant_margin_change_5d": margin_change_5d,
        "tquant_price_return_20d": price_return_20d,
        "tquant_financing_crowding_risk": bool(financing_crowding),
    }
    features.update(_latest_financial_features(ticker, signal_date))
    features.update(_latest_valuation_features(ticker, signal_date))
    return features


def _annotate_cross_section_ranks(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    rank_specs = {
        "tquant_vam_21d": "tquant_vam_pct_rank",
        "tquant_mrat_21_200": "tquant_mrat_pct_rank",
        "tquant_baz": "tquant_baz_pct_rank",
        "tquant_expanded_momentum": "tquant_expanded_momentum_pct_rank",
        "revenue_yoy_3m_avg": "tquant_revenue_pct_rank",
        "tquant_gross_margin_pct": "tquant_gross_margin_pct_rank",
        "tquant_net_margin_pct": "tquant_net_margin_pct_rank",
        "tquant_aroon_up_25": "tquant_aroon_up_pct_rank",
    }
    for source, target in rank_specs.items():
        if source not in out:
            out[target] = np.nan
            continue
        out[target] = out.groupby("signal_date")[source].rank(pct=True, ascending=True)
    return out


def _annotate_votes(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    penalty_reason = _text_series(out, ["penalty_overlay_reason"])
    beta = _numeric_series(out, ["beta_60_20d", "beta_60"])
    gap = _numeric_series(out, ["gap_pct_20d", "gap_pct"])
    prob_edge = _numeric_series(out, ["prob_edge"])
    pred = _numeric_series(out, ["pred_return_20d"])

    out["tquant_vam_vote"] = (out["tquant_vam_pct_rank"] >= 0.70) & (out["tquant_vam_21d"] > 0)
    out["tquant_mrat_vote"] = (
        (out["tquant_mrat_pct_rank"] >= 0.70)
        & (out["tquant_mrat_21_200"] > 0)
        & out["tquant_price_structure_vote"].fillna(False).astype(bool)
    )
    out["tquant_baz_vote"] = (out["tquant_baz_pct_rank"] >= 0.65) & (out["tquant_baz"] > 0)
    out["tquant_expanded_momentum_vote"] = (
        (out["tquant_expanded_momentum_pct_rank"] >= 0.65)
        & (out["tquant_expanded_momentum"] > 0)
    )
    out["tquant_revenue_vote"] = out["tquant_revenue_vote"].fillna(False).astype(bool)
    out["tquant_institutional_vote"] = out["tquant_institutional_vote"].fillna(False).astype(bool)
    for column in [
        "tquant_kd_reversal_vote",
        "tquant_rsi_reversal_vote",
        "tquant_aroon_trend_vote",
        "tquant_financial_quality_vote",
        "tquant_financial_turnaround_vote",
        "tquant_value_vote",
        "tquant_kd_overheat_risk",
        "tquant_margin_deterioration_risk",
        "tquant_valuation_risk",
    ]:
        if column not in out:
            out[column] = False
        out[column] = out[column].fillna(False).astype(bool)
    out["tquant_ml_edge_vote"] = (pred > 0) & (prob_edge >= -0.10)

    settlement_phase = _text_series(out, ["futures_settlement_phase"])
    settlement_action = _text_series(out, ["futures_settlement_action"])
    settlement_window = _text_series(out, ["futures_settlement_window"])
    settlement_risk = (
        settlement_phase.isin(["pre_settlement_day", "settlement_day"])
        | settlement_action.str.contains("CAP|REDUCE|BLOCK", case=False, na=False)
        | settlement_window.str.contains("settlement", case=False, na=False)
    ) & ((beta > 1.25) | penalty_reason.str.contains("gap_risk", na=False))
    out["tquant_settlement_risk"] = settlement_risk.fillna(False).astype(bool)
    out["tquant_financing_crowding_risk"] = out["tquant_financing_crowding_risk"].fillna(False).astype(bool)
    out["tquant_high_beta_risk"] = (beta > 1.45).fillna(False)
    out["tquant_gap_risk"] = ((gap > 0.05) | penalty_reason.str.contains("gap_risk", na=False)).fillna(False)
    sector_col = "sector_20d" if "sector_20d" in out else "sector"
    sector_share = out.groupby(sector_col)["ticker"].transform("count") / max(len(out), 1) if sector_col in out else 0
    out["tquant_sector_crowding_risk"] = (
        pd.Series(sector_share, index=out.index).fillna(0) >= 0.35
    ) & (beta > 1.25).fillna(False)

    support_cols = [
        "tquant_vam_vote",
        "tquant_mrat_vote",
        "tquant_baz_vote",
        "tquant_expanded_momentum_vote",
        "tquant_kd_reversal_vote",
        "tquant_rsi_reversal_vote",
        "tquant_aroon_trend_vote",
        "tquant_revenue_vote",
        "tquant_financial_quality_vote",
        "tquant_financial_turnaround_vote",
        "tquant_value_vote",
        "tquant_institutional_vote",
        "tquant_ml_edge_vote",
    ]
    risk_cols = [
        "tquant_financing_crowding_risk",
        "tquant_settlement_risk",
        "tquant_high_beta_risk",
        "tquant_gap_risk",
        "tquant_kd_overheat_risk",
        "tquant_margin_deterioration_risk",
        "tquant_valuation_risk",
        "tquant_sector_crowding_risk",
    ]
    out["tquant_support_votes"] = out[support_cols].sum(axis=1).astype(int)
    out["tquant_risk_flags"] = out[risk_cols].sum(axis=1).astype(int)
    out["tquant_technical_votes"] = out[
        [
            "tquant_vam_vote",
            "tquant_mrat_vote",
            "tquant_baz_vote",
            "tquant_expanded_momentum_vote",
            "tquant_kd_reversal_vote",
            "tquant_rsi_reversal_vote",
            "tquant_aroon_trend_vote",
        ]
    ].sum(axis=1).astype(int)
    out["tquant_fundamental_votes"] = out[
        [
            "tquant_revenue_vote",
            "tquant_financial_quality_vote",
            "tquant_financial_turnaround_vote",
            "tquant_value_vote",
        ]
    ].sum(axis=1).astype(int)
    out["tquant_reference_score"] = out["tquant_support_votes"] - out["tquant_risk_flags"]
    out["tquant_consensus"] = np.select(
        [
            (out["tquant_support_votes"] >= 6) & (out["tquant_risk_flags"] <= 2),
            (out["tquant_support_votes"] >= 3) & (out["tquant_risk_flags"] <= 2),
        ],
        ["strong", "watch"],
        default="reject",
    )

    def _reasons(row: pd.Series) -> str:
        labels: list[str] = []
        for col, label in [
            ("tquant_vam_vote", "VAM"),
            ("tquant_mrat_vote", "MRAT"),
            ("tquant_baz_vote", "BAZ"),
            ("tquant_expanded_momentum_vote", "EXP_MOM"),
            ("tquant_kd_reversal_vote", "KD"),
            ("tquant_rsi_reversal_vote", "RSI"),
            ("tquant_aroon_trend_vote", "AROON"),
            ("tquant_revenue_vote", "REVENUE"),
            ("tquant_financial_quality_vote", "FIN_QUALITY"),
            ("tquant_financial_turnaround_vote", "FIN_TURN"),
            ("tquant_value_vote", "VALUE"),
            ("tquant_institutional_vote", "INST"),
            ("tquant_ml_edge_vote", "ML_EDGE"),
        ]:
            if bool(row.get(col, False)):
                labels.append(label)
        return ",".join(labels)

    def _risks(row: pd.Series) -> str:
        labels: list[str] = []
        for col, label in [
            ("tquant_financing_crowding_risk", "FINANCING_CROWDING"),
            ("tquant_settlement_risk", "SETTLEMENT"),
            ("tquant_high_beta_risk", "HIGH_BETA"),
            ("tquant_gap_risk", "GAP"),
            ("tquant_kd_overheat_risk", "KD_OVERHEAT"),
            ("tquant_margin_deterioration_risk", "MARGIN_DETERIORATION"),
            ("tquant_valuation_risk", "VALUATION"),
            ("tquant_sector_crowding_risk", "SECTOR_CROWDING"),
        ]:
            if bool(row.get(col, False)):
                labels.append(label)
        return ",".join(labels)

    out["tquant_support_reasons"] = out.apply(_reasons, axis=1)
    out["tquant_risk_reasons"] = out.apply(_risks, axis=1)
    return out


def build_tquant_frame(
    start_date: str,
    end_date: str,
    *,
    horizon_days: int = 5,
    db_path: str | Path = DEFAULT_DB_PATH,
    model_dir: str | Path = MODEL_DIR,
) -> pd.DataFrame:
    paths = _signal_paths_between(start_date, end_date, model_dir)
    frames = [_read_signal(path) for path in paths]
    frames = [frame for frame in frames if not frame.empty]
    if not frames:
        return pd.DataFrame()

    candidates = pd.concat(frames, ignore_index=True).copy()
    candidates["selected_by_ml"] = pd.to_numeric(
        candidates.get("target_units", 0), errors="coerce"
    ).fillna(0) > 0

    latest_price = _latest_price_date(db_path)
    lookback_start = (pd.Timestamp(start_date) - pd.Timedelta(days=430)).strftime("%Y-%m-%d")
    trading_dates = _trading_dates_between(lookback_start, latest_price, db_path=db_path)

    entry_dates: list[str | None] = []
    mark_dates: list[str | None] = []
    for signal_date in candidates["signal_date"]:
        entry = _next_trading_date(str(signal_date), trading_dates)
        mark = _horizon_mark_date(entry, trading_dates, horizon_days) if entry else None
        entry_dates.append(entry)
        mark_dates.append(mark)
    candidates["entry_date"] = entry_dates
    candidates["mark_date"] = mark_dates
    candidates = candidates[candidates["entry_date"].notna() & candidates["mark_date"].notna()].copy()
    if candidates.empty:
        return candidates

    tickers = sorted(candidates["ticker"].dropna().astype(str).unique())
    max_mark = str(candidates["mark_date"].max())
    prices = _load_price_history(tickers, lookback_start, max_mark, db_path=db_path)

    point_features: list[dict[str, Any]] = []
    for _, row in candidates.iterrows():
        ticker = str(row["ticker"])
        signal_date = str(row["signal_date"])
        features = _point_in_time_features(ticker, signal_date, prices)
        features.update(_latest_revenue_features(ticker, signal_date))
        point_features.append(features)
    feature_df = pd.DataFrame(point_features, index=candidates.index)
    out = pd.concat([candidates, feature_df], axis=1)

    price_key = prices[["ticker", "date", "Open", "Close"]].copy()
    entry_prices = price_key.rename(columns={"date": "entry_date", "Open": "entry_open"})[
        ["ticker", "entry_date", "entry_open"]
    ]
    mark_prices = price_key.rename(columns={"date": "mark_date", "Close": "mark_close"})[
        ["ticker", "mark_date", "mark_close"]
    ]
    out = out.merge(entry_prices, on=["ticker", "entry_date"], how="left")
    out = out.merge(mark_prices, on=["ticker", "mark_date"], how="left")
    out["return_to_mark"] = pd.to_numeric(out["mark_close"], errors="coerce") / pd.to_numeric(
        out["entry_open"], errors="coerce"
    ) - 1.0
    out["has_return"] = out["return_to_mark"].notna()

    out = _annotate_cross_section_ranks(out)
    out = _annotate_votes(out)
    return out


def _basket_metric(name: str, df: pd.DataFrame) -> BasketMetric:
    clean = df[pd.to_numeric(df.get("return_to_mark"), errors="coerce").notna()].copy()
    if clean.empty:
        return BasketMetric(name, 0, None, None, None, None, None, None)
    returns = pd.to_numeric(clean["return_to_mark"], errors="coerce")
    pred = _numeric_series(clean, ["pred_return_20d"])
    votes = _numeric_series(clean, ["tquant_support_votes"])
    risks = _numeric_series(clean, ["tquant_risk_flags"])
    return BasketMetric(
        basket=name,
        names=int(clean.shape[0]),
        avg_return=float(returns.mean()),
        median_return=float(returns.median()),
        win_rate=float((returns > 0).mean()),
        avg_pred_return_20d=float(pred.mean()) if pred.notna().any() else None,
        avg_support_votes=float(votes.mean()) if votes.notna().any() else None,
        avg_risk_flags=float(risks.mean()) if risks.notna().any() else None,
    )


def _mad_winsorize(series: pd.Series, limit: float = 5.0) -> pd.Series:
    values = pd.to_numeric(series, errors="coerce")
    median = values.median()
    mad = (values - median).abs().median()
    if pd.isna(median) or pd.isna(mad) or mad <= 1e-12:
        return values
    scaled = 1.4826 * mad
    return values.clip(median - limit * scaled, median + limit * scaled)


def _zscore(series: pd.Series) -> pd.Series:
    values = pd.to_numeric(series, errors="coerce")
    std = values.std(ddof=0)
    if pd.isna(std) or std <= 1e-12:
        return pd.Series(np.nan, index=series.index, dtype="float64")
    return (values - values.mean()) / std


def _validated_factor_values(df: pd.DataFrame, source_col: str) -> pd.Series:
    """FactorLibrary-style MAD winsorize, z-score, and neutralize by sector/size."""

    if source_col not in df:
        return pd.Series(np.nan, index=df.index, dtype="float64")

    amount = _numeric_series(df, ["avg_20d_amount_20d", "avg_20d_amount"], default=np.nan)
    sector_col = "sector_20d" if "sector_20d" in df else "sector"
    out = pd.Series(np.nan, index=df.index, dtype="float64")

    for _, group in df.groupby("signal_date"):
        values = _zscore(_mad_winsorize(group[source_col]))
        valid = values.notna()
        if valid.sum() < 4:
            out.loc[group.index] = values
            continue

        y = values.loc[valid].to_numpy(dtype="float64")
        design_parts = [pd.Series(1.0, index=values.loc[valid].index, name="intercept")]

        amount_part = amount.reindex(values.loc[valid].index)
        if amount_part.notna().sum() >= 4:
            log_amount = np.log(amount_part.clip(lower=1.0))
            design_parts.append(_zscore(log_amount).fillna(0).rename("log_amount"))

        if sector_col in group.columns:
            sector = group.loc[values.loc[valid].index, sector_col].fillna("UNKNOWN").astype(str)
            dummies = pd.get_dummies(sector, prefix="sector", drop_first=True, dtype=float)
            if 0 < dummies.shape[1] < max(valid.sum() - 2, 1):
                design_parts.append(dummies)

        design = pd.concat(design_parts, axis=1).astype(float)
        try:
            beta, *_ = np.linalg.lstsq(design.to_numpy(), y, rcond=None)
            residual = y - design.to_numpy().dot(beta)
            out.loc[values.loc[valid].index] = _zscore(pd.Series(residual, index=values.loc[valid].index))
        except np.linalg.LinAlgError:
            out.loc[group.index] = values

    return out


def _factor_metric(label: str, values: pd.Series, returns: pd.Series, dates: pd.Series) -> dict[str, Any]:
    frame = pd.DataFrame({"value": values, "return": returns, "signal_date": dates}).dropna()
    if frame.empty:
        return {
            "factor": label,
            "observations": 0,
            "days": 0,
            "rank_ic_mean": None,
            "rank_ic_ir": None,
            "top_quintile_avg_return": None,
            "bottom_quintile_avg_return": None,
            "top_bottom_spread": None,
        }

    ics: list[float] = []
    for _, group in frame.groupby("signal_date"):
        if group["value"].nunique() < 3 or group["return"].nunique() < 3:
            continue
        corr = group["value"].corr(group["return"], method="spearman")
        if not pd.isna(corr):
            ics.append(float(corr))

    pct_rank = frame["value"].rank(pct=True)
    top = frame[pct_rank >= 0.80]["return"]
    bottom = frame[pct_rank <= 0.20]["return"]
    rank_ic_mean = float(np.mean(ics)) if ics else None
    rank_ic_ir = (
        float(np.mean(ics) / np.std(ics, ddof=1))
        if len(ics) >= 2 and np.std(ics, ddof=1) > 1e-12
        else None
    )
    top_avg = float(top.mean()) if not top.empty else None
    bottom_avg = float(bottom.mean()) if not bottom.empty else None
    return {
        "factor": label,
        "observations": int(frame.shape[0]),
        "days": int(frame["signal_date"].nunique()),
        "rank_ic_mean": rank_ic_mean,
        "rank_ic_ir": rank_ic_ir,
        "top_quintile_avg_return": top_avg,
        "bottom_quintile_avg_return": bottom_avg,
        "top_bottom_spread": top_avg - bottom_avg if top_avg is not None and bottom_avg is not None else None,
    }


def compute_factor_validation(candidates: pd.DataFrame) -> dict[str, Any]:
    if candidates.empty or "return_to_mark" not in candidates:
        return {
            "method": "MAD5 winsorize -> z-score by date -> sector/log-amount neutralized -> Spearman RankIC",
            "status": "no_data",
            "metrics": [],
        }

    priced = candidates[pd.to_numeric(candidates["return_to_mark"], errors="coerce").notna()].copy()
    if priced.empty:
        return {
            "method": "MAD5 winsorize -> z-score by date -> sector/log-amount neutralized -> Spearman RankIC",
            "status": "no_priced_rows",
            "metrics": [],
        }

    factor_specs = [
        ("reference_score", "tquant_reference_score"),
        ("technical_votes", "tquant_technical_votes"),
        ("fundamental_votes", "tquant_fundamental_votes"),
        ("support_votes", "tquant_support_votes"),
        ("risk_flags_inverse", "tquant_risk_flags"),
        ("model_pred_return", "pred_return_20d"),
        ("model_prob_edge", "prob_edge"),
    ]
    returns = pd.to_numeric(priced["return_to_mark"], errors="coerce")
    metrics: list[dict[str, Any]] = []
    for label, column in factor_specs:
        if column not in priced:
            continue
        values = _validated_factor_values(priced, column)
        if label == "risk_flags_inverse":
            values = -values
        metrics.append(_factor_metric(label, values, returns, priced["signal_date"]))

    return {
        "method": "MAD5 winsorize -> z-score by date -> sector/log-amount neutralized -> Spearman RankIC",
        "status": "ok" if metrics else "no_factor_columns",
        "metrics": metrics,
    }


def compute_report(candidates: pd.DataFrame, *, horizon_days: int) -> dict[str, Any]:
    if candidates.empty:
        return {
            "status": "no_data",
            "horizon_days": horizon_days,
            "failures": ["no candidate rows"],
            "metrics": [],
            "factor_validation": {
                "method": "MAD5 winsorize -> z-score by date -> sector/log-amount neutralized -> Spearman RankIC",
                "status": "no_data",
                "metrics": [],
            },
        }

    selected = candidates[candidates["selected_by_ml"]]
    not_selected = candidates[~candidates["selected_by_ml"]]
    strong = candidates[candidates["tquant_consensus"] == "strong"]
    watch_or_strong = candidates[candidates["tquant_consensus"].isin(["strong", "watch"])]
    selected_strong = selected[selected["tquant_consensus"] == "strong"]
    selected_watch_or_strong = selected[selected["tquant_consensus"].isin(["strong", "watch"])]
    selected_reject = selected[selected["tquant_consensus"] == "reject"]
    tquant_strong_not_ml = not_selected[not_selected["tquant_consensus"] == "strong"]

    metrics = [
        _basket_metric("ml_selected", selected),
        _basket_metric("ml_not_selected", not_selected),
        _basket_metric("tquant_consensus_strong", strong),
        _basket_metric("tquant_consensus_watch_or_strong", watch_or_strong),
        _basket_metric("ml_selected_and_tquant_strong", selected_strong),
        _basket_metric("ml_selected_and_tquant_watch_or_strong", selected_watch_or_strong),
        _basket_metric("ml_selected_but_tquant_reject", selected_reject),
        _basket_metric("tquant_strong_not_ml_selected", tquant_strong_not_ml),
    ]
    metric_map = {metric.basket: metric for metric in metrics}
    ml_metric = metric_map["ml_selected"]
    guard_metric = metric_map["ml_selected_and_tquant_watch_or_strong"]
    production_metric = metric_map["ml_selected_and_tquant_strong"]
    strong_metric = metric_map["tquant_consensus_strong"]
    rejected_metric = metric_map["ml_selected_but_tquant_reject"]

    failures: list[str] = []
    warnings: list[str] = []
    guard_delta = (
        guard_metric.avg_return - ml_metric.avg_return
        if guard_metric.avg_return is not None and ml_metric.avg_return is not None
        else None
    )
    strong_delta = (
        strong_metric.avg_return - ml_metric.avg_return
        if strong_metric.avg_return is not None and ml_metric.avg_return is not None
        else None
    )
    reject_drag = (
        rejected_metric.avg_return - ml_metric.avg_return
        if rejected_metric.avg_return is not None and ml_metric.avg_return is not None
        else None
    )
    production_delta = (
        production_metric.avg_return - ml_metric.avg_return
        if production_metric.avg_return is not None and ml_metric.avg_return is not None
        else None
    )
    evidence: list[str] = []
    if production_metric.names <= 0:
        failures.append("production_strong_gate_zero_names")
    if production_delta is not None and production_delta <= 0:
        warnings.append("production_strong_gate_did_not_improve_ml_selected")
    if guard_delta is not None and guard_delta <= 0:
        evidence.append("loose_watch_guard_not_promoted")
    if strong_delta is not None and strong_delta <= 0:
        warnings.append("tquant_strong_did_not_beat_ml_selected")
    if rejected_metric.names > 0 and reject_drag is not None and reject_drag < 0:
        evidence.append("ml_selected_reject_basket_dragged_returns")
    elif rejected_metric.names > 0 and reject_drag is not None and reject_drag > 0:
        warnings.append("rejected_basket_outperformed_ml_selected")

    daily_rows: list[dict[str, Any]] = []
    for signal_date, group in candidates.groupby("signal_date"):
        selected_day = group[group["selected_by_ml"]]
        guard_day = selected_day[selected_day["tquant_consensus"].isin(["strong", "watch"])]
        daily_rows.append(
            {
                "signal_date": str(signal_date),
                "candidate_rows": int(group.shape[0]),
                **_basket_metric("ml_selected", selected_day).to_dict(),
                "guard_avg_return": _basket_metric("guard", guard_day).avg_return,
                "guard_names": _basket_metric("guard", guard_day).names,
            }
        )

    status = "pass" if not failures else "fail"
    if status == "pass" and warnings:
        status = "warn"

    return {
        "status": status,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "horizon_days": horizon_days,
        "start_date": str(candidates["signal_date"].min()),
        "end_date": str(candidates["signal_date"].max()),
        "candidate_rows": int(candidates.shape[0]),
        "priced_rows": int(candidates["has_return"].sum()),
        "guard_delta_vs_ml_selected": guard_delta,
        "production_strong_delta_vs_ml_selected": production_delta,
        "tquant_strong_delta_vs_ml_selected": strong_delta,
        "tquant_reject_delta_vs_ml_selected": reject_drag,
        "failures": failures,
        "warnings": warnings,
        "evidence": evidence,
        "metrics": [metric.to_dict() for metric in metrics],
        "factor_validation": compute_factor_validation(candidates),
        "daily": daily_rows,
        "recommendation": _recommendation(production_delta, strong_delta, rejected_metric.names, horizon_days),
    }


def _recommendation(
    production_delta: float | None,
    strong_delta: float | None,
    rejected_names: int,
    horizon_days: int,
) -> str:
    if production_delta is not None and production_delta > 0 and rejected_names > 0:
        return (
            "Keep the enhanced TEJ/TQuant strong consensus as the production 20D entry gate; "
            f"the {horizon_days}D replay supports the strong threshold, while watch consensus "
            "remains a looser manual override mode."
        )
    if strong_delta is not None and strong_delta > 0:
        return (
            "Use TQuant strong names as an offensive watchlist sleeve; current evidence is "
            "not yet enough for a production gate."
        )
    return "Keep as research-only reference layer until a positive replay window appears."


def _render_markdown(report: dict[str, Any], candidates: pd.DataFrame) -> str:
    lines: list[str] = []
    lines.append("# TQuant Reference Sleeves Backtest")
    lines.append("")
    lines.append(f"- Generated: `{report.get('generated_at', '-')}`")
    lines.append(f"- Signal window: `{report.get('start_date', '-')}` to `{report.get('end_date', '-')}`")
    lines.append(f"- Horizon: `{report.get('horizon_days', '-')}` trading days, entry next-day open to horizon close")
    lines.append(f"- Status: `{report.get('status', '-')}`")
    lines.append(f"- Candidate rows / priced rows: `{report.get('candidate_rows', 0)}` / `{report.get('priced_rows', 0)}`")
    lines.append("")
    lines.append("> Offline/read-only. No production gate, model-selection, scheduler, DB schema, or paper-book mutation.")
    lines.append("")
    lines.append("## Verdict")
    lines.append("")
    lines.append(f"- Production strong-gate delta vs ML selected: `{_fmt_pct(report.get('production_strong_delta_vs_ml_selected'))}`")
    lines.append(f"- Guard delta vs ML selected: `{_fmt_pct(report.get('guard_delta_vs_ml_selected'))}`")
    lines.append(f"- TQuant strong delta vs ML selected: `{_fmt_pct(report.get('tquant_strong_delta_vs_ml_selected'))}`")
    lines.append(f"- ML selected but TQuant reject delta: `{_fmt_pct(report.get('tquant_reject_delta_vs_ml_selected'))}`")
    lines.append(f"- Failures: `{', '.join(report.get('failures', [])) if report.get('failures') else 'none'}`")
    lines.append(f"- Warnings: `{', '.join(report.get('warnings', [])) if report.get('warnings') else 'none'}`")
    lines.append(f"- Evidence: `{', '.join(report.get('evidence', [])) if report.get('evidence') else 'none'}`")
    lines.append(f"- SA recommendation: {report.get('recommendation', '-')}")
    lines.append("")
    lines.append("## Basket Metrics")
    lines.append("")
    lines.append("| Basket | Names | Avg | Median | Win | Avg 20D Pred | Avg TQuant Votes | Avg Risk Flags |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|")
    for metric in report.get("metrics", []):
        lines.append(
            "| {basket} | {names} | {avg} | {median} | {win} | {pred} | {votes} | {risks} |".format(
                basket=metric["basket"],
                names=metric["names"],
                avg=_fmt_pct(metric["avg_return"]),
                median=_fmt_pct(metric["median_return"]),
                win=_fmt_pct(metric["win_rate"]),
                pred=_fmt_pct(metric["avg_pred_return_20d"]),
                votes=_fmt_float(metric["avg_tquant_support_votes"]),
                risks=_fmt_float(metric["avg_tquant_risk_flags"]),
            )
        )

    lines.append("")
    lines.append("## Sleeve Definitions")
    lines.append("")
    lines.append("- `VAM`: 21D return divided by 21D daily-return volatility, top 30% cross-section.")
    lines.append("- `MRAT`: MA21 / MA200 with close-to-high, higher-low, and range-compression checks.")
    lines.append("- `BAZ`: three-timescale EWM MACD response score from the TQuant LambdaMART example.")
    lines.append("- `EXP_MOM`: log-price slope times R-squared, inspired by Expanded Momentum Model.")
    lines.append("- `KD` / `RSI` / `AROON`: TEJ/TQuant technical confirmation votes.")
    lines.append("- `REVENUE`: point-in-time monthly revenue YoY and 3M YoY both positive.")
    lines.append("- `FIN_QUALITY` / `FIN_TURN` / `VALUE`: conservative point-in-time financial and valuation votes.")
    lines.append("- `INST`: recent institution net buying from local daily_k data.")
    lines.append("- Risk flags: financing crowding, settlement/high-beta risk, high beta, gap risk, KD overheat, margin deterioration, valuation, and sector crowding.")

    factor_validation = report.get("factor_validation") or {}
    if factor_validation:
        lines.append("")
        lines.append("## Factor Validation")
        lines.append("")
        lines.append(f"- Method: `{factor_validation.get('method', '-')}`")
        lines.append(f"- Status: `{factor_validation.get('status', '-')}`")
        lines.append("")
        lines.append("| Factor | Obs | Days | RankIC | IR | Top Q Avg | Bottom Q Avg | Spread |")
        lines.append("|---|---:|---:|---:|---:|---:|---:|---:|")
        for metric in factor_validation.get("metrics", []):
            lines.append(
                "| {factor} | {obs} | {days} | {ic} | {ir} | {top} | {bottom} | {spread} |".format(
                    factor=metric["factor"],
                    obs=metric["observations"],
                    days=metric["days"],
                    ic=_fmt_float(metric["rank_ic_mean"], 3),
                    ir=_fmt_float(metric["rank_ic_ir"], 3),
                    top=_fmt_pct(metric["top_quintile_avg_return"]),
                    bottom=_fmt_pct(metric["bottom_quintile_avg_return"]),
                    spread=_fmt_pct(metric["top_bottom_spread"]),
                )
            )

    if report.get("daily"):
        lines.append("")
        lines.append("## Daily Replay")
        lines.append("")
        lines.append("| Signal | Candidates | ML Names | ML Avg | ML Win | Guard Names | Guard Avg |")
        lines.append("|---|---:|---:|---:|---:|---:|---:|")
        for row in report["daily"]:
            lines.append(
                "| {signal} | {candidates} | {names} | {avg} | {win} | {guard_names} | {guard_avg} |".format(
                    signal=row["signal_date"],
                    candidates=row["candidate_rows"],
                    names=row["names"],
                    avg=_fmt_pct(row["avg_return"]),
                    win=_fmt_pct(row["win_rate"]),
                    guard_names=row["guard_names"],
                    guard_avg=_fmt_pct(row["guard_avg_return"]),
                )
            )

    priced = candidates[candidates["has_return"]].copy()
    if not priced.empty:
        cols = [
            "signal_date",
            "entry_date",
            "mark_date",
            "ticker",
            "selected_by_ml",
            "tquant_consensus",
            "tquant_support_votes",
            "tquant_risk_flags",
            "tquant_support_reasons",
            "tquant_risk_reasons",
            "return_to_mark",
            "pred_return_20d",
        ]
        available = [col for col in cols if col in priced.columns]
        for title, table in [
            ("Best TQuant Strong", priced[priced["tquant_consensus"] == "strong"].sort_values("return_to_mark", ascending=False).head(12)),
            ("Worst ML Selected", priced[priced["selected_by_ml"]].sort_values("return_to_mark").head(12)),
        ]:
            lines.append("")
            lines.append(f"## {title}")
            lines.append("")
            lines.append("| " + " | ".join(available) + " |")
            lines.append("|" + "|".join(["---"] * len(available)) + "|")
            for _, row in table.iterrows():
                values: list[str] = []
                for col in available:
                    value = row[col]
                    if col in {"return_to_mark", "pred_return_20d"}:
                        values.append(_fmt_pct(value))
                    else:
                        values.append("" if pd.isna(value) else str(value))
                lines.append("| " + " | ".join(values) + " |")

    return "\n".join(lines) + "\n"


def write_outputs(
    report: dict[str, Any],
    candidates: pd.DataFrame,
    *,
    output_dir: str | Path = REPORT_DIR,
    output_prefix: str = DEFAULT_OUTPUT_PREFIX,
) -> dict[str, str]:
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    md_path = output_path / f"{output_prefix}.md"
    json_path = output_path / f"{output_prefix}.json"
    detail_path = output_path / f"{output_prefix}_candidates.csv"
    md_path.write_text(_render_markdown(report, candidates), encoding="utf-8")
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    candidates.to_csv(detail_path, index=False, encoding="utf-8-sig")
    return {"md": str(md_path), "json": str(json_path), "candidates": str(detail_path)}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run TQuant-inspired reference sleeve replay.")
    parser.add_argument("--start-date", required=True, help="First unified signal date, YYYY-MM-DD.")
    parser.add_argument("--end-date", required=True, help="Last unified signal date, YYYY-MM-DD.")
    parser.add_argument("--horizon-days", type=int, default=5, help="Trading-day mark horizon from entry day.")
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--model-dir", default=str(MODEL_DIR))
    parser.add_argument("--output-dir", default=str(REPORT_DIR))
    parser.add_argument("--output-prefix", default=DEFAULT_OUTPUT_PREFIX)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.horizon_days < 1:
        raise ValueError("--horizon-days must be >= 1")
    candidates = build_tquant_frame(
        args.start_date,
        args.end_date,
        horizon_days=args.horizon_days,
        db_path=args.db_path,
        model_dir=args.model_dir,
    )
    report = compute_report(candidates, horizon_days=args.horizon_days)
    outputs = write_outputs(report, candidates, output_dir=args.output_dir, output_prefix=args.output_prefix)
    print(f"TQuant reference sleeve status: {report['status']}")
    print(f"Recommendation: {report.get('recommendation', '-')}")
    for label, path in outputs.items():
        print(f"{label}: {path}")


if __name__ == "__main__":
    main()
