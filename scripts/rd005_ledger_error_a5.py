"""Build RD-005 A5 ledger reconciliation error artifact.

This answers the PM follow-up on the +9.62% ledger reconciliation error in the
A2 bridge report. It does not run timing/OOS phase B/C and does not change any
production model, allocation, or guardrail.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))

from ml.config import REPORT_DIR  # noqa: E402
from scripts.champion_nav_attribution import build_nav_attribution  # noqa: E402

DEFAULT_LEGACY_SPLIT = Path(REPORT_DIR) / "rd005_residual_split_20260517.json"
DEFAULT_PREFIX = "rd005_ledger_error_a5_20260517"
DEFAULT_START = "2026-05-11"
DEFAULT_END = "2026-05-15"


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(path)
    return json.loads(path.read_text(encoding="utf-8"))


def _pct(value: Any, digits: int = 2) -> str:
    if value is None:
        return "-"
    return f"{float(value) * 100:+.{digits}f}%"


def build_a5_report(start_date: str, end_date: str, legacy_split_path: Path) -> dict[str, Any]:
    legacy = _load_json(legacy_split_path)
    artifacts = build_nav_attribution(start_date, end_date)
    summary = artifacts.summary
    ledger = summary["ledger_reconciliation"]
    legacy_error = legacy["ledger_reconciliation"]["reconciliation_error_pct"]
    nav_return = summary["nav"]["return_pct"]
    baseline = ledger["ledger_baseline_pnl_pct"]
    cumulative_end = ledger["ledger_cumulative_pnl_pct"]
    corrected_delta = ledger["ledger_total_pnl_pct"]
    corrected_error = ledger["reconciliation_error_pct"]

    return {
        "status": "a5_closed_for_pm_review_bc_still_hold",
        "window": [start_date, end_date],
        "legacy_artifact": str(legacy_split_path),
        "source_classification": {
            "data_source_inconsistency": False,
            "sampling_timestamp_gap": False,
            "calculation_definition_mismatch": True,
            "reason": (
                "The legacy ledger check compared a window NAV delta with cumulative ledger P&L as of the window end. "
                "Both values came from the same Champion DB and marks; no missing source table was needed to close the gap."
            ),
        },
        "one_time_vs_structural": {
            "classification": "structural_for_non_inception_windows",
            "reason": (
                "Any report whose start date is after ledger inception can create a false ledger error if the start "
                "baseline cumulative P&L is not subtracted. The magnitude equals the negative pre-window ledger baseline."
            ),
        },
        "legacy_check": {
            "nav_window_return_pct": nav_return,
            "legacy_cumulative_ledger_total_pct": legacy["ledger_reconciliation"]["ledger_total_pnl_pct"],
            "legacy_reconciliation_error_pct": legacy_error,
        },
        "corrected_window_check": {
            "baseline_date": ledger["baseline_date"],
            "ledger_baseline_cumulative_pnl_pct": baseline,
            "ledger_end_cumulative_pnl_pct": cumulative_end,
            "ledger_window_delta_pct": corrected_delta,
            "corrected_reconciliation_error_pct": corrected_error,
            "legacy_error_equals_negative_baseline": abs(float(legacy_error) + float(baseline)) <= 1e-9,
            "nav_return_matches_ledger_window_delta": abs(float(nav_return) - float(corrected_delta)) <= 1e-9,
        },
        "fix_direction": {
            "selected": "adjust_definition",
            "implemented_in": "scripts/champion_nav_attribution.py",
            "details": (
                "Ledger reconciliation is now start-aware: it records the pre-window baseline cumulative P&L, "
                "the window-end cumulative P&L, and reconciles NAV return against the ledger window delta."
            ),
        },
        "bridge_decision": {
            "bc_allowed": False,
            "reason": (
                "A5 closes the ledger accounting error, but it does not make holding-level factor residual and "
                "NAV-level unexplained residual the same target. PM still needs to select the target metric before B/C."
            ),
        },
    }


def _write_markdown(path: Path, payload: dict[str, Any]) -> None:
    source = payload["source_classification"]
    legacy = payload["legacy_check"]
    corrected = payload["corrected_window_check"]
    lines = [
        "# RD-005 A5 Ledger Error Closure (2026-05-17)",
        "",
        "## Status",
        "- Scope: A5 ledger reconciliation error only.",
        "- B/C timing and OOS remain HOLD.",
        "- No production allocation, model, sector cap, or guardrail change is recommended.",
        "",
        "## Finding",
        f"- Legacy NAV window return: {_pct(legacy['nav_window_return_pct'])}",
        f"- Legacy cumulative ledger total at window end: {_pct(legacy['legacy_cumulative_ledger_total_pct'])}",
        f"- Legacy reported ledger error: {_pct(legacy['legacy_reconciliation_error_pct'], 4)}",
        "",
        "The +9.62% error came from a calculation-definition mismatch: the old check compared a 5/11~5/15 NAV window delta with cumulative ledger P&L through 5/15.",
        "",
        "## Corrected Window-Aware Check",
        f"- Baseline date: {corrected['baseline_date']}",
        f"- Ledger baseline cumulative P&L: {_pct(corrected['ledger_baseline_cumulative_pnl_pct'], 4)}",
        f"- Ledger end cumulative P&L: {_pct(corrected['ledger_end_cumulative_pnl_pct'], 4)}",
        f"- Ledger window delta: {_pct(corrected['ledger_window_delta_pct'], 4)}",
        f"- Corrected reconciliation error: {_pct(corrected['corrected_reconciliation_error_pct'], 4)}",
        f"- Legacy error equals negative baseline: `{corrected['legacy_error_equals_negative_baseline']}`",
        f"- NAV return matches ledger window delta: `{corrected['nav_return_matches_ledger_window_delta']}`",
        "",
        "## Source Classification",
        f"- Data source inconsistency: `{source['data_source_inconsistency']}`",
        f"- Sampling timestamp gap: `{source['sampling_timestamp_gap']}`",
        f"- Calculation definition mismatch: `{source['calculation_definition_mismatch']}`",
        "",
        source["reason"],
        "",
        "## One-Time vs Structural",
        f"- Classification: `{payload['one_time_vs_structural']['classification']}`",
        "",
        payload["one_time_vs_structural"]["reason"],
        "",
        "## Fix Direction",
        f"- Selected: `{payload['fix_direction']['selected']}`",
        f"- Implemented in: `{payload['fix_direction']['implemented_in']}`",
        "",
        payload["fix_direction"]["details"],
        "",
        "## SA Gate",
        "- A5 may be sent to PM for closure review.",
        "- B/C remains blocked until PM accepts A5 and chooses the target metric.",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_artifacts(payload: dict[str, Any], report_dir: Path, prefix: str) -> dict[str, str]:
    report_dir.mkdir(parents=True, exist_ok=True)
    json_path = report_dir / f"{prefix}.json"
    md_path = report_dir / f"{prefix}.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _write_markdown(md_path, payload)
    return {"json": str(json_path), "md": str(md_path)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build RD-005 A5 ledger error closure artifacts.")
    parser.add_argument("--start", default=DEFAULT_START)
    parser.add_argument("--end", default=DEFAULT_END)
    parser.add_argument("--legacy-split", default=str(DEFAULT_LEGACY_SPLIT))
    parser.add_argument("--report-dir", default=str(REPORT_DIR))
    parser.add_argument("--prefix", default=DEFAULT_PREFIX)
    args = parser.parse_args(argv)

    payload = build_a5_report(args.start, args.end, Path(args.legacy_split))
    paths = write_artifacts(payload, Path(args.report_dir), args.prefix)
    print(
        "[rd005-a5] "
        f"status={payload['status']} "
        f"corrected_error={_pct(payload['corrected_window_check']['corrected_reconciliation_error_pct'], 4)} "
        f"md={paths['md']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
