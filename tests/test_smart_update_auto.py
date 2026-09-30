import io
import importlib
import json
import logging
import sys
from datetime import date

import pytest

from scripts import smart_update_auto


@pytest.fixture(autouse=True)
def isolate_smart_update_logger(monkeypatch):
    # Phase-1 invokes these outside run_step; mocking run_step alone is insufficient.
    # Tests must never heal live finance files or send real user alerts.
    from scripts import fundamentals_completeness_audit
    monkeypatch.setattr(fundamentals_completeness_audit, "run_audit", lambda *a, **k: [])
    monkeypatch.setattr(smart_update_auto, "_send_alert_email", lambda *a, **k: None)
    monkeypatch.setattr(smart_update_auto, "_send_special_status_email", lambda *a, **k: None)
    # Phase-1 tests that do not stub the canonical snapshot would otherwise leak the
    # evening-entry warning into later degraded-step assertions.
    monkeypatch.setitem(smart_update_auto.RUN_CONTEXT, "entry_evening_warning", None)
    logger = logging.getLogger("test_smart_update_auto")
    logger.handlers.clear()
    logger.addHandler(logging.NullHandler())
    logger.propagate = False
    logger.setLevel(logging.INFO)
    monkeypatch.setattr(smart_update_auto, "LOGGER", logger)


def test_parse_pid_output_keeps_unique_positive_numeric_pids():
    output = """
    42856
    not-a-pid
    0
    7696
    42856
    """

    assert smart_update_auto._parse_pid_output(output) == {42856, 7696}


def test_web_stop_ports_cover_scheduled_and_manual_dashboards():
    assert 8000 in smart_update_auto.WEB_STOP_PORTS
    assert smart_update_auto.WEB_PORT in smart_update_auto.WEB_STOP_PORTS


def test_kill_process_by_pid_records_access_denied(monkeypatch):
    calls = []

    class Result:
        returncode = 1
        stdout = ""
        stderr = "Access is denied"

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return Result()

    monkeypatch.setattr(smart_update_auto.subprocess, "run", fake_run)
    monkeypatch.setitem(smart_update_auto.RUN_CONTEXT, "web_stop_warning", None)

    assert smart_update_auto._kill_process_by_pid(33544) is False
    assert calls[0][:3] == ["taskkill", "/F", "/PID"]
    assert calls[1][:3] == ["powershell", "-NoProfile", "-Command"]
    assert str(smart_update_auto.RUN_CONTEXT["web_stop_warning"]).startswith(
        "web_stop_failed_pid_33544"
    )


def _closed_stream_logger() -> logging.Logger:
    closed_stream = io.StringIO()
    logger = logging.getLogger("test_smart_update_auto_closed_stream")
    logger.handlers.clear()
    logger.propagate = False
    logger.setLevel(logging.INFO)
    handler = smart_update_auto._SafeStreamHandler(closed_stream)
    closed_stream.close()
    logger.addHandler(handler)
    return logger


def test_run_step_survives_closed_stream_handler(monkeypatch):
    monkeypatch.setattr(smart_update_auto, "LOGGER", _closed_stream_logger())
    assert smart_update_auto.run_step("closed stream logging", lambda: None) is True


def test_run_step_exception_path_survives_closed_stream_handler(monkeypatch):
    monkeypatch.setattr(smart_update_auto, "LOGGER", _closed_stream_logger())

    def raise_error():
        raise RuntimeError("expected")

    assert smart_update_auto.run_step("closed stream exception logging", raise_error) is False


def test_install_safe_stdio_survives_closed_stdout_and_stderr(monkeypatch, tmp_path):
    closed_stdout = io.StringIO()
    closed_stderr = io.StringIO()
    closed_stdout.close()
    closed_stderr.close()
    fallback = tmp_path / "stdio.log"
    monkeypatch.setattr(smart_update_auto.sys, "stdout", closed_stdout)
    monkeypatch.setattr(smart_update_auto.sys, "stderr", closed_stderr)

    smart_update_auto._install_safe_stdio(str(fallback))
    print("stdout ok", file=smart_update_auto.sys.stdout)
    smart_update_auto.sys.stderr.write("stderr ok\n")

    assert smart_update_auto.os.environ["STOCK_SCHEDULED_STDIO_LOG"] == str(fallback)
    assert "stdout ok" in fallback.read_text(encoding="utf-8")
    assert "stderr ok" in fallback.read_text(encoding="utf-8")


def test_phase1_import_steps_tolerate_safe_stdio_without_buffer(monkeypatch, tmp_path):
    fallback = tmp_path / "stdio.log"
    monkeypatch.setattr(
        sys,
        "stdout",
        smart_update_auto._SafeTextStream(sys.stdout, fallback_path=str(fallback), label="stdout"),
    )
    monkeypatch.setattr(
        sys,
        "stderr",
        smart_update_auto._SafeTextStream(sys.stderr, fallback_path=str(fallback), label="stderr"),
    )

    for module_name in ("scripts.fetch_disposition", "scripts.fetch_public_market_context"):
        module = importlib.import_module(module_name)
        importlib.reload(module)


def test_web_ready_timeout_default_allows_slow_scheduled_startup():
    assert smart_update_auto.WEB_READY_TIMEOUT_SECONDS >= 90


def test_start_web_and_tunnel_reuses_existing_ready_web(monkeypatch):
    popen_calls = []
    waited = []

    monkeypatch.setattr(smart_update_auto, "_web_ready_now", lambda: True)
    monkeypatch.setattr(smart_update_auto, "_wait_for_web_ready", lambda timeout_seconds=None: waited.append(timeout_seconds))
    monkeypatch.setattr(
        smart_update_auto,
        "_start_cloudflared_once",
        lambda attempt: ("https://ready.example.trycloudflare.com", object(), "tunnel.log"),
    )
    monkeypatch.setattr(
        smart_update_auto.subprocess,
        "Popen",
        lambda *args, **kwargs: popen_calls.append((args, kwargs)),
    )

    assert smart_update_auto.start_web_and_tunnel() == "https://ready.example.trycloudflare.com"
    assert waited == [None]
    assert popen_calls == []


def test_special_status_subject_date_uses_effective_date_before_next_trading_day():
    report = {
        "effective_date": "2026-05-15",
        "last_successful_effective_date": "2026-05-15",
        "fetched_at": "2026-05-15T23:18:26",
    }

    assert smart_update_auto._special_status_subject_date(report) == "2026-05-15"


def test_special_status_subject_date_falls_back_to_fetched_at():
    report = {"fetched_at": "2026-05-15T23:18:26"}

    assert smart_update_auto._special_status_subject_date(report) == "2026-05-15"


def test_two_stage_defaults_enable_asymmetric_exit_policy(monkeypatch):
    monkeypatch.delenv("V2_MODEL_META", raising=False)
    monkeypatch.delenv("TWO_STAGE_RANKER_ENABLED", raising=False)
    monkeypatch.delenv("TWO_STAGE_RANKER_META", raising=False)
    monkeypatch.delenv("UNIFIED_EXIT_POLICY", raising=False)
    monkeypatch.delenv("SECTOR_OVERHEAT_THRESHOLDS_ENABLED", raising=False)

    smart_update_auto._enable_two_stage_champion_defaults()

    assert smart_update_auto.os.environ["V2_MODEL_META"].endswith(
        "lgbm_v2_20260508_200128_meta.json"
    )
    assert smart_update_auto.os.environ["TWO_STAGE_RANKER_ENABLED"] == "1"
    assert smart_update_auto.os.environ["UNIFIED_EXIT_POLICY"] == "asymmetric_v2"
    assert smart_update_auto.os.environ["SECTOR_OVERHEAT_THRESHOLDS_ENABLED"] == "1"


def test_phase1_skips_duckdb_ingest_when_twstock_fails(monkeypatch):
    calls = []

    def fake_run_step(name, func):
        calls.append(name)
        return not name.startswith("Update TW stock base data")

    monkeypatch.setattr(smart_update_auto, "run_step", fake_run_step)
    monkeypatch.setattr(smart_update_auto, "_load_special_status_report", lambda: None)
    monkeypatch.setattr(smart_update_auto, "_special_status_problem", lambda report: False)
    monkeypatch.setattr(smart_update_auto, "_check_data_freshness", lambda *a, **k: [])

    results = smart_update_auto._run_phase1_tw_data()

    assert "Ingest refreshed data into DuckDB" not in calls
    assert "Update agent arena" not in calls
    assert "Start web server & tunnel" in calls
    assert ("Ingest refreshed data into DuckDB", False) in results
    assert ("Update agent arena", False) in results
    assert ("Start web server & tunnel", True) in results


def test_phase1_updates_agent_arena_after_successful_duckdb_ingest(monkeypatch):
    calls = []

    def fake_run_step(name, func):
        calls.append(name)
        return True

    monkeypatch.setattr(smart_update_auto, "run_step", fake_run_step)
    monkeypatch.setattr(smart_update_auto, "_load_special_status_report", lambda: None)
    monkeypatch.setattr(smart_update_auto, "_special_status_problem", lambda report: False)
    monkeypatch.setattr(smart_update_auto, "_check_data_freshness", lambda *a, **k: [])

    results = smart_update_auto._run_phase1_tw_data()

    assert "Ingest refreshed data into DuckDB" in calls
    assert "Update TAIEX total-return index" in calls
    assert "Update agent arena" in calls
    assert "Start web server & tunnel" in calls
    assert calls.index("Update TW stock base data (daily, skip revenue)") < calls.index(
        "Update TAIEX total-return index"
    )
    assert calls.index("Update TAIEX total-return index") < calls.index(
        "Ingest refreshed data into DuckDB"
    )
    assert calls.index("Ingest refreshed data into DuckDB") < calls.index("Update agent arena")
    assert calls.index("Update agent arena") < calls.index("Start web server & tunnel")
    assert ("Update agent arena", True) in results
    assert ("Start web server & tunnel", True) in results


def test_phase1_marks_chipk_retired_by_default(monkeypatch):
    calls = []

    def fake_run_step(name, func):
        calls.append(name)
        return True

    monkeypatch.delenv("CHIPK_DESKTOP_ENABLED", raising=False)
    monkeypatch.delenv("STOCK_ENABLE_CHIPK_DESKTOP", raising=False)
    monkeypatch.setattr(smart_update_auto, "run_step", fake_run_step)
    monkeypatch.setattr(smart_update_auto, "_load_special_status_report", lambda: None)
    monkeypatch.setattr(smart_update_auto, "_special_status_problem", lambda report: False)
    monkeypatch.setattr(smart_update_auto, "_check_data_freshness", lambda *a, **k: [])

    smart_update_auto._run_phase1_tw_data()

    assert "Mark ChipK desktop retired" in calls
    assert "Archive ChipK desktop snapshot" not in calls
    assert "Analyze ChipK model diagnosis" not in calls


def test_phase1_runs_chipk_desktop_steps_only_when_enabled(monkeypatch):
    calls = []

    def fake_run_step(name, func):
        calls.append(name)
        return True

    monkeypatch.setenv("CHIPK_DESKTOP_ENABLED", "1")
    monkeypatch.setattr(smart_update_auto, "run_step", fake_run_step)
    monkeypatch.setattr(smart_update_auto, "_load_special_status_report", lambda: None)
    monkeypatch.setattr(smart_update_auto, "_special_status_problem", lambda report: False)
    monkeypatch.setattr(smart_update_auto, "_check_data_freshness", lambda *a, **k: [])

    smart_update_auto._run_phase1_tw_data()

    assert "Mark ChipK desktop retired" not in calls
    assert "Archive ChipK desktop snapshot" in calls
    assert "Analyze ChipK model diagnosis" in calls


def test_runtime_degraded_ignores_chipk_warning_when_desktop_retired(monkeypatch):
    monkeypatch.delenv("CHIPK_DESKTOP_ENABLED", raising=False)
    monkeypatch.delenv("STOCK_ENABLE_CHIPK_DESKTOP", raising=False)
    monkeypatch.setitem(smart_update_auto.RUN_CONTEXT, "remote_tunnel_attempted", None)
    monkeypatch.setitem(smart_update_auto.RUN_CONTEXT, "remote_url", None)
    monkeypatch.setitem(smart_update_auto.RUN_CONTEXT, "chipk_warning", "chipk_snapshot: stale")
    monkeypatch.setitem(smart_update_auto.RUN_CONTEXT, "agent_arena_warning", None)
    monkeypatch.setitem(smart_update_auto.RUN_CONTEXT, "web_stop_warning", None)

    assert smart_update_auto._runtime_degraded_steps() == []


def test_exec_chipk_retired_status_writes_non_degraded_status(monkeypatch, tmp_path):
    path = tmp_path / "chipk_snapshot_status_latest.json"
    monkeypatch.setattr(smart_update_auto, "CHIPK_STATUS_REPORT_PATH", str(path))
    monkeypatch.setitem(smart_update_auto.RUN_CONTEXT, "chipk_warning", "old warning")

    smart_update_auto.exec_chipk_retired_status()

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["status"] == "retired"
    assert payload["user_action"] is None
    assert smart_update_auto.RUN_CONTEXT["chipk_warning"] is None


def test_exec_agent_arena_treats_duckdb_lock_as_degraded(monkeypatch):
    from scripts import agent_arena

    def fake_run_daily_competition():
        raise RuntimeError(
            "DuckDB is locked by the web process; release the web DB connection "
            "before running Agent Arena standalone."
        )

    monkeypatch.setattr(agent_arena, "run_daily_competition", fake_run_daily_competition)
    released = []
    monkeypatch.setattr(smart_update_auto.smart_update, "_api_post", lambda path, **kw: released.append(path) or False)
    monkeypatch.setitem(smart_update_auto.RUN_CONTEXT, "remote_tunnel_attempted", None)
    monkeypatch.setitem(smart_update_auto.RUN_CONTEXT, "remote_url", None)
    monkeypatch.setitem(smart_update_auto.RUN_CONTEXT, "chipk_warning", None)
    monkeypatch.setitem(smart_update_auto.RUN_CONTEXT, "agent_arena_warning", None)

    smart_update_auto.exec_agent_arena()

    assert str(smart_update_auto.RUN_CONTEXT["agent_arena_warning"]).startswith(
        "agent_arena_skipped_duckdb_lock"
    )
    assert smart_update_auto._runtime_degraded_steps() == [
        smart_update_auto.RUN_CONTEXT["agent_arena_warning"]
    ]


def test_exec_agent_arena_reraises_non_lock_errors(monkeypatch):
    from scripts import agent_arena

    def fake_run_daily_competition():
        raise RuntimeError("agent scoring schema mismatch")

    monkeypatch.setattr(agent_arena, "run_daily_competition", fake_run_daily_competition)

    with pytest.raises(RuntimeError, match="agent scoring schema mismatch"):
        smart_update_auto.exec_agent_arena()


def test_phase2_freshness_gate_blocks_prediction_when_inputs_stale(monkeypatch):
    calls = []
    alerts = []

    def fake_run_step(name, func):
        calls.append(name)
        return True

    monkeypatch.setattr(smart_update_auto, "run_step", fake_run_step)
    monkeypatch.setattr(smart_update_auto, "_load_special_status_report", lambda: None)
    monkeypatch.setattr(smart_update_auto, "_special_status_problem", lambda report: False)
    monkeypatch.setattr(
        smart_update_auto,
        "_check_phase2_input_freshness",
        lambda now=None: ["index_TWII: stale"],
    )
    monkeypatch.setattr(
        smart_update_auto,
        "_send_freshness_gate_email",
        lambda gaps, *, phase, remote_url=None: alerts.append((gaps, phase, remote_url)),
    )

    results = smart_update_auto._run_phase2_predict()

    assert "Generate latest predictions" not in calls
    assert ("Freshness gate", False) in results
    assert ("Send daily email report", False) in results
    assert alerts == [(["index_TWII: stale"], "Phase-2 input", None)]


def test_phase2_output_freshness_gate_skips_normal_email(monkeypatch):
    calls = []
    alerts = []

    def fake_run_step(name, func):
        calls.append(name)
        return True

    monkeypatch.setattr(smart_update_auto, "run_step", fake_run_step)
    monkeypatch.setattr(smart_update_auto, "_load_special_status_report", lambda: None)
    monkeypatch.setattr(smart_update_auto, "_special_status_problem", lambda report: False)
    monkeypatch.setattr(smart_update_auto, "_check_phase2_input_freshness", lambda now=None: [])
    monkeypatch.setattr(smart_update_auto, "_check_phase2_output_freshness", lambda: ["predictions: missing"])
    monkeypatch.setattr(smart_update_auto.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(
        smart_update_auto,
        "_send_freshness_gate_email",
        lambda gaps, *, phase, remote_url=None: alerts.append((gaps, phase, remote_url)),
    )

    results = smart_update_auto._run_phase2_predict()

    assert "Generate latest predictions" in calls
    assert "Send daily email report" not in calls
    assert ("Output freshness gate", False) in results
    assert ("Send daily email report", False) in results
    assert alerts == [(["predictions: missing"], "Phase-2 output", None)]


def test_phase2_output_freshness_accepts_empty_unified_signal_file(monkeypatch, tmp_path):
    model_dir = tmp_path / "models"
    model_dir.mkdir()
    (model_dir / "predictions_2026-06-10.csv").write_text(
        "date,ticker,pred_return_20d\n2026-06-10,2492,0.12\n",
        encoding="utf-8",
    )
    (model_dir / "unified_signals_2026-06-10.csv").write_text(
        "prediction_date,ticker,signal_type,target_units,target_weight_ratio,close_ref\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(smart_update_auto, "MODEL_DIR", str(model_dir))
    monkeypatch.setattr(
        smart_update_auto,
        "_expected_twse_data_date",
        lambda now=None: date(2026, 6, 10),
    )

    assert smart_update_auto._check_phase2_output_freshness() == []


def test_resume_special_status_treats_agent_arena_as_non_blocking(monkeypatch):
    calls = []

    def fake_run_step(name, func):
        calls.append(name)
        return name != "Update agent arena"

    monkeypatch.setattr(smart_update_auto, "run_step", fake_run_step)
    monkeypatch.setattr(smart_update_auto, "_enable_two_stage_champion_defaults", lambda: None)
    monkeypatch.setattr(
        smart_update_auto,
        "_load_special_status_report",
        lambda: {"status": "OK", "gate_action": "ALLOW_T1_AND_DUAL"},
    )
    monkeypatch.setattr(smart_update_auto, "_special_status_problem", lambda report: False)
    monkeypatch.setattr(
        smart_update_auto,
        "_capture_remote_url",
        lambda state: state.update({"remote_url": None}),
    )
    monkeypatch.setattr(smart_update_auto.time, "sleep", lambda seconds: None)

    results = smart_update_auto._run_resume_after_special_status()

    assert "Update agent arena" in calls
    assert ("Update agent arena", True) in results
    assert all(success for _, success in results)


def test_phase2_output_freshness_blocks_nonempty_unified_file_without_date(monkeypatch, tmp_path):
    model_dir = tmp_path / "models"
    model_dir.mkdir()
    (model_dir / "predictions_2026-06-10.csv").write_text(
        "date,ticker,pred_return_20d\n2026-06-10,2492,0.12\n",
        encoding="utf-8",
    )
    (model_dir / "unified_signals_2026-06-10.csv").write_text(
        "prediction_date,ticker,signal_type,target_units,target_weight_ratio,close_ref\n"
        ",2492,20D_ONLY,1,0.1,100\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(smart_update_auto, "MODEL_DIR", str(model_dir))
    monkeypatch.setattr(
        smart_update_auto,
        "_expected_twse_data_date",
        lambda now=None: date(2026, 6, 10),
    )

    assert smart_update_auto._check_phase2_output_freshness() == [
        "unified_signals_2026-06-10.csv: missing date, expected >= 2026-06-10"
    ]


def test_canonical_entry_plan_is_fail_fast_and_uses_trade_date(monkeypatch, tmp_path):
    from scripts import job_runner

    calls = []

    def fake_run_job(name, command, **kwargs):
        calls.append((name, command, kwargs))
        return {"status": "ok"}

    monkeypatch.setattr(job_runner, "run_job", fake_run_job)
    monkeypatch.setattr(smart_update_auto, "BASE_DIR", str(tmp_path))

    plan_path = smart_update_auto._run_canonical_entry_plan(date(2026, 7, 17))

    assert plan_path.endswith("entry_list_20260717.json")
    assert [call[0] for call in calls] == [
        "entry_sector_strength",
        "entry_disposition_radar",
        "canonical_entry_candidates",
        "rere_lane_record",
        "rere_lane_check",
    ]
    generator = calls[2]
    assert generator[1][generator[1].index("--date") + 1] == "2026/07/17"
    assert all(call[2]["raise_on_fail"] is True for call in calls)


def test_tdcc_step_runs_every_day_not_only_friday(monkeypatch):
    """TDCC 週快照必須每個 Phase-1 都嘗試抓,不可只在週五。

    2026-07-19 事故背景:TDCC 週末才發布,原本「只在交易日週五執行」的閘門
    使這個專用步驟永遠抓不到新資料;真正在抓的是 twstock step 9,而 twstock
    在 step 9 之前崩潰(7/17 融資時序)當天就完全沒抓。TDCC OpenAPI 只給最新一期,
    錯過窗口該週永久遺失,故本步驟必須是獨立於 twstock 的每日路徑。
    """
    calls = []
    monkeypatch.setattr(
        smart_update_auto.smart_update, "exec_tdcc", lambda: calls.append(True)
    )

    # 週一~週日各觸發一次,全部都應呼叫 exec_tdcc
    for day in (
        date(2026, 7, 13),  # Mon
        date(2026, 7, 15),  # Wed
        date(2026, 7, 17),  # Fri
        date(2026, 7, 18),  # Sat
    ):
        calls.clear()

        class _FixedDate(date):
            @classmethod
            def today(cls):
                return day

        monkeypatch.setattr(smart_update_auto, "date", _FixedDate)
        smart_update_auto.exec_tdcc_if_needed()
        assert calls, f"{day} 未觸發 TDCC 抓取(週五限定閘門疑似復活)"

def test_shadow_dataA_refresh_record_gets_full_snapshot_timeout(monkeypatch):
    """record --refresh-snapshot rebuilds the whole-market frame (~20-40 min);
    the 900s step budget timed out every attempt on 2026-09-07."""
    from scripts import job_runner
    calls = []
    monkeypatch.setattr(job_runner, "run_job", lambda name, cmd, **kw: calls.append((name, cmd, kw)))
    monkeypatch.setattr(smart_update_auto, "_expected_twse_data_date", lambda: date(2026, 9, 7))
    smart_update_auto.exec_shadow_dataA_tracker(refresh_snapshot=True)
    by_name = {name: (cmd, kw) for name, cmd, kw in calls}
    assert "--refresh-snapshot" in by_name["shadow_dataA_record"][0]
    assert by_name["shadow_dataA_record"][1]["timeout"] >= 3600
    assert by_name["shadow_dataA_check"][1]["timeout"] == 900
    calls.clear()
    smart_update_auto.exec_shadow_dataA_tracker(refresh_snapshot=False)
    assert {name: kw["timeout"] for name, _, kw in calls} == {"shadow_dataA_record": 900, "shadow_dataA_check": 900}

def _phase1_stubs(monkeypatch, calls):
    monkeypatch.setattr(smart_update_auto, "run_step", lambda name, func: (calls.append(name), True)[1])
    monkeypatch.setattr(smart_update_auto, "_load_special_status_report", lambda: None)
    monkeypatch.setattr(smart_update_auto, "_special_status_problem", lambda report: False)
    monkeypatch.setattr(smart_update_auto, "_check_data_freshness", lambda *a, **k: [])
    monkeypatch.setattr(smart_update_auto, "_expected_twse_data_date", lambda *a, **k: date(2026, 9, 7))
    monkeypatch.setitem(smart_update_auto.RUN_CONTEXT, "entry_evening_warning", None)


def test_phase1_skips_evening_entry_plan_when_canonical_snapshot_is_stale(monkeypatch):
    """Certified generation needs snapshot as-of T; Phase-1 only rebuilds the DataA
    frame, so a weekday evening must degrade instead of failing (2026-09-07)."""
    calls = []
    _phase1_stubs(monkeypatch, calls)
    monkeypatch.setattr(smart_update_auto, "_canonical_snapshot_asof", lambda: date(2026, 9, 4))
    results = smart_update_auto._run_phase1_tw_data()
    assert "Entry candidates + rere lane tracker" not in calls
    assert ("Entry candidates + rere lane tracker", True) in results
    assert "Update agent arena" in calls
    assert any(w.startswith("entry_evening_skipped") for w in smart_update_auto._runtime_degraded_steps())


def test_phase1_runs_evening_entry_plan_when_canonical_snapshot_is_current(monkeypatch):
    calls = []
    _phase1_stubs(monkeypatch, calls)
    monkeypatch.setattr(smart_update_auto, "_canonical_snapshot_asof", lambda: date(2026, 9, 7))
    results = smart_update_auto._run_phase1_tw_data()
    assert "Entry candidates + rere lane tracker" in calls
    assert ("Entry candidates + rere lane tracker", True) in results
    assert not [w for w in smart_update_auto._runtime_degraded_steps() if w.startswith("entry_evening")]


def test_expected_margin_date_lags_until_twse_publishes_at_21(monkeypatch):
    from datetime import datetime
    assert smart_update_auto._expected_margin_data_date(datetime(2026, 9, 8, 19, 30)) == date(2026, 9, 7)
    # Even after TWSE publishes (~21:00) nothing fetches the file until Phase-2 07:00.
    assert smart_update_auto._expected_margin_data_date(datetime(2026, 9, 8, 21, 5)) == date(2026, 9, 7)
    assert smart_update_auto._expected_margin_data_date(datetime(2026, 9, 6, 12, 0)) == date(2026, 9, 3)


def test_freshness_gate_checks_margin_on_its_own_date(monkeypatch, tmp_path):
    monkeypatch.setattr(smart_update_auto, "BASE_DIR", str(tmp_path))
    (tmp_path / "法人快取").mkdir()
    (tmp_path / "原始融資資料").mkdir()
    for name in ("fund_20260908.csv", "fund_tpex_20260908.csv"):
        (tmp_path / "法人快取" / name).write_text("x")
    (tmp_path / "原始融資資料" / "raw_margin_twse_20260907.csv").write_text("x")
    assert smart_update_auto._check_data_freshness(expected_asof=date(2026, 9, 8), margin_asof=date(2026, 9, 7)) == []
    assert smart_update_auto._check_data_freshness(expected_asof=date(2026, 9, 8)) == ["融資券(TWSE) 2026-09-08 缺失"]


def test_exec_agent_arena_releases_web_duckdb_first_and_degrades_on_winerror(monkeypatch):
    from scripts import agent_arena
    released = []
    monkeypatch.setattr(smart_update_auto.smart_update, "_api_post", lambda path, **kw: released.append(path) or True)
    def locked():
        raise PermissionError("[WinError 32] 程序無法存取檔案，因為檔案正由另一個程序使用。")
    monkeypatch.setattr(agent_arena, "run_daily_competition", locked)
    monkeypatch.setitem(smart_update_auto.RUN_CONTEXT, "agent_arena_warning", None)
    smart_update_auto.exec_agent_arena()  # degraded, not raised
    assert released == ["/api/db/release"]
    assert str(smart_update_auto.RUN_CONTEXT["agent_arena_warning"]).startswith("agent_arena_skipped_duckdb_lock")
