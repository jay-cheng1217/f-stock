# -*- coding: utf-8 -*-
"""BT-rere-revenue-turn(回測票 #36):月營收「翻正加速」的 rere 候選,60 根後是否明顯較好?

背景(PM 2026-10-17「開票驗月營收翻正加速」):瑞耘 6532 她自行以籌碼K篩出(9/15~19 週),當時公開資料=
月營收年增率 2 月 −35% → 8 月 +74% 逐月加速、散戶 4 週退場;媒體 10/01 才報導。散戶退場已驗(#20/#27,影子 #28),
營收翻轉加速未驗。**定義是在看過瑞耘的資料後擬的**——所以只用通用條件、不為讓瑞耘入選而調,並以全市場六年驗證。

母體:`rere_candidate_panel.build_panel()`(同 #20/#23/#25/#26/#31):還原收盤 10 日高回落 >= 8%、|距月線| <= 5%、
量比 < 1.2、收 > 季線、20 日均量 >= 500 張;報酬 fwd = 次日收盤進、第 60 根收盤、不停損、毛報酬;
ex = fwd − 當日全體候選平均(扣掉當時盤勢)。2020-01 起,每日候選 >= 8 檔才計。

營收資料:MOPS 全市場月報彙總 t21sc03(上市/上櫃 × 本國/外國),存研究快取 ml/reports/cache/revenue_t21sc03/,
**不寫入正式 月營收/ 目錄**(本機該目錄 1,247 檔只從 2025 起,不足以做六年回測)。
時點:第 m 月營收一律視為「m+1 月 11 日起」才可得(法定 10 日前公布,保守取 11 日),杜絕前視。

狀態(以訊號日當時最新可得月份 m 判定;y3(m) = m、m−1、m−2 三個月年增率平均):
  TURN   翻正加速:y3(m) > 0,且 y3(m−1..m−6) 中至少一個 <= 0,且 yoy(m) > yoy(m−1) > yoy(m−2),且 yoy(m) <= 500%
  TROUGH 谷底:y3(m) <= 0
  GROWTH 已在成長:y3(m) > 0 且非 TURN
  NA     營收資料不足

事前判準(2026-10-17 寫死,先於執行提交;資料檢查不過=INVALID,不判 PASS/FAIL):
  資料檢查 A:t21sc03 年增率與本機 月營收/ 重疊的 (代號,月) 中,差距 <= 0.05pp 的比例 >= 99%
  資料檢查 B:候選中 NA 比例 <= 15%
  1. TURN 平均 ex >= +2.0pp
  2. TURN 平均 ex − 其他有營收資料候選(TROUGH+GROWTH)平均 ex >= +2.0pp
  3. 2020~2026 中 TURN 樣本 >= 20 的年份,TURN 平均 ex > 0 的年數 >= 該年數 − 2(且至少 5 年)
  4. TURN 左尾(fwd <= −20%)比例 <= 其他 + 2pp
  5. TURN 樣本數 >= 300
PASS(五項全過)→ 只建議卡片加「營收翻正加速」標籤(僅顯示);改排序/過濾需 PM 另行核可。FAIL → 關票,不調定義重跑。
TROUGH / GROWTH 對比只做描述(回答 edge 在哪一段),不參與判決。
限制:不含已下市股票;樣本日重疊;t21sc03 為官方彙總最終值,事後更正者以更正後數字計(影響小)。

用法:python -X utf8 scripts/backtest_rere_revenue_turn.py
輸出:ml/reports/bt_rere_revenue_turn.csv + stdout
"""
from __future__ import annotations

import io
import sys
import time
from io import StringIO
from pathlib import Path

import numpy as np
import pandas as pd

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
from rere_candidate_panel import build_panel  # noqa: E402

CACHE = REPO / "ml" / "reports" / "cache" / "revenue_t21sc03"
LOCAL = REPO / "月營收"
OUT = REPO / "ml" / "reports" / "bt_rere_revenue_turn.csv"
FIRST, LAST = (2019, 1), (2026, 9)


def _months():
    y, m = FIRST
    while (y, m) <= LAST:
        yield y, m
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def fetch_all() -> None:
    import requests

    CACHE.mkdir(parents=True, exist_ok=True)
    H = {"User-Agent": "Mozilla/5.0 (research; monthly revenue summary)"}
    n_new = 0
    for y, m in _months():
        for mk in ("sii", "otc"):
            for k in (0, 1):
                fp = CACHE / f"{mk}_{y}{m:02d}_{k}.html"
                if fp.exists():
                    continue
                url = f"https://mopsov.twse.com.tw/nas/t21/{mk}/t21sc03_{y - 1911}_{m}_{k}.html"
                for attempt in range(3):
                    try:
                        r = requests.get(url, headers=H, timeout=30)
                        break
                    except Exception as exc:  # noqa: BLE001
                        print(f"  retry {attempt + 1} {url}: {exc}")
                        time.sleep(5)
                else:
                    continue
                if r.status_code == 200:
                    fp.write_bytes(r.content)
                    n_new += 1
                time.sleep(1.0)
        if m == 12:
            print(f"fetched through {y}-12 (new files so far {n_new})", flush=True)
    print(f"fetch done, new files {n_new}")


def parse_all() -> pd.DataFrame:
    rows = []
    for fp in sorted(CACHE.glob("*.html")):
        ym = fp.stem.split("_")[1]
        html = fp.read_bytes().decode("big5", errors="replace")
        try:
            tabs = pd.read_html(StringIO(html))
        except ValueError:
            continue
        for t in tabs:
            if t.shape[1] < 10:
                continue
            t = t.copy()
            t.columns = [" ".join(map(str, c)) if isinstance(c, tuple) else str(c) for c in t.columns]
            code = next((c for c in t.columns if "代號" in c), None)
            yoy = next((c for c in t.columns if "去年同月" in c and "增減" in c), None)
            if not code or not yoy:
                continue
            sub = pd.DataFrame({"ticker": t[code].astype(str).str.strip(),
                                "yoy": pd.to_numeric(t[yoy], errors="coerce")})
            sub = sub[sub["ticker"].str.fullmatch(r"\d{4}")]
            sub["ym"] = f"{ym[:4]}-{ym[4:]}"
            rows.append(sub)
    R = pd.concat(rows, ignore_index=True).dropna(subset=["yoy"]).drop_duplicates(["ticker", "ym"])
    return R


def check_against_local(R: pd.DataFrame) -> float:
    loc = []
    for f in LOCAL.glob("revenue_*.csv"):
        d = pd.read_csv(f, usecols=["Date", "YoY_pct_change"])
        d["ticker"] = f.stem.split("_")[1]
        loc.append(d.rename(columns={"Date": "ym", "YoY_pct_change": "yoy_local"}))
    L = pd.concat(loc, ignore_index=True)
    L["ym"] = L["ym"].astype(str).str[:7]
    J = R.merge(L, on=["ticker", "ym"]).dropna(subset=["yoy", "yoy_local"])
    ok = ((J["yoy"] - J["yoy_local"]).abs() <= 0.05).mean() * 100
    print(f"資料檢查 A:重疊 {len(J)} 筆,年增率差距 <= 0.05pp 比例 {ok:.2f}%(門檻 99%)")
    return ok


def classify(R: pd.DataFrame, D: pd.DataFrame) -> pd.Series:
    """回傳每列候選的狀態;只用訊號日當時已公布(m+1 月 11 日起)的月份。"""
    R = R.copy()
    R["p"] = pd.PeriodIndex(R["ym"], freq="M")
    states = pd.Series("NA", index=D.index, dtype=object)
    for tk, g in D.groupby("ticker"):
        s = R[R["ticker"] == tk].set_index("p")["yoy"].sort_index()
        if s.empty:
            continue
        full = s.reindex(pd.period_range(s.index.min(), s.index.max(), freq="M"))
        y3 = full.rolling(3).mean()
        for idx, d in zip(g.index, g["date"]):
            dt = pd.Timestamp(d)
            m = (dt.to_period("M") - 1) if dt.day >= 11 else (dt.to_period("M") - 2)
            if m not in full.index:
                continue
            need = [m - i for i in range(9)]
            if any(p not in full.index or pd.isna(full[p]) for p in need):
                continue
            cur, hist = y3[m], [y3[m - i] for i in range(1, 7)]
            a, b, c = full[m], full[m - 1], full[m - 2]
            if cur <= 0:
                states[idx] = "TROUGH"
            elif min(hist) <= 0 and a > b > c and a <= 500:
                states[idx] = "TURN"
            else:
                states[idx] = "GROWTH"
    return states


def main() -> None:
    fetch_all()
    R = parse_all()
    print(f"t21sc03 解析:{R['ticker'].nunique()} 檔,{R['ym'].min()}~{R['ym'].max()},{len(R)} 筆")
    check_a = check_against_local(R)

    D, _ = build_panel()
    D["ex"] = D["fwd"] - D["pool_mean"]
    D["state"] = classify(R, D)
    na_share = (D["state"] == "NA").mean() * 100
    print(f"=== BT-rere-revenue-turn 候選 {len(D)} 筆,{D.date.min()}~{D.date.max()};資料檢查 B:NA {na_share:.1f}%(門檻 <= 15%) ===")

    stats = {}
    for st, g in D.groupby("state"):
        stats[st] = {"state": st, "n": len(g), "mean_ex": g["ex"].mean(), "mean_fwd": g["fwd"].mean(),
                     "median_fwd": g["fwd"].median(), "win": (g["fwd"] > 0).mean() * 100,
                     "tail20": (g["fwd"] <= -20).mean() * 100,
                     "yearly_ex": g.groupby("year")["ex"].mean(), "yearly_n": g.groupby("year").size()}
    for s in stats.values():
        yr = " ".join(f"{y}:{v:+.1f}(n{s['yearly_n'][y]})" for y, v in s["yearly_ex"].items())
        print(f"{s['state']:7} n={s['n']:6} 超額 {s['mean_ex']:+.2f}pp 平均 {s['mean_fwd']:+.2f}% 中位 {s['median_fwd']:+.2f}% "
              f"勝率 {s['win']:.1f}% 左尾 {s['tail20']:.1f}%  逐年超額 {yr}")

    if check_a < 99 or na_share > 15:
        print("\n判決:INVALID(資料檢查未過,不判 PASS/FAIL)")
        verdict = "INVALID"
    else:
        T = D[D["state"] == "TURN"]
        O = D[D["state"].isin(["TROUGH", "GROWTH"])]
        yr = T.groupby("year")["ex"].agg(["mean", "size"])
        yr = yr[yr["size"] >= 20]
        pos_years, k = int((yr["mean"] > 0).sum()), len(yr)
        checks = [T["ex"].mean() >= 2.0,
                  T["ex"].mean() - O["ex"].mean() >= 2.0,
                  pos_years >= max(k - 2, 5),
                  (T["fwd"] <= -20).mean() * 100 <= (O["fwd"] <= -20).mean() * 100 + 2.0,
                  len(T) >= 300]
        print(f"\nTURN − 其他 = {T['ex'].mean() - O['ex'].mean():+.2f}pp;TURN 超額為正的年份 {pos_years}/{k}(n>=20 的年)")
        verdict = "PASS" if all(checks) else "FAIL"
        print("判準 1-5:", "".join("✓" if c else "✗" for c in checks), "→",
              "PASS(建議卡片加「營收翻正加速」標籤,僅顯示,待 PM 核可)" if verdict == "PASS" else "FAIL(關票,不調定義重跑)")

    rz = D[D["ticker"] == "6532"][["date", "state", "fwd", "ex"]]
    print("\n瑞耘 6532 在母體中的列(只描述,不影響判決):")
    print(rz.tail(12).to_string(index=False) if len(rz) else "  (2020 起無符合 rere 型態的候選日)")

    pd.DataFrame([{k: v for k, v in s.items() if not k.startswith("yearly")}
                   | {"yearly_ex": " ".join(f"{y}:{v:+.2f}" for y, v in s["yearly_ex"].items()),
                      "verdict": verdict, "check_a_pct": round(check_a, 2), "na_pct": round(na_share, 2)}
                   for s in stats.values()]).round(4).to_csv(OUT, index=False, encoding="utf-8-sig")
    print("彙總:", OUT)


if __name__ == "__main__":
    main()
