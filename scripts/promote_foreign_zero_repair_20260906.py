"""Publish reviewed numeric-zero reconstructions; preserve every input backup."""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "ml/reports/research/data_layer_consistency_20260906/foreign_zero"
STAGE = ROOT / "output/pipeline_acceptance_20260906/workspace/output/historical_gaps_20260906/zero_repair"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    manifest = json.loads((REPORT / "repair_manifest.json").read_text(encoding="utf-8"))
    comparison = json.loads((REPORT / "foreign_latest_group_ab.json").read_text(encoding="utf-8"))
    assert comparison["status"] == "PASS" and comparison["changed_cells"] == 0
    files = manifest["files"]
    assert len({r["date"] for r in files}) == len(files)
    assert len(files) == 1618 and sum(r["changed_cells"] for r in files) == 29557
    planned = []
    for item in files:
        source, backup = Path(item["target"]).resolve(), Path(item["input_copy"]).resolve()
        target = (ROOT / "外資持股" / f"{item['date']}.csv").resolve()
        assert source.is_relative_to((STAGE / "candidates").resolve())
        assert backup.is_relative_to((STAGE / "inputs").resolve())
        assert target.parent == (ROOT / "外資持股").resolve()
        assert sha(source) == item["sha256"]
        assert sha(backup) == item["source_before_sha256"]
        current = sha(target)
        assert current in {item["source_before_sha256"], item["sha256"]}, f"Concurrent change: {target}"
        planned.append((item, source, target, current))
    receipt = {"started_at": datetime.now().astimezone().isoformat(), "files": [],
               "scope": "Only missing displayed Foreign_Pct reconstructed; per-cell provenance in changed_cells.csv"}
    # Publish latest date last. The API's live map remains on the previous
    # complete latest file until all historical replacements have completed.
    for item, source, target, before in sorted(planned, key=lambda v: v[0]["date"]):
        state = "already_published"
        if before != item["sha256"]:
            temporary = target.with_name(target.name + ".zero_repair.tmp")
            shutil.copyfile(source, temporary)
            assert sha(temporary) == item["sha256"]
            assert sha(target) == before, f"Concurrent change: {target}"
            os.replace(temporary, target)
            state = "published"
        assert sha(target) == item["sha256"]
        receipt["files"].append({"date": item["date"], "status": state,
                                 "after_sha256": item["sha256"], "changed_cells": item["changed_cells"]})
    receipt.update(completed_at=datetime.now().astimezone().isoformat(), status="PASS",
                   file_count=len(files), changed_cells=sum(r["changed_cells"] for r in files),
                   latest_affected_model_feature_cells=comparison["changed_cells"])
    destination = REPORT / "promotion.json"
    if destination.exists():
        destination = ROOT / "output/data_layer_consistency_20260906" / f"zero_rerun_{datetime.now():%H%M%S}.json"
    destination.write_text(json.dumps(receipt, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps({k:v for k,v in receipt.items() if k != "files"}))


if __name__ == "__main__":
    main()
