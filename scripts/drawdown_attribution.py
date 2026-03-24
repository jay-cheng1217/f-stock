"""回撤歸因分析：解剖 2025-08 最大回撤

比較無防呆 vs 有防呆的 Top 30 持倉明細、產業分佈、
找出回撤加深的真正原因。
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

# 載入產業對應表
_sector_df = load_sector_mapping()
_SECTOR_LOOKUP = {}
if _sector_df is not None:
    _SECTOR_LOOKUP = dict(zip(_sector_df["Ticker"], _sector_df["Sector"]))


def get_sector_name(ticker):
    """取得產業名稱"""
    if ticker in _SECTOR_LOOKUP:
        return _SECTOR_LOOKUP[ticker]
    return "其他"


# === Same features as train_v2_backtest.py ===
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

    # Train model for 2025-08 test window
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

    # === v1 (no filter) Top 30 ===
    top30_nf = last_day.head(30).copy()
    top30_nf["sector"] = top30_nf["ticker"].apply(get_sector_name)

    # === v2 (with filter) Top 30 ===
    filtered = last_day.copy()
    if "operating_margin_latest" in filtered.columns:
        filtered = filtered[~(filtered["operating_margin_latest"] < 0)]
    if "Volume" in filtered.columns:
        filtered = filtered[filtered["Volume"] >= 500_000]
    if "price_vs_ma20" in filtered.columns:
        filtered = filtered[filtered["price_vs_ma20"].abs() <= 0.30]
    filtered = filtered[filtered["pred_return"].abs() <= 0.50]

    top30_f = filtered.head(30).copy()
    top30_f["sector"] = top30_f["ticker"].apply(get_sector_name)

    # === Report ===
    print(f"\n{'='*80}")
    print(f"  2025-08 回撤歸因分析")
    print(f"{'='*80}")

    print(f"\n--- 無防呆 Top 30 (實際報酬: {top30_nf['target_20d'].mean()*100:+.2f}%) ---")
    print(f"{'#':>3} {'Ticker':>6} {'收盤':>8} {'預測':>8} {'實際20d':>10} {'產業':>8} "
          f"{'營利率':>8} {'成交量(張)':>10} {'MA20乖離':>10}")
    for i, (_, r) in enumerate(top30_nf.iterrows(), 1):
        op = r.get("operating_margin_latest", np.nan)
        vol = r.get("Volume", np.nan)
        bias = r.get("price_vs_ma20", np.nan)
        op_str = f"{op*100:+.1f}%" if not np.isnan(op) else "N/A"
        vol_str = f"{int(vol/1000):,}" if not np.isnan(vol) else "N/A"
        bias_str = f"{bias*100:+.1f}%" if not np.isnan(bias) else "N/A"
        flag = ""
        if not np.isnan(op) and op < 0:
            flag += " [虧損]"
        if not np.isnan(vol) and vol < 500_000:
            flag += " [低量]"
        if not np.isnan(bias) and abs(bias) > 0.30:
            flag += " [乖離]"
        print(f"{i:>3} {r['ticker']:>6} {r['Close']:>8.1f} {r['pred_return']*100:>+7.1f}% "
              f"{r['target_20d']*100:>+9.1f}% {r['sector']:>8} "
              f"{op_str:>8} {vol_str:>10} {bias_str:>10}{flag}")

    print(f"\n--- 有防呆 Top 30 (實際報酬: {top30_f['target_20d'].mean()*100:+.2f}%) ---")
    for i, (_, r) in enumerate(top30_f.iterrows(), 1):
        op = r.get("operating_margin_latest", np.nan)
        vol = r.get("Volume", np.nan)
        bias = r.get("price_vs_ma20", np.nan)
        op_str = f"{op*100:+.1f}%" if not np.isnan(op) else "N/A"
        vol_str = f"{int(vol/1000):,}" if not np.isnan(vol) else "N/A"
        bias_str = f"{bias*100:+.1f}%" if not np.isnan(bias) else "N/A"
        print(f"{i:>3} {r['ticker']:>6} {r['Close']:>8.1f} {r['pred_return']*100:>+7.1f}% "
              f"{r['target_20d']*100:>+9.1f}% {r['sector']:>8} "
              f"{op_str:>8} {vol_str:>10} {bias_str:>10}")

    # === 產業集中度分析 ===
    print(f"\n{'='*80}")
    print(f"  產業集中度對比")
    print(f"{'='*80}")

    sec_nf = top30_nf["sector"].value_counts()
    sec_f = top30_f["sector"].value_counts()

    all_sectors = sorted(set(sec_nf.index) | set(sec_f.index))
    print(f"\n{'產業':>10} {'無防呆':>8} {'有防呆':>8} {'差異':>8}")
    print(f"{'─'*40}")
    for s in all_sectors:
        n1 = sec_nf.get(s, 0)
        n2 = sec_f.get(s, 0)
        pct1 = n1 / 30 * 100
        pct2 = n2 / 30 * 100
        bar = "▓" * int(pct2 / 5)
        print(f"{s:>10} {n1:>4} ({pct1:>4.0f}%) {n2:>4} ({pct2:>4.0f}%) {n2-n1:>+4} {bar}")

    # === 被過濾掉的股票分析 ===
    nf_tickers = set(top30_nf["ticker"])
    f_tickers = set(top30_f["ticker"])
    removed = nf_tickers - f_tickers
    added = f_tickers - nf_tickers

    if removed:
        print(f"\n{'='*80}")
        print(f"  被防呆過濾掉的股票（從 Top30 移除）")
        print(f"{'='*80}")
        removed_df = top30_nf[top30_nf["ticker"].isin(removed)]
        for _, r in removed_df.iterrows():
            op = r.get("operating_margin_latest", np.nan)
            vol = r.get("Volume", np.nan)
            bias = r.get("price_vs_ma20", np.nan)
            reasons = []
            if not np.isnan(op) and op < 0:
                reasons.append(f"本業虧損({op*100:.1f}%)")
            if not np.isnan(vol) and vol < 500_000:
                reasons.append(f"低流動性({int(vol/1000)}張)")
            if not np.isnan(bias) and abs(bias) > 0.30:
                reasons.append(f"乖離過大({bias*100:+.1f}%)")
            reason_str = "、".join(reasons) if reasons else "被排名較高的替換"
            print(f"  {r['ticker']:>6} {r['sector']:>8} 實際20d={r['target_20d']*100:+.1f}%  "
                  f"原因: {reason_str}")

    if added:
        print(f"\n  遞補進來的股票:")
        added_df = top30_f[top30_f["ticker"].isin(added)]
        for _, r in added_df.iterrows():
            print(f"  {r['ticker']:>6} {r['sector']:>8} 實際20d={r['target_20d']*100:+.1f}%")

    # === 關鍵數據 ===
    print(f"\n{'='*80}")
    print(f"  關鍵數據摘要")
    print(f"{'='*80}")

    # 被過濾掉股票的平均表現
    if removed:
        removed_ret = removed_df["target_20d"].mean()
        print(f"  被移除股票平均 20d 報酬: {removed_ret*100:+.2f}%")
    if added:
        added_ret = added_df["target_20d"].mean()
        print(f"  遞補股票平均 20d 報酬:   {added_ret*100:+.2f}%")

    # v1 中低流動性股票的表現
    low_vol_nf = top30_nf[top30_nf.get("Volume", pd.Series(dtype=float)) < 500_000]
    if len(low_vol_nf) > 0:
        print(f"\n  v1 Top30 中低流動性股票: {len(low_vol_nf)} 檔")
        print(f"  低流動性股票平均 20d: {low_vol_nf['target_20d'].mean()*100:+.2f}%")
        print(f"  高流動性股票平均 20d: {top30_nf[~top30_nf.index.isin(low_vol_nf.index)]['target_20d'].mean()*100:+.2f}%")

    # v1 中本業虧損股票的表現
    loss_nf = top30_nf[top30_nf.get("operating_margin_latest", pd.Series(dtype=float)) < 0]
    if len(loss_nf) > 0:
        print(f"\n  v1 Top30 中本業虧損股票: {len(loss_nf)} 檔")
        print(f"  虧損股平均 20d: {loss_nf['target_20d'].mean()*100:+.2f}%")
        print(f"  獲利股平均 20d: {top30_nf[~top30_nf.index.isin(loss_nf.index)]['target_20d'].mean()*100:+.2f}%")

    # 電子股集中度
    elec_nf = (top30_nf["sector"] == "電子").sum()
    elec_f = (top30_f["sector"] == "電子").sum()
    print(f"\n  電子股佔比: 無防呆 {elec_nf}/30 ({elec_nf/30*100:.0f}%) → "
          f"有防呆 {elec_f}/30 ({elec_f/30*100:.0f}%)")

    if elec_f / 30 > 0.50:
        print(f"  ⚠️ 電子股佔比超過 50%，存在嚴重的產業集中風險！")


if __name__ == "__main__":
    main()
