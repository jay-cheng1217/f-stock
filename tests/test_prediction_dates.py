"""2026-10-10/12 事故回歸測試:預測批次的資料日不可取排序後的第一列。"""
import sqlite3

import pandas as pd

from ml.prediction_dates import canonical_prediction_date, stale_prediction_rows


def _frame_with_stale_first_row():
    # 重現 10/12:排第一的 6173 最後一根K棒停在 10/06,其餘都是 10/08
    rows = [{"ticker": "6173", "date": "2026-10-06", "close": 284.0, "up_prob": 0.38}]
    rows += [{"ticker": f"{2000 + i}", "date": "2026-10-08", "close": 50.0 + i, "up_prob": 0.30} for i in range(5)]
    rows += [{"ticker": "5371", "date": "2026-08-21", "close": 10.0, "up_prob": 0.10}]
    return pd.DataFrame(rows)


def test_canonical_date_is_majority_not_first_row():
    df = _frame_with_stale_first_row()
    assert str(df["date"].iloc[0]) == "2026-10-06"
    assert canonical_prediction_date(df) == "2026-10-08"


def test_canonical_date_tie_takes_later_day_and_ignores_single_outlier():
    tie = pd.DataFrame({"date": ["2026-10-07", "2026-10-08"]})
    assert canonical_prediction_date(tie) == "2026-10-08"
    outlier = pd.DataFrame({"date": ["2026-10-08"] * 5 + ["2026-10-11"]})   # 單筆非交易日假棒不可決定整批日期
    assert canonical_prediction_date(outlier) == "2026-10-08"


def test_canonical_date_missing_inputs_return_none():
    assert canonical_prediction_date(None) is None
    assert canonical_prediction_date(pd.DataFrame()) is None
    assert canonical_prediction_date(pd.DataFrame({"ticker": ["2330"]})) is None
    assert canonical_prediction_date(pd.DataFrame({"date": ["not-a-date", None]})) is None


def test_stale_rows_are_listed_for_the_warning():
    df = _frame_with_stale_first_row()
    assert stale_prediction_rows(df, "2026-10-08") == ["6173@2026-10-06", "5371@2026-08-21"]
    assert stale_prediction_rows(df, None) == []


def test_run_prediction_names_file_by_canonical_date(monkeypatch):
    import ml.predict as predict
    import ml.prediction_provenance as provenance

    df = _frame_with_stale_first_row()
    published = {}
    monkeypatch.setattr(predict, "predict_all", lambda **kw: (df.copy(), {
        "_prediction_provenance": {"stub": True}, "model_file": "lgbm_stub.txt", "model_slot": "production"}))
    monkeypatch.setattr(provenance, "publish_prediction_csv",
                        lambda frame, path, run: published.setdefault("path", str(path)))
    predict.run_prediction(top_n=3)
    assert published["path"].replace("\\", "/").endswith("predictions_2026-10-08.csv")


def test_paper_portfolio_locks_run_under_canonical_date(monkeypatch, tmp_path):
    import scripts.update_paper_portfolio as paper

    df = _frame_with_stale_first_row()
    source = tmp_path / "predictions_stub.csv"
    df.to_csv(source, index=False)
    monkeypatch.setattr(paper, "_load_prediction_file", lambda path: df.copy())
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    paper.ensure_schema(conn)
    _, prediction_date, created = paper.lock_prediction_run(conn, str(source), top_n=2,
                                                            rule_version="test", stats=paper.SyncStats())
    assert prediction_date == "2026-10-08" and created
    assert conn.execute("SELECT prediction_date FROM portfolio_runs").fetchone()[0] == "2026-10-08"
