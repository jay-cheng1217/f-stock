# -*- coding: utf-8 -*-
"""BT-dividend-refill:填息股貼息買入回測(research-only)。

rere 假說:「近N年填息率高的股票,除息(貼息殺低)就是買點」。
- 事件:上市現金股利除息日(exdiv_calendar_twse.csv,kind 含「息」)
- 進場:除息日開盤買入(貼息秒撿的保守化:不等殺低,直接首日進)
- 填息判定(還原價校正):日K歷史為還原價,除息缺口被抹平;
  raw 填息 Close>=除息前收盤 ⟺ adj Close >= adj_prev_close × (prev_close/ref_price)
- 指標:120日內填息率、填息天數、20/60日持有報酬
- 假說檢驗:該股「過往事件填息率>=80%(且>=2次)」分組 vs 其他
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path(__file__).resolve().parents[1]
DAILY = BASE / "日K資料"
CAL = BASE / "ml" / "data" / "exdiv_calendar_twse.csv"

cal = pd.read_csv(CAL, dtype={"ticker": str})
cal = cal[cal["kind"].astype(str).str.contains("息")]
cal = cal.dropna(subset=["prev_close", "ref_price"])
cal = cal[(cal["ref_price"] > 0) & (cal["prev_close"] > cal["ref_price"])]

events = []
for tk, g in cal.groupby("ticker"):
    f = DAILY / f"{tk}.csv"
    if not f.exists():
        continue
    try:
        k = pd.read_csv(f, usecols=["Date", "Open", "Close"])
    except Exception:
        continue
    k["Date"] = k["Date"].astype(str).str[:10]
    dates = k["Date"].tolist()
    pos = {d: i for i, d in enumerate(dates)}
    c = k["Close"].astype(float).values
    o = k["Open"].astype(float).values
    n = len(k)
    for _, ev in g.iterrows():
        t = pos.get(ev["date"])
        if t is None or t < 1 or t + 1 >= n:
            continue
        thresh = c[t - 1] * (ev["prev_close"] / ev["ref_price"])
        entry = o[t]
        if not np.isfinite(entry) or entry <= 0:
            continue
        end = min(n, t + 121)
        fill_day = None
        for j in range(t, end):
            if c[j] >= thresh:
                fill_day = j - t
                break
        f20 = c[t + 20] / entry - 1 if t + 20 < n else np.nan
        f60 = c[t + 60] / entry - 1 if t + 60 < n else np.nan
        events.append({"ticker": tk, "date": ev["date"], "year": ev["date"][:4],
                       "yield_pct": (ev["prev_close"] - ev["ref_price"]) / ev["prev_close"] * 100,
                       "filled": fill_day is not None, "fill_days": fill_day,
                       "f20": f20, "f60": f60})

df = pd.DataFrame(events).sort_values(["ticker", "date"]).reset_index(drop=True)
# 過往填息率(嚴格只用事件之前的歷史)
prior_rate, prior_n = [], []
for tk, g in df.groupby("ticker"):
    filled_cum = g["filled"].astype(float).cumsum().shift(1).fillna(0)
    cnt = pd.Series(range(len(g)), index=g.index, dtype=float)
    rate = np.where(cnt > 0, filled_cum / cnt, np.nan)
    prior_rate.extend(rate.tolist())
    prior_n.extend(cnt.tolist())
df["prior_rate"] = prior_rate
df["prior_n"] = prior_n

out = BASE / "ml" / "reports" / "bt_dividend_refill.csv"
df.to_csv(out, index=False, encoding="utf-8-sig")


def stats(d, col):
    v = d[col].dropna()
    if not len(v):
        return "n=0"
    return (f"n={len(v):4d} 平均{v.mean()*100:+6.2f}% 中位{v.median()*100:+6.2f}% "
            f"勝率{(v>0).mean()*100:4.1f}%")


print(f"=== BT-dividend-refill(現金除息事件 {len(df)} 筆,2020-2026 上市)===")
print(f"整體 120日內填息率 {df['filled'].mean()*100:.1f}%"
      f" · 填息中位天數 {df.loc[df['filled'], 'fill_days'].median():.0f} 天")
print(f"(對照全樣本基準:20日 +1.58%/50.1%,60日 +5.74%/53.2%)\n")
print("全部事件       20日:", stats(df, "f20"))
print("全部事件       60日:", stats(df, "f60"))
hi = df[(df["prior_n"] >= 2) & (df["prior_rate"] >= 0.8)]
lo = df[(df["prior_n"] >= 2) & (df["prior_rate"] < 0.8)]
print("高填息史群     20日:", stats(hi, "f20"))
print("高填息史群     60日:", stats(hi, "f60"))
print("低填息史群     20日:", stats(lo, "f20"))
print("低填息史群     60日:", stats(lo, "f60"))
hy = hi[hi["yield_pct"] >= 4]
print("高填息+殖利率>=4% 20日:", stats(hy, "f20"))
print("高填息+殖利率>=4% 60日:", stats(hy, "f60"))
print(f"  (高填息群填息率 {hi['filled'].mean()*100:.1f}% vs 低填息群 {lo['filled'].mean()*100:.1f}%)")
print("\n逐年 60日(全部事件):")
for y, g in df.groupby("year"):
    print(f"  {y}:", stats(g, "f60"))
print(f"\n明細 -> {out}")
