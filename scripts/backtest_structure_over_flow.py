# -*- coding: utf-8 -*-
"""BT-structure-over-flow(research-only,0706 南茂教訓):

假說:「大戶週增>=2.5pp + 散戶減 + 收盤站上MA60」的結構訊號成立時,
外資近5日賣超不應構成否決/降級。

設計:
- 訊號:TDCC 週頻(tdcc_summary,2020-2026,296週) whale_delta>=2.5 且 retail_delta<0
- 結構:訊號週五收盤 >= MA60;流動性 20日均量>=500張
- 進場:公布後次一交易日開盤;持有 20/60 交易日(還原價,含息)
- 分組:外資近5日淨額 f5<=-500張(舊規則的降級觸發) vs f5>-500
- 對照:全樣本基準 20日 +1.58%/50.1%、60日 +5.74%/53.2%(同窗口抽樣)
判準:若 SELL 組報酬/勝率不遜於基準且與 BUY 組差距小 → 外資日賣=噪音,規則可改。
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path(__file__).resolve().parents[1]
DAILY = BASE / "日K資料"

s = pd.read_csv(BASE / "集保分散" / "tdcc_summary.csv", dtype={"Ticker": str, "Date": str})
s = s[s["Ticker"].str.fullmatch(r"\d{4}")]
s = s.sort_values(["Ticker", "Date"])
s["wd"] = s.groupby("Ticker")["Whale_Pct"].diff()
s["rd"] = s.groupby("Ticker")["Retail_Pct"].diff()
sig = s[(s["wd"] >= 2.5) & (s["rd"] < 0)].copy()
print(f"原始訊號 {len(sig)} 筆(未過濾)")

rows = []
for tk, g in sig.groupby("Ticker"):
    f = DAILY / f"{tk}.csv"
    if not f.exists():
        continue
    try:
        k = pd.read_csv(f, usecols=["Date", "Open", "Close", "Volume",
                                    "MA_60", "VOL_MA_20", "Foreign_BuySell"])
    except Exception:
        continue
    if len(k) < 80:
        continue
    k["d8"] = k["Date"].astype(str).str[:10].str.replace("-", "")
    d8 = k["d8"].values
    c = k["Close"].astype(float).values
    o = k["Open"].astype(float).values
    ma60 = pd.to_numeric(k["MA_60"], errors="coerce").values
    vma = pd.to_numeric(k["VOL_MA_20"], errors="coerce").values
    fby = pd.to_numeric(k["Foreign_BuySell"], errors="coerce").fillna(0).values
    n = len(k)
    for _, ev in g.iterrows():
        # 訊號週資料日(週五) → 找 <= 該日的最後交易列
        pos = np.searchsorted(d8, ev["Date"], side="right") - 1
        if pos < 60 or pos + 1 >= n:
            continue
        if not (np.isfinite(ma60[pos]) and c[pos] >= ma60[pos]):
            continue  # 破 MA60 → 結構不成立
        if not (np.isfinite(vma[pos]) and vma[pos] >= 500_000):
            continue  # 流動性
        f5 = fby[max(0, pos - 4):pos + 1].sum() / 1000
        entry = o[pos + 1]
        if not np.isfinite(entry) or entry <= 0:
            continue
        f20 = c[pos + 21] / entry - 1 if pos + 21 < n else np.nan
        f60 = c[pos + 61] / entry - 1 if pos + 61 < n else np.nan
        rows.append({"ticker": tk, "date": ev["Date"], "year": ev["Date"][:4],
                     "wd": ev["wd"], "f5": f5, "f20": f20, "f60": f60})

df = pd.DataFrame(rows)
out = BASE / "ml" / "reports" / "bt_structure_over_flow.csv"
df.to_csv(out, index=False, encoding="utf-8-sig")


def stats(d, col):
    v = d[col].dropna()
    if not len(v):
        return "n=0"
    return (f"n={len(v):5d} 平均{v.mean()*100:+6.2f}% 中位{v.median()*100:+6.2f}% "
            f"勝率{(v>0).mean()*100:4.1f}%")


sell = df[df["f5"] <= -500]
buy = df[df["f5"] > -500]
print(f"\n=== 結構訊號(站MA60+流動性過濾後 {len(df)} 筆)===")
print("基準(同窗口): 20日 +1.58%/50.1% | 60日 +5.74%/53.2%\n")
print("全部結構訊號   20日:", stats(df, "f20"))
print("全部結構訊號   60日:", stats(df, "f60"))
print(f"\n外資5日賣<=-500張(舊規則降級組, n={len(sell)}):")
print("  20日:", stats(sell, "f20"))
print("  60日:", stats(sell, "f60"))
print(f"外資5日 >-500張(對照組, n={len(buy)}):")
print("  20日:", stats(buy, "f20"))
print("  60日:", stats(buy, "f60"))
print("\n外資大賣<=-2000張 極端組:")
xs = df[df["f5"] <= -2000]
print("  20日:", stats(xs, "f20"))
print("  60日:", stats(xs, "f60"))
print("\n逐年 60日(全部結構訊號):")
for y, g in df.groupby("year"):
    print(f"  {y}:", stats(g, "f60"))
