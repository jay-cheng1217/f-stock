"""Information-boundary fixtures. No real LightGBM fitting or model writes."""
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

import scripts.train_v2 as v2


def frame():
    dates = pd.bdate_range("2022-01-03", "2024-03-29")
    raw = pd.DataFrame([(ticker, date, float(i), float(i % 11))
                        for ticker in ("2330", "2317") for i, date in enumerate(dates)],
                       columns=["ticker", "Date", "feature", "target_value"])
    return v2._prepare_temporal_frame(raw)


def test_both_label_horizons_finish_before_the_next_partition():
    prepared, calendar = frame()
    fold = v2._temporal_fold(prepared, calendar, pd.Timestamp("2024-01-01"))
    assert fold is not None
    train, validation, test = (fold[k] for k in ("train", "validation", "test"))
    assert train["_label_end_date"].max() < validation["Date"].min()
    assert validation["_label_end_date"].max() < test["Date"].min()
    assert validation["Date"].nunique() == 60
    for earlier, later in ((train, validation), (validation, test)):
        left = calendar.get_loc(earlier["Date"].max())
        right = calendar.get_loc(later["Date"].min())
        assert right - left == 21


def test_sparse_ticker_horizons_cannot_sneak_across_global_date_purge():
    dates = pd.bdate_range("2022-01-03", "2024-03-29")
    raw = pd.concat([
        pd.DataFrame({"ticker": "2330", "Date": dates}),
        pd.DataFrame({"ticker": "9999", "Date": dates[::3]}),
    ], ignore_index=True)
    prepared, calendar = v2._prepare_temporal_frame(raw)
    fold = v2._temporal_fold(prepared, calendar, pd.Timestamp("2024-01-01"))
    assert fold is not None
    sparse_validation = fold["validation"].query("ticker == '9999'")
    assert len(sparse_validation) > 0
    assert (sparse_validation["_label_end_date"] < fold["test_start"]).all()
    # The final sparse validation signals must be removed even when their
    # signal dates precede the ordinary global embargo cutoff.
    candidates = prepared[(prepared["ticker"] == "9999")
                          & prepared["Date"].between(fold["validation_start"], fold["validation_end_exclusive"], inclusive="left")]
    assert len(sparse_validation) < len(candidates)


def test_target_missing_days_remain_in_the_calendar():
    prepared, calendar = frame()
    removed = calendar[300:310]
    labelled = prepared[~prepared["Date"].isin(removed)]
    assert len(calendar.intersection(removed)) == 10
    fold = v2._temporal_fold(labelled, calendar, pd.Timestamp("2024-01-01"))
    assert fold is not None
    assert fold["validation"]["_label_end_date"].max() < fold["test_start"]


def test_early_stopping_never_receives_test_labels(monkeypatch):
    prepared, calendar = frame()
    fold = v2._temporal_fold(prepared, calendar, pd.Timestamp("2024-01-01"))
    for name, value in (("train", 1.0), ("validation", 2.0), ("test", 9999.0)):
        fold[name]["target_value"] = value
    calls = []
    callback_calls = []

    class FakeDataset:
        def __init__(self, data, label, **kwargs):
            self.data = np.array(data, copy=True)
            self.label = np.array(label, copy=True)

    class FakeModel:
        best_iteration = 37

        def predict(self, values, num_iteration):
            assert num_iteration == 37
            return np.array(values)[:, 0] * 0.01

    def fake_early_stopping(rounds, verbose):
        assert rounds == v2.V2_EARLY_STOPPING
        def callback(environment):
            callback_calls.append(environment.evaluation_result_list)
            assert all(result[0] == "validation" for result in environment.evaluation_result_list)
        return callback

    def fake_train(params, train_set, *, num_boost_round, valid_sets, valid_names, callbacks):
        assert valid_names == ["validation"]
        assert len(valid_sets) == 1
        assert np.unique(train_set.label).tolist() == [1.0]
        assert np.unique(valid_sets[0].label).tolist() == [2.0]
        calls.append((train_set.label.copy(), valid_sets[0].label.copy()))
        for callback in callbacks:
            callback(SimpleNamespace(evaluation_result_list=[("validation", "rmse", 0.1, False)]))
        return FakeModel()

    monkeypatch.setattr(v2.lgb, "Dataset", FakeDataset)
    monkeypatch.setattr(v2.lgb, "train", fake_train)
    monkeypatch.setattr(v2.lgb, "early_stopping", fake_early_stopping)
    _, before_rounds, before_pred = v2._fit_fold(fold, ["feature"], {}, "target_value")
    fold["test"]["target_value"] = -12345.0
    _, after_rounds, after_pred = v2._fit_fold(fold, ["feature"], {}, "target_value")
    assert before_rounds == after_rounds == 37
    np.testing.assert_array_equal(before_pred, after_pred)
    for before, after in zip(calls[0], calls[1]):
        np.testing.assert_array_equal(before, after)
    assert len(callback_calls) == 2


def test_final_rounds_and_month_end_cohort_cannot_use_test_performance():
    assert v2._final_num_rounds([37, 49, 65]) == 49
    with pytest.raises(ValueError):
        v2._final_num_rounds([])
    with pytest.raises(ValueError):
        v2._final_num_rounds([0])
    table = pd.DataFrame({"ticker": ["2330", "2330", "9999"],
                          "Date": pd.to_datetime(["2024-01-30", "2024-01-31", "2024-01-30"]),
                          "pred_return": [1, 2, 999]})
    cohort = v2._month_end_cohort(table)
    assert cohort["ticker"].tolist() == ["2330"]
    assert cohort["Date"].nunique() == 1


def test_temporal_frame_rejects_ambiguous_rows():
    prepared, _ = frame()
    with pytest.raises(ValueError, match="ticker/date"):
        v2._prepare_temporal_frame(pd.concat([prepared, prepared.iloc[:1]]))
