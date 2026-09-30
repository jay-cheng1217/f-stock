"""Walk-forward backtest for the legacy classifier stack.

Benchmark contract:
    - Portfolio return columns in this script are excess returns versus TWII.
    - The explicit benchmark for selection alpha is the equal-weight universe
      excess return on the same signal day.
"""

from __future__ import annotations

import json
import os
import warnings
from datetime import datetime
from typing import Any

import numpy as np

from ml.backtest_metrics import build_backtest_risk_summary
from ml.benchmark_contract import build_selection_alpha_benchmark_contract
from ml.config import FORWARD_DAYS, REPORT_DIR, TARGET_CLASSES
from ml.dataset import build_dataset, walk_forward_split
from ml.features.registry import get_feature_columns
from ml.train import train_fold

warnings.filterwarnings("ignore", category=UserWarning)


def run_backtest(max_stocks: int = 0, top_n: int = 10) -> dict[str, Any] | None:
    """Run the legacy walk-forward backtest using a fixed benchmark contract."""

    print("=" * 60)
    print(f"  Walk-Forward Backtest (Top {top_n})")
    print("=" * 60)

    dataset = build_dataset(max_stocks=max_stocks)
    feature_cols = get_feature_columns()

    for column in feature_cols:
        if column not in dataset.columns:
            dataset[column] = np.nan

    if "excess_return" not in dataset.columns:
        print("[skip] dataset has no excess_return column")
        return None

    all_fold_records: list[dict[str, Any]] = []
    fold_summaries: list[dict[str, Any]] = []

    print("\nStarting walk-forward backtest...")
    for fold_idx, train_df, val_df, test_df, test_end in walk_forward_split(dataset):
        model = train_fold(train_df, val_df, feature_cols)

        x_test = test_df[feature_cols].values
        y_proba = model.predict(x_test)
        y_pred = np.argmax(y_proba, axis=1)

        test_result = test_df[["ticker", "Date", "Close", "excess_return", "target"]].copy()
        test_result["up_prob"] = y_proba[:, 2]
        test_result["down_prob"] = y_proba[:, 0]
        test_result["pred_signal"] = [TARGET_CLASSES[pred] for pred in y_pred]

        dates = sorted(test_result["Date"].unique())
        fold_daily_returns: list[float] = []
        fold_benchmark_returns: list[float] = []

        for trade_date in dates:
            day_df = test_result[test_result["Date"] == trade_date].copy()
            day_valid = day_df.dropna(subset=["excess_return"])
            if len(day_valid) < top_n:
                continue

            top_stocks = day_valid.nlargest(top_n, "up_prob")
            portfolio_return = float(top_stocks["excess_return"].mean())
            benchmark_return = float(day_valid["excess_return"].mean())

            fold_daily_returns.append(portfolio_return)
            fold_benchmark_returns.append(benchmark_return)

            for _, row in top_stocks.iterrows():
                all_fold_records.append(
                    {
                        "fold": fold_idx,
                        "date": str(row["Date"].date()) if hasattr(row["Date"], "date") else str(row["Date"]),
                        "ticker": row["ticker"],
                        "close": round(float(row["Close"]), 2),
                        "excess_return": round(float(row["excess_return"]) * 100, 4),
                        "up_prob": round(float(row["up_prob"]), 4),
                        "actual_label": TARGET_CLASSES.get(int(row["target"]), "?"),
                        "pred_signal": row["pred_signal"],
                    }
                )

        if not fold_daily_returns:
            continue

        avg_port = float(np.mean(fold_daily_returns) * 100)
        avg_bench = float(np.mean(fold_benchmark_returns) * 100)
        selection_alpha = avg_port - avg_bench
        hit_rate = float(np.mean([value > 0 for value in fold_daily_returns]) * 100)
        beat_bench = float(
            np.mean([p_val > b_val for p_val, b_val in zip(fold_daily_returns, fold_benchmark_returns)]) * 100
        )

        cumul_port = float(np.prod([1 + value for value in fold_daily_returns]) - 1)
        cumul_bench = float(np.prod([1 + value for value in fold_benchmark_returns]) - 1)

        fold_summaries.append(
            {
                "fold": fold_idx,
                "test_end": str(test_end.date()),
                "n_days": len(fold_daily_returns),
                "avg_daily_excess_return_pct": round(avg_port, 4),
                "avg_equal_weight_universe_excess_pct": round(avg_bench, 4),
                "selection_alpha_vs_equal_weight_pct": round(selection_alpha, 4),
                # Legacy aliases kept for downstream readers during contract migration.
                "avg_benchmark_pct": round(avg_bench, 4),
                "alpha_pct": round(selection_alpha, 4),
                "hit_rate_pct": round(hit_rate, 2),
                "beat_benchmark_pct": round(beat_bench, 2),
                "cumul_portfolio_pct": round(cumul_port * 100, 4),
                "cumul_equal_weight_universe_excess_pct": round(cumul_bench * 100, 4),
                "cumul_benchmark_pct": round(cumul_bench * 100, 4),
            }
        )

        print(
            f"  Fold {fold_idx} ({test_end.date()}): "
            f"portfolio_excess={avg_port:+.3f}% "
            f"ew_universe_excess={avg_bench:+.3f}% "
            f"selection_alpha={selection_alpha:+.3f}% "
            f"hit_rate={hit_rate:.0f}% "
            f"beat_benchmark={beat_bench:.0f}%"
        )

    if not fold_summaries:
        print("No valid folds found.")
        return None

    all_alphas = [fold["selection_alpha_vs_equal_weight_pct"] for fold in fold_summaries]
    all_hit = [fold["hit_rate_pct"] for fold in fold_summaries]
    all_beat = [fold["beat_benchmark_pct"] for fold in fold_summaries]
    all_port_cumul = [fold["cumul_portfolio_pct"] for fold in fold_summaries]
    all_bench_cumul = [fold["cumul_equal_weight_universe_excess_pct"] for fold in fold_summaries]

    overall = {
        "strategy": f"Top {top_n} by UP probability",
        "benchmark_contract": build_selection_alpha_benchmark_contract(),
        "forward_days": FORWARD_DAYS,
        "n_folds": len(fold_summaries),
        "date_range": [fold_summaries[0]["test_end"], fold_summaries[-1]["test_end"]],
        "avg_portfolio_excess_return_pct": round(
            float(np.mean([fold["avg_daily_excess_return_pct"] for fold in fold_summaries])), 4
        ),
        "avg_equal_weight_universe_excess_pct": round(
            float(np.mean([fold["avg_equal_weight_universe_excess_pct"] for fold in fold_summaries])), 4
        ),
        "avg_selection_alpha_vs_equal_weight_pct": round(float(np.mean(all_alphas)), 4),
        # Legacy aliases kept for downstream readers during contract migration.
        "avg_alpha_pct": round(float(np.mean(all_alphas)), 4),
        "median_alpha_pct": round(float(np.median(all_alphas)), 4),
        "positive_alpha_folds": sum(1 for alpha in all_alphas if alpha > 0),
        "avg_hit_rate_pct": round(float(np.mean(all_hit)), 2),
        "avg_beat_benchmark_pct": round(float(np.mean(all_beat)), 2),
        "total_portfolio_excess_return_pct": round(float(sum(all_port_cumul)), 4),
        "total_equal_weight_universe_excess_pct": round(float(sum(all_bench_cumul)), 4),
        "total_portfolio_return_pct": round(float(sum(all_port_cumul)), 4),
        "total_benchmark_return_pct": round(float(sum(all_bench_cumul)), 4),
    }
    risk_summary = build_backtest_risk_summary(
        fold_summaries,
        top_n=top_n,
        forward_days=FORWARD_DAYS,
        total_portfolio_return_pct=overall["total_portfolio_return_pct"],
        total_benchmark_return_pct=overall["total_benchmark_return_pct"],
    )
    overall.update(
        {
            "negative_alpha_folds": risk_summary["negative_alpha_folds"],
            "negative_portfolio_folds": risk_summary["negative_portfolio_folds"],
            "total_signal_days": risk_summary["total_signal_days"],
            "estimated_selection_count": risk_summary["estimated_selection_count"],
            "max_drawdown_pct": risk_summary["max_drawdown_pct"],
            "return_mdd_ratio": risk_summary["return_mdd_ratio"],
            "calmar_ratio": risk_summary["calmar_ratio"],
        }
    )

    print(f"\n{'=' * 60}")
    print(f"  Backtest Summary (Top {top_n})")
    print(f"{'=' * 60}")
    print(f"  Date range: {overall['date_range'][0]} ~ {overall['date_range'][1]}")
    print(f"  Folds: {overall['n_folds']}")
    print(f"  Avg portfolio excess: {overall['avg_portfolio_excess_return_pct']:+.4f}% / day")
    print(f"  Avg EW-universe excess: {overall['avg_equal_weight_universe_excess_pct']:+.4f}% / day")
    print(f"  Avg selection alpha: {overall['avg_selection_alpha_vs_equal_weight_pct']:+.4f}% / day")
    print(f"  Positive alpha folds: {overall['positive_alpha_folds']} / {overall['n_folds']}")
    print(f"  Avg hit rate (>0): {overall['avg_hit_rate_pct']:.1f}%")
    print(f"  Avg beat benchmark: {overall['avg_beat_benchmark_pct']:.1f}%")
    print(f"  Portfolio excess total: {overall['total_portfolio_excess_return_pct']:+.2f}%")
    print(f"  EW-universe excess total: {overall['total_equal_weight_universe_excess_pct']:+.2f}%")

    os.makedirs(REPORT_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    result_path = os.path.join(REPORT_DIR, f"backtest_{timestamp}.json")

    result = {
        "generated_at": timestamp,
        "top_n": top_n,
        "max_stocks": max_stocks,
        "overall": overall,
        "risk_summary": risk_summary,
        "fold_summaries": fold_summaries,
    }

    def _default(obj: Any) -> Any:
        if hasattr(obj, "item"):
            return obj.item()
        raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")

    with open(result_path, "w", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2, default=_default)

    detail_path = os.path.join(REPORT_DIR, f"backtest_detail_{timestamp}.csv")
    import pandas as pd  # local import to keep module import surface light

    pd.DataFrame(all_fold_records).to_csv(detail_path, index=False, encoding="utf-8-sig")

    print(f"\n  Backtest report: {result_path}")
    print(f"  Backtest detail: {detail_path}")

    latest_path = os.path.join(REPORT_DIR, "backtest_latest.json")
    with open(latest_path, "w", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2, default=_default)
    print(f"  Latest report: {latest_path}")

    return result


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Walk-forward backtest")
    parser.add_argument("--top", type=int, default=10, help="Top N positions")
    parser.add_argument("--max-stocks", type=int, default=0, help="Optional stock cap")
    cli_args = parser.parse_args()
    run_backtest(max_stocks=cli_args.max_stocks, top_n=cli_args.top)
