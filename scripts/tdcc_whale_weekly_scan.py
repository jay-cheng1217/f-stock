# -*- coding: utf-8 -*-
"""Refresh the unchanged >=3pp whale / declining-retail observation radar.

Raw acquisition belongs to fetch_tdcc_weekly.py. This scheduled derivative
uses adjacent validated local weeks, never a stale rolling snapshot or a
second network source. It does not update models, ledgers, gates or HTML.

python scripts/tdcc_whale_weekly_scan.py
python scripts/tdcc_whale_weekly_scan.py --output-root output/tdcc_radar_staging
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

BASE = Path(os.environ.get("STOCK_BASE_DIR") or Path(__file__).resolve().parents[1])
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.tdcc_whale_radar import load_local_week, refresh


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--min-delta", type=float, default=3.0)
    ap.add_argument("--input-dir", type=Path, default=BASE / "集保分散")
    ap.add_argument("--output-root", type=Path, default=BASE)
    ap.add_argument("--sector-path", type=Path, default=BASE / "ml/data/sector_mapping.csv")
    ap.add_argument("--as-of", help="Latest allowed raw date, YYYY-MM-DD or YYYYMMDD")
    a = ap.parse_args()
    result = refresh(input_dir=a.input_dir, output_root=a.output_root, sector_path=a.sector_path,
                     as_of=a.as_of, min_delta=a.min_delta)
    print(json.dumps({"status": result["status"], "previous_date": result["previous_date"],
                      "as_of_date": result["as_of_date"], "paired_tickers": result["sources"]["paired_tickers"],
                      "named_radar_rows": result["named_radar_rows"], "output_root": str(a.output_root)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
