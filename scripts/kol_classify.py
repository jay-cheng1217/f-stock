# -*- coding: utf-8 -*-
"""T2 KOL 貼文標的擷取 + 多空分類(docs/PLAN_kol_opinion_tracker.md)。

用本機 `claude` CLI headless(免 API key)批次分類 kol_tracker.db 中未分類貼文:
每則擷取 {symbol, market, stance, confidence, reason},寫入 mentions。
無標的貼文也標記 classified_at,避免重複送。

stance 定義(dashboard 語意):
  bullish  = 明確看多該股(論點/加碼/目標)
  bearish  = 明確看空(風險警告/出脫/毒性融資)
  neutral  = 提及但無方向(新聞轉述/中性比較)

用法:
  python scripts/kol_classify.py                # 分類所有未分類
  python scripts/kol_classify.py --limit 20     # 只跑一批(測試)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
DB_PATH = BASE_DIR / "kol_tracker.db"
LOG_PATH = BASE_DIR / "logs" / "kol_tracker.log"
BATCH = 15
MODEL = "haiku"

PROMPT_HEAD = """You are a financial post classifier for an opinion tracker.
For EACH post below, extract every stock the author expresses something about
(explicit $TICKER or company names you can confidently map to a ticker).
Classify the author's stance PER STOCK in THAT post:
- "bullish": clearly positive thesis / accumulating / praising fundamentals
- "bearish": clearly negative / warning / toxic financing / selling
- "neutral": mentioned without direction (news relay, neutral comparison, question)
Skip: indices, ETFs-as-market-proxy, crypto coins, macro-only posts (return empty mentions).
market: "US" unless clearly another exchange (e.g. Swedish SIVE -> "SE", Taiwan -> "TW").
Output ONLY a JSON array, one object per post, same order, no markdown fences:
[{"post_id":"...","mentions":[{"symbol":"NBIS","market":"US","stance":"bullish","confidence":0.9,"reason":"<=12 words"}]}]
Posts:
"""


def _log(msg: str) -> None:
    LOG_PATH.parent.mkdir(exist_ok=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} [classify] {msg}\n")


_ANTHROPIC_KEY_CACHE: str | None = None


def _anthropic_key() -> str | None:
    """讀 ANTHROPIC_API_KEY:先環境,再 .env。

    kol_classify 的 `claude -p` 子程序若沒帶 key,會退回互動登入態;一旦 CLI
    登出就回「Not logged in」而非 JSON,導致 batch parse FAIL(2026-07 起停擺 3 週)。
    .env 已有 key(app.py 等在用),把它顯式帶進子程序即可免互動登入。
    """
    global _ANTHROPIC_KEY_CACHE
    if _ANTHROPIC_KEY_CACHE is not None:
        return _ANTHROPIC_KEY_CACHE or None
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        env_path = BASE_DIR / ".env"
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
                s = line.strip()
                if s.startswith("ANTHROPIC_API_KEY") and "=" in s:
                    key = s.split("=", 1)[1].strip().strip('"').strip("'")
                    break
    _ANTHROPIC_KEY_CACHE = key
    return key or None


def _claude(prompt: str) -> str | None:
    exe = shutil.which("claude")
    if not exe:
        print("claude CLI 不存在"); sys.exit(1)
    env = os.environ.copy()
    key = _anthropic_key()
    if key:
        env["ANTHROPIC_API_KEY"] = key
    try:
        r = subprocess.run([exe, "-p", "--model", MODEL], input=prompt.encode("utf-8"),
                           capture_output=True, timeout=300, env=env)
        return r.stdout.decode("utf-8", errors="replace")
    except Exception as e:  # noqa: BLE001
        _log(f"claude call FAIL: {e}")
        return None


def _parse(out: str) -> list[dict] | None:
    m = re.search(r"\[.*\]", out, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


def enrich_meta(con: sqlite3.Connection) -> None:
    """v2:為新 symbol 補公司名+供應鏈定位標籤(一次性,快取於 symbol_meta)。"""
    con.execute("create table if not exists symbol_meta("
                "symbol text primary key, name text, industry text, enriched_at text)")
    new = [r[0] for r in con.execute(
        "select distinct m.symbol from mentions m left join symbol_meta s on s.symbol=m.symbol "
        "where s.symbol is null")]
    if not new:
        return
    print(f"補標注 {len(new)} 個 symbol 的公司名/產業層")
    for i in range(0, len(new), 25):
        batch = new[i:i + 25]
        prompt = ("For each stock ticker, give company name and its AI-supply-chain layer label "
                  "(style: 'AI Photonics/CPO Lasers', 'InP Substrates', 'Optical Modules', 'AI Chips', "
                  "'Neocloud', 'Foundry', or a fitting short label; unknown -> null). "
                  "Output ONLY JSON array, no fences: "
                  '[{"symbol":"SIVE","name":"Sivers Semiconductor","industry":"AI Photonics/CPO Lasers"}]\n'
                  "Tickers: " + ", ".join(batch))
        out = _claude(prompt)
        parsed = _parse(out) if out else None
        if not parsed:
            continue
        now = datetime.now(timezone.utc).isoformat()
        for row in parsed:
            sym = str(row.get("symbol") or "").upper()
            if sym in batch:
                con.execute("insert or replace into symbol_meta values(?,?,?,?)",
                            (sym, row.get("name"), row.get("industry"), now))
        con.commit()


def run(limit: int | None) -> None:
    con = sqlite3.connect(DB_PATH)
    try:
        con.execute("alter table posts add column classified_at text")
    except sqlite3.OperationalError:
        pass
    rows = con.execute(
        "select id, text from posts where classified_at is null order by posted_at").fetchall()
    if limit:
        rows = rows[:limit]
    if not rows:
        print("無未分類貼文"); return
    print(f"待分類 {len(rows)} 則")
    done = 0
    for i in range(0, len(rows), BATCH):
        batch = rows[i:i + BATCH]
        body = "\n".join(f'--- post_id: {pid}\n{txt[:800]}' for pid, txt in batch)
        out = _claude(PROMPT_HEAD + body)
        parsed = _parse(out) if out else None
        if parsed is None:
            out2 = _claude(PROMPT_HEAD + body)  # 重試一次
            parsed = _parse(out2) if out2 else None
        if parsed is None:
            _log(f"batch {i//BATCH} parse FAIL, skip {len(batch)} posts")
            continue
        by_id = {str(p.get("post_id")): p.get("mentions") or [] for p in parsed}
        now = datetime.now(timezone.utc).isoformat()
        for pid, _ in batch:
            mts = by_id.get(str(pid))
            if mts is None:
                continue  # 模型漏答,留待下次
            for mt in mts:
                sym = str(mt.get("symbol") or "").strip().upper()
                if not sym or len(sym) > 8:
                    continue
                con.execute(
                    "insert or ignore into mentions(post_id, symbol, market, stance, stance_source,"
                    " confidence, reason_summary, classified_at) values(?,?,?,?,'ai',?,?,?)",
                    (str(pid), sym, mt.get("market") or "US",
                     mt.get("stance") if mt.get("stance") in ("bullish", "bearish", "neutral") else "unclear",
                     mt.get("confidence"), mt.get("reason"), now))
            con.execute("update posts set classified_at=? where id=?", (now, str(pid)))
            done += 1
        con.commit()
        print(f"  批次 {i//BATCH + 1}: 累計完成 {done}/{len(rows)}")
    n = con.execute("select count(*) from mentions").fetchone()[0]
    enrich_meta(con)
    con.close()
    _log(f"classified {done} posts, mentions total {n}")
    print(f"完成 {done} 則;mentions 總數 {n}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int)
    run(ap.parse_args().limit)
