"""CL3 backtest for StopOnly production exit promotion.

This is the closure artifact for RD-2026-0508 W1. It compares the original
Champion exit policy with the current StopOnly policy on the same regenerated
walk-forward fold Top30 entry snapshots:

- Baseline: ma5_break_plus_hwm_8pct, with legacy MA-break exits explicitly on.
- Test: stop_only_20d, with only production 10% intraday stop and 20D timeout.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))

from ml.config import DAILY_K_DIR, INDEX_DIR, REPORT_DIR  # noqa: E402
from scripts.exit_policies import (  # noqa: E402
    EXIT_POLICY_MA5_BREAK_PLUS_HWM_8PCT,
    EXIT_POLICY_STOP_ONLY_20D,
    ExitManager,
)
from scripts.order_simulation import (  # noqa: E402
    FILL_STATUS_FILLED,
    EXIT_REASON_TIME_20D,
    simulate_20d_entry,
    simulate_intraday_stop,
)

VARIANTS = {
    "Baseline_MA5_HWM8": {
        "policy": EXIT_POLICY_MA5_BREAK_PLUS_HWM_8PCT,
        "enable_ma_break_exits": True,
    },
    "Test_StopOnly": {
        "policy": EXIT_POLICY_STOP_ONLY_20D,
        "enable_ma_break_exits": False,
    },
}


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
        for column in ["Open", "High", "Low", "Close"]:
            df[column] = pd.to_numeric(df[column], errors="coerce")
        df = df.dropna(subset=["Date", "Open", "High", "Low", "Close"])
        df = df.sort_values("Date").reset_index(drop=True)
        df["MA5"] = df["Close"].rolling(window=5, min_periods=5).mean()
        df["MA10"] = df["Close"].rolling(window=10, min_periods=10).mean()
        df["MA20"] = df["Close"].rolling(window=20, min_periods=20).mean()
    except Exception:
        df = None
    cache[ticker] = df
    return df


def _load_twii() -> pd.DataFrame:
    path = Path(INDEX_DIR) / "index_TWII.csv"
    twii = pd.read_csv(path, dtype={"Date": str})
    twii["Date"] = pd.to_datetime(twii["Date"], errors="coerce")
    for column in ["Open", "Close"]:
        twii[column] = pd.to_numeric(twii[column], errors="coerce")
    return twii.dropna(subset=["Date", "Open", "Close"]).sort_values("Date").reset_index(drop=True)


def _twii_return(twii: pd.DataFrame, entry_date: str, exit_date: str) -> float | None:
    rows = twii[(twii["Date"] >= pd.Timestamp(entry_date)) & (twii["Date"] <= pd.Timestamp(exit_date))]
    if rows.empty:
        return None
    entry_open = _safe_float(rows.iloc[0]["Open"])
    exit_close = _safe_float(rows.iloc[-1]["Close"])
    if entry_open is None or entry_open <= 0 or exit_close is None:
        return None
    return exit_close / entry_open - 1.0


def _signal_close(price_history: pd.DataFrame, prediction_date: str) -> float | None:
    rows = price_history[price_history["Date"] <= pd.Timestamp(prediction_date)]
    if rows.empty:
        return None
    return _safe_float(rows.iloc[-1]["Close"])


def _simulate_trade(
    *,
    ticker: str,
    prediction_date: str,
    month: str,
    rank: int,
    policy_name: str,
    variant: str,
    enable_ma_break_exits: bool,
    price_history: pd.DataFrame | None,
    twii: pd.DataFrame,
) -> dict[str, Any] | None:
    if price_history is None or price_history.empty:
        return None
    reference_price = _signal_close(price_history, prediction_date)
    future = price_history[price_history["Date"] > pd.Timestamp(prediction_date)].reset_index(drop=True)
    if future.empty:
        return None

    entry_row = future.iloc[0]
    entry = simulate_20d_entry(
        reference_price=reference_price,
        open_price=entry_row["Open"],
        high_price=entry_row["High"],
    )
    if entry.fill_status != FILL_STATUS_FILLED or entry.fill_price is None:
        return None

    entry_price = float(entry.fill_price)
    entry_date = entry_row["Date"].date().isoformat()
    window = future.head(20).copy()
    exit_manager = ExitManager(
        policy_name,
        entry_price=entry_price,
        enable_ma_break_exits=enable_ma_break_exits,
    )

    min_drawdown = 0.0
    exit_price: float | None = None
    exit_date: str | None = None
    exit_reason: str | None = None
    hold_days: int | None = None
    pending_trigger_date: str | None = None

    for day_number, (_, mark_row) in enumerate(window.iterrows(), start=1):
        mark_date = mark_row["Date"].date().isoformat()
        policy_exit = exit_manager.before_open(mark_row)

        if policy_exit is not None:
            open_price = _safe_float(mark_row["Open"])
            if open_price is None:
                return None
            min_drawdown = min(min_drawdown, open_price / entry_price - 1.0)
            exit_price = float(policy_exit.exit_price)
            exit_date = mark_date
            exit_reason = policy_exit.exit_reason
            hold_days = day_number
            pending_trigger_date = policy_exit.trigger_date
            break

        low_price = _safe_float(mark_row["Low"])
        if low_price is not None:
            min_drawdown = min(min_drawdown, low_price / entry_price - 1.0)

        stop = simulate_intraday_stop(entry_price, mark_row["Open"], mark_row["Low"])
        if stop is not None:
            exit_price = float(stop.exit_price)
            exit_date = mark_date
            exit_reason = stop.exit_reason
            hold_days = day_number
            break

        if day_number == 20 and len(window) >= 20:
            close_price = _safe_float(mark_row["Close"])
            if close_price is None:
                return None
            exit_price = close_price
            exit_date = mark_date
            exit_reason = EXIT_REASON_TIME_20D
            hold_days = day_number
            break

        if day_number < 20:
            exit_manager.after_close(mark_row)

    if exit_price is None or exit_date is None or hold_days is None:
        return None

    realized = exit_price / entry_price - 1.0
    twii_ret = _twii_return(twii, entry_date, exit_date)
    return {
        "variant": variant,
        "policy": policy_name,
        "month": month,
        "ticker": ticker,
        "rank": rank,
        "prediction_date": prediction_date,
        "entry_date": entry_date,
        "exit_date": exit_date,
        "entry_price": entry_price,
        "exit_price": exit_price,
        "return_pct": realized,
        "twii_return_pct": twii_ret,
        "alpha_pct": realized - twii_ret if twii_ret is not None else None,
        "hold_days": hold_days,
        "exit_reason": exit_reason,
        "trigger_date": pending_trigger_date,
        "max_drawdown_pct": min_drawdown,
    }


def _max_drawdown(returns: pd.Series) -> float:
    if returns.empty:
        return 0.0
    equity = (1.0 + returns.fillna(0.0)).cumprod()
    drawdown = equity / equity.cummax() - 1.0
    return float(drawdown.min())


def _sharpe(monthly_returns: pd.Series) -> float:
    if monthly_returns.empty:
        return 0.0
    std = float(monthly_returns.std(ddof=1))
    if std <= 0 or not math.isfinite(std):
        return 0.0
    return float(monthly_returns.mean() / std * math.sqrt(12.0))


def _calmar(monthly_returns: pd.Series, mdd: float) -> float:
    if monthly_returns.empty or mdd >= 0:
        return 0.0
    annualized = float((1.0 + monthly_returns.mean()) ** 12 - 1.0)
    return annualized / abs(mdd)


def _summarize(trades: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    monthly = (
        trades.groupby(["variant", "month"], as_index=False)
        .agg(
            basket_return=("return_pct", "mean"),
            basket_alpha=("alpha_pct", "mean"),
            avg_hold_days=("hold_days", "mean"),
            trades=("ticker", "count"),
        )
        .sort_values(["variant", "month"])
    )
    rows: list[dict[str, Any]] = []
    for variant, group in monthly.groupby("variant", sort=False):
        trade_group = trades[trades["variant"] == variant]
        returns = group["basket_return"]
        mdd = _max_drawdown(returns)
        rows.append(
            {
                "variant": variant,
                "months": int(group["month"].nunique()),
                "trades": int(len(trade_group)),
                "monthly_avg_return": float(returns.mean()),
                "monthly_avg_alpha": float(group["basket_alpha"].mean()),
                "compound_return": float((1.0 + returns).prod() - 1.0),
                "mdd": mdd,
                "sharpe": _sharpe(returns),
                "calmar": _calmar(returns, mdd),
                "avg_hold_days": float(trade_group["hold_days"].mean()),
                "ma_exit_count": int(trade_group["exit_reason"].astype(str).str.contains("MA").sum()),
                "hwm_exit_count": int(trade_group["exit_reason"].astype(str).str.contains("HWM").sum()),
                "stop_exit_count": int(trade_group["exit_reason"].astype(str).str.contains("STOP|GAP_STOP", regex=True).sum()),
                "timeout_count": int((trade_group["exit_reason"] == EXIT_REASON_TIME_20D).sum()),
            }
        )
    return pd.DataFrame(rows), monthly


def _cl3(summary: pd.DataFrame) -> dict[str, Any]:
    baseline = summary[summary["variant"] == "Baseline_MA5_HWM8"].iloc[0]
    test = summary[summary["variant"] == "Test_StopOnly"].iloc[0]
    alpha_sacrifice = max(0.0, float(baseline["monthly_avg_return"] - test["monthly_avg_return"]))
    mdd_delta = float(test["mdd"] - baseline["mdd"])
    sharpe_regression = (
        max(0.0, float(baseline["sharpe"] - test["sharpe"])) / abs(float(baseline["sharpe"]))
        if baseline["sharpe"] not in {0, np.nan}
        else 0.0
    )
    calmar_regression = (
        max(0.0, float(baseline["calmar"] - test["calmar"])) / abs(float(baseline["calmar"]))
        if baseline["calmar"] not in {0, np.nan}
        else 0.0
    )
    checks = {
        "alpha_sacrifice": {
            "value": alpha_sacrifice,
            "threshold": 0.005,
            "pass": alpha_sacrifice <= 0.005,
        },
        "mdd_delta": {
            "value": mdd_delta,
            "threshold": 0.001,
            "pass": mdd_delta >= 0.001,
        },
        "sharpe_regression": {
            "value": sharpe_regression,
            "threshold": 0.05,
            "pass": sharpe_regression <= 0.05,
        },
        "calmar_regression": {
            "value": calmar_regression,
            "threshold": 0.05,
            "pass": calmar_regression <= 0.05,
        },
    }
    return {
        "checks": checks,
        "overall_pass": all(item["pass"] for item in checks.values()),
    }


def _write_md(path: Path, *, summary: pd.DataFrame, cl3: dict[str, Any], metadata: dict[str, Any]) -> None:
    lines = [
        "# W1 StopOnly CL3 Backtest",
        "",
        f"- Generated at: `{metadata['generated_at']}`",
        f"- Fold artifact: `{metadata['fold_artifact']}`",
        f"- Window: `{metadata['actual_start']}` to `{metadata['actual_end']}`",
        f"- Entry snapshots: `{metadata['months']}` months x Top30",
        "",
        "## Performance",
        "",
        "| Variant | Monthly ret | Alpha | MDD | Sharpe | Calmar | Avg hold | MA exits | HWM exits | Stop exits | 20D |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in summary.to_dict("records"):
        lines.append(
            f"| {row['variant']} | {_fmt_pct(row['monthly_avg_return'])} | {_fmt_pct(row['monthly_avg_alpha'])} | "
            f"{_fmt_pct(row['mdd'])} | {row['sharpe']:.3f} | {row['calmar']:.3f} | {row['avg_hold_days']:.2f} | "
            f"{int(row['ma_exit_count'])} | {int(row['hwm_exit_count'])} | {int(row['stop_exit_count'])} | {int(row['timeout_count'])} |"
        )
    lines.extend(["", "## CL3", "", "| Check | Value | Threshold | Pass |", "|---|---:|---:|---|"])
    for name, item in cl3["checks"].items():
        value = item["value"]
        threshold = item["threshold"]
        lines.append(
            f"| {name} | {_fmt_pct(value) if 'regression' not in name else _fmt_pct(value)} | "
            f"{_fmt_pct(threshold)} | {'yes' if item['pass'] else 'no'} |"
        )
    lines.extend(
        [
            "",
            f"Overall: {'PASS' if cl3['overall_pass'] else 'FAIL'}",
            "",
            "## Notes",
            "",
            "- Baseline explicitly enables legacy MA-break exits even though production now defaults them off.",
            "- Both branches share identical regenerated walk-forward fold Top30 entry snapshots.",
            "- Stop-loss simulation uses production `simulate_intraday_stop` behavior.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run(args: argparse.Namespace) -> dict[str, Any]:
    fold = pd.read_csv(args.fold_artifact, dtype={"ticker": str})
    fold = fold[(fold["month"] >= args.start_month) & (fold["month"] <= args.end_month)].copy()
    if fold.empty:
        raise ValueError("No fold rows in requested window")

    twii = _load_twii()
    price_cache: dict[str, pd.DataFrame | None] = {}
    rows: list[dict[str, Any]] = []
    for row in fold.sort_values(["month", "rank"]).to_dict("records"):
        ticker = str(row["ticker"])
        price_history = _load_price_history(ticker, price_cache)
        prediction_date = str(row["date"])
        for variant, config in VARIANTS.items():
            result = _simulate_trade(
                ticker=ticker,
                prediction_date=prediction_date,
                month=str(row["month"]),
                rank=int(row["rank"]),
                policy_name=str(config["policy"]),
                variant=variant,
                enable_ma_break_exits=bool(config["enable_ma_break_exits"]),
                price_history=price_history,
                twii=twii,
            )
            if result is not None:
                rows.append(result)

    trades = pd.DataFrame(rows)
    summary, monthly = _summarize(trades)
    cl3 = _cl3(summary)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    prefix = args.output_prefix
    trades_path = output_dir / f"{prefix}_trades.csv"
    monthly_path = output_dir / f"{prefix}_monthly.csv"
    summary_path = output_dir / f"{prefix}_summary.csv"
    json_path = output_dir / f"{prefix}.json"
    md_path = output_dir / f"{prefix}.md"

    trades.to_csv(trades_path, index=False, encoding="utf-8-sig")
    monthly.to_csv(monthly_path, index=False, encoding="utf-8-sig")
    summary.to_csv(summary_path, index=False, encoding="utf-8-sig")
    metadata = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "fold_artifact": args.fold_artifact,
        "actual_start": str(fold["month"].min()),
        "actual_end": str(fold["month"].max()),
        "months": int(fold["month"].nunique()),
        "entries": int(len(fold)),
        "trades_csv": str(trades_path),
        "monthly_csv": str(monthly_path),
        "summary_csv": str(summary_path),
    }
    payload = {
        "metadata": metadata,
        "summary": summary.to_dict("records"),
        "cl3": cl3,
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_md(md_path, summary=summary, cl3=cl3, metadata=metadata)
    print(summary.to_string(index=False))
    print(f"[stoponly-cl3] overall={'PASS' if cl3['overall_pass'] else 'FAIL'} wrote {md_path}")
    return payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run StopOnly CL3 backtest on fold Top30 entries.")
    parser.add_argument("--fold-artifact", default=str(Path(REPORT_DIR) / "v2_fold_top30_20260508_200128.csv"))
    parser.add_argument("--start-month", default="2024-01")
    parser.add_argument("--end-month", default="2026-04")
    parser.add_argument("--output-dir", default=REPORT_DIR)
    parser.add_argument("--output-prefix", default="stoponly_cl3_backtest_20260508")
    return parser.parse_args()


if __name__ == "__main__":
    run(parse_args())
