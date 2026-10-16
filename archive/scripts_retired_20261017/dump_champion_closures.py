"""Dump V2 Champion paper ledger positions into closure artifacts.

The resulting parquet files are fixed-snapshot inputs for later same-entry
exit-policy replay. The script reads the Champion DB and unified signal CSVs
only; it does not mutate the production ledger.
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))

from ml.config import MODEL_DIR, REPORT_DIR  # noqa: E402

DEFAULT_DB_PATH = BASE_DIR / "paper_portfolio_v2_champion.db"
DEFAULT_OUTPUT_DIR = Path(REPORT_DIR) / "closures"
DEFAULT_START = "2026-04-23"
DEFAULT_END = "2026-04-29"


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Dump Champion DB positions into replay closure parquet artifacts."
    )
    parser.add_argument("--start", default=DEFAULT_START)
    parser.add_argument("--end", default=DEFAULT_END)
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument(
        "--closed-only",
        action="store_true",
        help="Exclude still-open positions from the dumped snapshot.",
    )
    return parser.parse_args(argv)


def _safe_json_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _safe_json_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_safe_json_value(item) for item in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        number = float(value)
        return number if math.isfinite(number) else None
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if pd.isna(value):
        return None
    return value


def _json_dumps(value: Any) -> str:
    return json.dumps(_safe_json_value(value), ensure_ascii=False, sort_keys=True)


def _load_positions(db_path: Path, start: str, end: str, closed_only: bool) -> pd.DataFrame:
    status_filter = "AND p.status IN ('closed', 'stopped_out')" if closed_only else ""
    query = f"""
        SELECT
            p.*,
            r.signals_file_path,
            r.signals_sha256,
            r.rule_version
        FROM unified_positions p
        JOIN unified_runs r ON r.id = p.opened_by_run_id
        WHERE p.prediction_date BETWEEN ? AND ?
          {status_filter}
        ORDER BY p.prediction_date, p.ticker
    """
    with sqlite3.connect(db_path) as con:
        positions = pd.read_sql_query(query, con, params=(start, end), dtype={"ticker": str})
    if not positions.empty:
        positions["ticker"] = positions["ticker"].astype(str).str.zfill(4)
    return positions


def _load_marks(db_path: Path, position_ids: list[int]) -> pd.DataFrame:
    if not position_ids:
        return pd.DataFrame()
    placeholders = ",".join("?" for _ in position_ids)
    query = f"""
        SELECT *
        FROM unified_marks
        WHERE position_id IN ({placeholders})
        ORDER BY position_id, trading_day_number, mark_date
    """
    with sqlite3.connect(db_path) as con:
        return pd.read_sql_query(query, con, params=position_ids)


def _read_signal_rows(positions: pd.DataFrame) -> dict[tuple[str, str], dict[str, Any]]:
    cache: dict[str, pd.DataFrame] = {}
    rows: dict[tuple[str, str], dict[str, Any]] = {}
    for item in positions.itertuples(index=False):
        prediction_date = str(item.prediction_date)
        ticker = str(item.ticker).zfill(4)
        raw_path = getattr(item, "signals_file_path", None)
        path = Path(str(raw_path)) if raw_path else Path(MODEL_DIR) / f"unified_signals_{prediction_date}.csv"
        if not path.exists():
            fallback = Path(MODEL_DIR) / f"unified_signals_{prediction_date}.csv"
            path = fallback
        cache_key = str(path)
        if cache_key not in cache:
            if path.exists():
                frame = pd.read_csv(path, encoding="utf-8-sig", dtype={"ticker": str})
                frame["ticker"] = frame["ticker"].astype(str).str.zfill(4)
                cache[cache_key] = frame
            else:
                cache[cache_key] = pd.DataFrame()
        frame = cache[cache_key]
        if frame.empty:
            rows[(prediction_date, ticker)] = {
                "signal_file_found": False,
                "signal_file_path": str(path),
            }
            continue
        matched = frame[frame["ticker"] == ticker]
        if matched.empty:
            rows[(prediction_date, ticker)] = {
                "signal_file_found": True,
                "signal_row_found": False,
                "signal_file_path": str(path),
            }
            continue
        raw = matched.iloc[0].to_dict()
        raw["signal_file_found"] = True
        raw["signal_row_found"] = True
        raw["signal_file_path"] = str(path)
        rows[(prediction_date, ticker)] = raw
    return rows


def _latest_mark_by_position(marks: pd.DataFrame) -> pd.DataFrame:
    if marks.empty:
        return pd.DataFrame(columns=["position_id", "snapshot_date", "snapshot_close"])
    latest = marks.sort_values(["position_id", "mark_date"]).groupby("position_id").tail(1)
    return latest[["position_id", "mark_date", "close"]].rename(
        columns={"mark_date": "snapshot_date", "close": "snapshot_close"}
    )


def _build_artifacts(
    positions: pd.DataFrame,
    marks: pd.DataFrame,
    raw_rows: dict[tuple[str, str], dict[str, Any]],
) -> pd.DataFrame:
    latest_marks = _latest_mark_by_position(marks)
    result = positions.merge(latest_marks, left_on="id", right_on="position_id", how="left")
    result = result.drop(columns=["position_id"], errors="ignore")
    mark_groups = {
        int(position_id): group.drop(columns=["id"], errors="ignore").to_dict(orient="records")
        for position_id, group in marks.groupby("position_id")
    }

    raw_feature_json = []
    marks_json = []
    closure_dates = []
    closure_date_sources = []
    closure_statuses = []
    for item in result.itertuples(index=False):
        prediction_date = str(item.prediction_date)
        ticker = str(item.ticker).zfill(4)
        raw_feature_json.append(_json_dumps(raw_rows.get((prediction_date, ticker), {})))
        marks_json.append(_json_dumps(mark_groups.get(int(item.id), [])))

        exit_date = getattr(item, "exit_date", None)
        snapshot_date = getattr(item, "snapshot_date", None)
        if isinstance(exit_date, str) and exit_date:
            closure_dates.append(exit_date)
            closure_date_sources.append("exit_date")
            closure_statuses.append("realized")
        elif isinstance(snapshot_date, str) and snapshot_date:
            closure_dates.append(snapshot_date)
            closure_date_sources.append("snapshot_date")
            closure_statuses.append("mark_to_market")
        else:
            closure_dates.append(prediction_date)
            closure_date_sources.append("prediction_date_fallback")
            closure_statuses.append("no_mark")

    result["raw_features_json"] = raw_feature_json
    result["marks_json"] = marks_json
    result["closure_date"] = closure_dates
    result["closure_date_source"] = closure_date_sources
    result["closure_status"] = closure_statuses
    result = result.rename(columns={"id": "position_id"})

    preferred = [
        "position_id",
        "prediction_date",
        "ticker",
        "sector",
        "status",
        "closure_status",
        "closure_date",
        "closure_date_source",
        "entry_signal_type",
        "current_signal_type",
        "target_units",
        "target_weight",
        "planned_entry_ref_price",
        "entry_date",
        "entry_price",
        "exit_date",
        "exit_price",
        "exit_reason",
        "realized_return_pct",
        "max_drawdown_pct",
        "max_drawdown_date",
        "max_drawdown_low",
        "days_observed",
        "last_mark_date",
        "snapshot_date",
        "snapshot_close",
        "order_type",
        "fill_status",
        "fill_price",
        "entry_slippage_pct",
        "exit_policy",
        "hold_days_target",
        "signals_file_path",
        "signals_sha256",
        "rule_version",
        "raw_features_json",
        "marks_json",
    ]
    columns = [column for column in preferred if column in result.columns]
    rest = [column for column in result.columns if column not in columns]
    return result[columns + rest]


def _write_manifest(
    path: Path,
    *,
    args: argparse.Namespace,
    artifacts: pd.DataFrame,
    files: list[dict[str, Any]],
) -> dict[str, Any]:
    status_counts = {
        str(key): int(value)
        for key, value in artifacts["status"].fillna("NULL").value_counts().sort_index().items()
    }
    closure_counts = {
        str(key): int(value)
        for key, value in artifacts["closure_status"].fillna("NULL").value_counts().sort_index().items()
    }
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "start": args.start,
        "end": args.end,
        "db_path": str(args.db_path),
        "closed_only": bool(args.closed_only),
        "position_count": int(len(artifacts)),
        "status_counts": status_counts,
        "closure_status_counts": closure_counts,
        "files": files,
        "schema_columns": list(artifacts.columns),
    }
    path.write_text(json.dumps(_safe_json_value(payload), ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def _write_markdown(path: Path, manifest: dict[str, Any]) -> None:
    lines = [
        "# Champion Closure Artifact Dump",
        "",
        "## Scope",
        "",
        f"- Generated at: {manifest['generated_at']}",
        f"- Window: {manifest['start']} to {manifest['end']}",
        f"- Positions: {manifest['position_count']}",
        f"- Closed only: {manifest['closed_only']}",
        "",
        "## Status Counts",
        "",
        "| status | count |",
        "| --- | ---: |",
    ]
    for key, value in manifest["status_counts"].items():
        lines.append(f"| {key} | {value} |")
    lines.extend(["", "## Closure Files", "", "| closure_date | rows | path |", "| --- | ---: | --- |"])
    for item in manifest["files"]:
        lines.append(f"| {item['closure_date']} | {item['rows']} | `{item['path']}` |")
    lines.extend(
        [
            "",
            "## Notes",
            "",
            "- Open positions are included as mark-to-market snapshots unless `--closed-only` is set.",
            "- `closure_date_source=exit_date` means realized; `closure_date_source=snapshot_date` means open MTM snapshot, not a completed exit.",
            "- `raw_features_json` stores the original unified signal row for same-entry replay.",
            "- `marks_json` stores the observed mark path from the Champion DB.",
            "- This dump is research-only and does not change production paper books.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def run_dump(args: argparse.Namespace) -> dict[str, Any]:
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    positions = _load_positions(Path(args.db_path), args.start, args.end, args.closed_only)
    marks = _load_marks(Path(args.db_path), positions["id"].astype(int).tolist() if not positions.empty else [])
    raw_rows = _read_signal_rows(positions) if not positions.empty else {}
    artifacts = _build_artifacts(positions, marks, raw_rows) if not positions.empty else pd.DataFrame()

    suffix = f"{args.start.replace('-', '')}_{args.end.replace('-', '')}"
    files: list[dict[str, Any]] = []
    if not artifacts.empty:
        for closure_date, group in artifacts.groupby("closure_date", dropna=False):
            date_label = str(closure_date).replace("-", "")
            parquet_path = output_dir / f"{date_label}.parquet"
            group.to_parquet(parquet_path, index=False)
            files.append(
                {
                    "closure_date": str(closure_date),
                    "rows": int(len(group)),
                    "path": str(parquet_path),
                }
            )

    manifest_path = output_dir / f"champion_closures_{suffix}_manifest.json"
    md_path = output_dir / f"champion_closures_{suffix}.md"
    manifest = _write_manifest(manifest_path, args=args, artifacts=artifacts, files=files)
    _write_markdown(md_path, manifest)
    return {
        "manifest": manifest,
        "manifest_path": str(manifest_path),
        "markdown": str(md_path),
        "files": files,
    }


def main(argv: list[str] | None = None) -> dict[str, Any]:
    args = _parse_args(argv)
    result = run_dump(args)
    manifest = result["manifest"]
    print(
        "[champion-closure-dump] "
        f"positions={manifest['position_count']} "
        f"files={len(result['files'])} "
        f"closed_only={manifest['closed_only']}"
    )
    print(f"[champion-closure-dump] manifest={result['manifest_path']}")
    print(f"[champion-closure-dump] report={result['markdown']}")
    return result


if __name__ == "__main__":
    main()
