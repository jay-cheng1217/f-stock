# -*- coding: utf-8 -*-
"""產業/個股/台美連動 新聞日報(白名單制,Google News RSS)。

設計原則(2026-07-02 用戶定調):
1. **來源高度可信**:只收白名單網域(官方/一線財經媒體)。同學會、討論牆、
   部落格、聊天室一律丟棄——那類常是出貨文。
2. **日期嚴謹**:query 帶 when: 限時窗;每則解析 pubDate 並顯示,舊聞不冒充新聞。
3. 三層覆蓋:名單/watchlist 個股、六大產業主題、台美供應鏈連動。

輸出:ml/reports/industry_news_latest.md(+ dated 副本)
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlparse

import requests

BASE_DIR = Path(__file__).resolve().parents[1]
REPORT_DIR = BASE_DIR / "ml" / "reports"
STATIC_JSON = BASE_DIR / "frontend" / "static" / "entry_canonical.json"
WATCHLIST = BASE_DIR / "logs" / "entry_watchlist.json"

# 白名單:官方 + 一線財經/科技媒體(出貨文重災區一律不收:
# cmoney 同學會/投資網誌、LINE TODAY 聊天室、部落格、PTT、Dcard)
SOURCE_WHITELIST = {
    "ctee.com.tw": "工商時報",
    "money.udn.com": "經濟日報",
    "udn.com": "聯合報系",
    "cna.com.tw": "中央社",
    "cnyes.com": "鉅亨網",
    "moneydj.com": "MoneyDJ",
    "technews.tw": "科技新報",
    "digitimes.com.tw": "DIGITIMES",
    "digitimes.com": "DIGITIMES",
    "ec.ltn.com.tw": "自由財經",
    "ltn.com.tw": "自由時報",
    "businesstoday.com.tw": "今周刊",
    "cw.com.tw": "天下",
    "wealth.com.tw": "財訊",
    "reuters.com": "Reuters",
    "bloomberg.com": "Bloomberg",
    "twse.com.tw": "證交所",
    "tpex.org.tw": "櫃買中心",
    "mops.twse.com.tw": "公開資訊觀測站",
}

THEMES = [
    ("載板/PCB", "ABF載板 OR PCB報價 when:2d"),
    ("成熟製程", "成熟製程 OR 8吋晶圓 代工 when:2d"),
    ("Power IC/功率", "Power IC OR 功率元件 OR MOSFET 台廠 when:2d"),
    ("BBU/電源", "BBU OR 伺服器電源 台股 when:2d"),
    ("電力設備", "重電 OR 變壓器 台電 when:2d"),
    ("封裝封測", "封測 OR 先進封裝 CoWoS when:2d"),
]
TW_US_LINKS = [
    ("NVIDIA→AI供應鏈", "NVIDIA 台積電 OR 供應鏈 when:2d"),
    ("費半/美股半導體", "費城半導體 OR 美股半導體 台股 when:2d"),
    ("TI/電源鏈", "德州儀器 OR TI 電源 調價 when:7d"),
]


def _stock_queries() -> list[tuple[str, str, str]]:
    """回傳 (顯示標籤, query, 標題必含詞)。
    教訓(2026-07-02):兩字股名(澤米)會被 Google 模糊比對配到無關新聞
    (AV 花邊也能過白名單)——白名單只把關來源,不把關相關性。
    防線:標題必須真的含「股名」或「代號」才收。"""
    out = []
    seen = set()
    try:
        d = json.loads(STATIC_JSON.read_text(encoding="utf-8"))
        for r in d.get("rows", []):
            m = re.match(r"(\d{4})\s+(\S+)", str(r.get("stock", "")))
            if m and m.group(1) not in seen:
                seen.add(m.group(1))
                out.append((f"{m.group(1)} {m.group(2)}",
                            f"\"{m.group(2)}\" (股價 OR 營收 OR 台股 OR {m.group(1)}) when:2d",
                            m.group(2)))
    except Exception:
        pass
    try:
        for w in json.loads(WATCHLIST.read_text(encoding="utf-8")):
            tk = str(w.get("ticker", ""))
            nm = str(w.get("note", ""))[:3].strip()
            if tk and tk not in seen and nm:
                seen.add(tk)
                out.append((f"{tk} {nm}", f"\"{nm}\" (股價 OR 營收 OR 台股 OR {tk}) when:2d", nm))
    except Exception:
        pass
    return out[:20]


def fetch(query: str, must_contain: str | None = None) -> list[dict]:
    try:
        r = requests.get(
            "https://news.google.com/rss/search",
            params={"q": query, "hl": "zh-TW", "gl": "TW", "ceid": "TW:zh-Hant"},
            timeout=12,
        )
        items = re.findall(r"<item>(.*?)</item>", r.text, re.S)
    except Exception:
        return []
    out = []
    for it in items:
        t = re.search(r"<title>(.*?)</title>", it, re.S)
        d = re.search(r"<pubDate>(.*?)</pubDate>", it)
        src = re.search(r'<source url="([^"]+)"', it)
        link = re.search(r"<link>(.*?)</link>", it)
        if not (t and d and src):
            continue
        dom = urlparse(src.group(1)).netloc.replace("www.", "")
        name = None
        for wl_dom, wl_name in SOURCE_WHITELIST.items():
            if dom == wl_dom or dom.endswith("." + wl_dom):
                name = wl_name
                break
        if not name:
            continue  # 非白名單 → 丟(出貨文防線)
        try:
            dt = parsedate_to_datetime(d.group(1))
        except Exception:
            continue
        title = re.sub(r"\s*-\s*[^-]+$", "", t.group(1)).strip()  # 去尾部媒體名
        if must_contain and must_contain not in title:
            continue  # 相關性防線:標題必含股名/代號(白名單只擋來源,不擋誤配)
        out.append({"dt": dt, "src": name, "title": title,
                    "link": (link.group(1) if link else "")})
    # 去重(同標題)+ 時間新到舊
    seen, ded = set(), []
    for x in sorted(out, key=lambda x: x["dt"], reverse=True):
        k = x["title"][:30]
        if k in seen:
            continue
        seen.add(k)
        ded.append(x)
    return ded[:6]


MOPS_DIR = BASE_DIR / "新聞資料"
# 官方公告關鍵字(用法分層:警示類=否決層直接用;庫藏股=雷達;法說=行事曆)
KW_ALERT = ["持股轉讓", "股份轉讓", "私募", "現金增資", "辦理減資"]  # 收窄:裸「董事」誤標庫藏股決議
KW_RADAR = ["庫藏股", "買回"]
KW_CAL = ["法人說明會", "法說"]


def _our_tickers() -> dict[str, str]:
    out = {}
    try:
        d = json.loads(STATIC_JSON.read_text(encoding="utf-8"))
        for r in d.get("rows", []):
            m = re.match(r"(\d{4})\s+(\S+)", str(r.get("stock", "")))
            if m:
                out[m.group(1)] = m.group(2)
    except Exception:
        pass
    try:
        for w in json.loads(WATCHLIST.read_text(encoding="utf-8")):
            out.setdefault(str(w.get("ticker", "")), str(w.get("note", ""))[:3])
    except Exception:
        pass
    return out


def section_mops(lines: list) -> None:
    """零、MOPS 官方公告訊號(最高可信層;來自既有每晚公告抓取)。"""
    import pandas as pd
    from datetime import timedelta
    lines.append("## 零、MOPS 官方公告訊號(最高可信)")
    try:
        files = sorted(MOPS_DIR.glob("announcements_*.csv"))[-2:]
        df = pd.concat([pd.read_csv(f, dtype={"Ticker": str}) for f in files], ignore_index=True)
        df["Ticker"] = df["Ticker"].str.zfill(4)
        cutoff = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d")
        df = df[df["Date"].astype(str) >= cutoff]
    except Exception as exc:
        lines.append(f"(公告讀取失敗:{exc})")
        lines.append("")
        return
    ours = _our_tickers()
    hit = False
    mine = df[df["Ticker"].isin(ours)]
    if len(mine):
        hit = True
        lines.append("### 你的名單/觀察股 公告(3日內)")
        for _, r in mine.iterrows():
            tags = []
            title = str(r["Title"])
            if any(k in title for k in KW_ALERT) and "庫藏股" not in title:
                tags.append("⚠️警示")
            if any(k in title for k in KW_RADAR):
                tags.append("💰庫藏股")
            if any(k in title for k in KW_CAL):
                tags.append("📅法說")
            lines.append(f"- `{r['Date']}` **{r['Ticker']} {r['Name']}** {' '.join(tags)} {title[:60]}")
        lines.append("")
    buyback = df[df["Title"].astype(str).str.contains("|".join(KW_RADAR), na=False)].head(10)
    if len(buyback):
        hit = True
        lines.append("### 全市場 庫藏股/買回(3日內,雷達非扳機)")
        for _, r in buyback.iterrows():
            lines.append(f"- `{r['Date']}` {r['Ticker']} {r['Name']}:{str(r['Title'])[:52]}")
        lines.append("")
    concall = df[df["Title"].astype(str).str.contains("|".join(KW_CAL), na=False)]
    concall_mine = concall[concall["Ticker"].isin(ours)]
    if len(concall_mine):
        hit = True
        lines.append("### 📅 你的股票近期法說會")
        for _, r in concall_mine.iterrows():
            lines.append(f"- `{r['Date']}` {r['Ticker']} {r['Name']}:{str(r['Title'])[:55]}")
        lines.append("")
    if not hit:
        lines.append("(3日內無相關官方訊號)")
        lines.append("")


def main() -> int:
    now = datetime.now()
    lines = [f"# 產業新聞日報(白名單制) {now:%Y-%m-%d %H:%M}", "",
             f"- 來源僅限:{'、'.join(sorted(set(SOURCE_WHITELIST.values())))}",
             "- 同學會/討論牆/部落格類一律排除(出貨文防線);每則附日期與來源。", ""]

    def section(title: str, pairs: list):
        lines.append(f"## {title}")
        any_hit = False
        for pair in pairs:
            label, q = pair[0], pair[1]
            must = pair[2] if len(pair) > 2 else None
            items = fetch(q, must_contain=must)
            if not items:
                continue
            any_hit = True
            lines.append(f"### {label}")
            for x in items:
                lines.append(f"- `{x['dt']:%m-%d %H:%M}` **[{x['src']}]** {x['title']}")
            lines.append("")
        if not any_hit:
            lines.append("(時窗內無白名單來源新聞)")
            lines.append("")

    section_mops(lines)
    section("一、名單/觀察個股", _stock_queries())
    section("二、產業主題", THEMES)
    section("三、台美供應鏈連動", TW_US_LINKS)

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    latest = REPORT_DIR / "industry_news_latest.md"
    latest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    (REPORT_DIR / f"industry_news_{now:%Y%m%d}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    # web 新聞情報分頁用(免重啟 static 模式,同 entry_canonical)
    html: list[str] = []
    for ln in lines:
        if ln.startswith("### "):
            html.append(f"<h4>{ln[4:]}</h4>")
        elif ln.startswith("## "):
            html.append(f"<h3>{ln[3:]}</h3>")
        elif ln.startswith("# "):
            continue
        elif ln.startswith("- "):
            t = ln[2:]
            t = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", t)
            t = re.sub(r"`(.+?)`", r"<code>\1</code>", t)
            html.append(f"<div class='news-line'>{t}</div>")
        elif ln.strip():
            html.append(f"<div class='muted' style='font-size:12px'>{ln}</div>")
    (BASE_DIR / "frontend" / "static" / "industry_news.html").write_text(
        "\n".join(html), encoding="utf-8")
    print(f"[industry-news] 已寫 {latest.name}({len(lines)} 行)+ web html")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
