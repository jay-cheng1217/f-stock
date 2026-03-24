"""圖表資料 API — 提供前端 ECharts 所需格式."""

import math
from fastapi import APIRouter, Query
from backend.db.engine import query_df


def _safe_list(series):
    """將 pandas Series 轉為 JSON-safe list (NaN → None)"""
    return [None if (v is None or (isinstance(v, float) and math.isnan(v))) else v
            for v in series.tolist()]

router = APIRouter(prefix="/api/charts", tags=["charts"])


@router.get("/{ticker}/kline")
def kline_data(ticker: str, days: int = Query(120, ge=1, le=1000)):
    """K線 + 均線 + 布林 + 成交量 — ECharts 格式."""
    df = query_df(f"""
        SELECT Date, Open, High, Low, Close, Volume,
               MA_5, MA_20, MA_60,
               "BBU_20_2.0" AS bb_upper,
               "BBM_20_2.0" AS bb_middle,
               "BBL_20_2.0" AS bb_lower,
               VOL_MA_5, VOL_MA_20
        FROM daily_k
        WHERE Ticker = $1
        ORDER BY Date DESC
        LIMIT {days}
    """, [ticker])

    if df.empty:
        return {"ticker": ticker, "dates": [], "ohlc": [], "volumes": []}

    df = df.iloc[::-1]
    df = df.where(df.notna(), None)

    dates = df["Date"].tolist()
    ohlc = df[["Open", "Close", "Low", "High"]].values.tolist()
    volumes = _safe_list(df["Volume"])

    return {
        "ticker": ticker,
        "dates": dates,
        "ohlc": ohlc,
        "volumes": volumes,
        "ma5": _safe_list(df["MA_5"]),
        "ma20": _safe_list(df["MA_20"]),
        "ma60": _safe_list(df["MA_60"]),
        "bb_upper": _safe_list(df["bb_upper"]),
        "bb_middle": _safe_list(df["bb_middle"]),
        "bb_lower": _safe_list(df["bb_lower"]),
        "vol_ma5": _safe_list(df["VOL_MA_5"]),
        "vol_ma20": _safe_list(df["VOL_MA_20"]),
    }


@router.get("/{ticker}/indicators")
def indicator_data(ticker: str, days: int = Query(120, ge=1, le=1000)):
    """技術指標子圖資料 (RSI, MACD, KD)."""
    df = query_df(f"""
        SELECT Date,
               RSI_14,
               MACD_12_26_9 AS macd,
               MACDs_12_26_9 AS macd_signal,
               MACDh_12_26_9 AS macd_hist,
               K, D
        FROM daily_k
        WHERE Ticker = $1
        ORDER BY Date DESC
        LIMIT {days}
    """, [ticker])

    if df.empty:
        return {"ticker": ticker, "dates": []}

    df = df.iloc[::-1]
    df = df.where(df.notna(), None)

    return {
        "ticker": ticker,
        "dates": df["Date"].tolist(),
        "rsi14": _safe_list(df["RSI_14"]),
        "macd": _safe_list(df["macd"]),
        "macd_signal": _safe_list(df["macd_signal"]),
        "macd_hist": _safe_list(df["macd_hist"]),
        "k": _safe_list(df["K"]),
        "d": _safe_list(df["D"]),
    }


@router.get("/{ticker}/institutional")
def institutional_chart(ticker: str, days: int = Query(60, ge=1, le=500)):
    """法人買賣超長條圖資料."""
    df = query_df(f"""
        SELECT Date, Foreign_BuySell, Trust_BuySell, Dealer_BuySell
        FROM daily_k
        WHERE Ticker = $1
        ORDER BY Date DESC
        LIMIT {days}
    """, [ticker])

    if df.empty:
        return {"ticker": ticker, "dates": []}

    df = df.iloc[::-1]
    df = df.where(df.notna(), None)

    return {
        "ticker": ticker,
        "dates": df["Date"].tolist(),
        "foreign": _safe_list(df["Foreign_BuySell"]),
        "trust": _safe_list(df["Trust_BuySell"]),
        "dealer": _safe_list(df["Dealer_BuySell"]),
    }


@router.get("/{ticker}/revenue_chart")
def revenue_chart(ticker: str, months: int = Query(24, ge=1, le=60)):
    """月營收趨勢圖資料."""
    df = query_df(f"""
        SELECT Date, Monthly_Revenue, YoY_pct_change
        FROM revenue
        WHERE Ticker = $1
        ORDER BY Date DESC
        LIMIT {months}
    """, [ticker])

    if df.empty:
        return {"ticker": ticker, "dates": []}

    df = df.iloc[::-1]
    df = df.where(df.notna(), None)

    return {
        "ticker": ticker,
        "dates": df["Date"].tolist(),
        "monthly_revenue": _safe_list(df["Monthly_Revenue"]),
        "yoy_pct": _safe_list(df["YoY_pct_change"]),
    }
