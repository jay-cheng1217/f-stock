import subprocess

from scripts import job_runner
from scripts.job_runner import DEFAULT_TIMEOUTS


def test_twstock_full_timeout_fits_between_phase1_and_phase2():
    assert DEFAULT_TIMEOUTS["twstock_full"] == 4 * 60 * 60
    assert DEFAULT_TIMEOUTS["twstock_full"] < 5 * 60 * 60


def test_twstock_daily_timeout_is_shorter_than_full_refresh():
    assert DEFAULT_TIMEOUTS["twstock_daily"] == 120 * 60
    assert DEFAULT_TIMEOUTS["twstock_daily"] < DEFAULT_TIMEOUTS["twstock_full"]


def test_run_job_redirects_child_output_in_scheduled_stdio_mode(monkeypatch, tmp_path):
    stdio_log = tmp_path / "scheduled_stdio.log"
    captured = {}

    def fake_run(cmd, **kwargs):
        captured.update(kwargs)
        kwargs["stdout"].write("child stdout\n")
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setenv(job_runner.SCHEDULED_STDIO_LOG_ENV, str(stdio_log))
    monkeypatch.setattr(job_runner.subprocess, "run", fake_run)

    result = job_runner.run_job("sample_child", ["python", "-c", "print('x')"], timeout=1)

    assert result["status"] == "ok"
    assert captured["stderr"] == subprocess.STDOUT
    assert "child stdout" in stdio_log.read_text(encoding="utf-8")
    assert "[job_runner:sample_child] child output start" in stdio_log.read_text(encoding="utf-8")
    assert "[job_runner:sample_child] child output end" in stdio_log.read_text(encoding="utf-8")
