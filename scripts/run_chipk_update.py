"""Standalone scheduled ChipK snapshot update.

This keeps ChipK capture independent from the heavier nightly pipeline.  A
stale or missing desktop snapshot is reported as DEGRADED and exits 0 so the
Windows task does not become another noisy 0x1 incident.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))

from scripts.archive_chipk_snapshot import archive_latest_chipk_snapshot  # noqa: E402
from scripts.chipk_model_diagnosis import run as run_chipk_model_diagnosis  # noqa: E402
from scripts.notify_ntfy import send_ntfy  # noqa: E402
from scripts.taiwan_trading_calendar import (  # noqa: E402
    is_taiwan_trading_day,
    previous_taiwan_trading_day,
)
from scripts.task_lock import acquire_lock  # noqa: E402

REPORT_DIR = BASE_DIR / "ml" / "reports"
LOG_DIR = BASE_DIR / "logs"
STATUS_PATH = REPORT_DIR / "chipk_snapshot_status_latest.json"
LOCK_PATH = LOG_DIR / "chipk_update.lock"
CHIPK_DESKTOP_ENABLED_ENV = "CHIPK_DESKTOP_ENABLED"
LEGACY_CHIPK_DESKTOP_ENABLED_ENV = "STOCK_ENABLE_CHIPK_DESKTOP"

USER_REFRESH_INSTRUCTION = (
    "請打開或重開籌碼K桌面版，確認資料已更新到最新交易日；"
    "若仍是舊日期，請先在籌碼K手動刷新/切換個股後，再重跑 TW_Stock_ChipK_Update。"
)

# Level-1 自癒:snapshot 過期時自動(重)啟動理財寶/籌碼K 桌面版,觸發資料刷新。
DEFAULT_CHIPK_APP_PATH = r"C:\Program Files (x86)\CMoney\CMoney理財寶\AppViewer.exe"


def _env_flag(name: str, default: str = "0") -> bool:
    return str(os.environ.get(name, default)).strip().lower() in {"1", "true", "yes", "on"}


def _chipk_desktop_enabled() -> bool:
    if CHIPK_DESKTOP_ENABLED_ENV in os.environ:
        return _env_flag(CHIPK_DESKTOP_ENABLED_ENV)
    return _env_flag(LEGACY_CHIPK_DESKTOP_ENABLED_ENV)


def _chipk_app_path() -> str:
    return os.environ.get("CHIPK_APP_PATH") or DEFAULT_CHIPK_APP_PATH


def _relaunch_chipk_app(wait_seconds: int) -> dict[str, Any]:
    """啟動/聚焦 AppViewer.exe(單例 app 會帶到前景並觸發更新),等候後回傳結果。
    不處理登入:若 CMoney 已登出,此步無法自動修復,仍會回報 stale 交由通知。"""
    path = _chipk_app_path()
    info: dict[str, Any] = {"attempted": True, "app_path": path, "launched": False, "waited_seconds": 0, "error": None}
    if not os.path.exists(path):
        info.update({"attempted": False, "error": f"app not found: {path}"})
        return info
    # 2026-07-02:AppViewer 需提權(直接 Popen 得 WinError 740)。
    # 優先觸發預註冊的提權排程任務(schtasks /run 免提權);fallback 直接啟動。
    launched = False
    try:
        rc = subprocess.run(
            ["schtasks", "/run", "/tn", "TW_Stock_ChipK_Relaunch"],
            capture_output=True, timeout=30,
        ).returncode
        if rc == 0:
            launched = True
            info["launched"] = True
            info["via"] = "schtasks"
    except Exception:
        pass
    if not launched:
        try:
            subprocess.Popen([path], close_fds=True)  # noqa: S603 - 固定的本機 app 路徑
            info["launched"] = True
            info["via"] = "direct"
        except Exception as exc:  # pragma: no cover - 依賴本機 GUI 環境
            info["error"] = str(exc)
            return info
    wait = max(0, int(wait_seconds))
    time.sleep(wait)
    info["waited_seconds"] = wait
    return info


def _expected_chipk_asof(now: datetime | None = None) -> date:
    now = now or datetime.now()
    today = now.date()
    if is_taiwan_trading_day(today) and now.hour >= 18:
        return today
    return previous_taiwan_trading_day(today)


def _parse_date(value: Any) -> date | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text[:10]).date()
    except ValueError:
        return None


def _freshness_gap(actual: date | None, expected: date) -> str | None:
    if actual is None:
        return f"chipk_snapshot: missing date, expected >= {expected.isoformat()}"
    if actual < expected:
        return f"chipk_snapshot: stale {actual.isoformat()}, expected >= {expected.isoformat()}"
    return None


def _write_status(payload: dict[str, Any]) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    STATUS_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def _notify(*, status: str, payload: dict[str, Any], no_notify: bool, notify_ok: bool) -> None:
    if no_notify:
        return
    if status == "OK" and not notify_ok:
        return

    title = f"Stock ChipK Update: {status}"
    priority = "default" if status == "OK" else "high"
    tags = "white_check_mark" if status == "OK" else "warning"
    lines = [
        f"Status: {status}",
        f"Expected: {payload.get('expected_asof_date')}",
        f"ChipK asof: {payload.get('asof_date')}",
        f"Rows: {payload.get('row_count')}",
        f"Diagnosis: {payload.get('diagnosis_status', '-')}",
    ]
    if payload.get("freshness_gap"):
        lines.append(f"Gap: {payload['freshness_gap']}")
    if payload.get("user_action"):
        lines.append(f"Action: {payload['user_action']}")
    try:
        send_ntfy(title=title, message="\n".join(lines), priority=priority, tags=tags)
    except Exception as exc:  # pragma: no cover - notification must not break the task.
        print(f"[chipk-update] ntfy skipped: {exc}", file=sys.stderr)


def run(args: argparse.Namespace) -> int:
    now = datetime.now()
    if not _chipk_desktop_enabled():
        status_payload: dict[str, Any] = {
            "generated_at": now.isoformat(timespec="seconds"),
            "status": "retired",
            "message": (
                "ChipK desktop automation is retired. Mobile screenshots may still be "
                "used manually as an optional veto, but stale desktop snapshots are not "
                "a pipeline degradation."
            ),
            "user_action": None,
        }
        _write_status(status_payload)
        print(json.dumps(status_payload, ensure_ascii=False))
        return 0

    expected = _expected_chipk_asof(now)
    status_payload: dict[str, Any] = {
        "generated_at": now.isoformat(timespec="seconds"),
        "expected_asof_date": expected.isoformat(),
        "status": "unknown",
        "user_action": None,
    }

    try:
        archive_report = archive_latest_chipk_snapshot()
    except Exception as exc:
        status_payload.update(
            {
                "status": "error",
                "message": f"archive exception: {exc}",
                "user_action": USER_REFRESH_INSTRUCTION,
            }
        )
        _write_status(status_payload)
        _notify(status="DEGRADED", payload=status_payload, no_notify=args.no_notify, notify_ok=args.notify_ok)
        print(json.dumps(status_payload, ensure_ascii=False))
        return 0

    status_payload.update(archive_report)
    actual = _parse_date(archive_report.get("asof_date"))
    gap = _freshness_gap(actual, expected)

    # Level-1 自癒:過期時自動重啟 app 並重跑一次 archive,只有仍失敗才通知。
    if gap and getattr(args, "auto_relaunch", False):
        heal = _relaunch_chipk_app(getattr(args, "relaunch_wait", 60))
        if heal.get("launched"):
            try:
                archive_report = archive_latest_chipk_snapshot()
                status_payload.update(archive_report)
                actual = _parse_date(archive_report.get("asof_date"))
                gap = _freshness_gap(actual, expected)
                heal["post_asof_date"] = archive_report.get("asof_date")
                heal["recovered"] = gap is None
            except Exception as exc:
                heal["rearchive_error"] = str(exc)
                heal["recovered"] = False
        status_payload["auto_heal"] = heal

    if archive_report.get("archived") and not gap:
        status_payload.update({"status": "ok", "freshness_gap": None})
    elif archive_report.get("archived"):
        status_payload.update({"status": "stale", "freshness_gap": gap, "user_action": USER_REFRESH_INSTRUCTION})
    else:
        status_payload.update(
            {
                "status": "skipped",
                "freshness_gap": gap,
                "user_action": USER_REFRESH_INSTRUCTION,
            }
        )

    if not args.archive_only:
        try:
            output_prefix = args.output_prefix or (
                str(status_payload.get("asof_date") or "").replace("-", "") or now.strftime("%Y%m%d")
            )
            result = run_chipk_model_diagnosis(
                argparse.Namespace(
                    prediction_path=None,
                    chipk_path=None,
                    top_n=args.top_n,
                    output_prefix=output_prefix,
                )
            )
            status_payload.update(
                {
                    "diagnosis_status": "ok",
                    "diagnosis_generated_at": datetime.now().isoformat(timespec="seconds"),
                    "diagnosis_md": result.get("md"),
                    "diagnosis_json": result.get("json"),
                    "diagnosis_csv": result.get("csv"),
                    "diagnosis_decision_counts": result.get("decision_counts"),
                    "diagnosis_top_n_summary": result.get("top_n_summary"),
                }
            )
        except Exception as exc:
            status_payload.update(
                {
                    "diagnosis_status": "error",
                    "diagnosis_error": str(exc),
                    "diagnosis_generated_at": datetime.now().isoformat(timespec="seconds"),
                }
            )

    _write_status(status_payload)
    final_status = "OK" if status_payload.get("status") == "ok" and status_payload.get("diagnosis_status") == "ok" else "DEGRADED"
    _notify(status=final_status, payload=status_payload, no_notify=args.no_notify, notify_ok=args.notify_ok)
    print(json.dumps(status_payload, ensure_ascii=False, indent=2, default=str))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Archive ChipK snapshot and build the same-day diagnosis report.")
    parser.add_argument("--archive-only", action="store_true")
    parser.add_argument("--no-notify", action="store_true")
    parser.add_argument("--notify-ok", action="store_true")
    parser.add_argument("--output-prefix", default=None)
    parser.add_argument("--top-n", type=int, default=30)
    parser.add_argument(
        "--auto-relaunch",
        action="store_true",
        default=os.environ.get("CHIPK_AUTO_RELAUNCH", "").lower() in {"1", "true", "yes"},
        help="snapshot 過期時自動(重)啟動籌碼K桌面版並重試一次 archive(Level-1 自癒)",
    )
    parser.add_argument("--relaunch-wait", type=int, default=60, help="重啟後等待 app 刷新的秒數")
    args = parser.parse_args(argv)

    lock_handle, existing_lock = acquire_lock(str(LOCK_PATH), "chipk_update", stale_after_seconds=2 * 60 * 60)
    if lock_handle is None:
        print(json.dumps({"status": "skipped", "reason": "chipk_update already running", "lock": existing_lock}, ensure_ascii=False))
        return 0
    try:
        return run(args)
    finally:
        lock_handle.release()


if __name__ == "__main__":
    raise SystemExit(main())
