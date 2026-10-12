# -*- coding: utf-8 -*-
"""BT-rere-scale-out:rere lane 分批停利 + 移動停利,對照現行「持有 60 日」(研究,不改任何規則)。

背景(PM 2026-10-12「可以開始研究」):rere 10/12 限動公開台表科出場表——上漲前就寫好價格階梯,
220 時設定:270(+23%)賣 20~30%、290(+32%)賣 30% 左右、300 收割、365(+66%)樂觀目標,並強調「移動停利很重要」。
系統 rere lane 帳本目前持有 60 日、只有訊號收盤 −40% 災難停損,沒有任何分批停利。

口徑同 BT-rere-stop-width 第二輪:rere v2 訊號(wash>=8% + |v20|<=5% + 量比<1.2 + close>MA60 + 均量>=500張),
次日收盤進場、完整 60 棒、同檔 20 日 cooldown、公司行動連續還原、毛報酬(每單位只買一次賣一次,各政策成本相同,故比毛報酬)。
判定一律用收盤價:當日收盤觸價即以當日收盤成交;同一天先檢查災難停損,再檢查分批,最後檢查移動停利。
階梯以「進場價」為基準(她以設定當時的 220 為基準,不是成本,這裡無法逐筆還原,屬近似)。
移動停利幅度 15% 是本票假設,不是她公開的參數。

政策(判定對象只有 P1):
  P0 現行:持有 60 棒,訊號收盤 −40% 災難停損。
  P1 她的階梯(主):+23% 賣 25%、+32% 再賣 30%;第一段觸發後,剩餘部位自最高收盤回落 15% 全出;其餘同 P0。
  P2 只有階梯(參考):同 P1 但不加移動停利,剩餘持有到期。
  P3 只有移動停利(參考):漲到 +23% 後,全部部位自最高收盤回落 15% 全出。
  P4 階梯 + 樂觀目標(參考):P1 再加 +66% 全數出清。
指標:平均/中位/勝率、跌逾 20%、漲逾 30%、最差 1%、「曾漲逾 20% 最後卻低於 +5%」(獲利回吐)比例、
資金占用天數(各部位持有天數的加權)、每 60 資金日報酬(平均報酬 ÷ 平均占用天數 × 60)、逐年平均。

事前判準(2026-10-12 寫死,先於執行提交;P1 對 P0,任一 FAIL = 不建議開並行帳本):
  1. 平均報酬 >= P0 − 1.0pp
  2. 獲利回吐比例相對 P0 下降 >= 30%
  3. 每 60 資金日報酬 >= P0
  4. 逐年(該年 n >= 20):平均報酬 >= P0 − 1pp 的年份 >= 5/7
  5. 勝率 >= P0
PASS 也只代表「值得在 rere 帳本旁加一欄分批停利的研究帳本」,不改現行出場規則;是否採用由 PM 裁決。
限制:不含已下市股票;收盤成交忽略盤中觸價與跳空;不扣成本;樣本跨股票重疊。

用法:python -X utf8 scripts/backtest_rere_scale_out.py   (worktree 無日K時設 STOCK_BASE_DIR 指向主工作目錄)
輸出:ml/reports/bt_rere_scale_out.csv(各政策彙總與逐年)+ stdout
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
HOLD, COOLDOWN, STOP = 60, 20, 0.40
LADDER = ((1.23, 0.25), (1.32, 0.30))
TRAIL, TRAIL_FROM, TARGET = 0.15, 1.23, 1.66
POLICIES = {
    "P0 現行持有60日": dict(ladder=(), trail=False, target=None),
    "P1 她的階梯+移動停利(主)": dict(ladder=LADDER, trail=True, target=None),
    "P2 只有階梯": dict(ladder=LADDER, trail=False, target=None),
    "P3 只有移動停利": dict(ladder=(), trail=True, target=None),
    "P4 階梯+樂觀目標": dict(ladder=LADDER, trail=True, target=TARGET),
}
P0, P1 = "P0 現行持有60日", "P1 她的階梯+移動停利(主)"

sys.path.insert(0, str(REPO))
from ml.corporate_actions import backward_adjustment_multiplier, load_action_calendar  # noqa: E402

ACTIONS = load_action_calendar(str(BASE / "ml" / "data" / "ex_dividend_calendar.csv"),
                               str(BASE / "ml" / "data" / "corporate_action_price_factors.csv"))
ACTION_TICKERS = set(ACTIONS["stock_id"].astype(str))


def simulate(path: np.ndarray, entry: float, stop_level: float, *, ladder, trail, target) -> tuple[float, float]:
    """回傳(報酬 %, 資金占用天數)。path = 進場後第 1..60 根還原收盤。"""
    remaining, realized, days_used, peak, armed = 1.0, 0.0, 0.0, entry, False
    done = set()
    for i, price in enumerate(path, start=1):
        if remaining <= 0:
            break
        if price < stop_level:                                   # 災難停損(剩餘部位)
            realized += remaining * (price / entry - 1)
            days_used += remaining * i
            remaining = 0.0
            break
        peak = max(peak, price)
        for k, (mult, frac) in enumerate(ladder):                # 分批停利
            if k not in done and price >= entry * mult and remaining > 0:
                sell = min(frac, remaining)
                realized += sell * (price / entry - 1)
                days_used += sell * i
                remaining -= sell
                done.add(k)
        if price >= entry * TRAIL_FROM:
            armed = True
        if target and remaining > 0 and price >= entry * target:  # 樂觀目標全出
            realized += remaining * (price / entry - 1)
            days_used += remaining * i
            remaining = 0.0
            break
        if trail and armed and remaining > 0 and price < peak * (1 - TRAIL):   # 移動停利
            realized += remaining * (price / entry - 1)
            days_used += remaining * i
            remaining = 0.0
            break
    if remaining > 0:
        realized += remaining * (path[-1] / entry - 1)
        days_used += remaining * len(path)
    return realized * 100, days_used


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
        path = av[e + 1:e + HOLD + 1]
        if not np.all(np.isfinite(path)):
            continue
        stop_level = cv[t] * (1 - STOP) * mult[t]
        rec = {"ticker": tk, "signal_date": dates[t], "year": dates[t][:4], "max_gain": (path.max() / entry - 1) * 100}
        for name, kw in POLICIES.items():
            rec[name], rec[name + "|days"] = simulate(path, entry, stop_level, **kw)
        rows.append(rec)

d = pd.DataFrame(rows)
print(f"=== BT-rere-scale-out 訊號 {len(d)},{d.year.min()}~{d.year.max()} ===")
summary = []
for name in POLICIES:
    x, days = d[name], d[name + "|days"]
    giveback = ((d.max_gain >= 20) & (x < 5)).mean() * 100
    per60 = x.mean() / days.mean() * 60
    yearly = {y: g[name].mean() for y, g in d.groupby("year") if len(g) >= 20}
    summary.append({"policy": name, "n": len(x), "mean": x.mean(), "median": x.median(), "win": (x > 0).mean() * 100,
                    "left20": (x < -20).mean() * 100, "big30": (x > 30).mean() * 100, "worst1": x.quantile(.01),
                    "giveback": giveback, "days": days.mean(), "per60": per60,
                    "yearly": " ".join(f"{y}:{v:+.2f}" for y, v in yearly.items())})
    print(f"{name:20} 均{x.mean():+6.2f}% 中位{x.median():+6.2f}% 勝率{(x > 0).mean() * 100:5.1f}% 跌逾20%:{(x < -20).mean() * 100:4.1f}% "
          f"漲逾30%:{(x > 30).mean() * 100:4.1f}% 最差1%{x.quantile(.01):+6.1f}% | 獲利回吐 {giveback:4.1f}% | "
          f"資金占用 {days.mean():4.1f} 日 | 每60資金日 {per60:+6.2f}%")
S = pd.DataFrame(summary).set_index("policy")
print("\n逐年平均:")
for name in POLICIES:
    print(f"  {name:20} {S.loc[name, 'yearly']}")

a, b = S.loc[P0], S.loc[P1]
yrs = [(y, g[P1].mean() - g[P0].mean()) for y, g in d.groupby("year") if len(g) >= 20]
checks = [(f"1 平均 >= P0 − 1.0pp (實得 {b['mean'] - a['mean']:+.2f}pp)", b["mean"] - a["mean"] >= -1.0),
          (f"2 獲利回吐比例下降 >= 30% (P0 {a['giveback']:.1f}% → P1 {b['giveback']:.1f}%,降 "
           f"{(1 - b['giveback'] / a['giveback']) * 100 if a['giveback'] else 0:.0f}%)",
           a["giveback"] > 0 and (1 - b["giveback"] / a["giveback"]) >= 0.30),
          (f"3 每60資金日報酬 >= P0 (P0 {a['per60']:+.2f}% / P1 {b['per60']:+.2f}%)", b["per60"] >= a["per60"]),
          (f"4 逐年 >= P0 − 1pp 的年份 >= 5/{len(yrs)} (實得 {sum(1 for _, v in yrs if v >= -1)})",
           sum(1 for _, v in yrs if v >= -1) >= 5),
          (f"5 勝率 >= P0 (P0 {a['win']:.1f}% / P1 {b['win']:.1f}%)", b["win"] >= a["win"])]
print("\n事前判準(P1 對 P0):")
for label, ok in checks:
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
print("判決:", "PASS — 可提請 PM 在 rere 帳本旁加分批停利研究欄" if all(ok for _, ok in checks) else "FAIL — 不建議")
print("引擎核對:P0 應重現 BT-rere-stop-width 的 −40% 列(14,065 筆 / +6.55% / 勝率 47.6%)")
out = REPO / "ml" / "reports" / "bt_rere_scale_out.csv"
S.round(3).to_csv(out, encoding="utf-8-sig")
print("彙總:", out)
