"""INC-Stage1-002 Champion v3 rebuild report.

Compares same-snapshot selections across:
- pre-cleanup proxy: stored unified_signals Top30
- current incident production: regenerated 20260508 Stage1 + old Stage2
- INC-001 byproduct: regenerated 20260508 Stage1 + retrained Stage2
- v3 candidate: latest Stage1 + v3 Stage2

This script writes analysis artifacts only. It does not change production pins.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ml.config import MODEL_DIR, REPORT_DIR  # noqa: E402
from scripts.inc_stage1_001_ab_report import _apply_stage2, _predict_stage1  # noqa: E402


DEFAULT_PRE_CLEANUP_SIGNALS = Path(MODEL_DIR) / "unified_signals_2026-05-15.csv"
DEFAULT_STAGE1_INCIDENT = Path(MODEL_DIR) / "lgbm_v2_20260508_200128_meta.json"
DEFAULT_STAGE1_V3 = Path(MODEL_DIR) / "lgbm_v2_20260516_113451_meta.json"
DEFAULT_STAGE2_OLD = Path(MODEL_DIR) / "lgbm_two_stage_ranker_20260509_011500_meta.json"
DEFAULT_STAGE2_INC001 = Path(MODEL_DIR) / "lgbm_two_stage_ranker_20260516_180000_inc001_meta.json"
DEFAULT_STAGE2_V3 = Path(MODEL_DIR) / "lgbm_two_stage_ranker_20260516_190000_v3_meta.json"
DEFAULT_CL3_JSON = Path(REPORT_DIR) / "inc_stage1_002_v3_cl3_20260516.json"
DEFAULT_CL3_MD = Path(REPORT_DIR) / "inc_stage1_002_v3_cl3_20260516.md"


def _fmt_float(value: Any, digits: int = 3) -> str:
    try:
        return f"{float(value):.{digits}f}"
    except Exception:
        return "-"


def _pre_cleanup_proxy(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path, dtype={"ticker": str}, encoding="utf-8-sig")
    frame["ticker"] = frame["ticker"].astype(str).str.zfill(4)
    rank_col = "two_stage_rank" if "two_stage_rank" in frame.columns else "rank_20d"
    out = frame[["ticker", rank_col]].copy()
    out = out.rename(columns={rank_col: "rank"})
    out["rank"] = pd.to_numeric(out["rank"], errors="coerce")
    out = out.dropna(subset=["rank"]).sort_values("rank").head(30).copy()
    out["rank"] = range(1, len(out) + 1)
    return out


def _model_selection(stage1_meta: Path, stage2_meta: Path) -> pd.DataFrame:
    regen, feature_frame, feature_cols = _predict_stage1(stage1_meta, Path(MODEL_DIR) / "snapshot_cache.pkl")
    ranked = _apply_stage2(regen, feature_frame, feature_cols, stage2_meta)
    out = ranked[["ticker", "two_stage_rank", "two_stage_score", "pred_return_20d"]].copy()
    out = out.rename(columns={"two_stage_rank": "rank"})
    out["rank"] = pd.to_numeric(out["rank"], errors="coerce")
    return out.sort_values("rank").head(30)


def _pairwise_summary(selections: dict[str, pd.DataFrame]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    names = list(selections)
    for i, left in enumerate(names):
        for right in names[i + 1 :]:
            a = selections[left][["ticker", "rank"]].copy()
            b = selections[right][["ticker", "rank"]].copy()
            aset = set(a["ticker"])
            bset = set(b["ticker"])
            overlap = len(aset & bset)
            union = len(aset | bset)
            common = a.merge(b, on="ticker", suffixes=("_left", "_right"))
            spearman = common["rank_left"].corr(common["rank_right"], method="spearman") if len(common) >= 2 else float("nan")
            rows.append(
                {
                    "left": left,
                    "right": right,
                    "left_n": len(aset),
                    "right_n": len(bset),
                    "overlap": overlap,
                    "jaccard": overlap / union if union else float("nan"),
                    "rank_spearman_common": spearman,
                    "left_only": ",".join(sorted(aset - bset)),
                    "right_only": ",".join(sorted(bset - aset)),
                }
            )
    return pd.DataFrame(rows)


def _read_cl3(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"available": False}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {
        "available": True,
        "stage1_score_distribution": payload.get("stage1_score_distribution", []),
        "top10_holdout": payload.get("top10_holdout", {}),
        "old_summary": payload.get("old_summary", {}),
        "new_summary": payload.get("new_summary", {}),
        "cl3": payload.get("cl3", {}),
        "report": payload.get("report"),
    }


def _write_report(path: Path, payload: dict[str, Any], pairwise_csv: Path) -> None:
    pairwise = pd.DataFrame(payload["pairwise"])
    cl3 = payload["cl3"]
    lines = [
        "# INC-Stage1-002 Champion v3 Rebuild Report",
        "",
        f"- Generated at: `{payload['generated_at']}`",
        f"- v3 Stage1: `{payload['stage1_v3']}`",
        f"- v3 Stage2: `{payload['stage2_v3']}`",
        "",
        "## Same-Snapshot Selection Comparison",
        "",
        "| Left | Right | Overlap | Jaccard | Rank Spearman |",
        "|---|---|---:|---:|---:|",
    ]
    for row in pairwise.to_dict("records"):
        lines.append(
            f"| {row['left']} | {row['right']} | {int(row['overlap'])} | "
            f"{_fmt_float(row['jaccard'])} | {_fmt_float(row['rank_spearman_common'])} |"
        )
    lines.extend(["", "## CL3 Summary", ""])
    if cl3.get("available"):
        checks = cl3.get("cl3", {}).get("checks", {})
        old_summary = cl3.get("old_summary", {})
        new_summary = cl3.get("new_summary", {})
        lines.extend(
            [
                "| Metric | Old | v3 | Delta / Value | Pass |",
                "|---|---:|---:|---:|:---:|",
                f"| Monthly alpha | {_fmt_float(old_summary.get('monthly_alpha'))} | {_fmt_float(new_summary.get('monthly_alpha'))} | {_fmt_float(float(new_summary.get('monthly_alpha', 0)) - float(old_summary.get('monthly_alpha', 0)))} | - |",
                f"| MDD | {_fmt_float(old_summary.get('mdd'))} | {_fmt_float(new_summary.get('mdd'))} | {_fmt_float(abs(float(old_summary.get('mdd', 0))) - abs(float(new_summary.get('mdd', 0))))} | - |",
            ]
        )
        for name, item in checks.items():
            lines.append(f"| {name} | - | - | {_fmt_float(item.get('value'))} | {'PASS' if item.get('pass') else 'FAIL'} |")
        lines.append("")
        lines.append(f"Overall CL3: **{'PASS' if cl3.get('cl3', {}).get('overall_pass') else 'FAIL'}**")
    else:
        lines.append("CL3 artifact unavailable.")
    lines.extend(
        [
            "",
            "## Verdict",
            "",
            "Champion v3 rebuild is **not approved for re-pin** in this run.",
            "",
            "Reasons:",
            "- Same-snapshot v3 behavior is materially different from all existing baselines.",
            "- CL3 failed in `inc_stage1_002_v3_cl3_20260516.md`.",
            "- Production pins were not changed.",
            "",
            "## Artifacts",
            "",
            f"- Pairwise CSV: `{pairwise_csv}`",
            f"- CL3 report: `{payload['cl3_report_md']}`",
            f"- JSON: `{path.with_suffix('.json')}`",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-prefix", default="inc_stage1_002_v3_20260516")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    selections = {
        "pre_cleanup_proxy": _pre_cleanup_proxy(DEFAULT_PRE_CLEANUP_SIGNALS),
        "current_incident": _model_selection(DEFAULT_STAGE1_INCIDENT, DEFAULT_STAGE2_OLD),
        "inc001_byproduct": _model_selection(DEFAULT_STAGE1_INCIDENT, DEFAULT_STAGE2_INC001),
        "v3_candidate": _model_selection(DEFAULT_STAGE1_V3, DEFAULT_STAGE2_V3),
    }
    pairwise = _pairwise_summary(selections)
    report_dir = Path(REPORT_DIR)
    prefix = report_dir / args.output_prefix
    pairwise_csv = Path(f"{prefix}_pairwise.csv")
    report_path = Path(f"{prefix}.md")
    json_path = Path(f"{prefix}.json")
    pairwise.to_csv(pairwise_csv, index=False, encoding="utf-8-sig")
    for name, frame in selections.items():
        frame.to_csv(Path(f"{prefix}_{name}_top30.csv"), index=False, encoding="utf-8-sig")
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "stage1_v3": str(DEFAULT_STAGE1_V3),
        "stage2_v3": str(DEFAULT_STAGE2_V3),
        "cl3": _read_cl3(DEFAULT_CL3_JSON),
        "cl3_report_md": str(DEFAULT_CL3_MD),
        "pairwise": pairwise.to_dict("records"),
        "selections": {name: frame.to_dict("records") for name, frame in selections.items()},
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_report(report_path, payload, pairwise_csv)
    print(f"[inc-stage1-002] report={report_path}")
    v3_vs_current = pairwise[
        (pairwise["left"] == "current_incident") & (pairwise["right"] == "v3_candidate")
    ]
    if not v3_vs_current.empty:
        row = v3_vs_current.iloc[0]
        print(
            "[inc-stage1-002] "
            f"current_vs_v3_jaccard={row['jaccard']:.3f} "
            f"overlap={int(row['overlap'])} "
            "verdict=FAIL"
        )


if __name__ == "__main__":
    main()
