# -*- coding: utf-8 -*-
"""BT-main-veto-backfill(回測票 #43):主 lane 被官方警示否決的卡,改由第 13 名起依序補位,值不值得?

背景(PM 2026-10-19「主 lane 也改成補位,先回測驗證」;rere 版 #42 已 PASS):主 lane 依型態乾淨度
(clean = −|距月線 − 4%| − 0.1×量比)排序後先截 12 檔,之後才做 MOPS 官方警示否決,被否決的名額空著。
與 rere 不同,主 lane 依分數排序而不是中性雜湊——補上來的是第 13 名起,可能系統性較差,這是本票要驗的重點。

重放(與產生器同式,門檻直接取 generate_entry_candidates 的常數):
  池 = −2%<=距月線<=+8%、站上季線、量比<=2.5(缺值放行)、RSI 45-80(缺值放行)、MACD 3 日變化/收盤 >= −1%、
       近 20 日量無缺且均量 >= 500 張;
  有當日 ml/models/predictions_<日>.csv(2026-03-11 起)的日子:母體=預測檔內股票,營益率 < 0 排除,
       營益率或技術指標缺值者為「資料待確認」(產生器 kind=watch)排在其他列之後;預測檔 2026-04-22 前沒有營益率欄,
       那段只限母體、不做營益率防呆;沒有預測檔的日子(C 段早期)只用型態池。
  排序 = (資料待確認, −clean);ChipK 已退役,chip_bonus=0。
報酬(主 lane 口徑):訊號次日收盤進;20 根內收盤跌破訊號日 MA20×0.96 即出,否則第 20 根收盤出。
  價格用股東報酬連續還原(load_action_calendar:除息+減資等公司行動,與 #42 同),等同除息加回且不受減資跳價影響;
  超額=減同期市場(每日等權,當日母體=前一日近 20 日均量 >= 500 張的普通股,進場時點條件)。
否決資料:新聞資料/announcements_*.csv(2026-03-15 起),關鍵字與產生器相同,窗口 [D−3 日, D]。

事前判準(2026-10-19 寫死,先於執行提交):
  B 政策比較(訊號日 2026-03-18 起、有預測檔、走滿 20 根):現行(先截 12 再否決,名額空著)vs 補位(否決先做再截 12)。
     B1:補位每筆超額平均 >= 現行 − 0.5pp;B2:補位左尾(報酬 <= −10%)比例 <= 現行 + 1pp。
  C 補位候選品質(大樣本,訊號日 2021-01 起走滿 20 根):第 13~15 名每筆超額平均 − 第 1~12 名每筆超額平均 >= −1.0pp。
  A 頻率、D 被否決候選本身只描述。
  PASS = B1、B2、C 全過 → 建議主 lane 也改為補位(實作需 PM 核可);任一不過 → 不改。
限制:處置股、資料品質以外的警示、dataA 預測檔母體差異未回放(用 Champion 預測檔母體);不含已下市股票;樣本日重疊;
     C 段 2026-03 前無營益率防呆與資料待確認排序。

用法:python -X utf8 scripts/backtest_main_veto_backfill.py   (worktree 無日K時設 STOCK_BASE_DIR)
輸出:ml/reports/bt_main_veto_backfill.csv + stdout
"""
from __future__ import annotations

import io
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

HOLD, TOPN, NEXT_N = 20, gen.MAIN_MAX, 3
LIQ_MIN = gen.MIN_LIQUIDITY_LOTS * 1000
ALERT_KW = ("持股轉讓", "股份轉讓", "私募", "現金增資", "辦理減資")          # 與產生器 MOPS overlay 相同
C_START, MOPS_START = "2021-01-01", "2026-03-18"
OUT = REPO / "ml" / "reports" / "bt_main_veto_backfill.csv"


def load_alert_dates() -> dict[str, list[str]]:
    files = sorted((BASE / "新聞資料").glob("announcements_*.csv"))
    d = pd.concat([pd.read_csv(f, dtype={"Ticker": str}) for f in files], ignore_index=True)
    d["Ticker"] = d["Ticker"].astype(str).str.zfill(4)
    a = d[d["Title"].astype(str).apply(lambda x: any(k in x for k in ALERT_KW) and "庫藏股" not in x)]
    return {tk: sorted(g["Date"].astype(str).str[:10]) for tk, g in a.groupby("Ticker")}


def is_vetoed(tk: str, as_of: str, alerts: dict[str, list[str]]) -> bool:
    lo = (pd.Timestamp(as_of) - pd.Timedelta(days=3)).strftime("%Y-%m-%d")
    return any(lo <= d <= as_of for d in alerts.get(tk, []))


def load_universe_om() -> dict[str, tuple[dict[str, float], bool]]:
    """{訊號日: ({代號: 營益率(可為 NaN)}, 該檔有無營益率欄)};代號集合即當日母體。"""
    out = {}
    for p in sorted((BASE / "ml" / "models").glob("predictions_2026-*.csv")):
        try:
            pr = pd.read_csv(p, dtype={"ticker": str})
        except Exception:
            continue
        has_om = "operating_margin_latest" in pr.columns
        om = (pd.to_numeric(pr["operating_margin_latest"], errors="coerce") if has_om
              else pd.Series(np.nan, index=pr.index))
        out[p.stem.replace("predictions_", "")] = (dict(zip(pr["ticker"].str.zfill(4), om)), has_om)
    return out


def build_pool() -> pd.DataFrame:
    actions = load_action_calendar(str(BASE / "ml" / "data" / "ex_dividend_calendar.csv"),
                                   str(BASE / "ml" / "data" / "corporate_action_price_factors.csv"))
    action_tickers = set(actions["stock_id"].astype(str))
    rows, closes, liquid = [], {}, {}
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
        dts = f["Date"].to_numpy()
        cl = pd.to_numeric(f["Close"], errors="coerce")
        vol = pd.to_numeric(f["Volume"], errors="coerce")
        v20, v60 = f["price_vs_ma20"], f["price_vs_ma60"]
        vr, rsi = f["volume_ratio_20d"], f["RSI_14"]
        mrel = f["macd_hist_delta_3d"] / cl.where(cl > 0)
        liq = (vol.rolling(20).mean() >= LIQ_MIN) & vol.rolling(20).count().eq(20)
        gate = (v20.between(gen.MA20_LOW, gen.MA20_HIGH) & (v60 > 0)
                & (vr.isna() | (vr <= gen.MAX_VOL_RATIO))
                & (rsi.isna() | rsi.between(gen.RSI_LOW, gen.RSI_HIGH))
                & (mrel >= gen.MACD_DELTA_FLOOR_REL) & liq).fillna(False)
        mult = (backward_adjustment_multiplier(f["Date"], tk, actions).to_numpy(float) if tk in action_tickers
                else np.ones(len(f)))
        av = cl.to_numpy(float) * mult
        closes[tk] = pd.Series(av, index=dts)
        liquid[tk] = pd.Series(liq.fillna(False).to_numpy(bool), index=dts)
        n = len(f)
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
                         "entry_date": dts[e], "exit_date": dts[x], "ret": (av[x] / av[e] - 1) * 100})
    D = pd.DataFrame(rows)
    M = pd.DataFrame(closes).sort_index()
    LQ = pd.DataFrame(liquid).reindex(index=M.index, columns=M.columns).fillna(False).astype(bool)
    mkt = M.pct_change(fill_method=None).where(LQ.shift(1).fillna(False).astype(bool)).mean(axis=1).fillna(0.0)
    mcum = (1 + mkt).cumprod()
    D["ex"] = D["ret"] - (mcum.reindex(D["exit_date"]).to_numpy() / mcum.reindex(D["entry_date"]).to_numpy() - 1) * 100
    return D


def summarize(rows: pd.DataFrame) -> dict:
    return {"n": len(rows), "ex_mean": rows["ex"].mean(), "ret_mean": rows["ret"].mean(),
            "win": (rows["ret"] > 0).mean() * 100, "tail10": (rows["ret"] <= -10).mean() * 100}


def main() -> None:
    alerts, univ = load_alert_dates(), load_universe_om()
    D = build_pool()
    D["has_pred"] = D["as_of"].isin(set(univ))
    D = D[[(not hp) or (t in univ[d][0]) for d, t, hp in zip(D["as_of"], D["tk"], D["has_pred"])]].copy()
    D["om"] = [univ[d][0].get(t, np.nan) if hp else np.nan for d, t, hp in zip(D["as_of"], D["tk"], D["has_pred"])]
    D["has_om"] = [hp and univ[d][1] for d, hp in zip(D["as_of"], D["has_pred"])]
    D = D[~(D["om"] < 0)].copy()                                            # 營益率防呆(OM<0 排除)
    # 資料待確認(產生器 kind=watch)排在後面:營益率缺值(該日預測檔有此欄時)或技術指標缺值
    D["watch"] = D["has_pred"] & ((D["has_om"] & D["om"].isna()) | D["tech_nan"])
    D["vetoed"] = [d >= MOPS_START and is_vetoed(t, d, alerts) for d, t in zip(D["as_of"], D["tk"])]
    D = D.sort_values(["as_of", "watch", "clean"], ascending=[True, True, False])
    D["rank"] = D.groupby("as_of").cumcount() + 1
    print(f"=== BT-main-veto-backfill 主 lane 池 {len(D)} 筆,訊號日 {D.as_of.min()}~{D.as_of.max()}"
          f"({D.as_of.nunique()} 日,每日平均 {len(D) / D.as_of.nunique():.0f} 檔;池 >= 13 檔的日子 "
          f"{(D.groupby('as_of').size() > TOPN).mean() * 100:.0f}%)===")

    W = D[(D.as_of >= MOPS_START) & D.has_pred]
    cur, bf, rep = [], [], []
    lost_cards = lost_days = 0
    for _, day in W.groupby("as_of"):
        chosen = day[day["rank"] <= TOPN]
        lost = int(chosen["vetoed"].sum())
        lost_cards += lost
        lost_days += bool(lost)
        b = day[~day["vetoed"]].head(TOPN)
        cur.append(chosen[~chosen["vetoed"]])
        bf.append(b)
        rep.append(b[b["rank"] > TOPN])
    days = W.as_of.nunique()
    print(f"\nA 頻率(2026-03-18 起、有預測檔、走滿 20 根的 {days} 個訊號日):前 {TOPN} 名被否決 {lost_cards} 張,受影響 {lost_days} 日;"
          f"資料待確認列佔前 {TOPN} 名 {W[W['rank'] <= TOPN]['watch'].mean() * 100:.1f}%")
    sc, sb, sr = (summarize(pd.concat(x)) for x in (cur, bf, rep))
    print("B 政策比較:")
    for name, s in (("現行(否決後空著)", sc), ("補位", sb), ("其中補上的卡", sr)):
        print(f"  {name:12} n={s['n']:5} 每筆超額 {s['ex_mean']:+.2f}pp 平均報酬 {s['ret_mean']:+.2f}% "
              f"勝率 {s['win']:.1f}% 左尾 {s['tail10']:.1f}%")
    b1 = sb["ex_mean"] >= sc["ex_mean"] - 0.5
    b2 = sb["tail10"] <= sc["tail10"] + 1.0

    top, nxt = D[D["rank"] <= TOPN], D[(D["rank"] > TOPN) & (D["rank"] <= TOPN + NEXT_N)]
    st, sn, sp = summarize(top), summarize(nxt), summarize(D)
    diff = sn["ex_mean"] - st["ex_mean"]
    se = np.sqrt(top["ex"].var() / len(top) + nxt["ex"].var() / len(nxt))
    c_ok = diff >= -1.0
    print(f"C 補位候選品質(2021 起):第 1~{TOPN} 名 n={st['n']} 超額 {st['ex_mean']:+.2f}pp | 第 {TOPN + 1}~{TOPN + NEXT_N} 名 "
          f"n={sn['n']} 超額 {sn['ex_mean']:+.2f}pp | 差 {diff:+.2f}pp(獨立樣本標準誤約 {se:.2f},樣本重疊下低估)"
          f" | 池等權 {sp['ex_mean']:+.2f}pp")
    yr = pd.DataFrame({"top": top.groupby(top.as_of.str[:4])["ex"].mean(),
                       "next": nxt.groupby(nxt.as_of.str[:4])["ex"].mean()})
    print("  逐年 第13~15名 − 第1~12名(描述):", " ".join(f"{y}:{r['next'] - r['top']:+.2f}" for y, r in yr.iterrows()))
    tw, nw = summarize(W[W["rank"] <= TOPN]), summarize(W[(W["rank"] > TOPN) & (W["rank"] <= TOPN + NEXT_N)])
    print(f"  有預測檔期間(描述):第 1~{TOPN} 名 {tw['ex_mean']:+.2f}pp(n={tw['n']})| 第 {TOPN + 1}~{TOPN + NEXT_N} 名 "
          f"{nw['ex_mean']:+.2f}pp(n={nw['n']})")
    dv, dn = summarize(W[W["vetoed"]]), summarize(W[~W["vetoed"]])
    print(f"D 否決本身(描述):被否決候選 n={dv['n']} 超額 {dv['ex_mean']:+.2f}pp 左尾 {dv['tail10']:.1f}% | "
          f"未否決 n={dn['n']} 超額 {dn['ex_mean']:+.2f}pp 左尾 {dn['tail10']:.1f}%")
    verdict = "PASS" if (b1 and b2 and c_ok) else "FAIL"
    print(f"\n判準 B1 B2 C:{'✓' if b1 else '✗'}{'✓' if b2 else '✗'}{'✓' if c_ok else '✗'} → "
          + ("PASS(建議主 lane 也改為補位,實作需 PM 核可)" if verdict == "PASS" else "FAIL(主 lane 不改)"))
    pd.DataFrame([{"part": "A", "lost_cards": lost_cards, "lost_days": lost_days, "days": days},
                  {"part": "B_current", **sc}, {"part": "B_backfill", **sb}, {"part": "B_replaced", **sr},
                  {"part": "C_top", **st}, {"part": "C_next", **sn, "diff": diff, "se": se}, {"part": "C_pool", **sp},
                  {"part": "D_vetoed", **dv}, {"part": "D_not_vetoed", **dn},
                  {"part": "verdict", "verdict": verdict}]).round(4).to_csv(OUT, index=False, encoding="utf-8-sig")
    print("彙總:", OUT)


if __name__ == "__main__":
    main()
