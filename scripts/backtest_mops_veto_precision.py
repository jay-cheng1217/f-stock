# -*- coding: utf-8 -*-
"""BT-mops-veto-precision(回測票 #46):官方警示否決改成「分辨主體」——放行不是本公司稀釋或內部人轉讓的公告,值不值得?

背景(PM 2026-10-21「好,請調整」):現行規則只比對標題有沒有「持股轉讓/股份轉讓/私募/現金增資/辦理減資」(排除庫藏股),
不分是誰的事。7/1~10/19 正式名單上 15 張官方警示否決卡有 5 張屬誤擋(子公司層級、本公司取得他公司私募)。
新規則 = 現行命中 且 標題(去空白)不符合下列任一放行條件(EXEMPT,實作時產生器用同一組):
  1 子公司層級:含「子公司」(含孫公司以「代孫公司」形式另見 2)
  2 代(子)公司公告:標題開頭、括號後或「金控/控股」後接「代」(排除「代收」「代理」)
  3 本公司買賣/投資/認購他公司私募或可轉債:(取得|處分|贖回|投資|認購) 後 40 字內有 私募/可轉換/可交換,中間不得出現「本公司」;「投資人」不算
  4 本公司參與他公司現金增資:(參與|投資|取得|認購) 後 40 字內有 現金增資,中間不得出現「本公司」,且之前要有公司名稱字眼
  5 買回股份:與現行排除的「庫藏股」同一件事
  6 法人說明會/法說:行事曆,不是警示(產生器另行標 📅)
  以 3/15~10/20 全部公告驗證:873 則不重複警示標題放行 398 則、仍擋 475 則,放行清單逐則人工檢查沒有本公司發行事件。

重放:
  主 lane:#43 同一母體(build_pool + 預測檔母體/營益率防呆),20 根口徑;訊號日 2026-03-18 起、有預測檔、走滿 20 根。
  rere:#42 同一候選(build_candidates),60 根口徑(停損 −40%);訊號日 2026-03-18 起、走滿 60 根。
  否決窗口同產生器:[訊號日 − 3 日, 訊號日]。
分組(每列=股票日):放行組 = 現行否決、新規則不否決;仍否決組 = 新規則仍否決;未否決組 = 現行就不否決。

事前判準(2026-10-21 寫死,先於執行提交):
  主 lane:放行組每筆超額平均 >= 未否決組 − 1.0pp;放行組左尾(報酬 <= −10%)比例 <= 未否決組 + 3pp。
  rere:  放行組每筆超額平均 >= 未否決組 − 2.0pp;放行組左尾(報酬 <= −20%)比例 <= 未否決組 + 3pp。
  某 lane 放行組 n < 30 → 該 lane 只描述不判;兩 lane 都 < 30 → 證據不足,不改。
  可判的 lane 全過 = PASS → 依 PM 核可改規則;任一可判 lane 不過 = FAIL → 不改。
  描述(不判):仍否決組 vs 未否決組;正式名單層面 7/1 起的官方警示否決卡有幾張會被放行。
限制:公告資料 2026-03-15 起才有;樣本日重疊(同一事件在 3 日窗口內會有多列);不含已下市股票;放行組樣本小。

用法:python -X utf8 scripts/backtest_mops_veto_precision.py
輸出:ml/reports/bt_mops_veto_precision.csv + stdout
"""
from __future__ import annotations

import glob
import json
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
# 兩支舊回測 import 時都會把 stdout 包成 UTF-8;保留第一個包裝的參照,避免被回收時關掉共用的 buffer
from scripts import backtest_main_veto_backfill as b43  # noqa: E402
_STDOUT_KEEP = [sys.stdout]
from scripts import backtest_rere_veto_backfill as b42  # noqa: E402
_STDOUT_KEEP.append(sys.stdout)
from scripts import backtest_main_lane_order as lo  # noqa: E402

MOPS_START = "2026-03-18"
ALERT_KW = ("持股轉讓", "股份轉讓", "私募", "現金增資", "辦理減資")     # 與產生器 MOPS_ALERT_KW 相同
_ENTITY = r"(公司|證券|期貨|銀行|人壽|控股|投控|Co\.|Ltd|Inc|L\.?P|Fund|基金)"
EXEMPT = (
    re.compile(r"子公司"),
    re.compile(r"(^|[)）]|金控|控股)代(?!收|理)"),
    re.compile(r"(取得|處分|贖回|投資(?!人)|認購)(?:(?!本公司).){0,40}(私募|可轉換|可交換)"),
    re.compile(r"(參與|投資(?!人)|取得|認購)(?=(?:(?!本公司).){1,40}現金增資)(?=.{0,40}" + _ENTITY + r".{0,25}現金增資)"),
    re.compile(r"買回股份"),
    re.compile(r"法人說明會|法說"),
)
OUT = b43.REPO / "ml" / "reports" / "bt_mops_veto_precision.csv"


def is_alert_old(title: str) -> bool:
    t = str(title)
    return any(k in t for k in ALERT_KW) and "庫藏股" not in t


def is_alert_new(title: str) -> bool:
    t = re.sub(r"\s+", "", str(title))
    return any(k in t for k in ALERT_KW) and "庫藏股" not in t and not any(p.search(t) for p in EXEMPT)


def alert_dates(pred) -> dict[str, list[str]]:
    files = sorted((b43.BASE / "新聞資料").glob("announcements_*.csv"))
    d = pd.concat([pd.read_csv(f, dtype={"Ticker": str}) for f in files], ignore_index=True)
    d["Ticker"] = d["Ticker"].astype(str).str.zfill(4)
    a = d[d["Title"].astype(str).map(pred)]
    return {tk: sorted(g["Date"].astype(str).str[:10]) for tk, g in a.groupby("Ticker")}


def groups(frame: pd.DataFrame, old: dict, new: dict) -> pd.DataFrame:
    frame = frame.copy()
    frame["v_old"] = [b43.is_vetoed(t, d, old) for t, d in zip(frame["tk"], frame["as_of"])]
    frame["v_new"] = [b43.is_vetoed(t, d, new) for t, d in zip(frame["tk"], frame["as_of"])]
    frame["grp"] = np.where(~frame["v_old"], "未否決", np.where(frame["v_new"], "仍否決", "放行"))
    return frame


def stat(rows: pd.DataFrame, tail: float) -> dict:
    r = rows.dropna(subset=["ex"])
    return {"n": len(r), "events": r[["tk"]].assign(m=r["as_of"].str[:7]).drop_duplicates().shape[0],
            "ex_mean": r["ex"].mean(), "ret_mean": r["ret"].mean(), "win": (r["ret"] > 0).mean() * 100,
            "tail": (r["ret"] <= tail).mean() * 100}


def lane_check(name: str, G: pd.DataFrame, tail: float, ex_margin: float) -> tuple[str, list[dict]]:
    s = {g: stat(G[G["grp"] == g], tail) for g in ("未否決", "放行", "仍否決")}
    for g, x in s.items():
        print(f"  {name} {g:4} n={x['n']:6}(約 {x['events']} 個股票月)每筆超額 {x['ex_mean']:+.2f}pp "
              f"平均報酬 {x['ret_mean']:+.2f}% 勝率 {x['win']:.1f}% 左尾(<= {tail:.0f}%) {x['tail']:.1f}%")
    rel, base = s["放行"], s["未否決"]
    if rel["n"] < 30:
        verdict = "INSUFFICIENT"
        print(f"  → {name}:放行組 n={rel['n']} < 30,只描述不判")
    else:
        ok1 = rel["ex_mean"] >= base["ex_mean"] - ex_margin
        ok2 = rel["tail"] <= base["tail"] + 3.0
        verdict = "PASS" if (ok1 and ok2) else "FAIL"
        print(f"  → {name}:超額 >= 未否決 − {ex_margin}pp {'✓' if ok1 else '✗'};左尾 <= 未否決 + 3pp {'✓' if ok2 else '✗'} → {verdict}")
    return verdict, [{"lane": name, "group": g, **x} for g, x in s.items()]


def list_level(old_pred, new_pred) -> None:
    """正式名單上的官方警示否決卡,新規則下有幾張會被放行(描述)。"""
    files = sorted((b43.BASE / "新聞資料").glob("announcements_*.csv"))
    m = pd.concat([pd.read_csv(f, dtype={"Ticker": str}) for f in files], ignore_index=True)
    m["Ticker"] = m["Ticker"].astype(str).str.zfill(4)
    m["D"] = m["Date"].astype(str).str[:10]
    from datetime import date
    from scripts.taiwan_trading_calendar import previous_taiwan_trading_day
    cards = lifted = 0
    names = []
    for p in sorted(glob.glob(str(b43.REPO / "logs" / "entry_list_2026*.json"))):
        if p.endswith("manifest.json"):
            continue
        d = json.load(open(p, encoding="utf-8"))
        if not isinstance(d.get("rows"), list):
            continue
        stamp = re.search(r"(\d{8})", Path(p).name).group(1)
        as_of = str(d.get("as_of_date") or "").replace("/", "-")
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", as_of):
            as_of = previous_taiwan_trading_day(date(int(stamp[:4]), int(stamp[4:6]), int(stamp[6:]))).isoformat()
        lo_d = (pd.Timestamp(as_of) - pd.Timedelta(days=3)).strftime("%Y-%m-%d")
        for r in d["rows"]:
            if r.get("kind") != "veto" or "官方警示" not in str(r.get("status", "")):
                continue
            tk = re.search(r"\d{4}", str(r["stock"])).group(0)
            titles = m[(m["Ticker"] == tk) & (m["D"] >= lo_d) & (m["D"] <= as_of)]["Title"].astype(str)
            if not any(old_pred(t) for t in titles):
                continue
            cards += 1
            if not any(new_pred(t) for t in titles):
                lifted += 1
                names.append(f"{stamp} {r['stock']}")
    print(f"\n名單層面(描述):正式名單上的官方警示否決卡 {cards} 張,新規則下會放行 {lifted} 張:{'、'.join(names)}")


def main() -> None:
    old, new = alert_dates(is_alert_old), alert_dates(is_alert_new)
    print(f"=== BT-mops-veto-precision:有警示的股票 現行 {len(old)} 檔、新規則 {len(new)} 檔 ===")

    D = lo.apply_universe(b43.build_pool())
    W = D[(D["as_of"] >= MOPS_START) & D["has_pred"]]
    GM = groups(W, old, new)
    print("\n主 lane(20 根,超額減進場時點流動母體):")
    vm, rows_m = lane_check("主lane", GM, -10, 1.0)

    C, _ = b42.build_candidates()
    C = C[(C["as_of"] >= MOPS_START) & C["complete"]]
    GR = groups(C, old, new)
    print("\nrere(60 根,停損 −40%):")
    vr, rows_r = lane_check("rere", GR, -20, 2.0)

    judged = [v for v in (vm, vr) if v != "INSUFFICIENT"]
    verdict = "INSUFFICIENT" if not judged else ("PASS" if all(v == "PASS" for v in judged) else "FAIL")
    list_level(is_alert_old, is_alert_new)
    print(f"\n判決:主 lane {vm}、rere {vr} → {verdict}"
          + {"PASS": "(依 PM 核可改規則)", "FAIL": "(不改)", "INSUFFICIENT": "(證據不足,不改)"}[verdict])
    pd.DataFrame(rows_m + rows_r + [{"lane": "verdict", "group": verdict}]).round(4).to_csv(OUT, index=False, encoding="utf-8-sig")
    print("彙總:", OUT)


if __name__ == "__main__":
    main()
