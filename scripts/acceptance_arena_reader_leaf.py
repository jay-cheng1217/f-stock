"""Isolated real-data reader leaf; never run Agent Arena competition or training."""
from pathlib import Path
from datetime import datetime
import hashlib
import json
import os

import pandas as pd

from backend.db import engine
from scripts import agent_arena as arena
from scripts import pipeline_acceptance_guard as guard

root = Path(os.environ["STOCK_ACCEPTANCE_ROOT"]).resolve()
db_path = Path(engine.DUCKDB_PATH).resolve()
assert db_path.is_relative_to(root)
assert Path(arena.__file__).resolve().is_relative_to(root)
assert guard._INSTALLED is not None


def file_state(path):
    stat = path.stat()
    with path.open("rb") as handle:
        sha = hashlib.file_digest(handle, "sha256").hexdigest()
    return {"path": str(path), "bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns, "sha256": sha}


def frame_state(frame):
    ordered = frame.sort_values(["ticker", "dt"]).reset_index(drop=True)
    return {
        "rows": len(ordered), "tickers": sorted(ordered["ticker"].unique().tolist()),
        "min_date": str(ordered["dt"].min().date()), "max_date": str(ordered["dt"].max().date()),
        "columns": ordered.columns.tolist(),
        "frame_sha256": hashlib.sha256(pd.util.hash_pandas_object(ordered, index=False).values.tobytes()).hexdigest(),
    }


before = file_state(db_path)
checks = {}
parent = engine.get_conn()
try:
    assert engine._conn_read_only is False
    cursor, snapshot = arena._connect_duckdb_readonly(db_path)
    assert cursor is not parent and snapshot is None
    checks["owned_cursor_select"] = cursor.execute("SELECT 42").fetchone() == (42,)
    arena._close_duckdb_readonly(cursor, snapshot)
    checks["parent_usable_after_reader_close"] = parent.execute("SELECT 42").fetchone() == (42,)

    ticker_frame = arena._load_ticker_feature_frame({"2330", "8046"}, duckdb_path=db_path, lookback_days=180)
    checks["parent_usable_after_ticker_loader"] = parent.execute("SELECT 42").fetchone() == (42,)
    all_frame = arena._load_feature_frame(duckdb_path=db_path, start_date="2026-09-04", end_date="2026-09-04")
    checks["parent_usable_after_full_loader"] = parent.execute("SELECT 42").fetchone() == (42,)
    checks["same_shared_parent_retained"] = engine.get_conn() is parent
    checks["ticker_set_exact"] = set(ticker_frame["ticker"]) == {"2330", "8046"}
    checks["ticker_date_current"] = str(ticker_frame["dt"].max().date()) == "2026-09-04"
    checks["full_frame_date_current"] = set(all_frame["dt"].dt.strftime("%Y-%m-%d")) == {"2026-09-04"}
    expected_count = parent.execute("SELECT count(*) FROM daily_k WHERE Date=DATE '2026-09-04'").fetchone()[0]
    checks["full_frame_matches_db_count"] = len(all_frame) == expected_count == 1911
    checks["no_duplicate_keys"] = not all_frame.duplicated(["ticker", "dt"]).any()
finally:
    # This child process owns the isolated connection; release it for later stages.
    engine.close_conn()

after = file_state(db_path)
checks["database_hash_and_mtime_unchanged"] = before == after
checks["guard_zero_violations"] = not guard._INSTALLED.violations
checks["child_engine_closed"] = engine._conn is None and engine._snapshot_conn is None
checks = {k: bool(v) for k, v in checks.items()}
result = {
    "verified_at": datetime.now().astimezone().isoformat(), "status": "PASS" if all(checks.values()) else "FAIL",
    "scope": "Real isolated database readers only; no training, competition, ledger or service action.",
    "checks": checks, "ticker_frame": frame_state(ticker_frame), "full_date_frame": frame_state(all_frame),
    "database_before": before, "database_after": after,
    "arena_code": file_state(Path(arena.__file__)), "guard_pid": os.getpid(),
    "guard_violations": guard._INSTALLED.violations,
}
(root / "logs" / "acceptance_arena_reader_repaired.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
print(json.dumps({"status": result["status"], "checks": checks, "ticker_rows": len(ticker_frame),
                  "full_date_rows": len(all_frame), "guard_pid": os.getpid()}))
assert all(checks.values())
