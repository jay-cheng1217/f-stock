from __future__ import annotations

import argparse
import glob
import logging
import os
import sys
from datetime import date, datetime, timedelta

BASE_DIR = r"F:\stock"
sys.path.insert(0, BASE_DIR)

from scripts import smart_update as smart_update

LOG_DIR = os.path.join(BASE_DIR, "logs")
os.makedirs(LOG_DIR, exist_ok=True)


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


def exec_email() -> None:
    from scripts.send_daily_email import send_latest_email

    result = send_latest_email()
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Non-interactive scheduled smart update with email delivery."
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Run even on weekends/non-trading days.",
    )
    args = parser.parse_args(argv)

    now = datetime.now()
    LOGGER.info("===== Smart morning update started at %s =====", now.strftime("%Y-%m-%d %H:%M:%S"))

    if not args.force and not is_weekday():
        LOGGER.info("SKIP  non-trading day")
        return 0

    data_steps = [
        ("Update TW stock base data", smart_update.exec_twstock),
        ("Update global indices", smart_update.exec_indices),
        ("Update valuation data", smart_update.exec_valuation),
        ("Update EPS data", smart_update.exec_eps),
        ("Update daily news", smart_update.exec_news),
        ("Update TDCC weekly data", exec_tdcc_if_needed),
    ]

    step_results: list[tuple[str, bool]] = []
    for step_name, func in data_steps:
        step_results.append((step_name, run_step(step_name, func)))

    # --- 資料完整性檢查：前一個交易日的法人/融資券是否到位 ---
    data_gaps = _check_data_freshness()
    if data_gaps:
        LOGGER.warning("DATA GAPS: %s", "; ".join(data_gaps))
        _send_alert_email(data_gaps)

    ingest_ok = run_step("Ingest refreshed data into DuckDB", smart_update.exec_ingest)
    step_results.append(("Ingest refreshed data into DuckDB", ingest_ok))

    t1_retrain_ok = run_step("Retrain T+1 model for today's open", smart_update.exec_retrain_t1)
    step_results.append(("Retrain T+1 model for today's open", t1_retrain_ok))
    if not t1_retrain_ok:
        LOGGER.warning("FALLBACK continue with the latest saved T+1 model for morning predictions")

    predict_ok = run_step("Generate latest predictions", smart_update.exec_predict)
    step_results.append(("Generate latest predictions", predict_ok))

    if predict_ok:
        portfolio_ok = run_step("Sync paper portfolio", exec_paper_portfolio)
        monitor_ok = run_step("Model monitoring", exec_monitor)
        verify_ok = run_step("Verify historical predictions", smart_update.exec_verify)
        email_ok = run_step("Send daily email report", exec_email)
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

    all_ok = all(success for _, success in step_results)
    elapsed = datetime.now() - now
    LOGGER.info("===== Smart morning update finished in %.1f min | all_ok=%s =====", elapsed.total_seconds() / 60.0, all_ok)
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
