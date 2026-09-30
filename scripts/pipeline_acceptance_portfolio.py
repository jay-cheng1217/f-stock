"""Exercise canonical Champion sync against a NEW database in the guarded copy.

Run only through scripts/pipeline_acceptance.py. No prices or signal dates are
fabricated: a current-date order remains pending until an actual later bar exists.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import sys
import traceback

ROOT = Path(__file__).resolve().parents[1]
AS_OF = "2026-09-04"
REPORT_DIR = ROOT / "ml/reports/research/pipeline_acceptance_20260906"


def _assert_isolated():
    if Path(os.environ.get("STOCK_ACCEPTANCE_ROOT", "")).resolve() != ROOT:
        raise RuntimeError("Script must physically reside under STOCK_ACCEPTANCE_ROOT")
    if os.environ.get("STOCK_ACCEPTANCE_GUARD_ACTIVE") != str(ROOT):
        raise RuntimeError("Acceptance guard marker is absent or mismatched")
    if not Path.cwd().resolve().is_relative_to(ROOT):
        raise RuntimeError("Working directory must be inside the physical copy")
    sys.path.insert(0, str(ROOT))
    from scripts import pipeline_acceptance_guard
    guard = pipeline_acceptance_guard._INSTALLED
    if guard is None or guard.root != ROOT:
        raise RuntimeError("An installed audit guard is required")
    return guard


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _clean(value):
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _snapshot(path: Path) -> dict:
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert not conn.execute("PRAGMA foreign_key_check").fetchall()
        tables = {table: [dict(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY id")]
                  for table in ("unified_runs", "unified_positions", "unified_marks")}
    for table, columns in (("unified_runs", ("prediction_date",)),
                           ("unified_positions", ("ticker", "prediction_date")),
                           ("unified_marks", ("position_id", "mark_date"))):
        keys = [tuple(row[c] for c in columns) for row in tables[table]]
        assert len(keys) == len(set(keys)), f"Duplicate {table} keys"
    return tables


def _logical(tables: dict) -> dict:
    # The real pending refresh updates this audit timestamp on every invocation.
    # All other columns, including run creation times and snapshot data, must agree.
    return {table: [{k: v for k, v in row.items()
                     if not (table == "unified_positions" and k == "updated_at")}
                    for row in rows] for table, rows in tables.items()}


def run() -> dict:
    _assert_isolated()
    import pandas as pd
    from backend.services.warroom_service import CHAMPION_DB_PATH
    from scripts.exit_policies import EXIT_POLICY_ASYMMETRIC_V2, EXIT_POLICY_BASELINE_WITH_MA20
    from scripts.order_simulation import FILL_STATUS_PENDING
    from scripts.smart_update_auto import _resolve_unified_nightly_config
    from scripts.update_unified_portfolio import DEFAULT_RULE_VERSION, sync_unified_portfolio

    signal_path = ROOT / f"ml/models/unified_signals_{AS_OF}.csv"
    frame = pd.read_csv(signal_path, dtype={"ticker": str})
    assert len(frame) and set(frame["prediction_date"].astype(str)) == {AS_OF}
    signal_sha = _sha(signal_path)
    config = _resolve_unified_nightly_config(
        default_db_path=CHAMPION_DB_PATH,
        default_rule_version=DEFAULT_RULE_VERSION,
        default_exit_policy=EXIT_POLICY_ASYMMETRIC_V2,
        default_shadow_exit_policy=EXIT_POLICY_BASELINE_WITH_MA20,
    )
    assert Path(str(config["db_path"])).resolve().is_relative_to(ROOT)
    protected = [ROOT / "paper_portfolio_v2_champion.db", ROOT / "shadow_portfolio_v2_baseline.db"]
    protected_before = {str(p): _sha(p) for p in protected if p.exists()}
    attempt = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    database = ROOT / f"output/portfolio_fresh/champion_20260904_{attempt}.db"
    assert database.resolve().is_relative_to(ROOT)
    assert not database.exists(), "Refuse to reuse an existing acceptance database"
    assert database.resolve() not in [p.resolve() for p in protected]
    database.parent.mkdir(parents=True, exist_ok=True)

    daily = {}
    for ticker in sorted(set(frame["ticker"])):
        path = ROOT / "日K資料" / f"{ticker}.csv"
        prices = pd.read_csv(path, usecols=["Date"])
        dates = pd.to_datetime(prices["Date"], errors="raise")
        assert dates.notna().all() and not dates.duplicated().any()
        assert not (dates > pd.Timestamp(AS_OF)).any(), f"Future bar in {ticker}"
        daily[ticker] = {"sha256": _sha(path), "rows": len(prices),
                         "max_date": dates.max().date().isoformat(),
                         "bars_after_prediction_date": int((dates > pd.Timestamp(AS_OF)).sum())}

    kwargs = dict(signal_files=[str(signal_path)], db_path=str(database),
                  rule_version=str(config["rule_version"]),
                  exit_policy_name=str(config["exit_policy"]))
    first = sync_unified_portfolio(**kwargs)
    first_rows = _snapshot(database)
    second = sync_unified_portfolio(**kwargs)
    second_rows = _snapshot(database)
    assert first["new_runs"] == 1 and first["processed_dates"] == [AS_OF]
    assert second["new_runs"] == 0 and second["new_positions"] == 0
    assert second["upgraded_positions"] == 0 and second["skipped_runs"] == 1
    assert second["processed_dates"] == [] and second["cash_deployed"] == 0
    assert _logical(first_rows) == _logical(second_rows), "Repeat sync changed business ledger contents"
    assert _sha(signal_path) == signal_sha
    assert len(first_rows["unified_runs"]) == 1
    locked = first_rows["unified_runs"][0]
    assert locked["prediction_date"] == AS_OF and locked["signals_sha256"] == signal_sha
    assert locked["rule_version"] == config["rule_version"]
    assert locked["new_positions"] == len(first_rows["unified_positions"]) == first["new_positions"]
    for position in second_rows["unified_positions"]:
        assert position["status"] == "pending" and position["fill_status"] == FILL_STATUS_PENDING
        assert position["entry_date"] is None and position["entry_price"] is None
        assert position["last_mark_date"] is None
    assert not second_rows["unified_marks"]
    assert first["marks_upserted"] == second["marks_upserted"] == 0
    protected_after = {str(p): _sha(p) for p in protected if p.exists()}
    assert protected_before == protected_after, "Existing copied portfolio changed during acceptance"
    guard_log = ROOT / f"output/acceptance_guard_{os.getpid()}.jsonl"
    assert guard_log.exists() and not guard_log.read_text(encoding="utf-8").strip()

    gate_columns = [c for c in frame.columns if
                    c.startswith(("production_gate", "tradability_", "market_regime", "tquant_", "benchmark_"))
                    or c in {"ticker", "signal_type", "target_units", "target_weight_ratio", "close_ref",
                             "penalty_hard_block", "guardrail_blocked", "loose_mode", "macro_weight_multiplier"}]
    return _clean({
        "status": "PASS", "as_of": AS_OF, "generated_at": datetime.now().isoformat(),
        "scope": "Fresh canonical Champion database; same newly generated unified input and production policy",
        "workspace": str(ROOT), "database": str(database), "database_sha256": _sha(database),
        "guard_pid": os.getpid(), "guard_denials": 0,
        "canonical_config": config, "only_sync_config_override": "db_path (new acceptance SQLite)",
        "input": {"path": str(signal_path), "sha256": signal_sha, "rows": len(frame)},
        "input_gate_facts": frame[gate_columns].to_dict("records"), "daily_inputs": daily,
        "first_sync": first, "second_sync": second,
        "counts": {t: len(rows) for t, rows in second_rows.items()},
        "run_rows": second_rows["unified_runs"],
        "positions": [{k: row.get(k) for k in ("id", "ticker", "prediction_date", "status", "fill_status",
                       "target_units", "target_weight", "entry_date", "entry_price", "last_mark_date", "exit_policy")}
                      for row in second_rows["unified_positions"]],
        "assertions": {"actual_run_insert_and_lock": True, "actual_position_insert": first["new_positions"] > 0,
                       "pending_refresh": bool(second_rows["unified_positions"]),
                       "second_sync_unique_keys_stable": True, "second_sync_business_contents_stable": True,
                       "sqlite_integrity_and_foreign_keys": True, "existing_copied_books_unchanged": True,
                       "no_future_fill": True},
        "idempotence_timestamp_exception": "unified_positions.updated_at (canonical pending refresh audit time only)",
        "existing_copied_book_hashes": protected_after,
        "mark_write_exercised": False,
        "limitation": "Actual data ends 2026-09-04. Orders from that close cannot fill or write marks before an actual later session; 2026-09-07 remains unfilled. Pending target weight is reserved allocation, not a filled trade. No mark/exit execution claim is made.",
    })


def main() -> int:
    _assert_isolated()
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    try:
        result = run()
    except Exception:
        result = {"status": "FAIL", "traceback": traceback.format_exc()}
    (REPORT_DIR / "portfolio_acceptance.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    if result["status"] == "PASS":
        first, second = result["first_sync"], result["second_sync"]
        lines = ["# Champion 全新紙上帳本驗收 — 2026-09-06", "",
                 "PASS：使用實體副本、已安裝 guard、9/4 新產 unified CSV 與正式 nightly policy/config。只改 SQLite 目標為全新驗收檔。", "",
                 f"- 首次：新增 run {first['new_runs']}、新增 pending {first['new_positions']}、marks {first['marks_upserted']}；保留配置權重 {first['cash_deployed']:.6f}。",
                 f"- 第二次：新增 run {second['new_runs']}、新增 position {second['new_positions']}、略過已鎖定 run {second['skipped_runs']}、新增配置 {second['cash_deployed']}。",
                 "- run/position/mark 唯一鍵、完整業務欄位及 SQLite integrity/FK 檢查通過。僅排除 canonical pending refresh 的 updated_at 稽核時間。",
                 "- 既有副本 Champion/Shadow SQLite 雜湊完全相同；guard 無拒絕事件。",
                 f"- Policy：{first['exit_policy_name']}；rule：{first['rule_version']}。", "",
                 "行情截止 9/4，因此 9/7 尚未成交。這次實際驗證新增 pending、refresh 與 run lock；marks=0 是正確等待狀態，尚未驗到未來成交後的 mark/exit 寫入。配置權重不代表已成交。", "",
                 "輸入逐欄 gate、行情雜湊、兩次 canonical 回傳與資料庫路徑詳見 portfolio_acceptance.json。"]
    else:
        lines = ["# Champion 全新紙上帳本驗收", "", "FAIL；詳見 JSON traceback。"]
    (REPORT_DIR / "portfolio_acceptance.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "report": str(REPORT_DIR / "portfolio_acceptance.json"),
                      "counts": result.get("counts"), "traceback": result.get("traceback")}, ensure_ascii=False))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
