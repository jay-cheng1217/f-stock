# -*- coding: utf-8 -*-
"""#38 本機大模型新聞判讀驗收:Strata(Qwen3.8-Flash-Next IQ2_XS)vs gemma4(2026-10-17 PM「請開始」,事前登記)。

問題:#33/#34 的判讀器(gemma4:26b-a4b 標題單判)在部署母體只有 78.3%,#33 的 qwen3:32b 更差(69.2%)。
PM 另裝 Strata(125B 專家混合模型,2-bit,H:\\Strata,OpenAI 相容 API 127.0.0.1:8080),本票驗「換更強的本機模型」
能不能讓 #34 的完整判讀(B/N/R 三類)在**全新盲標樣本**上過關。

樣本 gold7(先於任何模型輸出完成):母體同 gold5/gold6——事件股(#31)× rere 事件日 [D-13, D] 視窗內 cnyes 新聞,
  編輯標註代號 1-4 檔;2025、2026 各抽 60 則,seed 7777777;排除 s1-s6 全部 (code,title):
  s1-s4 以 v34 原程式重建且須逐筆重現 gold4_sample.csv,s5 須以 seed 55555 重現 gold5_sample.csv,
  s6 須以 seed 666666 重現 gold6_sample.csv,任何一份重現失敗即停止。
盲標:標題+內文,標註者=Claude(與 gold4-6 同一流程與口徑:這則新聞對「這一檔」的基本面影響);
  標完先凍結 sha256 並寫入 GOLD7_SHA,之後才可跑任何模型。

受測設計(提示詞 = v34.PROMPT_TITLE,一字不改;user 訊息格式同 v34;temperature 0;兩輪,第二輪 seed 7777777 亂序):
  主設計(唯一決定 PASS/FAIL):Strata 標題單判,思考開啟(模型預設),max_tokens 4096。
  描述用(不得據以宣稱通過):Strata 思考關閉;gemma4:26b-a4b 標題單判(v34 原樣,think=False)。
  解析:回覆正文第一個 B/R/N 字母;空白或無字母 → N(同 v34),並另計次數。
門檻(沿用 #34,先設後跑,三項全過才 PASS):準確率 ≥85%、利多精確率 ≥85%、兩輪一致 ≥90%。
  另報(描述):利多召回率、與 gemma4 同樣本差距(n=120 時準確率標準誤約 3.5pp,差距 <7pp 視為無法區分)。
PASS → 只提出建議:卡片新聞改顯示 B/R 完整標籤並改用 Strata,上線另需 PM 核可與運維方案
  (Strata 佔約 39GB 記憶體,不能和 Ollama 同時載入;一次只處理一個請求)。
FAIL → 結論為「本機可得模型(4B-active MoE → 32B dense → 125B MoE 2-bit)都過不了」,#34 維持關閉,
  卡片維持 #35 的 gemma4 📈 標記;不得用 gold7 調提示詞或換壓縮版後宣稱通過,重開需第八份全新樣本。

用法:
  python -X utf8 scripts/validate_news_strata.py build          # 重建排除集+抽 gold7+抓內文+產生標註清單
  python -X utf8 scripts/validate_news_strata.py eval-strata    # 盲標凍結後;需 Strata 執行中
  python -X utf8 scripts/validate_news_strata.py eval-gemma     # 需關閉 Strata、Ollama 可用
  python -X utf8 scripts/validate_news_strata.py score
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
import validate_news_window_classifier as v34  # noqa: E402

GOLD_DIR = v34.GOLD_DIR
STRATA = "http://127.0.0.1:8080/v1"
BARS = v34.BARS  # acc 0.85, b_prec 0.85, agree 0.90
SEED = 7777777
GOLD7_SHA = "3848ad59448f27342e314484140aa968619925cb0ff774ea7d298da92aff60ec"  # 盲標凍結 2026-10-17,先於任何模型輸出(B55/N60/R5)
DESIGNS = {  # tag: (說明, 是否主設計)
    "strata_think": ("Strata 標題單判 思考開啟", True),
    "strata_nothink": ("Strata 標題單判 思考關閉", False),
    "gemma_title": ("gemma4:26b-a4b 標題單判(v34 原樣)", False),
}


def build() -> None:
    ev = pd.read_csv(v34.EVENTS, dtype={"ticker": str})
    tickers = set(ev["ticker"].str.zfill(4))
    excl = v34._sample_prior(tickers)  # s1-s4,內含 gold4 逐筆重現檢查

    for seed, name in ((55555, "gold5_sample.csv"), (666666, "gold6_sample.csv")):
        by_year, _ = v35._window_items(tickers, excl)
        random.seed(seed)
        drawn = random.sample(by_year[2025], 60) + random.sample(by_year[2026], 60)
        disk = pd.read_csv(GOLD_DIR / name, dtype={"ticker": str})
        if [(t, s, n) for _, t, s, n in drawn] != list(zip(disk.ticker, disk.title, disk.nid)):
            raise SystemExit(f"seed-{seed} 無法重現 {name} — 排除集不可信,停止")
        print(f"reconstruction OK: seed-{seed} reproduces {name} exactly")
        excl |= {(t, s) for _, t, s, _ in drawn}

    by_year, dates = v35._window_items(tickers, excl)
    print("window-eligible after s1-s6:", {y: len(v) for y, v in by_year.items()})
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
    df.to_csv(GOLD_DIR / "gold7_sample.csv", index=False, encoding="utf-8-sig")
    print(f"saved gold7_sample.csv, bodies {int((df['body'].str.len() > 50).sum())}/120")
    with open(GOLD_DIR / "gold7_listing.txt", "w", encoding="utf-8") as f:
        for r in rows:
            body = r["body"] or ""
            m = re.search(r"\(\s*" + r["ticker"] + r"-TW", body)
            seg = body[max(0, m.start() - 80):m.end() + 110] if m else "(內文未提及該股)"
            f.write(f"{r['i']:3d} {r['year']} {r['date']} {r['ticker']} {r['name']:　<6}|{r['title']}\n"
                    f"    首段:{body[:120]}\n    提及:{seg}\n")
    print("listing -> gold7_listing.txt")


def _gold() -> pd.DataFrame:
    path = GOLD_DIR / "gold7_labeled.csv"
    if GOLD7_SHA == "GOLD7_LABELED_SHA256_PLACEHOLDER":
        raise SystemExit("盲標 sha 尚未登記,不得跑模型")
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    assert sha == GOLD7_SHA, f"gold7 與凍結 sha 不符: {sha}"
    return pd.read_csv(path, dtype={"ticker": str})


def _strata(messages: list, think: bool) -> str:
    body = {"model": "local", "messages": messages, "temperature": 0, "max_tokens": 4096 if think else 64}
    if not think:
        body["chat_template_kwargs"] = {"enable_thinking": False}
    req = urllib.request.Request(f"{STRATA}/chat/completions", data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=900) as resp:
                return json.loads(resp.read())["choices"][0]["message"].get("content") or ""
        except Exception as exc:  # noqa: BLE001
            print(f"  retry {attempt+1}: {exc}", flush=True)
            time.sleep(10)
    return ""


def _letter(out: str) -> tuple[str, bool]:
    m = re.search(r"[BRN]", (out or "").upper())
    return (m.group(0), False) if m else ("N", True)


def _messages(row) -> list:
    return [{"role": "system", "content": v34.PROMPT_TITLE},
            {"role": "user", "content": f"股票:{row.ticker} {row['name']}\n標題:{row.title}"}]


def _run(df: pd.DataFrame, tag: str, ask) -> None:
    order2 = list(range(len(df)))
    random.Random(SEED).shuffle(order2)
    for rnd, order in (("r1", list(range(len(df)))), ("r2", order2)):
        ck = GOLD_DIR / f"gold7_{tag}_{rnd}.csv"
        done = {int(r.i): r for r in pd.read_csv(ck).itertuples()} if ck.exists() else {}
        rows = [{"i": r.i, "pred": r.pred, "unparsed": r.unparsed, "sec": r.sec} for r in done.values()]
        for n, idx in enumerate(order):
            if idx in done:
                continue
            t0 = time.time()
            pred, unparsed = _letter(ask(df.iloc[idx]))
            rows.append({"i": idx, "pred": pred, "unparsed": unparsed, "sec": round(time.time() - t0, 2)})
            pd.DataFrame(rows).to_csv(ck, index=False)
            print(f"[{tag} {rnd} {n+1}/{len(df)}] i={idx} {df.iloc[idx].ticker} -> {pred}", flush=True)


def eval_strata() -> None:
    df = _gold()
    with urllib.request.urlopen(f"{STRATA}/models", timeout=30) as resp:
        info = json.loads(resp.read())
    (GOLD_DIR / "gold7_strata_model.json").write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")
    _run(df, "strata_think", lambda row: _strata(_messages(row), think=True))
    _run(df, "strata_nothink", lambda row: _strata(_messages(row), think=False))


def eval_gemma() -> None:
    df = _gold()
    _run(df, "gemma_title", lambda row: v34._ask(_messages(row), False))


def score() -> None:
    df = _gold()
    gold = df["label"].tolist()
    out = {"n": len(gold), "gold_dist": df["label"].value_counts().to_dict(), "designs": {}}
    table = pd.DataFrame({"i": range(len(gold)), "ticker": df.ticker, "title": df.title, "gold": gold})
    for tag, (desc, primary) in DESIGNS.items():
        r1p, r2p = GOLD_DIR / f"gold7_{tag}_r1.csv", GOLD_DIR / f"gold7_{tag}_r2.csv"
        if not (r1p.exists() and r2p.exists()):
            continue
        r1 = pd.read_csv(r1p).set_index("i").sort_index()
        r2 = pd.read_csv(r2p).set_index("i").sort_index()
        if len(r1) != len(gold) or len(r2) != len(gold):
            print(f"{tag}: incomplete ({len(r1)}/{len(r2)})")
            continue
        p1, p2 = r1["pred"].tolist(), r2["pred"].tolist()
        acc = sum(g == p for g, p in zip(gold, p1)) / len(gold)
        flagged = [g for g, p in zip(gold, p1) if p == "B"]
        b_prec = sum(g == "B" for g in flagged) / len(flagged) if flagged else float("nan")
        n_b = sum(g == "B" for g in gold)
        b_rec = sum(1 for g, p in zip(gold, p1) if g == "B" and p == "B") / n_b if n_b else float("nan")
        agree = sum(a == b for a, b in zip(p1, p2)) / len(gold)
        ok = acc >= BARS["acc"] and b_prec >= BARS["b_prec"] and agree >= BARS["agree"]
        out["designs"][tag] = {"desc": desc, "primary": primary, "acc": acc, "b_prec": b_prec, "b_flagged": len(flagged),
                               "b_recall": b_rec, "agree": agree, "pass": ok,
                               "unparsed_r1": int(r1["unparsed"].sum()), "median_sec": float(r1["sec"].median())}
        table[f"{tag}_p1"], table[f"{tag}_p2"] = p1, p2
        print(f"{'PRIMARY ' if primary else ''}{tag}: acc {acc:.1%} | B-prec {b_prec:.1%} (n={len(flagged)}) | "
              f"B-recall {b_rec:.1%} | agree {agree:.1%} | unparsed {int(r1['unparsed'].sum())} -> "
              f"{'PASS' if ok else 'FAIL'}")
    (GOLD_DIR / "gold7_result.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    table.to_csv(GOLD_DIR / "gold7_result.csv", index=False, encoding="utf-8-sig")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    {"build": build, "eval-strata": eval_strata, "eval-gemma": eval_gemma, "score": score}[sys.argv[1]]()
