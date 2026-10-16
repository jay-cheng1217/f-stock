# -*- coding: utf-8 -*-
"""BT-rere-breakout-add(回測票 #30):已持有的 rere 部位,在突破日加碼(金字塔)有沒有道理?

背景(PM 2026-10-16「全開」):她公開/加碼的時點是突破確認(洗盤 0、月線上 +10~39%、放量),系統抓的是突破前的回踩;
系統沒有加碼機制。#18 驗過「新進場追高」FAIL,但「對已持有部位於突破日加碼」是不同問題:持有者已有部位當緩衝,
問題只剩突破後 60 根內的續漲有沒有優勢。

母體:rere v2 蹲點/淺洗盤訊號(洗盤 >= 8%、|距月線| <= 5%、量比 < 1.2、收 > 季線、均量 >= 500 張;不設 cooldown),
  進場 e = 訊號次日收盤,持有到 e+60。突破日 k = e 之後 30 根內,第一個「還原收盤創近 20 根新高且量比 >= 1.5」的日子。
  殘餘報酬 = adj[e+60] / adj[k] − 1(= 加碼單位從加碼日到期的報酬)。
  對照 = 同一偏移 d=k−e 下,**沒有突破**的訊號從 e+d 到 e+60 的平均報酬(逐 d 計算),
  每筆突破訊號的超額 = 殘餘報酬 − 對照[d]。
事前判準(2026-10-16 寫死,先於執行提交;四項全過 = 建議設計加碼規則,仍需 PM 核可):
  1. 突破訊號的平均超額 >= +2.0pp
  2. 2020-2022 與 2023-2026 兩段平均超額都 >= +1.0pp
  3. 逐年平均超額為正的年份 >= 5/7
  4. 發生突破的訊號占比在 15%~70% 之間(太少=規則幾乎不觸發,太多=突破定義太鬆)
另列(不判定):加碼後整體部位(原 1 單位 + 加碼 1 單位)60 根總報酬對原部位的差、加碼單位勝率、左尾。
限制:不含已下市股票;樣本日重疊;突破定義只用一種,不掃參數(掃了就是資料窺探)。

用法:python -X utf8 scripts/backtest_rere_breakout_add.py   (worktree 無日K時設 STOCK_BASE_DIR)
輸出:ml/reports/bt_rere_breakout_add.csv + stdout
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
HOLD, ADD_WINDOW = 60, 30
COLS = ["Date", "Close", "Volume", "VOL_MA_20", "MA_20", "MA_60"]
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
        k = pd.read_csv(f, usecols=lambda c: c in COLS)
    except Exception:
        continue
    if len(k) < 150 or not set(COLS) <= set(k.columns):
        continue
    k["Date"] = k["Date"].astype(str).str[:10]
    num = {c: pd.to_numeric(k[c], errors="coerce") for c in COLS[1:]}
    c, vol, vma = num["Close"], num["Volume"], num["VOL_MA_20"]
    mult = (backward_adjustment_multiplier(k["Date"], tk, ACTIONS).to_numpy(float) if tk in ACTION_TICKERS
            else np.ones(len(k)))
    av = (c.to_numpy(float) * mult)
    avs = pd.Series(av)
    wash = avs.rolling(10).max() / avs - 1
    vr = (vol / vma).to_numpy(float)
    new20 = (avs > avs.shift(1).rolling(20).max()).to_numpy()
    sig = (((c / num["MA_20"] - 1).abs() <= 0.05) & (vol / vma < 1.2) & (c > num["MA_60"]) & (vma >= 500_000)
           & (wash >= 0.08) & (k["Date"] >= "2020-01-01")).fillna(False).to_numpy()
    n = len(av)
    for t in np.flatnonzero(sig):
        e = t + 1
        if e + HOLD >= n or not np.isfinite(av[e]) or not np.isfinite(av[e + HOLD]):
            continue
        base_ret = (av[e + HOLD] / av[e] - 1) * 100
        kday = None
        for d in range(1, ADD_WINDOW + 1):
            j = e + d
            if new20[j] and np.isfinite(vr[j]) and vr[j] >= 1.5:
                kday = d
                break
        resid = {d: (av[e + HOLD] / av[e + d] - 1) * 100 for d in range(1, ADD_WINDOW + 1)}
        rows.append({"ticker": tk, "date": k["Date"].iloc[t], "base_ret": base_ret, "k": kday,
                     "add_ret": resid[kday] if kday else np.nan, **{f"r{d}": resid[d] for d in range(1, ADD_WINDOW + 1)}})

D = pd.DataFrame(rows)
D["year"] = D["date"].str[:4]
D["half"] = np.where(D["year"] <= "2022", "2020-22", "2023-26")
bo = D[D["k"].notna()].copy()
nb = D[D["k"].isna()]
control = {d: nb[f"r{d}"].mean() for d in range(1, ADD_WINDOW + 1)}      # 無突破訊號在同偏移的殘餘平均
bo["control"] = bo["k"].astype(int).map(control)
bo["excess"] = bo["add_ret"] - bo["control"]
share = len(bo) / len(D) * 100
halves = bo.groupby("half")["excess"].mean()
yearly = bo.groupby("year")["excess"].mean()
checks = [bo["excess"].mean() >= 2.0, bool((halves >= 1.0).all()) and len(halves) == 2,
          int((yearly > 0).sum()) >= 5, 15 <= share <= 70]
print(f"=== BT-rere-breakout-add 訊號 {len(D)},{D.date.min()}~{D.date.max()};原部位 60 根平均 {D.base_ret.mean():+.2f}% ===")
print(f"發生突破(30 根內創 20 日新高且量比>=1.5)的訊號 {len(bo)}({share:.1f}%),突破日中位第 {bo.k.median():.0f} 根")
print(f"加碼單位(突破日→第 60 根)平均 {bo.add_ret.mean():+.2f}%、勝率 {(bo.add_ret > 0).mean() * 100:.1f}%、左尾(<=-20%) {(bo.add_ret <= -20).mean() * 100:.1f}%")
print(f"對照(無突破訊號同偏移殘餘)平均 {bo.control.mean():+.2f}% → 超額 {bo.excess.mean():+.2f}pp;前段/後段 {halves.get('2020-22', np.nan):+.2f}/{halves.get('2023-26', np.nan):+.2f}pp")
print("逐年超額:" + " ".join(f"{y}:{v:+.1f}" for y, v in yearly.items()))
combined = ((bo["base_ret"] + bo["add_ret"]) / 2).mean()
print(f"(參考)突破訊號:原部位 {bo.base_ret.mean():+.2f}% vs 加碼後兩單位平均 {combined:+.2f}%;無突破訊號原部位 {nb.base_ret.mean():+.2f}%")
print("判準 1-4:", "".join("✓" if ok else "✗" for ok in checks), "→", "PASS(建議設計加碼規則,待 PM 核可)" if all(checks) else "FAIL(不設加碼機制)")
out = REPO / "ml" / "reports" / "bt_rere_breakout_add.csv"
pd.DataFrame([{"signals": len(D), "breakout_signals": len(bo), "share_pct": share, "add_mean": bo.add_ret.mean(),
               "add_win": (bo.add_ret > 0).mean() * 100, "add_tail20": (bo.add_ret <= -20).mean() * 100,
               "control_mean": bo.control.mean(), "excess": bo.excess.mean(),
               "h1": halves.get("2020-22", np.nan), "h2": halves.get("2023-26", np.nan),
               "years_positive": int((yearly > 0).sum()), "base_breakout": bo.base_ret.mean(), "combined": combined,
               "base_nobreakout": nb.base_ret.mean(), "checks": "".join("✓" if ok else "✗" for ok in checks),
               "verdict": "PASS" if all(checks) else "FAIL"}]).round(4).to_csv(out, index=False, encoding="utf-8-sig")
print("彙總:", out)
