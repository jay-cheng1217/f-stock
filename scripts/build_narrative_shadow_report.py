from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date

import duckdb
import pandas as pd

BASE_DIR = os.environ.get("STOCK_BASE_DIR") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from backend.config import DUCKDB_PATH
from backend.db.ingest import ensure_narrative_schema
from backend.features.narrative.heat import FEATURE_COLUMNS, build_daily_features


REPORT_DIR = os.path.join(BASE_DIR, "ml", "reports")
os.makedirs(REPORT_DIR, exist_ok=True)


def _as_date(value: str | None) -> date:
    return date.fromisoformat(value) if value else date.today()


def build_shadow_report(report_date: date) -> tuple[pd.DataFrame, str]:
    conn = duckdb.connect(DUCKDB_PATH)
    try:
        ensure_narrative_schema(conn)
        features = build_daily_features(conn, report_date)
    finally:
        conn.close()

    out = features.copy()
    if out.empty:
        out = pd.DataFrame(columns=FEATURE_COLUMNS)
    out = out.sort_values(["heat_14d", "article_count_7d", "ticker"], ascending=[False, False, True])
    path = os.path.join(REPORT_DIR, f"shadow_narrative_{report_date.strftime('%Y%m%d')}.csv")
    out.to_csv(path, index=False, encoding="utf-8-sig")
    return out, path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build daily shadow narrative report.")
    parser.add_argument("--date", default=None)
    args = parser.parse_args(argv)
    report_date = _as_date(args.date)
    df, path = build_shadow_report(report_date)
    print(
        json.dumps(
            {
                "status": "ok",
                "date": report_date.isoformat(),
                "rows": int(len(df)),
                "path": path,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
