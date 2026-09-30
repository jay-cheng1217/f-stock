"""T+1 1D direction model daily observation tracker.

Appends one row per trading day to a CSV log, tracking the 5 key metrics
for the clean observation period starting 2026-04-17.

Usage:
    python scripts/track_t1_observation.py
"""

from __future__ import annotations

import os
import sqlite3
import sys
from datetime import date

import numpy as np
import pandas as pd

BASE_DIR = os.environ.get("STOCK_BASE_DIR") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from ml.config import MODEL_DIR
from ml.predict_t1 import T1_LEDGER_RESTART_DATE, _check_portfolio_mdd_halt

PORTFOLIO_DB = os.path.join(BASE_DIR, "paper_portfolio_t1.db")
REPORT_DIR = os.path.join(BASE_DIR, "ml", "reports")
TRACKING_CSV = os.path.join(REPORT_DIR, "t1_observation_log.csv")

COLUMNS = [
    "date",
    "selected_count",
    "avg_hit_prob",
    "day_return_pct",
    "cumulative_return_pct",
    "win_rate_pct",
    "expectancy_pct",
    "mdd_pct",
    "mdd_halted",
    "total_closed",
    "total_pending",
]


def _load_existing_log() -> pd.DataFrame:
    if os.path.exists(TRACKING_CSV):
        return pd.read_csv(TRACKING_CSV, dtype={"date": str})
    return pd.DataFrame(columns=COLUMNS)


def _latest_prediction_stats() -> tuple[int, float | None, str]:
    """Return (selected_count, avg_hit_prob, prediction_date) from latest CSV."""
    import glob
    files = sorted(glob.glob(os.path.join(MODEL_DIR, "predictions_t1_*.csv")))
    if not files:
        return 0, None, ""
    latest = files[-1]
    df = pd.read_csv(latest, dtype={"ticker": str})
    pred_date = str(df["date"].iloc[0]) if not df.empty else ""
    if "selected_for_trade" in df.columns:
        sel = df[df["selected_for_trade"].fillna(False).astype(str).str.lower().isin({"1", "true", "yes"})]
    else:
        sel = df.head(10)
    count = len(sel)
    avg_prob = float(sel["hit_prob_3pct"].mean()) if count > 0 and "hit_prob_3pct" in sel.columns else None
    return count, avg_prob, pred_date


def _portfolio_metrics() -> dict:
    """Compute cumulative metrics from paper portfolio since restart date."""
    if not os.path.exists(PORTFOLIO_DB):
        return {}
    conn = sqlite3.connect(PORTFOLIO_DB)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT prediction_date, realized_return_pct, status "
            "FROM t1_positions WHERE prediction_date >= ? "
            "ORDER BY prediction_date",
            (T1_LEDGER_RESTART_DATE,),
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        return {
            "day_return_pct": None,
            "cumulative_return_pct": 0.0,
            "win_rate_pct": None,
            "expectancy_pct": None,
            "mdd_pct": 0.0,
            "total_closed": 0,
            "total_pending": 0,
        }

    df = pd.DataFrame([dict(r) for r in rows])
    closed = df[df["status"] == "closed"].copy()
    pending = df[df["status"].isin(["pending", "open"])].copy()

    if closed.empty:
        return {
            "day_return_pct": None,
            "cumulative_return_pct": 0.0,
            "win_rate_pct": None,
            "expectancy_pct": None,
            "mdd_pct": 0.0,
            "total_closed": 0,
            "total_pending": len(pending),
        }

    daily = closed.groupby("prediction_date")["realized_return_pct"].mean()
    latest_day_return = float(daily.iloc[-1]) if len(daily) > 0 else None

    equity = (1 + daily).cumprod()
    cum_return = float(equity.iloc[-1] - 1.0) if len(equity) > 0 else 0.0

    peak = equity.cummax()
    drawdown = (equity - peak) / peak
    mdd = float(drawdown.min()) if len(drawdown) > 0 else 0.0

    returns = closed["realized_return_pct"].dropna()
    win_rate = float((returns > 0).mean() * 100) if len(returns) > 0 else None
    expectancy = float(returns.mean() * 100) if len(returns) > 0 else None

    return {
        "day_return_pct": round(latest_day_return * 100, 2) if latest_day_return is not None else None,
        "cumulative_return_pct": round(cum_return * 100, 2),
        "win_rate_pct": round(win_rate, 1) if win_rate is not None else None,
        "expectancy_pct": round(expectancy, 2) if expectancy is not None else None,
        "mdd_pct": round(mdd * 100, 2),
        "total_closed": len(closed),
        "total_pending": len(pending),
    }


def track_daily() -> dict:
    """Record today's observation and return the row as a dict."""
    today = date.today().isoformat()

    log = _load_existing_log()
    if today in log["date"].values:
        log = log[log["date"] != today]

    selected_count, avg_prob, pred_date = _latest_prediction_stats()
    metrics = _portfolio_metrics()
    halted, _ = _check_portfolio_mdd_halt()

    row = {
        "date": today,
        "selected_count": selected_count,
        "avg_hit_prob": round(avg_prob * 100, 1) if avg_prob is not None else None,
        "day_return_pct": metrics.get("day_return_pct"),
        "cumulative_return_pct": metrics.get("cumulative_return_pct", 0.0),
        "win_rate_pct": metrics.get("win_rate_pct"),
        "expectancy_pct": metrics.get("expectancy_pct"),
        "mdd_pct": metrics.get("mdd_pct", 0.0),
        "mdd_halted": halted,
        "total_closed": metrics.get("total_closed", 0),
        "total_pending": metrics.get("total_pending", 0),
    }

    new_row = pd.DataFrame([row])
    if log.empty:
        log = new_row
    else:
        log = pd.concat([log, new_row], ignore_index=True)
    os.makedirs(REPORT_DIR, exist_ok=True)
    log.to_csv(TRACKING_CSV, index=False)

    print(f"[t1-obs] {today}: selected={selected_count} "
          f"cum={row['cumulative_return_pct']:.1f}% "
          f"win={row['win_rate_pct']}% "
          f"exp={row['expectancy_pct']}% "
          f"mdd={row['mdd_pct']:.1f}% "
          f"halted={halted} "
          f"closed={row['total_closed']} pending={row['total_pending']}")

    return row


if __name__ == "__main__":
    track_daily()
