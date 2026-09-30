from __future__ import annotations

import json
import math
from datetime import date, datetime
from typing import Any

import duckdb
import pandas as pd

from backend.config import DUCKDB_PATH
from backend.db.ingest import ensure_narrative_schema
from .labeler import taxonomy_half_life_lookup


FEATURE_COLUMNS = [
    "date",
    "ticker",
    "active_labels",
    "heat_3d",
    "heat_14d",
    "decay_slope",
    "top_label",
    "label_age_days",
    "article_count_7d",
]


def _as_date(value: str | date | None = None) -> date:
    if value is None:
        return date.today()
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    return pd.to_datetime(value).date()


def _empty_features(as_of: date) -> pd.DataFrame:
    return pd.DataFrame(columns=FEATURE_COLUMNS).assign(date=pd.Series(dtype="object"))


def _table_exists(conn: duckdb.DuckDBPyConnection, table: str) -> bool:
    return bool(
        conn.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_name = ?",
            [table],
        ).fetchone()[0]
    )


def build_daily_features(
    conn: duckdb.DuckDBPyConnection,
    as_of_date: str | date | None = None,
    min_confidence: float = 0.7,
) -> pd.DataFrame:
    """Build and persist daily ticker narrative heat features."""
    ensure_narrative_schema(conn)
    as_of = _as_date(as_of_date)
    required = {"news_articles", "news_ticker_links", "news_narrative_labels"}
    if not all(_table_exists(conn, table) for table in required):
        return _empty_features(as_of)

    raw = conn.execute(
        """
        SELECT
            a.news_id,
            a.published_at,
            a.title,
            a.content_hash,
            l.ticker,
            l.confidence,
            n.label
        FROM news_articles a
        JOIN news_ticker_links l ON a.news_id = l.news_id
        JOIN news_narrative_labels n ON a.news_id = n.news_id
        WHERE l.confidence >= ?
          AND CAST(a.published_at AS DATE) <= ?
          AND CAST(a.published_at AS DATE) >= ?::DATE - INTERVAL 120 DAY
        """,
        [float(min_confidence), as_of.isoformat(), as_of.isoformat()],
    ).fetchdf()
    if raw.empty:
        conn.execute("DELETE FROM daily_ticker_narrative_features WHERE date = ?", [as_of.isoformat()])
        conn.commit()
        return _empty_features(as_of)

    raw["published_date"] = pd.to_datetime(raw["published_at"], errors="coerce").dt.date
    raw = raw.dropna(subset=["published_date", "ticker", "label"])
    raw = raw.drop_duplicates(subset=["ticker", "label", "content_hash"], keep="first")
    half_life = taxonomy_half_life_lookup()

    rows: list[dict[str, Any]] = []
    grouped = raw.groupby("ticker", dropna=False)
    for ticker, frame in grouped:
        label_heat: dict[str, float] = {}
        label_first_seen: dict[str, int] = {}
        article_ids_7d: set[str] = set()
        heat_3d = 0.0
        heat_14d = 0.0

        for _, row in frame.iterrows():
            age_days = max((as_of - row["published_date"]).days, 0)
            label = str(row["label"])
            hl = max(int(half_life.get(label, 30)), 1)
            contribution = math.exp(-age_days / hl)
            if age_days <= 3:
                heat_3d += contribution
            if age_days <= 14:
                heat_14d += contribution
                label_heat[label] = label_heat.get(label, 0.0) + contribution
            if age_days <= 7:
                article_ids_7d.add(str(row["news_id"]))
            if label not in label_first_seen or age_days > label_first_seen[label]:
                label_first_seen[label] = age_days

        active_labels = [
            label
            for label, _ in sorted(label_heat.items(), key=lambda item: (-item[1], item[0]))
        ]
        top_label = active_labels[0] if active_labels else None
        decay_slope = (heat_3d - heat_14d) / heat_14d if heat_14d > 0 else 0.0
        rows.append(
            {
                "date": as_of.isoformat(),
                "ticker": str(ticker),
                "active_labels": json.dumps(active_labels, ensure_ascii=False),
                "heat_3d": round(float(heat_3d), 6),
                "heat_14d": round(float(heat_14d), 6),
                "decay_slope": round(float(decay_slope), 6),
                "top_label": top_label,
                "label_age_days": int(label_first_seen.get(top_label, 0)) if top_label else 0,
                "article_count_7d": int(len(article_ids_7d)),
            }
        )

    out = pd.DataFrame(rows, columns=FEATURE_COLUMNS)
    conn.execute("DELETE FROM daily_ticker_narrative_features WHERE date = ?", [as_of.isoformat()])
    if not out.empty:
        conn.register("narrative_feature_rows", out)
        conn.execute("INSERT INTO daily_ticker_narrative_features SELECT * FROM narrative_feature_rows")
        conn.unregister("narrative_feature_rows")
    conn.commit()
    return out


def get_narrative_labels_for_ticker(
    ticker: str,
    conn: duckdb.DuckDBPyConnection | None = None,
    limit: int = 8,
) -> list[dict[str, Any]]:
    """Read latest shadow narrative labels for prediction_explain."""
    owns_conn = conn is None
    db = conn or duckdb.connect(DUCKDB_PATH, read_only=True)
    try:
        if not _table_exists(db, "daily_ticker_narrative_features"):
            return []
        df = db.execute(
            """
            SELECT *
            FROM daily_ticker_narrative_features
            WHERE ticker = ?
            ORDER BY date DESC
            LIMIT 1
            """,
            [str(ticker)],
        ).fetchdf()
    except Exception:
        return []
    finally:
        if owns_conn:
            db.close()

    if df.empty:
        return []
    row = df.iloc[0]
    feature_date = pd.to_datetime(row.get("date"), errors="coerce")
    date_text = (
        feature_date.date().isoformat()
        if not pd.isna(feature_date)
        else str(row.get("date"))
    )
    try:
        labels = json.loads(str(row.get("active_labels") or "[]"))
    except json.JSONDecodeError:
        labels = []
    return [
        {
            "label": str(label),
            "date": date_text,
            "top_label": str(row.get("top_label") or ""),
            "heat_3d": round(float(row.get("heat_3d") or 0.0), 6),
            "heat_14d": round(float(row.get("heat_14d") or 0.0), 6),
            "decay_slope": round(float(row.get("decay_slope") or 0.0), 6),
            "label_age_days": int(row.get("label_age_days") or 0),
            "article_count_7d": int(row.get("article_count_7d") or 0),
        }
        for label in labels[: max(int(limit), 1)]
    ]
