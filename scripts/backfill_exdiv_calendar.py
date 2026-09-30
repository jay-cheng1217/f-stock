# -*- coding: utf-8 -*-
"""建置上市除權息日曆(TWSE TWT49U,月為單位範圍查詢)。

輸出 ml/data/exdiv_calendar_twse.csv:
  date(ISO), ticker, name, prev_close(除權息前收盤), ref_price(參考價),
  value(權值+息值), kind(權/息/權息)
支援 BT-dividend-refill 與「除息假跌破停損」ticket。TPEX 歷史端點不可得,
上櫃另以 openapi prepost 前向累積(scripts 未含,見 STRATEGY_overview 註記)。

用法: python scripts/backfill_exdiv_calendar.py --start 2019-12 --end 2026-07
"""
from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path

import pandas as pd

BASE = Path(__file__).resolve().parents[1]
OUT = BASE / "ml" / "data" / "exdiv_calendar_twse.csv"
URL = ("https://www.twse.com.tw/rwd/zh/exRight/TWT49U"
       "?startDate={s}&endDate={e}&response=json")


def month_range(start: str, end: str):
    cur = pd.Period(start, "M")
    last = pd.Period(end, "M")
    while cur <= last:
        yield cur.strftime("%Y%m01"), (cur.asfreq("D", "end")).strftime("%Y%m%d")
        cur += 1


def roc_to_iso(s: str) -> str | None:
    s = str(s).replace("年", "/").replace("月", "/").replace("日", "")
    parts = s.split("/")
    if len(parts) != 3:
        return None
    try:
        return f"{int(parts[0]) + 1911}-{int(parts[1]):02d}-{int(parts[2]):02d}"
    except Exception:
        return None


def num(v):
    try:
        return float(str(v).replace(",", ""))
    except Exception:
        return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2019-12")
    ap.add_argument("--end", default="2026-07")
    a = ap.parse_args()

    rows = []
    for s, e in month_range(a.start, a.end):
        out = subprocess.run(["curl", "-sL", "--max-time", "40",
                              "-H", "User-Agent: Mozilla/5.0", URL.format(s=s, e=e)],
                             capture_output=True, timeout=60)
        try:
            d = json.loads(out.stdout.decode("utf-8"))
        except Exception:
            print(f"  {s}: decode fail, skip")
            time.sleep(2)
            continue
        data = d.get("data") or []
        for r in data:
            iso = roc_to_iso(r[0])
            tk = str(r[1]).strip()
            if not iso or not (tk.isdigit() and len(tk) == 4):
                continue
            rows.append({"date": iso, "ticker": tk, "name": str(r[2]).strip(),
                         "prev_close": num(r[3]), "ref_price": num(r[4]),
                         "value": num(r[5]), "kind": str(r[6]).strip()})
        print(f"  {s[:6]}: +{len(data)}")
        time.sleep(1.2)

    df = pd.DataFrame(rows).drop_duplicates(subset=["date", "ticker"])
    df = df.sort_values(["date", "ticker"])
    df.to_csv(OUT, index=False, encoding="utf-8-sig")
    print(f"\n{len(df)} 筆除權息事件 -> {OUT}")


if __name__ == "__main__":
    main()
