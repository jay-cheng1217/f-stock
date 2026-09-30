# Daily pipeline failure exit status

## Method lock before implementation

Scope: production-adjacent pipeline status repair. Only `scripts/daily_pipeline.py`, focused fixture tests and this report may change. No training, scheduler changes, model selection, model artifacts, ledgers or actual pipeline execution.

Observed defect: `run_step()` converts a step exception to `False`; `main()` writes `pipeline_status.json` with `all_ok=false`, but then returns `None`. Its CLI calls `main()` without propagating a failure status. The weekly wrapper and batch file therefore receive success despite recorded step failure.

Sole behavioral change: after the existing complete step sequence and metadata write, return exit code 1 if `all_ok` is false, otherwise 0. The CLI propagates that integer. Existing mutual-exclusion and nontrading-day skips remain exit code 0. Existing exception propagation and `finally` lock release remain intact. Step order, training count, optional skips, metadata schema and continuation after an individual step failure remain unchanged.

Acceptance criteria: fixture execution of the real orchestration and `run_step()` must show failure code 1 with complete metadata and lock release, success/skips code 0, metadata-write exception still releasing the lock, and the CLI forwarding the result. Fixture execution of the unmodified weekly wrapper must show one pipeline invocation and web reconnection/restoration for nonzero status.

## Retry and cleanup review

- `run_weekly_retrain_guard.py::_run_daily_pipeline()` has one `subprocess.run(..., check=False)` and returns its code. There is no pipeline retry loop.
- The wrapper's `finally` reconnects an API-released web connection or restores a stopped web/tunnel before returning the pipeline code. Changing a returned integer does not bypass this block.
- `scripts/run_weekly_retrain.bat` uses `exit /b %errorlevel%` after the wrapper.
- Read-only Windows task inspection on 2026-09-06: both `TW_Stock_Weekly_Retrain` and `TW_Stock_Weekly_Retrain_Backup` have `RestartCount=0`, no `RestartInterval`, and `MultipleInstances=IgnoreNew`. The independent backup trigger already exists; this patch adds no trigger or retry.

## Validation

Command: `python -m pytest tests/test_daily_pipeline_exit_status.py -q --disable-warnings`.

- Before the repair: **7 failed, 3 passed**. The failures reproduced `main()` returning `None` for failure/success/skips and the CLI failing to propagate either return code.
- After the repair: **10 passed in 0.14s**. The AgentArena failure fixture produced complete metadata with only that step failed, ran the remaining work-unit fixtures through email, returned 1, and released the lock exactly once. Success and both skip cases returned 0. A metadata-write exception still released the lock. The training-failure fixture invoked its stub only once.
- Both weekly-wrapper paths returned the child failure code after exactly one child-process fixture: API release followed by reconnection, and stopped service followed by restoration. The actual CLI entry block was executed with fixture return values 0 and 1 and raised the matching `SystemExit`.
- All job bodies/process spawns/service operations were fixture replacements; the real orchestration, step exception handling, metadata writer, weekly-wrapper sequencing and CLI adapter were exercised. Logs and metadata created by these tests stayed under pytest temporary directories, with a private logger. No real fetch, training, prediction, email, ledger or service action occurred.
- `git diff --check` passed. Only `scripts/daily_pipeline.py`, `tests/test_daily_pipeline_exit_status.py` and this report were changed for this repair; root owns integration/commit.

This report does not claim a live weekly retrain or repaired scheduler run. The older 9/5 Windows task result is historical and will not be rewritten by this repair.

## Limits

This repair propagates failures already represented by `step_status`. Several pre-existing fetch wrappers discard `run_job(..., raise_on_fail=False)` result dictionaries; failures swallowed there are a separate upstream status-contract gap and remain outside this minimal patch. The wrapper also has pre-pipeline setup outside its pipeline `try/finally`; this patch does not change that existing boundary. No model or feature calculation changes, so model `prob_edge` A/B is not applicable.
