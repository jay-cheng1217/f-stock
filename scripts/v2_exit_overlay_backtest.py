"""Research-only exit overlay replay for production V2 Top-N signals.

This script does not change the production backtest or paper portfolio. It
reuses saved predictions_YYYY-MM-DD.csv files as the signal source, rebuilds
the same sector-capped Top-N list, and tests alternative exit policies:

    entry   = Open[t+1]
    timeout = Close[t+N]
    optional TP / SL checked with daily High / Low

Because daily bars do not tell whether High or Low happened first, the default
same-day ambiguity policy is conservative: stop-loss first.
"""
from __future__ import annotations

import argparse
import glob
import json
import math
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))

from ml.config import MODEL_DIR, REPORT_DIR
from ml.predict import apply_sector_cap

DAILY_K_DIR = BASE_DIR / "日K資料"
PREDICTION_RE = re.compile(r"^predictions_(\d{4}-\d{2}-\d{2})\.csv$")

DEFAULT_OUTPUT_PREFIX = "v2_exit_overlay_latest"
DEFAULT_FRICTION = 0.004
DEFAULT_TOP_N = 30
DEFAULT_MAX_HOLD_DAYS = 20


@dataclass(frozen=True)
class OverlayConfig:
    name: str
    max_hold_days: int
    take_profit: float | None = None
    stop_loss: float | None = None
    atr_stop_mult: float | None = None
    atr_take_mult: float | None = None


def default_overlays(max_hold_days: int) -> list[OverlayConfig]:
    return [
        OverlayConfig("fixed20", max_hold_days=max_hold_days),
        OverlayConfig("time10", max_hold_days=10),
        OverlayConfig("time15", max_hold_days=15),
        OverlayConfig("tp10_no_sl", max_hold_days=max_hold_days, take_profit=0.10),
        OverlayConfig("tp15_no_sl", max_hold_days=max_hold_days, take_profit=0.15),
        OverlayConfig("tp20_no_sl", max_hold_days=max_hold_days, take_profit=0.20),
        OverlayConfig("sl07_tp14", max_hold_days=max_hold_days, stop_loss=0.07, take_profit=0.14),
        OverlayConfig("sl10_tp20", max_hold_days=max_hold_days, stop_loss=0.10, take_profit=0.20),
        OverlayConfig("atr2_tp4", max_hold_days=max_hold_days, atr_stop_mult=2.0, atr_take_mult=4.0),
        OverlayConfig("atr3_tp6", max_hold_days=max_hold_days, atr_stop_mult=3.0, atr_take_mult=6.0),
    ]


def _round(value: Any, digits: int = 6) -> float | None:
    try:
        if pd.isna(value):
            return None
        num = float(value)
    except Exception:
        return None
    if not np.isfinite(num):
        return None
    return round(num, digits)


def _fmt_pct(value: Any, digits: int = 1) -> str:
    num = _round(value, digits + 2)
    if num is None:
        return "-"
    return f"{num * 100:+.{digits}f}%"


def _fmt_num(value: Any, digits: int = 3) -> str:
    num = _round(value, digits)
    if num is None:
        return "-"
    return f"{num:.{digits}f}"


def _prediction_date_from_path(path: str | Path) -> str:
    match = PREDICTION_RE.match(Path(path).name)
    if not match:
        raise ValueError(f"Cannot infer prediction date from {path}")
    return match.group(1)


def list_prediction_files(start_date: str | None, end_date: str | None) -> list[Path]:
    files: list[Path] = []
    for raw in sorted(glob.glob(os.path.join(MODEL_DIR, "predictions_*.csv"))):
        path = Path(raw)
        if not PREDICTION_RE.match(path.name):
            continue
        date = _prediction_date_from_path(path)
        if start_date and date < start_date:
            continue
        if end_date and date > end_date:
            continue
        files.append(path)
    return files


def load_price_history(ticker: str, cache: dict[str, pd.DataFrame | None]) -> pd.DataFrame | None:
    if ticker in cache:
        return cache[ticker]

    path = DAILY_K_DIR / f"{ticker}.csv"
    if not path.exists():
        cache[ticker] = None
        return None

    try:
        df = pd.read_csv(path, dtype={"Date": str})
        df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
        for col in ["Open", "High", "Low", "Close", "ATR_14"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.dropna(subset=["Date", "Open", "High", "Low", "Close"])
        df = df.sort_values("Date").reset_index(drop=True)
    except Exception:
        df = None

    cache[ticker] = df
    return df


def select_top_n(prediction_path: Path, top_n: int) -> pd.DataFrame:
    df = pd.read_csv(prediction_path, encoding="utf-8-sig", dtype={"ticker": str})
    if "date" not in df.columns:
        df["date"] = _prediction_date_from_path(prediction_path)
    top = apply_sector_cap(df, top_n=top_n)
    if top.empty:
        sort_col = "leaderboard_score" if "leaderboard_score" in df.columns else "pred_return_20d"
        top = df.sort_values(sort_col, ascending=False).head(top_n).copy()
    top = top.reset_index(drop=True).copy()
    top["selection_rank"] = np.arange(1, len(top) + 1)
    return top


def _resolve_thresholds(
    config: OverlayConfig,
    entry_price: float,
    entry_row: pd.Series,
) -> tuple[float | None, float | None]:
    stop_loss = config.stop_loss
    take_profit = config.take_profit

    atr = _round(entry_row.get("ATR_14"))
    if atr is not None and entry_price > 0:
        atr_pct = atr / entry_price
        if config.atr_stop_mult is not None:
            stop_loss = max(0.0, float(config.atr_stop_mult) * atr_pct)
        if config.atr_take_mult is not None:
            take_profit = max(0.0, float(config.atr_take_mult) * atr_pct)

    return stop_loss, take_profit


def simulate_trade(
    *,
    ticker: str,
    prediction_date: str,
    price_history: pd.DataFrame | None,
    config: OverlayConfig,
    friction: float,
    include_open_mtm: bool,
    ambiguous_policy: str,
) -> dict[str, Any] | None:
    if price_history is None or price_history.empty:
        return None

    selection_ts = pd.Timestamp(prediction_date)
    future = price_history[price_history["Date"] > selection_ts].reset_index(drop=True)
    if future.empty:
        return None

    max_hold_days = int(config.max_hold_days)
    window = future.head(max_hold_days).copy()
    if window.empty:
        return None

    entry_row = window.iloc[0]
    entry_price = _round(entry_row["Open"])
    if entry_price is None or entry_price <= 0:
        return None

    stop_loss, take_profit = _resolve_thresholds(config, entry_price, entry_row)
    mfe = -math.inf
    mae = math.inf
    exit_reason = None
    exit_day_number = None
    exit_date = None
    gross_return = None
    exit_price = None

    for day_number, row in enumerate(window.itertuples(index=False), start=1):
        open_ret = float(row.Open) / entry_price - 1.0
        high_ret = float(row.High) / entry_price - 1.0
        low_ret = float(row.Low) / entry_price - 1.0
        close_ret = float(row.Close) / entry_price - 1.0
        mfe = max(mfe, high_ret)
        mae = min(mae, low_ret)

        hit_gap_stop = stop_loss is not None and open_ret <= -stop_loss
        hit_gap_take = take_profit is not None and open_ret >= take_profit
        hit_stop = stop_loss is not None and low_ret <= -stop_loss
        hit_take = take_profit is not None and high_ret >= take_profit

        if hit_gap_stop:
            gross_return = open_ret
            exit_price = float(row.Open)
            exit_reason = "gap_stop"
        elif hit_gap_take:
            gross_return = take_profit
            exit_price = entry_price * (1.0 + take_profit)
            exit_reason = "gap_take_profit"
        elif hit_stop and hit_take:
            if ambiguous_policy == "target_first":
                gross_return = take_profit
                exit_price = entry_price * (1.0 + take_profit)
                exit_reason = "ambiguous_take_profit"
            elif ambiguous_policy == "close":
                gross_return = close_ret
                exit_price = float(row.Close)
                exit_reason = "ambiguous_close"
            else:
                gross_return = -stop_loss
                exit_price = entry_price * (1.0 - stop_loss)
                exit_reason = "ambiguous_stop_loss"
        elif hit_take:
            gross_return = take_profit
            exit_price = entry_price * (1.0 + take_profit)
            exit_reason = "take_profit"
        elif hit_stop:
            gross_return = -stop_loss
            exit_price = entry_price * (1.0 - stop_loss)
            exit_reason = "stop_loss"
        elif day_number == max_hold_days and len(window) >= max_hold_days:
            gross_return = close_ret
            exit_price = float(row.Close)
            exit_reason = "timeout"

        if exit_reason is not None:
            exit_day_number = day_number
            exit_date = pd.Timestamp(row.Date).date().isoformat()
            break

    if exit_reason is None:
        if not include_open_mtm:
            return None
        last = window.iloc[-1]
        gross_return = float(last["Close"]) / entry_price - 1.0
        exit_price = float(last["Close"])
        exit_reason = "open_mtm"
        exit_day_number = len(window)
        exit_date = pd.Timestamp(last["Date"]).date().isoformat()

    net_return = float(gross_return) - friction
    return {
        "ticker": ticker,
        "prediction_date": prediction_date,
        "overlay": config.name,
        "entry_date": pd.Timestamp(entry_row["Date"]).date().isoformat(),
        "entry_open": entry_price,
        "exit_date": exit_date,
        "exit_price": _round(exit_price, 4),
        "exit_day_number": int(exit_day_number),
        "exit_reason": exit_reason,
        "gross_return": _round(gross_return),
        "net_return": _round(net_return),
        "mfe": _round(mfe),
        "mae": _round(mae),
        "stop_loss": _round(stop_loss),
        "take_profit": _round(take_profit),
        "bars_available": int(len(window)),
    }


def max_drawdown(equity: pd.Series) -> float:
    if equity.empty:
        return 0.0
    peak = equity.cummax()
    drawdown = equity / peak - 1.0
    return float(drawdown.min())


def summarize_overlay(trades: pd.DataFrame, signal_returns: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for overlay, sub in trades.groupby("overlay", sort=True):
        sig = signal_returns[signal_returns["overlay"] == overlay].sort_values("prediction_date")
        equity = (1.0 + sig["basket_return"]).cumprod()
        cum_return = float(equity.iloc[-1] - 1.0) if not equity.empty else 0.0
        mdd = max_drawdown(equity) if not equity.empty else 0.0
        period_years = None
        cagr = None
        if len(sig) >= 2:
            first = pd.Timestamp(sig["prediction_date"].iloc[0])
            last = pd.Timestamp(sig["prediction_date"].iloc[-1])
            period_years = max((last - first).days / 365.25, 1.0 / 365.25)
            ending = float(equity.iloc[-1])
            if ending > 0:
                cagr = ending ** (1.0 / period_years) - 1.0
        calmar_proxy = cum_return / abs(mdd) if mdd < 0 else None
        annualized_calmar = (
            cagr / abs(mdd)
            if cagr is not None and period_years is not None and period_years >= 0.5 and mdd < 0
            else None
        )

        exit_rates = sub["exit_reason"].value_counts(normalize=True).to_dict()
        rows.append(
            {
                "overlay": overlay,
                "trades": int(len(sub)),
                "signal_days": int(sig["prediction_date"].nunique()),
                "basket_avg_return": _round(sig["basket_return"].mean()),
                "basket_cum_return": _round(cum_return),
                "signal_basket_cagr": _round(cagr),
                "mdd": _round(mdd),
                "calmar_proxy": _round(calmar_proxy),
                "annualized_calmar": _round(annualized_calmar),
                "trade_avg_return": _round(sub["net_return"].mean()),
                "trade_median_return": _round(sub["net_return"].median()),
                "win_rate": _round((sub["net_return"] > 0).mean()),
                "avg_hold_days": _round(sub["exit_day_number"].mean(), 3),
                "avg_mfe": _round(sub["mfe"].mean()),
                "avg_mae": _round(sub["mae"].mean()),
                "take_profit_rate": _round(
                    sub["exit_reason"].astype(str).str.contains("take_profit").mean()
                ),
                "stop_loss_rate": _round(
                    sub["exit_reason"].astype(str).str.contains("stop").mean()
                ),
                "timeout_rate": _round(exit_rates.get("timeout", 0.0)),
                "open_mtm_rate": _round(exit_rates.get("open_mtm", 0.0)),
            }
        )
    out = pd.DataFrame(rows)
    if not out.empty:
        out = out.sort_values(["calmar_proxy", "basket_cum_return"], ascending=[False, False], na_position="last")
    return out


def build_equity_curve(signal_returns: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for overlay, sub in signal_returns.groupby("overlay", sort=True):
        ordered = sub.sort_values("prediction_date").copy()
        equity = (1.0 + ordered["basket_return"]).cumprod()
        peak = equity.cummax()
        ordered["equity"] = equity
        ordered["cum_return"] = equity - 1.0
        ordered["running_peak"] = peak
        ordered["drawdown"] = equity / peak - 1.0
        rows.append(ordered)
    if not rows:
        return pd.DataFrame()
    out = pd.concat(rows, ignore_index=True)
    for col in ["basket_return", "avg_hold_days", "avg_mfe", "avg_mae", "equity", "cum_return", "running_peak", "drawdown"]:
        if col in out.columns:
            out[col] = out[col].map(_round)
    return out.sort_values(["overlay", "prediction_date"]).reset_index(drop=True)


def _add_rank_bucket(trades: pd.DataFrame) -> pd.DataFrame:
    out = trades.copy()
    ranks = pd.to_numeric(out["selection_rank"], errors="coerce")
    labels = ["01-05", "06-10", "11-15", "16-20", "21-25", "26-30", "31+"]
    out["rank_bucket"] = pd.cut(
        ranks,
        bins=[0, 5, 10, 15, 20, 25, 30, float("inf")],
        labels=labels,
        include_lowest=True,
    )
    out["rank_bucket"] = out["rank_bucket"].astype("string").fillna("unknown")
    return out


def summarize_breakdown(trades: pd.DataFrame, group_cols: list[str]) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame()

    work = trades.copy()
    if "rank_bucket" in group_cols:
        work = _add_rank_bucket(work)
    if "sector" in group_cols:
        work["sector"] = work["sector"].fillna("unknown").replace("", "unknown")

    out = (
        work.groupby(group_cols, dropna=False)
        .agg(
            trades=("ticker", "count"),
            unique_tickers=("ticker", "nunique"),
            avg_net_return=("net_return", "mean"),
            median_net_return=("net_return", "median"),
            win_rate=("net_return", lambda s: (s > 0).mean()),
            avg_hold_days=("exit_day_number", "mean"),
            avg_mfe=("mfe", "mean"),
            avg_mae=("mae", "mean"),
            take_profit_rate=("exit_reason", lambda s: s.astype(str).str.contains("take_profit").mean()),
            stop_loss_rate=("exit_reason", lambda s: s.astype(str).str.contains("stop").mean()),
            timeout_rate=("exit_reason", lambda s: (s == "timeout").mean()),
            open_mtm_rate=("exit_reason", lambda s: (s == "open_mtm").mean()),
        )
        .reset_index()
    )
    for col in [
        "avg_net_return",
        "median_net_return",
        "win_rate",
        "avg_hold_days",
        "avg_mfe",
        "avg_mae",
        "take_profit_rate",
        "stop_loss_rate",
        "timeout_rate",
        "open_mtm_rate",
    ]:
        out[col] = out[col].map(_round)

    sort_cols = ["overlay"] + [col for col in group_cols if col != "overlay"]
    return out.sort_values(sort_cols).reset_index(drop=True)


def build_audit(
    *,
    trade_details: pd.DataFrame,
    signal_returns: pd.DataFrame,
    summary: pd.DataFrame,
    metadata: dict[str, Any],
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []

    def add(check: str, status: str, value: Any, threshold: str, details: str) -> None:
        rows.append(
            {
                "check": check,
                "status": status,
                "value": value,
                "threshold": threshold,
                "details": details,
            }
        )

    prediction_ts = pd.to_datetime(trade_details["prediction_date"], errors="coerce")
    entry_ts = pd.to_datetime(trade_details["entry_date"], errors="coerce")
    exit_ts = pd.to_datetime(trade_details["exit_date"], errors="coerce")
    selected = trade_details.drop_duplicates(["prediction_date", "ticker", "selection_rank"]).copy()

    lookahead_count = int((entry_ts <= prediction_ts).sum())
    add(
        "entry_after_prediction",
        "PASS" if lookahead_count == 0 else "FAIL",
        lookahead_count,
        "0",
        "Entry date must be strictly after prediction date.",
    )

    bad_exit_count = int((exit_ts < entry_ts).sum())
    add(
        "exit_not_before_entry",
        "PASS" if bad_exit_count == 0 else "FAIL",
        bad_exit_count,
        "0",
        "Exit date must not be earlier than entry date.",
    )

    overlay_counts = trade_details.groupby("overlay").size()
    overlay_min = int(overlay_counts.min()) if not overlay_counts.empty else 0
    overlay_max = int(overlay_counts.max()) if not overlay_counts.empty else 0
    add(
        "overlay_trade_count_consistency",
        "PASS" if overlay_min == overlay_max else "WARN",
        f"{overlay_min}..{overlay_max}",
        "same count across overlays",
        "Different counts usually mean incomplete price windows or skipped simulations.",
    )

    duplicate_symbols = int(selected.duplicated(["prediction_date", "ticker"]).sum())
    add(
        "duplicate_symbol_per_signal_day",
        "PASS" if duplicate_symbols == 0 else "FAIL",
        duplicate_symbols,
        "0",
        "Each selected ticker should appear once per prediction date before overlay expansion.",
    )

    per_day_selected = selected.groupby("prediction_date")["ticker"].nunique()
    min_selected = int(per_day_selected.min()) if not per_day_selected.empty else 0
    max_selected = int(per_day_selected.max()) if not per_day_selected.empty else 0
    add(
        "simulated_top_n_fill",
        "PASS" if min_selected == metadata["top_n"] and max_selected == metadata["top_n"] else "WARN",
        f"{min_selected}..{max_selected}",
        str(metadata["top_n"]),
        "Uses the final selector output with usable future OHLC data; fewer names reduce comparability.",
    )

    skipped = int(metadata.get("skipped_trade_simulations", 0))
    add(
        "skipped_trade_simulations",
        "PASS" if skipped == 0 else "WARN",
        skipped,
        "0",
        "Skipped rows are usually missing price history or incomplete non-MTM windows.",
    )

    max_open_mtm = _round(summary["open_mtm_rate"].max() if "open_mtm_rate" in summary else 0.0)
    add(
        "open_mtm_rate",
        "PASS" if (max_open_mtm or 0.0) <= 0.10 else "WARN",
        max_open_mtm,
        "<= 0.10",
        "High MTM means the report is a live mark-to-market view, not a fully closed trade study.",
    )

    if not signal_returns.empty:
        idx = signal_returns["basket_return"].abs().idxmax()
        extreme = signal_returns.loc[idx]
        max_abs = _round(abs(extreme["basket_return"]))
        add(
            "extreme_signal_basket_return",
            "PASS" if (max_abs or 0.0) <= 0.25 else "WARN",
            max_abs,
            "<= 0.25",
            f"{extreme['overlay']} on {extreme['prediction_date']} had basket_return={_round(extreme['basket_return'])}.",
        )

    gap_rate = _round(trade_details["exit_reason"].astype(str).str.startswith("gap_").mean())
    add(
        "gap_exit_rate",
        "PASS" if (gap_rate or 0.0) <= 0.30 else "WARN",
        gap_rate,
        "<= 0.30",
        "Large gap exit share makes daily-bar TP/SL execution assumptions more sensitive.",
    )

    ambiguous_rate = _round(trade_details["exit_reason"].astype(str).str.startswith("ambiguous_").mean())
    add(
        "same_day_tp_sl_ambiguity",
        "PASS" if (ambiguous_rate or 0.0) <= 0.01 else "WARN",
        ambiguous_rate,
        "<= 0.01",
        f"Policy used: {metadata['ambiguous_policy']}.",
    )

    sector_share = 0.0
    if "sector" in selected.columns and not selected.empty:
        sector_share = float(selected["sector"].fillna("unknown").value_counts(normalize=True).max())
    add(
        "max_period_sector_share",
        "PASS" if sector_share <= 0.40 else "WARN",
        _round(sector_share),
        "<= 0.40",
        "Period-level concentration can stay high even when each day is sector-capped.",
    )

    return pd.DataFrame(rows)


def build_audit_markdown(audit: pd.DataFrame, metadata: dict[str, Any]) -> str:
    statuses = audit["status"].astype(str).tolist() if not audit.empty else []
    overall = "FAIL" if "FAIL" in statuses else "WARN" if "WARN" in statuses else "PASS"
    lines = [
        "# V2 Exit Overlay Backtest Audit",
        "",
        f"- Generated: `{metadata['generated_at']}`",
        f"- Branch: `{metadata.get('branch', '-')}`",
        f"- Prediction date range: `{metadata['prediction_start']}` to `{metadata['prediction_end']}`",
        f"- Overall: `{overall}`",
        "",
        "| Check | Status | Value | Threshold | Details |",
        "|---|---:|---:|---:|---|",
    ]
    for row in audit.to_dict("records"):
        lines.append(
            "| "
            + " | ".join(
                [
                    str(row["check"]),
                    str(row["status"]),
                    str(row["value"]),
                    str(row["threshold"]),
                    str(row["details"]),
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## Output Files",
            "",
            f"- Equity curve CSV: `{metadata['equity_curve_csv']}`",
            f"- Rank breakdown CSV: `{metadata['rank_breakdown_csv']}`",
            f"- Sector breakdown CSV: `{metadata['sector_breakdown_csv']}`",
            f"- Trade detail CSV: `{metadata['trade_details_csv']}`",
        ]
    )
    return "\n".join(lines) + "\n"


def build_markdown(
    *,
    summary: pd.DataFrame,
    signal_returns: pd.DataFrame,
    trade_details: pd.DataFrame,
    audit: pd.DataFrame,
    metadata: dict[str, Any],
) -> str:
    lines = [
        "# V2 Exit Overlay Research Backtest",
        "",
        f"- Generated: `{metadata['generated_at']}`",
        f"- Branch: `{metadata.get('branch', '-')}`",
        f"- Prediction files: `{metadata['prediction_file_count']}`",
        f"- Prediction date range: `{metadata['prediction_start']}` to `{metadata['prediction_end']}`",
        f"- Price data latest date: `{metadata.get('price_latest_date')}`",
        f"- Entry: `Open[t+1]`",
        f"- Friction: `{metadata['friction']:.2%}` round-trip",
        f"- Top N: `{metadata['top_n']}` sector-capped production leaderboard",
        f"- Same-day TP/SL ambiguity: `{metadata['ambiguous_policy']}`",
        f"- Include open MTM: `{metadata['include_open_mtm']}`",
        "",
        "> This is a research-only signal-basket replay. `Calmar*` is `basket cumulative return / |MDD|` for the short sample. Annualized CAGR is kept in CSV, but it is not capital-accurate overlapping-position CAGR.",
        "",
        "## Summary",
        "",
        "| Overlay | Trades | Signal Days | Basket Cum | MDD | Calmar* | Avg Trade | Win | Avg Hold | TP | SL | Timeout | Open MTM |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in summary.to_dict("records"):
        lines.append(
            "| "
            + " | ".join(
                [
                    str(row["overlay"]),
                    str(row["trades"]),
                    str(row["signal_days"]),
                    _fmt_pct(row.get("basket_cum_return")),
                    _fmt_pct(row.get("mdd")),
                    _fmt_num(row.get("calmar_proxy")),
                    _fmt_pct(row.get("trade_avg_return")),
                    _fmt_pct(row.get("win_rate")),
                    _fmt_num(row.get("avg_hold_days"), 2),
                    _fmt_pct(row.get("take_profit_rate")),
                    _fmt_pct(row.get("stop_loss_rate")),
                    _fmt_pct(row.get("timeout_rate")),
                    _fmt_pct(row.get("open_mtm_rate")),
                ]
            )
            + " |"
        )

    best = summary.head(1).to_dict("records")
    if best:
        row = best[0]
        lines.extend(
            [
                "",
                "## First Read",
                "",
                f"- Best by Calmar proxy: `{row['overlay']}`.",
                f"- Basket cumulative return: `{_fmt_pct(row.get('basket_cum_return'))}`.",
                f"- MDD: `{_fmt_pct(row.get('mdd'))}`.",
                f"- Average hold days: `{_fmt_num(row.get('avg_hold_days'), 2)}`.",
                f"- TP / SL / timeout / open-MTM rates: `{_fmt_pct(row.get('take_profit_rate'))}` / `{_fmt_pct(row.get('stop_loss_rate'))}` / `{_fmt_pct(row.get('timeout_rate'))}` / `{_fmt_pct(row.get('open_mtm_rate'))}`.",
            ]
        )

    reason = (
        trade_details.groupby(["overlay", "exit_reason"]).size().rename("count").reset_index()
        if not trade_details.empty
        else pd.DataFrame()
    )
    if not reason.empty:
        lines.extend(["", "## Exit Reason Counts", ""])
        for overlay, sub in reason.groupby("overlay", sort=True):
            counts = {r["exit_reason"]: int(r["count"]) for r in sub.to_dict("records")}
            lines.append(f"- `{overlay}`: `{counts}`")

    if not audit.empty:
        status_counts = audit["status"].value_counts().to_dict()
        lines.extend(["", "## Audit Snapshot", ""])
        for status in ["FAIL", "WARN", "PASS"]:
            if status in status_counts:
                lines.append(f"- `{status}`: `{int(status_counts[status])}` checks")

    lines.extend(
        [
            "",
            "## Output Files",
            "",
            f"- Summary CSV: `{metadata['summary_csv']}`",
            f"- Signal return CSV: `{metadata['signal_returns_csv']}`",
            f"- Trade detail CSV: `{metadata['trade_details_csv']}`",
            f"- Audit MD: `{metadata['audit_md']}`",
            f"- Equity curve CSV: `{metadata['equity_curve_csv']}`",
            f"- Rank breakdown CSV: `{metadata['rank_breakdown_csv']}`",
            f"- Sector breakdown CSV: `{metadata['sector_breakdown_csv']}`",
        ]
    )
    return "\n".join(lines) + "\n"


def run_backtest(args: argparse.Namespace) -> dict[str, Any]:
    prediction_files = list_prediction_files(args.start_date, args.end_date)
    if not prediction_files:
        raise FileNotFoundError("No production predictions_YYYY-MM-DD.csv files matched.")

    overlays = default_overlays(args.max_hold_days)
    price_cache: dict[str, pd.DataFrame | None] = {}
    trade_rows: list[dict[str, Any]] = []
    skipped = 0

    for idx, pred_path in enumerate(prediction_files, start=1):
        prediction_date = _prediction_date_from_path(pred_path)
        print(f"[{idx}/{len(prediction_files)}] {prediction_date} {pred_path.name}", flush=True)
        picks = select_top_n(pred_path, top_n=args.top_n)
        for pick in picks.to_dict("records"):
            ticker = str(pick.get("ticker"))
            price_history = load_price_history(ticker, price_cache)
            for overlay in overlays:
                result = simulate_trade(
                    ticker=ticker,
                    prediction_date=prediction_date,
                    price_history=price_history,
                    config=overlay,
                    friction=args.friction,
                    include_open_mtm=args.include_open_mtm,
                    ambiguous_policy=args.ambiguous_policy,
                )
                if result is None:
                    skipped += 1
                    continue
                result.update(
                    {
                        "selection_rank": pick.get("selection_rank"),
                        "pred_return_20d": _round(pick.get("pred_return_20d")),
                        "leaderboard_score": _round(pick.get("leaderboard_score")),
                        "recommendation": pick.get("recommendation"),
                        "sector": pick.get("sector"),
                    }
                )
                trade_rows.append(result)

    if not trade_rows:
        raise RuntimeError("No simulated trades were produced.")

    trade_details = pd.DataFrame(trade_rows)
    signal_returns = (
        trade_details.groupby(["prediction_date", "overlay"], as_index=False)
        .agg(
            basket_return=("net_return", "mean"),
            trade_count=("ticker", "count"),
            avg_hold_days=("exit_day_number", "mean"),
            avg_mfe=("mfe", "mean"),
            avg_mae=("mae", "mean"),
        )
        .sort_values(["overlay", "prediction_date"])
    )
    summary = summarize_overlay(trade_details, signal_returns)
    equity_curve = build_equity_curve(signal_returns)
    rank_breakdown = summarize_breakdown(trade_details, ["overlay", "rank_bucket"])
    sector_breakdown = summarize_breakdown(trade_details, ["overlay", "sector"])

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    prefix = args.output_prefix
    summary_csv = output_dir / f"{prefix}_summary.csv"
    signal_csv = output_dir / f"{prefix}_signal_returns.csv"
    trade_csv = output_dir / f"{prefix}_trades.csv"
    audit_csv = output_dir / f"{prefix}_audit.csv"
    equity_csv = output_dir / f"{prefix}_equity_curve.csv"
    rank_csv = output_dir / f"{prefix}_by_rank.csv"
    sector_csv = output_dir / f"{prefix}_by_sector.csv"
    audit_md_path = output_dir / f"{prefix}_audit.md"
    md_path = output_dir / f"{prefix}.md"
    json_path = output_dir / f"{prefix}.json"

    latest_dates = []
    for df in price_cache.values():
        if df is not None and not df.empty:
            latest_dates.append(df["Date"].max())

    metadata = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "branch": os.popen("git branch --show-current").read().strip(),
        "prediction_file_count": len(prediction_files),
        "prediction_start": _prediction_date_from_path(prediction_files[0]),
        "prediction_end": _prediction_date_from_path(prediction_files[-1]),
        "price_latest_date": max(latest_dates).date().isoformat() if latest_dates else None,
        "top_n": args.top_n,
        "friction": args.friction,
        "max_hold_days": args.max_hold_days,
        "include_open_mtm": bool(args.include_open_mtm),
        "ambiguous_policy": args.ambiguous_policy,
        "skipped_trade_simulations": int(skipped),
        "summary_csv": str(summary_csv),
        "signal_returns_csv": str(signal_csv),
        "trade_details_csv": str(trade_csv),
        "audit_csv": str(audit_csv),
        "audit_md": str(audit_md_path),
        "equity_curve_csv": str(equity_csv),
        "rank_breakdown_csv": str(rank_csv),
        "sector_breakdown_csv": str(sector_csv),
    }
    audit = build_audit(
        trade_details=trade_details,
        signal_returns=signal_returns,
        summary=summary,
        metadata=metadata,
    )

    summary.to_csv(summary_csv, index=False, encoding="utf-8-sig")
    signal_returns.to_csv(signal_csv, index=False, encoding="utf-8-sig")
    trade_details.to_csv(trade_csv, index=False, encoding="utf-8-sig")
    audit.to_csv(audit_csv, index=False, encoding="utf-8-sig")
    equity_curve.to_csv(equity_csv, index=False, encoding="utf-8-sig")
    rank_breakdown.to_csv(rank_csv, index=False, encoding="utf-8-sig")
    sector_breakdown.to_csv(sector_csv, index=False, encoding="utf-8-sig")

    md_path.write_text(
        build_markdown(
            summary=summary,
            signal_returns=signal_returns,
            trade_details=trade_details,
            audit=audit,
            metadata=metadata,
        ),
        encoding="utf-8",
    )
    audit_md_path.write_text(build_audit_markdown(audit, metadata), encoding="utf-8")
    json_path.write_text(
        json.dumps(
            {
                "metadata": metadata,
                "summary": summary.to_dict("records"),
                "audit": audit.to_dict("records"),
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )

    print("\nSummary:")
    print(
        summary[
            [
                "overlay",
                "trades",
                "signal_days",
                "basket_cum_return",
                "mdd",
                "calmar_proxy",
                "trade_avg_return",
                "avg_hold_days",
                "take_profit_rate",
                "stop_loss_rate",
                "timeout_rate",
                "open_mtm_rate",
            ]
        ].to_string(index=False)
    )
    print(f"\nWrote {md_path}")
    print(f"Wrote {audit_md_path}")
    print(f"Wrote {summary_csv}")
    print(f"Wrote {signal_csv}")
    print(f"Wrote {equity_csv}")
    print(f"Wrote {rank_csv}")
    print(f"Wrote {sector_csv}")
    print(f"Wrote {trade_csv}")
    return metadata


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Research-only V2 exit overlay replay.")
    parser.add_argument("--start-date", default="2026-03-11")
    parser.add_argument("--end-date", default=None)
    parser.add_argument("--top-n", type=int, default=DEFAULT_TOP_N)
    parser.add_argument("--max-hold-days", type=int, default=DEFAULT_MAX_HOLD_DAYS)
    parser.add_argument("--friction", type=float, default=DEFAULT_FRICTION)
    parser.add_argument("--include-open-mtm", action="store_true")
    parser.add_argument(
        "--ambiguous-policy",
        choices=["stop_first", "target_first", "close"],
        default="stop_first",
    )
    parser.add_argument("--output-dir", default=REPORT_DIR)
    parser.add_argument("--output-prefix", default=DEFAULT_OUTPUT_PREFIX)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    run_backtest(args)


if __name__ == "__main__":
    main()
