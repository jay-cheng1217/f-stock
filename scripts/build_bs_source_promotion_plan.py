"""Freeze reviewed 26-quarter BS candidates into the generic source promotion plan."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.stage_q2_financial_reconciliation import receipt


def build(root):
    historical_path = root / "output/bs_historical_reconciliation_20260906/manifest.json"
    retained_path = root / "output/bs_retained_official_reconciliation_20260906/manifest.json"
    q2_path = root / "output/financial_q2_repaired_20260906/manifest.json"
    e_path = root / "output/bs_historical_latest_E_20260906/manifest.json"
    f_path = root / "output/bs_retained_latest_F_20260906/manifest.json"
    historical, retained, q2, e, f = [json.loads(p.read_text(encoding="utf-8")) for p in (historical_path, retained_path, q2_path, e_path, f_path)]
    if len(retained["reports"]) != 26 or retained["failed_quarters"]:
        raise ValueError("Incomplete final retained reconciliation")
    if e["raw_receipt"]["sha256"] != f["raw_receipt"]["sha256"]:
        raise ValueError("E/F raw frames differ")
    original = {(r["year"], r["quarter"]): r["before"] for r in historical["reports"]}
    original[(2026, 2)] = q2["reports"]["bs"]["before"]
    files, evidence, known = [], [], set()
    def add_evidence(item):
        path = Path(item["path"])
        actual = receipt(path)
        if actual["sha256"] != item["sha256"]:
            raise ValueError("Source evidence changed")
        if actual["path"] not in known:
            known.add(actual["path"])
            evidence.append(actual)
    for p in (historical_path, retained_path, q2_path, e_path, f_path,
              root / "docs/METHOD_bs_historical_reconciliation_20260906.md"):
        add_evidence(receipt(p))
    for report in historical["reports"]:
        for item in report["sources"]:
            add_evidence(item)
    for report in retained["reports"]:
        year, quarter = report["year"], report["quarter"]
        if report["status"] != "staged_retained_verified" or report["candidate_identity"]["violation_tickers"]:
            raise ValueError("Final quarter is not verified")
        for item in (report["all_source"], *report["individual_anchors"]):
            add_evidence(item)
        candidate = report["candidate"]
        if receipt(Path(candidate["path"]))["sha256"] != candidate["sha256"]:
            raise ValueError("Candidate changed")
        target = Path("資產負債") / f"bs_{year}Q{quarter}.csv"
        current = receipt(root / target)["sha256"]
        approved_baselines = {original[(year, quarter)]["sha256"], candidate["sha256"]}
        if (year, quarter) == (2026, 2):
            approved_baselines.add(q2["reports"]["bs"]["candidate"]["sha256"])
        if current not in approved_baselines:
            raise ValueError(f"Unexpected concurrent production source: {target}")
        files.append({"source": candidate["path"], "target": target.as_posix(),
                      "before_sha256": current, "after_sha256": candidate["sha256"]})
    result = {"label": "bs_26_quarter_official_repair", "evidence": evidence, "files": files,
              "scope": "26 BS source CSVs only; historical parser corrections + exact retained official filings. No DB/model/ledger writes or retraining.",
              "E_feature_changed_cells": e["changed_cells"], "F_feature_changed_cells": f["changed_cells"],
              "status": "reviewed_source_candidate_plan; root owns publication and model E acceptance"}
    target = retained_path.parent / "promotion_plan.json"
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"plan": receipt(target), "files": len(files), "evidence": len(evidence),
                      "E_changed_cells": e["changed_cells"], "F_changed_cells": f["changed_cells"]}, ensure_ascii=True))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    build(args.root.resolve())
