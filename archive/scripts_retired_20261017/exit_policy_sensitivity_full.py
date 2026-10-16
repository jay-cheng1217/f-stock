"""Exit policy sensitivity replay for saved V2 prediction artifacts.

Research-only. It reads saved ``predictions_YYYY-MM-DD.csv`` files, rebuilds
the sector-capped Top-N selection, and compares MA-break variants on the same
entry snapshots:

- Current: MA5 break OR 10% stop OR 20D timeout
- Variant A: MA10 break OR 10% stop OR 20D timeout
- Variant B: 10% stop OR 20D timeout
- Variant C: MA5 break requires two consecutive closes below MA5

Entry and stop simulation reuse the production order helpers.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))

from ml.config import DAILY_K_DIR, INDEX_DIR, MODEL_DIR, REPORT_DIR  # noqa: E402
from ml.predict import apply_sector_cap  # noqa: E402
from scripts.order_simulation import (  # noqa: E402
    FILL_STATUS_FILLED,
    simulate_20d_entry,
    simulate_intraday_stop,
)

PRED_RE = re.compile(r"^predictions_(\d{4}-\d{2}-\d{2})\.csv$")


@dataclass(frozen=True)
class ExitVariant:
    name: str
    ma_window: int | None
    consecutive_breaks: int = 1


VARIANTS = [
    ExitVariant("Current_MA5", ma_window=5, consecutive_breaks=1),
    ExitVariant("Variant_A_MA10", ma_window=10, consecutive_breaks=1),
    ExitVariant("Variant_B_StopOnly", ma_window=None, consecutive_breaks=0),
    ExitVariant("Variant_C_MA5_2Day", ma_window=5, consecutive_breaks=2),
]


def _safe_float(value: Any) -> float | None:
    try:
        number = float(value)
    except Exception:
        return None
    return number if math.isfinite(number) else None


def _fmt_pct(value: Any, digits: int = 2) -> str:
    number = _safe_float(value)
    if number is None:
        return "-"
    return f"{number * 100:+.{digits}f}%"


def _list_prediction_files(start: str, end: str) -> list[Path]:
    rows: list[tuple[str, Path]] = []
    for path in Path(MODEL_DIR).glob("predictions_*.csv"):
        match = PRED_RE.match(path.name)
        if not match:
            continue
        date_value = match.group(1)
        if start <= date_value <= end:
            rows.append((date_value, path))
    return [path for _, path in sorted(rows)]


def _prediction_date(path: Path) -> str:
    match = PRED_RE.match(path.name)
    if not match:
        raise ValueError(f"Cannot infer prediction date from {path}")
    return match.group(1)


def _sort_col(df: pd.DataFrame) -> str:
    for column in ["leaderboard_score", "pred_return_20d", "risk_adjusted_return", "up_prob"]:
        if column in df.columns:
            return column
    raise ValueError("Prediction file has no supported ranking column")


def _select_top_n(path: Path, top_n: int) -> pd.DataFrame:
    df = pd.read_csv(path, encoding="utf-8-sig", dtype={"ticker": str})
    if "date" not in df.columns:
        df["date"] = _prediction_date(path)
    top = apply_sector_cap(df, top_n=top_n)
    if top.empty:
        top = df.sort_values(_sort_col(df), ascending=False).head(top_n).copy()
    top = top.reset_index(drop=True)
    top["selection_rank"] = np.arange(1, len(top) + 1)
    return top


def _load_price_history(ticker: str, cache: dict[str, pd.DataFrame | None]) -> pd.DataFrame | None:
    ticker = str(ticker).upper()
    if ticker in cache:
        return cache[ticker]
    path = Path(DAILY_K_DIR) / f"{ticker}.csv"
    if not path.exists():
        cache[ticker] = None
        return None
    try:
        df = pd.read_csv(path, dtype={"Date": str})
        df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
        for col in ["Open", "High", "Low", "Close", "MA_5", "MA_10", "MA_20"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        if "MA_5" not in df.columns:
            df["MA_5"] = df["Close"].rolling(5, min_periods=5).mean()
        if "MA_10" not in df.columns:
            df["MA_10"] = df["Close"].rolling(10, min_periods=10).mean()
        df = df.dropna(subset=["Date", "Open", "High", "Low", "Close"]).sort_values("Date").reset_index(drop=True)
    except Exception:
        df = None
    cache[ticker] = df
    return df


def _load_twii() -> pd.DataFrame:
    path = Path(INDEX_DIR) / "index_TWII.csv"
    twii = pd.read_csv(path, dtype={"Date": str})
    twii["Date"] = pd.to_datetime(twii["Date"], errors="coerce")
    return twii.dropna(subset=["Date"]).sort_values("Date").reset_index(drop=True)


def _twii_return(twii: pd.DataFrame, entry_date: str, exit_date: str) -> float | None:
    rows = twii[(twii["Date"] >= pd.Timestamp(entry_date)) & (twii["Date"] <= pd.Timestamp(exit_date))]
    if rows.empty:
        return None
    entry_open = _safe_float(rows.iloc[0].get("Open", rows.iloc[0].get("Close")))
    exit_close = _safe_float(rows.iloc[-1].get("Close"))
    if entry_open is None or entry_open <= 0 or exit_close is None:
        return None
    return exit_close / entry_open - 1.0


def _simulate_trade(
    *,
    ticker: str,
    prediction_date: str,
    reference_price: float | None,
    variant: ExitVariant,
    price_history: pd.DataFrame | None,
    twii: pd.DataFrame,
    include_open_mtm: bool,
) -> dict[str, Any] | None:
    if price_history is None or price_history.empty:
        return None
    future = price_history[price_history["Date"] > pd.Timestamp(prediction_date)].reset_index(drop=True)
    if future.empty:
        return None

    entry_row = future.iloc[0]
    entry = simulate_20d_entry(reference_price, entry_row["Open"], entry_row["High"])
    if entry.fill_status != FILL_STATUS_FILLED or entry.fill_price is None:
        return None
    entry_price = float(entry.fill_price)
    entry_date = pd.Timestamp(entry_row["Date"]).date().isoformat()

    window = future.head(20).copy()
    pending_reason: str | None = None
    pending_trigger_date: str | None = None
    break_streak = 0
    max_drawdown = 0.0
    exit_price: float | None = None
    exit_date: str | None = None
    exit_reason: str | None = None
    hold_days: int | None = None

    for day_number, row in enumerate(window.itertuples(index=False), start=1):
        current_date = pd.Timestamp(row.Date).date().isoformat()

        if pending_reason is not None:
            exit_price = _safe_float(row.Open)
            exit_date = current_date
            exit_reason = pending_reason
            hold_days = day_number
            break

        low = _safe_float(row.Low)
        if low is not None:
            max_drawdown = min(max_drawdown, low / entry_price - 1.0)

        stop = simulate_intraday_stop(entry_price, row.Open, row.Low)
        if stop is not None:
            exit_price = float(stop.exit_price)
            exit_date = current_date
            exit_reason = stop.exit_reason
            hold_days = day_number
            break

        if day_number == 20 and len(window) >= 20:
            exit_price = _safe_float(row.Close)
            exit_date = current_date
            exit_reason = "TIME_20D"
            hold_days = day_number
            break

        if variant.ma_window is not None:
            ma_col = f"MA_{variant.ma_window}"
            ma_value = _safe_float(getattr(row, ma_col, None))
            close = _safe_float(row.Close)
            if ma_value is not None and close is not None and close < ma_value:
                break_streak += 1
            else:
                break_streak = 0
            if break_streak >= variant.consecutive_breaks:
                pending_reason = f"MA{variant.ma_window}_BREAK"
                if variant.consecutive_breaks > 1:
                    pending_reason += f"_{variant.consecutive_breaks}D"
                pending_trigger_date = current_date

    if exit_price is None:
        if not include_open_mtm or window.empty:
            return None
        last = window.iloc[-1]
        exit_price = _safe_float(last["Close"])
        exit_date = pd.Timestamp(last["Date"]).date().isoformat()
        exit_reason = "OPEN_MTM"
        hold_days = len(window)

    if exit_price is None or exit_price <= 0:
        return None
    gross_return = exit_price / entry_price - 1.0
    twii_ret = _twii_return(twii, entry_date, exit_date) if exit_date else None
    return {
        "variant": variant.name,
        "ticker": ticker,
        "prediction_date": prediction_date,
        "entry_date": entry_date,
        "exit_date": exit_date,
        "entry_price": entry_price,
        "exit_price": exit_price,
        "return_pct": gross_return,
        "twii_return_pct": twii_ret,
        "alpha_pct": gross_return - twii_ret if twii_ret is not None else None,
        "hold_days": hold_days,
        "exit_reason": exit_reason,
        "pending_trigger_date": pending_trigger_date,
        "max_drawdown_pct": max_drawdown,
    }


def _max_drawdown(equity: pd.Series) -> float:
    if equity.empty:
        return 0.0
    peak = equity.cummax()
    return float((equity / peak - 1.0).min())


def _summarize(trades: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    daily = (
        trades.groupby(["variant", "prediction_date"], as_index=False)
        .agg(
            basket_return_pct=("return_pct", "mean"),
            basket_alpha_pct=("alpha_pct", "mean"),
            avg_hold_days=("hold_days", "mean"),
            exits=("ticker", "count"),
        )
        .sort_values(["variant", "prediction_date"])
    )
    rows: list[dict[str, Any]] = []
    for variant, group in daily.groupby("variant", sort=False):
        equity = (1.0 + group["basket_return_pct"]).cumprod()
        months = max(pd.to_datetime(group["prediction_date"]).dt.to_period("M").nunique(), 1)
        trade_group = trades[trades["variant"] == variant]
        rows.append(
            {
                "variant": variant,
                "signal_days": int(group["prediction_date"].nunique()),
                "trades": int(len(trade_group)),
                "monthly_avg_return_pct": float(group.groupby(pd.to_datetime(group["prediction_date"]).dt.to_period("M"))["basket_return_pct"].mean().mean()),
                "monthly_avg_alpha_pct": float(group.groupby(pd.to_datetime(group["prediction_date"]).dt.to_period("M"))["basket_alpha_pct"].mean().mean()),
                "compound_return_pct": float(equity.iloc[-1] - 1.0),
                "mdd_pct": _max_drawdown(equity),
                "avg_hold_days": float(trade_group["hold_days"].mean()),
                "turnover_per_month": float(len(trade_group) / months),
                "ma_exit_count": int(trade_group["exit_reason"].astype(str).str.contains("MA").sum()),
                "stop_exit_count": int(trade_group["exit_reason"].astype(str).str.contains("STOP|GAP_STOP", regex=True).sum()),
                "timeout_count": int((trade_group["exit_reason"] == "TIME_20D").sum()),
                "open_mtm_count": int((trade_group["exit_reason"] == "OPEN_MTM").sum()),
            }
        )
    return pd.DataFrame(rows), daily


def _write_md(path: Path, *, summary: pd.DataFrame, metadata: dict[str, Any], focus: pd.DataFrame) -> None:
    lines = [
        "# Exit Policy Full Sensitivity Replay",
        "",
        f"- Generated at: `{metadata['generated_at']}`",
        f"- Requested window: `{metadata['requested_start']}` to `{metadata['requested_end']}`",
        f"- Actual prediction artifacts: `{metadata['actual_start']}` to `{metadata['actual_end']}`",
        f"- Prediction files: `{metadata['prediction_file_count']}`",
        f"- Include open MTM: `{metadata['include_open_mtm']}`",
        f"- Top N: `{metadata['top_n']}` sector-capped production picks",
        "",
        "## Summary",
        "",
        "| Exit version | Monthly ret | Alpha vs TWII | MDD | Avg hold | Turnover/mo | MA exits | Stop exits | 20D | MTM |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in summary.to_dict("records"):
        lines.append(
            f"| {row['variant']} | {_fmt_pct(row['monthly_avg_return_pct'])} | "
            f"{_fmt_pct(row['monthly_avg_alpha_pct'])} | {_fmt_pct(row['mdd_pct'])} | "
            f"{row['avg_hold_days']:.2f} | {row['turnover_per_month']:.1f} | "
            f"{row['ma_exit_count']} | {row['stop_exit_count']} | {row['timeout_count']} | {row['open_mtm_count']} |"
        )
    if not focus.empty:
        lines.extend(
            [
                "",
                "## Focus Tickers",
                "",
                "| ticker | variant | prediction_date | return | alpha | hold | exit |",
                "| --- | --- | --- | ---: | ---: | ---: | --- |",
            ]
        )
        for row in focus.to_dict("records"):
            lines.append(
                f"| {row['ticker']} | {row['variant']} | {row['prediction_date']} | "
                f"{_fmt_pct(row['return_pct'])} | {_fmt_pct(row['alpha_pct'])} | "
                f"{int(row['hold_days'])} | {row['exit_reason']} |"
            )
    lines.extend(
        [
            "",
            "## Notes",
            "",
            "- Existing saved production prediction artifacts only cover the actual artifact range above; 2024-2025 daily prediction CSVs are not present in this workspace.",
            "- Incomplete 20D windows are included as `OPEN_MTM` when requested, so latest May rows are diagnostic, not final realized exits.",
            "- Stop-loss simulation reuses production `simulate_intraday_stop`, including gap-stop and stop-loss slippage behavior.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def run(args: argparse.Namespace) -> dict[str, Any]:
    files = _list_prediction_files(args.start, args.end)
    if not files:
        raise FileNotFoundError("No saved prediction artifacts found")
    twii = _load_twii()
    price_cache: dict[str, pd.DataFrame | None] = {}
    rows: list[dict[str, Any]] = []

    for idx, path in enumerate(files, start=1):
        date_value = _prediction_date(path)
        print(f"[exit-sensitivity] {idx}/{len(files)} {date_value}", flush=True)
        picks = _select_top_n(path, args.top_n)
        for pick in picks.to_dict("records"):
            ticker = str(pick["ticker"])
            price_history = _load_price_history(ticker, price_cache)
            reference_price = _safe_float(pick.get("close") or pick.get("Close"))
            for variant in VARIANTS:
                result = _simulate_trade(
                    ticker=ticker,
                    prediction_date=date_value,
                    reference_price=reference_price,
                    variant=variant,
                    price_history=price_history,
                    twii=twii,
                    include_open_mtm=args.include_open_mtm,
                )
                if result is None:
                    continue
                result.update(
                    {
                        "selection_rank": pick.get("selection_rank"),
                        "sector": pick.get("sector"),
                        "recommendation": pick.get("recommendation"),
                    }
                )
                rows.append(result)

    trades = pd.DataFrame(rows)
    summary, daily = _summarize(trades)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    prefix = args.output_prefix
    summary_path = output_dir / f"{prefix}_summary.csv"
    daily_path = output_dir / f"{prefix}_daily.csv"
    trades_path = output_dir / f"{prefix}_trades.csv"
    md_path = output_dir / f"{prefix}.md"
    json_path = output_dir / f"{prefix}.json"
    focus = trades[trades["ticker"].isin(["1305", "3131", "6568"])].sort_values(
        ["ticker", "prediction_date", "variant"]
    )
    metadata = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "requested_start": args.start,
        "requested_end": args.end,
        "actual_start": _prediction_date(files[0]),
        "actual_end": _prediction_date(files[-1]),
        "prediction_file_count": len(files),
        "include_open_mtm": bool(args.include_open_mtm),
        "top_n": args.top_n,
        "summary_csv": str(summary_path),
        "daily_csv": str(daily_path),
        "trades_csv": str(trades_path),
    }
    summary.to_csv(summary_path, index=False, encoding="utf-8-sig")
    daily.to_csv(daily_path, index=False, encoding="utf-8-sig")
    trades.to_csv(trades_path, index=False, encoding="utf-8-sig")
    json_path.write_text(
        json.dumps({"metadata": metadata, "summary": summary.to_dict("records")}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _write_md(md_path, summary=summary, metadata=metadata, focus=focus)
    print(summary.to_string(index=False))
    print(f"[exit-sensitivity] wrote {md_path}")
    return metadata


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run exit policy sensitivity on saved predictions.")
    parser.add_argument("--start", default="2024-01-01")
    parser.add_argument("--end", default="2026-05-08")
    parser.add_argument("--top-n", type=int, default=30)
    parser.add_argument("--include-open-mtm", action="store_true", default=True)
    parser.add_argument("--output-dir", default=REPORT_DIR)
    parser.add_argument("--output-prefix", default="exit_policy_sensitivity_20260508")
    return parser.parse_args()


if __name__ == "__main__":
    run(parse_args())
