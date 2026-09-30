"""Import and inspect the latest CMoney ChipK local snapshot.

This script reads only the non-secret UBSKChart market-data snapshot.  It does
not read cookies, session storage, registry credentials, or browser login data.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))

from ml.chipk import load_latest_chipk_main_force, redact_chipk_source_path  # noqa: E402


def _score_distribution(df: pd.DataFrame) -> dict[str, int]:
    if df.empty:
        return {}
    buckets = pd.cut(
        pd.to_numeric(df["chipk_main_force_score"], errors="coerce"),
        bins=[-0.01, 0.25, 0.50, 0.70, 1.01],
        labels=["weak", "neutral", "supportive", "strong"],
    )
    return {str(k): int(v) for k, v in buckets.value_counts(sort=False).items()}


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect CMoney ChipK local snapshot")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    parser.add_argument("--output-csv", help="Optional path to write parsed market-data snapshot")
    parser.add_argument("--top", type=int, default=10, help="Rows to print in text mode")
    args = parser.parse_args()

    df, meta = load_latest_chipk_main_force()
    payload = {
        **meta.to_dict(),
        "source_path": redact_chipk_source_path(meta.source_path),
        "score_distribution": _score_distribution(df),
    }
    if args.output_csv and not df.empty:
        out_path = Path(args.output_csv)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(out_path, index=False, encoding="utf-8-sig")
        payload["output_csv"] = str(out_path)

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if meta.status == "ok" else 1

    print(f"status: {meta.status}")
    print(f"asof_date: {meta.asof_date or '-'}")
    print(f"rows: {meta.row_count}")
    print(f"source: {payload['source_path'] or '-'}")
    print(f"history_available: {meta.history_available}")
    print(f"score_distribution: {payload['score_distribution']}")
    if meta.message:
        print(f"message: {meta.message}")
    if not df.empty:
        cols = [
            "ticker",
            "chipk_name",
            "chipk_close",
            "chipk_main_force_1d",
            "chipk_main_force_5d",
            "chipk_main_force_20d",
            "chipk_main_force_score",
        ]
        print(df[cols].sort_values("chipk_main_force_score", ascending=False).head(args.top).to_string(index=False))
    return 0 if meta.status == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
