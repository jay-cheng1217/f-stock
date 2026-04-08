"""Parameter sweep for T+1 trading rules.

This script reuses one set of walk-forward model scores and replays many
trading-rule combinations on top of those scores. It is meant to answer:

- Which take-profit / stop-loss pair has the best expectancy?
- How selective should the probability threshold be?
- Should we concentrate into Top 1-3 names or spread across 10?
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from itertools import product

import pandas as pd
from tqdm import tqdm

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ml.config import REPORT_DIR
from ml.dataset_t1 import build_t1_dataset
from scripts.train_t1_backtest import (
    CLI_TARGET_CHOICES,
    _ensure_feature_columns,
    evaluate_scored_fold,
    iter_t1_walk_forward_splits,
    resolve_target_name,
    score_fold_predictions,
    summarize_backtest_overall,
    train_binary_fold,
)


DEFAULT_TAKE_PROFITS = [None, 0.03, 0.05]
DEFAULT_STOP_LOSSES = [None, 0.02, 0.03]
DEFAULT_MIN_PROBS = [0.50, 0.55, 0.60, 0.65, 0.70]
DEFAULT_TOP_NS = [1, 3, 5, 10, 20]
DEFAULT_AMBIGUOUS_FILLS = ["close"]


def _parse_float_grid(text: str, allow_none: bool = False) -> list[float | None]:
    values: list[float | None] = []
    for token in text.split(","):
        item = token.strip().lower()
        if not item:
            continue
        if item in {"none", "null", "off"}:
            if not allow_none:
                raise ValueError(f"Grid does not allow None: {token}")
            values.append(None)
            continue
        values.append(float(item))
    return values


def _parse_int_grid(text: str) -> list[int]:
    return [int(token.strip()) for token in text.split(",") if token.strip()]


def _format_rule_label(stop_loss: float | None) -> str:
    return "none" if stop_loss is None else f"{stop_loss:.3f}"


def prepare_scored_folds(
    max_stocks: int,
    train_months: int,
    val_months: int,
    test_months: int,
    device: str,
    target: str = "t1_excess_positive",
) -> tuple[pd.DataFrame, list[str], list[dict], str]:
    """Train one walk-forward score cache for later rule replays."""
    target = resolve_target_name(target)
    dataset = build_t1_dataset(max_stocks=max_stocks, verbose=True)
    dataset["Date"] = pd.to_datetime(dataset["Date"])
    dataset = dataset.sort_values(["Date", "ticker"]).reset_index(drop=True)
    feature_cols = _ensure_feature_columns(dataset)

    scored_folds: list[dict] = []
    actual_device = device
    iterator = iter_t1_walk_forward_splits(
        dataset,
        train_months=train_months,
        val_months=val_months,
        test_months=test_months,
    )

    for fold_idx, train_df, val_df, test_df, test_start in tqdm(
        list(iterator),
        desc="Train T+1 folds",
    ):
        if train_df[target].nunique() < 2 or val_df[target].nunique() < 2:
            continue

        model, params = train_binary_fold(train_df, val_df, feature_cols, device=actual_device, target_col=target)
        actual_device = params["device"]
        scored = score_fold_predictions(model=model, test_df=test_df, feature_cols=feature_cols, target_col=target)

        scored_folds.append(
            {
                "fold": fold_idx,
                "test_month": str(pd.Timestamp(test_start).date()),
                "scored": scored,
            }
        )

    if not scored_folds:
        raise ValueError("No valid T+1 folds were available for parameter sweep.")

    return dataset, feature_cols, scored_folds, actual_device


def run_t1_param_sweep(
    max_stocks: int = 0,
    target: str = "t1_excess_positive",
    take_profits: list[float] | None = None,
    stop_losses: list[float | None] | None = None,
    min_probs: list[float] | None = None,
    top_ns: list[int] | None = None,
    ambiguous_fills: list[str] | None = None,
    friction: float = 0.006,
    ambiguous_fill: str = "stop_first",
    train_months: int = 24,
    val_months: int = 3,
    test_months: int = 1,
    device: str = "gpu",
    top_results: int = 15,
) -> pd.DataFrame:
    """Sweep trading-rule parameters on top of one scored fold cache."""
    target = resolve_target_name(target)
    take_profits = take_profits or DEFAULT_TAKE_PROFITS
    stop_losses = stop_losses or DEFAULT_STOP_LOSSES
    min_probs = min_probs or DEFAULT_MIN_PROBS
    top_ns = top_ns or DEFAULT_TOP_NS
    fills = ambiguous_fills or [ambiguous_fill]

    total = len(take_profits) * len(stop_losses) * len(min_probs) * len(top_ns) * len(fills)
    print("=" * 86)
    print("  T+1 Trading Rule Sweep")
    print("=" * 86)
    print(
        f"  Grid size: {len(take_profits)} TP x {len(stop_losses)} SL x "
        f"{len(min_probs)} prob x {len(top_ns)} topN x {len(fills)} fill = "
        f"{total} combos"
    )

    dataset, feature_cols, scored_folds, actual_device = prepare_scored_folds(
        max_stocks=max_stocks,
        train_months=train_months,
        val_months=val_months,
        test_months=test_months,
        device=device,
        target=target,
    )

    results = []
    grid = list(product(take_profits, stop_losses, min_probs, top_ns, fills))
    for take_profit, stop_loss, min_prob, top_n, fill_mode in tqdm(grid, desc="Replay rule grid"):
        fold_summaries = []
        trade_records = []
        daily_records = []

        for fold_info in scored_folds:
            summary, trades, days = evaluate_scored_fold(
                scored=fold_info["scored"],
                top_n=top_n,
                min_prob=min_prob,
                take_profit=take_profit,
                stop_loss=stop_loss,
                friction=friction,
                ambiguous_fill=fill_mode,
                target_col=target,
            )
            summary.fold = fold_info["fold"]
            summary.test_month = fold_info["test_month"]
            fold_summaries.append(summary)
            trade_records.extend(trades)
            daily_records.extend(days)

        overall = summarize_backtest_overall(
            fold_summaries=fold_summaries,
            trade_records=trade_records,
            daily_records=daily_records,
        )
        fold_df = pd.DataFrame([summary.__dict__ for summary in fold_summaries])

        results.append(
            {
                "take_profit": take_profit,
                "take_profit_label": _format_rule_label(take_profit),
                "stop_loss": stop_loss,
                "stop_loss_label": _format_rule_label(stop_loss),
                "min_prob": min_prob,
                "top_n": top_n,
                "ambiguous_fill": fill_mode,
                "avg_expectancy": overall["avg_expectancy"],
                "avg_day_return": overall["avg_day_return"],
                "cumulative_portfolio_return": overall["cumulative_portfolio_return"],
                "avg_trade_win_rate": overall["avg_trade_win_rate"],
                "avg_selected_hit_rate": overall["avg_selected_hit_rate"],
                "avg_take_profit_rate": overall["avg_take_profit_rate"],
                "avg_stop_loss_rate": overall["avg_stop_loss_rate"],
                "avg_max_drawdown": overall["avg_max_drawdown"],
                "avg_auc": overall["avg_auc"],
                "avg_logloss": overall["avg_logloss"],
                "total_trades": overall["total_trades"],
                "total_trade_days": overall["total_trade_days"],
                "positive_expectancy_folds": overall["positive_expectancy_folds"],
                "positive_day_rate": overall["positive_day_rate"],
                "median_fold_expectancy": float(fold_df["avg_net_return"].median()) if not fold_df.empty else 0.0,
                "std_fold_expectancy": float(fold_df["avg_net_return"].std(ddof=0)) if not fold_df.empty else 0.0,
                "worst_fold_expectancy": float(fold_df["avg_net_return"].min()) if not fold_df.empty else 0.0,
                "best_fold_expectancy": float(fold_df["avg_net_return"].max()) if not fold_df.empty else 0.0,
            }
        )

    results_df = pd.DataFrame(results)
    results_df = results_df.sort_values(
        by=["avg_expectancy", "avg_max_drawdown", "positive_expectancy_folds", "avg_trade_win_rate"],
        ascending=[False, False, False, False],
    ).reset_index(drop=True)

    os.makedirs(REPORT_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = os.path.join(REPORT_DIR, f"t1_sweep_results_{timestamp}.csv")
    latest_csv_path = os.path.join(REPORT_DIR, "t1_sweep_results_latest.csv")
    meta_path = os.path.join(REPORT_DIR, f"t1_sweep_meta_{timestamp}.json")

    results_df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    results_df.to_csv(latest_csv_path, index=False, encoding="utf-8-sig")

    meta = {
        "generated_at": timestamp,
        "dataset": {
            "n_rows": int(len(dataset)),
            "n_stocks": int(dataset["ticker"].nunique()),
            "date_range": [
                str(pd.Timestamp(dataset["Date"].min()).date()),
                str(pd.Timestamp(dataset["Date"].max()).date()),
            ],
            "positive_rate": float(dataset[target].mean()),
            "target": target,
            "feature_count": len(feature_cols),
        },
        "walk_forward": {
            "train_months": train_months,
            "val_months": val_months,
            "test_months": test_months,
            "device": actual_device,
            "n_folds": len(scored_folds),
        },
        "grid": {
            "take_profits": take_profits,
            "stop_losses": stop_losses,
            "min_probs": min_probs,
            "top_ns": top_ns,
            "friction": friction,
            "ambiguous_fills": fills,
        },
        "result_files": {
            "csv": csv_path,
            "latest_csv": latest_csv_path,
        },
    }
    with open(meta_path, "w", encoding="utf-8") as fh:
        json.dump(meta, fh, ensure_ascii=False, indent=2)

    print("\n" + "=" * 86)
    print("  Top Parameter Sets")
    print("=" * 86)
    preview_cols = [
        "take_profit",
        "stop_loss_label",
        "min_prob",
        "top_n",
        "ambiguous_fill",
        "avg_expectancy",
        "avg_day_return",
        "cumulative_portfolio_return",
        "avg_max_drawdown",
        "total_trades",
        "positive_expectancy_folds",
    ]
    print(results_df[preview_cols].head(top_results).to_string(index=False))
    print(f"\n  Sweep CSV: {csv_path}")
    print(f"  Latest CSV: {latest_csv_path}")
    print(f"  Meta JSON: {meta_path}")

    return results_df


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Sweep T+1 trading-rule parameters.")
    parser.add_argument("--max-stocks", type=int, default=0, help="Limit the universe size for quick tests.")
    parser.add_argument(
        "--target",
        default="t1_excess_positive",
        choices=CLI_TARGET_CHOICES,
        help="Binary target column for the classifier.",
    )
    parser.add_argument(
        "--take-profits",
        default="0.02,0.03,0.04,0.05",
        help="Comma-separated take-profit grid.",
    )
    parser.add_argument(
        "--stop-losses",
        default="0.015,0.02,0.03,None",
        help="Comma-separated stop-loss grid. Use None to disable.",
    )
    parser.add_argument(
        "--min-probs",
        default="0.55,0.60,0.65,0.70",
        help="Comma-separated minimum hit-probability grid.",
    )
    parser.add_argument(
        "--top-ns",
        default="1,3,5,10",
        help="Comma-separated Top N grid.",
    )
    parser.add_argument("--friction", type=float, default=0.006, help="Round-trip friction cost.")
    parser.add_argument(
        "--ambiguous-fill",
        choices=["stop_first", "target_first", "close"],
        default="stop_first",
        help="How to resolve same-day take-profit and stop-loss hits with only daily OHLC.",
    )
    parser.add_argument("--train-months", type=int, default=24, help="Rolling train window.")
    parser.add_argument("--val-months", type=int, default=3, help="Validation window.")
    parser.add_argument("--test-months", type=int, default=1, help="Test window.")
    parser.add_argument("--device", choices=["gpu", "cpu"], default="gpu", help="Preferred LightGBM device.")
    parser.add_argument("--top-results", type=int, default=15, help="How many top rows to print.")
    args = parser.parse_args()

    run_t1_param_sweep(
        max_stocks=args.max_stocks,
        target=args.target,
        take_profits=[float(v) for v in _parse_float_grid(args.take_profits)],
        stop_losses=_parse_float_grid(args.stop_losses, allow_none=True),
        min_probs=[float(v) for v in _parse_float_grid(args.min_probs)],
        top_ns=_parse_int_grid(args.top_ns),
        friction=args.friction,
        ambiguous_fill=args.ambiguous_fill,
        train_months=args.train_months,
        val_months=args.val_months,
        test_months=args.test_months,
        device=args.device,
        top_results=args.top_results,
    )
