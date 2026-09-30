"""INC-Stage1-001 same-snapshot A/B report.

This incident script compares:

1. Stored pre-cleanup predictions vs the regenerated pinned Stage1 binary.
2. Regenerated Stage1 + old Stage2 vs regenerated Stage1 + retrained Stage2
   on the same latest snapshot frame.

It intentionally does not update production pins.  The original Stage1 binary
is gone, so this report is a closure/safety artifact rather than a promotion
script.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import lightgbm as lgb
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ml.config import MODEL_DIR, REPORT_DIR  # noqa: E402
from ml.dataset import apply_snapshot_zscore  # noqa: E402
from ml.predict import _apply_two_stage_ranking  # noqa: E402


DEFAULT_STAGE1_META = Path(MODEL_DIR) / "lgbm_v2_20260508_200128_meta.json"
DEFAULT_OLD_STAGE2_META = Path(MODEL_DIR) / "lgbm_two_stage_ranker_20260509_011500_meta.json"
DEFAULT_SNAPSHOT = Path(MODEL_DIR) / "snapshot_cache.pkl"
DEFAULT_PREDICTIONS = Path(MODEL_DIR) / "predictions_2026-05-15.csv"
DEFAULT_SIGNALS = Path(MODEL_DIR) / "unified_signals_2026-05-15.csv"


def _fmt_pct(value: Any, digits: int = 2) -> str:
    try:
        number = float(value)
    except Exception:
        return "-"
    if not math.isfinite(number):
        return "-"
    return f"{number * 100:+.{digits}f}%"


def _fmt_float(value: Any, digits: int = 4) -> str:
    try:
        number = float(value)
    except Exception:
        return "-"
    if not math.isfinite(number):
        return "-"
    return f"{number:.{digits}f}"


def _load_meta(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _predict_stage1(stage1_meta_path: Path, snapshot_path: Path) -> tuple[pd.DataFrame, pd.DataFrame, list[str]]:
    meta = _load_meta(stage1_meta_path)
    feature_cols = list(meta["feature_columns"])
    snapshot = pd.read_pickle(snapshot_path)
    snapshot["ticker"] = snapshot["ticker"].astype(str).str.zfill(4)
    for col in feature_cols:
        if col not in snapshot.columns:
            snapshot[col] = np.nan
    feature_frame = apply_snapshot_zscore(snapshot.copy()) if meta.get("cross_sectional_zscore") else snapshot.copy()
    x = pd.DataFrame(
        {col: feature_frame[col].values if col in feature_frame.columns else np.full(len(feature_frame), np.nan) for col in feature_cols}
    )
    model = lgb.Booster(model_file=meta["model_file"])
    out = pd.DataFrame(
        {
            "ticker": snapshot["ticker"].astype(str).str.zfill(4),
            "date": pd.to_datetime(snapshot["Date"]).dt.strftime("%Y-%m-%d"),
            "regen_pred_return_20d": model.predict(x.values),
        }
    )
    return out, x, feature_cols


def _stored_diff(
    stored_predictions_path: Path,
    stored_signals_path: Path,
    regen: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    pred = pd.read_csv(stored_predictions_path, dtype={"ticker": str}, encoding="utf-8-sig")
    pred["ticker"] = pred["ticker"].astype(str).str.zfill(4)
    pred["date"] = pred["date"].astype(str)
    pred["stored_pred_return_20d"] = pd.to_numeric(pred["pred_return_20d"], errors="coerce")
    all_diff = pred[["ticker", "date", "stored_pred_return_20d"]].merge(regen, on=["ticker", "date"], how="left")
    all_diff["stage1_diff"] = all_diff["regen_pred_return_20d"] - all_diff["stored_pred_return_20d"]
    all_diff["stage1_abs_diff"] = all_diff["stage1_diff"].abs()

    signals = pd.read_csv(stored_signals_path, dtype={"ticker": str}, encoding="utf-8-sig")
    signals["ticker"] = signals["ticker"].astype(str).str.zfill(4)
    top_diff = signals[["ticker"]].merge(all_diff, on="ticker", how="left")

    def _summary(frame: pd.DataFrame) -> dict[str, Any]:
        clean = frame.dropna(subset=["stage1_abs_diff"])
        return {
            "rows": int(len(frame)),
            "valid": int(len(clean)),
            "max_abs_diff": float(clean["stage1_abs_diff"].max()) if len(clean) else float("nan"),
            "mean_abs_diff": float(clean["stage1_abs_diff"].mean()) if len(clean) else float("nan"),
            "median_abs_diff": float(clean["stage1_abs_diff"].median()) if len(clean) else float("nan"),
            "p95_abs_diff": float(clean["stage1_abs_diff"].quantile(0.95)) if len(clean) else float("nan"),
        }

    return all_diff, top_diff, {"all_predictions": _summary(all_diff), "unified_top30": _summary(top_diff)}


def _apply_stage2(
    regen: pd.DataFrame,
    feature_frame: pd.DataFrame,
    feature_cols: list[str],
    stage2_meta_path: Path,
) -> pd.DataFrame:
    meta = _load_meta(stage2_meta_path)
    model = lgb.Booster(model_file=meta["model_file"])
    base = pd.DataFrame(
        {
            "ticker": regen["ticker"].astype(str).str.zfill(4),
            "pred_return_20d": pd.to_numeric(regen["regen_pred_return_20d"], errors="coerce"),
            "leaderboard_score": pd.to_numeric(regen["regen_pred_return_20d"], errors="coerce"),
        }
    )
    ranked = _apply_two_stage_ranking(base, feature_frame, feature_cols, model, meta)
    ranked = ranked[ranked["two_stage_rank"].notna()].copy()
    ranked["two_stage_rank"] = ranked["two_stage_rank"].astype(int)
    return ranked.sort_values("two_stage_rank").head(int(meta.get("top_n", 30))).copy()


def _rank_compare(old: pd.DataFrame, new: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    old_small = old[["ticker", "two_stage_rank", "two_stage_score", "pred_return_20d"]].rename(
        columns={
            "two_stage_rank": "old_rank",
            "two_stage_score": "old_stage2_score",
            "pred_return_20d": "regen_stage1_score",
        }
    )
    new_small = new[["ticker", "two_stage_rank", "two_stage_score", "pred_return_20d"]].rename(
        columns={
            "two_stage_rank": "new_rank",
            "two_stage_score": "new_stage2_score",
            "pred_return_20d": "new_regen_stage1_score",
        }
    )
    merged = old_small.merge(new_small, on="ticker", how="outer")
    merged["in_old_top30"] = merged["old_rank"].notna()
    merged["in_new_top30"] = merged["new_rank"].notna()
    merged["rank_delta_new_minus_old"] = merged["new_rank"] - merged["old_rank"]
    old_set = set(old["ticker"].astype(str))
    new_set = set(new["ticker"].astype(str))
    overlap = len(old_set & new_set)
    union = len(old_set | new_set)
    common = merged.dropna(subset=["old_rank", "new_rank"])
    rank_spearman = common["old_rank"].corr(common["new_rank"], method="spearman") if len(common) >= 2 else float("nan")
    summary = {
        "old_top30_count": int(len(old_set)),
        "new_top30_count": int(len(new_set)),
        "overlap": int(overlap),
        "jaccard": float(overlap / union) if union else float("nan"),
        "rank_spearman_common": float(rank_spearman) if pd.notna(rank_spearman) else float("nan"),
        "old_only": sorted(old_set - new_set),
        "new_only": sorted(new_set - old_set),
    }
    return merged.sort_values(["new_rank", "old_rank"], na_position="last"), summary


def _write_report(
    path: Path,
    *,
    payload: dict[str, Any],
    compare_csv: Path,
    topdiff_csv: Path,
) -> None:
    stage1 = payload["stage1_diff_summary"]
    rank = payload["rank_summary"]
    lines = [
        "# INC-Stage1-001 Report",
        "",
        f"- Generated at: `{payload['generated_at']}`",
        f"- Stage1 meta: `{payload['stage1_meta']}`",
        f"- Old Stage2 meta: `{payload['old_stage2_meta']}`",
        f"- New Stage2 meta: `{payload['new_stage2_meta']}`",
        f"- Snapshot: `{payload['snapshot']}`",
        "",
        "## Stage1 Sanity",
        "",
        "| Scope | Rows | Max abs diff | Mean abs diff | P95 abs diff | Gate |",
        "|---|---:|---:|---:|---:|:---:|",
    ]
    for scope in ["unified_top30", "all_predictions"]:
        row = stage1[scope]
        gate = "PASS" if row["max_abs_diff"] <= 0.005 else "FAIL"
        lines.append(
            f"| {scope} | {row['valid']}/{row['rows']} | {_fmt_pct(row['max_abs_diff'])} | "
            f"{_fmt_pct(row['mean_abs_diff'])} | {_fmt_pct(row['p95_abs_diff'])} | {gate} |"
        )
    lines.extend(
        [
            "",
            "Gate threshold: max abs diff <= `0.50pp`. The regenerated Stage1 does **not** reproduce the pre-cleanup pinned binary.",
            "",
            "## Same-Snapshot Stage2 A/B",
            "",
            "| Metric | Value |",
            "|---|---:|",
            f"| Old Top30 count | {rank['old_top30_count']} |",
            f"| New Top30 count | {rank['new_top30_count']} |",
            f"| Overlap | {rank['overlap']} |",
            f"| Jaccard | {_fmt_float(rank['jaccard'], 3)} |",
            f"| Common-rank Spearman | {_fmt_float(rank['rank_spearman_common'], 3)} |",
            "",
            f"- Old-only tickers: `{', '.join(rank['old_only'])}`",
            f"- New-only tickers: `{', '.join(rank['new_only'])}`",
            "",
            "## Closure Verdict",
            "",
            "INC-Stage1-001 is **not safe to close as PASS**.",
            "",
            "Reason:",
            "1. The original Stage1 binary is unrecoverable in local/git/LFS/backup search.",
            "2. The regenerated Stage1 fails the <=0.50pp sanity gate by a wide margin.",
            "3. A new Stage2 can be trained and compared on the latest snapshot, but this cannot recreate the original pre-cleanup CL3 baseline.",
            "",
            "Recommended PM action: keep Champion operationally pinned as-is only with `stage1_incident_tainted=True`, and rebuild a fresh coherent baseline before using #6/#2 Stage B model-derived attribution.",
            "",
            "## Artifacts",
            "",
            f"- Top30 comparison CSV: `{compare_csv}`",
            f"- Stage1 top diff CSV: `{topdiff_csv}`",
            f"- JSON: `{path.with_suffix('.json')}`",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage1-meta", type=Path, default=DEFAULT_STAGE1_META)
    parser.add_argument("--old-stage2-meta", type=Path, default=DEFAULT_OLD_STAGE2_META)
    parser.add_argument("--new-stage2-meta", type=Path, required=True)
    parser.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT)
    parser.add_argument("--stored-predictions", type=Path, default=DEFAULT_PREDICTIONS)
    parser.add_argument("--stored-signals", type=Path, default=DEFAULT_SIGNALS)
    parser.add_argument("--output-prefix", default=f"inc_stage1_001_{datetime.now().strftime('%Y%m%d')}")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report_dir = Path(REPORT_DIR)
    report_dir.mkdir(parents=True, exist_ok=True)

    regen, feature_frame, feature_cols = _predict_stage1(args.stage1_meta, args.snapshot)
    _, top_diff, stage1_summary = _stored_diff(args.stored_predictions, args.stored_signals, regen)
    old_ranked = _apply_stage2(regen, feature_frame, feature_cols, args.old_stage2_meta)
    new_ranked = _apply_stage2(regen, feature_frame, feature_cols, args.new_stage2_meta)
    comparison, rank_summary = _rank_compare(old_ranked, new_ranked)

    prefix = report_dir / args.output_prefix
    compare_csv = Path(f"{prefix}_same_snapshot_top30.csv")
    topdiff_csv = Path(f"{prefix}_stage1_top30_diff.csv")
    json_path = Path(f"{prefix}.json")
    report_path = Path(f"{prefix}.md")
    comparison.to_csv(compare_csv, index=False, encoding="utf-8-sig")
    top_diff.sort_values("stage1_abs_diff", ascending=False).to_csv(topdiff_csv, index=False, encoding="utf-8-sig")
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "stage1_meta": str(args.stage1_meta),
        "old_stage2_meta": str(args.old_stage2_meta),
        "new_stage2_meta": str(args.new_stage2_meta),
        "snapshot": str(args.snapshot),
        "stored_predictions": str(args.stored_predictions),
        "stored_signals": str(args.stored_signals),
        "stage1_diff_summary": stage1_summary,
        "rank_summary": rank_summary,
        "same_snapshot_csv": str(compare_csv),
        "stage1_topdiff_csv": str(topdiff_csv),
        "report": str(report_path),
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_report(report_path, payload=payload, compare_csv=compare_csv, topdiff_csv=topdiff_csv)
    print(f"[inc-stage1-001] report={report_path}")
    print(
        "[inc-stage1-001] "
        f"stage1_top30_max_abs={stage1_summary['unified_top30']['max_abs_diff']:.4%} "
        f"jaccard={rank_summary['jaccard']:.3f} "
        "verdict=FAIL"
    )


if __name__ == "__main__":
    main()
