# -*- coding: utf-8 -*-
"""rere 候選股票日面板(研究共用;回測票 #25、#26 使用)。

母體與報酬口徑沿用 BT-rere-in-list-ranking(#20)與 BT-rere-list-ordering(#23):
  候選 = 還原收盤 10 日高回落 >= 8%、|距月線| <= 5%、量比 < 1.2、收盤 > 季線、20 日均量 >= 500 張,不設 cooldown;
  報酬 fwd = 次日收盤進、其後第 60 根收盤(公司行動連續還原、不停損、毛報酬,單位 %);只用完整 60 根的樣本;
  每個訊號日候選至少 MIN_POOL 檔才計。
另回傳每檔的原始收盤序列(族群強度要用全市場、不只候選)。
限制:不含已下市股票(日K資料只有現存股票);樣本日重疊。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
BASE = Path(os.environ.get("STOCK_BASE_DIR") or REPO)
DAILY = BASE / "日K資料"
HOLD, MIN_POOL = 60, 8
COLS = ["Date", "Close", "Volume", "VOL_MA_20", "MA_20", "MA_60"]

sys.path.insert(0, str(REPO))
from ml.corporate_actions import backward_adjustment_multiplier, load_action_calendar  # noqa: E402


def _is_stock(tk: str) -> bool:
    return tk.isdigit() and len(tk) == 4 and tk[0] != "0"


def build_panel(start: str = "2020-01-01") -> tuple[pd.DataFrame, dict[str, pd.DataFrame]]:
    """回傳 (候選股票日, {ticker: DataFrame[date, close, adj_close, volume]})。"""
    actions = load_action_calendar(str(BASE / "ml" / "data" / "ex_dividend_calendar.csv"),
                                   str(BASE / "ml" / "data" / "corporate_action_price_factors.csv"))
    action_tickers = set(actions["stock_id"].astype(str))
    cands, series = [], {}
    for f in sorted(DAILY.glob("*.csv")):
        tk = f.stem
        if not _is_stock(tk):
            continue
        try:
            k = pd.read_csv(f, usecols=lambda c: c in COLS)
        except Exception:
            continue
        if len(k) < 25 or not set(COLS) <= set(k.columns):
            continue
        k["Date"] = k["Date"].astype(str).str[:10]
        num = {c: pd.to_numeric(k[c], errors="coerce") for c in COLS[1:]}
        c, vol, vma = num["Close"], num["Volume"], num["VOL_MA_20"]
        mult = (backward_adjustment_multiplier(k["Date"], tk, actions).to_numpy(float) if tk in action_tickers
                else np.ones(len(k)))
        av = pd.Series(c.to_numpy(float) * mult)
        series[tk] = pd.DataFrame({"date": k["Date"].to_numpy(), "close": c.to_numpy(float),
                                   "adj_close": av.to_numpy(float), "volume": vol.to_numpy(float)})
        if len(k) < 150:
            continue
        feat = pd.DataFrame({"wash": 1 - av / av.rolling(10).max(), "vr": vol / vma, "v20": c / num["MA_20"] - 1})
        feat["fwd"] = (av.shift(-(HOLD + 1)) / av.shift(-1) - 1) * 100
        feat["date"] = k["Date"].to_numpy()
        feat["volume"] = vol.to_numpy(float)
        feat["vol5"] = vol.rolling(5).sum().to_numpy(float)
        mask = ((feat["wash"] >= 0.08) & (feat["v20"].abs() <= 0.05) & (feat["vr"] < 1.2) & (c > num["MA_60"])
                & (vma >= 500_000) & feat["fwd"].notna() & (feat["date"] >= start))
        cand = feat[mask.fillna(False)].copy()
        if cand.empty:
            continue
        cand["ticker"] = tk
        cands.append(cand)
    D = pd.concat(cands, ignore_index=True).replace([np.inf, -np.inf], np.nan)
    D = D[D.groupby("date")["fwd"].transform("size") >= MIN_POOL].copy()
    D["year"] = D["date"].str[:4]
    D["half"] = np.where(D["year"] <= "2022", "2020-22", "2023-26")
    D["pool_mean"] = D.groupby("date")["fwd"].transform("mean")
    return D.reset_index(drop=True), series
