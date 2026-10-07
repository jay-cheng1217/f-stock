# -*- coding: utf-8 -*-
"""BT-rere-regime-sector:兩個重構候選的正式回測(PM 2026-10-07 核可排程;判準事前寫死,不改任何 gate)。

  B2 大盤濾網:訊號日加權指數收盤 <= 自身 60 日均線(空頭)時,rere 不開新倉。
  B1 產業濾網:rere 只做科技股(交易所電子八類),非科技股不做。

母體:rere v2 訊號(wash>=8% + |v20|<=5% + 量比<1.2 + close>MA60 + 均量>=500張;即 2026-10-07 上線的蹲點 v2 ∪ 淺洗盤 v2 ∪ 現行兩型),
同檔 20 日 cooldown,次日收盤進、完整 60 棒、除息加回、毛報酬。主口徑=現行停損(還原收盤 < MA60×0.97);另列不停損作穩健性。
產業=ml/data/sector_mapping.csv 現況分類(非 point-in-time);本地檔不含已下市股票。

事前判準(2026-10-07 寫死):
  B2(空頭組 vs 多頭組;全過才建議「空頭不開新倉」)
    1. 空頭平均 <= 多頭平均 − 1.5pp
    2. 空頭勝率 <= 多頭勝率
    3. 兩組各 n>=30 的年份中,空頭平均 < 多頭平均 的年份 >= 2/3
    4. 多頭訊號數 >= 全體 60%(濾網不能把 lane 砍到剩不到六成)
    5. 不停損口徑下,空頭平均 <= 多頭平均 − 1.0pp(結論不依賴出場規則)
  B1(科技組 vs 非科技組;全過才建議「只做科技股」)
    1. 科技平均 >= 非科技平均 + 1.5pp
    2. 科技勝率 >= 非科技勝率
    3. 兩組各 n>=30 的年份中,科技平均 > 非科技平均 的年份 >= 5/7
    4. 科技訊號數 >= 全體 50%
    5. 不停損口徑下,科技平均 >= 非科技平均 + 1.0pp
任一 FAIL = 不採用;PASS 也需 PM 核可並以獨立標籤並行驗證。

用法:python -X utf8 scripts/backtest_rere_regime_sector.py
輸出:ml/reports/bt_rere_regime_sector.csv + stdout
"""
from __future__ import annotations

import io
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
REPO = Path(__file__).resolve().parents[1]
BASE = Path(os.environ.get("STOCK_BASE_DIR") or REPO)   # 資料根目錄(worktree 沒有日K時指向主工作目錄);輸出一律寫回本 repo
DAILY = BASE / "日K資料"
HOLD, COOLDOWN = 60, 20
TECH = {"半導體業", "電子零組件業", "光電業", "電腦及週邊設備業", "其他電子業", "通信網路業", "電子通路業", "資訊服務業"}

cal = pd.read_csv(BASE / "ml" / "data" / "ex_dividend_calendar.csv", dtype={"stock_id": str}, encoding="utf-8-sig")
cal["dv"] = pd.to_numeric(cal["stock_and_cache_dividend"], errors="coerce").fillna(0.0)
cal["date"] = cal["date"].astype(str).str[:10]
CAL = {tk: g[["date", "dv"]].values.tolist() for tk, g in cal.groupby("stock_id")}
sec = pd.read_csv(BASE / "ml" / "data" / "sector_mapping.csv", dtype=str, encoding="utf-8-sig")
SECTOR = dict(zip(sec["Ticker"], sec["Sector"]))
idx = pd.read_csv(BASE / "大盤指數" / "index_TWII.csv", usecols=["Date", "Close"])
idx["Date"] = idx["Date"].astype(str).str[:10]
idx = idx.sort_values("Date")
idx["bull"] = idx["Close"] > idx["Close"].rolling(60).mean()
BULL = dict(zip(idx["Date"], idx["bull"]))

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
    mask = ((v20.abs() <= 0.05) & (vr < 1.2) & (c > ma60) & (vma >= 500_000) & (wash >= 0.08)).fillna(False).values
    events, n, last = CAL.get(tk, []), len(k), -10 ** 6
    cv, dates = c.to_numpy(float), k.Date.to_numpy()
    for t in np.where(mask)[0]:
        if t - last < COOLDOWN or t + 1 + HOLD >= n:
            continue
        e = t + 1
        entry = cv[e]
        if not np.isfinite(entry) or entry <= 0 or not np.isfinite(ma60.iloc[t]) or dates[t] not in BULL:
            continue
        last = t
        adj = np.array([cv[j] + sum(dv for d, dv in events if dates[e] < d <= dates[j]) for j in range(e + 1, e + HOLD + 1)])
        hit = np.where(adj < ma60.iloc[t] * 0.97)[0]
        rows.append({"ticker": tk, "signal_date": dates[t], "year": dates[t][:4], "bull": bool(BULL[dates[t]]),
                     "tech": SECTOR.get(tk, "") in TECH, "ret": (adj[hit[0]] if len(hit) else adj[-1]) / entry - 1,
                     "ret_nostop": adj[-1] / entry - 1})

df = pd.DataFrame(rows)
out = REPO / "ml" / "reports" / "bt_rere_regime_sector.csv"
df.to_csv(out, index=False, encoding="utf-8-sig")


def line(name, g):
    v, w = g["ret"], g["ret_nostop"]
    return (f"{name:14} n={len(g):6} | 現行停損 均{v.mean()*100:+6.2f}% 勝率{(v>0).mean()*100:5.1f}% 跌逾20%:{(v<-0.2).mean()*100:4.1f}% "
            f"| 不停損 均{w.mean()*100:+6.2f}% 勝率{(w>0).mean()*100:5.1f}%")


def verdict(title, good, bad, good_name, bad_name, min_share, year_need):
    print(f"\n=== {title} ===")
    print(line(good_name, good)); print(line(bad_name, bad))
    yrs_ok = yrs_n = 0
    print("  逐年(現行停損平均;兩組各 n>=30 才計):")
    for y in sorted(df["year"].unique()):
        a, b = good[good.year == y]["ret"], bad[bad.year == y]["ret"]
        counted = len(a) >= 30 and len(b) >= 30
        if counted:
            yrs_n += 1
            yrs_ok += int(a.mean() > b.mean())
        fa = f"{a.mean()*100:+6.2f}% (n={len(a)})" if len(a) else "n=0"
        fb = f"{b.mean()*100:+6.2f}% (n={len(b)})" if len(b) else "n=0"
        print(f"    {y}: {good_name} {fa:22} | {bad_name} {fb:22} {'' if counted else '(不計)'}")
    need = year_need(yrs_n)
    gm, bm = good["ret"].mean() * 100, bad["ret"].mean() * 100
    checks = [(f"1 {good_name} 平均 − {bad_name} 平均 >= 1.5pp (實得 {gm-bm:+.2f}pp)", gm - bm >= 1.5),
              (f"2 {good_name} 勝率 >= {bad_name} 勝率 ({(good.ret>0).mean()*100:.1f}% vs {(bad.ret>0).mean()*100:.1f}%)",
               (good.ret > 0).mean() >= (bad.ret > 0).mean()),
              (f"3 逐年 {good_name} 較高 >= {need}/{yrs_n} (實得 {yrs_ok}/{yrs_n})", yrs_ok >= need and yrs_n > 0),
              (f"4 {good_name} 訊號數占比 >= {min_share:.0%} (實得 {len(good)/len(df):.1%})", len(good) / len(df) >= min_share),
              (f"5 不停損口徑差距 >= 1.0pp (實得 {(good.ret_nostop.mean()-bad.ret_nostop.mean())*100:+.2f}pp)",
               (good.ret_nostop.mean() - bad.ret_nostop.mean()) * 100 >= 1.0)]
    for name, ok in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    passed = all(ok for _, ok in checks)
    print("  判決:", "PASS(仍需 PM 核可)" if passed else "FAIL — 不採用")
    return passed


print(f"=== BT-rere-regime-sector(完整60棒,{len(df)} 筆,{df.signal_date.min()} ~ {df.signal_date.max()})===")
print(line("全體", df))
b2 = verdict("B2 大盤濾網:多頭(加權>MA60) vs 空頭", df[df.bull], df[~df.bull], "多頭", "空頭", 0.60,
             lambda n: int(np.ceil(n * 2 / 3)))
b1 = verdict("B1 產業濾網:科技(電子八類) vs 非科技", df[df.tech], df[~df.tech], "科技", "非科技", 0.50,
             lambda n: min(5, n) if n >= 7 else int(np.ceil(n * 5 / 7)))
print("\n交叉(現行停損平均 / n):")
for tech in (True, False):
    for bull in (True, False):
        g = df[(df.tech == tech) & (df.bull == bull)]
        print(f"  {'科技' if tech else '非科技'}×{'多頭' if bull else '空頭'}: {g.ret.mean()*100:+6.2f}% 勝率{(g.ret>0).mean()*100:5.1f}% (n={len(g)}) | 不停損 {g.ret_nostop.mean()*100:+6.2f}%")
print(f"\n總結:B2 {'PASS' if b2 else 'FAIL'}、B1 {'PASS' if b1 else 'FAIL'};明細 -> {out}")
