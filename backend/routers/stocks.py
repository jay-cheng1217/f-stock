"""個股資料 API."""

from fastapi import APIRouter, Query
from backend.db.engine import query_df

router = APIRouter(prefix="/api/stocks", tags=["stocks"])


@router.get("/list")
def stock_list(
    q: str = Query("", description="搜尋股票代號或名稱"),
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=500),
):
    """股票清單 + 最新價格."""
    offset = (page - 1) * size

    if q:
        df = query_df(f"""
            SELECT * FROM stock_list
            WHERE Ticker LIKE '%{q}%' OR Name LIKE '%{q}%'
            ORDER BY Ticker
            LIMIT {size} OFFSET {offset}
        """)
        total_df = query_df(f"""
            SELECT COUNT(*) AS cnt FROM stock_list
            WHERE Ticker LIKE '%{q}%' OR Name LIKE '%{q}%'
        """)
    else:
        df = query_df(f"""
            SELECT * FROM stock_list
            ORDER BY Ticker
            LIMIT {size} OFFSET {offset}
        """)
        total_df = query_df("SELECT COUNT(*) AS cnt FROM stock_list")

    total = int(total_df.iloc[0]["cnt"]) if not total_df.empty else 0

    records = []
    for _, row in df.iterrows():
        records.append({
            "ticker": row["Ticker"],
            "name": row.get("Name", ""),
            "last_close": _float(row.get("Last_Close")),
            "last_volume": _int(row.get("Last_Volume")),
            "last_date": str(row.get("Last_Date", "")),
            "last_change_pct": _float(row.get("Last_Change_Pct")),
        })

    return {"total": total, "page": page, "size": size, "data": records}


@router.get("/{ticker}")
def stock_detail(ticker: str):
    """個股完整資料 (最新一筆日K + 基本資訊)."""
    info_df = query_df(
        "SELECT * FROM stock_list WHERE Ticker = $1", [ticker]
    )
    if info_df.empty:
        return {"error": "找不到此股票", "ticker": ticker}

    latest_df = query_df("""
        SELECT * FROM daily_k
        WHERE Ticker = $1
        ORDER BY Date DESC
        LIMIT 1
    """, [ticker])

    info = info_df.iloc[0].to_dict()
    latest = latest_df.iloc[0].to_dict() if not latest_df.empty else {}

    return {
        "ticker": ticker,
        "name": info.get("Name", ""),
        "last_close": _float(info.get("Last_Close")),
        "last_volume": _int(info.get("Last_Volume")),
        "last_date": str(info.get("Last_Date", "")),
        "last_change_pct": _float(info.get("Last_Change_Pct")),
        "latest_daily": _clean_dict(latest),
    }


@router.get("/{ticker}/daily")
def stock_daily(ticker: str, days: int = Query(120, ge=1, le=1000)):
    """日K圖表資料."""
    df = query_df(f"""
        SELECT * FROM daily_k
        WHERE Ticker = $1
        ORDER BY Date DESC
        LIMIT {days}
    """, [ticker])

    if df.empty:
        return {"ticker": ticker, "data": []}

    df = df.iloc[::-1]  # 恢復正序
    records = [_clean_dict(row.to_dict()) for _, row in df.iterrows()]
    return {"ticker": ticker, "count": len(records), "data": records}


@router.get("/{ticker}/revenue")
def stock_revenue(ticker: str):
    """月營收歷史."""
    df = query_df("""
        SELECT * FROM revenue
        WHERE Ticker = $1
        ORDER BY Date
    """, [ticker])

    records = [_clean_dict(row.to_dict()) for _, row in df.iterrows()]
    return {"ticker": ticker, "count": len(records), "data": records}


@router.get("/{ticker}/financials")
def stock_financials(ticker: str):
    """季報財務."""
    df = query_df("""
        SELECT * FROM financials
        WHERE CAST(Ticker AS VARCHAR) = $1
        ORDER BY Year DESC, Season DESC
    """, [ticker])

    records = [_clean_dict(row.to_dict()) for _, row in df.iterrows()]
    return {"ticker": ticker, "count": len(records), "data": records}


@router.get("/{ticker}/institutional")
def stock_institutional(ticker: str, days: int = Query(60, ge=1, le=500)):
    """法人買賣超."""
    df = query_df(f"""
        SELECT Date, Foreign_BuySell, Trust_BuySell, Dealer_BuySell
        FROM daily_k
        WHERE Ticker = $1
        ORDER BY Date DESC
        LIMIT {days}
    """, [ticker])

    df = df.iloc[::-1]
    records = [_clean_dict(row.to_dict()) for _, row in df.iterrows()]
    return {"ticker": ticker, "count": len(records), "data": records}


@router.get("/{ticker}/margin")
def stock_margin(ticker: str, days: int = Query(60, ge=1, le=500)):
    """融資券餘額."""
    df = query_df(f"""
        SELECT Date, Margin_Balance, Short_Balance
        FROM daily_k
        WHERE Ticker = $1
        ORDER BY Date DESC
        LIMIT {days}
    """, [ticker])

    df = df.iloc[::-1]
    records = [_clean_dict(row.to_dict()) for _, row in df.iterrows()]
    return {"ticker": ticker, "count": len(records), "data": records}


# --- helpers ---

def _float(val) -> float | None:
    import math
    if val is None:
        return None
    try:
        f = float(val)
        return None if math.isnan(f) else round(f, 2)
    except (ValueError, TypeError):
        return None


def _int(val) -> int | None:
    import math
    if val is None:
        return None
    try:
        f = float(val)
        return None if math.isnan(f) else int(f)
    except (ValueError, TypeError):
        return None


def _clean_dict(d: dict) -> dict:
    """清理 dict 中的 NaN 值."""
    import math
    out = {}
    for k, v in d.items():
        if isinstance(v, float) and math.isnan(v):
            out[k] = None
        else:
            out[k] = v
    return out
