# -*- coding: utf-8 -*-
"""T3 KOL 觀點 dashboard 產生器 v2(docs/PLAN_kol_opinion_tracker.md)。

讀 kol_tracker.db → 自包含 HTML,UI/UX 參照 Serenity Watch demo(2026-07-02 逐幀分析):
- Daily:most-discussed 卡片(vs prior close+stance badge)+ notable/new/other
- Weekly/Monthly:排名列表(stance mix 比例條+Gain+NEW badge)+ 期間新標的卡片
- Quarterly:方向統計 tile + 綠紅比例條 + 全名單可排序表(產業層/首末提及價/Gain,≥3 mentions)
- 個股詳情頁:頭部統計 + 價格線與提及日圓點(SVG) + Bull case/Risks 雙欄 + All posts 緊湊列表

價格:Yahoo chart API 日線快取於 price_history 表(US/.ST/.TW best-effort,失敗略過);
Gain = 首次追蹤提及日收盤 → 最新收盤(注意:是「本資料集首次提及」,非 KOL 生涯首提)。

用法:
  python scripts/kol_dashboard.py               # 產出 ml/reports/kol_dashboard_latest.html
  python scripts/kol_dashboard.py --no-fetch    # 跳過報價/歷史價抓取(離線快速重產)
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

BASE_DIR = Path(__file__).resolve().parents[1]
DB_PATH = BASE_DIR / "kol_tracker.db"
OUT = BASE_DIR / "ml" / "reports" / "kol_dashboard_latest.html"
ET = ZoneInfo("America/New_York")
QUOTE_URL = "https://www.trackserenity.com/api/stocks?symbols={}"
YAHOO_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{}?range=1y&interval=1d"
UA = {"User-Agent": "Mozilla/5.0 (kol-tracker-personal)"}
MKT_SUFFIX = {"US": "", "SE": ".ST", "TW": ".TW", "KR": ".KQ", "KQ": ".KQ", "EUR": ".PA", "GB": ".L"}


def load_mentions(con) -> list[dict]:
    posts = {r[0]: {"text": r[1], "url": r[2], "posted_at": r[3]}
             for r in con.execute("select id, text, url, posted_at from posts")}
    mentions = []
    for r in con.execute("select post_id, symbol, market, stance, reason_summary from mentions"):
        p = posts.get(r[0], {})
        try:
            et_date = datetime.fromisoformat(p.get("posted_at", "")).astimezone(ET).strftime("%Y-%m-%d")
        except Exception:
            et_date = (p.get("posted_at") or "")[:10]
        mentions.append({"post_id": r[0], "symbol": r[1], "market": r[2] or "US",
                         "stance": r[3], "reason": r[4] or "", "et_date": et_date,
                         "posted_at": p.get("posted_at", ""), "url": p.get("url", ""),
                         "snippet": (p.get("text") or "")[:220]})
    return mentions


def fetch_quotes(symbols: list[str]) -> dict:
    out = {}
    for i in range(0, len(symbols), 20):
        try:
            req = urllib.request.Request(QUOTE_URL.format(",".join(symbols[i:i + 20])), headers=UA)
            with urllib.request.urlopen(req, timeout=15) as r:
                q = json.loads(r.read().decode("utf-8")).get("quotes") or {}
            for sym, v in q.items():
                if v and v.get("price") is not None:
                    out[sym] = {"price": v["price"], "chg": v.get("changePercent"),
                                "ccy": v.get("currency", "USD")}
        except Exception:
            pass
    return out


def update_prices(con, sym_market: dict[str, str]) -> None:
    """Yahoo 日線 → price_history 快取(best-effort,單檔失敗不擋)。"""
    con.execute("create table if not exists price_history("
                "symbol text, date text, close real, primary key(symbol, date))")
    for sym, mkt in sym_market.items():
        ysym = sym + MKT_SUFFIX.get(mkt, "")
        try:
            req = urllib.request.Request(YAHOO_URL.format(ysym), headers=UA)
            with urllib.request.urlopen(req, timeout=15) as r:
                d = json.loads(r.read().decode("utf-8"))
            res = d["chart"]["result"][0]
            ts, cl = res["timestamp"], res["indicators"]["quote"][0]["close"]
            rows = [(sym, datetime.fromtimestamp(t, tz=ET).strftime("%Y-%m-%d"), c)
                    for t, c in zip(ts, cl) if c is not None]
            con.executemany("insert or replace into price_history values(?,?,?)", rows)
            con.commit()
        except Exception:
            pass
        time.sleep(0.15)


def build_payload(no_fetch: bool) -> dict:
    con = sqlite3.connect(DB_PATH)
    mentions = load_mentions(con)
    symbols = sorted({m["symbol"] for m in mentions})
    sym_market = {}
    for m in mentions:
        sym_market.setdefault(m["symbol"], m["market"])
    first_mention = {}
    for m in mentions:
        d = first_mention.get(m["symbol"])
        if d is None or m["et_date"] < d:
            first_mention[m["symbol"]] = m["et_date"]

    quotes = {} if no_fetch else fetch_quotes(symbols)
    if not no_fetch:
        update_prices(con, sym_market)

    # 價格序列(首提前 7 天起)與 Gain(首提日收盤 → 最新收盤)
    prices, gains = {}, {}
    has_ph = con.execute(
        "select count(*) from sqlite_master where name='price_history'").fetchone()[0]
    if has_ph:
        for sym in symbols:
            fm = first_mention[sym]
            rows = con.execute(
                "select date, close from price_history where symbol=? and date>=date(?,'-7 day') "
                "order by date", (sym, fm)).fetchall()
            if not rows:
                continue
            prices[sym] = [[d, round(c, 3)] for d, c in rows]
            base = next((c for d, c in rows if d >= fm), None)
            if base:
                gains[sym] = {"gain": rows[-1][1] / base - 1,
                              "first_price": round(base, 3), "last_price": round(rows[-1][1], 3)}

    meta = {}
    if con.execute("select count(*) from sqlite_master where name='symbol_meta'").fetchone()[0]:
        meta = {r[0]: {"name": r[1], "industry": r[2]}
                for r in con.execute("select symbol, name, industry from symbol_meta")}
    con.close()

    now_utc = datetime.now(timezone.utc)
    return {"mentions": mentions, "quotes": quotes, "prices": prices, "gains": gains, "meta": meta,
            "latest_et": max((m["et_date"] for m in mentions), default=""),
            "first_collect": min((m["et_date"] for m in mentions), default=""),
            "updated_et": now_utc.astimezone(ET).strftime("%Y-%m-%d %H:%M"),
            "updated_local": datetime.now().strftime("%Y-%m-%d %H:%M")}


HTML = """<!doctype html>
<html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>KOL Watch — Serenity</title>
<style>
:root{
  --page:#f2efe6; --card:#fbfaf4; --ink:#22211d; --ink2:#5b594f; --muted:#8b887c;
  --line:#e3e0d3; --pill:#1f4433; --pill-ink:#f2efe6;
  --bull:#2f7d4f; --bull-wash:#e7f0e7; --bear:#b0392e; --bear-wash:#f7e8e5;
  --neu:#7a7668; --neu-wash:#eeebe0; --track:#e6e3d6;
}
*{box-sizing:border-box}
body{margin:0;background:var(--page);color:var(--ink);
  font:16px/1.55 "Segoe UI",system-ui,sans-serif;padding:28px 4vw 60px}
.mono{font-family:Consolas,Menlo,monospace}
h1{font-family:Georgia,"Times New Roman",serif;font-weight:800;font-size:38px;
  display:inline-block;margin:0 10px 0 0}
.pill{display:inline-block;background:var(--pill);color:var(--pill-ink);
  border-radius:6px;padding:4px 14px;font-family:Consolas,monospace;font-size:17px;
  font-weight:700;vertical-align:6px}
.meta{color:var(--ink2);font-family:Consolas,monospace;font-size:13.5px;margin:8px 0 4px}
.meta .tag{background:var(--neu-wash);border-radius:4px;padding:2px 8px;margin-right:6px}
.tabs{margin:18px 0 22px;border-bottom:2px solid var(--ink)}
.tabs button{background:none;border:none;cursor:pointer;font-family:Georgia,serif;
  font-size:22px;font-weight:700;color:var(--muted);padding:8px 20px 10px}
.tabs button.on{color:var(--ink);border-bottom:3px solid var(--pill);margin-bottom:-2px}
.sec{color:var(--ink2);font-family:Consolas,monospace;font-size:14px;
  letter-spacing:.02em;margin:26px 0 12px;border-bottom:1px solid var(--line);padding-bottom:6px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(310px,1fr));gap:14px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;
  padding:16px 18px;box-shadow:0 1px 2px rgba(34,33,29,.05);border-left:3px solid var(--line)}
.card.c-bull{border-left-color:var(--bull)} .card.c-bear{border-left-color:var(--bear)}
.card .tk{font-size:26px;font-weight:800;letter-spacing:.01em;cursor:pointer}
.card .mkt{font-size:12px;background:var(--neu-wash);border:1px solid var(--line);
  border-radius:4px;padding:1px 5px;vertical-align:3px;margin-left:5px;color:var(--ink2)}
.badge{float:right;font-size:14px;font-weight:700;border-radius:6px;padding:4px 10px}
.b-bull{color:var(--bull);background:var(--bull-wash)}
.b-bear{color:var(--bear);background:var(--bear-wash)}
.b-neu{color:var(--neu);background:var(--neu-wash)}
.px{font-size:13px;font-family:Consolas,monospace;border-radius:4px;padding:1px 7px;margin-left:8px}
.px.up{color:var(--bull);background:var(--bull-wash)}
.px.dn{color:var(--bear);background:var(--bear-wash)}
.px.na{color:var(--muted);background:var(--neu-wash)}
.pxlbl{color:var(--muted);font-family:Consolas,monospace;font-size:12.5px;margin-left:8px}
.big{font-size:36px;font-weight:800;margin-right:6px}
.sub{color:var(--ink2);font-family:Consolas,monospace;font-size:16px}
.mix{color:var(--ink2);font-family:Consolas,monospace;font-size:15px;margin:6px 0 2px}
.mix b.bl{color:var(--bull)} .mix b.br{color:var(--bear)}
.first{color:var(--muted);font-family:Consolas,monospace;font-size:14.5px;margin-top:8px}
.det{float:right;font-family:Consolas,monospace;font-size:13.5px;color:var(--pill);
  cursor:pointer;text-decoration:none;font-weight:700}
.bar{height:8px;border-radius:4px;background:var(--track);overflow:hidden;
  display:flex;margin:8px 0 4px}
.bar i{display:block;height:100%}
.bar .fb{background:var(--bull);border-right:2px solid var(--card)}
.bar .fr{background:var(--bear)}
.chips{display:flex;flex-wrap:wrap;gap:8px}
.chip{background:var(--card);border:1px solid var(--line);border-radius:6px;
  padding:5px 12px;font-family:Consolas,monospace;font-size:13.5px;cursor:pointer}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:14px;margin:6px 0 14px}
.tile{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px;text-align:center}
.tile .v{font-family:Georgia,serif;font-size:44px;font-weight:800}
.tile .k{color:var(--ink2);font-size:13.5px;margin-top:2px}
.note{color:var(--muted);font-size:13px;margin:6px 0 18px}
.note b.g{color:var(--bull)} .note b.r{color:var(--bear)}
table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);
  border-radius:10px;overflow:hidden;font-variant-numeric:tabular-nums}
th{font-family:Consolas,monospace;font-size:13px;color:var(--ink2);text-align:left;
  padding:10px 12px;border-bottom:2px solid var(--line);cursor:pointer;user-select:none;white-space:nowrap}
td{padding:9px 12px;border-bottom:1px solid var(--line);font-size:14.5px;vertical-align:top}
td .sub2{color:var(--muted);font-family:Consolas,monospace;font-size:12px}
tr:hover td{background:var(--neu-wash)}
.tblwrap{overflow-x:auto}
.empty{background:var(--card);border:1px solid var(--line);border-radius:10px;
  padding:16px;color:var(--muted);font-size:14.5px}
/* 排名列表(weekly/monthly) */
.rank{background:var(--card);border:1px solid var(--line);border-radius:10px;overflow:hidden}
.rrow{display:grid;grid-template-columns:44px 160px 1fr 130px 120px 80px;gap:10px;
  align-items:center;padding:12px 16px;border-bottom:1px solid var(--line)}
.rrow:last-child{border-bottom:none}
.rrow:hover{background:var(--neu-wash)}
.rrow .no{font-family:Georgia,serif;font-size:20px;font-weight:700;color:var(--ink2)}
.rrow .sym{font-weight:800;font-size:18px;cursor:pointer}
.rrow .cnt{font-family:Consolas,monospace;font-size:14px;white-space:nowrap}
.rrow .mixs{font-family:Consolas,monospace;font-size:12.5px;color:var(--ink2);margin-bottom:4px}
.newb{background:var(--pill);color:var(--pill-ink);font-size:10.5px;border-radius:3px;
  padding:1px 5px;font-family:Consolas,monospace;vertical-align:2px;margin-left:6px}
.gain{font-family:Consolas,monospace;font-size:13.5px;font-weight:700;border-radius:4px;padding:2px 8px}
.gain.up{color:var(--bull);background:var(--bull-wash)}
.gain.dn{color:var(--bear);background:var(--bear-wash)}
.gain.na{color:var(--muted);background:var(--neu-wash)}
.dbtn{font-family:Consolas,monospace;font-size:12.5px;color:var(--pill);border:1px solid var(--line);
  background:var(--page);border-radius:5px;padding:4px 10px;cursor:pointer;font-weight:700}
/* 個股詳情 */
#overlay{position:fixed;inset:0;background:rgba(34,33,29,.45);display:none;z-index:9}
#panel{position:fixed;top:2vh;left:50%;transform:translateX(-50%);width:min(1100px,96vw);
  max-height:95vh;overflow-y:auto;background:var(--page);border-radius:12px;
  padding:22px 30px;display:none;z-index:10;box-shadow:0 12px 40px rgba(0,0,0,.25)}
.back{background:var(--ink);color:var(--page);border:none;border-radius:6px;
  padding:6px 14px;font-family:Consolas,monospace;font-size:13px;cursor:pointer;font-weight:700}
.crumb{color:var(--muted);font-size:13px;margin-left:10px}
.dhead{display:flex;justify-content:space-between;flex-wrap:wrap;gap:14px;
  margin:14px 0 4px;border-bottom:2px solid var(--ink);padding-bottom:14px}
.dstats{text-align:right;font-family:Consolas,monospace;font-size:13.5px;color:var(--ink2);line-height:1.9}
.dstats b{color:var(--ink)}
.company{color:var(--ink2);font-size:15px;margin-top:-2px}
.cols{display:grid;grid-template-columns:1fr 1fr;gap:16px}
@media (max-width:820px){.cols{grid-template-columns:1fr}
  .rrow{grid-template-columns:34px 110px 1fr 90px;row-gap:6px}}
.opbox{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px}
.opbox.bull{border-top:3px solid var(--bull)} .opbox.bear{border-top:3px solid var(--bear)}
.opbox h3{margin:0 0 10px;font-family:Georgia,serif;font-size:18px}
.opbox h3 .nf{float:right;color:var(--muted);font-size:12px;font-family:Consolas,monospace;font-weight:400}
.op{display:flex;justify-content:space-between;gap:10px;padding:7px 0;border-bottom:1px dashed var(--line);font-size:14.5px}
.op:last-child{border-bottom:none}
.op .dt{color:var(--muted);font-family:Consolas,monospace;font-size:12.5px;white-space:nowrap}
.op a{color:var(--pill);text-decoration:none}
.chart{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px;margin:14px 0}
.leg{color:var(--muted);font-size:12.5px;font-family:Consolas,monospace;margin-top:6px}
.leg .dot{display:inline-block;width:9px;height:9px;border-radius:50%;vertical-align:-1px;margin:0 4px 0 10px}
.pwrap{position:relative}
.pwrap .kx{position:absolute;top:0;bottom:0;width:0;border-left:1px dashed var(--muted);
  display:none;pointer-events:none}
.pwrap .ktip{position:absolute;top:8px;display:none;background:var(--card);border:1px solid var(--line);
  border-radius:6px;padding:6px 10px;font-family:Consolas,monospace;font-size:12.5px;line-height:1.7;
  pointer-events:none;z-index:5;white-space:nowrap;color:var(--muted)}
.pwrap .ktip b{color:var(--ink)}
/* All posts 緊湊列表(照 demo) */
.aphead{margin:6px 0 0}
.appill{display:inline-block;background:var(--ink);color:var(--page);border-radius:14px;
  padding:4px 14px;font-family:Consolas,monospace;font-size:13.5px;font-weight:700}
.aphint{float:right;color:var(--muted);font-size:12.5px;font-family:Consolas,monospace;margin-top:8px}
.post{display:grid;grid-template-columns:92px 92px 1fr;gap:12px;padding:12px 4px;
  border-bottom:1px solid var(--line);font-size:14.5px;align-items:start}
.post .d{font-family:Consolas,monospace;font-size:12.5px;color:var(--muted);padding-top:2px}
.post .st{font-family:Consolas,monospace;font-size:12px;font-weight:700;border-radius:4px;
  padding:2px 8px;text-align:center;white-space:nowrap}
.post .tx a{color:var(--pill);text-decoration:none;font-family:Consolas,monospace}
.shift{background:#eef2f9;color:#3a5b8c;border-radius:5px;padding:2px 8px;
  font-size:13px;font-family:Consolas,monospace;margin-left:8px}
footer{color:var(--muted);font-size:12.5px;margin-top:40px;font-family:Consolas,monospace}
</style></head><body>
<div id="hdr"></div>
<div class="tabs" id="tabs"></div>
<div id="view"></div>
<div id="overlay" data-close="1"></div>
<div id="panel"></div>
<footer>KOL Watch(自用研究工具)· stance 為 AI 推斷可能有誤,每則附原文連結請自行驗證 ·
Gain 基準為「本資料集首次追蹤提及日」非 KOL 生涯首提 · 不構成投資建議 · __GENERATED__</footer>
<script id="data" type="application/json">__DATA__</script>
<script>
const DATA = JSON.parse(document.getElementById('data').textContent);
const M = DATA.mentions, QUOTES = DATA.quotes, PRICES = DATA.prices,
      GAINS = DATA.gains, META = DATA.meta, LATEST = DATA.latest_et;
const WINDOWS = {daily:1, weekly:7, monthly:28, quarterly:90};
const TITLES = {daily:'Daily', weekly:'Weekly', monthly:'Monthly', quarterly:'Quarterly'};
let cur = 'daily';
function daysAgo(d, n){ const t = new Date(d+'T00:00:00'); t.setDate(t.getDate()-n+1);
  return t.toISOString().slice(0,10); }
function inWin(m, w){ return m.et_date >= daysAgo(LATEST, WINDOWS[w]) && m.et_date <= LATEST; }
function agg(list){
  const by = {};
  for(const m of list){
    const k = m.symbol; by[k] = by[k] || {symbol:k, market:m.market, n:0, bull:0, bear:0, neu:0};
    by[k].n++;
    if(m.stance==='bullish') by[k].bull++; else if(m.stance==='bearish') by[k].bear++; else by[k].neu++;
  }
  for(const k in by){
    const s = by[k];
    s.stance = s.bull>s.bear ? 'bullish' : s.bear>s.bull ? 'bearish' : (s.bull+s.bear>0?'mixed':'neutral');
    const all = M.filter(x=>x.symbol===k).map(x=>x.et_date).sort();
    s.first = all[0]; s.latest = all[all.length-1]; s.total = all.length;
    s.n7 = M.filter(x=>x.symbol===k && inWin(x,'weekly')).length;
    s.n28 = M.filter(x=>x.symbol===k && inWin(x,'monthly')).length;
  }
  return Object.values(by).sort((a,b)=>b.n-a.n);
}
function badge(st, suffix){
  const map = {bullish:['▲ Bullish','b-bull'], bearish:['▼ Bearish','b-bear'],
               mixed:['◆ Mixed','b-neu'], neutral:['● Neutral','b-neu']};
  const [t,c] = map[st] || ['● No stance','b-neu'];
  return `<span class="badge ${c}">${t}${suffix?' · '+suffix:''}</span>`;
}
function pxpill(sym, lbl){
  const q = QUOTES[sym];
  const pre = lbl ? '<span class="pxlbl">vs prior close</span>' : '';
  if(!q || q.chg==null) return pre+'<span class="px na">n/a</span>';
  const c = q.chg;
  return `${pre}<span class="px ${c>=0?'up':'dn'}">${c>=0?'+':''}${c.toFixed(1)}%</span>`;
}
function gainpill(sym){
  const g = GAINS[sym];
  if(!g) return '<span class="gain na">Gain n/a</span>';
  const v = g.gain*100;
  return `<span class="gain ${v>=0?'up':'dn'}">Gain ${v>=0?'+':''}${v.toFixed(1)}%</span>`;
}
function ratio(s){
  const tot = Math.max(s.n,1), b = s.bull/tot*100, r = s.bear/tot*100;
  return `<i class="fb" style="width:${b}%"></i><i class="fr" style="width:${r}%"></i>`;
}
function card(s, w){
  const lbl = {daily:'today', weekly:'this week', monthly:'this month', quarterly:'this quarter'}[w];
  const cls = s.stance==='bullish'?'c-bull':s.stance==='bearish'?'c-bear':'';
  return `<div class="card ${cls}">
    ${badge(s.stance, lbl)}
    <span class="tk" data-sym="${s.symbol}">${s.symbol}</span>${s.market!=='US'?`<span class="mkt">${s.market}</span>`:''}
    ${pxpill(s.symbol, true)}
    <div style="margin-top:10px"><span class="big">${s.n}</span>
      <span class="sub">mentions · 7d ${s.n7} · 28d ${s.n28}</span></div>
    <div class="mix"><b class="bl">${s.bull} bull</b> · <b class="br">${s.bear} bear</b> · ${s.neu} neu</div>
    <div class="bar">${ratio(s)}</div>
    <div class="first">First mention ${s.first}
      <a class="det" data-sym="${s.symbol}">Detail →</a></div>
  </div>`;
}
function stanceShifts(){
  const out = [];
  for(const s of agg(M.filter(m=>m.et_date===LATEST))){
    const prev = M.filter(m=>m.symbol===s.symbol && m.et_date<LATEST && m.stance!=='neutral')
                  .sort((a,b)=>a.et_date<b.et_date?1:-1);
    if(!prev.length) continue;
    const p = prev[0].stance;
    if((s.stance==='bullish'||s.stance==='bearish') && p!==s.stance && (p==='bullish'||p==='bearish'))
      out.push({sym:s.symbol, from:p, to:s.stance});
  }
  return out;
}
function rankList(stocks, w){
  const lbl = w==='weekly' ? 'this week' : 'this month';
  const winStart = daysAgo(LATEST, WINDOWS[w]);
  let h = '<div class="rank">';
  stocks.slice(0,12).forEach((s,i)=>{
    const isNew = s.first >= winStart;
    h += `<div class="rrow">
      <div class="no">${i+1}</div>
      <div><span class="sym" data-sym="${s.symbol}">${s.symbol}</span>${isNew?'<span class="newb">NEW</span>':''}
        ${s.market!=='US'?`<span class="mkt">${s.market}</span>`:''}</div>
      <div><div class="mixs">${s.bull} bull · ${s.bear} bear · ${s.neu} neu</div>
        <div class="bar" style="margin:0">${ratio(s)}</div></div>
      <div class="cnt"><b>${s.n}</b> ${lbl}</div>
      <div>${gainpill(s.symbol)}</div>
      <div><button class="dbtn" data-sym="${s.symbol}">Detail</button></div>
    </div>`;
  });
  return h + '</div>';
}
function render(){
  const w = cur, list = M.filter(m=>inWin(m,w)), stocks = agg(list);
  const names = stocks.length, cnt = list.length;
  const rangeTxt = w==='daily' ? LATEST : daysAgo(LATEST,WINDOWS[w])+' ~ '+LATEST;
  document.getElementById('hdr').innerHTML =
    `<h1>${TITLES[w]}</h1><span class="pill">${rangeTxt} ET</span>
     <div class="meta"><span class="tag">${w==='daily'?'today':'last '+WINDOWS[w]+'d'} · ${names} names · ${cnt} mentions</span>
     <span class="tag">Updated ET ${DATA.updated_et} · Your local: ${DATA.updated_local}</span></div>`;
  let h = '';
  if(!cnt){ h = '<div class="empty">此期間無資料(收集器 '+DATA.first_collect+' 才開始累積)</div>'; }
  else if(w==='quarterly'){ h = quarterView(stocks, cnt); }
  else if(w==='daily'){
    const top = stocks.filter(s=>s.stance!=='neutral').slice(0,6);
    const rest = stocks.filter(s=>!top.includes(s));
    h += `<div class="sec">▼ Most-discussed today (by mentions today) | Stance = today stance; rolling window; counts are per-window</div>`;
    h += `<div class="grid">${top.map(s=>card(s,w)).join('')}</div>`;
    const bears = stocks.filter(s=>s.stance==='bearish'), shifts = stanceShifts();
    h += `<div class="sec">▼ Notable today (bearish / stance shift)</div>`;
    if(bears.length || shifts.length){
      h += `<div class="grid">${bears.map(s=>card(s,w)).join('')}</div>`;
      h += shifts.map(x=>`<p><span class="chip" data-sym="${x.sym}">${x.sym}</span>
        <span class="shift">Stance shift: ${x.from==='bullish'?'Bullish→Bearish':'Bearish→Bullish'}</span></p>`).join('');
    } else h += '<div class="empty">No bearish or stance-shift names this period.</div>';
    const news = stocks.filter(s=>s.first===LATEST);
    h += `<div class="sec">▼ New today</div>`;
    h += news.length ? `<div class="empty" style="color:var(--ink)"><b>${news.length}</b> first appeared today (tap for detail):<br><br>
        <span class="chips">${news.map(s=>`<span class="chip" data-sym="${s.symbol}">${s.symbol}·${s.n}</span>`).join('')}</span></div>`
                     : '<div class="empty">0 first appeared today</div>';
    if(rest.length){
      h += `<div class="sec">▼ Other mentions</div>
        <div class="empty" style="color:var(--ink)"><b>${rest.length}</b> more mentioned today, ongoing or background (tap for detail):<br><br>
        <span class="chips">${rest.map(s=>`<span class="chip" data-sym="${s.symbol}">${s.symbol}·${s.n}</span>`).join('')}</span></div>`;
    }
  } else {
    h += `<div class="sec">▼ Most-discussed ${w==='weekly'?'this week (by mentions in last 7d)':'this month (by 28d mentions)'} & stance mix | Stance = ${w==='weekly'?'this week':'this month'} stance; rolling window; counts are per-window</div>`;
    h += rankList(stocks, w);
    h += `<div class="note">NEW = first appeared this ${w==='weekly'?'week':'month'} · Bar = stance mix (<b class="g">▲bull</b> / <b class="r">▼bear</b> / ●neu) · Gain = since first TRACKED mention</div>`;
    const winStart = daysAgo(LATEST, WINDOWS[w]);
    const minN = w==='weekly' ? 2 : 3;
    const news = stocks.filter(s=>s.first>=winStart && s.n>=minN);
    h += `<div class="sec">▼ New names this ${w==='weekly'?'week':'month'} (first appearance & ≥${minN} mentions)</div>`;
    h += news.length ? `<div class="grid">${news.map(s=>card(s,w)).join('')}</div>`
                     : '<div class="empty">None this period.</div>';
  }
  document.getElementById('view').innerHTML = h;
}
function quarterView(stocks, cnt){
  const nb = stocks.filter(s=>s.bull>s.bear).length, nr = stocks.filter(s=>s.bear>s.bull).length;
  const bearOnly = stocks.filter(s=>s.bear>0 && s.bull===0).length;
  const bal = stocks.filter(s=>s.bull===s.bear && s.bull>0).length;
  const withS = nb+nr+bal;
  const tb = stocks.reduce((a,s)=>a+s.bull,0), tr = stocks.reduce((a,s)=>a+s.bear,0),
        tn = stocks.reduce((a,s)=>a+s.neu,0);
  const shown = stocks.filter(s=>s.n>=3);
  let h = `<div class="sec">▼ Quarter direction (by # of names, not # of stances)</div>
  <div class="tiles">
    <div class="tile"><div class="v">${nb}</div><div class="k">Net-bullish names</div></div>
    <div class="tile"><div class="v">${nr}</div><div class="k">Net-bearish names</div></div>
    <div class="tile"><div class="v">${bal}</div><div class="k">Balanced</div></div>
    <div class="tile"><div class="v">${withS}</div><div class="k">With stance</div></div>
  </div>
  <div class="bar" style="height:14px;max-width:900px">
    <i class="fb" style="width:${withS?nb/withS*100:0}%"></i><i class="fr" style="width:${withS?nr/withS*100:0}%"></i></div>
  <div class="note">By # of names: net-bullish <b class="g">${withS?Math.round(nb/withS*100):0}%</b> ·
   net-bearish <b class="r">${withS?Math.round(nr/withS*100):0}%</b> (of which ${bearOnly} bearish-only) |
   total stances — bull ${tb} / bear ${tr} / neutral ${tn} (counts skew to a few high-frequency names, so direction is judged by # of names)<br>
   Methodology: bullish/bearish opinions the account expressed in posts (stances) — NOT actual holdings.</div>
  <div class="sec">▼ All-names table (quarter) | Tap Gain / Mentions / Bull / Bear / Neutral headers to sort;
   ≥3 mentions in 90d (${shown.length} names); blank industry (—) = unclassified</div>
  <div class="tblwrap"><table id="qt"><thead><tr>
    <th data-sort="0" data-str="1">Ticker</th><th data-sort="1" data-str="1">Industry</th>
    <th data-sort="2" data-str="1">First mention</th><th data-sort="3" data-str="1">Latest mention</th>
    <th data-sort="4" data-str="0">Gain</th><th data-sort="5" data-str="0">Mentions</th>
    <th data-sort="6" data-str="0">Bull</th><th data-sort="7" data-str="0">Bear</th><th data-sort="8" data-str="0">Neutral</th>
    </tr></thead><tbody>`;
  for(const s of shown){
    const g = GAINS[s.symbol], mt = META[s.symbol]||{}, q = QUOTES[s.symbol];
    const ccy = q&&q.ccy!=='USD' ? q.ccy+' ' : '';
    h += `<tr data-sym="${s.symbol}" style="cursor:pointer">
     <td><b>${s.symbol}</b>${s.market!=='US'?' <span class="mkt">'+s.market+'</span>':''}</td>
     <td style="color:var(--ink2)">${mt.industry||'—'}</td>
     <td class="mono">${s.first}<div class="sub2">${g?ccy+g.first_price:''}</div></td>
     <td class="mono">${s.latest}<div class="sub2">${g?ccy+g.last_price:''}</div></td>
     <td>${g?`<span class="gain ${g.gain>=0?'up':'dn'}">${g.gain>=0?'+':''}${(g.gain*100).toFixed(1)}%</span>`:'<span class="gain na">—</span>'}</td>
     <td><b>${s.n}</b></td><td style="color:var(--bull);font-weight:700">${s.bull}</td>
     <td style="color:var(--bear);font-weight:700">${s.bear}</td><td>${s.neu}</td></tr>`;
  }
  return h + '</tbody></table></div>';
}
let sortState = {};
function sortT(col, isStr){
  const tb = document.querySelector('#qt tbody');
  const rows = [...tb.rows];
  const dir = sortState[col] = !(sortState[col]);
  rows.sort((a,b)=>{
    let x = a.cells[col].innerText.trim(), y = b.cells[col].innerText.trim();
    if(!isStr){ x = parseFloat(x.replace(/[%+,—]/g,''))||0; y = parseFloat(y.replace(/[%+,—]/g,''))||0; }
    return (x<y?-1:x>y?1:0) * (dir?-1:1);
  });
  rows.forEach(r=>tb.appendChild(r));
}
function svgChart(sym){
  const pr = PRICES[sym];
  if(!pr || pr.length < 2) return '<div class="empty">無價格資料(來源缺此標的或非支援市場)</div>';
  const W=1000, H=260, P=24;
  const dates = pr.map(p=>p[0]), vals = pr.map(p=>p[1]);
  const mn = Math.min(...vals), mx = Math.max(...vals), rng = (mx-mn)||1;
  const x = i => P + i*(W-2*P)/(pr.length-1);
  const y = v => H-P - (v-mn)/rng*(H-2*P);
  let path = pr.map((p,i)=>(i?'L':'M')+x(i).toFixed(1)+' '+y(p[1]).toFixed(1)).join('');
  const byDay = {};
  for(const m of M.filter(m=>m.symbol===sym)){
    byDay[m.et_date] = byDay[m.et_date] || {b:0,r:0,n:0};
    if(m.stance==='bullish') byDay[m.et_date].b++;
    else if(m.stance==='bearish') byDay[m.et_date].r++;
    else byDay[m.et_date].n++;
  }
  let dots = '';
  for(const d in byDay){
    let idx = dates.findIndex(dd=>dd>=d);
    if(idx===-1) idx = dates.length-1;
    const c = byDay[d];
    const col = c.b>c.r ? 'var(--bull)' : c.r>c.b ? 'var(--bear)' : 'var(--neu)';
    dots += `<circle cx="${x(idx).toFixed(1)}" cy="${y(vals[idx]).toFixed(1)}" r="4.5"
      fill="${col}" stroke="var(--card)" stroke-width="1.5">
      <title>${d} · ${c.b+c.r+c.n} mentions (${c.b} bull/${c.r} bear/${c.n} neu) · close ${vals[idx]}</title></circle>`;
  }
  const area = path + `L${x(pr.length-1).toFixed(1)} ${H-P}L${x(0).toFixed(1)} ${H-P}Z`;
  /* 格線+價格刻度 */
  let grid = '';
  for(const t of [0, 0.25, 0.5, 0.75, 1]){
    const pv = mn + rng*t, py = y(pv);
    grid += `<line x1="${P}" y1="${py}" x2="${W-P}" y2="${py}" stroke="var(--line)" stroke-width="1" opacity="${t===0||t===1?0:.5}"/>
      <text x="${W-P+3}" y="${py+4}" font-size="10.5" fill="var(--muted)" font-family="Consolas,monospace">${pv.toFixed(rng<5?2:rng<100?1:0)}</text>`;
  }
  /* 高低點+最新價標記 */
  let iH2=0, iL2=0;
  vals.forEach((v,i)=>{ if(v>vals[iH2]) iH2=i; if(v<vals[iL2]) iL2=i; });
  const lastV = vals[vals.length-1];
  const mark = `<circle cx="${x(vals.length-1)}" cy="${y(lastV)}" r="3.5" fill="var(--ink)"/>
    <text x="${Math.min(x(vals.length-1), W-P-4)}" y="${Math.max(14, y(lastV)-9)}" font-size="12.5" font-weight="700"
      fill="var(--ink)" text-anchor="end" font-family="Consolas,monospace">${lastV}</text>
    <text x="${x(iH2)}" y="${Math.max(12, y(vals[iH2])-8)}" font-size="11" font-weight="700" fill="var(--bull)"
      text-anchor="${iH2>vals.length*0.75?'end':iH2<vals.length*0.25?'start':'middle'}">▲${vals[iH2]} (${dates[iH2]})</text>
    <text x="${x(iL2)}" y="${Math.min(H-16, y(vals[iL2])+15)}" font-size="11" font-weight="700" fill="var(--bear)"
      text-anchor="${iL2>vals.length*0.75?'end':iL2<vals.length*0.25?'start':'middle'}">▼${vals[iL2]} (${dates[iL2]})</text>`;
  /* X 軸日期刻度 */
  let xticks = '';
  const stp = Math.max(1, Math.ceil(dates.length/5));
  for(let i=0;i<dates.length;i+=stp){
    xticks += `<text x="${x(i)}" y="${H-6}" font-size="10" fill="var(--muted)" font-family="Consolas,monospace"
      text-anchor="${i===0?'start':'middle'}">${dates[i]}</text>`;
  }
  CURP = {dates, vals, byDay, P, W, n: dates.length};
  return `<div class="pwrap"><svg viewBox="0 0 ${W} ${H}" style="width:100%;height:auto;display:block" role="img" aria-label="${sym} price since first mention">
    <path d="${area}" fill="var(--bull-wash)" opacity=".6"/>
    ${grid}
    <path d="${path}" fill="none" stroke="var(--pill)" stroke-width="2"/>
    ${dots}${mark}${xticks}
  </svg><div class="kx"></div><div class="ktip"></div></div>
  <div class="leg"><span class="dot" style="background:var(--bull)"></span>Mentioned while bullish
   <span class="dot" style="background:var(--bear)"></span>Mentioned while bearish
   <span class="dot" style="background:var(--neu)"></span>neutral ·
   Dot = mention day (same-day merged); Y = that day's close · 滑鼠移動看逐日收盤</div>`;
}
let CURP = null;
function hookP(){
  const w = document.querySelector('#panel .pwrap'); if(!w || !CURP) return;
  const svg = w.querySelector('svg'), tip = w.querySelector('.ktip'), kx = w.querySelector('.kx');
  function mv(ev){
    const r = svg.getBoundingClientRect(); if(!r.width) return;
    const vx = (ev.clientX - r.left) / r.width * CURP.W;
    const step = (CURP.W - 2*CURP.P) / Math.max(1, CURP.n - 1);
    let i = Math.round((vx - CURP.P) / step);
    i = Math.max(0, Math.min(CURP.n - 1, i));
    const px = (CURP.P + i*step) / CURP.W * r.width;
    kx.style.left = px + 'px'; kx.style.display = 'block';
    const prev = i>0 ? CURP.vals[i-1] : CURP.vals[i];
    const chg = prev ? (CURP.vals[i]/prev-1)*100 : 0;
    const mday = CURP.byDay[CURP.dates[i]];
    tip.innerHTML = `<b>${CURP.dates[i]}</b> <span style="color:${chg>=0?'var(--bull)':'var(--bear)'}">${chg>=0?'+':''}${chg.toFixed(2)}%</span><br>
      close <b>${CURP.vals[i]}</b>${mday?`<br>mentions ${mday.b+mday.r+mday.n} (${mday.b} bull/${mday.r} bear)`:''}`;
    tip.style.display = 'block';
    const tw = tip.offsetWidth;
    tip.style.left = (px + 14 + tw > r.width ? Math.max(0, px - tw - 12) : px + 12) + 'px';
  }
  w.addEventListener('mousemove', mv);
  w.addEventListener('mouseleave', ()=>{ tip.style.display='none'; kx.style.display='none'; });
}
function opCol(rows, kind){
  const title = kind==='bull' ? '● Bull case' : '● Risks mentioned';
  const seen = new Set();
  const items = rows.filter(m=>m.reason && !seen.has(m.reason) && seen.add(m.reason)).slice(0,8);
  let h = `<div class="opbox ${kind}"><h3 style="color:var(--${kind==='bull'?'bull':'bear'})">${title}
    <span class="nf">Newest first</span></h3>`;
  h += items.length ? items.map(m=>`<div class="op"><span>· ${m.reason}</span>
      <span class="dt">${m.et_date} <a href="${m.url}" target="_blank">↗</a></span></div>`).join('')
    : '<div class="op" style="color:var(--muted)">none recorded</div>';
  return h + '</div>';
}
function showDetail(sym){
  const rows = M.filter(m=>m.symbol===sym).sort((a,b)=>a.posted_at<b.posted_at?1:-1);
  const s = agg(rows)[0];
  const q = QUOTES[sym], g = GAINS[sym], mt = META[sym]||{};
  const ccy = q&&q.ccy ? q.ccy : '';
  let h = `<button class="back" data-close="1">← Back</button>
   <span class="crumb">Stock detail · Aggregates the account's public posts only — not investment advice</span>
   <div class="dhead">
     <div><h1 style="font-size:34px;margin:0">${sym}</h1>
       <div class="company">${mt.name||''}${mt.industry?' · '+mt.industry:''}</div>
       <div style="margin-top:10px">${badge(s.stance,'')}${q?`<span class="pill" style="font-size:14px;padding:3px 10px">${ccy} ${q.price}</span>`:''}${pxpill(sym)} ${gainpill(sym)}</div></div>
     <div class="dstats">
       First mention <b>${s.first}</b> · Latest mention <b>${s.latest}</b><br>
       Total mentions <b>${s.total}</b>${g?` · First price <b>${ccy} ${g.first_price}</b>`:''}<br>
       <span style="color:var(--bull)">▲${s.bull} Bullish</span> ·
       <span style="color:var(--bear)">▼${s.bear} Bearish</span> · ●${s.neu} Neutral<br>
       Today <b>${M.filter(m=>m.symbol===sym&&m.et_date===LATEST).length}</b> ·
       7d <b>${s.n7}</b> · 28d <b>${s.n28}</b></div></div>
   <div class="chart">${svgChart(sym)}</div>
   <div class="cols">${opCol(rows.filter(m=>m.stance==='bullish'),'bull')}
                      ${opCol(rows.filter(m=>m.stance==='bearish'),'bear')}</div>
   <div class="aphead"><span class="appill">All posts ${rows.length}</span>
     <span class="aphint">Reverse chronological · original language kept, tap to open</span></div>`;
  for(const m of rows){
    const st = m.stance==='bullish' ? ['Bullish','b-bull'] : m.stance==='bearish' ? ['Bearish','b-bear'] : ['Background','b-neu'];
    h += `<div class="post"><div class="d">${m.et_date}</div>
      <div class="st ${st[1]}">${st[0]}</div>
      <div class="tx">${m.snippet.replace(/</g,'&lt;')}… <a href="${m.url}" target="_blank">↗</a></div></div>`;
  }
  document.getElementById('panel').innerHTML = h;
  document.getElementById('panel').style.display = 'block';
  document.getElementById('overlay').style.display = 'block';
  document.getElementById('panel').scrollTop = 0;
  hookP();
}
function hideDetail(){
  document.getElementById('panel').style.display = 'none';
  document.getElementById('overlay').style.display = 'none';
}
const tabs = document.getElementById('tabs');
tabs.innerHTML = Object.keys(WINDOWS).map(w=>
  `<button id="tb-${w}" data-w="${w}">${TITLES[w]}</button>`).join('');
function mark(){ for(const w in WINDOWS) document.getElementById('tb-'+w).className = w===cur?'on':''; }
// CSP-safe 事件委派(Artifact 會擋 inline onclick)
document.addEventListener('click', e=>{
  const tb = e.target.closest('[data-w]'); if(tb){ cur = tb.dataset.w; mark(); render(); return; }
  const th = e.target.closest('[data-sort]'); if(th){ sortT(+th.dataset.sort, +th.dataset.str); return; }
  const el = e.target.closest('[data-sym]'); if(el){ showDetail(el.dataset.sym); return; }
  if(e.target.closest('[data-close]') || e.target.id==='overlay') hideDetail();
});
mark(); render();
</script></body></html>"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-fetch", action="store_true")
    args = ap.parse_args()
    payload = build_payload(args.no_fetch)
    html = (HTML
            .replace("__DATA__", json.dumps(payload, ensure_ascii=False).replace("</", "<\\/"))
            .replace("__GENERATED__", f"generated {datetime.now():%Y-%m-%d %H:%M}"))
    OUT.write_text(html, encoding="utf-8")
    print(f"dashboard v2 -> {OUT}  (mentions={len(payload['mentions'])}, "
          f"prices={len(payload['prices'])}, gains={len(payload['gains'])}, meta={len(payload['meta'])})")


if __name__ == "__main__":
    main()
