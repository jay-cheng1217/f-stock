"""REQ-016 CL3 backtest for the MA5 entry-quality hard gate.

Control and treatment use the same Two-Stage N=75 OOF entry snapshots and the
same asymmetric_v2 exit simulation artifact. Treatment removes positions whose
actual entry-open price is below the previous trading day's MA5 threshold.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ml.config import DAILY_K_DIR, REPORT_DIR
from ml.thresholds import ENTRY_MA5_MIN_PCT

CONTROL_VARIANT = "Control_AsymmetricV2"
TREATMENT_VARIANT = "Treatment_MA5EntryGuard"


def _safe_float(value: Any) -> float | None:
    try:
        number = float(value)
    except Exception:
        return None
    if not math.isfinite(number):
        return None
    return number


def _fmt_pct(value: Any) -> str:
    number = _safe_float(value)
    if number is None:
        return "n/a"
    return f"{number * 100:.2f}%"


def _normalize_ticker(value: Any) -> str:
    ticker = str(value).strip().upper()
    if ticker.isdigit() and len(ticker) < 4:
        return ticker.zfill(4)
    return ticker


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
    df["Open"] = pd.to_numeric(df["Open"], errors="coerce")
    df["Close"] = pd.to_numeric(df["Close"], errors="coerce")
    if "MA_5" in df.columns:
        df["MA5"] = pd.to_numeric(df["MA_5"], errors="coerce")
        df["MA5"] = df["MA5"].fillna(df["Close"].rolling(window=5).mean())
    else:
        df["MA5"] = df["Close"].rolling(window=5).mean()
    cache[ticker] = df
    return df


def _entry_price_vs_ma5(row: pd.Series, cache: dict[str, pd.DataFrame | None]) -> float | None:
    prices = _load_price_history(str(row["ticker"]), cache)
    if prices is None or prices.empty:
        return None
    entry_date = pd.Timestamp(str(row["entry_date"]))
    before = prices.loc[prices["Date"] < entry_date]
    entry_rows = prices.loc[prices["Date"].eq(entry_date)]
    if before.empty or entry_rows.empty:
        return None
    ma5 = _safe_float(before.iloc[-1].get("MA5"))
    entry_open = _safe_float(entry_rows.iloc[0].get("Open"))
    if ma5 is None or ma5 == 0 or entry_open is None:
        return None
    return entry_open / ma5 - 1.0


def _bucket(value: float | None) -> str:
    if value is None:
        return "MISSING"
    if value > 0:
        return "A_price_vs_ma5_at_entry_gt_0"
    if value > -0.05:
        return "B_minus5pct_to_0"
    return "C_le_minus5pct"


def _max_drawdown(returns: pd.Series) -> float:
    if returns.empty:
        return 0.0
    equity = (1.0 + returns.fillna(0.0)).cumprod()
    drawdown = equity / equity.cummax() - 1.0
    return float(drawdown.min())


def _summarize_monthly(monthly: pd.DataFrame, variant: str) -> dict[str, Any]:
    returns = pd.to_numeric(monthly["monthly_return"], errors="coerce").fillna(0.0)
    mean_return = float(returns.mean())
    std = float(returns.std(ddof=1)) if len(returns) > 1 else 0.0
    sharpe = mean_return / std * math.sqrt(12) if std > 0 else 0.0
    mdd = _max_drawdown(returns)
    annual_return = (1.0 + mean_return) ** 12 - 1.0
    calmar = annual_return / abs(mdd) if mdd < 0 else 0.0
    return {
        "variant": variant,
        "monthly_avg_return": mean_return,
        "monthly_avg_alpha": float(pd.to_numeric(monthly["monthly_alpha"], errors="coerce").mean()),
        "mdd": mdd,
        "sharpe": sharpe,
        "calmar": calmar,
        "avg_hold_days": float(pd.to_numeric(monthly["avg_hold_days"], errors="coerce").mean()),
        "trades": int(monthly["trades"].sum()),
        "removed_trades": int(monthly.get("removed_trades", pd.Series(0, index=monthly.index)).sum()),
    }


def _relative_regression(before: float, after: float) -> float:
    if before == 0 or not math.isfinite(before):
        return 0.0
    return max(0.0, before - after) / abs(before)


def _cl3(summary: pd.DataFrame) -> dict[str, Any]:
    control = summary[summary["variant"].eq(CONTROL_VARIANT)].iloc[0]
    treatment = summary[summary["variant"].eq(TREATMENT_VARIANT)].iloc[0]
    alpha_sacrifice = max(0.0, float(control["monthly_avg_return"] - treatment["monthly_avg_return"]))
    mdd_delta = float(treatment["mdd"] - control["mdd"])
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
            "value": _relative_regression(float(control["sharpe"]), float(treatment["sharpe"])),
            "threshold": 0.05,
            "pass": _relative_regression(float(control["sharpe"]), float(treatment["sharpe"])) <= 0.05,
        },
        "calmar_regression": {
            "value": _relative_regression(float(control["calmar"]), float(treatment["calmar"])),
            "threshold": 0.05,
            "pass": _relative_regression(float(control["calmar"]), float(treatment["calmar"])) <= 0.05,
        },
    }
    return {"checks": checks, "overall_pass": all(item["pass"] for item in checks.values())}


def _monthly_tables(trades: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    base = (
        trades.groupby("month", as_index=False)
        .agg(
            monthly_return=("return_pct", "mean"),
            monthly_alpha=("alpha_pct", "mean"),
            avg_hold_days=("hold_days", "mean"),
            trades=("ticker", "count"),
        )
        .assign(variant=CONTROL_VARIANT, removed_trades=0)
    )
    kept = trades.loc[trades["ma5_entry_guard_keep"]].copy()
    treatment = (
        kept.groupby("month", as_index=False)
        .agg(
            monthly_return=("return_pct", "mean"),
            monthly_alpha=("alpha_pct", "mean"),
            avg_hold_days=("hold_days", "mean"),
            trades=("ticker", "count"),
        )
        .assign(variant=TREATMENT_VARIANT)
    )
    months = pd.DataFrame({"month": sorted(trades["month"].astype(str).unique())})
    treatment = months.merge(treatment, on="month", how="left")
    treatment["variant"] = TREATMENT_VARIANT
    for col in ["monthly_return", "monthly_alpha", "avg_hold_days", "trades"]:
        treatment[col] = treatment[col].fillna(0)
    removed = (
        trades.loc[~trades["ma5_entry_guard_keep"]].groupby("month").size().rename("removed_trades").reset_index()
    )
    treatment = treatment.merge(removed, on="month", how="left")
    treatment["removed_trades"] = treatment["removed_trades"].fillna(0).astype(int)
    monthly = pd.concat([base, treatment], ignore_index=True)
    summary = pd.DataFrame([_summarize_monthly(base, CONTROL_VARIANT), _summarize_monthly(treatment, TREATMENT_VARIANT)])
    return summary, monthly, kept


def _group_stats(trades: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for bucket, group in trades.groupby("entry_ma5_bucket", sort=False):
        kept = group.loc[group["ma5_entry_guard_keep"]]
        removed = group.loc[~group["ma5_entry_guard_keep"]]
        rows.append(
            {
                "entry_ma5_bucket": bucket,
                "samples": int(len(group)),
                "kept": int(len(kept)),
                "removed": int(len(removed)),
                "removed_pct": len(removed) / len(group) if len(group) else 0.0,
                "baseline_avg_return": float(group["return_pct"].mean()),
                "kept_avg_return": float(kept["return_pct"].mean()) if not kept.empty else None,
                "removed_avg_return": float(removed["return_pct"].mean()) if not removed.empty else None,
                "ma5_break_exit_pct": float(group["exit_reason"].astype(str).eq("MA5_BREAK").mean()),
            }
        )
    return pd.DataFrame(rows)


def _write_md(path: Path, *, summary: pd.DataFrame, group_stats: pd.DataFrame, cl3: dict[str, Any], metadata: dict[str, Any]) -> None:
    lines = [
        "# REQ-016 MA5 Entry Guard CL3 Backtest",
        "",
        f"- Generated at: `{metadata['generated_at']}`",
        f"- Source trades: `{metadata['source_trades']}`",
        f"- Threshold: `price_vs_ma5_at_entry >= {metadata['threshold']:.2%}`",
        f"- Window: `{metadata['actual_start']}` to `{metadata['actual_end']}`",
        f"- Simulated baseline trades: `{metadata['baseline_trades']}`; removed by guard: `{metadata['removed_trades']}`",
        "",
        "## CL3 Summary",
        "",
        "| Variant | Monthly return | Monthly alpha | MDD | Sharpe | Calmar | Avg hold days | Trades | Removed |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in summary.to_dict("records"):
        lines.append(
            f"| {row['variant']} | {_fmt_pct(row['monthly_avg_return'])} | {_fmt_pct(row['monthly_avg_alpha'])} | "
            f"{_fmt_pct(row['mdd'])} | {row['sharpe']:.3f} | {row['calmar']:.3f} | "
            f"{row['avg_hold_days']:.2f} | {int(row['trades'])} | {int(row['removed_trades'])} |"
        )
    lines.extend(["", "## Acceptance Checks", "", "| Check | Value | Threshold | Pass |", "|---|---:|---:|:---:|"])
    for name, item in cl3["checks"].items():
        lines.append(f"| {name} | {_fmt_pct(item['value'])} | {_fmt_pct(item['threshold'])} | {'PASS' if item['pass'] else 'FAIL'} |")
    lines.extend(["", f"**Overall:** `{'PASS' if cl3['overall_pass'] else 'FAIL'}`", "", "## Entry MA5 Group Analysis", "", "| Group | Samples | Kept | Removed | Removed % | Baseline avg return | Kept avg return | Removed avg return | MA5_BREAK exit % |", "|---|---:|---:|---:|---:|---:|---:|---:|---:|"])
    for row in group_stats.to_dict("records"):
        lines.append(
            f"| {row['entry_ma5_bucket']} | {row['samples']} | {row['kept']} | {row['removed']} | "
            f"{_fmt_pct(row['removed_pct'])} | {_fmt_pct(row['baseline_avg_return'])} | "
            f"{_fmt_pct(row['kept_avg_return'])} | {_fmt_pct(row['removed_avg_return'])} | "
            f"{_fmt_pct(row['ma5_break_exit_pct'])} |"
        )
    lines.extend([
        "",
        "## Notes",
        "",
        "- This is a same-snapshot A/B over the existing REQ-013 asymmetric_v2 OOF trade artifact.",
        "- Treatment removes weak MA5 entries and re-normalizes among surviving names within each month.",
        "- If a month has no surviving names, that month is treated as cash return 0.",
    ])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run(args: argparse.Namespace) -> dict[str, Any]:
    trades = pd.read_csv(args.trades_csv, dtype={"ticker": str})
    trades = trades.loc[trades["variant"].eq(args.variant)].copy()
    trades["month"] = trades["month"].astype(str)
    trades = trades.loc[(trades["month"] >= args.start_month) & (trades["month"] <= args.end_month)].copy()
    if trades.empty:
        raise ValueError("No trades in requested window")
    cache: dict[str, pd.DataFrame | None] = {}
    trades["price_vs_ma5_at_entry"] = trades.apply(lambda row: _entry_price_vs_ma5(row, cache), axis=1)
    trades["entry_ma5_bucket"] = trades["price_vs_ma5_at_entry"].map(_bucket)
    threshold = float(args.threshold)
    trades["ma5_entry_guard_keep"] = pd.to_numeric(trades["price_vs_ma5_at_entry"], errors="coerce").ge(threshold)

    summary, monthly, _ = _monthly_tables(trades)
    group_stats = _group_stats(trades)
    cl3 = _cl3(summary)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    prefix = args.output_prefix
    trades_path = output_dir / f"{prefix}_trades.csv"
    monthly_path = output_dir / f"{prefix}_monthly.csv"
    summary_path = output_dir / f"{prefix}_summary.csv"
    group_path = output_dir / f"{prefix}_groups.csv"
    json_path = output_dir / f"{prefix}.json"
    md_path = output_dir / f"{prefix}.md"

    trades.to_csv(trades_path, index=False, encoding="utf-8-sig")
    monthly.to_csv(monthly_path, index=False, encoding="utf-8-sig")
    summary.to_csv(summary_path, index=False, encoding="utf-8-sig")
    group_stats.to_csv(group_path, index=False, encoding="utf-8-sig")
    metadata = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_trades": args.trades_csv,
        "actual_start": str(trades["month"].min()),
        "actual_end": str(trades["month"].max()),
        "threshold": threshold,
        "baseline_trades": int(len(trades)),
        "removed_trades": int((~trades["ma5_entry_guard_keep"]).sum()),
        "summary_csv": str(summary_path),
        "monthly_csv": str(monthly_path),
        "trades_csv": str(trades_path),
        "groups_csv": str(group_path),
    }
    payload = {"metadata": metadata, "summary": summary.to_dict("records"), "groups": group_stats.to_dict("records"), "cl3": cl3}
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_md(md_path, summary=summary, group_stats=group_stats, cl3=cl3, metadata=metadata)
    print(summary.to_string(index=False))
    print(f"[ma5-entry-cl3] overall={'PASS' if cl3['overall_pass'] else 'FAIL'} wrote {md_path}")
    return payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run REQ-016 MA5 entry guard CL3 backtest.")
    parser.add_argument("--trades-csv", default=str(Path(REPORT_DIR) / "exit_v2_cl3_backtest_20260509_trades.csv"))
    parser.add_argument("--variant", default="Treatment_AsymmetricV2")
    parser.add_argument("--threshold", type=float, default=ENTRY_MA5_MIN_PCT)
    parser.add_argument("--start-month", default="2024-01")
    parser.add_argument("--end-month", default="2026-04")
    parser.add_argument("--output-dir", default=REPORT_DIR)
    parser.add_argument("--output-prefix", default="ma5_entry_guard_cl3_backtest_20260509")
    return parser.parse_args()


if __name__ == "__main__":
    run(parse_args())
