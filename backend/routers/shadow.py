"""Shadow Mode 對照 API — 提供 Production vs Shadow 即時比較數據。"""
from __future__ import annotations

import json
import os

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from ml.config import MODEL_DIR

SHADOW_REPORT_PATH = os.path.join(MODEL_DIR, "..", "reports", "shadow_mode_latest.json")

router = APIRouter(tags=["shadow"])


@router.get("/api/shadow/report")
def get_shadow_report():
    """回傳最新 shadow mode 對照報告。

    優先讀取 pipeline 產生的快取 JSON；若不存在則即時建構。
    """
    report_path = os.path.normpath(SHADOW_REPORT_PATH)

    # 嘗試讀快取
    if os.path.exists(report_path):
        try:
            with open(report_path, "r", encoding="utf-8") as fh:
                report = json.load(fh)
            return JSONResponse(content={"status": "success", **report})
        except Exception as exc:
            return JSONResponse(
                content={"status": "error", "message": f"讀取快取失敗: {exc}"},
                status_code=500,
            )

    # 快取不存在 → 即時建構
    try:
        from scripts.shadow_mode import build_shadow_mode_report
        report = build_shadow_mode_report()
        return JSONResponse(content={"status": "success", **report})
    except Exception as exc:
        return JSONResponse(
            content={"status": "not_ready", "message": f"Shadow Mode 尚未啟動或資料不足: {exc}"},
            status_code=404,
        )
