from __future__ import annotations

import pandas as pd

from backend.features.market_regime import add_market_regime_features, build_market_regime_features
from ml.config import REGIME_FEATURE_COLS
from ml.features.registry import get_feature_columns


def test_market_regime_features_are_percentiles() -> None:
    regime = build_market_regime_features()
    assert set(REGIME_FEATURE_COLS).issubset(regime.columns)

    usable = regime[REGIME_FEATURE_COLS].dropna(how="all")
    assert not usable.empty
    for col in REGIME_FEATURE_COLS:
        values = usable[col].dropna()
        assert not values.empty
        assert values.between(0.0, 1.0).all()


def test_market_regime_features_are_registered() -> None:
    cols = get_feature_columns()
    for col in REGIME_FEATURE_COLS:
        assert col in cols


def test_market_regime_join_uses_prior_market_day() -> None:
    regime = build_market_regime_features()
    complete = regime.dropna(subset=REGIME_FEATURE_COLS)
    assert len(complete) >= 2

    current = complete.iloc[-1]
    previous = complete.iloc[-2]
    frame = pd.DataFrame(
        {
            "Date": [current["Date"]],
            "ticker": ["2330"],
            "dummy": [1.0],
        }
    )

    out = add_market_regime_features(frame)

    # The feature row for date T must not use T's just-finished close; it should
    # match the precomputed lagged value available before T.
    for col in REGIME_FEATURE_COLS:
        assert out[col].iloc[0] == current[col]
        assert current[col] == current[col]  # not NaN
    assert previous["Date"] < current["Date"]
