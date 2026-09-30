from __future__ import annotations

import glob
import json
import os
import re
import sqlite3
from functools import lru_cache
from typing import Any

import pandas as pd
from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse, StreamingResponse

from backend.services.warroom_service import CHAMPION_DB_PATH
from ml.config import MODEL_DIR
from ml.features.sector import SECTOR_MAPPING_PATH
from scripts.signal_explainability import explain_signal_row
from ml.t1_production_gate import evaluate_t1_production_gate

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
UNIFIED_PORTFOLIO_DB_PATH = CHAMPION_DB_PATH
UNIFIED_PORTFOLIO_START_DATE = "2026-04-09"
UNIFIED_SIGNAL_RE = re.compile(r"^unified_signals_(\d{4}-\d{2}-\d{2})\.csv$")
SIGNAL_TYPE_MAP = {
    "dual": "Dual",
    "20d_only": "20D_only",
    "t1_only": "T1_only",
}
SIGNAL_TYPE_ORDER = ("Dual", "20D_only", "T1_only")
STATUS_GROUPS = {
    "ACTIVE": ("pending", "open"),
    "PENDING": ("pending",),
    "OPEN": ("open",),
    "CLOSED": ("closed",),
    "STOPPED_OUT": ("stopped_out",),
}
VALID_FORMATS = {"json", "csv"}

router = APIRouter(tags=["unified"])


@lru_cache(maxsize=1)
def _load_name_lookup() -> dict[str, str]:
    if not os.path.exists(SECTOR_MAPPING_PATH):
        return {}

    df = pd.read_csv(SECTOR_MAPPING_PATH, dtype={"Ticker": str})
    if "Name" not in df.columns:
        return {}
    return dict(zip(df["Ticker"], df["Name"]))


def _normalize_signal_type(signal_type: str | None) -> str | None:
    if signal_type is None:
        return None

    key = str(signal_type).strip().lower()
    if not key:
        return None
    if key not in SIGNAL_TYPE_MAP:
        allowed = ", ".join(SIGNAL_TYPE_ORDER)
        raise ValueError(f"Unsupported signal_type '{signal_type}'. Expected one of: {allowed}.")
    return SIGNAL_TYPE_MAP[key]


def _normalize_format(output_format: str) -> str:
    normalized = str(output_format or "json").strip().lower()
    if normalized not in VALID_FORMATS:
        allowed = ", ".join(sorted(VALID_FORMATS))
        raise ValueError(f"Unsupported format '{output_format}'. Expected one of: {allowed}.")
    return normalized


def _parse_status_filter(status: str | None) -> list[str] | None:
    if status is None:
        return None

    raw = str(status).strip()
    if not raw:
        return None

    resolved: list[str] = []
    seen: set[str] = set()
    for token in re.split(r"[\s,]+", raw):
        if not token:
            continue
        upper_token = token.upper()
        if upper_token in STATUS_GROUPS:
            values = STATUS_GROUPS[upper_token]
        else:
            lower_token = token.lower()
            if lower_token not in {"pending", "open", "closed", "stopped_out"}:
                allowed = ", ".join(list(STATUS_GROUPS.keys()) + ["pending", "open", "closed", "stopped_out"])
                raise ValueError(f"Unsupported status '{token}'. Expected one of: {allowed}.")
            values = (lower_token,)

        for value in values:
            if value not in seen:
                resolved.append(value)
                seen.add(value)

    return resolved or None


def _latest_unified_signal_path() -> str | None:
    candidates = [
        path
        for path in sorted(glob.glob(os.path.join(MODEL_DIR, "unified_signals_*.csv")))
        if UNIFIED_SIGNAL_RE.match(os.path.basename(path))
    ]
    return candidates[-1] if candidates else None


def _safe_records(df: pd.DataFrame) -> list[dict[str, Any]]:
    if df.empty:
        return []
    return json.loads(df.to_json(orient="records", force_ascii=False))


def _add_signal_explain_columns(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df

    out = df.copy()
    explanations = [explain_signal_row(row, source="unified") for row in out.to_dict(orient="records")]
    out["explain_action"] = [item["action"] for item in explanations]
    out["explain_action_label"] = [item["action_label"] for item in explanations]
    out["explain_summary"] = [item["summary"] for item in explanations]
    out["explain_drivers"] = [" / ".join(item["drivers"]) for item in explanations]
    out["explain_warnings"] = [" / ".join(item["warnings"]) for item in explanations]
    return out


def _signal_type_counts(series: pd.Series | None) -> dict[str, int]:
    counts = {signal_type: 0 for signal_type in SIGNAL_TYPE_ORDER}
    if series is None:
        return counts

    for signal_type, count in series.dropna().astype(str).value_counts().items():
        counts[signal_type] = int(count)
    return counts


def _connect_unified_db() -> sqlite3.Connection:
    conn = sqlite3.connect(UNIFIED_PORTFOLIO_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _latest_mark_cte() -> str:
    return """
        WITH latest_marks AS (
            SELECT
                position_id,
                mark_date,
                close,
                close_return_pct,
                ROW_NUMBER() OVER (
                    PARTITION BY position_id
                    ORDER BY mark_date DESC
                ) AS rn
            FROM unified_marks
        )
    """


def _empty_portfolio_summary(
    status_filter: list[str] | None,
    signal_type: str | None,
) -> dict[str, Any]:
    return {
        "run_count": 0,
        "latest_run_date": None,
        "latest_signal_date": None,
        "latest_mark_date": None,
        "total_positions": 0,
        "total_tickers": 0,
        "active_positions": 0,
        "pending_positions": 0,
        "open_positions": 0,
        "closed_positions": 0,
        "stopped_out_positions": 0,
        "invested_weight_pct": 0.0,
        "avg_open_return_pct": None,
        "avg_closed_return_pct": None,
        "signal_type_counts": _signal_type_counts(None),
        "filters": {
            "status": status_filter or [],
            "signal_type": signal_type,
        },
    }


def _load_latest_unified_signals_df() -> tuple[pd.DataFrame, str]:
    signal_path = _latest_unified_signal_path()
    if signal_path is None:
        raise FileNotFoundError("No unified_signals_YYYY-MM-DD.csv file was found.")

    df = pd.read_csv(signal_path, encoding="utf-8-sig", dtype={"ticker": str})
    if "ticker" not in df.columns:
        raise ValueError(f"{os.path.basename(signal_path)} is missing the ticker column.")

    names = _load_name_lookup()
    df["name"] = df["ticker"].map(names).fillna("")
    for column in [
        "target_units",
        "target_weight_ratio",
        "rank_20d",
        "rank_t1",
        "hit_prob_3pct",
        "pred_return_20d",
        "close_ref",
    ]:
        if column in df.columns:
            df[column] = pd.to_numeric(df[column], errors="coerce")

    if "target_weight_ratio" in df.columns:
        df["target_weight_pct"] = (df["target_weight_ratio"] * 100).round(2)
    else:
        df["target_weight_pct"] = None

    if "prediction_date" in df.columns and not df.empty:
        prediction_date = str(df["prediction_date"].iloc[0])
    else:
        match = UNIFIED_SIGNAL_RE.match(os.path.basename(signal_path))
        prediction_date = match.group(1) if match else None
        if prediction_date is not None:
            df["prediction_date"] = prediction_date

    return df, signal_path


def _portfolio_base_query(status_filter: list[str] | None, signal_type: str | None) -> tuple[str, list[Any]]:
    where_clauses = ["p.prediction_date >= ?"]
    params: list[Any] = [UNIFIED_PORTFOLIO_START_DATE]

    if status_filter:
        placeholders = ", ".join("?" for _ in status_filter)
        where_clauses.append(f"p.status IN ({placeholders})")
        params.extend(status_filter)

    if signal_type:
        where_clauses.append("p.current_signal_type = ?")
        params.append(signal_type)

    query = f"""
        {_latest_mark_cte()}
        SELECT
            p.id,
            p.prediction_date,
            p.last_signal_date,
            p.ticker,
            p.sector,
            p.entry_signal_type,
            p.current_signal_type AS signal_type,
            p.target_units,
            p.target_weight,
            p.planned_entry_ref_price,
            p.entry_date,
            p.entry_price,
            p.exit_policy,
            p.hold_days_target,
            p.upgrade_count,
            p.status,
            p.days_observed,
            p.last_mark_date,
            p.exit_date,
            p.exit_price,
            p.realized_return_pct,
            p.max_drawdown_pct,
            lm.mark_date AS latest_mark_date,
            lm.close AS latest_price,
            lm.close_return_pct AS latest_close_return_pct
        FROM unified_positions p
        LEFT JOIN latest_marks lm
            ON lm.position_id = p.id
           AND lm.rn = 1
        WHERE {" AND ".join(where_clauses)}
        ORDER BY
            CASE WHEN p.status IN ('pending', 'open') THEN 0 ELSE 1 END,
            p.target_weight DESC,
            CASE
                WHEN p.current_signal_type = 'Dual' THEN 0
                WHEN p.current_signal_type = '20D_only' THEN 1
                WHEN p.current_signal_type = 'T1_only' THEN 2
                ELSE 3
            END,
            p.prediction_date DESC,
            p.ticker ASC
    """
    return query, params


def _load_unified_portfolio_df(
    status_filter: list[str] | None,
    signal_type: str | None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    columns = [
        "id",
        "prediction_date",
        "last_signal_date",
        "ticker",
        "name",
        "sector",
        "signal_type",
        "entry_signal_type",
        "status",
        "target_units",
        "target_weight_pct",
        "planned_entry_ref_price",
        "entry_date",
        "entry_price",
        "latest_mark_date",
        "latest_price",
        "exit_date",
        "exit_price",
        "return_pct",
        "max_drawdown_pct",
        "exit_policy",
        "hold_days_target",
        "days_observed",
        "upgrade_count",
    ]
    empty_df = pd.DataFrame(columns=columns)

    if not os.path.exists(UNIFIED_PORTFOLIO_DB_PATH):
        return empty_df, _empty_portfolio_summary(status_filter=status_filter, signal_type=signal_type)

    query, params = _portfolio_base_query(status_filter=status_filter, signal_type=signal_type)
    conn = _connect_unified_db()
    try:
        df = pd.read_sql_query(query, conn, params=params)
        run_summary = conn.execute(
            """
            SELECT
                COUNT(*) AS run_count,
                MAX(prediction_date) AS latest_run_date
            FROM unified_runs
            WHERE prediction_date >= ?
            """,
            (UNIFIED_PORTFOLIO_START_DATE,),
        ).fetchone()
    finally:
        conn.close()

    summary = _empty_portfolio_summary(status_filter=status_filter, signal_type=signal_type)
    summary["run_count"] = int(run_summary["run_count"] or 0)
    summary["latest_run_date"] = run_summary["latest_run_date"]

    if df.empty:
        return empty_df, summary

    names = _load_name_lookup()
    df["ticker"] = df["ticker"].astype(str)
    df["signal_type"] = df["signal_type"].fillna("").astype(str)
    df["entry_signal_type"] = df["entry_signal_type"].fillna("").astype(str)
    df["name"] = df["ticker"].map(names).fillna("")
    for column in [
        "target_units",
        "target_weight",
        "planned_entry_ref_price",
        "entry_price",
        "latest_price",
        "exit_price",
        "realized_return_pct",
        "latest_close_return_pct",
        "max_drawdown_pct",
        "hold_days_target",
        "days_observed",
        "upgrade_count",
    ]:
        df[column] = pd.to_numeric(df[column], errors="coerce")

    df["target_weight_pct"] = (df["target_weight"] * 100).round(2)
    closed_mask = df["status"].isin(["closed", "stopped_out"])
    return_ratio = df["latest_close_return_pct"].where(~closed_mask, df["realized_return_pct"])
    df["return_pct"] = (return_ratio * 100).round(2)
    df["max_drawdown_pct"] = (df["max_drawdown_pct"] * 100).round(2)
    df["latest_price"] = df["latest_price"].where(df["latest_price"].notna(), df["exit_price"])

    status_series = df["status"].fillna("")
    signal_series = df["signal_type"].fillna("")
    active_mask = status_series.isin(["pending", "open"])
    open_mask = status_series == "open"
    closed_or_stopped_mask = status_series.isin(["closed", "stopped_out"])

    summary.update(
        {
            "latest_signal_date": df["last_signal_date"].dropna().astype(str).max() if not df.empty else None,
            "latest_mark_date": df["latest_mark_date"].dropna().astype(str).max() if not df.empty else None,
            "total_positions": int(len(df)),
            "total_tickers": int(df["ticker"].astype(str).nunique()),
            "active_positions": int(active_mask.sum()),
            "pending_positions": int((status_series == "pending").sum()),
            "open_positions": int(open_mask.sum()),
            "closed_positions": int((status_series == "closed").sum()),
            "stopped_out_positions": int((status_series == "stopped_out").sum()),
            "invested_weight_pct": round(float(df.loc[active_mask, "target_weight"].fillna(0.0).sum() * 100), 2),
            "avg_open_return_pct": (
                round(float(df.loc[open_mask, "return_pct"].mean()), 2)
                if open_mask.any() and df.loc[open_mask, "return_pct"].notna().any()
                else None
            ),
            "avg_closed_return_pct": (
                round(float(df.loc[closed_or_stopped_mask, "return_pct"].mean()), 2)
                if closed_or_stopped_mask.any() and df.loc[closed_or_stopped_mask, "return_pct"].notna().any()
                else None
            ),
            "signal_type_counts": _signal_type_counts(signal_series),
        }
    )

    api_df = df.loc[:, columns]
    return api_df, summary


def _portfolio_export_df(df: pd.DataFrame) -> pd.DataFrame:
    export_df = df.copy()
    rename_map = {
        "prediction_date": "Prediction Date",
        "last_signal_date": "Last Signal Date",
        "ticker": "Ticker",
        "name": "Name",
        "sector": "Sector",
        "signal_type": "Signal Type",
        "entry_signal_type": "Entry Signal Type",
        "status": "Status",
        "target_units": "Target Units",
        "target_weight_pct": "Target Weight %",
        "planned_entry_ref_price": "Planned Entry Ref Price",
        "entry_date": "Entry Date",
        "entry_price": "Entry Price",
        "latest_mark_date": "Latest Mark Date",
        "latest_price": "Latest Price",
        "exit_date": "Exit Date",
        "exit_price": "Exit Price",
        "return_pct": "Return %",
        "max_drawdown_pct": "Max Drawdown %",
        "exit_policy": "Exit Policy",
        "hold_days_target": "Hold Days Target",
        "days_observed": "Days Observed",
        "upgrade_count": "Upgrade Count",
    }
    available_columns = [column for column in rename_map if column in export_df.columns]
    return export_df.loc[:, available_columns].rename(columns=rename_map)


def _signals_export_df(df: pd.DataFrame) -> pd.DataFrame:
    export_df = df.copy()
    rename_map = {
        "prediction_date": "Prediction Date",
        "ticker": "Ticker",
        "name": "Name",
        "sector": "Sector",
        "signal_type": "Signal Type",
        "signal_type_before_gate": "Signal Type Before Gate",
        "target_units": "Target Units",
        "target_weight_pct": "Target Weight %",
        "rank_20d": "20D Rank",
        "rank_t1": "T1 Rank",
        "hit_prob_3pct": "T1 Hit Prob",
        "pred_return_20d": "20D Pred Return",
        "recommendation_20d": "20D Recommendation",
        "recommendation_t1": "T1 Recommendation",
        "setup_tags_t1": "T1 Setup Tags",
        "risk_tags_20d": "20D Risk Tags",
        "risk_tags_t1": "T1 Risk Tags",
        "t1_block_reason": "T1 Block Reason",
        "production_gate_status": "Production Gate Status",
        "production_gate_reason": "Production Gate Reason",
        "close_ref": "Close Ref",
        "explain_action_label": "Explain Action",
        "explain_summary": "Explain Summary",
        "explain_drivers": "Explain Drivers",
        "explain_warnings": "Explain Warnings",
    }
    available_columns = [column for column in rename_map if column in export_df.columns]
    return export_df.loc[:, available_columns].rename(columns=rename_map)


@router.get("/api/unified/signals/latest")
def get_latest_unified_signals(
    signal_type: str | None = Query(default=None),
    format: str = Query(default="json"),
):
    try:
        normalized_signal_type = _normalize_signal_type(signal_type)
        output_format = _normalize_format(format)

        full_df, signal_path = _load_latest_unified_signals_df()
        filtered_df = full_df
        if normalized_signal_type:
            filtered_df = full_df[full_df["signal_type"] == normalized_signal_type].copy()
        filtered_df = _add_signal_explain_columns(filtered_df)

        prediction_date = (
            str(full_df["prediction_date"].iloc[0])
            if not full_df.empty and "prediction_date" in full_df.columns
            else None
        )

        if output_format == "csv":
            export_df = _signals_export_df(filtered_df)
            file_date = prediction_date or "latest"
            file_label = normalized_signal_type or "all"
            csv_bytes = export_df.to_csv(index=False).encode("utf-8-sig")
            return StreamingResponse(
                iter([csv_bytes]),
                media_type="text/csv; charset=utf-8",
                headers={
                    "Content-Disposition": (
                        f'attachment; filename="unified_signals_{file_date}_{file_label}.csv"'
                    )
                },
            )

        api_columns = [
            "prediction_date",
            "ticker",
            "name",
            "sector",
            "signal_type",
            "signal_type_before_gate",
            "target_units",
            "target_weight_pct",
            "rank_20d",
            "rank_t1",
            "hit_prob_3pct",
            "pred_return_20d",
            "recommendation_20d",
            "recommendation_t1",
            "setup_tags_t1",
            "risk_tags_20d",
            "risk_tags_t1",
            "t1_block_reason",
            "production_gate_status",
            "production_gate_reason",
            "close_ref",
            "explain_action",
            "explain_action_label",
            "explain_summary",
            "explain_drivers",
            "explain_warnings",
        ]
        filtered_api_df = filtered_df.loc[:, [column for column in api_columns if column in filtered_df.columns]]

        return JSONResponse(
            content={
                "status": "success",
                "prediction_date": prediction_date,
                "source_file": os.path.basename(signal_path),
                "total": int(len(full_df)),
                "filtered_count": int(len(filtered_df)),
                "signal_counts": _signal_type_counts(full_df["signal_type"] if "signal_type" in full_df.columns else None),
                "production_gate": evaluate_t1_production_gate(),
                "filters": {"signal_type": normalized_signal_type},
                "data": _safe_records(filtered_api_df),
            }
        )
    except FileNotFoundError as exc:
        return JSONResponse(
            content={"status": "error", "message": str(exc), "error": str(exc), "data": []},
            status_code=404,
        )
    except ValueError as exc:
        return JSONResponse(
            content={"status": "error", "message": str(exc), "error": str(exc), "data": []},
            status_code=400,
        )
    except Exception as exc:
        return JSONResponse(
            content={"status": "error", "message": str(exc), "error": str(exc), "data": []},
            status_code=500,
        )


@router.get("/api/unified/portfolio")
def get_unified_portfolio(
    status: str | None = Query(default=None),
    signal_type: str | None = Query(default=None),
    format: str = Query(default="json"),
):
    try:
        normalized_status = _parse_status_filter(status)
        normalized_signal_type = _normalize_signal_type(signal_type)
        output_format = _normalize_format(format)

        detail_df, summary = _load_unified_portfolio_df(
            status_filter=normalized_status,
            signal_type=normalized_signal_type,
        )

        if output_format == "csv":
            export_df = _portfolio_export_df(detail_df)
            file_date = summary.get("latest_signal_date") or summary.get("latest_run_date") or "latest"
            file_label = normalized_signal_type or "all"
            csv_bytes = export_df.to_csv(index=False).encode("utf-8-sig")
            return StreamingResponse(
                iter([csv_bytes]),
                media_type="text/csv; charset=utf-8",
                headers={
                    "Content-Disposition": (
                        f'attachment; filename="unified_portfolio_{file_date}_{file_label}.csv"'
                    )
                },
            )

        return JSONResponse(
            content={
                "status": "success",
                "summary": summary,
                "data": _safe_records(detail_df),
            }
        )
    except ValueError as exc:
        return JSONResponse(
            content={"status": "error", "message": str(exc), "error": str(exc), "data": []},
            status_code=400,
        )
    except Exception as exc:
        return JSONResponse(
            content={"status": "error", "message": str(exc), "error": str(exc), "data": []},
            status_code=500,
        )
