# -*- coding: utf-8 -*-
"""BT-extended-chase(探索性,未事前登記判準,不可據以改 gate):「已經噴出」的股票,隔天追進去之後的歷史分布。

背景(PM 2026-10-07 傍晚,台表科 6278 收 250.5、距月線 +22.5%、創 60 日新高、外資連 7 買):「還會不會漲」。
這不是預測,只回答基準率:歷史上處在同樣位置的股票,之後 5/10/20/60 個交易棒怎麼走、會不會拉回到系統的進場帶。

狀態定義(訊號日 t,還原收盤):
  S    = 創 60 日新高 且 距月線(MA20)+18%~+28% 且 20 日均量 >= 500 張
  S+F  = S 且 外資當日買超、近 5 日合計買超
  S+2D = S 且 近 2 日累計漲幅 >= 12%
口徑:次日收盤進場(隔日一字鎖漲停買不到者剔除)、不設停損、同檔 20 日 cooldown、公司行動連續還原、毛報酬;
  超額 = 對同一訊號日全股票池(均量 >= 500 張)同天期平均。
  另計:進場後 20 棒內收盤最深回落、20 棒內是否回到「距月線 <= +8%」(主 lane 型態 gate 的上緣)。
限制:不含已下市股票、樣本跨股票重疊、狀態帶寬為配合單一案例人工設定(有事後挑條件的偏誤)。

用法:python -X utf8 scripts/backtest_extended_chase.py   (worktree 無日K時設 STOCK_BASE_DIR)
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
HORIZONS, COOLDOWN, LIQ_MIN = (5, 10, 20, 60), 20, 500_000
COLS = ["Date", "High", "Low", "Close", "Volume", "VOL_MA_20", "Foreign_BuySell"]

sys.path.insert(0, str(REPO))
from ml.corporate_actions import backward_adjustment_multiplier, load_action_calendar  # noqa: E402

ACTIONS = load_action_calendar(str(BASE / "ml" / "data" / "ex_dividend_calendar.csv"),
                               str(BASE / "ml" / "data" / "corporate_action_price_factors.csv"))
ACTION_TICKERS = set(ACTIONS["stock_id"].astype(str))

rows, pool_parts = [], {h: [] for h in HORIZONS}
for f in sorted(DAILY.glob("*.csv")):
    tk = f.stem
    if not (tk.isdigit() and len(tk) == 4 and tk[0] != "0"):
        continue
    try:
        k = pd.read_csv(f, usecols=lambda c: c in COLS)
    except Exception:
        continue
    if len(k) < 150 or not {"Date", "High", "Low", "Close", "Volume", "VOL_MA_20"} <= set(k.columns):
        continue
    if "Foreign_BuySell" not in k.columns:
        k["Foreign_BuySell"] = np.nan
    k["Date"] = k["Date"].astype(str).str[:10]
    c, hi, lo = (pd.to_numeric(k[x], errors="coerce") for x in ("Close", "High", "Low"))
    vma, fb = pd.to_numeric(k["VOL_MA_20"], errors="coerce"), pd.to_numeric(k["Foreign_BuySell"], errors="coerce")
    n = len(k)
    mult = backward_adjustment_multiplier(k["Date"], tk, ACTIONS).to_numpy(float) if tk in ACTION_TICKERS else np.ones(n)
    av_s = c * mult
    av, cv, dates = av_s.to_numpy(float), c.to_numpy(float), k["Date"].to_numpy()
    ma20 = av_s.rolling(20).mean()
    v20 = (av_s / ma20 - 1).to_numpy(float)
    liq = vma >= LIQ_MIN
    state = (av_s > av_s.shift(1).rolling(60).max()) & (av_s / ma20 - 1).between(0.18, 0.28) & liq
    groups = {"S 創高且距月線+18~28%": state, "S+F 外資當日與5日皆買超": state & (fb > 0) & (fb.rolling(5).sum() > 0),
              "S+2D 近2日漲>=12%": state & (av_s / av_s.shift(2) - 1 >= 0.12)}
    for name, mask in groups.items():
        last = -10 ** 6
        for t in np.where(mask.fillna(False).to_numpy())[0]:
            if t - last < COOLDOWN or t + 1 + max(HORIZONS) >= n:
                continue
            e = t + 1
            if not np.isfinite(av[e]) or av[e] <= 0:
                continue
            if hi.iloc[e] == lo.iloc[e] and cv[e] >= cv[t] * 1.09:
                continue                                              # 隔日一字鎖漲停買不到
            last = t
            rec = {"group": name, "ticker": tk, "signal_date": dates[t], "year": dates[t][:4]}
            for h in HORIZONS:
                rec[f"r{h}"] = (av[e + h] / av[e] - 1) * 100
            rec["mae20"] = (av[e + 1:e + 21].min() / av[e] - 1) * 100
            back = np.where(v20[t + 1:t + 21] <= 0.08)[0]             # 20 棒內回到距月線 <= +8%
            rec["back_days"] = int(back[0]) + 1 if len(back) else np.nan
            rows.append(rec)
    ok_liq = liq.to_numpy()
    for h in HORIZONS:
        fwd = ((av_s.shift(-(h + 1)) / av_s.shift(-1) - 1) * 100).to_numpy()
        ok = ok_liq & np.isfinite(fwd)
        pool_parts[h].append(pd.Series(fwd[ok], index=dates[ok]))

D = pd.DataFrame(rows)
pool = {h: pd.concat(pool_parts[h]).groupby(level=0).mean() for h in HORIZONS}
for h in HORIZONS:
    D[f"x{h}"] = D[f"r{h}"] - D.signal_date.map(pool[h])
print(f"=== BT-extended-chase(探索性)訊號 {D.signal_date.min()}~{D.signal_date.max()} ===")
out_rows = []
for name, g in D.groupby("group", sort=False):
    print(f"\n{name}  n={len(g)}")
    for h in HORIZONS:
        r, x = g[f"r{h}"], g[f"x{h}"]
        print(f"  {h:>2} 棒:均 {r.mean():+6.2f}% 中位 {r.median():+6.2f}% 上漲比例 {(r > 0).mean() * 100:5.1f}% | 對同日市場超額 均 {x.mean():+5.2f}pp"
              f" | 跌逾10% {(r < -10).mean() * 100:5.1f}% 漲逾10% {(r > 10).mean() * 100:5.1f}% 漲逾20% {(r > 20).mean() * 100:5.1f}%")
        out_rows.append({"group": name, "horizon": h, "n": len(g), "mean": r.mean(), "median": r.median(), "up_share": (r > 0).mean() * 100,
                         "excess_mean": x.mean(), "lt_m10": (r < -10).mean() * 100, "gt_10": (r > 10).mean() * 100, "gt_20": (r > 20).mean() * 100})
    yrs = [(y, x.x20.mean(), len(x)) for y, x in g.groupby("year") if len(x) >= 20]
    print(f"  20 棒內曾回落逾 10%:{(g.mae20 <= -10).mean() * 100:.1f}% | 曾回落逾 20%:{(g.mae20 <= -20).mean() * 100:.1f}% | 最深回落中位 {g.mae20.median():+.1f}%")
    print(f"  20 棒內回到距月線 <= +8%:{g.back_days.notna().mean() * 100:.1f}%(回到者中位第 {g.back_days.median():.0f} 棒)")
    print("  逐年 20 棒超額:", " ".join(f"{y}:{ex:+.1f}({cnt})" for y, ex, cnt in yrs))
out = REPO / "ml" / "reports" / "bt_extended_chase.csv"
pd.DataFrame(out_rows).round(3).to_csv(out, index=False, encoding="utf-8-sig")
print("\n彙總:", out)
