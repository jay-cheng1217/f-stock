from __future__ import annotations

import json

from scripts.req034_my_holdings_dry_run import write_artifacts
from scripts.send_my_holdings_risk_cut_report import append_real_coverage_backfill, render_text, write_report_artifacts


def _summary():
    return {
        "as_of_date": "2026-05-16",
        "status": "dry_run_only_pm_review_required",
        "counts": {"red": 1, "yellow": 1, "green": 1, "total": 3},
        "coverage": {
            "has_red_sample": True,
            "has_yellow_sample": True,
            "has_green_sample": True,
            "has_etf_processing_result": True,
            "has_prediction_universe_fallback": True,
            "prediction_universe_fallback_gap": False,
        },
        "degraded_data_cases": [],
        "synthetic_boundary_appendix": [
            {
                "case": "etf_rule_subset_boundary",
                "bucket": "yellow",
                "reason": "Real 12 holdings include no ETF; this validates ETF coverage expectation only.",
                "example_conditions": ["ETF_V2_ALPHA_MODEL_UNSUPPORTED"],
            }
        ],
        "holdings": [
            {
                "ticker": "2107",
                "asset_type": "stock",
                "bucket": "red",
                "signal": "EXIT",
                "action_group": "cut_or_exit_review",
                "review_status": "STOCK_V2_ALPHA_MODEL_ENABLED",
                "daily_k_status": "ok",
                "reason_codes": ["LOW_LIQUIDITY"],
                "prediction_available": True,
            },
            {
                "ticker": "0050",
                "asset_type": "etf",
                "bucket": "yellow",
                "signal": "TRIM",
                "action_group": "trim_or_watch",
                "review_status": "ETF_RULE_SUBSET_ONLY",
                "daily_k_status": "ok",
                "reason_codes": ["OVERHEAT_MA20"],
                "prediction_available": False,
            },
            {
                "ticker": "9999",
                "asset_type": "stock",
                "bucket": "green",
                "signal": "HOLD",
                "action_group": "keep",
                "review_status": "MODEL_UNAVAILABLE_REVIEW_REQUIRED",
                "daily_k_status": "ok",
                "reason_codes": [],
                "prediction_available": False,
            },
        ],
    }


def test_send_report_omits_private_fields_and_keeps_synthetic_note(tmp_path):
    summary = _summary()
    text = render_text(summary)
    paths = write_report_artifacts(summary, log_dir=tmp_path)

    assert "synthetic-tested" in text
    assert "成本" in text
    assert "股數" in text
    assert "avg_cost" not in text
    assert "shares" not in text
    assert "unrealized" not in text
    assert "2107" in text
    assert "LOW_LIQUIDITY" in text
    assert (tmp_path / "my_holdings_risk_cut_20260516.html").exists()
    assert set(paths) == {"text", "json", "html"}


def test_real_coverage_backfill_appends_only_real_cases(tmp_path):
    summary = _summary()
    log_path = tmp_path / "req034_real_coverage_backfill.log"

    count = append_real_coverage_backfill(summary, log_path=log_path)

    assert count == 2
    rows = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    assert rows[0]["ticker"] == "0050"
    assert set(rows[0]["cases"]) == {"real_yellow", "real_etf"}
    assert rows[1]["ticker"] == "9999"
    assert rows[1]["cases"] == ["real_prediction_fallback"]


def test_dry_run_writes_red_and_synthetic_split_artifacts(tmp_path):
    paths = write_artifacts(_summary(), tmp_path, "req034_v3_my_holdings_dry_run_20260517")

    assert "red_md" in paths
    assert "synthetic_md" in paths
    red_text = (tmp_path / "req034_v3_red_trigger_list_20260517.md").read_text(encoding="utf-8")
    synthetic_text = (tmp_path / "req034_v3_synthetic_boundary_20260517.md").read_text(encoding="utf-8")
    assert "LOW_LIQUIDITY" in red_text
    assert "synthetic_case_etf_rule_subset_boundary" in synthetic_text
