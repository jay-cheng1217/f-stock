"""Replay and optionally promote the current Champion entry filter.

The replay rebuilds unified_signals from existing prediction CSVs with the
current live defaults:

- Two-Stage ranker enabled when the prediction file supports it.
- REQ-004 sector-conditioned OVERHEAT thresholds enabled.
- asymmetric_v2 exit policy in the rebuilt paper book.

Generated replay reports live under ml/reports/archive/ so runtime artifacts do
not pollute git status. Use --promote to back up and replace the live Champion
SQLite DB after the replay succeeds.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

import pandas as pd

BASE_DIR = Path(os.environ.get("STOCK_BASE_DIR") or Path(__file__).resolve().parents[1])
sys.path.insert(0, str(BASE_DIR))

from backend.services.warroom_service import export_warroom_json_feeds
from scripts.build_unified_signals import build_unified_signals
from scripts.exit_policies import EXIT_POLICY_ASYMMETRIC_V2
from scripts.update_unified_portfolio import (
    DEFAULT_DB_PATH as CHAMPION_DB_PATH,
    DEFAULT_RULE_VERSION,
    sync_unified_portfolio,
)

MODEL_DIR = BASE_DIR / "ml" / "models"
ARCHIVE_DIR = BASE_DIR / "ml" / "reports" / "archive" / "new_champion_entry_filter"
TWO_STAGE_CHAMPION_META_PATH = MODEL_DIR / "lgbm_two_stage_ranker_20260509_011500_meta.json"
TWO_STAGE_CHAMPION_STAGE1_META_PATH = MODEL_DIR / "lgbm_v2_20260508_200128_meta.json"


@dataclass
class SignalSummary:
    date: str
    signal_path: str
    rows: int
    selected_count: int
    blocked_count: int
    sector_threshold_rows: int
    default_threshold_rows: int
    penalty_overlay: str


def _prediction_dates(start_date: str, end_date: str | None) -> list[str]:
    dates: list[str] = []
    for path in sorted(MODEL_DIR.glob("predictions_????-??-??.csv")):
        date_str = path.stem.replace("predictions_", "")
        if date_str < start_date:
            continue
        if end_date and date_str > end_date:
            continue
        dates.append(date_str)
    if not dates:
        raise FileNotFoundError(
            f"No predictions_YYYY-MM-DD.csv files found from {start_date}"
            + (f" through {end_date}" if end_date else "")
        )
    return dates


def _summarize_signal(path: str, date_str: str, penalty_overlay: str) -> SignalSummary:
    df = pd.read_csv(path, encoding="utf-8-sig", dtype={"ticker": str})
    active = df.get("signal_type", pd.Series(dtype=object)).astype(str).ne("NONE")
    threshold_group = df.get("overheat_threshold_group", pd.Series(dtype=object)).fillna("")
    blocked = (
        df.get("tradability_blocked", pd.Series(dtype=object))
        .fillna(False)
        .astype(str)
        .str.lower()
        .isin({"1", "true", "yes"})
    )
    return SignalSummary(
        date=date_str,
        signal_path=str(Path(path).resolve()),
        rows=int(len(df)),
        selected_count=int(active.sum()),
        blocked_count=int(blocked.sum()),
        sector_threshold_rows=int(threshold_group.ne("production_default").sum()),
        default_threshold_rows=int(threshold_group.eq("production_default").sum()),
        penalty_overlay=penalty_overlay,
    )


def _write_report(
    *,
    report_dir: Path,
    signal_summaries: list[SignalSummary],
    portfolio_result: dict[str, object],
    promoted: bool,
    backup_path: str | None,
    exported_feeds: dict[str, str] | None,
) -> dict[str, str]:
    report_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "live_defaults": {
            "V2_MODEL_META": os.environ.get("V2_MODEL_META"),
            "TWO_STAGE_RANKER_ENABLED": os.environ.get("TWO_STAGE_RANKER_ENABLED"),
            "TWO_STAGE_RANKER_META": os.environ.get("TWO_STAGE_RANKER_META"),
            "SECTOR_OVERHEAT_THRESHOLDS_ENABLED": os.environ.get(
                "SECTOR_OVERHEAT_THRESHOLDS_ENABLED"
            ),
            "UNIFIED_EXIT_POLICY": os.environ.get("UNIFIED_EXIT_POLICY"),
        },
        "signal_summaries": [asdict(item) for item in signal_summaries],
        "portfolio_result": portfolio_result,
        "promoted": promoted,
        "backup_path": backup_path,
        "exported_feeds": exported_feeds or {},
    }
    json_path = report_dir / "new_champion_entry_filter_replay.json"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# New Champion Entry Filter Replay",
        "",
        f"- Generated at: {payload['generated_at']}",
        f"- Dates replayed: {signal_summaries[0].date} to {signal_summaries[-1].date}",
        f"- Signal files: {len(signal_summaries)}",
        f"- Replay DB: {portfolio_result.get('db_path')}",
        f"- Exit policy: {portfolio_result.get('exit_policy_name')}",
        f"- Promoted to live Champion DB: {'yes' if promoted else 'no'}",
    ]
    if backup_path:
        lines.append(f"- Previous Champion backup: {backup_path}")
    summary = portfolio_result.get("summary", {})
    if isinstance(summary, dict):
        lines.extend(
            [
                "",
                "## Portfolio Summary",
                "",
                f"- Total positions: {summary.get('total_positions')}",
                f"- Open positions: {summary.get('open_positions')}",
                f"- Pending positions: {summary.get('pending_positions')}",
                f"- Closed positions: {summary.get('closed_positions')}",
                f"- Stopped-out positions: {summary.get('stopped_out_positions')}",
                f"- Total marks: {summary.get('total_marks')}",
            ]
        )
    lines.extend(
        [
            "",
            "## Signal Replay",
            "",
            "| Date | Rows | Selected | Blocked | Sector thresholds | Default threshold |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    for item in signal_summaries:
        lines.append(
            "| "
            f"{item.date} | {item.rows} | {item.selected_count} | {item.blocked_count} | "
            f"{item.sector_threshold_rows} | {item.default_threshold_rows} |"
        )
    lines.extend(["", "## Penalty Overlay", ""])
    for item in signal_summaries:
        if item.penalty_overlay != "v1":
            lines.append(f"- {item.date}: {item.penalty_overlay}")
    if exported_feeds:
        lines.extend(["", "## Exported War Room Feeds", ""])
        for key, path in exported_feeds.items():
            lines.append(f"- {key}: {path}")

    md_path = report_dir / "new_champion_entry_filter_replay.md"
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"json": str(json_path), "md": str(md_path)}


def run(
    *,
    start_date: str,
    end_date: str | None,
    promote: bool,
    portfolio_start_date: str,
) -> dict[str, object]:
    os.environ.setdefault("V2_MODEL_META", str(TWO_STAGE_CHAMPION_STAGE1_META_PATH))
    os.environ.setdefault("TWO_STAGE_RANKER_ENABLED", "1")
    os.environ.setdefault("TWO_STAGE_RANKER_META", str(TWO_STAGE_CHAMPION_META_PATH))
    os.environ.setdefault("SECTOR_OVERHEAT_THRESHOLDS_ENABLED", "1")
    os.environ.setdefault("UNIFIED_EXIT_POLICY", EXIT_POLICY_ASYMMETRIC_V2)

    dates = _prediction_dates(start_date, end_date)
    signal_paths: list[str] = []
    signal_summaries: list[SignalSummary] = []
    for date_str in dates:
        penalty_overlay = "v1"
        try:
            result = build_unified_signals(
                pred_date=date_str,
                penalty_overlay=True,
                penalty_overlay_version="v1",
                disable_t1=True,
                verbose=False,
            )
        except ValueError as exc:
            if "Penalty overlay requires prediction artifact columns" not in str(exc):
                raise
            penalty_overlay = "disabled_missing_legacy_columns"
            result = build_unified_signals(
                pred_date=date_str,
                penalty_overlay=False,
                penalty_overlay_version="v1",
                disable_t1=True,
                verbose=False,
            )
        signal_paths.append(result.output_path)
        signal_summaries.append(_summarize_signal(result.output_path, date_str, penalty_overlay))

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    replay_db_path = BASE_DIR / f"paper_portfolio_v2_champion_new_entry_filter_replay_{stamp}.db"
    portfolio_result = sync_unified_portfolio(
        signal_files=signal_paths,
        db_path=str(replay_db_path),
        rule_version=f"{DEFAULT_RULE_VERSION}:new-entry-filter-20260509:paper:champion",
        exit_policy_name=EXIT_POLICY_ASYMMETRIC_V2,
        portfolio_start_date=portfolio_start_date,
    )

    backup_path: str | None = None
    exported_feeds: dict[str, str] | None = None
    if promote:
        champion_path = Path(CHAMPION_DB_PATH)
        if champion_path.exists():
            backup_path = str(champion_path.with_suffix(champion_path.suffix + f".bak_new_entry_filter_{stamp}"))
            shutil.copy2(champion_path, backup_path)
        shutil.copy2(replay_db_path, champion_path)
        exported_feeds = export_warroom_json_feeds()

    report_paths = _write_report(
        report_dir=ARCHIVE_DIR / stamp,
        signal_summaries=signal_summaries,
        portfolio_result=portfolio_result,
        promoted=promote,
        backup_path=backup_path,
        exported_feeds=exported_feeds,
    )
    return {
        "dates": dates,
        "signal_files": signal_paths,
        "replay_db_path": str(replay_db_path),
        "promoted": promote,
        "backup_path": backup_path,
        "report_paths": report_paths,
        "portfolio_result": portfolio_result,
    }


def main(argv: list[str] | None = None) -> dict[str, object]:
    parser = argparse.ArgumentParser(
        description="Replay April-to-now Champion records with the current entry filter."
    )
    parser.add_argument("--start-date", default="2026-04-01")
    parser.add_argument("--end-date", default=None)
    parser.add_argument(
        "--portfolio-start-date",
        default="2026-04-01",
        help="Earliest prediction date accepted by the replay ledger.",
    )
    parser.add_argument(
        "--promote",
        action="store_true",
        help="Back up and replace paper_portfolio_v2_champion.db with the replay DB.",
    )
    args = parser.parse_args(argv)
    result = run(
        start_date=args.start_date,
        end_date=args.end_date,
        promote=args.promote,
        portfolio_start_date=args.portfolio_start_date,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return result


if __name__ == "__main__":
    main()
