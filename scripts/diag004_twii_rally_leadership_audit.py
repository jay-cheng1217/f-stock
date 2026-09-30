"""DIAG-004 TWII rally leadership coverage audit.

This diagnostic asks whether the April/early-May 2026 TWII rally leaders were
present in the Champion selection chain.  It intentionally does not modify any
production model, gate, or paper book.

Local data caveat:
The repository does not store shares outstanding in stock.duckdb.  Following the
PM fallback, this script uses start-close x trailing 20D average volume as a
capital/impact proxy, then contribution ~= proxy_weight x rally_return.
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ml.config import MODEL_DIR, REPORT_DIR  # noqa: E402
from ml.universe import is_etf_ticker, normalize_ticker  # noqa: E402
from scripts.build_unified_signals import _normalize_guardrail_reason_series  # noqa: E402

DEFAULT_START = "2026-04-01"
DEFAULT_END = "2026-05-08"


@dataclass(frozen=True)
class Paths:
    stock_db: Path
    champion_db: Path
    model_dir: Path
    output_dir: Path


def _safe_float(value: Any) -> float | None:
    try:
        number = float(value)
    except Exception:
        return None
    if not math.isfinite(number):
        return None
    return number


def _fmt_pct(value: Any) -> str:
    number = _safe_float(value)
    if number is None:
        return "n/a"
    return f"{number * 100:.2f}%"


def _fmt_number(value: Any) -> str:
    number = _safe_float(value)
    if number is None:
        return "n/a"
    return f"{number:,.0f}"


def _read_sector_mapping() -> pd.DataFrame:
    path = ROOT / "ml" / "data" / "sector_mapping.csv"
    if not path.exists():
        return pd.DataFrame(columns=["ticker", "name", "sector"])
    df = pd.read_csv(path, dtype={"Ticker": str})
    return df.rename(columns={"Ticker": "ticker", "Name": "name", "Sector": "sector"})[
        ["ticker", "name", "sector"]
    ].assign(ticker=lambda x: x["ticker"].map(normalize_ticker))


def _load_rally_prices(paths: Paths, start: str, end: str) -> pd.DataFrame:
    con = duckdb.connect(str(paths.stock_db), read_only=True)
    try:
        daily = con.execute(
            """
            SELECT Ticker AS ticker, Date AS date, Close AS close, Volume AS volume
            FROM daily_k
            WHERE Date BETWEEN CAST(? AS DATE) - INTERVAL 45 DAY AND CAST(? AS DATE)
            """,
            [start, end],
        ).fetchdf()
    finally:
        con.close()

    if daily.empty:
        raise ValueError("daily_k returned no rows for the requested window")
    daily["ticker"] = daily["ticker"].map(normalize_ticker)
    daily["date"] = pd.to_datetime(daily["date"], errors="coerce")
    daily["close"] = pd.to_numeric(daily["close"], errors="coerce")
    daily["volume"] = pd.to_numeric(daily["volume"], errors="coerce")
    daily = daily.dropna(subset=["ticker", "date", "close"])
    daily = daily[daily["ticker"].str.fullmatch(r"\d{4}")]
    daily = daily[~daily["ticker"].map(is_etf_ticker)]
    daily = daily.sort_values(["ticker", "date"])

    start_ts = pd.Timestamp(start)
    end_ts = pd.Timestamp(end)
    start_rows = (
        daily[daily["date"] <= start_ts]
        .sort_values(["ticker", "date"])
        .groupby("ticker", as_index=False)
        .tail(1)[["ticker", "date", "close"]]
        .rename(columns={"date": "start_price_date", "close": "start_close"})
    )
    end_rows = (
        daily[daily["date"] <= end_ts]
        .sort_values(["ticker", "date"])
        .groupby("ticker", as_index=False)
        .tail(1)[["ticker", "date", "close"]]
        .rename(columns={"date": "end_price_date", "close": "end_close"})
    )
    vol_rows = (
        daily[daily["date"] <= start_ts]
        .sort_values(["ticker", "date"])
        .groupby("ticker")
        .tail(20)
        .groupby("ticker", as_index=False)["volume"]
        .mean()
        .rename(columns={"volume": "avg_volume_20d_at_start"})
    )
    out = start_rows.merge(end_rows, on="ticker", how="inner").merge(vol_rows, on="ticker", how="left")
    out = out.dropna(subset=["start_close", "end_close"])
    out = out[out["start_close"] > 0].copy()
    out["rally_return"] = out["end_close"] / out["start_close"] - 1.0
    out["capital_proxy"] = out["start_close"] * out["avg_volume_20d_at_start"].fillna(0.0)
    total_proxy = float(out["capital_proxy"].sum())
    if total_proxy <= 0:
        raise ValueError("capital proxy is empty; cannot rank TWII contribution")
    out["proxy_weight"] = out["capital_proxy"] / total_proxy
    out["contribution_proxy"] = out["proxy_weight"] * out["rally_return"]
    return out


def _prediction_files(model_dir: Path, start: str, end: str) -> list[Path]:
    start_ts = pd.Timestamp(start)
    end_ts = pd.Timestamp(end)
    files: list[Path] = []
    for path in sorted(model_dir.glob("predictions_2026-*.csv")):
        stem_date = path.stem.replace("predictions_", "").split(".")[0]
        try:
            dt = pd.Timestamp(stem_date)
        except Exception:
            continue
        if start_ts <= dt <= end_ts:
            files.append(path)
    return files


def _unified_signal_files(model_dir: Path, start: str, end: str) -> list[Path]:
    start_ts = pd.Timestamp(start)
    end_ts = pd.Timestamp(end)
    files: list[Path] = []
    for path in sorted(model_dir.glob("unified_signals_2026-*.csv")):
        stem_date = path.stem.replace("unified_signals_", "").split(".")[0]
        try:
            dt = pd.Timestamp(stem_date)
        except Exception:
            continue
        if start_ts <= dt <= end_ts:
            files.append(path)
    return files


def _score_column(df: pd.DataFrame) -> str:
    for col in ["leaderboard_score_before_two_stage", "leaderboard_score", "risk_adjusted_return", "pred_return_20d"]:
        if col in df.columns:
            return col
    raise ValueError("No score column found in predictions file")


def _scan_predictions(files: list[Path], leaders: set[str]) -> tuple[pd.DataFrame, dict[str, Any]]:
    records: list[dict[str, Any]] = []
    meta = {
        "prediction_days": len(files),
        "stage1_top75_source_days": {},
        "two_stage_available_days": 0,
    }
    for path in files:
        date = path.stem.replace("predictions_", "").split(".")[0]
        df = pd.read_csv(path, dtype={"ticker": str})
        if "ticker" not in df.columns:
            continue
        df["ticker"] = df["ticker"].map(normalize_ticker)
        df = df[~df["ticker"].map(is_etf_ticker)].copy()
        score_col = _score_column(df)
        df[score_col] = pd.to_numeric(df[score_col], errors="coerce")
        if "two_stage_candidate" in df.columns:
            top75 = df[df["two_stage_candidate"].astype(str).str.lower().isin({"1", "true"})].copy()
            meta["stage1_top75_source_days"][date] = "two_stage_candidate"
        else:
            top75 = df.dropna(subset=[score_col]).sort_values(score_col, ascending=False).head(75).copy()
            meta["stage1_top75_source_days"][date] = f"top75_by_{score_col}"
        if "two_stage_rank" in df.columns:
            meta["two_stage_available_days"] += 1
        blocked_series = pd.to_numeric(
            df["guardrail_blocked"] if "guardrail_blocked" in df.columns else pd.Series(0, index=df.index),
            errors="coerce",
        ).fillna(0)
        normalized_reason = _normalize_guardrail_reason_series(
            blocked_series,
            df["guardrail_trigger_reason"]
            if "guardrail_trigger_reason" in df.columns
            else pd.Series("", index=df.index),
            df["recommendation"] if "recommendation" in df.columns else pd.Series("", index=df.index),
        )
        for ticker in leaders:
            row = df[df["ticker"].eq(ticker)].head(1)
            top75_row = top75[top75["ticker"].eq(ticker)].head(1)
            in_top75 = not top75_row.empty
            two_stage_rank = None
            if not row.empty and "two_stage_rank" in row.columns:
                two_stage_rank = _safe_float(row.iloc[0].get("two_stage_rank"))
            reason = None
            if not row.empty:
                trigger = normalized_reason.loc[row.index[0]]
                if pd.notna(trigger) and str(trigger).strip():
                    reason = str(trigger)
            records.append(
                {
                    "date": date,
                    "ticker": ticker,
                    "in_prediction_universe": not row.empty,
                    "top75": bool(in_top75),
                    "score_col": score_col,
                    "score": _safe_float(top75_row.iloc[0].get(score_col)) if in_top75 else None,
                    "two_stage_rank": two_stage_rank,
                    "two_stage_top30": two_stage_rank is not None and two_stage_rank <= 30,
                    "prediction_blocked_reason": reason,
                }
            )
    return pd.DataFrame(records), meta


def _scan_unified_signals(files: list[Path], leaders: set[str]) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    for path in files:
        date = path.stem.replace("unified_signals_", "")
        df = pd.read_csv(path, dtype={"ticker": str})
        if "ticker" not in df.columns:
            continue
        df["ticker"] = df["ticker"].map(normalize_ticker)
        for ticker in leaders:
            rows = df[df["ticker"].eq(ticker)]
            if rows.empty:
                continue
            row = rows.iloc[0]
            rank = row.get("two_stage_rank")
            if pd.isna(rank):
                rank = row.get("rank_20d")
            rank_value = _safe_float(rank)
            target_units = _safe_float(row.get("target_units"))
            target_weight = _safe_float(row.get("target_weight_ratio"))
            signal_type = str(row.get("signal_type") or "").strip().upper()
            actionable = (
                target_units is not None
                and target_units > 0
                and target_weight is not None
                and target_weight > 0
                and signal_type not in {"", "NONE", "NAN"}
            )
            reason_parts: list[str] = []
            for col in [
                "guardrail_trigger_reason",
                "guardrail_trigger_reason_20d",
                "tradability_reason",
                "production_gate_reason",
                "t1_block_reason",
            ]:
                if col in row.index and pd.notna(row.get(col)) and str(row.get(col)).strip():
                    reason_parts.append(f"{col}={row.get(col)}")
            records.append(
                {
                    "date": date,
                    "ticker": ticker,
                    "top30_candidate": rank_value is not None and rank_value <= 30,
                    "actionable_signal": bool(actionable),
                    "rank": rank_value,
                    "two_stage_rank": _safe_float(row.get("two_stage_rank")) if "two_stage_rank" in row.index else None,
                    "signal_type": row.get("signal_type"),
                    "target_units": target_units,
                    "target_weight_ratio": target_weight,
                    "signal_reason": "; ".join(reason_parts) if reason_parts else None,
                }
            )
    return pd.DataFrame(records)


def _scan_paper_book(db_path: Path, leaders: set[str], start: str, end: str) -> pd.DataFrame:
    if not db_path.exists():
        return pd.DataFrame(columns=["ticker", "paper_book_positions", "paper_entry_dates", "paper_exit_reasons"])
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    try:
        placeholders = ",".join("?" for _ in leaders)
        rows = con.execute(
            f"""
            SELECT ticker, prediction_date, entry_date, exit_date, status, exit_reason
            FROM unified_positions
            WHERE ticker IN ({placeholders})
              AND (
                prediction_date BETWEEN ? AND ?
                OR entry_date BETWEEN ? AND ?
              )
            ORDER BY ticker, prediction_date
            """,
            [*sorted(leaders), start, end, start, end],
        ).fetchall()
    finally:
        con.close()
    if not rows:
        return pd.DataFrame(columns=["ticker", "paper_book_positions", "paper_entry_dates", "paper_exit_reasons"])
    df = pd.DataFrame([dict(row) for row in rows])
    return (
        df.groupby("ticker", as_index=False)
        .agg(
            paper_book_positions=("ticker", "count"),
            paper_entry_dates=("entry_date", lambda x: ",".join(sorted({str(v) for v in x if pd.notna(v)}))),
            paper_exit_reasons=("exit_reason", lambda x: ",".join(sorted({str(v) for v in x if pd.notna(v)}))),
        )
    )


def _aggregate_reasons(values: pd.Series) -> str:
    clean = [str(v) for v in values if pd.notna(v) and str(v).strip()]
    if not clean:
        return ""
    counts = pd.Series(clean).value_counts()
    return "; ".join(f"{idx} x{count}" for idx, count in counts.items())


def _root_cause(row: pd.Series, prediction_days: int) -> str:
    if not bool(row["in_universe"]):
        return "universe_absent"
    if int(row["top75_days"]) == 0:
        return "stage1_scoring_absent"
    if int(row["top30_days"]) == 0 and int(row["in_signals_days"]) == 0:
        return "top75_not_promoted_to_top30_or_signals"
    if int(row["top30_days"]) > int(row["in_signals_days"]):
        return "post_top30_gate_or_signal_filter"
    if int(row["in_signals_days"]) == 0:
        return "signals_absent"
    if int(row.get("paper_book_positions", 0) or 0) == 0:
        return "signals_not_opened_in_paper_book"
    if int(row["in_signals_days"]) < prediction_days:
        return "intermittent_signals"
    return "captured"


def _build_report(
    *,
    leaders: pd.DataFrame,
    pred_scan: pd.DataFrame,
    pred_meta: dict[str, Any],
    signal_scan: pd.DataFrame,
    paper_scan: pd.DataFrame,
    start: str,
    end: str,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    pred_agg = (
        pred_scan.groupby("ticker", as_index=False)
        .agg(
            in_universe=("in_prediction_universe", "sum"),
            top75_days=("top75", "sum"),
            prediction_two_stage_top30_days=("two_stage_top30", "sum"),
            blocked_reason_pred=("prediction_blocked_reason", _aggregate_reasons),
        )
        if not pred_scan.empty
        else pd.DataFrame(
            columns=[
                "ticker",
                "in_universe",
                "top75_days",
                "prediction_two_stage_top30_days",
                "blocked_reason_pred",
            ]
        )
    )
    signal_agg = (
        signal_scan.assign(
            actionable_date=lambda df: df["date"].where(df["actionable_signal"].astype(bool), pd.NA)
        )
        .groupby("ticker", as_index=False)
        .agg(
            top30_days=("top30_candidate", "sum"),
            in_signals_days=("actionable_signal", "sum"),
            signal_dates=("actionable_date", lambda x: ",".join(sorted({str(v) for v in x if pd.notna(v)}))),
            signal_reason=("signal_reason", _aggregate_reasons),
        )
        if not signal_scan.empty
        else pd.DataFrame(columns=["ticker", "top30_days", "in_signals_days", "signal_dates", "signal_reason"])
    )
    out = leaders.merge(pred_agg, on="ticker", how="left").merge(signal_agg, on="ticker", how="left")
    out = out.merge(paper_scan, on="ticker", how="left")
    for col in [
        "in_universe",
        "top75_days",
        "prediction_two_stage_top30_days",
        "top30_days",
        "in_signals_days",
        "paper_book_positions",
    ]:
        if col not in out.columns:
            out[col] = 0
        out[col] = pd.to_numeric(out[col], errors="coerce").fillna(0).astype(int)
    for col in ["blocked_reason_pred", "signal_reason", "signal_dates", "paper_entry_dates", "paper_exit_reasons"]:
        if col not in out.columns:
            out[col] = ""
        out[col] = out[col].fillna("")
    out["in_universe"] = out["in_universe"] > 0
    out["blocked_reason"] = out.apply(
        lambda row: "; ".join(
            part
            for part in [row.get("blocked_reason_pred", ""), row.get("signal_reason", "")]
            if isinstance(part, str) and part.strip()
        ),
        axis=1,
    )
    out["root_cause_layer"] = out.apply(lambda row: _root_cause(row, pred_meta["prediction_days"]), axis=1)
    out = out[
        [
            "ticker",
            "name",
            "sector",
            "rally_return",
            "contribution_rank",
            "contribution_proxy",
            "proxy_weight",
            "capital_proxy",
            "in_universe",
            "top75_days",
            "top30_days",
            "prediction_two_stage_top30_days",
            "in_signals_days",
            "paper_book_positions",
            "blocked_reason",
            "root_cause_layer",
            "signal_dates",
            "paper_entry_dates",
            "paper_exit_reasons",
        ]
    ].sort_values("contribution_rank")

    root_summary = (
        out.groupby("root_cause_layer", as_index=False)
        .agg(leaders=("ticker", "count"), contribution_proxy_sum=("contribution_proxy", "sum"))
        .sort_values("leaders", ascending=False)
    )
    return out, root_summary


def _write_md(path: Path, *, table: pd.DataFrame, root_summary: pd.DataFrame, metadata: dict[str, Any]) -> None:
    lines = [
        "# DIAG-004 TWII Rally Leadership Coverage Audit",
        "",
        f"- Generated at: `{metadata['generated_at']}`",
        f"- Window: `{metadata['start']}` to `{metadata['end']}`",
        f"- Contribution method: `{metadata['contribution_method']}`",
        f"- Prediction files scanned: `{metadata['prediction_days']}`",
        f"- Unified signal files scanned: `{metadata['signal_days']}`",
        f"- Two-Stage rank available in prediction files: `{metadata['two_stage_available_days']}` days",
        "",
        "## Top 10 Leadership Coverage",
        "",
        "| Rank | Ticker | Name | Sector | 4/1~5/8 return | Proxy contribution | In universe | Top75 days | Top30 days | Signals days | Paper positions | Root cause | Blocked / signal reason |",
        "|---:|---:|---|---|---:|---:|:---:|---:|---:|---:|---:|---|---|",
    ]
    for row in table.to_dict("records"):
        lines.append(
            f"| {int(row['contribution_rank'])} | {row['ticker']} | {row['name']} | {row['sector']} | "
            f"{_fmt_pct(row['rally_return'])} | {_fmt_pct(row['contribution_proxy'])} | "
            f"{'Y' if row['in_universe'] else 'N'} | {row['top75_days']} | {row['top30_days']} | "
            f"{row['in_signals_days']} | {row['paper_book_positions']} | {row['root_cause_layer']} | "
            f"{str(row['blocked_reason'])[:220]} |"
        )

    lines.extend(
        [
            "",
            "## Root-Cause Summary",
            "",
            "| Layer | Leaders | Proxy contribution sum |",
            "|---|---:|---:|",
        ]
    )
    for row in root_summary.to_dict("records"):
        lines.append(
            f"| {row['root_cause_layer']} | {int(row['leaders'])} | {_fmt_pct(row['contribution_proxy_sum'])} |"
        )

    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- `top75_days` uses the saved Two-Stage candidate flag when present; before that column existed, it falls back to the daily prediction score Top75.",
            "- `top30_days` counts `unified_signals` rows with rank <= 30, including rows later marked `signal_type=NONE` by gates.",
            "- `prediction_two_stage_top30_days` is available in the CSV artifact for exact saved Two-Stage ranks; most historical files in this window predate that column.",
            "- `in_signals_days` means actionable final output only: `target_units > 0`, positive target weight, and `signal_type != NONE`.",
            "- Contribution is a liquidity/impact proxy, not exact TWII market-cap contribution, because shares outstanding is not stored locally.",
            "",
            "## Data Artifacts",
            "",
            f"- CSV: `{metadata['table_csv']}`",
            f"- Root summary CSV: `{metadata['root_summary_csv']}`",
            f"- JSON: `{metadata['json']}`",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run(args: argparse.Namespace) -> dict[str, Any]:
    paths = Paths(
        stock_db=Path(args.stock_db),
        champion_db=Path(args.champion_db),
        model_dir=Path(args.model_dir),
        output_dir=Path(args.output_dir),
    )
    paths.output_dir.mkdir(parents=True, exist_ok=True)

    rally = _load_rally_prices(paths, args.start, args.end)
    mapping = _read_sector_mapping()
    leaders = (
        rally.sort_values("contribution_proxy", ascending=False)
        .head(args.top_n)
        .merge(mapping, on="ticker", how="left")
    )
    leaders["name"] = leaders["name"].fillna("")
    leaders["sector"] = leaders["sector"].fillna("")
    leaders["contribution_rank"] = range(1, len(leaders) + 1)
    leader_set = set(leaders["ticker"])

    prediction_files = _prediction_files(paths.model_dir, args.start, args.end)
    unified_files = _unified_signal_files(paths.model_dir, args.start, args.end)
    pred_scan, pred_meta = _scan_predictions(prediction_files, leader_set)
    signal_scan = _scan_unified_signals(unified_files, leader_set)
    paper_scan = _scan_paper_book(paths.champion_db, leader_set, args.start, args.end)
    table, root_summary = _build_report(
        leaders=leaders,
        pred_scan=pred_scan,
        pred_meta=pred_meta,
        signal_scan=signal_scan,
        paper_scan=paper_scan,
        start=args.start,
        end=args.end,
    )

    prefix = args.output_prefix
    table_csv = paths.output_dir / f"{prefix}.csv"
    root_csv = paths.output_dir / f"{prefix}_root_summary.csv"
    json_path = paths.output_dir / f"{prefix}.json"
    md_path = paths.output_dir / f"{prefix}.md"
    table.to_csv(table_csv, index=False, encoding="utf-8-sig")
    root_summary.to_csv(root_csv, index=False, encoding="utf-8-sig")
    metadata = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "start": args.start,
        "end": args.end,
        "top_n": args.top_n,
        "contribution_method": "start_close_x_trailing_20d_avg_volume_proxy_weight_times_close_return",
        "prediction_days": pred_meta["prediction_days"],
        "signal_days": len(unified_files),
        "two_stage_available_days": pred_meta["two_stage_available_days"],
        "stage1_top75_source_days": pred_meta["stage1_top75_source_days"],
        "table_csv": str(table_csv),
        "root_summary_csv": str(root_csv),
        "json": str(json_path),
        "md": str(md_path),
    }
    payload = {
        "metadata": metadata,
        "leaders": table.to_dict("records"),
        "root_summary": root_summary.to_dict("records"),
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    _write_md(md_path, table=table, root_summary=root_summary, metadata=metadata)
    print(table.to_string(index=False))
    print(f"[diag004] wrote {md_path}")
    return payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run DIAG-004 TWII rally leadership coverage audit.")
    parser.add_argument("--start", default=DEFAULT_START)
    parser.add_argument("--end", default=DEFAULT_END)
    parser.add_argument("--top-n", type=int, default=10)
    parser.add_argument("--stock-db", default=str(ROOT / "stock.duckdb"))
    parser.add_argument("--champion-db", default=str(ROOT / "paper_portfolio_v2_champion.db"))
    parser.add_argument("--model-dir", default=MODEL_DIR)
    parser.add_argument("--output-dir", default=REPORT_DIR)
    parser.add_argument("--output-prefix", default="diag004_twii_rally_leadership_20260509")
    return parser.parse_args()


if __name__ == "__main__":
    run(parse_args())
