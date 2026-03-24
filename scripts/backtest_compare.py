"""v2 模型回測比較：三版本對比

A) 原始版 (No Filter) — 純模型排名 Top 30
B) 舊版防呆 (Old Filter) — 虧損+流動性+乖離+極端全部硬擋
C) 新版組合防呆 (New) — 僅硬擋流動性/暴跌/極端 + 產業上限 30%

驗證新版是否救回 Sortino Ratio 和最大回撤。
"""
import os, sys, warnings
import pandas as pd
import numpy as np
import lightgbm as lgb

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ml.config import DAILY_K_DIR, MODEL_DIR
from ml.dataset import load_single_stock, _load_twii, _list_daily_tickers
from ml.features.sector import load_sector_mapping

# === 產業對應表 ===
_sector_df = load_sector_mapping()
_SECTOR_LOOKUP = {}
if _sector_df is not None:
    _SECTOR_LOOKUP = dict(zip(_sector_df["Ticker"], _sector_df["Sector"]))

SECTOR_CAP_RATIO = 0.30

# === Features (same as train_v2_backtest.py) ===
SELECTED_FEATURES = [
    "price_vs_ma5", "price_vs_ma10", "price_vs_ma20", "price_vs_ma60",
    "return_5d", "return_10d", "return_20d", "return_60d",
    "momentum_accel", "roc_5", "roc_10", "roc_20",
    "volatility_5d", "volatility_20d", "bb_width", "bb_position",
    "atr_14", "atr_pct", "vol_contraction",
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
SELECTED_FEATURES = list(dict.fromkeys(SELECTED_FEATURES))
FILTER_COLS = ["operating_margin_latest", "price_vs_ma20", "Volume", "return_20d"]


def max_drawdown(cum_series):
    peak = cum_series.expanding().max()
    dd = (cum_series - peak) / peak
    return dd.min()


def sortino_ratio(returns):
    mean_r = returns.mean()
    downside = returns[returns < 0].std()
    return mean_r / downside * np.sqrt(12) if downside > 0 else 0


def select_with_sector_cap(ranked_df, top_n=30, cap_ratio=SECTOR_CAP_RATIO):
    """產業集中度上限選股"""
    max_per_sector = max(1, int(top_n * cap_ratio))
    selected = []
    sector_count = {}
    for _, row in ranked_df.iterrows():
        sector = _SECTOR_LOOKUP.get(row["ticker"], "其他")
        current = sector_count.get(sector, 0)
        if current < max_per_sector:
            selected.append(row)
            sector_count[sector] = current + 1
        if len(selected) >= top_n:
            break
    return pd.DataFrame(selected)


def main():
    print("Loading data...")
    twii = _load_twii()
    tickers = _list_daily_tickers()

    all_data = []
    for t in tickers:
        df = load_single_stock(t, twii_df=twii)
        if df is None or len(df) < 120:
            continue
        df = df.copy()
        df["target_20d"] = df["Close"].shift(-20) / df["Close"] - 1
        available_cols = [c for c in SELECTED_FEATURES if c in df.columns]
        keep = ["Date", "Close", "target_20d"] + available_cols
        for fc in FILTER_COLS:
            if fc in df.columns and fc not in keep:
                keep.append(fc)
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
    total = len(test_starts)

    params = {
        "objective": "regression", "metric": "rmse", "boosting_type": "gbdt", "device": "gpu",
        "num_leaves": 31, "learning_rate": 0.05, "feature_fraction": 0.7,
        "bagging_fraction": 0.7, "bagging_freq": 5, "verbose": -1, "n_jobs": -1, "seed": 42,
    }

    results_a = []  # A: No filter
    results_b = []  # B: Old filter (all hard)
    results_c = []  # C: New (hard block only + sector cap)

    for idx, test_start in enumerate(test_starts, 1):
        pct = idx / total * 100
        test_end = test_start + pd.offsets.MonthEnd(0)
        train_start = test_start - pd.DateOffset(months=24)

        train = big[(big["Date"] >= train_start) & (big["Date"] < test_start)]
        test = big[(big["Date"] >= test_start) & (big["Date"] <= test_end)]

        if len(train) < 10000 or len(test) < 100:
            continue

        dtrain = lgb.Dataset(train[feature_cols].values, train["target_20d"].values,
                             feature_name=feature_cols)
        model = lgb.train(params, dtrain, num_boost_round=300)

        test_df = test[["ticker", "Date", "Close", "target_20d"]].copy()
        test_df["pred_return"] = model.predict(test[feature_cols].values)
        for fc in FILTER_COLS:
            if fc in test.columns:
                test_df[fc] = test[fc].values

        last_day = test_df.groupby("ticker").tail(1).sort_values("pred_return", ascending=False)

        # === A: No filter — 純排名 Top 30 ===
        top30_a = last_day.head(30)
        ret_a = top30_a["target_20d"].mean()
        win_a = (top30_a["target_20d"] > 0).mean()
        ic = last_day["pred_return"].corr(last_day["target_20d"], method="spearman")

        # === B: Old filter — 全部硬擋 ===
        old_filtered = last_day.copy()
        if "operating_margin_latest" in old_filtered.columns:
            old_filtered = old_filtered[~(old_filtered["operating_margin_latest"] < 0)]
        if "Volume" in old_filtered.columns:
            old_filtered = old_filtered[old_filtered["Volume"] >= 500_000]
        if "price_vs_ma20" in old_filtered.columns:
            old_filtered = old_filtered[old_filtered["price_vs_ma20"].abs() <= 0.30]
        old_filtered = old_filtered[old_filtered["pred_return"].abs() <= 0.50]

        top30_b = old_filtered.head(30)
        ret_b = top30_b["target_20d"].mean() if len(top30_b) > 0 else 0
        win_b = (top30_b["target_20d"] > 0).mean() if len(top30_b) > 0 else 0

        # === C: New — 硬擋(流動性+暴跌+極端) + 產業上限 30% ===
        new_filtered = last_day.copy()
        # 硬擋 1: 流動性
        if "Volume" in new_filtered.columns:
            new_filtered = new_filtered[new_filtered["Volume"] >= 500_000]
        # 硬擋 2: 異常暴跌
        if "return_20d" in new_filtered.columns:
            new_filtered = new_filtered[~(new_filtered["return_20d"] < -0.40)]
        # 硬擋 3: 極端預測值
        new_filtered = new_filtered[new_filtered["pred_return"].abs() <= 0.50]
        # 不再硬擋: operating_margin、price_vs_ma20

        # 產業集中度上限
        top30_c = select_with_sector_cap(new_filtered, top_n=30)
        ret_c = top30_c["target_20d"].mean() if len(top30_c) > 0 else 0
        win_c = (top30_c["target_20d"] > 0).mean() if len(top30_c) > 0 else 0

        results_a.append({
            "month": test_start.strftime("%Y-%m"),
            "top30": ret_a, "top_win": win_a, "ic": ic,
        })
        results_b.append({
            "month": test_start.strftime("%Y-%m"),
            "top30": ret_b, "top_win": win_b,
        })
        results_c.append({
            "month": test_start.strftime("%Y-%m"),
            "top30": ret_c, "top_win": win_c,
        })

        bar = "=" * int(pct // 4)
        print(f"  [{bar:<25s}] {pct:5.1f}% {test_start.strftime('%Y-%m')}  "
              f"A={ret_a*100:+.1f}%  B={ret_b*100:+.1f}%  C={ret_c*100:+.1f}%  "
              f"Win: {win_a*100:.0f}%/{win_b*100:.0f}%/{win_c*100:.0f}%")

    # === Summary ===
    a = pd.DataFrame(results_a)
    b = pd.DataFrame(results_b)
    c = pd.DataFrame(results_c)

    cum_a = (1 + a["top30"]).cumprod()
    cum_b = (1 + b["top30"]).cumprod()
    cum_c = (1 + c["top30"]).cumprod()

    mdd_a = max_drawdown(cum_a)
    mdd_b = max_drawdown(cum_b)
    mdd_c = max_drawdown(cum_c)

    sharpe_a = a["top30"].mean() / a["top30"].std() * np.sqrt(12) if a["top30"].std() > 0 else 0
    sharpe_b = b["top30"].mean() / b["top30"].std() * np.sqrt(12) if b["top30"].std() > 0 else 0
    sharpe_c = c["top30"].mean() / c["top30"].std() * np.sqrt(12) if c["top30"].std() > 0 else 0

    sortino_a = sortino_ratio(a["top30"])
    sortino_b = sortino_ratio(b["top30"])
    sortino_c = sortino_ratio(c["top30"])

    # calmar ratio = annualized return / |max drawdown|
    ann_a = a["top30"].mean() * 12
    ann_b = b["top30"].mean() * 12
    ann_c = c["top30"].mean() * 12
    calmar_a = ann_a / abs(mdd_a) if mdd_a != 0 else 0
    calmar_b = ann_b / abs(mdd_b) if mdd_b != 0 else 0
    calmar_c = ann_c / abs(mdd_c) if mdd_c != 0 else 0

    print(f"\n{'='*80}")
    print(f"  v2 三版本回測比較 ({len(a)} months)")
    print(f"  A=No Filter  B=Old Filter(all hard)  C=New(hard+sector cap)")
    print(f"{'='*80}")
    print(f"{'':25s} {'A(原始)':>12s} {'B(舊防呆)':>12s} {'C(新組合)':>12s}")
    print(f"{'─'*65}")
    print(f"{'Top30 avg monthly':25s} {a['top30'].mean()*100:>+11.2f}% {b['top30'].mean()*100:>+11.2f}% {c['top30'].mean()*100:>+11.2f}%")
    print(f"{'Top30 win rate':25s} {a['top_win'].mean()*100:>11.1f}% {b['top_win'].mean()*100:>11.1f}% {c['top_win'].mean()*100:>11.1f}%")
    print(f"{'Cumulative return':25s} {(cum_a.iloc[-1]-1)*100:>+11.1f}% {(cum_b.iloc[-1]-1)*100:>+11.1f}% {(cum_c.iloc[-1]-1)*100:>+11.1f}%")
    print(f"{'Max Drawdown (MDD)':25s} {mdd_a*100:>11.1f}% {mdd_b*100:>11.1f}% {mdd_c*100:>11.1f}%")
    print(f"{'Sharpe Ratio':25s} {sharpe_a:>11.3f} {sharpe_b:>11.3f} {sharpe_c:>11.3f}")
    print(f"{'Sortino Ratio':25s} {sortino_a:>11.3f} {sortino_b:>11.3f} {sortino_c:>11.3f}")
    print(f"{'Calmar Ratio':25s} {calmar_a:>11.3f} {calmar_b:>11.3f} {calmar_c:>11.3f}")
    print(f"{'Avg IC (Spearman)':25s} {a['ic'].mean():>+11.4f}")
    print(f"{'IC > 0 months':25s} {(a['ic']>0).sum()}/{len(a)} ({(a['ic']>0).mean()*100:.0f}%)")

    # Highlight improvements
    print(f"\n{'='*80}")
    print(f"  C vs B improvement (new vs old filter)")
    print(f"{'='*80}")
    print(f"  MDD:     {mdd_b*100:.1f}% -> {mdd_c*100:.1f}% ({(mdd_c-mdd_b)*100:+.1f}%)")
    print(f"  Sortino: {sortino_b:.3f} -> {sortino_c:.3f} ({sortino_c-sortino_b:+.3f})")
    print(f"  Sharpe:  {sharpe_b:.3f} -> {sharpe_c:.3f} ({sharpe_c-sharpe_b:+.3f})")
    print(f"  Return:  {(cum_b.iloc[-1]-1)*100:+.1f}% -> {(cum_c.iloc[-1]-1)*100:+.1f}%")

    # Monthly details
    print(f"\n{'='*80}")
    print(f"  Monthly detail")
    print(f"{'='*80}")
    print(f"{'Month':>8s} {'A(raw)':>10s} {'B(old)':>10s} {'C(new)':>10s} {'C-B':>8s} {'WinA':>6s} {'WinB':>6s} {'WinC':>6s}")
    print(f"{'─'*70}")
    for i in range(len(a)):
        diff_cb = (c.iloc[i]["top30"] - b.iloc[i]["top30"]) * 100
        diff_color = "+" if diff_cb > 0 else ""
        print(f"  {a.iloc[i]['month']:>6s} {a.iloc[i]['top30']*100:>+9.1f}% "
              f"{b.iloc[i]['top30']*100:>+9.1f}% "
              f"{c.iloc[i]['top30']*100:>+9.1f}% "
              f"{diff_color}{diff_cb:>6.1f}% "
              f"{a.iloc[i]['top_win']*100:>5.0f}% "
              f"{b.iloc[i]['top_win']*100:>5.0f}% "
              f"{c.iloc[i]['top_win']*100:>5.0f}%")

    # C wins vs B
    c_wins = sum(1 for i in range(len(c)) if c.iloc[i]["top30"] > b.iloc[i]["top30"])
    print(f"\n  C beats B: {c_wins}/{len(c)} months ({c_wins/len(c)*100:.0f}%)")


if __name__ == "__main__":
    main()
