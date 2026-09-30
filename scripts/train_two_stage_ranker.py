"""Train the REQ-011 production Stage 2 LambdaRank model.

Stage 1 is the current V2 regression model. It scores each historical monthly
snapshot, keeps the Top75 candidates, and Stage 2 learns to rerank those
candidates with 5-level LambdaRank labels.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import lightgbm as lgb
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ml.config import (  # noqa: E402
    MODEL_DIR,
    REPORT_DIR,
    TWO_STAGE_CANDIDATE_SIZE,
    TWO_STAGE_LABEL_BINS,
    TWO_STAGE_TOP_N,
)
from ml.dataset import build_dataset, get_feature_columns  # noqa: E402
from ml.predict import load_v2_model  # noqa: E402
from ml.two_stage_model import TwoStageModel, TwoStagePredictionConfig  # noqa: E402
from scripts.train_v2 import V2_PARAMS  # noqa: E402


TARGET_COL = "trade_excess_return_20d"
RAW_RETURN_COL = "trade_return_20d"


def _make_rank_labels(values: pd.Series, bins: int) -> pd.Series:
    ranks = values.rank(method="first", ascending=True)
    n = len(ranks)
    if n == 0:
        return pd.Series(dtype=np.int32)
    labels = np.floor(((ranks - 1) / n) * bins).clip(lower=0, upper=bins - 1)
    return labels.astype(np.int32)


def _groups_for_lgb(frame: pd.DataFrame) -> list[int]:
    return frame.groupby("rebalance_month", sort=False).size().astype(int).tolist()


def _lambdarank_params(device: str, label_bins: int) -> dict[str, Any]:
    params = V2_PARAMS.copy()
    params.update(
        {
            "objective": "lambdarank",
            "metric": "ndcg",
            "eval_at": [TWO_STAGE_TOP_N],
            "label_gain": [float(2**i - 1) for i in range(label_bins)],
            "ndcg_eval_at": [TWO_STAGE_TOP_N],
            "verbose": -1,
        }
    )
    if device == "gpu":
        params["device"] = "gpu"
    else:
        params.pop("device", None)
    return params


def _load_stage1(stage1_meta: str | None) -> tuple[lgb.Booster, dict[str, Any]]:
    if not stage1_meta:
        model, meta = load_v2_model()
        if model is None or meta is None:
            raise FileNotFoundError("No V2 model found for Stage 1")
        return model, meta
    with open(stage1_meta, "r", encoding="utf-8") as f:
        meta = json.load(f)
    return lgb.Booster(model_file=meta["model_file"]), meta


def _prepare_monthly_frame(
    stage1_model: lgb.Booster,
    stage1_meta: dict[str, Any],
    *,
    candidate_size: int,
    label_bins: int,
    verbose: bool,
) -> tuple[pd.DataFrame, pd.DataFrame, list[str]]:
    dataset = build_dataset(verbose=verbose)
    if TARGET_COL not in dataset.columns:
        raise ValueError(f"{TARGET_COL} missing from dataset")
    dataset = dataset.dropna(subset=[TARGET_COL]).copy()
    dataset["Date"] = pd.to_datetime(dataset["Date"])
    dataset["ticker"] = dataset["ticker"].astype(str)
    dataset["rebalance_month"] = dataset["Date"].dt.to_period("M").astype(str)

    monthly = dataset.groupby(["rebalance_month", "ticker"], sort=False).tail(1).copy()
    monthly = monthly.sort_values(["rebalance_month", "ticker"]).reset_index(drop=True)

    stage1_cols = list(stage1_meta["feature_columns"])
    missing_stage1 = [col for col in stage1_cols if col not in monthly.columns]
    if missing_stage1:
        raise ValueError(f"Missing Stage 1 feature columns: {missing_stage1[:8]}")
    monthly["stage1_score"] = stage1_model.predict(monthly[stage1_cols].values)
    monthly["stage1_rank"] = (
        monthly.groupby("rebalance_month")["stage1_score"]
        .rank(method="first", ascending=False)
        .astype(int)
    )

    candidates = monthly[monthly["stage1_rank"] <= candidate_size].copy()
    candidates["rank_label"] = (
        candidates.groupby("rebalance_month", group_keys=False)[TARGET_COL]
        .apply(lambda s: _make_rank_labels(s, label_bins))
        .astype(np.int32)
    )
    candidates = candidates.sort_values(["rebalance_month", "stage1_rank"]).reset_index(drop=True)

    feature_cols = [col for col in get_feature_columns() if col in candidates.columns]
    if not feature_cols:
        raise ValueError("No Stage 2 feature columns found")
    return monthly, candidates, feature_cols


def _smoke_test(
    model: lgb.Booster,
    monthly: pd.DataFrame,
    feature_cols: list[str],
    *,
    candidate_size: int,
    top_n: int,
) -> dict[str, Any]:
    latest_month = str(monthly["rebalance_month"].max())
    frame = monthly[monthly["rebalance_month"] == latest_month].copy()
    two_stage = TwoStageModel(
        stage1_model=None,
        stage2_model=model,
        feature_cols=feature_cols,
        config=TwoStagePredictionConfig(candidate_size=candidate_size, top_n=top_n),
    )
    ranked = two_stage.predict(frame, precomputed_stage1_score_col="stage1_score")
    if len(ranked) != top_n:
        raise RuntimeError(f"TwoStage smoke expected {top_n} rows, got {len(ranked)}")
    if ranked["two_stage_rank"].isna().any():
        raise RuntimeError("TwoStage smoke produced NaN rank")
    return {
        "month": latest_month,
        "rows": int(len(ranked)),
        "top_ticker": str(ranked.iloc[0]["ticker"]),
        "top_score": float(ranked.iloc[0]["stage2_score"]),
    }


def train_two_stage_ranker(args: argparse.Namespace) -> dict[str, Any]:
    candidate_size = int(args.candidate_size)
    label_bins = int(args.label_bins)
    top_n = int(args.top_n)
    stage1_model, stage1_meta = _load_stage1(args.stage1_meta)
    monthly, candidates, feature_cols = _prepare_monthly_frame(
        stage1_model,
        stage1_meta,
        candidate_size=candidate_size,
        label_bins=label_bins,
        verbose=not args.quiet,
    )

    params = _lambdarank_params(args.device, label_bins)
    dtrain = lgb.Dataset(
        candidates[feature_cols].values,
        label=candidates["rank_label"].values,
        group=_groups_for_lgb(candidates),
        feature_name=feature_cols,
    )
    model = lgb.train(params, dtrain, num_boost_round=args.num_boost_round)

    smoke = _smoke_test(
        model,
        monthly,
        feature_cols,
        candidate_size=candidate_size,
        top_n=top_n,
    )

    timestamp = args.timestamp or datetime.now().strftime("%Y%m%d_%H%M%S")
    model_dir = Path(MODEL_DIR)
    report_dir = Path(REPORT_DIR)
    model_dir.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)

    model_path = model_dir / f"lgbm_two_stage_ranker_{timestamp}.txt"
    meta_path = model_dir / f"lgbm_two_stage_ranker_{timestamp}_meta.json"
    candidate_path = report_dir / f"two_stage_ranker_train_candidates_{timestamp}.csv"
    importance_path = report_dir / f"two_stage_ranker_feature_importance_{timestamp}.csv"

    model.save_model(str(model_path))
    export_cols = [
        "rebalance_month",
        "ticker",
        "Date",
        "stage1_score",
        "stage1_rank",
        "rank_label",
        RAW_RETURN_COL,
        TARGET_COL,
    ]
    candidates[[col for col in export_cols if col in candidates.columns]].to_csv(
        candidate_path,
        index=False,
        encoding="utf-8",
    )
    importance = pd.DataFrame(
        {
            "feature": feature_cols,
            "importance_gain": model.feature_importance(importance_type="gain"),
            "importance_split": model.feature_importance(importance_type="split"),
        }
    ).sort_values("importance_gain", ascending=False)
    importance.to_csv(importance_path, index=False, encoding="utf-8")

    meta = {
        "model_file": str(model_path),
        "model_version": "two_stage_lambdarank_n75_label5",
        "trained_at": timestamp,
        "stage": 2,
        "stage1_model_file": stage1_meta.get("model_file"),
        "stage1_model_version": stage1_meta.get("model_version"),
        "stage1_trained_at": stage1_meta.get("trained_at"),
        "candidate_size": candidate_size,
        "label_bins": label_bins,
        "top_n": top_n,
        "feature_columns": feature_cols,
        "n_features": len(feature_cols),
        "n_months": int(candidates["rebalance_month"].nunique()),
        "n_candidates": int(len(candidates)),
        "n_stocks": int(monthly["ticker"].nunique()),
        "target": TARGET_COL,
        "objective": "lambdarank",
        "metric": "ndcg",
        "eval_at": [top_n],
        "lgbm_params": params,
        "candidate_artifact": str(candidate_path),
        "feature_importance_artifact": str(importance_path),
        "smoke_test": smoke,
    }
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[two-stage-train] model={model_path}")
    print(f"[two-stage-train] meta={meta_path}")
    print(f"[two-stage-train] candidates={candidate_path}")
    print(f"[two-stage-train] smoke={smoke}")
    return meta


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", default="cpu", choices=["cpu", "gpu"])
    parser.add_argument("--stage1-meta", default=None)
    parser.add_argument("--candidate-size", type=int, default=TWO_STAGE_CANDIDATE_SIZE)
    parser.add_argument("--label-bins", type=int, default=TWO_STAGE_LABEL_BINS)
    parser.add_argument("--top-n", type=int, default=TWO_STAGE_TOP_N)
    parser.add_argument("--num-boost-round", type=int, default=500)
    parser.add_argument("--timestamp", default=None)
    parser.add_argument("--quiet", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    train_two_stage_ranker(parse_args())
