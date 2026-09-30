"""Exercise the real health reader without starting/importing the full Web app."""

import ast
from datetime import datetime
import os
from pathlib import Path
import re

import duckdb
import pytest

import backend.config as backend_config
import backend.db.engine as engine
import ml.config as ml_config


def _load_health_reader():
    # Compiling these exact source functions avoids app import side effects
    # (credentials, Web logging, all routers), while running the real DB logic.
    source_path = Path(__file__).resolve().parents[1] / "app.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    names = {"_build_daily_production_health", "_check_status", "_file_info"}
    selected = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    assert {node.name for node in selected} == names
    namespace = {
        "os": os,
        "datetime": datetime,
        "_previous_trading_day": lambda: "2026-09-04",
        "_expected_prediction_trading_day": lambda: "2026-09-04",
        "_latest_artifact": lambda *_: {"exists": True, "date": "2026-09-04", "rows": 1},
        "_latest_phase_result": lambda *_: {"all_ok": True},
        "PRODUCTION_PREDICTION_RE": re.compile(r"predictions_.*"),
        "UNIFIED_SIGNALS_RE": re.compile(r"unified_signals_.*"),
    }
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(source_path), "exec"), namespace)
    return namespace["_build_daily_production_health"]


@pytest.fixture
def health_db(tmp_path, monkeypatch):
    db_path = tmp_path / "stock.duckdb"
    connection = duckdb.connect(str(db_path))
    connection.execute("CREATE TABLE daily_k (Date DATE, Ticker VARCHAR, Close DOUBLE, Volume DOUBLE)")
    connection.execute("""
        INSERT INTO daily_k
        SELECT DATE '2026-09-04', CAST(i AS VARCHAR), 100.0, 1000000.0
        FROM range(1000, 2000) AS tickers(i)
    """)
    connection.execute("CREATE TABLE stock_list AS SELECT Ticker, Date AS Last_Date FROM daily_k")
    connection.execute("CREATE TABLE ingest_meta (table_name VARCHAR, rows BIGINT, generation_id VARCHAR)")
    connection.execute("INSERT INTO ingest_meta VALUES ('daily_k', 1000, 'same')")
    for name in ('revenue', 'financials', 'tdcc', 'indices'):
        connection.execute(f"CREATE TABLE {name} AS SELECT 1 AS value")
        connection.execute("INSERT INTO ingest_meta VALUES (?, 1, 'same')", [name])
    monkeypatch.setattr(backend_config, "DUCKDB_PATH", str(db_path))
    monkeypatch.setattr(backend_config, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(ml_config, "MODEL_DIR", str(tmp_path))
    monkeypatch.setattr(engine, "DUCKDB_PATH", str(db_path))
    monkeypatch.setattr(engine, "_conn", connection)
    monkeypatch.setattr(engine, "_conn_read_only", False)
    monkeypatch.setattr(engine, "_snapshot_conn", None)
    monkeypatch.setattr(engine, "_snapshot_signature", None)
    try:
        yield db_path, connection, _load_health_reader()
    finally:
        connection.close()


def test_health_reuses_cursor_when_engine_has_readwrite_connection(health_db):
    db_path, connection, read_health = health_db
    # Reproduce the actual old endpoint failure with real DuckDB handles.
    with pytest.raises(duckdb.ConnectionException, match="different configuration"):
        duckdb.connect(str(db_path), read_only=True)

    health = read_health()

    assert health["status"] == "ok"
    assert health["duckdb"]["daily_k_latest_date"] == "2026-09-04"
    assert health["duckdb"]["daily_k_tickers_latest"] == 1000
    assert "error" not in health["duckdb"]
    assert engine.get_conn(read_only=True) is connection
    assert connection.execute("SELECT COUNT(*) FROM daily_k").fetchone()[0] == 1000


def test_health_still_fails_for_actually_stale_market_data(health_db):
    _, connection, read_health = health_db
    connection.execute("UPDATE daily_k SET Date = DATE '2026-09-03'")
    connection.execute("UPDATE stock_list SET Last_Date = DATE '2026-09-03'")

    health = read_health()

    assert health["status"] == "fail"
    assert health["ok"] is False
    assert health["stale"] is True
    checks = {check["name"]: check["status"] for check in health["checks"]}
    assert checks["daily_k_fresh"] == "error"
    assert checks["daily_k_snapshot_size"] == "ok"
    assert health["duckdb"]["daily_k_latest_date"] == "2026-09-03"


def test_health_query_failure_does_not_close_shared_connection(health_db):
    _, connection, read_health = health_db
    connection.execute("DROP TABLE stock_list")

    health = read_health()

    assert health["status"] == "fail"
    assert "stock_list" in health["duckdb"]["error"]
    assert engine.get_conn(read_only=True) is connection
    assert connection.execute("SELECT COUNT(*) FROM daily_k").fetchone()[0] == 1000


@pytest.mark.parametrize('corruption', ['generation', 'row_count', 'duplicate', 'missing', 'stale'])
def test_health_detects_cross_layer_inconsistency(health_db, corruption):
    _, connection, read_health = health_db
    statements = {
        'generation': "UPDATE ingest_meta SET generation_id = 'old' WHERE table_name = 'tdcc'",
        'row_count': "UPDATE ingest_meta SET rows = 1 WHERE table_name = 'daily_k'",
        'duplicate': "INSERT INTO stock_list SELECT * FROM stock_list WHERE Ticker = '1000'",
        'missing': "DELETE FROM stock_list WHERE Ticker = '1000'",
        'stale': "UPDATE stock_list SET Last_Date = DATE '2026-09-03' WHERE Ticker = '1000'",
    }
    connection.execute(statements[corruption])
    result = read_health()
    assert result['status'] == 'fail'
    assert next(c for c in result['checks'] if c['name'] == 'ingest_layer_consistency')['status'] == 'error'
