"""AUDIT-002 Step 5: two-stage candidate-size x label-bin grid search."""
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
from scripts.train_v2 import V2_PARAMS, V2_TRAIN_MONTHS


REPORT_PATH = Path(REPORT_DIR)
TARGET_COL = "trade_excess_return_20d"
RAW_RETURN_COL = "trade_return_20d"
DEFAULT_START = "2024-01"
DEFAULT_END = "2026-04"


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


def _month_starts(start_month: str, end_month: str) -> pd.DatetimeIndex:
    return pd.date_range(f"{start_month}-01", f"{end_month}-01", freq="MS")


def _groups_for_lgb(frame: pd.DataFrame) -> list[int]:
    return frame.groupby("rebalance_month", sort=False).size().astype(int).tolist()


def _make_rank_labels(values: pd.Series, bins: int) -> pd.Series:
    ranks = values.rank(method="first", ascending=True)
    n = len(ranks)
    if n == 0:
        return pd.Series(dtype=np.int32)
    labels = np.floor(((ranks - 1) / n) * bins).clip(lower=0, upper=bins - 1)
    return labels.astype(np.int32)


def _add_candidate_labels(frame: pd.DataFrame, bins: int) -> pd.DataFrame:
    out = frame.copy()
    out["rank_label"] = (
        out.groupby("rebalance_month", group_keys=False)[TARGET_COL]
        .apply(lambda s: _make_rank_labels(s, bins))
        .astype(np.int32)
    )
    return out


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


def _load_monthly_features(stage1_path: Path, quiet: bool) -> tuple[pd.DataFrame, pd.DataFrame, list[str]]:
    stage1_cols = [
        "rebalance_month",
        "ticker",
        "Date",
        RAW_RETURN_COL,
        TARGET_COL,
        "stage1_score",
        "stage1_rank",
    ]
    stage1 = pd.read_csv(stage1_path, usecols=stage1_cols)
    stage1["Date"] = pd.to_datetime(stage1["Date"])
    stage1["ticker"] = stage1["ticker"].astype(str)

    dataset = build_dataset(verbose=not quiet)
    dataset = dataset.dropna(subset=[TARGET_COL]).copy()
    dataset["Date"] = pd.to_datetime(dataset["Date"])
    dataset["ticker"] = dataset["ticker"].astype(str)
    dataset["rebalance_month"] = dataset["Date"].dt.to_period("M").astype(str)
    monthly = dataset.groupby(["rebalance_month", "ticker"], sort=False).tail(1).copy()
    feature_cols = [col for col in get_feature_columns() if col in monthly.columns]
    keep_cols = ["rebalance_month", "ticker"] + feature_cols
    merged = stage1.merge(monthly[keep_cols], on=["rebalance_month", "ticker"], how="left")
    missing_feature_rows = int(merged[feature_cols].isna().all(axis=1).sum())
    if missing_feature_rows:
        raise ValueError(f"{missing_feature_rows} Stage1 rows did not match monthly feature frame")

    stage1_monthly = (
        merged.groupby("rebalance_month", as_index=False)
        .agg(
            stage1_universe_ic=("stage1_score", lambda s: float(s.corr(merged.loc[s.index, TARGET_COL], method="spearman"))),
            universe_n=("ticker", "count"),
        )
        .rename(columns={"rebalance_month": "month"})
    )
    return merged, stage1_monthly, feature_cols


def _train_eval_combo(
    stage1_oof: pd.DataFrame,
    stage1_monthly: pd.DataFrame,
    feature_cols: list[str],
    *,
    candidate_size: int,
    label_bins: int,
    start_month: str,
    end_month: str,
    device: str,
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
        stage1_ic = float(stage1_meta.iloc[0]["stage1_universe_ic"])
        train_start = month_start - pd.DateOffset(months=V2_TRAIN_MONTHS)
        train = stage1_oof[
            (stage1_oof["Date"] >= train_start)
            & (stage1_oof["Date"] < month_start)
            & (stage1_oof["stage1_rank"] <= candidate_size)
        ].copy()
        test_candidates = stage1_oof[
            (stage1_oof["rebalance_month"] == month)
            & (stage1_oof["stage1_rank"] <= candidate_size)
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
        model = lgb.train(
            params,
            dtrain,
            num_boost_round=num_boost_round,
            valid_sets=[dval],
            callbacks=[lgb.early_stopping(30, verbose=False)],
        )
        two_stage = TwoStageModel(
            stage1_model=None,
            stage2_model=model,
            feature_cols=feature_cols,
            config=TwoStagePredictionConfig(candidate_size=candidate_size, top_n=30),
        )
        ranked = two_stage.predict(test_candidates, precomputed_stage1_score_col="stage1_score")
        ranked = ranked.rename(columns={"stage2_score": "rank_score", "two_stage_rank": "rank"})
        candidate_scores = model.predict(test_candidates[feature_cols].values)
        ndcg30 = _ndcg_at_k(test_candidates["rank_label"].to_numpy(dtype=float), candidate_scores, 30)
        top30_ic = ranked["rank_score"].corr(ranked[TARGET_COL], method="spearman")
        monthly_rows.append(
            {
                "month": month,
                "candidate_size": candidate_size,
                "label_bins": label_bins,
                "stage1_universe_ic": float(stage1_ic) if pd.notna(stage1_ic) else np.nan,
                "final_top30_ic": float(top30_ic) if pd.notna(top30_ic) else np.nan,
                "ndcg_at_30": float(ndcg30) if pd.notna(ndcg30) else np.nan,
                "top30_excess": float(ranked[TARGET_COL].mean()),
                "top30_raw_return": float(ranked[RAW_RETURN_COL].mean()),
                "top30_win_rate": float((ranked[RAW_RETURN_COL] > 0).mean()),
                "train_candidate_rows": len(train),
            }
        )
        out = ranked[
            [
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
        ].copy()
        out["month"] = month
        out["candidate_size"] = candidate_size
        out["label_bins"] = label_bins
        fold_rows.extend(out.to_dict("records"))
    return pd.DataFrame(monthly_rows), pd.DataFrame(fold_rows)


def _summary(values: pd.Series) -> tuple[float, float]:
    clean = pd.to_numeric(values, errors="coerce").dropna()
    if len(clean) < 2:
        return float(clean.mean()) if len(clean) else float("nan"), float("nan")
    mean = float(clean.mean())
    std = float(clean.std(ddof=1))
    t_stat = float(mean / (std / math.sqrt(len(clean)))) if std > 0 else float("nan")
    return mean, t_stat


def _calibration(fold: pd.DataFrame) -> tuple[pd.DataFrame, CalibrationSummary]:
    clean = fold.dropna(subset=["rank_score", TARGET_COL]).copy()
    clean["pred_decile"] = pd.qcut(clean["rank_score"], 10, labels=False, duplicates="drop") + 1
    calib = (
        clean.groupby("pred_decile", as_index=False)
        .agg(
            n=("ticker", "count"),
            score_mean=("rank_score", "mean"),
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


def _combo_pass(row: pd.Series) -> bool:
    return bool(
        row["stage1_universe_ic"] >= 0.04
        and row["final_top30_ic"] >= 0.05
        and row["ndcg_at_30"] >= 0.55
        and row["max_consecutive_negative_deciles"] < 2
        and row["top30_excess"] >= 0.008
    )


def _draw_heatmap(grid: pd.DataFrame, output: Path) -> None:
    metrics = [
        ("final_top30_ic", "Top30 IC", 0.05),
        ("ndcg_at_30", "NDCG@30", 0.55),
        ("top30_excess", "Top30 excess", 0.008),
    ]
    candidate_sizes = sorted(grid["candidate_size"].unique())
    label_bins = sorted(grid["label_bins"].unique())
    cell_w, cell_h = 92, 58
    width = 220 + len(candidate_sizes) * cell_w
    height = 110 + len(label_bins) * len(metrics) * cell_h
    img = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("arial.ttf", 13)
        title_font = ImageFont.truetype("arial.ttf", 20)
    except OSError:
        font = ImageFont.load_default()
        title_font = ImageFont.load_default()
    draw.text((width // 2, 26), "AUDIT-002 Step 5 Grid Search Heatmap", anchor="mm", fill="#222", font=title_font)
    x0, y0 = 200, 70
    for idx, size in enumerate(candidate_sizes):
        draw.text((x0 + idx * cell_w + cell_w / 2, y0 - 22), f"N={size}", anchor="mm", fill="#333", font=font)
    row_idx = 0
    for bins in label_bins:
        for metric, label, threshold in metrics:
            y = y0 + row_idx * cell_h
            draw.text((12, y + cell_h / 2), f"{bins} bins / {label}", anchor="lm", fill="#333", font=font)
            vals = grid[grid["label_bins"] == bins].set_index("candidate_size")[metric]
            valid = vals.replace([np.inf, -np.inf], np.nan).dropna()
            lo = float(valid.min()) if not valid.empty else 0.0
            hi = float(valid.max()) if not valid.empty else 1.0
            if hi == lo:
                hi = lo + 1e-6
            for col_idx, size in enumerate(candidate_sizes):
                value = float(vals.get(size, np.nan))
                if pd.isna(value):
                    color = (230, 230, 230)
                    text = "-"
                else:
                    strength = (value - lo) / (hi - lo)
                    if value >= threshold:
                        color = (int(210 - 80 * strength), int(245 - 60 * (1 - strength)), int(210 - 80 * strength))
                    else:
                        color = (245, int(220 - 80 * strength), int(220 - 80 * strength))
                    text = _fmt_pct(value) if metric == "top30_excess" else f"{value:.3f}"
                x = x0 + col_idx * cell_w
                draw.rectangle((x, y, x + cell_w - 2, y + cell_h - 2), fill=color, outline="#ffffff")
                draw.text((x + cell_w / 2, y + cell_h / 2), text, anchor="mm", fill="#222", font=font)
            row_idx += 1
    img.save(output)


def _write_report(
    *,
    output: Path,
    grid: pd.DataFrame,
    recommended: dict[str, Any] | None,
    chart_path: Path,
    stage1_path: Path,
    next_step: str,
) -> None:
    lines = [
        "# AUDIT-002 Step 5 - Candidate Pool x Label Granularity Grid Search",
        "",
        f"- Generated at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- Stage1 OOF source: `{stage1_path}`",
        f"- Heatmap: `{chart_path}`",
        f"- Recommended Config: `{recommended['config']}`" if recommended else "- Recommended Config: none",
        "",
        "## Grid Search",
        "",
        "| N | Label bins | Stage1 IC | Top30 IC | NDCG@30 | Max consecutive negative deciles | Top30 excess | AC |",
        "|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in grid.sort_values(["label_bins", "candidate_size"]).to_dict("records"):
        lines.append(
            f"| {int(row['candidate_size'])} | {int(row['label_bins'])} | "
            f"{_fmt_float(row['stage1_universe_ic'])} | {_fmt_float(row['final_top30_ic'])} | "
            f"{_fmt_float(row['ndcg_at_30'], 3)} | {int(row['max_consecutive_negative_deciles'])} | "
            f"{_fmt_pct(row['top30_excess'])} | {'PASS' if row['pass_all'] else 'FAIL'} |"
        )
    lines.extend(["", "## Verdict", "", next_step, ""])
    output.write_text("\n".join(lines), encoding="utf-8")


def run(args: argparse.Namespace) -> dict[str, Any]:
    prefix = args.output_prefix or f"audit002_step5_grid_search_{datetime.now().strftime('%Y%m%d')}"
    stage1_path = Path(args.stage1_oof_csv) if args.stage1_oof_csv else _latest("audit002_step4_two_stage_*_stage1_oof_candidates.csv")
    candidate_sizes = [int(x) for x in args.candidate_sizes.split(",") if x.strip()]
    label_bins_values = [int(x) for x in args.label_bins.split(",") if x.strip()]
    stage1_oof, stage1_monthly, feature_cols = _load_monthly_features(stage1_path, args.quiet)
    grid_rows: list[dict[str, Any]] = []
    monthly_paths: dict[str, str] = {}
    fold_paths: dict[str, str] = {}
    calib_paths: dict[str, str] = {}

    for bins in label_bins_values:
        for size in candidate_sizes:
            print(f"[audit002-step5] run N={size} label_bins={bins}")
            monthly, fold = _train_eval_combo(
                stage1_oof,
                stage1_monthly,
                feature_cols,
                candidate_size=size,
                label_bins=bins,
                start_month=args.start_month,
                end_month=args.end_month,
                device=args.device,
                num_boost_round=args.num_boost_round,
            )
            calib, calib_summary = _calibration(fold)
            stage1_ic, stage1_t = _summary(monthly["stage1_universe_ic"])
            final_ic, final_t = _summary(monthly["final_top30_ic"])
            key = f"N{size}_bins{bins}"
            monthly_path = REPORT_PATH / f"{prefix}_{key}_monthly.csv"
            fold_path = REPORT_PATH / f"{prefix}_{key}_fold_top30.csv"
            calib_path = REPORT_PATH / f"{prefix}_{key}_calibration_deciles.csv"
            monthly.to_csv(monthly_path, index=False, encoding="utf-8-sig")
            fold.to_csv(fold_path, index=False, encoding="utf-8-sig")
            calib.to_csv(calib_path, index=False, encoding="utf-8-sig")
            monthly_paths[key] = str(monthly_path)
            fold_paths[key] = str(fold_path)
            calib_paths[key] = str(calib_path)
            row = {
                "candidate_size": size,
                "label_bins": bins,
                "stage1_universe_ic": stage1_ic,
                "stage1_universe_t": stage1_t,
                "final_top30_ic": final_ic,
                "final_top30_t": final_t,
                "ndcg_at_30": float(monthly["ndcg_at_30"].mean()),
                "positive_deciles": calib_summary.positive_deciles,
                "total_deciles": calib_summary.total_deciles,
                "positive_decile_pct": calib_summary.positive_decile_pct,
                "max_consecutive_negative_deciles": calib_summary.max_consecutive_negative_deciles,
                "calibration_spearman": calib_summary.monotonic_spearman,
                "top30_excess": float(monthly["top30_excess"].mean()),
                "top30_raw_return": float(monthly["top30_raw_return"].mean()),
            }
            row["pass_all"] = _combo_pass(pd.Series(row))
            grid_rows.append(row)
            print(
                f"[audit002-step5] N={size} bins={bins} IC={final_ic:+.4f} "
                f"NDCG={row['ndcg_at_30']:.3f} excess={row['top30_excess']:+.2%} "
                f"pass={row['pass_all']}"
            )

    grid = pd.DataFrame(grid_rows)
    grid_path = REPORT_PATH / f"{prefix}_grid.csv"
    chart_path = REPORT_PATH / f"{prefix}_heatmap.png"
    report_path = REPORT_PATH / f"{prefix}.md"
    json_path = REPORT_PATH / f"{prefix}.json"
    grid.to_csv(grid_path, index=False, encoding="utf-8-sig")
    _draw_heatmap(grid, chart_path)

    passing = grid[grid["pass_all"]].sort_values(["candidate_size", "label_bins"])
    recommended: dict[str, Any] | None = None
    if not passing.empty:
        best = passing.iloc[0].to_dict()
        recommended = {"config": f"N={int(best['candidate_size'])}, label_bins={int(best['label_bins'])}", **best}
        next_step = (
            f"Grid search found a passing config: {recommended['config']}. "
            "Recommendation: run same-snapshot production A/B before any promotion."
        )
    else:
        scored = grid.copy()
        scored["miss_count"] = (
            (scored["stage1_universe_ic"] < 0.04).astype(int)
            + (scored["final_top30_ic"] < 0.05).astype(int)
            + (scored["ndcg_at_30"] < 0.55).astype(int)
            + (scored["max_consecutive_negative_deciles"] >= 2).astype(int)
            + (scored["top30_excess"] < 0.008).astype(int)
        )
        best = scored.sort_values(
            ["miss_count", "final_top30_ic", "ndcg_at_30", "top30_excess"],
            ascending=[True, False, False, False],
        ).iloc[0].to_dict()
        recommended = {"config": f"closest: N={int(best['candidate_size'])}, label_bins={int(best['label_bins'])}", **best}
        next_step = (
            f"No config cleared all AC. Closest config is N={int(best['candidate_size'])}, "
            f"label_bins={int(best['label_bins'])}. Recommendation: test ranking-loss variants "
            "or tune `eval_at`/label construction before production work."
        )

    _write_report(
        output=report_path,
        grid=grid,
        recommended=recommended,
        chart_path=chart_path,
        stage1_path=stage1_path,
        next_step=next_step,
    )
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "stage1_oof_csv": str(stage1_path),
        "grid_csv": str(grid_path),
        "heatmap_png": str(chart_path),
        "report": str(report_path),
        "monthly_csv": monthly_paths,
        "fold_top30_csv": fold_paths,
        "calibration_csv": calib_paths,
        "candidate_sizes": candidate_sizes,
        "label_bins": label_bins_values,
        "recommended": recommended,
        "grid": grid.to_dict("records"),
        "verdict": next_step,
    }
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[audit002-step5] report={report_path}")
    print(f"[audit002-step5] heatmap={chart_path}")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="AUDIT-002 Step 5 grid search.")
    parser.add_argument("--start-month", default=DEFAULT_START)
    parser.add_argument("--end-month", default=DEFAULT_END)
    parser.add_argument("--candidate-sizes", default="60,70,75,80,100")
    parser.add_argument("--label-bins", default="5,10")
    parser.add_argument("--device", choices=["cpu", "gpu"], default="cpu")
    parser.add_argument("--num-boost-round", type=int, default=300)
    parser.add_argument("--stage1-oof-csv", default=None)
    parser.add_argument("--output-prefix", default=None)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()
    run(args)


if __name__ == "__main__":
    main()
