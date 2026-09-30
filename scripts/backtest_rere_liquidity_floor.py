# -*- coding: utf-8 -*-
"""BT-rere-liquidity-floor:驗證「冷門小票 rere 訊號是負 EV 子群」假說。

動機(2026-09-01 八月 cohort 歸因):8 檔輸家(<-5%)中 5 檔命中
「日均成交值 <1 億 且 外資20日累計 <-1000 張」;贏家日均成交值中位 27.7 億。
假說:同樣的蹲點型態,發生在冷門小票+外資持續倒貨的股票上是假拐點。

訊號與帳本慣例與 backtest_rere_lane_baseline.py 完全一致
(蹲點型、entry=次日收盤、MA60×0.97 停損、60 交易日、除息還原 adj 模式)。
新增每筆訊號特徵(point-in-time,訊號日含當日往前 20 日):
  turnover20 = mean(Close×Volume) / 1e8(億元)
  f20        = sum(Foreign_BuySell) 股數

分組:
  FLOOR = turnover20 < 1.0億 且 f20 < -1000 張   (假說負 EV 群)
  TURN  = 只有 turnover20 < 1.0億
  FRGN  = 只有 f20 < -1000 張
  PASS  = 兩者皆非

用法:python -X utf8 scripts/backtest_rere_liquidity_floor.py
輸出:ml/reports/bt_rere_liquidity_floor.csv + stdout 統計
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

WASH_MIN, MA20_BAND, VOL_MAX, HOLD = 0.10, 0.05, 1.2, 60
TURNOVER_FLOOR = 1.0e8          # 1 億
FOREIGN_FLOOR = -1_000_000      # -1000 張(股數)

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

    hi10 = c.rolling(10).max()
    wash = 1 - c / hi10
    v20 = c / ma20 - 1
    vr = vol / vma
    fr_prev5 = fr.shift(1).rolling(5).sum()
    turnover20 = (c * vol).rolling(20).mean()
    f20 = fr.rolling(20).sum()

    cond = ((wash >= WASH_MIN) & (v20.abs() <= MA20_BAND) & (vr < VOL_MAX)
            & (c > ma60) & (fr_prev5 < 0) & (fr > 0) & (vol >= 500_000))
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

        to20 = float(turnover20.iloc[t]) if np.isfinite(turnover20.iloc[t]) else np.nan
        fsum = float(f20.iloc[t]) if np.isfinite(f20.iloc[t]) else 0.0
        low_turn = np.isfinite(to20) and to20 < TURNOVER_FLOOR
        low_frgn = fsum < FOREIGN_FLOOR
        grp = ("FLOOR" if (low_turn and low_frgn) else
               "TURN" if low_turn else
               "FRGN" if low_frgn else "PASS")
        rows.append({"ticker": tk, "signal_date": k["Date"].iloc[t],
                     "year": k["Date"].iloc[t][:4], "grp": grp,
                     "turnover20_e": round(to20 / 1e8, 3) if np.isfinite(to20) else np.nan,
                     "f20_lots": round(fsum / 1000), "ret": exit_ret})


rows: list[dict] = []
files = sorted(DAILY.glob("*.csv"))
for i, f in enumerate(files):
    tk = f.stem
    if not (tk.isdigit() and len(tk) == 4):
        continue
    try:
        k = pd.read_csv(f, usecols=["Date", "Close", "Volume",
                                    "Foreign_BuySell", "VOL_MA_20", "MA_20", "MA_60"])
    except Exception:
        continue
    if len(k) < 90:
        continue
    run_one(tk, k, rows)
    if (i + 1) % 500 == 0:
        print(f"  {i + 1}/{len(files)}")

df = pd.DataFrame(rows)
out = BASE / "ml" / "reports" / "bt_rere_liquidity_floor.csv"
df.to_csv(out, index=False, encoding="utf-8-sig")

print(f"\n=== BT-rere-liquidity-floor(adj 模式,訊號 {len(df)} 筆)===")
for g in ("PASS", "FRGN", "TURN", "FLOOR"):
    v = df[df["grp"] == g]["ret"].dropna()
    if not len(v):
        continue
    tail = (v < -0.20).mean() * 100
    print(f"{g:5}: n={len(v):5} 平均{v.mean()*100:+.2f}% 中位{v.median()*100:+.2f}% "
          f"勝率{(v>0).mean()*100:.1f}% 左尾<-20%占{tail:.1f}%")

print("\n逐年 FLOOR vs PASS(平均/勝率):")
for y in sorted(df["year"].unique()):
    fl = df[(df["year"] == y) & (df["grp"] == "FLOOR")]["ret"].dropna()
    ps = df[(df["year"] == y) & (df["grp"] == "PASS")]["ret"].dropna()
    fs = f"{fl.mean()*100:+6.2f}%/{(fl>0).mean()*100:4.1f}% (n={len(fl)})" if len(fl) else "n=0"
    print(f"  {y}: FLOOR {fs:28} | PASS {ps.mean()*100:+6.2f}%/{(ps>0).mean()*100:4.1f}% (n={len(ps)})")
print(f"\n明細 -> {out}")
