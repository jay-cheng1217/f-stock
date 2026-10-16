"""Generate V2 walk-forward rank IC and calibration diagnostics."""
from __future__ import annotations

import argparse
import json
import math
from html import escape
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


REPO_ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = REPO_ROOT / "ml" / "reports"


@dataclass(frozen=True)
class IcSummary:
    months: int
    mean_ic: float
    std_ic: float
    t_stat: float
    ic_sharpe: float
    positive_months: int
    positive_month_pct: float
    significant_months: int
    significant_month_pct: float
    pass_mean_ic_005: bool
    pass_t_stat_2: bool


def _latest(pattern: str) -> Path:
    paths = sorted(REPORT_DIR.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    if not paths:
        raise FileNotFoundError(f"No report matches {pattern!r} under {REPORT_DIR}")
    return paths[0]


def _fmt_pct(value: float | None, digits: int = 2) -> str:
    if value is None or pd.isna(value):
        return "-"
    return f"{value * 100:+.{digits}f}%"


def _fmt_float(value: float | None, digits: int = 3) -> str:
    if value is None or pd.isna(value):
        return "-"
    return f"{value:.{digits}f}"


def _ic_summary(ic_values: pd.Series, significant: pd.Series | None = None) -> IcSummary:
    values = pd.to_numeric(ic_values, errors="coerce").dropna()
    months = int(len(values))
    mean_ic = float(values.mean()) if months else float("nan")
    std_ic = float(values.std(ddof=1)) if months > 1 else float("nan")
    t_stat = float(mean_ic / (std_ic / math.sqrt(months))) if months > 1 and std_ic > 0 else float("nan")
    ic_sharpe = float(mean_ic / std_ic) if months > 1 and std_ic > 0 else float("nan")
    positive_months = int((values > 0).sum())
    if significant is None:
        significant_months = 0
    else:
        significant_months = int(significant.reindex(values.index).fillna(False).sum())
    return IcSummary(
        months=months,
        mean_ic=mean_ic,
        std_ic=std_ic,
        t_stat=t_stat,
        ic_sharpe=ic_sharpe,
        positive_months=positive_months,
        positive_month_pct=float(positive_months / months) if months else float("nan"),
        significant_months=significant_months,
        significant_month_pct=float(significant_months / months) if months else float("nan"),
        pass_mean_ic_005=bool(mean_ic > 0.05) if not pd.isna(mean_ic) else False,
        pass_t_stat_2=bool(t_stat > 2.0) if not pd.isna(t_stat) else False,
    )


def _prepare_monthly_ic(backtest_path: Path) -> tuple[pd.DataFrame, IcSummary]:
    df = pd.read_csv(backtest_path)
    required = {"month", "n", "ic"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"{backtest_path} missing required columns: {sorted(missing)}")

    out = df[["month", "n", "ic"]].copy()
    out["n"] = pd.to_numeric(out["n"], errors="coerce")
    out["ic"] = pd.to_numeric(out["ic"], errors="coerce")
    denom = (1.0 - out["ic"].pow(2)).clip(lower=1e-12)
    out["t_stat"] = out["ic"] * np.sqrt((out["n"] - 2).clip(lower=1) / denom)
    out["significant"] = out["t_stat"].abs() >= 2.0
    out["year"] = out["month"].str.slice(0, 4)
    out["ic_rolling_6m"] = out["ic"].rolling(6, min_periods=3).mean()
    out["ic_cumulative_mean"] = out["ic"].expanding(min_periods=1).mean()
    return out, _ic_summary(out["ic"], out["significant"])


def _prepare_top30_calibration(fold_path: Path) -> tuple[pd.DataFrame, pd.DataFrame, IcSummary]:
    df = pd.read_csv(fold_path)
    required = {"month", "pred_return", "trade_return_20d", "trade_excess_return_20d"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"{fold_path} missing required columns: {sorted(missing)}")

    clean = df.dropna(subset=["pred_return", "trade_excess_return_20d"]).copy()
    clean["pred_return"] = pd.to_numeric(clean["pred_return"], errors="coerce")
    clean["trade_return_20d"] = pd.to_numeric(clean["trade_return_20d"], errors="coerce")
    clean["trade_excess_return_20d"] = pd.to_numeric(
        clean["trade_excess_return_20d"], errors="coerce"
    )
    clean = clean.dropna(subset=["pred_return", "trade_excess_return_20d"])
    clean["pred_decile"] = pd.qcut(
        clean["pred_return"], q=10, labels=False, duplicates="drop"
    ) + 1

    calib = (
        clean.groupby("pred_decile", as_index=False)
        .agg(
            n=("ticker", "count"),
            pred_return_mean=("pred_return", "mean"),
            actual_excess_return_mean=("trade_excess_return_20d", "mean"),
            actual_return_mean=("trade_return_20d", "mean"),
            positive_rate=("trade_return_20d", lambda s: float((s > 0).mean())),
        )
        .sort_values("pred_decile")
    )

    monthly_top30_ic = (
        clean.groupby("month")
        .apply(
            lambda g: g["pred_return"].corr(g["trade_excess_return_20d"], method="spearman"),
            include_groups=False,
        )
        .rename("top30_internal_ic")
        .reset_index()
    )
    monthly_top30_ic["significant"] = False
    top30_summary = _ic_summary(monthly_top30_ic["top30_internal_ic"])
    return calib, monthly_top30_ic, top30_summary


def _svg_header(width: int, height: int, title: str) -> list[str]:
    return [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        "<style>",
        "text{font-family:Arial,Helvetica,sans-serif;fill:#222}",
        ".title{font-size:20px;font-weight:700}",
        ".axis{stroke:#333;stroke-width:1}",
        ".grid{stroke:#ddd;stroke-width:1}",
        ".tick{font-size:11px;fill:#555}",
        ".legend{font-size:12px}",
        "</style>",
        f'<rect x="0" y="0" width="{width}" height="{height}" fill="white"/>',
        f'<text class="title" x="{width / 2:.1f}" y="28" text-anchor="middle">{escape(title)}</text>',
    ]


def _scale(value: float, src_min: float, src_max: float, dst_min: float, dst_max: float) -> float:
    if src_max == src_min:
        return (dst_min + dst_max) / 2
    return dst_min + (value - src_min) * (dst_max - dst_min) / (src_max - src_min)


def _nice_bounds(values: list[float], include: list[float] | None = None) -> tuple[float, float]:
    finite = [float(v) for v in values if not pd.isna(v) and np.isfinite(v)]
    if include:
        finite.extend(include)
    if not finite:
        return -1.0, 1.0
    lo = min(finite)
    hi = max(finite)
    pad = max((hi - lo) * 0.12, 0.01)
    return lo - pad, hi + pad


def _axis_and_grid(
    parts: list[str],
    *,
    left: int,
    top: int,
    right: int,
    bottom: int,
    y_min: float,
    y_max: float,
    y_label: str,
) -> None:
    parts.append(f'<line class="axis" x1="{left}" y1="{bottom}" x2="{right}" y2="{bottom}"/>')
    parts.append(f'<line class="axis" x1="{left}" y1="{top}" x2="{left}" y2="{bottom}"/>')
    for frac in np.linspace(0, 1, 6):
        value = y_min + (y_max - y_min) * frac
        y = _scale(value, y_min, y_max, bottom, top)
        parts.append(f'<line class="grid" x1="{left}" y1="{y:.1f}" x2="{right}" y2="{y:.1f}"/>')
        parts.append(
            f'<text class="tick" x="{left - 8}" y="{y + 4:.1f}" text-anchor="end">{value:.2f}</text>'
        )
    parts.append(
        f'<text class="tick" x="18" y="{(top + bottom) / 2:.1f}" text-anchor="middle" '
        f'transform="rotate(-90 18 {(top + bottom) / 2:.1f})">{escape(y_label)}</text>'
    )


def _polyline(points: list[tuple[float, float]], color: str, width: float = 2.2) -> str:
    valid = [(x, y) for x, y in points if np.isfinite(x) and np.isfinite(y)]
    if len(valid) < 2:
        return ""
    encoded = " ".join(f"{x:.1f},{y:.1f}" for x, y in valid)
    return f'<polyline points="{encoded}" fill="none" stroke="{color}" stroke-width="{width}"/>'


def _legend(parts: list[str], items: list[tuple[str, str]], *, x: int, y: int) -> None:
    for idx, (label, color) in enumerate(items):
        yy = y + idx * 18
        parts.append(f'<rect x="{x}" y="{yy - 10}" width="12" height="12" fill="{color}"/>')
        parts.append(f'<text class="legend" x="{x + 18}" y="{yy}">{escape(label)}</text>')


def _plot_monthly_ic(monthly: pd.DataFrame, output: Path) -> None:
    width, height = 1180, 560
    left, right, top, bottom = 72, 1130, 58, 455
    y_min, y_max = _nice_bounds(
        monthly["ic"].tolist() + monthly["ic_rolling_6m"].dropna().tolist(), include=[0.0, 0.05]
    )
    parts = _svg_header(width, height, "V2 Walk-forward Rank IC by Month (All Universe)")
    _axis_and_grid(
        parts,
        left=left,
        top=top,
        right=right,
        bottom=bottom,
        y_min=y_min,
        y_max=y_max,
        y_label="Spearman IC",
    )
    zero_y = _scale(0, y_min, y_max, bottom, top)
    hurdle_y = _scale(0.05, y_min, y_max, bottom, top)
    parts.append(f'<line x1="{left}" y1="{zero_y:.1f}" x2="{right}" y2="{zero_y:.1f}" stroke="#333" stroke-width="1"/>')
    parts.append(
        f'<line x1="{left}" y1="{hurdle_y:.1f}" x2="{right}" y2="{hurdle_y:.1f}" '
        'stroke="#888" stroke-width="1" stroke-dasharray="5,4"/>'
    )
    n = len(monthly)
    step = (right - left) / max(n, 1)
    bar_w = step * 0.62
    roll_points: list[tuple[float, float]] = []
    for idx, row in enumerate(monthly.to_dict("records")):
        x = left + step * idx + step / 2
        ic = float(row["ic"])
        y = _scale(ic, y_min, y_max, bottom, top)
        color = "#2f8f5b" if ic >= 0 else "#c84c4c"
        parts.append(
            f'<rect x="{x - bar_w / 2:.1f}" y="{min(y, zero_y):.1f}" '
            f'width="{bar_w:.1f}" height="{abs(zero_y - y):.1f}" fill="{color}" opacity="0.84"/>'
        )
        if not pd.isna(row["ic_rolling_6m"]):
            roll_points.append((x, _scale(float(row["ic_rolling_6m"]), y_min, y_max, bottom, top)))
        parts.append(
            f'<text class="tick" x="{x:.1f}" y="{bottom + 21}" text-anchor="end" '
            f'transform="rotate(-60 {x:.1f} {bottom + 21})">{escape(str(row["month"]))}</text>'
        )
    parts.append(_polyline(roll_points, "#1f4e79", 2.8))
    _legend(parts, [("Monthly IC", "#2f8f5b"), ("6M rolling mean", "#1f4e79"), ("IC 0.05 hurdle", "#888888")], x=84, y=88)
    parts.append("</svg>")
    output.write_text("\n".join(parts), encoding="utf-8")


def _plot_calibration(calib: pd.DataFrame, output: Path) -> None:
    width, height = 960, 540
    left, right, top, bottom = 72, 910, 58, 435
    series = []
    for col in ["actual_excess_return_mean", "actual_return_mean", "pred_return_mean"]:
        series.extend((calib[col] * 100).tolist())
    y_min, y_max = _nice_bounds(series, include=[0.0])
    parts = _svg_header(width, height, "Prediction Calibration by Predicted Return Decile (Fold Top30)")
    _axis_and_grid(
        parts,
        left=left,
        top=top,
        right=right,
        bottom=bottom,
        y_min=y_min,
        y_max=y_max,
        y_label="20D return (%)",
    )
    zero_y = _scale(0, y_min, y_max, bottom, top)
    parts.append(f'<line x1="{left}" y1="{zero_y:.1f}" x2="{right}" y2="{zero_y:.1f}" stroke="#333" stroke-width="1"/>')
    deciles = calib["pred_decile"].tolist()
    x_min, x_max = min(deciles), max(deciles)

    def points(col: str) -> list[tuple[float, float]]:
        return [
            (
                _scale(float(row["pred_decile"]), x_min, x_max, left, right),
                _scale(float(row[col]) * 100, y_min, y_max, bottom, top),
            )
            for row in calib.to_dict("records")
        ]

    line_specs = [
        ("actual_excess_return_mean", "#1f77b4", "Actual excess return"),
        ("actual_return_mean", "#2ca02c", "Actual raw return"),
        ("pred_return_mean", "#ff7f0e", "Predicted excess return"),
    ]
    for col, color, _label in line_specs:
        pts = points(col)
        parts.append(_polyline(pts, color, 2.8))
        for x, y in pts:
            parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4" fill="{color}"/>')
    for d in deciles:
        x = _scale(float(d), x_min, x_max, left, right)
        parts.append(f'<text class="tick" x="{x:.1f}" y="{bottom + 22}" text-anchor="middle">{int(d)}</text>')
    parts.append(f'<text class="tick" x="{(left + right) / 2:.1f}" y="{height - 24}" text-anchor="middle">Predicted return decile, low to high</text>')
    _legend(parts, [(label, color) for _col, color, label in line_specs], x=90, y=88)
    parts.append("</svg>")
    output.write_text("\n".join(parts), encoding="utf-8")


def _plot_ic_stability(monthly: pd.DataFrame, output: Path) -> None:
    width, height = 1180, 560
    left, right, top, bottom = 72, 1130, 58, 455
    values = (
        monthly["ic"].tolist()
        + monthly["ic_rolling_6m"].dropna().tolist()
        + monthly["ic_cumulative_mean"].dropna().tolist()
    )
    y_min, y_max = _nice_bounds(values, include=[0.0, 0.05])
    parts = _svg_header(width, height, "V2 Rank IC Stability")
    _axis_and_grid(
        parts,
        left=left,
        top=top,
        right=right,
        bottom=bottom,
        y_min=y_min,
        y_max=y_max,
        y_label="Spearman IC",
    )
    zero_y = _scale(0, y_min, y_max, bottom, top)
    hurdle_y = _scale(0.05, y_min, y_max, bottom, top)
    parts.append(f'<line x1="{left}" y1="{zero_y:.1f}" x2="{right}" y2="{zero_y:.1f}" stroke="#333" stroke-width="1"/>')
    parts.append(
        f'<line x1="{left}" y1="{hurdle_y:.1f}" x2="{right}" y2="{hurdle_y:.1f}" '
        'stroke="#888" stroke-width="1" stroke-dasharray="5,4"/>'
    )
    n = len(monthly)
    step = (right - left) / max(n - 1, 1)

    def points(col: str) -> list[tuple[float, float]]:
        out: list[tuple[float, float]] = []
        for idx, row in enumerate(monthly.to_dict("records")):
            if pd.isna(row[col]):
                continue
            out.append((left + step * idx, _scale(float(row[col]), y_min, y_max, bottom, top)))
        return out

    specs = [
        ("ic", "#b0b0b0", "Monthly IC", 1.5),
        ("ic_rolling_6m", "#d62728", "6M rolling mean", 2.8),
        ("ic_cumulative_mean", "#1f77b4", "Cumulative mean", 2.8),
    ]
    for col, color, _label, width_px in specs:
        pts = points(col)
        parts.append(_polyline(pts, color, width_px))
        if col == "ic":
            for x, y in pts:
                parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3" fill="{color}"/>')
    for idx, month in enumerate(monthly["month"].tolist()):
        x = left + step * idx
        parts.append(
            f'<text class="tick" x="{x:.1f}" y="{bottom + 21}" text-anchor="end" '
            f'transform="rotate(-60 {x:.1f} {bottom + 21})">{escape(str(month))}</text>'
        )
    _legend(parts, [(label, color) for _col, color, label, _w in specs] + [("IC 0.05 hurdle", "#888888")], x=84, y=88)
    parts.append("</svg>")
    output.write_text("\n".join(parts), encoding="utf-8")


def _write_report(
    *,
    output: Path,
    backtest_path: Path,
    fold_path: Path,
    monthly: pd.DataFrame,
    calib: pd.DataFrame,
    summary: IcSummary,
    top30_summary: IcSummary,
    chart_paths: dict[str, Path],
) -> None:
    yearly = monthly.groupby("year").agg(mean_ic=("ic", "mean"), months=("month", "count")).reset_index()

    lines: list[str] = [
        "# V2 Rank IC Diagnostics",
        "",
        f"- Generated at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- Monthly IC source: `{backtest_path}`",
        f"- Calibration source: `{fold_path}`",
        "- IC scope: all-universe monthly last snapshot, target = `trade_excess_return_20d`.",
        "- Calibration scope: fold Top30 only, because the persisted fold artifact does not contain full-universe predictions.",
        "",
        "## IC Summary",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| Months | {summary.months} |",
        f"| Mean IC | {_fmt_float(summary.mean_ic, 4)} |",
        f"| IC Std | {_fmt_float(summary.std_ic, 4)} |",
        f"| IC t-stat | {_fmt_float(summary.t_stat, 2)} |",
        f"| IC Sharpe | {_fmt_float(summary.ic_sharpe, 2)} |",
        f"| Positive months | {summary.positive_months}/{summary.months} ({summary.positive_month_pct:.0%}) |",
        f"| Significant monthly IC (abs t >= 2) | {summary.significant_months}/{summary.months} ({summary.significant_month_pct:.0%}) |",
        f"| Mean IC > 0.05 | {'PASS' if summary.pass_mean_ic_005 else 'FAIL'} |",
        f"| t-stat > 2 | {'PASS' if summary.pass_t_stat_2 else 'FAIL'} |",
        "",
        "## Interpretation",
        "",
    ]

    if summary.pass_mean_ic_005 and summary.pass_t_stat_2:
        lines.append("- Verdict: IC is positive and clears both PM hurdles. Model ranking has usable predictive power.")
    elif summary.pass_t_stat_2:
        lines.append(
            "- Verdict: IC is statistically positive, but mean IC is below the 0.05 hurdle. "
            "Treat the model as weak-positive rather than strongly reliable."
        )
    else:
        lines.append(
            "- Verdict: IC does not clear the PM hurdle. Model ranking should be treated as unstable/noisy."
        )

    lines.extend(
        [
            f"- Top30 internal rank IC mean: {_fmt_float(top30_summary.mean_ic, 4)} "
            f"(t-stat {_fmt_float(top30_summary.t_stat, 2)}). This is weaker than all-universe IC.",
            "",
            "## Yearly IC",
            "",
            "| Year | Months | Mean IC |",
            "|---|---:|---:|",
        ]
    )
    for row in yearly.to_dict("records"):
        lines.append(f"| {row['year']} | {int(row['months'])} | {_fmt_float(row['mean_ic'], 4)} |")

    lines.extend(
        [
            "",
            "## Monthly IC",
            "",
            "| Month | N | IC | t-stat | Significant? |",
            "|---|---:|---:|---:|---|",
        ]
    )
    for row in monthly.to_dict("records"):
        lines.append(
            f"| {row['month']} | {int(row['n'])} | {_fmt_float(row['ic'], 4)} | "
            f"{_fmt_float(row['t_stat'], 2)} | {'YES' if row['significant'] else 'NO'} |"
        )

    lines.extend(
        [
            "",
            "## Calibration Deciles",
            "",
            "| Decile | N | Pred excess | Actual excess | Actual raw | Positive rate |",
            "|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in calib.to_dict("records"):
        lines.append(
            f"| {int(row['pred_decile'])} | {int(row['n'])} | {_fmt_pct(row['pred_return_mean'])} | "
            f"{_fmt_pct(row['actual_excess_return_mean'])} | {_fmt_pct(row['actual_return_mean'])} | "
            f"{row['positive_rate']:.0%} |"
        )

    lines.extend(
        [
            "",
            "## Charts",
            "",
            f"- Rank IC by month: `{chart_paths['monthly_ic']}`",
            f"- Prediction calibration deciles: `{chart_paths['calibration']}`",
            f"- IC stability: `{chart_paths['stability']}`",
            "",
        ]
    )
    output.write_text("\n".join(lines), encoding="utf-8")


def run(args: argparse.Namespace) -> dict[str, Any]:
    backtest_path = Path(args.backtest_csv) if args.backtest_csv else _latest("v2_backtest_*.csv")
    fold_path = Path(args.fold_top30_csv) if args.fold_top30_csv else _latest("v2_fold_top30_*.csv")
    out_prefix = args.output_prefix or f"v2_rank_ic_diagnostics_{datetime.now().strftime('%Y%m%d')}"

    monthly, summary = _prepare_monthly_ic(backtest_path)
    calib, monthly_top30_ic, top30_summary = _prepare_top30_calibration(fold_path)

    monthly_csv = REPORT_DIR / f"{out_prefix}_monthly_ic.csv"
    calib_csv = REPORT_DIR / f"{out_prefix}_calibration_deciles.csv"
    top30_ic_csv = REPORT_DIR / f"{out_prefix}_top30_internal_ic.csv"
    monthly.to_csv(monthly_csv, index=False, encoding="utf-8-sig")
    calib.to_csv(calib_csv, index=False, encoding="utf-8-sig")
    monthly_top30_ic.to_csv(top30_ic_csv, index=False, encoding="utf-8-sig")

    chart_paths = {
        "monthly_ic": REPORT_DIR / f"{out_prefix}_rank_ic_by_month.svg",
        "calibration": REPORT_DIR / f"{out_prefix}_calibration_deciles.svg",
        "stability": REPORT_DIR / f"{out_prefix}_ic_stability.svg",
    }
    _plot_monthly_ic(monthly, chart_paths["monthly_ic"])
    _plot_calibration(calib, chart_paths["calibration"])
    _plot_ic_stability(monthly, chart_paths["stability"])

    report_path = REPORT_DIR / f"{out_prefix}.md"
    _write_report(
        output=report_path,
        backtest_path=backtest_path,
        fold_path=fold_path,
        monthly=monthly,
        calib=calib,
        summary=summary,
        top30_summary=top30_summary,
        chart_paths=chart_paths,
    )

    json_path = REPORT_DIR / f"{out_prefix}.json"
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "backtest_csv": str(backtest_path),
        "fold_top30_csv": str(fold_path),
        "monthly_ic_csv": str(monthly_csv),
        "calibration_deciles_csv": str(calib_csv),
        "top30_internal_ic_csv": str(top30_ic_csv),
        "report": str(report_path),
        "charts": {k: str(v) for k, v in chart_paths.items()},
        "ic_summary": asdict(summary),
        "top30_internal_ic_summary": asdict(top30_summary),
    }
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"[v2-rank-ic] report={report_path}")
    print(f"[v2-rank-ic] mean_ic={summary.mean_ic:.4f} t_stat={summary.t_stat:.2f}")
    print(f"[v2-rank-ic] calibration={calib_csv}")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate V2 rank IC diagnostics.")
    parser.add_argument("--backtest-csv", default=None, help="Path to v2_backtest_*.csv")
    parser.add_argument("--fold-top30-csv", default=None, help="Path to v2_fold_top30_*.csv")
    parser.add_argument("--output-prefix", default=None)
    args = parser.parse_args()
    run(args)


if __name__ == "__main__":
    main()
