"""Cross-check current model picks with the latest CMoney ChipK snapshot.

This is a same-day diagnostic, not a historical backtest.  It answers:
which model buy candidates are confirmed by ChipK main-force shape, and which
look like weak/trap-risk names.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))

from ml.chipk import load_latest_chipk_main_force, redact_chipk_source_path  # noqa: E402
from ml.config import MODEL_DIR, REPORT_DIR  # noqa: E402

PRED_RE = re.compile(r"^predictions_(\d{4}-\d{2}-\d{2})\.csv$")
BUY_WORD = chr(0x8CB7) + chr(0x9032)
DEFAULT_TOP_N = 30


def _latest_prediction_path() -> Path:
    candidates: list[tuple[str, Path]] = []
    for path in Path(MODEL_DIR).glob("predictions_*.csv"):
        match = PRED_RE.match(path.name)
        if match:
            candidates.append((match.group(1), path))
    if not candidates:
        raise FileNotFoundError("No predictions_YYYY-MM-DD.csv files found")
    return sorted(candidates)[-1][1]


def _safe_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _jsonable(value: Any) -> Any:
    if pd.isna(value):
        return None
    if isinstance(value, (int, float, str, bool)):
        return value
    return str(value)


def _format_pct(value: Any) -> str:
    number = _safe_float(value)
    if number is None:
        return "-"
    return f"{number * 100.0:+.2f}%"


def _format_num(value: Any, digits: int = 3) -> str:
    number = _safe_float(value)
    if number is None:
        return "-"
    return f"{number:.{digits}f}"


def _sort_model_buys(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["_leaderboard_sort"] = pd.to_numeric(out.get("leaderboard_score"), errors="coerce")
    out["_pred_sort"] = pd.to_numeric(out.get("pred_return_20d"), errors="coerce")
    return out.sort_values(
        ["_leaderboard_sort", "_pred_sort", "ticker"],
        ascending=[False, False, True],
        na_position="last",
    ).drop(columns=["_leaderboard_sort", "_pred_sort"], errors="ignore")


def _model_chipk_decision(row: pd.Series) -> str:
    alignment = str(row.get("chipk_entry_alignment") or "")
    if alignment in {"CONFIRM_ENTRY", "SUPPORTIVE_ENTRY"}:
        return "model_chipk_confirmed"
    if alignment in {"DO_NOT_CHASE", "AVOID"}:
        return "model_chipk_conflict"
    if alignment in {"WAIT_FOR_TURN", "WATCH"}:
        return "model_chipk_watch"
    return "chipk_missing"


def _table(rows: pd.DataFrame, limit: int = 15) -> list[str]:
    cols = [
        "ticker",
        "chipk_name",
        "recommendation",
        "pred_return_20d",
        "leaderboard_score",
        "chipk_bucket",
        "chipk_main_force_pattern",
        "chipk_entry_alignment",
        "chipk_main_force_1d",
        "chipk_main_force_5d",
        "chipk_main_force_20d",
        "chipk_main_force_score",
    ]
    if rows.empty:
        return ["_none_"]
    lines = [
        "| ticker | name | rec | pred20 | lb_score | bucket | pattern | action | 1D | 5D | 20D | score |",
        "|---|---|---|---:|---:|---|---|---|---:|---:|---:|---:|",
    ]
    for _, row in rows.head(limit).iterrows():
        values = {col: row.get(col) for col in cols}
        lines.append(
            "| "
            + " | ".join(
                [
                    str(values["ticker"]),
                    str(values["chipk_name"] or ""),
                    str(values["recommendation"] or ""),
                    _format_pct(values["pred_return_20d"]),
                    _format_num(values["leaderboard_score"], 3),
                    str(values["chipk_bucket"] or ""),
                    str(values["chipk_main_force_pattern"] or ""),
                    str(values["chipk_entry_alignment"] or ""),
                    _format_num(values["chipk_main_force_1d"], 0),
                    _format_num(values["chipk_main_force_5d"], 0),
                    _format_num(values["chipk_main_force_20d"], 0),
                    _format_num(values["chipk_main_force_score"], 3),
                ]
            )
            + " |"
        )
    return lines


def _write_markdown(path: Path, payload: dict[str, Any], buys: pd.DataFrame) -> None:
    confirmed = buys[buys["model_chipk_decision"].eq("model_chipk_confirmed")]
    conflict = buys[buys["model_chipk_decision"].eq("model_chipk_conflict")]
    watch = buys[buys["model_chipk_decision"].eq("model_chipk_watch")]
    lines = [
        "# ChipK Model Diagnosis",
        "",
        "## Scope",
        f"- prediction_date: `{payload['prediction_date']}`",
        f"- chipk_asof_date: `{payload['chipk_asof_date']}`",
        f"- model_buy_count: `{payload['model_buy_count']}`",
        "- status: diagnostic only, not historical backtest",
        "",
        "## Summary",
        f"- confirmed: `{payload['decision_counts'].get('model_chipk_confirmed', 0)}`",
        f"- watch: `{payload['decision_counts'].get('model_chipk_watch', 0)}`",
        f"- conflict/do-not-chase: `{payload['decision_counts'].get('model_chipk_conflict', 0)}`",
        f"- top{payload['top_n']} support_or_strong_rate: `{payload['top_n_summary']['support_or_strong_rate']}`",
        f"- top{payload['top_n']} weak_rate: `{payload['top_n_summary']['weak_rate']}`",
        "",
        "## Confirmed",
        *_table(confirmed, limit=20),
        "",
        "## Conflict / Do Not Chase",
        *_table(conflict, limit=20),
        "",
        "## Watch",
        *_table(watch, limit=20),
        "",
        "## Data Boundary",
        "- Current ChipK file is a latest snapshot. It cannot be used as historical backtest input.",
        "- Trap-risk labels are inferred from 1D/5D/20D main-force shape only.",
        "- Broker-detail, retail-flow, and signed main buy/sell exports are still required for hard gates.",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run(args: argparse.Namespace) -> dict[str, Any]:
    pred_path = Path(args.prediction_path).resolve() if args.prediction_path else _latest_prediction_path()
    pred = pd.read_csv(pred_path, encoding="utf-8-sig", dtype={"ticker": str})
    chipk, meta = load_latest_chipk_main_force(args.chipk_path)
    if meta.status != "ok" or chipk.empty:
        raise RuntimeError(f"ChipK snapshot unavailable: {meta.status} {meta.message}")

    keep = [
        "ticker",
        "chipk_name",
        "chipk_close",
        "chipk_main_force_1d",
        "chipk_main_force_5d",
        "chipk_main_force_20d",
        "chipk_main_force_score",
        "chipk_bucket",
        "chipk_main_force_pattern",
        "chipk_entry_alignment",
        "chipk_trap_risk",
        "chipk_distribution_risk",
        "chipk_risk_flags",
    ]
    chipk_columns = [col for col in keep if col != "ticker"]
    pred_for_merge = pred.drop(columns=[col for col in chipk_columns if col in pred.columns], errors="ignore")
    merged = pred_for_merge.merge(chipk[[col for col in keep if col in chipk.columns]], on="ticker", how="left")
    recommendation = merged.get("recommendation", pd.Series("", index=merged.index)).fillna("").astype(str)
    buys = _sort_model_buys(merged[recommendation.str.contains(BUY_WORD, regex=False)].copy())
    buys["model_chipk_decision"] = buys.apply(_model_chipk_decision, axis=1)

    top_n = min(max(1, int(args.top_n)), len(buys)) if len(buys) else int(args.top_n)
    top = buys.head(top_n)
    support_mask = top["chipk_bucket"].isin(["supportive", "strong"]) if not top.empty else pd.Series(dtype=bool)
    weak_mask = top["chipk_bucket"].eq("weak") if not top.empty else pd.Series(dtype=bool)
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "prediction_path": str(pred_path),
        "prediction_date": str(pred["date"].iloc[0]) if "date" in pred.columns and not pred.empty else None,
        "chipk_asof_date": meta.asof_date,
        "chipk_source_path": redact_chipk_source_path(meta.source_path),
        "chipk_rows": meta.row_count,
        "chipk_history_available": meta.history_available,
        "model_buy_count": int(len(buys)),
        "decision_counts": {str(k): int(v) for k, v in buys["model_chipk_decision"].value_counts().items()},
        "bucket_counts": {str(k): int(v) for k, v in buys["chipk_bucket"].value_counts(dropna=False).items()},
        "pattern_counts": {str(k): int(v) for k, v in buys["chipk_main_force_pattern"].value_counts(dropna=False).items()},
        "top_n": int(top_n),
        "top_n_summary": {
            "support_or_strong_rate": f"{float(support_mask.mean()) * 100.0:.1f}%" if len(top) else "n/a",
            "weak_rate": f"{float(weak_mask.mean()) * 100.0:.1f}%" if len(top) else "n/a",
            "avg_chipk_score": _safe_float(top["chipk_main_force_score"].mean()) if len(top) else None,
        },
    }

    out_prefix = args.output_prefix or datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = Path(REPORT_DIR) / f"chipk_model_diagnosis_{out_prefix}.csv"
    json_path = Path(REPORT_DIR) / f"chipk_model_diagnosis_{out_prefix}.json"
    md_path = Path(REPORT_DIR) / f"chipk_model_diagnosis_{out_prefix}.md"
    export_cols = [
        "ticker",
        "chipk_name",
        "recommendation",
        "pred_return_20d",
        "leaderboard_score",
        "risk_tags",
        "chipk_bucket",
        "chipk_main_force_pattern",
        "chipk_entry_alignment",
        "model_chipk_decision",
        "chipk_risk_flags",
        "chipk_main_force_1d",
        "chipk_main_force_5d",
        "chipk_main_force_20d",
        "chipk_main_force_score",
    ]
    buys[[col for col in export_cols if col in buys.columns]].to_csv(csv_path, index=False, encoding="utf-8-sig")
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=_jsonable) + "\n", encoding="utf-8")
    _write_markdown(md_path, payload, buys)
    result = {**payload, "csv": str(csv_path), "json": str(json_path), "md": str(md_path)}
    print(json.dumps(result, ensure_ascii=False))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Cross-check model buys against latest ChipK snapshot")
    parser.add_argument("--prediction-path", default=None)
    parser.add_argument("--chipk-path", default=None)
    parser.add_argument("--top-n", type=int, default=DEFAULT_TOP_N)
    parser.add_argument("--output-prefix", default=None)
    args = parser.parse_args()
    run(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
