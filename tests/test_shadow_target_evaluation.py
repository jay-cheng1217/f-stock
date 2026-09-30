import numpy as np
import pandas as pd
import pytest
from scripts.evaluate_shadow_dataA_integrity import target_return, paired_cohorts


def test_next_open_and_exdiv_on_entry_excluded():
    dates = pd.bdate_range("2026-01-01", periods=25)
    d = pd.DataFrame({"Date": dates, "Open": 90., "Close": 45.})
    d.loc[0, "Close"] = 100.
    cal = pd.DataFrame({"stock_id": ["2330", "2330"], "date": [dates[1], dates[10]], "factor": [.9, .5]})
    out = target_return(d, "2330", str(dates[0].date()), cal)
    assert out["status"] == "mature" and out["gross_pct"] == pytest.approx(0)
    assert out["entry_date"] == str(dates[1].date())
    assert out["exit_date"] == str(dates[20].date())
    assert out["net_25bps_pct"] < out["net_10bps_pct"] < out["net_0bps_pct"] < 0
    assert target_return(d.iloc[:20], "2330", str(dates[0].date()), cal)["status"] == "immature"


def test_partial_basket_never_enters_paired_average():
    rows = pd.DataFrame([{"date": "2026-01-01", "ticker": str(t), "side": side,
                          "status": "mature", "gross_pct": 1., "net_10bps_pct": .2}
                         for side in ("shadow", "champion") for t in range(30)])
    assert len(paired_cohorts(rows)) == 1
    rows.loc[0, "status"] = "immature"
    assert paired_cohorts(rows).empty
