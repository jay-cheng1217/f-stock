# -*- coding: utf-8 -*-
"""BT-rere-pre-trust-accumulation:「外資未倒貨的月線盤整」型(第四種形狀)回測。

觸發:側寫預登記規則「第 3 例出現即開回測票」——台表科 6278(9/16-18)、瑞耘 6532
(9/16-17)、鈦昇 8027(9/16-24)三役同型:貼 MA20 盤整、外資持續淨買(**無**由賣轉買
拐點)、投信 0、量縮,之後噴出。現行三個 rere 子型態都要求「外資前5日賣超→當日買超」
拐點,此型全數漏掉;她的自述「買在投信前」正是此型。

條件(point-in-time,訊號日 t):
  |v20| <= 5%(貼月線) + vr < 1.2(量縮) + close > MA60 + 外資近20日淨買 > 0
  + 外資近5日淨買 > 0(持續吸籌,非拐點) + 投信近20日淨買 <= 0(投信未進)
  + 當日量 >= 500 張
  洗盤深度不設限(0~任意),另按 wash 分桶觀察。
對照:BASE = 現行蹲點型(wash>=10%+外資拐點),同帳本慣例(次日收盤進、MA60×0.97 停損、
60 日、除息還原)。分組:PRE_TRUST(本型) / PRE_TRUST 依 wash 桶 / BASE。
去重:同檔 20 日 cooldown(此型會連日成立,避免同一段盤整重複計數)。

用法:python -X utf8 scripts/backtest_rere_pre_trust_accumulation.py
輸出:ml/reports/bt_rere_pre_trust_accumulation.csv + stdout
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

cal = pd.read_csv(CAL_PATH, dtype={"stock_id": str}, encoding="utf-8-sig")
cal["dv"] = pd.to_numeric(cal["stock_and_cache_dividend"], errors="coerce").fillna(0.0)
cal["date"] = cal["date"].astype(str).str[:10]
CAL = {tk: g[["date", "dv"]].values.tolist() for tk, g in cal.groupby("stock_id")}


def _ret(k, c, ma60, events, t, n):
    e_idx = t + 1
    if e_idx >= n:
        return None
    entry = float(c.iloc[e_idx])
    if not np.isfinite(entry) or entry <= 0:
        return None
    stop = float(ma60.iloc[t]) * 0.97 if np.isfinite(ma60.iloc[t]) else np.nan
    end = min(e_idx + HOLD, n - 1)
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
    fr5, fr20 = fr.rolling(5).sum(), fr.rolling(20).sum()
    fr_prev5 = fr.shift(1).rolling(5).sum()
    tr20 = tr.rolling(20).sum()
    common = (v20.abs() <= MA20_BAND) & (vr < VOL_MAX) & (c > ma60) & (vol >= 500_000)
    pre_trust = common & (fr20 > 0) & (fr5 > 0) & (tr20 <= 0)
    base = common & (wash >= 0.10) & (fr_prev5 < 0) & (fr > 0)

    n = len(k)
    events = CAL.get(tk, [])
    last_t = -10_000
    for t in np.where(pre_trust.fillna(False).values)[0]:
        if t - last_t < COOLDOWN:
            continue
        r = _ret(k, c, ma60, events, t, n)
        if r is None:
            continue
        last_t = t
        w = float(wash.iloc[t]) if np.isfinite(wash.iloc[t]) else 0.0
        bucket = "w<4%" if w < 0.04 else ("w4-8%" if w < 0.08 else ("w8-10%" if w < 0.10 else "w>=10%"))
        rows.append({"ticker": tk, "signal_date": k["Date"].iloc[t], "year": k["Date"].iloc[t][:4],
                     "grp": "PRE_TRUST", "wash_bucket": bucket, "ret": r})
    for t in np.where(base.fillna(False).values)[0]:
        r = _ret(k, c, ma60, events, t, n)
        if r is None:
            continue
        rows.append({"ticker": tk, "signal_date": k["Date"].iloc[t], "year": k["Date"].iloc[t][:4],
                     "grp": "BASE", "wash_bucket": "w>=10%", "ret": r})
    # CONTROL:同樣貼MA20+量縮+>MA60(不管外資/投信),同 cooldown——隔離「外資淨買+投信未進」的貢獻
    last_t = -10_000
    for t in np.where(common.fillna(False).values)[0]:
        if t - last_t < COOLDOWN:
            continue
        r = _ret(k, c, ma60, events, t, n)
        if r is None:
            continue
        last_t = t
        rows.append({"ticker": tk, "signal_date": k["Date"].iloc[t], "year": k["Date"].iloc[t][:4],
                     "grp": "CONTROL", "wash_bucket": "", "ret": r})


rows: list[dict] = []
files = sorted(DAILY.glob("*.csv"))
for i, f in enumerate(files):
    tk = f.stem
    if not (tk.isdigit() and len(tk) == 4):
        continue
    try:
        k = pd.read_csv(f, usecols=["Date", "Close", "Volume", "Foreign_BuySell",
                                    "Trust_BuySell", "VOL_MA_20", "MA_20", "MA_60"])
    except Exception:
        continue
    if len(k) < 90:
        continue
    run_one(tk, k, rows)
    if (i + 1) % 500 == 0:
        print(f"  {i + 1}/{len(files)}")

df = pd.DataFrame(rows)
out = BASE / "ml" / "reports" / "bt_rere_pre_trust_accumulation.csv"
df.to_csv(out, index=False, encoding="utf-8-sig")


def _line(name, v):
    if not len(v):
        return f"{name:22}: n=0"
    return (f"{name:22}: n={len(v):5} 平均{v.mean()*100:+.2f}% 中位{v.median()*100:+.2f}% "
            f"勝率{(v>0).mean()*100:.1f}% 左尾<-20%占{(v<-0.20).mean()*100:.1f}%")


print(f"\n=== BT-rere-pre-trust-accumulation(adj 模式,訊號 {len(df)} 筆)===")
print(_line("BASE(蹲點型現行)", df[df.grp == "BASE"]["ret"].dropna()))
print(_line("CONTROL(貼線量縮,不管法人)", df[df.grp == "CONTROL"]["ret"].dropna()))
pt = df[df.grp == "PRE_TRUST"]
print(_line("PRE_TRUST(全部)", pt["ret"].dropna()))
for b in ("w<4%", "w4-8%", "w8-10%", "w>=10%"):
    print(_line(f"  PRE_TRUST {b}", pt[pt.wash_bucket == b]["ret"].dropna()))
print("\n逐年 PRE_TRUST vs BASE(平均/勝率):")
for y in sorted(df["year"].unique()):
    a = pt[pt["year"] == y]["ret"].dropna()
    s = df[(df["year"] == y) & (df["grp"] == "BASE")]["ret"].dropna()
    fa = f"{a.mean()*100:+6.2f}%/{(a>0).mean()*100:4.1f}% (n={len(a)})" if len(a) else "n=0"
    fs = f"{s.mean()*100:+6.2f}%/{(s>0).mean()*100:4.1f}% (n={len(s)})" if len(s) else "n=0"
    print(f"  {y}: PRE_TRUST {fa:28} | BASE {fs}")
print(f"\n明細 -> {out}")
