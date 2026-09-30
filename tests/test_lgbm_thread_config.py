from ml.config import (
    DEFAULT_LGBM_NUM_THREADS,
    LGBM_NUM_THREADS,
    coerce_lgbm_num_threads,
    get_lgbm_num_threads,
)
from scripts.train_v2 import V2_PARAMS


def test_lgbm_thread_default_is_finite(monkeypatch):
    monkeypatch.delenv("STOCK_LGBM_THREADS", raising=False)
    monkeypatch.delenv("LGBM_NUM_THREADS", raising=False)

    assert get_lgbm_num_threads() == DEFAULT_LGBM_NUM_THREADS
    assert DEFAULT_LGBM_NUM_THREADS > 0


def test_lgbm_thread_env_override(monkeypatch):
    monkeypatch.setenv("STOCK_LGBM_THREADS", "6")

    assert get_lgbm_num_threads() == 6


def test_lgbm_thread_invalid_env_falls_back(monkeypatch):
    monkeypatch.setenv("STOCK_LGBM_THREADS", "-1")

    assert get_lgbm_num_threads() == DEFAULT_LGBM_NUM_THREADS
    assert coerce_lgbm_num_threads("not-an-int", default=4) == 4


def test_v2_params_use_finite_thread_cap():
    assert V2_PARAMS["n_jobs"] == LGBM_NUM_THREADS
    assert V2_PARAMS["n_jobs"] > 0
