import logging

import pytest

from scripts import run_weekly_retrain_guard as guard


@pytest.fixture(autouse=True)
def _isolate_guard_side_effects(monkeypatch, tmp_path):
    """P1-7(2026-09-23 審查):測試不得觸真實服務或污染正式日誌。

    (1) main() 的 _release/_reconnect_web_db_connection 會打 127.0.0.1:8001
    admin API,本機 web 是否在跑會翻轉測試結果(release 成功→跳過
    WEEKLY_RETRAIN_WEB_PORT_RETAINED 分支→假失敗);一律 mock 掉。
    (2) guard 共用 smart_update_auto 的 LOGGER(FileHandler 指向正式
    logs/smart_update_auto_*.log),測試的假「starting weekly retrain」會混入
    正式日誌誤導日後稽核;改導獨立 tmp 日誌。"""
    monkeypatch.setattr(guard, "_release_web_db_connection", lambda: False)
    monkeypatch.setattr(guard, "_reconnect_web_db_connection", lambda: None)
    test_logger = logging.getLogger("weekly_retrain_guard_test")
    test_logger.handlers = [logging.FileHandler(tmp_path / "guard_test.log", encoding="utf-8")]
    test_logger.propagate = False
    monkeypatch.setattr(guard, "LOGGER", test_logger)


def test_stop_web_for_retrain_force_kills_lingering_dashboard_owner(monkeypatch):
    calls = []
    port_checks = iter([True, False])

    monkeypatch.setattr(guard, "stop_web_and_tunnel", lambda: calls.append(("stop", None)))
    monkeypatch.setattr(guard, "_port_open", lambda port: next(port_checks))
    monkeypatch.setattr(guard, "_find_dashboard_pids", lambda: {1234})
    monkeypatch.setattr(guard, "_kill_pid", lambda pid: calls.append(("kill", pid)) or True)
    monkeypatch.setattr(guard.time, "sleep", lambda seconds: calls.append(("sleep", seconds)))

    closed = guard._stop_web_for_retrain(10)

    assert calls == [("stop", None), ("kill", 1234), ("sleep", 2)]
    assert closed is True


def test_stop_web_for_retrain_returns_false_when_owner_cannot_be_killed(monkeypatch):
    now = iter([0, 1, 3])

    monkeypatch.setattr(guard, "stop_web_and_tunnel", lambda: None)
    monkeypatch.setattr(guard, "_port_open", lambda port: True)
    monkeypatch.setattr(guard, "_find_dashboard_pids", lambda: {5678})
    monkeypatch.setattr(guard, "_kill_pid", lambda pid: False)
    monkeypatch.setattr(guard.time, "time", lambda: next(now))
    monkeypatch.setattr(guard.time, "sleep", lambda seconds: None)

    closed = guard._stop_web_for_retrain(2)

    assert closed is False


def test_main_proceeds_when_web_port_remains_open_but_duckdb_writable(monkeypatch):
    calls = []

    monkeypatch.delenv("WEEKLY_RETRAIN_WEB_PORT_RETAINED", raising=False)
    monkeypatch.setattr(guard.sys, "argv", ["run_weekly_retrain_guard.py", "--restart-web", "never"])
    monkeypatch.setattr(guard, "_port_open", lambda port: True)
    monkeypatch.setattr(guard, "_process_running", lambda image_name: False)
    monkeypatch.setattr(guard, "_stop_web_for_retrain", lambda timeout: False)
    monkeypatch.setattr(guard, "_duckdb_writable", lambda: True)
    monkeypatch.setattr(guard, "_run_daily_pipeline", lambda args: calls.append(("pipeline", args)) or 0)

    assert guard.main() == 0
    assert calls == [("pipeline", [])]
    assert guard.os.environ["WEEKLY_RETRAIN_WEB_PORT_RETAINED"] == "1"
