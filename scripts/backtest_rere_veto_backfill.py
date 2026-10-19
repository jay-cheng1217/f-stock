# -*- coding: utf-8 -*-
"""BT-rere-veto-backfill(回測票 #42):被官方警示否決的 rere 卡,名額改由同組下一順位補上,值不值得?

背景(PM 2026-10-19「否決卡改成補位,先回測驗證」):現行產生器先依中性雜湊順序分配名額(現行三型 6、v2 兩型 3+3、
5~8% 帶 3),之後才做 MOPS 官方警示否決(3 日內有持股轉讓/股份轉讓/私募/現金增資/辦理減資公告,排除庫藏股),
被否決的名額空著(10/19 1711 永光例:現行型只剩 5 張可用)。提案:否決先做,被否決者不佔名額,由同組下一順位補上。

重放方式:沿用 scripts/replay_rere_new_rules.py 的逐日型態判定(現行三型、v2 兩型),加上 10/16 起的 5~8% 帶;
排序用產生器的 _neutral_order_key(訊號日, 代號) 由大到小;v2 名額用產生器的 _pick_rere_v2。
報酬=帳本口徑:訊號次日收盤進場、持有 60 根、停損=訊號收盤 −40%(公司行動連續還原);
超額=減同期市場(每日等權,當日母體=前一日 20 日均量 >= 500 張的普通股,進場時點條件)。
否決資料:新聞資料/announcements_*.csv(2026-03-15 起才有,官方無歷史查詢),所以否決相關比較只能從 2026-03-18 起。

事前判準(2026-10-19 寫死,先於執行提交):
  A 頻率(描述):2026-03-18 起每個訊號日,現行規則下「已入選卡被否決」的張數與受影響日數。
  B 政策比較(訊號日 2026-03-18 起、能走滿 60 根者):現行(否決後空著)vs 補位,兩政策的每筆超額平均、左尾(<= −20%)、筆數。
     B1:補位每筆超額平均 >= 現行 − 0.5pp;B2:補位左尾比例 <= 現行 + 1pp。
  C 可交換性(大樣本,訊號日 2021-01 起、能走滿 60 根,不需公告資料):各組名額內 vs 名額外下一批(同一挑法再挑一次)
     每筆超額平均差。C1:|差| <= 1.0pp——中性雜湊排序下往下多抽一檔,不應系統性變差。
  D 否決本身(描述):被否決候選 vs 未被否決候選的 60 根超額;樣本小,只描述,不判。
  PASS = B1、B2、C1 全過 → 建議改為補位(實作仍需 PM 核可);任一不過 → 不改。
限制:處置股、籌碼否決、資料品質剔除、「主 lane 已列者排除」未回放;不含已下市股票;樣本日重疊;主 lane 不在本票範圍。

用法:python -X utf8 scripts/backtest_rere_veto_backfill.py   (worktree 無日K時設 STOCK_BASE_DIR)
輸出:ml/reports/bt_rere_veto_backfill.csv + stdout
"""
from __future__ import annotations

import io
import os
import sys
import warnings
from datetime import date
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
HOLD = 60
ALERT_KW = ["持股轉讓", "股份轉讓", "私募", "現金增資", "辦理減資"]      # 與產生器 MOPS overlay 相同
C_START, MOPS_START = "2021-01-01", "2026-03-18"
OUT = REPO / "ml" / "reports" / "bt_rere_veto_backfill.csv"


def load_alert_dates() -> dict[str, list[str]]:
    files = sorted((BASE / "新聞資料").glob("announcements_*.csv"))
    d = pd.concat([pd.read_csv(f, dtype={"Ticker": str}) for f in files], ignore_index=True)
    d["Ticker"] = d["Ticker"].astype(str).str.zfill(4)
    title = d["Title"].astype(str)
    a = d[title.apply(lambda x: any(k in x for k in ALERT_KW) and "庫藏股" not in x)]
    return {tk: sorted(g["Date"].astype(str).str[:10]) for tk, g in a.groupby("Ticker")}


def is_vetoed(tk: str, as_of: str, alerts: dict[str, list[str]]) -> bool:
    lo = (pd.Timestamp(as_of) - pd.Timedelta(days=3)).strftime("%Y-%m-%d")
    return any(lo <= d <= as_of for d in alerts.get(tk, []))


def build_candidates() -> tuple[pd.DataFrame, pd.Series]:
    actions = load_action_calendar(str(BASE / "ml" / "data" / "ex_dividend_calendar.csv"),
                                   str(BASE / "ml" / "data" / "corporate_action_price_factors.csv"))
    action_tickers = set(actions["stock_id"].astype(str))
    cands, closes, liquid = [], {}, {}
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
        adj_px = gen._action_adjusted_close(f["Date"], cl, tk)
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
        band5 = (wash >= gen.RERE_WASH_SHALLOW5) & (wash < gen.RERE_WASH_SHALLOW)
        masks = (("shallow5_v2", band5 & near & quiet & up60),
                 ("shallow_v2", band & near & quiet & up60), ("shakeout_v2", deep & near & quiet & up60),
                 ("shallow", band & near & quiet & up60 & turn),
                 ("ignition", deep & up60 & (vr >= gen.RERE_IGNITE_VOL) & (cl > ma20)
                  & ((v20.shift(1) < 0) | (day_ret > 0.02)) & turn & (tb > 0)),
                 ("shakeout", deep & near & quiet & up60 & turn))
        ptype = pd.Series("", index=f.index)
        for name, m in masks:                                             # 後寫者勝 = 產生器的優先序
            ptype[(m & liq).fillna(False)] = name
        mult = (backward_adjustment_multiplier(f["Date"], tk, actions).to_numpy(float) if tk in action_tickers
                else np.ones(len(f)))
        av = cl.to_numpy(float) * mult
        closes[tk] = pd.Series(av, index=f["Date"])
        liquid[tk] = pd.Series(liq.fillna(False).to_numpy(bool), index=f["Date"])
        n = len(f)
        for i in f.index[(f["Date"] >= C_START) & ptype.ne("")]:
            e = i + 1
            if e >= n:
                continue
            complete = e + HOLD < n
            ret, xdate = np.nan, None
            if complete:
                stop = cl.iloc[i] * (1 - gen.RERE_STOP_LOSS_PCT) * mult[i]
                seg = av[e + 1:e + HOLD + 1]
                hit = np.where(seg < stop)[0]
                x = e + 1 + hit[0] if len(hit) else e + HOLD
                ret, xdate = (av[x] / av[e] - 1) * 100, f["Date"].iloc[x]
            cands.append({"as_of": f["Date"].iloc[i], "tk": tk, "ptype": ptype.iloc[i], "complete": complete,
                          "entry_date": f["Date"].iloc[e], "exit_date": xdate, "ret": ret})
    C = pd.DataFrame(cands)
    M = pd.DataFrame(closes).sort_index()
    LQ = pd.DataFrame(liquid).reindex(index=M.index, columns=M.columns).fillna(False).astype(bool)
    mkt = M.pct_change(fill_method=None).where(LQ.shift(1).fillna(False).astype(bool)).mean(axis=1).fillna(0.0)
    mcum = (1 + mkt).cumprod()
    ok = C["complete"]
    C.loc[ok, "mkt"] = ((mcum.reindex(C.loc[ok, "exit_date"]).to_numpy() / mcum.reindex(C.loc[ok, "entry_date"]).to_numpy())
                        - 1) * 100
    C["ex"] = C["ret"] - C["mkt"]
    return C, mcum


def pick(day: pd.DataFrame) -> set[str]:
    """產生器的名額規則;day 必須已依中性鍵由大到小排序,且有 score 欄。"""
    cur = day[~day["ptype"].isin(gen.RERE_NO_FOREIGN_PTYPES)].head(gen.RERE_MAX)
    v2 = gen._pick_rere_v2(day.to_dict("records"))
    s5 = day[day["ptype"] == "shallow5_v2"].head(gen.RERE_SHALLOW5_MAX)
    return set(cur["tk"]) | {r["tk"] for r in v2} | set(s5["tk"])


def summarize(rows: pd.DataFrame) -> dict:
    r = rows.dropna(subset=["ex"])
    return {"n": len(r), "ex_mean": r["ex"].mean(), "ret_mean": r["ret"].mean(), "win": (r["ret"] > 0).mean() * 100,
            "tail20": (r["ret"] <= -20).mean() * 100}


def main() -> None:
    alerts = load_alert_dates()
    C, _ = build_candidates()
    C["score"] = [gen._neutral_order_key(date.fromisoformat(d), t) for d, t in zip(C["as_of"], C["tk"])]
    C["vetoed"] = [d >= MOPS_START and is_vetoed(t, d, alerts) for d, t in zip(C["as_of"], C["tk"])]
    print(f"=== BT-rere-veto-backfill 候選 {len(C)} 筆,訊號日 {C.as_of.min()}~{C.as_of.max()};"
          f"子型態 {C.ptype.value_counts().to_dict()} ===")

    lost_cards, lost_days, cur_trades, bf_trades, replaced, next_in, chosen_all = 0, 0, [], [], [], [], []
    for as_of, day in C.groupby("as_of"):
        day = day.sort_values("score", ascending=False)
        chosen = pick(day)
        if as_of >= MOPS_START:
            vet = set(day.loc[day["vetoed"], "tk"])
            lost = chosen & vet
            lost_cards += len(lost)
            lost_days += bool(lost)
            chosen_bf = pick(day[~day["tk"].isin(vet)])
            cur_trades.append(day[day["tk"].isin(chosen - vet)])
            bf_trades.append(day[day["tk"].isin(chosen_bf)])
            replaced.append(day[day["tk"].isin(chosen_bf - chosen)])
        nxt = pick(day[~day["tk"].isin(chosen)])
        chosen_all.append(day[day["tk"].isin(chosen)])
        next_in.append(day[day["tk"].isin(nxt)])

    mops_days = C.loc[C.as_of >= MOPS_START, "as_of"].nunique()
    print(f"\nA 頻率(2026-03-18 起 {mops_days} 個訊號日,含未滿 60 根):已入選卡被否決 {lost_cards} 張,受影響 {lost_days} 日")
    cur, bf, rep = (summarize(pd.concat(x)) for x in (cur_trades, bf_trades, replaced))
    print("B 政策比較(能走滿 60 根者):")
    for name, s in (("現行(否決後空著)", cur), ("補位", bf), ("其中補上的卡", rep)):
        print(f"  {name:12} n={s['n']:5} 每筆超額 {s['ex_mean']:+.2f}pp 平均報酬 {s['ret_mean']:+.2f}% 勝率 {s['win']:.1f}% 左尾 {s['tail20']:.1f}%")
    b1 = bf["ex_mean"] >= cur["ex_mean"] - 0.5
    b2 = bf["tail20"] <= cur["tail20"] + 1.0

    ch, nx = summarize(pd.concat(chosen_all)), summarize(pd.concat(next_in))
    diff = nx["ex_mean"] - ch["ex_mean"]
    a, b = pd.concat(chosen_all).dropna(subset=["ex"])["ex"], pd.concat(next_in).dropna(subset=["ex"])["ex"]
    se = np.sqrt(a.var() / len(a) + b.var() / len(b))
    c1 = abs(diff) <= 1.0
    print(f"C 可交換性(2021 起):名額內 n={ch['n']} 超額 {ch['ex_mean']:+.2f}pp | 下一批 n={nx['n']} 超額 {nx['ex_mean']:+.2f}pp | "
          f"差 {diff:+.2f}pp(標準誤約 {se:.2f})")

    mw = C[(C.as_of >= MOPS_START)]
    dv, dn = summarize(mw[mw["vetoed"]]), summarize(mw[~mw["vetoed"]])
    print(f"D 否決本身(描述):被否決候選 n={dv['n']} 超額 {dv['ex_mean']:+.2f}pp 左尾 {dv['tail20']:.1f}% | "
          f"未否決 n={dn['n']} 超額 {dn['ex_mean']:+.2f}pp 左尾 {dn['tail20']:.1f}%")

    verdict = "PASS" if (b1 and b2 and c1) else "FAIL"
    print(f"\n判準 B1 B2 C1:{'✓' if b1 else '✗'}{'✓' if b2 else '✗'}{'✓' if c1 else '✗'} → "
          + ("PASS(建議改為補位,實作需 PM 核可)" if verdict == "PASS" else "FAIL(不改)"))
    pd.DataFrame([{"part": "A", "lost_cards": lost_cards, "lost_days": lost_days, "mops_days": mops_days},
                  {"part": "B_current", **cur}, {"part": "B_backfill", **bf}, {"part": "B_replaced", **rep},
                  {"part": "C_chosen", **ch}, {"part": "C_next", **nx, "diff": diff, "se": se},
                  {"part": "D_vetoed", **dv}, {"part": "D_not_vetoed", **dn},
                  {"part": "verdict", "verdict": verdict}]).round(4).to_csv(OUT, index=False, encoding="utf-8-sig")
    print("彙總:", OUT)


if __name__ == "__main__":
    main()
