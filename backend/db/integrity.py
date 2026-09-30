"""Read-only checks of committed import generations and API stock identities."""

REQUIRED_TABLES = ("daily_k", "revenue", "financials", "tdcc", "indices")


def read_ingest_consistency(conn):
    metadata = {
        name: {"recorded_rows": int(rows), "generation_id": generation}
        for name, rows, generation in conn.execute(
            "SELECT table_name, rows, generation_id FROM ingest_meta"
        ).fetchall() if name in REQUIRED_TABLES
    }
    for name in REQUIRED_TABLES:
        if name in metadata:
            metadata[name]["actual_rows"] = conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
    missing = sorted(set(REQUIRED_TABLES) - set(metadata))
    generations = {entry["generation_id"] for entry in metadata.values()}
    generation_ok = not missing and len(generations) == 1 and all(generations)
    counts_ok = not missing and all(
        entry["recorded_rows"] == entry["actual_rows"] > 0 for entry in metadata.values()
    )
    mismatch_count = conn.execute("""
        WITH prices AS (
            SELECT Ticker, MAX(Date) AS latest_date FROM daily_k GROUP BY Ticker
        ), listed AS (
            SELECT Ticker, COUNT(*) AS n, MAX(Last_Date) AS latest_date
            FROM stock_list GROUP BY Ticker
        )
        SELECT COUNT(*) FROM prices p FULL OUTER JOIN listed s USING (Ticker)
        WHERE p.Ticker IS NULL OR s.Ticker IS NULL OR s.n <> 1
           OR p.latest_date IS DISTINCT FROM s.latest_date
    """).fetchone()[0]
    return {
        "tables": metadata, "missing_metadata": missing,
        "same_generation": bool(generation_ok), "row_counts_match": counts_ok,
        "stock_list_mismatched_tickers": mismatch_count,
        "ok": bool(generation_ok and counts_ok and mismatch_count == 0),
    }
