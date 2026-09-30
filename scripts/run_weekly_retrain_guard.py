from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
import time

import duckdb

BASE_DIR = os.environ.get("STOCK_BASE_DIR") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from scripts.smart_update_auto import (  # noqa: E402
    LOGGER,
    WEB_PORT,
    _find_dashboard_pids,
    start_web_and_tunnel,
    stop_web_and_tunnel,
)

DUCKDB_PATH = os.path.join(BASE_DIR, "stock.duckdb")


def _port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(1)
        return sock.connect_ex(("127.0.0.1", int(port))) == 0


def _process_running(image_name: str) -> bool:
    result = subprocess.run(
        ["tasklist", "/FI", f"IMAGENAME eq {image_name}"],
        capture_output=True,
        text=True,
        # tasklist emits locale-encoded bytes (e.g. cp950/Big5 on zh-TW Windows).
        # Under UTF-8 mode (python -X utf8) the default UTF-8 decode raises in the
        # reader thread and leaves stdout=None -> AttributeError. errors="replace"
        # keeps decoding lossy-but-safe; (result.stdout or "") guards the None case.
        errors="replace",
        timeout=10,
    )
    return image_name.lower() in (result.stdout or "").lower()


def _wait_until(predicate, timeout_seconds: int, label: str) -> None:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(1)
    raise RuntimeError(f"Timed out waiting for {label} within {timeout_seconds}s")


def _kill_pid(pid: int) -> bool:
    try:
        result = subprocess.run(
            ["taskkill", "/F", "/PID", str(pid)],
            capture_output=True,
            text=True,
            errors="replace",  # locale-encoded taskkill output; avoid UTF-8-mode decode crash
            timeout=10,
        )
    except Exception as exc:
        LOGGER.warning("Failed to taskkill pid=%s: %s", pid, exc)
        return False
    if result.returncode == 0:
        LOGGER.info("Force-stopped web port owner pid=%s", pid)
        return True
    detail = (result.stderr or result.stdout or "").strip()
    LOGGER.warning("taskkill pid=%s returned %s: %s", pid, result.returncode, detail)
    return False


def _stop_web_for_retrain(timeout_seconds: int) -> bool:
    """Stop dashboard/tunnel and actively converge until the dashboard port closes.

    Returns ``True`` when the dashboard port is closed. If Windows refuses to
    terminate a dashboard process owned by another context, return ``False`` so
    the caller can still proceed when DuckDB is writable.
    """
    stop_web_and_tunnel()
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if not _port_open(WEB_PORT):
            LOGGER.info("Weekly retrain web port %s is closed", WEB_PORT)
            return True
        pids = sorted(_find_dashboard_pids())
        if pids:
            LOGGER.warning("Web port %s still open before retrain; force-killing pids=%s", WEB_PORT, pids)
            for pid in pids:
                _kill_pid(pid)
        else:
            LOGGER.warning("Web port %s still open before retrain, but no owner PID was found", WEB_PORT)
        time.sleep(2)

    remaining = sorted(_find_dashboard_pids())
    LOGGER.warning(
        "Timed out waiting for port %s to close within %ss; remaining_owner_pids=%s. "
        "Continuing only if DuckDB is writable.",
        WEB_PORT,
        timeout_seconds,
        remaining,
    )
    return False


def _duckdb_writable() -> bool:
    try:
        conn = duckdb.connect(DUCKDB_PATH, read_only=False)
    except duckdb.IOException:
        return False
    except PermissionError:
        return False
    else:
        conn.close()
        return True


def _release_web_db_connection() -> bool:
    """Ask the running web server to drop its DuckDB handle — no kill required.

    Reuses the same admin-token API path exec_ingest relies on. Preferred over
    killing the web because it needs no elevation: the 4-week retrain freeze
    (2026-07/08) was a Limited-privilege scheduled task unable to `taskkill` an
    elevated web process. Returns True only when the release call succeeded AND
    the write lock actually cleared, so the caller may retrain with the web up.
    """
    try:
        from scripts.smart_update import _api_post
    except Exception as exc:  # pragma: no cover - import guard
        LOGGER.warning("cannot import _api_post for web DB release: %s", exc)
        return False
    if not _api_post("/api/db/release", timeout=30):
        return False
    for _ in range(5):  # give the OS a moment to drop the file lock
        if _duckdb_writable():
            LOGGER.info("Web released its DuckDB connection; retrain will run with web left up")
            return True
        time.sleep(1)
    LOGGER.warning("Web DuckDB release returned OK but write lock did not clear")
    return False


def _reconnect_web_db_connection() -> None:
    """Ask the web server to re-open its DuckDB handle after retrain."""
    try:
        from scripts.smart_update import _api_post
    except Exception as exc:  # pragma: no cover - import guard
        LOGGER.warning("cannot import _api_post for web DB reconnect: %s", exc)
        return
    if _api_post("/api/db/reconnect", timeout=30):
        LOGGER.info("Web reconnected its DuckDB connection after retrain")
    else:
        LOGGER.warning("Web DuckDB reconnect call failed; web may serve from snapshot until next restart")


def _run_daily_pipeline(extra_args: list[str]) -> int:
    forwarded_args = list(extra_args)
    full_legacy = os.environ.get("WEEKLY_RETRAIN_FULL_LEGACY", "").strip().lower()
    if full_legacy not in {"1", "true", "yes", "on"} and "--retrain-v2-only" not in forwarded_args:
        forwarded_args.append("--retrain-v2-only")
    cmd = [
        sys.executable,
        os.path.join(BASE_DIR, "scripts", "daily_pipeline.py"),
        "--retrain",
        "--force",
        *forwarded_args,
    ]
    LOGGER.info("Running weekly retrain pipeline: %s", " ".join(cmd))
    return subprocess.run(cmd, cwd=BASE_DIR, check=False).returncode


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Guard weekly retrain by stopping web/tunnel, waiting for DuckDB, then restoring web if needed."
    )
    parser.add_argument(
        "--restart-web",
        choices=("auto", "always", "never"),
        default="auto",
        help="Restore web+tunnel after retrain. 'auto' restores only if it was running before.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=90,
        help="Timeout for port shutdown and DuckDB writable checks.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only report current state and planned action without stopping services or running retrain.",
    )
    parser.add_argument(
        "pipeline_args",
        nargs="*",
        help="Extra args forwarded to daily_pipeline.py after --retrain --force.",
    )
    args = parser.parse_args()

    was_web_running = _port_open(WEB_PORT)
    was_tunnel_running = _process_running("cloudflared.exe")
    should_restart = args.restart_web == "always" or (
        args.restart_web == "auto" and (was_web_running or was_tunnel_running)
    )

    LOGGER.info(
        "Weekly retrain guard state: web_running=%s tunnel_running=%s restart_policy=%s",
        was_web_running,
        was_tunnel_running,
        args.restart_web,
    )

    if args.dry_run:
        print(
            f"dry-run: web_running={was_web_running} tunnel_running={was_tunnel_running} "
            f"should_restart={should_restart}"
        )
        return 0

    # Preferred path: ask the running web to release its DuckDB handle instead of
    # killing it. Needs no elevation and keeps the dashboard + tunnel up (stable
    # URL). Falls back to stop/kill only if release does not clear the lock, so
    # behaviour is never worse than before.
    released_via_api = was_web_running and _release_web_db_connection()

    if not released_via_api:
        web_port_closed = _stop_web_for_retrain(args.timeout_seconds)
        _wait_until(_duckdb_writable, args.timeout_seconds, "DuckDB write lock to clear")
        if not web_port_closed:
            LOGGER.warning(
                "Web port %s remained open, but DuckDB is writable; proceeding with weekly retrain",
                WEB_PORT,
            )
            os.environ["WEEKLY_RETRAIN_WEB_PORT_RETAINED"] = "1"
    LOGGER.info("DuckDB is writable; starting weekly retrain")

    pipeline_code = 1
    restart_error: Exception | None = None
    try:
        pipeline_code = _run_daily_pipeline(args.pipeline_args)
    finally:
        if released_via_api:
            # Web was never stopped — just have it re-open its DuckDB handle.
            _reconnect_web_db_connection()
        elif should_restart:
            try:
                url = start_web_and_tunnel()
                LOGGER.info("Weekly retrain restored web/tunnel (remote_url=%s)", url)
            except Exception as exc:  # pragma: no cover - operational guard
                restart_error = exc
                LOGGER.exception("Failed to restore web/tunnel after weekly retrain")

    if restart_error is not None:
        raise RuntimeError(f"weekly retrain finished but web restore failed: {restart_error}") from restart_error
    return pipeline_code


if __name__ == "__main__":
    raise SystemExit(main())
