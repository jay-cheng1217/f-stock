from __future__ import annotations

import json
import os
import sqlite3
from functools import lru_cache
from typing import Any

import pandas as pd
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from backend.services.warroom_service import CHAMPION_DB_PATH, SHADOW_DB_PATH
from ml.features.sector import SECTOR_MAPPING_PATH

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
UNIFIED_PORTFOLIO_START_DATE = "2026-04-09"
CHALLENGER_DB_PATH = SHADOW_DB_PATH

router = APIRouter(tags=["shadow"])


@lru_cache(maxsize=1)
def _load_name_lookup() -> dict[str, str]:
    if not os.path.exists(SECTOR_MAPPING_PATH):
        return {}

    df = pd.read_csv(SECTOR_MAPPING_PATH, dtype={"Ticker": str})
    if "Name" not in df.columns:
        return {}
    return dict(zip(df["Ticker"], df["Name"]))


def _connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _latest_mark_cte() -> str:
    return """
        WITH latest_marks AS (
            SELECT
                position_id,
                mark_date,
                close,
                close_return_pct,
                ROW_NUMBER() OVER (
                    PARTITION BY position_id
                    ORDER BY mark_date DESC
                ) AS rn
            FROM unified_marks
        )
    """


def _safe_records(df: pd.DataFrame) -> list[dict[str, Any]]:
    if df.empty:
        return []
    return json.loads(df.to_json(orient="records", force_ascii=False))


def _empty_book(label: str, db_path: str, expected_exit_policy: str) -> dict[str, Any]:
    return {
        "label": label,
        "db_path": db_path,
        "exists": False,
        "expected_exit_policy": expected_exit_policy,
        "summary": {
            "latest_run_date": None,
            "latest_signal_date": None,
            "latest_mark_date": None,
            "latest_rule_version": None,
            "latest_run_signal_file": None,
            "latest_run_exit_policies": [],
            "total_positions": 0,
            "active_positions": 0,
            "pending_positions": 0,
            "open_positions": 0,
            "closed_positions": 0,
            "stopped_out_positions": 0,
            "invested_weight_pct": 0.0,
            "avg_open_return_pct": None,
            "avg_closed_return_pct": None,
        },
        "active_positions": [],
    }


def _load_book(label: str, db_path: str, expected_exit_policy: str) -> dict[str, Any]:
    if not os.path.exists(db_path):
        return _empty_book(label, db_path, expected_exit_policy)

    conn = _connect(db_path)
    try:
        run_row = conn.execute(
            """
            SELECT
                prediction_date,
                rule_version,
                signals_file_path
            FROM unified_runs
            WHERE prediction_date >= ?
            ORDER BY prediction_date DESC
            LIMIT 1
            """,
            (UNIFIED_PORTFOLIO_START_DATE,),
        ).fetchone()

        latest_run_date = str(run_row["prediction_date"]) if run_row else None
        latest_rule_version = str(run_row["rule_version"]) if run_row else None
        latest_signal_file = (
            os.path.basename(str(run_row["signals_file_path"]))
            if run_row and run_row["signals_file_path"]
            else None
        )

        summary_row = conn.execute(
            f"""
            {_latest_mark_cte()}
            SELECT
                MAX(p.last_signal_date) AS latest_signal_date,
                MAX(COALESCE(lm.mark_date, p.last_mark_date, p.exit_date)) AS latest_mark_date,
                COUNT(*) AS total_positions,
                SUM(CASE WHEN p.status IN ('pending', 'open') THEN 1 ELSE 0 END) AS active_positions,
                SUM(CASE WHEN p.status = 'pending' THEN 1 ELSE 0 END) AS pending_positions,
                SUM(CASE WHEN p.status = 'open' THEN 1 ELSE 0 END) AS open_positions,
                SUM(CASE WHEN p.status = 'closed' THEN 1 ELSE 0 END) AS closed_positions,
                SUM(CASE WHEN p.status = 'stopped_out' THEN 1 ELSE 0 END) AS stopped_out_positions,
                SUM(CASE WHEN p.status IN ('pending', 'open') THEN p.target_weight ELSE 0 END) * 100.0 AS invested_weight_pct,
                AVG(CASE WHEN p.status = 'open' THEN lm.close_return_pct END) * 100.0 AS avg_open_return_pct,
                AVG(CASE WHEN p.status IN ('closed', 'stopped_out') THEN p.realized_return_pct END) * 100.0 AS avg_closed_return_pct
            FROM unified_positions p
            LEFT JOIN latest_marks lm
              ON lm.position_id = p.id
             AND lm.rn = 1
            WHERE p.prediction_date >= ?
            """,
            (UNIFIED_PORTFOLIO_START_DATE,),
        ).fetchone()

        exit_policy_rows = []
        if latest_run_date:
            exit_policy_rows = conn.execute(
                """
                SELECT exit_policy, COUNT(*) AS position_count
                FROM unified_positions
                WHERE prediction_date = ?
                GROUP BY exit_policy
                ORDER BY position_count DESC, exit_policy ASC
                """,
                (latest_run_date,),
            ).fetchall()

        active_df = pd.read_sql_query(
            f"""
            {_latest_mark_cte()}
            SELECT
                p.prediction_date,
                p.ticker,
                p.sector,
                p.current_signal_type AS signal_type,
                p.status,
                ROUND(p.target_weight * 100.0, 2) AS target_weight_pct,
                p.entry_date,
                p.entry_price,
                p.exit_policy,
                COALESCE(lm.mark_date, p.last_mark_date, p.exit_date) AS latest_mark_date,
                COALESCE(lm.close, p.exit_price) AS latest_price,
                ROUND(
                    CASE
                        WHEN p.status IN ('closed', 'stopped_out') THEN p.realized_return_pct * 100.0
                        ELSE lm.close_return_pct * 100.0
                    END,
                    2
                ) AS return_pct
            FROM unified_positions p
            LEFT JOIN latest_marks lm
              ON lm.position_id = p.id
             AND lm.rn = 1
            WHERE p.prediction_date >= ?
              AND p.status IN ('pending', 'open')
            ORDER BY p.target_weight DESC, p.prediction_date DESC, p.ticker ASC
            LIMIT 30
            """,
            conn,
            params=(UNIFIED_PORTFOLIO_START_DATE,),
        )
    finally:
        conn.close()

    summary = {
        "latest_run_date": latest_run_date,
        "latest_signal_date": summary_row["latest_signal_date"] if summary_row else None,
        "latest_mark_date": summary_row["latest_mark_date"] if summary_row else None,
        "latest_rule_version": latest_rule_version,
        "latest_run_signal_file": latest_signal_file,
        "latest_run_exit_policies": [
            {
                "exit_policy": str(row["exit_policy"]),
                "position_count": int(row["position_count"] or 0),
            }
            for row in exit_policy_rows
        ],
        "total_positions": int((summary_row["total_positions"] or 0) if summary_row else 0),
        "active_positions": int((summary_row["active_positions"] or 0) if summary_row else 0),
        "pending_positions": int((summary_row["pending_positions"] or 0) if summary_row else 0),
        "open_positions": int((summary_row["open_positions"] or 0) if summary_row else 0),
        "closed_positions": int((summary_row["closed_positions"] or 0) if summary_row else 0),
        "stopped_out_positions": int((summary_row["stopped_out_positions"] or 0) if summary_row else 0),
        "invested_weight_pct": round(float((summary_row["invested_weight_pct"] or 0.0) if summary_row else 0.0), 2),
        "avg_open_return_pct": round(float(summary_row["avg_open_return_pct"]), 2)
        if summary_row and summary_row["avg_open_return_pct"] is not None
        else None,
        "avg_closed_return_pct": round(float(summary_row["avg_closed_return_pct"]), 2)
        if summary_row and summary_row["avg_closed_return_pct"] is not None
        else None,
    }

    names = _load_name_lookup()
    if not active_df.empty:
        active_df["ticker"] = active_df["ticker"].astype(str)
        active_df["name"] = active_df["ticker"].map(names).fillna("")

    return {
        "label": label,
        "db_path": db_path,
        "exists": True,
        "expected_exit_policy": expected_exit_policy,
        "summary": summary,
        "active_positions": _safe_records(active_df),
    }


def _build_overlap(champion: dict[str, Any], challenger: dict[str, Any]) -> dict[str, Any]:
    champion_tickers = sorted(
        {
            str(row.get("ticker"))
            for row in champion.get("active_positions", [])
            if row.get("ticker")
        }
    )
    challenger_tickers = sorted(
        {
            str(row.get("ticker"))
            for row in challenger.get("active_positions", [])
            if row.get("ticker")
        }
    )

    champion_set = set(champion_tickers)
    challenger_set = set(challenger_tickers)
    shared = sorted(champion_set & challenger_set)
    champion_only = sorted(champion_set - challenger_set)
    challenger_only = sorted(challenger_set - champion_set)

    champion_count = len(champion_tickers)
    challenger_count = len(challenger_tickers)

    return {
        "shared_count": len(shared),
        "champion_count": champion_count,
        "challenger_count": challenger_count,
        "shared_ratio_vs_champion": round(len(shared) / champion_count, 4) if champion_count else None,
        "shared_ratio_vs_challenger": round(len(shared) / challenger_count, 4) if challenger_count else None,
        "shared": shared,
        "champion_only": champion_only,
        "challenger_only": challenger_only,
    }


@router.get("/api/shadow/report")
def get_shadow_report():
    champion = _load_book(
        label="Two-Stage Champion",
        db_path=CHAMPION_DB_PATH,
        expected_exit_policy="asymmetric_v2",
    )
    challenger = _load_book(
        label="Baseline Shadow",
        db_path=CHALLENGER_DB_PATH,
        expected_exit_policy="baseline_with_ma20",
    )
    overlap = _build_overlap(champion, challenger)

    latest_dates = [
        book.get("summary", {}).get("latest_mark_date")
        or book.get("summary", {}).get("latest_signal_date")
        or book.get("summary", {}).get("latest_run_date")
        for book in (champion, challenger)
        if book.get("exists")
    ]

    return JSONResponse(
        content={
            "status": "success" if champion.get("exists") else "not_ready",
            "as_of_date": max(latest_dates) if latest_dates else None,
            "champion": champion,
            "challenger": challenger,
            "overlap": overlap,
        }
    )
