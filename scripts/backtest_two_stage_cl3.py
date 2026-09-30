"""REQ-010 CL3 backtest for AUDIT-002 Two-Stage N=75 promotion.

The script consumes walk-forward out-of-fold artifacts:

- Control: Stage 1 V2 regression Top30 from the fixed OOF frame.
- Treatment: AUDIT-002 Step 5 recommended Two-Stage N=75 / 5-level Top30.

It does not use the Step 6 fixed 6-month snapshot result and does not rebuild
features. The goal is a production-promotion CL3 readout plus turnover/cost
monitoring.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ml.config import (  # noqa: E402
    REPORT_DIR,
    TWO_STAGE_CANDIDATE_SIZE,
    TWO_STAGE_LABEL_BINS,
    TWO_STAGE_TOP_N,
)


REPORT_PATH = Path(REPORT_DIR)
RAW_RETURN_COL = "trade_return_20d"
EXCESS_RETURN_COL = "trade_excess_return_20d"


@dataclass(frozen=True)
class PerformanceSummary:
    variant: str
    months: int
    monthly_return: float
    monthly_alpha: float
    mdd: float
    sharpe: float
    calmar: float
    win_rate: float


def _latest(pattern: str) -> Path:
    paths = sorted(REPORT_PATH.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    if not paths:
        raise FileNotFoundError(f"No report matches {pattern!r} under {REPORT_PATH}")
    return paths[0]


def _fmt_pct(value: Any, digits: int = 2) -> str:
    try:
        number = float(value)
    except Exception:
        return "-"
    if not math.isfinite(number):
        return "-"
    return f"{number * 100:+.{digits}f}%"


def _fmt_float(value: Any, digits: int = 3) -> str:
    try:
        number = float(value)
    except Exception:
        return "-"
    if not math.isfinite(number):
        return "-"
    return f"{number:.{digits}f}"


def _chart_font(size: int) -> ImageFont.ImageFont:
    candidates = [
        Path("C:/Windows/Fonts/NotoSansTC-VF.ttf"),
        Path("C:/Windows/Fonts/msjh.ttc"),
        Path("C:/Windows/Fonts/mingliu.ttc"),
        Path("C:/Windows/Fonts/arial.ttf"),
    ]
    for font_path in candidates:
        if not font_path.exists():
            continue
        try:
            return ImageFont.truetype(str(font_path), size)
        except OSError:
            continue
    return ImageFont.load_default()


def _max_drawdown(returns: pd.Series) -> float:
    clean = pd.to_numeric(returns, errors="coerce").dropna()
    if clean.empty:
        return float("nan")
    equity = (1.0 + clean).cumprod()
    drawdown = equity / equity.cummax() - 1.0
    return float(drawdown.min())


def _sharpe(returns: pd.Series) -> float:
    clean = pd.to_numeric(returns, errors="coerce").dropna()
    if len(clean) < 2:
        return float("nan")
    std = float(clean.std(ddof=1))
    if std <= 0 or not math.isfinite(std):
        return float("nan")
    return float(clean.mean() / std * math.sqrt(12.0))


def _calmar(returns: pd.Series, mdd: float) -> float:
    clean = pd.to_numeric(returns, errors="coerce").dropna()
    if clean.empty or not math.isfinite(mdd) or mdd >= 0:
        return float("inf")
    annualized = float((1.0 + clean.mean()) ** 12 - 1.0)
    return annualized / abs(mdd)


def _read_stage1(path: Path) -> pd.DataFrame:
    cols = [
        "rebalance_month",
        "ticker",
        "Date",
        RAW_RETURN_COL,
        EXCESS_RETURN_COL,
        "stage1_score",
        "stage1_rank",
    ]
    frame = pd.read_csv(path, usecols=cols, dtype={"ticker": str})
    frame["rebalance_month"] = frame["rebalance_month"].astype(str)
    frame["Date"] = pd.to_datetime(frame["Date"])
    return frame


def _read_treatment(path: Path) -> pd.DataFrame:
    cols = [
        "rebalance_month",
        "ticker",
        "Date",
        "rank",
        "stage1_rank",
        "stage1_score",
        "rank_score",
        RAW_RETURN_COL,
        EXCESS_RETURN_COL,
        "month",
        "candidate_size",
        "label_bins",
    ]
    frame = pd.read_csv(path, usecols=cols, dtype={"ticker": str})
    frame["month"] = frame["month"].astype(str)
    frame["Date"] = pd.to_datetime(frame["Date"])
    return frame


def _select_months(stage1: pd.DataFrame, treatment: pd.DataFrame, start_month: str, end_month: str) -> list[str]:
    common = sorted(set(stage1["rebalance_month"]).intersection(set(treatment["month"])))
    selected = [month for month in common if start_month <= month <= end_month]
    if not selected:
        raise ValueError(f"No common OOF months in {start_month}..{end_month}")
    return selected


def _build_monthly(stage1: pd.DataFrame, treatment: pd.DataFrame, months: list[str], top_n: int) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    monthly_rows: list[dict[str, Any]] = []
    control_rows: list[pd.DataFrame] = []
    treatment_rows: list[pd.DataFrame] = []
    for month in months:
        control = (
            stage1[stage1["rebalance_month"] == month]
            .sort_values(["stage1_rank", "ticker"], ascending=[True, True])
            .head(top_n)
            .copy()
            .rename(columns={"stage1_rank": "control_rank", "stage1_score": "control_score"})
        )
        treat = (
            treatment[treatment["month"] == month]
            .sort_values(["rank", "ticker"], ascending=[True, True])
            .head(top_n)
            .copy()
            .rename(columns={"rank": "treatment_rank", "rank_score": "treatment_score"})
        )
        control_set = set(control["ticker"])
        treatment_set = set(treat["ticker"])
        intersection = control_set.intersection(treatment_set)
        union = control_set.union(treatment_set)
        jaccard = len(intersection) / len(union) if union else float("nan")
        replacement_ratio = 1.0 - len(intersection) / top_n if top_n else float("nan")
        monthly_rows.append(
            {
                "month": month,
                "control_return": float(control[RAW_RETURN_COL].mean()),
                "treatment_return": float(treat[RAW_RETURN_COL].mean()),
                "control_alpha": float(control[EXCESS_RETURN_COL].mean()),
                "treatment_alpha": float(treat[EXCESS_RETURN_COL].mean()),
                "intersection_count": int(len(intersection)),
                "union_count": int(len(union)),
                "jaccard": float(jaccard),
                "replacement_ratio": float(replacement_ratio),
            }
        )
        control_out = control.copy()
        control_out["month"] = month
        treat_out = treat.copy()
        treat_out["month"] = month
        control_rows.append(control_out)
        treatment_rows.append(treat_out)

    monthly = pd.DataFrame(monthly_rows)
    monthly["return_delta"] = monthly["treatment_return"] - monthly["control_return"]
    monthly["alpha_delta"] = monthly["treatment_alpha"] - monthly["control_alpha"]
    return monthly, pd.concat(control_rows, ignore_index=True), pd.concat(treatment_rows, ignore_index=True)


def _summarize(monthly: pd.DataFrame, variant: str, ret_col: str, alpha_col: str) -> PerformanceSummary:
    returns = monthly[ret_col]
    mdd = _max_drawdown(returns)
    return PerformanceSummary(
        variant=variant,
        months=int(len(monthly)),
        monthly_return=float(returns.mean()),
        monthly_alpha=float(monthly[alpha_col].mean()),
        mdd=mdd,
        sharpe=_sharpe(returns),
        calmar=_calmar(returns, mdd),
        win_rate=float((returns > 0).mean()),
    )


def _regression_ratio(before: float, after: float) -> float:
    if not math.isfinite(before) or before == 0:
        return 0.0
    return max(0.0, before - after) / abs(before)


def _cl3(control: PerformanceSummary, treatment: PerformanceSummary) -> dict[str, Any]:
    alpha_sacrifice = max(0.0, control.monthly_alpha - treatment.monthly_alpha)
    mdd_improvement = abs(control.mdd) - abs(treatment.mdd)
    sharpe_regression = _regression_ratio(control.sharpe, treatment.sharpe)
    calmar_regression = _regression_ratio(control.calmar, treatment.calmar)
    checks = {
        "alpha_sacrifice": {
            "value": alpha_sacrifice,
            "threshold": 0.005,
            "pass": alpha_sacrifice <= 0.005,
        },
        "mdd_improvement": {
            "value": mdd_improvement,
            "threshold": 0.001,
            "pass": mdd_improvement >= 0.001,
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


def _line_chart(monthly: pd.DataFrame, output_path: Path) -> None:
    width, height = 1100, 620
    ml, mr, mt, mb = 92, 42, 62, 92
    plot_w = width - ml - mr
    plot_h = height - mt - mb
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    small = _chart_font(14)
    title_font = _chart_font(24)

    values = pd.concat([monthly["control_return"], monthly["treatment_return"]]).astype(float)
    ymin = min(0.0, float(values.min()))
    ymax = max(0.0, float(values.max()))
    pad = max((ymax - ymin) * 0.12, 0.02)
    ymin -= pad
    ymax += pad
    if math.isclose(ymin, ymax):
        ymin -= 0.01
        ymax += 0.01

    def x_at(index: int) -> float:
        return ml + (plot_w / 2 if len(monthly) == 1 else index * plot_w / (len(monthly) - 1))

    def y_at(value: float) -> float:
        return mt + (ymax - value) * plot_h / (ymax - ymin)

    draw.text((ml, 22), "REQ-010 Two-Stage CL3 每月回測報酬", fill="#111827", font=title_font)
    draw.rectangle((ml, mt, ml + plot_w, mt + plot_h), outline="#d1d5db")
    for i in range(6):
        frac = i / 5
        y = mt + frac * plot_h
        value = ymax - frac * (ymax - ymin)
        draw.line((ml, y, ml + plot_w, y), fill="#eef2f7")
        draw.text((12, y - 8), f"{value*100:+.1f}%", fill="#4b5563", font=small)
    draw.line((ml, y_at(0.0), ml + plot_w, y_at(0.0)), fill="#9ca3af", width=2)

    series = [
        ("control_return", "#2563eb", "藍線：舊版回歸 Top30"),
        ("treatment_return", "#dc2626", "紅線：Two-Stage N=75"),
    ]
    for col, color, label in series:
        points = [(x_at(i), y_at(float(value))) for i, value in enumerate(monthly[col])]
        if len(points) >= 2:
            draw.line(points, fill=color, width=3)
        for x, y in points:
            draw.ellipse((x - 3, y - 3, x + 3, y + 3), fill=color)
        lx = ml + (0 if col == "control_return" else 360)
        ly = height - 38
        draw.line((lx, ly, lx + 38, ly), fill=color, width=4)
        draw.text((lx + 48, ly - 10), label, fill="#111827", font=small)

    # Avoid overcrowding the x-axis; show quarterly-ish labels.
    for i, month in enumerate(monthly["month"]):
        if i % 3 == 0 or i == len(monthly) - 1:
            x = x_at(i)
            draw.text((x - 22, mt + plot_h + 18), month, fill="#4b5563", font=small)
    image.save(output_path)


def _write_report(
    path: Path,
    *,
    metadata: dict[str, Any],
    control: PerformanceSummary,
    treatment: PerformanceSummary,
    cl3: dict[str, Any],
    monthly: pd.DataFrame,
    cost_bps: float,
    monthly_csv: Path,
    summary_csv: Path,
    chart_png: Path,
    control_csv: Path,
    treatment_csv: Path,
) -> None:
    avg_replacement = float(monthly["replacement_ratio"].mean())
    avg_jaccard = float(monthly["jaccard"].mean())
    one_way_cost = avg_replacement * (cost_bps / 10000.0)
    round_trip_cost = avg_replacement * (2.0 * cost_bps / 10000.0)
    cost_adjusted_treatment_alpha = treatment.monthly_alpha - round_trip_cost
    cost_adjusted_delta = cost_adjusted_treatment_alpha - control.monthly_alpha

    lines: list[str] = [
        "# REQ-010 Two-Stage N=75 CL3 Backtest",
        "",
        f"- Generated at: `{metadata['generated_at']}`",
        f"- Window: `{metadata['start_month']}` to `{metadata['end_month']}`",
        f"- Control: `{metadata['control_artifact']}`",
        f"- Treatment: `{metadata['treatment_artifact']}`",
        f"- Config: candidate_size={TWO_STAGE_CANDIDATE_SIZE}, label_bins={TWO_STAGE_LABEL_BINS}, top_n={TWO_STAGE_TOP_N}",
        "",
        "## CL3",
        "",
        "| Check | Value | Threshold | Pass |",
        "|---|---:|---:|:---:|",
    ]
    for name, item in cl3["checks"].items():
        threshold = item["threshold"]
        lines.append(
            f"| {name} | {_fmt_pct(item['value'])} | {_fmt_pct(threshold)} | "
            f"{'PASS' if item['pass'] else 'FAIL'} |"
        )
    lines.append("")
    lines.append(f"Overall: **{'PASS' if cl3['overall_pass'] else 'FAIL'}**")
    lines.extend(
        [
            "",
            "## Performance",
            "",
            "| Metric | Control | Treatment | Delta |",
            "|---|---:|---:|---:|",
            f"| Monthly return | {_fmt_pct(control.monthly_return)} | {_fmt_pct(treatment.monthly_return)} | {_fmt_pct(treatment.monthly_return - control.monthly_return)} |",
            f"| Monthly alpha | {_fmt_pct(control.monthly_alpha)} | {_fmt_pct(treatment.monthly_alpha)} | {_fmt_pct(treatment.monthly_alpha - control.monthly_alpha)} |",
            f"| MDD | {_fmt_pct(control.mdd)} | {_fmt_pct(treatment.mdd)} | {_fmt_pct(abs(control.mdd) - abs(treatment.mdd))} improvement |",
            f"| Sharpe | {_fmt_float(control.sharpe)} | {_fmt_float(treatment.sharpe)} | {_fmt_float(treatment.sharpe - control.sharpe)} |",
            f"| Calmar | {_fmt_float(control.calmar)} | {_fmt_float(treatment.calmar)} | {_fmt_float(treatment.calmar - control.calmar)} |",
            f"| Win rate | {_fmt_pct(control.win_rate)} | {_fmt_pct(treatment.win_rate)} | {_fmt_pct(treatment.win_rate - control.win_rate)} |",
            "",
            "## Turnover And Cost",
            "",
            f"- Average Jaccard: {_fmt_float(avg_jaccard)}",
            f"- Average replacement ratio: {_fmt_pct(avg_replacement)}",
            f"- Cost assumption: {cost_bps:.1f} bps one-way",
            f"- Estimated one-way monthly cost drag: {_fmt_pct(one_way_cost)}",
            f"- Estimated round-trip monthly cost drag: {_fmt_pct(round_trip_cost)}",
            f"- Treatment cost-adjusted monthly alpha: {_fmt_pct(cost_adjusted_treatment_alpha)}",
            f"- Cost-adjusted alpha delta vs Control: {_fmt_pct(cost_adjusted_delta)}",
            "",
            "## Monthly Sample",
            "",
            "| Month | Control Ret | Treatment Ret | Control Alpha | Treatment Alpha | Jaccard | Replacement |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in monthly.to_dict("records"):
        lines.append(
            f"| {row['month']} | {_fmt_pct(row['control_return'])} | {_fmt_pct(row['treatment_return'])} | "
            f"{_fmt_pct(row['control_alpha'])} | {_fmt_pct(row['treatment_alpha'])} | "
            f"{_fmt_float(row['jaccard'])} | {_fmt_pct(row['replacement_ratio'])} |"
        )
    lines.extend(
        [
            "",
            "## Artifacts",
            "",
            f"- Summary CSV: `{summary_csv}`",
            f"- Monthly CSV: `{monthly_csv}`",
            f"- Control picks CSV: `{control_csv}`",
            f"- Treatment picks CSV: `{treatment_csv}`",
            f"- Monthly return chart: `{chart_png}`",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage1-oof", type=Path, default=None)
    parser.add_argument("--treatment-fold", type=Path, default=None)
    parser.add_argument("--start-month", default="2024-01")
    parser.add_argument("--end-month", default="2026-04")
    parser.add_argument("--cost-bps", type=float, default=30.0)
    parser.add_argument("--output-prefix", default=None)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    stage1_path = args.stage1_oof or _latest("audit002_step4_two_stage_*_stage1_oof_candidates.csv")
    treatment_path = args.treatment_fold or _latest(
        f"audit002_step5_grid_search_*_N{TWO_STAGE_CANDIDATE_SIZE}_bins{TWO_STAGE_LABEL_BINS}_fold_top30.csv"
    )
    prefix = args.output_prefix or f"two_stage_cl3_backtest_{datetime.now().strftime('%Y%m%d')}"
    stage1 = _read_stage1(stage1_path)
    treatment = _read_treatment(treatment_path)
    months = _select_months(stage1, treatment, args.start_month, args.end_month)
    monthly, control_picks, treatment_picks = _build_monthly(
        stage1,
        treatment,
        months,
        top_n=TWO_STAGE_TOP_N,
    )

    control = _summarize(monthly, "Control_Regression_Top30", "control_return", "control_alpha")
    treatment_summary = _summarize(monthly, "Treatment_TwoStage_N75", "treatment_return", "treatment_alpha")
    cl3 = _cl3(control, treatment_summary)

    avg_replacement = float(monthly["replacement_ratio"].mean())
    round_trip_cost = avg_replacement * (2.0 * args.cost_bps / 10000.0)
    cost_adjusted_treatment_alpha = treatment_summary.monthly_alpha - round_trip_cost
    cost_adjusted_delta = cost_adjusted_treatment_alpha - control.monthly_alpha

    out_dir = REPORT_PATH
    monthly_path = out_dir / f"{prefix}_monthly.csv"
    summary_path = out_dir / f"{prefix}_summary.csv"
    control_path = out_dir / f"{prefix}_control_top30.csv"
    treatment_out_path = out_dir / f"{prefix}_treatment_top30.csv"
    chart_path = out_dir / f"{prefix}_monthly_returns.png"
    md_path = out_dir / f"{prefix}.md"
    json_path = out_dir / f"{prefix}.json"

    monthly.to_csv(monthly_path, index=False, encoding="utf-8")
    control_picks.to_csv(control_path, index=False, encoding="utf-8")
    treatment_picks.to_csv(treatment_out_path, index=False, encoding="utf-8")
    summary = pd.DataFrame([asdict(control), asdict(treatment_summary)])
    summary.to_csv(summary_path, index=False, encoding="utf-8")
    _line_chart(monthly, chart_path)

    metadata = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "start_month": months[0],
        "end_month": months[-1],
        "requested_start_month": args.start_month,
        "requested_end_month": args.end_month,
        "months": len(months),
        "control_artifact": str(stage1_path),
        "treatment_artifact": str(treatment_path),
        "cost_bps": args.cost_bps,
        "monthly_csv": str(monthly_path),
        "summary_csv": str(summary_path),
        "control_top30_csv": str(control_path),
        "treatment_top30_csv": str(treatment_out_path),
        "chart_png": str(chart_path),
        "report": str(md_path),
    }
    payload = {
        "metadata": metadata,
        "control": asdict(control),
        "treatment": asdict(treatment_summary),
        "cl3": cl3,
        "turnover": {
            "average_jaccard": float(monthly["jaccard"].mean()),
            "average_replacement_ratio": avg_replacement,
            "estimated_round_trip_cost": round_trip_cost,
            "cost_adjusted_treatment_alpha": cost_adjusted_treatment_alpha,
            "cost_adjusted_alpha_delta": cost_adjusted_delta,
        },
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_report(
        md_path,
        metadata=metadata,
        control=control,
        treatment=treatment_summary,
        cl3=cl3,
        monthly=monthly,
        cost_bps=args.cost_bps,
        monthly_csv=monthly_path,
        summary_csv=summary_path,
        chart_png=chart_path,
        control_csv=control_path,
        treatment_csv=treatment_out_path,
    )

    print(f"[two-stage-cl3] report={md_path}")
    print(
        "[two-stage-cl3] "
        f"alpha_delta={_fmt_pct(treatment_summary.monthly_alpha - control.monthly_alpha)} "
        f"mdd_improvement={_fmt_pct(abs(control.mdd) - abs(treatment_summary.mdd))} "
        f"sharpe_delta={treatment_summary.sharpe - control.sharpe:+.3f} "
        f"calmar_delta={treatment_summary.calmar - control.calmar:+.3f} "
        f"overall={'PASS' if cl3['overall_pass'] else 'FAIL'}"
    )


if __name__ == "__main__":
    main()
