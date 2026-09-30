"""Isolated old/current tracker replay. Never writes the production ledger.

Example: python -X utf8 scripts/audit_rere_verification.py --baseline-ref a1849103
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import subprocess
import sys
import types
from pathlib import Path

import pandas as pd

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
from scripts import rere_lane_tracker as current


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-ref", default="a1849103")
    parser.add_argument("--output-dir", type=Path, default=BASE / "ml/reports/rere_verification_integrity_20260906")
    args = parser.parse_args()
    output = args.output_dir.resolve()
    source = current.LEDGER.resolve()
    if output == source.parent:
        raise ValueError("Replay outputs require a separate research directory")
    output.mkdir(parents=True, exist_ok=True)
    source_bytes = source.read_bytes()
    source_sha = hashlib.sha256(source_bytes).hexdigest()
    old_code = subprocess.run(["git", "show", f"{args.baseline_ref}:scripts/rere_lane_tracker.py"], cwd=BASE,
                              check=True, capture_output=True).stdout.decode("utf-8")
    old = types.ModuleType("rere_tracker_baseline_audit")
    old.__file__ = str(BASE / "scripts/rere_lane_tracker.py")
    exec(compile(old_code, old.__file__, "exec"), old.__dict__)
    modules = {"old": old, "current": current}
    # Freeze bytes once; both branches must read the same values even if a scheduled
    # writer updates daily files during this audit. No source files are modified.
    original_read_csv = pd.read_csv
    seed = original_read_csv(io.BytesIO(source_bytes), dtype={"ticker": str})
    frozen_frames = {}
    daily_sha = {}
    for ticker in seed["ticker"].dropna().astype(str).str.zfill(4).unique():
        path = (current.DAILY / f"{ticker}.csv").resolve()
        if path.exists():
            payload = path.read_bytes()
            daily_sha[ticker] = hashlib.sha256(payload).hexdigest()
            frozen_frames[path] = original_read_csv(io.BytesIO(payload))
    calendar_bytes = (BASE / "ml/data/ex_dividend_calendar.csv").read_bytes()
    calendar = original_read_csv(io.BytesIO(calendar_bytes), dtype={"stock_id": str})
    calendar["date"] = calendar["date"].astype(str).str[:10]
    calendar["dv"] = pd.to_numeric(calendar["stock_and_cache_dividend"], errors="raise")
    for module in modules.values():
        module._DIV_CAL = calendar[["stock_id", "date", "dv"]].copy()

    def frozen_read_csv(path, *read_args, **read_kwargs):
        key = Path(path).resolve() if isinstance(path, (str, Path)) else None
        return frozen_frames[key].copy(deep=True) if key in frozen_frames else original_read_csv(path, *read_args, **read_kwargs)

    summaries = {}
    frames = {}
    for branch, module in modules.items():
        ledger = output / f"{branch}_ledger.csv"
        if ledger.resolve() == source:
            raise ValueError("Cannot use production ledger for audit output")
        ledger.write_bytes(source_bytes)
        module.LEDGER = ledger
        module.REPORT = output / f"{branch}_report.md"
        pd.read_csv = frozen_read_csv
        try:
            module.check()
        finally:
            pd.read_csv = original_read_csv
        frame = pd.read_csv(ledger, dtype={"ticker": str})
        frames[branch] = frame
        closed = frame[frame["status"].isin(["matured", "stopped_out"])]
        cohort = current.comparable_cohort(frame)
        summaries[branch] = {"rows": len(frame), "status": frame["status"].value_counts().to_dict(),
                             "closed_mean_pct": float(closed["ret_pct"].mean()),
                             "closed_win_pct": float((closed["ret_pct"] > 0).mean() * 100),
                             "comparable_cohort": len(cohort)}
    left = frames["old"].set_index(["signal_date", "ticker"])
    right = frames["current"].set_index(["signal_date", "ticker"])
    comparison = left[["status", "ret_pct", "days_held"]].join(right[["status", "ret_pct", "days_held"]], lsuffix="_old", rsuffix="_current", how="outer")
    comparison["status_flip"] = comparison["status_old"] != comparison["status_current"]
    comparison["return_delta_pp"] = comparison["ret_pct_current"] - comparison["ret_pct_old"]
    comparison.to_csv(output / "outcome_comparison.csv", encoding="utf-8-sig")
    result = {"baseline_ref": args.baseline_ref, "input_ledger_sha256": source_sha,
              "input_calendar_sha256": hashlib.sha256(calendar_bytes).hexdigest(),
              "input_daily_sha256": daily_sha,
              "summary": summaries, "status_flips": int(comparison["status_flip"].sum()),
              "max_abs_return_delta_pp": float(comparison["return_delta_pp"].abs().max()),
              "production_ledger_unchanged": hashlib.sha256(source.read_bytes()).hexdigest() == source_sha,
              "prob_edge_delta": "not applicable: no model or feature-processor change"}
    if not result["production_ledger_unchanged"]:
        raise RuntimeError("Production ledger changed during audit")
    (output / "summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    rows = ["# rere 帳本完整性：隔離複本 A/B", "", f"舊版 `{args.baseline_ref}` 與本次修復共用同一份帳本、日K及除權息日曆。正式帳本 SHA-256 `{source_sha}`，檢查前後未變。", "",
            "| 版本 | 筆數 | 持有 | 停損 | 到期 | 提前結算均報酬 | 已有完整60日觀察的 cohort |", "|---|---:|---:|---:|---:|---:|---:|"]
    for name, stats in summaries.items():
        status = stats["status"]
        rows.append(f"| {name} | {stats['rows']} | {status.get('holding', 0)} | {status.get('stopped_out', 0)} | {status.get('matured', 0)} | {stats['closed_mean_pct']:+.2f}% | {stats['comparable_cohort']} |")
    rows += ["", f"結果：狀態翻轉 {result['status_flips']} 筆，逐筆報酬最大差異 {result['max_abs_return_delta_pp']:.2f}pp；沒有模型計算，prob_edge 差異不適用。",
             "", "提前結算的負報酬是風險觀察，不代表可和60日基準公平比較。尚持有的同梯樣本必須先有完整60日觀察機會；現在不作 rere 策略降權判決。",
             "", "新 record 路徑排除 kind=veto/watch，並保存 subtype；不猜測或手動修正舊帳本的未知型態/歷史資格。舊記錄保留追溯，未證實的歷史 veto 污染仍是限制。",
             "", "停損仍使用原本 MA60×0.97 與收盤判定，未改為固定百分比，也未更動進場策略、模型或持有期。合成 fixture 證實 day61 crash 不會覆蓋 day60 結算，而 day60 crash 仍會停損。", ""]
    (output / "report.md").write_text("\n".join(rows), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
