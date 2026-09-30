"""V2 迴歸模型訓練：20 日超額報酬 walk-forward

Canonical trade spec: Entry=Open[t+1], Exit=Close[t+20]
Target: trade_excess_return_20d = Close[t+20]/Open[t+1] - 1 - TWII_20d
強制模型學可交易的 Alpha (選股)，而非 Beta (擇時)。

Usage:
    python scripts/train_v2.py                 # GPU walk-forward 訓練
    python scripts/train_v2.py --device cpu     # CPU 模式
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import warnings
from datetime import datetime

import lightgbm as lgb
import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ml.config import (
    LGBM_NUM_THREADS,
    MODEL_DIR,
    REPORT_DIR,
    REGIME_FEATURE_COLS,
    REGIME_FEATURE_FRACTION_CAP,
    coerce_lgbm_num_threads,
)
from ml.dataset import build_dataset, get_feature_columns
from scripts.public_market_context_quality import (
    evaluate_public_market_context,
    write_public_market_context_quality_report,
)

# === V2 超參數 ===
V2_PARAMS = {
    "objective": "regression",
    "metric": "rmse",
    "boosting_type": "gbdt",
    "num_leaves": 31,
    "learning_rate": 0.05,
    "feature_fraction": 0.6,       # 刻意壓低，避免過度依賴少數大盤特徵
    "bagging_fraction": 0.7,
    "bagging_freq": 5,
    "lambda_l1": 0.1,              # L1 正則化
    "lambda_l2": 1.0,              # L2 正則化
    "min_child_samples": 50,       # 避免過擬合小群體
    "verbose": -1,
    "n_jobs": LGBM_NUM_THREADS,
    "seed": 42,
}
V2_NUM_ROUNDS = 500
V2_EARLY_STOPPING = 50
V2_TRAIN_MONTHS = 24
V2_TEST_MONTHS = 1
V2_EMBARGO_TRADING_DAYS = 20
V2_VALIDATION_TRADING_DAYS = 60


def _prepare_temporal_frame(dataset: pd.DataFrame) -> tuple[pd.DataFrame, pd.DatetimeIndex]:
    """Preserve the pre-target-filter calendar and conservative label end dates."""
    frame = dataset.copy()
    frame["Date"] = pd.to_datetime(frame["Date"], errors="raise").dt.normalize()
    if frame["Date"].isna().any() or frame.duplicated(["ticker", "Date"]).any():
        raise ValueError("V2 temporal frame requires one valid date per ticker/date")
    frame = frame.sort_values(["ticker", "Date"])
    calendar = pd.DatetimeIndex(frame["Date"].drop_duplicates().sort_values())
    # A ticker with sparse rows may realize its 20-bar label after the global
    # 20-session boundary. Missing end dates must not pass the purge.
    frame["_label_end_date"] = frame.groupby("ticker")["Date"].shift(-V2_EMBARGO_TRADING_DAYS)
    return frame, calendar


def _temporal_fold(dataset: pd.DataFrame, calendar: pd.DatetimeIndex,
                   test_start: pd.Timestamp) -> dict | None:
    """Train / 60-session validation / monthly test, with two 20-session purges."""
    test_start = pd.Timestamp(test_start)
    test_end = test_start + pd.offsets.MonthEnd(0)
    test = dataset[dataset["Date"].between(test_start, test_end)].copy()
    if test.empty:
        return None
    test_first = test["Date"].min()
    test_index = int(calendar.get_indexer([test_first])[0])
    validation_end_index = test_index - V2_EMBARGO_TRADING_DAYS
    validation_start_index = validation_end_index - V2_VALIDATION_TRADING_DAYS
    train_end_index = validation_start_index - V2_EMBARGO_TRADING_DAYS
    if train_end_index <= 0:
        return None
    validation_start = calendar[validation_start_index]
    validation_end = calendar[validation_end_index]
    train_end = calendar[train_end_index]
    train_start = test_start - pd.DateOffset(months=V2_TRAIN_MONTHS)
    train = dataset[(dataset["Date"] >= train_start) & (dataset["Date"] < train_end)
                    & (dataset["_label_end_date"] < validation_start)].copy()
    validation = dataset[(dataset["Date"] >= validation_start) & (dataset["Date"] < validation_end)
                         & (dataset["_label_end_date"] < test_first)].copy()
    if train.empty or validation.empty:
        return None
    return {"train": train, "validation": validation, "test": test,
            "train_start": train_start, "train_end_exclusive": train_end,
            "validation_start": validation_start, "validation_end_exclusive": validation_end,
            "test_start": test_first, "test_end": test_end}


def _fit_validated_model(train: pd.DataFrame, validation: pd.DataFrame,
                         features: list[str], params: dict, target: str) -> tuple[lgb.Booster, int]:
    """No test argument: its labels are inaccessible to early stopping."""
    if train.empty or validation.empty or train["Date"].max() >= validation["Date"].min():
        raise ValueError("V2 requires nonempty chronological train and validation sets")
    dtrain = lgb.Dataset(train[features].values, train[target].values, feature_name=features)
    dval = lgb.Dataset(validation[features].values, validation[target].values,
                       feature_name=features, reference=dtrain)
    model = lgb.train(params, dtrain, num_boost_round=V2_NUM_ROUNDS,
                      valid_sets=[dval], valid_names=["validation"],
                      callbacks=[lgb.early_stopping(V2_EARLY_STOPPING, verbose=False)])
    rounds = int(model.best_iteration or V2_NUM_ROUNDS)
    return model, rounds


def _final_num_rounds(validation_rounds: list[int]) -> int:
    if not validation_rounds or any(r <= 0 or r > V2_NUM_ROUNDS for r in validation_rounds):
        raise ValueError("V2 final rounds require valid validation-selected iterations")
    return int(np.median(validation_rounds))


def _fit_fold(fold: dict, features: list[str], params: dict, target: str) -> tuple[lgb.Booster, int, np.ndarray]:
    """Fit/early-stop on prior partitions, then predict untouched test features."""
    model, rounds = _fit_validated_model(fold["train"], fold["validation"], features, params, target)
    predictions = model.predict(fold["test"][features].values, num_iteration=rounds)
    return model, rounds, predictions


def _month_end_cohort(test: pd.DataFrame) -> pd.DataFrame:
    """One common as-of date; never substitute an earlier row for a missing name."""
    if test.empty:
        return test.copy()
    return test.loc[test["Date"] == test["Date"].max()].sort_values("pred_return", ascending=False)


def _regime_importance_share(model: lgb.Booster, feature_cols: list[str]) -> float:
    importance = model.feature_importance(importance_type="gain")
    total = float(np.sum(importance))
    if total <= 0:
        return 0.0
    regime = set(REGIME_FEATURE_COLS)
    return sum(float(v) for c, v in zip(feature_cols, importance) if c in regime) / total


def train_v2(device: str = "gpu", cpu_threads: int | None = None) -> dict | None:
    """Walk-forward 訓練 V2 迴歸模型並存檔。"""
    os.makedirs(REPORT_DIR, exist_ok=True)
    params = V2_PARAMS.copy()
    params["n_jobs"] = coerce_lgbm_num_threads(cpu_threads, default=LGBM_NUM_THREADS)
    if device == "gpu":
        params["device"] = "gpu"
    else:
        params.pop("device", None)

    print("=" * 60)
    print("  V2 迴歸模型訓練 (20D 超額報酬)")
    print("=" * 60)
    print(f"  LightGBM device={device} cpu_threads={params['n_jobs']}")
    public_context_quality = evaluate_public_market_context()
    print(
        "  Public market context: "
        f"status={public_context_quality['status']} "
        f"mode={public_context_quality['selection_usage_mode']} "
        f"gap={public_context_quality['model_feature_readiness']}"
    )

    # 載入完整 dataset（已含 excess_return_20d）
    dataset = build_dataset(verbose=True)
    feature_cols = get_feature_columns()

    # 確認 target 欄位存在
    if "trade_excess_return_20d" not in dataset.columns:
        raise ValueError("trade_excess_return_20d 不在 dataset 中，請確認 target.py 已更新")

    # Keep the calendar and ticker label horizons before dropping invalid targets.
    dataset, unique_dates = _prepare_temporal_frame(dataset)
    # 過濾有效樣本
    dataset = dataset.dropna(subset=["trade_excess_return_20d"]).copy()
    target_col = "trade_excess_return_20d"

    # 確保特徵欄位存在
    available_features = [c for c in feature_cols if c in dataset.columns]
    print(f"  Features: {len(available_features)}")
    print(f"  Samples: {len(dataset):,}")
    print(f"  Date range: {dataset['Date'].min().date()} ~ {dataset['Date'].max().date()}")
    print(f"  Embargo: {V2_EMBARGO_TRADING_DAYS} trading days")
    print(f"  Validation: {V2_VALIDATION_TRADING_DAYS} trading days; purge on both boundaries")

    # === Walk-forward ===
    test_starts = pd.date_range("2024-01-01", dataset["Date"].max(), freq="MS")

    monthly_results = []
    fold_top30_rows = []
    validation_rounds = []

    for test_start in test_starts:
        fold = _temporal_fold(dataset, unique_dates, test_start)
        if fold is None:
            continue
        train, validation, test = fold["train"], fold["validation"], fold["test"]
        if len(train) < 10000 or len(validation) < 100 or len(test) < 100:
            continue
        train_start, embargo_cutoff, test_end = fold["train_start"], fold["train_end_exclusive"], fold["test_end"]

        model, best_iteration, pred = _fit_fold(fold, available_features, params, target_col)
        validation_rounds.append(best_iteration)
        regime_share = _regime_importance_share(model, available_features)
        if regime_share > REGIME_FEATURE_FRACTION_CAP:
            print(
                f"    WARNING RegimeImp={regime_share:.1%} "
                f"> cap {REGIME_FEATURE_FRACTION_CAP:.0%}"
            )

        # 預測與評估
        artifact_cols = [
            "ticker",
            "Date",
            "Close",
            "trade_return_20d",
            "trade_excess_return_20d",
        ]
        artifact_cols.extend([col for col in REGIME_FEATURE_COLS if col in test.columns])
        test_df = test[[col for col in artifact_cols if col in test.columns]].copy()
        test_df["pred_return"] = pred

        # 同一個月底截面，不把停牌股票更早的價格混入。
        last_day = _month_end_cohort(test_df)

        ic = last_day["pred_return"].corr(last_day[target_col], method="spearman")
        top30 = last_day.head(30)
        bot30 = last_day.tail(30)
        top_ret = top30[target_col].mean()
        bot_ret = bot30[target_col].mean()
        spread = top_ret - bot_ret
        top_win = (top30[target_col] > 0).mean()

        monthly_results.append({
            "month": test_start.strftime("%Y-%m"),
            "n": len(last_day),
            "top30_excess": top_ret,
            "bot30_excess": bot_ret,
            "spread": spread,
            "top_win_rate": top_win,
            "ic": ic,
            "regime_feature_importance_share": regime_share,
            "validation_selected_rounds": best_iteration,
            "train_samples": len(train), "validation_samples": len(validation), "test_samples": len(test),
            "evaluation_date": pd.Timestamp(last_day["Date"].max()).date().isoformat(),
            "validation_start": fold["validation_start"].date().isoformat(),
            "validation_end_exclusive": fold["validation_end_exclusive"].date().isoformat(),
        })
        for rank, row in enumerate(top30.to_dict("records"), start=1):
            fold_top30_rows.append(
                {
                    "month": test_start.strftime("%Y-%m"),
                    "fold_test_start": test_start.date().isoformat(),
                    "fold_test_end": test_end.date().isoformat(),
                    "train_start": train_start.date().isoformat(),
                    "train_end_exclusive": pd.Timestamp(embargo_cutoff).date().isoformat(),
                    "validation_start": fold["validation_start"].date().isoformat(),
                    "validation_end_exclusive": fold["validation_end_exclusive"].date().isoformat(),
                    "validation_selected_rounds": best_iteration,
                    "ticker": row["ticker"],
                    "date": pd.Timestamp(row["Date"]).date().isoformat(),
                    "rank": rank,
                    "pred_return": float(row["pred_return"]),
                    "trade_return_20d": float(row["trade_return_20d"])
                    if not pd.isna(row.get("trade_return_20d"))
                    else None,
                    "trade_excess_return_20d": float(row["trade_excess_return_20d"])
                    if not pd.isna(row.get("trade_excess_return_20d"))
                    else None,
                    **{
                        col: (
                            float(row[col])
                            if col in row and not pd.isna(row.get(col))
                            else None
                        )
                        for col in REGIME_FEATURE_COLS
                    },
                }
            )

        print(f"  {test_start.strftime('%Y-%m')}  N={len(last_day):>4d}  "
              f"Top30={top_ret*100:+.2f}%  Spread={spread*100:+.2f}%  "
              f"WR={top_win*100:.0f}%  IC={ic:+.3f}  RegimeImp={regime_share:.1%}")

    if not monthly_results:
        print("  No valid folds, aborting.")
        return None

    rdf = pd.DataFrame(monthly_results)

    # === Summary ===
    print(f"\n{'=' * 60}")
    print(f"  V2 Backtest Summary (20D excess return)")
    print(f"{'=' * 60}")
    print(f"  Months:             {len(rdf)}")
    print(f"  Avg Top30 excess:   {rdf['top30_excess'].mean()*100:+.3f}%")
    print(f"  Avg Spread:         {rdf['spread'].mean()*100:+.3f}%")
    print(f"  Avg Top30 win rate: {rdf['top_win_rate'].mean()*100:.1f}%")
    print(f"  Avg IC:             {rdf['ic'].mean():+.4f}")
    print(f"  IC > 0 months:      {(rdf['ic'] > 0).sum()}/{len(rdf)}")

    cum_excess = (1 + rdf["top30_excess"]).cumprod()
    print(f"  Cumulative excess:  {(cum_excess.iloc[-1] - 1) * 100:+.1f}%")

    # === 用全部資料訓練最終模型 ===
    final_rounds = _final_num_rounds(validation_rounds)
    print(f"\n  Training final model on all data with {final_rounds} validation-selected rounds...")
    X_all = dataset[available_features].values
    y_all = dataset[target_col].values
    dtrain_all = lgb.Dataset(X_all, y_all, feature_name=available_features)
    final_model = lgb.train(params, dtrain_all, num_boost_round=final_rounds)
    final_regime_share = _regime_importance_share(final_model, available_features)
    if final_regime_share > REGIME_FEATURE_FRACTION_CAP:
        print(
            f"  WARNING final RegimeImp={final_regime_share:.1%} "
            f"> cap {REGIME_FEATURE_FRACTION_CAP:.0%}"
        )

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    model_path = os.path.join(MODEL_DIR, f"lgbm_v2_{ts}.txt")
    final_model.save_model(model_path)
    public_context_quality_path = os.path.join(
        REPORT_DIR,
        f"public_market_context_quality_v2_{ts}.json",
    )
    write_public_market_context_quality_report(
        public_context_quality,
        path=public_context_quality_path,
    )

    meta = {
        "model_file": model_path,
        "model_version": "v2_regression_20d_excess",
        "trained_at": ts,
        "feature_columns": available_features,
        "n_features": len(available_features),
        "n_stocks": dataset["ticker"].nunique(),
        "n_samples": len(dataset),
        "target": "trade_excess_return_20d",
        "cross_sectional_zscore": True,
        "embargo_trading_days": V2_EMBARGO_TRADING_DAYS,
        "evaluation_protocol": "purged_train_validation_test_v1",
        "validation_trading_days": V2_VALIDATION_TRADING_DAYS,
        "validation_selected_rounds": validation_rounds,
        "final_num_boost_round": final_rounds,
        "final_round_selection": "integer_median_of_validation_best_iterations",
        "date_range": [
            str(dataset["Date"].min().date()),
            str(dataset["Date"].max().date()),
        ],
        "lgbm_params": params,
        "backtest_months": len(rdf),
        "backtest_avg_spread": float(rdf["spread"].mean()),
        "backtest_avg_ic": float(rdf["ic"].mean()),
        "backtest_top30_win_rate": float(rdf["top_win_rate"].mean()),
        "backtest_top30_avg_excess": float(rdf["top30_excess"].mean()),
        "regime_feature_cols": REGIME_FEATURE_COLS,
        "regime_feature_fraction_cap": REGIME_FEATURE_FRACTION_CAP,
        "production_regime_feature_importance_share": final_regime_share,
        "production_regime_feature_importance_pass": final_regime_share <= REGIME_FEATURE_FRACTION_CAP,
        "fold_top30_artifact": None,
        "public_market_context_quality_artifact": public_context_quality_path,
        "public_market_context_quality": public_context_quality,
    }
    meta_path = model_path.replace(".txt", "_meta.json")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)

    # Save backtest monthly report
    report_path = os.path.join(REPORT_DIR, f"v2_backtest_{ts}.csv")
    rdf.to_csv(report_path, index=False)
    fold_top30_path = os.path.join(REPORT_DIR, f"v2_fold_top30_{ts}.csv")
    pd.DataFrame(fold_top30_rows).to_csv(fold_top30_path, index=False, encoding="utf-8-sig")
    meta["fold_top30_artifact"] = fold_top30_path
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)

    # Save feature importance
    fi = pd.DataFrame({
        "feature": available_features,
        "importance": final_model.feature_importance(importance_type="gain"),
    }).sort_values("importance", ascending=False)
    fi_path = os.path.join(REPORT_DIR, f"feature_importance_v2_{ts}.csv")
    fi.to_csv(fi_path, index=False)

    print(f"\n  Model: {model_path}")
    print(f"  Meta:  {meta_path}")
    print(f"  Report: {report_path}")
    print(f"  Fold Top30: {fold_top30_path}")

    return meta


def main():
    parser = argparse.ArgumentParser(description="Train V2 regression model (20D excess return)")
    parser.add_argument("--device", default="gpu", choices=["gpu", "cpu"])
    parser.add_argument(
        "--cpu-threads",
        type=int,
        default=None,
        help="LightGBM CPU thread cap. Defaults to STOCK_LGBM_THREADS/LGBM_NUM_THREADS or 8.",
    )
    args = parser.parse_args()

    train_v2(device=args.device, cpu_threads=args.cpu_threads)


if __name__ == "__main__":
    main()
