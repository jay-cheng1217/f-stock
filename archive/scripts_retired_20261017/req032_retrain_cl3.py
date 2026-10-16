"""REQ-032 two-stage retrain CL3 validation.

This is a read-only validation script. It compares the current pinned
Two-Stage fold artifact with a newly rebuilt Stage1/Stage2 fold artifact and
checks the REQ-032 holdout and CL3 gates before any production pin can change.
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

import lightgbm as lgb
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ml.config import MODEL_DIR, REPORT_DIR  # noqa: E402
from ml.dataset import build_dataset  # noqa: E402


RAW_RETURN_COL = "trade_return_20d"
EXCESS_RETURN_COL = "trade_excess_return_20d"
TOP_N = 30


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


def _latest(pattern: str, base: Path) -> Path:
    paths = sorted(base.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    if not paths:
        raise FileNotFoundError(f"No artifact matches {pattern!r} under {base}")
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


def _read_fold(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path, dtype={"ticker": str})
    frame["month"] = frame["month"].astype(str)
    frame["Date"] = pd.to_datetime(frame["Date"], errors="coerce")
    for col in ["rank", "stage1_rank", "stage1_score", "rank_score", RAW_RETURN_COL, EXCESS_RETURN_COL]:
        if col in frame.columns:
            frame[col] = pd.to_numeric(frame[col], errors="coerce")
    return frame


def _monthly_returns(frame: pd.DataFrame, months: list[str]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for month in months:
        picks = frame[frame["month"] == month].sort_values(["rank", "ticker"]).head(TOP_N)
        if picks.empty:
            continue
        rows.append(
            {
                "month": month,
                "return": float(picks[RAW_RETURN_COL].mean()),
                "alpha": float(picks[EXCESS_RETURN_COL].mean()),
                "win_rate": float((picks[RAW_RETURN_COL] > 0).mean()),
                "top10_win_rate": float((picks[picks["rank"] <= 10][RAW_RETURN_COL] > 0).mean()),
                "top10_return": float(picks[picks["rank"] <= 10][RAW_RETURN_COL].mean()),
                "repeat_count": int(len(picks) - picks["ticker"].nunique()),
                "position_rows": int(len(picks)),
            }
        )
    return pd.DataFrame(rows)


def _summarize(monthly: pd.DataFrame, variant: str) -> PerformanceSummary:
    returns = monthly["return"]
    mdd = _max_drawdown(returns)
    return PerformanceSummary(
        variant=variant,
        months=int(len(monthly)),
        monthly_return=float(returns.mean()),
        monthly_alpha=float(monthly["alpha"].mean()),
        mdd=mdd,
        sharpe=_sharpe(returns),
        calmar=_calmar(returns, mdd),
        win_rate=float((returns > 0).mean()),
    )


def _regression_ratio(before: float, after: float) -> float:
    if not math.isfinite(before) or before == 0:
        return 0.0
    return max(0.0, before - after) / abs(before)


def _cl3(old: PerformanceSummary, new: PerformanceSummary) -> dict[str, Any]:
    alpha_sacrifice = max(0.0, old.monthly_alpha - new.monthly_alpha)
    mdd_delta = abs(old.mdd) - abs(new.mdd)
    sharpe_regression = _regression_ratio(old.sharpe, new.sharpe)
    calmar_regression = _regression_ratio(old.calmar, new.calmar)
    checks = {
        "alpha_sacrifice": {"value": alpha_sacrifice, "threshold": 0.005, "pass": alpha_sacrifice <= 0.005},
        "mdd_not_worse": {"value": mdd_delta, "threshold": 0.0, "pass": mdd_delta >= 0.0},
        "sharpe_regression": {"value": sharpe_regression, "threshold": 0.05, "pass": sharpe_regression <= 0.05},
        "calmar_regression": {"value": calmar_regression, "threshold": 0.05, "pass": calmar_regression <= 0.05},
    }
    return {"checks": checks, "overall_pass": all(item["pass"] for item in checks.values())}


def _common_months(old_fold: pd.DataFrame, new_fold: pd.DataFrame, start: str, end: str) -> list[str]:
    common = sorted(set(old_fold["month"]).intersection(set(new_fold["month"])))
    selected = [month for month in common if start <= month <= end]
    if not selected:
        raise ValueError(f"No common months in {start}..{end}")
    return selected


def _holdout_months(dataset: pd.DataFrame, months: int = 3) -> list[str]:
    month_index = sorted(dataset["Date"].dt.to_period("M").astype(str).unique())
    return month_index[-months:]


def _stage1_score_distribution(
    old_meta_path: Path,
    new_meta_path: Path,
    *,
    holdout_month_count: int,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    dataset = build_dataset(verbose=False)
    dataset = dataset.dropna(subset=[EXCESS_RETURN_COL]).copy()
    dataset["Date"] = pd.to_datetime(dataset["Date"], errors="coerce")
    dataset["ticker"] = dataset["ticker"].astype(str)
    dataset["month"] = dataset["Date"].dt.to_period("M").astype(str)
    months = _holdout_months(dataset, holdout_month_count)
    holdout = dataset[dataset["month"].isin(months)].copy()
    holdout = holdout.groupby(["month", "ticker"], sort=False).tail(1).copy()

    rows: list[dict[str, Any]] = []
    meta_info: dict[str, Any] = {"holdout_months": months, "holdout_rows": int(len(holdout))}
    for label, meta_path in [("old_stage1", old_meta_path), ("new_stage1", new_meta_path)]:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        feature_cols = list(meta["feature_columns"])
        missing = [col for col in feature_cols if col not in holdout.columns]
        if missing:
            raise ValueError(f"{label} missing feature columns: {missing[:8]}")
        model = lgb.Booster(model_file=meta["model_file"])
        scores = pd.Series(model.predict(holdout[feature_cols].values), name="stage1_score")
        rows.append(
            {
                "model": label,
                "meta": str(meta_path),
                "trained_at": meta.get("trained_at"),
                "date_range": str(meta.get("date_range")),
                "n": int(scores.notna().sum()),
                "mean": float(scores.mean()),
                "median": float(scores.median()),
                "p25": float(scores.quantile(0.25)),
                "p75": float(scores.quantile(0.75)),
            }
        )
    return pd.DataFrame(rows), meta_info


def _top10_holdout_stats(fold: pd.DataFrame, months: list[str]) -> dict[str, Any]:
    top10 = fold[(fold["month"].isin(months)) & (fold["rank"] <= 10)].copy()
    return {
        "months": months,
        "rows": int(len(top10)),
        "win_rate": float((top10[RAW_RETURN_COL] > 0).mean()) if len(top10) else float("nan"),
        "avg_return": float(top10[RAW_RETURN_COL].mean()) if len(top10) else float("nan"),
        "avg_alpha": float(top10[EXCESS_RETURN_COL].mean()) if len(top10) else float("nan"),
    }


def _repeat_diagnostics(fold: pd.DataFrame, months: list[str]) -> dict[str, Any]:
    frame = fold[fold["month"].isin(months)].sort_values(["month", "rank"]).copy()
    total = int(len(frame))
    unique = int(frame["ticker"].nunique())
    repeated_rows = int(total - unique)
    repeat_tickers = frame["ticker"].value_counts()
    return {
        "rows": total,
        "unique_tickers": unique,
        "repeat_rows": repeated_rows,
        "repeat_ratio": float(repeated_rows / total) if total else float("nan"),
        "top_repeat_tickers": repeat_tickers[repeat_tickers > 1].head(10).to_dict(),
    }


def _daily_k_cutoff() -> str:
    """Return the latest raw daily-K date available on disk."""
    daily_dir = Path("日K資料")
    latest: pd.Timestamp | None = None
    for path in daily_dir.glob("*.csv"):
        try:
            frame = pd.read_csv(path, usecols=["Date"])
        except Exception:
            continue
        if frame.empty:
            continue
        date = pd.to_datetime(frame["Date"], errors="coerce").max()
        if pd.isna(date):
            continue
        latest = date if latest is None or date > latest else latest
    return str(latest.date()) if latest is not None else "unknown"


def _write_report(
    path: Path,
    *,
    metadata: dict[str, Any],
    score_dist: pd.DataFrame,
    old_summary: PerformanceSummary,
    new_summary: PerformanceSummary,
    cl3: dict[str, Any],
    monthly: pd.DataFrame,
    top10: dict[str, Any],
    repeats: dict[str, Any],
    score_dist_csv: Path,
    monthly_csv: Path,
    json_path: Path,
) -> None:
    lines: list[str] = [
        "# REQ-032 Two-Stage Model Retrain CL3",
        "",
        f"- Generated at: `{metadata['generated_at']}`",
        f"- Data cutoff: daily K `{metadata['daily_k_cutoff']}`, target dataset `{metadata['target_date_range']}`",
        f"- Old Stage1 meta: `{metadata['old_stage1_meta']}`",
        f"- New Stage1 meta: `{metadata['new_stage1_meta']}`",
        f"- New Stage2 meta: `{metadata['new_stage2_meta']}`",
        f"- Old fold: `{metadata['old_fold']}`",
        f"- New fold: `{metadata['new_fold']}`",
        "",
        "## Stage1 Holdout Score Distribution",
        "",
        "| Model | Trained At | N | Mean | Median | P25 | P75 | AC |",
        "|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in score_dist.to_dict("records"):
        median_pass = float(row["median"]) >= 0.055
        lines.append(
            f"| {row['model']} | {row['trained_at']} | {int(row['n'])} | "
            f"{_fmt_float(row['mean'], 4)} | {_fmt_float(row['median'], 4)} | "
            f"{_fmt_float(row['p25'], 4)} | {_fmt_float(row['p75'], 4)} | "
            f"{'PASS' if median_pass else 'FAIL'} |"
        )
    lines.extend(
        [
            "",
            f"- Holdout months: `{', '.join(metadata['holdout_months'])}`",
            f"- New Stage1 median threshold: `0.055`",
            "",
            "## Stage2 Top10 Holdout",
            "",
            f"- Months: `{', '.join(top10['months'])}`",
            f"- Rows: `{top10['rows']}`",
            f"- Top10 win rate: `{_fmt_pct(top10['win_rate'])}` (threshold `+40.00%`)",
            f"- Top10 average return: `{_fmt_pct(top10['avg_return'])}`",
            f"- Top10 average alpha: `{_fmt_pct(top10['avg_alpha'])}`",
            "",
            "## CL3",
            "",
            "| Check | Value | Threshold | Pass |",
            "|---|---:|---:|:---:|",
        ]
    )
    for name, item in cl3["checks"].items():
        lines.append(
            f"| {name} | {_fmt_pct(item['value'])} | {_fmt_pct(item['threshold'])} | "
            f"{'PASS' if item['pass'] else 'FAIL'} |"
        )
    lines.append("")
    lines.append(f"Overall: **{'PASS' if cl3['overall_pass'] else 'FAIL'}**")
    lines.extend(
        [
            "",
            "## Performance",
            "",
            "| Metric | Old Production Fold | New Retrain Fold | Delta |",
            "|---|---:|---:|---:|",
            f"| Monthly return | {_fmt_pct(old_summary.monthly_return)} | {_fmt_pct(new_summary.monthly_return)} | {_fmt_pct(new_summary.monthly_return - old_summary.monthly_return)} |",
            f"| Monthly alpha | {_fmt_pct(old_summary.monthly_alpha)} | {_fmt_pct(new_summary.monthly_alpha)} | {_fmt_pct(new_summary.monthly_alpha - old_summary.monthly_alpha)} |",
            f"| MDD | {_fmt_pct(old_summary.mdd)} | {_fmt_pct(new_summary.mdd)} | {_fmt_pct(abs(old_summary.mdd) - abs(new_summary.mdd))} (positive = improved) |",
            f"| Sharpe | {_fmt_float(old_summary.sharpe)} | {_fmt_float(new_summary.sharpe)} | {_fmt_float(new_summary.sharpe - old_summary.sharpe)} |",
            f"| Calmar | {_fmt_float(old_summary.calmar)} | {_fmt_float(new_summary.calmar)} | {_fmt_float(new_summary.calmar - old_summary.calmar)} |",
            f"| Positive months | {_fmt_pct(old_summary.win_rate)} | {_fmt_pct(new_summary.win_rate)} | {_fmt_pct(new_summary.win_rate - old_summary.win_rate)} |",
            "",
            "## Repeat Diagnostics",
            "",
            "| Model | Rows | Unique tickers | Repeat rows | Repeat ratio | Top repeat tickers |",
            "|---|---:|---:|---:|---:|---|",
        ]
    )
    for label in ["old", "new"]:
        item = repeats[label]
        lines.append(
            f"| {label} | {item['rows']} | {item['unique_tickers']} | {item['repeat_rows']} | "
            f"{_fmt_pct(item['repeat_ratio'])} | `{json.dumps(item['top_repeat_tickers'], ensure_ascii=False)}` |"
        )
    lines.extend(
        [
            "",
            "## Verdict",
            "",
        ]
    )
    stage1_pass = bool(float(score_dist.loc[score_dist["model"] == "new_stage1", "median"].iloc[0]) >= 0.055)
    top10_pass = bool(float(top10["win_rate"]) >= 0.40)
    if stage1_pass and top10_pass and cl3["overall_pass"]:
        lines.append("REQ-032 passes all gates. PM may open a deployment ticket; production pins were not changed by this audit.")
    else:
        failed = []
        if not stage1_pass:
            failed.append("Stage1 holdout median score < 0.055")
        if not top10_pass:
            failed.append("Stage2 Top10 holdout win rate < 40%")
        if not cl3["overall_pass"]:
            failed.append("CL3 failed")
        lines.append(
            "REQ-032 does **not** clear promotion gates: "
            + "; ".join(failed)
            + ". Keep production pinned to the current Champion models."
        )
    lines.extend(
        [
            "",
            "## Artifacts",
            "",
            f"- Stage1 score distribution CSV: `{score_dist_csv}`",
            f"- Monthly comparison CSV: `{monthly_csv}`",
            f"- JSON: `{json_path}`",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--old-stage1-meta", type=Path, default=Path(MODEL_DIR) / "lgbm_v2_20260508_200128_meta.json")
    parser.add_argument("--new-stage1-meta", type=Path, default=None)
    parser.add_argument("--new-stage2-meta", type=Path, default=None)
    parser.add_argument(
        "--old-fold",
        type=Path,
        default=Path(REPORT_DIR) / "audit002_step5_grid_search_20260509_N75_bins5_fold_top30.csv",
    )
    parser.add_argument("--new-fold", type=Path, default=None)
    parser.add_argument("--start-month", default="2024-01")
    parser.add_argument("--end-month", default="2026-04")
    parser.add_argument("--holdout-month-count", type=int, default=3)
    parser.add_argument("--output-prefix", default=None)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report_dir = Path(REPORT_DIR)
    model_dir = Path(MODEL_DIR)
    new_stage1_meta = args.new_stage1_meta or _latest("lgbm_v2_*_meta.json", model_dir)
    new_stage2_meta = args.new_stage2_meta or _latest("lgbm_two_stage_ranker_*_meta.json", model_dir)
    new_fold = args.new_fold or _latest("req032_step4_two_stage_*_top75_fold_top30.csv", report_dir)
    prefix = args.output_prefix or f"req032_retrain_cl3_{datetime.now().strftime('%Y%m%d')}"

    old_fold = _read_fold(args.old_fold)
    new_fold_frame = _read_fold(new_fold)
    months = _common_months(old_fold, new_fold_frame, args.start_month, args.end_month)
    old_monthly = _monthly_returns(old_fold, months)
    new_monthly = _monthly_returns(new_fold_frame, months)
    merged_monthly = old_monthly.merge(new_monthly, on="month", suffixes=("_old", "_new"))

    old_summary = _summarize(old_monthly, "old_two_stage")
    new_summary = _summarize(new_monthly, "new_two_stage")
    cl3 = _cl3(old_summary, new_summary)
    score_dist, holdout_meta = _stage1_score_distribution(
        args.old_stage1_meta,
        new_stage1_meta,
        holdout_month_count=args.holdout_month_count,
    )
    top10 = _top10_holdout_stats(new_fold_frame, holdout_meta["holdout_months"])
    repeats = {
        "old": _repeat_diagnostics(old_fold, months),
        "new": _repeat_diagnostics(new_fold_frame, months),
    }

    prefix_path = report_dir / prefix
    score_csv = Path(f"{prefix_path}_stage1_score_distribution.csv")
    monthly_csv = Path(f"{prefix_path}_monthly.csv")
    report_path = Path(f"{prefix_path}.md")
    json_path = Path(f"{prefix_path}.json")
    score_dist.to_csv(score_csv, index=False, encoding="utf-8")
    merged_monthly.to_csv(monthly_csv, index=False, encoding="utf-8")

    metadata = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "daily_k_cutoff": _daily_k_cutoff(),
        "target_date_range": json.loads(new_stage1_meta.read_text(encoding="utf-8")).get("date_range"),
        "old_stage1_meta": str(args.old_stage1_meta),
        "new_stage1_meta": str(new_stage1_meta),
        "new_stage2_meta": str(new_stage2_meta),
        "old_fold": str(args.old_fold),
        "new_fold": str(new_fold),
        "months": months,
        "holdout_months": holdout_meta["holdout_months"],
    }
    payload = {
        "metadata": metadata,
        "stage1_score_distribution": score_dist.to_dict("records"),
        "top10_holdout": top10,
        "old_summary": asdict(old_summary),
        "new_summary": asdict(new_summary),
        "cl3": cl3,
        "repeat_diagnostics": repeats,
        "monthly_csv": str(monthly_csv),
        "score_distribution_csv": str(score_csv),
        "report": str(report_path),
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_report(
        report_path,
        metadata=metadata,
        score_dist=score_dist,
        old_summary=old_summary,
        new_summary=new_summary,
        cl3=cl3,
        monthly=merged_monthly,
        top10=top10,
        repeats=repeats,
        score_dist_csv=score_csv,
        monthly_csv=monthly_csv,
        json_path=json_path,
    )
    print(f"[req032] report={report_path}")
    print(
        "[req032] "
        f"stage1_median={score_dist.loc[score_dist['model'] == 'new_stage1', 'median'].iloc[0]:.4f} "
        f"top10_win={top10['win_rate']:.2%} "
        f"alpha_delta={new_summary.monthly_alpha - old_summary.monthly_alpha:+.4%} "
        f"mdd_delta={abs(old_summary.mdd) - abs(new_summary.mdd):+.4%} "
        f"overall={'PASS' if cl3['overall_pass'] else 'FAIL'}"
    )


if __name__ == "__main__":
    main()
