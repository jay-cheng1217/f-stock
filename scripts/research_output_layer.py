"""Build PM-readable research output packs from local model artifacts.

This layer deliberately does not change model scores, gates, scheduler timing,
portfolio actions, or databases. It turns the local trading system's outputs into
investment memo artifacts that can later be uploaded to Drive or sent by Gmail.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd

BASE_DIR = Path(os.environ.get("STOCK_BASE_DIR") or Path(__file__).resolve().parents[1])
MODEL_DIR = BASE_DIR / "ml" / "models"
REPORT_DIR = BASE_DIR / "ml" / "reports"
PUBLIC_CONTEXT_DIR = BASE_DIR / "ml" / "data" / "public_market_context"
MY_HOLDINGS_DB = BASE_DIR / "my_holdings.db"

DEFAULT_WATCHLIST_PATHS = (
    Path("config") / "research_watchlist.csv",
    Path("watchlist.csv"),
    Path("data") / "watchlist.csv",
)

TICKER_RE = re.compile(r"(?<!\d)(\d{4})(?!\d)")


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as fh:
            payload = json.load(fh)
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _read_public_rows(dataset_id: str, base_dir: Path = BASE_DIR) -> list[dict[str, Any]]:
    path = base_dir / "ml" / "data" / "public_market_context" / f"{dataset_id}_latest.json"
    payload = _read_json(path)
    rows = payload.get("rows") if isinstance(payload, dict) else []
    rows = rows or []
    return [row for row in rows if isinstance(row, dict)]


def _latest_file(directory: Path, pattern: str) -> Path | None:
    paths = sorted(directory.glob(pattern), key=lambda p: p.stat().st_mtime if p.exists() else 0)
    return paths[-1] if paths else None


def _as_float(value: Any) -> float | None:
    try:
        if value is None or pd.isna(value):
            return None
        return float(value)
    except Exception:
        return None


def _fmt_pct(value: Any, digits: int = 2) -> str:
    number = _as_float(value)
    return "-" if number is None else f"{number:+.{digits}f}%"


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _safe_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _load_signal_detail(unified_latest: dict[str, Any], base_dir: Path = BASE_DIR) -> pd.DataFrame:
    source = str(unified_latest.get("source_signal_path") or "")
    if not source:
        return pd.DataFrame()
    path = Path(source)
    if not path.is_absolute():
        path = base_dir / source
    if not path.exists():
        return pd.DataFrame()
    try:
        df = pd.read_csv(path, encoding="utf-8-sig", dtype={"ticker": str})
    except Exception:
        return pd.DataFrame()
    if "ticker" in df.columns:
        df["ticker"] = df["ticker"].astype(str).str.strip().str.zfill(4)
    return df


def _signal_detail_by_ticker(unified_latest: dict[str, Any], base_dir: Path) -> dict[str, dict[str, Any]]:
    df = _load_signal_detail(unified_latest, base_dir=base_dir)
    if df.empty or "ticker" not in df.columns:
        return {}
    return {
        str(row["ticker"]).zfill(4): row
        for row in df.to_dict("records")
        if str(row.get("ticker") or "").strip()
    }


def _compact_signal_row(row: dict[str, Any], detail: dict[str, Any] | None = None) -> dict[str, Any]:
    detail = detail or {}
    ticker = str(row.get("ticker") or detail.get("ticker") or "").strip().zfill(4)
    return {
        "ticker": ticker,
        "name": row.get("name"),
        "sector": row.get("sector"),
        "signal_type": row.get("signal_type"),
        "rank_20d": row.get("rank_20d"),
        "target_weight_pct": row.get("target_weight_pct"),
        "net_score": row.get("net_score"),
        "rejected_reason": row.get("rejected_reason"),
        "tradability_reason": row.get("tradability_reason") or detail.get("tradability_reason"),
        "selection_model_alignment": detail.get("selection_model_alignment"),
        "selection_model_reason": detail.get("selection_model_reason"),
        "explain_summary": row.get("explain_summary"),
        "explain_drivers": row.get("explain_drivers"),
        "explain_warnings": row.get("explain_warnings"),
    }


def build_daily_investment_memo(base_dir: Path = BASE_DIR) -> dict[str, Any]:
    report_dir = base_dir / "ml" / "reports"
    unified = _read_json(report_dir / "unified_signals_latest.json")
    dashboard = _read_json(report_dir / "dashboard_summary.json")
    market_regime = _read_json(report_dir / "market_regime_latest.json")
    public_quality = _read_json(report_dir / "public_market_context_quality_latest.json")
    detail = _signal_detail_by_ticker(unified, base_dir)

    selected = [
        _compact_signal_row(row, detail.get(str(row.get("ticker") or "").zfill(4)))
        for row in _safe_list(unified.get("selected"))
    ]
    rejected = [
        _compact_signal_row(row, detail.get(str(row.get("ticker") or "").zfill(4)))
        for row in _safe_list(unified.get("rejected"))[:10]
    ]
    champion = dashboard.get("champion") if isinstance(dashboard.get("champion"), dict) else {}
    regime = market_regime if market_regime else dashboard.get("market_regime", {})
    if not isinstance(regime, dict):
        regime = {}

    return {
        "title": "Daily Investment Memo",
        "status": "ok" if unified else "missing_unified_signals",
        "prediction_date": unified.get("prediction_date"),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "selected_count": unified.get("selected_count", len(selected)),
        "rejected_count": unified.get("rejected_count", len(rejected)),
        "market_regime": {
            "state": regime.get("state"),
            "action": regime.get("action"),
            "reason": regime.get("reason"),
        },
        "champion_snapshot": {
            "active_positions": champion.get("active_positions"),
            "invested_weight_pct": champion.get("invested_weight_pct"),
            "mtm_return_pct": champion.get("mtm_return_pct"),
            "mtm_alpha_vs_twii_pct": champion.get("mtm_alpha_vs_twii_pct"),
            "max_drawdown_pct": champion.get("max_drawdown_pct"),
        },
        "public_market_context": {
            "status": public_quality.get("status"),
            "usage_mode": public_quality.get("selection_usage_mode"),
            "model_feature_readiness": public_quality.get("model_feature_readiness"),
            "model_feature_gap": public_quality.get("model_feature_gap"),
        },
        "selected": selected,
        "top_rejected": rejected,
    }


def _event_tickers(*values: Any) -> list[str]:
    found: list[str] = []
    for value in values:
        text = str(value or "")
        for match in TICKER_RE.findall(text):
            if match not in found:
                found.append(match)
    return found


def build_catalyst_tracker(base_dir: Path = BASE_DIR) -> dict[str, Any]:
    events: list[dict[str, Any]] = []

    def add_event(event_type: str, source: str, row: dict[str, Any], title_keys: tuple[str, ...], code_keys: tuple[str, ...]) -> None:
        title = next((str(row.get(key) or "").strip() for key in title_keys if row.get(key)), "")
        codes = [
            str(row.get(key) or "").strip().zfill(4)
            for key in code_keys
            if str(row.get(key) or "").strip().isdigit()
        ]
        tickers = codes or _event_tickers(title, json.dumps(row, ensure_ascii=False))
        events.append(
            {
                "date": row.get("Date") or row.get("date"),
                "ticker": ";".join(tickers),
                "event_type": event_type,
                "source": source,
                "title": title,
                "risk_bias": _event_bias(event_type),
                "actionability": "memo_context_only",
                "raw_keys": ",".join(sorted(row.keys())),
            }
        )

    for row in _read_public_rows("twse_news", base_dir)[:80]:
        add_event("official_news", "TWSE", row, ("Title",), ("Code",))
    for row in _read_public_rows("twse_disposition", base_dir):
        add_event("disposition", "TWSE", row, ("Name", "ReasonsOfDisposition", "DispositionMeasures"), ("Code",))
    for row in _read_public_rows("twse_attention", base_dir):
        if str(row.get("Code") or "").strip():
            add_event("attention", "TWSE", row, ("Name", "TradingInfoForAttention"), ("Code",))
    for row in _read_public_rows("tpex_trading_warning", base_dir):
        add_event("trading_warning", "TPEx", row, ("CompanyName", "TradingInformation"), ("SecuritiesCompanyCode",))
    for row in _read_public_rows("twse_exright_forecast", base_dir)[:80]:
        add_event("ex_right_forecast", "TWSE", row, ("Name", "Exdividend"), ("Code",))
    for row in _read_public_rows("tpex_exright_daily", base_dir):
        add_event("ex_right_daily", "TPEx", row, ("CompanyName", "ExRightsDiviend"), ("SecuritiesCompanyCode",))

    return {
        "title": "Catalyst Tracker",
        "status": "ok",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "event_count": len(events),
        "events": events,
    }


def _event_bias(event_type: str) -> str:
    if event_type in {"disposition", "attention", "trading_warning"}:
        return "risk"
    if event_type.startswith("ex_right"):
        return "return_adjustment"
    return "unknown"


def build_weekly_v2_pm_pack(base_dir: Path = BASE_DIR) -> dict[str, Any]:
    report_dir = base_dir / "ml" / "reports"
    model_dir = base_dir / "ml" / "models"
    backtest_path = _latest_file(report_dir, "v2_backtest_*.csv")
    fold_path = _latest_file(report_dir, "v2_fold_top30_*.csv")
    feature_path = _latest_file(report_dir, "feature_importance_v2_*.csv")
    meta_path = _latest_file(model_dir, "lgbm_v2_*_meta.json")
    public_quality = _read_json(report_dir / "public_market_context_quality_latest.json")

    backtest_summary: dict[str, Any] = {"path": str(backtest_path) if backtest_path else None}
    if backtest_path and backtest_path.exists():
        try:
            df = pd.read_csv(backtest_path)
            backtest_summary.update(
                {
                    "fold_count": int(len(df)),
                    "avg_top30_excess": _as_float(df.get("top30_excess", pd.Series(dtype=float)).mean()),
                    "avg_spread": _as_float(df.get("spread", pd.Series(dtype=float)).mean()),
                    "avg_ic": _as_float(df.get("ic", pd.Series(dtype=float)).mean()),
                    "ic_positive_months": int((pd.to_numeric(df.get("ic"), errors="coerce") > 0).sum()) if "ic" in df else None,
                }
            )
        except Exception as exc:
            backtest_summary["error"] = f"{type(exc).__name__}: {exc}"

    top_features: list[dict[str, Any]] = []
    if feature_path and feature_path.exists():
        try:
            fi = pd.read_csv(feature_path).head(10)
            top_features = fi.to_dict("records")
        except Exception:
            top_features = []

    meta = _read_json(meta_path) if meta_path else {}
    return {
        "title": "Weekly V2 Retrain PM Pack",
        "status": "ok" if backtest_path or meta else "missing_weekly_artifacts",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "model_meta_path": str(meta_path) if meta_path else None,
        "model_version": meta.get("model_version"),
        "trained_at": meta.get("trained_at"),
        "n_features": meta.get("n_features"),
        "n_samples": meta.get("n_samples"),
        "backtest": backtest_summary,
        "fold_top30_path": str(fold_path) if fold_path else None,
        "feature_importance_path": str(feature_path) if feature_path else None,
        "top_features": top_features,
        "public_market_context": {
            "status": public_quality.get("status"),
            "model_feature_readiness": public_quality.get("model_feature_readiness"),
            "model_feature_gap": public_quality.get("model_feature_gap"),
        },
    }


def build_ab_review_pack(base_dir: Path = BASE_DIR) -> dict[str, Any]:
    report_dir = base_dir / "ml" / "reports"
    overlay = _read_json(report_dir / "v2_overlay_decision_latest.json")
    arena = _read_json(report_dir / "agent_arena_backtest_latest.json")
    return {
        "title": "A/B And Backtest Review Pack",
        "status": "ok" if overlay or arena else "missing_ab_artifacts",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "overlay_decision": {
            "status": overlay.get("status"),
            "promotion_ready": overlay.get("promotion_ready"),
            "comparison_note": (overlay.get("audit") or {}).get("comparison_note") if isinstance(overlay.get("audit"), dict) else None,
            "chosen_overlay": (overlay.get("audit") or {}).get("chosen_overlay") if isinstance(overlay.get("audit"), dict) else None,
            "footer": overlay.get("footer"),
        },
        "agent_arena": {
            "status": arena.get("status"),
            "admitted_count": arena.get("admitted_count"),
            "agent_count": arena.get("agent_count"),
            "latest_market_date": arena.get("latest_market_date"),
            "top_admitted": [
                {
                    "agent_id": row.get("agent_id"),
                    "name": row.get("name"),
                    "total_return_pct": row.get("total_return_pct"),
                    "max_drawdown_pct": row.get("max_drawdown_pct"),
                    "win_rate_pct": row.get("win_rate_pct"),
                }
                for row in _safe_list(arena.get("results"))
                if row.get("admission_status") == "ADMITTED"
            ][:8],
        },
    }


def build_watchlist_context(base_dir: Path = BASE_DIR, watchlist_path: str | Path | None = None) -> dict[str, Any]:
    candidates = [Path(watchlist_path)] if watchlist_path else list(DEFAULT_WATCHLIST_PATHS)
    rows: list[dict[str, Any]] = []
    selected_path: Path | None = None
    for path in candidates:
        if not path.is_absolute():
            path = base_dir / path
        if not path.exists():
            continue
        selected_path = path
        try:
            with path.open("r", encoding="utf-8-sig", newline="") as fh:
                reader = csv.DictReader(fh)
                rows = [dict(row) for row in reader]
        except Exception:
            rows = []
        break
    return {
        "title": "Watchlist Context",
        "status": "ok" if selected_path else "missing_watchlist_csv",
        "source_path": str(selected_path) if selected_path else None,
        "expected_columns": ["ticker", "label", "thesis", "risk_note", "owner", "updated_at"],
        "row_count": len(rows),
        "rows": rows[:200],
        "integration_mode": "memo_context_only_not_gate",
    }


def build_holdings_memo(base_dir: Path = BASE_DIR) -> dict[str, Any]:
    db_path = base_dir / "my_holdings.db"
    if not db_path.exists():
        return {
            "title": "Holdings Risk Memo",
            "status": "missing_my_holdings_db",
            "holding_count": 0,
            "tickers": [],
            "privacy": "cost/share fields omitted",
        }
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            table_rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()
            tables = [str(row[0]) for row in table_rows]
            table = "holdings" if "holdings" in tables else ("my_holdings" if "my_holdings" in tables else None)
            if table is None:
                return {
                    "title": "Holdings Risk Memo",
                    "status": "missing_holdings_table",
                    "tables": tables,
                    "privacy": "cost/share fields omitted",
                }
            columns = [str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()]
            ticker_col = "ticker" if "ticker" in columns else ("Ticker" if "Ticker" in columns else None)
            if ticker_col is None:
                return {
                    "title": "Holdings Risk Memo",
                    "status": "missing_ticker_column",
                    "privacy": "cost/share fields omitted",
                }
            tickers = [
                str(row[0]).strip().zfill(4)
                for row in conn.execute(f"SELECT DISTINCT {ticker_col} FROM {table}").fetchall()
                if str(row[0]).strip()
            ]
        finally:
            conn.close()
    except Exception as exc:
        return {
            "title": "Holdings Risk Memo",
            "status": "read_error",
            "error": f"{type(exc).__name__}: {exc}",
            "privacy": "cost/share fields omitted",
        }
    return {
        "title": "Holdings Risk Memo",
        "status": "ok",
        "holding_count": len(tickers),
        "tickers": sorted(tickers),
        "privacy": "cost/share fields omitted",
        "integration_mode": "summary_only_use_req034_for_rules",
    }


def build_google_workspace_handoff(paths: dict[str, str]) -> dict[str, Any]:
    return {
        "title": "Google Workspace Handoff",
        "status": "ready",
        "drive_targets": [
            {"artifact": "daily_memo_md", "suggested_target": "Google Doc"},
            {"artifact": "catalyst_tracker_csv", "suggested_target": "Google Sheet"},
            {"artifact": "weekly_v2_pm_pack_md", "suggested_target": "Google Doc"},
            {"artifact": "ab_review_pack_md", "suggested_target": "Google Doc"},
        ],
        "gmail_draft": {
            "subject": "TW Stock Research Output Pack",
            "body_artifact": paths.get("research_output_layer_md"),
            "attachments": [
                paths.get("research_output_layer_json"),
                paths.get("catalyst_tracker_csv"),
            ],
        },
        "boundary": "Connector usage is manual/interactive; repo stores artifacts only, not Google credentials.",
    }


def build_research_output_pack(
    *,
    base_dir: str | Path = BASE_DIR,
    watchlist_path: str | Path | None = None,
) -> dict[str, Any]:
    root = Path(base_dir)
    daily = build_daily_investment_memo(root)
    catalyst = build_catalyst_tracker(root)
    weekly = build_weekly_v2_pm_pack(root)
    ab = build_ab_review_pack(root)
    watchlist = build_watchlist_context(root, watchlist_path=watchlist_path)
    holdings = build_holdings_memo(root)
    return {
        "title": "Research Output Layer",
        "status": "ok",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "as_of_date": daily.get("prediction_date") or date.today().isoformat(),
        "scope": "memo_and_research_output_only_no_gate_no_model_change",
        "daily_investment_memo": daily,
        "catalyst_tracker": catalyst,
        "weekly_v2_pm_pack": weekly,
        "ab_review_pack": ab,
        "watchlist_context": watchlist,
        "holdings_memo": holdings,
    }


def _md_table(rows: list[dict[str, Any]], columns: list[tuple[str, str]], *, limit: int | None = None) -> str:
    rows = rows[:limit] if limit is not None else rows
    if not rows:
        return "_None._"
    header = "| " + " | ".join(label for _, label in columns) + " |"
    sep = "| " + " | ".join("---" for _ in columns) + " |"
    body = []
    for row in rows:
        values = [str(row.get(key) if row.get(key) is not None else "-").replace("\n", " ") for key, _ in columns]
        body.append("| " + " | ".join(values) + " |")
    return "\n".join([header, sep, *body])


def render_daily_memo_md(daily: dict[str, Any]) -> str:
    champion = daily.get("champion_snapshot") or {}
    regime = daily.get("market_regime") or {}
    public_context = daily.get("public_market_context") or {}
    lines = [
        "# Daily Investment Memo",
        "",
        f"- Prediction date: `{daily.get('prediction_date') or '-'}`",
        f"- Selected / rejected: `{daily.get('selected_count')}` / `{daily.get('rejected_count')}`",
        f"- Market regime: `{regime.get('state') or '-'}` / `{regime.get('action') or '-'}`",
        f"- Champion MTM alpha vs TWII: `{_fmt_pct(champion.get('mtm_alpha_vs_twii_pct'))}`",
        f"- Public context: `{public_context.get('status') or '-'}` / `{public_context.get('model_feature_readiness') or '-'}`",
        "",
        "## Selected",
        _md_table(
            daily.get("selected") or [],
            [
                ("ticker", "Ticker"),
                ("signal_type", "Signal"),
                ("rank_20d", "Rank"),
                ("target_weight_pct", "Weight %"),
                ("selection_model_alignment", "Model alignment"),
                ("explain_summary", "Summary"),
            ],
        ),
        "",
        "## Top Rejected",
        _md_table(
            daily.get("top_rejected") or [],
            [
                ("ticker", "Ticker"),
                ("rejected_reason", "Reason"),
                ("tradability_reason", "Tradability"),
                ("explain_summary", "Summary"),
            ],
            limit=10,
        ),
    ]
    return "\n".join(lines) + "\n"


def render_catalyst_tracker_md(catalyst: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# Catalyst Tracker",
            "",
            f"- Event count: `{catalyst.get('event_count')}`",
            "- Actionability: memo context only; not a production gate.",
            "",
            _md_table(
                catalyst.get("events") or [],
                [
                    ("date", "Date"),
                    ("ticker", "Ticker"),
                    ("event_type", "Type"),
                    ("source", "Source"),
                    ("risk_bias", "Bias"),
                    ("title", "Title"),
                ],
                limit=40,
            ),
        ]
    ) + "\n"


def render_weekly_pm_pack_md(weekly: dict[str, Any]) -> str:
    backtest = weekly.get("backtest") or {}
    return "\n".join(
        [
            "# Weekly V2 Retrain PM Pack",
            "",
            f"- Status: `{weekly.get('status')}`",
            f"- Model version: `{weekly.get('model_version') or '-'}`",
            f"- Trained at: `{weekly.get('trained_at') or '-'}`",
            f"- Samples / features: `{weekly.get('n_samples') or '-'}` / `{weekly.get('n_features') or '-'}`",
            f"- Avg Top30 excess: `{_fmt_pct((_as_float(backtest.get('avg_top30_excess')) or 0) * 100 if backtest.get('avg_top30_excess') is not None else None)}`",
            f"- Avg spread: `{_fmt_pct((_as_float(backtest.get('avg_spread')) or 0) * 100 if backtest.get('avg_spread') is not None else None)}`",
            f"- Avg IC: `{backtest.get('avg_ic') if backtest.get('avg_ic') is not None else '-'}`",
            "",
            "## Top Features",
            _md_table(
                weekly.get("top_features") or [],
                [("feature", "Feature"), ("importance", "Importance")],
                limit=10,
            ),
        ]
    ) + "\n"


def render_ab_review_pack_md(ab: dict[str, Any]) -> str:
    overlay = ab.get("overlay_decision") or {}
    arena = ab.get("agent_arena") or {}
    return "\n".join(
        [
            "# A/B And Backtest Review Pack",
            "",
            f"- Overlay status: `{overlay.get('status') or '-'}`",
            f"- Overlay promotion ready: `{overlay.get('promotion_ready')}`",
            f"- Overlay comparison: `{overlay.get('comparison_note') or '-'}`",
            f"- Agent arena admitted / total: `{arena.get('admitted_count')}` / `{arena.get('agent_count')}`",
            "",
            "## Admitted Agents",
            _md_table(
                arena.get("top_admitted") or [],
                [
                    ("agent_id", "Agent"),
                    ("name", "Name"),
                    ("total_return_pct", "Return %"),
                    ("max_drawdown_pct", "MDD %"),
                    ("win_rate_pct", "Win %"),
                ],
                limit=8,
            ),
        ]
    ) + "\n"


def render_watchlist_md(watchlist: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# Watchlist Context",
            "",
            f"- Status: `{watchlist.get('status')}`",
            f"- Source: `{watchlist.get('source_path') or '-'}`",
            f"- Rows: `{watchlist.get('row_count')}`",
            "- Integration: memo context only; not a gate.",
            "",
            _md_table(watchlist.get("rows") or [], [("ticker", "Ticker"), ("label", "Label"), ("thesis", "Thesis"), ("risk_note", "Risk")], limit=30),
        ]
    ) + "\n"


def render_holdings_md(holdings: dict[str, Any]) -> str:
    tickers = ", ".join(holdings.get("tickers") or [])
    return "\n".join(
        [
            "# Holdings Risk Memo",
            "",
            f"- Status: `{holdings.get('status')}`",
            f"- Holding count: `{holdings.get('holding_count', '-')}`",
            f"- Tickers: `{tickers or '-'}`",
            f"- Privacy: `{holdings.get('privacy')}`",
        ]
    ) + "\n"


def render_research_output_md(pack: dict[str, Any], paths: dict[str, str] | None = None) -> str:
    paths = paths or {}
    daily = pack.get("daily_investment_memo") or {}
    catalyst = pack.get("catalyst_tracker") or {}
    weekly = pack.get("weekly_v2_pm_pack") or {}
    ab = pack.get("ab_review_pack") or {}
    watchlist = pack.get("watchlist_context") or {}
    holdings = pack.get("holdings_memo") or {}
    return "\n".join(
        [
            "# Research Output Layer",
            "",
            f"- Generated at: `{pack.get('generated_at')}`",
            f"- As-of date: `{pack.get('as_of_date')}`",
            f"- Scope: `{pack.get('scope')}`",
            "",
            "## Pack Status",
            f"- Daily memo: `{daily.get('status')}` selected `{daily.get('selected_count')}`",
            f"- Catalyst tracker: `{catalyst.get('event_count')}` events",
            f"- Weekly V2 pack: `{weekly.get('status')}`",
            f"- A/B review: `{ab.get('status')}`",
            f"- Watchlist: `{watchlist.get('status')}` rows `{watchlist.get('row_count')}`",
            f"- Holdings: `{holdings.get('status')}` count `{holdings.get('holding_count', '-')}`",
            "",
            "## Output Artifacts",
            _md_table(
                [{"artifact": key, "path": value} for key, value in sorted(paths.items())],
                [("artifact", "Artifact"), ("path", "Path")],
            ),
            "",
            "## Boundary",
            "This pack is for PM/research review only. It does not change models, gates, portfolios, scheduler timing/triggers, or database state.",
        ]
    ) + "\n"


def write_research_output_artifacts(
    pack: dict[str, Any],
    *,
    report_dir: str | Path = REPORT_DIR,
) -> dict[str, str]:
    target = Path(report_dir)
    target.mkdir(parents=True, exist_ok=True)

    paths = {
        "daily_memo_md": str(target / "research_daily_investment_memo_latest.md"),
        "daily_memo_json": str(target / "research_daily_investment_memo_latest.json"),
        "catalyst_tracker_md": str(target / "research_catalyst_tracker_latest.md"),
        "catalyst_tracker_csv": str(target / "research_catalyst_tracker_latest.csv"),
        "weekly_v2_pm_pack_md": str(target / "research_weekly_v2_pm_pack_latest.md"),
        "weekly_v2_pm_pack_json": str(target / "research_weekly_v2_pm_pack_latest.json"),
        "ab_review_pack_md": str(target / "research_ab_review_pack_latest.md"),
        "ab_review_pack_json": str(target / "research_ab_review_pack_latest.json"),
        "watchlist_context_md": str(target / "research_watchlist_context_latest.md"),
        "watchlist_context_json": str(target / "research_watchlist_context_latest.json"),
        "holdings_memo_md": str(target / "research_holdings_memo_latest.md"),
        "holdings_memo_json": str(target / "research_holdings_memo_latest.json"),
        "research_output_layer_md": str(target / "research_output_layer_latest.md"),
        "research_output_layer_json": str(target / "research_output_layer_latest.json"),
    }

    pack = dict(pack)
    pack["google_workspace_handoff"] = build_google_workspace_handoff(paths)

    daily = pack["daily_investment_memo"]
    catalyst = pack["catalyst_tracker"]
    weekly = pack["weekly_v2_pm_pack"]
    ab = pack["ab_review_pack"]
    watchlist = pack["watchlist_context"]
    holdings = pack["holdings_memo"]

    _write_json(Path(paths["daily_memo_json"]), daily)
    _write_text(Path(paths["daily_memo_md"]), render_daily_memo_md(daily))
    _write_json(Path(paths["weekly_v2_pm_pack_json"]), weekly)
    _write_text(Path(paths["weekly_v2_pm_pack_md"]), render_weekly_pm_pack_md(weekly))
    _write_json(Path(paths["ab_review_pack_json"]), ab)
    _write_text(Path(paths["ab_review_pack_md"]), render_ab_review_pack_md(ab))
    _write_json(Path(paths["watchlist_context_json"]), watchlist)
    _write_text(Path(paths["watchlist_context_md"]), render_watchlist_md(watchlist))
    _write_json(Path(paths["holdings_memo_json"]), holdings)
    _write_text(Path(paths["holdings_memo_md"]), render_holdings_md(holdings))
    _write_json(Path(paths["research_output_layer_json"]), pack)
    _write_text(Path(paths["research_output_layer_md"]), render_research_output_md(pack, paths))

    events = catalyst.get("events") or []
    with Path(paths["catalyst_tracker_csv"]).open("w", encoding="utf-8-sig", newline="") as fh:
        fieldnames = ["date", "ticker", "event_type", "source", "title", "risk_bias", "actionability", "raw_keys"]
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in events:
            writer.writerow({key: row.get(key) for key in fieldnames})
    _write_text(Path(paths["catalyst_tracker_md"]), render_catalyst_tracker_md(catalyst))

    return paths


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Build PM-readable research output artifacts.")
    parser.add_argument("--watchlist-csv", default=None, help="Optional local watchlist CSV path.")
    parser.add_argument("--report-dir", default=str(REPORT_DIR), help="Output report directory.")
    args = parser.parse_args(argv)

    pack = build_research_output_pack(watchlist_path=args.watchlist_csv)
    paths = write_research_output_artifacts(pack, report_dir=args.report_dir)
    print(
        json.dumps(
            {
                "status": pack["status"],
                "as_of_date": pack["as_of_date"],
                "artifact_count": len(paths),
                "research_output_layer_md": paths["research_output_layer_md"],
                "research_output_layer_json": paths["research_output_layer_json"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
