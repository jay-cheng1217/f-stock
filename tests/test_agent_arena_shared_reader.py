"""Real temporary DuckDB regressions for Agent Arena connection ownership."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import duckdb
import pandas as pd
import pytest

from backend import db as backend_db
from scripts import agent_arena as arena


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def shared_engine(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location(
        "arena_test_shared_engine", ROOT / "backend" / "db" / "engine.py",
    )
    engine = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(engine)
    engine.DUCKDB_PATH = str(tmp_path / "configured.duckdb")
    engine._snapshot_path = str(tmp_path / "engine_snapshot.duckdb")
    monkeypatch.setitem(sys.modules, "backend.db.engine", engine)
    monkeypatch.setattr(backend_db, "engine", engine, raising=False)
    yield engine
    # This engine instance owns only fixture connections, never production state.
    engine.close_conn()


def _populate_daily_k(parent):
    frame = pd.DataFrame({
        "Ticker": ["2330"] * 80,
        "Date": pd.bdate_range("2026-05-18", periods=80),
        "Open": [100.0 + i for i in range(80)],
        "High": [102.0 + i for i in range(80)],
        "Low": [99.0 + i for i in range(80)],
        "Close": [101.0 + i for i in range(80)],
        "Volume": [1_000_000.0] * 80,
    })
    for name in ("Foreign_BuySell", "Trust_BuySell", "Dealer_BuySell", "MA_5", "MA_20", "MA_60",
                 "RSI_14", "BBU_20_2.0", "BBL_20_2.0", "MACD_12_26_9", "MACDs_12_26_9",
                 "MACDh_12_26_9", "K", "D", "ATR_14", "VOL_MA_20"):
        frame[name] = 100.0
    parent.register("fixture_bars", frame)
    parent.execute("CREATE TABLE daily_k AS SELECT * FROM fixture_bars")
    parent.unregister("fixture_bars")


def test_real_duckdb_rejects_second_connection_with_different_access_mode(shared_engine):
    parent = shared_engine.get_conn()
    parent.execute("CREATE TABLE marker AS SELECT 42 AS value")
    with pytest.raises(duckdb.ConnectionException, match="different configuration"):
        duckdb.connect(shared_engine.DUCKDB_PATH, read_only=True)
    assert parent.execute("SELECT value FROM marker").fetchone() == (42,)


def test_configured_reader_returns_owned_cursor_and_keeps_rw_parent_open(shared_engine, monkeypatch):
    parent = shared_engine.get_conn()
    parent.execute("CREATE TABLE marker AS SELECT 42 AS value")
    monkeypatch.setattr(arena.shutil, "copy2", lambda *a, **k: pytest.fail("same-process read must not copy"))
    cursor, snapshot = arena._connect_duckdb_readonly(Path(shared_engine.DUCKDB_PATH))
    assert cursor is not parent
    assert snapshot is None
    assert cursor.execute("SELECT value FROM marker").fetchone() == (42,)
    arena._close_duckdb_readonly(cursor, snapshot)
    assert shared_engine.get_conn() is parent
    assert parent.execute("SELECT value FROM marker").fetchone() == (42,)


def test_both_real_feature_loaders_keep_shared_parent_open(shared_engine, monkeypatch, tmp_path):
    parent = shared_engine.get_conn()
    _populate_daily_k(parent)
    # Synthetic bars have no TDCC source; retain the real missing-source behavior.
    monkeypatch.setattr(arena._attach_tdcc_features, "__kwdefaults__", {
        "tdcc_summary_path": tmp_path / "no_tdcc_fixture.csv",
    })
    path = Path(shared_engine.DUCKDB_PATH)
    all_rows = arena._load_feature_frame(duckdb_path=path, start_date="2026-01-01")
    ticker_rows = arena._load_ticker_feature_frame({"2330"}, duckdb_path=path, lookback_days=180)
    assert len(all_rows) == len(ticker_rows) == 80
    assert all_rows["close_px"].equals(ticker_rows["close_px"])
    assert all_rows["ret_20"].notna().sum() == 60
    assert parent.execute("SELECT count(*) FROM daily_k").fetchone() == (80,)
    assert shared_engine.get_conn() is parent


def test_query_failure_closes_only_reader_cursor(shared_engine):
    parent = shared_engine.get_conn()
    with pytest.raises(duckdb.CatalogException, match="daily_k"):
        arena._load_feature_frame(duckdb_path=Path(shared_engine.DUCKDB_PATH))
    assert parent.execute("SELECT 42").fetchone() == (42,)


def test_custom_path_does_not_redirect_to_configured_engine(shared_engine, tmp_path):
    parent = shared_engine.get_conn()
    parent.execute("CREATE TABLE marker AS SELECT 42 AS value")
    custom = tmp_path / "custom.duckdb"
    with duckdb.connect(str(custom)) as fixture:
        fixture.execute("CREATE TABLE marker AS SELECT 99 AS value")
    cursor, snapshot = arena._connect_duckdb_readonly(custom)
    assert snapshot is None
    assert cursor.execute("SELECT value FROM marker").fetchone() == (99,)
    arena._close_duckdb_readonly(cursor, snapshot)
    assert parent.execute("SELECT value FROM marker").fetchone() == (42,)


def test_custom_path_keeps_snapshot_fallback_and_cleanup(shared_engine, monkeypatch, tmp_path):
    custom = tmp_path / "custom.duckdb"
    with duckdb.connect(str(custom)) as fixture:
        fixture.execute("CREATE TABLE marker AS SELECT 99 AS value")
    original_connect = duckdb.connect
    calls = []

    def locked_custom(path, **kwargs):
        calls.append(str(path))
        if Path(path) == custom:
            raise duckdb.IOException("fixture file is locked by another process")
        return original_connect(path, **kwargs)

    monkeypatch.setattr(duckdb, "connect", locked_custom)
    monkeypatch.setattr(arena.tempfile, "gettempdir", lambda: str(tmp_path))
    cursor, snapshot = arena._connect_duckdb_readonly(custom)
    assert snapshot is not None and snapshot.exists()
    assert cursor.execute("SELECT value FROM marker").fetchone() == (99,)
    arena._close_duckdb_readonly(cursor, snapshot)
    assert not snapshot.exists()
    assert len(calls) == 2
