"""RD-002 Stage A realized-slippage oracle diagnostic.

This is intentionally diagnostic-only. It uses realized entry_slippage_pct to
estimate the theoretical upper bound of a future ex-ante slippage throttle, but
it does not emit any production rule and must not be used directly for Stage B.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ml.config import REPORT_DIR

DEFAULT_DB_PATH = ROOT / "paper_portfolio_v2_champion.db"
DEFAULT_THRESHOLDS = (0.01, 0.015, 0.02, 0.025, 0.03, 0.035, 0.04)
STAGE_B_MIN_ORACLE_EDGE = 0.005


def _pct(value: float | None, digits: int = 2) -> str:
    if value is None or not np.isfinite(value):
        return "-"
    return f"{float(value) * 100:+.{digits}f}%"


def load_positions(db_path: str | Path = DEFAULT_DB_PATH) -> pd.DataFrame:
    path = Path(db_path)
    if not path.exists():
        raise FileNotFoundError(path)
    with sqlite3.connect(path) as conn:
        positions = pd.read_sql_query("SELECT * FROM unified_positions ORDER BY id", conn)
        marks = pd.read_sql_query(
            """
            SELECT m.position_id, m.mark_date, m.close_return_pct
            FROM unified_marks m
            JOIN (
                SELECT position_id, MAX(mark_date) AS mark_date
                FROM unified_marks
                GROUP BY position_id
            ) latest
              ON latest.position_id = m.position_id
             AND latest.mark_date = m.mark_date
            """,
            conn,
        )
    if marks.empty:
        positions["latest_close_return_pct"] = np.nan
        return positions
    latest = marks.rename(columns={"close_return_pct": "latest_close_return_pct"})
    return positions.merge(latest, left_on="id", right_on="position_id", how="left")


def prepare_slippage_frame(positions: pd.DataFrame) -> pd.DataFrame:
    df = positions.copy()
    required = {"id", "ticker", "target_weight", "entry_slippage_pct", "order_type"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"missing required columns: {sorted(missing)}")
    df["target_weight"] = pd.to_numeric(df["target_weight"], errors="coerce").fillna(0.0)
    df["entry_slippage_pct"] = pd.to_numeric(df["entry_slippage_pct"], errors="coerce")
    df["realized_return_pct"] = pd.to_numeric(df.get("realized_return_pct"), errors="coerce")
    df["latest_close_return_pct"] = pd.to_numeric(df.get("latest_close_return_pct"), errors="coerce")
    df["effective_return_pct"] = df["realized_return_pct"].where(
        df["realized_return_pct"].notna(),
        df["latest_close_return_pct"],
    )
    df["order_type"] = df["order_type"].fillna("UNKNOWN").astype(str)
    df = df.dropna(subset=["entry_slippage_pct"])
    df = df[df["target_weight"] > 0].copy()
    df["positive_slippage_pct"] = df["entry_slippage_pct"].clip(lower=0.0)
    df["price_improvement_pct"] = (-df["entry_slippage_pct"].clip(upper=0.0))
    df["weighted_slippage_cost_pct"] = df["target_weight"] * df["positive_slippage_pct"]
    df["weighted_price_improvement_pct"] = df["target_weight"] * df["price_improvement_pct"]
    df["weighted_effective_return_pct"] = df["target_weight"] * df["effective_return_pct"].fillna(0.0)
    return df


def _threshold_row(df: pd.DataFrame, threshold: float) -> dict[str, Any]:
    subset = df[df["entry_slippage_pct"] >= threshold].copy()
    if subset.empty:
        return {
            "threshold_pct": threshold,
            "trade_count": 0,
            "slippage_saved_pct": 0.0,
            "avoided_bad_chase_pct": 0.0,
            "missed_good_chase_pct": 0.0,
            "net_skip_oracle_pct": 0.0,
        }
    weighted_return = subset["weighted_effective_return_pct"].fillna(0.0)
    avoided_bad = float((-weighted_return[weighted_return < 0]).sum())
    missed_good = float(weighted_return[weighted_return > 0].sum())
    slippage_saved = float(subset["weighted_slippage_cost_pct"].sum())
    return {
        "threshold_pct": threshold,
        "trade_count": int(len(subset)),
        "slippage_saved_pct": slippage_saved,
        "avoided_bad_chase_pct": avoided_bad,
        "missed_good_chase_pct": missed_good,
        "net_skip_oracle_pct": slippage_saved + avoided_bad - missed_good,
    }


def analyze_stage_a(
    positions: pd.DataFrame,
    *,
    thresholds: tuple[float, ...] = DEFAULT_THRESHOLDS,
) -> tuple[dict[str, Any], pd.DataFrame, pd.DataFrame]:
    frame = prepare_slippage_frame(positions)
    total_cost = float(frame["weighted_slippage_cost_pct"].sum()) if not frame.empty else 0.0
    total_improvement = float(frame["weighted_price_improvement_pct"].sum()) if not frame.empty else 0.0
    by_order = (
        frame.groupby("order_type")
        .agg(
            trade_count=("id", "count"),
            avg_slippage_pct=("entry_slippage_pct", "mean"),
            slippage_cost_pct=("weighted_slippage_cost_pct", "sum"),
            avg_effective_return_pct=("effective_return_pct", "mean"),
        )
        .reset_index()
        if not frame.empty
        else pd.DataFrame()
    )
    threshold_df = pd.DataFrame([_threshold_row(frame, threshold) for threshold in thresholds])
    summary = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "trade_count": int(len(frame)),
        "oracle_slippage_only_upper_bound_pct": total_cost,
        "price_improvement_offset_pct": total_improvement,
        "net_realized_slippage_drag_pct": -total_cost + total_improvement,
        "stage_b_kill_switch_threshold_pct": STAGE_B_MIN_ORACLE_EDGE,
        "stage_b_recommendation": "KILL_STAGE_B" if total_cost < STAGE_B_MIN_ORACLE_EDGE else "ALLOW_STAGE_B_DESIGN",
        "diagnostic_only": True,
        "stage_b_warning": (
            "Stage B must use predicted_slippage_proxy and must not use realized entry_slippage_pct directly."
        ),
    }
    return summary, by_order, threshold_df


def write_report(
    summary: dict[str, Any],
    by_order: pd.DataFrame,
    threshold_df: pd.DataFrame,
    *,
    output_prefix: str,
    report_dir: str | Path = REPORT_DIR,
) -> tuple[Path, Path, Path, Path]:
    out_dir = Path(report_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / f"{output_prefix}.md"
    json_path = out_dir / f"{output_prefix}.json"
    order_path = out_dir / f"{output_prefix}_by_order_type.csv"
    threshold_path = out_dir / f"{output_prefix}_thresholds.csv"
    by_order.to_csv(order_path, index=False, encoding="utf-8-sig")
    threshold_df.to_csv(threshold_path, index=False, encoding="utf-8-sig")
    json_path.write_text(
        json.dumps(
            {
                "summary": summary,
                "by_order_type": by_order.to_dict("records"),
                "thresholds": threshold_df.to_dict("records"),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    lines = [
        "# RD-002 Stage A Slippage Oracle Diagnostic",
        "",
        "Diagnostic only. This report uses realized slippage to estimate an upper bound; it is forbidden to use realized slippage directly in Stage B.",
        "",
        "## Summary",
        f"- Trades with slippage data: {summary['trade_count']}",
        f"- Slippage-only oracle upper bound: {_pct(summary['oracle_slippage_only_upper_bound_pct'])}",
        f"- Price-improvement offset: {_pct(summary['price_improvement_offset_pct'])}",
        f"- Net realized slippage drag: {_pct(summary['net_realized_slippage_drag_pct'])}",
        f"- Stage B kill switch: {_pct(summary['stage_b_kill_switch_threshold_pct'])}",
        f"- Recommendation: **{summary['stage_b_recommendation']}**",
        "",
        "## By Order Type",
        "| order_type | trades | avg slippage | slippage cost | avg effective return |",
        "|---|---:|---:|---:|---:|",
    ]
    for row in by_order.to_dict("records"):
        lines.append(
            f"| {row['order_type']} | {int(row['trade_count'])} | {_pct(row['avg_slippage_pct'])} | "
            f"{_pct(row['slippage_cost_pct'])} | {_pct(row['avg_effective_return_pct'])} |"
        )
    lines.extend(
        [
            "",
            "## Oracle Threshold Table",
            "| threshold | trades blocked | slippage saved | avoided bad chase | missed good chase | net skip oracle |",
            "|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in threshold_df.to_dict("records"):
        lines.append(
            f"| {_pct(row['threshold_pct'])} | {int(row['trade_count'])} | {_pct(row['slippage_saved_pct'])} | "
            f"{_pct(row['avoided_bad_chase_pct'])} | {_pct(row['missed_good_chase_pct'])} | "
            f"{_pct(row['net_skip_oracle_pct'])} |"
        )
    lines.extend(
        [
            "",
            "## Stage B Guardrails",
            "- If the oracle upper bound is below +0.50pp, Stage B should be killed and not designed.",
            "- If Stage B proceeds, it must use an ex-ante `predicted_slippage_proxy`.",
            "- Stage B must report avoided-bad-chase and missed-good-chase separately.",
        ]
    )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return md_path, json_path, order_path, threshold_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run RD-002 Stage A realized slippage oracle diagnostic.")
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--report-dir", default=str(REPORT_DIR))
    parser.add_argument("--output-prefix", default=f"rd002_stage_a_slippage_oracle_{datetime.now():%Y%m%d}")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    positions = load_positions(args.db_path)
    summary, by_order, threshold_df = analyze_stage_a(positions)
    md_path, json_path, order_path, threshold_path = write_report(
        summary,
        by_order,
        threshold_df,
        output_prefix=args.output_prefix,
        report_dir=args.report_dir,
    )
    print(f"[slippage-stage-a] report={md_path}")
    print(f"[slippage-stage-a] oracle_upper_bound={_pct(summary['oracle_slippage_only_upper_bound_pct'])}")
    print(f"[slippage-stage-a] recommendation={summary['stage_b_recommendation']}")
    print(f"[slippage-stage-a] json={json_path}")
    print(f"[slippage-stage-a] by_order={order_path}")
    print(f"[slippage-stage-a] thresholds={threshold_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

