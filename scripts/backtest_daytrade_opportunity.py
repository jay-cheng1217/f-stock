# -*- coding: utf-8 -*-
"""驗證篩選池「唯一賣點」的穩定性:選出的股票隔天是否真的『有得沖』?
不看方向(已證零 edge),只看:隔日日內振幅是否穩定 > 全市場,以及池子每天有沒有貨。"""
import glob
import io
import os
import sys

import numpy as np
import pandas as pd

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
os.chdir(r"F:\stock")

MIN_LOTS, MIN_VR, MIN_WASH, MIN_ATR = 3000, 1.2, 0.08, 2.5
START = "2026-01-01"
GOOD_RANGE = 4.0  # 隔日振幅 ≥4% 視為「有得沖」

# 逐日累積:每個交易日,篩選池 vs 全市場
per_day = {}   # date -> {"pool":[range...], "all":[range...]}
for p in glob.glob("日K資料/*.csv"):
    tk = os.path.splitext(os.path.basename(p))[0]
    if not tk.isdigit() or len(tk) != 4:
        continue
    try:
        k = pd.read_csv(p, usecols=["Date", "Open", "High", "Low", "Close", "Volume", "VOL_MA_20", "ATR_14"],
                        encoding="utf-8-sig")
    except Exception:
        continue
    if len(k) < 40:
        continue
    k["Date"] = k["Date"].astype(str).str[:10]
    c = pd.to_numeric(k["Close"], errors="coerce"); o = pd.to_numeric(k["Open"], errors="coerce")
    h = pd.to_numeric(k["High"], errors="coerce"); lo = pd.to_numeric(k["Low"], errors="coerce")
    vol = pd.to_numeric(k["Volume"], errors="coerce"); vma = pd.to_numeric(k["VOL_MA_20"], errors="coerce")
    atr = pd.to_numeric(k["ATR_14"], errors="coerce")
    vol5 = vol.rolling(5).mean(); hi10 = c.rolling(10).max()
    for i in range(20, len(k) - 1):
        di = k["Date"].iloc[i + 1]  # 進場日(隔日)
        if di < START:
            continue
        no = o.iloc[i + 1]
        if not (no > 0):
            continue
        rng = (h.iloc[i + 1] - lo.iloc[i + 1]) / no * 100
        d = per_day.setdefault(di, {"pool": [], "all": []})
        d["all"].append(rng)
        avg5 = vol5.iloc[i] / 1000
        vr = vol.iloc[i] / vma.iloc[i] if vma.iloc[i] > 0 else np.nan
        wash = 1 - c.iloc[i] / hi10.iloc[i]
        atrp = atr.iloc[i] / c.iloc[i] * 100 if c.iloc[i] else np.nan
        if avg5 >= MIN_LOTS and vr >= MIN_VR and wash >= MIN_WASH and atrp >= MIN_ATR:
            d["pool"].append(rng)

days = sorted(per_day)
sizes = [len(per_day[d]["pool"]) for d in days]
pool_med = [pd.Series(per_day[d]["pool"]).median() if per_day[d]["pool"] else np.nan for d in days]
all_med = [pd.Series(per_day[d]["all"]).median() for d in days]
pool_goodrate = [(pd.Series(per_day[d]["pool"]) >= GOOD_RANGE).mean() * 100 if per_day[d]["pool"] else np.nan for d in days]

print(f"回測 {days[0]} ~ {days[-1]}  共 {len(days)} 個交易日\n")
print("=== 1. 池子每天有沒有貨(空池率) ===")
empty = sum(1 for s in sizes if s == 0)
print(f"  池子大小: 中位 {int(np.median(sizes))} 檔  最小 {min(sizes)}  最大 {max(sizes)}")
print(f"  空池天數: {empty}/{len(days)} ({empty/len(days)*100:.1f}%)")
print(f"  <5檔的天數: {sum(1 for s in sizes if s<5)}/{len(days)}")

print("\n=== 2. 振幅優勢是否每天都成立(唯一賣點的穩定性) ===")
wins = sum(1 for pm, am in zip(pool_med, all_med) if pd.notna(pm) and pm > am)
valid = sum(1 for pm in pool_med if pd.notna(pm))
print(f"  篩選池振幅中位 > 全市場 的天數: {wins}/{valid} ({wins/valid*100:.1f}%)")
print(f"  篩選池振幅中位: 全期均值 {np.nanmean(pool_med):.2f}%  vs 全市場 {np.nanmean(all_med):.2f}%")
gr = np.nanmean(pool_goodrate)
print(f"  池內『隔日振幅≥{GOOD_RANGE}%(真有得沖)』比例: 平均 {gr:.1f}%")

print("\n=== 3. 逐月穩定度 ===")
dfm = pd.DataFrame({"date": days, "size": sizes, "pool_med": pool_med, "all_med": all_med, "goodrate": pool_goodrate})
dfm["ym"] = dfm["date"].str[:7]
for ym, g in dfm.groupby("ym"):
    print(f"  {ym}: 池均{g['size'].mean():4.0f}檔  振幅池{g['pool_med'].mean():.2f}%/市{g['all_med'].mean():.2f}%  "
          f"有得沖率{g['goodrate'].mean():.0f}%")
