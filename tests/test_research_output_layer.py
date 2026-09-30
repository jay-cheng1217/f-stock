from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pandas as pd

from scripts.research_output_layer import (
    build_research_output_pack,
    write_research_output_artifacts,
)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_research_output_layer_builds_all_packs(tmp_path):
    report_dir = tmp_path / "ml" / "reports"
    model_dir = tmp_path / "ml" / "models"
    public_dir = tmp_path / "ml" / "data" / "public_market_context"
    report_dir.mkdir(parents=True)
    model_dir.mkdir(parents=True)
    public_dir.mkdir(parents=True)

    signal_csv = model_dir / "unified_signals_2026-06-02.csv"
    pd.DataFrame(
        [
            {
                "ticker": "2360",
                "signal_type": "20D_only",
                "tradability_reason": "",
                "selection_model_alignment": "tquant_and_entry_benchmark_aligned",
                "selection_model_reason": "aligned",
            },
            {
                "ticker": "2451",
                "signal_type": "20D_only",
                "tradability_reason": "",
                "selection_model_alignment": "tquant_override_entry_benchmark",
                "selection_model_reason": "entry benchmark did not confirm",
            },
        ]
    ).to_csv(signal_csv, index=False, encoding="utf-8-sig")
    _write_json(
        report_dir / "unified_signals_latest.json",
        {
            "prediction_date": "2026-06-02",
            "source_signal_path": str(signal_csv),
            "selected_count": 2,
            "rejected_count": 1,
            "selected": [
                {
                    "ticker": "2360",
                    "signal_type": "20D_only",
                    "rank_20d": 2,
                    "target_weight_pct": 50,
                    "explain_summary": "ready",
                },
                {
                    "ticker": "2451",
                    "signal_type": "20D_only",
                    "rank_20d": 11,
                    "target_weight_pct": 50,
                    "explain_summary": "thin edge",
                },
            ],
            "rejected": [
                {
                    "ticker": "4989",
                    "rejected_reason": "TQUANT_REFERENCE_GATE",
                    "tradability_reason": "TQUANT_REFERENCE_GATE",
                    "explain_summary": "blocked",
                }
            ],
        },
    )
    _write_json(
        report_dir / "dashboard_summary.json",
        {
            "champion": {
                "active_positions": 10,
                "invested_weight_pct": 79.87,
                "mtm_return_pct": -9.69,
                "mtm_alpha_vs_twii_pct": -29.96,
                "max_drawdown_pct": -12.57,
            }
        },
    )
    _write_json(
        report_dir / "market_regime_latest.json",
        {"state": "OPEN", "action": "ALLOW_NEW_POSITIONS", "reason": "ok"},
    )
    _write_json(
        report_dir / "public_market_context_quality_latest.json",
        {
            "status": "ok",
            "selection_usage_mode": "advisory_metadata_only",
            "model_feature_readiness": "not_ready",
            "model_feature_gap": "latest_snapshot_only",
        },
    )
    _write_json(
        public_dir / "twse_news_latest.json",
        {"rows": [{"Date": "1150602", "Title": "2360 official news", "Url": "https://example.test"}]},
    )
    _write_json(
        public_dir / "twse_disposition_latest.json",
        {
            "rows": [
                {
                    "Date": "1150602",
                    "Code": "4989",
                    "Name": "test",
                    "DispositionPeriod": "115/06/02",
                    "DispositionMeasures": "test",
                }
            ]
        },
    )
    pd.DataFrame(
        [{"month": "2026-05", "top30_excess": 0.01, "spread": 0.02, "ic": 0.3}]
    ).to_csv(report_dir / "v2_backtest_20260601_010101.csv", index=False)
    pd.DataFrame([{"feature": "x", "importance": 10.0}]).to_csv(
        report_dir / "feature_importance_v2_20260601_010101.csv", index=False
    )
    pd.DataFrame([{"ticker": "2360"}]).to_csv(
        report_dir / "v2_fold_top30_20260601_010101.csv", index=False
    )
    _write_json(
        model_dir / "lgbm_v2_20260601_010101_meta.json",
        {"model_version": "v2", "trained_at": "20260601_010101", "n_features": 3, "n_samples": 100},
    )
    _write_json(
        report_dir / "v2_overlay_decision_latest.json",
        {
            "status": "FAIL",
            "promotion_ready": False,
            "audit": {"comparison_note": "strategy_comparison", "chosen_overlay": "time15"},
            "footer": "no promotion",
        },
    )
    _write_json(
        report_dir / "agent_arena_backtest_latest.json",
        {
            "status": "ok",
            "admitted_count": 1,
            "agent_count": 2,
            "latest_market_date": "2026-06-02",
            "results": [
                {
                    "agent_id": "volume_breakout",
                    "name": "Volume Breakout",
                    "admission_status": "ADMITTED",
                    "total_return_pct": 31.6,
                    "max_drawdown_pct": -19.8,
                    "win_rate_pct": 38.1,
                }
            ],
        },
    )
    watchlist = tmp_path / "config" / "research_watchlist.csv"
    watchlist.parent.mkdir(parents=True)
    watchlist.write_text(
        "ticker,label,thesis,risk_note,owner,updated_at\n2360,PM watch,test thesis,test risk,PM,2026-06-02\n",
        encoding="utf-8",
    )
    db = sqlite3.connect(tmp_path / "my_holdings.db")
    db.execute("CREATE TABLE holdings (ticker TEXT, shares REAL, avg_cost REAL)")
    db.execute("INSERT INTO holdings VALUES ('2360', 100, 10.5)")
    db.commit()
    db.close()

    pack = build_research_output_pack(base_dir=tmp_path)
    paths = write_research_output_artifacts(pack, report_dir=report_dir)

    assert pack["daily_investment_memo"]["selected_count"] == 2
    assert pack["catalyst_tracker"]["event_count"] >= 2
    assert pack["weekly_v2_pm_pack"]["backtest"]["avg_ic"] == 0.3
    assert pack["ab_review_pack"]["overlay_decision"]["status"] == "FAIL"
    assert pack["watchlist_context"]["row_count"] == 1
    assert pack["holdings_memo"]["tickers"] == ["2360"]
    assert "avg_cost" not in json.dumps(pack["holdings_memo"], ensure_ascii=False)
    assert Path(paths["research_output_layer_md"]).exists()
    assert Path(paths["catalyst_tracker_csv"]).exists()
