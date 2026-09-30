"""DuckDB 連線管理 — 用 cursor() 支援多執行緒並發讀取.

排程 ingest 前須先呼叫 /api/db/release 釋放連線，完成後呼叫 /api/db/reconnect。
"""

import os
import shutil
import tempfile
import threading
import time
import duckdb
from backend.config import DUCKDB_PATH

_conn: duckdb.DuckDBPyConnection | None = None
_conn_read_only: bool | None = None
_snapshot_conn: duckdb.DuckDBPyConnection | None = None
_snapshot_signature: tuple[int, int] | None = None
_snapshot_path = os.path.join(tempfile.gettempdir(), "stock_duckdb_frontend_snapshot.duckdb")
_lock_backoff_until = 0.0
_LOCK_BACKOFF_SECONDS = 5.0
_lock = threading.Lock()


class DatabaseUnavailableError(RuntimeError):
    """The database is temporarily unavailable; callers must not treat it as no data."""

    def __init__(self, message: str = "DuckDB is temporarily unavailable"):
        super().__init__(message)
        self.retry_after_seconds = int(_LOCK_BACKOFF_SECONDS)


def _db_signature() -> tuple[int, int]:
    stat = os.stat(DUCKDB_PATH)
    return stat.st_mtime_ns, stat.st_size


def _open_snapshot_conn() -> duckdb.DuckDBPyConnection:
    """Open a read-only temp snapshot when the main DuckDB file is locked."""
    global _snapshot_conn, _snapshot_signature

    signature = _db_signature()
    if _snapshot_conn is not None and _snapshot_signature == signature:
        return _snapshot_conn

    if _snapshot_conn is not None:
        _snapshot_conn.close()
        _snapshot_conn = None

    shutil.copy2(DUCKDB_PATH, _snapshot_path)
    _snapshot_conn = duckdb.connect(_snapshot_path, read_only=True)
    _snapshot_conn.execute("SET threads = 4")
    _snapshot_signature = signature
    return _snapshot_conn


def _is_lock_error(exc: Exception) -> bool:
    message = str(exc)
    return (
        "WinError 32" in message
        or "another process" in message
        or "正由另一個程序使用" in message
        or "程序無法存取檔案" in message
    )


def _mark_lock_backoff() -> None:
    global _lock_backoff_until
    _lock_backoff_until = time.monotonic() + _LOCK_BACKOFF_SECONDS


def _in_lock_backoff() -> bool:
    return time.monotonic() < _lock_backoff_until


def get_conn(read_only: bool = False) -> duckdb.DuckDBPyConnection:
    """取得 DuckDB 連線。

    一般 API 查詢可使用 read-only 連線，避免其他 pipeline/分析行程持有
    DuckDB write lock 時讓前端頁面整批 500；ingest/建表仍使用預設寫入連線。
    """
    global _conn, _conn_read_only
    with _lock:
        needs_reconnect = _conn is None or (not read_only and _conn_read_only is True)
        if needs_reconnect:
            if _conn is not None:
                _conn.close()
            try:
                _conn = duckdb.connect(DUCKDB_PATH, read_only=read_only)
                _conn_read_only = read_only
                _conn.execute("SET threads = 4")
            except duckdb.IOException:
                _conn = None
                _conn_read_only = None
                if read_only:
                    try:
                        return _open_snapshot_conn()
                    except (duckdb.IOException, PermissionError) as snapshot_exc:
                        if _is_lock_error(snapshot_exc):
                            _mark_lock_backoff()
                        raise
                raise
        return _conn


def close_conn():
    global _conn, _conn_read_only, _snapshot_conn, _snapshot_signature
    with _lock:
        if _conn is not None:
            _conn.close()
            _conn = None
            _conn_read_only = None
        if _snapshot_conn is not None:
            _snapshot_conn.close()
            _snapshot_conn = None
            _snapshot_signature = None


def reconnect():
    """關閉現有連線並重新建立 (ingest 後重新讀取新資料)."""
    close_conn()
    get_conn()


def query_df(sql: str, params: list | None = None):
    """執行查詢並回傳 pandas DataFrame (thread-safe).

    每次查詢建立一個 cursor，用完即關，避免並發衝突。
    """
    if _in_lock_backoff():
        raise DatabaseUnavailableError("DuckDB lock backoff is active")

    try:
        conn = get_conn(read_only=True)
        cursor = conn.cursor()
    except (duckdb.IOException, PermissionError) as exc:
        if _is_lock_error(exc):
            _mark_lock_backoff()
            raise DatabaseUnavailableError() from exc
        raise
    try:
        if params:
            return cursor.execute(sql, params).fetchdf()
        return cursor.execute(sql).fetchdf()
    except (duckdb.IOException, PermissionError) as exc:
        if _is_lock_error(exc):
            _mark_lock_backoff()
            raise DatabaseUnavailableError() from exc
        raise
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
