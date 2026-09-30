"""AUDIT-002 Step 2: feature-level IC cleanup and walk-forward replay.

This is a research audit script. It uses the existing V2 walk-forward artifact
as the baseline, computes monthly feature IC on the same out-of-fold snapshots,
removes weak features by mean absolute IC, then reruns the V2 walk-forward with
the remaining feature set.
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
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ml.config import REPORT_DIR
from ml.dataset import build_dataset, get_feature_columns
from scripts.train_v2 import (
    V2_EARLY_STOPPING,
    V2_EMBARGO_TRADING_DAYS,
    V2_NUM_ROUNDS,
    V2_PARAMS,
    V2_TRAIN_MONTHS,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = Path(REPORT_DIR)
TARGET_COL = "trade_excess_return_20d"
RAW_RETURN_COL = "trade_return_20d"
DEFAULT_START = "2024-01"
DEFAULT_END = "2026-04"


@dataclass(frozen=True)
class IcSummary:
    months: int
    mean_ic: float
    std_ic: float
    t_stat: float
    ic_sharpe: float
    positive_months: int
    positive_month_pct: float


@dataclass(frozen=True)
class CalibrationSummary:
    positive_deciles: int
    total_deciles: int
    positive_decile_pct: float
    max_consecutive_negative_deciles: int
    monotonic_spearman: float


def _latest(pattern: str) -> Path:
    paths = sorted(REPORT_PATH.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    if not paths:
        raise FileNotFoundError(f"No report matches {pattern!r} under {REPORT_PATH}")
    return paths[0]


def _fmt_pct(value: float | None, digits: int = 2) -> str:
    if value is None or pd.isna(value):
        return "-"
    return f"{value * 100:+.{digits}f}%"


def _fmt_float(value: float | None, digits: int = 4) -> str:
    if value is None or pd.isna(value):
        return "-"
    return f"{value:.{digits}f}"


def _ic_summary(values: pd.Series) -> IcSummary:
    clean = pd.to_numeric(values, errors="coerce").dropna()
    months = int(len(clean))
    mean_ic = float(clean.mean()) if months else float("nan")
    std_ic = float(clean.std(ddof=1)) if months > 1 else float("nan")
    t_stat = float(mean_ic / (std_ic / math.sqrt(months))) if months > 1 and std_ic > 0 else float("nan")
    ic_sharpe = float(mean_ic / std_ic) if months > 1 and std_ic > 0 else float("nan")
    positive = int((clean > 0).sum())
    return IcSummary(
        months=months,
        mean_ic=mean_ic,
        std_ic=std_ic,
        t_stat=t_stat,
        ic_sharpe=ic_sharpe,
        positive_months=positive,
        positive_month_pct=float(positive / months) if months else float("nan"),
    )


def _month_starts(start_month: str, end_month: str) -> pd.DatetimeIndex:
    return pd.date_range(f"{start_month}-01", f"{end_month}-01", freq="MS")


def _prepare_dataset(verbose: bool = True) -> tuple[pd.DataFrame, list[str], pd.Index]:
    dataset = build_dataset(verbose=verbose)
    if TARGET_COL not in dataset.columns:
        raise ValueError(f"{TARGET_COL} missing from dataset")
    dataset = dataset.dropna(subset=[TARGET_COL]).copy()
    dataset["Date"] = pd.to_datetime(dataset["Date"])
    dataset = dataset.sort_values(["ticker", "Date"])
    feature_cols = [col for col in get_feature_columns() if col in dataset.columns]
    if not feature_cols:
        raise ValueError("No feature columns found in dataset")
    unique_dates = pd.Index(dataset["Date"].drop_duplicates().sort_values())
    return dataset, feature_cols, unique_dates


def _last_snapshot_for_month(dataset: pd.DataFrame, month_start: pd.Timestamp) -> pd.DataFrame:
    month_end = month_start + pd.offsets.MonthEnd(0)
    test = dataset[(dataset["Date"] >= month_start) & (dataset["Date"] <= month_end)]
    if test.empty:
        return pd.DataFrame()
    return test.groupby("ticker", sort=False).tail(1).copy()


def _feature_ic_table(
    dataset: pd.DataFrame,
    feature_cols: list[str],
    *,
    start_month: str,
    end_month: str,
    threshold: float,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    monthly_snapshots = [
        _last_snapshot_for_month(dataset, month_start)
        for month_start in _month_starts(start_month, end_month)
    ]
    monthly_snapshots = [frame for frame in monthly_snapshots if not frame.empty]

    for feature in feature_cols:
        month_ics: list[float] = []
        month_ns: list[int] = []
        for snapshot in monthly_snapshots:
            if feature not in snapshot.columns:
                continue
            pair = snapshot[[feature, TARGET_COL]].replace([np.inf, -np.inf], np.nan).dropna()
            if len(pair) < 30 or pair[feature].nunique(dropna=True) < 2:
                continue
            ic = pair[feature].corr(pair[TARGET_COL], method="spearman")
            if pd.notna(ic):
                month_ics.append(float(ic))
                month_ns.append(int(len(pair)))
        values = pd.Series(month_ics, dtype="float64")
        summary = _ic_summary(values)
        mean_abs_ic = abs(summary.mean_ic) if pd.notna(summary.mean_ic) else float("nan")
        decision = "remove" if pd.isna(mean_abs_ic) or mean_abs_ic < threshold else "keep"
        rows.append(
            {
                "feature": feature,
                "mean_ic": summary.mean_ic,
                "abs_mean_ic": mean_abs_ic,
                "std_ic": summary.std_ic,
                "t_stat": summary.t_stat,
                "ic_sharpe": summary.ic_sharpe,
                "months": summary.months,
                "avg_n": float(np.mean(month_ns)) if month_ns else 0.0,
                "decision": decision,
            }
        )

    out = pd.DataFrame(rows)
    out = out.sort_values(["decision", "abs_mean_ic", "feature"], ascending=[False, True, True])
    return out


def _fold_train(
    dataset: pd.DataFrame,
    feature_cols: list[str],
    unique_dates: pd.Index,
    *,
    start_month: str,
    end_month: str,
    device: str,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    params = V2_PARAMS.copy()
    if device == "gpu":
        params["device"] = "gpu"
    else:
        params.pop("device", None)

    monthly_rows: list[dict[str, Any]] = []
    fold_top30_rows: list[dict[str, Any]] = []
    for test_start in _month_starts(start_month, end_month):
        test_end = test_start + pd.offsets.MonthEnd(0)
        train_start = test_start - pd.DateOffset(months=V2_TRAIN_MONTHS)
        test = dataset[(dataset["Date"] >= test_start) & (dataset["Date"] <= test_end)]
        if test.empty:
            continue
        first_test_date = pd.Timestamp(test["Date"].min())
        test_start_idx = unique_dates.get_indexer([first_test_date])[0]
        if test_start_idx < V2_EMBARGO_TRADING_DAYS:
            continue
        embargo_cutoff = unique_dates[test_start_idx - V2_EMBARGO_TRADING_DAYS]
        train = dataset[(dataset["Date"] >= train_start) & (dataset["Date"] < embargo_cutoff)]
        if len(train) < 10000 or len(test) < 100:
            continue

        dtrain = lgb.Dataset(train[feature_cols].values, train[TARGET_COL].values, feature_name=feature_cols)
        dval = lgb.Dataset(test[feature_cols].values, test[TARGET_COL].values, feature_name=feature_cols)
        model = lgb.train(
            params,
            dtrain,
            num_boost_round=V2_NUM_ROUNDS,
            valid_sets=[dval],
            callbacks=[lgb.early_stopping(V2_EARLY_STOPPING, verbose=False)],
        )

        artifact_cols = ["ticker", "Date", RAW_RETURN_COL, TARGET_COL]
        test_df = test[artifact_cols].copy()
        test_df["pred_return"] = model.predict(test[feature_cols].values)
        last_day = test_df.groupby("ticker", sort=False).tail(1)
        last_day = last_day.sort_values("pred_return", ascending=False)
        universe_ic = last_day["pred_return"].corr(last_day[TARGET_COL], method="spearman")
        top30 = last_day.head(30)
        bot30 = last_day.tail(30)
        top30_internal_ic = top30["pred_return"].corr(top30[TARGET_COL], method="spearman")
        monthly_rows.append(
            {
                "month": test_start.strftime("%Y-%m"),
                "n": len(last_day),
                "universe_ic": float(universe_ic) if pd.notna(universe_ic) else np.nan,
                "top30_internal_ic": float(top30_internal_ic) if pd.notna(top30_internal_ic) else np.nan,
                "top30_excess": float(top30[TARGET_COL].mean()),
                "top30_raw_return": float(top30[RAW_RETURN_COL].mean()),
                "bot30_excess": float(bot30[TARGET_COL].mean()),
                "spread": float(top30[TARGET_COL].mean() - bot30[TARGET_COL].mean()),
                "top30_win_rate": float((top30[RAW_RETURN_COL] > 0).mean()),
            }
        )
        for rank, row in enumerate(top30.to_dict("records"), start=1):
            fold_top30_rows.append(
                {
                    "month": test_start.strftime("%Y-%m"),
                    "ticker": row["ticker"],
                    "date": pd.Timestamp(row["Date"]).date().isoformat(),
                    "rank": rank,
                    "pred_return": float(row["pred_return"]),
                    RAW_RETURN_COL: float(row[RAW_RETURN_COL]) if pd.notna(row[RAW_RETURN_COL]) else None,
                    TARGET_COL: float(row[TARGET_COL]) if pd.notna(row[TARGET_COL]) else None,
                }
            )
        print(
            f"[audit002-step2] {test_start.strftime('%Y-%m')} "
            f"N={len(last_day)} IC={universe_ic:+.4f} Top30IC={top30_internal_ic:+.4f}"
        )

    return pd.DataFrame(monthly_rows), pd.DataFrame(fold_top30_rows)


def _baseline_monthly(backtest_path: Path) -> pd.DataFrame:
    df = pd.read_csv(backtest_path)
    if "ic" not in df.columns or "month" not in df.columns:
        raise ValueError(f"{backtest_path} missing month/ic")
    out = df.copy()
    out = out.rename(columns={"ic": "universe_ic"})
    if "top30_excess" not in out.columns and "top30" in out.columns:
        out = out.rename(columns={"top30": "top30_excess"})
    return out


def _top30_internal_ic(fold_top30: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for month, group in fold_top30.groupby("month"):
        pair = group[["pred_return", TARGET_COL]].replace([np.inf, -np.inf], np.nan).dropna()
        if len(pair) < 5 or pair["pred_return"].nunique(dropna=True) < 2:
            ic = np.nan
        else:
            ic = pair["pred_return"].corr(pair[TARGET_COL], method="spearman")
        rows.append({"month": month, "top30_internal_ic": ic})
    return pd.DataFrame(rows)


def _calibration(fold_top30: pd.DataFrame) -> tuple[pd.DataFrame, CalibrationSummary]:
    clean = fold_top30.dropna(subset=["pred_return", TARGET_COL]).copy()
    clean["pred_decile"] = pd.qcut(clean["pred_return"], 10, labels=False, duplicates="drop") + 1
    calib = (
        clean.groupby("pred_decile", as_index=False)
        .agg(
            n=("ticker", "count"),
            pred_return_mean=("pred_return", "mean"),
            actual_excess_return_mean=(TARGET_COL, "mean"),
            actual_raw_return_mean=(RAW_RETURN_COL, "mean"),
            positive_rate=(RAW_RETURN_COL, lambda s: float((s > 0).mean())),
        )
        .sort_values("pred_decile")
    )
    values = calib["actual_excess_return_mean"].tolist()
    positive = int(sum(v > 0 for v in values))
    max_run = 0
    run = 0
    for value in values:
        if value < 0:
            run += 1
            max_run = max(max_run, run)
        else:
            run = 0
    monotonic = calib["pred_decile"].corr(calib["actual_excess_return_mean"], method="spearman")
    summary = CalibrationSummary(
        positive_deciles=positive,
        total_deciles=int(len(calib)),
        positive_decile_pct=float(positive / len(calib)) if len(calib) else float("nan"),
        max_consecutive_negative_deciles=max_run,
        monotonic_spearman=float(monotonic) if pd.notna(monotonic) else float("nan"),
    )
    return calib, summary


def _draw_calibration_png(before: pd.DataFrame, after: pd.DataFrame, output: Path) -> None:
    width, height = 1000, 620
    left, right, top, bottom = 92, 950, 72, 500
    img = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("arial.ttf", 14)
        title_font = ImageFont.truetype("arial.ttf", 22)
    except OSError:
        font = ImageFont.load_default()
        title_font = ImageFont.load_default()

    draw.text((width // 2, 24), "AUDIT-002 Step 2 Calibration: Before vs After", anchor="mm", fill="#222", font=title_font)
    values = (
        (before["actual_excess_return_mean"] * 100).tolist()
        + (after["actual_excess_return_mean"] * 100).tolist()
        + [0.0]
    )
    y_min = min(values)
    y_max = max(values)
    pad = max((y_max - y_min) * 0.12, 1.0)
    y_min -= pad
    y_max += pad

    def x_scale(decile: float) -> float:
        return left + (decile - 1) * (right - left) / 9

    def y_scale(value: float) -> float:
        return bottom - (value - y_min) * (bottom - top) / (y_max - y_min)

    for i in range(6):
        frac = i / 5
        y_val = y_min + (y_max - y_min) * frac
        y = y_scale(y_val)
        draw.line((left, y, right, y), fill="#dddddd", width=1)
        draw.text((left - 8, y), f"{y_val:.1f}", anchor="rm", fill="#555", font=font)
    zero_y = y_scale(0)
    draw.line((left, zero_y, right, zero_y), fill="#333333", width=1)
    draw.line((left, bottom, right, bottom), fill="#333333", width=1)
    draw.line((left, top, left, bottom), fill="#333333", width=1)
    draw.text((24, (top + bottom) // 2), "20D excess return (%)", anchor="mm", fill="#333", font=font)
    draw.text(((left + right) // 2, height - 44), "Predicted return decile, low to high", anchor="mm", fill="#333", font=font)

    def plot(frame: pd.DataFrame, color: str, label: str, marker: str) -> None:
        points = [
            (x_scale(float(row["pred_decile"])), y_scale(float(row["actual_excess_return_mean"]) * 100))
            for row in frame.to_dict("records")
        ]
        if len(points) > 1:
            draw.line(points, fill=color, width=3)
        for x, y in points:
            if marker == "circle":
                draw.ellipse((x - 5, y - 5, x + 5, y + 5), fill=color)
            else:
                draw.rectangle((x - 5, y - 5, x + 5, y + 5), fill=color)
        legend_y = 84 if label.startswith("Before") else 108
        draw.rectangle((760, legend_y - 7, 772, legend_y + 5), fill=color)
        draw.text((780, legend_y), label, anchor="lm", fill="#222", font=font)

    plot(before, "#1f77b4", "Before baseline", "circle")
    plot(after, "#d62728", "After feature cleanup", "square")
    for decile in range(1, 11):
        x = x_scale(decile)
        draw.text((x, bottom + 20), str(decile), anchor="mm", fill="#555", font=font)
    img.save(output)


def _comparison_row(
    label: str,
    monthly: pd.DataFrame,
    fold_top30: pd.DataFrame,
    calib_summary: CalibrationSummary,
) -> dict[str, Any]:
    universe_summary = _ic_summary(monthly["universe_ic"])
    top30_summary = _ic_summary(_top30_internal_ic(fold_top30)["top30_internal_ic"])
    return {
        "model": label,
        "universe_ic_mean": universe_summary.mean_ic,
        "universe_ic_t_stat": universe_summary.t_stat,
        "top30_internal_ic_mean": top30_summary.mean_ic,
        "top30_internal_ic_t_stat": top30_summary.t_stat,
        "positive_deciles": calib_summary.positive_deciles,
        "total_deciles": calib_summary.total_deciles,
        "positive_decile_pct": calib_summary.positive_decile_pct,
        "max_consecutive_negative_deciles": calib_summary.max_consecutive_negative_deciles,
        "calibration_spearman": calib_summary.monotonic_spearman,
        "top30_excess_mean": float(monthly["top30_excess"].mean()) if "top30_excess" in monthly else np.nan,
    }


def _write_report(
    *,
    output: Path,
    feature_ic_path: Path,
    removed_path: Path,
    comparison: pd.DataFrame,
    before_calib: pd.DataFrame,
    after_calib: pd.DataFrame,
    chart_path: Path,
    removed_features: list[str],
    kept_features: list[str],
    threshold: float,
    next_step: str,
) -> None:
    lines = [
        "# AUDIT-002 Step 2 - Feature IC Cleanup",
        "",
        f"- Generated at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- Low IC threshold: abs(mean feature IC) < {threshold:.3f}",
        f"- Kept features: {len(kept_features)}",
        f"- Removed features: {len(removed_features)}",
        f"- Feature IC table: `{feature_ic_path}`",
        f"- Removed feature list: `{removed_path}`",
        f"- Calibration chart: `{chart_path}`",
        "",
        "## Before / After",
        "",
        "| Model | Universe IC | Universe t | Top30 IC | Top30 t | Positive deciles | Max consecutive negative deciles | Calibration rho | Top30 excess | AC |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in comparison.to_dict("records"):
        pass_universe = row["universe_ic_mean"] >= 0.05
        pass_top30 = row["top30_internal_ic_mean"] > 0
        pass_calib = row["max_consecutive_negative_deciles"] < 2
        ac = "PASS" if pass_universe and pass_top30 and pass_calib else "FAIL"
        lines.append(
            f"| {row['model']} | {_fmt_float(row['universe_ic_mean'])} | {_fmt_float(row['universe_ic_t_stat'], 2)} | "
            f"{_fmt_float(row['top30_internal_ic_mean'])} | {_fmt_float(row['top30_internal_ic_t_stat'], 2)} | "
            f"{int(row['positive_deciles'])}/{int(row['total_deciles'])} ({row['positive_decile_pct']:.0%}) | "
            f"{int(row['max_consecutive_negative_deciles'])} | {_fmt_float(row['calibration_spearman'])} | "
            f"{_fmt_pct(row['top30_excess_mean'])} | {ac} |"
        )

    lines.extend(
        [
            "",
            "## Removed Feature Sample",
            "",
            "| # | Feature |",
            "|---:|---|",
        ]
    )
    for idx, feature in enumerate(removed_features[:40], start=1):
        lines.append(f"| {idx} | `{feature}` |")
    if len(removed_features) > 40:
        lines.append(f"| ... | {len(removed_features) - 40} more in CSV |")

    lines.extend(
        [
            "",
            "## Calibration Deciles - Before",
            "",
            "| Decile | N | Pred | Actual excess | Actual raw |",
            "|---:|---:|---:|---:|---:|",
        ]
    )
    for row in before_calib.to_dict("records"):
        lines.append(
            f"| {int(row['pred_decile'])} | {int(row['n'])} | {_fmt_pct(row['pred_return_mean'])} | "
            f"{_fmt_pct(row['actual_excess_return_mean'])} | {_fmt_pct(row['actual_raw_return_mean'])} |"
        )
    lines.extend(
        [
            "",
            "## Calibration Deciles - After",
            "",
            "| Decile | N | Pred | Actual excess | Actual raw |",
            "|---:|---:|---:|---:|---:|",
        ]
    )
    for row in after_calib.to_dict("records"):
        lines.append(
            f"| {int(row['pred_decile'])} | {int(row['n'])} | {_fmt_pct(row['pred_return_mean'])} | "
            f"{_fmt_pct(row['actual_excess_return_mean'])} | {_fmt_pct(row['actual_raw_return_mean'])} |"
        )

    lines.extend(
        [
            "",
            "## Verdict",
            "",
            next_step,
            "",
        ]
    )
    output.write_text("\n".join(lines), encoding="utf-8")


def run(args: argparse.Namespace) -> dict[str, Any]:
    output_prefix = args.output_prefix or f"audit002_step2_feature_ic_{datetime.now().strftime('%Y%m%d')}"
    backtest_path = Path(args.baseline_backtest_csv) if args.baseline_backtest_csv else _latest("v2_backtest_*.csv")
    fold_path = Path(args.baseline_fold_top30_csv) if args.baseline_fold_top30_csv else _latest("v2_fold_top30_*.csv")

    print(f"[audit002-step2] baseline_backtest={backtest_path}")
    print(f"[audit002-step2] baseline_fold_top30={fold_path}")
    dataset, feature_cols, unique_dates = _prepare_dataset(verbose=not args.quiet)

    feature_ic = _feature_ic_table(
        dataset,
        feature_cols,
        start_month=args.start_month,
        end_month=args.end_month,
        threshold=args.low_ic_threshold,
    )
    removed_features = feature_ic.loc[feature_ic["decision"] == "remove", "feature"].tolist()
    kept_features = [feature for feature in feature_cols if feature not in set(removed_features)]
    if not kept_features:
        raise ValueError("Feature cleanup removed every feature")

    feature_ic_path = REPORT_PATH / f"{output_prefix}_features.csv"
    removed_path = REPORT_PATH / f"{output_prefix}_removed_features.csv"
    feature_ic.to_csv(feature_ic_path, index=False, encoding="utf-8-sig")
    feature_ic[feature_ic["decision"] == "remove"].to_csv(removed_path, index=False, encoding="utf-8-sig")

    print(
        f"[audit002-step2] features total={len(feature_cols)} kept={len(kept_features)} "
        f"removed={len(removed_features)}"
    )
    after_monthly, after_fold_top30 = _fold_train(
        dataset,
        kept_features,
        unique_dates,
        start_month=args.start_month,
        end_month=args.end_month,
        device=args.device,
    )

    baseline_monthly = _baseline_monthly(backtest_path)
    baseline_fold_top30 = pd.read_csv(fold_path)
    before_calib, before_calib_summary = _calibration(baseline_fold_top30)
    after_calib, after_calib_summary = _calibration(after_fold_top30)
    comparison = pd.DataFrame(
        [
            _comparison_row("Before_baseline", baseline_monthly, baseline_fold_top30, before_calib_summary),
            _comparison_row("After_feature_cleanup", after_monthly, after_fold_top30, after_calib_summary),
        ]
    )

    after_monthly_path = REPORT_PATH / f"{output_prefix}_after_monthly.csv"
    after_fold_path = REPORT_PATH / f"{output_prefix}_after_fold_top30.csv"
    comparison_path = REPORT_PATH / f"{output_prefix}_comparison.csv"
    before_calib_path = REPORT_PATH / f"{output_prefix}_before_calibration_deciles.csv"
    after_calib_path = REPORT_PATH / f"{output_prefix}_after_calibration_deciles.csv"
    chart_path = REPORT_PATH / f"{output_prefix}_calibration.png"
    report_path = REPORT_PATH / f"{output_prefix}.md"
    json_path = REPORT_PATH / f"{output_prefix}.json"

    after_monthly.to_csv(after_monthly_path, index=False, encoding="utf-8-sig")
    after_fold_top30.to_csv(after_fold_path, index=False, encoding="utf-8-sig")
    comparison.to_csv(comparison_path, index=False, encoding="utf-8-sig")
    before_calib.to_csv(before_calib_path, index=False, encoding="utf-8-sig")
    after_calib.to_csv(after_calib_path, index=False, encoding="utf-8-sig")
    _draw_calibration_png(before_calib, after_calib, chart_path)

    after_row = comparison[comparison["model"] == "After_feature_cleanup"].iloc[0]
    if after_row["top30_internal_ic_mean"] <= 0:
        next_step = (
            "Feature cleanup did not turn Top30 IC positive. Recommendation: move to Step 3 "
            "and evaluate LambdaRank / pairwise ranking objective or a two-stage screen-then-rank architecture."
        )
    elif after_row["universe_ic_mean"] < 0.05:
        next_step = (
            "Feature cleanup improved Top30 ranking but Universe IC remains below 0.05. Recommendation: "
            "continue feature engineering before production promotion."
        )
    else:
        next_step = (
            "Feature cleanup clears the core IC hurdles. Recommendation: run a fixed-snapshot production A/B "
            "before any model promotion."
        )

    _write_report(
        output=report_path,
        feature_ic_path=feature_ic_path,
        removed_path=removed_path,
        comparison=comparison,
        before_calib=before_calib,
        after_calib=after_calib,
        chart_path=chart_path,
        removed_features=removed_features,
        kept_features=kept_features,
        threshold=args.low_ic_threshold,
        next_step=next_step,
    )

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "baseline_backtest_csv": str(backtest_path),
        "baseline_fold_top30_csv": str(fold_path),
        "feature_ic_csv": str(feature_ic_path),
        "removed_features_csv": str(removed_path),
        "after_monthly_csv": str(after_monthly_path),
        "after_fold_top30_csv": str(after_fold_path),
        "comparison_csv": str(comparison_path),
        "before_calibration_deciles_csv": str(before_calib_path),
        "after_calibration_deciles_csv": str(after_calib_path),
        "calibration_png": str(chart_path),
        "report": str(report_path),
        "features": {
            "total": len(feature_cols),
            "kept": len(kept_features),
            "removed": len(removed_features),
            "low_ic_threshold": args.low_ic_threshold,
        },
        "comparison": comparison.to_dict("records"),
        "before_calibration": asdict(before_calib_summary),
        "after_calibration": asdict(after_calib_summary),
        "verdict": next_step,
    }
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[audit002-step2] report={report_path}")
    print(f"[audit002-step2] chart={chart_path}")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="AUDIT-002 Step 2 feature-level IC cleanup.")
    parser.add_argument("--start-month", default=DEFAULT_START)
    parser.add_argument("--end-month", default=DEFAULT_END)
    parser.add_argument("--low-ic-threshold", type=float, default=0.01)
    parser.add_argument("--device", choices=["cpu", "gpu"], default="cpu")
    parser.add_argument("--baseline-backtest-csv", default=None)
    parser.add_argument("--baseline-fold-top30-csv", default=None)
    parser.add_argument("--output-prefix", default=None)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()
    run(args)


if __name__ == "__main__":
    main()
