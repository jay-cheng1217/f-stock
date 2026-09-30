# -*- coding: utf-8 -*-
"""一次性:把已修復的上櫃 07-02 OHLCV 從 CSV 熱更新進 daily_k(web 停止時執行)。

冪等;只 UPDATE logs/tpex_0702_bar_repair.csv 列出的 ticker 的 2026-07-02 列。
"""
from pathlib import Path

import duckdb
import pandas as pd

BASE = Path(__file__).resolve().parents[1]
frames = []
for name in ("tpex_0702_bar_repair.csv", "twse_0702_bar_repair.csv"):
    f = BASE / "logs" / name
    if f.exists():
        frames.append(pd.read_csv(f, dtype={"ticker": str}))
rep = pd.concat(frames, ignore_index=True)
con = duckdb.connect(str(BASE / "stock.duckdb"))
n = 0
for tk in rep["ticker"]:
    f = BASE / "日K資料" / f"{tk}.csv"
    if not f.exists():
        continue
    k = pd.read_csv(f)
    row = k[k["Date"].astype(str).str[:10] == "2026-07-02"]
    if row.empty:
        continue
    r = row.iloc[-1]
    con.execute(
        "update daily_k set Open=?, High=?, Low=?, Close=?, Volume=? "
        "where Ticker=? and cast(Date as date) = date '2026-07-02'",
        [float(r["Open"]), float(r["High"]), float(r["Low"]),
         float(r["Close"]), float(r["Volume"]), tk])
    n += 1
con.close()
print(f"daily_k hotfix: {n} tickers updated")
