"""路徑常數與 DuckDB 設定."""

import os

BASE_DIR = r"F:\stock"

# Phase 1 資料目錄
DAILY_K_DIR = os.path.join(BASE_DIR, "日K資料")
REVENUE_DIR = os.path.join(BASE_DIR, "月營收")
FINANCIAL_DIR = os.path.join(BASE_DIR, "季報財務")
TDCC_DIR = os.path.join(BASE_DIR, "集保分散")
INDEX_DIR = os.path.join(BASE_DIR, "大盤指數")
FUND_CACHE_DIR = os.path.join(BASE_DIR, "法人快取")
CLEANED_DATA_DIR = os.path.join(BASE_DIR, "清理後資料")

# DuckDB
DUCKDB_PATH = os.path.join(BASE_DIR, "stock.duckdb")

# API 設定
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 500
CACHE_TTL_SECONDS = 300  # 5 分鐘
