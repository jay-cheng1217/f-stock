# -*- coding: utf-8 -*-
"""驗證盤前當沖篩選池「隔天」的實際表現。

當沖無法從日K精確重建(進出點在日內),但可測兩個代理:
  1. 隔日「開盤買→收盤賣」報酬(當沖若不停損的日內持有近似)
  2. 隔日「開盤買→當日最高」的最大有利波動(MFE,當沖最好情況上限)
  3. 隔日振幅(高-低)/開盤 = 有沒有得沖的空間
對照組:全市場當日均值。看篩選池是否比隨機好。
"""
import glob
import io
import os
import sys

import numpy as np
import pandas as pd

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
os.chdir(r"F:\stock")

MIN_LOTS, MIN_VR, MIN_WASH, MIN_ATR = 3000, 1.2, 0.08, 2.5
START = "2026-01-01"  # 回測期

pool_o2c, pool_mfe, pool_range = [], [], []
allm_o2c, allm_range = [], []

for p in glob.glob("日K資料/*.csv"):
    tk = os.path.splitext(os.path.basename(p))[0]
    if not tk.isdigit() or len(tk) != 4:
        continue
    try:
        k = pd.read_csv(p, usecols=["Date", "Open", "Close", "High", "Low", "Volume",
                                    "VOL_MA_20", "ATR_14"], encoding="utf-8-sig")
    except Exception:
        continue
    if len(k) < 40:
        continue
    k["Date"] = k["Date"].astype(str).str[:10]
    c = pd.to_numeric(k["Close"], errors="coerce")
    o = pd.to_numeric(k["Open"], errors="coerce")
    h = pd.to_numeric(k["High"], errors="coerce")
    lo = pd.to_numeric(k["Low"], errors="coerce")
    vol = pd.to_numeric(k["Volume"], errors="coerce")
    vma = pd.to_numeric(k["VOL_MA_20"], errors="coerce")
    atr = pd.to_numeric(k["ATR_14"], errors="coerce")
    vol5 = vol.rolling(5).mean()
    hi10 = c.rolling(10).max()

    for i in range(20, len(k) - 1):
        if k["Date"].iloc[i] < START:
            continue
        # 全市場基準(隔日)
        no = o.iloc[i + 1]
        if no > 0:
            allm_o2c.append((c.iloc[i + 1] / no - 1) * 100)
            allm_range.append((h.iloc[i + 1] - lo.iloc[i + 1]) / no * 100)
        # 篩選池條件(用第 i 日收盤資料,盤前決策)
        avg5 = vol5.iloc[i] / 1000
        vr = vol.iloc[i] / vma.iloc[i] if vma.iloc[i] > 0 else np.nan
        wash = 1 - c.iloc[i] / hi10.iloc[i]
        atrp = atr.iloc[i] / c.iloc[i] * 100 if c.iloc[i] else np.nan
        if not (avg5 >= MIN_LOTS and vr >= MIN_VR and wash >= MIN_WASH and atrp >= MIN_ATR):
            continue
        if not (no > 0):
            continue
        pool_o2c.append((c.iloc[i + 1] / no - 1) * 100)          # 開買收賣
        pool_mfe.append((h.iloc[i + 1] / no - 1) * 100)          # 開買→當日高(最好情況)
        pool_range.append((h.iloc[i + 1] - lo.iloc[i + 1]) / no * 100)

p_o2c = pd.Series(pool_o2c); a_o2c = pd.Series(allm_o2c)
print(f"回測期 {START} 起,篩選池觸發 {len(p_o2c):,} 檔次 vs 全市場 {len(a_o2c):,} 檔次\n")
print("=== 隔日『開盤買→收盤賣』(當沖不停損的近似) ===")
print(f"  篩選池: 平均 {p_o2c.mean():+.3f}%  中位 {p_o2c.median():+.3f}%  勝率 {(p_o2c>0).mean()*100:.1f}%")
print(f"  全市場: 平均 {a_o2c.mean():+.3f}%  中位 {a_o2c.median():+.3f}%  勝率 {(a_o2c>0).mean()*100:.1f}%")
print(f"  → 篩選池 vs 全市場超額: {p_o2c.mean()-a_o2c.mean():+.3f}pp")
COST = 0.2
print(f"  → 扣當沖成本 {COST}%(手續費+稅)後篩選池平均: {p_o2c.mean()-COST:+.3f}%")

print("\n=== 隔日日內波動空間(有沒有得沖) ===")
print(f"  篩選池振幅中位 {pd.Series(pool_range).median():.2f}%  vs 全市場 {pd.Series(allm_range).median():.2f}%")
print(f"  篩選池 MFE(開→高)中位 {pd.Series(pool_mfe).median():+.2f}%  (最好情況上限)")

print("\n=== 分年 ===")
# 簡化:不分年,看極端
print(f"  篩選池隔日 <-3% 的比例: {(p_o2c<-3).mean()*100:.1f}%   >+3%: {(p_o2c>3).mean()*100:.1f}%")
print(f"  (振幅大是雙面刃:接刀接錯一樣快)")
