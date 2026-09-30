"""Champion production preflight audit.

This script is intentionally mechanical: deployment sign-off should depend on
the pass/fail rows here, not on a hand-written delivery report.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8")

from ml.config import DAILY_K_DIR, MODEL_DIR, REPORT_DIR
from ml.features.group import OTHER_GROUP_CODE, annotate_group_columns, is_capped_group
from ml.thresholds import GROUP_CAP_RATIO
try:
    from ml.thresholds import ENTRY_MA5_MIN_PCT
except ImportError:
    ENTRY_MA5_MIN_PCT = -0.03
from ml.universe import is_etf_ticker, normalize_ticker
from scripts.model_pin_registry import PinValidationError, champion_defaults, validate_registry


EXPECTED_STAGE1_META = Path(MODEL_DIR) / "lgbm_v2_20260508_200128_meta.json"
EXPECTED_STAGE2_META = Path(MODEL_DIR) / "lgbm_two_stage_ranker_20260509_011500_meta.json"
DEFAULT_DB_PATH = ROOT / "paper_portfolio_v2_champion.db"


@dataclass
class CheckResult:
    code: str
    name: str
    passed: bool
    detail: str
    data: dict[str, Any] = field(default_factory=dict)
    severity: str = "CHECK"


def _safe_float(value: Any) -> float | None:
    try:
        number = float(value)
    except Exception:
        return None
    if not np.isfinite(number):
        return None
    return number


def _pct(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value * 100:.2f}%"


def _latest_signal_file() -> Path:
    candidates: list[tuple[str, Path]] = []
    pattern = re.compile(r"^unified_signals_(\d{4}-\d{2}-\d{2}|\d{8})\.csv$")
    for path in Path(MODEL_DIR).glob("unified_signals_*.csv"):
        match = pattern.match(path.name)
        if not match:
            continue
        raw = match.group(1)
        normalized = raw.replace("-", "")
        candidates.append((normalized, path))
    if not candidates:
        raise FileNotFoundError(f"No unified_signals_*.csv found under {MODEL_DIR}")
    return sorted(candidates, key=lambda item: item[0])[-1][1]


def _read_signals(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(path)
    df = pd.read_csv(path, dtype={"ticker": str}, encoding="utf-8-sig")
    if "ticker" in df.columns:
        df["ticker"] = df["ticker"].map(normalize_ticker)
    return df


def _active_signals(df: pd.DataFrame) -> pd.DataFrame:
    if "signal_type" not in df.columns:
        return df
    signal_type = df["signal_type"].fillna("").astype(str).str.upper()
    return df.loc[signal_type.ne("NONE")].copy()


def _prediction_date(df: pd.DataFrame, signal_file: Path) -> str | None:
    if "prediction_date" in df.columns and not df["prediction_date"].dropna().empty:
        return str(df["prediction_date"].dropna().iloc[0])[:10]
    match = re.search(r"unified_signals_(\d{4}-\d{2}-\d{2}|\d{8})", signal_file.name)
    if not match:
        return None
    raw = match.group(1)
    if len(raw) == 8:
        return f"{raw[:4]}-{raw[4:6]}-{raw[6:]}"
    return raw


def _price_vs_ma5_from_daily(ticker: str, prediction_date: str) -> float | None:
    path = Path(DAILY_K_DIR) / f"{normalize_ticker(ticker)}.csv"
    if not path.exists():
        return None
    try:
        prices = pd.read_csv(path, encoding="utf-8-sig")
    except Exception:
        return None
    if not {"Date", "Close"} <= set(prices.columns):
        return None
    prices["Date"] = pd.to_datetime(prices["Date"], errors="coerce")
    prices = prices.dropna(subset=["Date"]).sort_values("Date").reset_index(drop=True)
    prices["Close"] = pd.to_numeric(prices["Close"], errors="coerce")
    if "MA_5" in prices.columns:
        prices["ma5_for_audit"] = pd.to_numeric(prices["MA_5"], errors="coerce")
        prices["ma5_for_audit"] = prices["ma5_for_audit"].fillna(prices["Close"].rolling(5).mean())
    else:
        prices["ma5_for_audit"] = prices["Close"].rolling(5).mean()
    cutoff = pd.to_datetime(prediction_date)
    history = prices[prices["Date"] <= cutoff]
    if history.empty:
        return None
    row = history.iloc[-1]
    close = _safe_float(row.get("Close"))
    ma5 = _safe_float(row.get("ma5_for_audit"))
    if close is None or ma5 is None or ma5 == 0:
        return None
    return close / ma5 - 1.0


def check_ma5_entry_quality(df: pd.DataFrame, signal_file: Path) -> CheckResult:
    df = _active_signals(df)
    prediction_date = _prediction_date(df, signal_file)
    values: list[float] = []
    missing: list[str] = []
    if "price_vs_ma5" in df.columns:
        series = pd.to_numeric(df["price_vs_ma5"], errors="coerce")
        values = [float(v) for v in series.dropna()]
        missing = df.loc[series.isna(), "ticker"].astype(str).tolist() if "ticker" in df.columns else []
    elif prediction_date and "ticker" in df.columns:
        for ticker in df["ticker"].astype(str):
            value = _price_vs_ma5_from_daily(ticker, prediction_date)
            if value is None:
                missing.append(ticker)
            else:
                values.append(value)
    else:
        return CheckResult("C1", "MA5 進場品質", False, "missing price_vs_ma5 and cannot infer prediction_date")

    total = len(values)
    if total == 0:
        return CheckResult("C1", "MA5 進場品質", False, "no usable price_vs_ma5 values")
    below_ma5_count = sum(value < 0 for value in values)
    below_guard_count = sum(value < ENTRY_MA5_MIN_PCT for value in values)
    deep_below_count = sum(value <= -0.05 for value in values)
    below_ma5_ratio = below_ma5_count / total
    below_guard_ratio = below_guard_count / total
    deep_below_ratio = deep_below_count / total
    passed = not missing
    detail = (
        f"INFO only: below-MA5 {below_ma5_count}/{total} = {_pct(below_ma5_ratio)}; "
        f"below {ENTRY_MA5_MIN_PCT:.2%} {below_guard_count}/{total} = {_pct(below_guard_ratio)}; "
        f"deep <= -5.00% {deep_below_count}/{total} = {_pct(deep_below_ratio)}; "
        "asymmetric_v2 handles below-MA5 entries with MA5_BREAK"
    )
    if missing:
        detail += f"; missing MA5 for {len(missing)} tickers"
    return CheckResult(
        "C1",
        "MA5 進場品質觀測",
        passed,
        detail,
        {
            "below_ma5_count": below_ma5_count,
            "below_guard_count": below_guard_count,
            "deep_below_count": deep_below_count,
            "total": total,
            "below_ma5_ratio": below_ma5_ratio,
            "below_guard_ratio": below_guard_ratio,
            "deep_below_ratio": deep_below_ratio,
            "blocking": False,
            "missing_tickers": missing[:20],
        },
        "INFO" if passed else "CHECK",
    )


def check_two_stage_rank(df: pd.DataFrame) -> CheckResult:
    df = _active_signals(df)
    rank_col = "two_stage_rank" if "two_stage_rank" in df.columns else "two_stage_rank_20d"
    if rank_col not in df.columns:
        return CheckResult("C2", "Two-Stage rank 完整性", False, "two_stage_rank column missing")
    ranks = pd.to_numeric(df[rank_col], errors="coerce")
    total = len(ranks)
    missing = int(ranks.isna().sum())
    ratio = missing / total if total else 1.0
    return CheckResult(
        "C2",
        "Two-Stage rank 完整性",
        ratio <= 0.05,
        f"NaN {missing}/{total} = {_pct(ratio)} <= 5%",
        {"rank_col": rank_col, "missing": missing, "total": total, "ratio": ratio},
    )


def check_overheat_gate(df: pd.DataFrame) -> CheckResult:
    df = _active_signals(df)
    price_col = "price_vs_ma60" if "price_vs_ma60" in df.columns else "price_vs_ma60_20d"
    if price_col not in df.columns:
        return CheckResult("C3", "OVERHEAT gate 正常", False, "price_vs_ma60 column missing")
    if "overheat_threshold" not in df.columns:
        return CheckResult("C3", "OVERHEAT gate 正常", False, "overheat_threshold column missing")
    prices = pd.to_numeric(df[price_col], errors="coerce")
    thresholds = pd.to_numeric(df["overheat_threshold"], errors="coerce")
    bad = df.loc[prices > thresholds + 1e-12, ["ticker", price_col, "overheat_threshold", "sector"]].copy()
    passed = bad.empty
    examples = bad.head(20).to_dict("records")
    return CheckResult(
        "C3",
        "OVERHEAT gate 正常",
        passed,
        "0 active signals exceed sector threshold" if passed else f"{len(bad)} active signals exceed sector threshold",
        {"violations": examples, "violation_count": int(len(bad))},
    )


def check_guardrail_columns(df: pd.DataFrame) -> CheckResult:
    required = ["guardrail_blocked", "guardrail_trigger_reason"]
    missing = [col for col in required if col not in df.columns]
    return CheckResult(
        "C4",
        "Guardrail 欄位存在",
        not missing,
        "required columns present" if not missing else f"missing columns: {', '.join(missing)}",
        {"missing": missing},
    )


def check_etf_leakage(df: pd.DataFrame) -> CheckResult:
    if "ticker" not in df.columns:
        return CheckResult("C5", "ETF 洩漏", False, "ticker column missing")
    hits = [ticker for ticker in df["ticker"].astype(str).map(normalize_ticker) if is_etf_ticker(ticker)]
    return CheckResult(
        "C5",
        "ETF 洩漏",
        len(hits) == 0,
        "ETF hits = 0" if not hits else f"ETF hits = {len(hits)}: {', '.join(hits[:20])}",
        {"etf_hits": hits},
    )


def _resolve(path: str | Path) -> Path:
    path = Path(path)
    if not path.is_absolute():
        path = ROOT / path
    return path.resolve()


def check_production_flags() -> CheckResult:
    from scripts import smart_update_auto

    smart_update_auto._enable_two_stage_champion_defaults()
    expected = {
        "TWO_STAGE_RANKER_ENABLED": "1",
        "SECTOR_OVERHEAT_THRESHOLDS_ENABLED": "1",
    }
    observed = {key: os.environ.get(key) for key in expected}
    failures = [f"{key}={observed[key]!r}" for key, want in expected.items() if observed[key] != want]
    return CheckResult(
        "C6",
        "Production flags",
        not failures,
        "effective flags are enabled" if not failures else "bad flags: " + ", ".join(failures),
        {"observed": observed},
    )


def check_model_meta_lock() -> CheckResult:
    failures: list[str] = []
    try:
        defaults = champion_defaults()
        registry = validate_registry()
    except PinValidationError as exc:
        failures.append(str(exc))
        defaults = {}
        registry = {"passed": False, "failures": failures, "details": []}
    observed_stage1 = _resolve(os.environ.get("V2_MODEL_META", ""))
    observed_stage2 = _resolve(os.environ.get("TWO_STAGE_RANKER_META", ""))
    expected_stage1 = _resolve(defaults.get("V2_MODEL_META", EXPECTED_STAGE1_META))
    expected_stage2 = _resolve(defaults.get("TWO_STAGE_RANKER_META", EXPECTED_STAGE2_META))
    if observed_stage1 != expected_stage1:
        failures.append(f"stage1={observed_stage1} expected={expected_stage1}")
    if observed_stage2 != expected_stage2:
        failures.append(f"stage2={observed_stage2} expected={expected_stage2}")
    failures.extend(registry.get("failures", []))
    return CheckResult(
        "C7",
        "Stage1/Stage2 model meta 鎖定",
        not failures,
        (
            "stage1/stage2 meta, model binaries, and SHA256 registry match pinned manifest"
            if not failures
            else "; ".join(failures)
        ),
        {
            "observed_stage1": str(observed_stage1),
            "observed_stage2": str(observed_stage2),
            "expected_stage1": str(expected_stage1),
            "expected_stage2": str(expected_stage2),
            "registry_details": registry.get("details", []),
        },
    )


def check_tests(timeout_sec: int, skip_tests: bool) -> CheckResult:
    if skip_tests:
        return CheckResult("C8", "Tests", False, "skipped by --skip-tests", {"skipped": True})
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=timeout_sec,
    )
    output = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
    summary = "pytest failed"
    for line in reversed(output.splitlines()):
        if "passed" in line or "failed" in line or "error" in line:
            summary = line.strip()
            break
    return CheckResult(
        "C8",
        "Tests",
        proc.returncode == 0,
        summary,
        {"returncode": proc.returncode, "tail": "\n".join(output.splitlines()[-20:])},
    )


def check_db_schema(db_path: Path) -> CheckResult:
    if not db_path.exists():
        return CheckResult("C9", "DB schema 完整性", False, f"DB not found: {db_path}")
    required = {"two_stage_rank_at_entry", "price_vs_ma20_at_entry"}
    with sqlite3.connect(str(db_path)) as conn:
        rows = conn.execute("PRAGMA table_info(unified_positions)").fetchall()
    cols = {str(row[1]) for row in rows}
    missing = sorted(required - cols)
    return CheckResult(
        "C9",
        "DB schema 完整性",
        not missing,
        "required unified_positions columns present" if not missing else f"missing columns: {', '.join(missing)}",
        {"missing": missing, "db_path": str(db_path)},
    )


def check_group_cap(df: pd.DataFrame) -> CheckResult:
    active = annotate_group_columns(_active_signals(df))
    if active.empty:
        return CheckResult("C10", "Group concentration cap", True, "no active signals")
    max_per_group = max(1, int(len(active) * GROUP_CAP_RATIO))
    capped = active.loc[active["group_code"].map(is_capped_group)].copy()
    if capped.empty:
        return CheckResult(
            "C10",
            "Group concentration cap",
            True,
            "no capped groups in active signals",
            {"max_per_group": max_per_group, "group_counts": {}},
        )
    counts = capped["group_code"].astype(str).value_counts()
    violations = counts[counts > max_per_group]
    detail = (
        f"all capped groups <= {max_per_group}"
        if violations.empty
        else f"{len(violations)} groups exceed cap {max_per_group}: "
        + ", ".join(f"{code}={count}" for code, count in violations.items())
    )
    return CheckResult(
        "C10",
        "Group concentration cap",
        violations.empty,
        detail,
        {
            "cap_ratio": GROUP_CAP_RATIO,
            "max_per_group": max_per_group,
            "other_group": OTHER_GROUP_CODE,
            "group_counts": counts.to_dict(),
            "violations": violations.to_dict(),
        },
    )


def _status_icon(passed: bool) -> str:
    return "✅" if passed else "❌"


def _result_icon(result: CheckResult) -> str:
    if result.severity.upper() == "INFO" and result.passed:
        return "ℹ️"
    return _status_icon(result.passed)


def write_reports(results: list[CheckResult], signal_file: Path, output_date: str) -> tuple[Path, Path]:
    report_dir = Path(REPORT_DIR)
    report_dir.mkdir(parents=True, exist_ok=True)
    md_path = report_dir / f"champion_audit_{output_date}.md"
    json_path = report_dir / f"champion_audit_{output_date}.json"
    overall = all(item.passed for item in results)
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "signal_file": str(signal_file),
        "overall": "PASS" if overall else "FAIL",
        "checks": [asdict(item) for item in results],
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        f"# Champion Audit — {output_date[:4]}-{output_date[4:6]}-{output_date[6:]}",
        "",
        f"- Signal file: `{signal_file}`",
        f"- Overall: **{'PASS' if overall else 'FAIL'}**",
        "",
        "| Check | Result | Detail |",
        "|---|---|---|",
    ]
    for result in results:
        lines.append(f"| {result.code} {result.name} | {_result_icon(result)} | {result.detail} |")
    failures = [result for result in results if not result.passed]
    if failures:
        lines.extend(["", "## Failed Checks", ""])
        for result in failures:
            lines.append(f"- `{result.code}` {result.name}: {result.detail}")
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return md_path, json_path


def run(args: argparse.Namespace) -> tuple[list[CheckResult], Path, Path, Path]:
    signal_file = Path(args.signal_file) if args.signal_file else _latest_signal_file()
    df = _read_signals(signal_file)
    results = [
        check_ma5_entry_quality(df, signal_file),
        check_two_stage_rank(df),
        check_overheat_gate(df),
        check_guardrail_columns(df),
        check_etf_leakage(df),
        check_production_flags(),
        check_model_meta_lock(),
        check_tests(args.test_timeout_sec, args.skip_tests),
        check_db_schema(Path(args.db_path)),
        check_group_cap(df),
    ]
    output_date = args.output_date or datetime.now().strftime("%Y%m%d")
    md_path, json_path = write_reports(results, signal_file, output_date)
    return results, signal_file, md_path, json_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Champion production deployment audit.")
    parser.add_argument("--signal-file", default="", help="unified_signals CSV to audit; default latest under ml/models")
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH), help="Champion paper DB path")
    parser.add_argument("--output-date", default="", help="report date stamp, default today YYYYMMDD")
    parser.add_argument("--skip-tests", action="store_true", help="skip C8 pytest check; marks C8 failed")
    parser.add_argument("--test-timeout-sec", type=int, default=300)
    parser.add_argument("--no-fail-exit", action="store_true", help="always exit 0 after writing reports")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    results, signal_file, md_path, json_path = run(args)
    overall = all(item.passed for item in results)
    for result in results:
        print(f"{_result_icon(result)} {result.code} {result.name}: {result.detail}")
    print(f"Report: {md_path}")
    print(f"JSON: {json_path}")
    print(f"Overall: {'PASS' if overall else 'FAIL'}")
    if overall or args.no_fail_exit:
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
