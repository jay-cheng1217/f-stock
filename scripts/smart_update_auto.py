from __future__ import annotations

import argparse
import glob
import logging
import os
import re
import signal
import subprocess
import sys
import time
from datetime import date, datetime, timedelta

BASE_DIR = r"F:\stock"
sys.path.insert(0, BASE_DIR)

from scripts import smart_update as smart_update
from scripts.task_lock import acquire_lock

LOG_DIR = os.path.join(BASE_DIR, "logs")
os.makedirs(LOG_DIR, exist_ok=True)
LOCK_PATH = os.path.join(LOG_DIR, "smart_update_auto.lock")


def _build_logger() -> logging.Logger:
    logger = logging.getLogger("smart_update_auto")
    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)
    log_path = os.path.join(
        LOG_DIR, f"smart_update_auto_{datetime.now().strftime('%Y%m%d')}.log"
    )
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-5s | %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )

    file_handler = logging.FileHandler(log_path, encoding="utf-8", mode="a")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)
    return logger


LOGGER = _build_logger()


def is_weekday() -> bool:
    return date.today().weekday() < 5


def run_step(name: str, func) -> bool:
    LOGGER.info("START %s", name)
    try:
        func()
    except Exception:
        LOGGER.exception("FAIL  %s", name)
        return False

    LOGGER.info("DONE  %s", name)
    return True


def exec_paper_portfolio() -> None:
    from scripts.update_paper_portfolio import sync_paper_portfolio
    from scripts.update_paper_portfolio_t1 import sync_paper_portfolio_t1

    sync_paper_portfolio(top_n=30)
    sync_paper_portfolio_t1(top_n=10)


def exec_monitor() -> None:
    from scripts.monitor_t1 import generate_report
    generate_report(lookback_days=20)


def exec_email(remote_url: str | None = None) -> None:
    from scripts.send_daily_email import send_latest_email

    result = send_latest_email(remote_url=remote_url)
    status = str(result.get("status", ""))
    if status not in {"sent", "preview"}:
        raise RuntimeError(f"email status: {status or 'unknown'}")


def exec_tdcc_if_needed() -> None:
    if date.today().weekday() != 4:
        LOGGER.info("SKIP  TDCC weekly update (not Friday)")
        return
    smart_update.exec_tdcc()


def _prev_trading_day() -> date:
    """回傳前一個交易日（跳過週末）"""
    d = date.today() - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def _check_data_freshness() -> list[str]:
    """檢查前一交易日的法人/融資券快取是否存在，回傳缺口清單"""
    prev = _prev_trading_day()
    d8 = prev.strftime("%Y%m%d")
    gaps = []

    fund_dir = os.path.join(BASE_DIR, "法人快取")
    if not os.path.exists(os.path.join(fund_dir, f"fund_{d8}.csv")):
        gaps.append(f"法人(TWSE) {prev} 缺失")
    if not os.path.exists(os.path.join(fund_dir, f"fund_tpex_{d8}.csv")):
        gaps.append(f"法人(TPEx) {prev} 缺失")

    margin_dir = os.path.join(BASE_DIR, "融資券_raw")
    if os.path.isdir(margin_dir):
        if not os.path.exists(os.path.join(margin_dir, f"raw_margin_twse_{d8}.csv")):
            gaps.append(f"融資券(TWSE) {prev} 缺失")

    return gaps


def _send_alert_email(gaps: list[str]) -> None:
    """資料缺口時寄警報信"""
    try:
        from scripts.send_daily_email import load_email_settings, send_email
        settings = load_email_settings()
        if settings is None:
            LOGGER.warning("SMTP 未設定，無法寄送資料缺口警報")
            return

        subject = f"⚠️ 台股資料缺口警報 — {date.today()}"
        body = (
            "<h2>排程資料更新發現缺口</h2>"
            "<ul>" + "".join(f"<li>{g}</li>" for g in gaps) + "</ul>"
            "<p>可能原因：TWSE/TPEx API 在排程時段暫時無資料。</p>"
            "<p>建議：等 18:00 後手動執行 <code>python twstock.py</code> 補抓。</p>"
        )
        send_email(settings, subject, body)
        LOGGER.info("資料缺口警報已寄出")
    except Exception:
        LOGGER.exception("寄送資料缺口警報失敗")


# ---------------------------------------------------------------------------
# Web server & Cloudflare tunnel management
# ---------------------------------------------------------------------------

WEB_PORT = 8001
_PYTHON_EXE = sys.executable


def _kill_by_name(*names: str) -> int:
    """Kill processes by name. Returns number of processes killed."""
    killed = 0
    for name in names:
        try:
            result = subprocess.run(
                ["taskkill", "/F", "/IM", name],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0:
                killed += 1
        except Exception:
            pass
    return killed


def stop_web_and_tunnel() -> None:
    """Stop web server and cloudflare tunnel if running."""
    killed = _kill_by_name("cloudflared.exe")
    if killed:
        LOGGER.info("Stopped cloudflared tunnel")

    # Kill python processes running app.py on WEB_PORT
    try:
        result = subprocess.run(
            ["powershell", "-Command",
             f"Get-NetTCPConnection -LocalPort {WEB_PORT} -ErrorAction SilentlyContinue"
             " | Select-Object -ExpandProperty OwningProcess -Unique"],
            capture_output=True, text=True, timeout=10,
        )
        for line in result.stdout.strip().splitlines():
            pid = line.strip()
            if pid.isdigit() and int(pid) > 0:
                subprocess.run(
                    ["taskkill", "/F", "/PID", pid],
                    capture_output=True, timeout=10,
                )
                LOGGER.info("Stopped web server (pid=%s)", pid)
    except Exception:
        pass


def start_web_and_tunnel() -> str | None:
    """Start web server + cloudflare tunnel. Returns tunnel URL or None."""
    # Start web server
    subprocess.Popen(
        [_PYTHON_EXE, os.path.join(BASE_DIR, "app.py"),
         "--host", "0.0.0.0", "--port", str(WEB_PORT)],
        cwd=BASE_DIR,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    LOGGER.info("Web server started on port %d", WEB_PORT)

    # Wait for server to be ready
    time.sleep(5)

    # Start cloudflared tunnel
    proc = subprocess.Popen(
        ["cloudflared", "tunnel", "--url", f"http://localhost:{WEB_PORT}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )

    # Read output until we find the tunnel URL (timeout 30s)
    url = None
    deadline = time.time() + 30
    while time.time() < deadline:
        line = proc.stdout.readline()
        if not line:
            break
        match = re.search(r"(https://[a-z0-9-]+\.trycloudflare\.com)", line)
        if match:
            url = match.group(1)
            break

    if url:
        LOGGER.info("Cloudflare tunnel: %s", url)
    else:
        LOGGER.warning("Could not obtain cloudflare tunnel URL")

    return url


def _run_phase1_tw_data() -> list[tuple[str, bool]]:
    """Phase 1 (23:00)：台股資料更新 + DuckDB 匯入。

    台股收盤後所有資料都已就緒，不需要等美股。
    """
    step_results: list[tuple[str, bool]] = []

    # Stop web server & tunnel during data update to avoid DuckDB lock conflicts
    run_step("Stop web server & tunnel", stop_web_and_tunnel)

    data_steps = [
        ("Update TW stock base data", smart_update.exec_twstock),
        ("Update valuation data", smart_update.exec_valuation),
        ("Update EPS data", smart_update.exec_eps),
        ("Update daily news", smart_update.exec_news),
        ("Update TDCC weekly data", exec_tdcc_if_needed),
    ]

    for step_name, func in data_steps:
        step_results.append((step_name, run_step(step_name, func)))

    data_gaps = _check_data_freshness()
    if data_gaps:
        LOGGER.warning("DATA GAPS: %s", "; ".join(data_gaps))
        _send_alert_email(data_gaps)

    ingest_ok = run_step("Ingest refreshed data into DuckDB", smart_update.exec_ingest)
    step_results.append(("Ingest refreshed data into DuckDB", ingest_ok))

    # Web server 保持關閉，Phase 2 完成後再開
    return step_results


def _run_phase2_predict() -> list[tuple[str, bool]]:
    """Phase 2 (05:00)：美股收盤指標 + T1 重訓 + 預測 + 帳本 + 寄信。

    等美股收盤（台灣時間 ~04:00-05:00）後才能取得 VIX/費半/S&P500。
    """
    step_results: list[tuple[str, bool]] = []

    # Stop web server during prediction to avoid DuckDB lock conflicts
    run_step("Stop web server & tunnel", stop_web_and_tunnel)

    # 美股/國際指數（需等美股收盤）
    step_results.append(("Update global indices", run_step("Update global indices", smart_update.exec_indices)))

    # Re-ingest indices into DuckDB
    ingest_ok = run_step("Ingest indices into DuckDB", smart_update.exec_ingest)
    step_results.append(("Ingest indices into DuckDB", ingest_ok))

    # T+1 retrain (with fresh US data)
    t1_retrain_ok = run_step("Retrain T+1 model for today's open", smart_update.exec_retrain_t1)
    step_results.append(("Retrain T+1 model for today's open", t1_retrain_ok))
    if not t1_retrain_ok:
        LOGGER.warning("FALLBACK continue with the latest saved T+1 model for morning predictions")

    predict_ok = run_step("Generate latest predictions", smart_update.exec_predict)
    step_results.append(("Generate latest predictions", predict_ok))

    # Start web server & tunnel after predictions are ready
    remote_url = None
    try:
        remote_url = start_web_and_tunnel()
        LOGGER.info("DONE  Start web server & tunnel")
    except Exception:
        LOGGER.exception("FAIL  Start web server & tunnel")

    if predict_ok:
        portfolio_ok = run_step("Sync paper portfolio", exec_paper_portfolio)
        monitor_ok = run_step("Model monitoring", exec_monitor)
        verify_ok = run_step("Verify historical predictions", smart_update.exec_verify)
        email_ok = run_step("Send daily email report",
                            lambda: exec_email(remote_url=remote_url))
        step_results.extend(
            [
                ("Sync paper portfolio", portfolio_ok),
                ("Model monitoring", monitor_ok),
                ("Verify historical predictions", verify_ok),
                ("Send daily email report", email_ok),
            ]
        )
    else:
        LOGGER.warning("SKIP  paper portfolio / verify / email because prediction step failed")
        step_results.extend(
            [
                ("Sync paper portfolio", False),
                ("Verify historical predictions", False),
                ("Send daily email report", False),
            ]
        )

    return step_results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Non-interactive scheduled smart update with email delivery.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Run even on weekends/non-trading days.",
    )
    parser.add_argument(
        "--phase",
        choices=["1", "2", "all"],
        default="all",
        help="Phase 1 = 台股資料 (23:00), Phase 2 = 美股+預測+寄信 (05:00), all = 全部",
    )
    args = parser.parse_args(argv)

    lock_handle, existing_lock = acquire_lock(LOCK_PATH, "smart_update_auto")
    if lock_handle is None:
        pid = (existing_lock or {}).get("pid", "unknown")
        LOGGER.warning("SKIP  another smart update is already running (pid=%s)", pid)
        return 0

    now = datetime.now()
    phase_label = {"1": "Phase-1 (TW data)", "2": "Phase-2 (predict)", "all": "Full"}[args.phase]
    LOGGER.info("===== %s started at %s =====", phase_label, now.strftime("%Y-%m-%d %H:%M:%S"))

    try:
        if not args.force and not is_weekday():
            LOGGER.info("SKIP  non-trading day")
            return 0

        step_results: list[tuple[str, bool]] = []

        if args.phase in ("1", "all"):
            step_results.extend(_run_phase1_tw_data())

        if args.phase in ("2", "all"):
            step_results.extend(_run_phase2_predict())

        all_ok = all(success for _, success in step_results)
        elapsed = datetime.now() - now
        LOGGER.info(
            "===== %s finished in %.1f min | all_ok=%s =====",
            phase_label,
            elapsed.total_seconds() / 60.0,
            all_ok,
        )
        return 0 if all_ok else 1
    finally:
        lock_handle.release()


if __name__ == "__main__":
    raise SystemExit(main())
