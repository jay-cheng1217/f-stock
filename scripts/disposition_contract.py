"""Read-only, date-aware disposition eligibility and official correction rules.

Chart-pattern selection and trading eligibility are separate decisions. An
unknown result from this module must never be interpreted as an empty veto set.
Date-only knowledge cutoffs mean Taiwan end-of-day; use a timezone-aware
datetime for intraday cutoffs. The raw period file is never rewritten here.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, time, timezone, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

TAIPEI = timezone(timedelta(hours=8))
BASE_DIR = Path(__file__).resolve().parents[1]
CORRECTIONS_PATH = BASE_DIR / "config" / "disposition_corrections.json"
PERIOD_COLUMNS = ["stock_id", "period_start", "period_end"]
SOURCE_NAMES = ("TWSE", "TPEx")


class DispositionContractError(ValueError):
    """The available data cannot establish trading eligibility."""


def _day(value: Any) -> date:
    try:
        text = str(value)
        parsed = date.fromisoformat(text)
        if text != parsed.isoformat():
            raise ValueError("expected YYYY-MM-DD")
        return parsed
    except (TypeError, ValueError) as exc:
        raise DispositionContractError(f"invalid ISO date: {value!r}") from exc


def _cutoff(value: date | datetime | str | None) -> datetime:
    if value is None:
        return datetime.now(TAIPEI)
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, date) or len(str(value)) == 10:
        result = datetime.combine(_day(value), time.max, TAIPEI)
    else:
        try:
            result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError as exc:
            raise DispositionContractError(f"invalid knowledge timestamp: {value!r}") from exc
    if result.tzinfo is None:
        result = result.replace(tzinfo=TAIPEI)
    return result.astimezone(TAIPEI)


def validate_periods(periods: pd.DataFrame) -> pd.DataFrame:
    """Validate every row, including empty-frame schema; never silently drop it."""
    missing = sorted(set(PERIOD_COLUMNS) - set(periods.columns))
    if missing:
        raise DispositionContractError(f"disposition missing columns: {missing}")
    out = periods.copy()
    if out.empty:
        return out.reset_index(drop=True)
    for column in PERIOD_COLUMNS:
        if out[column].isna().any():
            raise DispositionContractError(f"disposition null required field: {column}")
    out["stock_id"] = out["stock_id"].astype(str).str.strip()
    if not out["stock_id"].str.fullmatch(r"[0-9A-Za-z]{4,6}").all():
        raise DispositionContractError("disposition invalid security code")
    for column in ("period_start", "period_end"):
        out[column] = out[column].map(lambda value: _day(value).isoformat())
    if (out["period_start"] > out["period_end"]).any():
        raise DispositionContractError("disposition period starts after it ends")
    if "announcement_date" in out.columns:
        if out["announcement_date"].isna().any():
            raise DispositionContractError("disposition null announcement date")
        out["announcement_date"] = out["announcement_date"].map(lambda value: _day(value).isoformat())
        if (out["announcement_date"] > out["period_start"]).any():
            raise DispositionContractError("disposition announced after period start")
    if "source" in out.columns and not out["source"].isin(SOURCE_NAMES).all():
        raise DispositionContractError("disposition invalid source")
    return out.drop_duplicates().sort_values(PERIOD_COLUMNS).reset_index(drop=True)


def load_corrections(path: str | Path = CORRECTIONS_PATH) -> dict[str, Any]:
    with Path(path).open(encoding="utf-8") as stream:
        payload = json.load(stream)
    if payload.get("schema_version") != 1 or not isinstance(payload.get("corrections"), list):
        raise DispositionContractError("invalid disposition corrections schema")
    return payload


def resolve_disposition_periods(
    periods: pd.DataFrame,
    *,
    knowledge_as_of: date | datetime | str,
    corrections: dict[str, Any] | None = None,
) -> pd.DataFrame:
    """Return periods known by cutoff, applying announced superseding corrections.

    Raw periods retain original dates on disk. `original_period_end` makes this
    transform idempotent and reversible for an earlier cutoff. Legacy three-
    column rows lack announcement dates; do not claim a point-in-time backtest
    from those rows alone (the snapshot gate enforces its fetched-at boundary).
    """
    out = validate_periods(periods)
    cutoff = _cutoff(knowledge_as_of)
    payload = load_corrections() if corrections is None else corrections
    if payload.get("schema_version") != 1 or not isinstance(payload.get("corrections"), list):
        raise DispositionContractError("invalid disposition corrections schema")
    if "original_period_end" in out.columns:
        original = out["original_period_end"].fillna("").astype(str)
        mask = original.ne("")
        out.loc[mask, "period_end"] = original.loc[mask].map(lambda value: _day(value).isoformat())
    else:
        out["original_period_end"] = out["period_end"]
    out["correction_id"] = ""
    out["correction_known_at"] = ""
    if "announcement_date" in out.columns:
        out = out[out["announcement_date"] <= cutoff.date().isoformat()].copy()

    seen_ids: set[str] = set()
    seen_versions: set[tuple[str, str, str]] = set()
    required = {"id", "stock_id", "source", "announcement_date", "period_start",
                "original_period_end", "corrected_period_end", "known_at", "effective_date"}
    for correction in payload["corrections"]:
        if not isinstance(correction, dict) or required - set(correction):
            raise DispositionContractError("correction missing required fields")
        if correction["source"] not in SOURCE_NAMES:
            raise DispositionContractError("correction invalid source")
    for correction in sorted(payload["corrections"], key=lambda row: _cutoff(row["known_at"])):
        known = _cutoff(correction["known_at"])
        key = (correction["stock_id"], correction["period_start"], known.isoformat())
        if correction["id"] in seen_ids or key in seen_versions:
            raise DispositionContractError("duplicate or conflicting disposition correction")
        seen_ids.add(correction["id"])
        seen_versions.add(key)
        start = _day(correction["period_start"])
        old_end = _day(correction["original_period_end"])
        new_end = _day(correction["corrected_period_end"])
        if start > min(old_end, new_end) or _day(correction["announcement_date"]) > start:
            raise DispositionContractError("invalid correction period")
        if _day(correction["effective_date"]) < known.date():
            raise DispositionContractError("correction effective before known date")
        if cutoff < known:
            continue
        match = out["stock_id"].eq(correction["stock_id"]) & out["period_start"].eq(start.isoformat())
        if "source" in out.columns:
            match &= out["source"].eq(correction["source"])
        if not out.loc[match, "period_end"].isin([old_end.isoformat(), new_end.isoformat()]).all():
            raise DispositionContractError(f"unrecognized superseding period for {correction['stock_id']}")
        out.loc[match, "original_period_end"] = old_end.isoformat()
        out.loc[match, "period_end"] = new_end.isoformat()
        out.loc[match, "correction_id"] = correction["id"]
        out.loc[match, "correction_known_at"] = known.isoformat()
    return out.drop_duplicates(PERIOD_COLUMNS).sort_values(PERIOD_COLUMNS).reset_index(drop=True)


def evaluate_disposition_snapshot(
    periods: pd.DataFrame,
    report: dict[str, Any],
    *,
    target_date: date | str,
    required_as_of: date | str,
    knowledge_as_of: date | datetime | str | None = None,
    corrections: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Pure eligibility decision; UNKNOWN means no permission to trade.

    `required_as_of` is the latest completed session whose announcements must be
    included (caller uses the trading calendar). `target_date` is the intended
    entry session, so future-announced periods do not block earlier sessions.
    """
    result: dict[str, Any] = {
        "status": "UNKNOWN", "complete": False, "blocked_tickers": [],
        "target_date": str(target_date), "required_as_of": str(required_as_of),
        "effective_date": report.get("effective_date"), "reasons": [],
        "unsupported_sources": report.get("unsupported_sources", ["attention", "full_delivery", "suspended"]),
        "scope": "disposition_only",
    }
    try:
        target, required = _day(target_date), _day(required_as_of)
        cutoff = _cutoff(knowledge_as_of)
        effective = _day(report.get("effective_date"))
        fetched = _cutoff(report.get("fetched_at")) if report.get("fetched_at") else None
        if required > target:
            raise DispositionContractError("required_as_of is after target_date")
        if report.get("status") != "OK" or report.get("missing_sources"):
            raise DispositionContractError("disposition source status is not complete")
        sources = report.get("source_status", {})
        if any(sources.get(source, {}).get("status") != "OK" for source in SOURCE_NAMES):
            raise DispositionContractError("both TWSE and TPEx must be complete")
        if effective < required or effective > cutoff.date() or effective > target:
            raise DispositionContractError("disposition effective date does not cover the decision")
        if fetched is None or fetched > cutoff or fetched.date() != effective:
            raise DispositionContractError("disposition fetched_at is missing, future, or inconsistent")
        if report.get("query_end_date") and _day(report["query_end_date"]) < required:
            raise DispositionContractError("disposition query end does not cover required session")
        coverage = report.get("disposition_coverage_end")
        if not coverage or _day(coverage) < target:
            raise DispositionContractError("next-session disposition coverage is unverified or incomplete")
        clean = validate_periods(periods)
        count = report.get("source_counts", {}).get("OfficialDisposition")
        if type(count) is not int or count != len(clean):
            raise DispositionContractError("disposition row count does not match health metadata")
        source_counts = [sources[source].get("rows") for source in SOURCE_NAMES]
        if any(type(value) is not int or value < 0 for value in source_counts) or sum(source_counts) != len(clean):
            raise DispositionContractError("disposition market row counts do not match the snapshot")
        if "source" in clean.columns:
            for source in SOURCE_NAMES:
                if int(clean["source"].eq(source).sum()) != sources[source]["rows"]:
                    raise DispositionContractError(f"{source} snapshot rows do not match metadata")
        if clean.empty and not report.get("empty_verified", False):
            raise DispositionContractError("empty disposition history was not explicitly verified")
        resolved = resolve_disposition_periods(clean, knowledge_as_of=cutoff, corrections=corrections)
        active = resolved[(resolved["period_start"] <= target.isoformat()) &
                          (resolved["period_end"] >= target.isoformat())]
        result.update(status="OK", complete=True,
                      blocked_tickers=sorted(active["stock_id"].unique().tolist()),
                      corrections_applied=int(resolved["correction_id"].ne("").sum()),
                      point_in_time_announcements="announcement_date" in clean.columns)
    except (ValueError, OSError, TypeError, KeyError, AttributeError) as exc:
        result["reasons"].append(str(exc))
    return result


def load_disposition_gate(
    base_dir: str | Path,
    *,
    target_date: date | str,
    required_as_of: date | str,
    knowledge_as_of: date | datetime | str | None = None,
) -> dict[str, Any]:
    """Read canonical metadata and full periods; verify generation hash if present."""
    base = Path(base_dir)
    try:
        report = json.loads((base / "ml/reports/special_stock_status_latest.json").read_text(encoding="utf-8-sig"))
        raw = (base / "ml/data/disposition_periods.csv").read_bytes()
        expected = report.get("output_sha256", {}).get("disposition_periods")
        if expected and hashlib.sha256(raw).hexdigest() != expected:
            raise DispositionContractError("disposition CSV generation does not match health metadata")
        from io import BytesIO
        periods = pd.read_csv(BytesIO(raw), dtype=str)
        return evaluate_disposition_snapshot(periods, report, target_date=target_date,
                                             required_as_of=required_as_of, knowledge_as_of=knowledge_as_of)
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        return {"status": "UNKNOWN", "complete": False, "blocked_tickers": [],
                "target_date": str(target_date), "required_as_of": str(required_as_of),
                "reasons": [str(exc)], "scope": "disposition_only",
                "unsupported_sources": ["attention", "full_delivery", "suspended"]}
