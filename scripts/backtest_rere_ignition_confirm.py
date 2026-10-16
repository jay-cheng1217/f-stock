# -*- coding: utf-8 -*-
"""BT-rere-ignition-confirm(回測票 #27):發動型的「投信同日買」是不是唯一對的確認?替代確認有沒有被冤枉?

背景(PM 2026-10-16「系統對買進過於保守,投信同日買也不是應歸責,你應該要多加學習」):
瑞耘 6532 於 9/29 發動(外資 +753 張、融資乾淨、散戶四週退),發動型因投信=0 不觸發,系統等到 10/13 回踩才出卡,
晚兩週、貴 6%。舊依據 BT-ignition-day 只驗過「發動+投信同買(50%/+8.79%)優於發動單獨(44%)」,
**從未驗過外資大買、散戶退場等替代確認**;發動型歷史僅 13 筆,等於「不追發動」從未被完整驗證。

母體(發動日事件,全市場股票日,2020-01 起):
  前一日還原收盤的 10 日洗盤深度 >= 10%(washed)、當日還原收盤站回 MA20 之上、量比 >= 1.5、收盤 > MA60、
  20 日均量 >= 500 張。不設 cooldown;報酬 = 次日收盤進、其後第 60 根收盤(公司行動連續還原、不停損、毛報酬);
  只用完整 60 根樣本。與 #20/#23/#25/#26 同口徑;基準=同事件日母體平均與全部事件平均。
五種確認(當日可知資料;TDCC 依 #20 慣例:週五資料 +3 天後可用、21 天內有效、前第 4 筆差):
  V0 無確認(全部發動日)                 V1 投信同日買 > 0(現行 gate 的確認項)
  V2 外資同日淨買 >= 當日量 10%           V3 散戶持股比 4 週變化 <= -1.0pp
  V4 V1/V2/V3 任一成立
事前判準(2026-10-16 寫死,先於執行提交;「放寬成立」須 V2/V3/V4 之一四項全過):
  1. 該變體 60 根平均 >= V1 平均 - 1.0pp
  2. 該變體平均 - V0 平均 >= +2.0pp
  3. 2020-2022 與 2023-2026 兩段,該變體平均 - V0 平均都 >= +1.0pp
  4. 該變體事件數 >= 3 × V1 事件數(確認要有覆蓋,否則只是換一個一樣窄的門)
判決用途:成立 → 只構成「向 PM 提發動型 gate 修改案」的依據,改 gate 仍需 PM 核可+指紋/帳本 cohort 處理;
不成立 → 「發動不追」視為已驗證的設計,錯失類似 6532 的個案是已知代價,不再以個案翻案。
反方向或其他組合即使好看,只能當下一張事前登記票,不得直接採用。
限制:不含已下市股票;樣本日重疊、無顯著性檢定;投信/外資欄位缺值視為條件不成立(fail closed);
TDCC 覆蓋不足的事件在 V3/V4 以「條件不成立」計,不剔除(與 gate 實際行為一致)。

用法:python -X utf8 scripts/backtest_rere_ignition_confirm.py   (worktree 無日K時設 STOCK_BASE_DIR)
輸出:ml/reports/bt_rere_ignition_confirm.csv + stdout
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
HOLD = 60
COLS = ["Date", "Close", "Volume", "VOL_MA_20", "MA_20", "MA_60", "Foreign_BuySell", "Trust_BuySell"]

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
TDCC = {tk: g[["avail", "retail4w"]].dropna() for tk, g in tdcc.groupby("Ticker")}

events = []
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
    washed_prev = (1 - av / av.rolling(10).max()).shift(1) >= 0.10
    ev = pd.DataFrame({
        "date": k["Date"].to_numpy(), "ticker": tk,
        "fwd": (av.shift(-(HOLD + 1)) / av.shift(-1) - 1) * 100,
        "trust_buy": num["Trust_BuySell"] > 0,
        "foreign_big": num["Foreign_BuySell"] >= 0.10 * vol,
    })
    mask = (washed_prev & (c > num["MA_20"]) & (vol / vma >= 1.5) & (c > num["MA_60"])
            & (vma >= 500_000) & ev["fwd"].notna() & (ev["date"] >= "2020-01-01"))
    hit = ev[mask.fillna(False)].copy()
    if hit.empty:
        continue
    weekly = TDCC.get(tk)
    if weekly is not None and len(weekly):
        hit["dt"] = pd.to_datetime(hit["date"])
        hit = pd.merge_asof(hit.sort_values("dt"), weekly.sort_values("avail"), left_on="dt", right_on="avail",
                            direction="backward", tolerance=pd.Timedelta(days=21)).drop(columns=["dt", "avail"])
    else:
        hit["retail4w"] = np.nan
    events.append(hit)

D = pd.concat(events, ignore_index=True)
D["trust_buy"] = D["trust_buy"].fillna(False)
D["foreign_big"] = D["foreign_big"].fillna(False)
D["retail_out"] = D["retail4w"] <= -1.0           # NaN → False(fail closed,同 gate 行為)
D["year"] = D["date"].str[:4]
D["half"] = np.where(D["year"] <= "2022", "2020-22", "2023-26")
print(f"=== BT-rere-ignition-confirm 發動日事件 {len(D)},{D.date.min()}~{D.date.max()},"
      f"事件日 {D.date.nunique()},V0 平均 {D.fwd.mean():+.2f}% ===")

VARIANTS = {"V0 無確認": pd.Series(True, index=D.index), "V1 投信同買(現行)": D["trust_buy"],
            "V2 外資淨買>=10%量": D["foreign_big"], "V3 散戶4週減>=1pp": D["retail_out"],
            "V4 任一成立": D["trust_buy"] | D["foreign_big"] | D["retail_out"]}
base_mean = D["fwd"].mean()
halves_base = D.groupby("half")["fwd"].mean()
rows = []
v1_stats = {}
for name, m in VARIANTS.items():
    g = D[m]
    halves = g.groupby("half")["fwd"].mean()
    yearly = g.groupby("year")["fwd"].mean()
    stats = {"variant": name, "events": len(g), "per_year": round(len(g) / 7, 1), "mean": g["fwd"].mean(),
             "median": g["fwd"].median(), "win": (g["fwd"] > 0).mean() * 100,
             "vs_v0": g["fwd"].mean() - base_mean,
             "h1_vs_v0": halves.get("2020-22", np.nan) - halves_base.get("2020-22", np.nan),
             "h2_vs_v0": halves.get("2023-26", np.nan) - halves_base.get("2023-26", np.nan),
             "yearly": " ".join(f"{y}:{v:+.1f}" for y, v in yearly.items())}
    if name.startswith("V1"):
        v1_stats = stats
    rows.append(stats)
for s in rows:
    if s["variant"].startswith(("V2", "V3", "V4")):
        checks = [s["mean"] >= v1_stats["mean"] - 1.0, s["vs_v0"] >= 2.0,
                  s["h1_vs_v0"] >= 1.0 and s["h2_vs_v0"] >= 1.0, s["events"] >= 3 * v1_stats["events"]]
        s["checks"] = "".join("✓" if ok else "✗" for ok in checks)
        s["verdict"] = "PASS" if all(checks) else "FAIL"
    else:
        s["checks"], s["verdict"] = "", "—"
    print(f'{s["variant"]:16} | n={s["events"]:5}({s["per_year"]}/yr) | 平均 {s["mean"]:+.2f}% 中位 {s["median"]:+.2f}% '
          f'勝率 {s["win"]:.1f}% | 對V0 {s["vs_v0"]:+.2f}pp(前段{s["h1_vs_v0"]:+.2f}/後段{s["h2_vs_v0"]:+.2f}) '
          f'| {s["checks"]} {s["verdict"]}')
    print(f'    逐年:{s["yearly"]}')
passed = [s["variant"] for s in rows if s.get("verdict") == "PASS"]
print("\n判決:", ("放寬候選成立:" + "、".join(passed) + "(仍需 PM 核可才改 gate)") if passed
      else "沒有替代確認四項全過——「發動+投信」維持現狀,發動不追屬已驗證設計")
out = REPO / "ml" / "reports" / "bt_rere_ignition_confirm.csv"
pd.DataFrame(rows).round(4).to_csv(out, index=False, encoding="utf-8-sig")
print("彙總:", out)
