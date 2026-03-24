"""大盤概況 API."""

import math
from fastapi import APIRouter, Query
from backend.db.engine import query_df

router = APIRouter(prefix="/api/market", tags=["market"])


@router.get("/indices")
def market_indices():
    """大盤指數最新資料."""
    df = query_df("""
        WITH latest AS (
            SELECT Index_Name, Close, Date,
                   ROW_NUMBER() OVER (PARTITION BY Index_Name ORDER BY Date DESC) AS rn
            FROM indices
        ),
        prev AS (
            SELECT Index_Name, Close AS Prev_Close,
                   ROW_NUMBER() OVER (PARTITION BY Index_Name ORDER BY Date DESC) AS rn
            FROM indices
        )
        SELECT l.Index_Name, l.Close, l.Date,
               p.Prev_Close
        FROM latest l
        LEFT JOIN prev p ON l.Index_Name = p.Index_Name AND p.rn = 2
        WHERE l.rn = 1
    """)

    # 固定顯示順序
    display_order = ["TWII", "GSPC", "SOX", "VIX", "USDTWDX"]
    name_map = {
        "TWII": "加權指數",
        "GSPC": "S&P 500",
        "SOX": "費城半導體",
        "VIX": "VIX恐慌指數",
        "USDTWDX": "美元/台幣",
    }

    row_map = {}
    for _, row in df.iterrows():
        close = _float(row["Close"])
        prev = _float(row.get("Prev_Close"))
        change = round(close - prev, 2) if close and prev else None
        change_pct = round(change / prev * 100, 2) if change and prev else None
        code = row["Index_Name"]
        row_map[code] = {
            "name": name_map.get(code, code),
            "code": code,
            "last_close": close,
            "change": change,
            "change_pct": change_pct,
            "last_date": str(row["Date"]),
        }

    results = [row_map[k] for k in display_order if k in row_map]
    # 未在 display_order 中的指數放最後
    for code in row_map:
        if code not in display_order:
            results.append(row_map[code])

    return results


@router.get("/overview")
def market_overview():
    """市場概況: 漲跌幅最大、成交量最大."""
    gainers = query_df("""
        SELECT Ticker, Name, Last_Close, Last_Change_Pct, Last_Volume
        FROM stock_list
        WHERE Last_Change_Pct IS NOT NULL
        ORDER BY Last_Change_Pct DESC
        LIMIT 10
    """)
    losers = query_df("""
        SELECT Ticker, Name, Last_Close, Last_Change_Pct, Last_Volume
        FROM stock_list
        WHERE Last_Change_Pct IS NOT NULL
        ORDER BY Last_Change_Pct ASC
        LIMIT 10
    """)
    volume = query_df("""
        SELECT Ticker, Name, Last_Close, Last_Change_Pct, Last_Volume
        FROM stock_list
        WHERE Last_Volume IS NOT NULL
        ORDER BY Last_Volume DESC
        LIMIT 10
    """)

    def _to_list(df):
        return [
            {
                "ticker": row["Ticker"],
                "name": row.get("Name", ""),
                "last_close": _float(row.get("Last_Close")),
                "last_change_pct": _float(row.get("Last_Change_Pct")),
                "last_volume": _int(row.get("Last_Volume")),
            }
            for _, row in df.iterrows()
        ]

    return {
        "top_gainers": _to_list(gainers),
        "top_losers": _to_list(losers),
        "top_volume": _to_list(volume),
    }


def _float(val):
    if val is None:
        return None
    try:
        f = float(val)
        return None if math.isnan(f) else round(f, 2)
    except (ValueError, TypeError):
        return None


def _int(val):
    if val is None:
        return None
    try:
        f = float(val)
        return None if math.isnan(f) else int(f)
    except (ValueError, TypeError):
        return None
