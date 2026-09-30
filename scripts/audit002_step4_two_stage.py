"""AUDIT-002 Step 4: two-stage regression screen + LambdaRank reorder."""
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
from ml.two_stage_model import TwoStageModel, TwoStagePredictionConfig
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


def _prepare_dataset(verbose: bool = True) -> tuple[pd.DataFrame, pd.DataFrame, list[str], pd.Index]:
    dataset = build_dataset(verbose=verbose)
    if TARGET_COL not in dataset.columns:
        raise ValueError(f"{TARGET_COL} missing from dataset")
    dataset = dataset.dropna(subset=[TARGET_COL]).copy()
    dataset["Date"] = pd.to_datetime(dataset["Date"])
    dataset = dataset.sort_values(["ticker", "Date"])
    dataset["rebalance_month"] = dataset["Date"].dt.to_period("M").astype(str)
    monthly = dataset.groupby(["rebalance_month", "ticker"], sort=False).tail(1).copy()
    monthly = monthly.sort_values(["rebalance_month", "ticker"])
    feature_cols = [col for col in get_feature_columns() if col in dataset.columns]
    unique_dates = pd.Index(dataset["Date"].drop_duplicates().sort_values())
    return dataset, monthly, feature_cols, unique_dates


def _regression_params(device: str) -> dict[str, Any]:
    params = V2_PARAMS.copy()
    if device == "gpu":
        params["device"] = "gpu"
    else:
        params.pop("device", None)
    return params


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


def _train_stage1_model(
    dataset: pd.DataFrame,
    feature_cols: list[str],
    unique_dates: pd.Index,
    month_start: pd.Timestamp,
    *,
    device: str,
    num_boost_round: int,
) -> tuple[lgb.Booster | None, pd.Timestamp | None, int]:
    first_candidates = dataset[(dataset["Date"] >= month_start) & (dataset["Date"] <= month_start + pd.offsets.MonthEnd(0))]
    if first_candidates.empty:
        return None, None, 0
    first_test_date = pd.Timestamp(first_candidates["Date"].min())
    test_start_idx = unique_dates.get_indexer([first_test_date])[0]
    if test_start_idx < V2_EMBARGO_TRADING_DAYS:
        return None, None, 0
    embargo_cutoff = pd.Timestamp(unique_dates[test_start_idx - V2_EMBARGO_TRADING_DAYS])
    train_start = month_start - pd.DateOffset(months=V2_TRAIN_MONTHS)
    train = dataset[(dataset["Date"] >= train_start) & (dataset["Date"] < embargo_cutoff)]
    if len(train) < 10000:
        return None, embargo_cutoff, len(train)
    dtrain = lgb.Dataset(train[feature_cols].values, train[TARGET_COL].values, feature_name=feature_cols)
    model = lgb.train(_regression_params(device), dtrain, num_boost_round=num_boost_round)
    return model, embargo_cutoff, len(train)


def _build_stage1_oof(
    dataset: pd.DataFrame,
    monthly: pd.DataFrame,
    feature_cols: list[str],
    unique_dates: pd.Index,
    *,
    oof_start_month: str,
    end_month: str,
    candidate_sizes: list[int],
    device: str,
    num_boost_round: int,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    max_candidate = max(candidate_sizes)
    all_rows: list[pd.DataFrame] = []
    monthly_rows: list[dict[str, Any]] = []
    for month_start in _month_starts(oof_start_month, end_month):
        month = month_start.strftime("%Y-%m")
        test = monthly[(monthly["Date"] >= month_start) & (monthly["Date"] <= month_start + pd.offsets.MonthEnd(0))].copy()
        if test.empty:
            continue
        model, embargo_cutoff, train_n = _train_stage1_model(
            dataset,
            feature_cols,
            unique_dates,
            month_start,
            device=device,
            num_boost_round=num_boost_round,
        )
        if model is None:
            continue
        test["stage1_score"] = model.predict(test[feature_cols].values)
        test["stage1_rank"] = test["stage1_score"].rank(method="first", ascending=False).astype(int)
        test["stage1_candidate_max"] = test["stage1_rank"] <= max_candidate
        universe_ic = test["stage1_score"].corr(test[TARGET_COL], method="spearman")
        top30 = test.nsmallest(30, "stage1_rank")
        monthly_rows.append(
            {
                "month": month,
                "date": pd.Timestamp(test["Date"].max()).date().isoformat(),
                "train_n": train_n,
                "embargo_cutoff": embargo_cutoff.date().isoformat() if embargo_cutoff is not None else None,
                "universe_n": len(test),
                "stage1_universe_ic": float(universe_ic) if pd.notna(universe_ic) else np.nan,
                "stage1_top30_excess": float(top30[TARGET_COL].mean()),
                "stage1_top30_raw_return": float(top30[RAW_RETURN_COL].mean()),
            }
        )
        keep_cols = [
            "rebalance_month",
            "ticker",
            "Date",
            RAW_RETURN_COL,
            TARGET_COL,
            "stage1_score",
            "stage1_rank",
            "stage1_candidate_max",
        ] + feature_cols
        all_rows.append(test[keep_cols])
        print(
            f"[audit002-step4] stage1 {month} N={len(test)} "
            f"IC={universe_ic:+.4f} candidates={max_candidate}"
        )
    if not all_rows:
        raise RuntimeError("No Stage 1 OOF rows produced")
    return pd.concat(all_rows, ignore_index=True), pd.DataFrame(monthly_rows)


def _make_rank_labels(values: pd.Series, bins: int) -> pd.Series:
    ranks = values.rank(method="first", ascending=True)
    n = len(ranks)
    if n == 0:
        return pd.Series(dtype=np.int32)
    labels = np.floor(((ranks - 1) / n) * bins).clip(lower=0, upper=bins - 1)
    return labels.astype(np.int32)


def _add_candidate_labels(frame: pd.DataFrame, label_bins: int) -> pd.DataFrame:
    out = frame.copy()
    out["rank_label"] = (
        out.groupby("rebalance_month", group_keys=False)[TARGET_COL]
        .apply(lambda s: _make_rank_labels(s, label_bins))
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


def _train_stage2_and_eval(
    stage1_oof: pd.DataFrame,
    stage1_monthly: pd.DataFrame,
    feature_cols: list[str],
    *,
    candidate_size: int,
    start_month: str,
    end_month: str,
    device: str,
    label_bins: int,
    num_boost_round: int,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    params = _lambdarank_params(device, label_bins)
    monthly_rows: list[dict[str, Any]] = []
    fold_rows: list[dict[str, Any]] = []
    for month_start in _month_starts(start_month, end_month):
        month = month_start.strftime("%Y-%m")
        stage1_meta = stage1_monthly[stage1_monthly["month"] == month]
        if stage1_meta.empty:
            continue
        embargo_cutoff = pd.Timestamp(stage1_meta.iloc[0]["embargo_cutoff"])
        train_start = month_start - pd.DateOffset(months=V2_TRAIN_MONTHS)
        train = stage1_oof[
            (stage1_oof["Date"] >= train_start)
            & (stage1_oof["Date"] < embargo_cutoff)
            & (stage1_oof["stage1_rank"] <= candidate_size)
        ].copy()
        test_candidates = stage1_oof[
            (stage1_oof["rebalance_month"] == month) & (stage1_oof["stage1_rank"] <= candidate_size)
        ].copy()
        if len(train) < candidate_size * 8 or len(test_candidates) < 30:
            continue
        train = _add_candidate_labels(train, label_bins)
        test_candidates = _add_candidate_labels(test_candidates, label_bins)
        train = train.sort_values(["rebalance_month", "stage1_rank"])
        test_candidates = test_candidates.sort_values(["rebalance_month", "stage1_rank"])
        dtrain = lgb.Dataset(
            train[feature_cols].values,
            label=train["rank_label"].values,
            group=_groups_for_lgb(train),
            feature_name=feature_cols,
        )
        dval = lgb.Dataset(
            test_candidates[feature_cols].values,
            label=test_candidates["rank_label"].values,
            group=_groups_for_lgb(test_candidates),
            feature_name=feature_cols,
            reference=dtrain,
        )
        stage2_model = lgb.train(
            params,
            dtrain,
            num_boost_round=num_boost_round,
            valid_sets=[dval],
            callbacks=[lgb.early_stopping(30, verbose=False)],
        )
        two_stage = TwoStageModel(
            stage1_model=None,
            stage2_model=stage2_model,
            feature_cols=feature_cols,
            config=TwoStagePredictionConfig(candidate_size=candidate_size, top_n=30),
        )
        ranked = two_stage.predict(
            test_candidates,
            precomputed_stage1_score_col="stage1_score",
        )
        ranked = ranked.rename(columns={"stage2_score": "rank_score", "two_stage_rank": "rank"})
        ndcg30 = _ndcg_at_k(
            test_candidates["rank_label"].to_numpy(dtype=float),
            stage2_model.predict(test_candidates[feature_cols].values),
            30,
        )
        final_ic = ranked["rank_score"].corr(ranked[TARGET_COL], method="spearman")
        stage1_ic = float(stage1_meta.iloc[0]["stage1_universe_ic"])
        monthly_rows.append(
            {
                "month": month,
                "candidate_size": candidate_size,
                "stage1_universe_ic": stage1_ic,
                "final_top30_ic": float(final_ic) if pd.notna(final_ic) else np.nan,
                "ndcg_at_30": float(ndcg30) if pd.notna(ndcg30) else np.nan,
                "top30_excess": float(ranked[TARGET_COL].mean()),
                "top30_raw_return": float(ranked[RAW_RETURN_COL].mean()),
                "top30_win_rate": float((ranked[RAW_RETURN_COL] > 0).mean()),
                "candidate_excess": float(test_candidates[TARGET_COL].mean()),
                "train_candidate_rows": len(train),
            }
        )
        export_cols = [
            "rebalance_month",
            "ticker",
            "Date",
            "rank",
            "stage1_rank",
            "stage1_score",
            "rank_score",
            RAW_RETURN_COL,
            TARGET_COL,
        ]
        out = ranked[export_cols].copy()
        out["month"] = month
        out["candidate_size"] = candidate_size
        fold_rows.extend(out.to_dict("records"))
        print(
            f"[audit002-step4] two-stage N={candidate_size} {month} "
            f"Stage1IC={stage1_ic:+.4f} FinalIC={final_ic:+.4f} NDCG@30={ndcg30:.3f}"
        )
    return pd.DataFrame(monthly_rows), pd.DataFrame(fold_rows)


def _top30_ic_from_fold(fold: pd.DataFrame, score_col: str) -> pd.Series:
    rows = []
    for month, group in fold.groupby("month"):
        pair = group[[score_col, TARGET_COL]].replace([np.inf, -np.inf], np.nan).dropna()
        if len(pair) < 5 or pair[score_col].nunique(dropna=True) < 2:
            ic = np.nan
        else:
            ic = pair[score_col].corr(pair[TARGET_COL], method="spearman")
        rows.append({"month": month, "top30_ic": ic})
    return pd.DataFrame(rows)["top30_ic"]


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


def _append_two_stage_rows(
    base_comparison: pd.DataFrame,
    monthly_by_size: dict[int, pd.DataFrame],
    fold_by_size: dict[int, pd.DataFrame],
    calib_summary_by_size: dict[int, CalibrationSummary],
) -> pd.DataFrame:
    rows = [row for row in base_comparison.to_dict("records")]
    for size, monthly in monthly_by_size.items():
        fold = fold_by_size[size]
        final_ic_summary = _ic_summary(_top30_ic_from_fold(fold, "rank_score"))
        stage1_summary = _ic_summary(monthly["stage1_universe_ic"])
        calib = calib_summary_by_size[size]
        rows.append(
            {
                "model": f"TwoStage_Top{size}",
                "universe_ic_mean": stage1_summary.mean_ic,
                "universe_ic_t_stat": stage1_summary.t_stat,
                "top30_internal_ic_mean": final_ic_summary.mean_ic,
                "top30_internal_ic_t_stat": final_ic_summary.t_stat,
                "positive_deciles": calib.positive_deciles,
                "total_deciles": calib.total_deciles,
                "positive_decile_pct": calib.positive_decile_pct,
                "max_consecutive_negative_deciles": calib.max_consecutive_negative_deciles,
                "calibration_spearman": calib.monotonic_spearman,
                "top30_excess_mean": float(monthly["top30_excess"].mean()),
                "ndcg_at_30": float(monthly["ndcg_at_30"].mean()),
            }
        )
    return pd.DataFrame(rows)


def _model_ac(row: pd.Series) -> str:
    pass_stage1 = row["universe_ic_mean"] >= 0.04
    pass_top30 = row["top30_internal_ic_mean"] >= 0.05
    pass_ndcg = row.get("ndcg_at_30", np.nan) >= 0.55 if pd.notna(row.get("ndcg_at_30", np.nan)) else False
    pass_calib = row["max_consecutive_negative_deciles"] < 2
    pass_excess = row["top30_excess_mean"] >= 0.008
    return "PASS" if pass_stage1 and pass_top30 and pass_ndcg and pass_calib and pass_excess else "FAIL"


def _draw_calibration_png(calib_by_label: dict[str, pd.DataFrame], output: Path) -> None:
    width, height = 1100, 650
    left, right, top, bottom = 92, 1030, 78, 520
    img = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("arial.ttf", 14)
        title_font = ImageFont.truetype("arial.ttf", 22)
    except OSError:
        font = ImageFont.load_default()
        title_font = ImageFont.load_default()
    draw.text((width // 2, 30), "AUDIT-002 Step 4 Calibration", anchor="mm", fill="#222", font=title_font)
    all_values = [0.0]
    for frame in calib_by_label.values():
        all_values.extend((frame["actual_excess_return_mean"] * 100).tolist())
    y_min, y_max = min(all_values), max(all_values)
    pad = max((y_max - y_min) * 0.12, 1.0)
    y_min -= pad
    y_max += pad

    def x_scale(decile: float) -> float:
        return left + (decile - 1) * (right - left) / 9

    def y_scale(value: float) -> float:
        return bottom - (value - y_min) * (bottom - top) / (y_max - y_min)

    for i in range(6):
        frac = i / 5
        value = y_min + (y_max - y_min) * frac
        y = y_scale(value)
        draw.line((left, y, right, y), fill="#dddddd", width=1)
        draw.text((left - 8, y), f"{value:.1f}", anchor="rm", fill="#555", font=font)
    zero_y = y_scale(0)
    draw.line((left, zero_y, right, zero_y), fill="#333333", width=1)
    draw.line((left, bottom, right, bottom), fill="#333333", width=1)
    draw.line((left, top, left, bottom), fill="#333333", width=1)
    draw.text((24, (top + bottom) // 2), "20D excess return (%)", anchor="mm", fill="#333", font=font)
    draw.text(((left + right) // 2, height - 42), "Score decile, low to high", anchor="mm", fill="#333", font=font)

    colors = ["#1f77b4", "#d62728", "#2ca02c", "#9467bd", "#ff7f0e"]
    for idx, (label, frame) in enumerate(calib_by_label.items()):
        color = colors[idx % len(colors)]
        points = [
            (x_scale(float(row["pred_decile"])), y_scale(float(row["actual_excess_return_mean"]) * 100))
            for row in frame.to_dict("records")
        ]
        if len(points) > 1:
            draw.line(points, fill=color, width=3)
        for x, y in points:
            draw.ellipse((x - 4, y - 4, x + 4, y + 4), fill=color)
        legend_y = 92 + idx * 22
        draw.rectangle((820, legend_y - 8, 834, legend_y + 6), fill=color)
        draw.text((842, legend_y), label, anchor="lm", fill="#222", font=font)

    for decile in range(1, 11):
        x = x_scale(decile)
        draw.text((x, bottom + 20), str(decile), anchor="mm", fill="#555", font=font)
    img.save(output)


def _write_report(
    *,
    output: Path,
    comparison: pd.DataFrame,
    chart_path: Path,
    stage1_path: Path,
    fold_paths: dict[int, Path],
    calib_paths: dict[int, Path],
    verdict: str,
    candidate_sizes: list[int],
) -> None:
    lines = [
        "# AUDIT-002 Step 4 - Two-Stage Regression + LambdaRank",
        "",
        f"- Generated at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "- Stage 1: regression model screens full universe into Top-N candidates.",
        "- Stage 2: LambdaRank reorders only Stage 1 candidates.",
        "- Stage 2 training data uses only Stage 1 OOF candidates before the embargo cutoff.",
        f"- Candidate sizes tested: {candidate_sizes}",
        f"- Stage 1 OOF artifact: `{stage1_path}`",
        f"- Calibration chart: `{chart_path}`",
        "",
        "## Four-Way Comparison",
        "",
        "| Model | Stage1/Universe IC | Universe t | Final Top30 IC | Top30 t | NDCG@30 | Positive deciles | Max consecutive negative deciles | Calibration rho | Top30 excess | AC |",
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
    lines.extend(["", "## Artifacts", ""])
    for size in candidate_sizes:
        lines.append(f"- Top{size} fold artifact: `{fold_paths[size]}`")
        lines.append(f"- Top{size} calibration deciles: `{calib_paths[size]}`")
    lines.extend(["", "## Verdict", "", verdict, ""])
    output.write_text("\n".join(lines), encoding="utf-8")


def run(args: argparse.Namespace) -> dict[str, Any]:
    prefix = args.output_prefix or f"audit002_step4_two_stage_{datetime.now().strftime('%Y%m%d')}"
    sizes = [int(part) for part in args.candidate_sizes.split(",") if part.strip()]
    if not sizes:
        raise ValueError("candidate_sizes must not be empty")
    step3_comparison_path = Path(args.step3_comparison_csv) if args.step3_comparison_csv else _latest("audit002_step3_lambdarank_*_comparison.csv")
    regression_calib_path = Path(args.regression_calibration_csv) if args.regression_calibration_csv else _latest("audit002_step2_feature_ic_*_before_calibration_deciles.csv")
    cleaned_calib_path = Path(args.cleaned_calibration_csv) if args.cleaned_calibration_csv else _latest("audit002_step2_feature_ic_*_after_calibration_deciles.csv")
    lambdarank_calib_path = Path(args.lambdarank_calibration_csv) if args.lambdarank_calibration_csv else _latest("audit002_step3_lambdarank_*_calibration_deciles.csv")

    dataset, monthly, feature_cols, unique_dates = _prepare_dataset(verbose=not args.quiet)
    oof_start = (pd.Timestamp(f"{args.start_month}-01") - pd.DateOffset(months=V2_TRAIN_MONTHS)).strftime("%Y-%m")
    stage1_oof, stage1_monthly = _build_stage1_oof(
        dataset,
        monthly,
        feature_cols,
        unique_dates,
        oof_start_month=oof_start,
        end_month=args.end_month,
        candidate_sizes=sizes,
        device=args.device,
        num_boost_round=args.stage1_num_boost_round,
    )

    stage1_path = REPORT_PATH / f"{prefix}_stage1_oof_candidates.csv"
    stage1_monthly_path = REPORT_PATH / f"{prefix}_stage1_monthly.csv"
    stage1_export_cols = [
        "rebalance_month",
        "ticker",
        "Date",
        RAW_RETURN_COL,
        TARGET_COL,
        "stage1_score",
        "stage1_rank",
        "stage1_candidate_max",
    ]
    stage1_oof[stage1_export_cols].to_csv(stage1_path, index=False, encoding="utf-8-sig")
    stage1_monthly.to_csv(stage1_monthly_path, index=False, encoding="utf-8-sig")

    monthly_by_size: dict[int, pd.DataFrame] = {}
    fold_by_size: dict[int, pd.DataFrame] = {}
    calib_by_size: dict[int, pd.DataFrame] = {}
    calib_summary_by_size: dict[int, CalibrationSummary] = {}
    fold_paths: dict[int, Path] = {}
    calib_paths: dict[int, Path] = {}
    monthly_paths: dict[int, Path] = {}
    for size in sizes:
        size_monthly, size_fold = _train_stage2_and_eval(
            stage1_oof,
            stage1_monthly,
            feature_cols,
            candidate_size=size,
            start_month=args.start_month,
            end_month=args.end_month,
            device=args.device,
            label_bins=args.label_bins,
            num_boost_round=args.stage2_num_boost_round,
        )
        monthly_by_size[size] = size_monthly
        fold_by_size[size] = size_fold
        calib, calib_summary = _calibration(size_fold, "rank_score")
        calib_by_size[size] = calib
        calib_summary_by_size[size] = calib_summary
        monthly_paths[size] = REPORT_PATH / f"{prefix}_top{size}_monthly.csv"
        fold_paths[size] = REPORT_PATH / f"{prefix}_top{size}_fold_top30.csv"
        calib_paths[size] = REPORT_PATH / f"{prefix}_top{size}_calibration_deciles.csv"
        size_monthly.to_csv(monthly_paths[size], index=False, encoding="utf-8-sig")
        size_fold.to_csv(fold_paths[size], index=False, encoding="utf-8-sig")
        calib.to_csv(calib_paths[size], index=False, encoding="utf-8-sig")

    base_comparison = pd.read_csv(step3_comparison_path)
    comparison = _append_two_stage_rows(base_comparison, monthly_by_size, fold_by_size, calib_summary_by_size)
    comparison_path = REPORT_PATH / f"{prefix}_comparison.csv"
    comparison.to_csv(comparison_path, index=False, encoding="utf-8-sig")

    chart_inputs: dict[str, pd.DataFrame] = {}
    for label, path in [
        ("Regression", regression_calib_path),
        ("Step2 cleaned", cleaned_calib_path),
        ("LambdaRank", lambdarank_calib_path),
    ]:
        frame = pd.read_csv(path)
        if "pred_return_mean" in frame.columns:
            frame = frame.rename(columns={"pred_return_mean": "score_mean"})
        chart_inputs[label] = frame
    for size, frame in calib_by_size.items():
        chart_inputs[f"TwoStage Top{size}"] = frame
    chart_path = REPORT_PATH / f"{prefix}_calibration.png"
    _draw_calibration_png(chart_inputs, chart_path)

    two_stage_rows = comparison[comparison["model"].astype(str).str.startswith("TwoStage")]
    passing = two_stage_rows[two_stage_rows.apply(_model_ac, axis=1) == "PASS"]
    if not passing.empty:
        best = passing.sort_values("candidate_size" if "candidate_size" in passing.columns else "top30_excess_mean").iloc[0]
        verdict = f"Two-stage passes AC. PM can choose `{best['model']}` for the next fixed-snapshot production A/B."
    else:
        best = two_stage_rows.sort_values(["top30_internal_ic_mean", "top30_excess_mean"], ascending=False).iloc[0]
        verdict = (
            "Two-stage does not fully clear AC. Best research candidate is "
            f"`{best['model']}` with Top30 IC {_fmt_float(best['top30_internal_ic_mean'])}, "
            f"NDCG@30 {_fmt_float(best.get('ndcg_at_30'), 3)}, and Top30 excess {_fmt_pct(best['top30_excess_mean'])}. "
            "Recommendation: redesign Stage 2 labels/objective or test a wider Stage 1 candidate pool before production work."
        )

    report_path = REPORT_PATH / f"{prefix}.md"
    json_path = REPORT_PATH / f"{prefix}.json"
    _write_report(
        output=report_path,
        comparison=comparison,
        chart_path=chart_path,
        stage1_path=stage1_path,
        fold_paths=fold_paths,
        calib_paths=calib_paths,
        verdict=verdict,
        candidate_sizes=sizes,
    )

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "step3_comparison_csv": str(step3_comparison_path),
        "stage1_oof_candidates_csv": str(stage1_path),
        "stage1_monthly_csv": str(stage1_monthly_path),
        "comparison_csv": str(comparison_path),
        "calibration_png": str(chart_path),
        "report": str(report_path),
        "candidate_sizes": sizes,
        "two_stage_monthly_csv": {str(k): str(v) for k, v in monthly_paths.items()},
        "two_stage_fold_top30_csv": {str(k): str(v) for k, v in fold_paths.items()},
        "two_stage_calibration_csv": {str(k): str(v) for k, v in calib_paths.items()},
        "comparison": comparison.to_dict("records"),
        "calibration_summary": {str(k): asdict(v) for k, v in calib_summary_by_size.items()},
        "verdict": verdict,
    }
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[audit002-step4] report={report_path}")
    print(f"[audit002-step4] chart={chart_path}")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="AUDIT-002 Step 4 two-stage experiment.")
    parser.add_argument("--start-month", default=DEFAULT_START)
    parser.add_argument("--end-month", default=DEFAULT_END)
    parser.add_argument("--candidate-sizes", default="60,80")
    parser.add_argument("--device", choices=["cpu", "gpu"], default="cpu")
    parser.add_argument("--label-bins", type=int, default=DEFAULT_LABEL_BINS)
    parser.add_argument("--stage1-num-boost-round", type=int, default=300)
    parser.add_argument("--stage2-num-boost-round", type=int, default=300)
    parser.add_argument("--step3-comparison-csv", default=None)
    parser.add_argument("--regression-calibration-csv", default=None)
    parser.add_argument("--cleaned-calibration-csv", default=None)
    parser.add_argument("--lambdarank-calibration-csv", default=None)
    parser.add_argument("--output-prefix", default=None)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()
    run(args)


if __name__ == "__main__":
    main()
