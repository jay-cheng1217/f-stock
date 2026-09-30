import duckdb
import pytest

from backend.db import ingest


def _old_daily_table(conn):
    conn.execute(
        """
        CREATE TABLE daily_k AS
        SELECT 'OLD'::VARCHAR AS Ticker, DATE '2026-01-01' AS Date,
               10.0::DOUBLE AS Close, 1000::BIGINT AS Volume
        """
    )


def test_staged_schema_failure_preserves_existing_table():
    conn = duckdb.connect()
    _old_daily_table(conn)

    with pytest.raises(ingest.IngestValidationError, match="missing required columns"):
        ingest._replace_table_from_query(
            conn,
            "daily_k",
            "SELECT 'NEW'::VARCHAR AS Ticker, DATE '2026-07-16' AS Date, 20.0 AS Close",
        )

    assert conn.execute("SELECT Ticker FROM daily_k").fetchone()[0] == "OLD"


def test_strict_contract_rejects_numeric_column_drift(tmp_path):
    path = tmp_path / "bad_daily.csv"
    path.write_text(
        "Ticker,Date,Close,Volume\n2330,2026-07-15,100,1000\n2330,2026-07-16,101,not-a-number\n",
        encoding="utf-8",
    )
    conn = duckdb.connect()

    with pytest.raises(ingest.IngestValidationError, match="numeric columns changed type"):
        ingest._replace_table_from_query(
            conn,
            "daily_k",
            f"SELECT * FROM read_csv_auto('{path.as_posix()}', header=true)",
        )

    assert not conn.execute(
        "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = 'daily_k'"
    ).fetchone()[0]


def test_financial_period_mismatch_is_blocked_before_swap():
    conn = duckdb.connect()
    conn.execute(
        """
        CREATE TABLE financials AS
        SELECT '2330'::VARCHAR AS Ticker, 2026::BIGINT AS Year, 1::BIGINT AS Season,
               10.0::DOUBLE AS Operating_Margin_Pct
        """
    )

    with pytest.raises(ingest.IngestValidationError, match="period mismatch"):
        ingest._replace_table_from_query(
            conn,
            "financials",
            """
            SELECT '2330'::VARCHAR AS Ticker, 2025::BIGINT AS Year, 4::BIGINT AS Season,
                   9.0::DOUBLE AS Operating_Margin_Pct
            """,
            expected_freshness="20261",
        )

    assert conn.execute("SELECT Year, Season FROM financials").fetchone() == (2026, 1)


def _stub_ingest_steps(monkeypatch, conn):
    monkeypatch.setattr(ingest, "get_conn", lambda: conn)
    for name in ("daily_k", "revenue", "financials", "tdcc", "indices"):
        monkeypatch.setattr(ingest, f"_ingest_{name}", lambda _generation_id: 1)
    monkeypatch.setattr(ingest, "_build_stock_list", lambda: None)
    monkeypatch.setattr(ingest, "ensure_narrative_schema", lambda: None)


def test_partial_ingest_keeps_valid_table_but_reports_failure(monkeypatch):
    conn = duckdb.connect()
    _old_daily_table(conn)
    _stub_ingest_steps(monkeypatch, conn)

    def replace_daily(_generation_id):
        conn.execute(
            """
            CREATE OR REPLACE TABLE daily_k AS
            SELECT 'NEW'::VARCHAR AS Ticker, DATE '2026-07-16' AS Date,
                   20.0::DOUBLE AS Close, 2000::BIGINT AS Volume
            """
        )
        return 1

    monkeypatch.setattr(ingest, "_ingest_daily_k", replace_daily)
    monkeypatch.setattr(
        ingest,
        "_ingest_revenue",
        lambda _generation_id: (_ for _ in ()).throw(RuntimeError("revenue failed")),
    )

    with pytest.raises(RuntimeError, match="revenue failed"):
        ingest.ingest_all()

    assert conn.execute("SELECT Ticker FROM daily_k").fetchone()[0] == "NEW"


def test_stock_list_failure_is_not_success(monkeypatch):
    conn = duckdb.connect()
    _stub_ingest_steps(monkeypatch, conn)
    monkeypatch.setattr(ingest, "_build_stock_list", lambda: (_ for _ in ()).throw(ValueError("duplicate stock")))
    with pytest.raises(ingest.IngestValidationError, match="stock_list.*duplicate stock"):
        ingest.ingest_all()


def test_success_reports_all_real_counts(monkeypatch):
    conn = duckdb.connect()
    _stub_ingest_steps(monkeypatch, conn)
    assert ingest.ingest_all() == dict.fromkeys(("daily_k", "revenue", "financials", "tdcc", "indices"), 1)


def test_stock_names_use_latest_period_and_new_company_metadata(monkeypatch, tmp_path):
    conn = duckdb.connect()
    monkeypatch.setattr(ingest, "get_conn", lambda: conn)
    monkeypatch.setattr(ingest, "BASE_DIR", str(tmp_path))
    metadata = tmp_path / "ml/data/sector_mapping.csv"
    metadata.parent.mkdir(parents=True)
    metadata.write_text("Ticker,Name\n2330,Older metadata\n7780,New company\n", encoding="utf-8")
    conn.execute("""CREATE TABLE daily_k AS SELECT * FROM (VALUES
        ('2330', DATE '2026-09-03', 100.0, 1000),
        ('2330', DATE '2026-09-04', 110.0, 1200),
        ('7780', DATE '2026-09-04', 60.0, 1300)
    ) t(Ticker, Date, Close, Volume)""")
    conn.execute("""CREATE TABLE financials AS SELECT * FROM (VALUES
        ('2330', 2025, 4, 'Old name'),
        ('2330', 2026, 1, 'New name'),
        ('2330', 2026, 2, 'Latest name'),
        ('2330', 2026, 2, 'Latest name')
    ) t(Ticker, Year, Season, Name)""")
    ingest._build_stock_list()
    assert conn.execute("SELECT Ticker, Name, Last_Change_Pct FROM stock_list ORDER BY Ticker").fetchall() == [
        ('2330', 'Latest name', 10.0), ('7780', 'New company', None)]
    # Replacement remains idempotent even with a unique index on the old table.
    ingest._build_stock_list()
    assert conn.execute("SELECT COUNT(*) FROM stock_list").fetchone()[0] == 2


def test_duplicate_name_metadata_rejected_before_replacing_stock_list(monkeypatch, tmp_path):
    conn = duckdb.connect()
    monkeypatch.setattr(ingest, "get_conn", lambda: conn)
    monkeypatch.setattr(ingest, "BASE_DIR", str(tmp_path))
    conn.execute("CREATE TABLE stock_list AS SELECT 'OLD' AS Ticker")
    metadata = tmp_path / "ml/data/sector_mapping.csv"
    metadata.parent.mkdir(parents=True)
    metadata.write_text("Ticker,Name\n2330,A\n2330,B\n", encoding="utf-8")
    with pytest.raises(ingest.IngestValidationError, match="duplicate ticker"):
        ingest._build_stock_list()
    assert conn.execute("SELECT Ticker FROM stock_list").fetchone()[0] == 'OLD'
