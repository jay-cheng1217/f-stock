"""v2 模型：20天報酬迴歸 + 因子篩選 + walk-forward 回測

一個腳本完成：訓練 → 回測 → 看結果。
"""
import os, sys, warnings, json
import pandas as pd
import numpy as np
import lightgbm as lgb
from datetime import datetime

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ml.config import DAILY_K_DIR, MODEL_DIR
from ml.dataset import load_single_stock, _load_twii, _list_daily_tickers


# === 有效因子 (基於 factor_research.py 驗證) ===
# IC > 0.02 的因子 + 已知有效的技術/籌碼因子
SELECTED_FEATURES = [
    # 均線偏離 (IC 最強)
    "price_vs_ma5", "price_vs_ma10", "price_vs_ma20", "price_vs_ma60",
    # 動量
    "return_5d", "return_10d", "return_20d", "return_60d",
    "momentum_accel", "roc_5", "roc_10", "roc_20",
    # 波動率
    "volatility_5d", "volatility_20d", "bb_width", "bb_position",
    "atr_14", "atr_pct", "vol_contraction",
    # RSI / 超買超賣
    "rsi_6", "rsi_14", "williams_r_14", "mfi_14", "stoch_rsi_k",
    # 成交量
    "vol_ratio_5_20", "vol_zscore", "obv_slope_20", "cmf_20",
    # 籌碼 (原始 + 正規化，含 1d/3d 短天期)
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
    "margin_change_5d", "margin_short_ratio",
    "turnover_rate",
    # 籌碼背離
    "chip_diverge_bear", "chip_diverge_bull",
    # 價格型態
    "dist_to_high_20d", "dist_to_low_20d",
    "dist_to_high_60d", "dist_to_low_60d",
    "position_52w", "up_day_ratio_20",
    # 均線排列
    "ma_slope_5", "ma_slope_20", "ma_bullish_align",
    # 趨勢
    "adx_14", "di_diff", "trend_direction", "lr_slope_20", "lr_r2_20",
    # MACD/KD
    "macd_hist", "kd_k", "kd_d",
    # 營收基本面
    "revenue_yoy_latest", "revenue_yoy_3m_avg", "revenue_yoy_momentum",
    "gross_margin_latest", "operating_margin_latest", "net_margin_latest",
    "margin_trend",
    # 估值
    "pe_ratio", "pb_ratio", "dividend_yield", "pe_percentile_60d",
    # EPS
    "eps_ttm", "eps_yoy", "eps_momentum",
    # 大盤
    "twii_return_5d", "twii_return_20d",
    # 產業
    "sector_relative_return_5d", "sector_relative_return_20d",
    "sector_momentum_20d", "sector_breadth",
    # 集保
    "whale_pct", "whale_pct_chg", "retail_pct_chg",
    # 進場因子 v2
    "price_vs_inst_cost", "vol_contraction_ratio",
    "inst_buy_ratio_20d", "mom_20d",
]
# 去重（保持順序）
SELECTED_FEATURES = list(dict.fromkeys(SELECTED_FEATURES))


def compute_20d_return(df):
    """計算 20 日前瞻報酬 (不做超額，直接用絕對報酬)"""
    df = df.copy()
    df["target_20d"] = df["Close"].shift(-20) / df["Close"] - 1
    return df


def main():
    from ml.dataset import load_raw_cache

    # 嘗試使用 v1 訓練時產生的快取（省去逐檔載入 + 特徵計算 ~40 min）
    cached = load_raw_cache(verbose=True)
    if cached is not None:
        print(f"Using cached dataset: {len(cached):,} rows, {cached['ticker'].nunique()} stocks")
        big = cached[["Date", "Close", "ticker"] +
                      [c for c in SELECTED_FEATURES if c in cached.columns]].copy()
        # 必須按 ticker 分組計算 20d 報酬，否則 shift(-20) 會跨不同股票
        big = big.sort_values(["ticker", "Date"]).reset_index(drop=True)
        big = big.groupby("ticker", group_keys=False).apply(compute_20d_return)
        big = big.dropna(subset=["target_20d"])
        big = big.sort_values("Date").reset_index(drop=True)
    else:
        print("No cache found, loading stocks individually...")
        twii = _load_twii()
        tickers = _list_daily_tickers()
        print(f"Loading {len(tickers)} stocks...")

        all_data = []
        for t in tickers:
            df = load_single_stock(t, twii_df=twii)
            if df is None or len(df) < 120:
                continue
            df = compute_20d_return(df)
            available_cols = [c for c in SELECTED_FEATURES if c in df.columns]
            keep = ["Date", "Close", "target_20d"] + available_cols
            sub = df[keep].copy()
            sub["ticker"] = t
            sub = sub.dropna(subset=["target_20d"])
            if len(sub) > 60:
                all_data.append(sub)

        print(f"Loaded {len(all_data)} stocks")
        big = pd.concat(all_data, ignore_index=True)
        big = big.sort_values("Date").reset_index(drop=True)

    print(f"Total samples: {len(big):,}, Date range: {big['Date'].min()} ~ {big['Date'].max()}")

    # 確定實際可用的特徵欄位
    feature_cols = [c for c in SELECTED_FEATURES if c in big.columns]
    print(f"Features available: {len(feature_cols)}")

    # === Walk-forward 回測 ===
    # 每 3 個月：用前 24 個月訓練，預測下 1 個月
    test_starts = pd.date_range("2024-01-01", "2026-02-01", freq="MS")

    monthly_results = []
    total_months = len(test_starts)
    for idx, test_start in enumerate(test_starts, 1):
        pct = idx / total_months * 100
        print(f"\r[{'█' * int(pct // 4):<25s}] {pct:5.1f}% ({idx}/{total_months}) "
              f"Training {test_start.strftime('%Y-%m')}...", end="", flush=True)

        test_end = test_start + pd.offsets.MonthEnd(0)
        train_start = test_start - pd.DateOffset(months=24)

        train = big[(big["Date"] >= train_start) & (big["Date"] < test_start)]
        test = big[(big["Date"] >= test_start) & (big["Date"] <= test_end)]

        if len(train) < 10000 or len(test) < 100:
            continue

        X_train = train[feature_cols].values
        y_train = train["target_20d"].values
        X_test = test[feature_cols].values

        # LightGBM 迴歸
        dtrain = lgb.Dataset(X_train, y_train, feature_name=feature_cols)
        params = {
            "objective": "regression",
            "metric": "rmse",
            "boosting_type": "gbdt",
            "device": "gpu",
            "num_leaves": 31,
            "learning_rate": 0.05,
            "feature_fraction": 0.7,
            "bagging_fraction": 0.7,
            "bagging_freq": 5,
            "verbose": -1,
            "n_jobs": -1,
            "seed": 42,
        }
        model = lgb.train(params, dtrain, num_boost_round=300)

        # 預測
        pred_return = model.predict(X_test)
        test_df = test[["ticker", "Date", "Close", "target_20d"]].copy()
        test_df["pred_return"] = pred_return

        # 每天取最後一筆（避免同一天重複）
        # 用月底的預測做排名
        last_day = test_df.groupby("ticker").tail(1)
        last_day = last_day.sort_values("pred_return", ascending=False)

        n = len(last_day)
        top30 = last_day.head(30)
        bot30 = last_day.tail(30)

        ic = last_day["pred_return"].corr(last_day["target_20d"], method="spearman")
        top_ret = top30["target_20d"].mean()
        bot_ret = bot30["target_20d"].mean()
        all_ret = last_day["target_20d"].mean()
        top_win = (top30["target_20d"] > 0).mean()

        monthly_results.append({
            "month": test_start.strftime("%Y-%m"),
            "n": n,
            "top30": top_ret,
            "bot30": bot_ret,
            "all": all_ret,
            "spread": top_ret - bot_ret,
            "top_win": top_win,
            "ic": ic,
        })

        print(f"\r[{'█' * int(pct // 4):<25s}] {pct:5.1f}% "
              f"{test_start.strftime('%Y-%m')}  N={n:>4d}  "
              f"Top30={top_ret*100:+.2f}%  Bot30={bot_ret*100:+.2f}%  "
              f"Spread={(top_ret-bot_ret)*100:+.2f}%  "
              f"WinRate={top_win*100:.0f}%  IC={ic:+.3f}")

    # === Summary ===
    rdf = pd.DataFrame(monthly_results)
    print("\n" + "=" * 75)
    print("  v2 Model Backtest (20d regression, walk-forward)")
    print("=" * 75)
    print(f"  Months: {len(rdf)}")
    print(f"  Top 30 avg 20d return:  {rdf['top30'].mean()*100:+.3f}%")
    print(f"  Bot 30 avg 20d return:  {rdf['bot30'].mean()*100:+.3f}%")
    print(f"  All stocks avg:         {rdf['all'].mean()*100:+.3f}%")
    print(f"  Top-Bot spread:         {rdf['spread'].mean()*100:+.3f}%")
    print(f"  Top 30 win rate:        {rdf['top_win'].mean()*100:.1f}%")
    print(f"  Avg IC:                 {rdf['ic'].mean():+.4f}")
    print(f"  IC > 0 months:          {(rdf['ic']>0).sum()}/{len(rdf)} ({(rdf['ic']>0).mean()*100:.0f}%)")
    print(f"  Spread > 0 months:      {(rdf['spread']>0).sum()}/{len(rdf)} ({(rdf['spread']>0).mean()*100:.0f}%)")

    # 累計報酬 (每月等權重買 Top 30 持有 20 天)
    cum_ret = (1 + rdf["top30"]).cumprod()
    cum_all = (1 + rdf["all"]).cumprod()
    print(f"\n  Cumulative (Top 30):    {(cum_ret.iloc[-1]-1)*100:+.1f}%")
    print(f"  Cumulative (All):       {(cum_all.iloc[-1]-1)*100:+.1f}%")
    print(f"  Alpha:                  {(cum_ret.iloc[-1]-cum_all.iloc[-1])*100:+.1f}%")

    # 存最後一次的模型供生產使用
    if len(rdf) > 0:
        # 用全部資料重新訓練最終模型
        X_all = big[feature_cols].values
        y_all = big["target_20d"].values
        dtrain = lgb.Dataset(X_all, y_all, feature_name=feature_cols)
        final_model = lgb.train(params, dtrain, num_boost_round=300)

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        model_path = os.path.join(MODEL_DIR, f"lgbm_v2_{ts}.txt")
        final_model.save_model(model_path)

        meta = {
            "model_file": model_path,
            "model_version": "v2_regression_20d",
            "trained_at": ts,
            "feature_columns": feature_cols,
            "n_features": len(feature_cols),
            "n_stocks": big["ticker"].nunique(),
            "n_samples": len(big),
            "target": "20d_absolute_return",
            "date_range": [str(big["Date"].min().date()), str(big["Date"].max().date())],
            "backtest_months": len(rdf),
            "backtest_avg_spread": float(rdf["spread"].mean()),
            "backtest_avg_ic": float(rdf["ic"].mean()),
            "backtest_top30_win_rate": float(rdf["top_win"].mean()),
            "backtest_top30_avg_return": float(rdf["top30"].mean()),
        }
        meta_path = model_path.replace(".txt", "_meta.json")
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2, ensure_ascii=False)
        print(f"\n  Final model saved: {model_path}")


if __name__ == "__main__":
    main()
