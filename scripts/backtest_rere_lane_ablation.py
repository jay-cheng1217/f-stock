# -*- coding: utf-8 -*-
"""BT-rere-lane-ablation:現行蹲點型逐條件拆解(重構選項的事前診斷,不改任何 gate)。

問題:#6/#7 兩次回測都顯示蹲點型(深洗盤>=10% + 外資前5日賣→當日買)跑輸「貼線量縮」對照組,
左尾 2.5 倍。本腳本拆開看是哪個條件造成,並量測幾個候選替代條件。

口徑同帳本:次日收盤進、MA60×0.97 停損、完整 60 棒、除息還原;各組同檔 20 日 cooldown。
COMMON = |v20|<=5% + 量比<1.2 + close>MA60 + 20日均量>=500張。

用法:python -X utf8 scripts/backtest_rere_lane_ablation.py
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
HOLD, COOLDOWN = 60, 20

cal = pd.read_csv(BASE / "ml" / "data" / "ex_dividend_calendar.csv", dtype={"stock_id": str}, encoding="utf-8-sig")
cal["dv"] = pd.to_numeric(cal["stock_and_cache_dividend"], errors="coerce").fillna(0.0)
cal["date"] = cal["date"].astype(str).str[:10]
CAL = {tk: g[["date", "dv"]].values.tolist() for tk, g in cal.groupby("stock_id")}

idx = pd.read_csv(BASE / "大盤指數" / "index_TWII.csv", usecols=["Date", "Close"])
idx["Date"] = idx["Date"].astype(str).str[:10]
idx = idx.sort_values("Date")
idx["bull"] = idx["Close"] > idx["Close"].rolling(60).mean()
BULL = dict(zip(idx["Date"], idx["bull"]))

TECH_SECTORS = {"半導體業", "電子零組件業", "光電業", "電腦及週邊設備業", "其他電子業", "通信網路業",
                "電子通路業", "資訊服務業"}   # 證交所「電子類」八個子產業
_sec = pd.read_csv(BASE / "ml" / "data" / "sector_mapping.csv", dtype=str, encoding="utf-8-sig")
SECTOR = dict(zip(_sec["Ticker"], _sec["Sector"]))   # 現況分類(非 point-in-time)
# 電子/半導體原物料供應商中,被交易所歸在化學/塑膠/玻璃/電機而未進電子八類者(人工清單,依公開業務認知,未逐檔查證營收占比)
MATERIAL_EXTRA = {"1303", "1802", "4722", "1717", "1711", "4755", "1773", "4772", "4770", "1560", "4763", "1727", "4739"}


def _ret(k, c, ma60, events, t, n, stop_mult=0.97):
    e = t + 1
    if e + HOLD >= n:
        return None
    entry = float(c.iloc[e])
    if not np.isfinite(entry) or entry <= 0:
        return None
    stop = float(ma60.iloc[t]) * stop_mult if np.isfinite(ma60.iloc[t]) else np.nan
    entry_date = k["Date"].iloc[e]
    for j in range(e + 1, e + HOLD + 1):
        cum = sum(dv for d, dv in events if entry_date < d <= k["Date"].iloc[j])
        px = float(c.iloc[j]) + cum
        if np.isfinite(stop) and px < stop:
            return px / entry - 1
    cum_end = sum(dv for d, dv in events if entry_date < d <= k["Date"].iloc[e + HOLD])
    return (float(c.iloc[e + HOLD]) + cum_end) / entry - 1


rows: list[dict] = []
for f in sorted(DAILY.glob("*.csv")):
    tk = f.stem
    if not (tk.isdigit() and len(tk) == 4 and tk[0] != "0"):
        continue
    try:
        k = pd.read_csv(f, usecols=["Date", "Close", "Volume", "Foreign_BuySell", "Trust_BuySell",
                                    "VOL_MA_20", "MA_20", "MA_60"])
    except Exception:
        continue
    if len(k) < 90:
        continue
    k["Date"] = k["Date"].astype(str).str[:10]
    c = pd.to_numeric(k["Close"], errors="coerce")
    vol = pd.to_numeric(k["Volume"], errors="coerce")
    vma = pd.to_numeric(k["VOL_MA_20"], errors="coerce")
    ma20 = pd.to_numeric(k["MA_20"], errors="coerce")
    ma60 = pd.to_numeric(k["MA_60"], errors="coerce")
    fr = pd.to_numeric(k["Foreign_BuySell"], errors="coerce").fillna(0)
    wash = 1 - c / c.rolling(10).max()
    v20 = c / ma20 - 1
    vr = vol / vma
    turn = (fr.shift(1).rolling(5).sum() < 0) & (fr > 0)
    ma60_up = ma60 > ma60.shift(20)                    # 季線上揚
    above60 = c / ma60 - 1
    common = (v20.abs() <= 0.05) & (vr < 1.2) & (c > ma60) & (vma >= 500_000)
    groups = {
        "CONTROL 貼線量縮": common,
        "A 只加深洗盤>=10%": common & (wash >= 0.10),
        "B 只加外資拐點": common & turn,
        "BASE 深洗盤+拐點(現行)": common & (wash >= 0.10) & turn,
        "C 洗盤<4%": common & (wash < 0.04),
        "C 洗盤4-8%": common & (wash >= 0.04) & (wash < 0.08),
        "C 洗盤8-10%": common & (wash >= 0.08) & (wash < 0.10),
        "D 淺洗盤4-10%+拐點": common & (wash >= 0.04) & (wash < 0.10) & turn,
        "E CONTROL+季線上揚": common & ma60_up,
        "F CONTROL+離季線>=5%": common & (above60 >= 0.05),
        "G BASE+季線上揚": common & (wash >= 0.10) & turn & ma60_up,
    }
    n, events = len(k), CAL.get(tk, [])
    for name, mask in groups.items():
        last = -10_000
        for t in np.where(mask.fillna(False).values)[0]:
            if t - last < COOLDOWN:
                continue
            r = _ret(k, c, ma60, events, t, n)
            if r is None:
                continue
            last = t
            d = k["Date"].iloc[t]
            rows.append({"grp": name, "year": d[:4], "bull": BULL.get(d), "ret": r, "sec": SECTOR.get(tk, ""), "tk": tk})
    # 停損變體:BASE 條件、停損改 MA60×1.00 / 0.94
    for mult, label in ((1.00, "H BASE 停損改MA60×1.00"), (0.94, "H BASE 停損改MA60×0.94")):
        last = -10_000
        for t in np.where((common & (wash >= 0.10) & turn).fillna(False).values)[0]:
            if t - last < COOLDOWN:
                continue
            r = _ret(k, c, ma60, events, t, n, stop_mult=mult)
            if r is None:
                continue
            last = t
            d = k["Date"].iloc[t]
            rows.append({"grp": label, "year": d[:4], "bull": BULL.get(d), "ret": r, "sec": SECTOR.get(tk, ""), "tk": tk})

df_all = pd.DataFrame(rows)


def line(name, v):
    if not len(v):
        return f"{name:26} n=0"
    return (f"{name:26} n={len(v):6} 平均{v.mean()*100:+6.2f}% 中位{v.median()*100:+6.2f}% 勝率{(v>0).mean()*100:5.1f}% "
            f"左尾<-20%:{(v<-0.20).mean()*100:4.1f}% 大賺>+30%:{(v>0.30).mean()*100:4.1f}%")


UNIVERSES = [
    ("全市場", df_all["sec"].notna()),
    ("科技股(電子八類)", df_all["sec"].isin(TECH_SECTORS)),
    ("半導體業", df_all["sec"] == "半導體業"),
    ("非科技股", ~df_all["sec"].isin(TECH_SECTORS)),
    ("原物料供應商(未列入電子類的13檔)", df_all["tk"].isin(MATERIAL_EXTRA)),
    ("科技股+這13檔", df_all["sec"].isin(TECH_SECTORS) | df_all["tk"].isin(MATERIAL_EXTRA)),
]
KEY = ("CONTROL 貼線量縮", "BASE 深洗盤+拐點(現行)", "E CONTROL+季線上揚")
for uname, umask in UNIVERSES:
    df = df_all[umask]
    print()
    print(f"=== BT-rere-lane-ablation|{uname}(完整60棒,{len(df)} 筆)===")
    for g in df_all["grp"].drop_duplicates():
        print(line(g, df[df.grp == g]["ret"]))
    print("--- 依大盤(加權>MA60=多頭)拆分 ---")
    for g in KEY:
        for flag, lab in ((True, "多頭"), (False, "空頭")):
            print(line(f"{g[:14]}|{lab}", df[(df.grp == g) & (df.bull == flag)]["ret"]))
    print("--- 逐年 平均報酬:CONTROL / BASE / E ---")
    for y in sorted(df_all["year"].unique()):
        out = []
        for g in KEY:
            v = df[(df.grp == g) & (df.year == y)]["ret"]
            out.append(f"{v.mean()*100:+6.2f}%(n={len(v)})" if len(v) else "n=0")
        print(f"  {y}: " + " | ".join(out))
