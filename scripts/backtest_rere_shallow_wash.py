# -*- coding: utf-8 -*-
"""BT-rere-shallow-wash:驗證「淺洗盤帶(8%<=wash<10%)」是否值得納入 rere lane。

動機(2026-09-24 台表科 6278 案例):rere 於 195 附近推薦、我方因洗盤僅 8~9.75%
差 0.25-2pp 未過 10% 門檻而 miss。單案不足以改 gate,本票以全歷史回答
「若門檻降到 8%,多收進來的邊界帶是 alpha 還是雜訊」。

訊號與帳本慣例與 backtest_rere_lane_baseline.py 完全一致(entry=次日收盤、
MA60×0.97 停損、60 交易日、除息還原),唯一差異是洗盤深度分組:
  BASE       wash >= 10%(現行 lane,對照組)
  BAND       8% <= wash < 10%(降門檻會多收的邊界帶)
  BAND_TRUST BAND 且 投信近5日累計買超 > 0(台表科型:投信先蹲)

用法:python -X utf8 scripts/backtest_rere_shallow_wash.py
輸出:ml/reports/bt_rere_shallow_wash.csv + stdout 統計
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
BASE = Path(__file__).resolve().parents[1]
DAILY = BASE / "日K資料"
CAL_PATH = BASE / "ml" / "data" / "ex_dividend_calendar.csv"

WASH_MIN, WASH_SHALLOW, MA20_BAND, VOL_MAX, HOLD = 0.10, 0.08, 0.05, 1.2, 60

cal = pd.read_csv(CAL_PATH, dtype={"stock_id": str}, encoding="utf-8-sig")
cal["dv"] = pd.to_numeric(cal["stock_and_cache_dividend"], errors="coerce").fillna(0.0)
cal["date"] = cal["date"].astype(str).str[:10]
CAL = {tk: g[["date", "dv"]].values.tolist() for tk, g in cal.groupby("stock_id")}


def run_one(tk: str, k: pd.DataFrame, rows: list) -> None:
    k["Date"] = k["Date"].astype(str).str[:10]
    c = pd.to_numeric(k["Close"], errors="coerce")
    vol = pd.to_numeric(k["Volume"], errors="coerce")
    vma = pd.to_numeric(k["VOL_MA_20"], errors="coerce")
    ma20 = pd.to_numeric(k["MA_20"], errors="coerce")
    ma60 = pd.to_numeric(k["MA_60"], errors="coerce")
    fr = pd.to_numeric(k["Foreign_BuySell"], errors="coerce").fillna(0)
    tr = pd.to_numeric(k.get("Trust_BuySell"), errors="coerce").fillna(0)

    hi10 = c.rolling(10).max()
    wash = 1 - c / hi10
    v20 = c / ma20 - 1
    vr = vol / vma
    fr_prev5 = fr.shift(1).rolling(5).sum()
    tr_sum5 = tr.rolling(5).sum()

    common = ((v20.abs() <= MA20_BAND) & (vr < VOL_MAX) & (c > ma60)
              & (fr_prev5 < 0) & (fr > 0) & (vol >= 500_000))
    cond = common & (wash >= WASH_SHALLOW)
    idxs = np.where(cond.fillna(False).values)[0]
    n = len(k)
    events = CAL.get(tk, [])
    for t in idxs:
        e_idx = t + 1
        if e_idx >= n:
            continue
        entry = float(c.iloc[e_idx])
        if not np.isfinite(entry) or entry <= 0:
            continue
        stop = float(ma60.iloc[t]) * 0.97 if np.isfinite(ma60.iloc[t]) else np.nan
        end = min(e_idx + HOLD, n - 1)
        entry_date = k["Date"].iloc[e_idx]

        exit_ret = None
        for j in range(e_idx + 1, end + 1):
            dj = k["Date"].iloc[j]
            cum = sum(dv for d, dv in events if entry_date < d <= dj)
            px = float(c.iloc[j]) + cum
            if np.isfinite(stop) and px < stop:
                exit_ret = px / entry - 1
                break
        if exit_ret is None:
            cum_end = sum(dv for d, dv in events if entry_date < d <= k["Date"].iloc[end])
            exit_ret = (float(c.iloc[end]) + cum_end) / entry - 1

        w = float(wash.iloc[t])
        trust5 = float(tr_sum5.iloc[t]) if np.isfinite(tr_sum5.iloc[t]) else 0.0
        if w >= WASH_MIN:
            grp = "BASE"
        else:
            grp = "BAND_TRUST" if trust5 > 0 else "BAND"
        rows.append({"ticker": tk, "signal_date": k["Date"].iloc[t],
                     "year": k["Date"].iloc[t][:4], "grp": grp,
                     "wash": round(w, 4), "trust5_lots": round(trust5 / 1000),
                     "ret": exit_ret})


rows: list[dict] = []
files = sorted(DAILY.glob("*.csv"))
for i, f in enumerate(files):
    tk = f.stem
    if not (tk.isdigit() and len(tk) == 4):
        continue
    try:
        k = pd.read_csv(f, usecols=["Date", "Close", "Volume", "Foreign_BuySell",
                                    "Trust_BuySell", "VOL_MA_20", "MA_20", "MA_60"])
    except Exception:
        continue
    if len(k) < 90:
        continue
    run_one(tk, k, rows)
    if (i + 1) % 500 == 0:
        print(f"  {i + 1}/{len(files)}")

df = pd.DataFrame(rows)
out = BASE / "ml" / "reports" / "bt_rere_shallow_wash.csv"
df.to_csv(out, index=False, encoding="utf-8-sig")

print(f"\n=== BT-rere-shallow-wash(adj 模式,訊號 {len(df)} 筆)===")
band_all = df[df["grp"].isin(("BAND", "BAND_TRUST"))]["ret"].dropna()
for name, v in (("BASE(>=10%,現行)", df[df["grp"] == "BASE"]["ret"].dropna()),
                ("BAND(8-10%,全部)", band_all),
                ("BAND 無投信", df[df["grp"] == "BAND"]["ret"].dropna()),
                ("BAND+投信5日買", df[df["grp"] == "BAND_TRUST"]["ret"].dropna())):
    if not len(v):
        continue
    tail = (v < -0.20).mean() * 100
    print(f"{name:18}: n={len(v):5} 平均{v.mean()*100:+.2f}% 中位{v.median()*100:+.2f}% "
          f"勝率{(v>0).mean()*100:.1f}% 左尾<-20%占{tail:.1f}%")

print("\n逐年 BAND全部 vs BASE(平均/勝率):")
for y in sorted(df["year"].unique()):
    b = df[(df["year"] == y) & (df["grp"].isin(("BAND", "BAND_TRUST")))]["ret"].dropna()
    s = df[(df["year"] == y) & (df["grp"] == "BASE")]["ret"].dropna()
    bs = f"{b.mean()*100:+6.2f}%/{(b>0).mean()*100:4.1f}% (n={len(b)})" if len(b) else "n=0"
    ss = f"{s.mean()*100:+6.2f}%/{(s>0).mean()*100:4.1f}% (n={len(s)})" if len(s) else "n=0"
    print(f"  {y}: BAND {bs:28} | BASE {ss}")
print(f"\n明細 -> {out}")
