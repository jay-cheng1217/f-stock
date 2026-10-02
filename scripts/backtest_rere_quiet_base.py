# -*- coding: utf-8 -*-
"""BT-rere-quiet-base:「法人靜默的極度量縮整理」型回測(她 2026-09 這批的形狀)。

觸發:2026-10-02 逐日條件重算——台表科 6278(9/15-17)、瑞耘 6532(9/15-16)在她布局期的
共同點不是「外資淨買」(9/30 BT-rere-pre-trust-accumulation 已判無 edge),而是
**外資、投信幾乎沒有進出 + 量縮到均量的 0.3~0.45 倍 + 自 10 日高回落 6~11% + 貼 MA20 + 站上 MA60**。

條件(point-in-time,訊號日 t):
  COMMON = |v20| <= 5% + vr < 1.2 + close > MA60 + 20日均量 >= 500 張
  DRY    = COMMON + vr < 0.5(極度量縮)
  FLOWQ  = COMMON + (|外資5日淨額| + |投信5日淨額|) <= 5日成交量的 3%(法人靜默)
  QUIET  = COMMON + vr < 0.5 + 法人靜默 + 5% <= wash <= 11%(本型)
  BASE   = 現行蹲點型(wash>=10% + 外資前5日賣→當日買)
慣例同帳本:次日收盤進、MA60×0.97 停損、60 個交易棒、除息還原;同檔同組 20 日 cooldown。

事前判準(跑之前寫死,2026-10-02):QUIET 要開子型態,須同時滿足
  (1) n >= 300;(2) 平均報酬 >= CONTROL + 1.5pp;(3) 勝率 >= CONTROL + 3pp;
  (4) 左尾(<-20%)占比 <= CONTROL。任一不過 = 不開子型態、不改 gate。

用法:python -X utf8 scripts/backtest_rere_quiet_base.py
輸出:ml/reports/bt_rere_quiet_base.csv + stdout
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

MA20_BAND, VOL_MAX, HOLD, COOLDOWN = 0.05, 1.2, 60, 20
DRY_VR, FLOW_QUIET_RATIO, WASH_LO, WASH_HI = 0.5, 0.03, 0.05, 0.11

cal = pd.read_csv(CAL_PATH, dtype={"stock_id": str}, encoding="utf-8-sig")
cal["dv"] = pd.to_numeric(cal["stock_and_cache_dividend"], errors="coerce").fillna(0.0)
cal["date"] = cal["date"].astype(str).str[:10]
CAL = {tk: g[["date", "dv"]].values.tolist() for tk, g in cal.groupby("stock_id")}


def _ret(k, c, ma60, events, t, n):
    e_idx = t + 1
    if e_idx + HOLD >= n:          # 只收完整 60 棒觀察機會的樣本(skill 公平比較慣例)
        return None
    entry = float(c.iloc[e_idx])
    if not np.isfinite(entry) or entry <= 0:
        return None
    stop = float(ma60.iloc[t]) * 0.97 if np.isfinite(ma60.iloc[t]) else np.nan
    end = e_idx + HOLD
    entry_date = k["Date"].iloc[e_idx]
    for j in range(e_idx + 1, end + 1):
        cum = sum(dv for d, dv in events if entry_date < d <= k["Date"].iloc[j])
        px = float(c.iloc[j]) + cum
        if np.isfinite(stop) and px < stop:
            return px / entry - 1
    cum_end = sum(dv for d, dv in events if entry_date < d <= k["Date"].iloc[end])
    return (float(c.iloc[end]) + cum_end) / entry - 1


def run_one(tk: str, k: pd.DataFrame, rows: list) -> None:
    k["Date"] = k["Date"].astype(str).str[:10]
    c = pd.to_numeric(k["Close"], errors="coerce")
    vol = pd.to_numeric(k["Volume"], errors="coerce")
    vma = pd.to_numeric(k["VOL_MA_20"], errors="coerce")
    ma20 = pd.to_numeric(k["MA_20"], errors="coerce")
    ma60 = pd.to_numeric(k["MA_60"], errors="coerce")
    fr = pd.to_numeric(k["Foreign_BuySell"], errors="coerce").fillna(0)
    tr = pd.to_numeric(k.get("Trust_BuySell"), errors="coerce").fillna(0)

    wash = 1 - c / c.rolling(10).max()
    v20 = c / ma20 - 1
    vr = vol / vma
    vol5 = vol.rolling(5).sum()
    flow_quiet = (fr.rolling(5).sum().abs() + tr.rolling(5).sum().abs()) <= FLOW_QUIET_RATIO * vol5
    fr_prev5 = fr.shift(1).rolling(5).sum()

    common = (v20.abs() <= MA20_BAND) & (vr < VOL_MAX) & (c > ma60) & (vma >= 500_000)
    groups = {
        "CONTROL": common,
        "DRY": common & (vr < DRY_VR),
        "FLOWQ": common & flow_quiet,
        "QUIET": common & (vr < DRY_VR) & flow_quiet & (wash >= WASH_LO) & (wash <= WASH_HI),
    }
    n = len(k)
    events = CAL.get(tk, [])
    for name, mask in groups.items():
        last_t = -10_000
        for t in np.where(mask.fillna(False).values)[0]:
            if t - last_t < COOLDOWN:
                continue
            r = _ret(k, c, ma60, events, t, n)
            if r is None:
                continue
            last_t = t
            rows.append({"ticker": tk, "signal_date": k["Date"].iloc[t], "year": k["Date"].iloc[t][:4],
                         "grp": name, "ret": r})
    base = common & (wash >= 0.10) & (fr_prev5 < 0) & (fr > 0)
    for t in np.where(base.fillna(False).values)[0]:
        r = _ret(k, c, ma60, events, t, n)
        if r is None:
            continue
        rows.append({"ticker": tk, "signal_date": k["Date"].iloc[t], "year": k["Date"].iloc[t][:4],
                     "grp": "BASE", "ret": r})


rows: list[dict] = []
files = sorted(DAILY.glob("*.csv"))
for i, f in enumerate(files):
    tk = f.stem
    if not (tk.isdigit() and len(tk) == 4 and tk[0] != "0"):
        continue
    try:
        k = pd.read_csv(f, usecols=["Date", "Close", "Volume", "Foreign_BuySell",
                                    "Trust_BuySell", "VOL_MA_20", "MA_20", "MA_60"])
    except Exception:
        continue
    if len(k) < 90:
        continue
    run_one(tk, k, rows)

df = pd.DataFrame(rows)
out = BASE / "ml" / "reports" / "bt_rere_quiet_base.csv"
df.to_csv(out, index=False, encoding="utf-8-sig")


def _stat(v):
    return {"n": len(v), "mean": v.mean() * 100, "med": v.median() * 100,
            "win": (v > 0).mean() * 100, "tail": (v < -0.20).mean() * 100,
            "big": (v > 0.30).mean() * 100}


def _line(name, v):
    if not len(v):
        return f"{name:26}: n=0"
    s = _stat(v)
    return (f"{name:26}: n={s['n']:6} 平均{s['mean']:+.2f}% 中位{s['med']:+.2f}% 勝率{s['win']:.1f}% "
            f"左尾<-20%占{s['tail']:.1f}% 大賺>+30%占{s['big']:.1f}%")


print(f"\n=== BT-rere-quiet-base(完整60棒樣本,訊號 {len(df)} 筆,{df.signal_date.min()} ~ {df.signal_date.max()})===")
for g, label in [("BASE", "BASE(蹲點型現行)"), ("CONTROL", "CONTROL(貼線量縮)"),
                 ("DRY", "DRY(+極度量縮)"), ("FLOWQ", "FLOWQ(+法人靜默)"), ("QUIET", "QUIET(本型)")]:
    print(_line(label, df[df.grp == g]["ret"].dropna()))

q, ctl = df[df.grp == "QUIET"]["ret"].dropna(), df[df.grp == "CONTROL"]["ret"].dropna()
sq, sc = _stat(q), _stat(ctl)
checks = [("n >= 300", sq["n"] >= 300),
          ("平均 >= CONTROL + 1.5pp", sq["mean"] >= sc["mean"] + 1.5),
          ("勝率 >= CONTROL + 3pp", sq["win"] >= sc["win"] + 3.0),
          ("左尾 <= CONTROL", sq["tail"] <= sc["tail"])]
print("\n事前判準:")
for name, ok in checks:
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
print("判決:", "PASS 全過" if all(ok for _, ok in checks) else "FAIL — 不開子型態、不改 gate")

print("\n逐年 QUIET vs CONTROL(平均/勝率):")
for y in sorted(df["year"].unique()):
    a = df[(df["year"] == y) & (df["grp"] == "QUIET")]["ret"].dropna()
    s = df[(df["year"] == y) & (df["grp"] == "CONTROL")]["ret"].dropna()
    fa = f"{a.mean()*100:+6.2f}%/{(a>0).mean()*100:4.1f}% (n={len(a)})" if len(a) else "n=0"
    fs = f"{s.mean()*100:+6.2f}%/{(s>0).mean()*100:4.1f}% (n={len(s)})" if len(s) else "n=0"
    print(f"  {y}: QUIET {fa:28} | CONTROL {fs}")
print(f"\n明細 -> {out}")
