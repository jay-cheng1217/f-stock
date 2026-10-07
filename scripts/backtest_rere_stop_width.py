# -*- coding: utf-8 -*-
"""BT-rere-stop-width:rere lane 停損寬度回測。

第一輪(2026-10-07 上午,探索性):前瞻帳本 7/1 起 298 筆照規則(收盤破 MA60×0.97)均 −2.01%/勝率 33.9%,同批不停損 +5.49%/53.0%;
六年樣本顯示停損越寬平均與勝率越高。
第二輪(2026-10-07 下午,**PM 裁示「放寬到 −40」的落地前驗證,判準事前寫死**):
  新規則 = 還原收盤 < 訊號日收盤 × (1 − 0.40) 即出場(帳本與卡片的失效價在訊號日就要定,所以以訊號日收盤為基準)。
  採用判準(−40% 對現行 MA60×0.97;任一 FAIL 即回報 PM,不落地):
    1. 平均報酬 >= 現行 + 1.5pp
    2. 勝率 >= 現行 + 5pp
    3. 逐年(7 年)平均較現行高的年份 >= 5
    4. 最差 1% 不低於 −45%(確認災難停損有把尾部框住;收盤判定會有跳空穿價)
    5. 單筆最大虧損不低於 −60%
  同時列出 −20%/−30%/不停損供對照。**不含已下市股票,寬停損與不停損的結果因此偏樂觀。**

口徑:rere v2 訊號(wash>=8% + |v20|<=5% + 量比<1.2 + close>MA60 + 均量>=500張),次日收盤進、完整 60 棒、同檔 20 日 cooldown、毛報酬。
報酬序列用 ml.corporate_actions 的經濟報酬因子(label_factor)做連續還原——除權息、減資、面額變更、分割都還原;
初版只加回現金股利,面額變更(如 5314 於 2025-03-31 ×0.05)會被算成 −94% 的假虧損(2026-10-07 下午查證後修正)。影響範圍只限 rere lane;主 lane(MA20×0.96)、Champion rule 14(−10%)、持股風險信皆不在此票。

用法:python -X utf8 scripts/backtest_rere_stop_width.py   (worktree 無日K時設 STOCK_BASE_DIR 指向主工作目錄)
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
BASE = Path(os.environ.get("STOCK_BASE_DIR") or REPO)
DAILY = BASE / "日K資料"
HOLD, COOLDOWN = 60, 20
CURRENT, NEW = "MA60×0.97(現行)", "訊號收盤−40%(PM裁示)"
# (名稱, 類型, 參數):ma60 = 訊號日 MA60×倍數;pct = 訊號日收盤×(1−pct);none = 不停損
STOPS = [(CURRENT, "ma60", 0.97), ("MA60×0.90", "ma60", 0.90), ("訊號收盤−20%", "pct", 0.20), ("訊號收盤−30%", "pct", 0.30),
         (NEW, "pct", 0.40), ("不停損", "none", None)]

sys.path.insert(0, str(REPO))
from ml.corporate_actions import backward_adjustment_multiplier, load_action_calendar  # noqa: E402

ACTIONS = load_action_calendar(str(BASE / "ml" / "data" / "ex_dividend_calendar.csv"),
                               str(BASE / "ml" / "data" / "corporate_action_price_factors.csv"))
ACTION_TICKERS = set(ACTIONS["stock_id"].astype(str))

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
    n, last = len(k), -10 ** 6
    cv, dates = c.to_numpy(float), k.Date.to_numpy()
    # 連續還原:事件日之前的價格乘上其後所有事件的經濟報酬因子,序列上的漲跌即含權息的真實報酬
    mult = (backward_adjustment_multiplier(k["Date"], tk, ACTIONS).to_numpy(float) if tk in ACTION_TICKERS
            else np.ones(n))
    av = cv * mult
    for t in np.where(mask)[0]:
        if t - last < COOLDOWN or t + 1 + HOLD >= n:
            continue
        e = t + 1
        entry = av[e]
        if not np.isfinite(entry) or entry <= 0 or not np.isfinite(ma60.iloc[t]):
            continue
        last = t
        adj = av[e + 1:e + HOLD + 1]
        rec = {"ticker": tk, "signal_date": dates[t], "year": dates[t][:4], "mdd": adj.min() / entry - 1}
        for name, kind, arg in STOPS:
            if kind == "none":
                rec[name] = adj[-1] / entry - 1
                continue
            # 停損價在訊號日以原始價定義,比較時換到同一還原基準
            level = (ma60.iloc[t] * arg if kind == "ma60" else cv[t] * (1 - arg)) * mult[t]
            hit = np.where(adj < level)[0]
            rec[name] = (adj[hit[0]] if len(hit) else adj[-1]) / entry - 1
        rows.append(rec)

d = pd.DataFrame(rows)
print(f"=== BT-rere-stop-width 訊號 {len(d)},{d.year.min()}~{d.year.max()} ===")
for name, _, _ in STOPS:
    x = d[name]
    print(f"{name:22} 均{x.mean()*100:+6.2f}% 中位{x.median()*100:+6.2f}% 勝率{(x>0).mean()*100:5.1f}% 跌逾20%:{(x<-0.2).mean()*100:5.1f}% "
          f"跌逾30%:{(x<-0.3).mean()*100:4.1f}% 最差5%{x.quantile(.05)*100:+6.1f}% 最差1%{x.quantile(.01)*100:+6.1f}% 最大虧損{x.min()*100:+6.1f}% 大賺>30%:{(x>0.3).mean()*100:5.1f}%")
print(f"不停損持有期間最深回落:中位 {d.mdd.median()*100:.1f}% | 曾回落逾 20% {(d.mdd<-0.2).mean()*100:.1f}% | 曾回落逾 40% {(d.mdd<-0.4).mean()*100:.2f}%")
print(f"−40% 規則實際被觸發的比例:{(d[NEW] != d['不停損']).mean()*100:.2f}%")
print("\n逐年 平均 / 最差1%:現行 vs −40%")
yrs_ok = 0
for y, g in d.groupby("year"):
    a, b = g[CURRENT], g[NEW]
    yrs_ok += int(b.mean() > a.mean())
    print(f"  {y}: 現行 {a.mean()*100:+6.2f}% / {a.quantile(.01)*100:+6.1f}%   −40% {b.mean()*100:+6.2f}% / {b.quantile(.01)*100:+6.1f}%   (n={len(g)})")
cur, new = d[CURRENT], d[NEW]
checks = [(f"1 平均 >= 現行 + 1.5pp (實得 {(new.mean()-cur.mean())*100:+.2f}pp)", (new.mean() - cur.mean()) * 100 >= 1.5),
          (f"2 勝率 >= 現行 + 5pp (實得 {((new>0).mean()-(cur>0).mean())*100:+.1f}pp)", ((new > 0).mean() - (cur > 0).mean()) * 100 >= 5),
          (f"3 逐年較現行高 >= 5/7 (實得 {yrs_ok}/{d.year.nunique()})", yrs_ok >= 5),
          (f"4 最差 1% >= −45% (實得 {new.quantile(.01)*100:+.1f}%)", new.quantile(.01) * 100 >= -45),
          (f"5 單筆最大虧損 >= −60% (實得 {new.min()*100:+.1f}%)", new.min() * 100 >= -60)]
print("\n採用判準(−40% 對現行):")
for name, ok in checks:
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
print("判決:", "PASS — 依 PM 裁示落地" if all(ok for _, ok in checks) else "FAIL — 回報 PM,不落地")
worst = d.nsmallest(8, NEW)[["ticker", "signal_date", NEW, CURRENT, "不停損", "mdd"]]
print("\n−40% 規則下最差 8 筆(確認是真實下跌而非資料斷點):")
print((worst.assign(**{c: (worst[c] * 100).round(1) for c in (NEW, CURRENT, "不停損", "mdd")})).to_string(index=False))
