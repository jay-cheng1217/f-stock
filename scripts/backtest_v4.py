"""v2 回測 D 版：舊防呆全保留 + 產業集中度上限 30%

對比：
B) 舊防呆（虧損+流動性+乖離+極端 全硬擋）
D) 舊防呆 + 產業上限 30%（從超限產業排名最低的替換）
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

_sector_df = load_sector_mapping()
_SECTOR_LOOKUP = {}
if _sector_df is not None:
    _SECTOR_LOOKUP = dict(zip(_sector_df["Ticker"], _sector_df["Sector"]))

SECTOR_CAP_RATIO = 0.30

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


def apply_old_filter(df):
    """舊版防呆：虧損+流動性+乖離+極端 全硬擋"""
    filtered = df.copy()
    if "operating_margin_latest" in filtered.columns:
        filtered = filtered[~(filtered["operating_margin_latest"] < 0)]
    if "Volume" in filtered.columns:
        filtered = filtered[filtered["Volume"] >= 500_000]
    if "price_vs_ma20" in filtered.columns:
        filtered = filtered[filtered["price_vs_ma20"].abs() <= 0.30]
    filtered = filtered[filtered["pred_return"].abs() <= 0.50]
    return filtered


def main():
    print("Loading data...", flush=True)
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

    print(f"Loaded {len(all_data)} stocks", flush=True)
    big = pd.concat(all_data, ignore_index=True).sort_values("Date").reset_index(drop=True)
    feature_cols = [c for c in SELECTED_FEATURES if c in big.columns]
    print(f"Total: {len(big):,}, Features: {len(feature_cols)}", flush=True)

    test_starts = pd.date_range("2024-01-01", "2026-02-01", freq="MS")
    total = len(test_starts)

    params = {
        "objective": "regression", "metric": "rmse", "boosting_type": "gbdt", "device": "gpu",
        "num_leaves": 31, "learning_rate": 0.05, "feature_fraction": 0.7,
        "bagging_fraction": 0.7, "bagging_freq": 5, "verbose": -1, "n_jobs": -1, "seed": 42,
    }

    results_b = []  # B: old filter
    results_d = []  # D: old filter + sector cap

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

        # === B: Old filter only ===
        old_f = apply_old_filter(last_day)
        top30_b = old_f.head(30)
        ret_b = top30_b["target_20d"].mean() if len(top30_b) > 0 else 0
        win_b = (top30_b["target_20d"] > 0).mean() if len(top30_b) > 0 else 0

        # === D: Old filter + sector cap ===
        top30_d = select_with_sector_cap(old_f, top_n=30)
        ret_d = top30_d["target_20d"].mean() if len(top30_d) > 0 else 0
        win_d = (top30_d["target_20d"] > 0).mean() if len(top30_d) > 0 else 0

        # HHI for this month
        sec_b = pd.Series([_SECTOR_LOOKUP.get(t, "其他") for t in top30_b["ticker"]]).value_counts()
        sec_d = pd.Series([_SECTOR_LOOKUP.get(t, "其他") for t in top30_d["ticker"]]).value_counts()
        hhi_b = ((sec_b / 30) ** 2).sum() if len(top30_b) == 30 else 0
        hhi_d = ((sec_d / len(top30_d)) ** 2).sum() if len(top30_d) > 0 else 0

        results_b.append({
            "month": test_start.strftime("%Y-%m"),
            "top30": ret_b, "top_win": win_b, "hhi": hhi_b,
        })
        results_d.append({
            "month": test_start.strftime("%Y-%m"),
            "top30": ret_d, "top_win": win_d, "hhi": hhi_d,
        })

        bar = "=" * int(pct // 4)
        print(f"  [{bar:<25s}] {pct:5.1f}% {test_start.strftime('%Y-%m')}  "
              f"B={ret_b*100:+.1f}%  D={ret_d*100:+.1f}%  "
              f"HHI: {hhi_b:.3f}->{hhi_d:.3f}", flush=True)

    # === Summary ===
    b = pd.DataFrame(results_b)
    d = pd.DataFrame(results_d)

    cum_b = (1 + b["top30"]).cumprod()
    cum_d = (1 + d["top30"]).cumprod()

    mdd_b = max_drawdown(cum_b)
    mdd_d = max_drawdown(cum_d)

    sharpe_b = b["top30"].mean() / b["top30"].std() * np.sqrt(12) if b["top30"].std() > 0 else 0
    sharpe_d = d["top30"].mean() / d["top30"].std() * np.sqrt(12) if d["top30"].std() > 0 else 0

    sortino_b = sortino_ratio(b["top30"])
    sortino_d = sortino_ratio(d["top30"])

    ann_b = b["top30"].mean() * 12
    ann_d = d["top30"].mean() * 12
    calmar_b = ann_b / abs(mdd_b) if mdd_b != 0 else 0
    calmar_d = ann_d / abs(mdd_d) if mdd_d != 0 else 0

    print(f"\n{'='*70}", flush=True)
    print(f"  B vs D: Old Filter vs Old Filter + Sector Cap ({len(b)} months)", flush=True)
    print(f"{'='*70}", flush=True)
    print(f"{'':25s} {'B(old)':>12s} {'D(old+cap)':>12s} {'diff':>10s}", flush=True)
    print(f"{'-'*60}", flush=True)
    print(f"{'Avg monthly return':25s} {b['top30'].mean()*100:>+11.2f}% {d['top30'].mean()*100:>+11.2f}% {(d['top30'].mean()-b['top30'].mean())*100:>+9.2f}%", flush=True)
    print(f"{'Win rate':25s} {b['top_win'].mean()*100:>11.1f}% {d['top_win'].mean()*100:>11.1f}% {(d['top_win'].mean()-b['top_win'].mean())*100:>+9.1f}%", flush=True)
    print(f"{'Cumulative return':25s} {(cum_b.iloc[-1]-1)*100:>+11.1f}% {(cum_d.iloc[-1]-1)*100:>+11.1f}%", flush=True)
    print(f"{'Max Drawdown':25s} {mdd_b*100:>11.1f}% {mdd_d*100:>11.1f}% {(mdd_d-mdd_b)*100:>+9.1f}%", flush=True)
    print(f"{'Sharpe Ratio':25s} {sharpe_b:>11.3f} {sharpe_d:>11.3f} {sharpe_d-sharpe_b:>+9.3f}", flush=True)
    print(f"{'Sortino Ratio':25s} {sortino_b:>11.3f} {sortino_d:>11.3f} {sortino_d-sortino_b:>+9.3f}", flush=True)
    print(f"{'Calmar Ratio':25s} {calmar_b:>11.3f} {calmar_d:>11.3f} {calmar_d-calmar_b:>+9.3f}", flush=True)
    print(f"{'Avg HHI':25s} {b['hhi'].mean():>11.4f} {d['hhi'].mean():>11.4f} {d['hhi'].mean()-b['hhi'].mean():>+9.4f}", flush=True)

    print(f"\n{'='*70}", flush=True)
    print(f"  Monthly detail", flush=True)
    print(f"{'='*70}", flush=True)
    print(f"{'Month':>8s} {'B(old)':>10s} {'D(+cap)':>10s} {'D-B':>8s} {'HHI_B':>8s} {'HHI_D':>8s}", flush=True)
    print(f"{'-'*55}", flush=True)
    for i in range(len(b)):
        diff = (d.iloc[i]["top30"] - b.iloc[i]["top30"]) * 100
        sign = "+" if diff > 0 else ""
        print(f"  {b.iloc[i]['month']:>6s} {b.iloc[i]['top30']*100:>+9.1f}% "
              f"{d.iloc[i]['top30']*100:>+9.1f}% "
              f"{sign}{diff:>6.1f}% "
              f"{b.iloc[i]['hhi']:>7.3f} {d.iloc[i]['hhi']:>7.3f}", flush=True)

    d_wins = sum(1 for i in range(len(d)) if d.iloc[i]["top30"] > b.iloc[i]["top30"])
    print(f"\n  D beats B: {d_wins}/{len(d)} months ({d_wins/len(d)*100:.0f}%)", flush=True)

    # Worst months comparison
    print(f"\n  Worst 5 months:", flush=True)
    worst_b = b.nsmallest(5, "top30")
    for _, row in worst_b.iterrows():
        d_row = d[d["month"] == row["month"]].iloc[0]
        diff = (d_row["top30"] - row["top30"]) * 100
        print(f"    {row['month']}  B={row['top30']*100:+.1f}%  D={d_row['top30']*100:+.1f}%  diff={diff:+.1f}%", flush=True)


if __name__ == "__main__":
    main()
