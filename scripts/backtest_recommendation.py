"""回測推薦標籤系統：v2 模型預測 → 推薦等級 → 實際報酬

驗證 pred_return_20d 的門檻 (-5%/-2%/+2%/+5%) 是否能區分好壞股票。
Walk-forward: 每月用前 24 個月訓練，預測當月，看 20 天後實際報酬。
"""
import os, sys, warnings
import pandas as pd
import numpy as np
import lightgbm as lgb

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ml.config import DAILY_K_DIR, MODEL_DIR
from ml.dataset import load_single_stock, _load_twii, _list_daily_tickers
from ml.predict import _DIMENSION_MAP

# v2 features (from meta)
SELECTED_FEATURES = [
    "price_vs_ma5", "price_vs_ma10", "price_vs_ma20", "price_vs_ma60",
    "return_5d", "return_10d", "return_20d", "return_60d",
    "momentum_accel", "roc_5", "roc_10", "roc_20",
    "volatility_5d", "volatility_20d", "bb_width", "bb_position",
    "atr_14", "vol_contraction",
    "rsi_6", "rsi_14", "williams_r_14", "mfi_14", "stoch_rsi_k",
    "vol_ratio_5_20", "vol_zscore", "obv_slope_20", "cmf_20",
    "foreign_cumsum_5d", "foreign_cumsum_10d", "foreign_cumsum_20d",
    "trust_cumsum_5d", "trust_cumsum_10d", "trust_cumsum_20d",
    "inst_total_5d", "inst_total_10d", "inst_total_20d",
    "foreign_trust_sync", "foreign_buy_streak",
    "margin_change_5d", "margin_short_ratio",
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
    "whale_pct", "whale_pct_chg", "retail_pct_chg",
    "price_vs_inst_cost", "vol_contraction_ratio",
    "inst_buy_ratio_20d", "mom_20d", "price_vs_ma60",
]


def compute_dim_scores(model, X, feature_cols):
    """pred_contrib dimension decomposition"""
    contribs = model.predict(X, pred_contrib=True)
    dims = {"tech": np.zeros(len(X)), "chip": np.zeros(len(X)),
            "fund": np.zeros(len(X)), "sector": np.zeros(len(X))}
    dim_key = {"技術面": "tech", "籌碼面": "chip", "基本面": "fund", "產業面": "sector"}
    for i, col in enumerate(feature_cols):
        d = _DIMENSION_MAP.get(col, "技術面")
        dims[dim_key[d]] += contribs[:, i]
    return dims


def main():
    twii = _load_twii()
    tickers = _list_daily_tickers()
    print(f"Loading {len(tickers)} stocks...")

    all_data = []
    for t in tickers:
        df = load_single_stock(t, twii_df=twii)
        if df is None or len(df) < 120:
            continue
        df = df.copy()
        df["target_20d"] = df["Close"].shift(-20) / df["Close"] - 1
        available_cols = [c for c in SELECTED_FEATURES if c in df.columns]
        keep = ["Date", "Close", "target_20d"] + available_cols
        sub = df[keep].copy()
        sub["ticker"] = t
        sub = sub.dropna(subset=["target_20d"])
        if len(sub) > 60:
            all_data.append(sub)

    print(f"Loaded {len(all_data)} stocks")
    big = pd.concat(all_data, ignore_index=True).sort_values("Date").reset_index(drop=True)
    feature_cols = [c for c in SELECTED_FEATURES if c in big.columns]
    print(f"Total: {len(big):,}, Features: {len(feature_cols)}")

    # Walk-forward
    test_starts = pd.date_range("2024-01-01", "2026-02-01", freq="MS")
    all_preds = []
    monthly_results = []

    params = {
        "objective": "regression", "metric": "rmse", "boosting_type": "gbdt", "device": "gpu",
        "num_leaves": 31, "learning_rate": 0.05,
        "feature_fraction": 0.7, "bagging_fraction": 0.7, "bagging_freq": 5,
        "verbose": -1, "n_jobs": -1, "seed": 42,
    }

    for test_start in test_starts:
        test_end = test_start + pd.offsets.MonthEnd(0)
        train_start = test_start - pd.DateOffset(months=24)

        train = big[(big["Date"] >= train_start) & (big["Date"] < test_start)]
        test = big[(big["Date"] >= test_start) & (big["Date"] <= test_end)]

        if len(train) < 10000 or len(test) < 100:
            continue

        X_train = train[feature_cols].values
        y_train = train["target_20d"].values
        X_test = test[feature_cols].values

        dtrain = lgb.Dataset(X_train, y_train)
        model = lgb.train(params, dtrain, num_boost_round=300)

        pred = model.predict(X_test)
        last_day = test.groupby("ticker").tail(1).copy()
        last_day["pred_return"] = model.predict(last_day[feature_cols].values)

        # Recommendation labels
        last_day["rec"] = pd.cut(
            last_day["pred_return"],
            bins=[-np.inf, -0.05, -0.02, 0.02, 0.05, np.inf],
            labels=["StrongSell", "Sell", "Hold", "Buy", "StrongBuy"],
        )

        # Dimension scores
        dims = compute_dim_scores(model, last_day[feature_cols].values, feature_cols)
        for k, v in dims.items():
            last_day[k] = v

        last_day["month"] = test_start.strftime("%Y-%m")
        all_preds.append(last_day[["ticker", "month", "pred_return", "target_20d", "rec",
                                    "tech", "chip", "fund", "sector"]])

        # Per-recommendation stats
        rec_stats = {}
        for rec in ["StrongBuy", "Buy", "Hold", "Sell", "StrongSell"]:
            sub = last_day[last_day["rec"] == rec]
            if len(sub) > 0:
                rec_stats[rec] = {
                    "n": len(sub),
                    "ret": sub["target_20d"].mean(),
                    "win": (sub["target_20d"] > 0).mean(),
                }

        n = len(last_day)
        ic = last_day["pred_return"].corr(last_day["target_20d"], method="spearman")
        monthly_results.append({"month": test_start.strftime("%Y-%m"), "n": n, "ic": ic, **rec_stats})

        line = f"  {test_start.strftime('%Y-%m')}  N={n:>4d}  IC={ic:+.3f}"
        for rec in ["StrongBuy", "Buy", "Hold", "Sell", "StrongSell"]:
            if rec in rec_stats:
                s = rec_stats[rec]
                line += f"  {rec[:2]}={s['ret']*100:+.1f}%({s['n']})"
        print(line)

    # === Summary ===
    ap = pd.concat(all_preds, ignore_index=True)
    print("\n" + "=" * 80)
    print("  Recommendation Backtest (v2 walk-forward, 20d return)")
    print("=" * 80)

    for rec in ["StrongBuy", "Buy", "Hold", "Sell", "StrongSell"]:
        sub = ap[ap["rec"] == rec]
        if len(sub) > 0:
            avg_ret = sub["target_20d"].mean()
            win = (sub["target_20d"] > 0).mean()
            print(f"  {rec:>10s}  N={len(sub):>6d}  Avg20d={avg_ret*100:+.3f}%  WinRate={win*100:.1f}%")

    # Monotonicity check
    rec_order = ["StrongSell", "Sell", "Hold", "Buy", "StrongBuy"]
    returns = []
    for rec in rec_order:
        sub = ap[ap["rec"] == rec]
        returns.append(sub["target_20d"].mean() if len(sub) > 0 else np.nan)
    monotonic = all(a <= b for a, b in zip(returns, returns[1:]) if not np.isnan(a) and not np.isnan(b))
    print(f"\n  Monotonic (SS < S < H < B < SB): {'YES' if monotonic else 'NO'}")
    print(f"  Return spread (SB - SS): {(returns[-1] - returns[0])*100:+.3f}%")

    # Dimension IC
    print(f"\n  --- Dimension IC (across all months) ---")
    for dim in ["tech", "chip", "fund", "sector"]:
        ic = ap[dim].corr(ap["target_20d"], method="spearman")
        print(f"  {dim:>8s}: IC={ic:+.4f}")

    # Monthly IC
    rdf = pd.DataFrame([{"month": m["month"], "ic": m["ic"]} for m in monthly_results])
    print(f"\n  Avg monthly IC: {rdf['ic'].mean():+.4f}")
    print(f"  IC > 0 months: {(rdf['ic']>0).sum()}/{len(rdf)}")

    # Cumulative return by recommendation
    print(f"\n  --- Cumulative Equity by Recommendation ---")
    for rec in ["StrongBuy", "Buy", "Hold"]:
        monthly_rets = []
        for m in sorted(ap["month"].unique()):
            sub = ap[(ap["month"] == m) & (ap["rec"] == rec)]
            if len(sub) > 0:
                monthly_rets.append(sub["target_20d"].mean())
        if monthly_rets:
            cum = np.prod([1 + r for r in monthly_rets])
            print(f"  {rec:>10s}: cumulative = {(cum-1)*100:+.1f}% over {len(monthly_rets)} months")


if __name__ == "__main__":
    main()
