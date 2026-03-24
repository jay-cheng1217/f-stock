"""ML 模型回測：按 up_prob 排名買入，實際報酬如何？

用 walk-forward 方式：每月用模型預測，買入 top N，持有到下月。
這才是真正的 out-of-sample 回測。
"""
import os, sys, json, warnings
import pandas as pd
import numpy as np
import lightgbm as lgb

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ml.config import MODEL_DIR, DAILY_K_DIR, TARGET_CLASSES, FORWARD_DAYS
from ml.dataset import load_single_stock, _load_twii, _list_daily_tickers


def main():
    # 載入模型
    meta_files = sorted(
        f for f in os.listdir(MODEL_DIR) if f.endswith("_meta.json")
    )
    meta_path = os.path.join(MODEL_DIR, meta_files[-1])
    with open(meta_path, "r", encoding="utf-8") as f:
        meta = json.load(f)

    model = lgb.Booster(model_file=meta["model_file"])
    feature_cols = meta["feature_columns"]
    print(f"Model: {os.path.basename(meta['model_file'])}")
    print(f"Features: {len(feature_cols)}, F1: {meta['avg_f1_weighted']:.3f}")

    # 載入所有股票資料
    twii = _load_twii()
    tickers = _list_daily_tickers()
    print(f"Loading {len(tickers)} stocks...")

    stock_data = {}
    for t in tickers:
        df = load_single_stock(t, twii_df=twii)
        if df is not None and len(df) > 60:
            stock_data[t] = df

    print(f"Loaded {len(stock_data)} stocks")

    # Walk-forward 回測：每月底預測，下月持有
    # 用 2024-01 ~ 2026-02 做回測 (模型訓練到 2026-03，但 walk-forward 有 out-of-sample)
    test_months = pd.date_range("2024-01-01", "2026-02-01", freq="MS")

    monthly_results = []
    for month_start in test_months:
        month_end = month_start + pd.offsets.MonthEnd(0)
        next_month_end = month_start + pd.offsets.MonthEnd(1)

        # 在月底做預測
        predictions = []
        for t, df in stock_data.items():
            # 取到月底的資料
            mask = df["Date"] <= month_end
            sub = df[mask]
            if len(sub) < 60:
                continue

            last_row = sub.iloc[-1]
            last_date = last_row["Date"]

            # 距月底不超過5天
            if (month_end - last_date).days > 5:
                continue

            # 構建特徵
            x = np.array([
                last_row[col] if col in sub.columns and not pd.isna(last_row.get(col))
                else np.nan
                for col in feature_cols
            ]).reshape(1, -1)

            proba = model.predict(x)[0]
            up_prob = proba[2]
            down_prob = proba[0]

            # 查看下個月的實際報酬
            future = df[df["Date"] > month_end]
            if len(future) < FORWARD_DAYS:
                continue

            # 用 FORWARD_DAYS (5天) 的報酬
            actual_return_5d = future["Close"].iloc[min(FORWARD_DAYS, len(future)-1)] / last_row["Close"] - 1
            # 也看 20 天
            actual_return_20d = future["Close"].iloc[min(20, len(future)-1)] / last_row["Close"] - 1 if len(future) > 20 else np.nan

            predictions.append({
                "ticker": t,
                "month": month_start.strftime("%Y-%m"),
                "up_prob": up_prob,
                "down_prob": down_prob,
                "actual_5d": actual_return_5d,
                "actual_20d": actual_return_20d if len(future) > 20 else np.nan,
                "close": last_row["Close"],
            })

        if not predictions:
            continue

        pdf = pd.DataFrame(predictions)
        pdf = pdf.sort_values("up_prob", ascending=False)
        n = len(pdf)

        # Top 30 vs Bottom 30 vs All
        top30 = pdf.head(30)
        bot30 = pdf.tail(30)

        monthly_results.append({
            "month": month_start.strftime("%Y-%m"),
            "n_stocks": n,
            "top30_5d": top30["actual_5d"].mean(),
            "bot30_5d": bot30["actual_5d"].mean(),
            "all_5d": pdf["actual_5d"].mean(),
            "top30_20d": top30["actual_20d"].mean(),
            "bot30_20d": bot30["actual_20d"].mean(),
            "all_20d": pdf["actual_20d"].mean(),
            "top30_win_5d": (top30["actual_5d"] > 0).mean(),
            "ic_5d": pdf["up_prob"].corr(pdf["actual_5d"], method="spearman"),
            "ic_20d": pdf["up_prob"].corr(pdf["actual_20d"], method="spearman"),
        })

        m = monthly_results[-1]
        print(f"  {m['month']}  N={m['n_stocks']:>4d}  "
              f"Top30 5d={m['top30_5d']*100:+.2f}%  Bot30 5d={m['bot30_5d']*100:+.2f}%  "
              f"IC5d={m['ic_5d']:+.3f}  IC20d={m['ic_20d']:+.3f}")

    # === Summary ===
    rdf = pd.DataFrame(monthly_results)
    print("\n" + "=" * 75)
    print("  ML Model Backtest Summary (monthly walk-forward)")
    print("=" * 75)
    print(f"  Months tested: {len(rdf)}")
    print(f"  Avg stocks/month: {rdf['n_stocks'].mean():.0f}")
    print(f"\n  --- 5-day holding ---")
    print(f"  Top 30 avg return: {rdf['top30_5d'].mean()*100:+.3f}%")
    print(f"  Bot 30 avg return: {rdf['bot30_5d'].mean()*100:+.3f}%")
    print(f"  All stocks avg:    {rdf['all_5d'].mean()*100:+.3f}%")
    print(f"  Top-Bot spread:    {(rdf['top30_5d']-rdf['bot30_5d']).mean()*100:+.3f}%")
    print(f"  Top 30 win rate:   {rdf['top30_win_5d'].mean()*100:.1f}%")
    print(f"  Avg IC (5d):       {rdf['ic_5d'].mean():+.4f}")
    print(f"\n  --- 20-day holding ---")
    r20 = rdf.dropna(subset=["top30_20d"])
    if len(r20) > 0:
        print(f"  Top 30 avg return: {r20['top30_20d'].mean()*100:+.3f}%")
        print(f"  Bot 30 avg return: {r20['bot30_20d'].mean()*100:+.3f}%")
        print(f"  Top-Bot spread:    {(r20['top30_20d']-r20['bot30_20d']).mean()*100:+.3f}%")
        print(f"  Avg IC (20d):      {r20['ic_20d'].mean():+.4f}")

    # IC per month
    print(f"\n  --- Monthly IC distribution ---")
    pos_ic = (rdf["ic_5d"] > 0).sum()
    print(f"  IC5d > 0 months: {pos_ic}/{len(rdf)} ({pos_ic/len(rdf)*100:.0f}%)")
    pos_ic20 = (r20["ic_20d"] > 0).sum()
    print(f"  IC20d > 0 months: {pos_ic20}/{len(r20)} ({pos_ic20/len(r20)*100:.0f}%)")


if __name__ == "__main__":
    main()
