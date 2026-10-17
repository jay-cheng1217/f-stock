# -*- coding: utf-8 -*-
"""#34(#33-B 續):候選股「訊號視窗內」新聞判讀器驗收(2026-10-17 PM 選 B)。

目的:上一輪 #33 的 FAIL 是在「事件池個股 × 任意時期」分年樣本上量的,否決的是全量歷史判讀;
PM 核可的日常用途是「系統先篩出名單 → 對名單近 14 天新聞標利多/利空」。本票在**對齊部署母體**的
第五份全新盲標樣本上重驗:

母體(gold5):cnyes 台股新聞中,(code,title) 配對滿足
  (1) 編輯標註代號 1-4 檔、code ∈ #31 事件股票池;
  (2) 發布日落在該股任一 rere 事件日的 [D-13, D] 視窗內(與卡片近 14 天口徑一致);
  (3) 文章年份 2025 或 2026(部署只會判當代新聞),各抽 60 則,seed 55555;
  (4) 排除前四份樣本全部 (code,title)(s1-s3 依 seed 重建,重建正確性以能逐筆重現磁碟上的
      gold4_sample.csv 為準;s4 直接讀檔)。

門檻(沿用,先設後跑):準確率 ≥85%、利多精確率 ≥85%、兩輪一致 ≥90%。
設計階序(先登記):主設計 = gemma4:26b-a4b 標題單判(部署即判標題,DuckDB 無內文);
主設計未過才評備援 = gemma4 抽取式 v3.1(標題+內文,部署需每日補抓內文)。
盲標:由標題+內文人工標註,於任何模型輸出之前凍結 sha256。
已知口徑差:抽樣用編輯標註代號,部署用 ticker_links,涵蓋相近但不完全相同。

用法:
  python -X utf8 scripts/validate_news_window_classifier.py build   # 重建排除集+抽樣+抓內文
  python -X utf8 scripts/validate_news_window_classifier.py eval    # 盲標凍結後才可跑
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[1]
CACHE = REPO / "ml" / "reports" / "cache" / "cnyes_tw_stock"
GOLD_DIR = REPO / "ml" / "reports" / "cache" / "news_gold"
EVENTS = REPO / "ml" / "reports" / "cache" / "bt_rere_story_presence_events.csv"
OLLAMA = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434/api/chat")
MODEL = "gemma4:26b-a4b"
BARS = {"acc": 0.85, "b_prec": 0.85, "agree": 0.90}
GOLD5_SHA = "f97874f42369267fe689c5b2b3a9b66e5ddd4d20b239078cfd3cf7b5f628aba4"  # 盲標凍結 2026-10-17,先於任何模型輸出

PROMPT_TITLE = """你是台股新聞分析員。給你一則新聞「標題」與一檔股票,判斷這則標題對「這一檔」的基本面影響:
B = 利多(營收/獲利/訂單/產能/漲價/上修/新客戶/打入供應鏈/授權合資等具體進展)
R = 利空(下修/調降/衰退/虧損/停工/禁令/訴訟/經營權糾紛等)
N = 其他一律 N(很重要):
- 標題主角不是這檔(只被並列、族群點名、同業對照)→ N
- 公關活動/論壇/CSR/得獎/產品亮相/技術展示(無具體財務數字或訂單)→ N
- 純股價漲跌/盤勢/籌碼(外資買賣超/融資)描述 → N
- 專欄/早報/操盤手/研究報告式薦股 → N
- 訊號混合(月增但年減、虧損但收斂)或看不出方向 → N
只輸出一個字母:B 或 R 或 N。"""

PROMPT_EXTRACT = """你是台股新聞分析員。讀完下面這篇新聞(標題+內文),分兩步:
第一步:判斷這篇的「主要報導對象」——通常就是標題點名的公司,至多 3 檔;標題沒點名任何公司時,取首段聚焦的那一檔。
第二步:只對主要報導對象,判斷文章對它的基本面影響:
B = 利多(營收/獲利/訂單/產能/漲價/上修/新客戶/打入供應鏈等)
R = 利空(下修/調降/衰退/停工/訴訟/經營權糾紛等)
不列的情況(很重要):
- 不是主要報導對象(只被順帶提及、同業對照、族群點名、文末補充)一律不列。
- 公關活動/論壇/CSR/得獎/產品亮相/技術展示,即使是主要對象,也不列(無具體財務數字或訂單)。
- 純股價漲跌/盤勢/籌碼描述,不列。
代號一律抄內文括號裡的 4 碼數字(例如「台積電(2330-TW)」→ 2330);內文沒給代號的不要列。
只輸出 JSON 陣列,不要其他文字,格式:[{"code":"2330","label":"B"}];若沒有,輸出 []"""


def _iter_items(files, cand: set):
    for fp in files:
        for line in open(fp, encoding="utf-8"):
            try:
                it = json.loads(line)
            except json.JSONDecodeError:  # 進行中月份檔的截斷列,略過
                continue
            codes = it.get("codes") or []
            if 1 <= len(codes) <= 4:
                for c in codes:
                    if c in cand:
                        yield it, c


def _pairs_all(years: set, cand: set) -> tuple[list, dict]:
    """與 gold2-4 抽樣完全同法:tuple (y,c,title,id) 經 sorted(set()) 去重排序。
    另回傳 (c,title,id)->發布日 查表供視窗過濾用。"""
    out, dates = [], {}
    for it, c in _iter_items(sorted(CACHE.glob("*.jsonl")), cand):
        y = datetime.fromtimestamp(int(it["publishAt"])).year
        if y in years:
            out.append((y, c, it["title"], it["id"]))
            dates[(c, it["title"], it["id"])] = datetime.fromtimestamp(int(it["publishAt"]))
    return sorted(set(out)), dates


def _sample_prior(cand: set) -> set:
    """以 gold4 建立時的同一套程式重建 s1-s3,並要求 seed-3333 重現磁碟上的 gold4_sample.csv。"""
    import glob as _glob

    files1 = _glob.glob(str(CACHE / "2026-0[6-9].jsonl")) + _glob.glob(str(CACHE / "2026-10.jsonl"))
    items1 = sorted({(c, it["title"]) for it, c in _iter_items(files1, cand)})
    random.seed(20261017)
    s1 = set(random.sample(items1, 120))

    items_y, _ = _pairs_all(set(range(2021, 2027)), cand)
    random.seed(33)
    pool2 = [x for x in items_y if (x[1], x[2]) not in s1]
    s2d = []
    for y in range(2021, 2027):
        p = [x for x in pool2 if x[0] == y]
        s2d += random.sample(p, min(20, len(p)))
    s2 = {(t, s) for _, t, s, _ in s2d}
    random.seed(333)
    pool3 = [x for x in items_y if (x[1], x[2]) not in s1 and (x[1], x[2]) not in s2]
    s3d = []
    for y in range(2021, 2027):
        s3d += random.sample([x for x in pool3 if x[0] == y], 20)
    s3 = {(t, s) for _, t, s, _ in s3d}

    random.seed(3333)
    pool4 = [x for x in items_y if (x[1], x[2]) not in s1 | s2 | s3]
    s4d = []
    for y in range(2021, 2027):
        s4d += random.sample([x for x in pool4 if x[0] == y], 20)
    gold4 = pd.read_csv(GOLD_DIR / "gold4_sample.csv", dtype={"ticker": str})
    got = [(t, s, n) for _, t, s, n in s4d]
    want = list(zip(gold4.ticker, gold4.title, gold4.nid))
    if got != want:
        diff = sum(1 for a, b in zip(got, want) if a != b)
        raise SystemExit(f"s1-s3 重建無法重現 gold4({diff}/120 不符)— 排除集不可信,停止")
    print("reconstruction OK: seed-3333 draw reproduces gold4_sample.csv exactly")
    return s1 | s2 | s3 | {(t, s) for _, t, s, _ in s4d}


def _fetch_body(nid: int) -> str:
    import requests
    from bs4 import BeautifulSoup

    try:
        r = requests.get(f"https://news.cnyes.com/news/id/{nid}",
                         headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
        r.raise_for_status()
        node = BeautifulSoup(r.text, "html.parser").select_one("#article-container")
        return re.sub(r"\s+", " ", node.get_text(" ", strip=True)) if node else ""
    except Exception:  # noqa: BLE001
        return ""


def build() -> None:
    ev = pd.read_csv(EVENTS, dtype={"ticker": str})
    tickers = set(ev["ticker"].str.zfill(4))
    windows: dict[str, list] = {}
    for t, d in zip(ev["ticker"].str.zfill(4), pd.to_datetime(ev["date"])):
        windows.setdefault(t, []).append(d)

    excl = _sample_prior(tickers)
    items_y, dates = _pairs_all({2025, 2026}, tickers)

    def in_window(tup) -> bool:
        _, c, title, nid = tup
        d = dates[(c, title, nid)].replace(hour=0, minute=0, second=0, microsecond=0)
        return any(e - timedelta(days=13) <= d <= e for e in windows.get(c, []))

    elig = [x for x in items_y if (x[1], x[2]) not in excl and in_window(x)]
    by_year = {y: [x for x in elig if x[0] == y] for y in (2025, 2026)}
    print("window-eligible:", {y: len(v) for y, v in by_year.items()})
    random.seed(55555)
    picked = random.sample(by_year[2025], 60) + random.sample(by_year[2026], 60)

    sec = pd.read_csv(REPO / "ml" / "data" / "sector_mapping.csv", dtype=str)
    names = dict(zip(sec["Ticker"].str.zfill(4), sec["Name"]))

    rows = []
    for i, (y, t, title, nid) in enumerate(picked):
        body = _fetch_body(nid)[:2500]
        rows.append({"i": i, "year": y, "date": dates[(t, title, nid)].strftime("%Y-%m-%d"),
                     "ticker": t, "name": names.get(t, ""), "title": title,
                     "nid": nid, "body": body})
        time.sleep(0.5)
        if (i + 1) % 20 == 0:
            print(f"bodies {i+1}/120", flush=True)
    df = pd.DataFrame(rows)
    out = GOLD_DIR / "gold5_sample.csv"
    df.to_csv(out, index=False, encoding="utf-8-sig")
    ok = int((df["body"].str.len() > 50).sum())
    print(f"saved {out}, bodies {ok}/120")

    lst = GOLD_DIR / "gold5_listing.txt"
    with open(lst, "w", encoding="utf-8") as f:
        for r in rows:
            body = r["body"] or ""
            m = re.search(r"\(\s*" + r["ticker"] + r"-TW", body)
            seg = body[max(0, m.start() - 80):m.end() + 110] if m else "(內文未提及該股)"
            f.write(f"{r['i']:3d} {r['year']} {r['date']} {r['ticker']} {r['name']:　<6}|{r['title']}\n"
                    f"    首段:{body[:120]}\n    提及:{seg}\n")
    print(f"listing -> {lst}")


def _ask(payload_msgs: list, want_json: bool) -> str:
    import requests

    body = {"model": MODEL, "stream": False, "think": False, "keep_alive": "30m",
            "options": {"temperature": 0, "num_ctx": 8192}, "messages": payload_msgs}
    for attempt in range(3):
        try:
            r = requests.post(OLLAMA, json=body, timeout=600)
            r.raise_for_status()
            return r.json()["message"]["content"]
        except Exception as exc:  # noqa: BLE001
            print(f"  retry {attempt+1}: {exc}", flush=True)
            time.sleep(10)
    return ""


def _predict_title(row) -> str:
    out = _ask([{"role": "system", "content": PROMPT_TITLE},
                {"role": "user", "content": f"股票:{row.ticker} {row['name']}\n標題:{row.title}"}], False)
    m = re.search(r"[BRN]", out.upper())
    return m.group(0) if m else "N"


def _predict_extract(row) -> str:
    body = "" if pd.isna(row.body) else str(row.body)
    out = _ask([{"role": "system", "content": PROMPT_EXTRACT},
                {"role": "user", "content": f"標題:{row.title}\n內文:{body}"[:6000]}], True)
    m = re.search(r"\[.*\]", out, re.S)
    if not m:
        return "N"
    try:
        arr = json.loads(m.group(0))
    except Exception:  # noqa: BLE001
        return "N"
    for item in arr:
        if str(item.get("code", "")).strip() == str(row.ticker):
            lab = str(item.get("label", "")).upper()
            return lab if lab in ("B", "R") else "N"
    return "N"


def _run_design(df: pd.DataFrame, fn, tag: str) -> tuple[list, list]:
    def one_round(order: list, ck: Path) -> list:
        done = {}
        if ck.exists():
            prev = pd.read_csv(ck)
            done = {int(r.i): str(r.pred) for r in prev.itertuples()}
        rows = []
        for n, idx in enumerate(order):
            if idx in done:
                rows.append({"i": idx, "pred": done[idx]})
                continue
            pred = fn(df.iloc[idx])
            rows.append({"i": idx, "pred": pred})
            pd.DataFrame(rows).to_csv(ck, index=False)
            print(f"[{tag} {n+1}/120] i={idx} {df.iloc[idx].ticker} -> {pred}", flush=True)
        return [r["pred"] for r in sorted(rows, key=lambda r: r["i"])]

    o2 = list(range(120))
    random.Random(55555).shuffle(o2)
    p1 = one_round(list(range(120)), GOLD_DIR / f"gold5_{tag}_r1.csv")
    p2 = one_round(o2, GOLD_DIR / f"gold5_{tag}_r2.csv")
    return p1, p2


def _score(gold: list, p1: list, p2: list, tag: str) -> bool:
    acc = sum(g == p for g, p in zip(gold, p1)) / len(gold)
    bs = [(g, p) for g, p in zip(gold, p1) if p == "B"]
    b_prec = sum(g == "B" for g, _ in bs) / len(bs) if bs else float("nan")
    agree = sum(a == b for a, b in zip(p1, p2)) / len(p1)
    ok = acc >= BARS["acc"] and b_prec >= BARS["b_prec"] and agree >= BARS["agree"]
    print(f"\n==== {tag} ====\nacc {acc:.1%} | B-prec {b_prec:.1%} (n={len(bs)}) | agree {agree:.1%} "
          f"-> {'PASS' if ok else 'FAIL'}")
    return ok


def evaluate() -> None:
    path = GOLD_DIR / "gold5_labeled.csv"
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    if GOLD5_SHA == "GOLD5_LABELED_SHA256_PLACEHOLDER":
        raise SystemExit("盲標 sha 尚未登記,不得跑模型")
    assert sha == GOLD5_SHA, f"gold5 檔案與凍結 sha 不符: {sha}"
    df = pd.read_csv(path, dtype={"ticker": str})
    gold = df["label"].tolist()

    p1, p2 = _run_design(df, _predict_title, "title")
    primary_ok = _score(gold, p1, p2, "primary gemma title-only")
    result = {"primary": {"p1": p1, "p2": p2, "pass": primary_ok}}
    if not primary_ok:
        q1, q2 = _run_design(df, _predict_extract, "extract")
        result["secondary"] = {"p1": q1, "p2": q2,
                               "pass": _score(gold, q1, q2, "secondary gemma extract v3.1")}
    out = pd.DataFrame({"i": range(120), "ticker": df.ticker, "gold": gold,
                        "title_p1": p1, "title_p2": p2})
    if "secondary" in result:
        out["ext_p1"] = result["secondary"]["p1"]
        out["ext_p2"] = result["secondary"]["p2"]
    out.to_csv(GOLD_DIR / "gold5_result.csv", index=False, encoding="utf-8-sig")
    print(f"saved {GOLD_DIR / 'gold5_result.csv'}")

    err = [(i, gold[i], p1[i], df.iloc[i].ticker, str(df.iloc[i].title)[:40])
           for i in range(120) if gold[i] != p1[i]]
    print(f"primary errors: {len(err)}")
    for e in err:
        print("  i=%3d gold=%s pred=%s %s %s" % e)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    {"build": build, "eval": evaluate}[sys.argv[1]]()
