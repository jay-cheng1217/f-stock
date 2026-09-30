"""REQ-026 large-cap flow sector-cap and foreign-sell relaxation test.

Runs a narrow A/B on top of REQ-025:
- Control: sector cap 2 names, foreign sell exits after 1 day.
- Treatment: sector cap 4 names, foreign sell exits after 2 consecutive days.

This is research-only and does not modify production portfolio logic.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ml.config import REPORT_DIR
from scripts.req025_large_cap_flow_portfolio_backtest import _fmt_pct, _summary, run_backtest


def _make_args(
    base: argparse.Namespace,
    *,
    max_per_sector: int,
    foreign_sell_days: int,
) -> SimpleNamespace:
    return SimpleNamespace(
        start=base.start,
        end=base.replay_end,
        max_positions=base.max_positions,
        max_per_sector=max_per_sector,
        foreign_sell_days=foreign_sell_days,
        hold_days=base.hold_days,
        hwm_drawdown=base.hwm_drawdown,
        output_prefix="",
    )


def _summary_delta(control: dict[str, Any], treatment: dict[str, Any]) -> dict[str, float]:
    c = control["summary"]
    t = treatment["summary"]
    return {
        "monthly_return_delta": t["monthly_return"] - c["monthly_return"],
        "monthly_alpha_vs_twii_delta": t["monthly_alpha_vs_twii"] - c["monthly_alpha_vs_twii"],
        "monthly_alpha_vs_strategy_fit_delta": t["monthly_alpha_vs_strategy_fit"] - c["monthly_alpha_vs_strategy_fit"],
        "mdd_delta": t["mdd"] - c["mdd"],
        "sharpe_delta": t["sharpe"] - c["sharpe"],
        "calmar_delta": t["calmar"] - c["calmar"],
        "avg_positions_delta": t["avg_positions_count"] - c["avg_positions_count"],
    }


def _truncate_summary_months(result: dict[str, Any], end_month: str) -> dict[str, Any]:
    out = dict(result)
    monthly = result["monthly"].loc[result["monthly"]["month"].le(end_month)].copy()
    out["monthly"] = monthly
    out["summary"] = _summary(monthly)
    return out


def _decision(summary: dict[str, Any]) -> dict[str, bool]:
    return {
        "monthly_alpha_vs_twii_gt_1pp": bool(summary["monthly_alpha_vs_twii"] > 0.01),
        "mdd_not_worse_than_minus_15pct": bool(summary["mdd"] >= -0.15),
        "calmar_gt_1": bool(summary["calmar"] > 1.0),
    }


def _merge_monthly(control: pd.DataFrame, treatment: pd.DataFrame) -> pd.DataFrame:
    keep = [
        "month",
        "portfolio_ret",
        "twii_ret",
        "alpha_vs_twii",
        "strategy_fit_ret",
        "alpha_vs_strategy_fit",
        "champion_ret",
        "alpha_vs_champion",
        "positions_count",
    ]
    c = control[keep].add_prefix("control_").rename(columns={"control_month": "month"})
    t = treatment[keep].add_prefix("treatment_").rename(columns={"treatment_month": "month"})
    out = c.merge(t, on="month", how="outer")
    out["delta_portfolio_ret"] = out["treatment_portfolio_ret"] - out["control_portfolio_ret"]
    out["delta_alpha_vs_twii"] = out["treatment_alpha_vs_twii"] - out["control_alpha_vs_twii"]
    out["delta_positions_count"] = out["treatment_positions_count"] - out["control_positions_count"]
    return out


def _april_focus(trades: pd.DataFrame) -> pd.DataFrame:
    if trades.empty or "entry_date" not in trades.columns:
        return pd.DataFrame()
    entry = pd.to_datetime(trades["entry_date"], errors="coerce")
    mask = entry.between(pd.Timestamp("2026-04-01"), pd.Timestamp("2026-05-08"))
    focus = trades.loc[mask & trades["ticker"].isin(["2330", "2454", "2308"])].copy()
    return focus.sort_values(["ticker", "entry_date", "exit_date"]) if not focus.empty else focus


def _april_open_focus(open_positions: pd.DataFrame) -> pd.DataFrame:
    if open_positions.empty or "entry_date" not in open_positions.columns:
        return pd.DataFrame()
    entry = pd.to_datetime(open_positions["entry_date"], errors="coerce")
    mask = entry.between(pd.Timestamp("2026-04-01"), pd.Timestamp("2026-05-08"))
    focus = open_positions.loc[mask & open_positions["ticker"].isin(["2330", "2454", "2308"])].copy()
    return focus.sort_values(["ticker", "entry_date"]) if not focus.empty else focus


def _fmt_num(value: object) -> str:
    try:
        value = float(value)
    except Exception:
        return "n/a"
    if not math.isfinite(value):
        return "n/a"
    return f"{value:.3f}"


def write_outputs(
    control: dict[str, Any],
    treatment: dict[str, Any],
    args: argparse.Namespace,
) -> Path:
    report_dir = Path(REPORT_DIR)
    report_dir.mkdir(parents=True, exist_ok=True)
    prefix = args.output_prefix or f"req026_large_cap_flow_relaxation_{datetime.now().strftime('%Y%m%d')}"
    paths = {
        "monthly": report_dir / f"{prefix}_monthly.csv",
        "treatment_trades": report_dir / f"{prefix}_treatment_trades.csv",
        "treatment_open": report_dir / f"{prefix}_treatment_open_positions.csv",
        "treatment_signals": report_dir / f"{prefix}_treatment_signals.csv",
        "april": report_dir / f"{prefix}_april2026.csv",
        "json": report_dir / f"{prefix}.json",
        "md": report_dir / f"{prefix}.md",
    }

    monthly = _merge_monthly(control["monthly"], treatment["monthly"])
    april = _april_focus(treatment["closed_trades"])
    april_open = _april_open_focus(treatment.get("open_positions", pd.DataFrame()))
    monthly.to_csv(paths["monthly"], index=False, encoding="utf-8-sig")
    treatment["closed_trades"].to_csv(paths["treatment_trades"], index=False, encoding="utf-8-sig")
    treatment.get("open_positions", pd.DataFrame()).to_csv(paths["treatment_open"], index=False, encoding="utf-8-sig")
    treatment["signals"].to_csv(paths["treatment_signals"], index=False, encoding="utf-8-sig")
    april.to_csv(paths["april"], index=False, encoding="utf-8-sig")

    delta = _summary_delta(control, treatment)
    checks = _decision(treatment["summary"])
    checks["mainline_pass"] = all(checks.values())
    payload = {
        "control": control["summary"],
        "treatment": treatment["summary"],
        "delta": delta,
        "decision_checks": checks,
        "control_signal_analysis": control["signal_analysis"],
        "treatment_signal_analysis": treatment["signal_analysis"],
        "method": {
            "control": "sector cap 2 names; foreign sell exits after 1 day",
            "treatment": "sector cap 4 names; foreign sell exits after 2 consecutive days",
            "summary_window": f"{args.start} to {args.end}",
            "replay_window": f"{args.start} to {args.replay_end}",
            "entry": "T+0 close",
            "exit": "foreign consecutive sell OR HWM -8% OR 20 trading days",
        },
        "paths": {key: str(path) for key, path in paths.items() if key != "md"},
    }
    paths["json"].write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# REQ-026 Large-Cap Flow Relaxation Backtest",
        "",
        "## Summary",
        "",
        "| Metric | Control | Treatment | Delta |",
        "|---|---:|---:|---:|",
        f"| Monthly return | {_fmt_pct(control['summary']['monthly_return'])} | {_fmt_pct(treatment['summary']['monthly_return'])} | {_fmt_pct(delta['monthly_return_delta'])} |",
        f"| Monthly alpha vs TWII | {_fmt_pct(control['summary']['monthly_alpha_vs_twii'])} | {_fmt_pct(treatment['summary']['monthly_alpha_vs_twii'])} | {_fmt_pct(delta['monthly_alpha_vs_twii_delta'])} |",
        f"| Monthly alpha vs strategy-fit | {_fmt_pct(control['summary']['monthly_alpha_vs_strategy_fit'])} | {_fmt_pct(treatment['summary']['monthly_alpha_vs_strategy_fit'])} | {_fmt_pct(delta['monthly_alpha_vs_strategy_fit_delta'])} |",
        f"| MDD | {_fmt_pct(control['summary']['mdd'])} | {_fmt_pct(treatment['summary']['mdd'])} | {_fmt_pct(delta['mdd_delta'])} |",
        f"| Sharpe | {_fmt_num(control['summary']['sharpe'])} | {_fmt_num(treatment['summary']['sharpe'])} | {_fmt_num(delta['sharpe_delta'])} |",
        f"| Calmar | {_fmt_num(control['summary']['calmar'])} | {_fmt_num(treatment['summary']['calmar'])} | {_fmt_num(delta['calmar_delta'])} |",
        f"| Avg positions | {_fmt_num(control['summary']['avg_positions_count'])} | {_fmt_num(treatment['summary']['avg_positions_count'])} | {_fmt_num(delta['avg_positions_delta'])} |",
        "",
        "## Decision Checks",
        "",
        "| Gate | Treatment Result |",
        "|---|---:|",
        f"| Monthly alpha vs TWII > +1pp | {'PASS' if checks['monthly_alpha_vs_twii_gt_1pp'] else 'FAIL'} |",
        f"| MDD not worse than -15% | {'PASS' if checks['mdd_not_worse_than_minus_15pct'] else 'FAIL'} |",
        f"| Calmar > 1.0 | {'PASS' if checks['calmar_gt_1'] else 'FAIL'} |",
        f"| Mainline gate | {'PASS' if checks['mainline_pass'] else 'FAIL'} |",
        "",
        "## Signal And Exit Analysis",
        "",
        f"- Control exit reasons: `{json.dumps(control['signal_analysis']['exit_reasons'], ensure_ascii=False)}`",
        f"- Treatment exit reasons: `{json.dumps(treatment['signal_analysis']['exit_reasons'], ensure_ascii=False)}`",
        f"- Treatment top tickers: `{json.dumps(treatment['signal_analysis']['top_tickers'], ensure_ascii=False)}`",
        f"- Treatment sector distribution: `{json.dumps(treatment['signal_analysis']['sector_distribution'], ensure_ascii=False)}`",
        "",
        "## April 2026 Replay",
        "",
        f"- Treatment 2330/2454/2308 closed trades: {len(april)}",
        f"- Treatment 2330/2454/2308 open positions at replay end: {len(april_open)}",
    ]
    if not april.empty:
        lines.extend(["", "| ticker | entry | exit | return | reason | hold_days |", "|---|---|---|---:|---|---:|"])
        for _, row in april.iterrows():
            lines.append(
                f"| {row['ticker']} | {row['entry_date']} | {row['exit_date']} | {_fmt_pct(row['return_pct'])} | {row['exit_reason']} | {row['hold_days']} |"
            )
    if not april_open.empty:
        lines.extend(["", "| ticker | entry | as_of | unrealized | hold_days | status |", "|---|---|---|---:|---:|---|"])
        for _, row in april_open.iterrows():
            lines.append(
                f"| {row['ticker']} | {row['entry_date']} | {row['as_of_date']} | {_fmt_pct(row['return_pct'])} | {row['hold_days']} | open |"
            )
    lines.extend(
        [
            "",
            "## Artifacts",
            "",
            f"- Monthly comparison CSV: `{paths['monthly']}`",
            f"- Treatment trades CSV: `{paths['treatment_trades']}`",
            f"- Treatment open positions CSV: `{paths['treatment_open']}`",
            f"- Treatment signals CSV: `{paths['treatment_signals']}`",
            f"- April replay CSV: `{paths['april']}`",
            f"- JSON: `{paths['json']}`",
        ]
    )
    paths["md"].write_text("\n".join(lines) + "\n", encoding="utf-8")
    return paths["md"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run REQ-026 large-cap flow relaxation A/B backtest.")
    parser.add_argument("--start", default="2024-01-01")
    parser.add_argument("--end", default="2026-04-30")
    parser.add_argument("--replay-end", default="2026-05-08")
    parser.add_argument("--max-positions", type=int, default=10)
    parser.add_argument("--hold-days", type=int, default=20)
    parser.add_argument("--hwm-drawdown", type=float, default=0.08)
    parser.add_argument("--output-prefix", default="req026_large_cap_flow_relaxation_20260510")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    control_full = run_backtest(_make_args(args, max_per_sector=2, foreign_sell_days=1))
    treatment_full = run_backtest(_make_args(args, max_per_sector=4, foreign_sell_days=2))
    summary_end_month = pd.Timestamp(args.end).to_period("M").strftime("%Y-%m")
    control = _truncate_summary_months(control_full, summary_end_month)
    treatment = _truncate_summary_months(treatment_full, summary_end_month)
    control["closed_trades"] = control_full["closed_trades"]
    treatment["closed_trades"] = treatment_full["closed_trades"]
    control["signals"] = control_full["signals"]
    treatment["signals"] = treatment_full["signals"]
    md_path = write_outputs(control, treatment, args)
    delta = _summary_delta(control, treatment)
    print(f"[req026] wrote {md_path}")
    print(
        f"[req026] treatment_alpha={_fmt_pct(treatment['summary']['monthly_alpha_vs_twii'])} "
        f"delta={_fmt_pct(delta['monthly_alpha_vs_twii_delta'])} "
        f"mdd={_fmt_pct(treatment['summary']['mdd'])}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
