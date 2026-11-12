# -*- coding: utf-8 -*-
"""斷頭發動型事前登記回測（docs/RESEARCH_margin_washout_ignition_20261112.md）。

研究專用：不讀任何 gate 設定、不寫正式資料目錄。輸出至 ml/reports/。
條件與成功準則以事前登記文件為準，本腳本不得擅改閾值。
"""
from __future__ import annotations

import duckdb
import numpy as np
import pandas as pd

DB = "stock.duckdb"
START = "2019-09-01"   # 含 lookback 緩衝
SIG_FROM = "2020-02-01"
SIG_TO = "2026-10-13"
OUT = "ml/reports/research_margin_washout_signals.csv"

WASHOUT = 0.85          # A: Close <= high10_prev * 0.85
MB_DROP = -0.20         # B: 10 日融資減幅
MB_FLOOR = 2000.0       # B: 10 日前融資餘額下限（張）
UP_MIN = 0.05           # D: 當日漲幅
LIQ_SHARES = 500 * 1000  # E: VOL_MA_20 >= 500 張（股）
DEDUP = 20              # 同票訊號間隔（交易日）
HORIZONS = (5, 10, 20)


def load() -> pd.DataFrame:
    con = duckdb.connect(DB, read_only=True)
    df = con.execute(
        """
        select Ticker, Date, Open, High, Close, Volume,
               Foreign_BuySell, Trust_BuySell, Margin_Balance, MA_20, VOL_MA_20
        from daily_k
        where Date >= ? and Ticker similar to '[1-9][0-9]{3}'
        order by Ticker, Date
        """,
        [START],
    ).df()
    con.close()
    return df


def add_features(df: pd.DataFrame) -> pd.DataFrame:
    g = df.groupby("Ticker", sort=False)
    df["prev_close"] = g["Close"].shift(1)
    df["ret1"] = df["Close"] / df["prev_close"] - 1
    df["high10_prev"] = g["High"].transform(lambda s: s.rolling(10).max().shift(1))
    df["mb_10ago"] = g["Margin_Balance"].shift(10)
    df["f5_prev"] = g["Foreign_BuySell"].transform(lambda s: s.shift(1).rolling(5).sum())
    # forward legs（研究量測，無前視疑慮：僅用於事後評估）
    df["open_t1"] = g["Open"].shift(-1)
    for h in HORIZONS:
        df[f"close_t{h}"] = g["Close"].shift(-h)
        df[f"fwd{h}"] = df[f"close_t{h}"] / df["open_t1"] - 1
    df["gap_t1"] = df["open_t1"] / df["Close"] - 1
    return df


def conditions(df: pd.DataFrame) -> pd.DataFrame:
    df["cA"] = df["Close"] <= df["high10_prev"] * WASHOUT
    df["cB"] = (df["Margin_Balance"] / df["mb_10ago"] - 1 <= MB_DROP) & (df["mb_10ago"] >= MB_FLOOR)
    df["cC"] = (df["f5_prev"] < 0) & (df["Foreign_BuySell"] > 0)
    df["cD"] = df["ret1"] >= UP_MIN
    df["cE"] = df["VOL_MA_20"] >= LIQ_SHARES
    return df


def dedup(sig: pd.DataFrame, pos: pd.Series) -> pd.DataFrame:
    keep = []
    for tkr, grp in sig.groupby("Ticker", sort=False):
        last = -(10**9)
        for idx, p in zip(grp.index, pos.loc[grp.index]):
            if p - last >= DEDUP:
                keep.append(idx)
                last = p
    return sig.loc[keep]


def summarize(name: str, sig: pd.DataFrame, base: pd.DataFrame) -> None:
    merged = sig.merge(base, on="Date", how="left", suffixes=("", "_bm"))
    print(f"\n=== {name} ===  N={len(merged)}")
    for h in HORIZONS:
        r = merged[f"fwd{h}"].dropna()
        ex = (merged[f"fwd{h}"] - merged[f"fwd{h}_bm"]).dropna()
        if len(r) == 0:
            continue
        print(
            f"T+{h:>2}: mean {r.mean()*100:+6.2f}%  median {r.median()*100:+6.2f}%  "
            f"win {(r > 0).mean()*100:5.1f}%  | excess mean {ex.mean()*100:+6.2f}%  "
            f"median {ex.median()*100:+6.2f}%  exwin {(ex > 0).mean()*100:5.1f}%  n={len(r)}"
        )
    gap = merged["gap_t1"].dropna()
    print(
        f"t+1 開盤 gap: mean {gap.mean()*100:+.2f}%  median {gap.median()*100:+.2f}%  "
        f"p90 {gap.quantile(.9)*100:+.2f}%  >9% 占比 {(gap > .09).mean()*100:.1f}%"
    )


def main() -> None:
    df = add_features(conditions_input := load())
    df = conditions(df)
    df["pos"] = df.groupby("Ticker", sort=False).cumcount()

    win = (df["Date"] >= SIG_FROM) & (df["Date"] <= SIG_TO)
    full = df[win & df.cA & df.cB & df.cC & df.cD & df.cE].copy()
    abl = df[win & df.cA & df.cC & df.cD & df.cE & ~df.cB.fillna(False)].copy()

    full = dedup(full, df["pos"])
    abl = dedup(abl, df["pos"])

    # 基準：同日 E 合格全市場等權（期初條件）
    pool = df[win & df.cE]
    base = pool.groupby("Date")[[f"fwd{h}" for h in HORIZONS]].mean().reset_index()

    summarize("全條件 A+B+C+D+E（斷頭發動型）", full, base)
    summarize("消融 A+C+D+E、且 B 不成立（無融資斷頭）", abl, base)

    # 現行發動型伴隨條件會擋掉多少全條件訊號
    vol_ratio = full["Volume"] / full["VOL_MA_20"]
    above_ma20 = full["Close"] >= full["MA_20"]
    trust_buy = full["Trust_BuySell"] > 0
    blocked = ~(above_ma20 & (vol_ratio >= 1.5) & trust_buy)
    print(
        f"\n現行發動型伴隨條件：站回MA20 {above_ma20.mean()*100:.0f}% 成立、"
        f"量比>=1.5 {(vol_ratio >= 1.5).mean()*100:.0f}% 成立、"
        f"投信同買 {trust_buy.mean()*100:.0f}% 成立；"
        f"三者同時成立僅 {(~blocked).mean()*100:.1f}% → 被擋掉 {blocked.mean()*100:.1f}%"
    )

    cols = ["Ticker", "Date", "Close", "ret1", "gap_t1", "fwd5", "fwd10", "fwd20",
            "Margin_Balance", "mb_10ago", "Foreign_BuySell", "f5_prev"]
    full[cols].to_csv(OUT, index=False)
    print(f"\nsignals -> {OUT}  (年份分佈: {full['Date'].dt.year.value_counts().sort_index().to_dict()})")


if __name__ == "__main__":
    main()
