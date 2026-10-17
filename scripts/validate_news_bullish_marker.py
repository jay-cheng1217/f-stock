# -*- coding: utf-8 -*-
"""#35 卡片「📈 利多」單向標記驗收(2026-10-17 PM「做📈那個」)。

背景:#34 主設計(gemma4 標題單判)在部署母體 gold5 上準確率 78.3% 未過,但「判利多時」精確率 91.4%;
錯誤集中在漏判(利多→中性)。本票驗的是另一個用法:**只在模型判利多時顯示 📈,其餘一律不顯示**——
只用到「說利多時準不準」,所以不再要求總準確率。這是看過 gold5 才提出的設計,因此:
  - prompt / 模型 / 參數 = #34 主設計原樣(`validate_news_window_classifier.PROMPT_TITLE`),一字不改;
  - 在第六份全新樣本 gold6 上驗(母體同 gold5:事件股 × rere 事件日 [D-13,D] 視窗內新聞,
    2025/2026 各 60,seed 666666,排除 s1-s5;s5 以 seed 55555 逐筆重現 gold5_sample.csv 才採信);
  - 盲標於任何模型輸出之前凍結 sha256。

門檻(先設後跑,三項全過才 PASS):
  1. 利多精確率(模型判 B 的標題中,gold 也是 B 的比例)≥ 85%
  2. 利多召回率(gold B 中被標出的比例)≥ 50% —— 標記太少就沒有資訊量
  3. 兩輪一致(第二輪亂序)≥ 90%
PASS → 卡片新聞行加「📈 利多 k 則」(僅顯示,不進排序/過濾/否決);FAIL → 不上線,#35 關閉。
卡片顯示口徑:「沒有 📈」不代表沒有利多(召回率門檻僅 50%),介面文字須避免暗示相反。

用法:
  python -X utf8 scripts/validate_news_bullish_marker.py build
  python -X utf8 scripts/validate_news_bullish_marker.py eval    # 盲標凍結並登記 sha 後才可跑
"""
from __future__ import annotations

import hashlib
import random
import re
import sys
import time
from datetime import timedelta

import pandas as pd

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
import validate_news_window_classifier as v34  # noqa: E402

GOLD_DIR = v34.GOLD_DIR
BARS = {"b_prec": 0.85, "b_recall": 0.50, "agree": 0.90}
GOLD6_SHA = "fd64ff2c690ecec75f3b7d534ec27ca068fc90f1dc7918e3d660cd625a03f3b8"  # 盲標凍結 2026-10-17,先於任何模型輸出


def _window_items(tickers: set, excl: set) -> tuple[dict, dict]:
    ev = pd.read_csv(v34.EVENTS, dtype={"ticker": str})
    windows: dict[str, list] = {}
    for t, d in zip(ev["ticker"].str.zfill(4), pd.to_datetime(ev["date"])):
        windows.setdefault(t, []).append(d)
    items_y, dates = v34._pairs_all({2025, 2026}, tickers)

    def in_window(tup) -> bool:
        _, c, title, nid = tup
        d = dates[(c, title, nid)].replace(hour=0, minute=0, second=0, microsecond=0)
        return any(e - timedelta(days=13) <= d <= e for e in windows.get(c, []))

    elig = [x for x in items_y if (x[1], x[2]) not in excl and in_window(x)]
    return {y: [x for x in elig if x[0] == y] for y in (2025, 2026)}, dates


def build() -> None:
    ev = pd.read_csv(v34.EVENTS, dtype={"ticker": str})
    tickers = set(ev["ticker"].str.zfill(4))
    excl = v34._sample_prior(tickers)

    by_year, dates = _window_items(tickers, excl)
    random.seed(55555)
    s5 = random.sample(by_year[2025], 60) + random.sample(by_year[2026], 60)
    gold5 = pd.read_csv(GOLD_DIR / "gold5_sample.csv", dtype={"ticker": str})
    if [(t, s, n) for _, t, s, n in s5] != list(zip(gold5.ticker, gold5.title, gold5.nid)):
        raise SystemExit("seed-55555 無法重現 gold5_sample.csv — 排除集不可信,停止")
    print("reconstruction OK: seed-55555 draw reproduces gold5_sample.csv exactly")
    excl |= {(t, s) for _, t, s, _ in s5}

    by_year, dates = _window_items(tickers, excl)
    print("window-eligible after s1-s5:", {y: len(v) for y, v in by_year.items()})
    random.seed(666666)
    picked = random.sample(by_year[2025], 60) + random.sample(by_year[2026], 60)

    sec = pd.read_csv(v34.REPO / "ml" / "data" / "sector_mapping.csv", dtype=str)
    names = dict(zip(sec["Ticker"].str.zfill(4), sec["Name"]))
    rows = []
    for i, (y, t, title, nid) in enumerate(picked):
        rows.append({"i": i, "year": y, "date": dates[(t, title, nid)].strftime("%Y-%m-%d"),
                     "ticker": t, "name": names.get(t, ""), "title": title,
                     "nid": nid, "body": v34._fetch_body(nid)[:2500]})
        time.sleep(0.5)
        if (i + 1) % 20 == 0:
            print(f"bodies {i+1}/120", flush=True)
    df = pd.DataFrame(rows)
    df.to_csv(GOLD_DIR / "gold6_sample.csv", index=False, encoding="utf-8-sig")
    print(f"saved gold6_sample.csv, bodies {int((df['body'].str.len() > 50).sum())}/120")
    with open(GOLD_DIR / "gold6_listing.txt", "w", encoding="utf-8") as f:
        for r in rows:
            body = r["body"] or ""
            m = re.search(r"\(\s*" + r["ticker"] + r"-TW", body)
            seg = body[max(0, m.start() - 80):m.end() + 110] if m else "(內文未提及該股)"
            f.write(f"{r['i']:3d} {r['year']} {r['date']} {r['ticker']} {r['name']:　<6}|{r['title']}\n"
                    f"    首段:{body[:120]}\n    提及:{seg}\n")
    print("listing -> gold6_listing.txt")


def evaluate() -> None:
    path = GOLD_DIR / "gold6_labeled.csv"
    if GOLD6_SHA == "GOLD6_LABELED_SHA256_PLACEHOLDER":
        raise SystemExit("盲標 sha 尚未登記,不得跑模型")
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    assert sha == GOLD6_SHA, f"gold6 與凍結 sha 不符: {sha}"
    df = pd.read_csv(path, dtype={"ticker": str})
    gold = df["label"].tolist()

    def one_round(order: list, ck) -> list:
        done = {}
        if ck.exists():
            done = {int(r.i): str(r.pred) for r in pd.read_csv(ck).itertuples()}
        rows = []
        for n, idx in enumerate(order):
            pred = done.get(idx) or v34._predict_title(df.iloc[idx])
            rows.append({"i": idx, "pred": pred})
            pd.DataFrame(rows).to_csv(ck, index=False)
            print(f"[{n+1}/120] i={idx} {df.iloc[idx].ticker} -> {pred}", flush=True)
        return [r["pred"] for r in sorted(rows, key=lambda r: r["i"])]

    o2 = list(range(120))
    random.Random(666666).shuffle(o2)
    p1 = one_round(list(range(120)), GOLD_DIR / "gold6_title_r1.csv")
    p2 = one_round(o2, GOLD_DIR / "gold6_title_r2.csv")

    flagged = [g for g, p in zip(gold, p1) if p == "B"]
    b_prec = sum(g == "B" for g in flagged) / len(flagged) if flagged else float("nan")
    n_gold_b = sum(g == "B" for g in gold)
    b_recall = sum(1 for g, p in zip(gold, p1) if g == "B" and p == "B") / n_gold_b if n_gold_b else float("nan")
    agree = sum(a == b for a, b in zip(p1, p2)) / 120
    ok = b_prec >= BARS["b_prec"] and b_recall >= BARS["b_recall"] and agree >= BARS["agree"]
    print(f"\n==== #35 bullish marker ====\nB-precision {b_prec:.1%} (n={len(flagged)}, bar 85%) | "
          f"B-recall {b_recall:.1%} (gold B={n_gold_b}, bar 50%) | agree {agree:.1%} (bar 90%) -> "
          f"{'PASS' if ok else 'FAIL'}")
    fp = [(i, gold[i], df.iloc[i].ticker, str(df.iloc[i].title)[:44]) for i in range(120)
          if p1[i] == "B" and gold[i] != "B"]
    print(f"false 📈 ({len(fp)}):")
    for e in fp:
        print("  i=%3d gold=%s %s %s" % e)
    pd.DataFrame({"i": range(120), "ticker": df.ticker, "gold": gold, "p1": p1, "p2": p2}).to_csv(
        GOLD_DIR / "gold6_result.csv", index=False, encoding="utf-8-sig")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    {"build": build, "eval": evaluate}[sys.argv[1]]()
