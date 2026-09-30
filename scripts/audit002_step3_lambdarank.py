"""AUDIT-002 Step 3: LambdaRank ranking-objective experiment."""
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
from scripts.train_v2 import V2_EMBARGO_TRADING_DAYS, V2_PARAMS, V2_TRAIN_MONTHS


REPORT_PATH = Path(REPORT_DIR)
TARGET_COL = "trade_excess_return_20d"
RAW_RETURN_COL = "trade_return_20d"
DEFAULT_START = "2024-01"
DEFAULT_END = "2026-04"
DEFAULT_LABEL_BINS = 5


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


def _prepare_monthly_snapshots(verbose: bool = True) -> tuple[pd.DataFrame, list[str], pd.Index]:
    dataset = build_dataset(verbose=verbose)
    if TARGET_COL not in dataset.columns:
        raise ValueError(f"{TARGET_COL} missing from dataset")
    dataset = dataset.dropna(subset=[TARGET_COL]).copy()
    dataset["Date"] = pd.to_datetime(dataset["Date"])
    dataset = dataset.sort_values(["ticker", "Date"])
    dataset["rebalance_month"] = dataset["Date"].dt.to_period("M").astype(str)
    monthly = dataset.groupby(["rebalance_month", "ticker"], sort=False).tail(1).copy()
    monthly = monthly.sort_values(["rebalance_month", "ticker"])
    feature_cols = [col for col in get_feature_columns() if col in monthly.columns]
    unique_dates = pd.Index(dataset["Date"].drop_duplicates().sort_values())
    return monthly, feature_cols, unique_dates


def _make_rank_labels(values: pd.Series, bins: int) -> pd.Series:
    ranks = values.rank(method="first", ascending=True)
    n = len(ranks)
    if n == 0:
        return pd.Series(dtype=np.int32)
    labels = np.floor(((ranks - 1) / n) * bins).clip(lower=0, upper=bins - 1)
    return labels.astype(np.int32)


def _add_rank_labels(frame: pd.DataFrame, bins: int) -> pd.DataFrame:
    out = frame.copy()
    out["rank_label"] = (
        out.groupby("rebalance_month", group_keys=False)[TARGET_COL]
        .apply(lambda s: _make_rank_labels(s, bins))
        .astype(np.int32)
    )
    return out


def _groups_for_lgb(frame: pd.DataFrame) -> list[int]:
    return frame.groupby("rebalance_month", sort=False).size().astype(int).tolist()


def _ndcg_at_k(relevance: np.ndarray, scores: np.ndarray, k: int) -> float:
    if len(relevance) == 0:
        return float("nan")
    k = min(k, len(relevance))
    order = np.argsort(scores)[::-1][:k]
    ideal = np.argsort(relevance)[::-1][:k]
    discounts = 1.0 / np.log2(np.arange(2, k + 2))
    gains = np.power(2.0, relevance.astype(float)) - 1.0
    dcg = float(np.sum(gains[order] * discounts))
    idcg = float(np.sum(gains[ideal] * discounts))
    return dcg / idcg if idcg > 0 else float("nan")


def _lambdarank_params(device: str, label_bins: int) -> dict[str, Any]:
    params = V2_PARAMS.copy()
    params.update(
        {
            "objective": "lambdarank",
            "metric": "ndcg",
            "eval_at": [30],
            "label_gain": [float(2**i - 1) for i in range(label_bins)],
            "ndcg_eval_at": [30],
            "verbose": -1,
        }
    )
    if device == "gpu":
        params["device"] = "gpu"
    else:
        params.pop("device", None)
    return params


def _train_lambdarank_walk_forward(
    monthly: pd.DataFrame,
    feature_cols: list[str],
    unique_dates: pd.Index,
    *,
    start_month: str,
    end_month: str,
    device: str,
    label_bins: int,
    num_boost_round: int,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    params = _lambdarank_params(device, label_bins)
    monthly = _add_rank_labels(monthly, label_bins)
    monthly_rows: list[dict[str, Any]] = []
    fold_top30_rows: list[dict[str, Any]] = []

    for test_start in _month_starts(start_month, end_month):
        test_end = test_start + pd.offsets.MonthEnd(0)
        train_start = test_start - pd.DateOffset(months=V2_TRAIN_MONTHS)
        test = monthly[(monthly["Date"] >= test_start) & (monthly["Date"] <= test_end)].copy()
        if test.empty:
            continue
        first_test_date = pd.Timestamp(test["Date"].min())
        test_start_idx = unique_dates.get_indexer([first_test_date])[0]
        if test_start_idx < V2_EMBARGO_TRADING_DAYS:
            continue
        embargo_cutoff = unique_dates[test_start_idx - V2_EMBARGO_TRADING_DAYS]
        train = monthly[(monthly["Date"] >= train_start) & (monthly["Date"] < embargo_cutoff)].copy()
        if len(train) < 10000 or len(test) < 100:
            continue

        train = train.sort_values(["rebalance_month", "ticker"])
        test = test.sort_values(["rebalance_month", "ticker"])
        dtrain = lgb.Dataset(
            train[feature_cols].values,
            label=train["rank_label"].values,
            group=_groups_for_lgb(train),
            feature_name=feature_cols,
        )
        dval = lgb.Dataset(
            test[feature_cols].values,
            label=test["rank_label"].values,
            group=_groups_for_lgb(test),
            feature_name=feature_cols,
            reference=dtrain,
        )
        model = lgb.train(
            params,
            dtrain,
            num_boost_round=num_boost_round,
            valid_sets=[dval],
            callbacks=[lgb.early_stopping(30, verbose=False)],
        )
        test = test.copy()
        test["rank_score"] = model.predict(test[feature_cols].values)
        ranked = test.sort_values("rank_score", ascending=False)
        top30 = ranked.head(30)
        bot30 = ranked.tail(30)
        universe_ic = ranked["rank_score"].corr(ranked[TARGET_COL], method="spearman")
        top30_ic = top30["rank_score"].corr(top30[TARGET_COL], method="spearman")
        ndcg30 = _ndcg_at_k(
            ranked["rank_label"].to_numpy(dtype=float),
            ranked["rank_score"].to_numpy(dtype=float),
            30,
        )
        monthly_rows.append(
            {
                "month": test_start.strftime("%Y-%m"),
                "n": len(ranked),
                "universe_ic": float(universe_ic) if pd.notna(universe_ic) else np.nan,
                "top30_internal_ic": float(top30_ic) if pd.notna(top30_ic) else np.nan,
                "ndcg_at_30": float(ndcg30) if pd.notna(ndcg30) else np.nan,
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
                    "rank_score": float(row["rank_score"]),
                    "rank_label": int(row["rank_label"]),
                    RAW_RETURN_COL: float(row[RAW_RETURN_COL]) if pd.notna(row[RAW_RETURN_COL]) else None,
                    TARGET_COL: float(row[TARGET_COL]) if pd.notna(row[TARGET_COL]) else None,
                }
            )
        print(
            f"[audit002-step3] {test_start.strftime('%Y-%m')} "
            f"N={len(ranked)} IC={universe_ic:+.4f} Top30IC={top30_ic:+.4f} NDCG@30={ndcg30:.3f}"
        )

    return pd.DataFrame(monthly_rows), pd.DataFrame(fold_top30_rows)


def _baseline_comparison(step2_comparison_path: Path) -> pd.DataFrame:
    comp = pd.read_csv(step2_comparison_path)
    comp = comp.copy()
    comp["ndcg_at_30"] = np.nan
    return comp


def _top30_ic(fold: pd.DataFrame, score_col: str) -> pd.Series:
    rows = []
    for month, group in fold.groupby("month"):
        pair = group[[score_col, TARGET_COL]].replace([np.inf, -np.inf], np.nan).dropna()
        if len(pair) < 5 or pair[score_col].nunique(dropna=True) < 2:
            ic = np.nan
        else:
            ic = pair[score_col].corr(pair[TARGET_COL], method="spearman")
        rows.append({"month": month, "top30_internal_ic": ic})
    return pd.DataFrame(rows)["top30_internal_ic"]


def _calibration(fold: pd.DataFrame, score_col: str) -> tuple[pd.DataFrame, CalibrationSummary]:
    clean = fold.dropna(subset=[score_col, TARGET_COL]).copy()
    clean["pred_decile"] = pd.qcut(clean[score_col], 10, labels=False, duplicates="drop") + 1
    calib = (
        clean.groupby("pred_decile", as_index=False)
        .agg(
            n=("ticker", "count"),
            score_mean=(score_col, "mean"),
            actual_excess_return_mean=(TARGET_COL, "mean"),
            actual_raw_return_mean=(RAW_RETURN_COL, "mean"),
            positive_rate=(RAW_RETURN_COL, lambda s: float((s > 0).mean())),
        )
        .sort_values("pred_decile")
    )
    values = calib["actual_excess_return_mean"].tolist()
    positive = int(sum(v > 0 for v in values))
    run = 0
    max_run = 0
    for value in values:
        if value < 0:
            run += 1
            max_run = max(max_run, run)
        else:
            run = 0
    rho = calib["pred_decile"].corr(calib["actual_excess_return_mean"], method="spearman")
    return calib, CalibrationSummary(
        positive_deciles=positive,
        total_deciles=int(len(calib)),
        positive_decile_pct=float(positive / len(calib)) if len(calib) else float("nan"),
        max_consecutive_negative_deciles=max_run,
        monotonic_spearman=float(rho) if pd.notna(rho) else float("nan"),
    )


def _append_lambdarank_comparison(
    comparison: pd.DataFrame,
    monthly: pd.DataFrame,
    fold: pd.DataFrame,
    calib_summary: CalibrationSummary,
) -> pd.DataFrame:
    universe = _ic_summary(monthly["universe_ic"])
    top30 = _ic_summary(_top30_ic(fold, "rank_score"))
    row = {
        "model": "LambdaRank",
        "universe_ic_mean": universe.mean_ic,
        "universe_ic_t_stat": universe.t_stat,
        "top30_internal_ic_mean": top30.mean_ic,
        "top30_internal_ic_t_stat": top30.t_stat,
        "positive_deciles": calib_summary.positive_deciles,
        "total_deciles": calib_summary.total_deciles,
        "positive_decile_pct": calib_summary.positive_decile_pct,
        "max_consecutive_negative_deciles": calib_summary.max_consecutive_negative_deciles,
        "calibration_spearman": calib_summary.monotonic_spearman,
        "top30_excess_mean": float(monthly["top30_excess"].mean()),
        "ndcg_at_30": float(monthly["ndcg_at_30"].mean()),
    }
    return pd.concat([comparison, pd.DataFrame([row])], ignore_index=True)


def _draw_calibration_png(
    before: pd.DataFrame,
    cleaned: pd.DataFrame,
    lambdarank: pd.DataFrame,
    output: Path,
) -> None:
    width, height = 1050, 640
    left, right, top, bottom = 92, 990, 78, 510
    img = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("arial.ttf", 14)
        title_font = ImageFont.truetype("arial.ttf", 22)
    except OSError:
        font = ImageFont.load_default()
        title_font = ImageFont.load_default()

    draw.text((width // 2, 30), "AUDIT-002 Step 3 Calibration", anchor="mm", fill="#222", font=title_font)
    all_values = []
    for frame in [before, cleaned, lambdarank]:
        all_values.extend((frame["actual_excess_return_mean"] * 100).tolist())
    all_values.append(0.0)
    y_min = min(all_values)
    y_max = max(all_values)
    pad = max((y_max - y_min) * 0.12, 1.0)
    y_min -= pad
    y_max += pad

    def x_scale(decile: float) -> float:
        return left + (decile - 1) * (right - left) / 9

    def y_scale(value: float) -> float:
        return bottom - (value - y_min) * (bottom - top) / (y_max - y_min)

    for i in range(6):
        frac = i / 5
        y_value = y_min + (y_max - y_min) * frac
        y = y_scale(y_value)
        draw.line((left, y, right, y), fill="#dddddd", width=1)
        draw.text((left - 8, y), f"{y_value:.1f}", anchor="rm", fill="#555", font=font)
    zero_y = y_scale(0)
    draw.line((left, zero_y, right, zero_y), fill="#333333", width=1)
    draw.line((left, bottom, right, bottom), fill="#333333", width=1)
    draw.line((left, top, left, bottom), fill="#333333", width=1)
    draw.text((26, (top + bottom) // 2), "20D excess return (%)", anchor="mm", fill="#333", font=font)
    draw.text(((left + right) // 2, height - 42), "Score decile, low to high", anchor="mm", fill="#333", font=font)

    specs = [
        ("Regression", before, "#1f77b4", "circle"),
        ("Step2 cleaned", cleaned, "#d62728", "square"),
        ("LambdaRank", lambdarank, "#2ca02c", "triangle"),
    ]
    for idx, (label, frame, color, marker) in enumerate(specs):
        points = [
            (x_scale(float(row["pred_decile"])), y_scale(float(row["actual_excess_return_mean"]) * 100))
            for row in frame.to_dict("records")
        ]
        if len(points) > 1:
            draw.line(points, fill=color, width=3)
        for x, y in points:
            if marker == "circle":
                draw.ellipse((x - 5, y - 5, x + 5, y + 5), fill=color)
            elif marker == "square":
                draw.rectangle((x - 5, y - 5, x + 5, y + 5), fill=color)
            else:
                draw.polygon([(x, y - 6), (x - 6, y + 5), (x + 6, y + 5)], fill=color)
        legend_y = 92 + idx * 22
        draw.rectangle((810, legend_y - 8, 824, legend_y + 6), fill=color)
        draw.text((832, legend_y), label, anchor="lm", fill="#222", font=font)

    for decile in range(1, 11):
        x = x_scale(decile)
        draw.text((x, bottom + 20), str(decile), anchor="mm", fill="#555", font=font)
    img.save(output)


def _model_ac(row: pd.Series) -> str:
    pass_top30 = row["top30_internal_ic_mean"] > 0
    pass_universe = row["universe_ic_mean"] >= 0.03
    pass_ndcg = row.get("ndcg_at_30", np.nan) > 0.50 if pd.notna(row.get("ndcg_at_30", np.nan)) else False
    pass_calib = row["max_consecutive_negative_deciles"] < 2
    return "PASS" if pass_top30 and pass_universe and pass_ndcg and pass_calib else "FAIL"


def _write_report(
    *,
    output: Path,
    comparison: pd.DataFrame,
    lambda_calib: pd.DataFrame,
    chart_path: Path,
    monthly_path: Path,
    fold_path: Path,
    label_bins: int,
    verdict: str,
) -> None:
    lines = [
        "# AUDIT-002 Step 3 - LambdaRank Ranking Objective",
        "",
        f"- Generated at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "- Objective: LightGBM `lambdarank`, metric `ndcg`, eval_at `30`.",
        f"- Label: monthly cross-sectional `{TARGET_COL}` quantile rank, {label_bins} bins.",
        "- Group: one rebalance month snapshot = all tickers in that month.",
        f"- LambdaRank monthly artifact: `{monthly_path}`",
        f"- LambdaRank Top30 artifact: `{fold_path}`",
        f"- Calibration chart: `{chart_path}`",
        "",
        "## Regression / Step2 / LambdaRank Comparison",
        "",
        "| Model | Universe IC | Universe t | Top30 IC | Top30 t | NDCG@30 | Positive deciles | Max consecutive negative deciles | Calibration rho | Top30 excess | AC |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in comparison.to_dict("records"):
        lines.append(
            f"| {row['model']} | {_fmt_float(row['universe_ic_mean'])} | {_fmt_float(row['universe_ic_t_stat'], 2)} | "
            f"{_fmt_float(row['top30_internal_ic_mean'])} | {_fmt_float(row['top30_internal_ic_t_stat'], 2)} | "
            f"{_fmt_float(row.get('ndcg_at_30'), 3)} | "
            f"{int(row['positive_deciles'])}/{int(row['total_deciles'])} ({row['positive_decile_pct']:.0%}) | "
            f"{int(row['max_consecutive_negative_deciles'])} | {_fmt_float(row['calibration_spearman'])} | "
            f"{_fmt_pct(row['top30_excess_mean'])} | {_model_ac(pd.Series(row))} |"
        )

    lines.extend(
        [
            "",
            "## LambdaRank Calibration Deciles",
            "",
            "| Decile | N | Score | Actual excess | Actual raw | Positive rate |",
            "|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in lambda_calib.to_dict("records"):
        lines.append(
            f"| {int(row['pred_decile'])} | {int(row['n'])} | {_fmt_float(row['score_mean'])} | "
            f"{_fmt_pct(row['actual_excess_return_mean'])} | {_fmt_pct(row['actual_raw_return_mean'])} | "
            f"{row['positive_rate']:.0%} |"
        )

    lines.extend(["", "## Verdict", "", verdict, ""])
    output.write_text("\n".join(lines), encoding="utf-8")


def run(args: argparse.Namespace) -> dict[str, Any]:
    prefix = args.output_prefix or f"audit002_step3_lambdarank_{datetime.now().strftime('%Y%m%d')}"
    step2_comparison_path = Path(args.step2_comparison_csv) if args.step2_comparison_csv else _latest("audit002_step2_feature_ic_*_comparison.csv")
    before_calib_path = Path(args.before_calibration_csv) if args.before_calibration_csv else _latest("audit002_step2_feature_ic_*_before_calibration_deciles.csv")
    cleaned_calib_path = Path(args.cleaned_calibration_csv) if args.cleaned_calibration_csv else _latest("audit002_step2_feature_ic_*_after_calibration_deciles.csv")

    monthly, feature_cols, unique_dates = _prepare_monthly_snapshots(verbose=not args.quiet)
    lr_monthly, lr_fold = _train_lambdarank_walk_forward(
        monthly,
        feature_cols,
        unique_dates,
        start_month=args.start_month,
        end_month=args.end_month,
        device=args.device,
        label_bins=args.label_bins,
        num_boost_round=args.num_boost_round,
    )
    lr_calib, lr_calib_summary = _calibration(lr_fold, "rank_score")
    comparison = _baseline_comparison(step2_comparison_path)
    comparison = _append_lambdarank_comparison(comparison, lr_monthly, lr_fold, lr_calib_summary)

    before_calib = pd.read_csv(before_calib_path)
    cleaned_calib = pd.read_csv(cleaned_calib_path)
    if "pred_return_mean" in before_calib.columns:
        before_calib = before_calib.rename(columns={"pred_return_mean": "score_mean"})
    if "pred_return_mean" in cleaned_calib.columns:
        cleaned_calib = cleaned_calib.rename(columns={"pred_return_mean": "score_mean"})

    monthly_path = REPORT_PATH / f"{prefix}_monthly.csv"
    fold_path = REPORT_PATH / f"{prefix}_fold_top30.csv"
    calib_path = REPORT_PATH / f"{prefix}_calibration_deciles.csv"
    comparison_path = REPORT_PATH / f"{prefix}_comparison.csv"
    chart_path = REPORT_PATH / f"{prefix}_calibration.png"
    report_path = REPORT_PATH / f"{prefix}.md"
    json_path = REPORT_PATH / f"{prefix}.json"

    lr_monthly.to_csv(monthly_path, index=False, encoding="utf-8-sig")
    lr_fold.to_csv(fold_path, index=False, encoding="utf-8-sig")
    lr_calib.to_csv(calib_path, index=False, encoding="utf-8-sig")
    comparison.to_csv(comparison_path, index=False, encoding="utf-8-sig")
    _draw_calibration_png(before_calib, cleaned_calib, lr_calib, chart_path)

    lambda_row = comparison[comparison["model"] == "LambdaRank"].iloc[0]
    if _model_ac(lambda_row) == "PASS":
        verdict = (
            "LambdaRank clears Step 3 AC. Recommendation: keep as research candidate and run "
            "same-snapshot production A/B before any promotion."
        )
    elif lambda_row["top30_internal_ic_mean"] > 0 and lambda_row["universe_ic_mean"] < 0.03:
        verdict = (
            "LambdaRank turns Top30 IC positive but Universe IC is below 0.03. Recommendation: "
            "evaluate two-stage architecture: Stage 1 regression screen, Stage 2 LambdaRank reorder."
        )
    else:
        verdict = (
            "LambdaRank does not solve the ranking problem. Recommendation: move to two-stage "
            "screen-then-rank design or revisit label/event segmentation before production work."
        )

    _write_report(
        output=report_path,
        comparison=comparison,
        lambda_calib=lr_calib,
        chart_path=chart_path,
        monthly_path=monthly_path,
        fold_path=fold_path,
        label_bins=args.label_bins,
        verdict=verdict,
    )

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "step2_comparison_csv": str(step2_comparison_path),
        "monthly_csv": str(monthly_path),
        "fold_top30_csv": str(fold_path),
        "calibration_deciles_csv": str(calib_path),
        "comparison_csv": str(comparison_path),
        "calibration_png": str(chart_path),
        "report": str(report_path),
        "label_bins": args.label_bins,
        "group_definition": "one rebalance month snapshot = all tickers in that month",
        "comparison": comparison.to_dict("records"),
        "lambda_calibration": asdict(lr_calib_summary),
        "verdict": verdict,
    }
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[audit002-step3] report={report_path}")
    print(f"[audit002-step3] chart={chart_path}")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="AUDIT-002 Step 3 LambdaRank experiment.")
    parser.add_argument("--start-month", default=DEFAULT_START)
    parser.add_argument("--end-month", default=DEFAULT_END)
    parser.add_argument("--device", choices=["cpu", "gpu"], default="cpu")
    parser.add_argument("--label-bins", type=int, default=DEFAULT_LABEL_BINS)
    parser.add_argument("--num-boost-round", type=int, default=300)
    parser.add_argument("--step2-comparison-csv", default=None)
    parser.add_argument("--before-calibration-csv", default=None)
    parser.add_argument("--cleaned-calibration-csv", default=None)
    parser.add_argument("--output-prefix", default=None)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()
    run(args)


if __name__ == "__main__":
    main()
