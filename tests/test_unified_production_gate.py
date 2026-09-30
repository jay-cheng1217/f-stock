from __future__ import annotations

import pandas as pd

from scripts import build_unified_signals as bus


def test_closed_t1_gate_does_not_close_20d_production_gate(tmp_path, monkeypatch) -> None:
    prediction_date = "2026-05-08"
    pred20_path = tmp_path / f"predictions_{prediction_date}.csv"
    predt1_path = tmp_path / f"predictions_t1_{prediction_date}.csv"
    pred20_path.write_text("ticker,date,close\n2330,2026-05-08,100\n", encoding="utf-8")
    predt1_path.write_text("ticker,date,selected_for_trade\n2330,2026-05-08,true\n", encoding="utf-8")

    monkeypatch.setattr(bus, "MODEL_DIR", str(tmp_path))
    monkeypatch.setattr(
        bus,
        "evaluate_t1_production_gate",
        lambda: {
            "status": "CLOSED",
            "closed": True,
            "action": "freeze_t1_and_dual",
            "reason": "t1_monitor_avg_ic=-0.10; t1_monitor_win_rate=0.20",
        },
    )
    monkeypatch.setattr(
        bus,
        "_build_20d_candidates",
        lambda pred20_df, top_n, penalty_overlay=False, penalty_overlay_version=bus.PENALTY_OVERLAY_DEFAULT_VERSION: pd.DataFrame(
            [
                {
                    "ticker": "2330",
                    "prediction_date": prediction_date,
                    "rank_20d": 1,
                    "recommendation_20d": "建議買進",
                    "signal_20d": "BUY",
                    "pred_return_20d": 0.08,
                    "risk_adjusted_return": 0.07,
                    "guardrail_blocked_20d": 0,
                    "guardrail_trigger_reason_20d": "",
                    "avg_20d_volume_20d": 10_000,
                    "avg_20d_amount_20d": 50_000_000,
                    "avg_5d_volume_20d": 10_000,
                    "avg_5d_amount_20d": 50_000_000,
                    "price_vs_ma60_20d": 0.10,
                    "price_vs_ma5_20d": 0.02,
                    "price_vs_ma20_20d": 0.04,
                    "beta_60_20d": 1.0,
                    "sector_20d": "半導體業",
                    "group_code_20d": "OTHER",
                    "group_name_20d": "Other / uncapped",
                    "risk_tags_20d": "",
                    "two_stage_rank_20d": 1,
                    "pe_ratio_20d": 20.0,
                }
            ]
        ),
    )

    result = bus.build_unified_signals(
        pred_date=prediction_date,
        prediction_20d_path=str(pred20_path),
        prediction_t1_path=str(predt1_path),
        verbose=False,
    )

    out = pd.read_csv(result.output_path, dtype={"ticker": str})
    row = out.iloc[0]

    assert result.production_gate_status == "OPEN"
    assert result.production_gate_reason == ""
    assert row["production_gate_status"] == "OPEN"
    assert not str(row.get("production_gate_reason", "")).startswith("t1_")
    assert row["signal_type"] == bus.SIGNAL_TYPE_20D_ONLY
    assert int(row["target_units"]) == bus.TARGET_UNITS_MAP[bus.SIGNAL_TYPE_20D_ONLY]
