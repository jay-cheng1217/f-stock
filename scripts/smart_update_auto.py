from __future__ import annotations

import argparse
import csv
import glob
import html
import json
import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta

BASE_DIR = os.environ.get("STOCK_BASE_DIR") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from scripts import smart_update as smart_update
from scripts.task_lock import acquire_lock
from scripts.taiwan_trading_calendar import (
    add_adhoc_closure,
    is_taiwan_trading_day,
    next_taiwan_trading_day,
    previous_taiwan_trading_day,
)
from scripts.model_pin_registry import PinValidationError, champion_defaults, validate_registry

LOG_DIR = os.path.join(BASE_DIR, "logs")
os.makedirs(LOG_DIR, exist_ok=True)
LOCK_PATH = os.path.join(LOG_DIR, "smart_update_auto.lock")
MODEL_DIR = os.path.join(BASE_DIR, "ml", "models")
REPORT_DIR = os.path.join(BASE_DIR, "ml", "reports")
NIGHTLY_ARCHIVE_ROOT = os.path.join(REPORT_DIR, "archive", "nightly")
SPECIAL_STATUS_REPORT_PATH = os.path.join(
    BASE_DIR, "ml", "reports", "special_stock_status_latest.json"
)
CHIPK_STATUS_REPORT_PATH = os.path.join(REPORT_DIR, "chipk_snapshot_status_latest.json")
CHIPK_DESKTOP_ENABLED_ENV = "CHIPK_DESKTOP_ENABLED"
LEGACY_CHIPK_DESKTOP_ENABLED_ENV = "STOCK_ENABLE_CHIPK_DESKTOP"
RUN_CONTEXT: dict[str, str | None] = {
    "entry_evening_warning": None,
    "remote_url": None,
    "remote_tunnel_attempted": None,
    "remote_tunnel_error": None,
    "chipk_warning": None,
    "agent_arena_warning": None,
    "web_stop_warning": None,
}

PHASE2_FRESHNESS_SENTINEL_TICKERS = ("2330", "2317")
# 300s:app.py 冷啟動要載模型與 snapshot cache(daily_k 已達 300 萬列),120s 對本機偶爾不夠。
# 2026-04~07 間 5 次 Phase-1 因此誤判失敗(伺服器其實隨後就緒),屬 flaky timeout 非真故障。
WEB_READY_TIMEOUT_SECONDS = int(os.environ.get("WEB_READY_TIMEOUT_SECONDS", "300"))
CHIPK_USER_REFRESH_INSTRUCTION = (
    "籌碼K資料未更新：請重開籌碼K桌面版，切到「選股 / 官方 / 主力動向」相關頁並等待列表刷新，"
    "再請 Codex 執行重抓補上。"
)


def _chipk_desktop_enabled() -> bool:
    """Return whether retired ChipK desktop automation should still run."""
    if CHIPK_DESKTOP_ENABLED_ENV in os.environ:
        return _env_flag(CHIPK_DESKTOP_ENABLED_ENV, False)
    return _env_flag(LEGACY_CHIPK_DESKTOP_ENABLED_ENV, False)


logging.raiseExceptions = False


class _SafeTextStream:
    """Best-effort stdout/stderr wrapper for Windows scheduled tasks."""

    def __init__(self, stream, *, fallback_path: str, label: str) -> None:
        self._stream = stream
        self._fallback_path = fallback_path
        self._label = label
        self.encoding = getattr(stream, "encoding", "utf-8") or "utf-8"
        self.errors = getattr(stream, "errors", "replace") or "replace"

    def write(self, text: str) -> int:
        if text is None:
            return 0
        try:
            if self._stream is not None and not getattr(self._stream, "closed", False):
                return self._stream.write(text)
        except Exception:
            pass
        try:
            with open(self._fallback_path, "a", encoding="utf-8") as fh:
                fh.write(str(text))
        except Exception:
            pass
        return len(str(text))

    def flush(self) -> None:
        try:
            if self._stream is not None and not getattr(self._stream, "closed", False):
                self._stream.flush()
        except Exception:
            pass

    def isatty(self) -> bool:
        try:
            return bool(self._stream is not None and self._stream.isatty())
        except Exception:
            return False

    def fileno(self) -> int:
        if self._stream is not None:
            return self._stream.fileno()
        raise OSError(f"{self._label} stream has no file descriptor")


class _SafeStreamHandler(logging.StreamHandler):
    """Keep scheduled jobs alive if stdout/stderr is closed by the host."""

    def handleError(self, record: logging.LogRecord) -> None:  # noqa: N802
        return None


def _install_safe_stdio(fallback_path: str | None = None) -> None:
    fallback = fallback_path or os.path.join(
        LOG_DIR,
        f"smart_update_auto_stdio_{datetime.now().strftime('%Y%m%d')}.log",
    )
    os.environ["STOCK_SCHEDULED_STDIO_LOG"] = fallback
    sys.stdout = _SafeTextStream(sys.stdout, fallback_path=fallback, label="stdout")
    sys.stderr = _SafeTextStream(sys.stderr, fallback_path=fallback, label="stderr")


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

    stream_handler = _SafeStreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)
    return logger


LOGGER = _build_logger()


def _env_flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off", ""}


def _enable_two_stage_champion_defaults() -> None:
    """Enable the current Champion defaults for nightly live trading."""
    try:
        defaults = champion_defaults()
        validation = validate_registry()
    except PinValidationError as exc:
        raise RuntimeError(f"Champion model pin validation failed: {exc}") from exc
    if not validation["passed"]:
        raise RuntimeError("Champion model pin validation failed: " + "; ".join(validation["failures"]))
    for key, value in defaults.items():
        if value:
            os.environ.setdefault(key, value)
    os.environ.setdefault("TQUANT_REFERENCE_GATE_ENABLED", "1")
    os.environ.setdefault("TQUANT_REFERENCE_GATE_MIN_CONSENSUS", "strong")
    os.environ.setdefault("ENTRY_BENCHMARK_GUARD_ENABLED", "1")
    LOGGER.info(
        "Two-Stage Champion ranker enabled: stage1_meta=%s meta=%s exit_policy=%s sector_overheat=%s model_era=%s tquant_gate=%s tquant_min=%s",
        os.environ.get("V2_MODEL_META"),
        os.environ.get("TWO_STAGE_RANKER_META"),
        os.environ.get("UNIFIED_EXIT_POLICY"),
        os.environ.get("SECTOR_OVERHEAT_THRESHOLDS_ENABLED"),
        os.environ.get("MODEL_ERA"),
        os.environ.get("TQUANT_REFERENCE_GATE_ENABLED"),
        os.environ.get("TQUANT_REFERENCE_GATE_MIN_CONSENSUS"),
    )


def _resolve_unified_nightly_config(
    *,
    default_db_path: str,
    default_rule_version: str,
    default_exit_policy: str,
    default_shadow_exit_policy: str,
) -> dict[str, object]:
    return {
        "db_path": os.path.abspath(
            os.environ.get(
                "V2_CHAMPION_DB_PATH",
                os.environ.get("UNIFIED_DB_PATH", default_db_path),
            )
        ),
        "rule_version": os.environ.get(
            "UNIFIED_RULE_VERSION",
            f"{default_rule_version}:paper:champion",
        ),
        "exit_policy": os.environ.get("UNIFIED_EXIT_POLICY", default_exit_policy).strip(),
        "shadow_db_path": os.path.abspath(
            os.environ.get(
                "V2_BASELINE_SHADOW_DB_PATH",
                os.environ.get(
                    "UNIFIED_SHADOW_DB_PATH",
                    os.path.join(BASE_DIR, "shadow_portfolio_v2_baseline.db"),
                ),
            )
        ),
        "shadow_rule_version": os.environ.get(
            "UNIFIED_SHADOW_RULE_VERSION",
            f"{default_rule_version}:paper:shadow",
        ),
        "shadow_exit_policy": os.environ.get(
            "UNIFIED_SHADOW_EXIT_POLICY",
            default_shadow_exit_policy,
        ).strip(),
        "penalty_overlay_enabled": _env_flag("UNIFIED_PENALTY_OVERLAY", True),
        "penalty_overlay_version": os.environ.get(
            "UNIFIED_PENALTY_OVERLAY_VERSION",
            "v1",
        ).strip().lower()
        or "v1",
        "archive_enabled": _env_flag("UNIFIED_ARCHIVE_ENABLED", True),
        "archive_root": os.path.abspath(
            os.environ.get("UNIFIED_ARCHIVE_ROOT", NIGHTLY_ARCHIVE_ROOT)
        ),
    }


def _copy_artifact(
    source_path: str,
    target_dir: str,
    copied: list[str],
    missing: list[str],
    *,
    target_name: str | None = None,
) -> None:
    if not source_path:
        return
    if not os.path.exists(source_path):
        missing.append(source_path)
        return
    os.makedirs(target_dir, exist_ok=True)
    destination = os.path.join(target_dir, target_name or os.path.basename(source_path))
    shutil.copy2(source_path, destination)
    copied.append(destination)


def _archive_nightly_unified_artifacts(
    *,
    prediction_date: str,
    unified_signal_path: str,
    champion_result: dict[str, object],
    shadow_result: dict[str, object],
    config: dict[str, object],
) -> dict[str, object]:
    archive_dir = os.path.join(str(config["archive_root"]), prediction_date)
    os.makedirs(archive_dir, exist_ok=True)

    copied: list[str] = []
    missing: list[str] = []
    prediction_paths = [
        os.path.join(MODEL_DIR, f"predictions_{prediction_date}.csv"),
        unified_signal_path,
        os.path.join(REPORT_DIR, "market_regime_latest.json"),
        os.path.join(REPORT_DIR, "market_regime_latest.md"),
        os.path.join(REPORT_DIR, "macro_strategy_context_latest.json"),
        os.path.join(REPORT_DIR, "macro_strategy_context_latest.md"),
        SPECIAL_STATUS_REPORT_PATH,
        os.path.join(REPORT_DIR, "prediction_tracking.json"),
        os.path.join(REPORT_DIR, "dashboard_summary.json"),
        os.path.join(REPORT_DIR, "portfolio_champion_vs_shadow.json"),
        os.path.join(REPORT_DIR, "unified_signals_latest.json"),
    ]
    for source_path in prediction_paths:
        _copy_artifact(source_path, archive_dir, copied, missing)

    manifest = {
        "archived_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "prediction_date": prediction_date,
        "archive_dir": archive_dir,
        "penalty_overlay_enabled": bool(config["penalty_overlay_enabled"]),
        "penalty_overlay_version": str(config["penalty_overlay_version"]),
        "champion": {
            "db_path": champion_result.get("db_path"),
            "rule_version": champion_result.get("rule_version"),
            "exit_policy_name": champion_result.get("exit_policy_name"),
            "processed_dates": champion_result.get("processed_dates"),
            "summary": champion_result.get("summary"),
        },
        "shadow": {
            "db_path": shadow_result.get("db_path"),
            "rule_version": shadow_result.get("rule_version"),
            "exit_policy_name": shadow_result.get("exit_policy_name"),
            "processed_dates": shadow_result.get("processed_dates"),
            "summary": shadow_result.get("summary"),
        },
        "copied_files": copied,
        "missing_files": missing,
    }
    manifest_path = os.path.join(archive_dir, "nightly_manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2)
    copied.append(manifest_path)
    LOGGER.info(
        "Archived nightly artifacts for %s to %s (copied=%d missing=%d)",
        prediction_date,
        archive_dir,
        len(copied),
        len(missing),
    )
    return {
        "archive_dir": archive_dir,
        "manifest_path": manifest_path,
        "copied_files": copied,
        "missing_files": missing,
    }


def is_weekday(today: date | None = None) -> bool:
    return (today or date.today()).weekday() < 5


def run_step(name: str, func) -> bool:
    LOGGER.info("START %s", name)
    try:
        if func() is False:
            raise RuntimeError(f"{name} explicitly reported failure")
    except Exception:
        LOGGER.exception("FAIL  %s", name)
        return False

    LOGGER.info("DONE  %s", name)
    return True


def exec_paper_portfolio() -> None:
    from backend.services.warroom_service import (
        CHAMPION_DB_PATH,
        SHADOW_DB_PATH,
        export_warroom_json_feeds,
    )
    from ml.market_regime import update_market_regime_report
    from scripts.build_unified_signals import build_unified_signals
    from scripts.exit_policies import (
        EXIT_POLICY_ASYMMETRIC_V2,
        EXIT_POLICY_BASELINE_WITH_MA20,
    )
    from scripts.update_unified_portfolio import (
        DEFAULT_RULE_VERSION as UNIFIED_DEFAULT_RULE_VERSION,
        sync_unified_portfolio,
    )

    update_market_regime_report()
    unified_config = _resolve_unified_nightly_config(
        default_db_path=CHAMPION_DB_PATH,
        default_rule_version=UNIFIED_DEFAULT_RULE_VERSION,
        default_exit_policy=EXIT_POLICY_ASYMMETRIC_V2,
        default_shadow_exit_policy=EXIT_POLICY_BASELINE_WITH_MA20,
    )
    unified_config["shadow_db_path"] = SHADOW_DB_PATH
    unified_result = build_unified_signals(
        penalty_overlay=bool(unified_config["penalty_overlay_enabled"]),
        penalty_overlay_version=str(unified_config["penalty_overlay_version"]),
        disable_t1=True,
        verbose=True,
    )
    champion_result = sync_unified_portfolio(
        signal_files=[unified_result.output_path],
        db_path=str(unified_config["db_path"]),
        rule_version=str(unified_config["rule_version"]),
        exit_policy_name=str(unified_config["exit_policy"]),
    )
    LOGGER.info(
        "Unified champion synced: policy=%s db=%s processed=%s",
        champion_result["exit_policy_name"],
        champion_result["db_path"],
        ",".join(champion_result["processed_dates"]) or "-",
    )
    shadow_result = sync_unified_portfolio(
        signal_files=[unified_result.output_path],
        db_path=str(unified_config["shadow_db_path"]),
        rule_version=str(unified_config["shadow_rule_version"]),
        exit_policy_name=str(unified_config["shadow_exit_policy"]),
    )
    LOGGER.info(
        "Unified shadow synced: policy=%s db=%s processed=%s",
        shadow_result["exit_policy_name"],
        shadow_result["db_path"],
        ",".join(shadow_result["processed_dates"]) or "-",
    )
    exported_paths = export_warroom_json_feeds()
    LOGGER.info(
        "Warroom feeds exported: %s",
        ", ".join(f"{name}={path}" for name, path in exported_paths.items()),
    )
    if bool(unified_config["archive_enabled"]):
        _archive_nightly_unified_artifacts(
            prediction_date=unified_result.prediction_date,
            unified_signal_path=unified_result.output_path,
            champion_result=champion_result,
            shadow_result=shadow_result,
            config=unified_config,
        )


def exec_monitor() -> None:
    from scripts.monitor_t1 import generate_report
    generate_report(lookback_days=20)


def exec_t1_observation() -> None:
    from scripts.track_t1_observation import track_daily
    track_daily()


def exec_email(remote_url: str | None = None) -> None:
    from scripts.send_daily_email import send_latest_email

    result = send_latest_email(remote_url=remote_url)
    status = str(result.get("status", ""))
    if status not in {"sent", "preview"}:
        raise RuntimeError(f"email status: {status or 'unknown'}")


def _is_agent_arena_duckdb_lock(exc: BaseException) -> bool:
    messages: list[str] = []
    current: BaseException | None = exc
    while current is not None:
        messages.append(str(current))
        current = current.__cause__ or current.__context__
    text = "\n".join(messages)
    return (
        "DuckDB is locked" in text
        or "File is already open" in text
        or "WinError 32" in text
        or ("stock.duckdb" in text and "already open" in text)
        or ("stock.duckdb" in text and "another process" in text)
    )


def exec_agent_arena() -> None:
    from scripts.agent_arena import run_daily_competition

    # A web server this session cannot stop (elevated task vs. manual resume,
    # 2026-09-07/08) still honours the release endpoint, exactly like ingest.
    if smart_update._api_post("/api/db/release", timeout=30):
        LOGGER.info("agent arena: web server released its DuckDB connection")
    try:
        run_daily_competition()
    except (RuntimeError, OSError) as exc:
        if not _is_agent_arena_duckdb_lock(exc):
            raise
        warning = (
            "agent_arena_skipped_duckdb_lock: stock.duckdb is busy; "
            "latest arena artifact preserved and can be refreshed after web/DuckDB is idle"
        )
        RUN_CONTEXT["agent_arena_warning"] = warning
        LOGGER.warning("SKIP  Update agent arena: %s", warning)


def exec_research_output_layer() -> None:
    """Build PM-readable research memos from existing local outputs."""
    from scripts.research_output_layer import (
        build_research_output_pack,
        write_research_output_artifacts,
    )

    pack = build_research_output_pack()
    paths = write_research_output_artifacts(pack)
    LOGGER.info("Research output layer wrote %s", paths.get("research_output_layer_md"))


def exec_entry_candidate_refresh() -> None:
    """Rebuild and publish the canonical entry plan after Phase-2 prediction."""
    from scripts.job_runner import run_job

    exec_shadow_dataA_tracker()
    run_job(
        "momentum_entry_candidates",
        [
            sys.executable,
            os.path.join(BASE_DIR, "scripts", "momentum_continuation_backtest.py"),
            "--asof",
            date.today().strftime("%Y%m%d"),
        ],
        timeout="default",
        cwd=BASE_DIR,
        raise_on_fail=True,
    )
    today = date.today()
    trade_day = today if is_taiwan_trading_day(today) else next_taiwan_trading_day(today)
    _run_canonical_entry_plan(trade_day)
    run_job(
        "publish_entry_dashboard",
        [
            sys.executable,
            "-X",
            "utf8",
            os.path.join(BASE_DIR, "scripts", "run_kol_tracker_daily.py"),
            "--entry-only",
        ],
        timeout="default",
        cwd=BASE_DIR,
        raise_on_fail=True,
    )


def exec_public_market_context() -> None:
    """抓公開市場情境(含 TAIFEX 三大法人/PutCall)。

    2026-07-23 修正:TAIFEX OpenAPI 在晚間 19:30-20:30 常短暫抽風(6/03、6/05、7/22、
    7/23 連續失敗),fetcher 內建的 urllib3 retry 只在數秒內退避,扛不過持續數分鐘的
    outage。加步驟級重試(間隔 120s)扛過短暫抽風;真的長時間掛掉才 raise 大聲失敗
    (保留 required gate,不弱化)。資料稍晚即正常,不是缺口。"""
    from scripts.fetch_public_market_context import fetch_public_market_context

    attempts = 3
    for attempt in range(1, attempts + 1):
        report = fetch_public_market_context()
        status = str(report.get("status") or "").lower()
        if status != "failed":
            if status == "degraded":
                LOGGER.warning("Public market context is DEGRADED: %s", report.get("failed_count"))
            return
        if attempt < attempts:
            LOGGER.warning(
                "public market context required failures %s (attempt %d/%d),120s 後重試",
                report.get("required_failures"), attempt, attempts,
            )
            time.sleep(120)
    raise RuntimeError(f"public market context required failures: {report.get('required_failures')}")


def exec_chipk_snapshot_archive() -> None:
    if not _chipk_desktop_enabled():
        exec_chipk_retired_status()
        return

    expected_asof = _expected_twse_data_date()
    status_payload: dict[str, object] = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "expected_asof_date": expected_asof.isoformat(),
        "status": "unknown",
    }
    try:
        from scripts.archive_chipk_snapshot import archive_latest_chipk_snapshot

        report = archive_latest_chipk_snapshot()
    except Exception:
        LOGGER.exception("ChipK desktop snapshot archive skipped due to unexpected error")
        RUN_CONTEXT["chipk_warning"] = f"chipk_archive_error | {CHIPK_USER_REFRESH_INSTRUCTION}"
        status_payload.update(
            {
                "status": "error",
                "message": "archive exception",
                "user_action": CHIPK_USER_REFRESH_INSTRUCTION,
            }
        )
        _write_chipk_status_report(status_payload)
        return

    status_payload.update(report)
    if report.get("archived"):
        asof = _parse_iso_date(str(report.get("asof_date") or ""))
        gap = _freshness_gap("chipk_snapshot", asof, expected_asof)
        if gap:
            warning = f"{gap} | {CHIPK_USER_REFRESH_INSTRUCTION}"
            RUN_CONTEXT["chipk_warning"] = warning
            status_payload.update(
                {
                    "status": "stale",
                    "freshness_gap": gap,
                    "user_action": CHIPK_USER_REFRESH_INSTRUCTION,
                }
            )
            LOGGER.warning("ChipK desktop snapshot stale: %s", warning)
        else:
            status_payload.update({"status": "ok", "freshness_gap": None, "user_action": None})
        LOGGER.info(
            "ChipK desktop snapshot archived: asof=%s rows=%s archive=%s",
            report.get("asof_date"),
            report.get("row_count"),
            report.get("archive_csv"),
        )
        _write_chipk_status_report(status_payload)
        return

    RUN_CONTEXT["chipk_warning"] = f"chipk_archive_skipped:{report.get('status')} | {CHIPK_USER_REFRESH_INSTRUCTION}"
    status_payload.update({"status": "skipped", "user_action": CHIPK_USER_REFRESH_INSTRUCTION})
    _write_chipk_status_report(status_payload)
    LOGGER.warning(
        "ChipK desktop snapshot archive skipped: status=%s rows=%s message=%s",
        report.get("status"),
        report.get("row_count"),
        report.get("message"),
    )


def exec_chipk_retired_status() -> None:
    """Mark the desktop ChipK source retired so stale snapshots stop paging users."""
    RUN_CONTEXT["chipk_warning"] = None
    payload: dict[str, object] = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "status": "retired",
        "message": (
            "ChipK desktop automation is retired. Mobile screenshots may still be "
            "used manually as an optional veto, but stale desktop snapshots are not "
            "a pipeline degradation."
        ),
        "user_action": None,
    }
    _write_chipk_status_report(payload)
    LOGGER.info("ChipK desktop automation retired; stale snapshot warnings suppressed")


def _write_chipk_status_report(payload: dict[str, object]) -> None:
    try:
        with open(CHIPK_STATUS_REPORT_PATH, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2, default=str)
            fh.write("\n")
    except Exception:
        LOGGER.exception("Failed to write ChipK status report")


def _load_chipk_status_report() -> dict[str, object]:
    try:
        with open(CHIPK_STATUS_REPORT_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except OSError:
        return {}
    except json.JSONDecodeError:
        LOGGER.warning("ChipK status report is not valid JSON: %s", CHIPK_STATUS_REPORT_PATH)
        return {}


SHADOW_DATAA_REFRESH_TIMEOUT = 3600  # seconds; matches job_runner "predict"


def exec_shadow_dataA_tracker(*, refresh_snapshot: bool = False) -> None:
    """DATA-PR-009 shadow-lite:新乾淨 Stage1 每日影子 Top30 vs champion,
    累積 forward 證據供升級決策；候選清單依賴此資料，因此失敗必須降級。"""
    from scripts.job_runner import run_job

    for mode in ("record", "check"):
        extra = (["--as-of", _expected_twse_data_date().isoformat()]
                 + (["--refresh-snapshot"] if refresh_snapshot else [])) if mode == "record" else []
        run_job(
            f"shadow_dataA_{mode}",
            [
                sys.executable,
                "-X",
                "utf8",
                os.path.join(BASE_DIR, "scripts", "shadow_dataA_tracker.py"),
                mode,
                *extra,
            ],
            # record --refresh-snapshot rebuilds the whole-market raw frame (same
            # cost as Phase-2 prediction, ~20-40 min); 900s only fits the no-op
            # / check paths (2026-09-07: timed out every attempt at 900s).
            timeout=SHADOW_DATAA_REFRESH_TIMEOUT if (mode == "record" and refresh_snapshot) else 900,
            cwd=BASE_DIR,
            raise_on_fail=True,
        )


def _run_canonical_entry_plan(trade_day: date) -> str:
    """Build one fail-fast canonical plan and update its sanctioned rere ledger."""
    from scripts.job_runner import run_job

    plan_path = os.path.join(BASE_DIR, "logs", f"entry_list_{trade_day:%Y%m%d}.json")
    jobs = (
        (
            "entry_sector_strength",
            [sys.executable, "-X", "utf8", os.path.join(BASE_DIR, "scripts", "sector_strength.py")],
            600,
        ),
        (
            "entry_disposition_radar",
            [
                sys.executable,
                "-X",
                "utf8",
                os.path.join(BASE_DIR, "scripts", "disposition_release_radar.py"),
            ],
            300,
        ),
        (
            "canonical_entry_candidates",
            [
                sys.executable,
                "-X",
                "utf8",
                os.path.join(BASE_DIR, "scripts", "generate_entry_candidates.py"),
                "--date",
                trade_day.strftime("%Y/%m/%d"),
                "-o",
                plan_path,
            ],
            1800,
        ),
        (
            "rere_lane_record",
            [
                sys.executable,
                "-X",
                "utf8",
                os.path.join(BASE_DIR, "scripts", "rere_lane_tracker.py"),
                "record",
                "--plan",
                plan_path,
            ],
            600,
        ),
        (
            "rere_lane_check",
            [
                sys.executable,
                "-X",
                "utf8",
                os.path.join(BASE_DIR, "scripts", "rere_lane_tracker.py"),
                "check",
            ],
            600,
        ),
    )
    for name, command, timeout in jobs:
        run_job(
            name,
            command,
            timeout=timeout,
            cwd=BASE_DIR,
            raise_on_fail=True,
        )
    LOGGER.info("canonical entry plan refreshed: %s", os.path.basename(plan_path))
    return plan_path


def exec_entry_candidates_and_rere_tracker() -> None:
    """每晚:產隔日 canonical 進場名單 → rere lane 記入驗證帳本 → 核對既有部位。
    前瞻驗證 rere lane 是否如回測基準(60日 +4.58%/勝率35.8%,2026-07-15 重定:
    停損+除息還原;舊 +9.4%/50.6% 為無停損 legacy 已作廢);報告:
    ml/reports/rere_lane_tracking_latest.md。任一步失敗均回報排程降級。"""
    next_day = next_taiwan_trading_day(date.today())
    _run_canonical_entry_plan(next_day)


def exec_chipk_model_diagnosis() -> None:
    if not _chipk_desktop_enabled():
        LOGGER.info("SKIP  ChipK model diagnosis because desktop automation is retired")
        return

    try:
        from scripts.chipk_model_diagnosis import run as run_chipk_model_diagnosis

        status_report = _load_chipk_status_report()
        asof = str(status_report.get("asof_date") or "").strip()
        output_prefix = asof.replace("-", "") if asof else datetime.now().strftime("%Y%m%d")
        result = run_chipk_model_diagnosis(
            argparse.Namespace(
                prediction_path=None,
                chipk_path=None,
                top_n=30,
                output_prefix=output_prefix,
            )
        )
        status_report.update(
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
        _write_chipk_status_report(status_report)
        LOGGER.info(
            "ChipK model diagnosis wrote %s confirmed=%s watch=%s conflict=%s",
            result.get("md"),
            (result.get("decision_counts") or {}).get("model_chipk_confirmed"),
            (result.get("decision_counts") or {}).get("model_chipk_watch"),
            (result.get("decision_counts") or {}).get("model_chipk_conflict"),
        )
    except Exception as exc:
        LOGGER.exception("ChipK model diagnosis skipped due to unexpected error")
        RUN_CONTEXT["chipk_warning"] = f"chipk_diagnosis_error:{exc}"
        status_report = _load_chipk_status_report()
        status_report.update(
            {
                "diagnosis_status": "error",
                "diagnosis_error": str(exc),
                "diagnosis_generated_at": datetime.now().isoformat(timespec="seconds"),
            }
        )
        _write_chipk_status_report(status_report)


def _send_completion_ntfy(
    *,
    phase_label: str,
    step_results: list[tuple[str, bool]],
    elapsed: timedelta,
    all_ok: bool,
    remote_url: str | None = None,
    degraded_steps: list[str] | None = None,
) -> None:
    try:
        from scripts.notify_ntfy import send_ntfy

        degraded_steps = degraded_steps or []
        failed_steps = [name for name, success in step_results if not success]
        status_text = "FAILED"
        if all_ok:
            status_text = "DEGRADED" if degraded_steps else "OK"
        title = f"Stock ML {phase_label}: {status_text}"
        failed_text = ", ".join(failed_steps[:6]) if failed_steps else "-"
        if len(failed_steps) > 6:
            failed_text += f", +{len(failed_steps) - 6} more"
        degraded_text = ", ".join(degraded_steps[:6]) if degraded_steps else "-"
        if len(degraded_steps) > 6:
            degraded_text += f", +{len(degraded_steps) - 6} more"
        message = "\n".join(
            [
                f"Status: {status_text}",
                f"Phase: {phase_label}",
                f"Elapsed: {elapsed.total_seconds() / 60.0:.1f} min",
                f"Steps: {sum(1 for _, success in step_results if success)}/{len(step_results)} ok",
                f"Failed: {failed_text}",
                f"Warnings: {degraded_text}",
            ]
        )
        result = send_ntfy(
            title=title,
            message=message,
            priority="high" if not all_ok else ("default" if not degraded_steps else "high"),
            tags="warning" if (not all_ok or degraded_steps) else "white_check_mark",
            click=remote_url or "",
        )
        if result.get("status") == "sent":
            LOGGER.info("Sent ntfy completion notification")
        else:
            LOGGER.info("Skip ntfy completion notification: %s", result.get("reason") or result)
    except Exception:
        LOGGER.exception("Failed to send ntfy completion notification")


def exec_tdcc_if_needed() -> None:
    """每個 Phase-1 都嘗試抓 TDCC 週快照(不再限定週五)。

    2026-07-19 修正:原本只在「交易日週五」執行,但 TDCC 是**週末才發布**——
    週五晚上跑時官方最新仍是上週(我們已有),等於這個專用步驟永遠抓不到新資料;
    真正在抓的是 twstock step 9,而 twstock 只要在 step 9 之前崩潰(如 7/17 於
    step 3 融資時序錯誤中斷),當天就完全沒抓 TDCC。

    TDCC OpenAPI 只提供「最新一期」,錯過發布窗口(下一期覆蓋前)該週即永久遺失,
    只能靠付費 FinMind 回補。因此本步驟改為每日執行,提供獨立於 twstock 的第二條
    路徑:抓取端本身會比對資料日期,已有就跳過,成本僅一次 API 呼叫。
    """
    smart_update.exec_tdcc()


def exec_tdcc_whale_refresh() -> None:
    """Catch up the optional weekly radar from existing raw files on every phase.

    Separate from raw acquisition and strategy gates; a bad derivative is a
    recorded step failure, never a fabricated weekly delta or a fresh badge.
    """
    from pathlib import Path
    from scripts.tdcc_whale_radar import refresh

    base = Path(BASE_DIR)
    report = refresh(input_dir=base / "集保分散", output_root=base,
                     sector_path=base / "ml/data/sector_mapping.csv")
    LOGGER.info("TDCC radar %s -> %s (%s paired securities)",
                report["previous_date"], report["as_of_date"], report["sources"]["paired_tickers"])


def exec_special_status_update() -> None:
    from scripts.fetch_disposition import update_disposition_status

    report = update_disposition_status()
    status = str(report.get("status") or "").upper()
    if status == "OK":
        return
    if status == "DEGRADED":
        LOGGER.warning(
            "Special stock status is DEGRADED; continuing with partial official snapshot: %s",
            report,
        )
        return
    if status == "STALE" and os.path.exists(os.path.join(BASE_DIR, "disposition_active.csv")):
        LOGGER.warning(
            "Special stock status is STALE; continuing with last successful snapshot (%s): %s",
            report.get("last_successful_effective_date") or "unknown",
            report.get("error_message") or "unknown error",
        )
        return
    if status != "OK":
        raise RuntimeError(
            "special stock status is "
            f"{status or 'UNKNOWN'}: {report.get('error_message') or 'unknown error'}"
        )


def _prev_trading_day(today: date | None = None) -> date:
    """回傳前一個台股交易日（跳過週末與 TWSE 休市日）"""
    return previous_taiwan_trading_day(today or date.today())


def _expected_margin_data_date(now: datetime | None = None) -> date:
    """Latest trading date whose TWSE margin file exists during Phase-1.

    TWSE publishes 融資融券 around 21:00, after the 19:30 twstock run, and the
    same-day file is only fetched by Phase-2's 07:00 backfill (raw_margin_twse_*
    mtimes are all 07:00). So during Phase-1 the newest margin file is always
    the previous trading day's, whatever the clock says; expecting the same-day
    file blocked candidates every evening after b8a39a52 (2026-09-08).
    """
    return previous_taiwan_trading_day(_expected_twse_data_date(now))


def _check_data_freshness(today: date | None = None, *, expected_asof: date | None = None,
                          margin_asof: date | None = None) -> list[str]:
    """檢查前一交易日的法人/融資券快取是否存在，回傳缺口清單"""
    prev = expected_asof or _prev_trading_day(today)
    margin_day = margin_asof or prev
    d8 = prev.strftime("%Y%m%d")
    gaps = []

    fund_dir = os.path.join(BASE_DIR, "法人快取")
    if not os.path.exists(os.path.join(fund_dir, f"fund_{d8}.csv")):
        gaps.append(f"法人(TWSE) {prev} 缺失")
    if not os.path.exists(os.path.join(fund_dir, f"fund_tpex_{d8}.csv")):
        gaps.append(f"法人(TPEx) {prev} 缺失")

    margin_dir = os.path.join(BASE_DIR, "原始融資資料")
    if os.path.isdir(margin_dir):
        m8 = margin_day.strftime("%Y%m%d")
        if not os.path.exists(os.path.join(margin_dir, f"raw_margin_twse_{m8}.csv")):
            gaps.append(f"融資券(TWSE) {margin_day} 缺失")

    return gaps


def _expected_twse_data_date(now: datetime | None = None) -> date:
    """Return the latest Taiwan trading date that should be available now."""
    now = now or datetime.now()
    today = now.date()
    if is_taiwan_trading_day(today) and now.hour >= 18:
        return today
    return previous_taiwan_trading_day(today)


def _parse_iso_date(raw: str | None) -> date | None:
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text[:10]).date()
    except ValueError:
        return None


def _latest_csv_date(path: str, columns: tuple[str, ...] = ("Date", "date", "prediction_date")) -> date | None:
    try:
        with open(path, newline="", encoding="utf-8-sig") as fh:
            reader = csv.DictReader(fh)
            latest: date | None = None
            for row in reader:
                for column in columns:
                    value = row.get(column)
                    parsed = _parse_iso_date(value)
                    if parsed is not None and (latest is None or parsed > latest):
                        latest = parsed
                    if parsed is not None:
                        break
            return latest
    except OSError:
        return None


def _csv_data_row_count(path: str) -> int | None:
    try:
        with open(path, newline="", encoding="utf-8-sig") as fh:
            reader = csv.DictReader(fh)
            return sum(1 for _ in reader)
    except OSError:
        return None


def _freshness_gap(label: str, actual: date | None, expected: date) -> str | None:
    if actual is None:
        return f"{label}: missing date, expected >= {expected}"
    if actual < expected:
        return f"{label}: stale {actual}, expected >= {expected}"
    return None


def _twse_confirms_no_trading(day: date) -> bool:
    """向證交所求證某日是否確實無交易資料（臨時休市，如颱風假）。

    只有 TWSE 明確回覆「查無資料」才回 True；網路錯誤/格式異常一律回 False，
    維持 gate 阻擋（寧可誤擋，不可把真正的資料缺口誤判成休市放行）。
    """
    url = (
        "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX"
        f"?date={day.strftime('%Y%m%d')}&type=IND&response=json"
    )
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 - 任何失敗都不可視為休市
        LOGGER.warning("TWSE closure probe failed for %s: %s", day, exc)
        return False
    stat = str(payload.get("stat") or "")
    if stat.upper() == "OK":
        # 臨時休市日 TWSE 回 stat=OK 但所有 tables 的 data 皆為空（2026-07-10 颱風假實測）
        tables = payload.get("tables") or []
        has_rows = any(t.get("data") for t in tables if isinstance(t, dict))
        return not has_rows
    # 部分端點查無資料時 stat 為「很抱歉，沒有符合條件的資料!」
    return ("沒有符合" in stat) or ("查無" in stat)


def _resolve_unscheduled_closures(now: datetime | None = None, max_days: int = 3) -> list[date]:
    """偵測未登錄的臨時休市日：expected 日在 TWSE 確認無資料時，寫入覆蓋檔並重算。

    回傳本次新登錄的休市日清單。最多回溯 max_days 天（連續多日颱風假）。
    """
    detected: list[date] = []
    for _ in range(max_days):
        expected = _expected_twse_data_date(now)
        if not _check_phase2_input_freshness(now):
            break  # gate 已可通過，不需再求證
        if not _twse_confirms_no_trading(expected):
            break  # TWSE 有該日資料（或求證失敗）→ 是真的資料缺口，維持阻擋
        add_adhoc_closure(expected, "auto-detected: TWSE 無該日交易資料（臨時休市）")
        LOGGER.warning(
            "FRESHNESS GATE auto-detected unscheduled market closure: %s (registered to adhoc_market_closures.json)",
            expected,
        )
        detected.append(expected)
    return detected


def _check_phase2_input_freshness(now: datetime | None = None) -> list[str]:
    """Guard Phase-2 from producing a normal report on stale TW market data."""
    expected = _expected_twse_data_date(now)
    gaps: list[str] = []

    for ticker in PHASE2_FRESHNESS_SENTINEL_TICKERS:
        path = os.path.join(smart_update.DAILY_K_DIR, f"{ticker}.csv")
        gap = _freshness_gap(f"daily_k/{ticker}", _latest_csv_date(path), expected)
        if gap:
            gaps.append(gap)

    twii_path = os.path.join(smart_update.INDEX_DIR, "index_TWII.csv")
    gap = _freshness_gap("index_TWII", _latest_csv_date(twii_path), expected)
    if gap:
        gaps.append(gap)

    return gaps


def _check_phase2_output_freshness(now: datetime | None = None) -> list[str]:
    """Guard the normal email from being sent with stale generated artifacts."""
    expected = _expected_twse_data_date(now)
    expected_str = expected.strftime("%Y-%m-%d")
    gaps: list[str] = []

    prediction_path = os.path.join(MODEL_DIR, f"predictions_{expected_str}.csv")
    if not os.path.exists(prediction_path):
        gaps.append(f"predictions_{expected_str}.csv: missing")
    else:
        gap = _freshness_gap(
            f"predictions_{expected_str}.csv",
            _latest_csv_date(prediction_path),
            expected,
        )
        if gap:
            gaps.append(gap)

    unified_path = os.path.join(MODEL_DIR, f"unified_signals_{expected_str}.csv")
    if not os.path.exists(unified_path):
        gaps.append(f"unified_signals_{expected_str}.csv: missing")
    else:
        actual_date = _latest_csv_date(unified_path, ("prediction_date", "Date", "date"))
        if actual_date is None and _csv_data_row_count(unified_path) == 0:
            actual_date = expected
        gap = _freshness_gap(
            f"unified_signals_{expected_str}.csv",
            actual_date,
            expected,
        )
        if gap:
            gaps.append(gap)

    return gaps


def _send_freshness_gate_email(gaps: list[str], *, phase: str, remote_url: str | None = None) -> None:
    """Send a hard-failure notice when a normal daily report is blocked."""
    try:
        from scripts.send_daily_email import load_email_settings, send_email

        settings = load_email_settings()
        if settings is None:
            LOGGER.warning("SMTP missing; cannot send freshness gate email")
            return

        expected = _expected_twse_data_date()
        remote_html = (
            f"<p>Remote dashboard: <a href=\"{html.escape(remote_url)}\">{html.escape(remote_url)}</a></p>"
            if remote_url
            else ""
        )
        body = (
            "<h2>Stock ML daily report blocked</h2>"
            f"<p>Phase: {html.escape(phase)}</p>"
            f"<p>Expected Taiwan market date: {expected}</p>"
            "<p>Normal daily email was not sent because required data/artifacts are stale.</p>"
            "<ul>"
            + "".join(f"<li>{html.escape(gap)}</li>" for gap in gaps)
            + "</ul>"
            + remote_html
            + "<p>Run Phase-1 data refresh, ingest DuckDB, then rerun Phase-2 before sending the daily report.</p>"
        )
        send_email(settings, f"[Stock ML] DATA STALE - daily report blocked ({expected})", body)
        LOGGER.info("Sent freshness gate email: %s", "; ".join(gaps))
    except Exception:
        LOGGER.exception("Failed to send freshness gate email")


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
# Special stock status health / alert helpers
# ---------------------------------------------------------------------------


def _next_weekday(start: date | None = None) -> date:
    return next_taiwan_trading_day(start or date.today())


def _special_status_subject_date(report: dict[str, object] | None) -> str:
    """Return the special-status data date for recovery email subjects."""
    if report:
        for key in ("effective_date", "last_successful_effective_date", "fetched_at"):
            parsed = _parse_iso_date(str(report.get(key) or ""))
            if parsed is not None:
                return parsed.isoformat()
    return date.today().isoformat()


def exec_verify_daily_bars() -> bool:
    """官方終盤校驗(scripts/verify_otc_bars.py):比對 TWSE/TPEX 官方,不符即覆寫 CSV。

    2026-07-02 事故:Yahoo 終盤化延遲,全市場 1191 檔收盤/量錯且增量抓取永不回補。
    校驗失敗回 False；run_step 必須將其記為 FAIL，阻擋 ingest 與候選發布。
    修復量 >50 檔時發 ntfy 告警(代表 Yahoo 又未定版,當晚訊號需留意)。
    """
    import re as _re
    try:
        proc = subprocess.run(
            [_PYTHON_EXE, os.path.join(BASE_DIR, "scripts", "verify_otc_bars.py")],
            capture_output=True, timeout=600, cwd=BASE_DIR)
        out = proc.stdout.decode("utf-8", errors="replace").strip()
        LOGGER.info("verify_daily_bars: %s", out[-300:])
        if proc.returncode != 0:
            err = proc.stderr.decode("utf-8", errors="replace").strip()
            LOGGER.error("verify_daily_bars rc=%s stderr tail: %s", proc.returncode, err[-1500:])
        m = _re.search(r"files_patched=(\d+)", out)
        patched = int(m.group(1)) if m else 0
        if patched > 50:
            try:
                from scripts.notify_ntfy import send_ntfy
                send_ntfy(title="日K終盤校驗告警",
                          message=f"官方對照修復 {patched} 檔(Yahoo 終盤化延遲重演),當晚訊號已用校驗後資料",
                          priority="high")
            except Exception:
                LOGGER.exception("verify ntfy failed")
        return proc.returncode == 0
    except Exception:
        LOGGER.exception("verify_daily_bars failed; candidate publication blocked")
        return False


def _load_special_status_report() -> dict[str, object] | None:
    if not os.path.exists(SPECIAL_STATUS_REPORT_PATH):
        return None
    try:
        with open(SPECIAL_STATUS_REPORT_PATH, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        LOGGER.exception("Failed to read special status report")
        return None


def _special_status_problem(report: dict[str, object] | None) -> bool:
    if report is None:
        return True
    return str(report.get("status") or "").upper() != "OK"


def _special_status_degraded_steps() -> list[str]:
    report = _load_special_status_report()
    if not _special_status_problem(report):
        return []
    if report is None:
        return ["special status report missing"]
    status = str(report.get("status") or "UNKNOWN").upper()
    gate = str(report.get("gate_action") or "unknown")
    missing = report.get("missing_sources") or []
    if isinstance(missing, list) and missing:
        missing_text = ",".join(str(item) for item in missing)
    else:
        missing_text = "-"
    return [f"special status {status} gate={gate} missing={missing_text}"]


def _format_special_report_items(report: dict[str, object] | None) -> str:
    if report is None:
        items = {
            "status": "MISSING",
            "report_path": SPECIAL_STATUS_REPORT_PATH,
            "gate_action": "CLOSED_FOR_T1_AND_DUAL",
        }
    else:
        items = {
            "status": report.get("status"),
            "fetched_at": report.get("fetched_at"),
            "effective_date": report.get("effective_date"),
            "last_successful_effective_date": report.get("last_successful_effective_date"),
            "source_counts": report.get("source_counts"),
            "missing_sources": report.get("missing_sources"),
            "retry_count": report.get("retry_count"),
            "gate_action": report.get("gate_action"),
            "error_message": report.get("error_message"),
        }
    return "".join(
        f"<li><b>{html.escape(str(key))}</b>: "
        f"<code>{html.escape(str(value))}</code></li>"
        for key, value in items.items()
    )


def _send_special_status_email(
    report: dict[str, object] | None,
    *,
    kind: str,
    remote_url: str | None = None,
) -> None:
    try:
        from scripts.send_daily_email import load_email_settings, send_email

        settings = load_email_settings()
        if settings is None:
            LOGGER.warning("SMTP missing; cannot send special status %s email", kind)
            return

        entry_date = _next_weekday().isoformat()
        status_date = _special_status_subject_date(report)
        prefix = settings.subject_prefix or "[Stock ML]"
        if kind.upper() == "RESUMED":
            subject = (
                f"{prefix}[RESUMED] Special status recovered - "
                f"T+1/Dual regenerated for {status_date}"
            )
            title = "Special stock status recovered"
            gate_line = (
                "The resume pipeline regenerated predictions, unified signals, "
                "portfolios, web, and email."
            )
        else:
            subject = (
                f"{prefix}[CRITICAL] SPECIAL_STATUS_STALE - "
                f"T+1/Dual Closed for {entry_date}"
            )
            title = "SPECIAL_STATUS_STALE"
            gate_line = "T+1 and Dual must fail closed until special stock status recovers."

        remote_section = ""
        if remote_url:
            safe_url = html.escape(remote_url)
            remote_section = f'<p><a href="{safe_url}">Open current web dashboard</a></p>'

        runbook = (
            "<ol>"
            "<li><code>python scripts\\fetch_disposition.py --retry-now</code></li>"
            "<li><code>python scripts\\smart_update_auto.py --resume-after-special-status --force</code></li>"
            "<li>Check <code>ml/reports/special_stock_status_latest.json</code> and pipeline logs.</li>"
            "</ol>"
        )
        body = (
            f"<h2>{html.escape(title)}</h2>"
            f"<p>{html.escape(gate_line)}</p>"
            f"<ul>{_format_special_report_items(report)}</ul>"
            f"{remote_section}"
            "<h3>Runbook</h3>"
            f"{runbook}"
        )
        send_email(settings, subject, body)
        LOGGER.info("Sent special status %s email", kind)
    except Exception:
        LOGGER.exception("Failed to send special status %s email", kind)


# ---------------------------------------------------------------------------
# Web server & Cloudflare tunnel management
# ---------------------------------------------------------------------------

WEB_PORT = 8001
WEB_STOP_PORTS = (8000, WEB_PORT)
_PYTHON_EXE = sys.executable
TUNNEL_RETRY_ATTEMPTS = 3
TUNNEL_URL_TIMEOUT_SECONDS = 45
TUNNEL_RETRY_SLEEP_SECONDS = 5
_TUNNEL_RE = re.compile(r"https://([a-z0-9-]+)\.trycloudflare\.com")
_NON_TUNNEL_HOSTS = {"api", "www", "developers"}


def _parse_pid_output(output: str) -> set[int]:
    pids: set[int] = set()
    for line in output.strip().splitlines():
        pid = line.strip()
        if pid.isdigit() and int(pid) > 0:
            pids.add(int(pid))
    return pids


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


def _kill_process_by_pid(pid: int) -> bool:
    taskkill_message = ""
    try:
        result = subprocess.run(
            ["taskkill", "/F", "/PID", str(pid)],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            LOGGER.info("Stopped web server (pid=%s)", pid)
            return True
        taskkill_message = (result.stderr or result.stdout or "").strip()
    except Exception as exc:
        taskkill_message = str(exc)

    powershell_message = ""
    try:
        result = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                f"Stop-Process -Id {pid} -Force -ErrorAction Stop",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            LOGGER.info("Stopped web server via Stop-Process (pid=%s)", pid)
            return True
        powershell_message = (result.stderr or result.stdout or "").strip()
    except Exception as exc:
        powershell_message = str(exc)

    message = taskkill_message or powershell_message or "unknown error"
    warning = f"web_stop_failed_pid_{pid}: {message}"
    RUN_CONTEXT["web_stop_warning"] = warning
    LOGGER.warning("Could not stop web server (pid=%s): %s", pid, message)
    return False


def _find_dashboard_pids() -> set[int]:
    """Find local dashboard app.py processes that can hold the DuckDB write lock."""
    pids: set[int] = set()
    ports = ",".join(str(port) for port in WEB_STOP_PORTS)
    try:
        result = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                (
                    f"Get-NetTCPConnection -LocalPort {ports} -ErrorAction SilentlyContinue"
                    " | Select-Object -ExpandProperty OwningProcess -Unique"
                ),
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        pids.update(_parse_pid_output(result.stdout))
    except Exception:
        pass

    try:
        result = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                (
                    "Get-CimInstance Win32_Process "
                    "| Where-Object { $_.Name -in @('python.exe','pythonw.exe') "
                    "  -and $_.CommandLine -like '*app.py*' } "
                    "| Select-Object -ExpandProperty ProcessId -Unique"
                ),
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        pids.update(_parse_pid_output(result.stdout))
    except Exception:
        pass

    return pids


def stop_web_and_tunnel() -> None:
    """Stop web server and cloudflare tunnel if running."""
    killed = _kill_by_name("cloudflared.exe")
    if killed:
        LOGGER.info("Stopped cloudflared tunnel")

    for pid in sorted(_find_dashboard_pids()):
        _kill_process_by_pid(pid)


def _wait_for_web_ready(timeout_seconds: int | None = None) -> None:
    """Block until the local web server responds with HTTP 200."""
    timeout_seconds = timeout_seconds or WEB_READY_TIMEOUT_SECONDS
    deadline = time.time() + timeout_seconds
    url = f"http://127.0.0.1:{WEB_PORT}/"

    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=5) as resp:
                if 200 <= int(getattr(resp, "status", 0)) < 400:
                    return
        except urllib.error.URLError:
            pass
        except Exception:
            pass
        time.sleep(1)

    raise RuntimeError(f"web server did not become ready within {timeout_seconds}s: {url}")


def _web_ready_now() -> bool:
    url = f"http://127.0.0.1:{WEB_PORT}/"
    try:
        with urllib.request.urlopen(url, timeout=3) as resp:
            return 200 <= int(getattr(resp, "status", 0)) < 400
    except Exception:
        return False


def _extract_tunnel_url(text: str) -> str | None:
    """Extract a real quick-tunnel URL from cloudflared output."""

    for match in _TUNNEL_RE.finditer(text or ""):
        if match.group(1) not in _NON_TUNNEL_HOSTS:
            return f"https://{match.group(1)}.trycloudflare.com"
    return None


def _tail_file(path: str, max_chars: int = 800) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError:
        return ""
    return text[-max_chars:].replace("\n", " | ").strip()


def _terminate_process(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    try:
        proc.terminate()
        proc.wait(timeout=5)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def _start_cloudflared_once(attempt: int) -> tuple[str | None, subprocess.Popen | None, str]:
    """Start one cloudflared quick tunnel attempt and poll its log for URL."""

    log_path = os.path.join(
        LOG_DIR,
        f"cloudflared_tunnel_{datetime.now().strftime('%Y%m%d_%H%M%S')}_attempt{attempt}.log",
    )
    log_handle = open(log_path, "w", encoding="utf-8", errors="replace")
    proc = subprocess.Popen(
        ["cloudflared", "tunnel", "--url", f"http://localhost:{WEB_PORT}"],
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    log_handle.close()

    deadline = time.time() + TUNNEL_URL_TIMEOUT_SECONDS
    while time.time() < deadline:
        tail = _tail_file(log_path, max_chars=4096)
        url = _extract_tunnel_url(tail)
        if url:
            return url, proc, log_path
        if proc.poll() is not None:
            break
        time.sleep(0.5)

    _terminate_process(proc)
    return None, None, log_path


def start_web_and_tunnel() -> str | None:
    """Start web server + cloudflare tunnel. Returns tunnel URL or None."""
    if _web_ready_now():
        LOGGER.info("Web server already ready on port %d; reusing it", WEB_PORT)
    else:
        subprocess.Popen(
            [_PYTHON_EXE, os.path.join(BASE_DIR, "app.py"),
             "--host", "0.0.0.0", "--port", str(WEB_PORT)],
            cwd=BASE_DIR,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        LOGGER.info("Web server started on port %d", WEB_PORT)

    _wait_for_web_ready()
    LOGGER.info("Web server passed readiness check on port %d", WEB_PORT)

    RUN_CONTEXT["remote_tunnel_attempted"] = "1"
    RUN_CONTEXT["remote_tunnel_error"] = None

    last_log = ""
    for attempt in range(1, TUNNEL_RETRY_ATTEMPTS + 1):
        url, _proc, log_path = _start_cloudflared_once(attempt)
        last_log = log_path
        if url:
            LOGGER.info("Cloudflare tunnel: %s", url)
            LOGGER.info("Cloudflare tunnel log: %s", log_path)
            RUN_CONTEXT["remote_tunnel_error"] = None
            return url

        tail = _tail_file(log_path)
        LOGGER.warning(
            "Cloudflare tunnel attempt %d/%d failed (log=%s): %s",
            attempt,
            TUNNEL_RETRY_ATTEMPTS,
            log_path,
            tail or "no output",
        )
        if attempt < TUNNEL_RETRY_ATTEMPTS:
            time.sleep(TUNNEL_RETRY_SLEEP_SECONDS)

    RUN_CONTEXT["remote_tunnel_error"] = f"missing_url_after_{TUNNEL_RETRY_ATTEMPTS}_attempts"
    LOGGER.warning("Could not obtain cloudflare tunnel URL after %d attempts (last_log=%s)", TUNNEL_RETRY_ATTEMPTS, last_log)
    return None


def _capture_remote_url(holder: dict[str, str | None]) -> None:
    holder["remote_url"] = start_web_and_tunnel()
    RUN_CONTEXT["remote_url"] = holder["remote_url"]
    # quick tunnel 每次重啟換網址 → 自動寄新網址(免費方案;best-effort 不擋 pipeline)
    if holder["remote_url"]:
        try:
            subprocess.run(
                [sys.executable, "-X", "utf8",
                 os.path.join(BASE_DIR, "scripts", "send_web_link_email.py"),
                 "--remote-url", holder["remote_url"]],
                cwd=BASE_DIR, timeout=120, check=False)
            LOGGER.info("web link email sent: %s", holder["remote_url"])
        except Exception as exc:
            LOGGER.warning("web link email failed: %s", exc)


def _runtime_degraded_steps() -> list[str]:
    degraded: list[str] = []
    if RUN_CONTEXT.get("remote_tunnel_attempted") == "1" and not RUN_CONTEXT.get("remote_url"):
        degraded.append(RUN_CONTEXT.get("remote_tunnel_error") or "remote_tunnel_url_missing")
    if _chipk_desktop_enabled() and RUN_CONTEXT.get("chipk_warning"):
        degraded.append(str(RUN_CONTEXT.get("chipk_warning")))
    if RUN_CONTEXT.get("agent_arena_warning"):
        degraded.append(str(RUN_CONTEXT.get("agent_arena_warning")))
    if RUN_CONTEXT.get("web_stop_warning"):
        degraded.append(str(RUN_CONTEXT.get("web_stop_warning")))
    if RUN_CONTEXT.get("entry_evening_warning"):
        degraded.append(str(RUN_CONTEXT.get("entry_evening_warning")))
    return degraded


def _canonical_snapshot_asof() -> date | None:
    """Latest bar date inside the Champion canonical snapshot (ml/models/snapshot_cache.pkl)."""
    path = os.path.join(BASE_DIR, "ml", "models", "snapshot_cache.pkl")
    try:
        import pandas as pd

        frame = pd.read_pickle(path)
        return pd.to_datetime(frame["Date"]).max().date()
    except Exception:  # noqa: BLE001 - absent/corrupt snapshot simply cannot certify
        return None


def _evening_entry_precondition(expected_asof: date) -> str | None:
    """Certified entry generation needs the canonical snapshot as of ``expected_asof``.

    Phase-1 rebuilds only the DataA shadow frame; the Champion snapshot is
    rebuilt by Phase-2 prediction the next morning, so on a weekday evening the
    certificate check in scripts/entry_artifact_lineage would reject every run
    (2026-09-07). Report the gap as a degraded warning instead of a step failure;
    Phase-2 "Refresh entry candidate table" publishes the certified plan.
    """
    snapshot_asof = _canonical_snapshot_asof()
    if snapshot_asof == expected_asof:
        return None
    return (f"entry_evening_skipped: canonical snapshot as-of {snapshot_asof} != {expected_asof}; "
            "certified plan is published by Phase-2 morning refresh")


def _run_phase1_tw_data() -> list[tuple[str, bool]]:
    """Phase 1 (23:00)：台股資料更新 + DuckDB 匯入。

    台股收盤後所有資料都已就緒，不需要等美股。
    """
    step_results: list[tuple[str, bool]] = []

    # Stop web server & tunnel during data update to avoid DuckDB lock conflicts
    run_step("Stop web server & tunnel", stop_web_and_tunnel)

    data_steps = [
        ("Update TW stock base data (daily, skip revenue)", smart_update.exec_twstock_daily),
        # 2026-07-03 新增:官方終盤校驗(防 Yahoo 終盤化延遲固化,如 0702 全市場 1191 檔事故)
        # 必須在 ingest 之前跑,DB 才不會灌到髒 bar;20:15 另有獨立排程當第二道保險
        ("Verify daily bars vs official (TWSE+TPEX)", exec_verify_daily_bars),
        (
            "Update TAIEX total-return index",
            smart_update.exec_taiex_total_return_index,
        ),
        ("Update monthly revenue (11-15 publish window)", smart_update.exec_monthly_revenue),
        ("Strategy scoreboard (monthly re-score, day 1-3)", smart_update.exec_strategy_scoreboard),
        ("Update valuation data", smart_update.exec_valuation),
        ("Update EPS data", smart_update.exec_eps),
        ("Update daily news", smart_update.exec_news),
        ("Update TDCC weekly data", exec_tdcc_if_needed),
        ("Refresh TDCC whale radar from raw weeks", exec_tdcc_whale_refresh),
        ("Update special stock status", exec_special_status_update),
        ("Update public market context", exec_public_market_context),
    ]
    if _chipk_desktop_enabled():
        data_steps.insert(9, ("Archive ChipK desktop snapshot", exec_chipk_snapshot_archive))
        data_steps.insert(10, ("Analyze ChipK model diagnosis", exec_chipk_model_diagnosis))
    else:
        data_steps.insert(9, ("Mark ChipK desktop retired", exec_chipk_retired_status))

    twstock_ok = True
    for step_name, func in data_steps:
        ok = run_step(step_name, func)
        step_results.append((step_name, ok))
        if step_name.startswith("Update TW stock base data"):
            twstock_ok = ok

    special_report = _load_special_status_report()
    if _special_status_problem(special_report):
        LOGGER.warning("SPECIAL_STATUS_STALE: %s", special_report)
        _send_special_status_email(special_report, kind="CRITICAL")

    data_gaps = _check_data_freshness(expected_asof=_expected_twse_data_date(),
                                      margin_asof=_expected_margin_data_date())

    # 財報完整性稽核(2026Q1 殘檔凍結事故後建立):月營收/季EPS/損益表/資產負債表
    # 覆蓋率檢查,季報缺口先自動回補(heal)再告警;缺口併入 DATA GAPS 警報信
    try:
        from scripts.fundamentals_completeness_audit import run_audit

        fundamental_gaps = run_audit(heal=True)
        if fundamental_gaps:
            data_gaps.extend(f"財報完整性: {g}" for g in fundamental_gaps)
    except Exception as exc:  # noqa: BLE001 - 稽核失敗不可擋 pipeline,但要告警
        LOGGER.exception("fundamentals completeness audit failed")
        data_gaps.append(f"財報完整性稽核執行失敗: {exc}")

    if data_gaps:
        LOGGER.warning("DATA GAPS: %s", "; ".join(data_gaps))
        _send_alert_email(data_gaps)

    official_bars_ok = dict(step_results).get("Verify daily bars vs official (TWSE+TPEX)", False)
    if twstock_ok and official_bars_ok:
        ingest_ok = run_step("Ingest refreshed data into DuckDB", smart_update.exec_ingest)
        step_results.append(("Ingest refreshed data into DuckDB", ingest_ok))
    else:
        LOGGER.warning(
            "SKIP  Ingest because base data or official daily-bar verification failed"
        )
        step_results.append(("Ingest refreshed data into DuckDB", False))
        ingest_ok = False

    critical_names = {
        "Update TAIEX total-return index", "Update monthly revenue (11-15 publish window)",
        "Update valuation data", "Update EPS data", "Update TDCC weekly data", "Update special stock status",
    }
    critical_failed = [name for name, ok in step_results if name in critical_names and not ok]
    if ingest_ok and not data_gaps and not critical_failed:
        # 候選依賴 ingest 完成的當日 frame，不能先用昨早快照配今日 CSV。
        shadow_ok = run_step("Shadow dataA model tracker", lambda: exec_shadow_dataA_tracker(refresh_snapshot=True))
        step_results.append(("Shadow dataA model tracker", shadow_ok))
        entry_warning = _evening_entry_precondition(_expected_twse_data_date()) if shadow_ok else None
        if entry_warning:
            RUN_CONTEXT["entry_evening_warning"] = entry_warning
            LOGGER.warning("SKIP  Entry candidates + rere lane tracker: %s", entry_warning)
            entry_ok = True
        else:
            entry_ok = shadow_ok and run_step("Entry candidates + rere lane tracker", exec_entry_candidates_and_rere_tracker)
        step_results.append(("Entry candidates + rere lane tracker", bool(entry_ok)))
        arena_ok = run_step("Update agent arena", exec_agent_arena)
        step_results.append(("Update agent arena", arena_ok))
    else:
        LOGGER.warning("Candidate publication blocked: gaps=%s failed=%s", data_gaps, critical_failed)
        step_results.append(("Shadow dataA model tracker", False))
        step_results.append(("Entry candidates + rere lane tracker", False))
        step_results.append(("Update agent arena", False))

    remote_state: dict[str, str | None] = {"remote_url": None}
    web_ok = run_step("Start web server & tunnel", lambda: _capture_remote_url(remote_state))
    step_results.append(("Start web server & tunnel", web_ok))

    return step_results


def _run_phase2_predict() -> list[tuple[str, bool]]:
    """Phase 2 (05:00)：美股收盤指標 + T1 重訓 + 預測 + 帳本 + 寄信。

    等美股收盤（台灣時間 ~04:00-05:00）後才能取得 VIX/費半/S&P500。
    """
    step_results: list[tuple[str, bool]] = []

    # Stop web server during prediction to avoid DuckDB lock conflicts
    run_step("Stop web server & tunnel", stop_web_and_tunnel)
    _enable_two_stage_champion_defaults()

    special_refresh_ok = run_step("Refresh special stock status", exec_special_status_update)
    step_results.append(("Refresh special stock status", special_refresh_ok))
    special_report = _load_special_status_report()
    if _special_status_problem(special_report):
        LOGGER.warning("SPECIAL_STATUS_STALE before prediction: %s", special_report)
        _send_special_status_email(special_report, kind="CRITICAL")

    # 美股/國際指數（需等美股收盤）
    step_results.append(("Update global indices", run_step("Update global indices", smart_update.exec_indices)))
    step_results.append(
        (
            "Backfill prev-day margin data",
            run_step("Backfill prev-day margin data", smart_update.exec_margin_backfill),
        )
    )
    step_results.append(
        (
            "Update ex-dividend calendar",
            run_step("Update ex-dividend calendar", smart_update.exec_exdiv_calendar),
        )
    )
    step_results.append(
        (
            "Update TAIEX total-return index",
            run_step(
                "Update TAIEX total-return index",
                smart_update.exec_taiex_total_return_index,
            ),
        )
    )
    step_results.append(
        (
            "Update macro strategy context",
            run_step("Update macro strategy context", smart_update.exec_macro_strategy_context),
        )
    )

    # Re-ingest indices into DuckDB
    ingest_ok = run_step("Ingest indices into DuckDB", smart_update.exec_ingest)
    step_results.append(("Ingest indices into DuckDB", ingest_ok))
    if not ingest_ok:
        gaps = ["Ingest indices into DuckDB failed; normal daily report blocked"]
        LOGGER.error("FRESHNESS GATE blocked before prediction: %s", "; ".join(gaps))
        _send_freshness_gate_email(gaps, phase="Phase-2 pre-prediction")
        step_results.extend(
            [
                ("Freshness gate", False),
                ("Generate latest predictions", False),
                ("Sync paper portfolio", False),
                ("Update agent arena", False),
                ("Verify historical predictions", False),
                ("Build research output layer", False),
                ("Refresh entry candidate table", False),
                ("Start web server & tunnel", False),
                ("Send daily email report", False),
            ]
        )
        return step_results

    input_gaps = _check_phase2_input_freshness()
    if input_gaps:
        closures = _resolve_unscheduled_closures()
        if closures:
            input_gaps = _check_phase2_input_freshness()
            if not input_gaps:
                LOGGER.info(
                    "FRESHNESS GATE passed after registering unscheduled closure(s): %s",
                    ", ".join(d.isoformat() for d in closures),
                )
    if input_gaps:
        LOGGER.error("FRESHNESS GATE blocked before prediction: %s", "; ".join(input_gaps))
        _send_freshness_gate_email(input_gaps, phase="Phase-2 input")
        step_results.extend(
            [
                ("Freshness gate", False),
                ("Generate latest predictions", False),
                ("Sync paper portfolio", False),
                ("Update agent arena", False),
                ("Verify historical predictions", False),
                ("Build research output layer", False),
                ("Refresh entry candidate table", False),
                ("Start web server & tunnel", False),
                ("Send daily email report", False),
            ]
        )
        return step_results
    step_results.append(("Freshness gate", True))

    # Saturday raw updates and missed nightly runs must catch up before display.
    step_results.append(("Refresh TDCC whale radar from raw weeks",
                         run_step("Refresh TDCC whale radar from raw weeks", exec_tdcc_whale_refresh)))

    predict_ok = run_step("Generate latest predictions", smart_update.exec_predict)
    step_results.append(("Generate latest predictions", predict_ok))

    if predict_ok:
        portfolio_ok = run_step("Sync paper portfolio", exec_paper_portfolio)
        arena_ok = run_step("Update agent arena", exec_agent_arena)
        verify_ok = run_step("Verify historical predictions", smart_update.exec_verify)
        research_ok = run_step("Build research output layer", exec_research_output_layer)
        entry_candidates_ok = run_step("Refresh entry candidate table", exec_entry_candidate_refresh)
        remote_state: dict[str, str | None] = {"remote_url": None}
        web_ok = run_step("Start web server & tunnel", lambda: _capture_remote_url(remote_state))
        # Give cloudflared NAT mappings 10s to stabilize before new outbound SMTP
        # connection — otherwise Gmail TCP SYN can get dropped mid-tunnel setup
        # (2026-04-16 TimeoutError(10060) incident).
        LOGGER.info("Waiting 10s for tunnel NAT to stabilize before SMTP")
        time.sleep(10)
        output_gaps = _check_phase2_output_freshness()
        if not entry_candidates_ok:
            output_gaps.append("canonical entry plan/dashboard refresh failed")
        if output_gaps:
            LOGGER.error("FRESHNESS GATE blocked normal email: %s", "; ".join(output_gaps))
            _send_freshness_gate_email(
                output_gaps,
                phase="Phase-2 output",
                remote_url=remote_state["remote_url"],
            )
            email_ok = False
        else:
            email_ok = run_step(
                "Send daily email report",
                lambda: exec_email(remote_url=remote_state["remote_url"]),
            )
        step_results.extend(
            [
                ("Sync paper portfolio", portfolio_ok),
                ("Update agent arena", arena_ok),
                ("Verify historical predictions", verify_ok),
                ("Build research output layer", research_ok),
                ("Refresh entry candidate table", entry_candidates_ok),
                ("Start web server & tunnel", web_ok),
                ("Output freshness gate", not output_gaps),
                ("Send daily email report", email_ok),
            ]
        )
    else:
        LOGGER.warning("SKIP  paper portfolio / verify / email because prediction step failed")
        step_results.extend(
            [
                ("Sync paper portfolio", False),
                ("Update agent arena", False),
                ("Verify historical predictions", False),
                ("Build research output layer", False),
                ("Refresh entry candidate table", False),
                ("Start web server & tunnel", False),
                ("Send daily email report", False),
            ]
        )

    step_results.append(("Commit daily products",
                         run_step("Commit daily products", exec_commit_daily_products)))
    return step_results


# 每日 runtime 產物提交白名單(2026-09-24,auto-commit hook 退役後的正式收檔管道)。
# 按副檔名比對:.tmp 天然不入列;模型 metadata(lgbm*/registry/model_selection/pin)
# 不在清單。禁止改成 git add -A(2026-09-21 掃入發布中暫存檔事故)。
DAILY_PRODUCT_COMMIT_GLOBS = (
    "frontend/static/*.json",
    "frontend/static/industry_news.html",
    "ml/data/*.csv",
    "ml/data/*.json",
    "ml/reports/*.csv",
    "ml/reports/*.md",
    "ml/reports/*.json",
    "ml/reports/*.html",
    "ml/models/dataA_predictions_*.csv",
)


def exec_commit_daily_products() -> None:
    """白名單提交每日排程產物(帳本/日曆/名單/儀表板/dataA 預測/報告)。

    UserPromptSubmit 的 auto-commit hook 已改純提醒(2026-09-23 審查 P1-6),
    排程產物由本步驟在 Phase-2 收尾明確選檔提交。best-effort:失敗只警告,
    不影響 pipeline 判定。若執行前 staging 區已有他人暫存內容,跳過以免劫持。"""
    import subprocess

    def _git(*args: str):
        return subprocess.run(["git", *args], cwd=BASE_DIR, capture_output=True,
                              text=True, encoding="utf-8", errors="replace")

    pre = _git("diff", "--cached", "--name-only")
    if (pre.stdout or "").strip():
        LOGGER.warning("daily products commit skipped: staging area not empty (%s)",
                       (pre.stdout or "").strip().splitlines()[0])
        return
    for pattern in DAILY_PRODUCT_COMMIT_GLOBS:
        _git("add", "--", pattern)
    staged = [n for n in (_git("diff", "--cached", "--name-only").stdout or "").splitlines() if n.strip()]
    if not staged:
        LOGGER.info("daily products commit: nothing to commit")
        return
    msg = f"chore(data): daily runtime products {date.today():%Y-%m-%d}"
    r = _git("commit", "-m", msg)
    if r.returncode == 0:
        LOGGER.info("daily products commit: %d files", len(staged))
        # best-effort 推遠端(2026-09-30 快照重接後防再漂移;離線/失敗只警告)
        p = _git("push", "origin", "main")
        if p.returncode == 0:
            LOGGER.info("daily products pushed to origin/main")
        else:
            LOGGER.warning("daily products push failed (will retry next day): %s",
                           (p.stderr or p.stdout or "")[-200:])
    else:
        _git("reset")  # 提交失敗時還原 staging,不留半套狀態
        LOGGER.warning("daily products commit failed: %s", (r.stderr or r.stdout or "")[-300:])


def _run_resume_after_special_status() -> list[tuple[str, bool]]:
    """Resume the post-special-status pipeline without full data refresh."""
    step_results: list[tuple[str, bool]] = []

    run_step("Stop web server & tunnel", stop_web_and_tunnel)
    _enable_two_stage_champion_defaults()

    special_ok = run_step("Refresh special stock status", exec_special_status_update)
    step_results.append(("Refresh special stock status", special_ok))
    special_report = _load_special_status_report()
    if not special_ok or _special_status_problem(special_report):
        LOGGER.warning("Resume blocked by SPECIAL_STATUS_STALE: %s", special_report)
        _send_special_status_email(special_report, kind="CRITICAL")
        return step_results + [
            ("Generate latest predictions", False),
            ("Sync paper portfolio", False),
            ("Update agent arena", False),
            ("Verify historical predictions", False),
            ("Build research output layer", False),
            ("Refresh entry candidate table", False),
            ("Start web server & tunnel", False),
            ("Send resumed email", False),
        ]

    macro_ok = run_step("Update macro strategy context", smart_update.exec_macro_strategy_context)
    step_results.append(("Update macro strategy context", macro_ok))

    predict_ok = run_step("Generate latest predictions", smart_update.exec_predict)
    step_results.append(("Generate latest predictions", predict_ok))

    if not predict_ok:
        LOGGER.warning("Resume stopped because prediction step failed")
        return step_results + [
            ("Sync paper portfolio", False),
            ("Update agent arena", False),
            ("Verify historical predictions", False),
            ("Build research output layer", False),
            ("Refresh entry candidate table", False),
            ("Start web server & tunnel", False),
            ("Send resumed email", False),
        ]

    portfolio_ok = run_step("Sync paper portfolio", exec_paper_portfolio)
    arena_run_ok = run_step("Update agent arena", exec_agent_arena)
    arena_ok = True
    if not arena_run_ok:
        LOGGER.warning(
            "Update agent arena failed during special-status resume; treating it as "
            "non-blocking because T+1/Dual recovery only requires refreshed special "
            "status, regenerated predictions, portfolio sync, web, and resumed email."
        )
    verify_ok = run_step("Verify historical predictions", smart_update.exec_verify)
    research_ok = run_step("Build research output layer", exec_research_output_layer)
    entry_candidates_ok = run_step("Refresh entry candidate table", exec_entry_candidate_refresh)

    remote_state: dict[str, str | None] = {"remote_url": None}
    web_ok = run_step("Start web server & tunnel", lambda: _capture_remote_url(remote_state))
    LOGGER.info("Waiting 10s for tunnel NAT to stabilize before SMTP")
    time.sleep(10)
    if entry_candidates_ok:
        daily_email_ok = run_step(
            "Send daily email report",
            lambda: exec_email(remote_url=remote_state["remote_url"]),
        )
    else:
        daily_email_ok = False
        _send_freshness_gate_email(
            ["canonical entry plan/dashboard refresh failed"],
            phase="Special-status resume output",
            remote_url=remote_state["remote_url"],
        )
    resumed_email_ok = run_step(
        "Send resumed special-status email",
        lambda: _send_special_status_email(
            _load_special_status_report(),
            kind="RESUMED",
            remote_url=remote_state["remote_url"],
        ),
    )

    step_results.extend(
        [
            ("Sync paper portfolio", portfolio_ok),
            ("Update agent arena", arena_ok),
            ("Verify historical predictions", verify_ok),
            ("Build research output layer", research_ok),
            ("Refresh entry candidate table", entry_candidates_ok),
            ("Start web server & tunnel", web_ok),
            ("Send daily email report", daily_email_ok),
            ("Send resumed email", resumed_email_ok),
        ]
    )
    return step_results


INTRADAY_BLOCK_START = (8, 30)
INTRADAY_BLOCK_END = (14, 30)


def _intraday_phase1_blocked(now: datetime, phase: str, allow_intraday: bool) -> str | None:
    """交易日盤中拒絕 Phase-1(2026-09-30 事故:盤中啟動後中止,twstock 已寫入 103 檔
    未收盤假棒,連鎖污染 DuckDB/預測/組合帳本/投資賽)。--force 不能繞過,只有
    --allow-intraday 能明示放行。回傳拒絕原因,None = 放行。"""
    if phase not in ("1", "all") or allow_intraday:
        return None
    if not is_taiwan_trading_day(now.date()):
        return None
    hm = (now.hour, now.minute)
    if INTRADAY_BLOCK_START <= hm < INTRADAY_BLOCK_END:
        return (f"Phase-1 during TW market hours ({now:%H:%M}, trading day {now:%Y-%m-%d}): "
                "Yahoo returns partial intraday bars that get written to 日K資料 and cannot be "
                "rolled back by aborting. Run after 14:30, or pass --allow-intraday explicitly.")
    return None


def main(argv: list[str] | None = None) -> int:
    _install_safe_stdio()
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
    parser.add_argument(
        "--resume-after-special-status",
        action="store_true",
        help="Refresh special status, then rerun prediction, portfolio, web, and email only.",
    )
    parser.add_argument(
        "--allow-intraday",
        action="store_true",
        help="Explicitly allow Phase-1 during TW market hours (writes partial bars; see AGENTS.md red lines).",
    )
    args = parser.parse_args(argv)

    blocked = _intraday_phase1_blocked(datetime.now(), args.phase, args.allow_intraday)
    if blocked:
        LOGGER.error("REFUSED %s", blocked)
        return 2

    lock_handle, existing_lock = acquire_lock(LOCK_PATH, "smart_update_auto")
    if lock_handle is None:
        pid = (existing_lock or {}).get("pid", "unknown")
        LOGGER.warning("SKIP  another smart update is already running (pid=%s)", pid)
        return 0

    now = datetime.now()
    phase_label = (
        "Resume after special status"
        if args.resume_after_special_status
        else {"1": "Phase-1 (TW data)", "2": "Phase-2 (predict)", "all": "Full"}[args.phase]
    )
    RUN_CONTEXT["remote_url"] = None
    RUN_CONTEXT["remote_tunnel_attempted"] = None
    RUN_CONTEXT["remote_tunnel_error"] = None
    RUN_CONTEXT["chipk_warning"] = None
    RUN_CONTEXT["agent_arena_warning"] = None
    RUN_CONTEXT["web_stop_warning"] = None
    LOGGER.info("===== %s started at %s =====", phase_label, now.strftime("%Y-%m-%d %H:%M:%S"))

    try:
        today = now.date()
        if not args.force and not is_weekday(today):
            LOGGER.info("SKIP  non-trading day")
            return 0
        if not args.force and args.phase == "1" and not is_taiwan_trading_day(today):
            LOGGER.info("SKIP  Phase-1 (TW data) TWSE holiday/non-trading day")
            return 0

        step_results: list[tuple[str, bool]] = []

        if args.resume_after_special_status:
            step_results.extend(_run_resume_after_special_status())
        else:
            if args.phase in ("1", "all"):
                if not args.force and not is_taiwan_trading_day(today):
                    LOGGER.info("SKIP  Phase-1 (TW data) TWSE holiday/non-trading day")
                else:
                    step_results.extend(_run_phase1_tw_data())

            if args.phase in ("2", "all"):
                step_results.extend(_run_phase2_predict())

        all_ok = all(success for _, success in step_results)
        degraded_steps = _special_status_degraded_steps() + _runtime_degraded_steps()
        elapsed = datetime.now() - now
        LOGGER.info(
            "===== %s finished in %.1f min | all_ok=%s =====",
            phase_label,
            elapsed.total_seconds() / 60.0,
            all_ok,
        )
        if degraded_steps:
            LOGGER.warning("%s degraded warnings: %s", phase_label, "; ".join(degraded_steps))
        _send_completion_ntfy(
            phase_label=phase_label,
            step_results=step_results,
            elapsed=elapsed,
            all_ok=all_ok,
            remote_url=RUN_CONTEXT.get("remote_url"),
            degraded_steps=degraded_steps,
        )
        return 0 if all_ok else 1
    finally:
        lock_handle.release()


if __name__ == "__main__":
    raise SystemExit(main())
