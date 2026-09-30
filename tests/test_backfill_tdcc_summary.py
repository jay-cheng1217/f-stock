import pandas as pd

from scripts import backfill_tdcc


def test_rebuild_summary_uses_pm_approved_retail_and_whale_levels(tmp_path, monkeypatch):
    monkeypatch.setattr(backfill_tdcc, "TDCC_DIR", str(tmp_path))

    rows = pd.DataFrame(
        {
            "Date": ["20260710"] * 6,
            "Ticker": ["2330"] * 6,
            "Level": [1, 9, 10, 12, 14, 15],
            "People": [10, 20, 30, 40, 50, 60],
            "HoldingShares": [100, 200, 300, 400, 500, 600],
            "HoldingSharesPer": [1.0, 2.0, 3.0, 4.0, 5.0, 80.0],
        }
    )
    rows.to_csv(tmp_path / "tdcc_20260710.csv", index=False, encoding="utf-8-sig")

    backfill_tdcc.rebuild_summary_v2()

    summary = pd.read_csv(tmp_path / "tdcc_summary.csv", dtype={"Ticker": str})
    assert len(summary) == 1
    assert summary.loc[0, "Ticker"] == "2330"
    assert summary.loc[0, "Retail_Pct"] == 3.0
    assert summary.loc[0, "Whale_Pct"] == 80.0
    assert summary.loc[0, "Total_Holders"] == 210
