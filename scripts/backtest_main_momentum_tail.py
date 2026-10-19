# -*- coding: utf-8 -*-
"""BT-main-momentum-tail(回測票 #45):主 lane 用中期動能(6-1)挑 12 張,加尾部控制後能否保住平均、壓住左尾?

背景(PM 2026-10-19「開新票驗 M 加尾部控制,先回測驗證」):#44 的 M(6-1 動能)每筆比中性 +0.98pp、6/6 年為正,
只差左尾一項(15.0% vs 現行 9.7%,門檻 10.7%)。本票是看過 #44 結果才提出的假設,因此:變體只用事前理由、不調參數;
另用 #44 沒看過的 2020 下半年當方向確認;通過也只進前瞻影子驗證,不直接改正式排序。

左尾的兩個可能來源各配一個變體(都不改出場規則——出場屬鎖定清單):
  MA 風險調整動能:6-1 報酬 ÷ 近 60 日日報酬標準差,由高到低。理由:波動越大的動能股越容易跳空穿過停損。
  MV 低波動半邊的動能:當日池內近 60 日波動 <= 當日中位數者,再依 6-1 報酬由高到低。理由同上,改用過濾。
  MS 近停損的動能:距月線 <= +4% 者(產生器乾淨度的甜蜜點常數),再依 6-1 報酬由高到低。理由:停損是月線×0.96,
     距月線越遠停損越遠,一被停損就容易超過 −10%;只挑停損近的,單筆最大虧損由出場規則本身限制。
參考:P0 現行乾淨度、M 原始動能(#44 數字重現)、N 中性(池平均,隨機挑的期望值)。

母體與報酬口徑 = #44(=#43):產生器門檻常數的型態池、近 20 日量無缺且均量 >= 500 張、次日收盤進、20 根內收盤破訊號日
  MA20×0.96 出、股東報酬連續還原、超額減同期進場時點流動母體;有當日預測檔的日子套母體與營益率防呆、資料待確認列排後;
  當日池 >= 24 檔才計;6-1 報酬 = 還原收盤 t−20 ÷ t−120 − 1,缺值者不參與動能類排序,有值者不足 12 檔的日子該排序不計。
期間:主期 2021-01-04 ~ 走滿 20 根(#44 已看過);保留期 2020-07-01 ~ 2020-12-31(日K 自 2020-01 起,6-1 需 120 根,M 未曾看過)。

事前判準(2026-10-19 寫死,先於執行提交;每個變體 X 七項全過 = PASS):
  主期:X − N 平均 >= +0.3pp;X − P0 平均 >= +0.3pp;逐年 X − N > 0 的年份 >= 5/6;前後兩段(2021-23 / 2024-26)X − N 都 > 0;
       X 左尾(報酬 <= −10% 的比例)<= P0 左尾 + 1pp。
  保留期:X − N 平均 > 0;X 左尾 <= 同期 P0 左尾 + 1pp。
  PASS → 建議進前瞻影子驗證(影子帳 >= 60 交易日,PM 核可後實作),不直接改正式排序;多個 PASS 全部進影子。全 FAIL → 結案,M 不再追。
限制:保留期只有半年、且是 2020 疫情後的多頭;不含已下市股票(越早期存活者偏誤越大);樣本日重疊;三個變體有多重比較風險。

用法:python -X utf8 scripts/backtest_main_momentum_tail.py
輸出:ml/reports/bt_main_momentum_tail.csv + stdout
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
# 沿用 #43/#44 的常數、母體限制與營益率防呆(import 時已把 stdout 包成 UTF-8、關閉警告)
from scripts import backtest_main_veto_backfill as bt  # noqa: E402
from scripts import backtest_main_lane_order as lo  # noqa: E402
from ml.corporate_actions import backward_adjustment_multiplier, load_action_calendar  # noqa: E402
from scripts import generate_entry_candidates as gen  # noqa: E402
from scripts.momentum_continuation_backtest import annotate_momentum_continuation, prepare_stock_frame  # noqa: E402

HOLD, TOPN, MIN_POOL = bt.HOLD, bt.TOPN, lo.MIN_POOL
HOLDOUT_START, MAIN_START = "2020-07-01", "2021-01-01"
MS_V20_MAX = 0.04                      # 產生器乾淨度的甜蜜點(clean = −|v20 − 0.04| …),不是本票調出來的
OUT = bt.REPO / "ml" / "reports" / "bt_main_momentum_tail.csv"
LABEL = {"P0": "P0 現行乾淨度", "M": "M 原始動能 6-1", "MA": "MA 動能÷波動", "MV": "MV 低波動半邊動能",
         "MS": "MS 距月線<=4% 動能"}
VARIANTS = ("MA", "MV", "MS")


def build_pool() -> pd.DataFrame:
    """#44 build_pool 同式(不含集保/外資),另存 v20,訊號日自 2020-07 起。"""
    actions = load_action_calendar(str(bt.BASE / "ml" / "data" / "ex_dividend_calendar.csv"),
                                   str(bt.BASE / "ml" / "data" / "corporate_action_price_factors.csv"))
    action_tickers = set(actions["stock_id"].astype(str))
    rows, closes, liquid = [], {}, {}
    for p in sorted(bt.DAILY.glob("*.csv")):
        tk = p.stem
        if not (tk.isdigit() and len(tk) == 4 and tk[0] != "0"):
            continue
        try:
            f = annotate_momentum_continuation(prepare_stock_frame(p)).reset_index(drop=True)
        except Exception:
            continue
        if len(f) < 80:
            continue
        f["Date"] = pd.to_datetime(f["Date"]).dt.strftime("%Y-%m-%d")
        dts = f["Date"].to_numpy()
        cl = pd.to_numeric(f["Close"], errors="coerce")
        vol = pd.to_numeric(f["Volume"], errors="coerce")
        v20, v60 = f["price_vs_ma20"], f["price_vs_ma60"]
        vr, rsi = f["volume_ratio_20d"], f["RSI_14"]
        mrel = f["macd_hist_delta_3d"] / cl.where(cl > 0)
        liq = (vol.rolling(20).mean() >= bt.LIQ_MIN) & vol.rolling(20).count().eq(20)
        gate = (v20.between(gen.MA20_LOW, gen.MA20_HIGH) & (v60 > 0)
                & (vr.isna() | (vr <= gen.MAX_VOL_RATIO))
                & (rsi.isna() | rsi.between(gen.RSI_LOW, gen.RSI_HIGH))
                & (mrel >= gen.MACD_DELTA_FLOOR_REL) & liq).fillna(False)
        mult = (backward_adjustment_multiplier(f["Date"], tk, actions).to_numpy(float) if tk in action_tickers
                else np.ones(len(f)))
        av = cl.to_numpy(float) * mult
        avs = pd.Series(av)
        closes[tk] = pd.Series(av, index=dts)
        liquid[tk] = pd.Series(liq.fillna(False).to_numpy(bool), index=dts)
        mom = (avs.shift(20) / avs.shift(120) - 1).to_numpy()
        vol60 = avs.pct_change(fill_method=None).rolling(60).std().to_numpy()
        n = len(f)
        for i in np.where(gate.to_numpy() & (dts >= HOLDOUT_START))[0]:
            e = i + 1
            if e + HOLD >= n or not (np.isfinite(av[e]) and av[e] > 0):
                continue
            stop = float(cl.iloc[i]) / (1 + float(v20.iloc[i])) * 0.96 * mult[i]
            hit = np.where(av[e + 1:e + HOLD + 1] < stop)[0]
            x = e + 1 + hit[0] if len(hit) else e + HOLD
            vr_i = float(vr.iloc[i]) if pd.notna(vr.iloc[i]) else 1.5
            rows.append({"as_of": dts[i], "tk": tk, "v20": float(v20.iloc[i]),
                         "clean": -abs(float(v20.iloc[i]) - 0.04) - 0.1 * vr_i,
                         "tech_nan": bool(pd.isna(vr.iloc[i]) or pd.isna(rsi.iloc[i])),
                         "mom_6_1": mom[i], "vol60": vol60[i],
                         "entry_date": dts[e], "exit_date": dts[x], "ret": (av[x] / av[e] - 1) * 100})
    D = pd.DataFrame(rows)
    M = pd.DataFrame(closes).sort_index()
    LQ = pd.DataFrame(liquid).reindex(index=M.index, columns=M.columns).fillna(False).astype(bool)
    mkt = M.pct_change(fill_method=None).where(LQ.shift(1).fillna(False).astype(bool)).mean(axis=1).fillna(0.0)
    mcum = (1 + mkt).cumprod()
    D["ex"] = D["ret"] - (mcum.reindex(D["exit_date"]).to_numpy() / mcum.reindex(D["entry_date"]).to_numpy() - 1) * 100
    return D


def pick(day: pd.DataFrame, policy: str) -> pd.DataFrame | None:
    if policy == "P0":
        return day.sort_values(["watch", "clean", "tk"], ascending=[True, False, True]).head(TOPN)
    valid = day.dropna(subset=["mom_6_1"])
    if policy == "MA":
        valid = valid[valid["vol60"] > 0].assign(score=lambda x: x["mom_6_1"] / x["vol60"])
    elif policy == "MV":
        med = day["vol60"].median()                             # 中位數取當日整個池(有波動值者)
        valid = valid[valid["vol60"] <= med].assign(score=lambda x: x["mom_6_1"])
    elif policy == "MS":
        valid = valid[valid["v20"] <= MS_V20_MAX].assign(score=lambda x: x["mom_6_1"])
    else:                                                       # M
        valid = valid.assign(score=lambda x: x["mom_6_1"])
    if len(valid) < TOPN:
        return None
    return valid.sort_values(["score", "tk"], ascending=[False, True]).head(TOPN)


def stats(vs_n: pd.Series, vs_p0: pd.Series | None, picked: pd.DataFrame) -> dict:
    s = vs_n.dropna()
    month = s.groupby(s.index.str[:7]).mean()
    t = month.mean() / (month.std(ddof=1) / np.sqrt(len(month))) if len(month) > 1 else np.nan
    out = {"days": len(s), "mean": s.mean(), "median": s.median(), "below_pct": (s < 0).mean() * 100, "t_month": t,
           "tail10": (picked["ret"] <= -10).mean() * 100, "n_trades": len(picked),
           "pick_v20": picked["v20"].mean() * 100, "pick_vol60": picked["vol60"].mean() * 100,
           "mean_vs_p0": vs_p0.mean() if vs_p0 is not None else np.nan}
    year = s.groupby(s.index.str[:4]).mean()
    out.update({f"y{y}": v for y, v in year.items()})
    half = s.groupby(np.where(s.index.str[:4] <= "2023", "h1", "h2")).mean()
    out.update({"h1": half.get("h1", np.nan), "h2": half.get("h2", np.nan)})
    return out


def run_period(D: pd.DataFrame, lo_date: str, hi_date: str) -> pd.DataFrame:
    P = D[(D.as_of >= lo_date) & (D.as_of <= hi_date)]
    policies = ("P0", "M", *VARIANTS)
    vs_n = {k: {} for k in policies}
    vs_p0 = {k: {} for k in policies}
    picked = {k: [] for k in policies}
    for as_of, day in P.groupby("as_of"):
        if len(day) < MIN_POOL:
            continue
        neutral = day["ex"].mean()
        p0 = pick(day, "P0")
        for k in policies:
            x = p0 if k == "P0" else pick(day, k)
            if x is None:
                continue
            vs_n[k][as_of] = x["ex"].mean() - neutral
            vs_p0[k][as_of] = x["ex"].mean() - p0["ex"].mean()
            picked[k].append(x)
    rows = []
    for k in policies:
        r = stats(pd.Series(vs_n[k], dtype=float), None if k == "P0" else pd.Series(vs_p0[k], dtype=float),
                  pd.concat(picked[k]))
        rows.append({"key": k, "policy": LABEL[k], **r})
    return pd.DataFrame(rows)


def show(S: pd.DataFrame, title: str, years: list[str]) -> None:
    print(f"\n{title}(逐日「挑中 12 檔 − 中性」,pp;挑中股平均距月線 / 60 日波動 %):")
    for _, r in S.iterrows():
        vp0 = f" | 對 P0 {r['mean_vs_p0']:+.2f}" if pd.notna(r["mean_vs_p0"]) else ""
        print(f"  {r['policy']:16} 日數 {r['days']:4} 平均 {r['mean']:+.2f} 中位 {r['median']:+.2f} 低於中性 {r['below_pct']:.0f}% "
              f"t(月) {r['t_month']:+.2f} 左尾 {r['tail10']:.1f}% 距月線 {r['pick_v20']:+.1f}% 波動 {r['pick_vol60']:.2f}%{vp0}")
        if years:
            print(f"  {'':16} 逐年 " + " ".join(f"{y[1:]}:{r[y]:+.2f}" for y in years)
                  + f" | 前段 {r['h1']:+.2f} 後段 {r['h2']:+.2f}")


def main() -> None:
    D = lo.apply_universe(build_pool())
    main_n = (D.as_of >= MAIN_START).sum()
    print(f"=== BT-main-momentum-tail 型態池 {len(D)} 筆(主期 {main_n} 筆,#44 同口徑應為 362,448),"
          f"訊號日 {D.as_of.min()}~{D.as_of.max()} ===")
    last = D["as_of"].max()
    SM = run_period(D, MAIN_START, last)
    SH = run_period(D, HOLDOUT_START, "2020-12-31")
    years = sorted(c for c in SM.columns if c.startswith("y"))
    show(SM, f"主期 {MAIN_START[:4]}~{last}", years)
    show(SH, "保留期 2020-07~2020-12", [])

    p0m, p0h = SM[SM.key == "P0"].iloc[0], SH[SH.key == "P0"].iloc[0]
    verdicts = {}
    print("\n事前判準(主期五項 + 保留期兩項):")
    for k in VARIANTS:
        m, h = SM[SM.key == k].iloc[0], SH[SH.key == k].iloc[0]
        pos_years = sum(m[y] > 0 for y in years)
        checks = [("主期對中性 >= +0.3", m["mean"] >= 0.3), ("主期對 P0 >= +0.3", m["mean_vs_p0"] >= 0.3),
                  (f"正的年份 {pos_years}/6 >= 5", pos_years >= 5), ("兩段皆正", m["h1"] > 0 and m["h2"] > 0),
                  (f"主期左尾 {m['tail10']:.1f}% <= {p0m['tail10'] + 1:.1f}%", m["tail10"] <= p0m["tail10"] + 1.0),
                  ("保留期對中性 > 0", h["mean"] > 0),
                  (f"保留期左尾 {h['tail10']:.1f}% <= {p0h['tail10'] + 1:.1f}%", h["tail10"] <= p0h["tail10"] + 1.0)]
        ok = all(c for _, c in checks)
        verdicts[k] = "PASS" if ok else "FAIL"
        print(f"  {LABEL[k]:16} " + " ".join(f"{name}{'✓' if c else '✗'}" for name, c in checks)
              + f" → {'PASS:建議進前瞻影子驗證' if ok else 'FAIL'}")
    if not any(v == "PASS" for v in verdicts.values()):
        print("三個變體全 FAIL → 結案,M 不再追。")
    out = pd.concat([SM.assign(period="main"), SH.assign(period="holdout_2020H2")], ignore_index=True)
    out["verdict"] = out["key"].map(verdicts).where(out["period"] == "main", "")
    out.round(4).to_csv(OUT, index=False, encoding="utf-8-sig")
    print("彙總:", OUT)


if __name__ == "__main__":
    main()
