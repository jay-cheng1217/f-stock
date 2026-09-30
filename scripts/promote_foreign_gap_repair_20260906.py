"""Publish the four reviewed official ownership repairs with hash preconditions."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
from datetime import datetime

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "ml/reports/research/data_layer_consistency_20260906"
STAGE = ROOT / "output/pipeline_acceptance_20260906/workspace/output/historical_gaps_20260906/candidates"
BACKUP = ROOT / "output/data_layer_consistency_20260906/foreign_backup"
DAYS = {"20220915", "20220916", "20220919", "20260807"}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def main():
    records = json.loads((REPORT / "foreign_candidates.json").read_text(encoding="utf-8"))
    evidence = json.loads((REPORT / "foreign_latest_group_ab.json").read_text(encoding="utf-8"))
    assert evidence["status"] == "PASS" and evidence["changed_cells"] == 0
    latest = ROOT / "外資持股" / evidence["latest_selected_file"]
    assert sha(latest) == evidence["latest_sha256"]
    assert {row["date"] for row in records} == DAYS and len(records) == len(DAYS)
    plan = []
    for row in records:
        source = Path(row["target"]).resolve()
        target = (ROOT / "外資持股" / f"{row['date']}.csv").resolve()
        assert source.is_relative_to(STAGE.resolve())
        assert target.parent == (ROOT / "外資持股").resolve()
        assert sha(source) == row["sha256"]
        current = sha(target)
        assert current in {row["source_before_sha256"], row["sha256"]}, f"Concurrent change: {target}"
        data = pd.read_csv(source, dtype={"Ticker": str})
        assert len(data) == row["rows"] and not data.Ticker.duplicated().any()
        assert {check["market"] for check in row["market_validation"]} == {"TWSE", "TPEx"}
        assert all(check["unknown_rows"] == 0 for check in row["market_validation"])
        assert all(check["ticker_overlap_ratio"] >= .8 for check in row["neighbor_coverage"])
        plan.append((row, source, target, current))
    # No target is touched until every candidate, baseline and A/B is verified.
    BACKUP.mkdir(parents=True, exist_ok=True)
    receipt = {"started_at": datetime.now().astimezone().isoformat(), "files": [],
               "category": "L1 official-source backfill; latest affected feature group exactly equal"}
    for row, source, target, before in plan:
        state = "already_published"
        if before != row["sha256"]:
            if target.exists():
                backup = BACKUP / target.name
                if backup.exists():
                    assert sha(backup) == before
                else:
                    shutil.copy2(target, backup)
            temporary = target.with_name(target.name + ".data_repair.tmp")
            shutil.copyfile(source, temporary)
            assert sha(temporary) == row["sha256"]
            assert sha(target) == before, f"Concurrent change before replacement: {target}"
            os.replace(temporary, target)
            state = "published"
        assert sha(target) == row["sha256"]
        receipt["files"].append({"date": row["date"], "path": str(target), "rows_before": row["source_before_rows"],
                                 "rows_after": row["rows"], "before_sha256": before,
                                 "after_sha256": sha(target), "status": state})
    receipt.update(completed_at=datetime.now().astimezone().isoformat(),
                   latest_file_unchanged=sha(latest) == evidence["latest_sha256"],
                   rows_added=sum(row["rows"]-row["source_before_rows"] for row in records),
                   db_scope="Ownership history is not an ingest_all DuckDB table. API/ML select unchanged latest CSV.",
                   status="PASS")
    output = REPORT / "foreign_promotion.json"
    if output.exists():
        output = BACKUP / f"promotion_rerun_{datetime.now():%H%M%S}.json"
    output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps(receipt, ensure_ascii=False))


if __name__ == "__main__":
    main()
