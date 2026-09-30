"""Run real rere/shadow ledger leaves only inside a guarded acceptance copy.

The input snapshot, canonical plan and prediction files must already exist.
This script neither creates synthetic data nor changes any trading method.
Example (from the physical copy):
  python -B -X utf8 scripts/pipeline_acceptance_ledger.py --as-of 2026-09-04 --trade-date 2026-09-07
"""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import sys
import traceback

ROOT = Path(__file__).resolve().parents[1]


def _assert_isolated():
    raw = os.environ.get("STOCK_ACCEPTANCE_ROOT")
    if not raw or Path(raw).resolve() != ROOT:
        raise RuntimeError("This leaf must physically reside under STOCK_ACCEPTANCE_ROOT")
    if os.environ.get("STOCK_ACCEPTANCE_GUARD_ACTIVE") != str(ROOT):
        raise RuntimeError("Acceptance guard active marker is absent or mismatched")
    if not Path.cwd().resolve().is_relative_to(ROOT):
        raise RuntimeError("Acceptance leaf working directory must be inside the copy")
    sys.path.insert(0, str(ROOT))
    from scripts import pipeline_acceptance_guard
    guard = pipeline_acceptance_guard._INSTALLED
    if guard is None or guard.root != ROOT:
        raise RuntimeError("A real installed audit guard is required; an environment marker is insufficient")
    return guard


def _inside(path: Path) -> Path:
    resolved = path.resolve()
    if not resolved.is_relative_to(ROOT):
        raise RuntimeError(f"Acceptance path escapes copy: {resolved}")
    return resolved


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read(path: Path):
    import pandas as pd
    return pd.read_csv(path, dtype={"ticker": str})


def _keys(frame, columns: list[str]) -> set[tuple]:
    missing = set(columns) - set(frame.columns)
    if missing:
        raise AssertionError(f"Ledger key columns missing: {sorted(missing)}")
    selected = frame[columns]
    if selected.isna().any().any() or selected.astype(str).eq("").any().any():
        raise AssertionError("Ledger key contains missing values")
    if selected.duplicated().any():
        raise AssertionError(f"Duplicate ledger keys: {selected.loc[selected.duplicated()].to_dict('records')[:8]}")
    return set(selected.astype(str).itertuples(index=False, name=None))


def _assert_dates(frame, column: str, as_of: str) -> None:
    import pandas as pd
    values = frame[column].fillna("").astype(str)
    dates = pd.to_datetime(values.where(values.ne("")), errors="coerce")
    if (values.ne("") & dates.isna()).any():
        raise AssertionError(f"Unparseable {column} in ledger")
    if (dates > pd.Timestamp(as_of)).any():
        bad = frame.loc[dates > pd.Timestamp(as_of), ["ticker", column]].to_dict("records")
        raise AssertionError(f"Future {column} beyond {as_of}: {bad[:8]}")


def _assert_unfilled_future_signals(frame, as_of: str) -> None:
    import pandas as pd
    _assert_dates(frame, "entry_date", as_of)
    future = pd.to_datetime(frame["signal_date"]) > pd.Timestamp(as_of)
    if frame.loc[future, "entry_date"].fillna("").astype(str).ne("").any():
        raise AssertionError("Future trade-day candidate was marked as entered")
    if not frame.loc[future, "status"].isin(["pending_entry", "duplicate_signal"]).all():
        raise AssertionError("Future trade-day candidate has an executed position status")


def _verify_daily_cutoff(tickers, as_of: str) -> dict:
    import pandas as pd
    manifest = {}
    for ticker in sorted(set(str(t).zfill(4) for t in tickers)):
        path = ROOT / "日K資料" / f"{ticker}.csv"
        if not path.exists():
            manifest[ticker] = {"missing": True}
            continue
        frame = pd.read_csv(path, usecols=["Date"])
        dates = pd.to_datetime(frame["Date"], errors="raise")
        if dates.isna().any() or (dates > pd.Timestamp(as_of)).any():
            raise AssertionError(f"Daily input violates acceptance as-of {as_of}: {ticker}")
        manifest[ticker] = {"max_date": dates.max().date().isoformat() if len(dates) else None,
                            "sha256": _sha(path)}
    return manifest


def _rere_summary(frame, tracker, as_of: str) -> dict:
    import pandas as pd
    cohort = tracker.comparable_cohort(frame)
    closed = frame[frame["status"].isin(["stopped_out", "matured"])]
    future = pd.to_datetime(frame["signal_date"]) > pd.Timestamp(as_of)
    return {"rows": len(frame), "status_counts": frame["status"].value_counts().to_dict(),
            "holding": int(frame["status"].eq("holding").sum()), "closed": len(closed),
            "complete_60_session_cohort": len(cohort), "future_signal_rows": int(future.sum()),
            "future_signal_pending": int((future & frame["status"].eq("pending_entry")).sum()),
            "closed_mean_return_pct": float(closed["ret_pct"].mean()) if len(closed) else None,
            "cohort_mean_return_pct": float(cohort["ret_pct"].mean()) if len(cohort) else None,
            "note": "Candidate observation ledger; early closures are not a complete-cohort verdict."}


def main() -> int:
    guard = _assert_isolated()  # Before loading any tracker or invoking a write leaf.
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--as-of", default="2026-09-04")
    parser.add_argument("--trade-date", default="2026-09-07")
    parser.add_argument("--plan", type=Path)
    args = parser.parse_args()
    as_of = datetime.strptime(args.as_of, "%Y-%m-%d").date().isoformat()
    trade_day = datetime.strptime(args.trade_date, "%Y-%m-%d").date()
    trade_date = trade_day.isoformat()
    plan_path = _inside(args.plan or ROOT / "logs" / f"entry_list_{trade_date.replace('-', '')}.json")
    output = ROOT / "logs" / "acceptance_ledger.json"
    receipt = {"status": "RUNNING", "started_at": datetime.now().isoformat(), "workspace": str(ROOT),
               "as_of": as_of, "trade_date": trade_date, "plan": str(plan_path), "stages": [],
               "scope": "Real tracker leaves in a physical copy; no fabricated inputs, promotion or training."}
    violation_start = len(guard.violations)
    try:
        from scripts import rere_lane_tracker as rere
        from scripts import shadow_dataA_tracker as shadow
        from scripts.taiwan_trading_calendar import previous_taiwan_trading_day, is_taiwan_trading_day
        for module in (rere, shadow):
            if Path(module.__file__).resolve().parents[1] != ROOT:
                raise AssertionError(f"Imported tracker does not belong to copy: {module.__file__}")
            for name in ("LEDGER", "REPORT", "DAILY"):
                _inside(Path(getattr(module, name)))
        for path in (plan_path, rere.LEDGER, shadow.LEDGER, shadow.SNAPSHOT_PKL):
            if not _inside(path).is_file():
                raise AssertionError(f"Required real input absent: {path}")
        if not is_taiwan_trading_day(trade_day) or previous_taiwan_trading_day(trade_day).isoformat() != as_of:
            raise AssertionError("Plan trade date must follow the accepted source trading day")
        plan = json.loads(plan_path.read_text(encoding="utf-8-sig"))
        if str(plan.get("trade_date", "")).replace("/", "-") != trade_date or plan.get("as_of_date") != as_of:
            raise AssertionError("Canonical plan metadata does not match the acceptance dates")
        receipt["plan_sha256"] = _sha(plan_path)
        before = _read(rere.LEDGER)
        before_keys = _keys(before, ["ticker", "signal_date"])
        _assert_unfilled_future_signals(before, as_of)
        plan_tickers = [str(row.get("stock", "")).split(" ", 1)[0] for row in plan.get("rows", [])]
        receipt["daily_inputs"] = _verify_daily_cutoff([*before["ticker"], *plan_tickers], as_of)
        receipt["rere_input_sha256"] = _sha(rere.LEDGER)

        rere.record(str(plan_path))
        rere.check()
        first = _read(rere.LEDGER)
        first_keys = _keys(first, ["ticker", "signal_date"])
        _assert_unfilled_future_signals(first, as_of)
        if not before_keys.issubset(first_keys):
            raise AssertionError("rere first run removed historical records")
        first_sha = _sha(rere.LEDGER)
        receipt["stages"].append({"name": "rere record/check first", "rows": len(first), "added": len(first) - len(before), "sha256": first_sha})

        rere.record(str(plan_path))
        rere.check()
        second = _read(rere.LEDGER)
        _assert_unfilled_future_signals(second, as_of)
        if _keys(second, ["ticker", "signal_date"]) != first_keys or len(second) != len(first):
            raise AssertionError("rere second record/check changed key set or row count")
        if _sha(rere.LEDGER) != first_sha:
            raise AssertionError("rere second record/check was not byte-for-byte stable")
        receipt["stages"].append({"name": "rere record/check second", "rows": len(second), "keys_stable": True, "content_stable": True})
        receipt["rere"] = _rere_summary(second, rere, as_of)

        shadow_before = _read(shadow.LEDGER)
        shadow_before_keys = _keys(shadow_before, ["date", "side", "ticker"])
        _assert_dates(shadow_before, "date", as_of)
        receipt["shadow_input_sha256"] = _sha(shadow.LEDGER)
        shadow.record(expected_asof=as_of)
        shadow_first = _read(shadow.LEDGER)
        shadow_first_keys = _keys(shadow_first, ["date", "side", "ticker"])
        _assert_dates(shadow_first, "date", as_of)
        if not shadow_before_keys.issubset(shadow_first_keys):
            raise AssertionError("shadow record removed historical records")
        receipt["stages"].append({"name": "shadow record first", "rows": len(shadow_first), "added": len(shadow_first) - len(shadow_before)})
        shadow.check()  # Real paired evaluator; run exactly once.
        paired_report = shadow.REPORT.parent / "shadow_dataA_evaluation_latest" / "shadow_target_replay.json"
        paired = json.loads(_inside(paired_report).read_text(encoding="utf-8"))
        if paired.get("as_of") != as_of:
            raise AssertionError("Shadow check used a different live as-of; receipt is not comparable")
        after_check_sha = _sha(shadow.LEDGER)
        receipt["stages"].append({"name": "shadow check", "paired_complete_dates": paired.get("paired_dates"), "as_of": paired.get("as_of")})
        shadow.record(expected_asof=as_of)
        shadow_second = _read(shadow.LEDGER)
        if _keys(shadow_second, ["date", "side", "ticker"]) != shadow_first_keys or len(shadow_second) != len(shadow_first):
            raise AssertionError("shadow second record grew duplicate rows or changed keys")
        if _sha(shadow.LEDGER) != after_check_sha:
            raise AssertionError("shadow second record changed existing ledger content")
        receipt["stages"].append({"name": "shadow record second", "rows": len(shadow_second), "keys_stable": True, "content_stable": True})
        receipt["shadow"] = {"rows": len(shadow_second), "by_side": shadow_second["side"].value_counts().to_dict(),
                             "as_of_baskets": shadow_second.loc[shadow_second["date"].eq(as_of), "side"].value_counts().to_dict(),
                             "complete_paired_cohorts": paired.get("paired_dates"),
                             "legacy_return_values": "preserved; the paired evaluator report supplies comparable outcomes"}
        if len(guard.violations) != violation_start:
            raise AssertionError("Leaf swallowed an audit-guard violation; acceptance must fail")
        receipt["status"] = "PASS"
    except Exception as exc:
        receipt["status"] = "FAIL"
        receipt["error"] = f"{type(exc).__name__}: {exc}"
        receipt["traceback"] = traceback.format_exc()
    finally:
        receipt["completed_at"] = datetime.now().isoformat()
        receipt["guard_violations"] = guard.violations[violation_start:]
        output.parent.mkdir(exist_ok=True)
        temporary = output.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(receipt, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
        os.replace(temporary, output)
    print(json.dumps({"status": receipt["status"], "receipt": str(output), "error": receipt.get("error")}, ensure_ascii=False))
    return 0 if receipt["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
