# -*- coding: utf-8 -*-
"""BT-rere-story-presence(回測票 #31):系統抓到 rere 候選時,前幾天網路上有沒有該股的「故事」?有故事的結果有比較好嗎?

背景(PM 2026-10-16「請嚴格調查」):瑞耘 6532 個案中,公開新聞(產能滿載、馬來西亞廠 Q4 驗證)與她的加碼時點吻合。
要回答兩件事:(Q1)系統訊號當下,有故事的比例是多少、和一般股票比高不高;(Q2)有故事的訊號結果是否較好,
還是故事與結果其實沒有一定關係。

資料(全部只用訊號日當天 23:59 以前已發布者,無前視):
  鉅亨網「台股新聞」分類標題與編輯標註代號(scripts/fetch_cnyes_news_history.py 回補,2020-12 起);
  公開資訊觀測站重大訊息(新聞資料/announcements_*.csv,2020 起)。
  標題分類沿用既有分類表 backend/features/narrative/taxonomy.yaml(2026-05-07 定版,非本票所訂);
  正向故事 = 命中 polarity=positive 的標籤(SUPPLY_SHORTAGE/PRICE_HIKE/POLICY_BENEFIT/AI_THEME/ORDER_VISIBILITY/CAPEX_EXPANSION);
  負向故事 = GOVERNANCE_RISK。標題含「漲停/跌停/股價/盤中/急漲/重挫」視為價格回聲,不計入正向故事(只計入「任何報導」)。
母體:scripts/rere_candidate_panel.build_panel() 的 rere 候選(與 #20/#23/#25/#26 同口徑),
  取「事件起點」= 該股前 20 根內未曾是候選的第一天(降低同一波段重複計數);訊號日 >= 2021-01-15(新聞回看窗口完整)。
  報酬 fwd60 = 次日收盤進、第 60 根收盤(公司行動還原、不停損、毛報酬);超額 = fwd60 − 同日全部候選平均(控制大盤)。
  另算 fwd20 為次要。
故事特徵(訊號日 T,窗口 = T−13 天 00:00 至 T 23:59,共 14 個日曆天≈10 個交易日):
  N0 無任何鉅亨報導;ANY 有任何報導;POS 有正向故事;NEG 有負向故事;IR 窗口內有重大訊息「法人說明會」。
Q1(描述,不判定):各組占比;基準 = 同一批訊號日,全部「20 日均量 >= 500 張」股票的同窗口報導比例。
Q2 事前判準(2026-10-16 寫死,先於任何結果提交;五項全過 = 「正向故事對 rere 訊號有增量資訊」):
  1. POS 平均超額 − N0 平均超額 >= +2.0pp(fwd60)
  2. 2021-2022 與 2023-2026 兩段皆 >= +1.0pp
  3. 逐年(2021-2026)差值為正的年份 >= 4/6
  4. 依訊號日 20 日均量三分位分組,三組差值皆 > 0(排除「有新聞=大型股」的混淆)
  5. POS 事件數 >= 300
  不過 → 結論為「故事與 rere 訊號結果沒有可靠關係」,故事面維持人工輔助,不進任何規則。
另列(不判定):ANY−N0、NEG−N0、IR−非IR、fwd20 版本、2026 實盤帳本(rere_lane_ledger)與她公開 11 檔在訊號日/公開日的故事有無。
限制:鉅亨只是一個來源(社群、PTT、券商報告、App 推播都看不到);標註靠鉅亨編輯,冷門股覆蓋天生較低(故有判準 4);
不含已下市股票;樣本日重疊、不做顯著性檢定;標題關鍵字分類粗糙,只能測「有沒有」,測不出故事的真偽或品質。

用法:先跑 fetch_cnyes_news_history.py 回補,再 python -X utf8 scripts/backtest_rere_story_presence.py
輸出:ml/reports/bt_rere_story_presence.csv + stdout
"""
from __future__ import annotations

import glob
import io
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.features.narrative.labeler import label_text, load_taxonomy  # noqa: E402
from scripts.rere_candidate_panel import BASE, REPO, build_panel  # noqa: E402

CACHE = BASE / "ml" / "reports" / "cache" / "cnyes_tw_stock"
WINDOW_DAYS = 14
ECHO = ("漲停", "跌停", "股價", "盤中", "急漲", "重挫")
START = "2021-01-15"


def load_news() -> pd.DataFrame:
    tax = load_taxonomy()
    rows = []
    for f in sorted(glob.glob(str(CACHE / "*.jsonl"))):
        for line in open(f, encoding="utf-8"):
            it = json.loads(line)
            if not it.get("codes") or not it.get("publishAt"):
                continue
            title = str(it.get("title") or "")
            labs = label_text(title, "", tax)
            echo = any(k in title for k in ECHO)
            pos = (not echo) and any(l.polarity == "positive" for l in labs)
            neg = any(l.label == "GOVERNANCE_RISK" for l in labs)
            day = datetime.fromtimestamp(int(it["publishAt"])).strftime("%Y-%m-%d")
            for code in it["codes"]:
                rows.append((code, day, 1, int(pos), int(neg)))
    news = pd.DataFrame(rows, columns=["ticker", "day", "any", "pos", "neg"])
    return news.groupby(["ticker", "day"], as_index=False).sum()


def load_ir() -> pd.DataFrame:
    frames = []
    for f in sorted((BASE / "新聞資料").glob("announcements_*.csv")):
        a = pd.read_csv(f, dtype={"Ticker": str}, usecols=["Ticker", "Date", "Title"])
        frames.append(a[a["Title"].astype(str).str.contains("法人說明會")])
    a = pd.concat(frames, ignore_index=True)
    a["ticker"] = a["Ticker"].str.zfill(4)
    a["day"] = a["Date"].astype(str).str[:10]
    a["ir"] = 1
    return a.groupby(["ticker", "day"], as_index=False)["ir"].sum()


def window_counts(events: pd.DataFrame, daily: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    """events[ticker,date] → 窗口 (T−13d, T] 內各欄加總。"""
    out = events[["ticker", "date"]].copy()
    for c in cols:
        out[c] = 0
    if daily.empty:
        return out
    by = {tk: g.set_index("day")[cols] for tk, g in daily.groupby("ticker")}
    vals = np.zeros((len(out), len(cols)), dtype=int)
    for i, (tk, d) in enumerate(zip(out["ticker"], out["date"])):
        g = by.get(tk)
        if g is None:
            continue
        lo = (datetime.fromisoformat(d) - timedelta(days=WINDOW_DAYS - 1)).strftime("%Y-%m-%d")
        sub = g[(g.index >= lo) & (g.index <= d)]
        if len(sub):
            vals[i] = sub.sum().to_numpy()
    out[cols] = vals
    return out


def main() -> int:
    done = CACHE / "_done_days.txt"
    if not done.exists():
        raise SystemExit("尚未回補新聞:先跑 scripts/fetch_cnyes_news_history.py")
    D, series = build_panel()
    D = D.sort_values(["ticker", "date"]).reset_index(drop=True)
    # 事件起點:該股前 20 根內未曾是候選
    starts = []
    for tk, g in D.groupby("ticker"):
        dates = series[tk]["date"].tolist()
        pos = {d: i for i, d in enumerate(dates)}
        last = -10**9
        for idx, d in zip(g.index, g["date"]):
            i = pos.get(d, -1)
            if i - last > 20:
                starts.append(idx)
            last = i
    E = D.loc[starts].copy()
    E = E[E["date"] >= START].copy()
    E["excess"] = E["fwd"] - E["pool_mean"]
    # fwd20 與訊號日 20 日均量
    f20, vma = [], []
    for tk, d in zip(E["ticker"], E["date"]):
        s = series[tk]
        i = s.index[s["date"] == d][0]
        a = s["adj_close"].to_numpy(float)
        f20.append((a[i + 21] / a[i + 1] - 1) * 100 if i + 21 < len(a) else np.nan)
        vma.append(np.nanmean(s["volume"].to_numpy(float)[max(0, i - 19): i + 1]))
    E["fwd20"], E["vma"] = f20, vma
    pool20 = E.groupby("date")["fwd20"].transform("mean")
    E["excess20"] = E["fwd20"] - pool20

    news = load_news()
    covered = set(done.read_text(encoding="utf-8").split())
    E = E[E["date"].isin(covered)].copy()
    W = window_counts(E, news, ["any", "pos", "neg"])
    I = window_counts(E, load_ir(), ["ir"])
    E = E.merge(W, on=["ticker", "date"]).merge(I, on=["ticker", "date"])
    E["year"] = E["date"].str[:4]
    E["half"] = np.where(E["year"] <= "2022", "2021-22", "2023-26")
    E["vol_t"] = pd.qcut(E["vma"].rank(method="first"), 3, labels=["低量", "中量", "高量"])

    print(f"=== BT-rere-story-presence 事件 {len(E)}({E.date.min()}~{E.date.max()});新聞涵蓋 {len(covered)} 天,"
          f"標註個股新聞 {int(news['any'].sum())} 則 ===")
    # Q1 prevalence + base rate
    rate = {k: (E[k] > 0).mean() * 100 for k in ("any", "pos", "neg", "ir")}
    sample_dates = sorted(E["date"].unique())[::5]
    univ = []
    for tk, s in series.items():
        v = s["volume"].rolling(20).mean()
        ok = s.loc[(v >= 500_000) & s["date"].isin(sample_dates), "date"]
        univ += [(tk, d) for d in ok]
    U = window_counts(pd.DataFrame(univ, columns=["ticker", "date"]), news, ["any", "pos"])
    print(f"\n[Q1] rere 事件:有任何報導 {rate['any']:.1f}%、正向故事 {rate['pos']:.1f}%、負向 {rate['neg']:.1f}%、法說 {rate['ir']:.1f}%")
    print(f"     基準(同期每 5 個訊號日抽樣,均量>=500 張的全部股票 {len(U)} 筆):有任何報導 {(U['any'] > 0).mean() * 100:.1f}%、"
          f"正向故事 {(U['pos'] > 0).mean() * 100:.1f}%")

    groups = {"N0 無報導": E["any"] == 0, "ANY 有報導": E["any"] > 0, "POS 正向故事": E["pos"] > 0,
              "NEG 負向": E["neg"] > 0, "IR 有法說": E["ir"] > 0}
    rows = []
    for name, m in groups.items():
        g = E[m]
        rows.append({"group": name, "n": len(g), "fwd60": g["fwd"].mean(), "excess60": g["excess"].mean(),
                     "win60": (g["fwd"] > 0).mean() * 100, "excess20": g["excess20"].mean()})
    T = pd.DataFrame(rows)
    print("\n組別 | 事件數 | 60根平均 | 對同日池超額 | 勝率 | 20根超額")
    for r in T.itertuples():
        print(f"{r.group:10} | {r.n:6} | {r.fwd60:+6.2f}% | {r.excess60:+6.2f}pp | {r.win60:4.1f}% | {r.excess20:+6.2f}pp")

    n0, pos = E[E["any"] == 0], E[E["pos"] > 0]
    diff = pos["excess"].mean() - n0["excess"].mean()
    halves = {h: E[(E.half == h) & (E.pos > 0)]["excess"].mean() - E[(E.half == h) & (E["any"] == 0)]["excess"].mean()
              for h in ("2021-22", "2023-26")}
    yearly = {y: E[(E.year == y) & (E.pos > 0)]["excess"].mean() - E[(E.year == y) & (E["any"] == 0)]["excess"].mean()
              for y in sorted(E["year"].unique())}
    terc = {t: E[(E.vol_t == t) & (E.pos > 0)]["excess"].mean() - E[(E.vol_t == t) & (E["any"] == 0)]["excess"].mean()
            for t in ("低量", "中量", "高量")}
    checks = [diff >= 2.0, all(v >= 1.0 for v in halves.values()), sum(v > 0 for v in yearly.values()) >= 4,
              all(v > 0 for v in terc.values()), len(pos) >= 300]
    print(f"\n[Q2] POS − N0 超額差 {diff:+.2f}pp;兩段 " + " / ".join(f"{k} {v:+.2f}" for k, v in halves.items()))
    print("     逐年 " + " ".join(f"{k}:{v:+.1f}" for k, v in yearly.items()))
    print("     量能三分位 " + " ".join(f"{k}:{v:+.2f}" for k, v in terc.items()))
    print(f"     ANY−N0 {E[E['any'] > 0]['excess'].mean() - n0['excess'].mean():+.2f}pp;"
          f"NEG−N0 {E[E['neg'] > 0]['excess'].mean() - n0['excess'].mean():+.2f}pp;"
          f"IR−非IR {E[E['ir'] > 0]['excess'].mean() - E[E['ir'] == 0]['excess'].mean():+.2f}pp")
    verdict = "PASS:正向故事對 rere 訊號有增量資訊" if all(checks) else "FAIL:故事與 rere 訊號結果沒有可靠關係"
    print("     判準 1-5:", "".join("✓" if c else "✗" for c in checks), "→", verdict)

    out = REPO / "ml" / "reports" / "bt_rere_story_presence.csv"
    T.assign(diff_pos_n0=diff, checks="".join("✓" if c else "✗" for c in checks), verdict=verdict).round(4).to_csv(
        out, index=False, encoding="utf-8-sig")
    E[["ticker", "date", "fwd", "excess", "fwd20", "any", "pos", "neg", "ir"]].to_csv(
        REPO / "ml" / "reports" / "cache" / "bt_rere_story_presence_events.csv", index=False, encoding="utf-8-sig")
    print("彙總:", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
