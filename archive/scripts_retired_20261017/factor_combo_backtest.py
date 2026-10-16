"""基於因子研究結果，設計新的信號系統並回測"""
import os, sys, random, warnings
import pandas as pd
import numpy as np

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ml.config import DAILY_K_DIR


def load_stock(ticker):
    path = os.path.join(DAILY_K_DIR, f"{ticker}.csv")
    if not os.path.exists(path):
        return None
    df = pd.read_csv(path, dtype={"Date": str})
    df["Date"] = pd.to_datetime(df["Date"])
    df = df.sort_values("Date").reset_index(drop=True)
    if len(df) < 250 or df["Volume"].tail(60).mean() < 500 or df["Close"].iloc[-1] < 10:
        return None
    return df


def compute_new_signal(df):
    """新信號系統 — 基於因子研究的實證結果

    核心邏輯:
    1. 均值回歸: 股價偏離 MA60 越多，反彈機會越大 (IC=-0.058)
    2. 短期超賣反彈: 20日動量為負 = 超賣 (IC=-0.045)
    3. 逆向法人: 法人買超比例低 = 反向買入機會 (IC=-0.028)
    4. 低波動: 波動率低的股票報酬更好 (IC=-0.029)
    5. 年高動量: 接近52週高點有延續性 (IC=+0.030)
    """
    c = df["Close"].astype(float)
    h = df["High"].astype(float)
    l = df["Low"].astype(float)
    v = df["Volume"].astype(float)

    foreign = df.get("Foreign_BuySell", pd.Series(0, index=df.index)).astype(float)
    trust = df.get("Trust_BuySell", pd.Series(0, index=df.index)).astype(float)
    inst_net = foreign + trust

    n = len(c)

    # 計算因子
    ma20 = c.rolling(20).mean()
    ma60 = c.rolling(60).mean()
    mom_20d = c.pct_change(20)

    ret = c.pct_change()
    vol_20d = ret.rolling(20).std()

    inst_buy_ratio_20d = (inst_net > 0).astype(int).rolling(20).mean()

    high_252 = h.rolling(252, min_periods=60).max()
    low_252 = l.rolling(252, min_periods=60).min()
    position_52w = (c - low_252) / (high_252 - low_252).replace(0, np.nan)

    price_vs_ma60 = (c - ma60) / ma60
    price_vs_ma20 = (c - ma20) / ma20

    # === 新信號分數 (0~10) ===
    score = np.zeros(n, dtype=float)

    for i in range(60, n):
        s = 0.0
        pma60 = price_vs_ma60.iloc[i]
        pma20 = price_vs_ma20.iloc[i]
        m20 = mom_20d.iloc[i]
        ibr = inst_buy_ratio_20d.iloc[i]
        vl = vol_20d.iloc[i]
        p52 = position_52w.iloc[i]

        if np.isnan(pma60) or np.isnan(m20):
            continue

        # 1. 均值回歸分 (0~3) — 股價低於 MA60 越多越好
        if pma60 < -0.15:
            s += 3.0  # 嚴重超賣
        elif pma60 < -0.10:
            s += 2.5
        elif pma60 < -0.05:
            s += 2.0
        elif pma60 < 0:
            s += 1.0
        elif pma60 > 0.15:
            s += 0  # 嚴重超買，不加分

        # 2. 短期超賣反彈 (0~2) — 20日動量為負
        if not np.isnan(m20):
            if m20 < -0.15:
                s += 2.0
            elif m20 < -0.10:
                s += 1.5
            elif m20 < -0.05:
                s += 1.0
            elif m20 < 0:
                s += 0.5

        # 3. 逆向法人 (0~2) — 法人買超比例低反而好
        if not np.isnan(ibr):
            if ibr < 0.3:
                s += 2.0  # 法人幾乎不買 → 最好的時機
            elif ibr < 0.4:
                s += 1.5
            elif ibr < 0.5:
                s += 1.0
            elif ibr > 0.7:
                s -= 1.0  # 法人瘋狂買 → 可能是頂部

        # 4. 低波動加分 (0~1.5)
        if not np.isnan(vl):
            if vl < 0.015:
                s += 1.5
            elif vl < 0.020:
                s += 1.0
            elif vl < 0.025:
                s += 0.5

        # 5. 52週位置 (0~1.5) — 兩端都有機會
        if not np.isnan(p52):
            # 接近年高: 動量延續
            if p52 > 0.90:
                s += 1.5
            elif p52 > 0.80:
                s += 1.0
            # 接近年低: 超賣反彈
            elif p52 < 0.10:
                s += 1.5
            elif p52 < 0.20:
                s += 1.0

        score[i] = max(0, s)

    return score, price_vs_ma60.values, mom_20d.values, inst_buy_ratio_20d.values


def main():
    tickers = sorted(
        os.path.splitext(f)[0]
        for f in os.listdir(DAILY_K_DIR)
        if f.endswith(".csv") and os.path.splitext(f)[0].isdigit()
    )
    random.seed(42)
    sample = random.sample(tickers, min(300, len(tickers)))

    results = []
    done = 0
    for t in sample:
        df = load_stock(t)
        if df is None:
            continue
        score, pma60, m20, ibr = compute_new_signal(df)
        close = df["Close"].values

        for i in range(60, len(df) - 20):
            if score[i] == 0 and np.isnan(pma60[i]):
                continue
            fwd5 = close[i+5]/close[i]-1 if i+5<len(close) else np.nan
            fwd10 = close[i+10]/close[i]-1 if i+10<len(close) else np.nan
            fwd20 = close[i+20]/close[i]-1 if i+20<len(close) else np.nan
            results.append({
                "score": score[i],
                "pma60": pma60[i],
                "m20": m20[i],
                "ibr": ibr[i],
                "fwd5": fwd5, "fwd10": fwd10, "fwd20": fwd20,
            })
        done += 1
        if done % 50 == 0:
            print(f"  {done} stocks...")

    rdf = pd.DataFrame(results).dropna(subset=["fwd20"])
    print(f"Total: {len(rdf):,} samples from {done} stocks\n")

    # === 新信號分數 vs 報酬 ===
    print("=" * 75)
    print("  New Signal Score vs Forward Return")
    print("=" * 75)
    bins = [(0, 2, "0-2 (weak)"), (2, 4, "2-4 (mild)"), (4, 6, "4-6 (moderate)"),
            (6, 8, "6-8 (strong)"), (8, 12, "8+ (very strong)")]
    for lo, hi, label in bins:
        sub = rdf[(rdf["score"] >= lo) & (rdf["score"] < hi)]
        if len(sub) < 100:
            continue
        w5 = (sub["fwd5"] > 0).mean() * 100
        w10 = (sub["fwd10"] > 0).mean() * 100
        w20 = (sub["fwd20"] > 0).mean() * 100
        print(f"  {label:<20s}  N={len(sub):>7,}  "
              f"5d={sub['fwd5'].mean()*100:+.2f}%(win{w5:.0f}%)  "
              f"10d={sub['fwd10'].mean()*100:+.2f}%  "
              f"20d={sub['fwd20'].mean()*100:+.2f}%(win{w20:.0f}%)")

    # === 對比: 隨機 baseline ===
    print(f"\n  Baseline (all):         N={len(rdf):>7,}  "
          f"20d={rdf['fwd20'].mean()*100:+.2f}%(win{(rdf['fwd20']>0).mean()*100:.0f}%)")

    # === IC of new score ===
    ic20 = rdf["score"].corr(rdf["fwd20"], method="spearman")
    ic10 = rdf["score"].corr(rdf["fwd10"], method="spearman")
    ic5 = rdf["score"].corr(rdf["fwd5"], method="spearman")
    print(f"\n  New Score IC:  5d={ic5:+.4f}  10d={ic10:+.4f}  20d={ic20:+.4f}")

    # === Quintile ===
    print("\n" + "=" * 75)
    print("  Quintile Analysis (by new score)")
    print("=" * 75)
    for q_lo, q_hi, label in [(0, 0.2, "Q1(bottom)"), (0.2, 0.4, "Q2"), (0.4, 0.6, "Q3"),
                                (0.6, 0.8, "Q4"), (0.8, 1.0, "Q5(top)")]:
        lo_val = rdf["score"].quantile(q_lo)
        hi_val = rdf["score"].quantile(q_hi)
        sub = rdf[(rdf["score"] >= lo_val) & (rdf["score"] < hi_val)] if q_hi < 1 else rdf[rdf["score"] >= lo_val]
        if len(sub) < 100:
            continue
        w20 = (sub["fwd20"] > 0).mean() * 100
        print(f"  {label:<12s} (score {lo_val:.1f}~{hi_val:.1f})  "
              f"N={len(sub):>7,}  "
              f"5d={sub['fwd5'].mean()*100:+.2f}%  "
              f"10d={sub['fwd10'].mean()*100:+.2f}%  "
              f"20d={sub['fwd20'].mean()*100:+.2f}%(win{w20:.0f}%)")

    # === 頭尾對比 ===
    top10 = rdf[rdf["score"] >= rdf["score"].quantile(0.9)]
    bot10 = rdf[rdf["score"] <= rdf["score"].quantile(0.1)]
    print(f"\n  Top 10%: 20d={top10['fwd20'].mean()*100:+.2f}%(win{(top10['fwd20']>0).mean()*100:.0f}%)")
    print(f"  Bot 10%: 20d={bot10['fwd20'].mean()*100:+.2f}%(win{(bot10['fwd20']>0).mean()*100:.0f}%)")
    print(f"  Spread:  {(top10['fwd20'].mean()-bot10['fwd20'].mean())*100:+.2f}%")


if __name__ == "__main__":
    main()
