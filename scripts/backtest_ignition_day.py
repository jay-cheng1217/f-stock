# -*- coding: utf-8 -*-
"""BT-ignition-day(南茂 7/2 型態):洗盤結束「放量發動第一天」進場 vs rere 量縮蹲點。

她買南茂的點還原:
- 前段深洗盤:近10日內自高點回落 >=10%
- 發動訊號日:外資由賣(前一日<0 或前5日累計<0)轉當日買 + 當日量 >=1.5x 20日均量
  + 收盤站回 MA20(前一日在 MA20 下、當日收在 MA20 上,或當日收 > MA20 且較前日放量突破)
- 收 > MA60(中期結構在)
進場:訊號日次一交易日開盤;持有 20/60 交易日(還原價)。
對照:
  A) rere 蹲點型(量縮<1.2x + 貼MA20) 同期基準
  B) 全樣本抽樣基準 20日+1.58%/60日+5.74%
分組:訊號日漲幅(發動力道)、是否伴投信同買。
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path(__file__).resolve().parents[1]
DAILY = BASE / "日K資料"

rows = []
base20, base60 = [], []
files = sorted(DAILY.glob("*.csv"))
for fi, f in enumerate(files):
    tk = f.stem
    if not (tk.isdigit() and len(tk) == 4):
        continue
    try:
        k = pd.read_csv(f, usecols=["Date", "Open", "High", "Low", "Close", "Volume",
                                    "MA_20", "MA_60", "VOL_MA_20", "Foreign_BuySell",
                                    "Trust_BuySell"])
    except Exception:
        continue
    if len(k) < 90:
        continue
    c = k["Close"].astype(float).values
    o = k["Open"].astype(float).values
    hi = k["High"].astype(float).values
    ma20 = pd.to_numeric(k["MA_20"], errors="coerce").values
    ma60 = pd.to_numeric(k["MA_60"], errors="coerce").values
    vol = k["Volume"].astype(float).values
    vma = pd.to_numeric(k["VOL_MA_20"], errors="coerce").values
    fby = pd.to_numeric(k["Foreign_BuySell"], errors="coerce").fillna(0).values
    tby = pd.to_numeric(k["Trust_BuySell"], errors="coerce").fillna(0).values
    n = len(k)
    for t in range(60, n - 1):
        if not (np.isfinite(ma20[t]) and np.isfinite(ma60[t]) and np.isfinite(vma[t])):
            continue
        if vma[t] < 500_000:
            continue
        # 深洗盤:近10日高點回落>=10%(以低點計曾洗過)
        hi10 = hi[t - 10:t + 1].max()
        washed = (hi10 - c[t]) / hi10 >= 0.10 or (hi10 - k["Low"].astype(float).values[t - 3:t + 1].min()) / hi10 >= 0.12
        if not washed:
            continue
        if c[t] <= ma60[t]:
            continue
        # 發動訊號日
        f5_prev = fby[max(0, t - 5):t].sum()
        turn_buy = fby[t] > 0 and (fby[t - 1] < 0 or f5_prev < 0)
        vol_burst = vol[t] >= 1.5 * vma[t]
        reclaim = c[t] > ma20[t] and (c[t - 1] <= ma20[t] or vol[t] > vol[t - 1])
        if not (turn_buy and vol_burst and reclaim):
            continue
        entry = o[t + 1]
        if not np.isfinite(entry) or entry <= 0:
            continue
        f20 = c[t + 21] / entry - 1 if t + 21 < n else np.nan
        f60 = c[t + 61] / entry - 1 if t + 61 < n else np.nan
        day_ret = c[t] / c[t - 1] - 1
        rows.append({"ticker": tk, "date": k["Date"].astype(str).str[:10].values[t],
                     "year": k["Date"].astype(str).str[:4].values[t],
                     "day_ret": day_ret, "trust_buy": tby[t] > 0,
                     "f20": f20, "f60": f60})
    for t in range(60, n - 61, 30):
        base20.append(c[t + 20] / c[t] - 1)
        base60.append(c[t + 60] / c[t] - 1)

df = pd.DataFrame(rows)
out = BASE / "ml" / "reports" / "bt_ignition_day.csv"
df.to_csv(out, index=False, encoding="utf-8-sig")


def stats(d, col):
    v = d[col].dropna()
    if not len(v):
        return "n=0"
    return (f"n={len(v):5d} 平均{v.mean()*100:+6.2f}% 中位{v.median()*100:+6.2f}% "
            f"勝率{(v>0).mean()*100:4.1f}%")


b20, b60 = pd.Series(base20).dropna(), pd.Series(base60).dropna()
print(f"=== BT-ignition-day 洗盤後放量發動第一天({len(df)} 筆)===")
print(f"全樣本基準: 20日 {b20.mean()*100:+.2f}%/{(b20>0).mean()*100:.0f}% | "
      f"60日 {b60.mean()*100:+.2f}%/{(b60>0).mean()*100:.0f}%\n")
print("全部發動訊號   20日:", stats(df, "f20"))
print("全部發動訊號   60日:", stats(df, "f60"))
print("\n發動日漲幅 3-6%(溫和發動,南茂型):")
mid = df[(df["day_ret"] >= 0.03) & (df["day_ret"] <= 0.06)]
print("  20日:", stats(mid, "f20"))
print("  60日:", stats(mid, "f60"))
print("發動日漲停/接近(>8%,追高風險):")
hot = df[df["day_ret"] > 0.08]
print("  20日:", stats(hot, "f20"))
print("  60日:", stats(hot, "f60"))
print("\n伴投信同買:")
tw = df[df["trust_buy"]]
print("  20日:", stats(tw, "f20"))
print("  60日:", stats(tw, "f60"))
print("\n逐年 60日:")
for y, g in df.groupby("year"):
    print(f"  {y}:", stats(g, "f60"))
