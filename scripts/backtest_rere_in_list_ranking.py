# -*- coding: utf-8 -*-
"""BT-rere-in-list-ranking:rere 候選名單「內部」哪個看得到的特徵能分出贏家(研究,不改任何排序)。

背景(PM 2026-10-12「先做第1和第2項」):型態本身對市場沒有超額(#17 +0.71pp),每天卻有二三十檔候選、只上榜十幾檔。
現行排序 = 族群強度 + 洗盤深度,沒有驗證過。她的優勢若在「挑哪一檔」,系統能做的是找出名單內有鑑別力的可觀察特徵。

母體:rere v2 型態候選的每個股票日(還原收盤 10 日高回落 >=8%、|距月線|<=5%、量比<1.2、收盤>季線、20 日均量>=500 張),
  **不設 cooldown**(要的是每天的候選池);報酬 = 次日收盤進、其後第 60 根收盤(公司行動連續還原、不停損、毛報酬);
  只用完整 60 根的樣本;每個訊號日的候選池至少 8 檔才計。比較對象是**同一天的候選池平均**,不是全市場。
特徵與事前假設方向(+ 越大越好,− 越小越好;方向來自她的打法與既有回測,不看結果再決定):
  wash +        洗盤深度(現行排序用它)          vr −          當日量比(越乾越好)
  vol_dry5 −    近 5 日均量 / 20 日均量              v20 −         距月線(越靠下緣越好)
  v60 +         距季線(趨勢品質,#8)              mom60 +       前 60 日漲幅(洗盤前的第一段)
  first_leg +   近 30 日最大單日漲幅(點火強度)     rsi −         RSI14(洗得越徹底越好)
  trust20 +     投信近 20 日買超 / 同期成交量        trust5 +      投信近 5 日買超 / 同期成交量
  foreign20 −   外資近 20 日買超 / 同期成交量(外資賣、投信接)
  margin20 −    融資餘額 20 日變化率(融資減少較好)
  whale4w +     千張大戶持股比 4 週變化(集保,資料日 +3 天後才可用)
  retail4w −    散戶持股比 4 週變化
指標(逐日計算再平均):Spearman IC;「每天取該特徵前 3 名」相對候選池平均的超額;前 3 名贏過候選池平均的天數比例。

事前判準(2026-10-12 寫死,先於執行提交;逐特徵、依假設方向判定,五項全過才算「可用於排序」):
  1. 平均日 IC(依假設方向)>= +0.03
  2. 逐年平均 IC 與假設同向的年份 >= 6/7
  3. 每日前 3 名平均超額 >= +1.5pp
  4. 2020-2022 與 2023-2026 兩段的前 3 名超額都 >= +1.0pp
  5. 前 3 名贏過候選池平均的天數比例 >= 55%
一次檢定 14 個特徵,會有碰運氣通過的風險;2、4 兩項(跨年與跨段穩定)是為此而設。反方向的結果一併列出但不判定——
若反方向看起來很強,只能當下一張事前登記票的假設,不能直接採用。PASS 也只建議以影子排序並行驗證,不改現行名單。
限制:不含已下市股票;樣本日重疊(60 根前瞻視窗),不做顯著性檢定;集保週資料有缺週,4 週變化以「前第 4 筆」近似。

用法:python -X utf8 scripts/backtest_rere_in_list_ranking.py   (worktree 無日K時設 STOCK_BASE_DIR 指向主工作目錄)
輸出:ml/reports/bt_rere_in_list_ranking.csv + stdout
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
HOLD, MIN_POOL, TOP_N = 60, 8, 3
SIGNS = {"wash": 1, "vr": -1, "vol_dry5": -1, "v20": -1, "v60": 1, "mom60": 1, "first_leg": 1, "rsi": -1,
         "trust20": 1, "trust5": 1, "foreign20": -1, "margin20": -1, "whale4w": 1, "retail4w": -1}
COLS = ["Date", "Close", "Volume", "VOL_MA_20", "MA_20", "MA_60", "RSI_14", "Foreign_BuySell", "Trust_BuySell", "Margin_Balance"]

sys.path.insert(0, str(REPO))
from ml.corporate_actions import backward_adjustment_multiplier, load_action_calendar  # noqa: E402

ACTIONS = load_action_calendar(str(BASE / "ml" / "data" / "ex_dividend_calendar.csv"),
                               str(BASE / "ml" / "data" / "corporate_action_price_factors.csv"))
ACTION_TICKERS = set(ACTIONS["stock_id"].astype(str))

tdcc = pd.read_csv(BASE / "集保分散" / "tdcc_summary.csv", dtype={"Ticker": str, "Date": str})
tdcc = tdcc[tdcc["Ticker"].str.fullmatch(r"[1-9]\d{3}")].copy()
tdcc["week"] = pd.to_datetime(tdcc["Date"], format="%Y%m%d", errors="coerce")
tdcc = tdcc.dropna(subset=["week"]).sort_values(["Ticker", "week"])
for src, dst in (("Whale_Pct", "whale4w"), ("Retail_Pct", "retail4w")):
    tdcc[dst] = pd.to_numeric(tdcc[src], errors="coerce").groupby(tdcc["Ticker"]).diff(4)
tdcc["avail"] = tdcc["week"] + pd.Timedelta(days=3)               # 週五資料,下週一起才算已知
TDCC = {tk: g[["avail", "whale4w", "retail4w"]].dropna(subset=["avail"]) for tk, g in tdcc.groupby("Ticker")}

rows = []
for f in sorted(DAILY.glob("*.csv")):
    tk = f.stem
    if not (tk.isdigit() and len(tk) == 4 and tk[0] != "0"):
        continue
    try:
        k = pd.read_csv(f, usecols=lambda c: c in COLS)
    except Exception:
        continue
    if len(k) < 150 or not {"Date", "Close", "Volume", "VOL_MA_20", "MA_20", "MA_60"} <= set(k.columns):
        continue
    for col in COLS:
        if col not in k.columns:
            k[col] = np.nan
    k["Date"] = k["Date"].astype(str).str[:10]
    num = {c: pd.to_numeric(k[c], errors="coerce") for c in COLS[1:]}
    c, vol, vma = num["Close"], num["Volume"], num["VOL_MA_20"]
    mult = (backward_adjustment_multiplier(k["Date"], tk, ACTIONS).to_numpy(float) if tk in ACTION_TICKERS
            else np.ones(len(k)))
    av = pd.Series(c.to_numpy(float) * mult)
    feat = pd.DataFrame({
        "wash": 1 - av / av.rolling(10).max(), "vr": vol / vma, "vol_dry5": vol.rolling(5).mean() / vma,
        "v20": c / num["MA_20"] - 1, "v60": c / num["MA_60"] - 1, "mom60": av / av.shift(60) - 1,
        "first_leg": av.pct_change().rolling(30).max(), "rsi": num["RSI_14"],
        "trust20": num["Trust_BuySell"].rolling(20).sum() / vol.rolling(20).sum(),
        "trust5": num["Trust_BuySell"].rolling(5).sum() / vol.rolling(5).sum(),
        "foreign20": num["Foreign_BuySell"].rolling(20).sum() / vol.rolling(20).sum(),
        "margin20": num["Margin_Balance"] / num["Margin_Balance"].shift(20) - 1,
    })
    feat["fwd"] = (av.shift(-(HOLD + 1)) / av.shift(-1) - 1) * 100
    feat["date"] = k["Date"].to_numpy()
    mask = ((feat["wash"] >= 0.08) & (feat["v20"].abs() <= 0.05) & (feat["vr"] < 1.2) & (c > num["MA_60"])
            & (vma >= 500_000) & feat["fwd"].notna())
    cand = feat[mask.fillna(False)].copy()
    if cand.empty:
        continue
    cand["ticker"] = tk
    weekly = TDCC.get(tk)
    if weekly is not None and len(weekly):
        cand["dt"] = pd.to_datetime(cand["date"])
        cand = pd.merge_asof(cand.sort_values("dt"), weekly.sort_values("avail"), left_on="dt", right_on="avail",
                             direction="backward", tolerance=pd.Timedelta(days=21)).drop(columns=["dt", "avail"])
    else:
        cand["whale4w"], cand["retail4w"] = np.nan, np.nan
    rows.append(cand)

D = pd.concat(rows, ignore_index=True).replace([np.inf, -np.inf], np.nan)
sizes = D.groupby("date")["fwd"].transform("size")
D = D[sizes >= MIN_POOL].copy()
D["year"] = D["date"].str[:4]
D["half"] = np.where(D["year"] <= "2022", "2020-22", "2023-26")
D["pool_mean"] = D.groupby("date")["fwd"].transform("mean")
days = D["date"].nunique()
print(f"=== BT-rere-in-list-ranking 候選股票日 {len(D)},訊號日 {days},每日候選中位 {int(D.groupby('date').size().median())} 檔,"
      f"{D.date.min()}~{D.date.max()};候選池 60 根平均 {D.fwd.mean():+.2f}% ===")


def daily_stats(feature: str, sign: int) -> pd.DataFrame:
    out = []
    for day, g in D.groupby("date"):
        x = g[[feature, "fwd"]].dropna()
        if len(x) < MIN_POOL:
            continue
        ic = x[feature].rank().corr(x["fwd"].rank())
        top = x.sort_values(feature, ascending=(sign < 0)).head(TOP_N)["fwd"].mean()
        rev = x.sort_values(feature, ascending=(sign > 0)).head(TOP_N)["fwd"].mean()
        out.append({"date": day, "ic": ic * sign, "top": top - x["fwd"].mean(), "rev": rev - x["fwd"].mean()})
    r = pd.DataFrame(out)
    r["year"] = r["date"].str[:4]
    r["half"] = np.where(r["year"] <= "2022", "2020-22", "2023-26")
    return r


summary = []
for feature, sign in SIGNS.items():
    r = daily_stats(feature, sign)
    yearly_ic = r.groupby("year")["ic"].mean()
    halves = r.groupby("half")["top"].mean()
    checks = [r["ic"].mean() >= 0.03, int((yearly_ic > 0).sum()) >= 6, r["top"].mean() >= 1.5,
              bool((halves >= 1.0).all()) and len(halves) == 2, (r["top"] > 0).mean() >= 0.55]
    summary.append({"feature": feature, "direction": "+" if sign > 0 else "−", "days": len(r), "ic": r["ic"].mean(),
                    "ic_pos_days": (r["ic"] > 0).mean() * 100, "years_same_sign": int((yearly_ic > 0).sum()),
                    "years": len(yearly_ic), "top3_excess": r["top"].mean(), "top3_h1": halves.get("2020-22", np.nan),
                    "top3_h2": halves.get("2023-26", np.nan), "top3_beat_days": (r["top"] > 0).mean() * 100,
                    "reverse_top3_excess": r["rev"].mean(), "passed": sum(checks), "verdict": "PASS" if all(checks) else "FAIL",
                    "checks": "".join("✓" if ok else "✗" for ok in checks),
                    "yearly_ic": " ".join(f"{y}:{v:+.3f}" for y, v in yearly_ic.items())})
S = pd.DataFrame(summary).sort_values("top3_excess", ascending=False)
print("\n特徵(假設方向) | 平均IC | IC>0天數 | 同向年數 | 前3名超額 | 前段/後段 | 前3名勝天數 | 反方向前3名 | 判準1-5 | 判決")
for r in S.itertuples():
    print(f"{r.feature:10}({r.direction}) | {r.ic:+.3f} | {r.ic_pos_days:4.1f}% | {r.years_same_sign}/{r.years} | {r.top3_excess:+5.2f}pp | "
          f"{r.top3_h1:+5.2f}/{r.top3_h2:+5.2f} | {r.top3_beat_days:4.1f}% | {r.reverse_top3_excess:+5.2f}pp | {r.checks} | {r.verdict}")
print("\n逐年 IC(依假設方向):")
for r in S.itertuples():
    print(f"  {r.feature:10} {r.yearly_ic}")
passed = S[S.verdict == "PASS"].feature.tolist()
print("\n判決:", ("可用於排序的特徵:" + "、".join(passed)) if passed else "沒有任何特徵五項全過——名單內排序找不到穩定的可觀察依據")
out = REPO / "ml" / "reports" / "bt_rere_in_list_ranking.csv"
S.round(4).to_csv(out, index=False, encoding="utf-8-sig")
print("彙總:", out)
