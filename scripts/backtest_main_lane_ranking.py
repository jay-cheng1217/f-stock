# -*- coding: utf-8 -*-
"""BT-main-lane-ranking:重構新增項——主 lane「模型過濾 + 模型分數排前 12」的替代排序正式回測。

背景:三個月績效快照顯示模型排序無超額;台表科 9/21 通過型態 gate、模型 +0.78%,卻因分數排第 12 名之外未上名單。
本票在**同一個型態池**上比較幾種排序/過濾的 20 日結果。判準在跑之前寫死;不改任何 production gate。

正式名單的模型來源(generator `_latest_predictions_path`):2026-07-02 起用 dataA(pred>0 即「模型不反對」),缺檔才退回
Champion(推薦含「買進」且 pred>0)。dataA 全池分數檔只保留 2026-09-03 之後,**7-8 月的 dataA 過濾無法重建**,因此:
  LIVE  = 正式名單的實際主 lane 列(ml/reports/entry_filter_ledger.csv,7/2~9/2;排除 veto),用本票同一套報酬口徑重算;
          7/2 以前(5/18~7/1)LIVE = R0c(Champion 過濾+分數,即當時的正式規則)。
  R0c   = Champion 過濾(推薦含「買進」且 pred>0)→ score = clean + pred20(參考;ChipK 停用故 chip_bonus=0)
  R3c   = Champion 過濾 → score = clean
  R1    = 不用模型 → score = clean(提案)
  R2c   = 不過濾 → score = Champion pred20(純模型排序,對照)
  POOL  = 型態池全部等權(無模型、無排序的 base)
池(每個訊號日):型態 gate 與 generator `_pattern_ok` 同式(−2%≤v20≤+8%、close>MA60、量比≤2.5、RSI 45-80、
MACD 3日變化/收盤 ≥ −1%;**此相對口徑 2026-08-28 才上線,7-8 月 LIVE 用的是絕對 −1.0 舊口徑,故部分 LIVE 列不在池內**)
+ 20 日均量≥500張 + 營益率 guard(OM<0 排除,財務防呆層,所有變體一律保留)。欄位來自 generator 同一套
`prepare_stock_frame`+`annotate_momentum_continuation`。clean = −|v20 − 0.04| − 0.1×量比(缺值以 1.5 計)。
報酬口徑(所有變體一致):訊號日次一交易日收盤進;20 棒內收盤跌破 MA20(訊號日)×0.96 即出(generator 失效價),否則第 20 棒
收盤出;除息加回;毛報酬,另列 −0.4% 摩擦。窗口:訊號日 2026-05-18(Champion pin 日)~2026-09-02(20 棒於 10/2 前成熟)。

事前判準(2026-10-07 寫死;R1 對 LIVE):
  1. 全部挑中樣本平均毛報酬 R1 ≥ LIVE + 0.3pp
  2. 勝率 R1 ≥ LIVE − 1pp
  3. 逐月(該月 ≥10 個訊號日才計):R1 ≥ LIVE 的月份 ≥ 3/4
  4. 逐日籃子:R1 > LIVE 的日子 ≥ 50%
任一 FAIL = 不採用 R1。附帶判讀:若 LIVE 與 R1 都 ≤ POOL,表示排序本身無價值,另案討論。
穩健性讀數(不入判準):同檔 20 日去重後的 LIVE / R1;dataA 期(7/2~9/2)單獨的 LIVE vs R1。

用法:python -X utf8 scripts/backtest_main_lane_ranking.py
輸出:ml/reports/bt_main_lane_ranking.csv(池)+ ml/reports/bt_main_lane_ranking_live.csv(LIVE 列)+ stdout
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
from scripts.momentum_continuation_backtest import prepare_stock_frame, annotate_momentum_continuation  # noqa: E402
from scripts.taiwan_trading_calendar import is_taiwan_trading_day  # noqa: E402

DAILY, MODELS, REPORTS = BASE / "日K資料", BASE / "ml" / "models", BASE / "ml" / "reports"
START, END, HOLD, TOPN, FRICTION_PP = "2026-05-18", "2026-09-02", 20, 12, 0.4
LIVE_LEDGER_START = "2026-07-02"
MA20_LOW, MA20_HIGH, MAX_VOL_RATIO, RSI_LOW, RSI_HIGH, MACD_FLOOR = -0.02, 0.08, 2.5, 45, 80, -0.010

cal = pd.read_csv(BASE / "ml" / "data" / "ex_dividend_calendar.csv", dtype={"stock_id": str}, encoding="utf-8-sig")
cal["dv"] = pd.to_numeric(cal["stock_and_cache_dividend"], errors="coerce").fillna(0.0)
cal["date"] = cal["date"].astype(str).str[:10]
CAL = {tk: g[["date", "dv"]].values.tolist() for tk, g in cal.groupby("stock_id")}

dates = [d.strftime("%Y-%m-%d") for d in pd.date_range(START, END) if is_taiwan_trading_day(d.date())]
all_td = [d.strftime("%Y-%m-%d") for d in pd.date_range("2026-01-01", "2026-12-31") if is_taiwan_trading_day(d.date())]
prev_td = {all_td[i]: all_td[i - 1] for i in range(1, len(all_td))}
preds = {}
for d in dates:
    p = MODELS / f"predictions_{d}.csv"
    if p.exists():
        preds[d] = pd.read_csv(p, dtype={"ticker": str}, usecols=["ticker", "pred_return_20d", "recommendation",
                                                                  "operating_margin_latest"]).set_index("ticker")
# LIVE 列:ledger 的 trade_date = 名單日,訊號日 = 前一交易日
ledger = pd.read_csv(REPORTS / "entry_filter_ledger.csv", dtype=str)
ledger = ledger[(ledger.lane == "main") & (ledger.state != "veto")].copy()
ledger["signal_date"] = ledger["trade_date"].map(prev_td)
ledger = ledger[(ledger.signal_date >= LIVE_LEDGER_START) & (ledger.signal_date <= END) & ledger.signal_date.isin(preds)]
want_live = set(zip(ledger.signal_date, ledger.ticker.str.zfill(4)))
print(f"訊號日 {len(dates)},有預測檔 {len(preds)};LIVE 帳本列 {len(want_live)}(訊號日 {ledger.signal_date.nunique()})")


def _ret(fr, c, i, events):
    """次日收盤進、MA20(訊號日)×0.96 收盤停損、20 棒到期;除息加回。"""
    n = len(fr)
    if i + 1 + HOLD >= n:
        return None
    ma20 = float(c.iloc[i]) / (1 + float(fr["price_vs_ma20"].iloc[i]))
    stop = ma20 * 0.96
    e = i + 1
    entry = float(c.iloc[e]); entry_date = fr["Date"].iloc[e]
    if not np.isfinite(entry) or entry <= 0:
        return None
    for j in range(e + 1, e + HOLD + 1):
        cum = sum(dv for dd, dv in events if entry_date < dd <= fr["Date"].iloc[j])
        px = float(c.iloc[j]) + cum
        if px < stop:
            return px / entry - 1
    cum = sum(dv for dd, dv in events if entry_date < dd <= fr["Date"].iloc[e + HOLD])
    return (float(c.iloc[e + HOLD]) + cum) / entry - 1


cand, live_rows = [], []
for f in sorted(DAILY.glob("*.csv")):
    tk = f.stem
    if not (tk.isdigit() and len(tk) == 4 and tk[0] != "0"):
        continue
    try:
        fr = annotate_momentum_continuation(prepare_stock_frame(f))
    except Exception:
        continue
    if fr.empty or len(fr) < 80:
        continue
    fr = fr.reset_index(drop=True)
    fr["Date"] = pd.to_datetime(fr["Date"]).dt.strftime("%Y-%m-%d")
    c = pd.to_numeric(fr["Close"], errors="coerce")
    vol = pd.to_numeric(fr["Volume"], errors="coerce")
    v20, v60 = fr["price_vs_ma20"], fr["price_vs_ma60"]
    vr, rsi = fr["volume_ratio_20d"], fr["RSI_14"]
    mrel = fr["macd_hist_delta_3d"] / c
    liq = vol.rolling(20).mean() >= 500_000
    gate = (v20.between(MA20_LOW, MA20_HIGH)) & (v60 > 0) & (vr.isna() | (vr <= MAX_VOL_RATIO)) \
        & (rsi.isna() | rsi.between(RSI_LOW, RSI_HIGH)) & (mrel >= MACD_FLOOR) & liq
    idx = {d: i for i, d in enumerate(fr["Date"])}
    events = CAL.get(tk, [])
    for d in preds:
        i = idx.get(d)
        if i is None or pd.isna(v20.iloc[i]):
            continue
        is_live = (d, tk) in want_live
        if not (is_live or bool(gate.iloc[i])):
            continue
        ret = _ret(fr, c, i, events)
        if ret is None:
            continue
        if is_live:
            live_rows.append({"date": d, "ticker": tk, "ret": ret, "in_pool": bool(gate.iloc[i])})
        if not bool(gate.iloc[i]):
            continue
        pr = preds[d]
        row = pr.loc[tk] if tk in pr.index else None
        om = float(row["operating_margin_latest"]) if row is not None and pd.notna(row["operating_margin_latest"]) else np.nan
        if np.isfinite(om) and om < 0:
            continue
        pred20 = float(row["pred_return_20d"]) if row is not None and pd.notna(row["pred_return_20d"]) else np.nan
        rec = str(row["recommendation"]) if row is not None else ""
        vr_i = float(vr.iloc[i]) if pd.notna(vr.iloc[i]) else 1.5
        cand.append({"date": d, "ticker": tk, "clean": -abs(float(v20.iloc[i]) - 0.04) - 0.1 * vr_i, "pred20": pred20,
                     "model_buy": ("買進" in rec) and np.isfinite(pred20) and pred20 > 0, "ret": ret})

df = pd.DataFrame(cand)
live = pd.DataFrame(live_rows)
df.to_csv(REPORTS / "bt_main_lane_ranking.csv", index=False, encoding="utf-8-sig")
live.to_csv(REPORTS / "bt_main_lane_ranking_live.csv", index=False, encoding="utf-8-sig")
print(f"型態池樣本 {len(df)}(訊號日 {df.date.nunique()},每日平均 {len(df)/max(df.date.nunique(),1):.0f} 檔;"
      f"Champion 過濾後每日平均 {df.model_buy.sum()/max(df.date.nunique(),1):.1f} 檔);LIVE 列可計算 {len(live)},其中在現行池內 {live.in_pool.mean()*100:.0f}%")


def pick(g, variant):
    if variant == "R0c":
        s = g[g.model_buy].assign(score=lambda x: x.clean + x.pred20)
    elif variant == "R3c":
        s = g[g.model_buy].assign(score=lambda x: x.clean)
    elif variant == "R1":
        s = g.assign(score=lambda x: x.clean)
    elif variant == "R2c":
        s = g.dropna(subset=["pred20"]).assign(score=lambda x: x.pred20)
    else:
        return g
    return s.sort_values("score", ascending=False).head(TOPN)


VARIANTS = ["R0c", "R3c", "R1", "R2c", "POOL"]
picks = {v: pd.concat([pick(g, v).assign(variant=v) for _, g in df.groupby("date")]) for v in VARIANTS}
# LIVE:dataA 期用帳本實際列;之前用 R0c(當時正式規則)
r0c_pre = picks["R0c"][picks["R0c"].date < LIVE_LEDGER_START]
picks["LIVE"] = pd.concat([r0c_pre[["date", "ticker", "ret"]], live[["date", "ticker", "ret"]]]).assign(variant="LIVE")


def stat(v):
    return {"n": len(v), "mean": v.mean() * 100, "net": v.mean() * 100 - FRICTION_PP, "med": v.median() * 100,
            "win": (v > 0).mean() * 100, "tail": (v < -0.10).mean() * 100, "big": (v > 0.15).mean() * 100}


def line(name, v):
    s = stat(v)
    return (f"{name:6} n={s['n']:5} 毛均{s['mean']:+6.2f}% 淨均{s['net']:+6.2f}% 中位{s['med']:+6.2f}% 勝率{s['win']:5.1f}% "
            f"跌逾10%:{s['tail']:4.1f}% 漲逾15%:{s['big']:4.1f}%")


ORDER = ["LIVE", "R1", "POOL", "R0c", "R3c", "R2c"]
print("\n=== 全部挑中樣本(20 棒,毛;淨=−0.4pp)===")
for v in ORDER:
    print(line(v, picks[v]["ret"]))

print("\n=== dataA 期(7/2~9/2)單獨:LIVE=帳本實際列 ===")
for v in ["LIVE", "R1", "POOL", "R0c"]:
    x = picks[v][picks[v].date >= LIVE_LEDGER_START]["ret"]
    print(line(v, x))

print("\n=== 逐月(挑中樣本平均毛報酬 / 勝率;括號=訊號日數)===")
months = sorted({d[:7] for d in df.date.unique()})
mon_ok = mon_n = 0
for m in months:
    parts = []
    for v in ["LIVE", "R1", "POOL", "R0c"]:
        x = picks[v][picks[v].date.str.startswith(m)]["ret"]
        parts.append(f"{v} {x.mean()*100:+6.2f}%/{(x>0).mean()*100:4.1f}%" if len(x) else f"{v} n=0")
    nd = df[df.date.str.startswith(m)].date.nunique()
    print(f"  {m} ({nd:2}d): " + " | ".join(parts))
    if nd >= 10:
        mon_n += 1
        l_ = picks["LIVE"][picks["LIVE"].date.str.startswith(m)]["ret"].mean()
        r1 = picks["R1"][picks["R1"].date.str.startswith(m)]["ret"].mean()
        mon_ok += int(r1 >= l_)

daily = {v: picks[v].groupby("date")["ret"].mean() for v in ["LIVE", "R1"]}
common = daily["LIVE"].index.intersection(daily["R1"].index)
day_frac = float((daily["R1"][common] > daily["LIVE"][common]).mean()) * 100
sl, s1, sp = stat(picks["LIVE"]["ret"]), stat(picks["R1"]["ret"]), stat(picks["POOL"]["ret"])
checks = [("1 毛均 R1 ≥ LIVE + 0.3pp", s1["mean"] >= sl["mean"] + 0.3),
          ("2 勝率 R1 ≥ LIVE − 1pp", s1["win"] >= sl["win"] - 1.0),
          (f"3 逐月 R1 ≥ LIVE ≥ 3/4 (實得 {mon_ok}/{mon_n})", mon_ok >= 3),
          (f"4 逐日籃子 R1 > LIVE ≥ 50% (實得 {day_frac:.0f}%,{len(common)} 日)", day_frac >= 50)]
print("\n事前判準(R1 對 LIVE):")
for name, ok in checks:
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
print("判決:", "PASS — 建議主 lane 排序改不依賴模型(仍需 PM 核可)" if all(ok for _, ok in checks) else "FAIL — 不採用 R1")
print(f"附帶:POOL 等權 {sp['mean']:+.2f}% | LIVE {sl['mean']:+.2f}% | R1 {s1['mean']:+.2f}% → "
      + ("兩者皆未贏過 POOL,排序本身無價值" if (sl["mean"] <= sp["mean"] and s1["mean"] <= sp["mean"]) else "至少一種排序有增值"))


def dedup(p):
    di = {d: i for i, d in enumerate(sorted(df.date.unique()))}
    p = p.sort_values(["ticker", "date"]); last, keep = {}, []
    for r in p.itertuples(index=False):
        if r.ticker not in last or di[r.date] - last[r.ticker] >= 20:
            keep.append(r.ret); last[r.ticker] = di[r.date]
    return pd.Series(keep)


print("\n穩健性:同檔 20 日去重")
for v in ["LIVE", "R1"]:
    print(line(v, dedup(picks[v])))
print(f"明細 -> {REPORTS / 'bt_main_lane_ranking.csv'} / _live.csv")
