# -*- coding: utf-8 -*-
"""研究用:回補鉅亨網「台股新聞」分類的標題與個股標註(回測票 #31 的資料源;不進 DuckDB、不是憑證來源)。

只存 metadata:新聞 id、發布時間、標題、編輯標註的台股代號(market.code)、關鍵字。不存內文。
輸出:ml/reports/cache/cnyes_tw_stock/<YYYY-MM>.jsonl(已 gitignore),每日完成記在 _done_days.txt,可中斷續抓。
禮貌抓取:每請求間隔 >= 1.2 秒;交易日 08:30-14:30 不抓(AGENTS 紅線:盤中不啟動任何抓取)。

用法:python -X utf8 scripts/fetch_cnyes_news_history.py --start 2020-12-01 --end 2026-10-16
"""
from __future__ import annotations

import argparse
import io
import json
import random
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)
REPO = Path(__file__).resolve().parents[1]
CACHE = REPO / "ml" / "reports" / "cache" / "cnyes_tw_stock"
URL = "https://api.cnyes.com/media/api/v1/newslist/category/tw_stock"
HEADERS = {"User-Agent": "Mozilla/5.0 (research; metadata only)"}
PAUSE = 1.2


def _blocked(now: datetime) -> bool:
    sys.path.insert(0, str(REPO))
    from scripts.taiwan_trading_calendar import is_taiwan_trading_day
    return is_taiwan_trading_day(now.date()) and now.replace(hour=8, minute=30) <= now < now.replace(hour=14, minute=30)


def _codes(item: dict) -> list[str]:
    codes = set()
    for m in item.get("market") or []:
        sym = str(m.get("symbol") or "")
        code = str(m.get("code") or "")
        if sym.startswith(("TWS:", "TWG:")) and code.isdigit() and len(code) == 4:
            codes.add(code)
    for c in item.get("stock") or []:
        if str(c).isdigit() and len(str(c)) == 4:
            codes.add(str(c))
    return sorted(codes)


def fetch_day(session: requests.Session, day: date) -> list[dict]:
    start = int(datetime.combine(day, datetime.min.time()).timestamp())
    out, page = [], 1
    while True:
        for attempt in range(4):
            try:
                r = session.get(URL, params={"startAt": start, "endAt": start + 86399, "limit": 30, "page": page},
                                headers=HEADERS, timeout=45)
                if r.status_code == 200:
                    break
            except requests.RequestException:
                pass
            time.sleep(10 * (attempt + 1))
        else:
            raise RuntimeError(f"cnyes fetch failed for {day} page {page}")
        items = r.json().get("items") or {}
        for it in items.get("data") or []:
            out.append({"id": it.get("newsId"), "publishAt": it.get("publishAt"), "title": it.get("title"),
                        "codes": _codes(it), "keyword": it.get("keyword") or []})
        if page >= int(items.get("last_page") or page):
            return out
        page += 1
        time.sleep(PAUSE + random.uniform(0, 0.3))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2020-12-01")
    ap.add_argument("--end", default=date.today().isoformat())
    args = ap.parse_args()
    CACHE.mkdir(parents=True, exist_ok=True)
    done_path = CACHE / "_done_days.txt"
    done = set(done_path.read_text(encoding="utf-8").split()) if done_path.exists() else set()
    session = requests.Session()
    day, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
    n_days = n_items = 0
    while day <= end:
        if day.isoformat() not in done:
            if _blocked(datetime.now()):
                print(f"[cnyes] {datetime.now():%H:%M} 交易時段禁抓,停止;下次從 {day} 續抓")
                return 0
            rows = fetch_day(session, day)
            with (CACHE / f"{day:%Y-%m}.jsonl").open("a", encoding="utf-8") as fh:
                for row in rows:
                    fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            with done_path.open("a", encoding="utf-8") as fh:
                fh.write(day.isoformat() + "\n")
            n_days += 1
            n_items += len(rows)
            if n_days % 60 == 0:
                print(f"[cnyes] {datetime.now():%H:%M} 到 {day},本輪 {n_days} 天 / {n_items} 篇")
            time.sleep(PAUSE + random.uniform(0, 0.3))
        day += timedelta(days=1)
    print(f"[cnyes] 完成:本輪 {n_days} 天 / {n_items} 篇")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
