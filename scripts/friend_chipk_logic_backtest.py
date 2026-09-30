"""Backtest a shared ChipK-style short-wave entry rule.

The rule is a deterministic translation of a discretionary workflow:

1. Foreign flow turns from selling to buying.
2. Institutional flow is no longer hostile.
3. MACD histogram improves.
4. Price is near support rather than extended.
5. Volume is not a chase spike.

Signals are generated after the signal-day close. Confirmation variants enter
at the next trading day's open; the pullback variant places a next-day support
limit order. Exits are fixed holding-day closes. The script is research-only
and does not mutate production gates, model selection, schedulers, or
portfolios.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "ml" / "reports"

DEFAULT_START_DATE = "2024-01-01"
DEFAULT_HOLD_DAYS = (5, 10, 20, 60)
DEFAULT_MAX_SIGNALS_PER_DAY = 10
FRICTION = 0.004
PULLBACK_LIMIT_MA60_MULTIPLIER = 1.005
PULLBACK_LIMIT_CLOSE_DISCOUNT = 0.98
REQUIRED_COLUMNS = {
    "Date",
    "Open",
    "High",
    "Low",
    "Close",
    "Volume",
    "Foreign_BuySell",
    "Trust_BuySell",
    "Dealer_BuySell",
    "MA_5",
    "MA_20",
    "MA_60",
    "MACDh_12_26_9",
    "VOL_MA_20",
}


@dataclass(frozen=True)
class BacktestConfig:
    start_date: str = DEFAULT_START_DATE
    end_date: str | None = None
    max_signals_per_day: int = DEFAULT_MAX_SIGNALS_PER_DAY
    friction: float = FRICTION
    hold_days: tuple[int, ...] = DEFAULT_HOLD_DAYS


def _resolve_daily_dir(raw: str | None = None) -> Path:
    if raw:
        path = Path(raw)
        if not path.is_absolute():
            path = ROOT / path
        return path

    best: tuple[int, Path] | None = None
    for candidate in ROOT.iterdir():
        if not candidate.is_dir():
            continue
        csv_files = list(candidate.glob("*.csv"))
        if len(csv_files) < 500:
            continue
        probe = candidate / "3013.csv"
        if not probe.exists():
            continue
        try:
            columns = set(pd.read_csv(probe, nrows=0).columns)
        except Exception:
            continue
        if REQUIRED_COLUMNS <= columns:
            score = len(csv_files)
            if best is None or score > best[0]:
                best = (score, candidate)
    if best is None:
        raise FileNotFoundError("Could not locate daily stock CSV directory")
    return best[1]


def _to_numeric(df: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    out = df.copy()
    for column in columns:
        if column in out.columns:
            out[column] = pd.to_numeric(out[column], errors="coerce")
    return out


def prepare_stock_frame(path: Path) -> pd.DataFrame:
    ticker = path.stem.zfill(4)
    df = pd.read_csv(path)
    missing = REQUIRED_COLUMNS - set(df.columns)
    if missing:
        return pd.DataFrame()
    df = _to_numeric(
        df,
        [
            "Open",
            "High",
            "Low",
            "Close",
            "Volume",
            "Foreign_BuySell",
            "Trust_BuySell",
            "Dealer_BuySell",
            "MA_5",
            "MA_20",
            "MA_60",
            "MACDh_12_26_9",
            "VOL_MA_20",
        ],
    )
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    df = df.dropna(subset=["Date", "Open", "Close"]).sort_values("Date").reset_index(drop=True)
    if df.empty:
        return df

    foreign = df["Foreign_BuySell"].fillna(0.0)
    dealer = df["Dealer_BuySell"].fillna(0.0)
    trust = df["Trust_BuySell"].fillna(0.0)
    inst_total = foreign + trust + dealer
    close = df["Close"]
    volume = df["Volume"]
    vol_ma20 = df["VOL_MA_20"].replace(0, np.nan)
    macd_hist = df["MACDh_12_26_9"]

    df["ticker"] = ticker
    df["inst_total"] = inst_total
    df["return_1d"] = close / close.shift(1) - 1.0
    df["foreign_prev3_all_sell"] = foreign.shift(1).lt(0).rolling(3).sum().eq(3)
    df["foreign_prev5_all_sell"] = foreign.shift(1).lt(0).rolling(5).sum().eq(5)
    df["foreign_prev5_sum"] = foreign.shift(1).rolling(5).sum()
    df["inst_prev5_sum"] = inst_total.shift(1).rolling(5).sum()
    df["foreign_turn_buy"] = foreign.gt(0) & df["foreign_prev5_sum"].lt(0)
    df["inst_turn_buy"] = inst_total.gt(0) & df["inst_prev5_sum"].lt(0)
    df["foreign_buy_ratio"] = foreign / volume.replace(0, np.nan)
    df["inst_buy_ratio"] = inst_total / volume.replace(0, np.nan)
    df["macd_improving"] = macd_hist.gt(macd_hist.shift(1))
    df["macd_improving_2d"] = macd_hist.gt(macd_hist.shift(1)) & macd_hist.shift(1).gt(macd_hist.shift(2))
    df["macd_hist_delta_3d"] = macd_hist - macd_hist.shift(3)
    df["above_ma5"] = close.ge(df["MA_5"])
    df["near_ma60_support"] = close.ge(df["MA_60"] * 0.98) & close.le(df["MA_60"] * 1.08)
    df["not_above_ma20_chase"] = close.le(df["MA_20"] * 1.03)
    df["volume_not_spike"] = volume.le(vol_ma20 * 1.5)
    df["volume_alive"] = volume.ge(vol_ma20 * 0.25)
    df["volume_loose_ok"] = volume.le(vol_ma20 * 2.0)
    df["not_price_chase"] = df["return_1d"].le(0.04) & close.le(df["MA_5"] * 1.08)
    df["liquid"] = close.ge(10.0) & volume.ge(250_000)

    next_open = df["Open"].shift(-1)
    df["entry_date"] = df["Date"].shift(-1)
    df["entry_open"] = next_open
    next_low = df["Low"].shift(-1)
    next_high = df["High"].shift(-1)
    df["pullback_limit_price"] = np.minimum(
        df["MA_60"] * PULLBACK_LIMIT_MA60_MULTIPLIER,
        df["Close"] * PULLBACK_LIMIT_CLOSE_DISCOUNT,
    )
    limit_price = df["pullback_limit_price"]
    pullback_fill = next_open.le(limit_price) | (next_low.le(limit_price) & next_high.ge(limit_price))
    df["pullback_filled"] = pullback_fill.fillna(False)
    df["pullback_entry_price"] = np.where(next_open.le(limit_price), next_open, limit_price)
    df.loc[~df["pullback_filled"], "pullback_entry_price"] = np.nan
    for hold in DEFAULT_HOLD_DAYS:
        exit_close = df["Close"].shift(-hold)
        exit_date = df["Date"].shift(-hold)
        df[f"exit_date_{hold}d"] = exit_date
        df[f"exit_close_{hold}d"] = exit_close
        df[f"gross_return_{hold}d"] = exit_close / next_open.replace(0, np.nan) - 1.0
        df[f"pullback_gross_return_{hold}d"] = exit_close / pd.Series(df["pullback_entry_price"]).replace(0, np.nan) - 1.0
    return df


def annotate_friend_rules(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["friend_base"] = (
        out["liquid"]
        & out["foreign_turn_buy"]
        & out["inst_total"].gt(0)
        & out["macd_improving"]
        & out["near_ma60_support"]
        & out["not_above_ma20_chase"]
        & out["volume_not_spike"]
        & out["volume_alive"]
        & out["not_price_chase"]
    )
    out["friend_strict"] = (
        out["liquid"]
        & out["foreign_prev5_all_sell"]
        & out["Foreign_BuySell"].gt(0)
        & out["inst_total"].gt(0)
        & out["Dealer_BuySell"].fillna(0).ge(0)
        & out["macd_improving_2d"]
        & out["above_ma5"]
        & out["near_ma60_support"]
        & out["not_above_ma20_chase"]
        & out["Volume"].le(out["VOL_MA_20"].replace(0, np.nan) * 1.2)
        & out["return_1d"].le(0.025)
    )
    out["friend_loose"] = (
        out["liquid"]
        & (out["foreign_turn_buy"] | out["inst_turn_buy"])
        & out["macd_improving"]
        & out["Close"].ge(out["MA_60"] * 0.95)
        & out["Close"].le(out["MA_20"] * 1.08)
        & out["volume_loose_ok"]
        & out["return_1d"].le(0.06)
    )
    out["friend_pullback_setup"] = (
        out["liquid"]
        & out["foreign_prev5_sum"].lt(0)
        & out["Close"].ge(out["MA_60"] * 0.98)
        & out["Close"].le(out["MA_60"] * 1.08)
        & out["not_above_ma20_chase"]
        & out["volume_not_spike"]
        & out["volume_alive"]
        & out["return_1d"].le(0.04)
        & out["MACDh_12_26_9"].ge(-2.0)
    )
    out["friend_pullback_limit"] = out["friend_pullback_setup"] & out["pullback_filled"].fillna(False)

    support_distance = (out["Close"] / out["MA_60"].replace(0, np.nan) - 1.0).abs()
    out["friend_logic_score"] = (
        out["foreign_buy_ratio"].clip(-0.2, 0.2).fillna(0.0) * 2.0
        + out["inst_buy_ratio"].clip(-0.2, 0.2).fillna(0.0)
        + out["macd_hist_delta_3d"].clip(-5, 5).fillna(0.0) * 0.03
        - support_distance.clip(0, 0.25).fillna(0.25)
        - out["return_1d"].clip(lower=0).fillna(0.0)
    )
    return out


def _load_universe(daily_dir: Path, config: BacktestConfig) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for path in sorted(daily_dir.glob("*.csv")):
        frame = prepare_stock_frame(path)
        if frame.empty:
            continue
        frames.append(annotate_friend_rules(frame))
    if not frames:
        raise RuntimeError(f"No usable daily frames under {daily_dir}")
    data = pd.concat(frames, ignore_index=True)
    start = pd.Timestamp(config.start_date)
    data = data[data["Date"].ge(start)].copy()
    if config.end_date:
        data = data[data["Date"].le(pd.Timestamp(config.end_date))].copy()
    return data.sort_values(["Date", "ticker"]).reset_index(drop=True)


def _select_signals(data: pd.DataFrame, rule: str, max_per_day: int) -> pd.DataFrame:
    signals = data[data[rule].fillna(False)].copy()
    if rule == "friend_pullback_limit" and not signals.empty:
        signals["entry_open"] = signals["pullback_entry_price"]
        for hold in DEFAULT_HOLD_DAYS:
            signals[f"gross_return_{hold}d"] = signals[f"pullback_gross_return_{hold}d"]
    signals = signals.dropna(subset=["entry_open"])
    if max_per_day <= 0 or signals.empty:
        return signals.sort_values(["Date", "friend_logic_score", "ticker"], ascending=[True, False, True])
    selected = (
        signals.sort_values(["Date", "friend_logic_score", "ticker"], ascending=[True, False, True])
        .groupby("Date", observed=True)
        .head(max_per_day)
        .reset_index(drop=True)
    )
    return selected


def _trade_summary(signals: pd.DataFrame, hold: int, friction: float) -> dict[str, Any]:
    ret_col = f"gross_return_{hold}d"
    trades = signals.dropna(subset=[ret_col]).copy()
    if trades.empty:
        return {
            "trades": 0,
            "signal_days": 0,
            "avg_net_return": None,
            "median_net_return": None,
            "win_rate": None,
            "basket_cum_return": None,
            "basket_max_drawdown": None,
        }
    trades["net_return"] = trades[ret_col] - friction
    daily = trades.groupby("Date", observed=True)["net_return"].mean().sort_index()
    equity = (1.0 + daily).cumprod()
    drawdown = equity / equity.cummax() - 1.0
    return {
        "trades": int(len(trades)),
        "signal_days": int(daily.shape[0]),
        "avg_net_return": float(trades["net_return"].mean()),
        "median_net_return": float(trades["net_return"].median()),
        "win_rate": float(trades["net_return"].gt(0).mean()),
        "basket_cum_return": float(equity.iloc[-1] - 1.0),
        "basket_max_drawdown": float(drawdown.min()),
    }


def _summarize_by_year(signals: pd.DataFrame, hold: int, friction: float) -> list[dict[str, Any]]:
    ret_col = f"gross_return_{hold}d"
    trades = signals.dropna(subset=[ret_col]).copy()
    if trades.empty:
        return []
    trades["year"] = trades["Date"].dt.year.astype(str)
    trades["net_return"] = trades[ret_col] - friction
    rows = []
    for year, group in trades.groupby("year", observed=True):
        rows.append(
            {
                "year": year,
                "trades": int(len(group)),
                "avg_net_return": float(group["net_return"].mean()),
                "win_rate": float(group["net_return"].gt(0).mean()),
            }
        )
    return rows


def _extreme_trades(
    signals: pd.DataFrame,
    *,
    hold: int,
    friction: float,
    top_n: int = 10,
) -> dict[str, list[dict[str, Any]]]:
    ret_col = f"gross_return_{hold}d"
    exit_date_col = f"exit_date_{hold}d"
    exit_close_col = f"exit_close_{hold}d"
    trades = signals.dropna(subset=[ret_col, "entry_open"]).copy()
    if trades.empty:
        return {"top": [], "bottom": []}

    trades["net_return"] = trades[ret_col] - friction
    ordered = trades.sort_values(["net_return", "Date", "ticker"], ascending=[False, True, True])

    def payload(row: pd.Series) -> dict[str, Any]:
        return {
            "ticker": str(row.get("ticker", "")).zfill(4),
            "signal_date": pd.Timestamp(row["Date"]).date().isoformat(),
            "entry_date": pd.Timestamp(row["entry_date"]).date().isoformat()
            if pd.notna(row.get("entry_date"))
            else None,
            "entry_price": float(row["entry_open"]) if pd.notna(row.get("entry_open")) else None,
            "signal_close": float(row["Close"]) if pd.notna(row.get("Close")) else None,
            "exit_date": pd.Timestamp(row[exit_date_col]).date().isoformat()
            if exit_date_col in row and pd.notna(row.get(exit_date_col))
            else None,
            "exit_close": float(row[exit_close_col])
            if exit_close_col in row and pd.notna(row.get(exit_close_col))
            else None,
            "net_return": float(row["net_return"]),
            "gross_return": float(row[ret_col]),
            "foreign": float(row["Foreign_BuySell"]) if pd.notna(row.get("Foreign_BuySell")) else None,
            "dealer": float(row["Dealer_BuySell"]) if pd.notna(row.get("Dealer_BuySell")) else None,
            "inst_total": float(row["inst_total"]) if pd.notna(row.get("inst_total")) else None,
            "macd_hist": float(row["MACDh_12_26_9"]) if pd.notna(row.get("MACDh_12_26_9")) else None,
            "score": float(row["friend_logic_score"]) if pd.notna(row.get("friend_logic_score")) else None,
        }

    top_rows = [payload(row) for _, row in ordered.head(top_n).iterrows()]
    bottom_ordered = ordered.tail(top_n).sort_values(["net_return", "Date", "ticker"])
    bottom_rows = [payload(row) for _, row in bottom_ordered.iterrows()]
    return {"top": top_rows, "bottom": bottom_rows}


def _format_pct(value: float | None) -> str:
    if value is None or not np.isfinite(value):
        return "n/a"
    return f"{value * 100.0:+.2f}%"


def _current_diagnostic(data: pd.DataFrame, ticker: str = "3013") -> list[dict[str, Any]]:
    sub = data[data["ticker"].eq(ticker)].sort_values("Date").tail(3).copy()
    rows: list[dict[str, Any]] = []
    for _, row in sub.iterrows():
        rows.append(
            {
                "date": row["Date"].date().isoformat(),
                "close": float(row["Close"]),
                "foreign": float(row["Foreign_BuySell"]),
                "dealer": float(row["Dealer_BuySell"]),
                "inst_total": float(row["inst_total"]),
                "macd_hist": float(row["MACDh_12_26_9"]),
                "volume_ratio_20d": float(row["Volume"] / row["VOL_MA_20"]) if row["VOL_MA_20"] else None,
                "base": bool(row["friend_base"]),
                "strict": bool(row["friend_strict"]),
                "loose": bool(row["friend_loose"]),
                "pullback_setup": bool(row["friend_pullback_setup"]),
                "pullback_entry": bool(row["friend_pullback_limit"]),
                "pullback_limit_price": float(row["pullback_limit_price"]) if pd.notna(row["pullback_limit_price"]) else None,
                "pullback_entry_price": float(row["pullback_entry_price"]) if pd.notna(row["pullback_entry_price"]) else None,
            }
        )
    return rows


def run_backtest(
    daily_dir: Path,
    config: BacktestConfig,
    *,
    asof: str | None = None,
) -> dict[str, Any]:
    data = _load_universe(daily_dir, config)
    asof_token = asof or datetime.now().strftime("%Y%m%d_%H%M%S")
    rules = ["friend_base", "friend_strict", "friend_loose", "friend_pullback_limit"]
    summaries: dict[str, Any] = {}
    extremes_60d: dict[str, Any] = {}
    trade_frames: list[pd.DataFrame] = []
    for rule in rules:
        selected = _select_signals(data, rule, config.max_signals_per_day)
        raw_signal_col = "friend_pullback_setup" if rule == "friend_pullback_limit" else rule
        summaries[rule] = {
            "raw_signals": int(data[raw_signal_col].fillna(False).sum()),
            "filled_signals": int(data[rule].fillna(False).sum()) if rule == "friend_pullback_limit" else None,
            "selected_signals": int(len(selected)),
            "hold_days": {
                str(hold): _trade_summary(selected, hold, config.friction)
                for hold in config.hold_days
            },
            "yearly_10d": _summarize_by_year(selected, 10, config.friction),
            "yearly_60d": _summarize_by_year(selected, 60, config.friction),
        }
        extremes_60d[rule] = _extreme_trades(selected, hold=60, friction=config.friction, top_n=10)
        if not selected.empty:
            export_cols = [
                "ticker",
                "Date",
                "entry_date",
                "entry_open",
                "pullback_limit_price",
                "Close",
                "Foreign_BuySell",
                "Dealer_BuySell",
                "inst_total",
                "MACDh_12_26_9",
                "friend_logic_score",
            ]
            export_cols.extend(f"gross_return_{hold}d" for hold in config.hold_days)
            frame = selected[[col for col in export_cols if col in selected.columns]].copy()
            frame.insert(0, "rule", rule)
            trade_frames.append(frame)

    trades = pd.concat(trade_frames, ignore_index=True) if trade_frames else pd.DataFrame()
    paths = write_outputs(
        asof_token=asof_token,
        daily_dir=daily_dir,
        config=config,
        summary=summaries,
        extremes_60d=extremes_60d,
        current_3013=_current_diagnostic(data, "3013"),
        trades=trades,
    )
    return {
        "asof": asof_token,
        "daily_dir": str(daily_dir),
        "config": config.__dict__,
        "summaries": summaries,
        "extremes_60d": extremes_60d,
        "current_3013": _current_diagnostic(data, "3013"),
        "paths": paths,
    }


def write_outputs(
    *,
    asof_token: str,
    daily_dir: Path,
    config: BacktestConfig,
    summary: dict[str, Any],
    extremes_60d: dict[str, Any],
    current_3013: list[dict[str, Any]],
    trades: pd.DataFrame,
) -> dict[str, str]:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    json_path = REPORT_DIR / f"friend_chipk_logic_backtest_{asof_token}.json"
    md_path = REPORT_DIR / f"friend_chipk_logic_backtest_{asof_token}.md"
    trades_path = REPORT_DIR / f"friend_chipk_logic_backtest_{asof_token}_trades.csv"
    latest_json = REPORT_DIR / "friend_chipk_logic_backtest_latest.json"
    latest_md = REPORT_DIR / "friend_chipk_logic_backtest_latest.md"
    latest_trades = REPORT_DIR / "friend_chipk_logic_backtest_latest_trades.csv"

    payload = {
        "asof": asof_token,
        "daily_dir": str(daily_dir),
        "config": {
            "start_date": config.start_date,
            "end_date": config.end_date,
            "max_signals_per_day": config.max_signals_per_day,
            "friction": config.friction,
            "hold_days": list(config.hold_days),
        },
        "rule_definitions": {
            "friend_base": "foreign prev-5 net sell then buy, total inst > 0, MACD hist improves, near MA60 support, not above MA20 chase, volume 0.25x-1.5x 20D average, no >4% chase day",
            "friend_strict": "base-like but requires prior 5 straight foreign sell days, dealer non-negative, 2-day MACD hist improvement, above MA5, volume <=1.2x 20D, daily return <=2.5%",
            "friend_loose": "foreign or total institutional turn-buy, MACD hist improves, price 0.95x MA60 to 1.08x MA20, volume <=2x 20D, daily return <=6%",
            "friend_pullback_limit": "prior-day support setup with previous foreign selling, price near MA60, non-spike volume, no chase, MACD hist >= -2.0; next day limit buy at min(MA60*1.005, close*0.98) only if touched",
        },
        "summary": summary,
        "extremes_60d": extremes_60d,
        "current_3013": current_3013,
    }
    json_text = json.dumps(payload, ensure_ascii=False, indent=2)
    json_path.write_text(json_text, encoding="utf-8")
    latest_json.write_text(json_text, encoding="utf-8")
    if not trades.empty:
        trades.to_csv(trades_path, index=False, encoding="utf-8-sig")
        trades.to_csv(latest_trades, index=False, encoding="utf-8-sig")

    lines = [
        "# Friend ChipK-Style Logic Backtest",
        "",
        f"- Generated: `{asof_token}`",
        f"- Daily data dir: `{daily_dir}`",
        f"- Signal window: `{config.start_date}` to `{config.end_date or 'latest'}`",
        "- Execution: confirmation rules enter next open; pullback rule places a next-day support-limit order; exits use fixed holding-day closes",
        f"- Friction: `{config.friction:.2%}` per round trip",
        f"- Max selected per signal day: `{config.max_signals_per_day}`",
        "",
        "## Rule Translation",
        "",
        "- `friend_base`: foreign turns from previous 5-day net selling to buy; total institutions net buy; MACD histogram improves; price is near MA60 support and not extended above MA20; volume is alive but not a spike.",
        "- `friend_strict`: requires 5 straight prior foreign sell days, dealer not selling, 2-day MACD improvement, above MA5, and tighter volume/price chase limits.",
        "- `friend_loose`: allows either foreign or total institutional turn-buy, with MACD improvement and looser support/chase filters.",
        "- `friend_pullback_limit`: models the 108.5-style entry. A setup is identified after the prior close, then a next-day limit buy is placed near MA60 support. It only counts if the next day's low/open touches the limit.",
        "",
        "## Summary",
        "",
        "| Rule | Raw Signals | Filled | Selected | Hold | Avg Net | Median Net | Win Rate | Basket Cum | Max DD |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for rule, info in summary.items():
        for hold, stats in info["hold_days"].items():
            lines.append(
                "| "
                + " | ".join(
                    [
                        rule,
                        str(info["raw_signals"]),
                        "-" if info.get("filled_signals") is None else str(info["filled_signals"]),
                        str(info["selected_signals"]),
                        f"{hold}D",
                        _format_pct(stats["avg_net_return"]),
                        _format_pct(stats["median_net_return"]),
                        _format_pct(stats["win_rate"]),
                        _format_pct(stats["basket_cum_return"]),
                        _format_pct(stats["basket_max_drawdown"]),
                    ]
                )
                + " |"
            )
    lines.extend(
        [
            "",
            "## Yearly Checks",
            "",
            "### 10D",
            "",
            "| Rule | Year | Trades | Avg Net | Win Rate |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for rule, info in summary.items():
        for row in info["yearly_10d"]:
            lines.append(
                f"| {rule} | {row['year']} | {row['trades']} | "
                f"{_format_pct(row['avg_net_return'])} | {_format_pct(row['win_rate'])} |"
            )

    lines.extend(
        [
            "",
            "### 60D",
            "",
            "| Rule | Year | Trades | Avg Net | Win Rate |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for rule, info in summary.items():
        for row in info["yearly_60d"]:
            lines.append(
                f"| {rule} | {row['year']} | {row['trades']} | "
                f"{_format_pct(row['avg_net_return'])} | {_format_pct(row['win_rate'])} |"
            )

    lines.extend(["", "## 60D Extreme Trades", ""])
    for rule, sides in extremes_60d.items():
        lines.extend(
            [
                f"### {rule} Top 10",
                "",
                "| Ticker | Signal | Entry | Entry Px | Exit | Exit Px | Net 60D | Foreign | Dealer | Inst Total | MACD Hist |",
                "|---|---|---|---:|---|---:|---:|---:|---:|---:|---:|",
            ]
        )
        for row in sides["top"]:
            lines.append(
                f"| {row['ticker']} | {row['signal_date']} | {row['entry_date']} | "
                f"{row['entry_price']:.2f} | {row['exit_date']} | {row['exit_close']:.2f} | "
                f"{_format_pct(row['net_return'])} | {row['foreign']:.0f} | {row['dealer']:.0f} | "
                f"{row['inst_total']:.0f} | {row['macd_hist']:.3f} |"
            )
        lines.extend(
            [
                "",
                f"### {rule} Bottom 10",
                "",
                "| Ticker | Signal | Entry | Entry Px | Exit | Exit Px | Net 60D | Foreign | Dealer | Inst Total | MACD Hist |",
                "|---|---|---|---:|---|---:|---:|---:|---:|---:|---:|",
            ]
        )
        for row in sides["bottom"]:
            lines.append(
                f"| {row['ticker']} | {row['signal_date']} | {row['entry_date']} | "
                f"{row['entry_price']:.2f} | {row['exit_date']} | {row['exit_close']:.2f} | "
                f"{_format_pct(row['net_return'])} | {row['foreign']:.0f} | {row['dealer']:.0f} | "
                f"{row['inst_total']:.0f} | {row['macd_hist']:.3f} |"
            )
        lines.append("")

    lines.extend(
        [
            "",
            "## 3013 Current Diagnostic",
            "",
            "| Date | Close | Foreign | Dealer | Inst Total | MACD Hist | Vol/20D | Base | Strict | Loose | Pullback Setup | Pullback Entry | Limit | Fill |",
            "|---|---:|---:|---:|---:|---:|---:|---|---|---|---|---|---:|---:|",
        ]
    )
    for row in current_3013:
        lines.append(
            f"| {row['date']} | {row['close']:.2f} | {row['foreign']:.0f} | "
            f"{row['dealer']:.0f} | {row['inst_total']:.0f} | {row['macd_hist']:.3f} | "
            f"{row['volume_ratio_20d']:.2f} | {row['base']} | {row['strict']} | {row['loose']} | "
            f"{row['pullback_setup']} | {row['pullback_entry']} | "
            f"{row['pullback_limit_price'] or 0:.2f} | {row['pullback_entry_price'] or 0:.2f} |"
        )
    lines.extend(
        [
            "",
            "## Interpretation Guardrails",
            "",
            "- This is an offline research backtest; it does not authorize a production gate change.",
            "- The local historical data does not include historical ChipK broker-branch or main-force app rows, so those parts are approximated by foreign/dealer/trust flow and price/volume behavior.",
            "- Results use next-open execution to avoid same-day lookahead.",
            "- 60D holding windows overlap heavily; `Basket Cum` is not capital-constrained and should be treated as a directionality stress metric, not deployable portfolio CAGR. Use average return, median return, win rate, and extreme-trade review for strategy judgment.",
        ]
    )
    md_text = "\n".join(lines) + "\n"
    md_path.write_text(md_text, encoding="utf-8")
    latest_md.write_text(md_text, encoding="utf-8")
    return {
        "json": str(json_path),
        "md": str(md_path),
        "trades_csv": str(trades_path) if not trades.empty else "",
        "latest_json": str(latest_json),
        "latest_md": str(latest_md),
        "latest_trades_csv": str(latest_trades) if not trades.empty else "",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Backtest a friend-shared ChipK-style short-wave logic.")
    parser.add_argument("--daily-dir", default=None, help="Daily stock CSV directory. Auto-detected by default.")
    parser.add_argument("--start-date", default=DEFAULT_START_DATE)
    parser.add_argument("--end-date", default=None)
    parser.add_argument("--max-signals-per-day", type=int, default=DEFAULT_MAX_SIGNALS_PER_DAY)
    parser.add_argument("--asof", default=None, help="Artifact timestamp token.")
    args = parser.parse_args()

    config = BacktestConfig(
        start_date=args.start_date,
        end_date=args.end_date,
        max_signals_per_day=args.max_signals_per_day,
    )
    daily_dir = _resolve_daily_dir(args.daily_dir)
    result = run_backtest(daily_dir, config, asof=args.asof)
    print(json.dumps(result["paths"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
