"""集中管理 web logger，讓 app.py 與 routers 都從同一處取得。

提供三個 logger：
- ``access_log``    — 一般 API 請求 (web_access.log)
- ``error_log``     — 例外 / 4xx 5xx (web_error.log)
- ``pipeline_log``  — 資料更新 / 模型訓練 / 手動觸發 (pipeline.log)
"""
from __future__ import annotations

import logging
import os

LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")
os.makedirs(LOG_DIR, exist_ok=True)


def setup_logger(name: str, filename: str, level: int = logging.INFO) -> logging.Logger:
    """建立帶有檔案 + console handler 的 logger（idempotent）。"""
    logger = logging.getLogger(name)
    logger.setLevel(level)
    if logger.handlers:
        return logger

    fh = logging.FileHandler(
        os.path.join(LOG_DIR, filename),
        encoding="utf-8",
        mode="a",
    )
    fh.setLevel(level)
    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-5s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    ch = logging.StreamHandler()
    ch.setLevel(level)
    ch.setFormatter(fmt)
    logger.addHandler(ch)
    return logger


access_log = setup_logger("web.access", "web_access.log")
error_log = setup_logger("web.error", "web_error.log", logging.ERROR)
pipeline_log = setup_logger("pipeline", "pipeline.log")
