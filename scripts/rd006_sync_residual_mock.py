"""Build RD-006 sync residual mock artifact.

This is an offline diagnostic. It reuses the locked RD-005 Phase B bucket
construction on the 2026-04-23 to 2026-04-29 mock window and compares it with
the formal 2026-05-11 to 2026-05-15 B4 result.
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

DEFAULT_MOCK_START = "2026-04-23"
DEFAULT_MOCK_END = "2026-04-29"
DEFAULT_FORMAL_B = Path(REPORT_DIR) / "rd005_phase_b_timing_split_20260517.json"
DEFAULT_MOCK_ATTRIBUTION = Path(REPORT_DIR) / "champion_nav_attribution_20260423_20260429.json"
DEFAULT_PREFIX = "rd006_sync_residual_mock_20260517"


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(path)
    return json.loads(path.read_text(encoding="utf-8"))


def _target_from_attribution(summary: dict[str, Any]) -> tuple[float, str]:
    residual_split = summary.get("attribution", {}).get("residual_split")
    if residual_split and residual_split.get("unexplained_reconciliation_residual_pct") is not None:
        return float(residual_split["unexplained_reconciliation_residual_pct"]), (
            "attribution.residual_split.unexplained_reconciliation_residual_pct"
        )
    attribution = summary["attribution"]
    nav = summary["nav"]
    target = (
        float(attribution["gross_exposure_cash_residual_pct"])
        - float(nav.get("execution_pnl_pct") or 0.0)
        - float(nav.get("exit_pnl_pct") or 0.0)
    )
    return target, "gross_exposure_cash_residual_pct - execution_pnl_pct - exit_pnl_pct"


def _split_window(start_date: str, end_date: str, target: float, db_path: Path) -> dict[str, Any]:
    positions, marks = _load_positions_and_marks(db_path)
    frame = _build_holding_day_frame(positions, marks, start_date, end_date)
    daily_cache: dict[str, pd.DataFrame] = {}
    rows = [_row_contributions(row, _daily_prices(daily_cache, row["ticker"], row["mark_date"])) for _, row in frame.iterrows()]
    row_df = pd.DataFrame(rows)
    if row_df.empty:
        raise RuntimeError(f"No holding-day rows for {start_date} to {end_date}")

    b1 = float(row_df["b1_overnight_gap_timing_pct"].sum())
    b2 = float(row_df["b2_rebalance_day_intraday_slippage_pct"].sum())
    b3 = float(row_df["b3_held_intraday_mark_timing_pct"].sum())
    b4 = float(target - b1 - b2 - b3)
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
    observable = b1 + b2 + b3
    return {
        "start_date": start_date,
        "end_date": end_date,
        "target_pct": target,
        "buckets": {
            "b1_overnight_gap_timing_pct": b1,
            "b2_rebalance_day_intraday_slippage_pct": b2,
            "b3_held_intraday_mark_timing_pct": b3,
            "b4_mark_weight_sync_residual_pct": b4,
        },
        "observable_b1_b2_b3_sum_pct": observable,
        "b4_dominance_triggered": abs(b4) >= abs(observable),
        "holding_day_rows": int(len(row_df)),
        "missing_required_price_rows": int(row_df["missing_required_price"].sum()),
        "avg_gross_exposure": float(daily["gross_exposure"].mean()),
        "daily_contributions": daily.to_dict("records"),
    }


def build_mock(
    mock_start: str,
    mock_end: str,
    db_path: Path,
    mock_attribution_path: Path,
    formal_b_path: Path,
) -> dict[str, Any]:
    mock_summary = _load_json(mock_attribution_path)
    formal = _load_json(formal_b_path)
    mock_target, target_formula = _target_from_attribution(mock_summary)
    mock = _split_window(mock_start, mock_end, mock_target, db_path)
    formal_b4 = float(next(row["contribution_pct"] for row in formal["buckets"] if row["key"] == "b4_mark_weight_sync_residual_pct"))
    formal_b1 = float(next(row["contribution_pct"] for row in formal["buckets"] if row["key"] == "b1_overnight_gap_timing_pct"))
    formal_b2 = float(next(row["contribution_pct"] for row in formal["buckets"] if row["key"] == "b2_rebalance_day_intraday_slippage_pct"))
    formal_b3 = float(next(row["contribution_pct"] for row in formal["buckets"] if row["key"] == "b3_held_intraday_mark_timing_pct"))
    formal_observable = float(formal["reconciliation"]["observable_b1_b2_b3_sum_pct"])
    mock_b4 = float(mock["buckets"]["b4_mark_weight_sync_residual_pct"])
    mock_observable = float(mock["observable_b1_b2_b3_sum_pct"])
    same_sign = formal_b4 * mock_b4 > 0
    entry_slippage_offset_ratio = abs(mock_b4) / abs(mock["buckets"]["b2_rebalance_day_intraday_slippage_pct"]) if mock["buckets"]["b2_rebalance_day_intraday_slippage_pct"] else None

    return {
        "status": "mock_inconsistent_needs_pm_review",
        "mock_window": [mock_start, mock_end],
        "mock_target_formula": target_formula,
        "mock": mock,
        "formal_reference": {
            "source": str(formal_b_path),
            "window": [formal["start_date"], formal["end_date"]],
            "b1_overnight_gap_timing_pct": formal_b1,
            "b2_rebalance_day_intraday_slippage_pct": formal_b2,
            "b3_held_intraday_mark_timing_pct": formal_b3,
            "b4_mark_weight_sync_residual_pct": formal_b4,
            "observable_b1_b2_b3_sum_pct": formal_observable,
            "b4_dominance_triggered": abs(formal_b4) >= abs(formal_observable),
        },
        "cross_window_checks": {
            "b4_same_sign": same_sign,
            "mock_b4_dominance_triggered": bool(mock["b4_dominance_triggered"]),
            "formal_b4_dominance_triggered": abs(formal_b4) >= abs(formal_observable),
            "entry_slippage_offset_ratio_abs_mock_b4_to_abs_mock_b2": entry_slippage_offset_ratio,
        },
        "candidate_readout": [
            {
                "candidate": "C1 target/bucket basis mismatch",
                "mock_support": "high",
                "reason": "B4 flips sign across the mock and formal windows, so a stable one-direction daily sync drag is not established.",
            },
            {
                "candidate": "C2 weight snapshot lag",
                "mock_support": "medium",
                "reason": "Mock gross exposure averages above 100%, while formal exposure is below 100%; exposure normalization likely changes B4 behavior.",
            },
            {
                "candidate": "C3 mark refresh / stale mark timing",
                "mock_support": "low_to_medium",
                "reason": "No missing required price rows are observed in the mock, but timestamp semantics still require row-level inspection.",
            },
            {
                "candidate": "C4 cash/gross exposure normalization",
                "mock_support": "medium",
                "reason": "Mock leverage and formal near-cash exposure differ materially, making gross/cash normalization a plausible B4 driver.",
            },
            {
                "candidate": "C5 corporate action / price basis",
                "mock_support": "low",
                "reason": "No candidate evidence from existing aggregate rows yet; defer unless row-level outliers appear.",
            },
        ],
        "sa_gate": {
            "rd_patch_allowed": False,
            "reason": "Mock does not reproduce the same B4 sign/dominance pattern. RD should not patch production attribution before PM accepts a broader diagnostic scope.",
        },
    }


def _write_markdown(path: Path, payload: dict[str, Any]) -> None:
    mock = payload["mock"]
    formal = payload["formal_reference"]
    cross = payload["cross_window_checks"]
    lines = [
        "# RD-006 Sync Residual Mock (2026-05-17)",
        "",
        "## Status",
        f"- Status: `{payload['status']}`",
        "- Scope: existing-data mock only.",
        "- No production code, ledger schema, model, allocation, sector cap, or guardrail change is recommended.",
        "",
        "## Window Comparison",
        "| Window | B1 | B2 | B3 | B4 | B1+B2+B3 | B4 dominance |",
        "|---|---:|---:|---:|---:|---:|---|",
        (
            f"| {mock['start_date']} to {mock['end_date']} | "
            f"{_pct(mock['buckets']['b1_overnight_gap_timing_pct'])} | "
            f"{_pct(mock['buckets']['b2_rebalance_day_intraday_slippage_pct'])} | "
            f"{_pct(mock['buckets']['b3_held_intraday_mark_timing_pct'])} | "
            f"{_pct(mock['buckets']['b4_mark_weight_sync_residual_pct'])} | "
            f"{_pct(mock['observable_b1_b2_b3_sum_pct'])} | "
            f"`{mock['b4_dominance_triggered']}` |"
        ),
        (
            f"| {formal['window'][0]} to {formal['window'][1]} | "
            f"{_pct(formal['b1_overnight_gap_timing_pct'])} | "
            f"{_pct(formal['b2_rebalance_day_intraday_slippage_pct'])} | "
            f"{_pct(formal['b3_held_intraday_mark_timing_pct'])} | "
            f"{_pct(formal['b4_mark_weight_sync_residual_pct'])} | "
            f"{_pct(formal['observable_b1_b2_b3_sum_pct'])} | "
            f"`{formal['b4_dominance_triggered']}` |"
        ),
        "",
        "## Cross-Window Checks",
        f"- B4 same sign across mock/formal: `{cross['b4_same_sign']}`",
        f"- Mock B4 dominance triggered: `{cross['mock_b4_dominance_triggered']}`",
        f"- Formal B4 dominance triggered: `{cross['formal_b4_dominance_triggered']}`",
        f"- Mock abs(B4) / abs(B2): {_pct(cross['entry_slippage_offset_ratio_abs_mock_b4_to_abs_mock_b2'])}",
        "",
        "## Candidate Readout",
    ]
    for item in payload["candidate_readout"]:
        lines.extend(
            [
                f"### {item['candidate']}",
                f"- Mock support: `{item['mock_support']}`",
                f"- Reason: {item['reason']}",
                "",
            ]
        )
    lines.extend(
        [
            "## SA Gate",
            f"- RD patch allowed: `{payload['sa_gate']['rd_patch_allowed']}`",
            f"- Reason: {payload['sa_gate']['reason']}",
            "",
            "Mock interpretation: RD-005 remains closed, but RD-006 should not assume a stable one-direction sync drag from the formal window alone.",
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
    parser = argparse.ArgumentParser(description="Build RD-006 sync residual mock artifacts.")
    parser.add_argument("--mock-start", default=DEFAULT_MOCK_START)
    parser.add_argument("--mock-end", default=DEFAULT_MOCK_END)
    parser.add_argument("--champion-db", default=str(DEFAULT_CHAMPION_DB_PATH))
    parser.add_argument("--mock-attribution", default=str(DEFAULT_MOCK_ATTRIBUTION))
    parser.add_argument("--formal-b", default=str(DEFAULT_FORMAL_B))
    parser.add_argument("--report-dir", default=str(REPORT_DIR))
    parser.add_argument("--prefix", default=DEFAULT_PREFIX)
    args = parser.parse_args(argv)

    payload = build_mock(
        mock_start=args.mock_start,
        mock_end=args.mock_end,
        db_path=Path(args.champion_db),
        mock_attribution_path=Path(args.mock_attribution),
        formal_b_path=Path(args.formal_b),
    )
    paths = write_artifacts(payload, Path(args.report_dir), args.prefix)
    print(
        "[rd006-sync-mock] "
        f"status={payload['status']} "
        f"mock_b4={_pct(payload['mock']['buckets']['b4_mark_weight_sync_residual_pct'])} "
        f"formal_b4={_pct(payload['formal_reference']['b4_mark_weight_sync_residual_pct'])} "
        f"md={paths['md']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
