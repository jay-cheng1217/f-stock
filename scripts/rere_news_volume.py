# -*- coding: utf-8 -*-
"""名單個股的近期新聞正面聲量(2026-10-16 PM:「選股再加一個分數看近期新聞的正面聲量」;只做呈現與記錄)。

依據與定位:回測票 #31(BT-rere-story-presence)在 2021-2026 共 10,004 個 rere 事件上驗過,正向新聞與結果
**沒有可靠關係**(POS−無報導 +1.40pp,只有 2025、2026 兩年為正;2026 年 +11.4pp 屬單年體制)。所以這個分數:
  - 只顯示在卡片上供人判讀,**不進排序、不過濾、不否決**;
  - 每天把當時的數值寫進 ml/reports/rere_news_volume.csv(append-only,同一交易日不重寫),供日後樣本外檢驗。

口徑與 #31 一致:資料日 T 的前 14 個日曆天(T−13 00:00 ~ T 23:59)鉅亨「台股新聞」中連到該股的標題
(DuckDB news_articles × news_ticker_links,每晚 Phase-1「Update daily news」更新,與完整回補比對覆蓋約 98%);
正向 = 既有分類表 backend/features/narrative/taxonomy.yaml 的 polarity=positive 標籤,且標題不含價格回聲字
(漲停/跌停/股價/盤中/急漲/重挫)。注意:#31 用鉅亨編輯標註代號,這裡用 ticker_links(正則/公司名對應),口徑相近但不完全相同。
pos_rank = 當日名單+候選池中正向則數的名次(1=最多,同數同名次)。

2026-10-17 #33 工具驗收後修正:關鍵字「正向」判定盲測準確率僅 42.5%、本地模型標題判讀在代表性樣本 81.7%
(利多精確率 76%),皆未過事前門檻(85/85/90)——卡片只顯示報導則數,pos14 欄位照記(研究連續性)但不顯示、不得使用。

2026-10-17 #35 PASS 後新增 bull14:本機 Ollama 對每則標題判「對這一檔是否利多」(prompt/模型與驗收時同一份,
import 自 validate_news_window_classifier,不得在此另改),只計 B。驗收:利多精確率 93.5%、召回率 66.2%——
卡片只在 bull14>0 時顯示「📈 利多 k 則」,**沒有 📈 不代表沒有利多**;不進排序、過濾或否決。
判讀結果快取於 ml/reports/cache/news_bull_labels.csv(同一 (代號,標題) 不重判)。Ollama 連不上時 bull14 留空,不擋流程。
用法:python -X utf8 scripts/rere_news_volume.py   (排程選用步驟;任何錯誤只印警告並以 0 結束)
"""
from __future__ import annotations

import io
import json
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
OUT = REPO / "ml" / "reports" / "rere_news_volume.csv"
PLAN_DIR = REPO / "logs"
WINDOW_DAYS = 14
ECHO = ("漲停", "跌停", "股價", "盤中", "急漲", "重挫")
COLS = ["trade_date", "as_of_date", "ticker", "any14", "pos14", "pos_rank", "pool_n", "bull14"]
BULL_CACHE = REPO / "ml" / "reports" / "cache" / "news_bull_labels.csv"


def _bull_prompt() -> tuple[str, str, str]:
    try:
        from scripts.validate_news_window_classifier import MODEL, OLLAMA, PROMPT_TITLE
    except ImportError:
        from validate_news_window_classifier import MODEL, OLLAMA, PROMPT_TITLE
    return PROMPT_TITLE, MODEL, OLLAMA


def _ask_bull(prompt: str, model: str, url: str, ticker: str, name: str, title: str) -> str:
    import requests

    body = {"model": model, "stream": False, "think": False, "keep_alive": "30m",
            "options": {"temperature": 0, "num_ctx": 8192},
            "messages": [{"role": "system", "content": prompt},
                         {"role": "user", "content": f"股票:{ticker} {name}\n標題:{title}"}]}
    r = requests.post(url, json=body, timeout=120)
    r.raise_for_status()
    m = re.search(r"[BRN]", r.json()["message"]["content"].upper())
    return m.group(0) if m else "N"


def classify_titles(pairs: list[tuple[str, str]], ask=None) -> dict[tuple[str, str], str] | None:
    """#35:回傳 {(代號, 標題): B/R/N}。Ollama 不可用回 None(呼叫端讓 bull14 留空,不可當成「沒有利多」)。"""
    pairs = sorted(set(pairs))
    cache = {}
    if BULL_CACHE.exists():
        c = pd.read_csv(BULL_CACHE, dtype={"ticker": str, "title": str, "label": str})
        cache = {(t, s): lab for t, s, lab in zip(c["ticker"], c["title"], c["label"])}
    todo = [p for p in pairs if p not in cache]
    if todo:
        prompt, model, url = _bull_prompt()
        ask = ask or (lambda t, n, s: _ask_bull(prompt, model, url, t, n, s))
        sec = pd.read_csv(REPO / "ml" / "data" / "sector_mapping.csv", dtype=str)
        names = dict(zip(sec["Ticker"].str.zfill(4), sec["Name"]))
        BULL_CACHE.parent.mkdir(parents=True, exist_ok=True)
        new_rows: list[dict] = []

        def flush() -> None:
            if new_rows:
                pd.DataFrame(new_rows).to_csv(BULL_CACHE, mode="a", header=not BULL_CACHE.exists(),
                                              index=False, encoding="utf-8-sig")
                new_rows.clear()

        for k, (t, s) in enumerate(todo, 1):
            try:
                lab = ask(t, names.get(t, ""), s)
            except Exception as exc:  # noqa: BLE001
                flush()
                print(f"[news-volume] bull classifier unavailable: {type(exc).__name__}: {exc}")
                return None
            cache[(t, s)] = lab
            new_rows.append({"ticker": t, "title": s, "label": lab, "model": model,
                             "classified_at": datetime.now().isoformat(timespec="seconds")})
            if k % 25 == 0:
                flush()                      # 步驟被逾時砍掉時,已判的標題留在快取,隔天不必重判
        flush()
    return {p: cache[p] for p in pairs}


def summarize(news: pd.DataFrame, tickers: list[str], as_of: str, taxonomy: dict | None = None,
              bull_labels: dict | None = None) -> pd.DataFrame:
    """news 欄位:ticker, published_at(datetime 或字串), title。回傳每檔 any14 / pos14 / pos_rank / bull14。
    bull_labels=None 時 bull14 留空(判讀器不可用),不是 0。"""
    from backend.features.narrative.labeler import label_text, load_taxonomy

    tax = taxonomy or load_taxonomy()
    lo = datetime.fromisoformat(as_of) - timedelta(days=WINDOW_DAYS - 1)
    hi = datetime.fromisoformat(as_of) + timedelta(days=1)
    n = news.copy()
    n["published_at"] = pd.to_datetime(n["published_at"])
    n = n[(n["published_at"] >= lo) & (n["published_at"] < hi)].drop_duplicates(["ticker", "title"])
    n["pos"] = [int(not any(k in t for k in ECHO) and any(l.polarity == "positive" for l in label_text(t, "", tax)))
                for t in n["title"].astype(str)]
    agg = n.groupby("ticker").agg(any14=("title", "size"), pos14=("pos", "sum"))
    out = pd.DataFrame({"ticker": sorted(set(tickers))})
    out = out.merge(agg, left_on="ticker", right_index=True, how="left").fillna({"any14": 0, "pos14": 0})
    out[["any14", "pos14"]] = out[["any14", "pos14"]].astype(int)
    out["pos_rank"] = out["pos14"].rank(method="min", ascending=False).astype(int)
    out["pool_n"] = len(out)
    if bull_labels is None:
        out["bull14"] = pd.NA
    else:
        n["bull"] = [int(bull_labels.get((t, s)) == "B") for t, s in zip(n["ticker"], n["title"].astype(str))]
        bull = n.groupby("ticker")["bull"].sum()
        out["bull14"] = out["ticker"].map(bull).fillna(0).astype(int)
    return out


def _plan_tickers(plan: dict) -> list[str]:
    tks = {str(p.get("ticker")).zfill(4) for p in plan.get("shadow_rere_pool") or []}
    tks |= {str(r.get("stock", "")).split()[0].zfill(4) for r in plan.get("rows") or [] if str(r.get("stock", "")).strip()}
    return sorted(t for t in tks if t.isdigit() and len(t) == 4)


def _load_news(tickers: list[str], as_of: str) -> pd.DataFrame:
    import duckdb
    from backend.config import DUCKDB_PATH

    lo = (datetime.fromisoformat(as_of) - timedelta(days=WINDOW_DAYS - 1)).strftime("%Y-%m-%d")
    hi = (datetime.fromisoformat(as_of) + timedelta(days=1)).strftime("%Y-%m-%d")
    con = duckdb.connect(DUCKDB_PATH, read_only=True)
    try:
        return con.execute(
            "select l.ticker, a.published_at, a.title from news_articles a join news_ticker_links l using(news_id) "
            "where a.source = 'cnyes' and a.published_at >= ? and a.published_at < ? and l.ticker in (select unnest(?))",
            [lo, hi, tickers]).df()
    finally:
        con.close()


def run(plan_path: Path | None = None) -> str:
    plans = sorted(p for p in PLAN_DIR.glob("entry_list_2*.json") if not p.name.endswith(".manifest.json"))
    plan_path = plan_path or (plans[-1] if plans else None)
    if plan_path is None:
        return "no entry plan"
    plan = json.loads(Path(plan_path).read_text(encoding="utf-8"))
    trade, as_of = plan.get("trade_date"), plan.get("as_of_date")
    if not trade or not as_of:
        return "plan lacks dates"
    existing = pd.read_csv(OUT, dtype={"ticker": str, "trade_date": str}) if OUT.exists() else pd.DataFrame(columns=COLS)
    if trade in set(existing["trade_date"]):
        return f"{trade} already recorded"
    tickers = _plan_tickers(plan)
    news = _load_news(tickers, as_of)
    labels = classify_titles(list(zip(news["ticker"], news["title"].astype(str)))) if len(news) else {}
    frame = summarize(news, tickers, as_of, bull_labels=labels)
    frame.insert(0, "as_of_date", as_of)
    frame.insert(0, "trade_date", trade)
    pd.concat(([existing] if len(existing) else []) + [frame[COLS]], ignore_index=True).to_csv(OUT, index=False, encoding="utf-8-sig")
    bull = "判讀器不可用" if labels is None else f"有📈利多 {int((frame['bull14'] > 0).sum())} 檔"
    return f"{trade}: {len(frame)} 檔,{bull}"


def main() -> int:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    try:
        print("[news-volume]", run())
    except Exception as exc:  # noqa: BLE001 - 研究欄位失敗不可擋名單儀表板
        print(f"[news-volume] WARNING skipped: {type(exc).__name__}: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
