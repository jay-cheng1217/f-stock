"""Build REQ-034-v3 my_holdings dry-run artifacts.

This script is intentionally read-only. It reuses the existing my_holdings
review core, sanitizes private cost fields, and writes PM-review artifacts.
It does not send email and does not mutate my_holdings.db.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any

BASE_DIR = Path(os.environ.get("STOCK_BASE_DIR") or Path(__file__).resolve().parents[1])
sys.path.insert(0, str(BASE_DIR))

from ml.config import REPORT_DIR  # noqa: E402

DEFAULT_DB_PATH = BASE_DIR / "my_holdings.db"
DEFAULT_AS_OF_DATE = "2026-05-16"


def _reason_codes(reasons: list[dict[str, Any]]) -> list[str]:
    return [str(item.get("code") or "UNKNOWN") for item in reasons if isinstance(item, dict)]


def _bucket(signal: str, review_status: str, reasons: list[dict[str, Any]]) -> tuple[str, str]:
    reason_codes = set(_reason_codes(reasons))
    if signal == "EXIT":
        if reason_codes & {"DAILY_K_DATA_MISSING"} or "DATA" in review_status:
            return "red", "data_quality_review"
        return "red", "cut_or_exit_review"
    if signal == "TRIM":
        return "yellow", "trim_or_watch"
    if signal == "ADD":
        return "green", "add_ok"
    return "green", "keep"


def _sanitize_review(review: dict[str, Any]) -> dict[str, Any]:
    holding = review.get("holding") or {}
    reasons = review.get("reasons") or []
    warnings = review.get("warnings") or []
    metrics = review.get("metrics") or {}
    bucket, action_group = _bucket(str(review.get("signal") or ""), str(review.get("review_status") or ""), reasons)
    return {
        "ticker": holding.get("ticker"),
        "asset_type": holding.get("asset_type"),
        "universe_type": holding.get("universe_type"),
        "signal": review.get("signal"),
        "bucket": bucket,
        "action_group": action_group,
        "review_status": review.get("review_status"),
        "v2_alpha_model_status": review.get("v2_alpha_model_status"),
        "rule_pool": review.get("rule_pool"),
        "daily_k_status": metrics.get("daily_k_status"),
        "latest_date": metrics.get("latest_date"),
        "latest_close": metrics.get("latest_close"),
        "ret_20d_pct": metrics.get("ret_20d_pct"),
        "price_vs_ma20_pct": metrics.get("price_vs_ma20_pct"),
        "reason_codes": _reason_codes(reasons),
        "warning_codes": _reason_codes(warnings),
        "prediction_available": bool(review.get("prediction")),
        "prediction_explain_available": bool(review.get("prediction_explain")),
    }


def _load_reviews(db_path: Path) -> list[dict[str, Any]]:
    if not db_path.exists():
        raise FileNotFoundError(f"my_holdings DB not found: {db_path}")
    os.environ["MY_HOLDINGS_DB_PATH"] = str(db_path)
    from backend.routers import my_holdings  # noqa: WPS433

    my_holdings.MY_HOLDINGS_DB_PATH = str(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        # Fully sold positions keep their row (and transaction history) with shares = 0.
        rows = conn.execute("SELECT * FROM holdings WHERE shares > 0 ORDER BY ticker, id").fetchall()
    return [_sanitize_review(my_holdings._build_review(row)) for row in rows]


def build_dry_run(as_of_date: str, db_path: Path) -> dict[str, Any]:
    reviews = _load_reviews(db_path)
    counts = {
        "red": sum(1 for item in reviews if item["bucket"] == "red"),
        "yellow": sum(1 for item in reviews if item["bucket"] == "yellow"),
        "green": sum(1 for item in reviews if item["bucket"] == "green"),
        "total": len(reviews),
    }
    etf_rows = [item for item in reviews if item["asset_type"] == "etf"]
    missing_prediction_rows = [
        item
        for item in reviews
        if item["asset_type"] == "stock" and not item["prediction_available"]
    ]
    degraded = [
        item
        for item in reviews
        if item["daily_k_status"] != "ok"
        or "DATA" in str(item.get("review_status") or "")
        or (item["asset_type"] == "stock" and not item["prediction_available"])
    ]
    coverage = {
        "has_red_sample": counts["red"] > 0,
        "has_yellow_sample": counts["yellow"] > 0,
        "has_green_sample": counts["green"] > 0,
        "has_etf_processing_result": bool(etf_rows),
        "has_prediction_universe_fallback": bool(missing_prediction_rows),
        "prediction_universe_fallback_gap": not bool(missing_prediction_rows),
    }
    synthetic_boundary_appendix: list[dict[str, Any]] = []
    if not coverage["has_yellow_sample"]:
        synthetic_boundary_appendix.append(
            {
                "case": "yellow_trim_watch_boundary",
                "bucket": "yellow",
                "not_real_holding": True,
                "reason": "Real 12 holdings produced no yellow sample; this validates report rendering only.",
                "example_conditions": ["OVERHEAT_MA20", "NEWS_NEGATIVE_ALERT"],
            }
        )
    if not coverage["has_etf_processing_result"]:
        synthetic_boundary_appendix.append(
            {
                "case": "etf_rule_subset_boundary",
                "bucket": "yellow",
                "not_real_holding": True,
                "reason": "Real 12 holdings include no ETF; this validates ETF coverage expectation only.",
                "example_conditions": ["ETF_V2_ALPHA_MODEL_UNSUPPORTED", "technical_rule_subset"],
            }
        )
    if not coverage["has_prediction_universe_fallback"]:
        synthetic_boundary_appendix.append(
            {
                "case": "prediction_universe_fallback_boundary",
                "bucket": "yellow",
                "not_real_holding": True,
                "reason": "Real 12 holdings all had prediction payloads; this validates fallback rendering only.",
                "example_conditions": ["MODEL_UNAVAILABLE_REVIEW_REQUIRED"],
            }
        )
    return {
        "as_of_date": as_of_date,
        "db_path": str(db_path),
        "status": "dry_run_only_pm_review_required",
        "privacy": "cost, shares, breakeven, market value, and unrealized PnL are intentionally omitted",
        "counts": counts,
        "coverage": coverage,
        "degraded_data_cases": degraded,
        "synthetic_boundary_appendix": synthetic_boundary_appendix,
        "holdings": reviews,
    }


def _write_markdown(path: Path, summary: dict[str, Any]) -> None:
    counts = summary["counts"]
    coverage = summary["coverage"]
    lines = [
        "# REQ-034-v3 My Holdings Dry Run (2026-05-17)",
        "",
        "## Scope",
        f"- Requested as-of date: {summary['as_of_date']}",
        "- Email sent: no",
        "- DB mutation: no",
        "- Private cost/share fields: omitted",
        f"- Status: `{summary['status']}`",
        "",
        "## Bucket Counts",
        f"- Red: {counts['red']}",
        f"- Yellow: {counts['yellow']}",
        f"- Green: {counts['green']}",
        f"- Total holdings: {counts['total']}",
        "",
        "## Coverage",
        f"- Red sample present: `{coverage['has_red_sample']}`",
        f"- Yellow sample present: `{coverage['has_yellow_sample']}`",
        f"- Green sample present: `{coverage['has_green_sample']}`",
        f"- ETF processing result present: `{coverage['has_etf_processing_result']}`",
        f"- Prediction-universe fallback present: `{coverage['has_prediction_universe_fallback']}`",
        f"- Prediction-universe fallback coverage gap: `{coverage['prediction_universe_fallback_gap']}`",
        "",
        "## Holdings",
        "| ticker | asset | bucket | signal | review status | daily K | reasons |",
        "|---|---|---|---|---|---|---|",
    ]
    for item in summary["holdings"]:
        reasons = ", ".join(item["reason_codes"]) or "-"
        lines.append(
            f"| {item['ticker']} | {item['asset_type']} | {item['bucket']} | {item['signal']} | "
            f"{item['review_status']} | {item['daily_k_status']} | {reasons} |"
        )
    lines.extend(["", "## Degraded Data Cases"])
    if summary["degraded_data_cases"]:
        lines.append("| ticker | action group | status | reason |")
        lines.append("|---|---|---|---|")
        for item in summary["degraded_data_cases"]:
            reason = ", ".join(item["reason_codes"]) or "-"
            lines.append(f"| {item['ticker']} | {item['action_group']} | {item['review_status']} | {reason} |")
    else:
        lines.append("- None.")
    lines.extend(["", "## Synthetic Boundary Appendix"])
    appendix = summary.get("synthetic_boundary_appendix") or []
    if appendix:
        lines.append("- These rows are not real holdings and are not included in bucket counts.")
        lines.append("| case | bucket | reason | conditions |")
        lines.append("|---|---|---|---|")
        for item in appendix:
            conditions = ", ".join(item.get("example_conditions") or [])
            lines.append(f"| {item['case']} | {item['bucket']} | {item['reason']} | {conditions} |")
    else:
        lines.append("- Not needed; real holdings covered all required boundary cases.")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_red_trigger_list(path: Path, summary: dict[str, Any]) -> None:
    red_rows = [item for item in summary["holdings"] if item["bucket"] == "red"]
    lines = [
        "# REQ-034-v3 Red Trigger List (2026-05-17)",
        "",
        "## Scope",
        f"- Source dry-run date: {summary['as_of_date']}",
        "- Private cost/share fields: omitted",
        "- PM condition: ticker + trigger rule names only",
        "",
        "## Red Holdings",
        "| ticker | signal | action group | trigger rules |",
        "|---|---|---|---|",
    ]
    if red_rows:
        for item in red_rows:
            reasons = ", ".join(item["reason_codes"]) or "-"
            lines.append(f"| {item['ticker']} | {item['signal']} | {item['action_group']} | {reasons} |")
    else:
        lines.append("| - | - | - | no red holdings |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_synthetic_boundary(path: Path, summary: dict[str, Any]) -> None:
    appendix = summary.get("synthetic_boundary_appendix") or []
    lines = [
        "# REQ-034-v3 Synthetic Boundary Appendix (2026-05-17)",
        "",
        "## Scope",
        f"- Source dry-run date: {summary['as_of_date']}",
        "- Synthetic rows are not real holdings.",
        "- Synthetic rows are not included in red/yellow/green counts.",
        "- Each case maps to a real coverage gap observed in the 12 holding dry-run.",
        "",
        "## Synthetic Cases",
        "| synthetic case | bucket | real coverage gap | conditions |",
        "|---|---|---|---|",
    ]
    if appendix:
        for item in appendix:
            conditions = ", ".join(item.get("example_conditions") or [])
            lines.append(f"| synthetic_case_{item['case']} | {item['bucket']} | {item['reason']} | {conditions} |")
    else:
        lines.append("| - | - | no synthetic gap | - |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_artifacts(summary: dict[str, Any], report_dir: Path, prefix: str) -> dict[str, str]:
    report_dir.mkdir(parents=True, exist_ok=True)
    json_path = report_dir / f"{prefix}.json"
    md_path = report_dir / f"{prefix}.md"
    red_json_path = report_dir / "req034_v3_red_trigger_list_20260517.json"
    red_md_path = report_dir / "req034_v3_red_trigger_list_20260517.md"
    synthetic_json_path = report_dir / "req034_v3_synthetic_boundary_20260517.json"
    synthetic_md_path = report_dir / "req034_v3_synthetic_boundary_20260517.md"
    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _write_markdown(md_path, summary)
    red_rows = [item for item in summary["holdings"] if item["bucket"] == "red"]
    red_json_path.write_text(json.dumps(red_rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _write_red_trigger_list(red_md_path, summary)
    synthetic_rows = summary.get("synthetic_boundary_appendix") or []
    synthetic_json_path.write_text(json.dumps(synthetic_rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _write_synthetic_boundary(synthetic_md_path, summary)
    return {
        "json": str(json_path),
        "md": str(md_path),
        "red_json": str(red_json_path),
        "red_md": str(red_md_path),
        "synthetic_json": str(synthetic_json_path),
        "synthetic_md": str(synthetic_md_path),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build REQ-034-v3 my_holdings dry-run artifacts.")
    parser.add_argument("--as-of-date", default=DEFAULT_AS_OF_DATE)
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--report-dir", default=str(REPORT_DIR))
    parser.add_argument("--prefix", default="req034_v3_my_holdings_dry_run_20260517")
    args = parser.parse_args(argv)

    summary = build_dry_run(args.as_of_date, Path(args.db_path))
    paths = write_artifacts(summary, Path(args.report_dir), args.prefix)
    print(f"[req034-dry-run] counts={summary['counts']} md={paths['md']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
