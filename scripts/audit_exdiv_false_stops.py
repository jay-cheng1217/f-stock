# -*- coding: utf-8 -*-
"""Audit stopped_out positions for ex-dividend-induced false stops (冤案審計).

Checks Champion unified book + rere lane ledger: for every stopped_out position,
sum cash/stock dividend value with ex-date inside the holding window, recompute
the dividend-adjusted return, and flag cases where the stop decision was driven
by the ex-dividend price gap rather than a real loss.

Also builds/extends ml/data/ex_dividend_calendar.csv for all audited tickers.
"""
from __future__ import annotations

import io
import os
import sqlite3
import sys
import time

import pandas as pd
import requests

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CAL_PATH = os.path.join(BASE_DIR, "ml", "data", "ex_dividend_calendar.csv")
FINMIND_URL = "https://api.finmindtrade.com/api/v4/data"
STOP_RULE = -0.10


def _token() -> str:
    for line in io.open(os.path.join(BASE_DIR, ".env"), encoding="utf-8-sig"):
        if line.startswith("FINMIND_TOKEN="):
            return line.strip().split("=", 1)[1]
    raise RuntimeError("FINMIND_TOKEN missing")


def fetch_dividends(tickers: list[str], token: str) -> pd.DataFrame:
    rows = []
    for i, tk in enumerate(tickers):
        for attempt in range(3):
            try:
                r = requests.get(FINMIND_URL, params={
                    "dataset": "TaiwanStockDividendResult", "data_id": tk,
                    "start_date": "2026-01-01", "token": token}, timeout=60)
                r.raise_for_status()
                data = r.json().get("data") or []
                rows.extend(data)
                break
            except Exception as exc:  # noqa: BLE001
                if attempt == 2:
                    print(f"  {tk} fetch failed: {exc}")
                time.sleep(3 * (attempt + 1))
        time.sleep(0.4)
        if (i + 1) % 20 == 0:
            print(f"  dividends fetched {i + 1}/{len(tickers)}")
    df = pd.DataFrame(rows)
    if df.empty:
        return pd.DataFrame(columns=["stock_id", "date", "stock_and_cache_dividend"])
    return df[["stock_id", "date", "before_price", "after_price", "stock_and_cache_dividend"]]


def load_positions() -> pd.DataFrame:
    out = []
    con = sqlite3.connect(os.path.join(BASE_DIR, "paper_portfolio_v2_champion.db"))
    for r in con.execute(
        "select ticker, entry_date, entry_price, exit_date, exit_price, realized_return_pct, exit_reason "
        "from unified_positions where status='stopped_out'"):
        out.append(dict(book="champion", ticker=str(r[0]), entry_date=r[1], entry_price=r[2],
                        exit_date=r[3], exit_price=r[4], raw_ret=r[5], reason=r[6]))
    con.close()
    led = pd.read_csv(os.path.join(BASE_DIR, "ml", "reports", "rere_lane_ledger.csv"),
                      dtype={"ticker": str}, encoding="utf-8-sig")
    for _, r in led[led["status"] == "stopped_out"].iterrows():
        if pd.isna(r.get("entry_close")) or pd.isna(r.get("last_close")):
            continue
        out.append(dict(book="rere", ticker=str(r["ticker"]), entry_date=r["entry_date"],
                        entry_price=float(r["entry_close"]), exit_date=r["last_date"],
                        exit_price=float(r["last_close"]),
                        raw_ret=float(r["ret_pct"]) / 100.0, reason="stop"))
    return pd.DataFrame(out)


def main() -> int:
    pos = load_positions()
    holdings = ["3033", "3227", "6695", "8096", "8150"]
    tickers = sorted(set(pos["ticker"]) | set(holdings))
    print(f"audit positions: {len(pos)} (champion {sum(pos.book=='champion')}, rere {sum(pos.book=='rere')}); "
          f"tickers to fetch: {len(tickers)}")

    div = fetch_dividends(tickers, _token())
    if os.path.exists(CAL_PATH):
        old = pd.read_csv(CAL_PATH, dtype={"stock_id": str}, encoding="utf-8-sig")
        div = pd.concat([old, div.astype({"stock_id": str})], ignore_index=True)
    div = div.drop_duplicates(["stock_id", "date"], keep="last").sort_values(["date", "stock_id"])
    os.makedirs(os.path.dirname(CAL_PATH), exist_ok=True)
    div.to_csv(CAL_PATH, index=False, encoding="utf-8-sig")
    print(f"calendar -> {CAL_PATH} ({len(div)} events)")

    div["dv"] = pd.to_numeric(div["stock_and_cache_dividend"], errors="coerce").fillna(0.0)
    false_stops, affected = [], 0
    for _, p in pos.iterrows():
        ev = div[(div["stock_id"] == p.ticker) & (div["date"] > str(p.entry_date)) & (div["date"] <= str(p.exit_date))]
        dv = float(ev["dv"].sum())
        if dv <= 0:
            continue
        affected += 1
        adj_ret = (p.exit_price + dv - p.entry_price) / p.entry_price
        wrongful = p.raw_ret <= STOP_RULE < adj_ret if p.book == "champion" else adj_ret - p.raw_ret > 0.005
        false_stops.append(dict(book=p.book, ticker=p.ticker, entry=f"{p.entry_date}@{p.entry_price:.2f}",
                                exit=f"{p.exit_date}@{p.exit_price:.2f}", div=dv,
                                raw=round(p.raw_ret * 100, 2), adj=round(adj_ret * 100, 2),
                                wrongful_stop=bool(wrongful)))
    print(f"\npositions with ex-div inside holding window: {affected}/{len(pos)}")
    if false_stops:
        print(pd.DataFrame(false_stops).to_string(index=False))
    else:
        print("=> 零冤案:所有 stopped_out 的持有期間內都沒有除權息事件,停損判定不受除息缺口影響。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
