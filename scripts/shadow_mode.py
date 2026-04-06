"""Helpers for shadow-mode monitoring between production and shadow ledgers."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime

BASE_DIR = r"F:\stock"
sys.path.insert(0, BASE_DIR)

from ml.model_selection import get_slot_label
from scripts.update_paper_portfolio import (
    DEFAULT_DB_PATH as PROD_DB_PATH,
    PORTFOLIO_START_DATE,
    connect_db,
    summarize_ledger,
)

DEFAULT_SHADOW_DB_PATH = os.path.join(BASE_DIR, "paper_portfolio_shadow_v23.db")
DEFAULT_REPORT_PATH = os.path.join(BASE_DIR, "ml", "reports", "shadow_mode_latest.json")


def _safe_ratio(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return float(numerator / denominator)


def _latest_run_snapshot(conn: sqlite3.Connection) -> dict[str, object]:
    run_row = conn.execute(
        """
        SELECT id, prediction_date, prediction_path, rule_version, top_n
        FROM portfolio_runs
        WHERE prediction_date >= ?
        ORDER BY prediction_date DESC, id DESC
        LIMIT 1
        """,
        (PORTFOLIO_START_DATE,),
    ).fetchone()
    if run_row is None:
        return {}

    positions = conn.execute(
        """
        SELECT selection_rank, ticker, sector, recommendation
        FROM portfolio_positions
        WHERE run_id = ?
        ORDER BY selection_rank ASC
        """,
        (int(run_row["id"]),),
    ).fetchall()

    return {
        "run_id": int(run_row["id"]),
        "prediction_date": str(run_row["prediction_date"]),
        "prediction_path": str(run_row["prediction_path"]),
        "rule_version": str(run_row["rule_version"]),
        "top_n": int(run_row["top_n"]),
        "tickers": [str(row["ticker"]) for row in positions],
        "positions": [
            {
                "selection_rank": int(row["selection_rank"]),
                "ticker": str(row["ticker"]),
                "sector": str(row["sector"] or ""),
                "recommendation": str(row["recommendation"] or ""),
            }
            for row in positions
        ],
    }


def _mark_to_market_book_mdd(conn: sqlite3.Connection) -> dict[str, float | str | None]:
    rows = conn.execute(
        """
        WITH daily_book AS (
            SELECT
                m.mark_date,
                AVG(1.0 + COALESCE(m.close_return_pct, 0.0)) AS book_value
            FROM portfolio_marks m
            JOIN portfolio_positions p ON p.id = m.position_id
            WHERE p.prediction_date >= ?
            GROUP BY m.mark_date
        )
        SELECT mark_date, book_value
        FROM daily_book
        ORDER BY mark_date
        """,
        (PORTFOLIO_START_DATE,),
    ).fetchall()

    if not rows:
        return {
            "latest_mark_date": None,
            "latest_book_value": None,
            "book_mdd_pct": None,
        }

    peak = None
    book_mdd = 0.0
    latest_mark_date = None
    latest_book_value = None

    for row in rows:
        value = float(row["book_value"])
        latest_mark_date = str(row["mark_date"])
        latest_book_value = value
        peak = value if peak is None else max(peak, value)
        if peak and peak > 0:
            book_mdd = min(book_mdd, value / peak - 1.0)

    return {
        "latest_mark_date": latest_mark_date,
        "latest_book_value": latest_book_value,
        "book_mdd_pct": book_mdd,
    }


def _load_ledger_snapshot(db_path: str, slot: str) -> dict[str, object]:
    if not os.path.exists(db_path):
        return {
            "slot": slot,
            "label": get_slot_label(slot),
            "db_path": os.path.abspath(db_path),
            "exists": False,
            "summary": {},
            "latest_run": {},
            "monitor": {},
        }

    with connect_db(db_path) as conn:
        summary = summarize_ledger(conn)
        latest_run = _latest_run_snapshot(conn)
        monitor = _mark_to_market_book_mdd(conn)

    total_positions = int(summary.get("total_positions", 0) or 0)
    stopped_out_positions = int(summary.get("stopped_out_positions", 0) or 0)
    observed_positions = int(summary.get("observed_positions", 0) or 0)
    breach_10pct_count = int(summary.get("breach_10pct_count", 0) or 0)

    summary = dict(summary)
    summary["stop_loss_rate"] = _safe_ratio(stopped_out_positions, total_positions)
    summary["breach_10pct_rate"] = _safe_ratio(breach_10pct_count, observed_positions)

    return {
        "slot": slot,
        "label": get_slot_label(slot),
        "db_path": os.path.abspath(db_path),
        "exists": True,
        "summary": summary,
        "latest_run": latest_run,
        "monitor": monitor,
    }


def _build_overlap(prod: dict[str, object], shadow: dict[str, object]) -> dict[str, object]:
    prod_run = prod.get("latest_run", {}) or {}
    shadow_run = shadow.get("latest_run", {}) or {}

    prod_tickers = list(prod_run.get("tickers", []))
    shadow_tickers = list(shadow_run.get("tickers", []))
    overlap = sorted(set(prod_tickers) & set(shadow_tickers))
    union = sorted(set(prod_tickers) | set(shadow_tickers))

    return {
        "production_date": prod_run.get("prediction_date"),
        "shadow_date": shadow_run.get("prediction_date"),
        "same_prediction_date": prod_run.get("prediction_date") == shadow_run.get("prediction_date"),
        "overlap_count": len(overlap),
        "overlap_ratio_vs_prod": _safe_ratio(len(overlap), len(prod_tickers)),
        "overlap_ratio_vs_shadow": _safe_ratio(len(overlap), len(shadow_tickers)),
        "union_count": len(union),
        "production_only": sorted(set(prod_tickers) - set(shadow_tickers)),
        "shadow_only": sorted(set(shadow_tickers) - set(prod_tickers)),
        "overlap_tickers": overlap,
    }


def build_shadow_mode_report(
    production_db_path: str = PROD_DB_PATH,
    shadow_db_path: str = DEFAULT_SHADOW_DB_PATH,
    output_path: str = DEFAULT_REPORT_PATH,
) -> dict[str, object]:
    production = _load_ledger_snapshot(production_db_path, slot="production")
    shadow = _load_ledger_snapshot(shadow_db_path, slot="shadow")
    overlap = _build_overlap(production, shadow)

    prod_summary = production.get("summary", {}) or {}
    shadow_summary = shadow.get("summary", {}) or {}
    prod_monitor = production.get("monitor", {}) or {}
    shadow_monitor = shadow.get("monitor", {}) or {}

    report = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "production": production,
        "shadow": shadow,
        "selection_overlap": overlap,
        "comparison": {
            "avg_realized_return_pct_delta": (
                (shadow_summary.get("avg_realized_return_pct") or 0.0)
                - (prod_summary.get("avg_realized_return_pct") or 0.0)
            )
            if production.get("exists") and shadow.get("exists")
            else None,
            "avg_max_drawdown_pct_delta": (
                (shadow_summary.get("avg_max_drawdown_pct") or 0.0)
                - (prod_summary.get("avg_max_drawdown_pct") or 0.0)
            )
            if production.get("exists") and shadow.get("exists")
            else None,
            "stop_loss_rate_delta": (
                (shadow_summary.get("stop_loss_rate") or 0.0)
                - (prod_summary.get("stop_loss_rate") or 0.0)
            )
            if production.get("exists") and shadow.get("exists")
            else None,
            "book_mdd_pct_delta": (
                (shadow_monitor.get("book_mdd_pct") or 0.0)
                - (prod_monitor.get("book_mdd_pct") or 0.0)
            )
            if production.get("exists") and shadow.get("exists")
            else None,
        },
    }

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
    return report


def _fmt_pct(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:+.2%}"


def _print_report(report: dict[str, object]) -> None:
    prod = report["production"]
    shadow = report["shadow"]
    comp = report["comparison"]

    print("[shadow] report updated")
    print(f"  production db: {prod['db_path']}")
    print(f"  shadow db:     {shadow['db_path']}")

    if prod.get("exists"):
        ps = prod["summary"]
        pm = prod["monitor"]
        print(
            f"  production: runs={ps.get('total_runs', 0)} positions={ps.get('total_positions', 0)} "
            f"stop_loss_rate={_fmt_pct(ps.get('stop_loss_rate'))} "
            f"book_mdd={_fmt_pct(pm.get('book_mdd_pct'))}"
        )
    if shadow.get("exists"):
        ss = shadow["summary"]
        sm = shadow["monitor"]
        print(
            f"  shadow:     runs={ss.get('total_runs', 0)} positions={ss.get('total_positions', 0)} "
            f"stop_loss_rate={_fmt_pct(ss.get('stop_loss_rate'))} "
            f"book_mdd={_fmt_pct(sm.get('book_mdd_pct'))}"
        )

    overlap = report["selection_overlap"]
    print(
        f"  latest overlap: {overlap.get('overlap_count', 0)} "
        f"(prod={overlap.get('production_date')}, shadow={overlap.get('shadow_date')})"
    )
    print(
        f"  delta avg_realized={_fmt_pct(comp.get('avg_realized_return_pct_delta'))} "
        f"delta stop_loss_rate={_fmt_pct(comp.get('stop_loss_rate_delta'))} "
        f"delta book_mdd={_fmt_pct(comp.get('book_mdd_pct_delta'))}"
    )


def main(argv: list[str] | None = None) -> dict[str, object]:
    parser = argparse.ArgumentParser(description="Build the latest shadow-mode comparison report.")
    parser.add_argument("--production-db", default=PROD_DB_PATH)
    parser.add_argument("--shadow-db", default=DEFAULT_SHADOW_DB_PATH)
    parser.add_argument("--output-path", default=DEFAULT_REPORT_PATH)
    args = parser.parse_args(argv)

    report = build_shadow_mode_report(
        production_db_path=args.production_db,
        shadow_db_path=args.shadow_db,
        output_path=args.output_path,
    )
    _print_report(report)
    return report


if __name__ == "__main__":
    main()
