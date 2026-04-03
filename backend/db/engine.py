"""DuckDB 連線管理 — 用 cursor() 支援多執行緒並發讀取.

排程 ingest 前須先呼叫 /api/db/release 釋放連線，完成後呼叫 /api/db/reconnect。
"""

import threading
import duckdb
from backend.config import DUCKDB_PATH

_conn: duckdb.DuckDBPyConnection | None = None
_lock = threading.Lock()


def get_conn() -> duckdb.DuckDBPyConnection:
    """取得主連線 (用於 ingest、建表等單執行緒操作)."""
    global _conn
    with _lock:
        if _conn is None:
            _conn = duckdb.connect(DUCKDB_PATH, read_only=False)
            _conn.execute("SET threads = 4")
        return _conn


def close_conn():
    global _conn
    with _lock:
        if _conn is not None:
            _conn.close()
            _conn = None


def reconnect():
    """關閉現有連線並重新建立 (ingest 後重新讀取新資料)."""
    close_conn()
    get_conn()


def query_df(sql: str, params: list | None = None):
    """執行查詢並回傳 pandas DataFrame (thread-safe).

    每次查詢建立一個 cursor，用完即關，避免並發衝突。
    """
    conn = get_conn()
    cursor = conn.cursor()
    try:
        if params:
            return cursor.execute(sql, params).fetchdf()
        return cursor.execute(sql).fetchdf()
    finally:
        cursor.close()


def execute(sql: str, params: list | None = None):
    """執行寫入 SQL."""
    conn = get_conn()
    if params:
        conn.execute(sql, params)
    else:
        conn.execute(sql)
    conn.commit()
