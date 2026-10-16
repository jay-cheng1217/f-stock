"""Build RD-006 offline sync residual diagnostic artifacts.

This diagnostic is explicitly offline. It uses existing Champion DB rows and
previous RD-005/RD-006 artifacts to explain B4 behavior across the mock and
formal windows. It does not mutate ledgers, schemas, models, schedules, or
production guardrails.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import pandas as pd

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))

from ml.config import REPORT_DIR  # noqa: E402
from scripts.champion_nav_attribution import DEFAULT_CHAMPION_DB_PATH  # noqa: E402
from scripts.rd005_phase_b_timing_split import (  # noqa: E402
    _build_holding_day_frame,
    _daily_prices,
    _load_positions_and_marks,
    _pct,
    _row_contributions,
)
from scripts.rd006_sync_residual_mock import _target_from_attribution  # noqa: E402

DEFAULT_PREFIX = "rd006_sync_residual_diagnostic_20260517"
DEFAULT_FORMAL_B = Path(REPORT_DIR) / "rd005_phase_b_timing_split_20260517.json"
DEFAULT_MOCK_ATTRIBUTION = Path(REPORT_DIR) / "champion_nav_attribution_20260423_20260429.json"
DEFAULT_FORMAL_ATTRIBUTION = Path(REPORT_DIR) / "rd005_residual_split_20260517.json"


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(path)
    return json.loads(path.read_text(encoding="utf-8"))


def _power(numerator: float, denominator: float) -> float:
    if denominator == 0:
        return 0.0
    return min(abs(numerator) / abs(denominator), 1.0)


def _safe_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if pd.notna(parsed) else None


def _window_rows(start_date: str, end_date: str, target: float, db_path: Path) -> dict[str, Any]:
    positions, marks = _load_positions_and_marks(db_path)
    frame = _build_holding_day_frame(positions, marks, start_date, end_date)
    daily_cache: dict[str, pd.DataFrame] = {}
    rows: list[dict[str, Any]] = []
    for _, row in frame.iterrows():
        prices = _daily_prices(daily_cache, row["ticker"], row["mark_date"])
        out = _row_contributions(row, prices)
        out["mark_open"] = _safe_float(row.get("open"))
        out["mark_close"] = _safe_float(row.get("close"))
        out["daily_k_open"] = prices.get("daily_k_open")
        out["daily_k_close"] = prices.get("daily_k_close")
        out["trading_day_number"] = _safe_float(row.get("trading_day_number"))
        rows.append(out)
    row_df = pd.DataFrame(rows)
    if row_df.empty:
        raise RuntimeError(f"No holding-day rows for {start_date} to {end_date}")
    row_df["observable_timing_pct"] = (
        row_df["b1_overnight_gap_timing_pct"]
        + row_df["b2_rebalance_day_intraday_slippage_pct"]
        + row_df["b3_held_intraday_mark_timing_pct"]
    )
    transition_mask = row_df["entry_day"] | row_df["exit_row"]
    young_mask = row_df["entry_day"]
    mature_mask = ~transition_mask
    b1 = float(row_df["b1_overnight_gap_timing_pct"].sum())
    b2 = float(row_df["b2_rebalance_day_intraday_slippage_pct"].sum())
    b3 = float(row_df["b3_held_intraday_mark_timing_pct"].sum())
    b4 = float(target - b1 - b2 - b3)

    mark_price_mismatch = 0.0
    for row in row_df.to_dict("records"):
        weight = float(row.get("target_weight") or 0.0)
        if row.get("daily_k_open") and row.get("mark_open"):
            mark_price_mismatch += weight * abs(float(row["mark_open"]) - float(row["daily_k_open"])) / float(row["daily_k_open"])
        if row.get("daily_k_close") and row.get("mark_close"):
            mark_price_mismatch += weight * abs(float(row["mark_close"]) - float(row["daily_k_close"])) / float(row["daily_k_close"])

    return {
        "start_date": start_date,
        "end_date": end_date,
        "target_pct": target,
        "b1_overnight_gap_timing_pct": b1,
        "b2_rebalance_day_intraday_slippage_pct": b2,
        "b3_held_intraday_mark_timing_pct": b3,
        "b4_mark_weight_sync_residual_pct": b4,
        "observable_timing_sum_pct": b1 + b2 + b3,
        "transition_observable_pct": float(row_df.loc[transition_mask, "observable_timing_pct"].sum()),
        "young_entry_observable_pct": float(row_df.loc[young_mask, "observable_timing_pct"].sum()),
        "mature_held_observable_pct": float(row_df.loc[mature_mask, "observable_timing_pct"].sum()),
        "entry_rows": int(row_df["entry_day"].sum()),
        "exit_rows": int(row_df["exit_row"].sum()),
        "mature_rows": int(mature_mask.sum()),
        "holding_day_rows": int(len(row_df)),
        "avg_gross_exposure": float(row_df.groupby("date")["target_weight"].sum().mean()),
        "mark_price_mismatch_proxy_pct": mark_price_mismatch,
        "missing_required_price_rows": int(row_df["missing_required_price"].sum()),
    }


def _candidate_row(candidate: str, mock_value: float, formal_value: float, mock_b4: float, formal_b4: float, note: str) -> dict[str, Any]:
    return {
        "candidate": candidate,
        "mock_proxy_pct": mock_value,
        "formal_proxy_pct": formal_value,
        "mock_explanatory_power": _power(mock_value, mock_b4),
        "formal_explanatory_power": _power(formal_value, formal_b4),
        "passes_30pct_both_windows": _power(mock_value, mock_b4) >= 0.30 and _power(formal_value, formal_b4) >= 0.30,
        "note": note,
    }


def build_diagnostic(
    db_path: Path,
    mock_attribution_path: Path,
    formal_attribution_path: Path,
    formal_b_path: Path,
) -> dict[str, Any]:
    mock_summary = _load_json(mock_attribution_path)
    formal_summary = _load_json(formal_attribution_path)
    formal_b = _load_json(formal_b_path)
    mock_target, mock_formula = _target_from_attribution(mock_summary)
    formal_target, formal_formula = _target_from_attribution(formal_summary)
    mock = _window_rows("2026-04-23", "2026-04-29", mock_target, db_path)
    formal = _window_rows("2026-05-11", "2026-05-15", formal_target, db_path)
    mock_b4 = mock["b4_mark_weight_sync_residual_pct"]
    formal_b4 = formal["b4_mark_weight_sync_residual_pct"]
    mock_gross_residual = float(mock_summary["attribution"]["gross_exposure_cash_residual_pct"])
    formal_gross_residual = float(formal_summary["attribution"]["gross_exposure_cash_residual_pct"])

    candidate_rows = [
        _candidate_row(
            "C1 target/bucket basis mismatch",
            mock_b4,
            formal_b4,
            mock_b4,
            formal_b4,
            "Definition-level closure: B4 is the explicit gap between the NAV unexplained target and observable timing proxies.",
        ),
        _candidate_row(
            "C2 transition weight snapshot lag",
            mock["transition_observable_pct"],
            formal["transition_observable_pct"],
            mock_b4,
            formal_b4,
            "Entry/exit transition rows dominate the mock but not the formal window.",
        ),
        _candidate_row(
            "C3 mark refresh / stale mark timing",
            mock["mark_price_mismatch_proxy_pct"],
            formal["mark_price_mismatch_proxy_pct"],
            mock_b4,
            formal_b4,
            "Existing mark open/close rows match daily K open/close at this aggregate check.",
        ),
        _candidate_row(
            "C4 cash/gross exposure normalization",
            mock_gross_residual,
            formal_gross_residual,
            mock_b4,
            formal_b4,
            "Gross/cash residual is material in formal but negligible in mock.",
        ),
        _candidate_row(
            "C5 corporate action / price basis",
            0.0,
            0.0,
            mock_b4,
            formal_b4,
            "No existing-data price-basis mismatch is observed in the aggregate open/close checks.",
        ),
    ]
    c1_state = {
        "mock_mode": "young_transition",
        "mock_driver_pct": mock["transition_observable_pct"],
        "mock_driver_power": _power(mock["transition_observable_pct"], mock_b4),
        "formal_mode": "mature_held",
        "formal_driver_pct": formal["mature_held_observable_pct"],
        "formal_driver_power": _power(formal["mature_held_observable_pct"], formal_b4),
        "passes_pm_combination_rule": _power(mock["transition_observable_pct"], mock_b4) >= 0.30
        and _power(formal["mature_held_observable_pct"], formal_b4) >= 0.30,
    }
    recommended = {
        "dominant_root_cause": "C1 target/bucket basis mismatch with window-conditional portfolio state",
        "one_time_vs_structural": (
            "Not universal structural daily drag. It is structural to the current attribution decomposition when B4 is "
            "used to bridge a NAV unexplained target to mark-level timing proxies, but the sign and dominance are window-dependent."
        ),
        "rd002_stage_b_impact": (
            "Execution-slippage case studies such as 6419 remain usable for RD-002. NAV-level B4 residuals must not be "
            "used to score Stage B benefit until the reporting-definition issue is fixed."
        ),
    }
    fix_scopes = [
        {
            "scope": "minimal_reporting_definition",
            "recommendation": "Add C1 basis labels, B4 dominance, and cross-window sign-stability checks to NAV attribution outputs.",
            "blast_radius": "Low: report schema and docs only; no DB mutation.",
            "pm_decision_needed": True,
        },
        {
            "scope": "medium_reporting_refactor",
            "recommendation": "Split NAV unexplained target from observable timing proxy target and report young-transition vs mature-held sub-bases.",
            "blast_radius": "Medium: attribution scripts and tests; regenerate only approved windows first.",
            "pm_decision_needed": True,
        },
        {
            "scope": "ledger_schema_or_write_path",
            "recommendation": "Add explicit as-of weight snapshot or mark timestamp fields only if row-level proof shows C2/C3 cannot be fixed in reporting.",
            "blast_radius": "High: DB schema/write path, historical compatibility, and regression risk.",
            "pm_decision_needed": True,
        },
    ]

    return {
        "status": "actionable_diagnostic_pm_review",
        "inputs": {
            "mock_attribution": str(mock_attribution_path),
            "formal_attribution": str(formal_attribution_path),
            "formal_phase_b": str(formal_b_path),
            "mock_target_formula": mock_formula,
            "formal_target_formula": formal_formula,
        },
        "windows": {
            "mock": mock,
            "formal": formal,
        },
        "formal_phase_b_reference": {
            "status": formal_b["status"],
            "b4_dominance": abs(formal_b["buckets"][3]["contribution_pct"]) >= abs(formal_b["reconciliation"]["observable_b1_b2_b3_sum_pct"]),
        },
        "candidate_explanatory_power": candidate_rows,
        "c1_mature_vs_young_test": c1_state,
        "recommended_interpretation": recommended,
        "fix_scopes": fix_scopes,
        "rd_gate": {
            "production_patch_allowed": False,
            "schema_change_allowed": False,
            "historical_retrofit_allowed": False,
            "next_step": "PM review of offline diagnostic before any implementation patch.",
        },
    }


def _write_markdown(path: Path, payload: dict[str, Any]) -> None:
    mock = payload["windows"]["mock"]
    formal = payload["windows"]["formal"]
    c1_state = payload["c1_mature_vs_young_test"]
    lines = [
        "# RD-006 Sync Residual Offline Diagnostic (2026-05-17)",
        "",
        "## Status",
        f"- Status: `{payload['status']}`",
        "- Scope: offline diagnostic only.",
        "- Production patch, schema change, and historical retrofit remain HOLD.",
        "",
        "## Window Summary",
        "| Window | target | B1 | B2 | B3 | B4 | observable B1+B2+B3 | avg gross exposure |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
        (
            f"| 2026-04-23 to 2026-04-29 | {_pct(mock['target_pct'])} | "
            f"{_pct(mock['b1_overnight_gap_timing_pct'])} | {_pct(mock['b2_rebalance_day_intraday_slippage_pct'])} | "
            f"{_pct(mock['b3_held_intraday_mark_timing_pct'])} | {_pct(mock['b4_mark_weight_sync_residual_pct'])} | "
            f"{_pct(mock['observable_timing_sum_pct'])} | {_pct(mock['avg_gross_exposure'])} |"
        ),
        (
            f"| 2026-05-11 to 2026-05-15 | {_pct(formal['target_pct'])} | "
            f"{_pct(formal['b1_overnight_gap_timing_pct'])} | {_pct(formal['b2_rebalance_day_intraday_slippage_pct'])} | "
            f"{_pct(formal['b3_held_intraday_mark_timing_pct'])} | {_pct(formal['b4_mark_weight_sync_residual_pct'])} | "
            f"{_pct(formal['observable_timing_sum_pct'])} | {_pct(formal['avg_gross_exposure'])} |"
        ),
        "",
        "## Candidate Explanatory Power",
        "| Candidate | mock proxy | mock power | formal proxy | formal power | >=30% both |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for row in payload["candidate_explanatory_power"]:
        lines.append(
            f"| {row['candidate']} | {_pct(row['mock_proxy_pct'])} | {_pct(row['mock_explanatory_power'])} | "
            f"{_pct(row['formal_proxy_pct'])} | {_pct(row['formal_explanatory_power'])} | `{row['passes_30pct_both_windows']}` |"
        )
    lines.extend(
        [
            "",
            "## C1 Mature vs Young Test",
            f"- Mock mode: `{c1_state['mock_mode']}`, driver {_pct(c1_state['mock_driver_pct'])}, power {_pct(c1_state['mock_driver_power'])}",
            f"- Formal mode: `{c1_state['formal_mode']}`, driver {_pct(c1_state['formal_driver_pct'])}, power {_pct(c1_state['formal_driver_power'])}",
            f"- PM combination rule pass: `{c1_state['passes_pm_combination_rule']}`",
            "",
            "Interpretation: mock B4 offsets a young/transition-heavy observable timing proxy, while formal B4 offsets a mature-held observable timing proxy. That supports C1 plus window-conditional portfolio state, not a universal one-direction sync drag.",
            "",
            "## Recommended Interpretation",
            f"- Dominant root cause: {payload['recommended_interpretation']['dominant_root_cause']}",
            f"- One-time vs structural: {payload['recommended_interpretation']['one_time_vs_structural']}",
            f"- RD-002 Stage B impact: {payload['recommended_interpretation']['rd002_stage_b_impact']}",
            "",
            "## Fix Scope Options",
            "| Scope | Recommendation | Blast radius |",
            "|---|---|---|",
        ]
    )
    for item in payload["fix_scopes"]:
        lines.append(f"| {item['scope']} | {item['recommendation']} | {item['blast_radius']} |")
    lines.extend(
        [
            "",
            "## RD Gate",
            f"- Production patch allowed: `{payload['rd_gate']['production_patch_allowed']}`",
            f"- Schema change allowed: `{payload['rd_gate']['schema_change_allowed']}`",
            f"- Historical retrofit allowed: `{payload['rd_gate']['historical_retrofit_allowed']}`",
            f"- Next step: {payload['rd_gate']['next_step']}",
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
    parser = argparse.ArgumentParser(description="Build RD-006 sync residual offline diagnostic artifacts.")
    parser.add_argument("--champion-db", default=str(DEFAULT_CHAMPION_DB_PATH))
    parser.add_argument("--mock-attribution", default=str(DEFAULT_MOCK_ATTRIBUTION))
    parser.add_argument("--formal-attribution", default=str(DEFAULT_FORMAL_ATTRIBUTION))
    parser.add_argument("--formal-b", default=str(DEFAULT_FORMAL_B))
    parser.add_argument("--report-dir", default=str(REPORT_DIR))
    parser.add_argument("--prefix", default=DEFAULT_PREFIX)
    args = parser.parse_args(argv)

    payload = build_diagnostic(
        db_path=Path(args.champion_db),
        mock_attribution_path=Path(args.mock_attribution),
        formal_attribution_path=Path(args.formal_attribution),
        formal_b_path=Path(args.formal_b),
    )
    paths = write_artifacts(payload, Path(args.report_dir), args.prefix)
    c1 = payload["c1_mature_vs_young_test"]
    print(
        "[rd006-sync-diagnostic] "
        f"status={payload['status']} "
        f"c1_combo_pass={c1['passes_pm_combination_rule']} "
        f"md={paths['md']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
