"""Publish hash-reviewed source repairs, with backups and resumable receipts.

Only source directories and official sector metadata are eligible targets.
The plan's evidence hashes freeze the reviews; this tool does not certify them.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil

ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIRS = {"日K資料", "外資持股", "估值資料", "原始融資資料", "清理後資料", "集保分散",
               "季報財務", "資產負債"}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def promote(plan_path, *, root=ROOT, publish=False):
    root = Path(root).resolve()
    plan_path = Path(plan_path).resolve()
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    label = plan["label"]
    if not re.fullmatch(r"[a-z0-9_]+", label):
        raise ValueError("Invalid repair label")
    if not plan.get("evidence") or not plan.get("files"):
        raise ValueError("Repair requires reviewed evidence and source files")
    for evidence in plan["evidence"]:
        if sha(evidence["path"]) != evidence["sha256"]:
            raise ValueError("Review evidence changed")
    planned, seen = [], set()
    backup_root = root / "output/data_layer_consistency_20260906/source_promotion" / label
    for item in plan["files"]:
        source, target = Path(item["source"]).resolve(), (root / item["target"]).resolve()
        if not source.is_relative_to(root / "output") or not target.is_relative_to(root):
            raise ValueError("Source or target escapes the workspace")
        rel = target.relative_to(root)
        if not (rel.parts[0] in SOURCE_DIRS or rel.as_posix() == "ml/data/sector_mapping.csv"):
            raise ValueError("Target is not an authorized source dataset")
        if target in seen:
            raise ValueError("Duplicate target")
        seen.add(target)
        if sha(source) != item["after_sha256"]:
            raise ValueError(f"Candidate changed: {source}")
        current = sha(target) if target.exists() else None
        if current not in (item["before_sha256"], item["after_sha256"]):
            raise ValueError(f"Concurrent source change: {target}")
        planned.append((item, source, target, current, rel))
    receipt = {"label": label, "plan_sha256": sha(plan_path),
               "started_at": datetime.now().astimezone().isoformat(),
               "status": "PREFLIGHT_PASS", "file_count": len(planned), "files": []}
    if not publish:
        return receipt
    backup_root.mkdir(parents=True, exist_ok=True)
    for item, source, target, current, rel in planned:
        state = "already_published"
        if current != item["after_sha256"]:
            if (sha(target) if target.exists() else None) != current:
                raise ValueError(f"Concurrent source change: {target}")
            backup = backup_root / rel
            if current is not None:
                backup.parent.mkdir(parents=True, exist_ok=True)
                if backup.exists():
                    if sha(backup) != current:
                        raise ValueError(f"Backup conflicts: {backup}")
                else:
                    shutil.copyfile(target, backup)
                if sha(backup) != current:
                    raise ValueError("Backup did not preserve original bytes")
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(target.name + ".source_repair.tmp")
            shutil.copyfile(source, temporary)
            if sha(temporary) != item["after_sha256"]:
                raise ValueError("Candidate changed while copying")
            if (sha(target) if target.exists() else None) != current:
                raise ValueError(f"Concurrent source change before swap: {target}")
            os.replace(temporary, target)
            state = "published"
        if sha(target) != item["after_sha256"]:
            raise ValueError(f"Published target differs: {target}")
        receipt["files"].append({"target": str(rel), "status": state,
                                 "before_sha256": item["before_sha256"], "after_sha256": item["after_sha256"]})
    receipt.update(status="PUBLISHED", completed_at=datetime.now().astimezone().isoformat())
    receipt_path = backup_root / f"receipt_{datetime.now():%H%M%S_%f}.json"
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    receipt["receipt_path"] = str(receipt_path)
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan", type=Path)
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args()
    receipt = promote(args.plan, publish=args.publish)
    print(json.dumps({k: v for k, v in receipt.items() if k != "files"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
