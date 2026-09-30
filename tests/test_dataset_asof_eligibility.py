import os

import numpy as np
import pandas as pd

from ml import dataset


def test_asof_mask_is_not_changed_by_future_rows(monkeypatch):
    monkeypatch.setattr(dataset, "MIN_HISTORY_DAYS", 3)
    monkeypatch.setattr(dataset, "MIN_AVG_VOLUME", 100)
    monkeypatch.setattr(dataset, "MIN_PRICE", 10.0)
    monkeypatch.setattr(dataset, "_LIQUIDITY_LOOKBACK_DAYS", 3)

    base = pd.DataFrame({"Volume": [200, 200, 200, 200], "Close": [20, 20, 20, 20]})
    extended = pd.concat(
        [base, pd.DataFrame({"Volume": [1, 1, 1], "Close": [5, 5, 5]})],
        ignore_index=True,
    )

    base_mask = dataset._asof_eligibility_mask(base)
    extended_mask = dataset._asof_eligibility_mask(extended)

    assert extended_mask.iloc[: len(base)].tolist() == base_mask.tolist()
    assert base_mask.tolist() == [False, False, True, True]


def test_historical_mode_keeps_eligible_history_when_latest_universe_fails(
    tmp_path,
    monkeypatch,
):
    dates = pd.date_range("2025-01-01", periods=180, freq="D")
    frame = pd.DataFrame(
        {
            "Date": dates.strftime("%Y-%m-%d"),
            "Close": np.full(180, 20.0),
            "Volume": np.r_[np.full(120, 500_000), np.full(60, 10_000)],
        }
    )
    frame.to_csv(tmp_path / "1234.csv", index=False)

    monkeypatch.setattr(dataset, "DAILY_K_DIR", str(tmp_path))
    monkeypatch.setattr(dataset, "get_available_features", lambda: set())
    monkeypatch.setattr(dataset, "_get_issued_shares", lambda: {})
    monkeypatch.setattr(
        dataset,
        "compute_target",
        lambda df, twii_df=None, **kwargs: df.assign(target=1),
    )

    historical = dataset.load_single_stock("1234", eligibility_mode="asof")
    latest = dataset.load_single_stock("1234", eligibility_mode="latest")

    assert historical is not None
    assert historical["Date"].min() == dates[119]
    assert historical["Date"].max() < dates[-1]
    assert latest is None


def test_latest_snapshot_requests_latest_eligibility(monkeypatch):
    calls = []
    monkeypatch.setattr(dataset, "_list_daily_tickers", lambda: ["1234"])
    monkeypatch.setattr(dataset, "_load_twii", lambda: None)
    monkeypatch.setattr(dataset, "get_available_features", lambda: set())
    monkeypatch.setattr(dataset, "compute_industry_features", lambda df: df)
    monkeypatch.setattr(dataset, "_apply_percentile_ranking", lambda df: df)
    monkeypatch.setattr(dataset, "_winsorize_features", lambda df: df)
    monkeypatch.setattr(dataset, "_apply_cross_sectional_zscore", lambda df: df)

    def fake_load(ticker, twii_df=None, *, eligibility_mode="asof"):
        calls.append(eligibility_mode)
        return pd.DataFrame(
            {"Date": [pd.Timestamp("2026-07-16")], "ticker": [ticker], "target": [1]}
        )

    monkeypatch.setattr(dataset, "load_single_stock", fake_load)

    snapshot = dataset.build_latest_snapshot(verbose=False)

    assert not snapshot.empty
    assert calls == ["latest"]


def test_raw_cache_invalidates_when_non_valuation_source_changes(tmp_path, monkeypatch):
    cache_path = tmp_path / "dataset_cache.parquet"
    source_path = tmp_path / "financial_2026Q1.csv"
    pd.DataFrame({"ticker": ["2330"], "target": [1]}).to_parquet(cache_path, index=False)
    source_path.write_text("Ticker,Year,Season\n2330,2026,1\n", encoding="utf-8")
    cache_time = cache_path.stat().st_mtime
    os.utime(source_path, (cache_time + 5, cache_time + 5))

    monkeypatch.setattr(dataset, "_get_cache_path", lambda: str(cache_path))
    monkeypatch.setattr(
        dataset,
        "_raw_cache_source_patterns",
        lambda: [str(tmp_path / "financial_*.csv")],
    )

    assert dataset.load_raw_cache(verbose=False) is None
    assert not cache_path.exists()


def test_raw_cache_survives_when_all_sources_are_older(tmp_path, monkeypatch):
    cache_path = tmp_path / "dataset_cache.parquet"
    source_path = tmp_path / "tdcc_summary.csv"
    source_path.write_text("Ticker,Date\n2330,20260709\n", encoding="utf-8")
    pd.DataFrame({"ticker": ["2330"], "target": [1]}).to_parquet(cache_path, index=False)
    cache_time = cache_path.stat().st_mtime
    os.utime(source_path, (cache_time - 5, cache_time - 5))

    monkeypatch.setattr(dataset, "_get_cache_path", lambda: str(cache_path))
    monkeypatch.setattr(dataset, "_raw_cache_source_patterns", lambda: [str(source_path)])

    cached = dataset.load_raw_cache(verbose=False)

    assert cached is not None
    assert cached.iloc[0]["ticker"] == "2330"
