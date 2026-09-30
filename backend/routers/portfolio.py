"""Paper Portfolio API endpoints (公開讀取).

從 app.py 抽出。資料來源是 SQLite (paper_portfolio.db)，透過 DuckDB 的 sqlite
extension 讀取，不影響日常 pipeline 對 SQLite 的寫入。
"""
from __future__ import annotations

import json
import os
import traceback
from datetime import datetime

import duckdb
from fastapi import APIRouter
from fastapi.responses import JSONResponse, StreamingResponse

from backend.config import BASE_DIR
from backend.logging_config import error_log
from backend.routers.t1 import _NAME_LOOKUP

router = APIRouter(tags=["portfolio"])

PAPER_PORTFOLIO_DB_PATH = os.path.join(BASE_DIR, "paper_portfolio.db")
PAPER_PORTFOLIO_START_DATE = "2026-03-18"


def _load_duckdb_sqlite_extension(conn) -> None:
    try:
        conn.execute("LOAD sqlite")
    except Exception:
        conn.execute("INSTALL sqlite")
        conn.execute("LOAD sqlite")


def _empty_portfolio_summary() -> dict:
    return {
        "open_positions": 0,
        "pending_positions": 0,
        "open_tickers": 0,
        "alerted_positions": 0,
        "avg_unrealized_pct": None,
        "latest_mark_date": None,
    }


def _load_portfolio_detail_and_summary():
    import pandas as pd

    columns = [
        "ticker",
        "position_count",
        "selection_history",
        "avg_entry_price",
        "latest_price",
        "avg_unrealized_pct",
        "max_drawdown_pct",
        "latest_mark_date",
    ]
    if not os.path.exists(PAPER_PORTFOLIO_DB_PATH):
        return pd.DataFrame(columns=columns), _empty_portfolio_summary()

    conn = None
    try:
        conn = duckdb.connect()
        _load_duckdb_sqlite_extension(conn)
        escaped_db_path = PAPER_PORTFOLIO_DB_PATH.replace("'", "''")
        conn.execute(
            f"ATTACH '{escaped_db_path}' AS ledger (TYPE SQLITE, READ_ONLY)"
        )

        latest_marks_cte = """
            WITH latest_marks AS (
                SELECT position_id, mark_date, close, close_return_pct
                FROM (
                    SELECT
                        position_id,
                        mark_date,
                        close,
                        close_return_pct,
                        ROW_NUMBER() OVER (
                            PARTITION BY position_id
                            ORDER BY mark_date DESC
                        ) AS rn
                    FROM ledger.portfolio_marks
                ) ranked_marks
                WHERE rn = 1
            )
        """

        detail_query = f"""
            {latest_marks_cte}
            SELECT
                p.ticker AS ticker,
                COUNT(*) AS position_count,
                STRING_AGG(
                    p.prediction_date || '|' ||
                    '#' || CAST(p.selection_rank AS VARCHAR) || '|' ||
                    COALESCE(p.recommendation, '未標記'),
                    '\n'
                    ORDER BY p.prediction_date DESC, p.selection_rank ASC
                ) AS selection_history,
                ROUND(AVG(p.entry_open), 2) AS avg_entry_price,
                ROUND(ARG_MAX(lm.close, lm.mark_date), 2) AS latest_price,
                ROUND(AVG(lm.close_return_pct) * 100, 2) AS avg_unrealized_pct,
                ROUND(MIN(p.max_drawdown_pct) * 100, 2) AS max_drawdown_pct,
                MAX(lm.mark_date) AS latest_mark_date
            FROM ledger.portfolio_positions p
            LEFT JOIN latest_marks lm
                ON lm.position_id = p.id
            WHERE p.status = 'open'
              AND p.entry_open IS NOT NULL
              AND p.prediction_date >= '{PAPER_PORTFOLIO_START_DATE}'
            GROUP BY p.ticker
            ORDER BY avg_unrealized_pct DESC NULLS LAST, p.ticker
        """
        detail_df = conn.execute(detail_query).df()

        summary_query = f"""
            {latest_marks_cte}
            SELECT
                SUM(CASE WHEN p.status = 'open' THEN 1 ELSE 0 END) AS open_positions,
                SUM(CASE WHEN p.status = 'pending' THEN 1 ELSE 0 END) AS pending_positions,
                COUNT(DISTINCT CASE WHEN p.status = 'open' THEN p.ticker END) AS open_tickers,
                SUM(
                    CASE
                        WHEN p.status = 'open' AND p.max_drawdown_pct <= -0.10 THEN 1
                        ELSE 0
                    END
                ) AS alerted_positions,
                ROUND(
                    AVG(
                        CASE
                            WHEN p.status = 'open' THEN lm.close_return_pct
                            ELSE NULL
                        END
                    ) * 100,
                    2
                ) AS avg_unrealized_pct,
                MAX(CASE WHEN p.status = 'open' THEN lm.mark_date END) AS latest_mark_date
            FROM ledger.portfolio_positions p
            LEFT JOIN latest_marks lm
                ON lm.position_id = p.id
            WHERE p.prediction_date >= '{PAPER_PORTFOLIO_START_DATE}'
        """
        summary_row = conn.execute(summary_query).fetchone()
        summary = {
            "open_positions": int(summary_row[0] or 0),
            "pending_positions": int(summary_row[1] or 0),
            "open_tickers": int(summary_row[2] or 0),
            "alerted_positions": int(summary_row[3] or 0),
            "avg_unrealized_pct": (
                round(float(summary_row[4]), 2) if summary_row[4] is not None else None
            ),
            "latest_mark_date": summary_row[5],
        }
        return detail_df, summary

    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def _format_selection_history_for_export(history: str) -> str:
    if not history:
        return ""

    items = []
    for raw_line in str(history).splitlines():
        parts = raw_line.split("|", 2)
        if len(parts) != 3:
            continue
        date, rank, recommendation = parts
        items.append(f"{date} {rank} {recommendation}")
    return " / ".join(items)


def _prepare_portfolio_export_df(detail_df):
    export_df = detail_df.copy()
    if "selection_history" in export_df.columns:
        export_df["selection_history"] = export_df["selection_history"].fillna("").apply(
            _format_selection_history_for_export
        )

    rename_map = {
        "ticker": "股票代號",
        "position_count": "持有筆數",
        "selection_history": "入選紀錄",
        "avg_entry_price": "平均進場成本",
        "latest_price": "最新收盤價",
        "avg_unrealized_pct": "平均帳面損益(%)",
        "max_drawdown_pct": "最大盤中回撤(%)",
        "latest_mark_date": "最新更新日",
    }
    available = [col for col in rename_map if col in export_df.columns]
    return export_df.loc[:, available].rename(columns=rename_map)


@router.get("/api/portfolio")
def get_paper_portfolio():
    try:
        detail_df, summary = _load_portfolio_detail_and_summary()

        detail_records = []
        if not detail_df.empty:
            detail_df["name"] = detail_df["ticker"].map(_NAME_LOOKUP).fillna("")
            detail_records = json.loads(
                detail_df.to_json(orient="records", force_ascii=False)
            )

        return JSONResponse(
            content={"status": "success", "data": detail_records, "summary": summary}
        )

    except Exception as e:
        error_log.error(
            "Paper portfolio API failed\n" + traceback.format_exc()
        )
        return JSONResponse(
            content={"status": "error", "message": str(e)},
            status_code=500,
        )


@router.get("/api/portfolio/download")
def download_paper_portfolio():
    try:
        detail_df, summary = _load_portfolio_detail_and_summary()
        export_df = _prepare_portfolio_export_df(detail_df)
        file_date = summary.get("latest_mark_date") or datetime.now().strftime("%Y-%m-%d")
        csv_bytes = export_df.to_csv(index=False).encode("utf-8-sig")
        return StreamingResponse(
            iter([csv_bytes]),
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": (
                    f'attachment; filename="paper_portfolio_{file_date}.csv"'
                )
            },
        )
    except Exception as e:
        error_log.error(
            "Paper portfolio download failed\n" + traceback.format_exc()
        )
        return JSONResponse(
            content={"status": "error", "message": str(e)},
            status_code=500,
        )
