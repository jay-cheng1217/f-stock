"""排行計算服務."""

from backend.db.engine import query_df
from backend.services.cache_service import cached


@cached("ranking", ttl=600)
def get_ranking(dimension: str, n: int = 50) -> list[dict]:
    """依指定面向取得排行榜."""

    if dimension == "score":
        # 需要全量計算 — 由 scoring_service 處理
        return _rank_by_score(n)

    elif dimension == "revenue_growth":
        return _rank_by_revenue_growth(n)

    elif dimension == "foreign_buy":
        return _rank_by_institutional("Foreign_BuySell", n)

    elif dimension == "trust_buy":
        return _rank_by_institutional("Trust_BuySell", n)

    elif dimension == "volume":
        return _rank_by_volume(n)

    elif dimension == "gross_margin":
        return _rank_by_margin("Gross_Margin_Pct", n)

    elif dimension == "operating_margin":
        return _rank_by_margin("Operating_Margin_Pct", n)

    elif dimension == "gainers":
        return _rank_by_change(n, ascending=False)

    elif dimension == "losers":
        return _rank_by_change(n, ascending=True)

    else:
        return []


def _rank_by_score(n: int) -> list[dict]:
    """綜合評分排行 — 批次計算所有股票."""
    from backend.services.scoring_service import score_stock

    tickers_df = query_df("SELECT Ticker FROM stock_list ORDER BY Ticker")
    results = []
    for _, row in tickers_df.iterrows():
        try:
            s = score_stock(row["Ticker"])
            results.append(s)
        except Exception:
            pass

    results.sort(key=lambda x: x["total_score"], reverse=True)
    out = []
    for i, r in enumerate(results[:n]):
        out.append({
            "rank": i + 1,
            "ticker": r["ticker"],
            "name": r["name"],
            "value": r["total_score"],
            "extra": {"signal": r["signal"]},
        })
    return out


def _rank_by_revenue_growth(n: int) -> list[dict]:
    df = query_df(f"""
        WITH latest AS (
            SELECT Ticker, Name, YoY_pct_change,
                   ROW_NUMBER() OVER (PARTITION BY Ticker ORDER BY Date DESC) AS rn
            FROM revenue
        )
        SELECT Ticker, Name, YoY_pct_change
        FROM latest
        WHERE rn = 1 AND YoY_pct_change IS NOT NULL
        ORDER BY YoY_pct_change DESC
        LIMIT {n}
    """)
    return [
        {
            "rank": i + 1,
            "ticker": row["Ticker"],
            "name": row.get("Name", ""),
            "value": round(float(row["YoY_pct_change"]), 2),
            "extra": None,
        }
        for i, row in df.iterrows()
    ]


def _rank_by_institutional(col: str, n: int) -> list[dict]:
    df = query_df(f"""
        WITH recent AS (
            SELECT Ticker, {col},
                   ROW_NUMBER() OVER (PARTITION BY Ticker ORDER BY Date DESC) AS rn
            FROM daily_k
        )
        SELECT r.Ticker, sl.Name,
               SUM(r.{col}) AS total_5d
        FROM recent r
        LEFT JOIN stock_list sl ON r.Ticker = sl.Ticker
        WHERE r.rn <= 5
        GROUP BY r.Ticker, sl.Name
        HAVING total_5d IS NOT NULL
        ORDER BY total_5d DESC
        LIMIT {n}
    """)
    return [
        {
            "rank": i + 1,
            "ticker": row["Ticker"],
            "name": row.get("Name", ""),
            "value": round(float(row["total_5d"]), 0),
            "extra": None,
        }
        for i, row in df.iterrows()
    ]


def _rank_by_volume(n: int) -> list[dict]:
    df = query_df(f"""
        WITH latest AS (
            SELECT Ticker, Volume,
                   ROW_NUMBER() OVER (PARTITION BY Ticker ORDER BY Date DESC) AS rn
            FROM daily_k
        )
        SELECT l.Ticker, sl.Name, l.Volume
        FROM latest l
        LEFT JOIN stock_list sl ON l.Ticker = sl.Ticker
        WHERE l.rn = 1 AND l.Volume IS NOT NULL
        ORDER BY l.Volume DESC
        LIMIT {n}
    """)
    return [
        {
            "rank": i + 1,
            "ticker": row["Ticker"],
            "name": row.get("Name", ""),
            "value": int(row["Volume"]),
            "extra": None,
        }
        for i, row in df.iterrows()
    ]


def _rank_by_margin(col: str, n: int) -> list[dict]:
    df = query_df(f"""
        WITH latest AS (
            SELECT Ticker, Name, {col},
                   ROW_NUMBER() OVER (PARTITION BY Ticker ORDER BY Year DESC, Season DESC) AS rn
            FROM financials
        )
        SELECT Ticker, Name, {col} AS val
        FROM latest
        WHERE rn = 1 AND {col} IS NOT NULL
        ORDER BY {col} DESC
        LIMIT {n}
    """)
    return [
        {
            "rank": i + 1,
            "ticker": str(row["Ticker"]),
            "name": row.get("Name", ""),
            "value": round(float(row["val"]), 2),
            "extra": None,
        }
        for i, row in df.iterrows()
    ]


def _rank_by_change(n: int, ascending: bool) -> list[dict]:
    order = "ASC" if ascending else "DESC"
    df = query_df(f"""
        SELECT Ticker, Last_Close, Last_Change_Pct, Name
        FROM stock_list
        WHERE Last_Change_Pct IS NOT NULL
        ORDER BY Last_Change_Pct {order}
        LIMIT {n}
    """)
    return [
        {
            "rank": i + 1,
            "ticker": row["Ticker"],
            "name": row.get("Name", ""),
            "value": round(float(row["Last_Change_Pct"]), 2),
            "extra": {"price": float(row["Last_Close"]) if row["Last_Close"] else None},
        }
        for i, row in df.iterrows()
    ]
