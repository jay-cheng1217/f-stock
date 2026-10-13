"""測試隔離本身的測試:tests/conftest.py 的防線若被拿掉或失效,這裡要先紅。"""
import logging
import os
import urllib.request

import pytest

from scripts import run_kol_tracker_daily, smart_update_auto   # 模組層匯入:conftest 只導向「測試開始前已匯入」的模組
from tests import conftest

ROOT_LOGS = os.path.join(conftest.ROOT, "logs")


def _file_handlers_under_logs(name: str) -> list[str]:
    out = []
    for handler in logging.getLogger(name).handlers:
        path = getattr(handler, "baseFilename", None)
        if path and os.path.normcase(os.path.abspath(path)).startswith(os.path.normcase(ROOT_LOGS)):
            out.append(path)
    return out


def test_production_loggers_never_open_the_real_log_files():
    # 匯入會建立這些 logger 的模組之後再檢查,才測得到「先佔位」有沒有生效。
    import backend.logging_config  # noqa: F401
    from scripts import daily_pipeline  # noqa: F401

    for name in conftest.PRODUCTION_LOGGERS:
        assert _file_handlers_under_logs(name) == [], name
    assert smart_update_auto.LOGGER.isEnabledFor(logging.INFO)      # 佔位後等級仍與正式環境相同


def test_pipeline_log_messages_still_reach_caplog(caplog):
    with caplog.at_level(logging.INFO):
        smart_update_auto.LOGGER.info("isolation-probe")
    assert "isolation-probe" in caplog.text


def test_coverage_report_and_kol_log_point_away_from_production(tmp_path):
    assert not os.path.normcase(os.path.abspath(smart_update_auto.LATEST_BAR_COVERAGE_REPORT)).startswith(
        os.path.normcase(ROOT_LOGS))
    before = run_kol_tracker_daily.LOG_PATH.stat().st_size if run_kol_tracker_daily.LOG_PATH.exists() else None
    run_kol_tracker_daily._append_log("isolation-probe")                       # 預設路徑 → 被導到暫存目錄
    after = run_kol_tracker_daily.LOG_PATH.stat().st_size if run_kol_tracker_daily.LOG_PATH.exists() else None
    assert before == after
    explicit = tmp_path / "explicit.log"
    run_kol_tracker_daily._append_log("kept", log_path=explicit)                # 明確指定的路徑不受影響
    assert "kept" in explicit.read_text(encoding="utf-8")


def test_real_http_is_refused_and_recorded(_no_production_side_effects):
    blocked = _no_production_side_effects
    with pytest.raises(conftest.BlockedNetworkCall):
        urllib.request.urlopen("http://127.0.0.1:8001/api/db/release", timeout=1)
    import requests

    with pytest.raises(conftest.BlockedNetworkCall):
        requests.get("http://127.0.0.1:8001/", timeout=1)
    assert len(blocked) == 2 and blocked[0].startswith("urllib ") and blocked[1].startswith("requests GET ")
    blocked.clear()          # 這支測試是故意觸發的;不清掉的話,收尾檢查會(正確地)判它失敗


def test_swallowed_network_error_still_fails_at_teardown():
    conftest.assert_no_blocked_calls([])
    with pytest.raises(AssertionError, match="allow_network"):
        conftest.assert_no_blocked_calls(["urllib http://127.0.0.1:8001/api/db/release"])
