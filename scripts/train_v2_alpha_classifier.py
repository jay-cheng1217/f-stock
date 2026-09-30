"""Train the V2 20D alpha classifier with execution-aware targets.

Goal:
    Predict whether a 20D candidate can still beat TWII after accounting for
    execution friction and next-open gap risk.

Research defaults:
    - target_mode=execution_adjusted
    - target_buffer=0.005
    - gap_penalty_multiplier=1.0
    - positive-label depoisoning by severe foreign selling and weak quality

This model remains research-only. Its artifact name intentionally does not
match lgbm_v2_* so the production regression loader cannot pick it up by
accident.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import warnings
from datetime import datetime
from pathlib import Path
from typing import Any

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, roc_auc_score

warnings.filterwarnings("ignore")

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))

from ml.config import INDEX_DIR, LGBM_NUM_THREADS, MODEL_DIR, REPORT_DIR
from ml.dataset import _apply_cross_sectional_zscore, _winsorize_features
from ml.features.registry import get_feature_columns

DEFAULT_ALPHA_GATE_THRESHOLD = 0.52
DEFAULT_TARGET_BUFFER = 0.005
DEFAULT_GAP_PENALTY_MULTIPLIER = 1.0
DEFAULT_FOREIGN_SELLING_FLOOR = 0.10
DEFAULT_MIN_ROA = 0.0
DEFAULT_MIN_OPERATING_MARGIN = 0.0

MAX_PRICE_VS_MA60 = 0.40
MAX_BETA_60 = 1.80
MIN_AVG_20D_VOLUME = 500.0
MIN_AVG_20D_AMOUNT = 20_000_000.0
VOLUME_SHARES_PER_LOT = 1000.0
BETA_LOOKBACK_DAYS = 60
BETA_MIN_OBSERVATIONS = 30
TRAIN_MONTHS = 24
EMBARGO_TRADING_DAYS = 20
NUM_ROUNDS = 500
EARLY_STOPPING = 50
THRESHOLDS = [0.50, 0.52, 0.55, 0.60]
DISPOSITION_PERIODS_PATH = BASE_DIR / "ml" / "data" / "disposition_periods.csv"

PARAMS = {
    "objective": "binary",
    "metric": ["binary_logloss", "auc"],
    "boosting_type": "gbdt",
    "num_leaves": 31,
    "learning_rate": 0.03,
    "feature_fraction": 0.7,
    "bagging_fraction": 0.8,
    "bagging_freq": 5,
    "lambda_l1": 0.1,
    "lambda_l2": 1.0,
    "min_child_samples": 80,
    "verbose": -1,
    "n_jobs": LGBM_NUM_THREADS,
    "seed": 42,
}


def _latest_dataset_cache() -> Path:
    candidates = sorted(Path(MODEL_DIR).glob("dataset_cache_*.parquet"))
    if not candidates:
        raise FileNotFoundError("No ml/models/dataset_cache_*.parquet found")
    return candidates[-1]


def _load_dataset(dataset_path: str | None, max_rows: int = 0) -> pd.DataFrame:
    path = Path(dataset_path) if dataset_path else _latest_dataset_cache()
    if not path.exists():
        raise FileNotFoundError(f"Dataset cache not found: {path}")
    df = pd.read_parquet(path)
    if max_rows > 0 and len(df) > max_rows:
        df = df.sort_values(["Date", "ticker"]).tail(max_rows).copy()
    return df


def _load_twii_returns() -> pd.DataFrame:
    index_path = Path(INDEX_DIR) / "index_TWII.csv"
    twii = pd.read_csv(index_path, usecols=["Date", "Close"])
    twii["Date"] = pd.to_datetime(twii["Date"], errors="coerce")
    twii["Close"] = pd.to_numeric(twii["Close"], errors="coerce")
    twii = twii.dropna(subset=["Date", "Close"]).sort_values("Date")
    twii["twii_return_1d"] = twii["Close"].pct_change()
    return twii[["Date", "twii_return_1d"]].dropna()


def _attach_beta_60(df: pd.DataFrame) -> pd.DataFrame:
    out = df.sort_values(["ticker", "Date"]).copy()
    twii = _load_twii_returns()
    out = out.merge(twii, on="Date", how="left")
    out["_stock_return_1d"] = out.groupby("ticker")["Close"].pct_change()
    beta = pd.Series(np.nan, index=out.index, dtype="float32")

    for _, group in out.groupby("ticker", sort=False):
        stock_ret = pd.to_numeric(group["_stock_return_1d"], errors="coerce")
        market_ret = pd.to_numeric(group["twii_return_1d"], errors="coerce")
        cov = stock_ret.rolling(BETA_LOOKBACK_DAYS, min_periods=BETA_MIN_OBSERVATIONS).cov(market_ret)
        var = market_ret.rolling(BETA_LOOKBACK_DAYS, min_periods=BETA_MIN_OBSERVATIONS).var()
        values = (cov / var.replace(0, np.nan)).replace([np.inf, -np.inf], np.nan)
        beta.loc[group.index] = values.astype("float32")

    out["beta_60"] = beta
    out = out.drop(columns=["twii_return_1d", "_stock_return_1d"])
    return out


def _load_disposition_periods() -> dict[str, list[tuple[pd.Timestamp, pd.Timestamp]]]:
    if not DISPOSITION_PERIODS_PATH.exists():
        return {}
    periods = pd.read_csv(DISPOSITION_PERIODS_PATH, dtype={"stock_id": str})
    required = {"stock_id", "period_start", "period_end"}
    if not required.issubset(periods.columns):
        return {}
    periods["period_start"] = pd.to_datetime(periods["period_start"], errors="coerce")
    periods["period_end"] = pd.to_datetime(periods["period_end"], errors="coerce")
    periods = periods.dropna(subset=["stock_id", "period_start", "period_end"])
    result: dict[str, list[tuple[pd.Timestamp, pd.Timestamp]]] = {}
    for ticker, group in periods.groupby(periods["stock_id"].astype(str).str.strip()):
        result[ticker] = [
            (pd.Timestamp(row.period_start), pd.Timestamp(row.period_end))
            for row in group.itertuples(index=False)
        ]
    return result


def _disposition_mask(df: pd.DataFrame) -> pd.Series:
    periods = _load_disposition_periods()
    if not periods:
        return pd.Series(False, index=df.index)
    mask = pd.Series(False, index=df.index)
    for ticker, group in df.groupby(df["ticker"].astype(str).str.strip(), sort=False):
        ticker_periods = periods.get(ticker)
        if not ticker_periods:
            continue
        dates = pd.to_datetime(group["Date"], errors="coerce")
        local_mask = pd.Series(False, index=group.index)
        for start, end in ticker_periods:
            local_mask |= (dates >= start) & (dates <= end)
        mask.loc[group.index] = local_mask
    return mask


def _positive_ratio(y: pd.Series | np.ndarray) -> float:
    values = pd.Series(y).dropna().astype(int)
    if values.empty:
        return float("nan")
    return float(values.mean())


def _scale_pos_weight(y: pd.Series | np.ndarray) -> float:
    values = pd.Series(y).dropna().astype(int)
    pos = int(values.sum())
    neg = int(len(values) - pos)
    if pos <= 0:
        return 1.0
    return max(1.0, neg / pos)


def _safe_auc(y_true: pd.Series, prob: np.ndarray) -> float | None:
    if y_true.nunique(dropna=True) < 2:
        return None
    return float(roc_auc_score(y_true, prob))


def _safe_ap(y_true: pd.Series, prob: np.ndarray) -> float | None:
    if y_true.nunique(dropna=True) < 2:
        return None
    return float(average_precision_score(y_true, prob))


def _infer_foreign_column(df: pd.DataFrame) -> str | None:
    for column in ("foreign_cumsum_20d", "foreign_cumsum_20d_norm"):
        if column in df.columns:
            return column
    return None


def _attach_context(df: pd.DataFrame) -> pd.DataFrame:
    out = df.sort_values(["ticker", "Date"]).copy()

    close = pd.to_numeric(out["Close"], errors="coerce")
    ma60 = pd.to_numeric(out.get("MA_60"), errors="coerce")
    open_price = pd.to_numeric(out.get("Open"), errors="coerce")
    volume_ma20 = pd.to_numeric(out.get("VOL_MA_20"), errors="coerce")

    if "price_vs_ma60" in out.columns:
        price_vs_ma60 = pd.to_numeric(out["price_vs_ma60"], errors="coerce")
    else:
        price_vs_ma60 = close / ma60.replace(0, np.nan) - 1.0

    next_open = open_price.groupby(out["ticker"]).shift(-1)
    entry_gap = (next_open / close.replace(0, np.nan) - 1.0).clip(lower=0.0)

    out["_gate_price_vs_ma60"] = price_vs_ma60.astype("float32")
    out["_gate_avg_20d_volume"] = (volume_ma20 / VOLUME_SHARES_PER_LOT).astype("float32")
    out["_gate_avg_20d_amount"] = (volume_ma20 * close).astype("float64")
    out["next_open_t1"] = next_open.astype("float32")
    out["entry_gap_next_open_pct"] = entry_gap.astype("float32")
    if "beta_60" not in out.columns:
        out = _attach_beta_60(out)
    return out


def _build_target_margin(
    df: pd.DataFrame,
    *,
    target_mode: str,
    target_buffer: float,
    gap_penalty_multiplier: float,
) -> pd.Series:
    raw_alpha = pd.to_numeric(df["trade_excess_return_20d"], errors="coerce")
    if target_mode == "vanilla":
        return raw_alpha - target_buffer
    gap_penalty = pd.to_numeric(df["entry_gap_next_open_pct"], errors="coerce").clip(lower=0.0)
    return raw_alpha - target_buffer - gap_penalty_multiplier * gap_penalty


def _prepare_training_frame(
    raw_df: pd.DataFrame,
    *,
    target_mode: str,
    target_buffer: float,
    gap_penalty_multiplier: float,
    foreign_selling_floor: float,
    quality_min_roa: float,
    quality_min_operating_margin: float,
    positive_depoison: bool,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    required = {"Date", "ticker", "Close", "Open", "trade_excess_return_20d"}
    missing = sorted(required - set(raw_df.columns))
    if missing:
        raise ValueError(f"Dataset missing columns: {missing}")

    df = raw_df.copy()
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    df = df.dropna(subset=["Date", "ticker", "Close", "Open", "trade_excess_return_20d"]).copy()
    samples_before = int(len(df))

    df = _attach_context(df)
    disposition = _disposition_mask(df)
    context_ok = (
        df["_gate_price_vs_ma60"].le(MAX_PRICE_VS_MA60)
        & df["beta_60"].le(MAX_BETA_60)
        & df["_gate_avg_20d_volume"].ge(MIN_AVG_20D_VOLUME)
        & df["_gate_avg_20d_amount"].ge(MIN_AVG_20D_AMOUNT)
        & (~disposition)
    )
    context_ok = context_ok.fillna(False)
    df = df.loc[context_ok].copy()

    df["target_margin_20d"] = _build_target_margin(
        df,
        target_mode=target_mode,
        target_buffer=target_buffer,
        gap_penalty_multiplier=gap_penalty_multiplier,
    ).astype("float32")
    df = df.dropna(subset=["target_margin_20d"]).copy()
    df["alpha_positive_20d"] = (df["target_margin_20d"] > 0).astype(np.int8)

    foreign_col = _infer_foreign_column(df)
    severe_foreign = pd.Series(False, index=df.index)
    if foreign_col is not None:
        severe_foreign = pd.to_numeric(df[foreign_col], errors="coerce").lt(foreign_selling_floor).fillna(False)

    weak_roa = pd.Series(False, index=df.index)
    if "roa_annualized" in df.columns:
        weak_roa = pd.to_numeric(df["roa_annualized"], errors="coerce").lt(quality_min_roa).fillna(False)

    weak_margin = pd.Series(False, index=df.index)
    if "operating_margin_latest" in df.columns:
        weak_margin = (
            pd.to_numeric(df["operating_margin_latest"], errors="coerce")
            .lt(quality_min_operating_margin)
            .fillna(False)
        )

    positive_before = int(df["alpha_positive_20d"].sum())
    depoison_mask = pd.Series(False, index=df.index)
    if positive_depoison:
        depoison_mask = (df["alpha_positive_20d"] == 1) & (severe_foreign | weak_roa | weak_margin)
        df.loc[depoison_mask, "alpha_positive_20d"] = 0

    stats = {
        "samples_before_context_gates": samples_before,
        "samples_after_context_gates": int(len(df)),
        "dropped_by_context_gates": int(samples_before - len(df)),
        "positive_ratio_before_depoisoning": (
            positive_before / len(df) if len(df) else float("nan")
        ),
        "positive_ratio_after_depoisoning": _positive_ratio(df["alpha_positive_20d"]),
        "target_mode": target_mode,
        "target_buffer": target_buffer,
        "gap_penalty_multiplier": gap_penalty_multiplier,
        "foreign_column": foreign_col,
        "positive_label_flips": int(depoison_mask.sum()),
        "positive_label_flips_by_rule": {
            "severe_foreign_selling": int((depoison_mask & severe_foreign).sum()),
            "weak_roa": int((depoison_mask & weak_roa).sum()),
            "weak_operating_margin": int((depoison_mask & weak_margin).sum()),
        },
        "research_constraints": {
            "foreign_selling_floor": foreign_selling_floor,
            "quality_min_roa": quality_min_roa,
            "quality_min_operating_margin": quality_min_operating_margin,
            "positive_depoison_enabled": positive_depoison,
        },
        "context_gates": {
            "max_price_vs_ma60": MAX_PRICE_VS_MA60,
            "max_beta_60": MAX_BETA_60,
            "min_avg_20d_volume": MIN_AVG_20D_VOLUME,
            "min_avg_20d_amount": MIN_AVG_20D_AMOUNT,
            "drop_disposition_periods": bool(_load_disposition_periods()),
        },
    }

    df = _winsorize_features(df)
    df = _apply_cross_sectional_zscore(df)
    return df, stats


def _threshold_metrics(last_day: pd.DataFrame, thresholds: list[float]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for threshold in thresholds:
        selected = last_day[last_day["alpha_win_prob_20d"] >= threshold]
        key = f"{threshold:.2f}"
        if selected.empty:
            result[key] = {
                "count": 0,
                "precision": None,
                "avg_trade_excess_return": None,
                "avg_target_margin": None,
            }
            continue
        result[key] = {
            "count": int(len(selected)),
            "precision": float(selected["alpha_positive_20d"].mean()),
            "avg_trade_excess_return": float(selected["trade_excess_return_20d"].mean()),
            "avg_target_margin": float(selected["target_margin_20d"].mean()),
        }
    return result


def train_alpha_classifier(
    *,
    device: str = "gpu",
    dataset_path: str | None = None,
    max_rows: int = 0,
    save_model: bool = True,
    target_mode: str = "execution_adjusted",
    target_buffer: float = DEFAULT_TARGET_BUFFER,
    gap_penalty_multiplier: float = DEFAULT_GAP_PENALTY_MULTIPLIER,
    foreign_selling_floor: float = DEFAULT_FOREIGN_SELLING_FLOOR,
    quality_min_roa: float = DEFAULT_MIN_ROA,
    quality_min_operating_margin: float = DEFAULT_MIN_OPERATING_MARGIN,
    positive_depoison: bool = True,
    start_month: str = "2024-01-01",
) -> dict[str, Any] | None:
    params = PARAMS.copy()
    if device == "gpu":
        params["device"] = "gpu"

    print("=" * 72)
    print("  V2 execution-aware alpha classifier training")
    print("=" * 72)
    raw = _load_dataset(dataset_path, max_rows=max_rows)
    dataset, prep_stats = _prepare_training_frame(
        raw,
        target_mode=target_mode,
        target_buffer=target_buffer,
        gap_penalty_multiplier=gap_penalty_multiplier,
        foreign_selling_floor=foreign_selling_floor,
        quality_min_roa=quality_min_roa,
        quality_min_operating_margin=quality_min_operating_margin,
        positive_depoison=positive_depoison,
    )
    feature_cols = [col for col in get_feature_columns() if col in dataset.columns]
    unique_dates = pd.Index(dataset["Date"].drop_duplicates().sort_values())

    print(f"  Samples before context gates: {prep_stats['samples_before_context_gates']:,}")
    print(f"  Samples after context gates:  {prep_stats['samples_after_context_gates']:,}")
    print(f"  Positives before depoison:    {prep_stats['positive_ratio_before_depoisoning']:.2%}")
    print(f"  Positives after depoison:     {prep_stats['positive_ratio_after_depoisoning']:.2%}")
    print(f"  Positive label flips:         {prep_stats['positive_label_flips']:,}")
    print(f"  Features:                     {len(feature_cols)}")
    print(f"  Target mode:                  {target_mode}")
    print(f"  Date range:                   {dataset['Date'].min().date()} ~ {dataset['Date'].max().date()}")

    test_starts = pd.date_range(start_month, dataset["Date"].max(), freq="MS")
    rows: list[dict[str, Any]] = []

    for test_start in test_starts:
        test_end = test_start + pd.offsets.MonthEnd(0)
        train_start = test_start - pd.DateOffset(months=TRAIN_MONTHS)
        test = dataset[(dataset["Date"] >= test_start) & (dataset["Date"] <= test_end)]
        if test.empty:
            continue

        first_test_date = pd.Timestamp(test["Date"].min())
        test_start_idx = unique_dates.get_indexer([first_test_date])[0]
        if test_start_idx < EMBARGO_TRADING_DAYS:
            continue

        embargo_cutoff = unique_dates[test_start_idx - EMBARGO_TRADING_DAYS]
        train = dataset[(dataset["Date"] >= train_start) & (dataset["Date"] < embargo_cutoff)]
        if len(train) < 10000 or len(test) < 100:
            continue

        y_train = train["alpha_positive_20d"].astype(np.int8)
        y_val = test["alpha_positive_20d"].astype(np.int8)
        fold_params = params.copy()
        fold_params["scale_pos_weight"] = _scale_pos_weight(y_train)

        dtrain = lgb.Dataset(train[feature_cols].values, y_train.values, feature_name=feature_cols)
        dval = lgb.Dataset(test[feature_cols].values, y_val.values, feature_name=feature_cols, reference=dtrain)
        model = lgb.train(
            fold_params,
            dtrain,
            num_boost_round=NUM_ROUNDS,
            valid_sets=[dval],
            callbacks=[lgb.early_stopping(EARLY_STOPPING, verbose=False)],
        )

        prob = model.predict(test[feature_cols].values)
        test_df = test[
            [
                "ticker",
                "Date",
                "trade_excess_return_20d",
                "target_margin_20d",
                "alpha_positive_20d",
                "entry_gap_next_open_pct",
            ]
        ].copy()
        test_df["alpha_win_prob_20d"] = prob
        last_day = test_df.groupby("ticker").tail(1)
        top30 = last_day.sort_values("alpha_win_prob_20d", ascending=False).head(30)

        auc = _safe_auc(y_val, prob)
        ap = _safe_ap(y_val, prob)
        top30_precision = float(top30["alpha_positive_20d"].mean()) if not top30.empty else None
        top30_trade_excess = float(top30["trade_excess_return_20d"].mean()) if not top30.empty else None
        top30_target_margin = float(top30["target_margin_20d"].mean()) if not top30.empty else None
        top30_gap = float(top30["entry_gap_next_open_pct"].mean()) if not top30.empty else None
        threshold_metrics = _threshold_metrics(last_day, THRESHOLDS)

        rows.append(
            {
                "month": test_start.strftime("%Y-%m"),
                "train_samples": int(len(train)),
                "test_samples": int(len(test)),
                "test_names": int(len(last_day)),
                "train_positive_ratio": _positive_ratio(y_train),
                "test_positive_ratio": _positive_ratio(y_val),
                "auc": auc,
                "average_precision": ap,
                "top30_precision": top30_precision,
                "top30_avg_trade_excess_return": top30_trade_excess,
                "top30_avg_target_margin": top30_target_margin,
                "top30_avg_entry_gap": top30_gap,
                "threshold_metrics": json.dumps(threshold_metrics, ensure_ascii=False),
            }
        )

        print(
            f"  {test_start.strftime('%Y-%m')} "
            f"N={len(last_day):>4d} "
            f"AP={(ap if ap is not None else float('nan')):.3f} "
            f"AUC={(auc if auc is not None else float('nan')):.3f} "
            f"Top30Precision={(top30_precision if top30_precision is not None else float('nan')):.1%} "
            f"Top30Adj={(top30_target_margin if top30_target_margin is not None else float('nan')):+.2%} "
            f"Top30Raw={(top30_trade_excess if top30_trade_excess is not None else float('nan')):+.2%}"
        )

    if not rows:
        print("  No valid folds, aborting.")
        return None

    report = pd.DataFrame(rows)
    print("\nSummary")
    print(f"  Months:               {len(report)}")
    print(f"  Avg AP:               {report['average_precision'].mean():.4f}")
    print(f"  Avg AUC:              {report['auc'].mean():.4f}")
    print(f"  Avg Top30 precision:  {report['top30_precision'].mean():.2%}")
    print(f"  Avg Top30 adj margin: {report['top30_avg_target_margin'].mean():+.2%}")
    print(f"  Avg Top30 raw alpha:  {report['top30_avg_trade_excess_return'].mean():+.2%}")

    base_meta = {
        "model_file": None,
        "model_version": f"v2_alpha_classifier_20d_{target_mode}",
        "feature_columns": feature_cols,
        "n_features": len(feature_cols),
        "n_stocks": int(dataset["ticker"].nunique()),
        "n_samples": int(len(dataset)),
        "target": "alpha_positive_20d",
        "target_mode": target_mode,
        "target_definition": "target_margin_20d > 0",
        "target_buffer": target_buffer,
        "gap_penalty_multiplier": gap_penalty_multiplier,
        "default_gate_threshold": DEFAULT_ALPHA_GATE_THRESHOLD,
        "cross_sectional_zscore": True,
        "embargo_trading_days": EMBARGO_TRADING_DAYS,
        "date_range": [
            str(dataset["Date"].min().date()),
            str(dataset["Date"].max().date()),
        ],
        "lgbm_params": params,
        "prep_stats": prep_stats,
        "backtest_months": int(len(report)),
        "backtest_avg_ap": float(report["average_precision"].mean()),
        "backtest_avg_auc": float(report["auc"].mean()),
        "backtest_top30_precision": float(report["top30_precision"].mean()),
        "backtest_top30_avg_target_margin": float(report["top30_avg_target_margin"].mean()),
        "backtest_top30_avg_trade_excess_return": float(report["top30_avg_trade_excess_return"].mean()),
    }
    if not save_model:
        return base_meta

    y_all = dataset["alpha_positive_20d"].astype(np.int8)
    final_params = params.copy()
    final_params["scale_pos_weight"] = _scale_pos_weight(y_all)
    dtrain_all = lgb.Dataset(dataset[feature_cols].values, y_all.values, feature_name=feature_cols)
    final_model = lgb.train(final_params, dtrain_all, num_boost_round=NUM_ROUNDS)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    model_path = os.path.join(MODEL_DIR, f"lgbm_alpha_cls_v2_{target_mode}_{ts}.txt")
    final_model.save_model(model_path)

    meta = base_meta.copy()
    meta.update(
        {
            "model_file": model_path,
            "trained_at": ts,
            "lgbm_params": final_params,
        }
    )
    meta_path = model_path.replace(".txt", "_meta.json")
    with open(meta_path, "w", encoding="utf-8") as fh:
        json.dump(meta, fh, indent=2, ensure_ascii=False)

    report_path = os.path.join(REPORT_DIR, f"v2_alpha_classifier_backtest_{target_mode}_{ts}.csv")
    report.to_csv(report_path, index=False, encoding="utf-8-sig")

    fi = pd.DataFrame(
        {
            "feature": feature_cols,
            "importance": final_model.feature_importance(importance_type="gain"),
        }
    ).sort_values("importance", ascending=False)
    fi_path = os.path.join(REPORT_DIR, f"feature_importance_v2_alpha_classifier_{target_mode}_{ts}.csv")
    fi.to_csv(fi_path, index=False, encoding="utf-8-sig")

    print(f"\n  Model:  {model_path}")
    print(f"  Meta:   {meta_path}")
    print(f"  Report: {report_path}")
    print(f"  FI:     {fi_path}")
    return meta


def main() -> None:
    parser = argparse.ArgumentParser(description="Train V2 20D alpha classifier")
    parser.add_argument("--device", default="gpu", choices=["gpu", "cpu"])
    parser.add_argument("--dataset-path", default=None)
    parser.add_argument("--max-rows", type=int, default=0, help="Limit rows for smoke tests.")
    parser.add_argument("--no-save", action="store_true", help="Run folds without writing a model.")
    parser.add_argument(
        "--target-mode",
        default="execution_adjusted",
        choices=["vanilla", "execution_adjusted"],
        help="Label definition for positive alpha.",
    )
    parser.add_argument("--target-buffer", type=float, default=DEFAULT_TARGET_BUFFER)
    parser.add_argument("--gap-penalty-multiplier", type=float, default=DEFAULT_GAP_PENALTY_MULTIPLIER)
    parser.add_argument("--foreign-selling-floor", type=float, default=DEFAULT_FOREIGN_SELLING_FLOOR)
    parser.add_argument("--quality-min-roa", type=float, default=DEFAULT_MIN_ROA)
    parser.add_argument("--quality-min-operating-margin", type=float, default=DEFAULT_MIN_OPERATING_MARGIN)
    parser.add_argument(
        "--disable-positive-depoison",
        action="store_true",
        help="Keep positive labels even when foreign selling / quality rules trigger.",
    )
    parser.add_argument("--start-month", default="2024-01-01")
    args = parser.parse_args()

    train_alpha_classifier(
        device=args.device,
        dataset_path=args.dataset_path,
        max_rows=args.max_rows,
        save_model=not args.no_save,
        target_mode=args.target_mode,
        target_buffer=args.target_buffer,
        gap_penalty_multiplier=args.gap_penalty_multiplier,
        foreign_selling_floor=args.foreign_selling_floor,
        quality_min_roa=args.quality_min_roa,
        quality_min_operating_margin=args.quality_min_operating_margin,
        positive_depoison=not args.disable_positive_depoison,
        start_month=args.start_month,
    )


if __name__ == "__main__":
    main()
