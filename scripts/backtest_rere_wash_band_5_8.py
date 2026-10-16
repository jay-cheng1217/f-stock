# -*- coding: utf-8 -*-
"""BT-rere-wash-band-5-8(回測票 #29):洗盤 5~8% 帶(鈦昇型)值不值得納入 rere 型態?

背景(PM 2026-10-16「全開」):D 類回放顯示鈦昇 8027 她 7/1 進場時 10 日洗盤只有 4.4%,現行 8% 下限永遠擋掉;
淺洗盤型(8~10%,#BT-rere-shallow-wash)當初也是從台表科/鈦昇的邊界 miss 開出來並 PASS。本票再往下驗一帶。

母體(2020-01 起全市場股票日,與 #20/#23 同口徑):
  |距月線| <= 5%、量比 < 1.2、收 > 季線、20 日均量 >= 500 張、不要求外資拐點(v2 口徑),
  依 10 日洗盤深度(hi10/close − 1,公司行動連續還原)分三帶:BAND 5~8%(本票)、REF 8~10%(淺洗盤型,參照)、DEEP >= 10%(蹲點型)。
  報酬 = 次日收盤進、第 60 根收盤出、不停損、毛報酬;只用完整 60 根樣本;不設 cooldown。
事前判準(2026-10-16 寫死,先於執行提交;四項全過 = 建議納入,仍需 PM 核可與名額/帳本 cohort 設計):
  1. BAND 平均 >= REF 平均 − 0.5pp
  2. BAND 勝率 >= REF 勝率 − 2pp
  3. BAND 左尾(<= −20%)占比 <= REF 左尾 + 2pp
  4. 逐年 BAND 平均低於 REF 超過 3pp 的年份 <= 2/7
限制:不含已下市股票;樣本日重疊;5~8% 帶樣本數可能遠大於 REF(淺洗盤本來就常見),納入後名額如何分配另案。

用法:python -X utf8 scripts/backtest_rere_wash_band_5_8.py   (worktree 無日K時設 STOCK_BASE_DIR)
輸出:ml/reports/bt_rere_wash_band_5_8.csv + stdout
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
    av = pd.Series(c.to_numpy(float) * mult)
    wash = av.rolling(10).max() / av - 1
    fwd = (av.shift(-(HOLD + 1)) / av.shift(-1) - 1) * 100
    base = (((c / num["MA_20"] - 1).abs() <= 0.05) & (vol / vma < 1.2) & (c > num["MA_60"]) & (vma >= 500_000)
            & fwd.notna() & (k["Date"] >= "2020-01-01") & (wash >= 0.05))
    hit = pd.DataFrame({"date": k["Date"], "wash": wash, "fwd": fwd})[base.fillna(False)]
    if len(hit):
        rows.append(hit.assign(ticker=tk))

D = pd.concat(rows, ignore_index=True)
D["band"] = np.where(D["wash"] < 0.08, "BAND 5-8%", np.where(D["wash"] < 0.10, "REF 8-10%", "DEEP >=10%"))
D["year"] = D["date"].str[:4]
print(f"=== BT-rere-wash-band-5-8 樣本 {len(D)},{D.date.min()}~{D.date.max()} ===")
stats = {}
for band, g in D.groupby("band"):
    stats[band] = {"band": band, "n": len(g), "per_year": round(len(g) / 7, 0), "mean": g.fwd.mean(), "median": g.fwd.median(),
                   "win": (g.fwd > 0).mean() * 100, "tail20": (g.fwd <= -20).mean() * 100,
                   "yearly": g.groupby("year")["fwd"].mean()}
ref, band = stats["REF 8-10%"], stats["BAND 5-8%"]
worse_years = int(((band["yearly"] - ref["yearly"]) < -3.0).sum())
checks = [band["mean"] >= ref["mean"] - 0.5, band["win"] >= ref["win"] - 2.0,
          band["tail20"] <= ref["tail20"] + 2.0, worse_years <= 2]
for s in stats.values():
    print(f'{s["band"]:11} n={s["n"]:6}({s["per_year"]:.0f}/yr) 平均 {s["mean"]:+.2f}% 中位 {s["median"]:+.2f}% 勝率 {s["win"]:.1f}% '
          f'左尾(<=-20%) {s["tail20"]:.1f}%  逐年 ' + " ".join(f"{y}:{v:+.1f}" for y, v in s["yearly"].items()))
print(f"\nBAND 低於 REF 超過 3pp 的年份:{worse_years}/7")
print("判準 1-4:", "".join("✓" if ok else "✗" for ok in checks), "→", "PASS(建議納入,待 PM 核可)" if all(checks) else "FAIL(5~8% 帶不納入)")
out = REPO / "ml" / "reports" / "bt_rere_wash_band_5_8.csv"
pd.DataFrame([{k: v for k, v in s.items() if k != "yearly"} | {"yearly": " ".join(f"{y}:{v:+.2f}" for y, v in s["yearly"].items())}
              for s in stats.values()]).round(4).to_csv(out, index=False, encoding="utf-8-sig")
print("彙總:", out)
