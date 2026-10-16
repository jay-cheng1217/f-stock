"""回測 entry 系統：phase + score 的前瞻報酬分析"""
import os, sys, random
import pandas as pd
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ml.config import DAILY_K_DIR
from ml.features.entry import compute_entry_features


def load_stock_simple(ticker: str) -> pd.DataFrame | None:
    path = os.path.join(DAILY_K_DIR, f"{ticker}.csv")
    if not os.path.exists(path):
        return None
    df = pd.read_csv(path, dtype={"Date": str})
    df["Date"] = pd.to_datetime(df["Date"])
    df = df.sort_values("Date").reset_index(drop=True)
    if len(df) < 120:
        return None
    # 基本流動性
    if df["Volume"].tail(60).mean() < 500 or df["Close"].iloc[-1] < 10:
        return None
    return df


def main():
    # 取所有股票
    tickers = sorted(
        os.path.splitext(f)[0]
        for f in os.listdir(DAILY_K_DIR)
        if f.endswith(".csv") and os.path.splitext(f)[0].isdigit()
    )
    random.seed(42)
    sample = random.sample(tickers, min(200, len(tickers)))
    print(f"取樣 {len(sample)} 檔股票做回測...")

    results = []
    done = 0
    for t in sample:
        df = load_stock_simple(t)
        if df is None:
            continue
        try:
            df = compute_entry_features(df)
        except Exception:
            continue

        close = df["Close"].values
        phase = df["phase"].values if "phase" in df.columns else None
        score = df["entry_score"].values if "entry_score" in df.columns else None

        if phase is None or score is None:
            continue

        for i in range(60, len(df) - 20):
            if np.isnan(phase[i]) or np.isnan(score[i]):
                continue
            fwd_5 = close[i + 5] / close[i] - 1 if i + 5 < len(close) else np.nan
            fwd_10 = close[i + 10] / close[i] - 1 if i + 10 < len(close) else np.nan
            fwd_20 = close[i + 20] / close[i] - 1 if i + 20 < len(close) else np.nan
            results.append({
                "phase": int(phase[i]),
                "score": score[i],
                "fwd_5": fwd_5,
                "fwd_10": fwd_10,
                "fwd_20": fwd_20,
            })

        done += 1
        if done % 20 == 0:
            print(f"  已完成 {done} 檔...")

    rdf = pd.DataFrame(results)
    print(f"\n總樣本數: {len(rdf):,}")

    phase_names = {0: "打底佈局", 1: "起漲突破", 2: "加速上漲", 3: "高檔出貨"}

    print("\n" + "=" * 65)
    print("  各階段前瞻報酬 (平均 %)")
    print("=" * 65)
    for ph in [0, 1, 2, 3]:
        sub = rdf[rdf["phase"] == ph]
        if len(sub) == 0:
            continue
        wr5 = (sub["fwd_5"] > 0).mean() * 100
        wr20 = (sub["fwd_20"] > 0).mean() * 100
        print(
            f"  {phase_names[ph]:>6s}  N={len(sub):>7,}  "
            f"5d={sub['fwd_5'].mean()*100:+.2f}%(win {wr5:.0f}%)  "
            f"10d={sub['fwd_10'].mean()*100:+.2f}%  "
            f"20d={sub['fwd_20'].mean()*100:+.2f}%(win {wr20:.0f}%)"
        )

    print("\n" + "=" * 65)
    print("  佈局分 vs 前瞻報酬")
    print("=" * 65)
    bins = [(0, 0.5, "0"), (0.5, 2, "0.5-2"), (2, 4, "2-4"), (4, 7, "4+")]
    for lo, hi, label in bins:
        sub = rdf[(rdf["score"] >= lo) & (rdf["score"] < hi)]
        if len(sub) == 0:
            continue
        wr20 = (sub["fwd_20"] > 0).mean() * 100
        print(
            f"  score {label:>5s}  N={len(sub):>7,}  "
            f"5d={sub['fwd_5'].mean()*100:+.2f}%  "
            f"10d={sub['fwd_10'].mean()*100:+.2f}%  "
            f"20d={sub['fwd_20'].mean()*100:+.2f}%(win {wr20:.0f}%)"
        )

    print("\n" + "=" * 65)
    print("  階段 × 佈局分 交叉分析 (20日報酬)")
    print("=" * 65)
    for ph in [0, 1, 2, 3]:
        for lo, hi, label in bins:
            sub = rdf[(rdf["phase"] == ph) & (rdf["score"] >= lo) & (rdf["score"] < hi)]
            if len(sub) < 50:
                continue
            wr = (sub["fwd_20"] > 0).mean() * 100
            print(
                f"  {phase_names[ph]:>6s} score={label:>5s}  "
                f"N={len(sub):>6,}  "
                f"20d={sub['fwd_20'].mean()*100:+.2f}%  "
                f"win={wr:.1f}%"
            )


if __name__ == "__main__":
    main()
