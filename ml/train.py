"""訓練 Pipeline：LightGBM Walk-Forward 訓練 + 模型儲存"""
import gc
import os
import json
import pickle
import warnings
from datetime import datetime

import numpy as np
import pandas as pd
import lightgbm as lgb
from sklearn.metrics import classification_report, f1_score
from tqdm import tqdm

from ml.config import (
    LGBM_PARAMS, LGBM_NUM_ROUNDS, LGBM_EARLY_STOPPING,
    MODEL_DIR, REPORT_DIR, TARGET_CLASSES,
)
from ml.features.registry import get_feature_columns
from ml.dataset import build_dataset, walk_forward_split

warnings.filterwarnings("ignore", category=UserWarning)


def train_fold(train_df, val_df, feature_cols):
    """訓練單一 fold 的 LightGBM 模型"""
    X_train = train_df[feature_cols].values
    y_train = train_df["target"].values
    X_val = val_df[feature_cols].values
    y_val = val_df["target"].values

    dtrain = lgb.Dataset(X_train, label=y_train, feature_name=feature_cols, free_raw_data=False)
    dval = lgb.Dataset(X_val, label=y_val, feature_name=feature_cols, free_raw_data=False)

    callbacks = [
        lgb.early_stopping(LGBM_EARLY_STOPPING, verbose=False),
        lgb.log_evaluation(period=0),
    ]

    model = lgb.train(
        LGBM_PARAMS,
        dtrain,
        num_boost_round=LGBM_NUM_ROUNDS,
        valid_sets=[dval],
        callbacks=callbacks,
    )
    return model


def evaluate_fold(model, test_df, feature_cols):
    """評估單一 fold"""
    X_test = test_df[feature_cols].values
    y_test = test_df["target"].values

    y_proba = model.predict(X_test)
    y_pred = np.argmax(y_proba, axis=1)

    f1_weighted = f1_score(y_test, y_pred, average="weighted", zero_division=0)
    f1_up = f1_score(y_test == 2, y_pred == 2, zero_division=0)

    report = classification_report(
        y_test, y_pred,
        target_names=list(TARGET_CLASSES.values()),
        output_dict=True, zero_division=0,
    )
    return {
        "f1_weighted": f1_weighted,
        "f1_up_class": f1_up,
        "precision_up": report.get("UP", {}).get("precision", 0),
        "recall_up": report.get("UP", {}).get("recall", 0),
        "n_test": len(y_test),
        "class_dist": {TARGET_CLASSES[i]: int((y_test == i).sum()) for i in range(3)},
    }


def run_training(max_stocks: int = 0):
    """完整訓練流程：組裝資料 → Walk-Forward 訓練 → 儲存最佳模型

    Args:
        max_stocks: 限制股票數（0=全部，用於快速測試）
    """
    print("=" * 60)
    print("  台股 ML 預測模型訓練")
    print("=" * 60)

    # 組裝資料集
    dataset = build_dataset(max_stocks=max_stocks)
    feature_cols = get_feature_columns()

    # 檢查並填補缺失值
    for col in feature_cols:
        if col not in dataset.columns:
            dataset[col] = np.nan

    print(f"\n開始 Walk-Forward 訓練...")
    # 先計算總 fold 數（不展開資料），再用 generator 逐 fold 處理省 RAM
    total_folds = sum(1 for _ in walk_forward_split(dataset))
    print(f"  共 {total_folds} 個 fold")

    fold_results = []
    best_model = None
    best_f1 = -1

    for i, (fold_idx, train_df, val_df, test_df, test_end) in enumerate(walk_forward_split(dataset), 1):
        pct = i / total_folds * 100
        print(f"  [{('█' * int(pct // 5)):<20s}] {pct:5.1f}% Fold {fold_idx} "
              f"(test→{test_end.date()})...", end="", flush=True)

        model = train_fold(train_df, val_df, feature_cols)
        metrics = evaluate_fold(model, test_df, feature_cols)
        metrics["fold"] = fold_idx
        metrics["test_end"] = str(test_end.date())
        fold_results.append(metrics)

        print(f" F1={metrics['f1_weighted']:.3f}, "
              f"UP精確率={metrics['precision_up']:.3f}, "
              f"UP召回率={metrics['recall_up']:.3f}, "
              f"測試樣本={metrics['n_test']}")

        if metrics["f1_weighted"] > best_f1:
            best_f1 = metrics["f1_weighted"]
            best_model = model

        del train_df, val_df, test_df
        gc.collect()

    if not fold_results:
        print("沒有足夠資料進行 Walk-Forward 訓練")
        return

    # 最終用全部資料訓練一個 production 模型
    print(f"\n用全部資料訓練 Production 模型...")
    all_dates = sorted(dataset["Date"].unique())
    cutoff = pd.Timestamp(all_dates[-1]) - pd.DateOffset(months=3)
    final_train = dataset[dataset["Date"] < cutoff]
    final_val = dataset[dataset["Date"] >= cutoff]
    production_model = train_fold(final_train, final_val, feature_cols)

    # 儲存
    os.makedirs(MODEL_DIR, exist_ok=True)
    os.makedirs(REPORT_DIR, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    model_path = os.path.join(MODEL_DIR, f"lgbm_{timestamp}.txt")
    production_model.save_model(model_path)

    meta = {
        "model_file": model_path,
        "trained_at": timestamp,
        "feature_columns": feature_cols,
        "n_features": len(feature_cols),
        "n_stocks": dataset["ticker"].nunique(),
        "n_samples": len(dataset),
        "date_range": [str(dataset["Date"].min().date()), str(dataset["Date"].max().date())],
        "walk_forward_folds": len(fold_results),
        "avg_f1_weighted": np.mean([r["f1_weighted"] for r in fold_results]),
        "avg_precision_up": np.mean([r["precision_up"] for r in fold_results]),
        "fold_results": fold_results,
    }
    meta_path = os.path.join(MODEL_DIR, f"lgbm_{timestamp}_meta.json")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2, default=str)

    # 特徵重要度
    importance = production_model.feature_importance(importance_type="gain")
    feat_imp = sorted(zip(feature_cols, importance), key=lambda x: -x[1])
    imp_path = os.path.join(REPORT_DIR, f"feature_importance_{timestamp}.csv")
    pd.DataFrame(feat_imp, columns=["feature", "importance"]).to_csv(imp_path, index=False)

    # 摘要
    print(f"\n{'=' * 60}")
    print(f"  訓練完成")
    print(f"{'=' * 60}")
    print(f"  模型: {model_path}")
    print(f"  平均 F1: {meta['avg_f1_weighted']:.4f}")
    print(f"  平均 UP 精確率: {meta['avg_precision_up']:.4f}")
    print(f"  Top 10 特徵:")
    for name, imp in feat_imp[:10]:
        print(f"    {name}: {imp:.1f}")

    return production_model, meta


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="訓練台股預測模型")
    parser.add_argument("--max-stocks", type=int, default=0, help="限制股票數(測試用)")
    args = parser.parse_args()
    run_training(max_stocks=args.max_stocks)
