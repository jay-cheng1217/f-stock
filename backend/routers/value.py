"""低基期價值股篩選 API — 每日動態篩選 PE 低 + EPS 成長 + 未起漲的標的。"""

from __future__ import annotations

import glob
import os

import numpy as np
import pandas as pd
from fastapi import APIRouter

from ml.config import DAILY_K_DIR, MODEL_DIR

router = APIRouter(tags=["value"])

# === 路徑 ===
EPS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "季報財務")
REV_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "月營收")

# === 篩選參數 ===
VALUE_MAX_PE = 20
VALUE_MIN_EPS_TTM = 0
VALUE_MAX_MA5_BIAS = 0.05
VALUE_MAX_RET_20D = 0.10
VALUE_MIN_AVG_VOL = 500  # 張
VALUE_MIN_PROB = 0.28


def _load_eps_data() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """載入 EPS TTM、QoQ、YoY。"""
    eps_files = sorted(glob.glob(os.path.join(EPS_DIR, "eps_*.csv")))
    if len(eps_files) < 2:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

    latest_file = eps_files[-1]
    prev_file = eps_files[-2]

    latest = pd.read_csv(latest_file, dtype={"Ticker": str})
    prev = pd.read_csv(prev_file, dtype={"Ticker": str})

    # 找同季去年（往前推 4 個檔案）
    yoy_file = eps_files[-5] if len(eps_files) >= 5 else None
    yoy_df = pd.read_csv(yoy_file, dtype={"Ticker": str}) if yoy_file else pd.DataFrame()

    # TTM: 最近 4 季合計
    recent_4 = eps_files[-4:] if len(eps_files) >= 4 else eps_files
    ttm_dfs = [pd.read_csv(f, dtype={"Ticker": str}) for f in recent_4]
    ttm_all = pd.concat(ttm_dfs, ignore_index=True)
    ttm = ttm_all.groupby("Ticker")["EPS_Basic"].sum().reset_index()
    ttm.columns = ["ticker", "eps_ttm"]

    # QoQ
    qoq = latest.merge(prev[["Ticker", "EPS_Basic"]], on="Ticker", suffixes=("_cur", "_prev"))
    qoq["eps_qoq"] = qoq["EPS_Basic_cur"] - qoq["EPS_Basic_prev"]
    qoq = qoq.rename(columns={"Ticker": "ticker", "EPS_Basic_cur": "eps_latest"})[
        ["ticker", "eps_latest", "eps_qoq"]
    ]

    # YoY
    yoy = pd.DataFrame()
    if not yoy_df.empty:
        yoy = latest.merge(yoy_df[["Ticker", "EPS_Basic"]], on="Ticker", suffixes=("_new", "_old"))
        yoy["eps_yoy"] = yoy["EPS_Basic_new"] - yoy["EPS_Basic_old"]
        yoy = yoy.rename(columns={"Ticker": "ticker"})[["ticker", "eps_yoy"]]

    return ttm, qoq, yoy


def _load_revenue_yoy(tickers: list[str] | None = None) -> pd.DataFrame:
    """讀取個股最新月營收 YoY。"""
    if tickers:
        rev_files = [os.path.join(REV_DIR, f"revenue_{ticker}.csv") for ticker in tickers]
    else:
        rev_files = sorted(glob.glob(os.path.join(REV_DIR, "revenue_*.csv")))[-2000:]
    records = {}
    for f in rev_files:
        if not os.path.exists(f):
            continue
        try:
            r = pd.read_csv(f)
            if "YoY_pct_change" in r.columns and len(r) > 0:
                ticker = os.path.basename(f).replace("revenue_", "").replace(".csv", "")
                row = r.dropna(subset=["YoY_pct_change"]).iloc[-1]
                records[ticker] = float(row["YoY_pct_change"])
        except Exception:
            pass
    return pd.DataFrame({"ticker": list(records.keys()), "rev_yoy": list(records.values())})


def _load_price_stats(tickers: list[str]) -> pd.DataFrame:
    """計算近期價格統計。"""
    rows = []
    for t in tickers:
        kf = os.path.join(DAILY_K_DIR, f"{t}.csv")
        if not os.path.exists(kf):
            continue
        try:
            dk = pd.read_csv(kf, usecols=["Date", "Close", "Volume"])
            dk = dk.sort_values("Date").tail(60)
            if len(dk) < 20:
                continue
            c = dk["Close"].values
            v = dk["Volume"].values
            ma5 = np.mean(c[-5:])
            rows.append({
                "ticker": t,
                "ret_20d": (c[-1] / c[-20] - 1),
                "ret_60d": (c[-1] / c[0] - 1) if len(c) >= 40 else None,
                "pct_from_60d_low": (c[-1] / np.min(c) - 1),
                "ma5_bias": (c[-1] - ma5) / ma5 if ma5 > 0 else 0,
                "avg_vol_5d": np.mean(v[-5:]) / 1000,  # 轉換為張
            })
        except Exception:
            pass
    return pd.DataFrame(
        rows,
        columns=[
            "ticker",
            "ret_20d",
            "ret_60d",
            "pct_from_60d_low",
            "ma5_bias",
            "avg_vol_5d",
        ],
    )


def _load_sector() -> dict[str, str]:
    """載入產業別。"""
    try:
        from ml.features.sector import load_sector_mapping
        sdf = load_sector_mapping()
        if sdf is not None and "ticker" in sdf.columns and "sector" in sdf.columns:
            return dict(zip(sdf["ticker"].astype(str), sdf["sector"]))
    except Exception:
        pass
    return {}


def screen_value_stocks() -> list[dict]:
    """篩選低基期價值股。"""
    # 讀 T+1 predictions 取 prob
    pred_files = sorted(glob.glob(os.path.join(MODEL_DIR, "predictions_t1_*.csv")))
    if not pred_files:
        return []
    pred = pd.read_csv(pred_files[-1], dtype={"ticker": str})
    if "hit_prob_3pct" not in pred.columns:
        return []

    pred_mini = pred[["ticker", "close", "hit_prob_3pct"]].copy()

    # 載入各資料
    ttm, qoq, yoy = _load_eps_data()
    sectors = _load_sector()

    # 合併
    df = pred_mini.merge(ttm, on="ticker", how="left")
    df = df.merge(qoq, on="ticker", how="left")
    df = df.merge(yoy, on="ticker", how="left")
    df["sector"] = df["ticker"].map(sectors).fillna("")
    df["pe"] = df["close"] / df["eps_ttm"].replace(0, np.nan)

    # 先用便宜的基本面與機率條件縮小 universe，再讀取日 K 計算價格統計。
    prelim_cond = (
        (df["eps_ttm"] > VALUE_MIN_EPS_TTM)
        & (df["eps_latest"] > 0)
        & (df["eps_qoq"] > 0)
        & (df["pe"] > 0)
        & (df["pe"] < VALUE_MAX_PE)
        & (df["hit_prob_3pct"] > VALUE_MIN_PROB)
    )
    candidate_tickers = df.loc[prelim_cond, "ticker"].astype(str).tolist()
    price = _load_price_stats(candidate_tickers)
    rev = _load_revenue_yoy(candidate_tickers)
    df = df.merge(rev, on="ticker", how="left")
    df = df.merge(price, on="ticker", how="left")

    # 篩選
    cond = (
        prelim_cond
        & (df["ma5_bias"] < VALUE_MAX_MA5_BIAS)
        & (df["ret_20d"] < VALUE_MAX_RET_20D)
        & (df["avg_vol_5d"] > VALUE_MIN_AVG_VOL)
    )
    result = df[cond].sort_values("hit_prob_3pct", ascending=False).head(30)

    # 組回傳
    out = []
    from backend.routers.t1 import _NAME_LOOKUP
    for _, r in result.iterrows():
        out.append({
            "ticker": str(r["ticker"]),
            "name": _NAME_LOOKUP.get(str(r["ticker"]), ""),
            "close": round(float(r["close"]), 2) if pd.notna(r["close"]) else None,
            "pe": round(float(r["pe"]), 1) if pd.notna(r["pe"]) else None,
            "eps_ttm": round(float(r["eps_ttm"]), 2) if pd.notna(r["eps_ttm"]) else None,
            "eps_latest": round(float(r["eps_latest"]), 2) if pd.notna(r["eps_latest"]) else None,
            "eps_qoq": round(float(r["eps_qoq"]), 2) if pd.notna(r["eps_qoq"]) else None,
            "eps_yoy": round(float(r["eps_yoy"]), 2) if pd.notna(r.get("eps_yoy")) else None,
            "rev_yoy": round(float(r["rev_yoy"]), 1) if pd.notna(r.get("rev_yoy")) else None,
            "ma5_bias": round(float(r["ma5_bias"]) * 100, 1) if pd.notna(r["ma5_bias"]) else None,
            "ret_20d": round(float(r["ret_20d"]) * 100, 1) if pd.notna(r["ret_20d"]) else None,
            "ret_60d": round(float(r["ret_60d"]) * 100, 1) if pd.notna(r.get("ret_60d")) else None,
            "pct_from_60d_low": round(float(r["pct_from_60d_low"]) * 100, 1) if pd.notna(r.get("pct_from_60d_low")) else None,
            "avg_vol_5d": round(float(r["avg_vol_5d"]), 0) if pd.notna(r["avg_vol_5d"]) else None,
            "hit_prob": round(float(r["hit_prob_3pct"]) * 100, 1) if pd.notna(r["hit_prob_3pct"]) else None,
            "sector": str(r["sector"]) if r["sector"] else None,
        })
    return out


@router.get("/api/value/screen")
def api_value_screen():
    """低基期價值股篩選。"""
    try:
        data = screen_value_stocks()
        return {
            "status": "success",
            "count": len(data),
            "criteria": {
                "max_pe": VALUE_MAX_PE,
                "eps_qoq_positive": True,
                "max_ma5_bias": f"{VALUE_MAX_MA5_BIAS*100:.0f}%",
                "max_ret_20d": f"{VALUE_MAX_RET_20D*100:.0f}%",
                "min_avg_vol": f"{VALUE_MIN_AVG_VOL} 張",
            },
            "data": data,
        }
    except Exception as e:
        return {"status": "error", "error": str(e), "data": []}
