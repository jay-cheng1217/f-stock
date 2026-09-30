"""Quality checks for the public market context snapshot layer.

The public context fetcher currently stores latest official snapshots only.
This module makes the gap explicit for weekly V2 retrain and unified signal
artifacts: the data is usable as advisory metadata, but it is not a training
feature until a point-in-time historical panel exists and is backtested.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import date, datetime
from pathlib import Path
from typing import Any

BASE_DIR = Path(os.environ.get("STOCK_BASE_DIR") or Path(__file__).resolve().parents[1])
DATA_DIR = BASE_DIR / "ml" / "data" / "public_market_context"
REPORT_DIR = BASE_DIR / "ml" / "reports"
SOURCE_REPORT_PATH = REPORT_DIR / "public_market_context_latest.json"
QUALITY_REPORT_PATH = REPORT_DIR / "public_market_context_quality_latest.json"

EXPECTED_FIELDS: dict[str, tuple[str, ...]] = {
    "taifex_put_call_ratio": ("Date", "PutCallVolumeRatio%", "PutCallOIRatio%"),
    "taifex_major_institutional_general": (
        "Date",
        "Item",
        "TradingVolume(Net)",
        "OpenInterest(Net)",
    ),
    "taifex_large_trader_futures_oi": (
        "Date",
        "Contract",
        "SettlementMonth",
        "Top5Buy",
        "Top5Sell",
        "OIOfMarket",
    ),
    "taifex_futures_fcm_volume": ("Date", "Contract", "FCMCode", "Volume"),
    "twse_market_5s_order_trade": (
        "Time",
        "AccBidOrders",
        "AccBidVolume",
        "AccAskOrders",
        "AccAskVolume",
    ),
    "twse_sbl_available": (
        "TWSECode",
        "TWSEAvailableVolume",
        "GRETAICode",
        "GRETAIAvailableVolume",
    ),
    "twse_exright_forecast": ("Date", "Code", "Name", "Exdividend"),
    "twse_attention": ("Date", "Code", "Name", "TradingInfoForAttention"),
    "twse_disposition": ("Date", "Code", "Name", "DispositionPeriod", "DispositionMeasures"),
    "twse_news": ("Date", "Title", "Url"),
    "tpex_intraday_statistics": (
        "Date",
        "DayTradingVolume",
        "DayTradingValueOfBuys",
        "DayTradingValueOfSells",
    ),
    "tpex_intraday_trading_his": (
        "Date",
        "SecuritiesCompanyCode",
        "FirstDayToSuspendSellThenBuy",
        "LastDayToSuspendSellThenBuy",
    ),
    "tpex_margin_sbl": (
        "Date",
        "SecuritiesCompanyCode",
        "SaleBalanceOfTheMarketDay",
        "AvailableVolumesForSBLShortSale",
    ),
    "tpex_short_sell": ("Date", "SecuritiesCompanyCode", "ShortSaleVolume", "SBLVolume"),
    "tpex_exright_daily": ("Date", "SecuritiesCompanyCode", "ExRightsDiviend"),
    "tpex_trading_warning": ("Date", "SecuritiesCompanyCode", "TradingInformation"),
    "tpex_peratio": (
        "Date",
        "SecuritiesCompanyCode",
        "PriceEarningRatio",
        "PriceBookRatio",
    ),
}

MODEL_CANDIDATE_DATASETS: tuple[str, ...] = (
    "taifex_put_call_ratio",
    "taifex_major_institutional_general",
    "taifex_large_trader_futures_oi",
    "twse_sbl_available",
    "twse_attention",
    "twse_disposition",
    "twse_news",
    "tpex_margin_sbl",
    "tpex_short_sell",
    "tpex_trading_warning",
    "tpex_peratio",
)


def _read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _parse_iso_date(value: Any) -> date | None:
    if value is None:
        return None
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp_path, path)


def _field_check(base_dir: Path, item: dict[str, Any]) -> dict[str, Any]:
    dataset_id = str(item.get("dataset_id") or "")
    expected = EXPECTED_FIELDS.get(dataset_id, ())
    output_path = item.get("output_path")
    rows: list[dict[str, Any]] = []
    if output_path:
        path = base_dir / str(output_path)
        if path.exists():
            try:
                payload = _read_json(path)
                raw_rows = payload.get("rows") if isinstance(payload, dict) else []
                rows = [row for row in raw_rows if isinstance(row, dict)]
            except Exception:
                rows = []
    available = set().union(*(row.keys() for row in rows[:20])) if rows else set()
    missing = [field for field in expected if field not in available]
    return {
        "dataset_id": dataset_id,
        "expected_fields": list(expected),
        "missing_fields": missing,
        "field_check_pass": not missing,
        "sampled_rows": min(len(rows), 20),
    }


def evaluate_public_market_context(
    *,
    base_dir: str | Path = BASE_DIR,
    as_of_date: str | date | None = None,
    max_stale_calendar_days: int = 5,
) -> dict[str, Any]:
    """Evaluate freshness, required-source status, and expected field coverage."""

    root = Path(base_dir)
    source_report_path = root / "ml" / "reports" / "public_market_context_latest.json"
    as_of = (
        as_of_date
        if isinstance(as_of_date, date)
        else datetime.strptime(str(as_of_date), "%Y-%m-%d").date()
        if as_of_date
        else date.today()
    )

    if not source_report_path.exists():
        return {
            "status": "missing",
            "generated_at": None,
            "as_of_date": as_of.isoformat(),
            "preflight_pass": False,
            "reason": "missing_public_market_context_report",
            "selection_usage_mode": "unavailable",
            "model_feature_readiness": "not_ready",
            "model_feature_gap": "latest_snapshot_report_missing",
            "dataset_count": 0,
            "ok_count": 0,
            "failed_count": 0,
            "required_failures": [],
            "stale_datasets": [],
            "missing_field_datasets": [],
            "candidate_feature_datasets": list(MODEL_CANDIDATE_DATASETS),
            "latest_dates": {},
        }

    report = _read_json(source_report_path)
    datasets = [item for item in report.get("datasets", []) if isinstance(item, dict)]
    required_failures = [
        str(item.get("dataset_id"))
        for item in datasets
        if bool(item.get("required")) and str(item.get("status")).lower() != "ok"
    ]
    stale_datasets: list[dict[str, Any]] = []
    latest_dates: dict[str, str | None] = {}
    for item in datasets:
        dataset_id = str(item.get("dataset_id") or "")
        latest = _parse_iso_date(item.get("latest_date"))
        latest_dates[dataset_id] = latest.isoformat() if latest else None
        if latest is None:
            stale_datasets.append({"dataset_id": dataset_id, "latest_date": None, "age_days": None})
            continue
        age_days = (as_of - latest).days
        if age_days > max_stale_calendar_days:
            stale_datasets.append(
                {
                    "dataset_id": dataset_id,
                    "latest_date": latest.isoformat(),
                    "age_days": age_days,
                }
            )

    field_checks = [_field_check(root, item) for item in datasets]
    missing_field_datasets = [
        {
            "dataset_id": item["dataset_id"],
            "missing_fields": item["missing_fields"],
        }
        for item in field_checks
        if not item["field_check_pass"]
    ]

    source_status = str(report.get("status") or "unknown").lower()
    preflight_pass = source_status != "failed" and not required_failures and not missing_field_datasets
    status = "ok" if preflight_pass and not stale_datasets else "degraded"
    if source_status == "failed" or required_failures or missing_field_datasets:
        status = "failed"

    return {
        "status": status,
        "source_status": source_status,
        "generated_at": report.get("generated_at"),
        "as_of_date": as_of.isoformat(),
        "preflight_pass": bool(preflight_pass),
        "reason": (
            "ok"
            if status == "ok"
            else "required_or_field_gap"
            if status == "failed"
            else "stale_optional_context"
        ),
        "selection_usage_mode": "advisory_metadata_only",
        "model_feature_readiness": "not_ready",
        "model_feature_gap": (
            "latest_snapshot_only_no_point_in_time_training_panel; "
            "do_not_use_as_lightgbm_feature_until historical_backfill + leakage_review + backtest"
        ),
        "dataset_count": int(report.get("dataset_count") or len(datasets)),
        "ok_count": int(report.get("ok_count") or 0),
        "failed_count": int(report.get("failed_count") or 0),
        "required_failures": required_failures,
        "stale_datasets": stale_datasets,
        "missing_field_datasets": missing_field_datasets,
        "field_checks": field_checks,
        "candidate_feature_datasets": list(MODEL_CANDIDATE_DATASETS),
        "latest_dates": latest_dates,
    }


def compact_public_market_context_columns(quality: dict[str, Any]) -> dict[str, Any]:
    """Flatten quality output for row-level signal metadata."""

    stale = quality.get("stale_datasets") or []
    missing_fields = quality.get("missing_field_datasets") or []
    return {
        "public_market_context_status": quality.get("status"),
        "public_market_context_generated_at": quality.get("generated_at"),
        "public_market_context_preflight_pass": bool(quality.get("preflight_pass")),
        "public_market_context_usage_mode": quality.get("selection_usage_mode"),
        "public_market_context_time_basis": "latest_snapshot_not_point_in_time",
        "public_market_context_model_feature_readiness": quality.get("model_feature_readiness"),
        "public_market_context_model_feature_gap": quality.get("model_feature_gap"),
        "public_market_context_stale_count": len(stale),
        "public_market_context_missing_field_count": len(missing_fields),
        "public_market_context_required_failures": ";".join(quality.get("required_failures") or []),
        "public_market_context_stale_datasets": ";".join(
            str(item.get("dataset_id")) for item in stale if isinstance(item, dict)
        ),
        "public_market_context_missing_field_datasets": ";".join(
            str(item.get("dataset_id")) for item in missing_fields if isinstance(item, dict)
        ),
    }


def write_public_market_context_quality_report(
    quality: dict[str, Any],
    *,
    path: str | Path = QUALITY_REPORT_PATH,
) -> str:
    target = Path(path)
    _atomic_write_json(target, quality)
    return str(target)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Check public market context snapshot quality.")
    parser.add_argument("--as-of-date", default=None, help="YYYY-MM-DD. Defaults to today.")
    parser.add_argument("--max-stale-calendar-days", type=int, default=5)
    parser.add_argument("--write", action="store_true", help="Write latest quality report artifact.")
    args = parser.parse_args(argv)

    quality = evaluate_public_market_context(
        as_of_date=args.as_of_date,
        max_stale_calendar_days=args.max_stale_calendar_days,
    )
    if args.write:
        quality["quality_report_path"] = write_public_market_context_quality_report(quality)
    print(json.dumps(quality, ensure_ascii=False, indent=2))
    if quality["status"] == "failed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
