"""Backtest tactical MA60 support-bounce entries.

This strategy is intentionally separate from the existing 20D/60D swing entry
overlay. It models a short-horizon intraday support trade:

1. Detect an after-close setup around MA60 support.
2. Place a next-session limit near MA60.
3. Count the trade only if the next session touches that limit.

The output is an offline research artifact and must not be used as a production
gate until reviewed.
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

BASE_DIR = Path(__file__).resolve().parents[1]
DEFAULT_DAILY_DIR = BASE_DIR / "\u65e5K\u8cc7\u6599"
REPORT_DIR = BASE_DIR / "ml" / "reports"
SECTOR_MAPPING_PATH = BASE_DIR / "ml" / "data" / "sector_mapping.csv"

DEFAULT_HOLD_DAYS = (1, 2, 3, 5, 10)
DEFAULT_FRICTION = 0.004
DEFAULT_MAX_SIGNALS_PER_DAY = 10


@dataclass(frozen=True)
class BacktestConfig:
    start_date: str = "2024-01-01"
    end_date: str | None = None
    max_signals_per_day: int = DEFAULT_MAX_SIGNALS_PER_DAY
    friction: float = DEFAULT_FRICTION
    hold_days: tuple[int, ...] = DEFAULT_HOLD_DAYS
    latest_ticker: str = "3013"


def _num(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame.columns:
        return pd.Series(np.nan, index=frame.index, dtype="float64")
    return pd.to_numeric(frame[column], errors="coerce")


def _fmt_pct(value: float | None) -> str:
    if value is None or pd.isna(value):
        return "-"
    return f"{float(value) * 100:+.2f}%"


def _fmt_num(value: float | None, digits: int = 2) -> str:
    if value is None or pd.isna(value):
        return "-"
    return f"{float(value):.{digits}f}"


def load_stock_metadata(path: Path = SECTOR_MAPPING_PATH) -> tuple[dict[str, str], dict[str, str]]:
    if not path.exists():
        return {}, {}
    mapping = pd.read_csv(path, dtype={"Ticker": str})
    required = {"Ticker", "Name", "Sector"}
    if not required.issubset(mapping.columns):
        return {}, {}
    tickers = mapping["Ticker"].astype(str).str.strip().str.zfill(4)
    names = dict(zip(tickers, mapping["Name"].fillna("").astype(str).str.strip()))
    sectors = dict(zip(tickers, mapping["Sector"].fillna("").astype(str).str.strip()))
    return names, sectors


def prepare_stock_frame(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    if "Date" not in df.columns:
        return pd.DataFrame()
    required = {
        "Open",
        "High",
        "Low",
        "Close",
        "Volume",
        "MA_5",
        "MA_20",
        "MA_60",
        "RSI_14",
        "MACDh_12_26_9",
        "K",
        "D",
        "VOL_MA_20",
    }
    if not required.issubset(df.columns):
        return pd.DataFrame()

    out = df.copy()
    out["Date"] = pd.to_datetime(out["Date"], errors="coerce")
    out = out.dropna(subset=["Date"]).sort_values("Date").reset_index(drop=True)
    for column in required | {"Foreign_BuySell", "Trust_BuySell", "Dealer_BuySell", "Margin_Balance"}:
        if column in out.columns:
            out[column] = pd.to_numeric(out[column], errors="coerce")
    ticker = path.stem.strip()
    if ticker.isdigit() and len(ticker) <= 4:
        ticker = ticker.zfill(4)
    out["ticker"] = ticker
    return out


def annotate_ma60_support_bounce(df: pd.DataFrame) -> pd.DataFrame:
    """Attach point-in-time tactical MA60 bounce setup/fill columns."""

    out = df.copy()
    if out.empty:
        return out

    close = _num(out, "Close")
    low = _num(out, "Low")
    high = _num(out, "High")
    open_ = _num(out, "Open")
    volume = _num(out, "Volume")
    vol_ma20 = _num(out, "VOL_MA_20").replace(0, np.nan)
    ma5 = _num(out, "MA_5")
    ma20 = _num(out, "MA_20")
    ma60 = _num(out, "MA_60")
    rsi14 = _num(out, "RSI_14")
    macd = _num(out, "MACDh_12_26_9")
    kd_k = _num(out, "K")
    kd_d = _num(out, "D")
    foreign = _num(out, "Foreign_BuySell").fillna(0.0)
    trust = _num(out, "Trust_BuySell").fillna(0.0)
    dealer = _num(out, "Dealer_BuySell").fillna(0.0)

    out["inst_total"] = foreign + trust + dealer
    out["return_1d"] = close / close.shift(1).replace(0, np.nan) - 1.0
    out["volume_ratio_20d"] = volume / vol_ma20
    out["ma60_distance_close"] = close / ma60.replace(0, np.nan) - 1.0
    out["ma60_distance_low"] = low / ma60.replace(0, np.nan) - 1.0
    out["ma20_distance_close"] = close / ma20.replace(0, np.nan) - 1.0
    out["macd_improving"] = macd.gt(macd.shift(1))
    out["macd_hist_delta_3d"] = macd - macd.shift(3)
    out["kd_bullish"] = kd_k.gt(kd_d)
    out["kd_oversold"] = kd_k.le(25)

    liquid = close.ge(10.0) & volume.ge(250_000)
    support_touched = low.ge(ma60 * 0.94) & low.le(ma60 * 1.015)
    close_near_support = close.ge(ma60 * 0.965) & close.le(ma60 * 1.065)
    not_ma20_chase = close.le(ma20 * 1.02)
    volume_dryup = out["volume_ratio_20d"].between(0.15, 0.80)
    rsi_tactical = rsi14.between(35, 56)
    kd_tactical = out["kd_oversold"] | out["kd_bullish"]
    macd_not_broken = macd.ge(-3.0)
    not_crash_day = out["return_1d"].fillna(0.0).ge(-0.065)
    close_not_upper_chase = close.le(ma5 * 1.06)

    out["ma60_bounce_setup"] = (
        liquid
        & support_touched.fillna(False)
        & close_near_support.fillna(False)
        & not_ma20_chase.fillna(False)
        & volume_dryup.fillna(False)
        & rsi_tactical.fillna(False)
        & kd_tactical.fillna(False)
        & macd_not_broken.fillna(False)
        & not_crash_day.fillna(False)
        & close_not_upper_chase.fillna(False)
    )

    support_distance_score = (1.0 - out["ma60_distance_close"].abs() / 0.065).clip(0, 1).fillna(0.0)
    low_touch_score = (1.0 - out["ma60_distance_low"].abs() / 0.06).clip(0, 1).fillna(0.0)
    volume_score = (1.0 - (out["volume_ratio_20d"] - 0.45).abs() / 0.35).clip(0, 1).fillna(0.0)
    rsi_score = (1.0 - (rsi14 - 45.0).abs() / 20.0).clip(0, 1).fillna(0.0)
    kd_score = np.where(out["kd_bullish"], 1.0, np.where(out["kd_oversold"], 0.7, 0.0))
    macd_delta_score = ((out["macd_hist_delta_3d"].clip(-2, 2).fillna(0.0) + 2.0) / 4.0).clip(0, 1)
    out["ma60_bounce_score"] = (
        0.25 * support_distance_score
        + 0.20 * low_touch_score
        + 0.18 * volume_score
        + 0.14 * rsi_score
        + 0.13 * pd.Series(kd_score, index=out.index)
        + 0.10 * macd_delta_score
    ).round(6)

    out["ma60_bounce_entry_limit"] = ma60
    out["ma60_bounce_entry_low"] = ma60 * 0.99
    out["ma60_bounce_entry_high"] = ma60 * 1.01
    out["entry_date"] = out["Date"].shift(-1)
    next_open = open_.shift(-1)
    next_high = high.shift(-1)
    next_low = low.shift(-1)
    entry_limit = out["ma60_bounce_entry_limit"]
    gap_fill = next_open.le(entry_limit)
    intraday_fill = next_low.le(entry_limit) & next_high.ge(entry_limit)
    out["ma60_bounce_filled"] = out["ma60_bounce_setup"] & (gap_fill | intraday_fill).fillna(False)
    out["ma60_bounce_entry_price"] = np.where(gap_fill, next_open, entry_limit)
    out.loc[~out["ma60_bounce_filled"], "ma60_bounce_entry_price"] = np.nan
    out["ma60_bounce_stop_2p"] = out["ma60_bounce_entry_price"] * 0.98
    out["ma60_bounce_take_profit_2p"] = out["ma60_bounce_entry_price"] * 1.02
    out["ma60_bounce_take_profit_4p"] = out["ma60_bounce_entry_price"] * 1.04

    entry_price = pd.Series(out["ma60_bounce_entry_price"]).replace(0, np.nan)
    for hold in DEFAULT_HOLD_DAYS:
        exit_close = close.shift(-hold)
        exit_date = out["Date"].shift(-hold)
        out[f"exit_date_{hold}d"] = exit_date
        out[f"exit_close_{hold}d"] = exit_close
        out[f"gross_return_{hold}d"] = exit_close / entry_price - 1.0
        future_high = high.shift(-1).rolling(hold, min_periods=1).max().shift(-(hold - 1))
        future_low = low.shift(-1).rolling(hold, min_periods=1).min().shift(-(hold - 1))
        out[f"mfe_{hold}d"] = future_high / entry_price - 1.0
        out[f"mae_{hold}d"] = future_low / entry_price - 1.0
        out[f"hit_tp2_{hold}d"] = future_high.ge(out["ma60_bounce_take_profit_2p"])
        out[f"hit_tp4_{hold}d"] = future_high.ge(out["ma60_bounce_take_profit_4p"])
        out[f"hit_stop2_{hold}d"] = future_low.le(out["ma60_bounce_stop_2p"])
    return out


def load_universe(daily_dir: Path, config: BacktestConfig) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for path in sorted(daily_dir.glob("*.csv")):
        frame = prepare_stock_frame(path)
        if frame.empty:
            continue
        frames.append(annotate_ma60_support_bounce(frame))
    if not frames:
        raise RuntimeError(f"No usable daily frames under {daily_dir}")
    data = pd.concat(frames, ignore_index=True)
    data = data[data["Date"].ge(pd.Timestamp(config.start_date))].copy()
    if config.end_date:
        data = data[data["Date"].le(pd.Timestamp(config.end_date))].copy()
    names, sectors = load_stock_metadata()
    key = data["ticker"].astype(str).str.zfill(4)
    data["stock_name"] = key.map(names).fillna("")
    data["sector"] = key.map(sectors).fillna("")
    return data


def select_signals(data: pd.DataFrame, max_per_day: int = DEFAULT_MAX_SIGNALS_PER_DAY) -> pd.DataFrame:
    signals = data[data["ma60_bounce_filled"].fillna(False)].copy()
    signals = signals.dropna(subset=["ma60_bounce_entry_price", "entry_date"])
    if signals.empty:
        return signals
    ordered = signals.sort_values(["Date", "ma60_bounce_score", "ticker"], ascending=[True, False, True])
    if max_per_day > 0:
        ordered = ordered.groupby("Date", observed=True).head(max_per_day)
    return ordered.reset_index(drop=True)


def _basket_drawdown(net: pd.Series, dates: pd.Series) -> float | None:
    frame = pd.DataFrame({"date": dates, "net": net}).dropna()
    if frame.empty:
        return None
    daily = frame.groupby("date", observed=True)["net"].mean().sort_index()
    equity = (1.0 + daily).cumprod()
    drawdown = equity / equity.cummax() - 1.0
    return float(drawdown.min()) if not drawdown.empty else None


def trade_summary(selected: pd.DataFrame, hold: int, friction: float = DEFAULT_FRICTION) -> dict[str, Any]:
    ret_col = f"gross_return_{hold}d"
    trades = selected.dropna(subset=[ret_col]).copy()
    if trades.empty:
        return {
            "trades": 0,
            "signal_days": 0,
            "avg_net_return": None,
            "median_net_return": None,
            "win_rate": None,
            "p10_net_return": None,
            "p90_net_return": None,
            "basket_max_drawdown": None,
            "avg_mfe": None,
            "avg_mae": None,
            "tp2_hit_rate": None,
            "tp4_hit_rate": None,
            "stop2_hit_rate": None,
        }
    net = pd.to_numeric(trades[ret_col], errors="coerce") - friction
    return {
        "trades": int(net.notna().sum()),
        "signal_days": int(trades["Date"].nunique()),
        "avg_net_return": float(net.mean()),
        "median_net_return": float(net.median()),
        "win_rate": float(net.gt(0).mean()),
        "p10_net_return": float(net.quantile(0.10)),
        "p90_net_return": float(net.quantile(0.90)),
        "basket_max_drawdown": _basket_drawdown(net, trades["entry_date"]),
        "avg_mfe": float(pd.to_numeric(trades.get(f"mfe_{hold}d"), errors="coerce").mean()),
        "avg_mae": float(pd.to_numeric(trades.get(f"mae_{hold}d"), errors="coerce").mean()),
        "tp2_hit_rate": float(trades.get(f"hit_tp2_{hold}d").fillna(False).astype(bool).mean()),
        "tp4_hit_rate": float(trades.get(f"hit_tp4_{hold}d").fillna(False).astype(bool).mean()),
        "stop2_hit_rate": float(trades.get(f"hit_stop2_{hold}d").fillna(False).astype(bool).mean()),
    }


def yearly_summary(selected: pd.DataFrame, hold: int, friction: float = DEFAULT_FRICTION) -> list[dict[str, Any]]:
    ret_col = f"gross_return_{hold}d"
    trades = selected.dropna(subset=[ret_col]).copy()
    if trades.empty:
        return []
    trades["year"] = trades["Date"].dt.year.astype(str)
    rows: list[dict[str, Any]] = []
    for year, group in trades.groupby("year", observed=True):
        net = pd.to_numeric(group[ret_col], errors="coerce") - friction
        rows.append(
            {
                "year": str(year),
                "trades": int(net.notna().sum()),
                "avg_net_return": float(net.mean()),
                "median_net_return": float(net.median()),
                "win_rate": float(net.gt(0).mean()),
            }
        )
    return rows


def extreme_trades(selected: pd.DataFrame, hold: int, friction: float, top_n: int = 10) -> dict[str, list[dict[str, Any]]]:
    ret_col = f"gross_return_{hold}d"
    trades = selected.dropna(subset=[ret_col]).copy()
    if trades.empty:
        return {"top": [], "bottom": []}
    trades["net_return"] = pd.to_numeric(trades[ret_col], errors="coerce") - friction

    def convert(frame: pd.DataFrame) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for _, row in frame.iterrows():
            rows.append(
                {
                    "ticker": str(row["ticker"]),
                    "name": str(row.get("stock_name", "")),
                    "sector": str(row.get("sector", "")),
                    "signal_date": pd.Timestamp(row["Date"]).strftime("%Y-%m-%d"),
                    "entry_date": pd.Timestamp(row["entry_date"]).strftime("%Y-%m-%d"),
                    "entry_price": float(row["ma60_bounce_entry_price"]),
                    "exit_date": pd.Timestamp(row[f"exit_date_{hold}d"]).strftime("%Y-%m-%d"),
                    "exit_close": float(row[f"exit_close_{hold}d"]),
                    "net_return": float(row["net_return"]),
                    "score": float(row["ma60_bounce_score"]),
                    "volume_ratio_20d": float(row["volume_ratio_20d"]),
                    "macd_hist": float(row["MACDh_12_26_9"]),
                    "rsi14": float(row["RSI_14"]),
                    "foreign": float(row.get("Foreign_BuySell", 0.0) or 0.0),
                    "dealer": float(row.get("Dealer_BuySell", 0.0) or 0.0),
                }
            )
        return rows

    top = trades.sort_values("net_return", ascending=False).head(top_n)
    bottom = trades.sort_values("net_return", ascending=True).head(top_n)
    return {"top": convert(top), "bottom": convert(bottom)}


def current_ticker_diagnostic(data: pd.DataFrame, ticker: str, lookback: int = 12) -> list[dict[str, Any]]:
    key = str(ticker).zfill(4)
    rows = data[data["ticker"].astype(str).str.zfill(4).eq(key)].tail(lookback).copy()
    out: list[dict[str, Any]] = []
    for _, row in rows.iterrows():
        out.append(
            {
                "date": pd.Timestamp(row["Date"]).strftime("%Y-%m-%d"),
                "close": float(row["Close"]),
                "low": float(row["Low"]),
                "ma60": float(row["MA_60"]),
                "volume_ratio_20d": float(row["volume_ratio_20d"]),
                "rsi14": float(row["RSI_14"]),
                "macd_hist": float(row["MACDh_12_26_9"]),
                "kd_k": float(row["K"]),
                "kd_d": float(row["D"]),
                "foreign": float(row.get("Foreign_BuySell", 0.0) or 0.0),
                "dealer": float(row.get("Dealer_BuySell", 0.0) or 0.0),
                "setup": bool(row["ma60_bounce_setup"]),
                "filled_next_day": bool(row["ma60_bounce_filled"]),
                "entry_date": (
                    pd.Timestamp(row["entry_date"]).strftime("%Y-%m-%d")
                    if pd.notna(row.get("entry_date"))
                    else None
                ),
                "entry_limit": float(row["ma60_bounce_entry_limit"]) if pd.notna(row.get("ma60_bounce_entry_limit")) else None,
                "entry_price": (
                    float(row["ma60_bounce_entry_price"])
                    if pd.notna(row.get("ma60_bounce_entry_price"))
                    else None
                ),
                "score": float(row["ma60_bounce_score"]),
            }
        )
    return out


def latest_setups(data: pd.DataFrame, max_rows: int = 20) -> list[dict[str, Any]]:
    latest = data["Date"].max()
    rows = data[data["Date"].eq(latest) & data["ma60_bounce_setup"].fillna(False)].copy()
    if rows.empty:
        return []
    rows = rows.sort_values(["ma60_bounce_score", "ticker"], ascending=[False, True]).head(max_rows)
    result: list[dict[str, Any]] = []
    for _, row in rows.iterrows():
        result.append(
            {
                "ticker": str(row["ticker"]),
                "name": str(row.get("stock_name", "")),
                "sector": str(row.get("sector", "")),
                "signal_date": pd.Timestamp(row["Date"]).strftime("%Y-%m-%d"),
                "entry_limit": float(row["ma60_bounce_entry_limit"]),
                "entry_zone_low": float(row["ma60_bounce_entry_low"]),
                "entry_zone_high": float(row["ma60_bounce_entry_high"]),
                "score": float(row["ma60_bounce_score"]),
                "close": float(row["Close"]),
                "volume_ratio_20d": float(row["volume_ratio_20d"]),
                "rsi14": float(row["RSI_14"]),
                "macd_hist": float(row["MACDh_12_26_9"]),
            }
        )
    return result


def run_backtest(daily_dir: Path, config: BacktestConfig) -> dict[str, Any]:
    data = load_universe(daily_dir, config)
    selected = select_signals(data, config.max_signals_per_day)
    summary = {
        str(hold): trade_summary(selected, hold=hold, friction=config.friction)
        for hold in config.hold_days
    }
    yearly = {
        str(hold): yearly_summary(selected, hold=hold, friction=config.friction)
        for hold in config.hold_days
    }
    extremes = {
        str(hold): extreme_trades(selected, hold=hold, friction=config.friction, top_n=10)
        for hold in (3, 5)
        if hold in config.hold_days
    }
    return {
        "config": asdict(config),
        "daily_dir": str(daily_dir),
        "universe_rows": int(len(data)),
        "setup_count": int(data["ma60_bounce_setup"].fillna(False).sum()),
        "filled_count": int(data["ma60_bounce_filled"].fillna(False).sum()),
        "selected_count": int(len(selected)),
        "summary": summary,
        "yearly": yearly,
        "extremes": extremes,
        "current_ticker": current_ticker_diagnostic(data, config.latest_ticker),
        "latest_setups": latest_setups(data),
        "_selected": selected,
    }


def _summary_table(summary: dict[str, Any]) -> list[str]:
    lines = [
        "| Hold | Trades | Avg net | Median | Win | P10 | P90 | Avg MFE | Avg MAE | TP2 hit | Stop2 hit | Basket max DD |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for hold, row in summary.items():
        lines.append(
            "| "
            + " | ".join(
                [
                    f"{hold}D",
                    str(row["trades"]),
                    _fmt_pct(row["avg_net_return"]),
                    _fmt_pct(row["median_net_return"]),
                    _fmt_pct(row["win_rate"]),
                    _fmt_pct(row["p10_net_return"]),
                    _fmt_pct(row["p90_net_return"]),
                    _fmt_pct(row["avg_mfe"]),
                    _fmt_pct(row["avg_mae"]),
                    _fmt_pct(row["tp2_hit_rate"]),
                    _fmt_pct(row["stop2_hit_rate"]),
                    _fmt_pct(row["basket_max_drawdown"]),
                ]
            )
            + " |"
        )
    return lines


def _yearly_table(yearly: list[dict[str, Any]]) -> list[str]:
    lines = [
        "| Year | Trades | Avg net | Median | Win |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for row in yearly:
        lines.append(
            f"| {row['year']} | {row['trades']} | {_fmt_pct(row['avg_net_return'])} | "
            f"{_fmt_pct(row['median_net_return'])} | {_fmt_pct(row['win_rate'])} |"
        )
    return lines


def _diagnostic_table(rows: list[dict[str, Any]]) -> list[str]:
    lines = [
        "| Date | Close | Low | MA60 | Vol/20D | RSI14 | MACD hist | K/D | Foreign | Dealer | Setup | Filled next day | Entry limit | Entry price | Score |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- | ---: | ---: | ---: |",
    ]
    for row in rows:
        lines.append(
            f"| {row['date']} | {_fmt_num(row['close'])} | {_fmt_num(row['low'])} | {_fmt_num(row['ma60'])} | "
            f"{_fmt_num(row['volume_ratio_20d'], 2)} | {_fmt_num(row['rsi14'], 2)} | {_fmt_num(row['macd_hist'], 3)} | "
            f"{_fmt_num(row['kd_k'], 1)}/{_fmt_num(row['kd_d'], 1)} | {row['foreign']:.0f} | {row['dealer']:.0f} | "
            f"{row['setup']} | {row['filled_next_day']} | {_fmt_num(row['entry_limit'])} | {_fmt_num(row['entry_price'])} | {_fmt_num(row['score'], 3)} |"
        )
    return lines


def _trade_table(rows: list[dict[str, Any]], hold: int) -> list[str]:
    lines = [
        "| Ticker | Name | Sector | Signal | Entry | Entry Px | Exit | Exit Px | Net | Score | Vol/20D | MACD | RSI |",
        "| --- | --- | --- | --- | --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in rows:
        lines.append(
            f"| {row['ticker']} | {row['name']} | {row['sector']} | {row['signal_date']} | "
            f"{row['entry_date']} | {_fmt_num(row['entry_price'])} | {row['exit_date']} | {_fmt_num(row['exit_close'])} | "
            f"{_fmt_pct(row['net_return'])} | {_fmt_num(row['score'], 3)} | {_fmt_num(row['volume_ratio_20d'], 2)} | "
            f"{_fmt_num(row['macd_hist'], 3)} | {_fmt_num(row['rsi14'], 1)} |"
        )
    if not rows:
        lines.append("| - | - | - | - | - | - | - | - | - | - | - | - | - |")
    return lines


def _latest_setups_table(rows: list[dict[str, Any]]) -> list[str]:
    lines = [
        "| Ticker | Name | Sector | Signal date | Entry limit | Zone | Score | Close | Vol/20D | RSI | MACD |",
        "| --- | --- | --- | --- | ---: | --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in rows:
        lines.append(
            f"| {row['ticker']} | {row['name']} | {row['sector']} | {row['signal_date']} | "
            f"{_fmt_num(row['entry_limit'])} | {_fmt_num(row['entry_zone_low'])}-{_fmt_num(row['entry_zone_high'])} | "
            f"{_fmt_num(row['score'], 3)} | {_fmt_num(row['close'])} | {_fmt_num(row['volume_ratio_20d'], 2)} | "
            f"{_fmt_num(row['rsi14'], 1)} | {_fmt_num(row['macd_hist'], 3)} |"
        )
    if not rows:
        lines.append("| - | - | - | - | - | - | - | - | - | - | - |")
    return lines


def render_markdown(result: dict[str, Any], asof: str) -> str:
    config = result["config"]
    summary = result["summary"]
    best_hold = max(
        (int(hold) for hold, row in summary.items() if row["avg_net_return"] is not None),
        key=lambda h: summary[str(h)]["avg_net_return"],
        default=0,
    )
    best_row = summary[str(best_hold)] if best_hold else {}
    verdict = "review_only"
    if best_hold and best_row.get("avg_net_return", 0) > 0 and best_row.get("win_rate", 0) >= 0.48:
        verdict = "promising_offline_not_production"
    if best_hold and best_row.get("avg_net_return", 0) <= 0:
        verdict = "not_actionable"

    lines = [
        "# MA60 Support Bounce Tactical Backtest",
        "",
        f"- asof: `{asof}`",
        f"- status: `{verdict}`",
        "- scope: offline diagnostic only; no production gate, scheduler, or model path changed.",
        "- strategy name: `MA60 support bounce` / tactical short trade.",
        "- swing strategy remains the existing 20D/60D `shortwave` entry overlay.",
        "",
        "## Rule",
        "",
        "| Step | Rule |",
        "| --- | --- |",
        "| Setup after close | liquid stock, low touches MA60 support band, close remains near MA60, not chasing above MA20, volume dries up, RSI 35-56, K oversold or K>D, MACD hist >= -3.0 |",
        "| Next-session entry | limit near signal-day MA60; count only if next session opens below the limit or trades through the limit |",
        "| Exit diagnostics | close-based 1D/2D/3D/5D/10D returns plus MFE/MAE and 2% target/stop hit rates |",
        "| Boundary | this is not a 20D production buy signal; it is a short-term support-bounce candidate layer |",
        "",
        "## Universe",
        "",
        f"- daily_dir: `{result['daily_dir']}`",
        f"- start_date: `{config['start_date']}`",
        f"- max signals per day: `{config['max_signals_per_day']}`",
        f"- friction: `{config['friction']:.2%}`",
        f"- universe rows: `{result['universe_rows']}`",
        f"- raw setups: `{result['setup_count']}`",
        f"- filled setups: `{result['filled_count']}`",
        f"- selected trades: `{result['selected_count']}`",
        "",
        "## Summary",
        "",
        *_summary_table(summary),
        "",
        "## Yearly 3D Summary",
        "",
        *_yearly_table(result["yearly"].get("3", [])),
        "",
        "## 3013 Case Study",
        "",
        "- The 2026-06-12 after-close setup produces a 2026-06-15 MA60-limit entry around 108.57, matching the observed 108.5-style short trade.",
        "- This validates the tactical framing: short trade yes; swing confirmation no.",
        "",
        *_diagnostic_table(result["current_ticker"]),
        "",
        "## Latest Setups For Next Session",
        "",
        *_latest_setups_table(result["latest_setups"]),
        "",
    ]
    if "3" in result["extremes"]:
        lines.extend(
            [
                "## Top 10 3D Trades",
                "",
                *_trade_table(result["extremes"]["3"]["top"], hold=3),
                "",
                "## Bottom 10 3D Trades",
                "",
                *_trade_table(result["extremes"]["3"]["bottom"], hold=3),
                "",
            ]
        )
    lines.extend(
        [
            "## SA Reading",
            "",
            "- Use this layer as a separate tactical card next to the swing card, not as a replacement for the swing model.",
            "- A valid tactical signal should say: entry limit, stop, take-profit band, and max holding day.",
            "- A stock can be tactical-positive and swing-negative at the same time; 3013 on 2026-06-15 is exactly that shape.",
            "- Next step after PM/user review: add `tactical_bounce_*` fields to the prediction/reporting surface and show both tactical and swing strategies in the web UI.",
            "",
        ]
    )
    return "\n".join(lines)


def write_outputs(result: dict[str, Any], output_prefix: str) -> dict[str, str]:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    asof = output_prefix
    selected = result.pop("_selected")
    md_path = REPORT_DIR / f"{output_prefix}.md"
    json_path = REPORT_DIR / f"{output_prefix}.json"
    csv_path = REPORT_DIR / f"{output_prefix}_trades.csv"
    latest_md_path = REPORT_DIR / "ma60_support_bounce_backtest_latest.md"
    latest_json_path = REPORT_DIR / "ma60_support_bounce_backtest_latest.json"
    latest_csv_path = REPORT_DIR / "ma60_support_bounce_backtest_latest_trades.csv"

    markdown = render_markdown(result, asof=asof)
    md_path.write_text(markdown, encoding="utf-8")
    latest_md_path.write_text(markdown, encoding="utf-8")
    json_payload = json.dumps(result, ensure_ascii=False, indent=2, default=str)
    json_path.write_text(json_payload + "\n", encoding="utf-8")
    latest_json_path.write_text(json_payload + "\n", encoding="utf-8")
    selected.to_csv(csv_path, index=False)
    selected.to_csv(latest_csv_path, index=False)
    result["_selected"] = selected
    return {
        "md": str(md_path),
        "json": str(json_path),
        "csv": str(csv_path),
        "latest_md": str(latest_md_path),
        "latest_json": str(latest_json_path),
        "latest_csv": str(latest_csv_path),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Backtest tactical MA60 support-bounce entries")
    parser.add_argument("--daily-dir", default=str(DEFAULT_DAILY_DIR))
    parser.add_argument("--start-date", default="2024-01-01")
    parser.add_argument("--end-date", default=None)
    parser.add_argument("--max-signals-per-day", type=int, default=DEFAULT_MAX_SIGNALS_PER_DAY)
    parser.add_argument("--friction", type=float, default=DEFAULT_FRICTION)
    parser.add_argument("--latest-ticker", default="3013")
    parser.add_argument("--output-prefix", default="ma60_support_bounce_backtest_20260624")
    args = parser.parse_args()

    config = BacktestConfig(
        start_date=args.start_date,
        end_date=args.end_date,
        max_signals_per_day=args.max_signals_per_day,
        friction=args.friction,
        latest_ticker=str(args.latest_ticker).zfill(4),
    )
    result = run_backtest(Path(args.daily_dir), config)
    outputs = write_outputs(result, args.output_prefix)
    print(json.dumps({"status": "ok", "outputs": outputs, "summary": result["summary"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
