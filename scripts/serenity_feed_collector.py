# -*- coding: utf-8 -*-
"""T0 資料保全收集器 —— KOL Opinion Tracker(docs/PLAN_kol_opinion_tracker.md)。

輪詢 trackserenity.com 的公開 feed(robots.txt Allow all,無認證),以推文 id dedup
累積到本地 SQLite。該 feed 只有 rolling ~80 則,不輪詢就永久遺失 → 掛 Windows 排程 hourly。

只收集不分類(分類是 T2);失敗靜默退出等下一輪,不重試轟炸對方。

用法:
  python scripts/serenity_feed_collector.py            # 抓一次
  python scripts/serenity_feed_collector.py --stats    # 看累積狀態
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
DB_PATH = BASE_DIR / "kol_tracker.db"
LOG_PATH = BASE_DIR / "logs" / "kol_tracker.log"
FEED_URL = "https://www.trackserenity.com/data/signals.json"
TIMEOUT = 30

SCHEMA = """
create table if not exists sources(
  id integer primary key autoincrement,
  grp text not null,             -- 'serenity' / 'tw_kol'
  platform text not null,        -- 'x' / 'manual'
  username text not null,
  nickname text,
  added_at text not null,
  unique(grp, platform, username)
);
create table if not exists posts(
  id text primary key,           -- 平台原生 id(X status id)或 hash
  source_id integer not null references sources(id),
  text text not null,
  url text,
  posted_at text,                -- ISO8601 UTC
  collected_at text not null,
  raw_json text
);
create table if not exists mentions(
  id integer primary key autoincrement,
  post_id text not null references posts(id),
  symbol text not null,
  market text,                   -- 'US' / 'TW'
  stance text,                   -- bullish/bearish/neutral/unclear
  stance_source text default 'ai',
  confidence real,
  reason_summary text,
  classified_at text,
  unique(post_id, symbol)
);
"""


def _log(msg: str) -> None:
    LOG_PATH.parent.mkdir(exist_ok=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}\n")


def _parse_created(s: str) -> str:
    """X 格式 'Thu Jul 02 10:34:42 +0000 2026' → ISO8601;失敗回原字串。"""
    try:
        return datetime.strptime(s, "%a %b %d %H:%M:%S %z %Y").astimezone(timezone.utc).isoformat()
    except Exception:
        return s


def collect() -> None:
    try:
        req = urllib.request.Request(FEED_URL, headers={"User-Agent": "kol-tracker-personal/0.1"})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception as e:  # noqa: BLE001
        _log(f"fetch FAIL: {e}")
        sys.exit(0)  # 靜默等下一輪

    tweets = data.get("tweets") or []
    con = sqlite3.connect(DB_PATH)
    con.executescript(SCHEMA)
    src_rows = data.get("sources") or [{"username": "aleabitoreddit", "nickname": "Serenity"}]
    new = 0
    for s in src_rows:
        con.execute(
            "insert or ignore into sources(grp, platform, username, nickname, added_at) values('serenity','x',?,?,?)",
            (s.get("username"), s.get("nickname"), datetime.now(timezone.utc).isoformat()))
    sid = {r[0]: r[1] for r in con.execute("select username, id from sources where grp='serenity'")}
    now = datetime.now(timezone.utc).isoformat()
    for t in tweets:
        cur = con.execute(
            "insert or ignore into posts(id, source_id, text, url, posted_at, collected_at, raw_json) "
            "values(?,?,?,?,?,?,?)",
            (str(t.get("id")), sid.get(t.get("username"), 1), t.get("text") or "",
             t.get("url"), _parse_created(t.get("createdAt") or ""), now,
             json.dumps(t, ensure_ascii=False)))
        new += cur.rowcount
    con.commit()
    total = con.execute("select count(*) from posts").fetchone()[0]
    con.close()
    _log(f"feed={len(tweets)} new={new} total={total}")
    print(f"feed {len(tweets)} 則, 新增 {new}, 累積 {total}")


def stats() -> None:
    if not DB_PATH.exists():
        print("尚無資料庫")
        return
    con = sqlite3.connect(DB_PATH)
    total = con.execute("select count(*) from posts").fetchone()[0]
    rng = con.execute("select min(posted_at), max(posted_at) from posts").fetchone()
    per_day = con.execute(
        "select substr(posted_at,1,10) d, count(*) from posts group by d order by d desc limit 7").fetchall()
    con.close()
    print(f"累積 {total} 則;時間範圍 {rng[0]} ~ {rng[1]}")
    for d, c in per_day:
        print(f"  {d}: {c}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--stats", action="store_true")
    a = ap.parse_args()
    stats() if a.stats else collect()
