"""T+1 momentum training + friction-aware walk-forward backtest.

This script trains a binary LightGBM classifier on a configurable target
(default: ``t1_open_next_close_positive``) and evaluates it with practical
trading rules:

1. Model runs after day D close, using D's features.
2. User enters at D+1 open (next morning 09:00).
3. Exit at D+1 close (same day 13:30), or earlier if TP/SL hit.
4. Subtract a fixed friction cost from every round-trip trade.

Supported targets:
    t1_open_next_close_positive — binary: (Close[D+1] - Open[D]) / Open[D] > 0 (~46% rate)
                                  Training target for open-entry strategy.
                                  Backtest uses realistic open→close return.
    t1_close_positive           — binary: next close > today's close (~46% rate)
    t1_hit_3pct                 — binary: next high ≥ +3% vs today's close (~15% rate)

Usage examples:
    python scripts/train_t1_backtest.py
    python scripts/train_t1_backtest.py --target t1_hit_3pct
    python scripts/train_t1_backtest.py --top 10 --min-prob 0.70
    python scripts/train_t1_backtest.py --max-stocks 100 --device cpu
"""

from __future__ import annotations

import json
import os
import sys
import warnings
from dataclasses import asdict, dataclass
from datetime import datetime

import lightgbm as lgb
import numpy as np
import pandas as pd
from dateutil.relativedelta import relativedelta
from sklearn.metrics import log_loss, precision_score, recall_score, roc_auc_score

warnings.filterwarnings("ignore", category=UserWarning)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ml.config import MODEL_DIR, REPORT_DIR
from ml.dataset_t1 import T1_FEATURE_COLUMNS, build_t1_dataset


DEFAULT_TARGET = "t1_close_positive"
DEFAULT_TOP_N = 10
DEFAULT_MIN_PROB = 0.60
DEFAULT_TAKE_PROFIT = 0.05  # 5% take-profit
DEFAULT_STOP_LOSS = None    # None = no stop loss
DEFAULT_FRICTION = 0.004    # round-trip friction (open-entry)
DEFAULT_TRAIN_MONTHS = 24
DEFAULT_VAL_MONTHS = 3
DEFAULT_TEST_MONTHS = 1
DEFAULT_NUM_ROUNDS = 800
DEFAULT_EARLY_STOPPING = 50


@dataclass
class FoldSummary:
    fold: int
    test_month: str
    n_test_rows: int
    n_trade_days: int
    n_trades: int
    positive_rate: float
    auc: float
    logloss: float
    precision_at_50: float
    recall_at_50: float
    selected_hit_rate: float
    trade_win_rate: float
    avg_net_return: float
    avg_day_return: float
    cumulative_return: float
    stop_loss_rate: float
    take_profit_rate: float
    max_drawdown: float


def _safe_float(value: float | int | np.generic | None) -> float | None:
    if value is None or pd.isna(value):
        return None
    return float(value)


def _format_pct(value: float | None) -> str:
    if value is None or pd.isna(value):
        return "n/a"
    return f"{value * 100:+.2f}%"


def _max_drawdown(returns: pd.Series) -> float:
    if returns.empty:
        return 0.0
    curve = (1.0 + returns.fillna(0.0)).cumprod()
    peak = curve.cummax()
    drawdown = curve / peak - 1.0
    return float(drawdown.min())


def iter_t1_walk_forward_splits(
    dataset: pd.DataFrame,
    train_months: int = DEFAULT_TRAIN_MONTHS,
    val_months: int = DEFAULT_VAL_MONTHS,
    test_months: int = DEFAULT_TEST_MONTHS,
):
    """Yield rolling monthly train/val/test splits for T+1 research."""
    dates = pd.to_datetime(dataset["Date"]).sort_values().unique()
    min_date = pd.Timestamp(dates[0]).replace(day=1)
    max_date = pd.Timestamp(dates[-1]).replace(day=1)

    test_start = min_date + relativedelta(months=train_months + val_months)
    fold = 0

    while test_start <= max_date:
        test_end = test_start + relativedelta(months=test_months)
        val_start = test_start - relativedelta(months=val_months)
        train_start = val_start - relativedelta(months=train_months)

        train_mask = (dataset["Date"] >= train_start) & (dataset["Date"] < val_start)
        val_mask = (dataset["Date"] >= val_start) & (dataset["Date"] < test_start)
        test_mask = (dataset["Date"] >= test_start) & (dataset["Date"] < test_end)

        train_df = dataset.loc[train_mask].copy()
        val_df = dataset.loc[val_mask].copy()
        test_df = dataset.loc[test_mask].copy()

        if len(train_df) > 500 and len(val_df) > 100 and len(test_df) > 20:
            yield fold, train_df, val_df, test_df, test_start
            fold += 1

        test_start += relativedelta(months=test_months)


def _ensure_feature_columns(df: pd.DataFrame) -> list[str]:
    for col in T1_FEATURE_COLUMNS:
        if col not in df.columns:
            df[col] = np.nan
    return [col for col in T1_FEATURE_COLUMNS if col in df.columns]


def train_binary_fold(
    train_df: pd.DataFrame,
    val_df: pd.DataFrame,
    feature_cols: list[str],
    device: str = "gpu",
    target_col: str = DEFAULT_TARGET,
) -> tuple[lgb.Booster, dict]:
    """Train one T+1 binary classifier fold."""
    X_train = train_df[feature_cols].values
    y_train = train_df[target_col].astype(int).values
    X_val = val_df[feature_cols].values
    y_val = val_df[target_col].astype(int).values

    pos_count = max(int(y_train.sum()), 1)
    neg_count = max(len(y_train) - pos_count, 1)

    params = {
        "objective": "binary",
        "metric": "binary_logloss",
        "boosting_type": "gbdt",
        "device": device,
        "num_leaves": 63,
        "learning_rate": 0.03,
        "feature_fraction": 0.6,  # 強迫模型挖掘個股 alpha，不依賴大盤特徵
        "bagging_fraction": 0.8,
        "bagging_freq": 5,
        "min_data_in_leaf": 80,
        "lambda_l1": 0.1,
        "lambda_l2": 0.5,
        "scale_pos_weight": neg_count / pos_count,
        "verbose": -1,
        "n_jobs": -1,
        "seed": 42,
    }

    dtrain = lgb.Dataset(X_train, label=y_train, feature_name=feature_cols, free_raw_data=False)
    dval = lgb.Dataset(X_val, label=y_val, feature_name=feature_cols, free_raw_data=False)
    callbacks = [
        lgb.early_stopping(DEFAULT_EARLY_STOPPING, verbose=False),
        lgb.log_evaluation(period=0),
    ]

    try:
        model = lgb.train(
            params,
            dtrain,
            num_boost_round=DEFAULT_NUM_ROUNDS,
            valid_sets=[dval],
            callbacks=callbacks,
        )
        actual_device = device
    except lgb.basic.LightGBMError:
        if device != "gpu":
            raise
        params["device"] = "cpu"
        model = lgb.train(
            params,
            dtrain,
            num_boost_round=DEFAULT_NUM_ROUNDS,
            valid_sets=[dval],
            callbacks=callbacks,
        )
        actual_device = "cpu"

    params["device"] = actual_device
    return model, params


def simulate_trade(
    row: pd.Series,
    take_profit: float | None,
    friction: float,
    stop_loss: float | None = None,
    ambiguous_fill: str = "stop_first",
    entry_mode: str = "close",
) -> dict[str, float | str | int | None] | None:
    """Simulate one next-day trade from daily OHLC targets.

    entry_mode:
        "close" — enter at today's close, exit based on next-day OHLC (legacy)
        "open"  — enter at next-day open, exit at next-day close.
                   The user's actual trade: buy D+1 open, sell D+1 close.
                   TP/SL are measured from open, using intraday high/low vs open.
    """
    if entry_mode == "open":
        # Open-entry: user buys at next open, sells at next close
        otc_ret = _safe_float(row.get("t1_open_to_close_return"))
        if otc_ret is None:
            return None

        # For TP/SL in open-entry mode, use high/low relative to next open
        # high_from_open = (High[D+1] - Open[D+1]) / Open[D+1]
        # low_from_open  = (Low[D+1]  - Open[D+1]) / Open[D+1]
        high_from_open = _safe_float(row.get("t1_high_from_open"))
        low_from_open = _safe_float(row.get("t1_low_from_open"))

        if take_profit is None and stop_loss is None:
            # Simple hold: enter at open, exit at close
            gross_return = otc_ret
            exit_reason = "close"
            net_return = gross_return - friction
            return {
                "gross_return": gross_return,
                "net_return": net_return,
                "exit_reason": exit_reason,
                "target_hit": 0,
                "stop_loss_hit": 0,
            }

        # With TP/SL from open
        if high_from_open is None or low_from_open is None:
            gross_return = otc_ret
            exit_reason = "close"
        else:
            hit_tp = take_profit is not None and high_from_open >= take_profit
            hit_sl = stop_loss is not None and low_from_open <= -stop_loss

            if hit_tp and hit_sl:
                if ambiguous_fill == "target_first":
                    gross_return = take_profit
                    exit_reason = "ambiguous_take_profit"
                elif ambiguous_fill == "close":
                    gross_return = otc_ret
                    exit_reason = "ambiguous_close"
                else:
                    gross_return = -stop_loss
                    exit_reason = "ambiguous_stop_loss"
            elif hit_tp:
                gross_return = take_profit
                exit_reason = "take_profit"
            elif hit_sl:
                gross_return = -stop_loss
                exit_reason = "stop_loss"
            else:
                gross_return = otc_ret
                exit_reason = "close"

        net_return = gross_return - friction
        return {
            "gross_return": gross_return,
            "net_return": net_return,
            "exit_reason": exit_reason,
            "target_hit": int(take_profit is not None and high_from_open is not None and high_from_open >= take_profit),
            "stop_loss_hit": int(stop_loss is not None and low_from_open is not None and low_from_open <= -stop_loss),
        }

    # Legacy close-entry mode
    open_ret = _safe_float(row.get("t1_open_return"))
    close_ret = _safe_float(row.get("t1_close_return"))
    high_ret = _safe_float(row.get("t1_high_return"))
    low_ret = _safe_float(row.get("t1_low_return"))

    if close_ret is None or high_ret is None or low_ret is None:
        return None

    hit_take_profit = take_profit is not None and high_ret >= take_profit
    hit_stop_loss = stop_loss is not None and low_ret <= -stop_loss

    if stop_loss is not None and open_ret is not None and open_ret <= -stop_loss:
        gross_return = open_ret
        exit_reason = "gap_stop"
    elif take_profit is not None and open_ret is not None and open_ret >= take_profit:
        gross_return = take_profit
        exit_reason = "gap_take_profit"
    elif hit_take_profit and hit_stop_loss:
        if ambiguous_fill == "target_first":
            gross_return = take_profit
            exit_reason = "ambiguous_take_profit"
        elif ambiguous_fill == "close":
            gross_return = close_ret
            exit_reason = "ambiguous_close"
        else:
            gross_return = -stop_loss
            exit_reason = "ambiguous_stop_loss"
    elif hit_take_profit:
        gross_return = take_profit
        exit_reason = "take_profit"
    elif hit_stop_loss and stop_loss is not None:
        gross_return = -stop_loss
        exit_reason = "stop_loss"
    else:
        gross_return = close_ret
        exit_reason = "close"

    net_return = gross_return - friction
    return {
        "gross_return": gross_return,
        "net_return": net_return,
        "exit_reason": exit_reason,
        "target_hit": int(hit_take_profit),
        "stop_loss_hit": int(bool(hit_stop_loss)),
    }


def score_fold_predictions(
    model: lgb.Booster,
    test_df: pd.DataFrame,
    feature_cols: list[str],
    target_col: str = DEFAULT_TARGET,
) -> pd.DataFrame:
    """Attach predicted hit probabilities to one fold."""
    base_cols = [
        "ticker", "Date", "Close", "Open",
        "t1_open_return", "t1_close_return", "t1_high_return", "t1_low_return",
        "t1_open_to_close_return", "t1_high_from_open", "t1_low_from_open",
        "t1_open_next_close_return",
    ]
    if target_col not in base_cols:
        base_cols.append(target_col)
    # Also include t1_hit_3pct for trade-level hit tracking if available
    if "t1_hit_3pct" not in base_cols and "t1_hit_3pct" in test_df.columns:
        base_cols.append("t1_hit_3pct")
    scored = test_df[[c for c in base_cols if c in test_df.columns]].copy()
    scored["hit_prob"] = np.clip(model.predict(test_df[feature_cols].values), 1e-6, 1.0 - 1e-6)
    scored["pred_label"] = (scored["hit_prob"] >= 0.5).astype(np.int8)
    return scored


def evaluate_scored_fold(
    scored: pd.DataFrame,
    top_n: int,
    min_prob: float,
    take_profit: float | None,
    stop_loss: float | None,
    friction: float,
    ambiguous_fill: str,
    target_col: str = DEFAULT_TARGET,
    entry_mode: str | None = None,
) -> tuple[FoldSummary, list[dict], list[dict]]:
    """Replay one scored fold under a given trading rule set."""
    if entry_mode is None:
        entry_mode = "open" if ("open_next_close" in target_col or "open_to_close" in target_col) else "close"
    y_true = scored[target_col].astype(int).values
    y_prob = scored["hit_prob"].values
    y_pred = scored["pred_label"].values

    auc = np.nan
    try:
        if np.unique(y_true).size > 1:
            auc = float(roc_auc_score(y_true, y_prob))
    except ValueError:
        pass

    try:
        ll = float(log_loss(y_true, y_prob, labels=[0, 1]))
    except ValueError:
        ll = np.nan

    precision = float(precision_score(y_true, y_pred, zero_division=0))
    recall = float(recall_score(y_true, y_pred, zero_division=0))

    trade_records: list[dict] = []
    daily_records: list[dict] = []

    for dt, day_df in scored.groupby("Date", sort=True):
        candidates = day_df.sort_values("hit_prob", ascending=False)
        if min_prob > 0:
            candidates = candidates[candidates["hit_prob"] >= min_prob]
        if top_n > 0:
            candidates = candidates.head(top_n)
        if candidates.empty:
            continue

        day_trade_returns = []
        day_gross_returns = []
        day_hit_flags = []
        day_stop_flags = []

        for _, row in candidates.iterrows():
            trade = simulate_trade(
                row=row,
                take_profit=take_profit,
                friction=friction,
                stop_loss=stop_loss,
                ambiguous_fill=ambiguous_fill,
                entry_mode=entry_mode,
            )
            if trade is None:
                continue

            day_trade_returns.append(trade["net_return"])
            day_gross_returns.append(trade["gross_return"])
            day_hit_flags.append(trade["target_hit"])
            day_stop_flags.append(trade["stop_loss_hit"])

            trade_records.append(
                {
                    "date": str(pd.Timestamp(dt).date()),
                    "ticker": row["ticker"],
                    "close": float(row["Close"]),
                    "hit_prob": float(row["hit_prob"]),
                    "target_label": int(row[target_col]),
                    "t1_open_return": _safe_float(row["t1_open_return"]),
                    "t1_high_return": _safe_float(row["t1_high_return"]),
                    "t1_low_return": _safe_float(row["t1_low_return"]),
                    "t1_close_return": _safe_float(row["t1_close_return"]),
                    **trade,
                }
            )

        if not day_trade_returns:
            continue

        daily_records.append(
            {
                "date": str(pd.Timestamp(dt).date()),
                "n_trades": len(day_trade_returns),
                "avg_hit_prob": float(candidates["hit_prob"].mean()),
                "selected_hit_rate": float(np.mean(day_hit_flags)),
                "stop_loss_rate": float(np.mean(day_stop_flags)),
                "portfolio_gross_return": float(np.mean(day_gross_returns)),
                "portfolio_net_return": float(np.mean(day_trade_returns)),
            }
        )

    trade_df = pd.DataFrame(trade_records)
    daily_df = pd.DataFrame(daily_records)

    if trade_df.empty:
        summary = FoldSummary(
            fold=-1,
            test_month="",
            n_test_rows=len(scored),
            n_trade_days=0,
            n_trades=0,
            positive_rate=float(np.mean(y_true)),
            auc=_safe_float(auc) or np.nan,
            logloss=_safe_float(ll) or np.nan,
            precision_at_50=precision,
            recall_at_50=recall,
            selected_hit_rate=0.0,
            trade_win_rate=0.0,
            avg_net_return=0.0,
            avg_day_return=0.0,
            cumulative_return=0.0,
            stop_loss_rate=0.0,
            take_profit_rate=0.0,
            max_drawdown=0.0,
        )
        return summary, trade_records, daily_records

    take_profit_rate = float((trade_df["exit_reason"].str.contains("take_profit")).mean())
    summary = FoldSummary(
        fold=-1,
        test_month="",
        n_test_rows=len(scored),
        n_trade_days=int(len(daily_df)),
        n_trades=int(len(trade_df)),
        positive_rate=float(np.mean(y_true)),
        auc=_safe_float(auc) or np.nan,
        logloss=_safe_float(ll) or np.nan,
        precision_at_50=precision,
        recall_at_50=recall,
        selected_hit_rate=float(trade_df["target_label"].mean()),
        trade_win_rate=float((trade_df["net_return"] > 0).mean()),
        avg_net_return=float(trade_df["net_return"].mean()),
        avg_day_return=float(daily_df["portfolio_net_return"].mean()) if not daily_df.empty else 0.0,
        cumulative_return=float((1.0 + daily_df["portfolio_net_return"]).prod() - 1.0) if not daily_df.empty else 0.0,
        stop_loss_rate=float(trade_df["stop_loss_hit"].mean()),
        take_profit_rate=take_profit_rate,
        max_drawdown=_max_drawdown(daily_df["portfolio_net_return"]) if not daily_df.empty else 0.0,
    )
    return summary, trade_records, daily_records


def evaluate_fold(
    model: lgb.Booster,
    test_df: pd.DataFrame,
    feature_cols: list[str],
    top_n: int,
    min_prob: float,
    take_profit: float | None,
    stop_loss: float | None,
    friction: float,
    ambiguous_fill: str,
    target_col: str = DEFAULT_TARGET,
) -> tuple[FoldSummary, list[dict], list[dict]]:
    """Score a test fold and simulate T+1 trades."""
    scored = score_fold_predictions(model=model, test_df=test_df, feature_cols=feature_cols, target_col=target_col)
    return evaluate_scored_fold(
        scored=scored,
        top_n=top_n,
        min_prob=min_prob,
        take_profit=take_profit,
        stop_loss=stop_loss,
        friction=friction,
        ambiguous_fill=ambiguous_fill,
        target_col=target_col,
    )


def summarize_backtest_overall(
    fold_summaries: list[FoldSummary],
    trade_records: list[dict],
    daily_records: list[dict],
) -> dict:
    """Aggregate overall backtest performance for one rule set."""
    fold_df = pd.DataFrame([asdict(item) for item in fold_summaries])
    trade_df = pd.DataFrame(trade_records)
    daily_df = pd.DataFrame(daily_records)

    global_avg_expectancy = float(trade_df["net_return"].mean()) if not trade_df.empty else 0.0
    global_trade_win_rate = float((trade_df["net_return"] > 0).mean()) if not trade_df.empty else 0.0
    global_selected_hit_rate = float(trade_df["target_label"].mean()) if not trade_df.empty else 0.0
    global_take_profit_rate = (
        float(trade_df["exit_reason"].str.contains("take_profit").mean()) if not trade_df.empty else 0.0
    )
    global_stop_loss_rate = float(trade_df["stop_loss_hit"].mean()) if not trade_df.empty else 0.0

    return {
        "n_folds": int(len(fold_df)),
        "avg_auc": _safe_float(fold_df["auc"].mean()),
        "avg_logloss": _safe_float(fold_df["logloss"].mean()),
        "avg_precision_at_50": _safe_float(fold_df["precision_at_50"].mean()),
        "avg_recall_at_50": _safe_float(fold_df["recall_at_50"].mean()),
        "avg_selected_hit_rate": _safe_float(global_selected_hit_rate),
        "avg_trade_win_rate": _safe_float(global_trade_win_rate),
        "avg_expectancy": _safe_float(global_avg_expectancy),
        "avg_day_return": _safe_float(fold_df["avg_day_return"].mean()),
        "avg_take_profit_rate": _safe_float(global_take_profit_rate),
        "avg_stop_loss_rate": _safe_float(global_stop_loss_rate),
        "avg_max_drawdown": _safe_float(fold_df["max_drawdown"].mean()),
        "total_trades": int(len(trade_df)),
        "total_trade_days": int(len(daily_df)),
        "cumulative_portfolio_return": _safe_float(
            (1.0 + daily_df["portfolio_net_return"]).prod() - 1.0 if not daily_df.empty else 0.0
        ),
        "positive_expectancy_folds": int((fold_df["avg_net_return"] > 0).sum()) if not fold_df.empty else 0,
        "positive_day_rate": _safe_float(
            (daily_df["portfolio_net_return"] > 0).mean() if not daily_df.empty else 0.0
        ),
    }


def _save_reports(
    timestamp: str,
    result: dict,
    trade_records: list[dict],
    daily_records: list[dict],
) -> tuple[str, str, str]:
    os.makedirs(REPORT_DIR, exist_ok=True)

    result_path = os.path.join(REPORT_DIR, f"t1_backtest_{timestamp}.json")
    with open(result_path, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2)

    trade_path = os.path.join(REPORT_DIR, f"t1_backtest_trades_{timestamp}.csv")
    pd.DataFrame(trade_records).to_csv(trade_path, index=False, encoding="utf-8-sig")

    daily_path = os.path.join(REPORT_DIR, f"t1_backtest_daily_{timestamp}.csv")
    pd.DataFrame(daily_records).to_csv(daily_path, index=False, encoding="utf-8-sig")

    latest_path = os.path.join(REPORT_DIR, "t1_backtest_latest.json")
    with open(latest_path, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2)

    return result_path, trade_path, daily_path


def _train_final_model(
    dataset: pd.DataFrame,
    feature_cols: list[str],
    val_months: int,
    device: str,
    save_model: bool,
    backtest_meta: dict,
    target_col: str = DEFAULT_TARGET,
) -> tuple[str | None, str | None, str | None]:
    if not save_model:
        return None, None, None

    cutoff = pd.Timestamp(dataset["Date"].max()).replace(day=1) - relativedelta(months=val_months)
    train_df = dataset[dataset["Date"] < cutoff].copy()
    val_df = dataset[dataset["Date"] >= cutoff].copy()
    if train_df.empty or val_df.empty:
        train_df = dataset.iloc[:- max(1, len(dataset) // 10)].copy()
        val_df = dataset.iloc[-max(1, len(dataset) // 10):].copy()

    model, params = train_binary_fold(train_df, val_df, feature_cols, device=device, target_col=target_col)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    os.makedirs(MODEL_DIR, exist_ok=True)
    os.makedirs(REPORT_DIR, exist_ok=True)

    model_path = os.path.join(MODEL_DIR, f"lgbm_t1_{timestamp}.txt")
    model.save_model(model_path)

    feat_imp = pd.DataFrame(
        {
            "feature": feature_cols,
            "importance_gain": model.feature_importance(importance_type="gain"),
        }
    ).sort_values("importance_gain", ascending=False)
    feature_path = os.path.join(REPORT_DIR, f"feature_importance_t1_{timestamp}.csv")
    feat_imp.to_csv(feature_path, index=False, encoding="utf-8-sig")

    meta = {
        "model_file": model_path,
        "model_version": f"t1_binary_{target_col}",
        "trained_at": timestamp,
        "device": params["device"],
        "feature_columns": feature_cols,
        "n_features": len(feature_cols),
        "n_stocks": int(dataset["ticker"].nunique()),
        "n_samples": int(len(dataset)),
        "date_range": [
            str(pd.Timestamp(dataset["Date"].min()).date()),
            str(pd.Timestamp(dataset["Date"].max()).date()),
        ],
        "target": target_col,
        "trade_rules": backtest_meta["trade_rules"],
        "walk_forward": backtest_meta["walk_forward"],
        "backtest_summary": backtest_meta["overall"],
    }
    meta_path = os.path.join(MODEL_DIR, f"lgbm_t1_{timestamp}_meta.json")
    with open(meta_path, "w", encoding="utf-8") as fh:
        json.dump(meta, fh, ensure_ascii=False, indent=2)

    return model_path, meta_path, feature_path


def run_t1_backtest(
    max_stocks: int = 0,
    target: str = DEFAULT_TARGET,
    top_n: int = DEFAULT_TOP_N,
    min_prob: float = DEFAULT_MIN_PROB,
    take_profit: float | None = DEFAULT_TAKE_PROFIT,
    stop_loss: float | None = DEFAULT_STOP_LOSS,
    friction: float = DEFAULT_FRICTION,
    ambiguous_fill: str = "close",
    train_months: int = DEFAULT_TRAIN_MONTHS,
    val_months: int = DEFAULT_VAL_MONTHS,
    test_months: int = DEFAULT_TEST_MONTHS,
    device: str = "gpu",
    save_final_model: bool = True,
    save_reports: bool = True,
):
    """Train and backtest the T+1 binary classifier."""
    print("=" * 78)
    print(f"  T+1 Momentum Backtest (target: {target})")
    print("=" * 78)
    print(
        f"  Rules: top={top_n}, min_prob={min_prob:.2f}, "
        f"take_profit={'off' if take_profit is None else f'{take_profit:.2%}'}, "
        f"stop_loss={'off' if stop_loss is None else f'{stop_loss:.2%}'}, "
        f"friction={friction:.2%}, ambiguous={ambiguous_fill}"
    )

    dataset = build_t1_dataset(max_stocks=max_stocks, verbose=True)
    dataset["Date"] = pd.to_datetime(dataset["Date"])
    dataset = dataset.sort_values(["Date", "ticker"]).reset_index(drop=True)

    feature_cols = _ensure_feature_columns(dataset)
    print(f"  Features: {len(feature_cols)}")
    print(f"  Target: {target}, positive rate: {dataset[target].mean() * 100:.2f}%")

    fold_summaries: list[FoldSummary] = []
    all_trade_records: list[dict] = []
    all_daily_records: list[dict] = []
    actual_device = device

    for fold_idx, train_df, val_df, test_df, test_start in iter_t1_walk_forward_splits(
        dataset,
        train_months=train_months,
        val_months=val_months,
        test_months=test_months,
    ):
        if train_df[target].nunique() < 2 or val_df[target].nunique() < 2:
            print(f"  Fold {fold_idx:02d} {pd.Timestamp(test_start).date()}: skipped (single-class train/val)")
            continue

        model, params = train_binary_fold(train_df, val_df, feature_cols, device=actual_device, target_col=target)
        actual_device = params["device"]

        summary, trade_records, daily_records = evaluate_fold(
            model=model,
            test_df=test_df,
            feature_cols=feature_cols,
            top_n=top_n,
            min_prob=min_prob,
            take_profit=take_profit,
            stop_loss=stop_loss,
            friction=friction,
            ambiguous_fill=ambiguous_fill,
            target_col=target,
        )
        summary.fold = fold_idx
        summary.test_month = str(pd.Timestamp(test_start).date())
        fold_summaries.append(summary)

        for rec in trade_records:
            rec["fold"] = fold_idx
            rec["test_month"] = summary.test_month
        for rec in daily_records:
            rec["fold"] = fold_idx
            rec["test_month"] = summary.test_month

        all_trade_records.extend(trade_records)
        all_daily_records.extend(daily_records)

        print(
            f"  Fold {fold_idx:02d} {summary.test_month}: "
            f"AUC={summary.auc if not pd.isna(summary.auc) else float('nan'):.3f} "
            f"Trades={summary.n_trades:>4d} "
            f"Expectancy={_format_pct(summary.avg_net_return)} "
            f"Win={summary.trade_win_rate * 100:5.1f}% "
            f"Cum={_format_pct(summary.cumulative_return)}"
        )

    if not fold_summaries:
        raise ValueError("No valid walk-forward folds were generated for T+1 backtest.")

    overall = summarize_backtest_overall(
        fold_summaries=fold_summaries,
        trade_records=all_trade_records,
        daily_records=all_daily_records,
    )

    result = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "dataset": {
            "n_rows": int(len(dataset)),
            "n_stocks": int(dataset["ticker"].nunique()),
            "date_range": [
                str(pd.Timestamp(dataset["Date"].min()).date()),
                str(pd.Timestamp(dataset["Date"].max()).date()),
            ],
            "positive_rate": float(dataset[target].mean()),
            "target": target,
        },
        "walk_forward": {
            "train_months": train_months,
            "val_months": val_months,
            "test_months": test_months,
            "device": actual_device,
        },
        "trade_rules": {
            "entry": "buy_on_close",
            "selection": {
                "top_n": top_n,
                "min_prob": min_prob,
            },
            "take_profit": take_profit,
            "stop_loss": stop_loss,
            "friction": friction,
            "ambiguous_fill": ambiguous_fill,
        },
        "overall": overall,
        "fold_summaries": [asdict(item) for item in fold_summaries],
    }

    result_path = None
    trade_path = None
    daily_path = None
    if save_reports:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        result_path, trade_path, daily_path = _save_reports(
            timestamp=timestamp,
            result=result,
            trade_records=all_trade_records,
            daily_records=all_daily_records,
        )
    model_path, meta_path, feature_path = _train_final_model(
        dataset=dataset,
        feature_cols=feature_cols,
        val_months=val_months,
        device=actual_device,
        save_model=save_final_model,
        backtest_meta=result,
        target_col=target,
    )

    print("\n" + "=" * 78)
    print("  T+1 Backtest Summary")
    print("=" * 78)
    print(f"  Folds: {overall['n_folds']}")
    print(f"  Avg AUC: {overall['avg_auc']:.4f}" if overall["avg_auc"] is not None else "  Avg AUC: n/a")
    print(f"  Avg expectancy: {_format_pct(overall['avg_expectancy'])}")
    print(f"  Avg day return: {_format_pct(overall['avg_day_return'])}")
    print(f"  Cumulative return: {_format_pct(overall['cumulative_portfolio_return'])}")
    print(f"  Trade win rate: {overall['avg_trade_win_rate'] * 100:.1f}%")
    print(f"  Selected hit rate: {overall['avg_selected_hit_rate'] * 100:.1f}%")
    print(f"  Positive expectancy folds: {overall['positive_expectancy_folds']} / {overall['n_folds']}")
    if result_path:
        print(f"  Reports: {result_path}")
    if trade_path:
        print(f"  Trade detail: {trade_path}")
    if daily_path:
        print(f"  Daily detail: {daily_path}")
    if model_path:
        print(f"  Model: {model_path}")
    if meta_path:
        print(f"  Meta: {meta_path}")
    if feature_path:
        print(f"  Importance: {feature_path}")

    return result


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Train and backtest the T+1 momentum classifier.")
    parser.add_argument("--max-stocks", type=int, default=0, help="Limit the universe size for quick tests.")
    parser.add_argument(
        "--target",
        default=DEFAULT_TARGET,
        choices=["t1_open_to_close_positive", "t1_open_next_close_positive", "t1_close_positive", "t1_hit_3pct"],
        help="Binary target column for the classifier.",
    )
    parser.add_argument("--top", type=int, default=DEFAULT_TOP_N, help="Top N names to trade each day.")
    parser.add_argument("--min-prob", type=float, default=DEFAULT_MIN_PROB, help="Minimum hit probability to enter.")
    parser.add_argument("--take-profit", type=float, default=None, help="Take-profit threshold. Omit for hold-to-close.")
    parser.add_argument(
        "--stop-loss",
        type=float,
        default=None,
        help="Stop-loss threshold. Omit or set to 0 to disable.",
    )
    parser.add_argument("--friction", type=float, default=DEFAULT_FRICTION, help="Round-trip friction cost.")
    parser.add_argument(
        "--ambiguous-fill",
        choices=["stop_first", "target_first", "close"],
        default="close",
        help="How to resolve same-day take-profit and stop-loss hits with only daily OHLC.",
    )
    parser.add_argument("--train-months", type=int, default=DEFAULT_TRAIN_MONTHS, help="Rolling train window.")
    parser.add_argument("--val-months", type=int, default=DEFAULT_VAL_MONTHS, help="Validation window.")
    parser.add_argument("--test-months", type=int, default=DEFAULT_TEST_MONTHS, help="Test window.")
    parser.add_argument("--device", choices=["gpu", "cpu"], default="gpu", help="Preferred LightGBM device.")
    parser.add_argument("--skip-final-model", action="store_true", help="Skip saving the final production model.")
    parser.add_argument("--skip-reports", action="store_true", help="Skip writing JSON/CSV backtest reports.")
    args = parser.parse_args()

    take_profit = args.take_profit if args.take_profit and args.take_profit > 0 else None
    stop_loss = args.stop_loss if args.stop_loss and args.stop_loss > 0 else None
    run_t1_backtest(
        max_stocks=args.max_stocks,
        target=args.target,
        top_n=args.top,
        min_prob=args.min_prob,
        take_profit=take_profit,
        stop_loss=stop_loss,
        friction=args.friction,
        ambiguous_fill=args.ambiguous_fill,
        train_months=args.train_months,
        val_months=args.val_months,
        test_months=args.test_months,
        device=args.device,
        save_final_model=not args.skip_final_model,
        save_reports=not args.skip_reports,
    )
