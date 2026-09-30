"""REQ-018 CL3 backtest for asymmetric_v2 MA5-entry exit split.

Treatment keeps the same Two-Stage N=75 OOF entries as REQ-013:

- price_vs_ma5_at_entry >= 0: current asymmetric_v2 behavior
- price_vs_ma5_at_entry < 0: stop_only_20d (-10% intraday stop + 20D timeout)

This is a research/CL3 artifact only. Production routing should not change
unless the CL3 checks pass and PM explicitly accepts deployment.
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

from ml.config import REPORT_DIR
from scripts.backtest_exit_v2_cl3 import (
    _load_price_history,
    _load_twii,
    _signal_snapshot,
    _simulate_trade,
    _summarize,
)
from scripts.exit_policies import (
    EXIT_POLICY_ASYMMETRIC_V2,
    EXIT_POLICY_STOP_ONLY_20D,
    EXIT_REASON_MA5_BREAK,
    is_asymmetric_v2_below_ma5_stop_only,
    is_asymmetric_v2_strong_trend,
)

CONTROL_VARIANT = "Control_AsymmetricV2"
TREATMENT_VARIANT = "Treatment_MA5Below_StopOnly"


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


def _entry_price_vs_ma5(price_history: pd.DataFrame | None, prediction_date: str) -> float | None:
    if price_history is None or price_history.empty:
        return None
    future = price_history[price_history["Date"] > pd.Timestamp(prediction_date)].reset_index(drop=True)
    signal_rows = price_history[price_history["Date"] <= pd.Timestamp(prediction_date)].reset_index(drop=True)
    if future.empty or signal_rows.empty:
        return None
    entry_open = _safe_float(future.iloc[0].get("Open"))
    signal_ma5 = _safe_float(signal_rows.iloc[-1].get("MA5"))
    if entry_open is None or signal_ma5 is None or signal_ma5 == 0:
        return None
    return entry_open / signal_ma5 - 1.0


def _bucket(value: float | None) -> str:
    if value is None:
        return "MISSING"
    if value > 0:
        return "A_price_vs_ma5_at_entry_gt_0"
    if value > -0.05:
        return "B_minus5pct_to_0"
    return "C_le_minus5pct"


def _relative_regression(before: float, after: float) -> float:
    if before == 0 or not math.isfinite(before):
        return 0.0
    return max(0.0, before - after) / abs(before)


def _cl3(summary: pd.DataFrame) -> dict[str, Any]:
    control = summary[summary["variant"].eq(CONTROL_VARIANT)].iloc[0]
    treatment = summary[summary["variant"].eq(TREATMENT_VARIANT)].iloc[0]
    alpha_sacrifice = max(0.0, float(control["monthly_avg_return"] - treatment["monthly_avg_return"]))
    mdd_delta = float(treatment["mdd"] - control["mdd"])
    sharpe_regression = _relative_regression(float(control["sharpe"]), float(treatment["sharpe"]))
    calmar_regression = _relative_regression(float(control["calmar"]), float(treatment["calmar"]))
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
    return {"checks": checks, "overall_pass": all(item["pass"] for item in checks.values())}


def _group_comparison(trades: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for bucket, group in trades.groupby("entry_ma5_bucket", sort=False):
        for variant, vgroup in group.groupby("variant", sort=False):
            rows.append(
                {
                    "entry_ma5_bucket": bucket,
                    "variant": variant,
                    "samples": int(len(vgroup)),
                    "avg_hold_days": float(vgroup["hold_days"].mean()),
                    "avg_realized_return": float(vgroup["return_pct"].mean()),
                    "win_rate": float((vgroup["return_pct"] > 0).mean()),
                    "ma5_break_exit_pct": float(vgroup["exit_reason"].eq(EXIT_REASON_MA5_BREAK).mean()),
                    "stop_only_route_pct": float(vgroup["stop_only_route"].mean()),
                    "stop_exit_pct": float(vgroup["exit_reason"].astype(str).str.contains("STOP|GAP_STOP", regex=True).mean()),
                    "timeout_pct": float(vgroup["exit_reason"].eq("TIME_20D").mean()),
                }
            )
    return pd.DataFrame(rows)


def _write_md(
    path: Path,
    *,
    summary: pd.DataFrame,
    group_stats: pd.DataFrame,
    cl3: dict[str, Any],
    metadata: dict[str, Any],
) -> None:
    lines = [
        "# REQ-018 MA5 Entry Exit Split CL3 Backtest",
        "",
        f"- Generated at: `{metadata['generated_at']}`",
        f"- Fold artifact: `{metadata['fold_artifact']}`",
        f"- Window: `{metadata['actual_start']}` to `{metadata['actual_end']}`",
        "- Treatment rule: `price_vs_ma5_at_entry < 0` routes to `stop_only_20d`; otherwise current `asymmetric_v2`.",
        f"- Simulated trades: `{metadata['simulated_trades']}`; skipped entries: `{metadata['skipped_entries']}`",
        "",
        "## CL3 Summary",
        "",
        "| Variant | Monthly return | Monthly alpha | MDD | Sharpe | Calmar | Avg hold days | MA exits | HWM exits | Stops | 20D timeout | Stop-only routes |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in summary.to_dict("records"):
        lines.append(
            f"| {row['variant']} | {_fmt_pct(row['monthly_avg_return'])} | {_fmt_pct(row['monthly_avg_alpha'])} | "
            f"{_fmt_pct(row['mdd'])} | {row['sharpe']:.3f} | {row['calmar']:.3f} | {row['avg_hold_days']:.2f} | "
            f"{row['ma_exit_count']} | {row['hwm_exit_count']} | {row['stop_exit_count']} | "
            f"{row['timeout_count']} | {row.get('stop_only_route_count', 0)} |"
        )
    lines.extend(["", "## Acceptance Checks", "", "| Check | Value | Threshold | Pass |", "|---|---:|---:|:---:|"])
    for name, item in cl3["checks"].items():
        lines.append(f"| {name} | {_fmt_pct(item['value'])} | {_fmt_pct(item['threshold'])} | {'PASS' if item['pass'] else 'FAIL'} |")
    lines.extend(["", f"**Overall:** `{'PASS' if cl3['overall_pass'] else 'FAIL'}`", "", "## Group B/C Comparison", "", "| Group | Variant | Samples | Avg hold days | Avg return | Win rate | MA5 exit % | Stop-only route % | Stop exit % | 20D timeout % |", "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|"])
    for row in group_stats.to_dict("records"):
        if row["entry_ma5_bucket"].startswith("A_"):
            continue
        lines.append(
            f"| {row['entry_ma5_bucket']} | {row['variant']} | {row['samples']} | "
            f"{row['avg_hold_days']:.2f} | {_fmt_pct(row['avg_realized_return'])} | "
            f"{_fmt_pct(row['win_rate'])} | {_fmt_pct(row['ma5_break_exit_pct'])} | "
            f"{_fmt_pct(row['stop_only_route_pct'])} | {_fmt_pct(row['stop_exit_pct'])} | "
            f"{_fmt_pct(row['timeout_pct'])} |"
        )
    lines.extend([
        "",
        "## Notes",
        "",
        "- This is a same-snapshot A/B over the Two-Stage N=75 OOF fold artifact.",
        "- Treatment changes exit routing only; entries and allocation count are unchanged.",
        "- Production deployment is blocked unless all CL3 checks pass.",
    ])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run(args: argparse.Namespace) -> dict[str, Any]:
    fold = pd.read_csv(args.fold_artifact, dtype={"ticker": str})
    fold["month"] = fold["month"].astype(str)
    fold = fold[(fold["month"] >= args.start_month) & (fold["month"] <= args.end_month)].copy()
    if fold.empty:
        raise ValueError("No fold rows in requested window")

    twii = _load_twii()
    price_cache: dict[str, pd.DataFrame | None] = {}
    rows: list[dict[str, Any]] = []
    skipped_entries = 0

    for fold_row in fold.sort_values(["month", "rank"]).to_dict("records"):
        ticker = str(fold_row["ticker"]).zfill(4)
        prediction_date = str(fold_row.get("date") or fold_row.get("Date"))
        rank = int(fold_row["rank"])
        price_history = _load_price_history(ticker, price_cache)
        _, price_vs_ma20 = _signal_snapshot(price_history, prediction_date) if price_history is not None else (None, None)
        price_vs_ma5 = _entry_price_vs_ma5(price_history, prediction_date)
        entry_bucket = _bucket(price_vs_ma5)
        strong_trend = is_asymmetric_v2_strong_trend(rank, price_vs_ma20)
        stop_only_route = is_asymmetric_v2_below_ma5_stop_only(price_vs_ma5)

        control_trade = _simulate_trade(
            ticker=ticker,
            prediction_date=prediction_date,
            month=str(fold_row["month"]),
            rank=rank,
            variant=CONTROL_VARIANT,
            policy_name=EXIT_POLICY_ASYMMETRIC_V2,
            disable_ma_break_for_position=strong_trend,
            price_vs_ma20=price_vs_ma20,
            price_history=price_history,
            twii=twii,
        )
        treatment_trade = _simulate_trade(
            ticker=ticker,
            prediction_date=prediction_date,
            month=str(fold_row["month"]),
            rank=rank,
            variant=TREATMENT_VARIANT,
            policy_name=EXIT_POLICY_STOP_ONLY_20D if stop_only_route else EXIT_POLICY_ASYMMETRIC_V2,
            disable_ma_break_for_position=False if stop_only_route else strong_trend,
            price_vs_ma20=price_vs_ma20,
            price_history=price_history,
            twii=twii,
        )
        if control_trade is None or treatment_trade is None:
            skipped_entries += 1
            continue
        for trade, route in [(control_trade, False), (treatment_trade, stop_only_route)]:
            trade["price_vs_ma5_at_entry"] = price_vs_ma5
            trade["entry_ma5_bucket"] = entry_bucket
            trade["stop_only_route"] = bool(route)
        rows.extend([control_trade, treatment_trade])

    trades = pd.DataFrame(rows)
    if trades.empty:
        raise ValueError("No simulated trades were produced")
    summary, monthly = _summarize(trades)
    route_counts = trades.groupby("variant")["stop_only_route"].sum().to_dict()
    summary["stop_only_route_count"] = summary["variant"].map(route_counts).fillna(0).astype(int)
    group_stats = _group_comparison(trades)
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
        "fold_artifact": args.fold_artifact,
        "actual_start": str(fold["month"].min()),
        "actual_end": str(fold["month"].max()),
        "months": int(fold["month"].nunique()),
        "entries": int(len(fold)),
        "simulated_trades": int(len(trades)),
        "skipped_entries": int(skipped_entries),
        "trades_csv": str(trades_path),
        "monthly_csv": str(monthly_path),
        "summary_csv": str(summary_path),
        "groups_csv": str(group_path),
    }
    payload = {
        "metadata": metadata,
        "summary": summary.to_dict("records"),
        "groups": group_stats.to_dict("records"),
        "cl3": cl3,
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_md(md_path, summary=summary, group_stats=group_stats, cl3=cl3, metadata=metadata)
    print(summary.to_string(index=False))
    print(f"[ma5-exit-split-cl3] overall={'PASS' if cl3['overall_pass'] else 'FAIL'} wrote {md_path}")
    return payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run REQ-018 MA5 entry exit split CL3 backtest.")
    parser.add_argument(
        "--fold-artifact",
        default=str(Path(REPORT_DIR) / "audit002_step5_grid_search_20260509_N75_bins5_fold_top30.csv"),
    )
    parser.add_argument("--start-month", default="2024-01")
    parser.add_argument("--end-month", default="2026-04")
    parser.add_argument("--output-dir", default=REPORT_DIR)
    parser.add_argument("--output-prefix", default="ma5_exit_split_cl3_backtest_20260509")
    return parser.parse_args()


if __name__ == "__main__":
    run(parse_args())
