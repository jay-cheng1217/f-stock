# -*- coding: utf-8 -*-
"""BT-rere-vol-washout:「放量假摔 + 法人大買」變體進場回測(research-only)。

假說(原相/興勤 2026-07-01 同型):
  當日跌 >3% + 量 >2x 20日均量 + 三大法人淨買 > 當日成交量 5%
  → 次日開盤進場,持有 20/60 交易日。
對照:rere lane 基線(縮量洗盤型,2026-07-15 重定)60日 +4.58%/勝率35.8%;另附全樣本抽樣基準。

子分組:
  tail  = 殺尾盤(收盤位於當日振幅下 30%)——最像 7/1 原相
  ma60  = 收盤仍站上 MA60(結構未破)

注意:日K歷史為還原價、近期為原始價之混合 regime,跨除息日的報酬含配息缺口噪音,
結論以相對比較為主。
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
for i, f in enumerate(files):
    tk = f.stem
    if not (tk.isdigit() and len(tk) == 4):
        continue
    try:
        k = pd.read_csv(f, usecols=["Date", "Open", "High", "Low", "Close", "Volume",
                                    "Foreign_BuySell", "Trust_BuySell", "Dealer_BuySell",
                                    "VOL_MA_20", "MA_60"])
    except Exception:
        continue
    if len(k) < 80:
        continue
    k["Date"] = k["Date"].astype(str).str[:10]
    c = k["Close"].astype(float)
    ret1 = c.pct_change()
    inst = (pd.to_numeric(k["Foreign_BuySell"], errors="coerce").fillna(0)
            + pd.to_numeric(k["Trust_BuySell"], errors="coerce").fillna(0)
            + pd.to_numeric(k["Dealer_BuySell"], errors="coerce").fillna(0))
    vol = k["Volume"].astype(float)
    vma = pd.to_numeric(k["VOL_MA_20"], errors="coerce")
    cond = ((ret1 <= -0.03) & (vol >= 2 * vma) & (inst >= 0.05 * vol)
            & (vol >= 500_000))  # 流動性下限 500 張
    idxs = np.where(cond.values)[0]
    o = k["Open"].astype(float).values
    hi, lo_ = k["High"].astype(float).values, k["Low"].astype(float).values
    ma60 = pd.to_numeric(k["MA_60"], errors="coerce").values
    n = len(k)
    for t in idxs:
        if t + 1 >= n:
            continue
        entry = o[t + 1]
        if not np.isfinite(entry) or entry <= 0:
            continue
        f20 = c.iloc[t + 21] / entry - 1 if t + 21 < n else np.nan
        f60 = c.iloc[t + 61] / entry - 1 if t + 61 < n else np.nan
        rng = hi[t] - lo_[t]
        tail = (c.iloc[t] - lo_[t]) / rng < 0.3 if rng > 0 else False
        above60 = c.iloc[t] > ma60[t] if np.isfinite(ma60[t]) else False
        rows.append({"ticker": tk, "date": k["Date"].iloc[t], "year": k["Date"].iloc[t][:4],
                     "f20": f20, "f60": f60, "tail": tail, "ma60": above60})
    # 抽樣基準:每隔 25 個交易日取一點
    for t in range(60, n - 61, 25):
        base20.append(c.iloc[t + 20] / c.iloc[t] - 1)
        base60.append(c.iloc[t + 60] / c.iloc[t] - 1)
    if (i + 1) % 400 == 0:
        print(f"  {i+1}/{len(files)}")

df = pd.DataFrame(rows)
out = BASE / "ml" / "reports" / "bt_rere_vol_washout.csv"
df.to_csv(out, index=False, encoding="utf-8-sig")


def stats(d, col):
    v = d[col].dropna()
    if not len(v):
        return "n=0"
    return (f"n={len(v):4d} 平均{v.mean()*100:+6.2f}% 中位{v.median()*100:+6.2f}% "
            f"勝率{(v>0).mean()*100:4.1f}%")


print(f"\n=== BT-rere-vol-washout(訊號 {len(df)} 筆)===")
b20, b60 = pd.Series(base20).dropna(), pd.Series(base60).dropna()
print(f"全樣本基準 20日: 平均{b20.mean()*100:+.2f}% 勝率{(b20>0).mean()*100:.1f}%"
      f" | 60日: 平均{b60.mean()*100:+.2f}% 勝率{(b60>0).mean()*100:.1f}%")
print("\n全部訊號       20日:", stats(df, "f20"))
print("全部訊號       60日:", stats(df, "f60"))
print("殺尾盤         20日:", stats(df[df["tail"]], "f20"))
print("殺尾盤         60日:", stats(df[df["tail"]], "f60"))
print("站上MA60       20日:", stats(df[df["ma60"]], "f20"))
print("站上MA60       60日:", stats(df[df["ma60"]], "f60"))
print("尾盤+MA60      20日:", stats(df[df["tail"] & df["ma60"]], "f20"))
print("尾盤+MA60      60日:", stats(df[df["tail"] & df["ma60"]], "f60"))
print("\n逐年 60日:")
for y, g in df.groupby("year"):
    print(f"  {y}:", stats(g, "f60"))
print(f"\n明細 -> {out}")
