"""量化因子研究：逐一測試每個因子在台股的預測力
只留有統計顯著性的因子，再組合成信號系統。
"""
import os, sys, random, warnings
import pandas as pd
import numpy as np

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ml.config import DAILY_K_DIR


def load_stock(ticker: str) -> pd.DataFrame | None:
    path = os.path.join(DAILY_K_DIR, f"{ticker}.csv")
    if not os.path.exists(path):
        return None
    df = pd.read_csv(path, dtype={"Date": str})
    df["Date"] = pd.to_datetime(df["Date"])
    df = df.sort_values("Date").reset_index(drop=True)
    if len(df) < 250:
        return None
    if df["Volume"].tail(60).mean() < 500 or df["Close"].iloc[-1] < 10:
        return None
    return df


def compute_factors(df: pd.DataFrame) -> pd.DataFrame:
    """計算各種已知量化因子"""
    c = df["Close"].astype(float)
    h = df["High"].astype(float)
    l = df["Low"].astype(float)
    v = df["Volume"].astype(float)

    foreign = df.get("Foreign_BuySell", pd.Series(0, index=df.index)).astype(float)
    trust = df.get("Trust_BuySell", pd.Series(0, index=df.index)).astype(float)
    inst_net = foreign + trust

    out = pd.DataFrame(index=df.index)
    out["close"] = c

    # === 動量因子 (Momentum) ===
    # 經典動量: 過去 N 日報酬
    out["mom_5d"] = c.pct_change(5)
    out["mom_10d"] = c.pct_change(10)
    out["mom_20d"] = c.pct_change(20)
    out["mom_60d"] = c.pct_change(60)

    # 跳過最近 5 日的中期動量 (避免短期反轉)
    out["mom_60d_skip5"] = c.shift(5).pct_change(55)

    # === 均線因子 ===
    ma5 = c.rolling(5).mean()
    ma20 = c.rolling(20).mean()
    ma60 = c.rolling(60).mean()
    out["price_vs_ma5"] = (c - ma5) / ma5
    out["price_vs_ma20"] = (c - ma20) / ma20
    out["price_vs_ma60"] = (c - ma60) / ma60

    # 均線排列: MA5 > MA20 > MA60
    out["ma_aligned"] = ((ma5 > ma20) & (ma20 > ma60)).astype(int)

    # === RSI ===
    delta = c.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    out["rsi_14"] = 100 - 100 / (1 + rs)

    # === 波動率因子 ===
    ret = c.pct_change()
    out["volatility_20d"] = ret.rolling(20).std()

    # Bollinger Band 寬度 & 位置
    bb_mid = c.rolling(20).mean()
    bb_std = c.rolling(20).std()
    bb_upper = bb_mid + 2 * bb_std
    bb_lower = bb_mid - 2 * bb_std
    out["bb_width"] = (bb_upper - bb_lower) / bb_mid  # 布林帶寬度
    out["bb_position"] = (c - bb_lower) / (bb_upper - bb_lower)  # 在帶中位置 0~1

    # === 成交量因子 ===
    vol_ma20 = v.rolling(20).mean()
    out["volume_ratio"] = v / vol_ma20.replace(0, np.nan)

    # 量能趨勢: 5日均量 vs 20日均量
    vol_ma5 = v.rolling(5).mean()
    out["volume_trend"] = vol_ma5 / vol_ma20.replace(0, np.nan)

    # OBV 斜率 (20日)
    obv = (np.sign(c.diff()) * v).cumsum()
    out["obv_slope_20d"] = obv.diff(20) / obv.rolling(20).mean().replace(0, np.nan)

    # === 法人因子 (台股特有) ===
    out["inst_net_5d"] = inst_net.rolling(5).sum()  # 5日法人淨買張
    out["inst_net_10d"] = inst_net.rolling(10).sum()
    out["inst_net_20d"] = inst_net.rolling(20).sum()

    # 法人買超天數比
    out["inst_buy_ratio_10d"] = (inst_net > 0).astype(int).rolling(10).mean()
    out["inst_buy_ratio_20d"] = (inst_net > 0).astype(int).rolling(20).mean()

    # 外資動能
    out["foreign_net_10d"] = foreign.rolling(10).sum()
    out["trust_net_10d"] = trust.rolling(10).sum()

    # === 價格型態因子 ===
    # 52週位置
    high_252 = h.rolling(252, min_periods=60).max()
    low_252 = l.rolling(252, min_periods=60).min()
    out["position_52w"] = (c - low_252) / (high_252 - low_252).replace(0, np.nan)

    # 距離20日高點
    out["dist_to_high_20d"] = c / h.rolling(20).max() - 1

    # 距離20日低點
    out["dist_to_low_20d"] = c / l.rolling(20).min() - 1

    # 新高新低
    out["near_52w_high"] = (c >= high_252 * 0.95).astype(int)
    out["near_52w_low"] = (c <= low_252 * 1.05).astype(int)

    # === ATR 相關 ===
    prev_c = c.shift(1)
    tr = pd.concat([h - l, (h - prev_c).abs(), (l - prev_c).abs()], axis=1).max(axis=1)
    atr_5 = tr.rolling(5).mean()
    atr_20 = tr.rolling(20).mean()
    out["atr_contraction"] = atr_5 / atr_20.replace(0, np.nan)  # < 1 表示波動收斂

    # === 前瞻報酬 (target) ===
    out["fwd_5d"] = c.shift(-5) / c - 1
    out["fwd_10d"] = c.shift(-10) / c - 1
    out["fwd_20d"] = c.shift(-20) / c - 1

    return out


def main():
    tickers = sorted(
        os.path.splitext(f)[0]
        for f in os.listdir(DAILY_K_DIR)
        if f.endswith(".csv") and os.path.splitext(f)[0].isdigit()
    )
    random.seed(42)
    sample = random.sample(tickers, min(300, len(tickers)))
    print(f"Loading {len(sample)} stocks...")

    all_data = []
    done = 0
    for t in sample:
        df = load_stock(t)
        if df is None:
            continue
        factors = compute_factors(df)
        factors["ticker"] = t
        # 只取有足夠歷史的部分
        factors = factors.iloc[60:].copy()
        all_data.append(factors)
        done += 1
        if done % 50 == 0:
            print(f"  {done} stocks done...")

    big = pd.concat(all_data, ignore_index=True)
    # 去掉有 NaN 前瞻報酬的 (最後20天)
    big = big.dropna(subset=["fwd_5d", "fwd_10d", "fwd_20d"])
    print(f"Total samples: {len(big):,}")

    # === 測試每個因子 ===
    factor_cols = [
        "mom_5d", "mom_10d", "mom_20d", "mom_60d", "mom_60d_skip5",
        "price_vs_ma5", "price_vs_ma20", "price_vs_ma60",
        "ma_aligned",
        "rsi_14",
        "volatility_20d", "bb_width", "bb_position",
        "volume_ratio", "volume_trend", "obv_slope_20d",
        "inst_net_5d", "inst_net_10d", "inst_net_20d",
        "inst_buy_ratio_10d", "inst_buy_ratio_20d",
        "foreign_net_10d", "trust_net_10d",
        "position_52w", "dist_to_high_20d", "dist_to_low_20d",
        "near_52w_high", "near_52w_low",
        "atr_contraction",
    ]

    print("\n" + "=" * 80)
    print("  Factor IC (Information Coefficient) - Rank correlation with forward return")
    print("  IC > 0.02 = useful, IC > 0.05 = strong")
    print("=" * 80)

    results = []
    for fac in factor_cols:
        if fac not in big.columns:
            continue
        sub = big[[fac, "fwd_5d", "fwd_10d", "fwd_20d"]].dropna()
        if len(sub) < 1000:
            continue

        ic5 = sub[fac].corr(sub["fwd_5d"], method="spearman")
        ic10 = sub[fac].corr(sub["fwd_10d"], method="spearman")
        ic20 = sub[fac].corr(sub["fwd_20d"], method="spearman")

        results.append({"factor": fac, "ic5": ic5, "ic10": ic10, "ic20": ic20,
                        "abs_ic20": abs(ic20), "N": len(sub)})

    rdf = pd.DataFrame(results).sort_values("abs_ic20", ascending=False)

    for _, r in rdf.iterrows():
        mark = "***" if r["abs_ic20"] > 0.05 else "** " if r["abs_ic20"] > 0.03 else "*  " if r["abs_ic20"] > 0.02 else "   "
        print(f"  {mark} {r['factor']:<25s}  IC5={r['ic5']:+.4f}  IC10={r['ic10']:+.4f}  IC20={r['ic20']:+.4f}  N={r['N']:>8,}")

    # === 分位數分析 (前20%vs後20%) ===
    print("\n" + "=" * 80)
    print("  Quintile Analysis: Top 20% vs Bottom 20% (20-day forward return)")
    print("=" * 80)

    for _, r in rdf.head(15).iterrows():
        fac = r["factor"]
        sub = big[[fac, "fwd_20d"]].dropna()
        q20 = sub[fac].quantile(0.2)
        q80 = sub[fac].quantile(0.8)
        bot = sub[sub[fac] <= q20]["fwd_20d"]
        top = sub[sub[fac] >= q80]["fwd_20d"]
        spread = top.mean() - bot.mean()
        top_wr = (top > 0).mean() * 100
        bot_wr = (bot > 0).mean() * 100
        print(f"  {fac:<25s}  Bot20%: {bot.mean()*100:+.2f}%(win{bot_wr:.0f}%)  "
              f"Top20%: {top.mean()*100:+.2f}%(win{top_wr:.0f}%)  "
              f"Spread: {spread*100:+.2f}%")


if __name__ == "__main__":
    main()
