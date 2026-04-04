"""Walk-Forward 模擬投資組合回測

每個 fold 訓練後，選 Top N 預測 UP 的股票，
計算其實際超額報酬，與基準（全體股票均值）比較。

使用方式:
    python -m ml.backtest               # 完整回測
    python -m ml.backtest --top 10      # Top 10 策略
    python -m ml.backtest --max-stocks 100  # 快速測試
"""
import os
import json
import warnings
from datetime import datetime

import numpy as np
import pandas as pd
import lightgbm as lgb
from tqdm import tqdm

from ml.config import (
    LGBM_PARAMS, LGBM_NUM_ROUNDS, LGBM_EARLY_STOPPING,
    MODEL_DIR, REPORT_DIR, TARGET_CLASSES, FORWARD_DAYS,
)
from ml.features.registry import get_feature_columns
from ml.dataset import build_dataset, walk_forward_split
from ml.train import train_fold

warnings.filterwarnings("ignore", category=UserWarning)


def run_backtest(max_stocks: int = 0, top_n: int = 10):
    """Walk-forward 模擬投資組合回測

    Args:
        max_stocks: 限制股票數 (0=全部)
        top_n: 每期選前 N 支股票
    """
    print("=" * 60)
    print(f"  Walk-Forward 模擬投資回測 (Top {top_n})")
    print("=" * 60)

    dataset = build_dataset(max_stocks=max_stocks)
    feature_cols = get_feature_columns()

    for col in feature_cols:
        if col not in dataset.columns:
            dataset[col] = np.nan

    # 確保有 excess_return 欄位供計算實際報酬
    if "excess_return" not in dataset.columns:
        print("[錯誤] dataset 中沒有 excess_return 欄位")
        return

    all_fold_records = []  # 每日選股記錄
    fold_summaries = []    # 每 fold 摘要

    print(f"\n開始 Walk-Forward 回測...")

    for fold_idx, train_df, val_df, test_df, test_end in walk_forward_split(dataset):
        # 訓練模型
        model = train_fold(train_df, val_df, feature_cols)

        # 在 test 資料上預測
        X_test = test_df[feature_cols].values
        y_proba = model.predict(X_test)
        y_pred = np.argmax(y_proba, axis=1)

        test_result = test_df[["ticker", "Date", "Close", "excess_return", "target"]].copy()
        test_result["up_prob"] = y_proba[:, 2]
        test_result["down_prob"] = y_proba[:, 0]
        test_result["pred_signal"] = [TARGET_CLASSES[p] for p in y_pred]

        # 逐日選股
        dates = sorted(test_result["Date"].unique())
        fold_daily_returns = []
        fold_benchmark_returns = []

        for dt in dates:
            day_df = test_result[test_result["Date"] == dt].copy()
            # 排除 excess_return 為 NaN 的（最後幾天沒有 forward return）
            day_valid = day_df.dropna(subset=["excess_return"])

            if len(day_valid) < top_n:
                continue

            # 策略: 選 up_prob 最高的 Top N
            top_stocks = day_valid.nlargest(top_n, "up_prob")
            portfolio_return = top_stocks["excess_return"].mean()

            # 基準: 全部股票等權平均
            benchmark_return = day_valid["excess_return"].mean()

            fold_daily_returns.append(portfolio_return)
            fold_benchmark_returns.append(benchmark_return)

            # 記錄每日選股
            for _, row in top_stocks.iterrows():
                all_fold_records.append({
                    "fold": fold_idx,
                    "date": str(row["Date"].date()) if hasattr(row["Date"], "date") else str(row["Date"]),
                    "ticker": row["ticker"],
                    "close": round(float(row["Close"]), 2),
                    "excess_return": round(float(row["excess_return"]) * 100, 4),
                    "up_prob": round(float(row["up_prob"]), 4),
                    "actual_label": TARGET_CLASSES.get(int(row["target"]), "?"),
                    "pred_signal": row["pred_signal"],
                })

        if not fold_daily_returns:
            continue

        # Fold 摘要
        avg_port = np.mean(fold_daily_returns) * 100
        avg_bench = np.mean(fold_benchmark_returns) * 100
        alpha = avg_port - avg_bench
        hit_rate = np.mean([r > 0 for r in fold_daily_returns]) * 100

        # 勝率 vs 基準
        beat_bench = np.mean([p > b for p, b in zip(fold_daily_returns, fold_benchmark_returns)]) * 100

        # 策略累積報酬 (假設每日換股)
        cumul_port = np.prod([1 + r for r in fold_daily_returns]) - 1
        cumul_bench = np.prod([1 + r for r in fold_benchmark_returns]) - 1

        fold_summaries.append({
            "fold": fold_idx,
            "test_end": str(test_end.date()),
            "n_days": len(fold_daily_returns),
            "avg_daily_excess_return_pct": round(avg_port, 4),
            "avg_benchmark_pct": round(avg_bench, 4),
            "alpha_pct": round(alpha, 4),
            "hit_rate_pct": round(hit_rate, 2),
            "beat_benchmark_pct": round(beat_bench, 2),
            "cumul_portfolio_pct": round(cumul_port * 100, 4),
            "cumul_benchmark_pct": round(cumul_bench * 100, 4),
        })

        print(f"  Fold {fold_idx} ({test_end.date()}): "
              f"策略{avg_port:+.3f}% vs 基準{avg_bench:+.3f}% "
              f"α={alpha:+.3f}% 勝率={hit_rate:.0f}% "
              f"勝基準={beat_bench:.0f}%")

    if not fold_summaries:
        print("無可用的回測結果")
        return

    # 全期摘要
    all_alphas = [f["alpha_pct"] for f in fold_summaries]
    all_hit = [f["hit_rate_pct"] for f in fold_summaries]
    all_beat = [f["beat_benchmark_pct"] for f in fold_summaries]
    all_port_cumul = [f["cumul_portfolio_pct"] for f in fold_summaries]
    all_bench_cumul = [f["cumul_benchmark_pct"] for f in fold_summaries]

    overall = {
        "strategy": f"Top {top_n} by UP probability",
        "forward_days": FORWARD_DAYS,
        "n_folds": len(fold_summaries),
        "date_range": [fold_summaries[0]["test_end"], fold_summaries[-1]["test_end"]],
        "avg_alpha_pct": round(np.mean(all_alphas), 4),
        "median_alpha_pct": round(np.median(all_alphas), 4),
        "positive_alpha_folds": sum(1 for a in all_alphas if a > 0),
        "avg_hit_rate_pct": round(np.mean(all_hit), 2),
        "avg_beat_benchmark_pct": round(np.mean(all_beat), 2),
        "total_portfolio_return_pct": round(sum(all_port_cumul), 4),
        "total_benchmark_return_pct": round(sum(all_bench_cumul), 4),
    }

    print(f"\n{'=' * 60}")
    print(f"  回測結果摘要 (Top {top_n})")
    print(f"{'=' * 60}")
    print(f"  回測期間: {overall['date_range'][0]} ~ {overall['date_range'][1]}")
    print(f"  總 fold 數: {overall['n_folds']}")
    print(f"  平均 Alpha: {overall['avg_alpha_pct']:+.4f}% / 日")
    print(f"  正 Alpha fold: {overall['positive_alpha_folds']} / {overall['n_folds']}")
    print(f"  平均勝率 (>0): {overall['avg_hit_rate_pct']:.1f}%")
    print(f"  平均勝基準率: {overall['avg_beat_benchmark_pct']:.1f}%")
    print(f"  策略總累計報酬: {overall['total_portfolio_return_pct']:+.2f}%")
    print(f"  基準總累計報酬: {overall['total_benchmark_return_pct']:+.2f}%")

    # 儲存結果
    os.makedirs(REPORT_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    result_path = os.path.join(REPORT_DIR, f"backtest_{timestamp}.json")

    result = {
        "generated_at": timestamp,
        "top_n": top_n,
        "max_stocks": max_stocks,
        "overall": overall,
        "fold_summaries": fold_summaries,
    }

    def _default(o):
        if hasattr(o, 'item'):
            return o.item()
        raise TypeError(f"Object of type {type(o).__name__} is not JSON serializable")

    with open(result_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2, default=_default)

    # 也存選股明細 CSV
    detail_path = os.path.join(REPORT_DIR, f"backtest_detail_{timestamp}.csv")
    pd.DataFrame(all_fold_records).to_csv(detail_path, index=False, encoding="utf-8-sig")

    print(f"\n  回測結果: {result_path}")
    print(f"  選股明細: {detail_path}")

    # 同時存一份 latest 供 API 讀取
    latest_path = os.path.join(REPORT_DIR, "backtest_latest.json")
    with open(latest_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2, default=_default)
    print(f"  Latest:   {latest_path}")

    return result


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Walk-Forward 模擬投資回測")
    parser.add_argument("--top", type=int, default=10, help="每期選前 N 支 (預設 10)")
    parser.add_argument("--max-stocks", type=int, default=0, help="限制股票數 (測試用)")
    args = parser.parse_args()
    run_backtest(max_stocks=args.max_stocks, top_n=args.top)
