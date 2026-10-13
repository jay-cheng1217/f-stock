# -*- coding: utf-8 -*-
"""BT-rere-list-ordering:rere 名單「用洗盤深度挑名額」是否比中性挑法差(研究;通過也要 PM 核可才改排序)。

背景(PM 2026-10-13「做全部」):回測票 #20 顯示洗盤深度在候選池內的 IC 七年全為負、每天挑最深 3 檔比池平均少 1.29pp。
但 #20 是「挑 3 檔對全池」;實際名單的名額是 v2 兩型各 3 檔(`_pick_rere_v2`:蹲點型 v2 = 洗盤 >=10%、淺洗盤型 v2 = 8~10%,
各自依分數取前 3,不足由另一型補滿 6 檔)。本票照實際名額結構重做。

母體:同 #20——rere v2 型態候選的每個股票日(還原收盤 10 日高回落 >=8%、|距月線|<=5%、量比<1.2、收盤>季線、均量>=500張),
  不設 cooldown;報酬 = 次日收盤進、其後第 60 根收盤(公司行動連續還原、不停損、毛報酬);只用完整 60 根;
  當日候選至少 7 檔才計(6 檔以下全數上榜,排序沒有作用)。
  族群強度是即時值、沒有歷史版本,無法回測;本票只檢驗分數裡的「洗盤深度」這一半,族群強度維持不動。
政策(每個訊號日各挑 6 檔):
  P0 現行:兩型各依洗盤深度取最深 3 檔,不足由另一型依深度補滿。
  N  中性:同樣的兩型名額,但型內隨機抽——以期望值計算(各型候選的平均報酬,依實際名額加權),不做隨機抽樣。
  R  集保(參考):兩型各取散戶持股比 4 週減最多 3 檔(該型有集保數值者不足 3 檔時由另一型補)。
  S  最淺(參考):兩型各取洗盤最淺 3 檔。
指標:逐日「P0 − N」再平均(以及 R − N、S − N);逐年、前後兩段。

事前判準(2026-10-13 寫死,先於執行提交;三項全過才建議把型內排序由洗盤深度改為中性):
  1. P0 − N 平均 <= −0.5pp
  2. 逐年 P0 − N < 0 的年份 >= 5/7
  3. 2020-2022 與 2023-2026 兩段的 P0 − N 都 < 0
PASS = 現行的深度排序在實際名額下確實比不排還差 → 提請 PM 核可改為中性排序(以日期+代號的雜湊決定型內順序,確定且可重現);
FAIL = 維持現行排序。R、S 只供參考,不能據以改用。限制:不含已下市股票;樣本日重疊;N 為期望值近似(補位時以另一型平均計)。

用法:python -X utf8 scripts/backtest_rere_list_ordering.py   (worktree 無日K時設 STOCK_BASE_DIR 指向主工作目錄)
輸出:ml/reports/bt_rere_list_ordering.csv + stdout
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
HOLD, MIN_POOL, PER_TYPE, TOTAL = 60, 7, 3, 6
COLS = ["Date", "Close", "Volume", "VOL_MA_20", "MA_20", "MA_60"]

sys.path.insert(0, str(REPO))
from ml.corporate_actions import backward_adjustment_multiplier, load_action_calendar  # noqa: E402

ACTIONS = load_action_calendar(str(BASE / "ml" / "data" / "ex_dividend_calendar.csv"),
                               str(BASE / "ml" / "data" / "corporate_action_price_factors.csv"))
ACTION_TICKERS = set(ACTIONS["stock_id"].astype(str))

tdcc = pd.read_csv(BASE / "集保分散" / "tdcc_summary.csv", dtype={"Ticker": str, "Date": str})
tdcc = tdcc[tdcc["Ticker"].str.fullmatch(r"[1-9]\d{3}")].copy()
tdcc["week"] = pd.to_datetime(tdcc["Date"], format="%Y%m%d", errors="coerce")
tdcc = tdcc.dropna(subset=["week"]).sort_values(["Ticker", "week"])
tdcc["retail4w"] = pd.to_numeric(tdcc["Retail_Pct"], errors="coerce").groupby(tdcc["Ticker"]).diff(4)
tdcc["avail"] = tdcc["week"] + pd.Timedelta(days=3)
TDCC = {tk: g[["avail", "retail4w"]] for tk, g in tdcc.groupby("Ticker")}

rows = []
for f in sorted(DAILY.glob("*.csv")):
    tk = f.stem
    if not (tk.isdigit() and len(tk) == 4 and tk[0] != "0"):
        continue
    try:
        k = pd.read_csv(f, usecols=COLS)
    except Exception:
        continue
    if len(k) < 150:
        continue
    k["Date"] = k["Date"].astype(str).str[:10]
    num = {c: pd.to_numeric(k[c], errors="coerce") for c in COLS[1:]}
    c, vol, vma = num["Close"], num["Volume"], num["VOL_MA_20"]
    mult = (backward_adjustment_multiplier(k["Date"], tk, ACTIONS).to_numpy(float) if tk in ACTION_TICKERS
            else np.ones(len(k)))
    av = pd.Series(c.to_numpy(float) * mult)
    wash = 1 - av / av.rolling(10).max()
    fwd = (av.shift(-(HOLD + 1)) / av.shift(-1) - 1) * 100
    mask = ((wash >= 0.08) & ((c / num["MA_20"] - 1).abs() <= 0.05) & (vol / vma < 1.2) & (c > num["MA_60"])
            & (vma >= 500_000) & fwd.notna()).fillna(False)
    cand = pd.DataFrame({"date": k["Date"][mask].to_numpy(), "wash": wash[mask].to_numpy(), "fwd": fwd[mask].to_numpy()})
    if cand.empty:
        continue
    cand["ticker"] = tk
    weekly = TDCC.get(tk)
    if weekly is not None and len(weekly):
        cand["dt"] = pd.to_datetime(cand["date"])
        cand = pd.merge_asof(cand.sort_values("dt"), weekly.sort_values("avail"), left_on="dt", right_on="avail",
                             direction="backward", tolerance=pd.Timedelta(days=21)).drop(columns=["dt", "avail"])
    else:
        cand["retail4w"] = np.nan
    rows.append(cand)

D = pd.concat(rows, ignore_index=True)
D["ptype"] = np.where(D["wash"] >= 0.10, "shakeout", "shallow")


def pick(group: pd.DataFrame, key: str, ascending: bool) -> pd.DataFrame:
    """兩型各取 3 檔,不足由另一型依同一排序補滿 6 檔(與 _pick_rere_v2 相同的名額結構)。"""
    ranked = group.dropna(subset=[key]).sort_values([key, "ticker"], ascending=[ascending, True])
    first = pd.concat([ranked[ranked.ptype == "shakeout"].head(PER_TYPE), ranked[ranked.ptype == "shallow"].head(PER_TYPE)])
    rest = ranked.drop(first.index)
    return pd.concat([first, rest.head(TOTAL - len(first))])


def neutral_expectation(group: pd.DataFrame) -> float:
    sh, sl = group[group.ptype == "shakeout"]["fwd"], group[group.ptype == "shallow"]["fwd"]
    k_sh, k_sl = min(PER_TYPE, len(sh)), min(PER_TYPE, len(sl))
    extra = TOTAL - k_sh - k_sl                               # 補位:來自名額未用完的那一型
    if extra > 0:
        if len(sh) > k_sh:
            k_sh += min(extra, len(sh) - k_sh)
        elif len(sl) > k_sl:
            k_sl += min(extra, len(sl) - k_sl)
    parts = ([sh.mean()] * k_sh if k_sh else []) + ([sl.mean()] * k_sl if k_sl else [])
    return float(np.mean(parts))


daily = []
for day, g in D.groupby("date"):
    if len(g) < MIN_POOL:
        continue
    n = neutral_expectation(g)
    rec = {"date": day, "pool": len(g), "neutral": n, "P0": pick(g, "wash", False)["fwd"].mean() - n,
           "S": pick(g, "wash", True)["fwd"].mean() - n}
    retail = pick(g, "retail4w", True)
    rec["R"] = retail["fwd"].mean() - n if len(retail) >= TOTAL else np.nan
    daily.append(rec)
R = pd.DataFrame(daily)
R["year"] = R["date"].str[:4]
R["half"] = np.where(R["year"] <= "2022", "2020-22", "2023-26")
print(f"=== BT-rere-list-ordering 訊號日 {len(R)}(候選 >= {MIN_POOL} 檔),每日候選中位 {int(R.pool.median())} 檔,"
      f"{R.date.min()}~{R.date.max()};中性挑法的 60 根平均 {R.neutral.mean():+.2f}% ===")
summary = []
for name, label in (("P0", "P0 現行(型內取最深)"), ("S", "S 型內取最淺"), ("R", "R 型內取散戶減最多")):
    x = R[name].dropna()
    yearly = R.dropna(subset=[name]).groupby("year")[name].mean()
    halves = R.dropna(subset=[name]).groupby("half")[name].mean()
    summary.append({"policy": label, "days": len(x), "mean_vs_neutral": x.mean(), "median_vs_neutral": x.median(),
                    "days_below_neutral": (x < 0).mean() * 100, "years_negative": int((yearly < 0).sum()), "years": len(yearly),
                    "h1": halves.get("2020-22", np.nan), "h2": halves.get("2023-26", np.nan),
                    "yearly": " ".join(f"{y}:{v:+.2f}" for y, v in yearly.items())})
    print(f"{label:20} 對中性 平均 {x.mean():+.2f}pp 中位 {x.median():+.2f}pp | 低於中性的日數 {(x < 0).mean() * 100:.1f}% | "
          f"負值年數 {int((yearly < 0).sum())}/{len(yearly)} | 前段 {halves.get('2020-22', np.nan):+.2f} 後段 {halves.get('2023-26', np.nan):+.2f}")
    print(f"    逐年:{summary[-1]['yearly']}")
p0 = summary[0]
checks = [(f"1 P0 − 中性 平均 <= −0.5pp (實得 {p0['mean_vs_neutral']:+.2f}pp)", p0["mean_vs_neutral"] <= -0.5),
          (f"2 逐年為負 >= 5/7 (實得 {p0['years_negative']}/{p0['years']})", p0["years_negative"] >= 5),
          (f"3 前後兩段皆為負 (前 {p0['h1']:+.2f} / 後 {p0['h2']:+.2f})", p0["h1"] < 0 and p0["h2"] < 0)]
print("\n事前判準(現行深度排序是否比中性差):")
for label, ok in checks:
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
print("判決:", "PASS — 提請 PM 核可把型內排序改為中性" if all(ok for _, ok in checks) else "FAIL — 維持現行排序")
out = REPO / "ml" / "reports" / "bt_rere_list_ordering.csv"
pd.DataFrame(summary).round(4).to_csv(out, index=False, encoding="utf-8-sig")
print("彙總:", out)
