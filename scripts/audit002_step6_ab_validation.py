"""AUDIT-002 Step 6: fixed-snapshot A/B validation for two-stage ranking.

This script intentionally reuses the Step 4/5 fixed monthly snapshot artifacts.
It does not rebuild features. Control is the Stage 1 regression Top30 from the
same frame; treatment is the Step 5 recommended Two-Stage N=75/5-level Top30.
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

from ml.config import REPORT_DIR


REPORT_PATH = Path(REPORT_DIR)
RAW_RETURN_COL = "trade_return_20d"
EXCESS_RETURN_COL = "trade_excess_return_20d"
DEFAULT_MONTHS = 6
DEFAULT_TREATMENT_CANDIDATE_SIZE = 75
DEFAULT_TREATMENT_LABEL_BINS = 5
DEFAULT_TOP_N = 30


@dataclass(frozen=True)
class PortfolioSummary:
    months: int
    monthly_return: float
    monthly_alpha: float
    mdd: float
    win_rate: float


def _latest(pattern: str) -> Path:
    paths = sorted(REPORT_PATH.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    if not paths:
        raise FileNotFoundError(f"No report matches {pattern!r} under {REPORT_PATH}")
    return paths[0]


def _fmt_pct(value: float | None, digits: int = 2) -> str:
    if value is None or pd.isna(value):
        return "-"
    return f"{value * 100:+.{digits}f}%"


def _fmt_float(value: float | None, digits: int = 3) -> str:
    if value is None or pd.isna(value):
        return "-"
    return f"{value:.{digits}f}"


def _max_drawdown(returns: pd.Series) -> float:
    clean = pd.to_numeric(returns, errors="coerce").dropna()
    if clean.empty:
        return float("nan")
    equity = (1.0 + clean).cumprod()
    drawdown = equity / equity.cummax() - 1.0
    return float(drawdown.min())


def _summarize(monthly: pd.DataFrame, return_col: str, alpha_col: str) -> PortfolioSummary:
    return PortfolioSummary(
        months=int(len(monthly)),
        monthly_return=float(monthly[return_col].mean()),
        monthly_alpha=float(monthly[alpha_col].mean()),
        mdd=_max_drawdown(monthly[return_col]),
        win_rate=float((monthly[return_col] > 0).mean()),
    )


def _read_stage1(path: Path) -> pd.DataFrame:
    usecols = [
        "rebalance_month",
        "ticker",
        "Date",
        RAW_RETURN_COL,
        EXCESS_RETURN_COL,
        "stage1_score",
        "stage1_rank",
    ]
    frame = pd.read_csv(path, usecols=usecols, dtype={"ticker": str})
    frame["Date"] = pd.to_datetime(frame["Date"])
    frame["rebalance_month"] = frame["rebalance_month"].astype(str)
    frame["stage1_rank"] = pd.to_numeric(frame["stage1_rank"], errors="coerce")
    return frame


def _read_treatment(path: Path) -> pd.DataFrame:
    usecols = [
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
    frame = pd.read_csv(path, usecols=usecols, dtype={"ticker": str})
    frame["Date"] = pd.to_datetime(frame["Date"])
    frame["month"] = frame["month"].astype(str)
    frame["rank"] = pd.to_numeric(frame["rank"], errors="coerce")
    return frame


def _select_months(stage1: pd.DataFrame, treatment: pd.DataFrame, months: int, end_month: str | None) -> list[str]:
    common = sorted(set(stage1["rebalance_month"]).intersection(set(treatment["month"])))
    if end_month:
        common = [month for month in common if month <= end_month]
    if len(common) < months:
        raise ValueError(f"Only {len(common)} common months available; need {months}")
    return common[-months:]


def _control_topn(stage1: pd.DataFrame, month: str, top_n: int) -> pd.DataFrame:
    out = (
        stage1[stage1["rebalance_month"] == month]
        .sort_values(["stage1_rank", "ticker"], ascending=[True, True])
        .head(top_n)
        .copy()
    )
    out = out.rename(columns={"stage1_rank": "control_rank", "stage1_score": "control_score"})
    return out


def _treatment_topn(treatment: pd.DataFrame, month: str, top_n: int) -> pd.DataFrame:
    out = (
        treatment[treatment["month"] == month]
        .sort_values(["rank", "ticker"], ascending=[True, True])
        .head(top_n)
        .copy()
    )
    out = out.rename(columns={"rank": "treatment_rank", "rank_score": "treatment_score"})
    return out


def _rank_diagnostics(control: pd.DataFrame, treatment: pd.DataFrame, top_n: int) -> dict[str, Any]:
    control_set = set(control["ticker"])
    treatment_set = set(treatment["ticker"])
    intersection = control_set.intersection(treatment_set)
    union = control_set.union(treatment_set)
    compare = pd.DataFrame({"ticker": sorted(union)})
    compare = compare.merge(control[["ticker", "control_rank"]], on="ticker", how="left")
    compare = compare.merge(treatment[["ticker", "treatment_rank"]], on="ticker", how="left")

    jaccard = len(intersection) / len(union) if union else float("nan")
    replacement_ratio = 1.0 - (len(intersection) / top_n) if top_n else float("nan")
    union_spearman = compare["control_rank"].fillna(top_n + 1).corr(
        compare["treatment_rank"].fillna(top_n + 1), method="spearman"
    )
    common = compare.dropna(subset=["control_rank", "treatment_rank"])
    intersection_spearman = (
        common["control_rank"].corr(common["treatment_rank"], method="spearman") if len(common) >= 2 else float("nan")
    )
    return {
        "control_count": int(len(control_set)),
        "treatment_count": int(len(treatment_set)),
        "intersection_count": int(len(intersection)),
        "union_count": int(len(union)),
        "jaccard": float(jaccard),
        "replacement_ratio": float(replacement_ratio),
        "union_rank_spearman_filled": float(union_spearman) if pd.notna(union_spearman) else np.nan,
        "intersection_rank_spearman": float(intersection_spearman) if pd.notna(intersection_spearman) else np.nan,
    }


def _build_monthly_ab(
    stage1: pd.DataFrame,
    treatment: pd.DataFrame,
    months: list[str],
    *,
    top_n: int,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    monthly_rows: list[dict[str, Any]] = []
    control_rows: list[pd.DataFrame] = []
    treatment_rows: list[pd.DataFrame] = []
    for month in months:
        control = _control_topn(stage1, month, top_n)
        treat = _treatment_topn(treatment, month, top_n)
        diag = _rank_diagnostics(control, treat, top_n)
        control_ret = float(control[RAW_RETURN_COL].mean())
        treatment_ret = float(treat[RAW_RETURN_COL].mean())
        control_alpha = float(control[EXCESS_RETURN_COL].mean())
        treatment_alpha = float(treat[EXCESS_RETURN_COL].mean())
        monthly_rows.append(
            {
                "month": month,
                "control_return": control_ret,
                "treatment_return": treatment_ret,
                "return_delta": treatment_ret - control_ret,
                "control_alpha": control_alpha,
                "treatment_alpha": treatment_alpha,
                "alpha_delta": treatment_alpha - control_alpha,
                **diag,
            }
        )
        control_out = control.copy()
        control_out["month"] = month
        treatment_out = treat.copy()
        treatment_out["month"] = month
        control_rows.append(control_out)
        treatment_rows.append(treatment_out)
    return pd.DataFrame(monthly_rows), pd.concat(control_rows, ignore_index=True), pd.concat(treatment_rows, ignore_index=True)


def _line_chart(monthly: pd.DataFrame, output_path: Path) -> None:
    width, height = 1100, 620
    margin_left, margin_right, margin_top, margin_bottom = 92, 42, 62, 92
    plot_w = width - margin_left - margin_right
    plot_h = height - margin_top - margin_bottom
    img = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("arial.ttf", 18)
        small = ImageFont.truetype("arial.ttf", 14)
        title_font = ImageFont.truetype("arial.ttf", 24)
    except OSError:
        font = small = title_font = ImageFont.load_default()

    values = pd.concat([monthly["control_return"], monthly["treatment_return"]]).astype(float)
    ymin = min(float(values.min()), 0.0)
    ymax = max(float(values.max()), 0.0)
    pad = max((ymax - ymin) * 0.12, 0.02)
    ymin -= pad
    ymax += pad
    if math.isclose(ymin, ymax):
        ymin -= 0.01
        ymax += 0.01

    def x_at(idx: int) -> float:
        if len(monthly) == 1:
            return margin_left + plot_w / 2
        return margin_left + idx * plot_w / (len(monthly) - 1)

    def y_at(value: float) -> float:
        return margin_top + (ymax - value) * plot_h / (ymax - ymin)

    draw.text((margin_left, 22), "AUDIT-002 Step 6 Monthly Return A/B", fill="#111827", font=title_font)
    draw.rectangle((margin_left, margin_top, margin_left + plot_w, margin_top + plot_h), outline="#d1d5db")
    for i in range(6):
        frac = i / 5
        y = margin_top + frac * plot_h
        value = ymax - frac * (ymax - ymin)
        draw.line((margin_left, y, margin_left + plot_w, y), fill="#eef2f7")
        draw.text((12, y - 8), f"{value*100:+.1f}%", fill="#4b5563", font=small)
    zero_y = y_at(0.0)
    draw.line((margin_left, zero_y, margin_left + plot_w, zero_y), fill="#9ca3af", width=2)

    colors = {"control_return": "#2563eb", "treatment_return": "#dc2626"}
    labels = {"control_return": "Control regression Top30", "treatment_return": "Treatment two-stage N75"}
    for col, color in colors.items():
        points = [(x_at(i), y_at(float(value))) for i, value in enumerate(monthly[col])]
        if len(points) >= 2:
            draw.line(points, fill=color, width=4)
        for x, y in points:
            draw.ellipse((x - 5, y - 5, x + 5, y + 5), fill=color)
        lx = margin_left + (0 if col == "control_return" else 360)
        ly = height - 38
        draw.line((lx, ly, lx + 38, ly), fill=color, width=4)
        draw.text((lx + 48, ly - 10), labels[col], fill="#111827", font=small)

    for i, month in enumerate(monthly["month"]):
        x = x_at(i)
        draw.text((x - 28, margin_top + plot_h + 20), month, fill="#4b5563", font=small)

    img.save(output_path)


def _write_report(
    *,
    report_path: Path,
    chart_path: Path,
    monthly_path: Path,
    control_path: Path,
    treatment_path: Path,
    monthly: pd.DataFrame,
    control_summary: PortfolioSummary,
    treatment_summary: PortfolioSummary,
    ac: dict[str, Any],
    stage1_path: Path,
    treatment_path_in: Path,
    top_n: int,
    cost_bps: float,
) -> None:
    avg_jaccard = float(monthly["jaccard"].mean())
    avg_replacement = float(monthly["replacement_ratio"].mean())
    one_way_cost = avg_replacement * (cost_bps / 10000.0)
    round_trip_cost = avg_replacement * (2.0 * cost_bps / 10000.0)
    lines: list[str] = []
    lines.append("# AUDIT-002 Step 6 Same-Snapshot A/B Validation")
    lines.append("")
    lines.append(f"- Generated at: {datetime.now().isoformat(timespec='seconds')}")
    lines.append(f"- Control frame: `{stage1_path}`")
    lines.append(f"- Treatment frame: `{treatment_path_in}`")
    lines.append(f"- Months: {', '.join(monthly['month'].tolist())}")
    lines.append(f"- Top N: {top_n}")
    lines.append("")
    lines.append("## AC Summary")
    lines.append("")
    lines.append("| AC | Threshold | Value | Pass |")
    lines.append("|---|---:|---:|:---:|")
    lines.append(
        f"| Treatment monthly excess delta | >= +0.30pp | {_fmt_pct(ac['alpha_delta'])} | "
        f"{'PASS' if ac['alpha_pass'] else 'FAIL'} |"
    )
    lines.append(
        f"| Treatment MDD deterioration | <= +1.00pp | {_fmt_pct(ac['mdd_deterioration'])} | "
        f"{'PASS' if ac['mdd_pass'] else 'FAIL'} |"
    )
    lines.append(
        f"| Average Jaccard | >= 0.40 | {_fmt_float(avg_jaccard)} | "
        f"{'PASS' if ac['jaccard_pass'] else 'FAIL'} |"
    )
    lines.append("")
    lines.append(f"Overall: **{'PASS' if ac['pass_all'] else 'FAIL'}**")
    lines.append("")
    lines.append("## Control vs Treatment")
    lines.append("")
    lines.append("| Metric | Control | Treatment | Delta |")
    lines.append("|---|---:|---:|---:|")
    lines.append(
        f"| Monthly return | {_fmt_pct(control_summary.monthly_return)} | "
        f"{_fmt_pct(treatment_summary.monthly_return)} | "
        f"{_fmt_pct(treatment_summary.monthly_return - control_summary.monthly_return)} |"
    )
    lines.append(
        f"| Monthly alpha vs TWII | {_fmt_pct(control_summary.monthly_alpha)} | "
        f"{_fmt_pct(treatment_summary.monthly_alpha)} | "
        f"{_fmt_pct(treatment_summary.monthly_alpha - control_summary.monthly_alpha)} |"
    )
    lines.append(
        f"| MDD | {_fmt_pct(control_summary.mdd)} | {_fmt_pct(treatment_summary.mdd)} | "
        f"{_fmt_pct(ac['mdd_deterioration'])} deterioration |"
    )
    lines.append(
        f"| Win rate | {_fmt_pct(control_summary.win_rate)} | {_fmt_pct(treatment_summary.win_rate)} | "
        f"{_fmt_pct(treatment_summary.win_rate - control_summary.win_rate)} |"
    )
    lines.append("")
    lines.append("## Monthly Details")
    lines.append("")
    lines.append(
        "| Month | Control Ret | Treatment Ret | Control Alpha | Treatment Alpha | "
        "Alpha Delta | Jaccard | Intersect | Union Spearman | Common Spearman |"
    )
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for row in monthly.to_dict("records"):
        lines.append(
            f"| {row['month']} | {_fmt_pct(row['control_return'])} | {_fmt_pct(row['treatment_return'])} | "
            f"{_fmt_pct(row['control_alpha'])} | {_fmt_pct(row['treatment_alpha'])} | "
            f"{_fmt_pct(row['alpha_delta'])} | {_fmt_float(row['jaccard'])} | "
            f"{int(row['intersection_count'])}/{int(row['control_count'])} | "
            f"{_fmt_float(row['union_rank_spearman_filled'])} | "
            f"{_fmt_float(row['intersection_rank_spearman'])} |"
        )
    lines.append("")
    lines.append("## Turnover And Cost")
    lines.append("")
    lines.append(f"- Average Jaccard: {_fmt_float(avg_jaccard)}")
    lines.append(f"- Average monthly replacement ratio: {_fmt_pct(avg_replacement)}")
    lines.append(f"- Cost assumption: {cost_bps:.1f} bps one-way")
    lines.append(f"- Estimated one-way monthly cost drag: {_fmt_pct(one_way_cost)}")
    lines.append(f"- Estimated round-trip monthly cost drag: {_fmt_pct(round_trip_cost)}")
    if avg_jaccard < 0.40:
        lines.append("- Jaccard is below AC, so the treatment should be treated as a high-turnover architecture change.")
    lines.append("")
    lines.append("## Artifacts")
    lines.append("")
    lines.append(f"- Monthly CSV: `{monthly_path}`")
    lines.append(f"- Control picks CSV: `{control_path}`")
    lines.append(f"- Treatment picks CSV: `{treatment_path}`")
    lines.append(f"- Monthly return chart: `{chart_path}`")
    lines.append("")
    report_path.write_text("\n".join(lines), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage1-oof", type=Path, default=None)
    parser.add_argument("--treatment-fold", type=Path, default=None)
    parser.add_argument("--months", type=int, default=DEFAULT_MONTHS)
    parser.add_argument("--end-month", default=None)
    parser.add_argument("--top-n", type=int, default=DEFAULT_TOP_N)
    parser.add_argument("--cost-bps", type=float, default=30.0)
    parser.add_argument("--output-prefix", default=None)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    stage1_path = args.stage1_oof or _latest("audit002_step4_two_stage_*_stage1_oof_candidates.csv")
    treatment_path = args.treatment_fold or _latest(
        f"audit002_step5_grid_search_*_N{DEFAULT_TREATMENT_CANDIDATE_SIZE}_bins{DEFAULT_TREATMENT_LABEL_BINS}_fold_top30.csv"
    )
    prefix = args.output_prefix or f"audit002_step6_ab_validation_{datetime.now().strftime('%Y%m%d')}"
    stage1 = _read_stage1(stage1_path)
    treatment = _read_treatment(treatment_path)
    months = _select_months(stage1, treatment, args.months, args.end_month)
    monthly, control_picks, treatment_picks = _build_monthly_ab(stage1, treatment, months, top_n=args.top_n)

    control_summary = _summarize(monthly, "control_return", "control_alpha")
    treatment_summary = _summarize(monthly, "treatment_return", "treatment_alpha")
    alpha_delta = treatment_summary.monthly_alpha - control_summary.monthly_alpha
    control_mdd_mag = abs(control_summary.mdd)
    treatment_mdd_mag = abs(treatment_summary.mdd)
    mdd_deterioration = treatment_mdd_mag - control_mdd_mag
    avg_jaccard = float(monthly["jaccard"].mean())
    ac = {
        "alpha_delta": alpha_delta,
        "alpha_pass": bool(alpha_delta >= 0.003),
        "control_mdd_magnitude": control_mdd_mag,
        "treatment_mdd_magnitude": treatment_mdd_mag,
        "mdd_deterioration": mdd_deterioration,
        "mdd_pass": bool(mdd_deterioration <= 0.01),
        "average_jaccard": avg_jaccard,
        "jaccard_pass": bool(avg_jaccard >= 0.40),
    }
    ac["pass_all"] = bool(ac["alpha_pass"] and ac["mdd_pass"] and ac["jaccard_pass"])

    monthly_path = REPORT_PATH / f"{prefix}_monthly.csv"
    control_path = REPORT_PATH / f"{prefix}_control_top30.csv"
    treatment_out_path = REPORT_PATH / f"{prefix}_treatment_top30.csv"
    chart_path = REPORT_PATH / f"{prefix}_monthly_returns.png"
    report_path = REPORT_PATH / f"{prefix}.md"
    json_path = REPORT_PATH / f"{prefix}.json"

    monthly.to_csv(monthly_path, index=False, encoding="utf-8")
    control_picks.to_csv(control_path, index=False, encoding="utf-8")
    treatment_picks.to_csv(treatment_out_path, index=False, encoding="utf-8")
    _line_chart(monthly, chart_path)
    _write_report(
        report_path=report_path,
        chart_path=chart_path,
        monthly_path=monthly_path,
        control_path=control_path,
        treatment_path=treatment_out_path,
        monthly=monthly,
        control_summary=control_summary,
        treatment_summary=treatment_summary,
        ac=ac,
        stage1_path=stage1_path,
        treatment_path_in=treatment_path,
        top_n=args.top_n,
        cost_bps=args.cost_bps,
    )
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "stage1_oof": str(stage1_path),
        "treatment_fold": str(treatment_path),
        "months": months,
        "monthly_csv": str(monthly_path),
        "control_top30_csv": str(control_path),
        "treatment_top30_csv": str(treatment_out_path),
        "chart_png": str(chart_path),
        "report": str(report_path),
        "control": asdict(control_summary),
        "treatment": asdict(treatment_summary),
        "ac": ac,
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[audit002-step6] report={report_path}")
    print(
        "[audit002-step6] "
        f"alpha_delta={_fmt_pct(alpha_delta)} "
        f"mdd_deterioration={_fmt_pct(mdd_deterioration)} "
        f"avg_jaccard={avg_jaccard:.3f} "
        f"status={'PASS' if ac['pass_all'] else 'FAIL'}"
    )


if __name__ == "__main__":
    main()
