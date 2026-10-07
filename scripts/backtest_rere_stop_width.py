# -*- coding: utf-8 -*-
"""BT-rere-stop-width(探索性,2026-10-07):rere 蹲點型在不同停損寬度下的 60 棒結果。

起因:PM 問「為什麼她績效那麼好、檢討系統」。前瞻帳本 7/1 起 295 筆:照規則(收盤破 MA60×0.97 停損)均 −2.03%/勝率 34.2%,
同批不停損抱到 60 棒/現在 均 +5.26%/52.9%;被停損的 158 筆中 62 筆後來漲回進場價 +10% 以上。本腳本用六年樣本檢查這是否只是近期現象。

口徑:蹲點型 v2 條件(wash≥10% + |v20|≤5% + 量比<1.2 + close>MA60 + 均量≥500張),次日收盤進、完整 60 棒、同檔 20 日 cooldown、除息加回、
毛報酬。停損=還原收盤 < MA60(訊號日)×倍數 即以當日收盤出。**未事前登記判準;本地檔不含已下市股票,不停損組的結果因此偏樂觀;
不可直接據以改 gate。** 帳本慣例與 rule 14(−10% 強制停損)屬 PM 決策範圍。

用法:python -X utf8 scripts/backtest_rere_stop_width.py
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
STOPS = {"MA60×0.97(現行)": 0.97, "MA60×0.94": 0.94, "MA60×0.90": 0.90, "MA60×0.85": 0.85, "不停損": None}

cal = pd.read_csv(BASE / "ml" / "data" / "ex_dividend_calendar.csv", dtype={"stock_id": str}, encoding="utf-8-sig")
cal["dv"] = pd.to_numeric(cal["stock_and_cache_dividend"], errors="coerce").fillna(0.0)
cal["date"] = cal["date"].astype(str).str[:10]
CAL = {tk: g[["date", "dv"]].values.tolist() for tk, g in cal.groupby("stock_id")}

rows = []
for f in sorted(DAILY.glob("*.csv")):
    tk = f.stem
    if not (tk.isdigit() and len(tk) == 4 and tk[0] != "0"):
        continue
    try:
        k = pd.read_csv(f, usecols=["Date", "Close", "Volume", "VOL_MA_20", "MA_20", "MA_60"])
    except Exception:
        continue
    if len(k) < 90:
        continue
    k["Date"] = k["Date"].astype(str).str[:10]
    c = pd.to_numeric(k.Close, errors="coerce")
    vol, vma = pd.to_numeric(k.Volume, errors="coerce"), pd.to_numeric(k.VOL_MA_20, errors="coerce")
    ma20, ma60 = pd.to_numeric(k.MA_20, errors="coerce"), pd.to_numeric(k.MA_60, errors="coerce")
    wash, v20, vr = 1 - c / c.rolling(10).max(), c / ma20 - 1, vol / vma
    mask = ((v20.abs() <= 0.05) & (vr < 1.2) & (c > ma60) & (vma >= 500_000) & (wash >= 0.10)).fillna(False).values
    events, n, last = CAL.get(tk, []), len(k), -10 ** 6
    cv, dates = c.to_numpy(float), k.Date.to_numpy()
    for t in np.where(mask)[0]:
        if t - last < COOLDOWN or t + 1 + HOLD >= n:
            continue
        e = t + 1
        entry = cv[e]
        if not np.isfinite(entry) or entry <= 0 or not np.isfinite(ma60.iloc[t]):
            continue
        last = t
        adj = np.array([cv[j] + sum(dv for d, dv in events if dates[e] < d <= dates[j]) for j in range(e + 1, e + HOLD + 1)])
        rec = {"year": dates[t][:4], "mdd": adj.min() / entry - 1}
        for name, mult in STOPS.items():
            if mult is None:
                rec[name] = adj[-1] / entry - 1
            else:
                hit = np.where(adj < ma60.iloc[t] * mult)[0]
                rec[name] = (adj[hit[0]] if len(hit) else adj[-1]) / entry - 1
        rows.append(rec)

d = pd.DataFrame(rows)
print(f"=== BT-rere-stop-width(探索性)訊號 {len(d)},{d.year.min()}~{d.year.max()} ===")
for name in STOPS:
    x = d[name]
    print(f"{name:16} 均{x.mean()*100:+6.2f}% 中位{x.median()*100:+6.2f}% 勝率{(x>0).mean()*100:5.1f}% 跌逾20%:{(x<-0.2).mean()*100:5.1f}% "
          f"最差5%{x.quantile(.05)*100:+6.1f}% 最差1%{x.quantile(.01)*100:+6.1f}% 大賺>30%:{(x>0.3).mean()*100:5.1f}%")
print(f"不停損持有期間最深回落:中位 {d.mdd.median()*100:.1f}% | 曾回落逾 20% 的比例 {(d.mdd<-0.2).mean()*100:.1f}%")
print("\n逐年 平均 / 最差5%:現行 vs 不停損")
for y, g in d.groupby("year"):
    a, b = g["MA60×0.97(現行)"], g["不停損"]
    print(f"  {y}: 現行 {a.mean()*100:+6.2f}% / {a.quantile(.05)*100:+6.1f}%   不停損 {b.mean()*100:+6.2f}% / {b.quantile(.05)*100:+6.1f}%   (n={len(g)})")
