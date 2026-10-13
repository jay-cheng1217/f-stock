"""共用 pytest 設定。

1. 把專案根目錄加入 sys.path，方便 ``from ml.predict import ...``。
2. 測試不可碰正式環境(2026-10-13 建立)。合併關卡開始在主工作目錄跑完整測試後才發現,既有測試會:
   - 把假的 ERROR / FAIL 寫進 logs/smart_update_auto_YYYYMMDD.log(排程出事時第一個要讀的檔案);
   - 把 logs/latest_bar_coverage_latest.json 蓋成測試用的假日期結果;
   - 對 127.0.0.1:8001 發出真的 POST /api/db/release,線上網頁伺服器因此釋放了 DuckDB 連線。
   個別測試各自記得隔離並不可靠(同一類問題 10/12、10/13 各發生一次),所以在這裡統一擋掉。
   需要真的連線的測試請標 ``@pytest.mark.allow_network``,並在測試內說明理由。
   限制:報告路徑與 KOL log 的導向只對「測試開始前已經匯入」的模組生效(測試檔在模組層匯入即可);
   在測試函式裡才匯入的話導不到,由 scripts/premerge_gate.py 的「排程檔案不得變動」檢查把關。
"""
import logging
import os
import sys
from pathlib import Path

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# 這四個 logger 的建立函式(scripts.smart_update_auto._build_logger、backend.logging_config.setup_logger)
# 都是「已經有 handler 就直接回傳」。在任何測試 import 它們之前先放一個 NullHandler,正式 log 檔就不會被打開。
# 訊息仍會往 root logger 傳,caplog 照常抓得到。
PRODUCTION_LOGGERS = ("smart_update_auto", "web.access", "web.error", "pipeline")
for _name in PRODUCTION_LOGGERS:
    _logger = logging.getLogger(_name)
    if not _logger.handlers:
        _logger.addHandler(logging.NullHandler())
logging.getLogger("smart_update_auto").setLevel(logging.INFO)  # _build_logger 提早回傳時不會設定等級


class BlockedNetworkCall(OSError):
    """測試中發出真實 HTTP 連線。繼承 OSError:被測程式多半把連線失敗當成一般 I/O 錯誤處理。"""


def pytest_configure(config):
    config.addinivalue_line("markers", "allow_network: 這個測試需要真實 HTTP 連線(預設一律擋下)")


@pytest.fixture(scope="session")
def _production_redirect_dir(tmp_path_factory) -> Path:
    return tmp_path_factory.mktemp("production_redirect")


@pytest.fixture(autouse=True)
def _no_production_side_effects(request, monkeypatch, _production_redirect_dir):
    blocked: list[str] = []
    if "allow_network" not in request.keywords:
        import urllib.request

        def _refuse_urlopen(req, *args, **kwargs):
            blocked.append(f"urllib {getattr(req, 'full_url', req)}")
            raise BlockedNetworkCall("tests must not open real HTTP connections")

        monkeypatch.setattr(urllib.request, "urlopen", _refuse_urlopen)
        try:
            from requests.adapters import HTTPAdapter
        except ImportError:  # pragma: no cover - requests 是專案相依套件
            HTTPAdapter = None
        if HTTPAdapter is not None:
            def _refuse_send(self, prepared, *args, **kwargs):
                blocked.append(f"requests {prepared.method} {prepared.url}")
                raise BlockedNetworkCall("tests must not open real HTTP connections")

            monkeypatch.setattr(HTTPAdapter, "send", _refuse_send)

    pipeline = sys.modules.get("scripts.smart_update_auto")
    if pipeline is not None and hasattr(pipeline, "LATEST_BAR_COVERAGE_REPORT"):
        monkeypatch.setattr(pipeline, "LATEST_BAR_COVERAGE_REPORT",
                            str(_production_redirect_dir / "latest_bar_coverage_latest.json"))

    kol = sys.modules.get("scripts.run_kol_tracker_daily")
    if kol is not None and hasattr(kol, "_append_log"):
        real_append, real_path = kol._append_log, Path(kol.LOG_PATH)

        def _append_log(message, *, log_path=real_path):
            target = _production_redirect_dir / "kol_tracker.log" if Path(log_path) == real_path else log_path
            return real_append(message, log_path=target)

        monkeypatch.setattr(kol, "_append_log", _append_log)

    yield blocked
    assert_no_blocked_calls(blocked)


def assert_no_blocked_calls(blocked: list[str]) -> None:
    """被測程式常把連線例外吞掉(例如 smart_update._api_post 回傳 False),所以測試收尾時再判一次;
    否則「測試去戳線上伺服器」只會悄悄變成「測試走了連線失敗的分支」。"""
    assert not blocked, "測試發出真實 HTTP 連線,請改用假物件或標 allow_network:" + ";".join(blocked[:5])
