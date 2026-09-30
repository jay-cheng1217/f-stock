"""Exit-status fixtures: no real jobs, services, training or repository writes."""
from __future__ import annotations

import ast
import importlib.util
import json
import logging
from pathlib import Path
from types import SimpleNamespace
import sys
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _load_script(filename, name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def pipeline(monkeypatch, tmp_path):
    monkeypatch.setenv("STOCK_BASE_DIR", str(tmp_path))
    monkeypatch.setattr(sys, "path", sys.path.copy())
    # Use a private logger so fixture failures never enter operational logs.
    private_logger = logging.Logger("daily_pipeline_exit_fixture")
    original_get_logger = logging.getLogger
    monkeypatch.setattr(
        logging, "getLogger",
        lambda name=None: private_logger if name == "pipeline" else original_get_logger(name),
    )
    module = _load_script("daily_pipeline.py", "daily_pipeline_exit_fixture")
    monkeypatch.setitem(
        sys.modules, "ml.model_selection",
        SimpleNamespace(has_shadow_model_slot=lambda: False),
    )
    (tmp_path / "ml" / "models").mkdir(parents=True)
    events = []
    release = SimpleNamespace(release=lambda: events.append("release"))
    monkeypatch.setattr(module, "acquire_lock", lambda *a, **k: (release, None))
    monkeypatch.setattr(module, "_install_safe_stdio", lambda: None)
    monkeypatch.setattr(module, "is_trading_day", lambda: True)
    monkeypatch.setattr(sys, "argv", ["daily_pipeline.py", "--predict-only", "--force"])
    # Retain real main()/run_step(), replacing only their external work units.
    module.real_work_units = {
        name: func for name, func in vars(module).copy().items()
        if name.startswith("run_") and name not in {"run_step", "run_job"}
    }
    for name in vars(module):
        if name.startswith("run_") and name not in {"run_step", "run_job"}:
            monkeypatch.setattr(module, name, lambda name=name: events.append(name))
    monkeypatch.setattr(module, "run_job", lambda *a, **k: pytest.fail("real job forbidden"))
    module.events = events
    module.status_path = tmp_path / "ml" / "models" / "pipeline_status.json"
    yield module
    for handler in private_logger.handlers:
        handler.close()
    module._log_handler.close()


def test_failed_step_returns_one_after_remaining_steps_metadata_and_release(pipeline, monkeypatch):
    def fail_arena():
        pipeline.events.append("failed_arena")
        raise RuntimeError("fixture DuckDB snapshot unavailable")

    monkeypatch.setattr(pipeline, "run_agent_arena", fail_arena)
    assert pipeline.main() == 1
    status = json.loads(pipeline.status_path.read_text(encoding="utf-8"))
    assert status["all_ok"] is False
    assert status["steps"]["AgentArena"] == "failed"
    assert list(status["steps"].values()).count("failed") == 1
    assert pipeline.events.index("failed_arena") < pipeline.events.index("run_email_report")
    assert pipeline.events[-1] == "release"
    assert pipeline.events.count("release") == 1
    assert "run_retrain" not in pipeline.events


def test_success_returns_zero_with_complete_metadata_and_release(pipeline):
    assert pipeline.main() == 0
    status = json.loads(pipeline.status_path.read_text(encoding="utf-8"))
    assert status["all_ok"] is True
    assert all(value == "ok" for value in status["steps"].values())
    assert pipeline.events[-1] == "release"
    assert pipeline.events.count("run_predict") == 1


def test_mutual_exclusion_skip_returns_zero_without_touching_status(pipeline, monkeypatch):
    pipeline.status_path.write_text("previous-run", encoding="utf-8")
    monkeypatch.setattr(pipeline, "acquire_lock", lambda *a, **k: (None, {"pid": 42}))
    assert pipeline.main() == 0
    assert pipeline.events == []
    assert pipeline.status_path.read_text(encoding="utf-8") == "previous-run"


def test_nontrading_skip_returns_zero_and_releases_lock(pipeline, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["daily_pipeline.py", "--predict-only"])
    monkeypatch.setattr(pipeline, "is_trading_day", lambda: False)
    assert pipeline.main() == 0
    assert pipeline.events == ["release"]
    assert not pipeline.status_path.exists()


def test_metadata_write_failure_still_releases_lock(pipeline, monkeypatch, tmp_path):
    monkeypatch.setattr(pipeline, "BASE_DIR", str(tmp_path / "missing-output-root"))
    with pytest.raises(FileNotFoundError):
        pipeline.main()
    assert pipeline.events[-1] == "release"
    assert pipeline.events.count("release") == 1


def test_retrain_failure_is_not_retried(pipeline, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["daily_pipeline.py", "--predict-only", "--force", "--retrain"])

    def fail_training():
        pipeline.events.append("fixture_train")
        raise RuntimeError("fixture training failure; no model work performed")

    monkeypatch.setattr(pipeline, "run_retrain", fail_training)
    assert pipeline.main() == 1
    assert pipeline.events.count("fixture_train") == 1
    assert pipeline.events.count("run_backtest") == 0
    assert pipeline.events[-1] == "release"
    assert json.loads(pipeline.status_path.read_text(encoding="utf-8"))["all_ok"] is False


@pytest.mark.parametrize("unit", [
    "run_data_update", "run_daily_bar_verification", "run_valuation_update",
    "run_disposition_update", "run_tdcc_update",
])
@pytest.mark.parametrize("failure", ["nonzero", "timeout", "missing_executable"])
def test_fetch_child_failure_reaches_step_and_cli_ledger(pipeline, monkeypatch, unit, failure):
    from scripts import job_runner

    alerts = []
    monkeypatch.setattr(job_runner, "_send_failure_alert", lambda *args: alerts.append(args))

    def fail_child(cmd, **kwargs):
        if failure == "timeout":
            raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])
        if failure == "missing_executable":
            raise FileNotFoundError("fixture executable")
        return SimpleNamespace(returncode=7)

    monkeypatch.setattr(job_runner.subprocess, "run", fail_child)
    monkeypatch.setattr(pipeline, "run_job", job_runner.run_job)
    monkeypatch.setattr(pipeline, unit, pipeline.real_work_units[unit])
    monkeypatch.setattr(sys, "argv", ["daily_pipeline.py", "--force"])
    assert pipeline.main() == 1
    status = json.loads(pipeline.status_path.read_text(encoding="utf-8"))
    assert status["all_ok"] is False
    assert list(status["steps"].values()).count("failed") == 1 + len(status["blocked_steps"])
    assert status["blocked_steps"]
    assert not {"run_ingest", "run_predict", "run_paper_portfolio", "run_email_report"} & set(pipeline.events)
    assert len(alerts) == 1
    assert pipeline.events[-1] == "release"


@pytest.mark.parametrize("failed_source", ["news", "finnhub_news_sentiment"])
def test_optional_news_sources_both_attempted_and_failure_is_recorded(pipeline, monkeypatch, failed_source):
    attempts = []

    def child_result(name, *args, **kwargs):
        attempts.append(name)
        return {"name": name, "status": "timeout" if name == failed_source else "ok",
                "returncode": None if name == failed_source else 0}

    monkeypatch.setattr(pipeline, "run_job", child_result)
    assert pipeline.run_step("news", pipeline.real_work_units["run_news_update"]) is False
    assert attempts == ["news", "finnhub_news_sentiment"]


@pytest.mark.parametrize("day", ["2026-09-04", "2026-09-05", "2026-09-07"])
def test_tdcc_catches_up_after_weekend_release(pipeline, monkeypatch, day):
    from datetime import date

    attempts = []
    monkeypatch.setattr(pipeline, "date", SimpleNamespace(today=lambda: date.fromisoformat(day)))
    monkeypatch.setattr(pipeline, "run_job", lambda name, *a, **k: attempts.append((name, k)))
    pipeline.real_work_units["run_tdcc_update"]()
    assert len(attempts) == 1
    assert attempts[0][0] == "tdcc"
    assert attempts[0][1]["raise_on_fail"] is True


def test_data_success_verifies_official_bars_before_ingest_and_prediction(pipeline, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["daily_pipeline.py", "--force"])
    assert pipeline.main() == 0
    assert pipeline.events.index("run_data_update") < pipeline.events.index("run_daily_bar_verification")
    assert pipeline.events.index("run_daily_bar_verification") < pipeline.events.index("run_ingest")
    assert pipeline.events.index("run_ingest") < pipeline.events.index("run_predict")
    assert json.loads(pipeline.status_path.read_text(encoding="utf-8"))["blocked_steps"] == []


def test_ingest_false_blocks_training_prediction_ledgers_and_regular_email(pipeline, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["daily_pipeline.py", "--force", "--retrain"])
    monkeypatch.setattr(pipeline, "run_ingest", lambda: False)
    assert pipeline.main() == 1
    assert not {"run_retrain", "run_predict", "run_paper_portfolio", "run_email_report"} & set(pipeline.events)
    status = json.loads(pipeline.status_path.read_text(encoding="utf-8"))
    assert status["all_ok"] is False
    assert status["steps"]["DuckDB"] == "failed"
    assert status["blocked_steps"]


def test_news_failure_records_degraded_run_without_blocking_valid_price_data(pipeline, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["daily_pipeline.py", "--force"])
    monkeypatch.setattr(pipeline, "run_news_update", lambda: False)
    assert pipeline.main() == 1
    assert "run_ingest" in pipeline.events
    assert "run_predict" in pipeline.events
    assert json.loads(pipeline.status_path.read_text(encoding="utf-8"))["blocked_steps"] == []


@pytest.mark.parametrize("unit,blocked", [
    ("run_predict", {"run_unified_signals", "run_unified_portfolio", "run_paper_portfolio", "run_email_report"}),
    ("run_t1_predict", {"run_unified_signals", "run_unified_portfolio", "run_v4_rank15_shadow", "run_email_report"}),
    ("run_unified_signals", {"run_unified_portfolio", "run_research_output_layer", "run_email_report"}),
    ("run_unified_portfolio", {"run_email_report"}),
])
def test_failed_output_does_not_reuse_previous_artifacts(pipeline, monkeypatch, unit, blocked):
    monkeypatch.setattr(pipeline, unit, lambda: False)
    assert pipeline.main() == 1
    assert not blocked & set(pipeline.events)
    assert json.loads(pipeline.status_path.read_text(encoding="utf-8"))["blocked_steps"]


@pytest.mark.parametrize("status", [0, 1])
def test_actual_cli_entrypoint_forwards_main_status_without_running_jobs(status):
    # Execute the actual entry block alone; the real orchestration is tested above.
    tree = ast.parse((ROOT / "scripts" / "daily_pipeline.py").read_text(encoding="utf-8"))
    entry = ast.Module(body=[tree.body[-1]], type_ignores=[])
    with pytest.raises(SystemExit) as exc:
        exec(compile(entry, "daily_pipeline_cli_fixture", "exec"), {
            "__name__": "__main__", "main": lambda: status,
        })
    assert exc.value.code == status


@pytest.fixture
def weekly_guard(monkeypatch, tmp_path):
    events = []
    monkeypatch.setenv("STOCK_BASE_DIR", str(tmp_path))
    monkeypatch.setattr(sys, "path", sys.path.copy())
    monkeypatch.delenv("WEEKLY_RETRAIN_FULL_LEGACY", raising=False)
    logger = SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None,
                             exception=lambda *a, **k: None)
    monkeypatch.setitem(sys.modules, "scripts.smart_update_auto", SimpleNamespace(
        LOGGER=logger, WEB_PORT=8001, _find_dashboard_pids=lambda: set(),
        start_web_and_tunnel=lambda: events.append("restore") or "fixture-url",
        stop_web_and_tunnel=lambda: pytest.fail("real service stop forbidden"),
    ))
    module = _load_script("run_weekly_retrain_guard.py", "weekly_exit_fixture")
    monkeypatch.setattr(sys, "argv", ["run_weekly_retrain_guard.py"])
    monkeypatch.setattr(module, "_process_running", lambda name: True)
    monkeypatch.setattr(module, "_duckdb_writable", lambda: True)
    monkeypatch.setattr(module, "_reconnect_web_db_connection", lambda: events.append("reconnect"))
    monkeypatch.setattr(module, "_stop_web_for_retrain", lambda timeout: events.append("stop") or True)
    monkeypatch.setattr(module, "_wait_until", lambda predicate, *a: predicate())
    # Retain the real _run_daily_pipeline(); intercept its single process spawn.
    def completed(cmd, **kwargs):
        events.append(("pipeline", cmd, kwargs))
        return SimpleNamespace(returncode=1)

    monkeypatch.setattr(module, "subprocess", SimpleNamespace(run=completed))
    module.events = events
    return module


@pytest.mark.parametrize("released_via_api", [True, False])
def test_weekly_failure_runs_once_and_restores_services(weekly_guard, monkeypatch, released_via_api):
    monkeypatch.setattr(weekly_guard, "_port_open", lambda port: released_via_api)
    monkeypatch.setattr(weekly_guard, "_release_web_db_connection", lambda: True)
    assert weekly_guard.main() == 1
    processes = [event for event in weekly_guard.events if isinstance(event, tuple)]
    assert len(processes) == 1
    assert processes[0][1].count("--retrain") == 1
    assert processes[0][1].count("--retrain-v2-only") == 1
    assert processes[0][2]["check"] is False
    assert weekly_guard.events[-1] == ("reconnect" if released_via_api else "restore")
