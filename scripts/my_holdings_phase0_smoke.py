"""Phase 0 smoke checks for the personal holdings MVP.

This script is intentionally read-only. It checks the data and service
preconditions that must hold before building the "my holdings" CRUD/review UI:

- personal ledger DB is ignored by git
- stock daily K data is available
- ETF daily K backfill gaps are explicit
- market regime report can be read
- prediction_explain can be called for arbitrary stock tickers without crashing
- ETF tickers do not receive V2 alpha-model signals in MVP
"""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import textwrap
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))

from ml.config import DAILY_K_DIR, MODEL_DIR, REPORT_DIR  # noqa: E402
from ml.universe import ETF_TICKERS, classify_ticker, get_etf_metadata  # noqa: E402

DEFAULT_STOCK_TICKERS = ["2330", "2317", "1305", "6588", "6510"]
DEFAULT_ETF_TICKERS = [
    ticker
    for ticker in ["0050", "0056", "006208", "00631L", "00632R", "00878", "00940"]
    if ticker in ETF_TICKERS
]
DEFAULT_CLASSIFICATION_TICKERS = [
    ticker for ticker in ["00981A", "00679B", "00635U", "00669R"] if ticker in ETF_TICKERS
]
LATEST_JSON = "my_holdings_phase0_smoke_latest.json"
LATEST_MD = "my_holdings_phase0_smoke_latest.md"


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: str
    detail: str
    data: dict[str, Any]


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run my-holdings MVP phase0 smoke checks.")
    parser.add_argument("--stock-tickers", nargs="*", default=DEFAULT_STOCK_TICKERS)
    parser.add_argument("--etf-tickers", nargs="*", default=DEFAULT_ETF_TICKERS)
    parser.add_argument("--classification-tickers", nargs="*", default=DEFAULT_CLASSIFICATION_TICKERS)
    parser.add_argument("--report-dir", default=REPORT_DIR)
    parser.add_argument("--explain-timeout-sec", type=int, default=180)
    parser.add_argument(
        "--skip-explain",
        action="store_true",
        help="Skip importing app.py and calling prediction_explain.",
    )
    return parser.parse_args(argv)


def _safe_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _safe_json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_safe_json(item) for item in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        number = float(value)
        return number if math.isfinite(number) else None
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    return value


def _latest_prediction_path() -> Path | None:
    files = sorted(Path(MODEL_DIR).glob("predictions_2026-*.csv"))
    return files[-1] if files else None


def _read_latest_prediction_tickers() -> tuple[Path | None, set[str]]:
    path = _latest_prediction_path()
    if path is None:
        return None, set()
    try:
        df = pd.read_csv(path, encoding="utf-8-sig", dtype={"ticker": str}, usecols=["ticker"])
    except Exception:
        return path, set()
    return path, set(df["ticker"].dropna().astype(str).str.zfill(4))


def _daily_k_status(ticker: str) -> dict[str, Any]:
    path = Path(DAILY_K_DIR) / f"{ticker}.csv"
    if not path.exists():
        return {
            "ticker": ticker,
            "path": str(path),
            "exists": False,
            "rows": 0,
            "latest_date": None,
            "latest_close": None,
        }
    try:
        df = pd.read_csv(path, dtype={"Date": str}, usecols=["Date", "Close", "Volume"])
    except Exception as exc:
        return {
            "ticker": ticker,
            "path": str(path),
            "exists": True,
            "rows": None,
            "latest_date": None,
            "latest_close": None,
            "error": str(exc),
        }
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    df["Close"] = pd.to_numeric(df["Close"], errors="coerce")
    df["Volume"] = pd.to_numeric(df["Volume"], errors="coerce")
    df = df.dropna(subset=["Date", "Close"]).sort_values("Date")
    if df.empty:
        return {
            "ticker": ticker,
            "path": str(path),
            "exists": True,
            "rows": 0,
            "latest_date": None,
            "latest_close": None,
        }
    latest = df.iloc[-1]
    return {
        "ticker": ticker,
        "path": str(path),
        "exists": True,
        "rows": int(len(df)),
        "latest_date": latest["Date"].date().isoformat(),
        "latest_close": float(latest["Close"]),
        "latest_volume": None if pd.isna(latest["Volume"]) else float(latest["Volume"]),
    }


def check_gitignore() -> CheckResult:
    path = BASE_DIR / ".gitignore"
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    required = ["my_holdings.db", "my_holdings.db-journal", "my_holdings.db-shm", "my_holdings.db-wal"]
    missing = [item for item in required if item not in text]
    status = "PASS" if not missing else "FAIL"
    return CheckResult(
        name="gitignore_privacy",
        status=status,
        detail="my_holdings.db runtime files are ignored" if not missing else "missing ignore entries",
        data={"required": required, "missing": missing},
    )


def check_daily_k(stock_tickers: list[str], etf_tickers: list[str]) -> CheckResult:
    stock_rows = [_daily_k_status(str(ticker).zfill(4)) for ticker in stock_tickers]
    etf_rows = [_daily_k_status(str(ticker).upper()) for ticker in etf_tickers]
    missing_stocks = [row["ticker"] for row in stock_rows if not row["exists"]]
    missing_etfs = [row["ticker"] for row in etf_rows if not row["exists"]]
    if missing_stocks:
        status = "FAIL"
        detail = f"stock daily K missing: {', '.join(missing_stocks)}"
    elif missing_etfs:
        status = "WARN"
        detail = f"ETF daily K backfill required: {', '.join(missing_etfs)}"
    else:
        status = "PASS"
        detail = "stock and ETF daily K data available"
    return CheckResult(
        name="daily_k_availability",
        status=status,
        detail=detail,
        data={
            "stocks": stock_rows,
            "etfs": etf_rows,
            "missing_stocks": missing_stocks,
            "missing_etfs": missing_etfs,
        },
    )


def check_prediction_csv(stock_tickers: list[str], etf_tickers: list[str]) -> CheckResult:
    path, tickers = _read_latest_prediction_tickers()
    stock_missing = [ticker for ticker in stock_tickers if str(ticker).zfill(4) not in tickers]
    etf_present = [ticker for ticker in etf_tickers if str(ticker).upper() in tickers]
    status = "PASS" if path is not None and not etf_present else "WARN"
    detail = "latest stock prediction CSV found; ETF alpha-model coverage correctly absent"
    if path is None:
        status = "FAIL"
        detail = "no latest predictions_2026-*.csv found"
    elif etf_present:
        detail = "ETF tickers appear in stock alpha prediction CSV; MVP must suppress V2 ETF model signals"
    return CheckResult(
        name="prediction_universe_policy",
        status=status,
        detail=detail,
        data={
            "prediction_path": str(path) if path else None,
            "stock_missing_from_prediction_csv": stock_missing,
            "etf_present_in_prediction_csv": etf_present,
            "policy": "ETF_V2_ALPHA_MODEL_UNSUPPORTED",
        },
    )


def check_ticker_classification(tickers: list[str]) -> CheckResult:
    rows = []
    for ticker in tickers:
        ticker = str(ticker).upper()
        metadata = get_etf_metadata(ticker) or {}
        classification = classify_ticker(ticker)
        rows.append(
            {
                "ticker": ticker,
                "classification": classification,
                "name": metadata.get("name"),
                "market": metadata.get("market"),
                "listing_date": metadata.get("listing_date"),
            }
        )

    not_etf = [row["ticker"] for row in rows if not str(row["classification"]).startswith("etf_")]
    return CheckResult(
        name="ticker_classification",
        status="PASS" if not not_etf else "WARN",
        detail="ETF suffix classifications are explicit" if not not_etf else "some tickers are not classified as ETF",
        data={"rows": rows, "not_etf": not_etf},
    )


def check_market_regime() -> CheckResult:
    path = Path(REPORT_DIR) / "market_regime_latest.json"
    if not path.exists():
        return CheckResult(
            name="market_regime_latest",
            status="FAIL",
            detail="market_regime_latest.json not found",
            data={"path": str(path)},
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return CheckResult(
            name="market_regime_latest",
            status="FAIL",
            detail=f"cannot parse market regime JSON: {exc}",
            data={"path": str(path)},
        )
    state = payload.get("state")
    action = payload.get("action")
    as_of = payload.get("as_of_date")
    status = "PASS" if state and action else "WARN"
    return CheckResult(
        name="market_regime_latest",
        status=status,
        detail=f"market regime {state}/{action} as of {as_of}",
        data={"path": str(path), "state": state, "action": action, "as_of_date": as_of},
    )


def run_prediction_explain_smoke(tickers: list[str], timeout_sec: int) -> CheckResult:
    child_code = textwrap.dedent(
        """
        import json
        import sys
        from app import prediction_explain

        tickers = json.loads(sys.argv[1])
        rows = []
        for ticker in tickers:
            try:
                result = prediction_explain(ticker)
                if hasattr(result, "body"):
                    payload = {"response_type": type(result).__name__}
                else:
                    payload = result
                rows.append({
                    "ticker": ticker,
                    "ok": not bool(isinstance(payload, dict) and payload.get("error")),
                    "keys": sorted(payload.keys()) if isinstance(payload, dict) else [],
                    "recommendation": payload.get("recommendation") if isinstance(payload, dict) else None,
                    "has_domain_warnings": bool(payload.get("domain_warnings")) if isinstance(payload, dict) else False,
                    "error": payload.get("error") if isinstance(payload, dict) else None,
                    "status": payload.get("status") if isinstance(payload, dict) else None,
                })
            except Exception as exc:
                rows.append({
                    "ticker": ticker,
                    "ok": False,
                    "keys": [],
                    "recommendation": None,
                    "has_domain_warnings": False,
                    "error": repr(exc),
                    "status": "exception",
                })
        print(json.dumps(rows, ensure_ascii=False))
        """
    ).strip()
    start = time.time()
    try:
        child_env = os.environ.copy()
        child_env["PYTHONIOENCODING"] = "utf-8"
        completed = subprocess.run(
            [sys.executable, "-c", child_code, json.dumps(tickers)],
            cwd=str(BASE_DIR),
            env=child_env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_sec,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return CheckResult(
            name="prediction_explain_smoke",
            status="FAIL",
            detail=f"prediction_explain timed out after {timeout_sec}s",
            data={"tickers": tickers, "timeout_sec": timeout_sec},
        )

    elapsed = round(time.time() - start, 3)
    if completed.returncode != 0:
        return CheckResult(
            name="prediction_explain_smoke",
            status="FAIL",
            detail="prediction_explain child process failed",
            data={
                "tickers": tickers,
                "returncode": completed.returncode,
                "elapsed_sec": elapsed,
                "stdout_tail": completed.stdout[-2000:],
                "stderr_tail": completed.stderr[-4000:],
            },
        )
    try:
        rows = json.loads(completed.stdout.strip().splitlines()[-1])
    except Exception as exc:
        return CheckResult(
            name="prediction_explain_smoke",
            status="FAIL",
            detail=f"cannot parse prediction_explain smoke output: {exc}",
            data={
                "tickers": tickers,
                "elapsed_sec": elapsed,
                "stdout_tail": completed.stdout[-4000:],
                "stderr_tail": completed.stderr[-4000:],
            },
        )

    failed = [row for row in rows if not row.get("ok")]
    status = "PASS" if not failed else "WARN"
    detail = "prediction_explain returned usable payloads for smoke tickers"
    if failed:
        detail = "prediction_explain returned unavailable/warming/error payloads for some tickers"
    return CheckResult(
        name="prediction_explain_smoke",
        status=status,
        detail=detail,
        data={"elapsed_sec": elapsed, "rows": rows, "stderr_tail": completed.stderr[-2000:]},
    )


def check_etf_review_policy(etf_tickers: list[str]) -> CheckResult:
    rows = []
    for ticker in etf_tickers:
        ticker = str(ticker).upper()
        classification = classify_ticker(ticker)
        k_status = _daily_k_status(ticker)
        if not k_status["exists"]:
            review_status = "ETF_DATA_MISSING_BACKFILL_REQUIRED"
        else:
            review_status = "ETF_RULE_SUBSET_ONLY"
        rows.append(
            {
                "ticker": ticker,
                "classification": classification,
                "is_leveraged_or_inverse": classification in {"etf_leveraged", "etf_inverse"},
                "daily_k_exists": bool(k_status["exists"]),
                "review_status": review_status,
                "v2_alpha_model_status": "ETF_V2_ALPHA_MODEL_UNSUPPORTED",
                "rule_pool": "technical_liquidity_price_event_subset",
            }
        )
    missing = [row["ticker"] for row in rows if row["review_status"] == "ETF_DATA_MISSING_BACKFILL_REQUIRED"]
    status = "WARN" if missing else "PASS"
    return CheckResult(
        name="etf_review_policy",
        status=status,
        detail="ETF MVP policy is explicit; missing ETF K data requires backfill" if missing else "ETF rule-subset policy ready",
        data={"rows": rows, "missing_etf_daily_k": missing},
    )


def _overall_status(results: list[CheckResult]) -> str:
    statuses = {result.status for result in results}
    if "FAIL" in statuses:
        return "FAIL"
    if "WARN" in statuses:
        return "WARN"
    return "PASS"


def _write_markdown(path: Path, payload: dict[str, Any]) -> None:
    lines = [
        "# My Holdings Phase 0 Smoke",
        "",
        "## Summary",
        "",
        f"- Generated at: {payload['generated_at']}",
        f"- Overall status: `{payload['overall_status']}`",
        f"- Stock tickers: {', '.join(payload['inputs']['stock_tickers'])}",
        f"- ETF tickers: {', '.join(payload['inputs']['etf_tickers'])}",
        f"- Prediction policy ETF universe count: {payload['inputs']['prediction_policy_etf_count']}",
        "",
        "## Checks",
        "",
        "| check | status | detail |",
        "| --- | --- | --- |",
    ]
    for result in payload["checks"]:
        lines.append(f"| {result['name']} | `{result['status']}` | {result['detail']} |")

    explain = next((item for item in payload["checks"] if item["name"] == "prediction_explain_smoke"), None)
    if explain and explain["data"].get("rows"):
        lines.extend(["", "## Prediction Explain Smoke", "", "| ticker | ok | recommendation | status | error |", "| --- | ---: | --- | --- | --- |"])
        for row in explain["data"]["rows"]:
            lines.append(
                f"| {row['ticker']} | {row['ok']} | {row.get('recommendation') or ''} | "
                f"{row.get('status') or ''} | {str(row.get('error') or '').replace('|', '/')} |"
            )

    etf = next((item for item in payload["checks"] if item["name"] == "etf_review_policy"), None)
    if etf and etf["data"].get("rows"):
        lines.extend(["", "## ETF Policy", "", "| ticker | class | daily K | review status | model status |", "| --- | --- | ---: | --- | --- |"])
        for row in etf["data"]["rows"]:
            lines.append(
                f"| {row['ticker']} | {row['classification']} | {row['daily_k_exists']} | {row['review_status']} | "
                f"{row['v2_alpha_model_status']} |"
            )

    classifications = next((item for item in payload["checks"] if item["name"] == "ticker_classification"), None)
    if classifications and classifications["data"].get("rows"):
        lines.extend(["", "## Ticker Classification", "", "| ticker | class | market | name |", "| --- | --- | --- | --- |"])
        for row in classifications["data"]["rows"]:
            lines.append(
                f"| {row['ticker']} | {row['classification']} | {row.get('market') or ''} | "
                f"{row.get('name') or ''} |"
            )

    lines.extend(
        [
            "",
            "## Notes",
            "",
            "- This smoke report is read-only and does not create `my_holdings.db`.",
            "- ETF MVP must not display V2 alpha-model predictions; use rule-subset signals only.",
            "- Missing ETF daily K rows mean backfill is required before ETF rule-subset reviews can be useful.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def run_smoke(args: argparse.Namespace) -> dict[str, Any]:
    stock_tickers = [str(ticker).zfill(4) for ticker in args.stock_tickers]
    etf_tickers = [str(ticker).upper() for ticker in args.etf_tickers]
    classification_tickers = [str(ticker).upper() for ticker in args.classification_tickers]
    prediction_policy_etfs = sorted(ETF_TICKERS)
    results = [
        check_gitignore(),
        check_daily_k(stock_tickers, etf_tickers),
        check_prediction_csv(stock_tickers, prediction_policy_etfs),
        check_market_regime(),
        check_etf_review_policy(etf_tickers),
        check_ticker_classification(classification_tickers),
    ]
    if args.skip_explain:
        results.append(
            CheckResult(
                name="prediction_explain_smoke",
                status="WARN",
                detail="skipped by --skip-explain",
                data={"rows": []},
            )
        )
    else:
        results.append(run_prediction_explain_smoke(stock_tickers, args.explain_timeout_sec))

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "overall_status": _overall_status(results),
        "inputs": {
            "stock_tickers": stock_tickers,
            "etf_tickers": etf_tickers,
            "classification_tickers": classification_tickers,
            "prediction_policy_etf_count": len(prediction_policy_etfs),
            "explain_timeout_sec": args.explain_timeout_sec,
            "skip_explain": bool(args.skip_explain),
        },
        "checks": [
            {
                "name": result.name,
                "status": result.status,
                "detail": result.detail,
                "data": result.data,
            }
            for result in results
        ],
    }
    payload = _safe_json(payload)

    report_dir = Path(args.report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    json_path = report_dir / LATEST_JSON
    md_path = report_dir / LATEST_MD
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_markdown(md_path, payload)
    payload["json_path"] = str(json_path)
    payload["markdown_path"] = str(md_path)
    return payload


def main(argv: list[str] | None = None) -> dict[str, Any]:
    args = _parse_args(argv)
    payload = run_smoke(args)
    print(
        "[my-holdings-phase0] "
        f"status={payload['overall_status']} "
        f"report={payload['markdown_path']} "
        f"json={payload['json_path']}"
    )
    for result in payload["checks"]:
        print(f"[my-holdings-phase0] {result['name']}={result['status']} {result['detail']}")
    return payload


if __name__ == "__main__":
    main()
