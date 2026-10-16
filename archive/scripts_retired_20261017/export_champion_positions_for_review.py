"""One-off export: Champion paper portfolio positions for external review.

Reads paper_portfolio_v2_champion.db and exports a single CSV that contains,
for every position (open or closed):
  - entry context (date, price, signal type, rank, weight, regime, slippage)
  - exit context (date, price, reason, holding days)
  - realized return, max gain during hold, max drawdown during hold

The script is read-only and does not modify production data.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd

BASE = Path(__file__).resolve().parents[1]
DB_PATH = BASE / "paper_portfolio_v2_champion.db"
OUT_PATH = BASE / "exports" / "champion_positions.csv"


def load_positions(conn: sqlite3.Connection) -> pd.DataFrame:
    return pd.read_sql_query(
        """
        SELECT
            p.id                          AS position_id,
            p.ticker,
            p.sector,
            p.status,
            p.exit_policy                 AS champion_policy,
            p.entry_signal_type,
            p.current_signal_type,
            p.prediction_date             AS entry_signal_date,
            p.entry_date,
            p.planned_entry_ref_price     AS entry_ref_price,
            p.entry_price,
            p.fill_price,
            p.fill_status,
            p.order_type,
            p.entry_slippage_pct,
            p.target_units,
            p.target_weight,
            p.two_stage_rank_at_entry,
            p.price_vs_ma20_at_entry,
            p.narrative_heat_at_entry,
            p.entry_tag,
            p.hold_days_target,
            p.upgrade_count,
            p.days_observed               AS holding_days,
            p.exit_date,
            p.exit_price,
            p.exit_reason,
            p.realized_return_pct,
            p.max_drawdown_pct,
            p.max_drawdown_low,
            p.max_drawdown_date,
            p.last_mark_date,
            p.created_at,
            p.updated_at,
            r.rule_version                AS regime_rule_version
        FROM unified_positions p
        LEFT JOIN unified_runs r ON r.id = p.opened_by_run_id
        ORDER BY p.entry_date, p.id
        """,
        conn,
    )


def compute_mark_extremes(conn: sqlite3.Connection) -> pd.DataFrame:
    """For each position, compute max gain and max drawdown observed across marks."""
    marks = pd.read_sql_query(
        """
        SELECT position_id, mark_date, close_return_pct, intraday_drawdown_pct
        FROM unified_marks
        """,
        conn,
    )
    if marks.empty:
        return pd.DataFrame(columns=["position_id", "max_gain_observed_pct", "max_loss_observed_pct"])
    marks["close_return_pct"] = pd.to_numeric(marks["close_return_pct"], errors="coerce")
    marks["intraday_drawdown_pct"] = pd.to_numeric(marks["intraday_drawdown_pct"], errors="coerce")
    grouped = marks.groupby("position_id").agg(
        max_gain_observed_pct=("close_return_pct", "max"),
        max_loss_observed_pct=("close_return_pct", "min"),
        mark_count=("mark_date", "count"),
    ).reset_index()
    return grouped


def main() -> int:
    if not DB_PATH.exists():
        raise SystemExit(f"DB missing: {DB_PATH}")
    with sqlite3.connect(str(DB_PATH)) as conn:
        positions = load_positions(conn)
        extremes = compute_mark_extremes(conn)

    df = positions.merge(extremes, on="position_id", how="left")
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT_PATH, index=False, encoding="utf-8-sig")

    print(f"wrote rows={len(df)} path={OUT_PATH}")
    entry_dates = pd.to_datetime(df["entry_date"], errors="coerce").dropna()
    if not entry_dates.empty:
        print(f"date range: entry_date {entry_dates.min().date()} -> {entry_dates.max().date()}")
    print(f"status distribution:")
    print(df["status"].value_counts().to_string())
    print(f"\nexit_reason distribution (closed only):")
    closed = df[df["status"].isin(["closed", "stopped_out"])]
    print(closed["exit_reason"].value_counts(dropna=False).to_string())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
