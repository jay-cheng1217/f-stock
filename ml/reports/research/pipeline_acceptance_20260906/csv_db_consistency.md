# Frozen CSV to DuckDB acceptance: 2026-09-04

Status: **PASS**. Read-only audit completed 2026-09-06T13:06:43.750888+08:00; no fetch, ingest, source-data, database, strategy, or ledger mutation.

- All **1,911** eligible 9/4 CSV ticker/date rows match the isolated DB one-to-one. Open, High, Low, Close and Volume have **zero exact mismatches**, zero null/nonfinite values, and maximum absolute delta **0**. No missing/extra keys or duplicate keys.
- All five tables share generation `4768117cb9fe4a6cb9e1322920d04298`. Source accepted rows, DB counts and ingest metadata agree: daily_k **2,974,036**, revenue **61,854**, financials **47,219**, tdcc **899,102**, indices **10,037**.
- Financials 2026Q2 contains **1,825** ticker rows matching its source; isolated fundamentals audit reports due quarter **2026Q2**, status **OK**, no gaps. TWII price and total-return rows on **9/4** match CSV exactly. Index metadata 9/5 comes from **USDTWDX**, not Taiwan stocks.
- Scanned **1,947** numeric nonretired daily-K files; **36** have no 9/4 row and are named in JSON. This audit verifies CSV-to-DB fidelity, not independent exchange-universe completeness.
- **3,878** source files (972.02 MiB) still match the frozen input manifest. DB SHA-256 and mtime were unchanged during the audit.

Retirement-contract preparation issue is **resolved**: the retirement CSV was missing during the initial workspace inspection and has since been copied into isolation. A read-only follow-up verified that isolated and canonical files have the identical SHA-256 `fbdb2f9e34335cab2e05997b4a753376c2baf63867f0c11beb7625e454b65403`. All six retired tickers were absent from the source CSVs and DB, so the original consistency verification remains valid. This follow-up checked only the two contract files; it did not rerun the full data audit.

DB: `F:\stock\output\pipeline_acceptance_20260906\workspace\stock.duckdb`
DB SHA-256: `f2f506d925950f16c3540ec0bc250dfe29d4de10ce3bd6f30cb8c31bece30eda`
9/4 keyed OHLCV SHA-256 (CSV = DB): `5162050fbd549c84c96dadc81fdd5cb8743abaa286b36628a399b068982d3a3a`

Method: independent pandas round-trip float parsing versus stored DuckDB DOUBLE, exact equality without tolerance; full outer join by ticker/date. Other tables are checked for source/DB/meta counts, generation, and stated freshness, not every non-OHLCV cell. All files originate from a snapshot copied on 9/6; this is not historical information-availability proof, live 9/7 exchange-fetch proof, or complete repaired-nightly acceptance. Per-table ingestion remains non-atomic across the database; this completed snapshot passes the generation check.

Detailed checks, source manifest/DB hashes, dates, counts and limitations: [csv_db_consistency.json](csv_db_consistency.json).
