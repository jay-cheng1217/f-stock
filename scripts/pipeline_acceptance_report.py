"""Collect bounded, non-secret evidence from the dated acceptance workspace."""
from datetime import datetime
import gzip
import hashlib
import json
from pathlib import Path
import shutil

BASE = Path(__file__).resolve().parents[1]
RUN = BASE / "output/pipeline_acceptance_20260906"
WORK = RUN / "workspace"
REPORT = BASE / "ml/reports/research/pipeline_acceptance_20260906"


def sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write(name, payload):
    (REPORT / name).write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def main():
    assert WORK.is_dir()
    REPORT.mkdir(parents=True, exist_ok=True)
    receipts = []
    for path in sorted(RUN.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if "execution_status" in payload:
            payload["log_sha256"] = sha(Path(payload["log"]))
            receipts.append(payload)
    write("stage_receipts.json", receipts)
    for name in ("source_integrity.json", "production_model_contract.json", "health_before_reload.json", "email_semantics_before_repair.json"):
        shutil.copy2(RUN / name, REPORT / name)
    for path in sorted((WORK / "logs").glob("acceptance_*.json")):
        shutil.copy2(path, REPORT / path.name)
    email_path = REPORT / "acceptance_email.json"
    if email_path.exists():
        email = json.loads(email_path.read_text(encoding="utf-8"))
        email.update(hash_scope="Original sha256 measures UTF-8 text after newline normalization",
                     exact_file_sha256=sha(WORK / "logs/acceptance_email.html"),
                     exact_file_bytes=(WORK / "logs/acceptance_email.html").stat().st_size)
        write(email_path.name, email)
    # Exact manifest retained compactly; it contains filenames and hashes, not data rows.
    source = RUN / "input_manifest.json"
    (REPORT / "input_manifest.json.gz").write_bytes(gzip.compress(source.read_bytes(), mtime=0))
    guard_logs = list((WORK / "output").glob("acceptance_guard_*.jsonl"))
    violations = [json.loads(line) for path in guard_logs for line in path.read_text(encoding="utf-8").splitlines() if line]
    write("guard_evidence.json", {"process_receipts": len(guard_logs), "violations": violations,
          "logs": [{"name": p.name, "bytes": p.stat().st_size, "sha256": sha(p)} for p in sorted(guard_logs)],
          "scope": "Python audit guard on installed child runs; not an OS sandbox. Early source/ingest stages predated guard installation."})
    assert not violations, "A denied side effect cannot be silently accepted"
    artifacts = [
        "stock.duckdb", "ml/models/snapshot_cache.pkl", "ml/models/predictions_2026-09-04.csv",
        "ml/models/dataA_predictions_2026-09-04.csv", "ml/models/unified_signals_2026-09-04.csv",
        "logs/entry_list_20260907.json", "ml/reports/entry_dashboard_latest.html", "logs/acceptance_email.html",
        "ml/reports/rere_lane_ledger.csv", "ml/reports/shadow_dataA_ledger.csv",
    ]
    write("artifact_manifest.json", {"collected_at": datetime.now().isoformat(), "workspace": str(WORK),
          "input_manifest_sha256": sha(source), "artifacts": [{"path": name, "bytes": (WORK / name).stat().st_size,
          "sha256": sha(WORK / name)} for name in artifacts]})
    code = [BASE / "app.py", *sorted((BASE / "scripts").glob("pipeline_acceptance*.py")),
            *[BASE / "scripts" / n for n in ("acceptance_arena_reader_leaf.py", "daily_pipeline.py", "agent_arena.py", "ai_summary.py",
                                           "send_daily_email.py", "entry_dashboard.py")]]
    write("final_code_manifest.json", [{"path": str(p.relative_to(BASE)), "sha256": sha(p)} for p in code])
    print(json.dumps({"report": str(REPORT), "receipts": len(receipts), "guard_violations": len(violations)}))


if __name__ == "__main__":
    main()
