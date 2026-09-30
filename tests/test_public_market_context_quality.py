from __future__ import annotations

import json
from pathlib import Path

from scripts.public_market_context_quality import (
    compact_public_market_context_columns,
    evaluate_public_market_context,
)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _base_report(dataset_id: str, *, latest_date: str, output_path: str) -> dict:
    return {
        "status": "ok",
        "generated_at": "2026-06-03T08:00:00",
        "dataset_count": 1,
        "ok_count": 1,
        "failed_count": 0,
        "datasets": [
            {
                "dataset_id": dataset_id,
                "status": "ok",
                "required": True,
                "latest_date": latest_date,
                "row_count": 1,
                "output_path": output_path,
            }
        ],
    }


def test_public_market_context_quality_passes_complete_fields(tmp_path):
    output_path = "ml/data/public_market_context/taifex_put_call_ratio_latest.json"
    _write_json(
        tmp_path / output_path,
        {
            "rows": [
                {
                    "Date": "20260602",
                    "PutCallVolumeRatio%": "118.71",
                    "PutCallOIRatio%": "171.12",
                }
            ]
        },
    )
    _write_json(
        tmp_path / "ml/reports/public_market_context_latest.json",
        _base_report("taifex_put_call_ratio", latest_date="2026-06-02", output_path=output_path),
    )

    quality = evaluate_public_market_context(base_dir=tmp_path, as_of_date="2026-06-03")
    compact = compact_public_market_context_columns(quality)

    assert quality["status"] == "ok"
    assert quality["preflight_pass"] is True
    assert quality["missing_field_datasets"] == []
    assert compact["public_market_context_time_basis"] == "latest_snapshot_not_point_in_time"
    assert compact["public_market_context_model_feature_readiness"] == "not_ready"


def test_public_market_context_quality_fails_missing_expected_field(tmp_path):
    output_path = "ml/data/public_market_context/taifex_put_call_ratio_latest.json"
    _write_json(
        tmp_path / output_path,
        {"rows": [{"Date": "20260602", "PutCallVolumeRatio%": "118.71"}]},
    )
    _write_json(
        tmp_path / "ml/reports/public_market_context_latest.json",
        _base_report("taifex_put_call_ratio", latest_date="2026-06-02", output_path=output_path),
    )

    quality = evaluate_public_market_context(base_dir=tmp_path, as_of_date="2026-06-03")

    assert quality["status"] == "failed"
    assert quality["preflight_pass"] is False
    assert quality["missing_field_datasets"] == [
        {"dataset_id": "taifex_put_call_ratio", "missing_fields": ["PutCallOIRatio%"]}
    ]


def test_public_market_context_quality_marks_stale_optional_context(tmp_path):
    output_path = "ml/data/public_market_context/twse_news_latest.json"
    _write_json(
        tmp_path / output_path,
        {"rows": [{"Date": "1150520", "Title": "x", "Url": "https://example.test"}]},
    )
    _write_json(
        tmp_path / "ml/reports/public_market_context_latest.json",
        _base_report("twse_news", latest_date="2026-05-20", output_path=output_path),
    )

    quality = evaluate_public_market_context(base_dir=tmp_path, as_of_date="2026-06-03")

    assert quality["status"] == "degraded"
    assert quality["preflight_pass"] is True
    assert quality["stale_datasets"][0]["dataset_id"] == "twse_news"
