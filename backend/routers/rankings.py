"""排行榜 API."""

from fastapi import APIRouter, Query
from backend.services.ranking_service import get_ranking

router = APIRouter(prefix="/api/rankings", tags=["rankings"])

VALID_DIMENSIONS = [
    "score", "revenue_growth", "foreign_buy", "trust_buy",
    "volume", "gross_margin", "operating_margin", "gainers", "losers",
]


@router.get("/{dimension}")
def ranking(
    dimension: str,
    n: int = Query(50, ge=1, le=500),
):
    """各面向排行榜."""
    if dimension not in VALID_DIMENSIONS:
        return {"error": f"不支援的維度: {dimension}", "valid": VALID_DIMENSIONS}
    return {"dimension": dimension, "data": get_ranking(dimension, n)}


@router.get("/screener/filter")
def screener(
    min_price: float = Query(None, description="最低價格"),
    max_price: float = Query(None, description="最高價格"),
    min_volume: int = Query(None, description="最低成交量"),
    min_score: float = Query(None, description="最低評分"),
    n: int = Query(50, ge=1, le=500),
):
    """條件選股."""
    from backend.db.engine import query_df

    conditions = ["1=1"]
    if min_price is not None:
        conditions.append(f"Last_Close >= {min_price}")
    if max_price is not None:
        conditions.append(f"Last_Close <= {max_price}")
    if min_volume is not None:
        conditions.append(f"Last_Volume >= {min_volume}")

    where = " AND ".join(conditions)
    df = query_df(f"""
        SELECT * FROM stock_list
        WHERE {where}
        ORDER BY Last_Volume DESC NULLS LAST
        LIMIT {n}
    """)

    records = []
    for _, row in df.iterrows():
        records.append({
            "ticker": row["Ticker"],
            "name": row.get("Name", ""),
            "last_close": _float(row.get("Last_Close")),
            "last_volume": _int(row.get("Last_Volume")),
            "last_change_pct": _float(row.get("Last_Change_Pct")),
        })
    return {"count": len(records), "data": records}


def _float(val):
    import math
    if val is None:
        return None
    try:
        f = float(val)
        return None if math.isnan(f) else round(f, 2)
    except (ValueError, TypeError):
        return None


def _int(val):
    import math
    if val is None:
        return None
    try:
        f = float(val)
        return None if math.isnan(f) else int(f)
    except (ValueError, TypeError):
        return None
