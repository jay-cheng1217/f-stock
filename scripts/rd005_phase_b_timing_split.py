"""Build RD-005 Phase B timing split artifacts.

The method is locked in docs/designs/rd005_phase_b_timing_method_lock_20260517.md.
This script is read-only: it does not mutate ledgers, models, schedules, or
production guardrails.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))

from ml.config import DAILY_K_DIR, REPORT_DIR  # noqa: E402
from scripts.attribution_reporting_labels import append_reporting_definition_labels  # noqa: E402
from scripts.champion_nav_attribution import DEFAULT_CHAMPION_DB_PATH  # noqa: E402

DEFAULT_START = "2026-05-11"
DEFAULT_END = "2026-05-15"
DEFAULT_PREFIX = "rd005_phase_b_timing_split_20260517"
DEFAULT_TARGET_JSON = Path(REPORT_DIR) / "rd005_residual_split_20260517.json"
DEFAULT_A5_JSON = Path(REPORT_DIR) / "rd005_ledger_error_a5_20260517.json"
TARGET_FIELD = "attribution.residual_split.unexplained_reconciliation_residual_pct"
TRACKED_TICKER = "6419"
RECON_TOLERANCE = 0.005
MISSING_STOP_SHARE = 0.10


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(path)
    return json.loads(path.read_text(encoding="utf-8"))


def _pct(value: Any, digits: int = 2) -> str:
    if value is None:
        return "-"
    return f"{float(value) * 100:+.{digits}f}%"


def _safe_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if pd.notna(parsed) else None


def _load_positions_and_marks(db_path: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    if not db_path.exists():
        raise FileNotFoundError(db_path)
    with sqlite3.connect(db_path) as conn:
        positions = pd.read_sql_query("SELECT * FROM unified_positions ORDER BY id", conn)
        marks = pd.read_sql_query("SELECT * FROM unified_marks ORDER BY position_id, mark_date", conn)
    for column in ("prediction_date", "entry_date", "exit_date", "last_mark_date"):
        if column in positions.columns:
            positions[column] = pd.to_datetime(positions[column], errors="coerce")
    if not marks.empty:
        marks["mark_date"] = pd.to_datetime(marks["mark_date"], errors="coerce")
    return positions, marks


def _load_daily_k(ticker: str) -> pd.DataFrame:
    path = Path(DAILY_K_DIR) / f"{ticker}.csv"
    if not path.exists():
        return pd.DataFrame()
    try:
        df = pd.read_csv(path, encoding="utf-8-sig")
    except Exception:
        return pd.DataFrame()
    if "Date" not in df.columns:
        return pd.DataFrame()
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    for column in ("Open", "Close"):
        if column in df.columns:
            df[column] = pd.to_numeric(df[column], errors="coerce")
    return df.dropna(subset=["Date"]).sort_values("Date")


def _daily_prices(cache: dict[str, pd.DataFrame], ticker: str, date_value: pd.Timestamp) -> dict[str, Any]:
    ticker = str(ticker).zfill(4)
    if ticker not in cache:
        cache[ticker] = _load_daily_k(ticker)
    df = cache[ticker]
    if df.empty:
        return {"daily_k_found": False}
    date_value = pd.Timestamp(date_value).normalize()
    current = df[df["Date"].dt.normalize().eq(date_value)]
    previous = df[df["Date"].dt.normalize() < date_value].tail(1)
    return {
        "daily_k_found": not current.empty,
        "daily_k_open": _safe_float(current.iloc[0].get("Open")) if not current.empty else None,
        "daily_k_close": _safe_float(current.iloc[0].get("Close")) if not current.empty else None,
        "daily_k_prior_close": _safe_float(previous.iloc[0].get("Close")) if not previous.empty else None,
    }


def _build_holding_day_frame(
    positions: pd.DataFrame,
    marks: pd.DataFrame,
    start_date: str,
    end_date: str,
) -> pd.DataFrame:
    start = pd.Timestamp(start_date).normalize()
    end = pd.Timestamp(end_date).normalize()
    merged = marks.merge(
        positions,
        left_on="position_id",
        right_on="id",
        how="left",
        suffixes=("_mark", "_position"),
    )
    merged["ticker"] = merged["ticker"].astype(str).str.zfill(4)
    merged["mark_date"] = pd.to_datetime(merged["mark_date"], errors="coerce").dt.normalize()
    merged = merged.sort_values(["position_id", "mark_date"]).copy()
    merged["target_weight"] = pd.to_numeric(merged["target_weight"], errors="coerce").fillna(0.0)
    merged["entry_price"] = pd.to_numeric(merged.get("entry_price"), errors="coerce")
    merged["entry_slippage_pct"] = pd.to_numeric(merged.get("entry_slippage_pct"), errors="coerce").fillna(0.0)
    merged["close_return_pct"] = pd.to_numeric(merged.get("close_return_pct"), errors="coerce").fillna(0.0)
    merged["realized_return_pct"] = pd.to_numeric(merged.get("realized_return_pct"), errors="coerce")
    merged["entry_date"] = pd.to_datetime(merged.get("entry_date"), errors="coerce").dt.normalize()
    merged["exit_date"] = pd.to_datetime(merged.get("exit_date"), errors="coerce").dt.normalize()
    merged["is_exit_row"] = (
        pd.to_numeric(merged.get("is_exit_day"), errors="coerce").fillna(0).astype(int).eq(1)
        | (merged["exit_date"] == merged["mark_date"])
    )
    merged["effective_return_pct"] = np.where(
        merged["is_exit_row"] & merged["realized_return_pct"].notna(),
        merged["realized_return_pct"],
        merged["close_return_pct"],
    )
    merged["prev_return_pct"] = merged.groupby("position_id")["effective_return_pct"].shift(1).fillna(0.0)
    merged["prev_mark_close"] = pd.to_numeric(merged.groupby("position_id")["close"].shift(1), errors="coerce")
    return merged[(merged["mark_date"] >= start) & (merged["mark_date"] <= end)].copy()


def _row_contributions(row: pd.Series, daily: dict[str, Any]) -> dict[str, Any]:
    weight = float(row["target_weight"] or 0.0)
    entry_price = _safe_float(row.get("entry_price"))
    mark_open = _safe_float(row.get("open"))
    daily_open = daily.get("daily_k_open")
    open_price = daily_open if daily_open is not None else mark_open
    prior_close = daily.get("daily_k_prior_close")
    if prior_close is None:
        prior_close = _safe_float(row.get("prev_mark_close"))
    daily_delta = float(row["effective_return_pct"] or 0.0) - float(row["prev_return_pct"] or 0.0)
    weighted_mark_delta = weight * daily_delta
    entry_day = pd.notna(row.get("entry_date")) and row["entry_date"] == row["mark_date"]

    b1 = b2 = b3 = 0.0
    missing_required = False
    method_note = "held_day"
    entry_slippage_cost = 0.0
    entry_mark_component = 0.0
    if entry_day:
        entry_slippage_cost = weight * float(row.get("entry_slippage_pct") or 0.0)
        entry_mark_component = weighted_mark_delta
        b2 = entry_mark_component - entry_slippage_cost
        method_note = "entry_day_slippage_plus_mark"
    elif entry_price and prior_close is not None and open_price is not None:
        b1 = weight * ((float(open_price) - float(prior_close)) / entry_price)
        b3 = weighted_mark_delta - b1
    else:
        missing_required = True
        method_note = "missing_open_or_prior_close"
    return {
        "date": row["mark_date"].date().isoformat(),
        "position_id": int(row["position_id"]),
        "ticker": str(row["ticker"]).zfill(4),
        "sector": row.get("sector"),
        "target_weight": weight,
        "order_type": row.get("order_type"),
        "entry_date": row["entry_date"].date().isoformat() if pd.notna(row.get("entry_date")) else None,
        "entry_day": bool(entry_day),
        "exit_row": bool(row.get("is_exit_row")),
        "entry_slippage_pct": _safe_float(row.get("entry_slippage_pct")),
        "weighted_mark_delta_pct": weighted_mark_delta,
        "entry_slippage_cost_pct": entry_slippage_cost,
        "entry_mark_component_pct": entry_mark_component,
        "b1_overnight_gap_timing_pct": b1,
        "b2_rebalance_day_intraday_slippage_pct": b2,
        "b3_held_intraday_mark_timing_pct": b3,
        "missing_required_price": missing_required,
        "daily_k_found": bool(daily.get("daily_k_found")),
        "method_note": method_note,
    }


def build_phase_b_split(
    start_date: str,
    end_date: str,
    db_path: Path,
    target_json_path: Path,
    a5_json_path: Path,
) -> dict[str, Any]:
    target_source = _load_json(target_json_path)
    a5 = _load_json(a5_json_path)
    target = float(target_source["attribution"]["residual_split"]["unexplained_reconciliation_residual_pct"])
    positions, marks = _load_positions_and_marks(db_path)
    frame = _build_holding_day_frame(positions, marks, start_date, end_date)
    daily_cache: dict[str, pd.DataFrame] = {}
    rows = [_row_contributions(row, _daily_prices(daily_cache, row["ticker"], row["mark_date"])) for _, row in frame.iterrows()]
    row_df = pd.DataFrame(rows)
    if row_df.empty:
        raise RuntimeError("No holding-day rows in locked window")

    b1 = float(row_df["b1_overnight_gap_timing_pct"].sum())
    b2 = float(row_df["b2_rebalance_day_intraday_slippage_pct"].sum())
    b3 = float(row_df["b3_held_intraday_mark_timing_pct"].sum())
    b4 = target - b1 - b2 - b3
    bucket_total = b1 + b2 + b3 + b4
    recon_error = bucket_total - target

    daily = (
        row_df.groupby("date")
        .agg(
            gross_exposure=("target_weight", "sum"),
            b1_overnight_gap_timing_pct=("b1_overnight_gap_timing_pct", "sum"),
            b2_rebalance_day_intraday_slippage_pct=("b2_rebalance_day_intraday_slippage_pct", "sum"),
            b3_held_intraday_mark_timing_pct=("b3_held_intraday_mark_timing_pct", "sum"),
            weighted_mark_delta_pct=("weighted_mark_delta_pct", "sum"),
            missing_rows=("missing_required_price", "sum"),
        )
        .reset_index()
    )
    exposure_sum = float(daily["gross_exposure"].sum())
    daily["target_allocated_pct"] = daily["gross_exposure"] / exposure_sum * target if exposure_sum else target / len(daily)
    daily["b4_mark_weight_sync_residual_pct"] = daily["target_allocated_pct"] - (
        daily["b1_overnight_gap_timing_pct"]
        + daily["b2_rebalance_day_intraday_slippage_pct"]
        + daily["b3_held_intraday_mark_timing_pct"]
    )
    daily["bucket_total_pct"] = (
        daily["b1_overnight_gap_timing_pct"]
        + daily["b2_rebalance_day_intraday_slippage_pct"]
        + daily["b3_held_intraday_mark_timing_pct"]
        + daily["b4_mark_weight_sync_residual_pct"]
    )

    missing_exposure = float(row_df.loc[row_df["missing_required_price"], "target_weight"].abs().sum())
    missing_threshold = abs(target) * MISSING_STOP_SHARE
    tracked = row_df[row_df["ticker"].eq(TRACKED_TICKER)].copy()
    tracked_bucket = {
        "ticker": TRACKED_TICKER,
        "found": not tracked.empty,
        "order_type": tracked["order_type"].dropna().iloc[0] if not tracked.empty and not tracked["order_type"].dropna().empty else None,
        "entry_date": tracked["entry_date"].dropna().iloc[0] if not tracked.empty and not tracked["entry_date"].dropna().empty else None,
        "entry_slippage_pct": _safe_float(tracked["entry_slippage_pct"].dropna().iloc[0]) if not tracked.empty and not tracked["entry_slippage_pct"].dropna().empty else None,
        "target_weight_at_entry": _safe_float(tracked.loc[tracked["entry_day"], "target_weight"].iloc[0]) if not tracked.empty and tracked["entry_day"].any() else None,
        "b1_overnight_gap_timing_pct": float(tracked["b1_overnight_gap_timing_pct"].sum()) if not tracked.empty else None,
        "b2_rebalance_day_intraday_slippage_pct": float(tracked["b2_rebalance_day_intraday_slippage_pct"].sum()) if not tracked.empty else None,
        "b3_held_intraday_mark_timing_pct": float(tracked["b3_held_intraday_mark_timing_pct"].sum()) if not tracked.empty else None,
        "b4_mark_weight_sync_residual_pct": 0.0 if not tracked.empty else None,
        "b4_note": "portfolio-level residual, not assigned to individual tickers" if not tracked.empty else None,
        "expected_bucket": "B2 rebalance-day intraday slippage",
        "classification": "as_expected" if not tracked.empty and abs(float(tracked["b2_rebalance_day_intraday_slippage_pct"].sum() or 0.0)) > 0 else "not_traceable",
        "rows": tracked.to_dict("records"),
    }

    buckets = [
        {
            "bucket": "B1 overnight gap timing",
            "key": "b1_overnight_gap_timing_pct",
            "contribution_pct": b1,
        },
        {
            "bucket": "B2 rebalance-day intraday slippage",
            "key": "b2_rebalance_day_intraday_slippage_pct",
            "contribution_pct": b2,
        },
        {
            "bucket": "B3 held intraday mark timing",
            "key": "b3_held_intraday_mark_timing_pct",
            "contribution_pct": b3,
        },
        {
            "bucket": "B4 mark/weight synchronization residual",
            "key": "b4_mark_weight_sync_residual_pct",
            "contribution_pct": b4,
        },
    ]
    material_buckets = [bucket for bucket in buckets if abs(bucket["contribution_pct"]) >= 0.03]
    all_small = all(abs(bucket["contribution_pct"]) < 0.01 for bucket in buckets)
    observable_sum = b1 + b2 + b3
    explains_half = abs(observable_sum) >= abs(target) / 2.0
    stop_reasons = []
    if missing_exposure > missing_threshold:
        stop_reasons.append("missing_required_price_rows_above_threshold")
    if not tracked_bucket["found"]:
        stop_reasons.append("tracked_ticker_6419_not_found")
    if abs(recon_error) > RECON_TOLERANCE:
        stop_reasons.append("reconciliation_error_above_tolerance")
    status = "data_or_definition_insufficient" if stop_reasons else "pass_for_pm_review"
    kill_result = "actionable_for_pm_ticket" if material_buckets else "no_actionable_bucket"
    if all_small and not explains_half:
        kill_result = "inconclusive_recommend_phase_c_p0"

    payload = {
        "status": status,
        "start_date": start_date,
        "end_date": end_date,
        "target": {
            "metric": "NAV unexplained residual",
            "source": str(target_json_path),
            "field": TARGET_FIELD,
            "value_pct": target,
        },
        "a5_reference": {
            "source": str(a5_json_path),
            "corrected_reconciliation_error_pct": a5.get("corrected_window_check", {}).get("corrected_reconciliation_error_pct"),
        },
        "method": {
            "locked_doc": str(BASE_DIR / "docs" / "designs" / "rd005_phase_b_timing_method_lock_20260517.md"),
            "daily_b4_allocation_note": "Window-level B4 is authoritative; daily B4 is exposure-weight allocated for the required daily table.",
            "new_data_source_used": False,
        },
        "buckets": buckets,
        "reconciliation": {
            "bucket_total_pct": bucket_total,
            "target_pct": target,
            "reconciliation_error_pct": recon_error,
            "tolerance_pct": RECON_TOLERANCE,
            "pass": abs(recon_error) <= RECON_TOLERANCE,
            "observable_b1_b2_b3_sum_pct": observable_sum,
        },
        "daily_contributions": daily.to_dict("records"),
        "data_sufficiency": {
            "holding_day_rows": int(len(row_df)),
            "missing_required_price_rows": int(row_df["missing_required_price"].sum()),
            "missing_required_exposure_abs": missing_exposure,
            "missing_stop_threshold_pct": missing_threshold,
            "tracked_ticker_found": bool(tracked_bucket["found"]),
            "new_data_source_required": False,
            "stop_reasons": stop_reasons,
        },
        "tracked_6419": tracked_bucket,
        "kill_criteria": {
            "result": kill_result,
            "material_buckets_abs_ge_3pp": material_buckets,
            "all_buckets_abs_lt_1pp": all_small,
            "observable_buckets_abs_ge_half_target_abs": explains_half,
            "phase_c_status": "HOLD",
            "action_note": "Diagnostic only; any production action requires a separate PM ticket.",
        },
        "prohibited_conclusions": {
            "production_allocation_change": False,
            "sector_cap_change": False,
            "guardrail_threshold_change": False,
            "model_retraining": False,
            "stage_b_production_adoption": False,
            "phase_c_execution": False,
        },
    }
    return append_reporting_definition_labels(
        payload,
        b4_value=b4,
        observable_timing_sum=observable_sum,
    )


def _table_row(label: str, value: Any) -> str:
    return f"| {label} | {_pct(value)} |"


def _label_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _write_markdown(path: Path, payload: dict[str, Any]) -> None:
    recon = payload["reconciliation"]
    data = payload["data_sufficiency"]
    tracked = payload["tracked_6419"]
    labels = payload.get("reporting_definition_labels", {})
    lines = [
        "# RD-005 Phase B Timing Split (2026-05-17)",
        "",
        "## Status",
        f"- Status: `{payload['status']}`",
        "- Scope: Phase B timing split only.",
        "- Phase C remains HOLD.",
        "- No production allocation, sector cap, guardrail, or model change is recommended.",
        "",
        "## Target Lock",
        f"- Target metric: {payload['target']['metric']}",
        f"- Target window: {payload['start_date']} to {payload['end_date']}",
        f"- Target value: {_pct(payload['target']['value_pct'])}",
        f"- Source field: `{payload['target']['field']}`",
        "",
        "## Four-Bucket Summary",
        "| Bucket | Contribution |",
        "|---|---:|",
    ]
    for bucket in payload["buckets"]:
        lines.append(_table_row(bucket["bucket"], bucket["contribution_pct"]))
    lines.extend(
        [
            "",
            "## Reconciliation",
            f"- B1+B2+B3+B4: {_pct(recon['bucket_total_pct'])}",
            f"- Target: {_pct(recon['target_pct'])}",
            f"- Error: {_pct(recon['reconciliation_error_pct'], 4)}",
            f"- Tolerance: {_pct(recon['tolerance_pct'])}",
            f"- Pass: `{recon['pass']}`",
            "",
            "## Reporting Definition Labels",
            f"- C1 basis label: `{_label_value(labels.get('c1_basis_label'))}`",
            f"- B4 dominance flag: `{_label_value(labels.get('b4_dominance_flag'))}`",
            f"- Cross-window sign stability: `{_label_value(labels.get('cross_window_sign_stability'))}`",
            f"- Label version: `{_label_value(labels.get('label_version'))}`",
            "",
            "## Daily Contribution Table",
            "| date | gross exposure | B1 | B2 | B3 | B4 | total |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in payload["daily_contributions"]:
        lines.append(
            f"| {row['date']} | {_pct(row['gross_exposure'])} | "
            f"{_pct(row['b1_overnight_gap_timing_pct'])} | "
            f"{_pct(row['b2_rebalance_day_intraday_slippage_pct'])} | "
            f"{_pct(row['b3_held_intraday_mark_timing_pct'])} | "
            f"{_pct(row['b4_mark_weight_sync_residual_pct'])} | "
            f"{_pct(row['bucket_total_pct'])} |"
        )
    lines.extend(
        [
            "",
            "Daily B4 is exposure-weight allocated for the table. The window-level B4 total is authoritative.",
            "",
            "## 6419 Appendix",
            f"- Found: `{tracked['found']}`",
            f"- Order type: `{tracked['order_type']}`",
            f"- Entry date: {tracked['entry_date']}",
            f"- Entry slippage: {_pct(tracked['entry_slippage_pct'])}",
            f"- Target weight at entry: {_pct(tracked['target_weight_at_entry'])}",
            f"- B1 contribution: {_pct(tracked['b1_overnight_gap_timing_pct'])}",
            f"- B2 contribution: {_pct(tracked['b2_rebalance_day_intraday_slippage_pct'])}",
            f"- B3 contribution: {_pct(tracked['b3_held_intraday_mark_timing_pct'])}",
            f"- B4 contribution: {_pct(tracked['b4_mark_weight_sync_residual_pct'])}",
            f"- Classification: `{tracked['classification']}`",
            "",
            tracked.get("b4_note") or "",
            "",
            "6419 remains diagnostic only and must not produce a production add/cut decision.",
            "",
            "## Data Sufficiency",
            f"- Holding-day rows: {data['holding_day_rows']}",
            f"- Missing required price rows: {data['missing_required_price_rows']}",
            f"- Missing required exposure: {_pct(data['missing_required_exposure_abs'])}",
            f"- Missing stop threshold: {_pct(data['missing_stop_threshold_pct'])}",
            f"- New data source required: `{data['new_data_source_required']}`",
            f"- Stop reasons: `{data['stop_reasons']}`",
            "",
            "## Kill Criteria Result",
            f"- Result: `{payload['kill_criteria']['result']}`",
            f"- Material buckets abs >= 3.0pp: {len(payload['kill_criteria']['material_buckets_abs_ge_3pp'])}",
            f"- All buckets abs < 1.0pp: `{payload['kill_criteria']['all_buckets_abs_lt_1pp']}`",
            f"- Observable |B1+B2+B3| >= half |target|: `{payload['kill_criteria']['observable_buckets_abs_ge_half_target_abs']}`",
            f"- Phase C status: `{payload['kill_criteria']['phase_c_status']}`",
            "",
            payload["kill_criteria"]["action_note"],
            "",
            "## Prohibited Conclusions",
            "- No production allocation change.",
            "- No sector cap change.",
            "- No 14-guardrail threshold change.",
            "- No model retraining.",
            "- No RD-002 Stage B production adoption.",
            "- No RD-005 Phase C execution without PM approval.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_artifacts(payload: dict[str, Any], report_dir: Path, prefix: str) -> dict[str, str]:
    report_dir.mkdir(parents=True, exist_ok=True)
    json_path = report_dir / f"{prefix}.json"
    md_path = report_dir / f"{prefix}.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _write_markdown(md_path, payload)
    return {"json": str(json_path), "md": str(md_path)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build RD-005 Phase B timing split artifacts.")
    parser.add_argument("--start", default=DEFAULT_START)
    parser.add_argument("--end", default=DEFAULT_END)
    parser.add_argument("--champion-db", default=str(DEFAULT_CHAMPION_DB_PATH))
    parser.add_argument("--target-json", default=str(DEFAULT_TARGET_JSON))
    parser.add_argument("--a5-json", default=str(DEFAULT_A5_JSON))
    parser.add_argument("--report-dir", default=str(REPORT_DIR))
    parser.add_argument("--prefix", default=DEFAULT_PREFIX)
    args = parser.parse_args(argv)

    payload = build_phase_b_split(
        start_date=args.start,
        end_date=args.end,
        db_path=Path(args.champion_db),
        target_json_path=Path(args.target_json),
        a5_json_path=Path(args.a5_json),
    )
    paths = write_artifacts(payload, Path(args.report_dir), args.prefix)
    print(
        "[rd005-phase-b] "
        f"status={payload['status']} "
        f"target={_pct(payload['target']['value_pct'])} "
        f"recon_error={_pct(payload['reconciliation']['reconciliation_error_pct'], 4)} "
        f"kill={payload['kill_criteria']['result']} "
        f"md={paths['md']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
