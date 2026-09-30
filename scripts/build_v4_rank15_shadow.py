"""V4 Rank-15 Shadow Builder.

Pipeline handshake:
    1. Read latest production V3 prediction CSV.
    2. Apply the existing 30%/Top-30 sector cap → V3 candidate pool (≤30 names).
    3. Look up t1_score / hit_prob_3pct for those tickers in the latest T+1 CSV.
    4. Sort by t1_score desc → take top 15.
    5. Save predictions_v4_rank15_YYYY-MM-DD.csv (same schema as V3, plus
       leaderboard_score = t1_score and a synthesized model_version column).
    6. Lock those 15 names into an isolated paper portfolio ledger
       (paper_portfolio_v4_rank15.db, rule_version='v4-rank15-shadow').

This script is invoked by daily_pipeline.py AFTER production V3 + T+1 are done.
The shadow ledger uses 1/15 equal weighting and is fully isolated from
the V3 production / V2.3 shadow books.
"""

from __future__ import annotations

import argparse
import glob
import os
import re
import sqlite3
import sys
from datetime import datetime

import numpy as np
import pandas as pd

BASE_DIR = os.environ.get("STOCK_BASE_DIR") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from ml.config import MODEL_DIR
from ml.predict import apply_sector_cap
from scripts.update_paper_portfolio import (
    _coerce_db_value,
    _sha256,
    _utc_now_str,
    HOLD_DAYS,
    connect_db,
    ensure_schema,
    refresh_open_positions,
    summarize_ledger,
    SyncStats,
)

V4_RANK15_DB_PATH = os.path.join(BASE_DIR, "paper_portfolio_v4_rank15.db")
V4_OUTPUT_PREFIX = "predictions_v4_rank15"
V4_RULE_VERSION = "v4-rank15-shadow"
V4_TOP_K = 15
V3_POOL_SIZE = 30

PRED_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})(?=\.csv$)")


# ---------------------------------------------------------------- helpers ---
def _list_v3_predictions() -> list[str]:
    """Production V3 predictions (excludes t1 and other shadow files)."""
    pattern = os.path.join(MODEL_DIR, "predictions_*.csv")
    out = []
    for path in sorted(glob.glob(pattern)):
        name = os.path.basename(path)
        if not re.match(r"^predictions_\d{4}-\d{2}-\d{2}\.csv$", name):
            continue
        out.append(path)
    return out


def _v3_path_for_date(pred_date: str | None) -> str:
    candidates = _list_v3_predictions()
    if not candidates:
        raise FileNotFoundError("No production V3 predictions_YYYY-MM-DD.csv found.")
    if pred_date is None:
        return candidates[-1]
    for path in candidates:
        if pred_date in os.path.basename(path):
            return path
    raise FileNotFoundError(f"V3 prediction for {pred_date} not found.")


def _t1_path_for_date(pred_date: str) -> str:
    pattern = os.path.join(MODEL_DIR, f"predictions_t1_{pred_date}.csv")
    if not os.path.exists(pattern):
        raise FileNotFoundError(f"T+1 prediction for {pred_date} not found at {pattern}.")
    return pattern


def _date_from_path(path: str) -> str:
    match = PRED_DATE_RE.search(os.path.basename(path))
    if not match:
        raise ValueError(f"Cannot infer date from {path}")
    return match.group(1)


# --------------------------------------------------------- core builder ----
def build_v4_rank15_csv(
    pred_date: str | None = None,
    verbose: bool = True,
) -> tuple[str, pd.DataFrame]:
    """Build the v4 rank-15 prediction CSV. Returns (output_path, df)."""
    v3_path = _v3_path_for_date(pred_date)
    pred_date = _date_from_path(v3_path)
    t1_path = _t1_path_for_date(pred_date)

    if verbose:
        print(f"[v4-shadow] V3 source: {os.path.basename(v3_path)}")
        print(f"[v4-shadow] T+1 source: {os.path.basename(t1_path)}")

    v3_df = pd.read_csv(v3_path, encoding="utf-8-sig", dtype={"ticker": str})
    t1_df = pd.read_csv(t1_path, encoding="utf-8-sig", dtype={"ticker": str})

    # Step 1+2: V3 sector-capped Top 30 (uses production rules: liquidity,
    # extreme distance from MA20, sector cap, etc., already enforced upstream).
    v3_top30 = apply_sector_cap(v3_df, top_n=V3_POOL_SIZE)
    if v3_top30.empty and verbose:
        print(f"[v4-shadow] INFO: V3 sector-capped pool is empty for {pred_date}.")
    v3_top30 = v3_top30.reset_index(drop=True)
    v3_top30["v3_rank"] = np.arange(1, len(v3_top30) + 1)

    # Step 3: lookup T+1 score / prob for those tickers.
    t1_keep = ["ticker", "hit_prob_3pct", "t1_score"]
    t1_lookup = t1_df[[c for c in t1_keep if c in t1_df.columns]].copy()
    if "t1_score" not in t1_lookup.columns:
        raise RuntimeError("predictions_t1 CSV is missing the t1_score column.")
    if "hit_prob_3pct" not in t1_lookup.columns:
        t1_lookup["hit_prob_3pct"] = np.nan

    merged = v3_top30.merge(t1_lookup, on="ticker", how="left")
    missing_t1 = int(merged["t1_score"].isna().sum())
    if verbose and missing_t1:
        print(f"[v4-shadow] WARN: {missing_t1}/{len(merged)} V3 names missing T+1 score.")

    # Step 4: rank by T+1 score desc, ties by hit_prob, then v3_rank.
    merged["t1_score_filled"] = merged["t1_score"].fillna(-1.0)
    merged["hit_prob_filled"] = merged["hit_prob_3pct"].fillna(-1.0)
    merged = merged.sort_values(
        ["t1_score_filled", "hit_prob_filled", "v3_rank"],
        ascending=[False, False, True],
    ).reset_index(drop=True)
    merged = merged.drop(columns=["t1_score_filled", "hit_prob_filled"])

    top15 = merged.head(V4_TOP_K).copy()
    top15["v4_rank"] = np.arange(1, len(top15) + 1)

    # leaderboard_score is the column update_paper_portfolio's sorter prefers.
    # Force descending integers so external sorters cannot reorder.
    top15["leaderboard_score"] = (V4_TOP_K + 1 - top15["v4_rank"]).astype(np.float32)
    top15["model_version"] = V4_RULE_VERSION
    top15["position_weight"] = 1.0 / V4_TOP_K

    # Recommendation: keep V3's recommendation if present, otherwise default 建議買進.
    if "recommendation" not in top15.columns:
        top15["recommendation"] = "建議買進"
    else:
        watch_mask = top15["recommendation"].astype(str).str.startswith("觀望")
        top15.loc[watch_mask, "recommendation"] = "建議買進"

    output_path = os.path.join(MODEL_DIR, f"{V4_OUTPUT_PREFIX}_{pred_date}.csv")
    tmp_path = output_path + ".tmp"
    top15.to_csv(tmp_path, index=False, encoding="utf-8-sig")
    os.replace(tmp_path, output_path)

    if verbose:
        print(
            f"[v4-shadow] Wrote {len(top15)} names → {os.path.basename(output_path)}"
        )
        for _, row in top15.head(15).iterrows():
            sector = str(row.get("sector", ""))
            t1s = row.get("t1_score")
            t1p = row.get("hit_prob_3pct")
            t1s_str = f"{float(t1s):.3f}" if pd.notna(t1s) else "  n/a"
            t1p_str = f"{float(t1p):.3f}" if pd.notna(t1p) else "  n/a"
            print(
                f"  #{int(row['v4_rank']):>2}  {row['ticker']:<6s}  "
                f"v3#{int(row['v3_rank']):<2}  "
                f"t1_score={t1s_str}  hit={t1p_str}  sector={sector}"
            )
    return output_path, top15


# ----------------------------------------------------- ledger sync ---------
def _lock_v4_run(
    conn: sqlite3.Connection,
    prediction_path: str,
    leaderboard: pd.DataFrame,
    rule_version: str,
    stats: SyncStats,
) -> tuple[int, str, bool]:
    """Insert a v4 run + 15 positions WITHOUT re-applying sector cap.

    Bypasses update_paper_portfolio._build_leaderboard so the order matches
    exactly the v4 csv (T+1 ranking inside the V3 sector-capped pool).
    """
    prediction_date = _date_from_path(prediction_path) if leaderboard.empty else str(leaderboard["date"].iloc[0])
    existing = conn.execute(
        "SELECT id FROM portfolio_runs WHERE prediction_date = ?",
        (prediction_date,),
    ).fetchone()
    if existing:
        stats.skipped_runs += 1
        print(f"[v4-shadow] {prediction_date} already locked, skip.")
        return int(existing["id"]), prediction_date, False

    now = _utc_now_str()
    cursor = conn.execute(
        """
        INSERT INTO portfolio_runs (
            prediction_date, created_at, prediction_path,
            prediction_sha256, rule_version, top_n
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            prediction_date,
            now,
            os.path.abspath(prediction_path),
            _sha256(prediction_path),
            rule_version,
            int(len(leaderboard)),
        ),
    )
    run_id = int(cursor.lastrowid)
    stats.new_runs += 1

    for rank_idx, row in enumerate(leaderboard.to_dict("records"), start=1):
        conn.execute(
            """
            INSERT INTO portfolio_positions (
                run_id, prediction_date, selection_rank, ticker, stock_name,
                sector, recommendation, signal, selection_close, pred_return_20d,
                up_prob, flat_prob, down_prob, prob_edge, risk_adjusted_return,
                price_vs_ma20, inst_net_10d, risk_tags, status, hold_days_target,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                prediction_date,
                rank_idx,
                str(row.get("ticker", "")),
                _coerce_db_value(row.get("name") or row.get("stock_name")),
                _coerce_db_value(row.get("sector")),
                _coerce_db_value(row.get("recommendation")),
                _coerce_db_value(row.get("signal")),
                _coerce_db_value(row.get("close")),
                _coerce_db_value(row.get("pred_return_20d")),
                _coerce_db_value(row.get("up_prob")),
                _coerce_db_value(row.get("flat_prob")),
                _coerce_db_value(row.get("down_prob")),
                _coerce_db_value(row.get("prob_edge")),
                _coerce_db_value(row.get("risk_adjusted_return")),
                _coerce_db_value(row.get("price_vs_ma20")),
                _coerce_db_value(row.get("inst_net_10d")),
                _coerce_db_value(row.get("risk_tags")),
                "pending",
                HOLD_DAYS,
                now,
                now,
            ),
        )
        stats.new_positions += 1

    conn.commit()
    print(f"[v4-shadow] Locked {len(leaderboard)} names for {prediction_date}.")
    return run_id, prediction_date, True


def sync_v4_rank15_ledger(
    prediction_file: str,
    leaderboard: pd.DataFrame,
    db_path: str = V4_RANK15_DB_PATH,
) -> dict:
    stats = SyncStats()
    with connect_db(db_path) as conn:
        ensure_schema(conn)
        refresh_open_positions(conn, stats)
        _, prediction_date, inserted = _lock_v4_run(
            conn=conn,
            prediction_path=prediction_file,
            leaderboard=leaderboard,
            rule_version=V4_RULE_VERSION,
            stats=stats,
        )
        if inserted:
            refresh_open_positions(conn, stats, prediction_dates={prediction_date})
        summary = summarize_ledger(conn)

    return {
        "db_path": os.path.abspath(db_path),
        "prediction_file": prediction_file,
        "new_runs": stats.new_runs,
        "skipped_runs": stats.skipped_runs,
        "new_positions": stats.new_positions,
        "refreshed_positions": stats.refreshed_positions,
        "marks_upserted": stats.marks_upserted,
        "summary": summary,
    }


def run_full(pred_date: str | None = None, verbose: bool = True) -> dict:
    """One-shot: build CSV + sync ledger."""
    output_path, top15 = build_v4_rank15_csv(pred_date=pred_date, verbose=verbose)
    result = sync_v4_rank15_ledger(prediction_file=output_path, leaderboard=top15)
    if verbose:
        s = result["summary"]
        print(
            f"[v4-shadow] Ledger: runs={s['total_runs']} positions={s['total_positions']} "
            f"closed={s['closed_positions']} stopped={s['stopped_out_positions']}"
        )
    return result


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Build V4 rank-15 shadow predictions and ledger.")
    parser.add_argument("--date", help="Prediction date YYYY-MM-DD (default: latest).")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)
    run_full(pred_date=args.date, verbose=not args.quiet)


if __name__ == "__main__":
    main()
