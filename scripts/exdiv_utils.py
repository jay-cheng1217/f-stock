# -*- coding: utf-8 -*-
"""除權息還原共用工具。

日曆檔:ml/data/ex_dividend_calendar.csv
欄位:stock_id, date(除權息日), before_price, after_price, stock_and_cache_dividend(權值+息值,元/股)

原則:凡是「進場後持有至今」的報酬與停損判定,都應把持有期間內的
權值+息值加回(effective_price = close + 累計權息值),避免除息缺口被
誤判為虧損/破線。日曆由 scripts/update_ex_dividend_calendar.py 每日更新。
"""
from __future__ import annotations

import os
from functools import lru_cache

import pandas as pd

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CAL_PATH = os.path.join(BASE_DIR, "ml", "data", "ex_dividend_calendar.csv")


@lru_cache(maxsize=1)
def _calendar() -> pd.DataFrame:
    if not os.path.exists(CAL_PATH):
        return pd.DataFrame(columns=["stock_id", "date", "dv"])
    df = pd.read_csv(CAL_PATH, dtype={"stock_id": str}, encoding="utf-8-sig")
    df["dv"] = pd.to_numeric(df.get("stock_and_cache_dividend"), errors="coerce").fillna(0.0)
    df["date"] = df["date"].astype(str).str[:10]
    return df[["stock_id", "date", "dv"]]


def cum_dividend(ticker: str, after_date: str, until_date: str) -> float:
    """回傳 (after_date, until_date] 之間該股的累計權值+息值(元/股)。"""
    cal = _calendar()
    ev = cal[(cal["stock_id"] == str(ticker))
             & (cal["date"] > str(after_date)[:10])
             & (cal["date"] <= str(until_date)[:10])]
    return float(ev["dv"].sum())


def adjusted_return(ticker: str, entry_price: float, entry_date: str,
                    close: float, close_date: str) -> float:
    """除息還原後報酬(小數)。無事件時等同 close/entry - 1。"""
    dv = cum_dividend(ticker, entry_date, close_date)
    return (float(close) + dv) / float(entry_price) - 1.0
