"""CSV → DuckDB 匯入模組.

讀取 Phase 1 產生的 CSV 檔，匯入 DuckDB 建立分析用表格。
"""

import os
import glob
import re
import time
import uuid
import csv

from backend.config import (
    DAILY_K_DIR, REVENUE_DIR, FINANCIAL_DIR,
    TDCC_DIR, INDEX_DIR, BASE_DIR,
)
from backend.db.engine import get_conn, execute
from ml.universe import load_retired_tickers


class IngestValidationError(RuntimeError):
    """Raised when a staged import violates a production data contract."""


_NUMERIC_TYPES = {
    "TINYINT", "SMALLINT", "INTEGER", "BIGINT", "HUGEINT",
    "UTINYINT", "USMALLINT", "UINTEGER", "UBIGINT",
    "FLOAT", "DOUBLE", "REAL", "DECIMAL",
}


_TABLE_CONTRACTS = {
    "daily_k": {
        "required": {"Ticker", "Date", "Close", "Volume"},
        "numeric": {"Close", "Volume"},
        "non_null": {"Ticker", "Date", "Close"},
        "freshness": "MAX(CAST(Date AS VARCHAR))",
    },
    "revenue": {
        "required": {"Ticker", "Date", "Monthly_Revenue"},
        "numeric": {"Monthly_Revenue"},
        "non_null": {"Ticker", "Date"},
        "freshness": "MAX(CAST(Date AS VARCHAR))",
    },
    "financials": {
        "required": {"Ticker", "Year", "Season", "Operating_Margin_Pct"},
        "numeric": {"Year", "Season", "Operating_Margin_Pct"},
        "non_null": {"Ticker", "Year", "Season"},
        "freshness": "MAX(CAST(Year AS BIGINT) * 10 + CAST(Season AS BIGINT))",
    },
    "tdcc": {
        "required": {"Ticker", "Date", "Retail_Pct", "Whale_Pct"},
        "numeric": {"Retail_Pct", "Whale_Pct"},
        "non_null": {"Ticker", "Date"},
        "freshness": "MAX(CAST(Date AS VARCHAR))",
    },
    "indices": {
        "required": {"Index_Name", "Date", "Close"},
        "numeric": {"Close"},
        "non_null": {"Index_Name", "Date", "Close"},
        "freshness": "MAX(CAST(Date AS VARCHAR))",
    },
}


def ensure_narrative_schema(conn=None):
    """Create shadow narrative metadata tables without touching production gates."""
    db = conn or get_conn()
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS news_articles (
            news_id TEXT PRIMARY KEY,
            source TEXT,
            published_at TIMESTAMP,
            title TEXT,
            link TEXT,
            snippet TEXT,
            content_hash TEXT,
            fetched_at TIMESTAMP
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS news_ticker_links (
            news_id TEXT,
            ticker TEXT,
            match_method TEXT,
            confidence REAL,
            created_at TIMESTAMP
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS news_narrative_labels (
            news_id TEXT,
            label TEXT,
            matched_keywords TEXT,
            created_at TIMESTAMP
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS daily_ticker_narrative_features (
            date DATE,
            ticker TEXT,
            active_labels TEXT,
            heat_3d REAL,
            heat_14d REAL,
            decay_slope REAL,
            top_label TEXT,
            label_age_days INTEGER,
            article_count_7d INTEGER
        )
        """
    )
    for statement in (
        "CREATE INDEX IF NOT EXISTS idx_news_articles_published ON news_articles(published_at)",
        "CREATE INDEX IF NOT EXISTS idx_news_articles_hash ON news_articles(content_hash)",
        "CREATE INDEX IF NOT EXISTS idx_news_ticker_links_news ON news_ticker_links(news_id)",
        "CREATE INDEX IF NOT EXISTS idx_news_ticker_links_ticker ON news_ticker_links(ticker)",
        "CREATE INDEX IF NOT EXISTS idx_news_narrative_labels_news ON news_narrative_labels(news_id)",
        "CREATE INDEX IF NOT EXISTS idx_daily_narrative_ticker_date ON daily_ticker_narrative_features(ticker, date)",
    ):
        try:
            db.execute(statement)
        except Exception:
            pass
    db.commit()


def _ensure_ingest_meta_schema(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ingest_meta (
            table_name VARCHAR PRIMARY KEY,
            rows BIGINT,
            ingested_at TIMESTAMP,
            freshness_value VARCHAR,
            generation_id VARCHAR
        )
        """
    )
    conn.execute("ALTER TABLE ingest_meta ADD COLUMN IF NOT EXISTS freshness_value VARCHAR")
    conn.execute("ALTER TABLE ingest_meta ADD COLUMN IF NOT EXISTS generation_id VARCHAR")


def _column_types(conn, table: str) -> dict[str, str]:
    return {row[0]: str(row[1]).upper() for row in conn.execute(f"DESCRIBE {table}").fetchall()}


def _is_numeric_type(type_name: str) -> bool:
    base = type_name.split("(", 1)[0]
    return base in _NUMERIC_TYPES


def _validate_stage_schema(conn, stage: str, logical_table: str) -> None:
    contract = _TABLE_CONTRACTS[logical_table]
    columns = _column_types(conn, stage)
    missing = sorted(contract["required"] - set(columns))
    if missing:
        raise IngestValidationError(
            f"[ingest] {logical_table} schema drift: missing required columns {missing}"
        )

    wrong_types = sorted(
        col for col in contract["numeric"]
        if not _is_numeric_type(columns[col])
    )
    if wrong_types:
        details = {col: columns[col] for col in wrong_types}
        raise IngestValidationError(
            f"[ingest] {logical_table} schema drift: numeric columns changed type {details}"
        )

    null_predicate = " OR ".join(f'"{col}" IS NULL' for col in sorted(contract["non_null"]))
    null_rows = conn.execute(f"SELECT COUNT(*) FROM {stage} WHERE {null_predicate}").fetchone()[0]
    if null_rows:
        raise IngestValidationError(
            f"[ingest] {logical_table} contains {null_rows:,} rows with null required keys"
        )


def _row_guard(
    conn,
    table: str,
    *,
    logical_table: str | None = None,
    expected_freshness: str | None = None,
    generation_id: str | None = None,
) -> tuple[int, str]:
    """列數守門(2026-07-16 全系統審查後建立,對應 ingest 層五項確認發現)。

    - 記錄每表列數與時間戳到 ingest_meta(跨次持久,可稽核「混合世代」)
    - 新列數 < 前次 70% → 拋錯讓 pipeline 大聲失敗(ignore_errors 靜默丟列的解毒劑);
      故意縮表(清垃圾列等)用環境變數 INGEST_ALLOW_SHRINK=1 放行一次
    """
    logical_table = logical_table or table
    _ensure_ingest_meta_schema(conn)
    rows = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    if rows <= 0:
        raise IngestValidationError(f"[ingest] {logical_table} staged table is empty")

    prev = conn.execute(
        "SELECT rows, freshness_value FROM ingest_meta WHERE table_name = ?", [logical_table]
    ).fetchone()
    prev_rows = prev[0] if prev else None
    prev_freshness = str(prev[1]) if prev and prev[1] is not None else None

    freshness_expr = _TABLE_CONTRACTS[logical_table]["freshness"]
    freshness_raw = conn.execute(f"SELECT {freshness_expr} FROM {table}").fetchone()[0]
    freshness = str(freshness_raw) if freshness_raw is not None else ""
    if not freshness:
        raise IngestValidationError(f"[ingest] {logical_table} has no freshness value")
    if prev_freshness and freshness < prev_freshness:
        raise IngestValidationError(
            f"[ingest] {logical_table} freshness regressed: {freshness} < {prev_freshness}"
        )
    if expected_freshness is not None and freshness != str(expected_freshness):
        raise IngestValidationError(
            f"[ingest] {logical_table} period mismatch: staged={freshness}, expected={expected_freshness}"
        )

    allow_shrink = os.environ.get("INGEST_ALLOW_SHRINK", "0") == "1"
    if prev_rows and rows < prev_rows * 0.7 and not allow_shrink:
        raise RuntimeError(
            f"[ingest] {logical_table} 列數守門: {rows:,} < 前次 {prev_rows:,} 的 70%,"
            "疑似來源殘缺或 ignore_errors 大量丟列;確認為刻意清理請設 INGEST_ALLOW_SHRINK=1"
        )
    if prev_rows and rows < prev_rows:
        print(
            f"[ingest] {logical_table}: {rows:,} 列 "
            f"(前次 {prev_rows:,},縮減 {prev_rows - rows:,})"
        )
    return rows, freshness


def _record_ingest_meta(
    conn,
    logical_table: str,
    rows: int,
    freshness: str,
    generation_id: str | None,
) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO ingest_meta
            (table_name, rows, ingested_at, freshness_value, generation_id)
        VALUES (?, ?, CURRENT_TIMESTAMP, ?, ?)
        """,
        [logical_table, rows, freshness, generation_id],
    )


def _replace_table_from_query(
    conn,
    logical_table: str,
    query: str,
    *,
    expected_freshness: str | None = None,
    generation_id: str | None = None,
) -> int:
    """Build and validate a temporary table before replacing production data."""
    stage = f"_ingest_stage_{logical_table}"
    conn.execute(f"DROP TABLE IF EXISTS {stage}")
    try:
        conn.execute(f"CREATE TEMP TABLE {stage} AS {query}")
        _validate_stage_schema(conn, stage, logical_table)
        rows, freshness = _row_guard(
            conn,
            stage,
            logical_table=logical_table,
            expected_freshness=expected_freshness,
            generation_id=generation_id,
        )
        conn.execute(f"CREATE OR REPLACE TABLE {logical_table} AS SELECT * FROM {stage}")
        _record_ingest_meta(conn, logical_table, rows, freshness, generation_id)
        return rows
    finally:
        conn.execute(f"DROP TABLE IF EXISTS {stage}")


def _ingest_daily_k(generation_id: str | None = None):
    """匯入所有日K CSV 到 daily_k 表 (用 glob 一次讀取，union_by_name 處理欄位差異)."""
    conn = get_conn()

    glob_path = os.path.join(DAILY_K_DIR, "*.csv").replace("\\", "/")

    csv_files = glob.glob(os.path.join(DAILY_K_DIR, "*.csv"))
    retired_tickers = load_retired_tickers()
    numeric_files = [
        f for f in csv_files
        if (
            os.path.splitext(os.path.basename(f))[0].isdigit()
            and os.path.splitext(os.path.basename(f))[0] not in retired_tickers
        )
    ]
    if not numeric_files:
        raise IngestValidationError("[ingest] 無日K資料")

    # 用 glob 一次讀入，union_by_name 自動處理不同欄位數
    # filename=true 取得來源檔名以擷取 Ticker
    safe_paths = [f.replace("\\", "/") for f in numeric_files]
    count = _replace_table_from_query(conn, "daily_k", f"""
        SELECT
            regexp_extract(filename, '(\\d+)\\.csv', 1) AS Ticker,
            * EXCLUDE (filename)
        FROM read_csv_auto({safe_paths},
            header=true, union_by_name=true, filename=true)
    """, generation_id=generation_id)

    # 建立索引
    try:
        conn.execute("CREATE INDEX idx_dk_ticker ON daily_k(Ticker)")
        conn.execute("CREATE INDEX idx_dk_date ON daily_k(Date)")
    except Exception:
        pass

    return count


def _ingest_revenue(generation_id: str | None = None):
    """匯入月營收 CSV 到 revenue 表 (用 glob 一次讀取)."""
    conn = get_conn()

    csv_files = glob.glob(os.path.join(REVENUE_DIR, "revenue_*.csv"))
    if not csv_files:
        raise IngestValidationError("[ingest] 無月營收資料")

    # 部分檔案自帶 Ticker 欄位，部分沒有 → union_by_name 合併
    # 沒有 Ticker 的從檔名擷取
    safe_paths = [f.replace("\\", "/") for f in csv_files]
    return _replace_table_from_query(conn, "revenue", f"""
        SELECT
            COALESCE(
                CAST(Ticker AS VARCHAR),
                regexp_extract(filename, 'revenue_(\\w+)\\.csv', 1)
            ) AS Ticker,
            Date, Name, Monthly_Revenue, Cumulative_Revenue,
            YoY_pct_change, Cumulative_YoY_pct_change
        FROM read_csv_auto({safe_paths},
            header=true, union_by_name=true, filename=true)
    """, generation_id=generation_id)


def _ingest_financials(generation_id: str | None = None):
    """匯入季報財務 CSV 到 financials 表."""
    conn = get_conn()

    csv_files = sorted(glob.glob(os.path.join(FINANCIAL_DIR, "financial_*.csv")))
    if not csv_files:
        raise IngestValidationError("[ingest] 無季報資料")

    safe_paths = [f.replace("\\", "/") for f in csv_files]
    periods = []
    for path in csv_files:
        match = re.search(r"financial_(\d{4})Q([1-4])\.csv$", os.path.basename(path))
        if match:
            periods.append(int(match.group(1)) * 10 + int(match.group(2)))
    expected_period = str(max(periods)) if periods else None

    return _replace_table_from_query(conn, "financials", f"""
        SELECT * FROM read_csv_auto({safe_paths},
            header=true, union_by_name=true)
    """, expected_freshness=expected_period, generation_id=generation_id)


def _ingest_tdcc(generation_id: str | None = None):
    """匯入集保分散 CSV 到 tdcc 表."""
    conn = get_conn()

    tdcc_file = os.path.join(TDCC_DIR, "tdcc_summary.csv").replace("\\", "/")
    if not os.path.exists(tdcc_file.replace("/", "\\")):
        # 嘗試正斜線路徑
        alt = os.path.join(TDCC_DIR, "tdcc_summary.csv")
        if not os.path.exists(alt):
            raise IngestValidationError("[ingest] 無集保分散資料")

    # 2026-07-16 修正:不再吞例外——CREATE 失敗必須讓 pipeline 大聲失敗,
    # 否則 tdcc 表無聲消失最長一週(舊版先 DROP 再 try/except print 的事故窗口)
    return _replace_table_from_query(conn, "tdcc", f"""
        SELECT * FROM read_csv_auto('{tdcc_file}', header=true)
    """, generation_id=generation_id)


def _ingest_indices(generation_id: str | None = None):
    """匯入大盤指數 CSV 到 indices 表."""
    conn = get_conn()

    csv_files = glob.glob(os.path.join(INDEX_DIR, "index_*.csv"))
    if not csv_files:
        raise IngestValidationError("[ingest] 無大盤指數資料")

    safe_paths = [f.replace("\\", "/") for f in csv_files]
    # 防呆:yfinance 偶爾回傳空 OHLC 的美股指數行(YFTzMissingError),Close 為 NULL。
    # 這種單格來源 glitch 不該讓整個 ingest(含台股 daily_k/tdcc/財報)全部失敗
    # (2026-08-18 事故:index_GSPC 一筆空行擋掉當晚全部匯入),故在此過濾 + 告警,
    # 而非交給下游 non_null 硬擋。台股指數缺鍵仍會因過濾後列數守門/新鮮度異常被抓到。
    read_expr = (
        f"read_csv_auto({safe_paths}, header=true, union_by_name=true, filename=true)"
    )
    null_report = conn.execute(f"""
        SELECT regexp_extract(filename, 'index_(\\w+)\\.csv', 1) AS Index_Name, COUNT(*) AS n
        FROM {read_expr}
        WHERE "Date" IS NULL OR "Close" IS NULL
        GROUP BY 1 ORDER BY 2 DESC
    """).fetchall()
    if null_report:
        detail = ", ".join(f"{r[0]}×{r[1]}" for r in null_report)
        print(f"[ingest] 大盤指數過濾 {sum(r[1] for r in null_report)} 筆空 OHLC 行(來源 glitch): {detail}")
    return _replace_table_from_query(conn, "indices", f"""
        SELECT
            regexp_extract(filename, 'index_(\\w+)\\.csv', 1) AS Index_Name,
            * EXCLUDE (filename)
        FROM {read_expr}
        WHERE "Date" IS NOT NULL AND "Close" IS NOT NULL
    """, generation_id=generation_id)


def _build_stock_list():
    """One row per price ticker; latest financial name, then official metadata."""
    conn = get_conn()
    metadata_path = os.path.join(BASE_DIR, "ml", "data", "sector_mapping.csv")
    fallback = []
    if os.path.exists(metadata_path):
        with open(metadata_path, encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if not {"Ticker", "Name"}.issubset(reader.fieldnames or []):
                raise IngestValidationError("[ingest] stock name metadata missing Ticker/Name")
            seen = set()
            for row in reader:
                ticker = str(row["Ticker"]).strip()
                if not ticker or ticker in seen:
                    raise IngestValidationError("[ingest] stock name metadata has blank/duplicate ticker")
                seen.add(ticker)
                fallback.append((ticker, str(row["Name"] or "").strip()))
    conn.execute("CREATE OR REPLACE TEMP TABLE stock_name_fallback (Ticker VARCHAR PRIMARY KEY, Name VARCHAR)")
    if fallback:
        conn.executemany("INSERT INTO stock_name_fallback VALUES (?, ?)", fallback)

    conn.execute("""
        CREATE OR REPLACE TABLE stock_list AS
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
            SELECT
                CAST(Ticker AS VARCHAR) AS Ticker,
                Name
            FROM financials
            WHERE Name IS NOT NULL AND TRIM(Name) <> ''
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY CAST(Ticker AS VARCHAR)
                ORDER BY Year DESC, Season DESC, Name
            ) = 1
        )
        SELECT
            lp.Ticker,
            COALESCE(n.Name, m.Name, '') AS Name,
            lp.Last_Close,
            lp.Last_Volume,
            lp.Last_Date,
            lp.Last_Change_Pct
        FROM latest_price lp
        LEFT JOIN names n ON lp.Ticker = n.Ticker
        LEFT JOIN stock_name_fallback m ON lp.Ticker = m.Ticker
    """)

    conn.execute("CREATE UNIQUE INDEX idx_sl_ticker ON stock_list(Ticker)")


def ingest_all():
    """執行全部匯入，回傳統計摘要."""
    t0 = time.time()

    results: dict[str, int] = {}
    errors: dict[str, str] = {}
    conn = get_conn()
    generation_id = uuid.uuid4().hex

    # 2026-08-18 事故後改為「逐表獨立提交」:先前全表包在單一交易,任何一表驗證失敗
    # 就 ROLLBACK 全部(一格美股指數空行害整晚台股 daily_k/tdcc/財報全進不去)。
    # 現在每表各自 BEGIN/COMMIT:成功的表立即持久化,失敗的表隔離、記錄、續跑下一表。
    # 成功表保留，但任一必要表或 stock_list 失敗都回報整次匯入失敗。
    # 讓上游 retry / 阻擋衍生輸出，不能把混合世代包裝成匯入成功。
    steps = [
        ("daily_k", "匯入日K資料", lambda: _ingest_daily_k(generation_id)),
        ("revenue", "匯入月營收", lambda: _ingest_revenue(generation_id)),
        ("financials", "匯入季報財務", lambda: _ingest_financials(generation_id)),
        ("tdcc", "匯入集保分散", lambda: _ingest_tdcc(generation_id)),
        ("indices", "匯入大盤指數", lambda: _ingest_indices(generation_id)),
    ]
    for key, label, fn in steps:
        print(f"[ingest] {label}...")
        try:
            conn.execute("BEGIN TRANSACTION")
            results[key] = fn()
            conn.execute("COMMIT")
        except Exception as exc:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            errors[key] = f"{type(exc).__name__}: {exc}"
            # 2026-09-30:cp950 主控台印 emoji 會 UnicodeEncodeError 炸掉整個 ingest 子程序、
            # 吞掉真正的錯誤訊息;警告只用 ASCII 標記,訊息本身以 errors= 'replace' 保底輸出
            msg = f"[ingest] [WARN] {key} failed, isolated from other tables: {errors[key]}"
            try:
                print(msg)
            except UnicodeEncodeError:
                print(msg.encode("ascii", "replace").decode("ascii"))

    # stock_list 依賴 daily_k(+financials);daily_k 成功才重建,避免用舊資料覆蓋
    if "daily_k" in results:
        print("[ingest] 建立股票清單...")
        try:
            conn.execute("BEGIN TRANSACTION")
            _build_stock_list()
            conn.execute("COMMIT")
        except Exception as exc:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            errors["stock_list"] = f"{type(exc).__name__}: {exc}"
            msg = f"[ingest] [WARN] stock_list failed: {errors['stock_list']}"
            try:
                print(msg)
            except UnicodeEncodeError:
                print(msg.encode("ascii", "replace").decode("ascii"))

    print("[ingest] 確認敘事 shadow schema...")
    ensure_narrative_schema()

    elapsed = time.time() - t0
    summary = f"[ingest] 完成! 耗時 {elapsed:.1f}s — {results}"
    if errors:
        summary += f" | ⚠️ 失敗(已隔離,成功表已落地): {errors}"
    print(summary)

    if errors:
        raise IngestValidationError(
            f"[ingest] 匯入未完整成功: {errors}; 已保留成功表: {sorted(results)}"
        )
    return results
