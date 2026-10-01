"""每日收盤後自動更新資料 + 重新預測

使用方式:
    python scripts/daily_pipeline.py           # 更新資料 + 預測
    python scripts/daily_pipeline.py --predict-only  # 只做預測
    python scripts/daily_pipeline.py --retrain  # 更新 + 重新訓練模型 + 預測

建議排程：每個交易日 17:00 後執行（台股收盤 13:30，資料約 15:00-16:00 更新完畢）
"""
import os
import sys
import argparse
import json
import logging
from datetime import datetime, date

BASE_DIR = os.environ.get("STOCK_BASE_DIR") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from scripts.task_lock import acquire_lock
from scripts.job_runner import run_job
from scripts.taiwan_trading_calendar import is_taiwan_trading_day

# Pipeline log
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


LOG_DIR = os.path.join(BASE_DIR, "logs")
os.makedirs(LOG_DIR, exist_ok=True)
LOCK_PATH = os.path.join(LOG_DIR, "daily_pipeline.lock")


def _install_safe_stdio(fallback_path: str | None = None) -> None:
    fallback = fallback_path or os.path.join(
        LOG_DIR,
        f"daily_pipeline_stdio_{datetime.now().strftime('%Y%m%d')}.log",
    )
    os.environ["STOCK_SCHEDULED_STDIO_LOG"] = fallback
    sys.stdout = _SafeTextStream(sys.stdout, fallback_path=fallback, label="stdout")
    sys.stderr = _SafeTextStream(sys.stderr, fallback_path=fallback, label="stderr")


_log_handler = logging.FileHandler(
    os.path.join(LOG_DIR, "pipeline.log"), encoding="utf-8", mode="a"
)
_log_handler.setFormatter(logging.Formatter(
    "%(asctime)s | %(levelname)-5s | %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
))
pipeline_log = logging.getLogger("pipeline")
pipeline_log.setLevel(logging.INFO)
if not pipeline_log.handlers:
    pipeline_log.addHandler(_log_handler)
    pipeline_log.addHandler(_SafeStreamHandler())

SHADOW_DB_PATH = os.path.join(BASE_DIR, "paper_portfolio_shadow_v23.db")
SHADOW_OUTPUT_PREFIX = "predictions_shadow_v23"
V4_RANK15_DB_PATH = os.path.join(BASE_DIR, "paper_portfolio_v4_rank15.db")
PIPELINE_STATE: dict[str, str] = {}


def is_trading_day():
    """判斷今日是否為台股交易日（排除週末與 TWSE 休市日）"""
    return is_taiwan_trading_day(date.today())


def run_step(name, func):
    """執行步驟並處理例外"""
    print(f"\n{'='*60}")
    print(f"  {name} ({datetime.now().strftime('%H:%M:%S')})")
    print(f"{'='*60}")
    pipeline_log.info(f"開始: {name}")
    try:
        result = func()
        if result is False:
            raise RuntimeError("step returned False")
        pipeline_log.info(f"完成: {name}")
        return True
    except Exception as e:
        pipeline_log.error(f"失敗: {name} — {e}")
        print(f"  [錯誤] {name}: {e}")
        return False


def run_data_update():
    """執行 twstock.py 更新最新資料（Step 1-7）"""
    run_job("twstock",
            [sys.executable, os.path.join(BASE_DIR, "twstock.py")],
            timeout="twstock_full", cwd=BASE_DIR, raise_on_fail=True)


def run_daily_bar_verification():
    """Apply the same mandatory official-price reconciliation as Phase-1."""
    run_job("verify_official_daily_bars",
            [sys.executable, os.path.join(BASE_DIR, "scripts", "verify_otc_bars.py")],
            timeout="data_fetch", cwd=BASE_DIR, raise_on_fail=True)


def run_valuation_update():
    """更新今日估值資料 (PE/PB/殖利率)"""
    today_str = date.today().strftime("%Y-%m-%d")
    run_job("valuation",
            [sys.executable,
             os.path.join(BASE_DIR, "scripts", "backfill_valuation.py"),
             "--start-date", today_str],
            timeout="valuation", cwd=BASE_DIR, raise_on_fail=True)


def run_news_update():
    """更新 MOPS 重大訊息公告 (消息面)"""
    # Attempt both independent sources, but preserve either failure in the
    # enclosing step ledger. run_job also sends the existing failure alert.
    results = [run_job("news",
            [sys.executable, os.path.join(BASE_DIR, "scripts", "fetch_daily_news.py")],
            timeout="news", cwd=BASE_DIR, raise_on_fail=False),
        run_job("finnhub_news_sentiment",
            [sys.executable, os.path.join(BASE_DIR, "scripts", "fetch_finnhub_news_sentiment.py")],
            timeout="news", cwd=BASE_DIR, raise_on_fail=False)]
    failed = [result for result in results if result.get("status") != "ok"]
    if failed:
        detail = "; ".join(
            f"{result.get('name')}: {result.get('status')} rc={result.get('returncode')}"
            for result in failed
        )
        raise RuntimeError(f"News sources failed: {detail}")


def run_disposition_update():
    """更新處置股名單 (每日，FinMind backer)"""
    run_job("disposition",
            [sys.executable, os.path.join(BASE_DIR, "scripts", "fetch_disposition.py")],
            timeout="news", cwd=BASE_DIR, raise_on_fail=True)


def run_public_market_context():
    """更新官方公開市場補強資料 (read-only risk context)."""
    run_job("public_market_context",
            [sys.executable, os.path.join(BASE_DIR, "scripts", "fetch_public_market_context.py")],
            timeout="news", cwd=BASE_DIR, raise_on_fail=True)


def run_tdcc_update():
    """每次資料更新都嘗試最新 TDCC，接住週末公布與先前失敗的期別。"""
    # Match smart_update_auto.exec_tdcc_if_needed: Friday-only misses the
    # official weekend release. The fetcher validates the source's own date.
    run_job("tdcc",
            [sys.executable, os.path.join(BASE_DIR, "scripts", "fetch_tdcc_weekly.py")],
            timeout="data_fetch", cwd=BASE_DIR, raise_on_fail=True)


def run_ingest():
    """將 CSV 資料匯入 DuckDB (供網頁讀取)"""
    from scripts.smart_update import exec_ingest
    # Use the canonical web release / transaction / reconnect path.
    exec_ingest()


def _resolve_lgbm_device(env_name: str, default: str = "gpu") -> str:
    value = os.environ.get(env_name, default).strip().lower()
    return value if value in {"gpu", "cpu"} else default


def run_retrain(v2_only: bool = False):
    """Retrain production-adjacent ML artifacts.

    Weekly V2 runs must not be blocked by the slower legacy classifier
    walk-forward, so V2 is trained first and the legacy branch can be skipped.
    """
    v2_only = v2_only or _env_flag("DAILY_PIPELINE_RETRAIN_V2_ONLY")

    from ml.config import LGBM_NUM_THREADS

    print(f"  LightGBM thermal-safe cpu_threads={LGBM_NUM_THREADS}")
    pipeline_log.info("LightGBM thermal-safe cpu_threads=%s", LGBM_NUM_THREADS)

    from scripts.train_v2 import train_v2

    v2_device = _resolve_lgbm_device("V2_RETRAIN_DEVICE", default="gpu")
    pipeline_log.info("V2 retrain starting: device=%s cpu_threads=%s", v2_device, LGBM_NUM_THREADS)
    v2_meta = train_v2(device=v2_device, cpu_threads=LGBM_NUM_THREADS)
    if not v2_meta:
        raise RuntimeError("V2 retrain produced no model artifact")

    if v2_only:
        print("  Legacy classifier retrain skipped (--retrain-v2-only).")
        pipeline_log.info("Legacy classifier retrain skipped by --retrain-v2-only")
        return

    from ml.train import run_training

    run_training()


def _run_backtest_legacy_original():
    """模擬投資回測"""
    from ml.backtest import run_backtest as _run_backtest
    _run_backtest(top_n=10)


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def run_backtest():
    if _env_flag("DAILY_PIPELINE_RETRAIN_V2_ONLY"):
        print("  Legacy backtest skipped (DAILY_PIPELINE_RETRAIN_V2_ONLY=1).")
        pipeline_log.info("Legacy backtest skipped by DAILY_PIPELINE_RETRAIN_V2_ONLY")
        return
    _run_backtest_legacy_original()


def run_predict():
    """執行預測"""
    from ml.predict import run_prediction
    from scripts.smart_update_auto import _enable_two_stage_champion_defaults

    _enable_two_stage_champion_defaults()
    result = run_prediction(
        top_n=30,
        model_slot="production",
        output_prefix="predictions",
        save_snapshot=True,
    )
    PIPELINE_STATE["production_prediction_path"] = result["output_path"]


def run_t1_predict():
    """T+1 次日動能模型推論（餵給 V4 shadow 用）。"""
    from ml.predict_t1 import predict_t1_all
    pred_df, meta = predict_t1_all(save_csv=True, verbose=True)
    PIPELINE_STATE["t1_prediction_path"] = meta.get("prediction_file", "")


def run_unified_signals():
    """Build the single-source-of-truth unified signal artifact."""
    from ml.market_regime import update_market_regime_report
    from scripts.build_unified_signals import build_unified_signals
    from scripts.smart_update_auto import _enable_two_stage_champion_defaults

    _enable_two_stage_champion_defaults()

    production_prediction_path = PIPELINE_STATE.get("production_prediction_path")
    t1_prediction_path = PIPELINE_STATE.get("t1_prediction_path")
    if not production_prediction_path:
        raise RuntimeError("production prediction file is missing; cannot build unified signals")
    if not t1_prediction_path:
        raise RuntimeError("T+1 prediction file is missing; cannot build unified signals")

    update_market_regime_report()
    result = build_unified_signals(
        prediction_20d_path=production_prediction_path,
        prediction_t1_path=t1_prediction_path,
        verbose=True,
    )
    PIPELINE_STATE["unified_signal_path"] = result.output_path


def run_unified_portfolio():
    """Sync the unified sidecar portfolio ledger."""
    from scripts.update_unified_portfolio import sync_unified_portfolio

    unified_signal_path = PIPELINE_STATE.get("unified_signal_path")
    if not unified_signal_path:
        raise RuntimeError("unified signal file is missing; cannot sync unified portfolio")

    result = sync_unified_portfolio(signal_files=[unified_signal_path])
    PIPELINE_STATE["unified_portfolio_db_path"] = result["db_path"]


def run_v4_rank15_shadow():
    """V4 rank-15 shadow: V3 sector-capped Top 30 → 用 T+1 重新排序 → Top 15。

    必須在 run_predict 與 run_t1_predict 之後執行。獨立帳本，
    不污染 production / V2.3 shadow 的部位。
    """
    from scripts.build_v4_rank15_shadow import run_full
    if not PIPELINE_STATE.get("production_prediction_path"):
        raise RuntimeError("V3 production prediction missing; cannot build V4 shadow")
    if not PIPELINE_STATE.get("t1_prediction_path"):
        raise RuntimeError("T+1 prediction missing; cannot build V4 shadow")
    result = run_full(pred_date=None, verbose=True)
    PIPELINE_STATE["v4_rank15_db_path"] = result["db_path"]


def run_shadow_predict():
    """執行 shadow mode 預測。"""
    from ml.predict import run_prediction
    result = run_prediction(
        top_n=30,
        model_slot="shadow",
        output_prefix=SHADOW_OUTPUT_PREFIX,
        save_snapshot=False,
    )
    PIPELINE_STATE["shadow_prediction_path"] = result["output_path"]


def run_paper_portfolio():
    """Lock the daily Top 30 into the paper portfolio ledger."""
    from scripts.update_paper_portfolio import sync_paper_portfolio
    prediction_path = PIPELINE_STATE.get("production_prediction_path")
    if not prediction_path:
        raise RuntimeError("production prediction file is missing in this pipeline run")
    sync_paper_portfolio(
        prediction_file=prediction_path,
        top_n=30,
        rule_version="production-main",
    )


def run_shadow_paper_portfolio():
    """Lock the shadow-mode Top 30 into its own isolated ledger."""
    from scripts.update_paper_portfolio import sync_paper_portfolio
    prediction_path = PIPELINE_STATE.get("shadow_prediction_path")
    if not prediction_path:
        raise RuntimeError("shadow prediction file is missing in this pipeline run")
    sync_paper_portfolio(
        prediction_file=prediction_path,
        db_path=SHADOW_DB_PATH,
        top_n=30,
        rule_version="shadow-v2.3",
    )


def run_shadow_report():
    """Build the latest production vs shadow monitor report."""
    from scripts.shadow_mode import build_shadow_mode_report, _print_report
    if not PIPELINE_STATE.get("production_prediction_path"):
        raise RuntimeError("production prediction is missing; skip shadow report")
    if not PIPELINE_STATE.get("shadow_prediction_path"):
        raise RuntimeError("shadow prediction is missing; skip shadow report")

    report = build_shadow_mode_report(
        production_db_path=os.path.join(BASE_DIR, "paper_portfolio.db"),
        shadow_db_path=SHADOW_DB_PATH,
    )
    _print_report(report)


def run_verify():
    """驗證歷史預測的實際表現"""
    from scripts.verify_predictions import main as verify_main
    verify_main()


def run_replay_monitor():
    """Build the rolling replay health report for recent production predictions."""
    from scripts.daily_replay_monitor import generate_report

    generate_report(days=10, top_n=30)


def run_email_report():
    """寄送每日 ML 預測與帳本觀察信件。"""
    from scripts.send_daily_email import send_latest_email
    send_latest_email()


def run_agent_arena():
    """Update the additive multi-agent paper investing contest."""
    retained = os.environ.get("WEEKLY_RETRAIN_WEB_PORT_RETAINED", "").strip().lower()
    if retained in {"1", "true", "yes", "on"}:
        message = "Agent Arena skipped because weekly retrain retained an existing web/DuckDB owner."
        print(f"  {message}")
        pipeline_log.warning(message)
        return

    from scripts import smart_update
    from scripts.agent_arena import run_daily_competition

    # Ingest reconnects the web's DuckDB handle mid-run; without a fresh release the
    # arena's read-only open fails and its snapshot copy hits WinError 32
    # (weekly retrain 9/12, 9/26). Same release as smart_update_auto.exec_agent_arena.
    if smart_update._api_post("/api/db/release", timeout=30):
        pipeline_log.info("agent arena: web server released its DuckDB connection")
    run_daily_competition()


def run_research_output_layer():
    """Build PM-readable research memos from existing local outputs."""
    from scripts.research_output_layer import (
        build_research_output_pack,
        write_research_output_artifacts,
    )

    pack = build_research_output_pack()
    paths = write_research_output_artifacts(pack)
    PIPELINE_STATE["research_output_layer_path"] = str(paths.get("research_output_layer_md", ""))


def run_entry_candidate_refresh():
    """Refresh the conditional entry-candidate table consumed by the daily email."""
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


def main() -> int:
    _install_safe_stdio()
    from ml.model_selection import has_shadow_model_slot

    parser = argparse.ArgumentParser(description="每日自動更新 + 預測 Pipeline")
    parser.add_argument("--predict-only", action="store_true", help="只做預測，不更新資料")
    parser.add_argument("--retrain", action="store_true", help="重新訓練模型")
    parser.add_argument("--force", action="store_true", help="非交易日也強制執行")
    parser.add_argument(
        "--retrain-v2-only",
        action="store_true",
        help="Train V2 only; skip legacy classifier retrain/backtest.",
    )
    args = parser.parse_args()
    if getattr(args, "retrain_v2_only", False):
        os.environ["DAILY_PIPELINE_RETRAIN_V2_ONLY"] = "1"

    lock_handle, existing_lock = acquire_lock(LOCK_PATH, "daily_pipeline", stale_after_seconds=24 * 60 * 60)
    if lock_handle is None:
        pid = (existing_lock or {}).get("pid", "unknown")
        pipeline_log.warning(f"SKIP: daily pipeline is already running (pid={pid})")
        print("另一個 daily pipeline 已在執行，略過本次啟動。")
        return 0

    try:
        if not args.force and not is_trading_day():
            print("今日非交易日，跳過。使用 --force 強制執行。")
            return 0

        start = datetime.now()
        pipeline_log.info(f"===== 每日 Pipeline 啟動 ({start.strftime('%Y-%m-%d %H:%M')}) =====")
        print(f"{'='*60}")
        print(f"  台股每日 Pipeline - {start.strftime('%Y-%m-%d %H:%M')}")
        print(f"{'='*60}")

        step_status = {}
        blocked_steps = []
        data_ready = True

        def derived_step(name, func, *, requires=()):
            failed_dependencies = [key for key in requires if not step_status.get(key)]
            if not data_ready or failed_dependencies:
                pipeline_log.error("BLOCKED: %s — source/ingest ready=%s; failed dependencies=%s",
                                   name, data_ready, failed_dependencies)
                blocked_steps.append(name)
                return False
            return run_step(name, func)

        if not args.predict_only:
            step_status["日K/法人/融資券/營收"] = run_step("更新台股日K/法人/融資券/營收", run_data_update)
            step_status["OfficialDailyBars"] = run_step("核對官方日K終盤值", run_daily_bar_verification)
            step_status["估值(PE/PB/殖利率)"] = run_step("更新估值資料 (PE/PB/殖利率)", run_valuation_update)
            step_status["MOPS重大訊息"] = run_step("更新 MOPS 重大訊息 (消息面)", run_news_update)
            step_status["處置股名單"] = run_step("更新處置股名單", run_disposition_update)
            step_status["公開市場補強"] = run_step("更新官方公開市場補強資料", run_public_market_context)
            step_status["TDCC集保分散"] = run_step("更新 TDCC 集保分散 (最新可得期別)", run_tdcc_update)
            data_ready = all(step_status[key] for key in (
                "日K/法人/融資券/營收", "OfficialDailyBars", "估值(PE/PB/殖利率)",
                "處置股名單", "TDCC集保分散",
            ))
            step_status["DuckDB匯入"] = derived_step("匯入資料到 DuckDB", run_ingest)
            data_ready = data_ready and step_status["DuckDB匯入"]

        if args.retrain:
            step_status["模型訓練"] = derived_step("重新訓練預測模型", run_retrain)
            step_status["回測"] = derived_step("模擬投資回測", run_backtest, requires=("模型訓練",))

        shadow_enabled = has_shadow_model_slot()

        step_status["預測"] = derived_step("產生預測", run_predict)
        step_status["T+1預測"] = derived_step("產生 T+1 次日動能預測", run_t1_predict)
        step_status["UnifiedSignals"] = derived_step("Build unified signals", run_unified_signals, requires=("預測", "T+1預測"))
        step_status["UnifiedPortfolio"] = derived_step("Sync unified portfolio", run_unified_portfolio, requires=("UnifiedSignals",))
        step_status["實戰觀測帳本"] = derived_step("更新實戰觀測帳本", run_paper_portfolio, requires=("預測",))
        step_status["AgentArena"] = derived_step("更新 Agent 投資模擬賽", run_agent_arena, requires=("預測",))
        step_status["ResearchOutputLayer"] = derived_step("Build research output layer", run_research_output_layer, requires=("預測", "UnifiedSignals"))
        step_status["EntryCandidateRefresh"] = derived_step(
            "Refresh entry candidate table",
            run_entry_candidate_refresh,
            requires=("預測",),
        )
        if shadow_enabled:
            step_status["Shadow預測"] = derived_step("產生 Shadow Mode 預測", run_shadow_predict)
            step_status["Shadow帳本"] = derived_step("更新 Shadow Mode 帳本", run_shadow_paper_portfolio, requires=("Shadow預測",))
            step_status["Shadow監控"] = derived_step("產生 Shadow Mode 監控報告", run_shadow_report, requires=("預測", "Shadow預測", "Shadow帳本"))
        step_status["V4Rank15Shadow"] = derived_step(
            "V4 Rank15 Shadow (V3 Top30 → T+1 排序 → Top15)", run_v4_rank15_shadow, requires=("預測", "T+1預測")
        )
        step_status["預測驗證"] = derived_step("驗證歷史預測表現", run_verify)
        step_status["Replay監控"] = derived_step("Build replay monitor report", run_replay_monitor)
        step_status["Email推播"] = derived_step(
            "寄送每日 ML 預測與帳本觀察", run_email_report,
            requires=("預測", "T+1預測", "UnifiedSignals", "UnifiedPortfolio", "實戰觀測帳本", "ResearchOutputLayer", "EntryCandidateRefresh"),
        )

        elapsed = datetime.now() - start

        freshness_path = os.path.join(BASE_DIR, "ml", "models", "pipeline_status.json")
        safe_step_status = {}
        for index, (key, value) in enumerate(step_status.items(), start=1):
            safe_key = "".join(
                ch if ch.isascii() and (ch.isalnum() or ch in "_+-") else "_"
                for ch in str(key)
            ).strip("_")
            safe_step_status[safe_key or f"step_{index}"] = value
        freshness = {
            "last_run": start.strftime("%Y-%m-%d %H:%M:%S"),
            "elapsed_min": round(elapsed.total_seconds() / 60, 1),
            "steps": {k: ("ok" if v else "failed") for k, v in safe_step_status.items()},
            "all_ok": all(step_status.values()),
            "blocked_steps": blocked_steps,
        }
        with open(freshness_path, "w", encoding="utf-8") as f:
            json.dump(freshness, f, ensure_ascii=False, indent=2)

        pipeline_log.info(f"===== Pipeline 完成 (耗時 {elapsed.total_seconds()/60:.1f} 分鐘) =====")
        print(f"\n全部完成，耗時 {elapsed.total_seconds()/60:.1f} 分鐘")
        # Propagate recorded failures only after metadata is complete. Returning
        # here still executes lock cleanup and the weekly wrapper's restoration.
        return 0 if freshness["all_ok"] else 1
    finally:
        lock_handle.release()


if __name__ == "__main__":
    raise SystemExit(main())
