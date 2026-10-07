# -*- coding: utf-8 -*-
"""REPLAY-rere-new-rules:把 2026-10-08 起生效的 rere 新規則回放到過去一段訊號日,看「當時若已改規則」帳面會長怎樣。

唯讀研究工具,不改任何規則、不寫帳本。回答的問題(PM 2026-10-07):「如果回到 9 月初,邏輯改變後帳面是否會趨近 rere?」

新規則 = 現行三型(蹲點/發動/淺洗盤,上限 6)+ v2 兩型(不要求外資拐點,上限 3+3),停損 = 訊號日收盤 × 0.60;
進場 = 交易日(訊號次日)收盤(帳本口徑),標到資料最後一日(**未滿 60 棒,是進行中的帳面**)。報酬用公司行動連續還原。

三個對照:
  1. 同期實際帳本(舊規則,真的上過名單的列)。
  2. 同一批訊號改用舊停損 MA60×0.97。
  3. **同日進場的全市場等權平均**(均量 >= 500 張的普通股)——普漲行情下絕對報酬會騙人,必須對同日基準看超額。

已知近似(結論要連同這些一起讀):產業強弱檔用今天的(只影響同日排序);處置/重訊 veto 與「主 lane 已列者排除」未回放;
未扣成本;不含已下市股票;同一檔連續多日觸發會重複計入(另印去重後數字)。

「她已公開標的」一欄只是公開標的的股價漲跌,**不是她的損益**——她的部位、成本、進出場與整體績效都看不到。

用法:python -X utf8 scripts/replay_rere_new_rules.py [--start 2026-08-31] [--end 2026-10-05]
      (worktree 無日K時設 STOCK_BASE_DIR 指向主工作目錄)
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
REPO = Path(__file__).resolve().parents[1]
BASE = Path(os.environ.get("STOCK_BASE_DIR") or REPO)
DAILY = BASE / "日K資料"
sys.path.insert(0, str(REPO))

from ml.corporate_actions import backward_adjustment_multiplier, load_action_calendar  # noqa: E402
from scripts import generate_entry_candidates as gen  # noqa: E402
from scripts.momentum_continuation_backtest import annotate_momentum_continuation, prepare_stock_frame  # noqa: E402

LIQ_MIN = 500_000


def _stat(name: str, v: pd.Series) -> str:
    return (f"{name:30} n={len(v):4} 均{v.mean():+6.2f}% 中位{v.median():+6.2f}% 勝率{(v > 0).mean() * 100:5.1f}% "
            f"漲逾20%:{int((v >= 20).sum()):3} 跌逾20%:{int((v <= -20).sum()):3}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2026-08-31", help="第一個訊號日(as_of)")
    ap.add_argument("--end", default="2026-10-05", help="最後一個訊號日(as_of)")
    a = ap.parse_args()

    actions = load_action_calendar(str(BASE / "ml" / "data" / "ex_dividend_calendar.csv"),
                                   str(BASE / "ml" / "data" / "corporate_action_price_factors.csv"))
    action_tickers = set(actions["stock_id"].astype(str))
    sec = pd.read_csv(BASE / "ml" / "data" / "sector_mapping.csv", dtype=str)
    sect, names = dict(zip(sec.Ticker, sec.Sector)), dict(zip(sec.Ticker, sec.Name))
    strength = json.loads((BASE / "ml" / "data" / "sector_strength.json").read_text(encoding="utf-8"))
    camp = pd.read_csv(REPO / "ml" / "data" / "rere_public_campaigns.csv", dtype=str).fillna("")
    public = dict(zip(camp.ticker.str.zfill(4), camp.first_public_date))

    cands, closes = [], {}
    for p in sorted(DAILY.glob("*.csv")):
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
        cl = pd.to_numeric(f["Close"], errors="coerce")
        vol = pd.to_numeric(f["Volume"], errors="coerce")
        v20, v60 = f["price_vs_ma20"], f["price_vs_ma60"]
        adj_px = gen._action_adjusted_close(f["Date"], cl, tk)          # 洗盤深度用價格連續序列(面額變更不算洗盤)
        wash = adj_px.rolling(10).max() / adj_px - 1
        vr = vol / vol.rolling(20).mean()
        liq = (vol.rolling(20).mean() >= LIQ_MIN) & vol.rolling(20).count().eq(20)
        fb = pd.to_numeric(f.get("Foreign_BuySell"), errors="coerce").fillna(0) / 1000
        tb = pd.to_numeric(f.get("Trust_BuySell"), errors="coerce").fillna(0) / 1000
        turn = (fb.shift(1).rolling(5).sum() < 0) & (fb > 0)
        ma20 = cl / (1 + v20)
        day_ret = cl / cl.shift(1) - 1
        near = v20.between(gen.RERE_MA20_LOW, gen.RERE_MA20_HIGH)
        quiet, up60 = vr < gen.RERE_VOL_MAX, v60 > 0
        deep = wash >= gen.RERE_WASH_MIN
        band = (wash >= gen.RERE_WASH_SHALLOW) & (wash < gen.RERE_WASH_MIN)
        masks = (("shallow_v2", band & near & quiet & up60), ("shakeout_v2", deep & near & quiet & up60),
                 ("shallow", band & near & quiet & up60 & turn),
                 ("ignition", deep & up60 & (vr >= gen.RERE_IGNITE_VOL) & (cl > ma20)
                  & ((v20.shift(1) < 0) | (day_ret > 0.02)) & turn & (tb > 0)),
                 ("shakeout", deep & near & quiet & up60 & turn))
        ptype = pd.Series("", index=f.index)
        for name, m in masks:                                             # 後寫者勝 = 產生器的優先序(現行三型先於 v2)
            ptype[(m & liq).fillna(False)] = name
        mult = backward_adjustment_multiplier(f["Date"], tk, actions).to_numpy(float) if tk in action_tickers else np.ones(len(f))
        av = cl.to_numpy(float) * mult
        if bool(liq.iloc[-1]):
            closes[tk] = pd.Series(av, index=f["Date"])                   # 同日基準的母體:最後一日仍達流動性門檻的普通股
        tier = (strength.get(sect.get(tk, "")) or {}).get("tier", "neutral")
        n = len(f)
        for i in f.index[(f["Date"] >= a.start) & (f["Date"] <= a.end) & ptype.ne("")]:
            if i + 1 >= n:
                continue
            e = i + 1
            seg = av[e + 1:]

            def run(level: float) -> float:
                hit = np.where(seg < level)[0]
                return ((seg[hit[0]] if len(hit) else av[-1]) / av[e] - 1) * 100

            cands.append({"as_of": f["Date"].iloc[i], "trade_date": f["Date"].iloc[e], "tk": tk, "name": names.get(tk, ""),
                          "ptype": ptype.iloc[i], "tier": tier, "wash": float(wash.iloc[i]) * 100,
                          "score": (2 if tier == "strong" else (0 if tier == "weak" else 1)) + float(wash.iloc[i]),
                          "entry": float(cl.iloc[e]), "last": float(cl.iloc[-1]), "last_date": f["Date"].iloc[-1],
                          "ret_new": run(cl.iloc[i] * (1 - gen.RERE_STOP_LOSS_PCT) * mult[i]),
                          "ret_old": run((cl.iloc[i] / (1 + v60.iloc[i])) * gen.RERE_PATTERN_LINE_MULT * mult[i]),
                          "ret_nostop": (av[-1] / av[e] - 1) * 100})
    C = pd.DataFrame(cands)
    print(f"=== REPLAY-rere-new-rules 訊號日 {a.start}~{a.end} ===")
    print("候選(未套名額):", len(C), "| 訊號日", C.as_of.nunique(), "| 每日平均", round(len(C) / C.as_of.nunique(), 1),
          "| 子型態", C.ptype.value_counts().to_dict())

    picked = []
    for _, g in C.groupby("as_of"):
        g = g.sort_values("score", ascending=False)
        picked.append(g[~g.ptype.isin(gen.RERE_V2_PTYPES)].head(gen.RERE_MAX).assign(group="現行三型"))
        v2 = gen._pick_rere_v2(g.to_dict("records"))
        if v2:
            picked.append(pd.DataFrame(v2).assign(group="v2"))
    P = pd.concat(picked, ignore_index=True)

    M = pd.DataFrame(closes).sort_index()
    M = M.loc[:, M.iloc[-1].notna()]
    fwd = (M.iloc[-1] / M - 1) * 100                                      # 各進場日 → 最後一日的全市場報酬
    bm_mean, bm_med = fwd.mean(axis=1), fwd.median(axis=1)
    P["bm"] = P.trade_date.map(bm_mean)

    print(f"\n--- 新規則重放(名額後;進場=交易日收盤;停損=訊號收盤−{gen.RERE_STOP_LOSS_PCT:.0%};標到 {P.last_date.max()})---")
    print(_stat("全部(現行三型+v2)", P.ret_new))
    for gname, g in P.groupby("group"):
        print(_stat("  " + gname, g.ret_new), f"| 同日市場 {g.bm.mean():+.2f}% → 超額 {(g.ret_new - g.bm).mean():+.2f}pp")
    for pt, g in P.groupby("ptype"):
        print(_stat("    " + pt, g.ret_new))
    print(_stat("同一批若用舊停損 MA60×0.97", P.ret_old))
    print(_stat("同一批不停損", P.ret_nostop))
    first = P.sort_values("as_of").drop_duplicates("tk")
    print(_stat("去重(每檔只算第一次上榜)", first.ret_new), "| 重複最多:", P.tk.value_counts().head(3).to_dict())
    print(f"同日進場的全市場等權平均 {P.bm.mean():+.2f}% (中位 {P.trade_date.map(bm_med).mean():+.2f}%) → 新規則超額 "
          f"{(P.ret_new - P.bm).mean():+.2f}pp;贏過同日市場平均的比例 {(P.ret_new > P.bm).mean() * 100:.1f}%")

    led_path = BASE / "ml" / "reports" / "rere_lane_ledger.csv"
    if led_path.exists():
        L = pd.read_csv(led_path, dtype={"ticker": str})
        L = L[(L.source_date.fillna(L.signal_date) >= a.start) & (L.source_date.fillna(L.signal_date) <= a.end)
              & L.entry_date.notna() & (L.duplicate_of.isna() | (L.duplicate_of == ""))].copy()
        L["ret_pct"] = pd.to_numeric(L.ret_pct, errors="coerce")
        L["bm"] = L.entry_date.astype(str).str[:10].map(bm_mean)
        print("\n--- 同期實際帳本(舊規則,實際上過名單的列)---")
        print(_stat("舊規則實際", L.ret_pct), f"| 同日市場 {L.bm.mean():+.2f}% → 超額 {(L.ret_pct - L.bm).mean():+.2f}pp")

    print("\n--- 她已公開標的在重放名單上的出現情形(公開日見 ml/data/rere_public_campaigns.csv)---")
    H = P[P.tk.isin(public)].sort_values(["tk", "as_of"])
    if len(H):
        H = H.assign(public_date=H.tk.map(public))
        print(H[["as_of", "trade_date", "tk", "name", "public_date", "ptype", "entry", "last", "ret_new"]].round(2).to_string(index=False))
    allH = C[C.tk.isin(public)]
    print("觸發但被名額擠掉:", sorted({(r.tk, r.as_of) for r in allH.itertuples()} - {(r.tk, r.as_of) for r in H.itertuples()}))
    print("期間內從未觸發:", sorted(set(public) - set(allH.tk)))

    ref = M.index[M.index >= a.start][1] if (M.index >= a.start).sum() > 1 else M.index[0]
    moves = {tk: float(fwd.loc[ref, tk]) for tk in public if tk in fwd.columns and np.isfinite(fwd.loc[ref, tk])}
    print(f"\n--- 她已公開標的自 {ref} 收盤至今的股價漲跌(不是她的損益)---")
    print("  " + " | ".join(f"{tk} {names.get(tk, '')} {v:+.1f}%" for tk, v in sorted(moves.items(), key=lambda kv: -kv[1])))
    if moves:
        print(f"  等權 {np.mean(list(moves.values())):+.1f}% (n={len(moves)}) vs 同期全市場等權 {bm_mean[ref]:+.2f}% (中位 {bm_med[ref]:+.2f}%)")

    out = REPO / "ml" / "reports" / f"replay_rere_new_rules_{a.start.replace('-', '')}_{a.end.replace('-', '')}.csv"
    P.round(4).to_csv(out, index=False, encoding="utf-8-sig")
    print("\n明細:", out)


if __name__ == "__main__":
    main()
