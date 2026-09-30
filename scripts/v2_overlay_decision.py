"""V2 exit overlay promotion decision report.

This compares the research overlay watch outputs with the V2-family champion
execution record. Per the validity audit, this is a strategy comparison rather
than a strict exit-only A/B against the champion DB positions.
"""
from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
import traceback
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))

from ml.config import REPORT_DIR  # noqa: E402

STATUS_PASS = "PASS"
STATUS_FAIL = "FAIL"
STATUS_INSUFFICIENT = "INSUFFICIENT_SAMPLE"
STATUS_CHAMPION_INSUFFICIENT = "CHAMPION_INSUFFICIENT"
STATUS_MISSING_INPUTS = "MISSING_INPUTS"

OVERLAY_VERSION = "v0.1"
WORDING_MODE = "strategy_comparison"
ENTRY_EQUIVALENT = "partial"
VERSION_EQUIVALENT = "no"
MDD_TOLERANCE_PP = 0.5
RETURN_THRESHOLD_PP = 1.0
WIN_RATE_THRESHOLD_PP = 5.0
MIN_COMPLETED_TRADES = 15
MIN_CHAMPION_MARK_DATES_20D = 15
MIN_CHAMPION_MARK_DATES_60D = 30

REPORT_DIR_PATH = Path(REPORT_DIR)
DEFAULT_PREFIX = "v2_exit_overlay_watch_latest"
DEFAULT_CHAMPION_DB = BASE_DIR / "paper_portfolio_v2_champion.db"
DEFAULT_ARCHIVE_DIR = REPORT_DIR_PATH / "archive" / "v2_overlay_decision"
LOG_PATH = BASE_DIR / "logs" / "v2_overlay_decision.log"

FOOTER_TEMPLATE = (
    "Conclusion: {status} for V2-family overlay strategy comparison "
    "(overlay watch replays sector-capped predictions; champion is unified "
    "pipeline execution) under current overlay rules {version}. "
    "This does not validate the correctness of the overlay rules themselves."
)


@dataclass(frozen=True)
class DecisionPaths:
    report_dir: Path = REPORT_DIR_PATH
    prefix: str = DEFAULT_PREFIX
    champion_db: Path = DEFAULT_CHAMPION_DB
    archive_dir: Path = DEFAULT_ARCHIVE_DIR
    latest_json: Path = REPORT_DIR_PATH / "v2_overlay_decision_latest.json"
    latest_md: Path = REPORT_DIR_PATH / "v2_overlay_decision_latest.md"

    @property
    def shadow_json(self) -> Path:
        return self.report_dir / f"{self.prefix}.json"

    @property
    def shadow_summary(self) -> Path:
        return self.report_dir / f"{self.prefix}_summary.csv"

    @property
    def shadow_equity(self) -> Path:
        return self.report_dir / f"{self.prefix}_equity_curve.csv"

    @property
    def shadow_trades(self) -> Path:
        return self.report_dir / f"{self.prefix}_trades.csv"


def _setup_logging() -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        logging.basicConfig(
            filename=LOG_PATH,
            level=logging.INFO,
            format="%(asctime)s | %(levelname)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
            force=True,
        )
    except PermissionError:
        # Windows locks the file when the batch file redirects stdout/stderr to
        # the same path; fall back to stderr so the batch redirection still logs.
        logging.basicConfig(
            stream=sys.stderr,
            level=logging.INFO,
            format="%(asctime)s | %(levelname)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
            force=True,
        )


def _as_pct_points(value: float | None) -> float | None:
    if value is None:
        return None
    return round(float(value) * 100.0, 6)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if pd.isna(value):
            return default
        return float(value)
    except Exception:
        return default


def _window_dates(dates: pd.Series, as_of: str, days: int) -> list[str]:
    parsed = pd.to_datetime(dates, errors="coerce").dropna().dt.date
    eligible = sorted({d.isoformat() for d in parsed if d.isoformat() <= as_of})
    return eligible[-days:]


def _compound_return(returns: pd.Series) -> float:
    if returns.empty:
        return 0.0
    return float((1.0 + returns.astype(float)).prod() - 1.0)


def _mdd_from_returns(returns: pd.Series) -> float:
    if returns.empty:
        return 0.0
    equity = (1.0 + returns.astype(float)).cumprod()
    drawdown = equity / equity.cummax() - 1.0
    return abs(float(drawdown.min()))


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _missing_inputs(paths: DecisionPaths) -> list[str]:
    required = [
        paths.shadow_json,
        paths.shadow_summary,
        paths.shadow_equity,
        paths.shadow_trades,
        paths.champion_db,
    ]
    return [str(path) for path in required if not path.exists()]


def _resolve_as_of(paths: DecisionPaths, asof: str | None) -> str | None:
    if asof:
        return asof
    data = _load_json(paths.shadow_json)
    if data.get("as_of"):
        return str(data["as_of"])
    metadata = data.get("metadata") or {}
    return metadata.get("prediction_end") or metadata.get("as_of")


def _freshness_warning(path: Path, as_of: str, today: date | None = None) -> tuple[str | None, bool]:
    today = today or date.today()
    if not path.exists():
        return None, False
    mtime_date = datetime.fromtimestamp(path.stat().st_mtime).date()
    age_days = (today - mtime_date).days
    if age_days > 7:
        return f"watch report is stale: mtime {mtime_date.isoformat()} is {age_days} days old", True
    if age_days > 3:
        return f"watch report is stale: mtime {mtime_date.isoformat()} is {age_days} days old", False
    return None, False


def _choose_shadow_overlay(summary: pd.DataFrame) -> str:
    if summary.empty or "overlay" not in summary.columns:
        raise ValueError("shadow summary has no overlay rows")
    score_col = "calmar_proxy" if "calmar_proxy" in summary.columns else "basket_cum_return"
    ranked = summary.copy()
    non_baseline = ranked[ranked["overlay"].astype(str) != "fixed20"].copy()
    if not non_baseline.empty:
        ranked = non_baseline
    ranked[score_col] = pd.to_numeric(ranked[score_col], errors="coerce")
    ranked = ranked.sort_values(score_col, ascending=False, na_position="last")
    return str(ranked.iloc[0]["overlay"])


def _shadow_metrics(
    *,
    paths: DecisionPaths,
    as_of: str,
    overlay: str,
) -> dict[str, Any]:
    equity = pd.read_csv(paths.shadow_equity, dtype={"prediction_date": str, "overlay": str})
    trades = pd.read_csv(paths.shadow_trades, dtype={"prediction_date": str, "exit_date": str, "overlay": str})
    sub_eq = equity[equity["overlay"] == overlay].copy()
    sub_trades = trades[trades["overlay"] == overlay].copy()
    if sub_eq.empty:
        raise ValueError(f"shadow equity has no rows for overlay {overlay}")

    dates_20 = _window_dates(sub_eq["prediction_date"], as_of, 20)
    dates_60 = _window_dates(sub_eq["prediction_date"], as_of, 60)
    eq20 = sub_eq[sub_eq["prediction_date"].isin(dates_20)].copy()
    eq60 = sub_eq[sub_eq["prediction_date"].isin(dates_60)].copy()
    eq20["basket_return"] = pd.to_numeric(eq20["basket_return"], errors="coerce").fillna(0.0)
    eq60["basket_return"] = pd.to_numeric(eq60["basket_return"], errors="coerce").fillna(0.0)

    sub_trades["exit_date"] = pd.to_datetime(sub_trades["exit_date"], errors="coerce").dt.date.astype("string")
    sub_trades["net_return"] = pd.to_numeric(sub_trades.get("net_return"), errors="coerce")
    completed = sub_trades[
        (sub_trades["exit_reason"].astype(str) != "open_mtm")
        & sub_trades["exit_date"].isin(dates_20)
    ].copy()
    win_rate = float((completed["net_return"] > 0).mean()) if not completed.empty else 0.0
    mae = pd.to_numeric(completed.get("mae"), errors="coerce").dropna()

    return {
        "return_20d": _compound_return(eq20["basket_return"]),
        "mdd_20d": _mdd_from_returns(eq20["basket_return"]),
        "mdd_60d": _mdd_from_returns(eq60["basket_return"]),
        "win_rate_20d": win_rate,
        "completed_trades_20d": int(len(completed)),
        "per_trade_mdd_avg": abs(float(mae.mean())) if not mae.empty else None,
        "per_trade_mdd_median": abs(float(mae.median())) if not mae.empty else None,
        "window_dates_20d": dates_20,
        "window_dates_60d": dates_60,
    }


def _load_champion_marks(db_path: Path) -> pd.DataFrame:
    query = """
        select
            m.mark_date,
            m.position_id,
            m.close_return_pct,
            m.intraday_drawdown_pct,
            m.is_exit_day,
            p.entry_date,
            p.exit_date,
            p.target_weight,
            p.realized_return_pct,
            p.max_drawdown_pct
        from unified_marks m
        join unified_positions p on p.id = m.position_id
    """
    with sqlite3.connect(db_path) as con:
        return pd.read_sql_query(query, con)


def _champion_metrics(
    *,
    paths: DecisionPaths,
    as_of: str,
    shadow_dates_20d: list[str] | None = None,
    shadow_dates_60d: list[str] | None = None,
) -> dict[str, Any]:
    marks = _load_champion_marks(paths.champion_db)
    if marks.empty:
        return {"insufficient": True, "reason": "no champion marks"}

    marks["mark_date"] = pd.to_datetime(marks["mark_date"], errors="coerce").dt.date.astype("string")
    marks = marks[marks["mark_date"].notna() & (marks["mark_date"] <= as_of)].copy()
    if marks.empty:
        return {"insufficient": True, "reason": f"no champion marks <= {as_of}"}

    marks["close_return_pct"] = pd.to_numeric(marks["close_return_pct"], errors="coerce").fillna(0.0)
    marks["target_weight"] = pd.to_numeric(marks["target_weight"], errors="coerce").fillna(0.0)
    dates_20 = list(shadow_dates_20d or _window_dates(marks["mark_date"], as_of, 20))
    dates_60 = list(shadow_dates_60d or _window_dates(marks["mark_date"], as_of, 60))

    def daily_equal(df: pd.DataFrame) -> pd.Series:
        return df.groupby("mark_date")["close_return_pct"].mean().sort_index()

    def daily_target(df: pd.DataFrame) -> pd.Series:
        return (
            df.assign(weighted=df["close_return_pct"] * df["target_weight"])
            .groupby("mark_date")["weighted"]
            .sum()
            .sort_index()
        )

    m20 = marks[marks["mark_date"].isin(dates_20)].copy()
    m60 = marks[marks["mark_date"].isin(dates_60)].copy()
    champion_mark_dates_20d = int(m20["mark_date"].nunique())
    champion_mark_dates_60d = int(m60["mark_date"].nunique())
    ret20_equal = daily_equal(m20)
    ret60_equal = daily_equal(m60)
    ret20_target = daily_target(m20)

    pos20 = marks[
        marks["exit_date"].notna()
        & (marks["exit_date"] != "")
        & marks["exit_date"].isin(dates_20)
    ].drop_duplicates("position_id")
    realized = pd.to_numeric(pos20.get("realized_return_pct"), errors="coerce").dropna()
    mdd_col = pd.to_numeric(pos20.get("max_drawdown_pct"), errors="coerce").dropna()
    intraday = pd.to_numeric(m20.get("intraday_drawdown_pct"), errors="coerce").dropna()

    return {
        "insufficient": champion_mark_dates_20d < MIN_CHAMPION_MARK_DATES_20D,
        "reason": (
            f"champion mark dates in shadow 20d window {champion_mark_dates_20d}"
            f" < {MIN_CHAMPION_MARK_DATES_20D}"
            if champion_mark_dates_20d < MIN_CHAMPION_MARK_DATES_20D
            else None
        ),
        "return_20d": _compound_return(ret20_equal),
        "mdd_20d": _mdd_from_returns(ret20_equal),
        "mdd_60d": _mdd_from_returns(ret60_equal),
        "mdd_60d_available": champion_mark_dates_60d >= MIN_CHAMPION_MARK_DATES_60D,
        "champion_mark_dates_20d": champion_mark_dates_20d,
        "champion_mark_dates_60d": champion_mark_dates_60d,
        "return_20d_target_weight": _compound_return(ret20_target),
        "win_rate_20d": float((realized > 0).mean()) if not realized.empty else 0.0,
        "per_trade_mdd_avg": abs(float(mdd_col.mean())) if not mdd_col.empty else None,
        "per_trade_mdd_median": abs(float(mdd_col.median())) if not mdd_col.empty else None,
        "intraday_mdd_avg": abs(float(intraday.mean())) if not intraday.empty else None,
        "window_dates_20d": dates_20,
        "window_dates_60d": dates_60,
    }


def _status_from_rules(
    shadow: dict[str, Any],
    champion: dict[str, Any],
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    completed = int(shadow["completed_trades_20d"])
    necessary = {
        "completed_trades_20d": {
            "value": completed,
            "threshold": MIN_COMPLETED_TRADES,
            "pass": completed >= MIN_COMPLETED_TRADES,
        },
        "mdd_20d_within_tolerance": {
            "shadow": _as_pct_points(shadow["mdd_20d"]),
            "champion": _as_pct_points(champion["mdd_20d"]),
            "tolerance_pp": MDD_TOLERANCE_PP,
            "pass": _as_pct_points(shadow["mdd_20d"]) <= _as_pct_points(champion["mdd_20d"]) + MDD_TOLERANCE_PP,
        },
    }
    if not necessary["completed_trades_20d"]["pass"]:
        return STATUS_INSUFFICIENT, necessary, {}

    return_delta = _as_pct_points(shadow["return_20d"] - champion["return_20d"])
    win_delta = _as_pct_points(shadow["win_rate_20d"] - champion["win_rate_20d"])
    mdd_60_available = bool(champion.get("mdd_60d_available", True))
    sufficient = {
        "return_20d": {
            "shadow": float(shadow["return_20d"]),
            "champion": float(champion["return_20d"]),
            "delta_pp": return_delta,
            "threshold_pp": RETURN_THRESHOLD_PP,
            "pass": return_delta >= RETURN_THRESHOLD_PP,
        },
        "win_rate_20d": {
            "shadow": float(shadow["win_rate_20d"]),
            "champion": float(champion["win_rate_20d"]),
            "delta_pp": win_delta,
            "threshold_pp": WIN_RATE_THRESHOLD_PP,
            "pass": win_delta >= WIN_RATE_THRESHOLD_PP,
        },
        "mdd_60d_within_tolerance": {
            "shadow": _as_pct_points(shadow["mdd_60d"]),
            "champion": _as_pct_points(champion["mdd_60d"]),
            "tolerance_pp": MDD_TOLERANCE_PP,
            "available": mdd_60_available,
            "pass": (
                _as_pct_points(shadow["mdd_60d"]) <= _as_pct_points(champion["mdd_60d"]) + MDD_TOLERANCE_PP
                if mdd_60_available
                else None
            ),
        },
    }

    if not all(item["pass"] for item in necessary.values()):
        return STATUS_FAIL, necessary, sufficient
    available_items = [item for item in sufficient.values() if item.get("pass") is not None]
    passed = sum(1 for item in available_items if item["pass"])
    return (STATUS_PASS if passed >= 2 else STATUS_FAIL), necessary, sufficient


def _previous_archive_statuses(archive_dir: Path, as_of: str) -> list[tuple[str, str]]:
    if not archive_dir.exists():
        return []
    rows = []
    for path in archive_dir.glob("*.json"):
        if path.stem >= as_of:
            continue
        try:
            data = _load_json(path)
        except Exception:
            continue
        rows.append((path.stem, str(data.get("status", ""))))
    return sorted(rows)[-2:]


def _latest_archive_before(archive_dir: Path, before_date: str) -> dict[str, Any] | None:
    if not archive_dir.exists():
        return None
    candidates = sorted(path for path in archive_dir.glob("*.json") if path.stem < before_date)
    for path in reversed(candidates):
        try:
            return _load_json(path)
        except Exception:
            continue
    return None


def _promotion_ready(status: str, archive_dir: Path, as_of: str) -> bool:
    if status != STATUS_PASS:
        return False
    statuses = [s for _, s in _previous_archive_statuses(archive_dir, as_of)] + [status]
    return len(statuses) >= 3 and statuses[-3:] == [STATUS_PASS, STATUS_PASS, STATUS_PASS]


def _pass_count(items: dict[str, Any]) -> tuple[int, int]:
    available = [item for item in (items or {}).values() if item.get("pass") is not None]
    return sum(1 for item in available if item.get("pass")), len(available)


def _build_missing_result(as_of: str | None, missing: list[str]) -> dict[str, Any]:
    status = STATUS_MISSING_INPUTS
    footer = FOOTER_TEMPLATE.format(status=status, version=OVERLAY_VERSION)
    return {
        "as_of": as_of,
        "overlay_version": OVERLAY_VERSION,
        "status": status,
        "promotion_ready": False,
        "validity": {
            "entry_equivalent": ENTRY_EQUIVALENT,
            "version_equivalent": VERSION_EQUIVALENT,
            "wording_mode": WORDING_MODE,
        },
        "necessary": {},
        "sufficient": {},
        "audit": {"missing_inputs": missing},
        "footer": footer,
    }


def build_decision(
    *,
    paths: DecisionPaths,
    asof: str | None = None,
    today: date | None = None,
) -> dict[str, Any]:
    missing = _missing_inputs(paths)
    if missing:
        return _build_missing_result(asof, missing)

    as_of = _resolve_as_of(paths, asof)
    if not as_of:
        return _build_missing_result(None, ["could not resolve as_of"])

    freshness_warning, abort_stale = _freshness_warning(paths.shadow_json, as_of, today=today)
    if abort_stale:
        return _build_missing_result(as_of, [freshness_warning or "stale watch report"])

    summary = pd.read_csv(paths.shadow_summary, dtype={"overlay": str})
    overlay = _choose_shadow_overlay(summary)
    shadow = _shadow_metrics(paths=paths, as_of=as_of, overlay=overlay)
    champion = _champion_metrics(
        paths=paths,
        as_of=as_of,
        shadow_dates_20d=shadow.get("window_dates_20d", []),
        shadow_dates_60d=shadow.get("window_dates_60d", []),
    )
    if champion.get("insufficient"):
        status = STATUS_CHAMPION_INSUFFICIENT
        necessary: dict[str, Any] = {}
        sufficient: dict[str, Any] = {}
    else:
        status, necessary, sufficient = _status_from_rules(shadow, champion)

    promotion_ready = _promotion_ready(status, paths.archive_dir, as_of)
    champion_equal = _as_pct_points(champion.get("return_20d")) if not champion.get("insufficient") else None
    champion_target = _as_pct_points(champion.get("return_20d_target_weight")) if not champion.get("insufficient") else None
    footer = FOOTER_TEMPLATE.format(status=status, version=OVERLAY_VERSION)
    return {
        "as_of": as_of,
        "overlay_version": OVERLAY_VERSION,
        "status": status,
        "promotion_ready": promotion_ready,
        "validity": {
            "entry_equivalent": ENTRY_EQUIVALENT,
            "version_equivalent": VERSION_EQUIVALENT,
            "wording_mode": WORDING_MODE,
        },
        "necessary": necessary,
        "sufficient": sufficient,
        "audit": {
            "chosen_overlay": overlay,
            "comparison_note": "strategy_comparison; not strict exit-only A/B",
            "per_trade_mdd_avg_shadow": _as_pct_points(shadow.get("per_trade_mdd_avg")),
            "per_trade_mdd_avg_champion": (
                _as_pct_points(champion.get("per_trade_mdd_avg")) if not champion.get("insufficient") else None
            ),
            "champion_intraday_mdd_avg": (
                _as_pct_points(champion.get("intraday_mdd_avg")) if not champion.get("insufficient") else None
            ),
            "champion_20d_return_equal_weight": champion_equal,
            "champion_20d_return_target_weight": champion_target,
            "weighting_divergence_pp": (
                round(abs(champion_equal - champion_target), 6)
                if champion_equal is not None and champion_target is not None
                else None
            ),
            "freshness_warning": freshness_warning,
            "champion_insufficient_reason": champion.get("reason") if champion.get("insufficient") else None,
            "champion_mark_dates_20d": champion.get("champion_mark_dates_20d"),
            "champion_mark_dates_60d": champion.get("champion_mark_dates_60d"),
            "champion_20d_mdd_partial": _as_pct_points(champion.get("mdd_20d")) if champion else None,
            "shadow_20d_mdd": _as_pct_points(shadow.get("mdd_20d")),
            "shadow_window_dates_20d": shadow.get("window_dates_20d", []),
            "champion_window_dates_20d": champion.get("window_dates_20d", []),
        },
        "footer": footer,
    }


def _write_outputs(result: dict[str, Any], paths: DecisionPaths) -> None:
    paths.report_dir.mkdir(parents=True, exist_ok=True)
    paths.archive_dir.mkdir(parents=True, exist_ok=True)
    as_of = result.get("as_of") or date.today().isoformat()
    archive_json = paths.archive_dir / f"{as_of}.json"
    archive_md = paths.archive_dir / f"{as_of}.md"
    text = json.dumps(result, ensure_ascii=False, indent=2)
    paths.latest_json.write_text(text + "\n", encoding="utf-8")
    archive_json.write_text(text + "\n", encoding="utf-8")
    md = _render_markdown(result)
    paths.latest_md.write_text(md, encoding="utf-8")
    archive_md.write_text(md, encoding="utf-8")


def _render_markdown(result: dict[str, Any]) -> str:
    audit = result.get("audit", {})
    lines = [
        "# V2 Overlay Decision",
        "",
        f"- As of: `{result.get('as_of')}`",
        f"- Status: `{result.get('status')}`",
        f"- Promotion ready: `{result.get('promotion_ready')}`",
        f"- Wording mode: `{result.get('validity', {}).get('wording_mode')}`",
        f"- Chosen overlay: `{audit.get('chosen_overlay', '-')}`",
        f"- Freshness warning: `{audit.get('freshness_warning') or '-'}`",
        "",
        "## Necessary",
        "",
    ]
    for key, value in (result.get("necessary") or {}).items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Sufficient", ""])
    for key, value in (result.get("sufficient") or {}).items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Audit", ""])
    for key, value in audit.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", result["footer"], ""])
    return "\n".join(lines)


def _ntfy_tags(result: dict[str, Any]) -> str:
    if result.get("promotion_ready"):
        return "rocket"
    if result.get("status") == STATUS_PASS:
        return "chart_with_upwards_trend"
    return "warning"


def _ntfy_message(result: dict[str, Any]) -> str:
    necessary_pass, necessary_total = _pass_count(result.get("necessary") or {})
    sufficient_pass, sufficient_total = _pass_count(result.get("sufficient") or {})
    audit = result.get("audit") or {}
    shadow_mdd = audit.get("shadow_20d_mdd")
    champion_mdd = audit.get("champion_20d_mdd_partial")
    if champion_mdd is None:
        mdd_check = (result.get("necessary") or {}).get("mdd_20d_within_tolerance") or {}
        champion_mdd = mdd_check.get("champion")
    return "\n".join([
        f"status: {result.get('status')}",
        f"chosen_overlay: {audit.get('chosen_overlay') or '-'}",
        f"shadow_20d_mdd:   {shadow_mdd if shadow_mdd is not None else '-'}  champion_20d_mdd: {champion_mdd if champion_mdd is not None else '-'}",
        f"necessary: {necessary_pass}/{necessary_total} passed   sufficient: {sufficient_pass}/{sufficient_total} passed",
        "",
        result["footer"],
    ])


def _maybe_send_ntfy(
    result: dict[str, Any],
    paths: DecisionPaths,
    *,
    dry_run: bool = False,
    no_ntfy: bool = False,
    today: date | None = None,
) -> dict[str, Any]:
    if no_ntfy:
        return {"status": "skipped", "reason": "disabled"}
    reference_date = (today or date.today()).isoformat()
    previous = _latest_archive_before(paths.archive_dir, reference_date)
    previous_status = previous.get("status") if previous else None
    previous_promotion = bool(previous.get("promotion_ready")) if previous else False
    status = result.get("status")
    promotion = bool(result.get("promotion_ready"))
    should_send = previous is None or previous_status != status
    high_priority = promotion and not previous_promotion
    if high_priority:
        should_send = True
    if not should_send:
        return {"status": "skipped", "reason": "status unchanged"}
    if dry_run:
        return {"status": "dry_run", "reason": "would send ntfy"}

    from scripts.notify_ntfy import send_ntfy

    priority = "high" if high_priority else "default"
    title = f"[stock] V2 Overlay Decision: {status}"
    message = _ntfy_message(result)
    return send_ntfy(title=title, message=message, priority=priority, tags=_ntfy_tags(result))


def _send_crash_ntfy(exc: BaseException, *, dry_run: bool = False, no_ntfy: bool = False) -> dict[str, Any]:
    if no_ntfy:
        return {"status": "skipped", "reason": "disabled"}
    summary = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__, limit=6))
    message = f"v2_overlay_decision crashed:\n\n{summary}"
    if dry_run:
        return {"status": "dry_run", "reason": "would send crash ntfy"}
    from scripts.notify_ntfy import send_ntfy

    return send_ntfy(
        title="[stock] v2_overlay_decision CRASHED",
        message=message,
        priority="high",
        tags="warning",
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build V2 overlay promotion decision report.")
    parser.add_argument("--asof", default=None, help="Decision as-of date YYYY-MM-DD.")
    parser.add_argument("--report-dir", default=str(REPORT_DIR_PATH))
    parser.add_argument("--prefix", default=DEFAULT_PREFIX)
    parser.add_argument("--champion-db", default=str(DEFAULT_CHAMPION_DB))
    parser.add_argument("--archive-dir", default=str(DEFAULT_ARCHIVE_DIR))
    parser.add_argument("--no-ntfy", action="store_true")
    parser.add_argument("--dry-run-ntfy", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    _setup_logging()
    args = parse_args(argv)
    paths = DecisionPaths(
        report_dir=Path(args.report_dir),
        prefix=args.prefix,
        champion_db=Path(args.champion_db),
        archive_dir=Path(args.archive_dir),
        latest_json=Path(args.report_dir) / "v2_overlay_decision_latest.json",
        latest_md=Path(args.report_dir) / "v2_overlay_decision_latest.md",
    )
    try:
        result = build_decision(paths=paths, asof=args.asof)
        ntfy_result = _maybe_send_ntfy(
            result,
            paths,
            dry_run=args.dry_run_ntfy,
            no_ntfy=args.no_ntfy,
        )
        _write_outputs(result, paths)
        logging.info("status=%s promotion_ready=%s ntfy=%s", result["status"], result["promotion_ready"], ntfy_result)
        print(result["footer"])
        print(f"Wrote {paths.latest_json}")
        print(f"Wrote {paths.latest_md}")
        return 1 if result["status"] == STATUS_MISSING_INPUTS else 0
    except Exception:
        logging.exception("v2 overlay decision failed")
        try:
            _send_crash_ntfy(sys.exc_info()[1], dry_run=args.dry_run_ntfy, no_ntfy=args.no_ntfy)
        except Exception:
            logging.exception("failed to send crash ntfy")
        raise


if __name__ == "__main__":
    raise SystemExit(main())
