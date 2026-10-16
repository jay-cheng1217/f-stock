"""DIAG-001: subgroup OOF exit trades by entry MA5 state."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ml.config import DAILY_K_DIR, REPORT_DIR


def _safe_float(value: Any) -> float | None:
    try:
        number = float(value)
    except Exception:
        return None
    if not np.isfinite(number):
        return None
    return number


def _normalize_ticker(value: Any) -> str:
    text = str(value).strip().upper()
    if text.isdigit() and len(text) < 4:
        return text.zfill(4)
    return text


def _load_price_history(ticker: str, cache: dict[str, pd.DataFrame | None]) -> pd.DataFrame | None:
    ticker = _normalize_ticker(ticker)
    if ticker in cache:
        return cache[ticker]
    path = Path(DAILY_K_DIR) / f"{ticker}.csv"
    if not path.exists():
        cache[ticker] = None
        return None
    df = pd.read_csv(path, dtype={"Date": str})
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    df = df.dropna(subset=["Date"]).sort_values("Date").reset_index(drop=True)
    for col in ["Open", "Close"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["MA5"] = df["Close"].rolling(window=5, min_periods=1).mean()
    cache[ticker] = df
    return df


def _entry_ma5_state(row: pd.Series, cache: dict[str, pd.DataFrame | None]) -> tuple[float | None, float | None]:
    history = _load_price_history(str(row["ticker"]), cache)
    if history is None or history.empty:
        return None, None
    pred_rows = history[history["Date"] <= pd.Timestamp(row["prediction_date"])]
    if pred_rows.empty:
        return None, None
    signal_row = pred_rows.iloc[-1]
    signal_close = _safe_float(signal_row["Close"])
    signal_ma5 = _safe_float(signal_row["MA5"])
    signal_vs_ma5 = (
        signal_close / signal_ma5 - 1.0
        if signal_close is not None and signal_ma5 not in {None, 0}
        else None
    )

    entry_rows = history[history["Date"] == pd.Timestamp(row["entry_date"])]
    if entry_rows.empty:
        return signal_vs_ma5, None
    entry_open = _safe_float(entry_rows.iloc[0]["Open"])
    entry_vs_prev_ma5 = (
        entry_open / signal_ma5 - 1.0
        if entry_open is not None and signal_ma5 not in {None, 0}
        else None
    )
    return signal_vs_ma5, entry_vs_prev_ma5


def _bucket(value: float | None) -> str:
    if value is None:
        return "MISSING"
    if value > 0:
        return "A_price_vs_ma5_at_entry_gt_0"
    if value > -0.05:
        return "B_minus5pct_to_0"
    return "C_le_minus5pct"


def _fmt_pct(value: Any) -> str:
    number = _safe_float(value)
    if number is None:
        return "n/a"
    return f"{number * 100:.2f}%"


def run(args: argparse.Namespace) -> dict[str, str]:
    trades = pd.read_csv(args.trades_csv, dtype={"ticker": str})
    trades = trades[
        (trades["variant"] == args.variant)
        & (trades["month"] >= args.start_month)
        & (trades["month"] <= args.end_month)
    ].copy()
    if trades.empty:
        raise ValueError("No trades matched the requested variant/window")

    cache: dict[str, pd.DataFrame | None] = {}
    states = trades.apply(lambda row: _entry_ma5_state(row, cache), axis=1)
    trades["signal_close_vs_ma5"] = [item[0] for item in states]
    trades["price_vs_ma5_at_entry"] = [item[1] for item in states]
    trades["entry_ma5_bucket"] = trades["price_vs_ma5_at_entry"].map(_bucket)
    trades["is_ma5_break_exit"] = trades["exit_reason"].astype(str).eq("MA5_BREAK")
    trades["is_win"] = pd.to_numeric(trades["return_pct"], errors="coerce").gt(0)

    order = [
        "A_price_vs_ma5_at_entry_gt_0",
        "B_minus5pct_to_0",
        "C_le_minus5pct",
        "MISSING",
    ]
    summary = (
        trades.groupby("entry_ma5_bucket", as_index=False)
        .agg(
            samples=("ticker", "count"),
            avg_hold_days=("hold_days", "mean"),
            avg_realized_return=("return_pct", "mean"),
            win_rate=("is_win", "mean"),
            ma5_break_exit_pct=("is_ma5_break_exit", "mean"),
            avg_price_vs_ma5_at_entry=("price_vs_ma5_at_entry", "mean"),
            avg_signal_close_vs_ma5=("signal_close_vs_ma5", "mean"),
        )
    )
    summary["entry_ma5_bucket"] = pd.Categorical(summary["entry_ma5_bucket"], order, ordered=True)
    summary = summary.sort_values("entry_ma5_bucket").reset_index(drop=True)

    report_dir = Path(REPORT_DIR)
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = args.output_date or datetime.now().strftime("%Y%m%d")
    csv_path = report_dir / f"diag001_entry_ma5_subgroup_{stamp}.csv"
    md_path = report_dir / f"diag001_entry_ma5_subgroup_{stamp}.md"
    summary.to_csv(csv_path, index=False, encoding="utf-8-sig")

    lines = [
        "# DIAG-001 Entry MA5 Subgroup Analysis",
        "",
        f"- Generated at: `{datetime.now().isoformat(timespec='seconds')}`",
        f"- Source trades: `{args.trades_csv}`",
        f"- Variant: `{args.variant}`",
        f"- Window: `{args.start_month}` to `{args.end_month}`",
        "- Grouping key: actual entry open vs previous trading day's MA5 (`price_vs_ma5_at_entry`).",
        "- `signal_close_vs_ma5` is included as a sanity check for the signal-date technical state.",
        "",
        "| Subgroup | Definition | Samples | Avg hold days | Avg realized return | Win rate | MA5_BREAK exit % | Avg price vs MA5 at entry | Avg signal close vs MA5 |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    definitions = {
        "A_price_vs_ma5_at_entry_gt_0": "price_vs_ma5_at_entry > 0",
        "B_minus5pct_to_0": "-5% < price_vs_ma5_at_entry <= 0",
        "C_le_minus5pct": "price_vs_ma5_at_entry <= -5%",
        "MISSING": "missing price/MA5 data",
    }
    for row in summary.to_dict("records"):
        bucket = str(row["entry_ma5_bucket"])
        lines.append(
            f"| {bucket} | {definitions.get(bucket, '')} | {int(row['samples'])} | "
            f"{row['avg_hold_days']:.2f} | {_fmt_pct(row['avg_realized_return'])} | "
            f"{_fmt_pct(row['win_rate'])} | {_fmt_pct(row['ma5_break_exit_pct'])} | "
            f"{_fmt_pct(row['avg_price_vs_ma5_at_entry'])} | "
            f"{_fmt_pct(row['avg_signal_close_vs_ma5'])} |"
        )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(summary.to_string(index=False))
    print(f"[diag001] wrote {md_path}")
    return {"csv": str(csv_path), "md": str(md_path)}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trades-csv", default=str(Path(REPORT_DIR) / "exit_v2_cl3_backtest_20260509_trades.csv"))
    parser.add_argument("--variant", default="Treatment_AsymmetricV2")
    parser.add_argument("--start-month", default="2024-01")
    parser.add_argument("--end-month", default="2026-04")
    parser.add_argument("--output-date", default=None)
    return parser.parse_args()


if __name__ == "__main__":
    run(parse_args())
