"""CSV → DuckDB 匯入模組.

讀取 Phase 1 產生的 CSV 檔，匯入 DuckDB 建立分析用表格。
"""

import os
import glob
import time

from backend.config import (
    DAILY_K_DIR, REVENUE_DIR, FINANCIAL_DIR,
    TDCC_DIR, INDEX_DIR,
)
from backend.db.engine import get_conn, execute


def _ingest_daily_k():
    """匯入所有日K CSV 到 daily_k 表 (用 glob 一次讀取，union_by_name 處理欄位差異)."""
    conn = get_conn()
    execute("DROP TABLE IF EXISTS daily_k")

    glob_path = os.path.join(DAILY_K_DIR, "*.csv").replace("\\", "/")

    csv_files = glob.glob(os.path.join(DAILY_K_DIR, "*.csv"))
    numeric_files = [
        f for f in csv_files
        if os.path.splitext(os.path.basename(f))[0].isdigit()
    ]
    if not numeric_files:
        print("[ingest] 無日K資料")
        return 0

    # 用 glob 一次讀入，union_by_name 自動處理不同欄位數
    # filename=true 取得來源檔名以擷取 Ticker
    safe_paths = [f.replace("\\", "/") for f in numeric_files]
    conn.execute(f"""
        CREATE TABLE daily_k AS
        SELECT
            regexp_extract(filename, '(\\d+)\\.csv', 1) AS Ticker,
            * EXCLUDE (filename)
        FROM read_csv_auto({safe_paths},
            header=true, ignore_errors=true, union_by_name=true, filename=true)
    """)

    count = len(numeric_files)

    # 建立索引
    try:
        conn.execute("CREATE INDEX idx_dk_ticker ON daily_k(Ticker)")
        conn.execute("CREATE INDEX idx_dk_date ON daily_k(Date)")
    except Exception:
        pass

    return count


def _ingest_revenue():
    """匯入月營收 CSV 到 revenue 表 (用 glob 一次讀取)."""
    conn = get_conn()
    execute("DROP TABLE IF EXISTS revenue")

    csv_files = glob.glob(os.path.join(REVENUE_DIR, "revenue_*.csv"))
    if not csv_files:
        print("[ingest] 無月營收資料")
        return 0

    # 部分檔案自帶 Ticker 欄位，部分沒有 → union_by_name 合併
    # 沒有 Ticker 的從檔名擷取
    safe_paths = [f.replace("\\", "/") for f in csv_files]
    conn.execute(f"""
        CREATE TABLE revenue AS
        SELECT
            COALESCE(
                CAST(Ticker AS VARCHAR),
                regexp_extract(filename, 'revenue_(\\w+)\\.csv', 1)
            ) AS Ticker,
            Date, Name, Monthly_Revenue, Cumulative_Revenue,
            YoY_pct_change, Cumulative_YoY_pct_change
        FROM read_csv_auto({safe_paths},
            header=true, ignore_errors=true, union_by_name=true, filename=true)
    """)

    return len(csv_files)


def _ingest_financials():
    """匯入季報財務 CSV 到 financials 表."""
    conn = get_conn()
    execute("DROP TABLE IF EXISTS financials")

    csv_files = sorted(glob.glob(os.path.join(FINANCIAL_DIR, "financial_*.csv")))
    if not csv_files:
        print("[ingest] 無季報資料")
        return 0

    safe_paths = [f.replace("\\", "/") for f in csv_files]
    conn.execute(f"""
        CREATE TABLE financials AS
        SELECT * FROM read_csv_auto({safe_paths},
            header=true, ignore_errors=true, union_by_name=true)
    """)

    return len(csv_files)


def _ingest_tdcc():
    """匯入集保分散 CSV 到 tdcc 表."""
    conn = get_conn()
    execute("DROP TABLE IF EXISTS tdcc")

    tdcc_file = os.path.join(TDCC_DIR, "tdcc_summary.csv").replace("\\", "/")
    if not os.path.exists(tdcc_file.replace("/", "\\")):
        # 嘗試正斜線路徑
        alt = os.path.join(TDCC_DIR, "tdcc_summary.csv")
        if not os.path.exists(alt):
            print("[ingest] 無集保分散資料")
            return 0

    try:
        conn.execute(f"""
            CREATE TABLE tdcc AS
            SELECT * FROM read_csv_auto('{tdcc_file}', header=true, ignore_errors=true)
        """)
        return 1
    except Exception as e:
        print(f"[ingest] 集保 失敗: {e}")
        return 0


def _ingest_indices():
    """匯入大盤指數 CSV 到 indices 表."""
    conn = get_conn()
    execute("DROP TABLE IF EXISTS indices")

    csv_files = glob.glob(os.path.join(INDEX_DIR, "index_*.csv"))
    if not csv_files:
        print("[ingest] 無大盤指數資料")
        return 0

    safe_paths = [f.replace("\\", "/") for f in csv_files]
    conn.execute(f"""
        CREATE TABLE indices AS
        SELECT
            regexp_extract(filename, 'index_(\\w+)\\.csv', 1) AS Index_Name,
            * EXCLUDE (filename)
        FROM read_csv_auto({safe_paths},
            header=true, ignore_errors=true, union_by_name=true, filename=true)
    """)

    return len(csv_files)


def _build_stock_list():
    """從 daily_k + financials 建立股票清單表."""
    conn = get_conn()
    execute("DROP TABLE IF EXISTS stock_list")

    conn.execute("""
        CREATE TABLE stock_list AS
        WITH ranked AS (
            SELECT
                Ticker, Date, Close, Volume,
                ROW_NUMBER() OVER (PARTITION BY Ticker ORDER BY Date DESC) AS rn,
                LAG(Close) OVER (PARTITION BY Ticker ORDER BY Date) AS prev_close
            FROM daily_k
        ),
        latest_price AS (
            SELECT
                Ticker,
                Close AS Last_Close,
                Volume AS Last_Volume,
                Date AS Last_Date,
                CASE WHEN prev_close > 0
                     THEN ROUND((Close - prev_close) / prev_close * 100, 2)
                     ELSE NULL END AS Last_Change_Pct
            FROM ranked
            WHERE rn = 1
        ),
        names AS (
            SELECT DISTINCT
                CAST(Ticker AS VARCHAR) AS Ticker,
                Name
            FROM financials
            WHERE Name IS NOT NULL
        )
        SELECT
            lp.Ticker,
            COALESCE(n.Name, '') AS Name,
            lp.Last_Close,
            lp.Last_Volume,
            lp.Last_Date,
            lp.Last_Change_Pct
        FROM latest_price lp
        LEFT JOIN names n ON lp.Ticker = n.Ticker
    """)

    try:
        conn.execute("CREATE INDEX idx_sl_ticker ON stock_list(Ticker)")
    except Exception:
        pass


def ingest_all():
    """執行全部匯入，回傳統計摘要."""
    t0 = time.time()

    results = {}
    print("[ingest] 匯入日K資料...")
    results["daily_k"] = _ingest_daily_k()

    print("[ingest] 匯入月營收...")
    results["revenue"] = _ingest_revenue()

    print("[ingest] 匯入季報財務...")
    results["financials"] = _ingest_financials()

    print("[ingest] 匯入集保分散...")
    results["tdcc"] = _ingest_tdcc()

    print("[ingest] 匯入大盤指數...")
    results["indices"] = _ingest_indices()

    print("[ingest] 建立股票清單...")
    _build_stock_list()

    elapsed = time.time() - t0
    print(f"[ingest] 完成! 耗時 {elapsed:.1f}s — {results}")
    return results
