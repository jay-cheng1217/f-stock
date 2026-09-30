# -*- coding: utf-8 -*-
"""出關雷達(觀察用,非進場訊號)。

BT-disposition-release(2026-07-08)判決:雙引信事件為樂透結構,雷達≠扳機。
本腳本每日掃「7 日內出關 + 今日在營收公布窗(月初1-10) + 已知月營收YoY>30%」,
輸出 ml/data/disposition_radar.json 供名單/儀表板打觀察標籤,由裁量層決定。
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
from scripts.disposition_contract import resolve_disposition_periods


def recent_releases(periods: pd.DataFrame, *, knowledge_as_of: str | datetime) -> pd.DataFrame:
    """Current observation view; keep official correction publication timing."""
    cutoff = pd.Timestamp(knowledge_as_of)
    today = cutoff.normalize().tz_localize(None) if cutoff.tzinfo else cutoff.normalize()
    disp = resolve_disposition_periods(periods, knowledge_as_of=knowledge_as_of)
    disp["period_end"] = pd.to_datetime(disp["period_end"])
    disp = disp[disp["stock_id"].str.fullmatch(r"\d{4}")]
    active_codes = set(disp.loc[(pd.to_datetime(disp["period_start"]) <= today) &
                               (disp["period_end"] >= today), "stock_id"])
    recent = disp[(disp["period_end"] < today) & ((today - disp["period_end"]).dt.days <= 7)]
    recent = recent[~recent["stock_id"].isin(active_codes)]
    return recent.sort_values("period_end").drop_duplicates("stock_id", keep="last")


def known_yoy(tk: str, today: pd.Timestamp) -> float | None:
    f = BASE / "月營收" / f"revenue_{tk}.csv"
    if not f.exists():
        return None
    try:
        r = pd.read_csv(f, usecols=["Date", "YoY_pct_change"])
    except Exception:
        return None
    m = today - pd.DateOffset(months=1 if today.day >= 11 else 2)
    hit = r[r["Date"].astype(str) == f"{m.year}-{m.month:02d}"]
    if hit.empty:
        return None
    v = pd.to_numeric(hit["YoY_pct_change"].iloc[-1], errors="coerce")
    return None if pd.isna(v) else float(v)


def main() -> None:
    now = datetime.now()
    today = pd.Timestamp(now.date())
    disp = pd.read_csv(BASE / "ml" / "data" / "disposition_periods.csv", dtype={"stock_id": str})
    recent = recent_releases(disp, knowledge_as_of=now)

    sec = pd.read_csv(BASE / "ml" / "data" / "sector_mapping.csv", dtype=str)
    nm = dict(zip(sec["Ticker"].str.zfill(4), zip(sec["Name"], sec["Sector"])))

    hits = []
    in_window = 1 <= today.day <= 10
    for _, ev in recent.iterrows():
        tk = ev["stock_id"].strip()
        yoy = known_yoy(tk, today)
        name, sector = nm.get(tk, ("", ""))
        hits.append({
            "ticker": tk, "name": name, "sector": sector,
            "released": str(ev["period_end"].date()),
            "days_since": int((today - ev["period_end"]).days),
            "known_yoy": yoy,
            "rev_window": in_window,
            "dual_fuse": bool(in_window and yoy is not None and yoy > 30),
        })
    out = {"date": str(today.date()), "hits": hits}
    (BASE / "ml" / "data" / "disposition_radar.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    dual = [h for h in hits if h["dual_fuse"]]
    print(f"[出關雷達] {today.date()} 近7日出關 {len(hits)} 檔,雙引信 {len(dual)} 檔")
    for h in hits:
        tag = "🔥雙引信" if h["dual_fuse"] else ("營收窗" if h["rev_window"] else "")
        yoy_s = f"YoY {h['known_yoy']:+.0f}%" if h["known_yoy"] is not None else "YoY n/a"
        print(f"  {h['ticker']} {h['name']:<6} 出關{h['released']} ({h['days_since']}天前) {yoy_s} {tag}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
