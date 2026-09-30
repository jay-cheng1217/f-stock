from __future__ import annotations

import pandas as pd

from ml.predict import _apply_recommendation_rules_core


def test_guardrail_blocked_rows_have_non_empty_trigger_reason() -> None:
    pred = pd.DataFrame(
        [
            {
                "ticker": "3528",
                "up_prob": 0.70,
                "flat_prob": 0.10,
                "down_prob": 0.20,
                "signal": "UP",
                "pred_return_20d": 0.08,
                "inst_vol_pct_10d": 0.0,
            }
        ]
    )
    snapshot = pd.DataFrame(
        [
            {
                "ticker": "3528",
                "price_vs_ma20": 0.05,
                "VOL_MA_5": 100_000,
                "Volume": 100_000,
                "return_20d": 0.02,
                "operating_margin_latest": 0.10,
                "chip_diverge_bear": 0.0,
                "chip_diverge_bull": 0.0,
            }
        ]
    )

    out = _apply_recommendation_rules_core(pred, snapshot)
    row = out.iloc[0]

    assert row["guardrail_blocked"] == 1
    assert row["guardrail_trigger_reason"]
    assert "LIQUIDITY_GUARD" in row["guardrail_trigger_reason"]
