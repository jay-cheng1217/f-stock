# -*- coding: utf-8 -*-
"""BT-breakout-basing-lanes:兩條候選新 lane 的正式回測——B4「突破型」與 B5「季線下打底型」。

背景(PM 2026-10-07「突破型與季線下打底兩條 lane,有無回測」):
  REPLAY-rere-new-rules(回測票 #16)顯示她 9 月漲最多的公開標的(睿生光電、南茂)不在 rere 型態內。
  突破型先前只有探索性線索(#9 創60日新高單一條件 60 棒超額 +2.17%;momentum_continuation_v1 每日回測 20 日中位 −3.56%),
  沒有 lane 等級、事前登記判準的回測;季線下打底完全沒測過(所有 rere 回測的共同條件都含 close>MA60)。
  本票唯讀研究,**不改任何 gate、不寫帳本**。判準在跑之前寫死(下方)。

共同口徑(與 BT-rere-stop-width 第二輪相同):4 碼普通股、20 日均量 >= 500 張;訊號日次一交易日收盤進場;完整 60 棒;
  同檔同 lane 20 日 cooldown;毛報酬;報酬與型態條件皆用公司行動連續還原序列(ml.corporate_actions)。
  主停損 = 訊號日收盤 −40%(與 rere lane 2026-10-08 起一致);另列較緊的結構停損與不停損供參考。
  **買不到的單剔除**:進場日一字鎖漲停(最高=最低且較訊號日收盤漲 >= 9%)視為無法成交,不計入(另印筆數)。
  超額 = 該筆報酬 − 同一訊號日全股票池(均量 >= 500 張)的 60 棒平均報酬(不停損)。

B4 突破型(主定義,判準對象):
  (1) 還原收盤創 60 日新高;(2) 「首次突破」:前 20 個交易日內沒有創過 60 日新高;(3) 放量:當日量 >= 1.5 × 20 日均量。
  參考變體(不判):B4a 不要求放量;B4b 加投信近 5 日買超;B4c 加基期收斂(前 60 日高低差 <= 30%);B4_CTRL 只要創 60 日新高。
B5 季線下打底型(主定義,判準對象):
  (1) 還原收盤在 MA60 之下、離季線 −15%~0%;(2) 打底:近 20 日最低收盤發生在 10 個交易日之前(10 日內未再破底);
  (3) 站回月線:收盤 > MA20 且 MA20 不低於 5 日前。
  參考變體(不判):B5a 加外資近 5 日買超;B5b 加投信近 5 日買超;B5c 加量縮(量比 < 1.0);B5_CTRL 只要在季線下 −15%~0%。
REF = rere v2 訊號(wash>=8% + |v20|<=5% + 量比<1.2 + close>MA60),用來核對引擎(應重現 #15 的 14,065 筆 / +6.55% / 47.6%)與提供尾部基準。

事前判準(2026-10-07 寫死,先於執行提交於 commit b682f26e;B4、B5 各自以主定義、主停損、毛報酬判定,任一 FAIL = 不建議開 lane):
  1. 樣本數 n >= 1,000
  2. 60 棒平均超額 >= +1.5pp
  3. 逐年(該年 n >= 20 才計)平均超額 > 0 的年份 >= 5
  4. 贏過同日市場平均的比例 >= 45%
  5. 跌逾 20% 的比例 <= REF + 3pp
  6. 逆風窗(訊號日 2026-04-01~2026-07-03)平均超額 >= −1pp
PASS 也只代表「值得以獨立標籤的並行模擬帳本前瞻驗證」,是否開 lane 由 PM 裁決。
限制:不含已下市股票(結果偏樂觀)、未扣成本(另印扣 0.785pp 的數字)、產業/題材未控制、樣本跨股票重疊。

用法:python -X utf8 scripts/backtest_breakout_basing_lanes.py   (worktree 無日K時設 STOCK_BASE_DIR 指向主工作目錄)
輸出:ml/reports/bt_breakout_basing_lanes.csv(各組彙總與逐年)+ stdout
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
HOLD, COOLDOWN, NET_COST_PP, LIQ_MIN = 60, 20, 0.785, 500_000
STOP_MAIN = 0.40
HEADWIND = ("2026-04-01", "2026-07-03")
COLS = ["Date", "High", "Low", "Close", "Volume", "VOL_MA_20", "MA_20", "MA_60", "Foreign_BuySell", "Trust_BuySell"]
PRIMARY = {"B4": "B4 突破型(主)", "B5": "B5 季線下打底(主)"}
REF = "REF rere v2"

sys.path.insert(0, str(REPO))
from ml.corporate_actions import backward_adjustment_multiplier, load_action_calendar  # noqa: E402

ACTIONS = load_action_calendar(str(BASE / "ml" / "data" / "ex_dividend_calendar.csv"),
                               str(BASE / "ml" / "data" / "corporate_action_price_factors.csv"))
ACTION_TICKERS = set(ACTIONS["stock_id"].astype(str))

trades, pool_parts, recent, unfillable = [], [], [], {}
camp_path = REPO / "ml" / "data" / "rere_public_campaigns.csv"
PUBLIC = set(pd.read_csv(camp_path, dtype=str).ticker.str.zfill(4)) if camp_path.exists() else set()

for f in sorted(DAILY.glob("*.csv")):
    tk = f.stem
    if not (tk.isdigit() and len(tk) == 4 and tk[0] != "0"):
        continue
    try:
        k = pd.read_csv(f, usecols=lambda c: c in COLS)
    except Exception:
        continue
    if len(k) < 150 or not {"Date", "High", "Low", "Close", "Volume", "VOL_MA_20", "MA_20", "MA_60"} <= set(k.columns):
        continue
    for col in ("Foreign_BuySell", "Trust_BuySell"):
        if col not in k.columns:
            k[col] = np.nan
    k["Date"] = k["Date"].astype(str).str[:10]
    num = {c: pd.to_numeric(k[c], errors="coerce") for c in COLS[1:]}
    c, hi, lo, vol, vma = num["Close"], num["High"], num["Low"], num["Volume"], num["VOL_MA_20"]
    n = len(k)
    mult = backward_adjustment_multiplier(k["Date"], tk, ACTIONS).to_numpy(float) if tk in ACTION_TICKERS else np.ones(n)
    av_s = c * mult                                                   # 連續還原收盤:型態條件與報酬共用
    av, cv, dates = av_s.to_numpy(float), c.to_numpy(float), k["Date"].to_numpy()
    ma20, ma60 = av_s.rolling(20).mean(), av_s.rolling(60).mean()
    vr, liq = vol / vma, (vma >= LIQ_MIN)
    fb5, tb5 = num["Foreign_BuySell"].rolling(5).sum(), num["Trust_BuySell"].rolling(5).sum()

    # --- B4 突破型 ---
    newhi = av_s > av_s.shift(1).rolling(60).max()
    first = ~(newhi.shift(1).rolling(20).sum() > 0)
    base_rng = av_s.shift(1).rolling(60).max() / av_s.shift(1).rolling(60).min() - 1
    b4 = newhi & first & (vr >= 1.5) & liq
    # --- B5 季線下打底 ---
    d60 = av_s / ma60 - 1
    below = (d60 < 0) & (d60 >= -0.15)
    low20 = av_s.rolling(20).min()
    based = av_s.rolling(10).min() > low20                            # 20 日最低收盤落在 10 日之前
    reclaim = (av_s > ma20) & (ma20 >= ma20.shift(5))
    b5 = below & based & reclaim & liq
    # --- REF:rere v2(條件用原始價與 CSV 均線,與 #15 同式)---
    ref = ((1 - c / c.rolling(10).max() >= 0.08) & ((c / num["MA_20"] - 1).abs() <= 0.05) & (vr < 1.2) & (c > num["MA_60"]) & liq)

    groups = {
        PRIMARY["B4"]: (b4, "lock", None), "B4a 不要求放量": (newhi & first & liq, "lock", None),
        "B4b +投信5日買超": (b4 & (tb5 > 0), "lock", None), "B4c +基期收斂<=30%": (b4 & (base_rng <= 0.30), "lock", None),
        "B4_CTRL 只要創60日新高": (newhi & liq, "lock", None),
        PRIMARY["B5"]: (b5, "lock", low20), "B5a +外資5日買超": (b5 & (fb5 > 0), "lock", low20),
        "B5b +投信5日買超": (b5 & (tb5 > 0), "lock", low20), "B5c +量縮<1.0": (b5 & (vr < 1.0), "lock", low20),
        "B5_CTRL 只要在季線下": (below & liq, "lock", low20),
        REF: (ref, "", None),
    }
    for name, (mask, lock, struct) in groups.items():
        m = mask.fillna(False).to_numpy()
        last = -10 ** 6
        for t in np.where(m)[0]:
            if t - last < COOLDOWN:
                continue
            e = t + 1
            if tk in PUBLIC and name in PRIMARY.values() and dates[t] >= "2026-07-01":
                recent.append((name, tk, dates[t]))                    # 覆蓋檢查:不要求完整 60 棒
            if t + 1 + HOLD >= n:
                if tk in PUBLIC and name in PRIMARY.values():
                    last = t
                continue
            entry = av[e]
            if not np.isfinite(entry) or entry <= 0:
                continue
            if lock and hi.iloc[e] == lo.iloc[e] and cv[e] >= cv[t] * 1.09:
                unfillable[name] = unfillable.get(name, 0) + 1         # 一字鎖漲停買不到:不成交、不啟動 cooldown
                continue
            last = t
            adj = av[e + 1:e + HOLD + 1]

            def run(level: float) -> float:
                hit = np.where(adj < level)[0]
                return ((adj[hit[0]] if len(hit) else adj[-1]) / entry - 1) * 100

            tight = (float(struct.iloc[t]) * 0.97) if struct is not None else cv[t] * 0.90 * mult[t]
            trades.append({"lane": name, "ticker": tk, "signal_date": dates[t], "year": dates[t][:4],
                           "ret": run(cv[t] * (1 - STOP_MAIN) * mult[t]), "ret_tight": run(tight),
                           "ret_nostop": (adj[-1] / entry - 1) * 100})
    fwd = (av_s.shift(-(HOLD + 1)) / av_s.shift(-1) - 1) * 100
    ok = (liq & fwd.notna()).to_numpy()
    pool_parts.append(pd.Series(fwd.to_numpy()[ok], index=dates[ok]))

pool = pd.concat(pool_parts).groupby(level=0).mean()
T = pd.DataFrame(trades)
T["pool"] = T.signal_date.map(pool)
T = T[T.pool.notna()].copy()
T["excess"] = T.ret - T.pool
days = T.signal_date.nunique()


def summary(name: str) -> dict:
    g = T[T.lane == name]
    yrs = [(y, x.excess.mean(), len(x)) for y, x in g.groupby("year") if len(x) >= 20]
    hw = g[(g.signal_date >= HEADWIND[0]) & (g.signal_date <= HEADWIND[1])]
    return {"lane": name, "n": len(g), "per_day": round(len(g) / max(g.signal_date.nunique(), 1), 1), "mean": g.ret.mean(),
            "median": g.ret.median(), "win": (g.ret > 0).mean() * 100, "pool": g.pool.mean(), "excess": g.excess.mean(),
            "excess_median": g.excess.median(), "beat_pool": (g.ret > g.pool).mean() * 100, "left20": (g.ret < -20).mean() * 100,
            "big30": (g.ret > 30).mean() * 100, "worst1": g.ret.quantile(.01), "mean_tight": g.ret_tight.mean(),
            "win_tight": (g.ret_tight > 0).mean() * 100, "mean_nostop": g.ret_nostop.mean(),
            "years_pos": sum(1 for _, ex, _ in yrs if ex > 0), "years_n": len(yrs),
            "yearly": " ".join(f"{y}:{ex:+.1f}({cnt})" for y, ex, cnt in yrs),
            "headwind_excess": hw.excess.mean() if len(hw) else float("nan"), "headwind_n": len(hw),
            "unfillable": unfillable.get(name, 0)}


S = pd.DataFrame([summary(nm) for nm in dict.fromkeys(T.lane)])
print(f"=== BT-breakout-basing-lanes 訊號 {T.signal_date.min()}~{T.signal_date.max()},{days} 個訊號日,股票池日均 60 棒報酬 {pool.mean():+.2f}% ===")
for r in S.itertuples():
    print(f"\n{r.lane}  n={r.n}(有訊號日平均 {r.per_day} 檔;買不到剔除 {r.unfillable})")
    print(f"  主停損−40%:均 {r.mean:+.2f}% 中位 {r.median:+.2f}% 勝率 {r.win:.1f}% | 同日市場 {r.pool:+.2f}% → 超額 {r.excess:+.2f}pp"
          f"(中位 {r.excess_median:+.2f}pp;扣成本 {r.excess - NET_COST_PP:+.2f}pp)| 贏過市場 {r.beat_pool:.1f}%")
    print(f"  尾部:跌逾20% {r.left20:.1f}% | 漲逾30% {r.big30:.1f}% | 最差1% {r.worst1:+.1f}% || 結構停損:均 {r.mean_tight:+.2f}% 勝率 {r.win_tight:.1f}%"
          f" | 不停損:均 {r.mean_nostop:+.2f}%")
    print(f"  逐年超額(筆數):{r.yearly} → 正超額 {r.years_pos}/{r.years_n} 年 | 逆風窗超額 {r.headwind_excess:+.2f}pp (n={r.headwind_n})")

ref_left = float(S.loc[S.lane == REF, "left20"].iloc[0])
print("\n=== 事前判準(主定義、主停損、毛報酬)===")
verdicts = {}
for key, name in PRIMARY.items():
    r = S[S.lane == name].iloc[0]
    checks = [(f"1 樣本數 >= 1,000 (實得 {r.n})", r.n >= 1000),
              (f"2 平均超額 >= +1.5pp (實得 {r.excess:+.2f}pp)", r.excess >= 1.5),
              (f"3 逐年正超額 >= 5 年 (實得 {r.years_pos}/{r.years_n})", r.years_pos >= 5),
              (f"4 贏過同日市場平均 >= 45% (實得 {r.beat_pool:.1f}%)", r.beat_pool >= 45),
              (f"5 跌逾20% <= REF {ref_left:.1f}% + 3pp (實得 {r.left20:.1f}%)", r.left20 <= ref_left + 3),
              (f"6 逆風窗超額 >= −1pp (實得 {r.headwind_excess:+.2f}pp, n={r.headwind_n})", r.headwind_excess >= -1)]
    print(name)
    for label, ok in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
    verdicts[key] = all(ok for _, ok in checks)
    print("  判決:", "PASS — 可提請 PM 以獨立標籤並行模擬帳本驗證" if verdicts[key] else "FAIL — 不建議開 lane")

print("\n=== 覆蓋檢查:她已公開標的自 2026-07-01 起在主定義下的訊號日(僅看有沒有被列出,不含報酬)===")
R = pd.DataFrame(recent, columns=["lane", "ticker", "signal_date"]).drop_duplicates()
for name in PRIMARY.values():
    g = R[R.lane == name]
    print(name, "→", "; ".join(f"{tk}:{','.join(x.signal_date.str[5:])}" for tk, x in g.groupby("ticker")) or "無")
    print("   從未觸發:", sorted(PUBLIC - set(g.ticker)))

out = REPO / "ml" / "reports" / "bt_breakout_basing_lanes.csv"
S.round(3).to_csv(out, index=False, encoding="utf-8-sig")
print("\n彙總:", out)
