# -*- coding: utf-8 -*-
"""#39 卡片「📈 利多」標記重驗:gemma4(線上引擎)vs Strata(2026-10-17 PM 指示,事前登記)。

背景:#35 在 gold6 上利多精確率 93.5% 通過後上線(9833ae8f);#38 的全新同母體樣本 gold7 上,同一個 gemma4 設計
只有 78.6%(描述性,非登記重驗),低於 #35 門檻 85%。Strata(思考開啟)在 gold7 上 91.1%(同為描述性)。
本票在第八份全新樣本上正式重驗兩者;重驗期間 📈 照常顯示(PM)。

樣本 gold8(先於任何模型輸出完成):母體同 gold5-7(事件股 × rere 事件日 [D-13,D] 視窗內 cnyes 新聞,編輯標註代號 1-4 檔),
  2025、2026 各抽 60 則,seed 88888888;排除 s1-s7:s1-s4 以 v34 原程式重建須重現 gold4_sample.csv,
  s5/s6/s7 須以 seed 55555/666666/7777777 逐筆重現磁碟上的 gold5/6/7_sample.csv,任一失敗即停止。
盲標:標題+內文,標註者=Claude,口徑與 gold5-7 相同(FactSet 目標價/EPS 上修→B、下修→R,營收速報→B,盤中速報→N,
  題材點名/專欄/公關/籌碼/非主角→N);凍結 sha256 後才可跑模型。揭露:標註者已看過 gold7 的模型錯誤型態。
受測(提示詞 = v34.PROMPT_TITLE 一字不改,與線上 rere_news_volume 相同;temperature 0;兩輪,第二輪 seed 88888888 亂序):
  gemma4:26b-a4b 標題單判 = 線上 📈 原樣(think=False,num_ctx 8192)。
  Strata 標題單判 = #38 主設計原樣(思考開啟,max_tokens 4096)。
  連線失敗(重試 3 次仍失敗)→ 該題不記錄,事後重跑,不算模型答案;模型回覆空白或無字母 → N,照計並另報。
門檻(沿用 #35,兩個模型各自判定,三項全過才 PASS):利多精確率 ≥85%、利多召回率 ≥50%、兩輪一致 ≥90%。
決策(先寫死,只提建議,上線變動皆需 PM 核可):
  gemma4 PASS → 📈 維持 gemma4,不動(Strata 結果僅供參考,不因 Strata 較高而換)。
  gemma4 FAIL 且 Strata PASS → 建議 📈 改用 Strata(需運維方案:Strata 佔約 39GB 記憶體,不能與 Ollama 同時載入)。
  兩者皆 FAIL → 建議隱藏 📈。
  只用 gold8 判定,不與 gold6/gold7 合併;不改提示詞(FactSet 定義衝突只影響召回,不在本票處理)。
n=120 時,利多精確率的標準誤約 5pp(依標利多數而定),結果落在門檻 ±5pp 內時照判定,但報告須註明。

用法:
  python -X utf8 scripts/validate_news_marker_recheck.py build
  python -X utf8 scripts/validate_news_marker_recheck.py eval-gemma    # Ollama 可用、Strata 關閉
  python -X utf8 scripts/validate_news_marker_recheck.py eval-strata   # Strata 執行中、Ollama 未載入模型
  python -X utf8 scripts/validate_news_marker_recheck.py score
"""
from __future__ import annotations

import hashlib
import json
import random
import re
import sys
import time
import urllib.request
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import validate_news_bullish_marker as v35  # noqa: E402
import validate_news_strata as v38  # noqa: E402
import validate_news_window_classifier as v34  # noqa: E402

GOLD_DIR = v34.GOLD_DIR
SEED = 88888888
BARS = v35.BARS  # b_prec 0.85, b_recall 0.50, agree 0.90
GOLD8_SHA = "27c031af179ae44a145df90b2e867b312d13904562a2e4718cfb25481cbb5fd0"  # 盲標凍結 2026-10-17,先於任何模型輸出(B62/N50/R8)
DESIGNS = {"gemma_title": "gemma4:26b-a4b 標題單判(線上 📈 原樣)", "strata_think": "Strata 標題單判 思考開啟(#38 主設計)"}


def build() -> None:
    ev = pd.read_csv(v34.EVENTS, dtype={"ticker": str})
    tickers = set(ev["ticker"].str.zfill(4))
    excl = v34._sample_prior(tickers)
    for seed, name in ((55555, "gold5_sample.csv"), (666666, "gold6_sample.csv"), (7777777, "gold7_sample.csv")):
        by_year, _ = v35._window_items(tickers, excl)
        random.seed(seed)
        drawn = random.sample(by_year[2025], 60) + random.sample(by_year[2026], 60)
        disk = pd.read_csv(GOLD_DIR / name, dtype={"ticker": str})
        if [(t, s, n) for _, t, s, n in drawn] != list(zip(disk.ticker, disk.title, disk.nid)):
            raise SystemExit(f"seed-{seed} 無法重現 {name} — 排除集不可信,停止")
        print(f"reconstruction OK: seed-{seed} reproduces {name} exactly")
        excl |= {(t, s) for _, t, s, _ in drawn}

    by_year, dates = v35._window_items(tickers, excl)
    print("window-eligible after s1-s7:", {y: len(v) for y, v in by_year.items()})
    random.seed(SEED)
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
    df.to_csv(GOLD_DIR / "gold8_sample.csv", index=False, encoding="utf-8-sig")
    print(f"saved gold8_sample.csv, bodies {int((df['body'].str.len() > 50).sum())}/120")
    with open(GOLD_DIR / "gold8_listing.txt", "w", encoding="utf-8") as f:
        for r in rows:
            body = r["body"] or ""
            m = re.search(r"\(\s*" + r["ticker"] + r"-TW", body)
            seg = body[max(0, m.start() - 80):m.end() + 110] if m else "(內文未提及該股)"
            f.write(f"{r['i']:3d} {r['year']} {r['date']} {r['ticker']} {r['name']:　<6}|{r['title']}\n"
                    f"    首段:{body[:120]}\n    提及:{seg}\n")
    print("listing -> gold8_listing.txt")


def _gold() -> pd.DataFrame:
    path = GOLD_DIR / "gold8_labeled.csv"
    if GOLD8_SHA == "GOLD8_LABELED_SHA256_PLACEHOLDER":
        raise SystemExit("盲標 sha 尚未登記,不得跑模型")
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    assert sha == GOLD8_SHA, f"gold8 與凍結 sha 不符: {sha}"
    return pd.read_csv(path, dtype={"ticker": str})


def _post(url: str, body: dict, pick) -> str:
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), headers={"Content-Type": "application/json"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=900) as resp:
                return pick(json.loads(resp.read())) or ""
        except Exception as exc:  # noqa: BLE001
            print(f"  retry {attempt+1}: {exc}", flush=True)
            time.sleep(10)
    raise RuntimeError("connection failed 3 times; item left unrecorded for re-run")


def _gemma(row) -> str:
    body = {"model": v34.MODEL, "stream": False, "think": False, "keep_alive": "30m",
            "options": {"temperature": 0, "num_ctx": 8192}, "messages": v38._messages(row)}
    return _post(v34.OLLAMA, body, lambda d: d["message"]["content"])


def _strata(row) -> str:
    body = {"model": "local", "messages": v38._messages(row), "temperature": 0, "max_tokens": 4096}
    return _post(f"{v38.STRATA}/chat/completions", body, lambda d: d["choices"][0]["message"].get("content"))


def _run(df: pd.DataFrame, tag: str, ask) -> None:
    order2 = list(range(len(df)))
    random.Random(SEED).shuffle(order2)
    for rnd, order in (("r1", list(range(len(df)))), ("r2", order2)):
        ck = GOLD_DIR / f"gold8_{tag}_{rnd}.csv"
        prev = pd.read_csv(ck) if ck.exists() else pd.DataFrame(columns=["i", "pred", "unparsed", "sec"])
        rows = prev.to_dict("records")
        done = {int(r["i"]) for r in rows}
        for n, idx in enumerate(order):
            if idx in done:
                continue
            t0 = time.time()
            pred, unparsed = v38._letter(ask(df.iloc[idx]))
            rows.append({"i": idx, "pred": pred, "unparsed": unparsed, "sec": round(time.time() - t0, 2)})
            pd.DataFrame(rows).to_csv(ck, index=False)
            print(f"[{tag} {rnd} {n+1}/{len(df)}] i={idx} {df.iloc[idx].ticker} -> {pred}", flush=True)


def score() -> None:
    df = _gold()
    gold = df["label"].tolist()
    out = {"n": len(gold), "gold_dist": df["label"].value_counts().to_dict(), "designs": {}}
    table = pd.DataFrame({"i": range(len(gold)), "ticker": df.ticker, "title": df.title, "gold": gold})
    for tag, desc in DESIGNS.items():
        paths = [GOLD_DIR / f"gold8_{tag}_{r}.csv" for r in ("r1", "r2")]
        if not all(p.exists() for p in paths):
            continue
        r1, r2 = (pd.read_csv(p).set_index("i").sort_index() for p in paths)
        if len(r1) != len(gold) or len(r2) != len(gold):
            print(f"{tag}: incomplete ({len(r1)}/{len(r2)})")
            continue
        p1, p2 = r1["pred"].tolist(), r2["pred"].tolist()
        flagged = [g for g, p in zip(gold, p1) if p == "B"]
        b_prec = sum(g == "B" for g in flagged) / len(flagged) if flagged else float("nan")
        n_b = sum(g == "B" for g in gold)
        b_rec = sum(1 for g, p in zip(gold, p1) if g == "B" and p == "B") / n_b if n_b else float("nan")
        agree = sum(a == b for a, b in zip(p1, p2)) / len(gold)
        ok = b_prec >= BARS["b_prec"] and b_rec >= BARS["b_recall"] and agree >= BARS["agree"]
        out["designs"][tag] = {"desc": desc, "b_prec": b_prec, "b_flagged": len(flagged), "b_recall": b_rec,
                               "agree": agree, "pass": ok, "unparsed_r1": int(r1["unparsed"].sum()),
                               "acc": sum(g == p for g, p in zip(gold, p1)) / len(gold)}
        table[f"{tag}_p1"], table[f"{tag}_p2"] = p1, p2
        print(f"{tag}: B-prec {b_prec:.1%} (n={len(flagged)}, bar 85%) | B-recall {b_rec:.1%} (bar 50%) | "
              f"agree {agree:.1%} (bar 90%) | unparsed {int(r1['unparsed'].sum())} -> {'PASS' if ok else 'FAIL'}")
    g, s = out["designs"].get("gemma_title"), out["designs"].get("strata_think")
    if g and s:
        out["decision"] = ("keep gemma4" if g["pass"] else "recommend switching 📈 to Strata" if s["pass"]
                           else "recommend hiding 📈")
        print("decision (pre-registered):", out["decision"])
    (GOLD_DIR / "gold8_result.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    table.to_csv(GOLD_DIR / "gold8_result.csv", index=False, encoding="utf-8-sig")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    {"build": build, "eval-gemma": lambda: _run(_gold(), "gemma_title", _gemma),
     "eval-strata": lambda: _run(_gold(), "strata_think", _strata), "score": score}[sys.argv[1]]()
