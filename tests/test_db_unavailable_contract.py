import duckdb
import pytest

from backend.db import engine


def test_query_df_raises_during_lock_backoff(monkeypatch):
    monkeypatch.setattr(engine, "_in_lock_backoff", lambda: True)

    with pytest.raises(engine.DatabaseUnavailableError) as exc_info:
        engine.query_df("SELECT 1")

    assert exc_info.value.retry_after_seconds == int(engine._LOCK_BACKOFF_SECONDS)


def test_query_df_converts_lock_error_to_explicit_unavailable(monkeypatch):
    monkeypatch.setattr(engine, "_in_lock_backoff", lambda: False)
    monkeypatch.setattr(
        engine,
        "get_conn",
        lambda read_only=True: (_ for _ in ()).throw(
            duckdb.IOException("Could not set lock on file: another process")
        ),
    )
    marked = []
    monkeypatch.setattr(engine, "_mark_lock_backoff", lambda: marked.append(True))

    with pytest.raises(engine.DatabaseUnavailableError):
        engine.query_df("SELECT 1")

    assert marked == [True]


def test_non_lock_database_error_is_not_relabelled(monkeypatch):
    monkeypatch.setattr(engine, "_in_lock_backoff", lambda: False)
    monkeypatch.setattr(
        engine,
        "get_conn",
        lambda read_only=True: (_ for _ in ()).throw(duckdb.IOException("corrupt database")),
    )

    with pytest.raises(duckdb.IOException, match="corrupt database"):
        engine.query_df("SELECT 1")
