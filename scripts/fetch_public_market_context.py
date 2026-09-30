"""Fetch official public market context snapshots for Taiwan risk overlays.

This data layer is intentionally read-only for production models. It records
official/open-data snapshots and a quality report that can be reviewed before
any feature enters a model, gate, or allocation rule.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import sys
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

if sys.platform == "win32":
    import io

    def _ensure_utf8_stream(stream: Any) -> Any:
        try:
            stream.reconfigure(encoding="utf-8")
            return stream
        except Exception:
            pass
        buffer = getattr(stream, "buffer", None)
        if buffer is None:
            return stream
        try:
            return io.TextIOWrapper(buffer, encoding="utf-8")
        except Exception:
            return stream

    sys.stdout = _ensure_utf8_stream(sys.stdout)
    sys.stderr = _ensure_utf8_stream(sys.stderr)

BASE_DIR = Path(os.environ.get("STOCK_BASE_DIR") or Path(__file__).resolve().parents[1])
DATA_DIR = BASE_DIR / "ml" / "data" / "public_market_context"
REPORT_DIR = BASE_DIR / "ml" / "reports"
REPORT_PATH = REPORT_DIR / "public_market_context_latest.json"
MANIFEST_PATH = DATA_DIR / "manifest_latest.json"

REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
    )
}


@dataclass(frozen=True)
class DatasetSpec:
    dataset_id: str
    source_name: str
    url: str
    purpose: str
    canonical_role: str
    cadence: str
    date_fields: tuple[str, ...] = ("Date",)
    required: bool = False


DATASETS: tuple[DatasetSpec, ...] = (
    DatasetSpec(
        "taifex_put_call_ratio",
        "TAIFEX OpenAPI",
        "https://openapi.taifex.com.tw/v1/PutCallRatio",
        "Index-options sentiment and settlement-week risk context.",
        "canonical",
        "daily_after_close",
        required=True,
    ),
    DatasetSpec(
        "taifex_major_institutional_general",
        "TAIFEX OpenAPI",
        "https://openapi.taifex.com.tw/v1/MarketDataOfMajorInstitutionalTradersGeneralBytheDate",
        "Futures/options institutional positioning by date.",
        "canonical",
        "daily_after_close",
        required=True,
    ),
    DatasetSpec(
        "taifex_large_trader_futures_oi",
        "TAIFEX OpenAPI",
        "https://openapi.taifex.com.tw/v1/OpenInterestOfLargeTradersFutures",
        "Large-trader futures open-interest concentration.",
        "canonical",
        "daily_after_close",
    ),
    DatasetSpec(
        "taifex_futures_fcm_volume",
        "TAIFEX OpenAPI",
        "https://openapi.taifex.com.tw/v1/Daily_FUT",
        "FCM futures volume reference for liquidity and concentration checks.",
        "canonical",
        "daily_after_close",
    ),
    DatasetSpec(
        "twse_market_5s_order_trade",
        "TWSE OpenAPI",
        "https://openapi.twse.com.tw/v1/exchangeReport/MI_5MINS",
        "TWSE market-level intraday order/trade proxy; not a full tick feed.",
        "canonical_proxy",
        "intraday_and_after_close",
        date_fields=("Date", "Time"),
    ),
    DatasetSpec(
        "twse_sbl_available",
        "TWSE OpenAPI",
        "https://openapi.twse.com.tw/v1/SBL/TWT96U",
        "TWSE/TPEx securities-borrowing availability pressure context.",
        "canonical",
        "daily_after_close",
    ),
    DatasetSpec(
        "twse_exright_forecast",
        "TWSE OpenAPI",
        "https://openapi.twse.com.tw/v1/exchangeReport/TWT48U_ALL",
        "Listed-stock ex-right/ex-dividend forecast for return-adjustment review.",
        "canonical",
        "daily",
    ),
    DatasetSpec(
        "twse_attention",
        "TWSE OpenAPI",
        "https://openapi.twse.com.tw/v1/announcement/notice",
        "TWSE attention securities context.",
        "canonical",
        "daily",
    ),
    DatasetSpec(
        "twse_disposition",
        "TWSE OpenAPI",
        "https://openapi.twse.com.tw/v1/announcement/punish",
        "TWSE disposition securities context; legacy gates keep using fetch_disposition.py.",
        "canonical",
        "daily",
    ),
    DatasetSpec(
        "twse_news",
        "TWSE OpenAPI",
        "https://openapi.twse.com.tw/v1/news/newsList",
        "Official TWSE news context for advisory summaries.",
        "canonical",
        "daily",
    ),
    DatasetSpec(
        "tpex_intraday_statistics",
        "TPEx OpenAPI",
        "https://www.tpex.org.tw/openapi/v1/tpex_intraday_trading_statistics",
        "TPEx intraday/day-trading market statistics proxy.",
        "canonical_proxy",
        "daily_after_close",
    ),
    DatasetSpec(
        "tpex_intraday_trading_his",
        "TPEx OpenAPI",
        "https://www.tpex.org.tw/openapi/v1/tpex_intraday_trading_his",
        "TPEx intraday trading restriction/history context.",
        "canonical_proxy",
        "daily",
    ),
    DatasetSpec(
        "tpex_margin_sbl",
        "TPEx OpenAPI",
        "https://www.tpex.org.tw/openapi/v1/tpex_margin_sbl",
        "TPEx margin/SBL detail for short-pressure context.",
        "canonical",
        "daily_after_close",
    ),
    DatasetSpec(
        "tpex_short_sell",
        "TPEx OpenAPI",
        "https://www.tpex.org.tw/openapi/v1/tpex_short_sell",
        "TPEx short-sale and SBL transaction value context.",
        "canonical",
        "daily_after_close",
    ),
    DatasetSpec(
        "tpex_exright_daily",
        "TPEx OpenAPI",
        "https://www.tpex.org.tw/openapi/v1/tpex_exright_daily",
        "TPEx ex-right/ex-dividend event context.",
        "canonical",
        "daily",
    ),
    DatasetSpec(
        "tpex_trading_warning",
        "TPEx OpenAPI",
        "https://www.tpex.org.tw/openapi/v1/tpex_trading_warning_information",
        "TPEx warning securities context.",
        "canonical",
        "daily",
    ),
    DatasetSpec(
        "tpex_peratio",
        "TPEx OpenAPI",
        "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_peratio_analysis",
        "TPEx PE/PB/dividend-yield cross-check for valuation coverage.",
        "canonical",
        "daily_after_close",
    ),
)


def _build_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=3,
        backoff_factor=0.8,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.headers.update(REQUEST_HEADERS)
    return session


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(text, encoding="utf-8")
    os.replace(tmp_path, path)


def _atomic_write_json(path: Path, payload: Any) -> None:
    _atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def _decode_json_bytes(content: bytes) -> Any:
    """解析來源回應:優先 JSON,失敗則嘗試 CSV(轉為 list[dict],與 JSON 格式等價)。

    2026-07-24 事故:TAIFEX OpenAPI 的
    MarketDataOfMajorInstitutionalTradersGeneralBytheDate 從回 JSON 改為回 **CSV**
    (HTTP 仍 200),導致 json.loads 拋 JSONDecodeError → 必要資料集連日失敗。
    來源格式漂移屬常態,解析層改為雙格式容忍;仍無法解析才拋錯(不靜默吞)。
    """
    text = content.decode("utf-8-sig")
    stripped = text.lstrip()
    if stripped.startswith(("[", "{")):
        return json.loads(text)
    # CSV fallback:第一列為表頭
    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows:
        raise ValueError("response is neither valid JSON nor a non-empty CSV table")
    return rows


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _roc_or_yyyymmdd_to_iso(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) < 7:
        return None
    try:
        if len(digits) == 8:
            year = int(digits[:4])
            month = int(digits[4:6])
            day = int(digits[6:8])
        elif len(digits) == 7:
            year = int(digits[:3]) + 1911
            month = int(digits[3:5])
            day = int(digits[5:7])
        else:
            return None
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def _max_iso_date(rows: list[dict[str, Any]], fields: tuple[str, ...]) -> str | None:
    dates: list[str] = []
    for row in rows:
        for field in fields:
            if field not in row:
                continue
            parsed = _roc_or_yyyymmdd_to_iso(row.get(field))
            if parsed:
                dates.append(parsed)
                break
    return max(dates) if dates else None


def _fetch_dataset(session: requests.Session, spec: DatasetSpec, *, timeout: int) -> dict[str, Any]:
    fetched_at = datetime.now().isoformat(timespec="seconds")
    fetched_date = datetime.now().date().isoformat()
    output_path = DATA_DIR / f"{spec.dataset_id}_latest.json"
    started = time.perf_counter()
    try:
        try:
            response = session.get(spec.url, timeout=timeout)
            response.raise_for_status()
            raw = response.content
            http_status = response.status_code
            content_type = response.headers.get("content-type", "")
        except requests.exceptions.SSLError:
            # TPEx 憑證缺 Subject Key Identifier,Python 3.14 拒絕;照專案慣例退回 curl
            # (同 scripts/update_ex_dividend_calendar.py 的處理)。2026-07-24 發現 5 個
            # TPEx 資料集因此長期失敗,錯誤能見度修復後才浮現。
            import subprocess

            out = subprocess.run(
                ["curl", "-sL", "--max-time", str(timeout),
                 "-H", "User-Agent: Mozilla/5.0", spec.url],
                capture_output=True, timeout=timeout + 20,
            )
            if out.returncode != 0 or not out.stdout:
                raise
            raw = out.stdout
            http_status = 200
            content_type = "curl-fallback"
        payload = _decode_json_bytes(raw)
        if not isinstance(payload, list):
            raise ValueError(f"expected list JSON payload, got {type(payload).__name__}")
        rows = [row for row in payload if isinstance(row, dict)]
        _atomic_write_json(
            output_path,
            {
                "dataset": asdict(spec),
                "fetched_at": fetched_at,
                "row_count": len(rows),
                "rows": rows,
            },
        )
        return {
            "dataset_id": spec.dataset_id,
            "source_name": spec.source_name,
            "url": spec.url,
            "purpose": spec.purpose,
            "canonical_role": spec.canonical_role,
            "cadence": spec.cadence,
            "required": spec.required,
            "status": "ok",
            "http_status": http_status,
            "content_type": content_type,
            "row_count": len(rows),
            "latest_date": _max_iso_date(rows, spec.date_fields) or fetched_date,
            "sha256": _sha256_bytes(raw),
            "output_path": str(output_path.relative_to(BASE_DIR)),
            "elapsed_ms": round((time.perf_counter() - started) * 1000.0, 1),
            "error": None,
        }
    except Exception as exc:
        return {
            "dataset_id": spec.dataset_id,
            "source_name": spec.source_name,
            "url": spec.url,
            "purpose": spec.purpose,
            "canonical_role": spec.canonical_role,
            "cadence": spec.cadence,
            "required": spec.required,
            "status": "failed",
            "http_status": None,
            "content_type": None,
            "row_count": 0,
            "latest_date": None,
            "sha256": None,
            "output_path": str(output_path.relative_to(BASE_DIR)),
            "elapsed_ms": round((time.perf_counter() - started) * 1000.0, 1),
            "error": str(exc),
        }


def fetch_public_market_context(
    *,
    selected_ids: set[str] | None = None,
    timeout: int = 20,
) -> dict[str, Any]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    session = _build_session()
    selected = [spec for spec in DATASETS if selected_ids is None or spec.dataset_id in selected_ids]
    results = [_fetch_dataset(session, spec, timeout=timeout) for spec in selected]
    ok_count = sum(1 for item in results if item["status"] == "ok")
    required_failures = [item["dataset_id"] for item in results if item["required"] and item["status"] != "ok"]
    status = "ok"
    if required_failures:
        status = "failed"
    elif ok_count != len(results):
        status = "degraded"
    report = {
        "status": status,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "policy": {
            "mode": "read_only_data_layer",
            "model_gate_impact": "none",
            "canonical_order": "TWSE/TPEx/MOPS/TAIFEX official first; FinMind only as future fallback/cross-check.",
            "note": "These snapshots are not production model features until separately backtested and approved.",
        },
        "dataset_count": len(results),
        "ok_count": ok_count,
        "failed_count": len(results) - ok_count,
        "required_failures": required_failures,
        "datasets": results,
    }
    _atomic_write_json(REPORT_PATH, report)
    _atomic_write_json(
        MANIFEST_PATH,
        {
            "generated_at": report["generated_at"],
            "status": status,
            "datasets": [
                {
                    "dataset_id": item["dataset_id"],
                    "source_name": item["source_name"],
                    "status": item["status"],
                    "latest_date": item["latest_date"],
                    "row_count": item["row_count"],
                    "output_path": item["output_path"],
                    "sha256": item["sha256"],
                    # 2026-07-24:error/http_status 原本被漏掉,導致失敗時 manifest 只寫
                    # status=failed 卻查不到原因(當時誤診為「連線抽風」,實為來源改回 CSV)。
                    "http_status": item["http_status"],
                    "error": item["error"],
                }
                for item in results
            ],
        },
    )
    return report


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Fetch official public market context snapshots.")
    parser.add_argument(
        "--dataset",
        action="append",
        dest="datasets",
        help="Dataset id to fetch. Repeat for multiple. Defaults to all configured datasets.",
    )
    parser.add_argument("--timeout", type=int, default=20)
    args = parser.parse_args(argv)
    selected = set(args.datasets) if args.datasets else None
    unknown = sorted(selected - {spec.dataset_id for spec in DATASETS}) if selected else []
    if unknown:
        raise SystemExit(f"unknown dataset id(s): {', '.join(unknown)}")
    report = fetch_public_market_context(selected_ids=selected, timeout=args.timeout)
    print(
        json.dumps(
            {
                "status": report["status"],
                "dataset_count": report["dataset_count"],
                "ok_count": report["ok_count"],
                "failed_count": report["failed_count"],
                "report_path": str(REPORT_PATH.relative_to(BASE_DIR)),
            },
            ensure_ascii=False,
        )
    )
    if report["status"] == "failed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
