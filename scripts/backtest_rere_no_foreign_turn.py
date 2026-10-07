# -*- coding: utf-8 -*-
"""BT-rere-no-foreign-turn:重構選項 (a)——rere 蹲點型/淺洗盤型拿掉「外資前5日賣→當日買」條件的正式回測。

背景:回測票 #8(ablation)顯示該條件邊際貢獻 0;來源釐清(rere_strategy 2026-10-02)確認它是 6/23 我方翻譯時加的,
不是她的規則;台表科 9/16-17 正是只卡此條件而漏掉。本票為正式驗證,判準在跑之前寫死(下方),不改任何 production gate。

口徑同帳本 / METHOD_rere_verification_20260906:次日收盤進、MA60(訊號日)×0.97 停損(進場後起算,還原收盤)、
第 60 個後續交易棒到期、除息加回、只收完整 60 棒樣本;各組同檔 20 日 cooldown;均量≥500張。
  CUR_SHAKEOUT = wash≥10% + |v20|≤5% + vr<1.2 + close>MA60 + 外資拐點   (現行)
  CUR_SHALLOW  = 8%≤wash<10% + 同上 + 外資拐點                             (現行 2026-09-24 版)
  NEW_SHAKEOUT / NEW_SHALLOW = 同上,不要求外資拐點                          (提案)
  ADDED_*      = NEW 中不滿足拐點者(= 提案新增的那批訊號)
  CONTROL      = |v20|≤5% + vr<1.2 + close>MA60(對照)
成本情境:淨值 = 毛值 − 0.785pp(賣出稅 0.3% + 手續費 0.1425%×2 + 滑價 0.1%×2)。

事前判準(2026-10-07 寫死;NEW 對 CUR,蹲點與淺洗盤分別判,兩者皆須全過才建議採用):
  1. n(NEW) ≥ 1.5 × n(CUR)                      — 提案的目的是補覆蓋
  2. 平均毛報酬 NEW ≥ CUR − 0.5pp
  3. 勝率 NEW ≥ CUR − 1pp
  4. 左尾(<−20%)占比 NEW ≤ CUR + 1pp
  5. 逐年(2020-2026,各年 n≥20 才計):NEW ≥ CUR − 1pp 的年份 ≥ 5/7
  6. 逆風窗(訊號日 2026-04-01~07-03,最近且已成熟):NEW ≥ CUR − 1pp
任一 FAIL = 不採用;PASS 也只建議以**新 subtype 標籤並行帳本**驗證,不取代現行 forward cohort。

用法:python -X utf8 scripts/backtest_rere_no_foreign_turn.py
輸出:ml/reports/bt_rere_no_foreign_turn.csv + stdout
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
HOLD, COOLDOWN, NET_COST_PP = 60, 20, 0.785
HEADWIND = ("2026-04-01", "2026-07-03")

cal = pd.read_csv(BASE / "ml" / "data" / "ex_dividend_calendar.csv", dtype={"stock_id": str}, encoding="utf-8-sig")
cal["dv"] = pd.to_numeric(cal["stock_and_cache_dividend"], errors="coerce").fillna(0.0)
cal["date"] = cal["date"].astype(str).str[:10]
CAL = {tk: g[["date", "dv"]].values.tolist() for tk, g in cal.groupby("stock_id")}


def _ret(k, c, ma60, events, t, n):
    e = t + 1
    if e + HOLD >= n:
        return None
    entry = float(c.iloc[e])
    if not np.isfinite(entry) or entry <= 0:
        return None
    stop = float(ma60.iloc[t]) * 0.97 if np.isfinite(ma60.iloc[t]) else np.nan
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
        k = pd.read_csv(f, usecols=["Date", "Close", "Volume", "Foreign_BuySell", "VOL_MA_20", "MA_20", "MA_60"])
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
    common = (v20.abs() <= 0.05) & (vr < 1.2) & (c > ma60) & (vma >= 500_000)
    deep, band = common & (wash >= 0.10), common & (wash >= 0.08) & (wash < 0.10)
    groups = {
        "CONTROL": common,
        "CUR_SHAKEOUT": deep & turn, "NEW_SHAKEOUT": deep, "ADDED_SHAKEOUT": deep & ~turn,
        "CUR_SHALLOW": band & turn, "NEW_SHALLOW": band, "ADDED_SHALLOW": band & ~turn,
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
            rows.append({"grp": name, "ticker": tk, "signal_date": d, "year": d[:4], "ret": r})

df = pd.DataFrame(rows)
out = BASE / "ml" / "reports" / "bt_rere_no_foreign_turn.csv"
df.to_csv(out, index=False, encoding="utf-8-sig")


def stat(v):
    return {"n": len(v), "mean": v.mean() * 100, "net": v.mean() * 100 - NET_COST_PP, "med": v.median() * 100,
            "win": (v > 0).mean() * 100, "tail": (v < -0.20).mean() * 100, "big": (v > 0.30).mean() * 100}


def line(name, v):
    if not len(v):
        return f"{name:16} n=0"
    s = stat(v)
    return (f"{name:16} n={s['n']:6} 毛均{s['mean']:+6.2f}% 淨均{s['net']:+6.2f}% 中位{s['med']:+6.2f}% 勝率{s['win']:5.1f}% "
            f"左尾{s['tail']:4.1f}% 大賺{s['big']:4.1f}%")


print(f"=== BT-rere-no-foreign-turn(完整60棒,{len(df)} 筆,{df.signal_date.min()} ~ {df.signal_date.max()})===")
for g in ["CONTROL", "CUR_SHAKEOUT", "NEW_SHAKEOUT", "ADDED_SHAKEOUT", "CUR_SHALLOW", "NEW_SHALLOW", "ADDED_SHALLOW"]:
    print(line(g, df[df.grp == g]["ret"]))

verdict_all = True
for sub in ("SHAKEOUT", "SHALLOW"):
    cur, new = df[df.grp == f"CUR_{sub}"]["ret"], df[df.grp == f"NEW_{sub}"]["ret"]
    sc, sn = stat(cur), stat(new)
    years_ok, years_n = 0, 0
    print(f"\n--- {sub}:逐年 NEW vs CUR(毛均/勝率)---")
    for y in sorted(df["year"].unique()):
        a, b = new[df.loc[new.index, "year"] == y], cur[df.loc[cur.index, "year"] == y]
        fa = f"{a.mean()*100:+6.2f}%/{(a>0).mean()*100:4.1f}% (n={len(a)})" if len(a) else "n=0"
        fb = f"{b.mean()*100:+6.2f}%/{(b>0).mean()*100:4.1f}% (n={len(b)})" if len(b) else "n=0"
        print(f"  {y}: NEW {fa:26} | CUR {fb}")
        if len(a) >= 20 and len(b) >= 20:
            years_n += 1
            years_ok += int(a.mean() * 100 >= b.mean() * 100 - 1.0)
    hw = (df.signal_date >= HEADWIND[0]) & (df.signal_date <= HEADWIND[1])
    hn, hc = df[hw & (df.grp == f"NEW_{sub}")]["ret"], df[hw & (df.grp == f"CUR_{sub}")]["ret"]
    print(f"  逆風窗 {HEADWIND[0]}~{HEADWIND[1]}: NEW {hn.mean()*100:+.2f}%/{(hn>0).mean()*100:.1f}% (n={len(hn)}) | "
          f"CUR {hc.mean()*100:+.2f}%/{(hc>0).mean()*100:.1f}% (n={len(hc)})")
    checks = [("1 n ≥ 1.5×CUR", sn["n"] >= 1.5 * sc["n"]),
              ("2 毛均 ≥ CUR−0.5pp", sn["mean"] >= sc["mean"] - 0.5),
              ("3 勝率 ≥ CUR−1pp", sn["win"] >= sc["win"] - 1.0),
              ("4 左尾 ≤ CUR+1pp", sn["tail"] <= sc["tail"] + 1.0),
              (f"5 逐年不輸(−1pp)≥5/7 (實得 {years_ok}/{years_n})", years_ok >= 5),
              ("6 逆風窗 ≥ CUR−1pp", (hn.mean() * 100 >= hc.mean() * 100 - 1.0) if len(hn) and len(hc) else False)]
    print(f"  事前判準 {sub}:")
    for name, ok in checks:
        print(f"    [{'PASS' if ok else 'FAIL'}] {name}")
    verdict_all &= all(ok for _, ok in checks)
print("\n判決:", "PASS — 建議以新 subtype 並行帳本驗證(仍需 PM 核可)" if verdict_all else "FAIL — 不採用")
print(f"明細 -> {out}")
