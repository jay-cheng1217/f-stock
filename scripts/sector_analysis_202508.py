"""2025-08 產業集中度分析"""
import os, sys, warnings
import pandas as pd
import numpy as np
import lightgbm as lgb

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ml.dataset import load_single_stock, _load_twii, _list_daily_tickers
from ml.features.sector import load_sector_mapping

_sector_df = load_sector_mapping()
_SECTOR_LOOKUP = {}
if _sector_df is not None:
    _SECTOR_LOOKUP = dict(zip(_sector_df["Ticker"], _sector_df["Sector"]))

def get_sector(t):
    return _SECTOR_LOOKUP.get(t, "其他")

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
FILTER_COLS = ["operating_margin_latest", "price_vs_ma20", "Volume"]


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

    # Train for 2025-08
    test_start = pd.Timestamp("2025-08-01")
    test_end = test_start + pd.offsets.MonthEnd(0)
    train_start = test_start - pd.DateOffset(months=24)

    train = big[(big["Date"] >= train_start) & (big["Date"] < test_start)]
    test = big[(big["Date"] >= test_start) & (big["Date"] <= test_end)]
    print(f"Train: {len(train):,}, Test: {len(test):,}")

    params = {
        "objective": "regression", "metric": "rmse", "boosting_type": "gbdt", "device": "gpu",
        "num_leaves": 31, "learning_rate": 0.05, "feature_fraction": 0.7,
        "bagging_fraction": 0.7, "bagging_freq": 5, "verbose": -1, "n_jobs": -1, "seed": 42,
    }
    dtrain = lgb.Dataset(train[feature_cols].values, train["target_20d"].values,
                         feature_name=feature_cols)
    model = lgb.train(params, dtrain, num_boost_round=300)

    test_df = test[["ticker", "Date", "Close", "target_20d"]].copy()
    test_df["pred_return"] = model.predict(test[feature_cols].values)
    for fc in FILTER_COLS:
        if fc in test.columns:
            test_df[fc] = test[fc].values

    last_day = test_df.groupby("ticker").tail(1).sort_values("pred_return", ascending=False)

    # No filter Top 30
    top30_nf = last_day.head(30).copy()
    top30_nf["sector"] = top30_nf["ticker"].apply(get_sector)

    # With filter Top 30
    filtered = last_day.copy()
    if "operating_margin_latest" in filtered.columns:
        filtered = filtered[~(filtered["operating_margin_latest"] < 0)]
    if "Volume" in filtered.columns:
        filtered = filtered[filtered["Volume"] >= 500_000]
    if "price_vs_ma20" in filtered.columns:
        filtered = filtered[filtered["price_vs_ma20"].abs() <= 0.30]
    filtered = filtered[filtered["pred_return"].abs() <= 0.50]

    top30_f = filtered.head(30).copy()
    top30_f["sector"] = top30_f["ticker"].apply(get_sector)

    # === Sector concentration ===
    print()
    print("=" * 70)
    print("  2025-08 sector concentration comparison")
    print("=" * 70)

    sec_nf = top30_nf["sector"].value_counts()
    sec_f = top30_f["sector"].value_counts()
    all_sectors = sorted(set(sec_nf.index) | set(sec_f.index))

    print(f"\n{'Sector':>12} {'NoFilter':>10} {'Filtered':>10} {'Diff':>6}")
    print("-" * 45)
    for s in all_sectors:
        n1 = sec_nf.get(s, 0)
        n2 = sec_f.get(s, 0)
        p1 = n1 / 30 * 100
        p2 = n2 / 30 * 100
        print(f"{s:>12} {n1:>4} ({p1:4.0f}%) {n2:>4} ({p2:4.0f}%) {n2 - n1:>+4}")

    # HHI
    def hhi(counts, total=30):
        shares = counts / total
        return (shares ** 2).sum()

    hhi_nf = hhi(sec_nf)
    hhi_f = hhi(sec_f)
    print(f"\nHHI: NoFilter={hhi_nf:.4f}  Filtered={hhi_f:.4f}")

    max_nf = sec_nf.max()
    max_f = sec_f.max()
    print(f"Max sector: NoFilter={sec_nf.idxmax()} {max_nf}/30 ({max_nf/30*100:.0f}%)  "
          f"Filtered={sec_f.idxmax()} {max_f}/30 ({max_f/30*100:.0f}%)")

    # Sector-level returns
    print()
    print("=" * 70)
    print("  Sector-level avg 20d returns within Top 30")
    print("=" * 70)

    print(f"\n{'Sector':>12} {'NF_avg':>10} {'F_avg':>10} {'NF_n':>6} {'F_n':>6}")
    print("-" * 50)
    for s in all_sectors:
        nf_sub = top30_nf[top30_nf["sector"] == s]
        f_sub = top30_f[top30_f["sector"] == s]
        nf_ret = nf_sub["target_20d"].mean() * 100 if len(nf_sub) > 0 else float("nan")
        f_ret = f_sub["target_20d"].mean() * 100 if len(f_sub) > 0 else float("nan")
        nf_str = f"{nf_ret:+.2f}%" if not np.isnan(nf_ret) else "N/A"
        f_str = f"{f_ret:+.2f}%" if not np.isnan(f_ret) else "N/A"
        print(f"{s:>12} {nf_str:>10} {f_str:>10} {len(nf_sub):>6} {len(f_sub):>6}")

    nf_avg = top30_nf["target_20d"].mean() * 100
    f_avg = top30_f["target_20d"].mean() * 100
    print(f"{'Overall':>12} {nf_avg:+.2f}%{'':>4} {f_avg:+.2f}%")

    # Removed vs Added
    nf_set = set(top30_nf["ticker"])
    f_set = set(top30_f["ticker"])
    removed = nf_set - f_set
    added = f_set - nf_set

    print()
    print("=" * 70)
    print(f"  Removed {len(removed)} / Added {len(added)}")
    print("=" * 70)

    if removed:
        rm_df = top30_nf[top30_nf["ticker"].isin(removed)]
        print(f"Removed avg 20d: {rm_df['target_20d'].mean()*100:+.2f}%")
        for _, r in rm_df.iterrows():
            reasons = []
            op = r.get("operating_margin_latest", np.nan)
            vol = r.get("Volume", np.nan)
            bias = r.get("price_vs_ma20", np.nan)
            if not np.isnan(op) and op < 0:
                reasons.append(f"loss({op*100:.1f}%)")
            if not np.isnan(vol) and vol < 500000:
                reasons.append(f"low_vol({int(vol/1000)})")
            if not np.isnan(bias) and abs(bias) > 0.30:
                reasons.append(f"bias({bias*100:+.1f}%)")
            reason_str = ", ".join(reasons) if reasons else "replaced_by_higher_rank"
            print(f"  {r['ticker']:>6} {r['sector']:>8} 20d={r['target_20d']*100:+.1f}%  {reason_str}")

    if added:
        ad_df = top30_f[top30_f["ticker"].isin(added)]
        print(f"\nAdded avg 20d: {ad_df['target_20d'].mean()*100:+.2f}%")
        for _, r in ad_df.iterrows():
            print(f"  {r['ticker']:>6} {r['sector']:>8} 20d={r['target_20d']*100:+.1f}%")

    # Conclusion
    rm_ret = top30_nf[top30_nf["ticker"].isin(removed)]["target_20d"].mean() if removed else 0
    ad_ret = top30_f[top30_f["ticker"].isin(added)]["target_20d"].mean() if added else 0
    elec_nf = (top30_nf["sector"] == "電子").sum()
    elec_f = (top30_f["sector"] == "電子").sum()

    print()
    print("=" * 70)
    print("  CONCLUSION")
    print("=" * 70)
    print(f"Removed avg: {rm_ret*100:+.2f}%  Added avg: {ad_ret*100:+.2f}%  "
          f"Gap: {(ad_ret-rm_ret)*100:+.2f}%")
    print(f"Electronics: {elec_nf}/30 -> {elec_f}/30")
    if max_f / 30 > 0.30:
        print(f"WARNING: Max sector {sec_f.idxmax()} = {max_f}/30 ({max_f/30*100:.0f}%) > 30% limit")


if __name__ == "__main__":
    main()
