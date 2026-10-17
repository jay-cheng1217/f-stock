# -*- coding: utf-8 -*-
"""#40 BT-factset-revision-event:鉅亨「Factset 最新調查」速報在台股有沒有資訊?調幅門檻該定在哪?

背景(PM 2026-10-17「開 #40」):卡片 📈 的 prompt 把 FactSet 共識修正判成中性,但盲標慣例算利多(#34/#38 的主要分歧)。
文獻:目標價修正資訊量大於同幅 EPS 修正、幅度越大資訊越多(Asquith-Mikhail-Au 2005;Brav-Lehavy 2003);跟隨共識的
修正資訊少(Gleason-Lee 2003);台股 MSCI 成分股共識修正只在公布當天/隔天有資訊、事前已漲(NYCU)。門檻無公認標準,用本地資料定。

事件:ml/reports/cache/cnyes_tw_stock/*.jsonl 中標題含「Factset 最新調查」者(2024 起),依標題分四類:
  TP_up「目標價調升至 X 元,幅度約 Y%」、TP_down「目標價調降…」、EPS_up「EPS預估上修至…」、EPS_down「EPS預估下修…」。
  代號取標題「(XXXX-TW)」;TP 的幅度 Y 取自標題。EPS 標題無幅度,只按方向分析。標題無分析師人數,**不設覆蓋人數篩選**。
  同一代號同一天同類型只算一次。
事件日 d0:發布於交易日 13:30 前 → 當天;否則 → 下一個交易日(盤中發布的 d0 會含發布前走勢,標註於報告)。
報酬(公司行動連續還原收盤):
  react  = d0 收盤 / d0−1 收盤 − 1(公布反應)
  drift20 = d0+20 收盤 / d0 收盤 − 1(公布後可交易的漂移)
  pre20  = d0−1 收盤 / d0−21 收盤 − 1(事前是否已漲,只描述)
  各減同窗口市場報酬:每日等權平均,當日母體 = 前一日 20 日均量 >= 500 張的股票(進場時點條件,不用期末條件)。
統計:先把事件超額報酬按「d0 所在月份」取平均,再對月均值做 t 檢定(降低同月事件相關);報告亦列事件數。

事前判準(2026-10-17 寫死,先於任何數據提交):
  「有資訊」= 該組 react 平均 >= +0.5pp,且月聚類 t >= 2.0,且 2024 與 2025-26 兩段 react 平均皆 > 0。
  TP_up:(a) 整組有資訊且「幅度 <= 3%」組本身也有資訊 → 判「全部算利多」(選項 2);
    (b) 否則在 b ∈ {3, 5, 10}(%)中取最小的 b,使「幅度 > b」有資訊且「幅度 <= b」沒有資訊 → 判「調升 > b% 才算利多」(選項 3);
    (c) 都不成立 → 判「全部算中性」(選項 1)。各組樣本 < 30 視為沒有資訊。
  EPS_up(無幅度):整組有資訊 → EPS 上修算利多;否則算中性。
  TP_down / EPS_down / drift20 / pre20 只描述,不參與判決。
用途:判決只決定 📈 對 FactSet 標題的處理(以規則解析標題,不交給模型);不進排序/過濾,上線前另需 PM 核可。
限制:只有 2024-2026(約 2.8 年);無分析師人數;事件日重疊;樣本內無法再切出獨立驗證期。

用法:python -X utf8 scripts/backtest_factset_revision_event.py
輸出:ml/reports/bt_factset_revision_event.csv + stdout
"""
from __future__ import annotations

import io
import json
import re
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
from rere_candidate_panel import build_panel  # noqa: E402

CACHE = REPO / "ml" / "reports" / "cache" / "cnyes_tw_stock"
OUT = REPO / "ml" / "reports" / "bt_factset_revision_event.csv"
CODE = re.compile(r"\((\d{4})-TW\)")
MAG = re.compile(r"幅度約\s*([\d.]+)\s*%")
BUCKETS = [(0, 3), (3, 5), (5, 10), (10, np.inf)]
INFO_MEAN, INFO_T = 0.5, 2.0


def load_events() -> pd.DataFrame:
    rows = []
    for f in sorted(CACHE.glob("*.jsonl")):
        for line in open(f, encoding="utf-8"):
            try:
                it = json.loads(line)
            except json.JSONDecodeError:
                continue
            t = it.get("title", "")
            if "actset 最新調查" not in t:
                continue
            kind = ("TP_up" if "目標價調升" in t else "TP_down" if "目標價調降" in t else
                    "EPS_up" if "EPS預估上修" in t else "EPS_down" if "EPS預估下修" in t else None)
            m = CODE.search(t)
            if not kind or not m:
                continue
            mag = MAG.search(t)
            rows.append({"ticker": m.group(1), "kind": kind, "published": datetime.fromtimestamp(it["publishAt"]),
                         "mag": float(mag.group(1)) if mag else np.nan, "title": t})
    E = pd.DataFrame(rows)
    E["pub_date"] = E["published"].dt.strftime("%Y-%m-%d")
    return E.drop_duplicates(["ticker", "pub_date", "kind"]).reset_index(drop=True)


def market_returns(series: dict) -> tuple[pd.DatetimeIndex, pd.Series, pd.DataFrame]:
    closes = pd.DataFrame({tk: s.set_index("date")["adj_close"] for tk, s in series.items()})
    vols = pd.DataFrame({tk: s.set_index("date")["volume"] for tk, s in series.items()})
    closes.index = vols.index = pd.to_datetime(closes.index)
    closes, vols = closes.sort_index(), vols.sort_index()
    ret = closes.pct_change()
    liquid = (vols.rolling(20).mean() >= 500_000).shift(1)
    mkt = ret.where(liquid.fillna(False)).mean(axis=1)
    return closes.index, mkt.fillna(0.0), closes


def main() -> None:
    E = load_events()
    print(f"事件 {len(E)}:", E["kind"].value_counts().to_dict(), f"{E.pub_date.min()}~{E.pub_date.max()}")
    _, series = build_panel()
    days, mkt, closes = market_returns(series)
    mcum = (1 + mkt).cumprod()

    out = []
    for r in E.itertuples():
        if r.ticker not in closes.columns:
            continue
        pub = pd.Timestamp(r.published)
        same = pd.Timestamp(pub.date())
        cutoff = pub.hour * 60 + pub.minute < 13 * 60 + 30
        pos = days.searchsorted(same)
        if pos >= len(days):
            continue
        if not (days[pos] == same and cutoff):
            pos = days.searchsorted(same, side="right")
        if pos - 21 < 0 or pos + 20 >= len(days):
            continue
        c = closes[r.ticker]
        p = lambda i: c.iloc[i]  # noqa: E731
        if any(pd.isna(p(i)) for i in (pos - 21, pos - 1, pos, pos + 20)):
            continue
        mk = lambda a, b: mcum.iloc[b] / mcum.iloc[a] - 1  # noqa: E731
        out.append({"ticker": r.ticker, "kind": r.kind, "mag": r.mag, "d0": days[pos],
                    "intraday_pub": bool(days[pos] == same and pub.hour >= 9),
                    "react": (p(pos) / p(pos - 1) - 1 - mk(pos - 1, pos)) * 100,
                    "drift20": (p(pos + 20) / p(pos) - 1 - mk(pos, pos + 20)) * 100,
                    "pre20": (p(pos - 1) / p(pos - 21) - 1 - mk(pos - 21, pos - 1)) * 100})
    X = pd.DataFrame(out)
    X["month"] = X["d0"].dt.strftime("%Y-%m")
    X["half"] = np.where(X["d0"].dt.year <= 2024, "2024", "2025-26")
    print(f"可計算事件 {len(X)}(盤中發布 {int(X.intraday_pub.sum())})")

    def stat(g: pd.DataFrame, col: str = "react") -> dict:
        mm = g.groupby("month")[col].mean()
        t = mm.mean() / (mm.std(ddof=1) / np.sqrt(len(mm))) if len(mm) > 2 and mm.std(ddof=1) > 0 else np.nan
        halves = g.groupby("half")[col].mean()
        return {"n": len(g), "months": len(mm), "mean": g[col].mean(), "t_month": t,
                "h2024": halves.get("2024", np.nan), "h2025_26": halves.get("2025-26", np.nan)}

    def has_info(g: pd.DataFrame) -> bool:
        s = stat(g)
        return bool(len(g) >= 30 and s["mean"] >= INFO_MEAN and s["t_month"] >= INFO_T and s["h2024"] > 0 and s["h2025_26"] > 0)

    rows = []
    for kind in ("TP_up", "TP_down", "EPS_up", "EPS_down"):
        G = X[X.kind == kind]
        groups = [("all", G)]
        if kind.startswith("TP"):
            groups += [(f"{lo:g}-{hi:g}%", G[(G.mag > lo) & (G.mag <= hi)]) for lo, hi in BUCKETS]
        for name, g in groups:
            if not len(g):
                continue
            rec = {"kind": kind, "group": name}
            for col in ("react", "drift20", "pre20"):
                s = stat(g, col)
                rec.update({f"{col}_{k}": v for k, v in s.items() if k not in ("n", "months")} | {"n": s["n"], "months": s["months"]})
            rows.append(rec)
            print(f"{kind:8} {name:8} n={rec['n']:5} | 反應 {rec['react_mean']:+.2f}pp t={rec['react_t_month']:.2f} "
                  f"(2024 {rec['react_h2024']:+.2f} / 25-26 {rec['react_h2025_26']:+.2f}) | 20日漂移 {rec['drift20_mean']:+.2f}pp "
                  f"t={rec['drift20_t_month']:.2f} | 事前20日 {rec['pre20_mean']:+.2f}pp")

    up = X[X.kind == "TP_up"]
    if has_info(up) and has_info(up[up.mag <= 3]):
        tp_verdict = "全部算利多(選項 2):整體與 <=3% 組都有資訊"
    else:
        chosen = next((b for b in (3, 5, 10) if has_info(up[up.mag > b]) and not has_info(up[up.mag <= b])), None)
        tp_verdict = (f"目標價調升 > {chosen}% 才算利多(選項 3)" if chosen is not None
                      else "全部算中性(選項 1):找不到「門檻以上有資訊、以下沒有」的切點")
    eps_verdict = "EPS 上修算利多" if has_info(X[X.kind == "EPS_up"]) else "EPS 上修算中性"
    print(f"\n判決:{tp_verdict};{eps_verdict}")
    pd.DataFrame(rows).assign(tp_verdict=tp_verdict, eps_verdict=eps_verdict).round(4).to_csv(OUT, index=False, encoding="utf-8-sig")
    print("彙總:", OUT)


if __name__ == "__main__":
    main()
