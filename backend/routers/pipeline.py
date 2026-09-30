"""Pipeline / DB / 回測 / Log 相關 API endpoints.

從 app.py 抽出，集中管理「觸發類」端點。
全部受 admin auth middleware 保護（/api/pipeline/*、/api/db/*）；
/api/backtest/latest 為公開讀取。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime

from fastapi import APIRouter, Query

from backend.logging_config import LOG_DIR, pipeline_log

# Pipeline / DB / Logs 路由（受 admin middleware 保護）
router = APIRouter(tags=["pipeline"])

# 回測讀取端點走另一個 router（公開）
backtest_router = APIRouter(tags=["backtest"])

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ==============================================================================
# 公開：取得最新回測結果
# ==============================================================================
@backtest_router.get("/api/backtest/latest")
def backtest_latest():
    from ml.config import REPORT_DIR
    path = os.path.join(REPORT_DIR, "backtest_latest.json")
    if not os.path.exists(path):
        return {"error": "尚無回測結果，請先執行 python -m ml.backtest"}
    with open(path, "r", encoding="utf-8") as f:
        payload = json.load(f)
    from ml.backtest_metrics import enrich_backtest_payload
    return enrich_backtest_payload(payload)


# ==============================================================================
# Admin：觸發回測 (背景執行)
# ==============================================================================
@router.post("/api/pipeline/backtest")
def trigger_backtest(top_n: int = 10):
    pipeline_log.info(f"=== 手動觸發回測 (Top {top_n}) ===")
    try:
        subprocess.Popen(
            [sys.executable, "-m", "ml.backtest", "--top", str(top_n)],
            cwd=_BASE_DIR,
        )
        return {"status": "started", "top_n": top_n}
    except Exception as e:
        pipeline_log.error(f"回測啟動失敗: {e}")
        return {"status": "error", "detail": str(e)}


# ==============================================================================
# Admin：資料更新 / Ingest / DB 連線管理
# ==============================================================================
@router.post("/api/pipeline/update")
def trigger_update():
    """觸發資料更新 (twstock + valuation + news)"""
    pipeline_log.info("=== 手動觸發資料更新 ===")
    results: dict = {}
    for name, cmd in [
        ("twstock", [sys.executable, "twstock.py"]),
        ("valuation", [sys.executable, "scripts/backfill_valuation.py",
                       "--start-date", datetime.now().strftime("%Y-%m-%d")]),
        ("news", [sys.executable, "scripts/fetch_daily_news.py"]),
    ]:
        try:
            t0 = time.time()
            r = subprocess.run(
                cmd, cwd=_BASE_DIR,
                capture_output=True, text=True, timeout=600,
            )
            elapsed = time.time() - t0
            ok = r.returncode == 0
            results[name] = {"success": ok, "elapsed_sec": round(elapsed, 1)}
            pipeline_log.info(f"  {name}: {'OK' if ok else 'FAIL'} ({elapsed:.1f}s)")
            if not ok:
                pipeline_log.error(f"  {name} stderr: {r.stderr[:500]}")
        except Exception as e:
            results[name] = {"success": False, "error": str(e)}
            pipeline_log.error(f"  {name} exception: {e}")
    pipeline_log.info(f"=== 資料更新完成: {results} ===")
    return {"status": "done", "results": results}


@router.post("/api/pipeline/ingest")
def trigger_ingest():
    """CSV → DuckDB 匯入 (in-process)"""
    pipeline_log.info("=== 手動觸發 ingest ===")
    try:
        t0 = time.time()
        from backend.db.ingest import ingest_all
        results = ingest_all()
        elapsed = time.time() - t0
        pipeline_log.info(f"  ingest 完成: {results} ({elapsed:.1f}s)")
        return {"success": True, "elapsed_sec": round(elapsed, 1), "results": results}
    except Exception as e:
        pipeline_log.error(f"  ingest exception: {e}")
        return {"success": False, "error": str(e)}


@router.post("/api/db/release")
def release_db():
    """釋放 DuckDB 連線，讓外部 process 可以寫入。"""
    from backend.db.engine import close_conn
    close_conn()
    pipeline_log.info("DuckDB 連線已釋放 (via /api/db/release)")
    return {"success": True}


@router.post("/api/db/reconnect")
def reconnect_db():
    """重新建立 DuckDB 連線 (ingest 完成後呼叫)。"""
    from backend.db.engine import reconnect
    reconnect()
    pipeline_log.info("DuckDB 連線已重建 (via /api/db/reconnect)")
    return {"success": True}


@router.post("/api/pipeline/retrain")
def trigger_retrain():
    pipeline_log.info("=== 手動觸發模型重訓 ===")
    try:
        t0 = time.time()
        r = subprocess.run(
            [sys.executable, "-m", "ml.train"],
            cwd=_BASE_DIR,
            capture_output=True, text=True, timeout=1800,
        )
        elapsed = time.time() - t0
        ok = r.returncode == 0
        pipeline_log.info(f"  重訓: {'OK' if ok else 'FAIL'} ({elapsed:.1f}s)")
        if not ok:
            pipeline_log.error(f"  重訓 stderr: {r.stderr[:1000]}")
        return {"success": ok, "elapsed_sec": round(elapsed, 1)}
    except Exception as e:
        pipeline_log.error(f"  重訓 exception: {e}")
        return {"success": False, "error": str(e)}


@router.post("/api/pipeline/predict")
def trigger_predict():
    pipeline_log.info("=== 手動觸發預測 ===")
    try:
        t0 = time.time()
        r = subprocess.run(
            [sys.executable, "-m", "ml.predict"],
            cwd=_BASE_DIR,
            capture_output=True, text=True, timeout=600,
        )
        elapsed = time.time() - t0
        ok = r.returncode == 0
        pipeline_log.info(f"  預測: {'OK' if ok else 'FAIL'} ({elapsed:.1f}s)")
        return {"success": ok, "elapsed_sec": round(elapsed, 1)}
    except Exception as e:
        pipeline_log.error(f"  預測 exception: {e}")
        return {"success": False, "error": str(e)}


@router.get("/api/pipeline/logs")
def get_logs(
    log_type: str = Query("access", alias="type"),
    lines: int = 100,
):
    """查看 log 檔案最後 N 行 (type: access | error | pipeline)"""
    file_map = {
        "access": "web_access.log",
        "error": "web_error.log",
        "pipeline": "pipeline.log",
    }
    if log_type not in file_map:
        return {"error": f"不支援的 log 類型: {log_type}", "valid": list(file_map.keys())}

    fpath = os.path.join(LOG_DIR, file_map[log_type])
    if not os.path.exists(fpath):
        return {"type": log_type, "lines": [], "total": 0}

    with open(fpath, "r", encoding="utf-8") as f:
        all_lines = f.readlines()

    tail = all_lines[-lines:] if len(all_lines) > lines else all_lines
    return {
        "type": log_type,
        "file": file_map[log_type],
        "total_lines": len(all_lines),
        "showing": len(tail),
        "lines": [l.rstrip() for l in tail],
    }
