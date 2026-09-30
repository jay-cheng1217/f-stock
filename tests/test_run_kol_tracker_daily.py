from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

from scripts import run_kol_tracker_daily
from scripts.run_kol_tracker_daily import _run_step, _validate_artifacts


def test_validate_artifacts_accepts_fresh_nonempty_html(tmp_path: Path) -> None:
    artifact = tmp_path / "dashboard.html"
    started_ns = time.time_ns() - 1_000_000_000
    artifact.write_bytes(b"<html>" + b"x" * 2048 + b"</html>")

    _validate_artifacts([artifact], not_before_ns=started_ns)


def test_validate_artifacts_rejects_stale_html(tmp_path: Path) -> None:
    artifact = tmp_path / "dashboard.html"
    artifact.write_bytes(b"<html>" + b"x" * 2048 + b"</html>")
    stale_seconds = time.time() - 60
    os.utime(artifact, (stale_seconds, stale_seconds))

    with pytest.raises(RuntimeError, match="artifact is stale"):
        _validate_artifacts([artifact], not_before_ns=time.time_ns())


def test_run_step_propagates_child_failure(tmp_path: Path) -> None:
    log_path = tmp_path / "kol.log"

    with pytest.raises(RuntimeError, match="exit code 7"):
        _run_step(
            "deliberate failure",
            [sys.executable, "-c", "print('failed output'); raise SystemExit(7)"],
            30,
            log_path=log_path,
        )

    assert "failed output" in log_path.read_text(encoding="utf-8")


def test_batch_wrapper_returns_orchestrator_exit_code() -> None:
    batch = Path("scripts/run_kol_tracker_daily.bat").read_text(encoding="utf-8")

    assert "run_kol_tracker_daily.py" in batch
    assert "exit /b %EXIT_CODE%" in batch
    assert ">> logs\\kol_tracker.log" not in batch


def test_default_scheduler_publishes_only_kol_dashboard(monkeypatch) -> None:
    calls = []
    published = []
    monkeypatch.setattr(
        run_kol_tracker_daily,
        "_run_step",
        lambda name, command, timeout: calls.append(name),
    )
    monkeypatch.setattr(
        run_kol_tracker_daily,
        "_validate_artifacts",
        lambda artifacts, *, not_before_ns: None,
    )
    monkeypatch.setattr(
        run_kol_tracker_daily,
        "_publish",
        lambda artifacts: published.extend(artifact.name for artifact in artifacts),
    )

    assert run_kol_tracker_daily.main([]) == 0
    assert calls == ["Classify KOL posts", "Build KOL dashboard"]
    assert published == ["kol_dashboard_latest.html"]


def test_entry_only_mode_builds_and_publishes_entry_dashboard(monkeypatch) -> None:
    calls = []
    published = []
    monkeypatch.setattr(
        run_kol_tracker_daily,
        "_run_step",
        lambda name, command, timeout: calls.append(name),
    )
    monkeypatch.setattr(
        run_kol_tracker_daily,
        "_validate_artifacts",
        lambda artifacts, *, not_before_ns: None,
    )
    monkeypatch.setattr(
        run_kol_tracker_daily,
        "_publish",
        lambda artifacts: published.extend(artifact.name for artifact in artifacts),
    )

    assert run_kol_tracker_daily.main(["--entry-only"]) == 0
    assert calls == ["Backfill entry tracker", "Build entry dashboard"]
    assert published == ["entry_dashboard_latest.html"]
