"""Replay frozen source/CSV/HTTP-DB/ML date evidence; no network or DB writes."""
from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.latest_bar_coverage import build_latest_bar_coverage


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot-dir", type=Path, required=True)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--target-date", required=True)
    parser.add_argument("--knowledge-date", required=True)
    parser.add_argument("--layers", default="stale_layer_dates.json")
    args = parser.parse_args()
    payloads = {}
    for market in ["twse", "tpex"]:
        with gzip.open(args.snapshot_dir / f"{market}_{args.target_date.replace('-', '')}.json.gz", "rt", encoding="utf-8") as stream:
            payloads[market] = json.load(stream)
    result = build_latest_bar_coverage(
        target_date=args.target_date, knowledge_date=args.knowledge_date,
        twse_payload=payloads["twse"], tpex_payload=payloads["tpex"],
        layer_dates=json.loads((args.snapshot_dir / args.layers).read_text(encoding="utf-8")),
        event_registry=json.loads(args.registry.read_text(encoding="utf-8")))
    print(json.dumps({k: v for k, v in result.items() if k != "rows"}, ensure_ascii=True, indent=2))
    return 1 if result["status"] == "fail" else 0


if __name__ == "__main__":
    raise SystemExit(main())
