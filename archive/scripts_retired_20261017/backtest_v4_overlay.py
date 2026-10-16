"""V4 Overlay Backtest: V3 20D Top N + T+1 Execution variants.

This script stacks the T+1 classifier on top of V3 20D regression and tests
several overlay designs in a single walk-forward pass:

    V3_B                Old filter, close entry, friction applied.
    V3_D                Old filter + sector cap, close entry, friction applied.
    LIMIT_ONLY          Limit 0.5% discount, no T+1 gate (isolate limit effect).
    HARD_0.64           T+1 prob >= 0.64 AND limit 0.5% (hard gate).
    SOFT_0.55           T+1 prob >= 0.55 AND limit 0.5% (soft gate).
    RANK_10_CLOSE       Among V3 Top 30, pick 10 with highest T+1 prob, close entry.
    RANK_15_CLOSE       Among V3 Top 30, pick 15 with highest T+1 prob, close entry.
    RANK_10_LIMIT       Pick top 10 by T+1 prob, then limit 0.5% entry.

Dataset caching: V3 and T+1 datasets are cached to parquet after first build,
so re-runs skip the expensive ~25 minute feature computation.
"""
from __future__ import annotations

import os
import sys
import warnings
from typing import Callable

import lightgbm as lgb
import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ml.config import REPORT_DIR
from ml.dataset import load_single_stock, _load_twii, _list_daily_tickers
from ml.dataset_t1 import build_t1_dataset, T1_FEATURE_COLUMNS
from ml.features.sector import load_sector_mapping


# ------------------------------------------------------------ Config --------
SECTOR_CAP_RATIO = 0.20
T1_TARGET = "t1_close_positive"
LIMIT_DISCOUNT = 0.005           # limit = close * (1 - 0.005)
FRICTION = 0.004
TOP_N = 30                        # V3 universe
TRAIN_MONTHS = 24
TEST_RANGE = ("2024-01-01", "2026-02-01")

CACHE_DIR = os.path.join(REPORT_DIR, "cache")
V3_CACHE = os.path.join(CACHE_DIR, "v3_dataset.parquet")
T1_CACHE = os.path.join(CACHE_DIR, "t1_dataset.parquet")

V3_FEATURES = [
    "price_vs_ma5", "price_vs_ma10", "price_vs_ma20", "price_vs_ma60",
    "return_5d", "return_10d", "return_20d", "return_60d",
    "momentum_accel", "roc_5", "roc_10", "roc_20",
    "volatility_5d", "volatility_20d", "bb_width", "bb_position",
    "atr_14", "atr_pct_rank", "vol_contraction",
    "rsi_6", "rsi_14", "williams_r_14", "mfi_14", "stoch_rsi_k",
    "vol_ratio_5_20", "vol_zscore", "obv_slope_20", "cmf_20",
    "foreign_cumsum_1d", "foreign_cumsum_3d",
    "foreign_cumsum_5d", "foreign_cumsum_10d", "foreign_cumsum_20d",
    "foreign_cumsum_1d_norm", "foreign_cumsum_3d_norm",
    "foreign_cumsum_5d_norm", "foreign_cumsum_10d_norm", "foreign_cumsum_20d_norm",
    "trust_cumsum_1d", "trust_cumsum_3d",
    "trust_cumsum_5d", "trust_cumsum_10d", "trust_cumsum_20d",
    "trust_cumsum_1d_norm", "trust_cumsum_3d_norm",
    "trust_cumsum_5d_norm", "trust_cumsum_10d_norm", "trust_cumsum_20d_norm",
    "dealer_cumsum_1d_norm", "dealer_cumsum_3d_norm",
    "dealer_cumsum_5d_norm", "dealer_cumsum_10d_norm",
    "inst_total_5d", "inst_total_10d", "inst_total_20d",
    "inst_total_5d_norm", "inst_total_10d_norm", "inst_total_20d_norm",
    "foreign_trust_sync", "foreign_buy_streak", "trust_buy_streak",
    "margin_change_5d", "margin_short_ratio", "turnover_rate",
    "dist_to_high_20d", "dist_to_low_20d",
    "dist_to_high_60d", "dist_to_low_60d",
    "position_52w", "up_day_ratio_20",
    "ma_slope_5", "ma_slope_20", "ma_bullish_align",
    "adx_14", "di_diff", "trend_direction", "lr_slope_20", "lr_r2_20",
    "macd_hist", "kd_k", "kd_d",
    "revenue_yoy_latest", "revenue_yoy_3m_avg", "revenue_yoy_momentum",
    "gross_margin_latest", "operating_margin_latest", "net_margin_latest",
    "margin_trend",
    "pe_ratio", "pb_ratio", "dividend_yield", "pe_percentile_60d",
    "eps_ttm", "eps_yoy", "eps_momentum",
    "twii_return_5d", "twii_return_20d",
    "sector_relative_return_5d", "sector_relative_return_20d",
    "sector_momentum_20d", "sector_breadth",
    "whale_pct", "whale_pct_chg", "retail_pct_chg",
    "price_vs_inst_cost", "vol_contraction_ratio",
    "inst_buy_ratio_20d", "mom_20d",
]
V3_FEATURES = list(dict.fromkeys(V3_FEATURES))
FILTER_COLS = ["operating_margin_latest", "price_vs_ma20", "Volume", "return_20d"]

_sector_df = load_sector_mapping()
_SECTOR_LOOKUP = dict(zip(_sector_df["Ticker"], _sector_df["Sector"])) if _sector_df is not None else {}


# ------------------------------------------------------- Metric helpers ----
def max_drawdown(cum_series: pd.Series) -> float:
    if len(cum_series) == 0:
        return 0.0
    peak = cum_series.expanding().max()
    dd = (cum_series - peak) / peak
    return float(dd.min())


def compute_metrics(returns: pd.Series) -> dict:
    if len(returns) == 0:
        return {"avg": 0.0, "cum": 0.0, "mdd": 0.0, "sharpe": 0.0,
                "sortino": 0.0, "calmar": 0.0, "win": 0.0}
    mean = returns.mean()
    std = returns.std()
    downside = returns[returns < 0].std()
    cum = (1 + returns).cumprod()
    mdd = max_drawdown(cum)
    return {
        "avg": mean,
        "cum": cum.iloc[-1] - 1,
        "mdd": mdd,
        "sharpe": mean / std * np.sqrt(12) if std and std > 0 else 0.0,
        "sortino": mean / downside * np.sqrt(12) if downside and downside > 0 else 0.0,
        "calmar": (mean * 12) / abs(mdd) if mdd != 0 else 0.0,
        "win": (returns > 0).mean(),
    }


def apply_old_filter(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if "operating_margin_latest" in out.columns:
        out = out[~(out["operating_margin_latest"] < 0)]
    if "Volume" in out.columns:
        out = out[out["Volume"] >= 500_000]
    if "price_vs_ma20" in out.columns:
        out = out[out["price_vs_ma20"].abs() <= 0.30]
    out = out[out["pred_return"].abs() <= 0.50]
    return out


def select_with_sector_cap(ranked: pd.DataFrame, top_n: int = TOP_N,
                           cap_ratio: float = SECTOR_CAP_RATIO) -> pd.DataFrame:
    max_per_sector = max(1, int(top_n * cap_ratio))
    selected = []
    sector_count: dict = {}
    for _, row in ranked.iterrows():
        sector = _SECTOR_LOOKUP.get(row["ticker"], "其他")
        if sector_count.get(sector, 0) < max_per_sector:
            selected.append(row)
            sector_count[sector] = sector_count.get(sector, 0) + 1
        if len(selected) >= top_n:
            break
    return pd.DataFrame(selected)


# ---------------------------------------------------- Portfolio helpers ----
def _slot_return_close_entry(row: pd.Series) -> float:
    """Vanilla: buy at Open[t+1], exit Close[t+20], subtract friction."""
    return row["target_20d"] - FRICTION


def _slot_return_limit_entry(row: pd.Series) -> float:
    """Limit 0.5% discount. NaN if unfilled."""
    if pd.isna(row.get("t1_low_return", np.nan)):
        return np.nan
    if row["t1_low_return"] > -LIMIT_DISCOUNT:
        return np.nan  # unfilled
    fill_mult = min(1.0 + row["t1_open_return"], 1.0 - LIMIT_DISCOUNT)
    return (1.0 + row["target_20d"]) / fill_mult - 1.0 - FRICTION


def _portfolio_return_slot_average(picks: pd.DataFrame, slot_cap: int) -> dict:
    """Sum of per-slot returns / slot_cap. Unfilled slots = 0 (cash)."""
    if picks is None or len(picks) == 0:
        return {"ret": 0.0, "exposure": 0.0, "filled": 0, "cap": slot_cap}
    filled_mask = picks["slot_return"].notna()
    filled = filled_mask.sum()
    total_ret = picks.loc[filled_mask, "slot_return"].sum()
    return {
        "ret": total_ret / slot_cap,
        "exposure": filled / slot_cap,
        "filled": int(filled),
        "cap": slot_cap,
    }


# --------------------------------------------------------- Strategy defs --
def strat_v3_baseline(universe: pd.DataFrame, use_sector_cap: bool) -> dict:
    """V3 vanilla: Open[t+1] entry, full 30 slots, friction applied."""
    picks = (
        select_with_sector_cap(universe, top_n=TOP_N) if use_sector_cap
        else universe.head(TOP_N)
    )
    picks = picks.copy()
    picks["slot_return"] = picks["target_20d"] - FRICTION
    return _portfolio_return_slot_average(picks, slot_cap=TOP_N)


def strat_limit_only(universe: pd.DataFrame, use_sector_cap: bool) -> dict:
    """Limit 0.5% discount, no T+1 gate."""
    picks = (
        select_with_sector_cap(universe, top_n=TOP_N) if use_sector_cap
        else universe.head(TOP_N)
    )
    picks = picks.copy()
    picks["slot_return"] = picks.apply(_slot_return_limit_entry, axis=1)
    return _portfolio_return_slot_average(picks, slot_cap=TOP_N)


def strat_t1_hard_gate(universe: pd.DataFrame, use_sector_cap: bool,
                       prob_threshold: float) -> dict:
    """T+1 prob >= threshold AND limit 0.5%."""
    picks = (
        select_with_sector_cap(universe, top_n=TOP_N) if use_sector_cap
        else universe.head(TOP_N)
    )
    picks = picks.copy()
    prob_ok = picks["t1_prob"].fillna(-1) >= prob_threshold
    limit_ret = picks.apply(_slot_return_limit_entry, axis=1)
    picks["slot_return"] = np.where(prob_ok, limit_ret, np.nan)
    return _portfolio_return_slot_average(picks, slot_cap=TOP_N)


def strat_t1_rank_close(universe: pd.DataFrame, use_sector_cap: bool,
                        take_k: int) -> dict:
    """Rank V3 Top N by T+1 prob desc, take top K, close entry."""
    base = (
        select_with_sector_cap(universe, top_n=TOP_N) if use_sector_cap
        else universe.head(TOP_N)
    )
    if len(base) == 0:
        return _portfolio_return_slot_average(None, slot_cap=take_k)
    ranked = base.copy().sort_values("t1_prob", ascending=False, na_position="last")
    picks = ranked.head(take_k).copy()
    picks["slot_return"] = picks["target_20d"] - FRICTION
    return _portfolio_return_slot_average(picks, slot_cap=take_k)


def strat_t1_rank_limit(universe: pd.DataFrame, use_sector_cap: bool,
                        take_k: int) -> dict:
    """Rank V3 Top N by T+1 prob desc, take top K, limit 0.5%."""
    base = (
        select_with_sector_cap(universe, top_n=TOP_N) if use_sector_cap
        else universe.head(TOP_N)
    )
    if len(base) == 0:
        return _portfolio_return_slot_average(None, slot_cap=take_k)
    ranked = base.copy().sort_values("t1_prob", ascending=False, na_position="last")
    picks = ranked.head(take_k).copy()
    picks["slot_return"] = picks.apply(_slot_return_limit_entry, axis=1)
    return _portfolio_return_slot_average(picks, slot_cap=take_k)


# ---------------------------------------------------------- Data loaders --
def build_v3_dataset() -> pd.DataFrame:
    print("Building V3 20D dataset (first run, will cache)...", flush=True)
    twii = _load_twii()
    tickers = _list_daily_tickers()
    frames = []
    for t in tickers:
        df = load_single_stock(t, twii_df=twii)
        if df is None or len(df) < 120:
            continue
        df = df.copy()
        next_open = pd.to_numeric(df["Open"], errors="coerce").shift(-1).replace(0, np.nan)
        df["target_20d"] = df["Close"].shift(-20) / next_open - 1.0
        avail = [c for c in V3_FEATURES if c in df.columns]
        keep = ["Date", "Open", "Close", "target_20d"] + avail
        for fc in FILTER_COLS:
            if fc in df.columns and fc not in keep:
                keep.append(fc)
        sub = df[keep].copy()
        sub["ticker"] = t
        sub = sub.dropna(subset=["target_20d"])
        if len(sub) > 60:
            frames.append(sub)
    out = pd.concat(frames, ignore_index=True).sort_values("Date").reset_index(drop=True)
    return out


def load_or_build_v3() -> pd.DataFrame:
    if os.path.exists(V3_CACHE):
        print(f"Loading V3 dataset from cache: {V3_CACHE}", flush=True)
        df = pd.read_parquet(V3_CACHE)
        df["Date"] = pd.to_datetime(df["Date"])
        return df
    df = build_v3_dataset()
    os.makedirs(CACHE_DIR, exist_ok=True)
    df.to_parquet(V3_CACHE, index=False)
    print(f"V3 cache saved: {V3_CACHE}", flush=True)
    return df


def load_or_build_t1() -> pd.DataFrame:
    if os.path.exists(T1_CACHE):
        print(f"Loading T+1 dataset from cache: {T1_CACHE}", flush=True)
        df = pd.read_parquet(T1_CACHE)
        df["Date"] = pd.to_datetime(df["Date"])
        df["ticker"] = df["ticker"].astype(str)
        return df
    print("Building T+1 dataset (first run, will cache)...", flush=True)
    df = build_t1_dataset(verbose=False)
    df["Date"] = pd.to_datetime(df["Date"])
    df["ticker"] = df["ticker"].astype(str)
    df = df.sort_values(["Date", "ticker"]).reset_index(drop=True)
    os.makedirs(CACHE_DIR, exist_ok=True)
    df.to_parquet(T1_CACHE, index=False)
    print(f"T+1 cache saved: {T1_CACHE}", flush=True)
    return df


# ---------------------------------------------------------------- main ----
def main():
    print("=== V4 Overlay Backtest (multi-variant) ===", flush=True)
    print(f"  T+1 target: {T1_TARGET}", flush=True)
    print(f"  Limit discount: {LIMIT_DISCOUNT:.3%}", flush=True)
    print(f"  Friction: {FRICTION:.3%}", flush=True)
    print(f"  Top N universe: {TOP_N}", flush=True)
    print(f"  Train window: {TRAIN_MONTHS} months", flush=True)
    print(f"  Test range: {TEST_RANGE[0]} ~ {TEST_RANGE[1]}", flush=True)
    print("", flush=True)

    v3 = load_or_build_v3()
    v3_features_avail = [c for c in V3_FEATURES if c in v3.columns]
    print(f"  V3: {len(v3):,} rows, {v3['ticker'].nunique()} tickers, "
          f"{len(v3_features_avail)} features", flush=True)

    t1 = load_or_build_t1()
    t1_features_avail = [c for c in T1_FEATURE_COLUMNS if c in t1.columns]
    print(f"  T+1: {len(t1):,} rows, {t1['ticker'].nunique()} tickers, "
          f"{len(t1_features_avail)} features", flush=True)

    test_starts = pd.date_range(TEST_RANGE[0], TEST_RANGE[1], freq="MS")
    total = len(test_starts)
    print(f"\nRunning walk-forward over {total} months...\n", flush=True)

    v3_params = {
        "objective": "regression", "metric": "rmse", "boosting_type": "gbdt",
        "device": "gpu", "num_leaves": 31, "learning_rate": 0.05,
        "feature_fraction": 0.7, "bagging_fraction": 0.7, "bagging_freq": 5,
        "verbose": -1, "n_jobs": -1, "seed": 42,
    }
    t1_params = {
        "objective": "binary", "metric": "binary_logloss", "boosting_type": "gbdt",
        "device": "gpu", "num_leaves": 63, "learning_rate": 0.03,
        "feature_fraction": 0.6, "bagging_fraction": 0.8, "bagging_freq": 5,
        "min_data_in_leaf": 80, "lambda_l1": 0.1, "lambda_l2": 0.5,
        "verbose": -1, "n_jobs": -1, "seed": 42,
    }

    # Per-variant monthly accumulators
    variants = [
        "V3_B",
        "V3_D",
        "LIMIT_B",
        "LIMIT_D",
        "HARD_064_B",
        "HARD_064_D",
        "SOFT_055_B",
        "SOFT_055_D",
        "RANK10_CLOSE_B",
        "RANK10_CLOSE_D",
        "RANK15_CLOSE_B",
        "RANK15_CLOSE_D",
        "RANK10_LIMIT_B",
        "RANK10_LIMIT_D",
    ]
    monthly_rows = []  # columns: month, variant, ret, exposure, filled, cap
    slot_rows = []     # per-pick detail for post-hoc analysis

    for idx, test_start in enumerate(test_starts, 1):
        test_end = test_start + pd.offsets.MonthEnd(0)
        train_start = test_start - pd.DateOffset(months=TRAIN_MONTHS)

        v3_train = v3[(v3["Date"] >= train_start) & (v3["Date"] < test_start)]
        v3_test = v3[(v3["Date"] >= test_start) & (v3["Date"] <= test_end)]
        if len(v3_train) < 10000 or len(v3_test) < 100:
            continue
        dtrain_v3 = lgb.Dataset(
            v3_train[v3_features_avail].values,
            v3_train["target_20d"].values,
            feature_name=v3_features_avail,
        )
        try:
            v3_model = lgb.train(v3_params, dtrain_v3, num_boost_round=300)
        except lgb.basic.LightGBMError:
            v3_model = lgb.train({**v3_params, "device": "cpu"}, dtrain_v3, num_boost_round=300)

        t1_train = t1[(t1["Date"] >= train_start) & (t1["Date"] < test_start)]
        t1_test = t1[(t1["Date"] >= test_start) & (t1["Date"] <= test_end)]
        if len(t1_train) < 10000 or len(t1_test) < 20:
            continue
        y_tr = t1_train[T1_TARGET].astype(int).values
        pos = max(int(y_tr.sum()), 1)
        neg = max(len(y_tr) - pos, 1)
        params_t1 = {**t1_params, "scale_pos_weight": neg / pos}
        dtrain_t1 = lgb.Dataset(
            t1_train[t1_features_avail].values,
            y_tr,
            feature_name=t1_features_avail,
        )
        try:
            t1_model = lgb.train(params_t1, dtrain_t1, num_boost_round=400)
        except lgb.basic.LightGBMError:
            t1_model = lgb.train({**params_t1, "device": "cpu"}, dtrain_t1, num_boost_round=400)

        # V3 predictions
        v3_pred = v3_test[["ticker", "Date", "Close", "target_20d"]].copy()
        v3_pred["pred_return"] = v3_model.predict(v3_test[v3_features_avail].values)
        for fc in FILTER_COLS:
            if fc in v3_test.columns:
                v3_pred[fc] = v3_test[fc].values

        # Signal day = last trading day of test month
        signal_day = v3_pred["Date"].max()
        last_day = v3_pred[v3_pred["Date"] == signal_day].sort_values(
            "pred_return", ascending=False
        )
        filtered = apply_old_filter(last_day)  # shared universe after B-filter
        if len(filtered) == 0:
            continue

        # T+1 scoring on signal day
        t1_signal = t1_test[t1_test["Date"] == signal_day].copy()
        if len(t1_signal) == 0:
            continue
        t1_signal["t1_prob"] = t1_model.predict(t1_signal[t1_features_avail].values)
        t1_lookup = t1_signal[
            ["Date", "ticker", "t1_prob", "t1_open_return", "t1_low_return"]
        ].copy()

        # Attach T+1 info to filtered universe
        universe = filtered.merge(t1_lookup, on=["Date", "ticker"], how="left")

        # Record per-slot detail (for post-hoc sweep)
        slot_rows.append(universe.assign(month=test_start.strftime("%Y-%m")))

        # Compute all variants (B and D flavors)
        def run_variant(fn: Callable, label: str, **kwargs):
            res_b = fn(universe, use_sector_cap=False, **kwargs)
            res_d = fn(universe, use_sector_cap=True, **kwargs)
            monthly_rows.append({"month": test_start.strftime("%Y-%m"),
                                 "variant": f"{label}_B", **res_b})
            monthly_rows.append({"month": test_start.strftime("%Y-%m"),
                                 "variant": f"{label}_D", **res_d})
            return res_b, res_d

        v3b, v3d = run_variant(strat_v3_baseline, "V3")
        lb, ld = run_variant(strat_limit_only, "LIMIT")
        hb, hd = run_variant(strat_t1_hard_gate, "HARD_064", prob_threshold=0.64)
        sb, sd = run_variant(strat_t1_hard_gate, "SOFT_055", prob_threshold=0.55)
        r10cb, r10cd = run_variant(strat_t1_rank_close, "RANK10_CLOSE", take_k=10)
        r15cb, r15cd = run_variant(strat_t1_rank_close, "RANK15_CLOSE", take_k=15)
        r10lb, r10ld = run_variant(strat_t1_rank_limit, "RANK10_LIMIT", take_k=10)

        pct = idx / total * 100
        bar = "=" * int(pct // 4)
        print(f"  [{bar:<25s}] {pct:5.1f}% {test_start.strftime('%Y-%m')}  "
              f"V3_B {v3b['ret']*100:+5.1f}%  "
              f"LIMIT {lb['ret']*100:+5.1f}% (x{lb['exposure']:.0%})  "
              f"HARD {hb['ret']*100:+5.1f}% (x{hb['exposure']:.0%})  "
              f"SOFT {sb['ret']*100:+5.1f}% (x{sb['exposure']:.0%})  "
              f"R10C {r10cb['ret']*100:+5.1f}%  "
              f"R10L {r10lb['ret']*100:+5.1f}% (x{r10lb['exposure']:.0%})",
              flush=True)

    if not monthly_rows:
        print("No results, exiting.", flush=True)
        return

    mdf = pd.DataFrame(monthly_rows)

    # ---- Summary per variant ----
    print(f"\n{'='*115}", flush=True)
    print(f"  Variant Summary  ({mdf['month'].nunique()} months)", flush=True)
    print(f"{'='*115}", flush=True)
    print(f"{'variant':<17s} {'avg_m':>9s} {'cum':>10s} {'mdd':>10s} "
          f"{'Sharpe':>9s} {'Sortino':>9s} {'Calmar':>9s} {'win%':>9s} {'expose%':>9s}",
          flush=True)
    print("-" * 115, flush=True)

    summary_rows = []
    for variant in variants:
        sub = mdf[mdf["variant"] == variant].sort_values("month")
        if len(sub) == 0:
            continue
        m = compute_metrics(sub["ret"])
        avg_exp = sub["exposure"].mean()
        summary_rows.append({"variant": variant, **m, "avg_exposure": avg_exp})
        print(f"{variant:<17s} "
              f"{m['avg']*100:>+8.2f}% {m['cum']*100:>+9.1f}% {m['mdd']*100:>+9.1f}% "
              f"{m['sharpe']:>9.3f} {m['sortino']:>9.3f} {m['calmar']:>9.3f} "
              f"{m['win']*100:>8.1f}% {avg_exp*100:>8.1f}%",
              flush=True)

    # ---- Save CSVs ----
    os.makedirs(REPORT_DIR, exist_ok=True)
    monthly_path = os.path.join(REPORT_DIR, "backtest_v4_overlay_monthly.csv")
    mdf.to_csv(monthly_path, index=False, encoding="utf-8-sig")
    print(f"\nSaved monthly: {monthly_path}", flush=True)

    summary_df = pd.DataFrame(summary_rows)
    summary_path = os.path.join(REPORT_DIR, "backtest_v4_overlay_summary.csv")
    summary_df.to_csv(summary_path, index=False, encoding="utf-8-sig")
    print(f"Saved summary: {summary_path}", flush=True)

    if slot_rows:
        slot_df = pd.concat(slot_rows, ignore_index=True)
        slot_path = os.path.join(REPORT_DIR, "backtest_v4_overlay_slots.csv")
        slot_df.to_csv(slot_path, index=False, encoding="utf-8-sig")
        print(f"Saved slots:   {slot_path}  ({len(slot_df)} rows)", flush=True)


if __name__ == "__main__":
    main()
