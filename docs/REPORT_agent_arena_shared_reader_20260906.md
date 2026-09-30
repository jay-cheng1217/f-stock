# Agent Arena same-process DuckDB reader repair

## Method lock before implementation

Scope: database connection ownership only, in `scripts/agent_arena.py`, focused regression tests and this report. No Agent Arena rules, training, production models, selection thresholds, paper ledgers, scheduling, global connection shutdown or live weekly pipeline execution.

Current behavior: `_connect_duckdb_readonly()` opens a new `duckdb.connect(..., read_only=True)` even when `daily_pipeline.run_ingest()` has left the shared backend engine connected read/write to the same file. A configuration conflict enters the file-copy fallback; on Windows the open source cannot be copied, producing a misleading web-process-lock error.

Candidate: only when the requested resolved path equals `backend.db.engine.DUCKDB_PATH`, return `engine.get_conn(read_only=True).cursor()` and no owned snapshot. Closing this returned cursor must leave the shared connection usable. Different/custom paths keep the existing direct-connect and snapshot fallback behavior. Queries and feature calculations remain identical.

Acceptance: actual temporary DuckDB regression must reproduce the read/write versus read-only conflict, then show both Agent Arena loader branches returning frames without closing the shared parent; query failure must also leave the parent usable. Custom-path success and fallback semantics must remain covered. A bounded leaf run against frozen real isolated data must obtain frames and confirm the parent stays usable; no competition, training or ledger operation may execute.

## Evidence before repair

- Actual temporary DuckDB plus a real, independently loaded backend shared engine reproduced this exception chain: `ConnectionException` (different configuration than existing connections), then `PermissionError` / WinError 32 from `shutil.copy2`, then the outer `RuntimeError` incorrectly naming the web process. The temporary parent connection remained usable.
- `logs/pipeline.log` records the outer error at **2026-09-05 12:14:26**; `logs/retrain_20260905.log` contains the same outer message. Those historical logs do not retain the first exception or traceback. The historical same-process explanation is strongly supported by the code path and actual reproduction, not directly proven by a historical exception trace.

## Validation

- Correctly initialized regression fixtures before repair: **3 failed, 3 passed**. The configured reader and actual feature loader reproduced the mismatch/copy failure; custom-path behavior already passed.
- After repair: `python -m pytest tests/test_agent_arena_shared_reader.py tests/test_agent_arena.py -q --disable-warnings --tb=short` → **26 passed in 0.97s**. This includes six focused tests and twenty existing Agent Arena tests. Both real temporary-data feature loaders returned 80 rows, valid 20-day returns, and left the shared parent usable; a query error closed only its reader. Custom direct reads and snapshot cleanup remained intact.
- Real-data leaf: `python scripts/pipeline_acceptance.py run --label arena_reader_repaired --timeout 600 -- scripts/acceptance_arena_reader_leaf.py` completed **PASS**, exit 0, **83.35 seconds**, at **2026-09-06 13:41:58 +08:00**. The repaired arena module was physically copied into the isolated workspace first; the real functions were not mocked.
- All **13 semantic checks passed**. The ticker loader returned **250 rows**, tickers **2330/8046**, dates **2026-03-09 through 2026-09-04**. The main feature loader returned **1,911 rows/tickers on 2026-09-04**, matching the isolated DB target-date count with no duplicate keys. The main-loader slice is intentionally one date for bounded database-access acceptance; it is not a full-history feature/backtest validation.
- The child held the isolated backend connection read/write to reproduce the post-ingest ownership condition. Each reader/cursor was closed and the same parent still answered `SELECT 42`; both loader branches completed. The child's `engine.close_conn()` ran after the reads and released the isolated DB for subsequent stages.
- Database SHA-256 and mtime were unchanged before/after: `f2f506d925950f16c3540ec0bc250dfe29d4de10ce3bd6f30cb8c31bece30eda`. Python audit guard PID **32148** reported **zero violations**; the guard is not an OS sandbox and does not cover all native extension I/O.
- Archived runner/semantic receipts, complete ticker sets, frame hashes and exact leaf-script hash: [arena_reader_repaired.json](../ml/reports/research/pipeline_acceptance_20260906/arena_reader_repaired.json). Original runtime receipt: `output/pipeline_acceptance_20260906/arena_reader_repaired.json`; semantic receipt: `workspace/logs/acceptance_arena_reader_repaired.json` under that run directory.
- `git diff --check` passed. Root owns integration/commit. No real training, Agent Arena competition, paper-book mutation, notification or service restart ran during this repair.

## Limits

The configured-path branch adopts the existing backend engine connection/fallback contract. This repair does not independently prove cross-process snapshot consistency or resolve true external lock failures. A shared read/write parent's cursor remains technically write-capable; Agent Arena uses it only for existing SELECT queries. Full weekly retraining and full arena competition remain unexecuted. Model `prob_edge` A/B is not applicable because no prediction or feature formula changes.
