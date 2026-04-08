"""V2 迴歸模型訓練：20 日超額報酬 walk-forward

Target: excess_return_20d = stock_return_20d - twii_return_20d
強制模型學 Alpha (選股)，而非 Beta (擇時)。

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

from ml.config import MODEL_DIR, REPORT_DIR
from ml.dataset import build_dataset, get_feature_columns

os.makedirs(REPORT_DIR, exist_ok=True)

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
    "n_jobs": -1,
    "seed": 42,
}
V2_NUM_ROUNDS = 500
V2_EARLY_STOPPING = 50
V2_TRAIN_MONTHS = 24
V2_TEST_MONTHS = 1


def train_v2(device: str = "gpu") -> dict | None:
    """Walk-forward 訓練 V2 迴歸模型並存檔。"""
    params = V2_PARAMS.copy()
    if device == "gpu":
        params["device"] = "gpu"

    print("=" * 60)
    print("  V2 迴歸模型訓練 (20D 超額報酬)")
    print("=" * 60)

    # 載入完整 dataset（已含 excess_return_20d）
    dataset = build_dataset(verbose=True)
    feature_cols = get_feature_columns()

    # 確認 target 欄位存在
    if "excess_return_20d" not in dataset.columns:
        raise ValueError("excess_return_20d 不在 dataset 中，請確認 target.py 已更新")

    # 過濾有效樣本
    dataset = dataset.dropna(subset=["excess_return_20d"]).copy()
    target_col = "excess_return_20d"

    # 確保特徵欄位存在
    available_features = [c for c in feature_cols if c in dataset.columns]
    print(f"  Features: {len(available_features)}")
    print(f"  Samples: {len(dataset):,}")
    print(f"  Date range: {dataset['Date'].min().date()} ~ {dataset['Date'].max().date()}")

    # === Walk-forward ===
    test_starts = pd.date_range("2024-01-01", dataset["Date"].max(), freq="MS")

    monthly_results = []
    best_model = None
    best_ic = -999

    for test_start in test_starts:
        test_end = test_start + pd.offsets.MonthEnd(0)
        train_start = test_start - pd.DateOffset(months=V2_TRAIN_MONTHS)

        train = dataset[(dataset["Date"] >= train_start) & (dataset["Date"] < test_start)]
        test = dataset[(dataset["Date"] >= test_start) & (dataset["Date"] <= test_end)]

        if len(train) < 10000 or len(test) < 100:
            continue

        X_train = train[available_features].values
        y_train = train[target_col].values
        X_val = test[available_features].values
        y_val = test[target_col].values

        dtrain = lgb.Dataset(X_train, y_train, feature_name=available_features)
        dval = lgb.Dataset(X_val, y_val, feature_name=available_features, reference=dtrain)

        model = lgb.train(
            params, dtrain,
            num_boost_round=V2_NUM_ROUNDS,
            valid_sets=[dval],
            callbacks=[lgb.early_stopping(V2_EARLY_STOPPING, verbose=False)],
        )

        # 預測與評估
        pred = model.predict(X_val)
        test_df = test[["ticker", "Date", "Close", target_col]].copy()
        test_df["pred_return"] = pred

        # 每日截面：取月底最後一天
        last_day = test_df.groupby("ticker").tail(1)
        last_day = last_day.sort_values("pred_return", ascending=False)

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
        })

        if ic > best_ic:
            best_ic = ic
            best_model = model

        print(f"  {test_start.strftime('%Y-%m')}  N={len(last_day):>4d}  "
              f"Top30={top_ret*100:+.2f}%  Spread={spread*100:+.2f}%  "
              f"WR={top_win*100:.0f}%  IC={ic:+.3f}")

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
    print("\n  Training final model on all data...")
    X_all = dataset[available_features].values
    y_all = dataset[target_col].values
    dtrain_all = lgb.Dataset(X_all, y_all, feature_name=available_features)
    final_model = lgb.train(params, dtrain_all, num_boost_round=V2_NUM_ROUNDS)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    model_path = os.path.join(MODEL_DIR, f"lgbm_v2_{ts}.txt")
    final_model.save_model(model_path)

    meta = {
        "model_file": model_path,
        "model_version": "v2_regression_20d_excess",
        "trained_at": ts,
        "feature_columns": available_features,
        "n_features": len(available_features),
        "n_stocks": dataset["ticker"].nunique(),
        "n_samples": len(dataset),
        "target": "20d_excess_return",
        "cross_sectional_zscore": True,
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
    }
    meta_path = model_path.replace(".txt", "_meta.json")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)

    # Save backtest monthly report
    report_path = os.path.join(REPORT_DIR, f"v2_backtest_{ts}.csv")
    rdf.to_csv(report_path, index=False)

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

    return meta


def main():
    parser = argparse.ArgumentParser(description="Train V2 regression model (20D excess return)")
    parser.add_argument("--device", default="gpu", choices=["gpu", "cpu"])
    args = parser.parse_args()

    train_v2(device=args.device)


if __name__ == "__main__":
    main()
