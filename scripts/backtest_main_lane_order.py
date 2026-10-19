# -*- coding: utf-8 -*-
"""BT-main-lane-order(回測票 #44):主 lane 12 張的挑法——現行乾淨度排序 vs 中性,與四種有事前理由的參考排序。

背景(PM 2026-10-19「開 #44,先回測驗證」):#43 事後描述顯示,逐日等權下 2021 起乾淨度前 12 名 +0.19pp ≈ 型態池 +0.23pp,
乾淨度十分位平坦,只有 2026 年前 12 名明顯較好。本票正式檢驗兩件事:現行排序該不該像 rere(#23)那樣改成中性;
有沒有一種有事前理由的排序,在型態池內確實挑得比較好。
透明聲明:P0 對 N 的逐年數字已在 #43 事後描述看過,判準 (a) 雖先於本腳本執行登記,但不是盲測;參考排序 R/M/V/F 未曾看過。

母體 = #43 同一個型態池與報酬口徑(scripts/backtest_main_veto_backfill.py:產生器門檻常數、近 20 日量無缺且均量 >= 500 張、
  次日收盤進、20 根內收盤破訊號日 MA20×0.96 出、股東報酬連續還原、超額減同期進場時點流動母體;有當日預測檔的日子套母體與
  營益率防呆、資料待確認列排後);訊號日 2021-01-04 起走滿 20 根;當日池 >= 24 檔才計(至少名額兩倍,排序才有作用)。
政策(每日挑 12 檔;逐日算「挑中 12 檔平均超額 − 中性」,再跨日平均):
  P0 現行:乾淨度(clean = −|距月線 − 4%| − 0.1×量比)由高到低,資料待確認列排後。
  N  中性:隨機挑 12 檔的期望值 = 當日池平均(不抽樣)。
  參考排序(單向、固定公式、不調參數;缺值的股票不參與該排序,有值者不足 12 檔的日子該排序不計;同值依代號):
  R 集保:散戶持股比 4 週變化最負(週資料日 +3 天才可用,21 天內最近一筆;同 #23)。事前理由:rere #20/#23 散戶退場。
  M 中期動能:t−120 到 t−20 的還原報酬最高(6-1 動能)。
  V 低波動:近 60 日還原日報酬標準差最低。
  F 外資:近 20 日外資淨買超股數 ÷ 近 20 日均量最高(20 日內任一日缺值即不參與)。
指標:逐日差的平均、中位、低於中性的日數比例、逐年(2021~2026 共 6 年,2026 至 9 月)、前後兩段(2021-2023 / 2024-2026)、
  左尾(挑中筆數裡報酬 <= −10% 的比例)、月區塊 t 值(每月平均後計,描述用)。

事前判準(2026-10-19 寫死,先於執行提交):
 (a) 現行改中性?三項全過才建議改中性:P0 − N 平均 <= −0.3pp;逐年 P0 − N < 0 的年份 >= 4/6;前後兩段 P0 − N 都 < 0。
     任一不過 = 維持現行乾淨度排序。
 (b) 參考排序 X 進前瞻影子驗證?五項全過才建議(依 #23 先例,不直接改正式排序):X − N 平均 >= +0.3pp;X − P0 平均 >= +0.3pp;
     逐年 X − N > 0 的年份 >= 5/6;前後兩段 X − N 都 > 0;X 左尾 <= P0 左尾 + 1pp。
 任何正式排序變更都需 PM 核可。限制:不含已下市股票;樣本日重疊;N 為期望值;2026-03 前無營益率防呆;處置股未回放。

用法:python -X utf8 scripts/backtest_main_lane_order.py
輸出:ml/reports/bt_main_lane_order.csv + stdout
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
# 沿用 #43 的母體與防呆口徑(import 時已把 stdout 包成 UTF-8、關閉警告)
from scripts import backtest_main_veto_backfill as bt  # noqa: E402
from ml.corporate_actions import backward_adjustment_multiplier, load_action_calendar  # noqa: E402
from scripts import generate_entry_candidates as gen  # noqa: E402
from scripts.momentum_continuation_backtest import annotate_momentum_continuation, prepare_stock_frame  # noqa: E402

HOLD, TOPN, MIN_POOL = bt.HOLD, bt.TOPN, 24
C_START = bt.C_START
OUT = bt.REPO / "ml" / "reports" / "bt_main_lane_order.csv"
# 參考排序:(欄位, 由小到大?)
REF = {"R": ("retail4w", True), "M": ("mom_6_1", False), "V": ("vol60", True), "F": ("foreign20", False)}
LABEL = {"P0": "P0 現行:乾淨度", "R": "R 集保:散戶 4 週減最多", "M": "M 中期動能 6-1", "V": "V 低波動 60 日",
         "F": "F 外資 20 日買超/均量"}


def load_tdcc() -> dict[str, pd.DataFrame]:
    t = pd.read_csv(bt.BASE / "集保分散" / "tdcc_summary.csv", dtype={"Ticker": str, "Date": str})
    t = t[t["Ticker"].str.fullmatch(r"[1-9]\d{3}")].copy()
    t["week"] = pd.to_datetime(t["Date"], format="%Y%m%d", errors="coerce")
    t = t.dropna(subset=["week"]).sort_values(["Ticker", "week"])
    t["retail4w"] = pd.to_numeric(t["Retail_Pct"], errors="coerce").groupby(t["Ticker"]).diff(4)
    t["avail"] = t["week"] + pd.Timedelta(days=3)
    return {tk: g[["avail", "retail4w"]] for tk, g in t.groupby("Ticker")}


def build_pool() -> pd.DataFrame:
    actions = load_action_calendar(str(bt.BASE / "ml" / "data" / "ex_dividend_calendar.csv"),
                                   str(bt.BASE / "ml" / "data" / "corporate_action_price_factors.csv"))
    action_tickers = set(actions["stock_id"].astype(str))
    tdcc = load_tdcc()
    parts, closes, liquid = [], {}, {}
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
        fb = (pd.to_numeric(f["Foreign_BuySell"], errors="coerce") if "Foreign_BuySell" in f
              else pd.Series(np.nan, index=f.index))
        foreign20 = (fb.rolling(20).sum() / vol.rolling(20).mean()).to_numpy()
        n = len(f)
        rows = []
        for i in np.where(gate.to_numpy() & (dts >= C_START))[0]:
            e = i + 1
            if e + HOLD >= n or not (np.isfinite(av[e]) and av[e] > 0):
                continue
            stop = float(cl.iloc[i]) / (1 + float(v20.iloc[i])) * 0.96 * mult[i]
            hit = np.where(av[e + 1:e + HOLD + 1] < stop)[0]
            x = e + 1 + hit[0] if len(hit) else e + HOLD
            vr_i = float(vr.iloc[i]) if pd.notna(vr.iloc[i]) else 1.5
            rows.append({"as_of": dts[i], "tk": tk, "clean": -abs(float(v20.iloc[i]) - 0.04) - 0.1 * vr_i,
                         "tech_nan": bool(pd.isna(vr.iloc[i]) or pd.isna(rsi.iloc[i])),
                         "mom_6_1": mom[i], "vol60": vol60[i], "foreign20": foreign20[i],
                         "entry_date": dts[e], "exit_date": dts[x], "ret": (av[x] / av[e] - 1) * 100})
        if not rows:
            continue
        part = pd.DataFrame(rows)
        weekly = tdcc.get(tk)
        if weekly is not None and len(weekly):
            part["dt"] = pd.to_datetime(part["as_of"])
            part = pd.merge_asof(part.sort_values("dt"), weekly.sort_values("avail"), left_on="dt", right_on="avail",
                                 direction="backward", tolerance=pd.Timedelta(days=21)).drop(columns=["dt", "avail"])
        else:
            part["retail4w"] = np.nan
        parts.append(part)
    D = pd.concat(parts, ignore_index=True)
    M = pd.DataFrame(closes).sort_index()
    LQ = pd.DataFrame(liquid).reindex(index=M.index, columns=M.columns).fillna(False).astype(bool)
    mkt = M.pct_change(fill_method=None).where(LQ.shift(1).fillna(False).astype(bool)).mean(axis=1).fillna(0.0)
    mcum = (1 + mkt).cumprod()
    D["ex"] = D["ret"] - (mcum.reindex(D["exit_date"]).to_numpy() / mcum.reindex(D["entry_date"]).to_numpy() - 1) * 100
    return D


def apply_universe(D: pd.DataFrame) -> pd.DataFrame:
    """與 #43 main() 相同:有預測檔的日子限母體、營益率 < 0 排除、資料待確認列標記。"""
    univ = bt.load_universe_om()
    D["has_pred"] = D["as_of"].isin(set(univ))
    D = D[[(not hp) or (t in univ[d][0]) for d, t, hp in zip(D["as_of"], D["tk"], D["has_pred"])]].copy()
    D["om"] = [univ[d][0].get(t, np.nan) if hp else np.nan for d, t, hp in zip(D["as_of"], D["tk"], D["has_pred"])]
    D["has_om"] = [hp and univ[d][1] for d, hp in zip(D["as_of"], D["has_pred"])]
    D = D[~(D["om"] < 0)].copy()
    D["watch"] = D["has_pred"] & ((D["has_om"] & D["om"].isna()) | D["tech_nan"])
    return D


def summarize(name: str, s: pd.Series, picked: pd.DataFrame) -> dict:
    s = s.dropna()
    year = s.groupby(s.index.str[:4]).mean()
    half = s.groupby(np.where(s.index.str[:4] <= "2023", "2021-23", "2024-26")).mean()
    month = s.groupby(s.index.str[:7]).mean()
    t = month.mean() / (month.std(ddof=1) / np.sqrt(len(month))) if len(month) > 1 else np.nan
    return {"policy": name, "days": len(s), "mean": s.mean(), "median": s.median(), "below_pct": (s < 0).mean() * 100,
            **{f"y{y}": v for y, v in year.items()}, "h1": half.get("2021-23", np.nan), "h2": half.get("2024-26", np.nan),
            "t_month": t, "tail10": (picked["ret"] <= -10).mean() * 100, "n_trades": len(picked)}


def main() -> None:
    D = apply_universe(build_pool())
    print(f"=== BT-main-lane-order 型態池 {len(D)} 筆(#43 同口徑應為 362,448),訊號日 {D.as_of.min()}~{D.as_of.max()} ===")
    vs_n = {k: {} for k in ["P0", *REF]}
    vs_p0 = {k: {} for k in REF}
    picked = {k: [] for k in ["P0", *REF]}
    pool_tail, n_days = [], 0
    for as_of, day in D.groupby("as_of"):
        if len(day) < MIN_POOL:
            continue
        n_days += 1
        neutral = day["ex"].mean()
        pool_tail.append(day)
        p0 = day.sort_values(["watch", "clean", "tk"], ascending=[True, False, True]).head(TOPN)
        vs_n["P0"][as_of] = p0["ex"].mean() - neutral
        picked["P0"].append(p0)
        for k, (col, asc) in REF.items():
            valid = day.dropna(subset=[col])
            if len(valid) < TOPN:
                continue
            x = valid.sort_values([col, "tk"], ascending=[asc, True]).head(TOPN)
            vs_n[k][as_of] = x["ex"].mean() - neutral
            vs_p0[k][as_of] = x["ex"].mean() - p0["ex"].mean()
            picked[k].append(x)
    pool_all = pd.concat(pool_tail)
    print(f"計入訊號日 {n_days}(池 >= {MIN_POOL} 檔);中性(池平均)逐日平均超額 "
          f"{pool_all.groupby('as_of')['ex'].mean().mean():+.2f}pp,池左尾 {(pool_all['ret'] <= -10).mean() * 100:.1f}%")

    rows = []
    for k in ["P0", *REF]:
        s = summarize(LABEL[k], pd.Series(vs_n[k]), pd.concat(picked[k]))
        s["key"] = k
        if k in REF:
            s["mean_vs_p0"] = pd.Series(vs_p0[k]).mean()
        rows.append(s)
    S = pd.DataFrame(rows)
    years = sorted(c for c in S.columns if c.startswith("y"))
    print("\n逐日「挑中 12 檔 − 中性」(pp):")
    for _, r in S.iterrows():
        yr = " ".join(f"{c[1:]}:{r[c]:+.2f}" for c in years)
        extra = f" | 對 P0 {r['mean_vs_p0']:+.2f}" if pd.notna(r.get("mean_vs_p0")) else ""
        print(f"  {r['policy']:22} 日數 {r['days']:4} 平均 {r['mean']:+.2f} 中位 {r['median']:+.2f} 低於中性 {r['below_pct']:.0f}% "
              f"t(月) {r['t_month']:+.2f} 前段 {r['h1']:+.2f} 後段 {r['h2']:+.2f} 左尾 {r['tail10']:.1f}%{extra}")
        print(f"  {'':22} 逐年 {yr}")

    p0 = S[S.key == "P0"].iloc[0]
    neg_years = sum(p0[c] < 0 for c in years)
    a = [p0["mean"] <= -0.3, neg_years >= 4, p0["h1"] < 0 and p0["h2"] < 0]
    print(f"\n(a) 現行改中性?平均 <= −0.3pp {'✓' if a[0] else '✗'};負的年份 {neg_years}/6 >= 4 {'✓' if a[1] else '✗'};"
          f"兩段皆負 {'✓' if a[2] else '✗'} → " + ("PASS:建議改中性(需 PM 核可)" if all(a) else "FAIL:維持現行乾淨度排序"))
    verdict_b = []
    for k in REF:
        r = S[S.key == k].iloc[0]
        pos_years = sum(r[c] > 0 for c in years)
        b = [r["mean"] >= 0.3, r["mean_vs_p0"] >= 0.3, pos_years >= 5, r["h1"] > 0 and r["h2"] > 0,
             r["tail10"] <= p0["tail10"] + 1.0]
        ok = all(b)
        verdict_b.append((k, ok))
        print(f"(b) {LABEL[k]:22} 對中性 >= +0.3 {'✓' if b[0] else '✗'} 對 P0 >= +0.3 {'✓' if b[1] else '✗'} "
              f"正的年份 {pos_years}/6 >= 5 {'✓' if b[2] else '✗'} 兩段皆正 {'✓' if b[3] else '✗'} 左尾 {'✓' if b[4] else '✗'} → "
              + ("PASS:建議進前瞻影子驗證(不直接改正式排序)" if ok else "FAIL"))
    S["verdict"] = ""
    S.loc[S.key == "P0", "verdict"] = "a_PASS" if all(a) else "a_FAIL"
    for k, ok in verdict_b:
        S.loc[S.key == k, "verdict"] = "b_PASS" if ok else "b_FAIL"
    S.round(4).to_csv(OUT, index=False, encoding="utf-8-sig")
    print("彙總:", OUT)


if __name__ == "__main__":
    main()
