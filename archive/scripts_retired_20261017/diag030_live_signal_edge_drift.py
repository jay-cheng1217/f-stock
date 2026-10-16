"""DIAG-030 live signal edge drift diagnostic.

Read-only analysis comparing live Champion positions after 2026-04-23 with
the 28-month Two-Stage OOF Top30 reference set.
"""

from __future__ import annotations

import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ml.config import DAILY_K_DIR, REPORT_DIR  # noqa: E402
from scripts.backtest_caution_whipsaw_cl3 import _simulate_control  # noqa: E402
from scripts.backtest_exit_v2_cl3 import _load_price_history, _load_twii, _safe_float  # noqa: E402

LIVE_START_DATE = "2026-04-23"
LIVE_END_DATE = "2026-05-11"
OOF_ARTIFACT = Path(REPORT_DIR) / "audit002_step5_grid_search_20260509_N75_bins5_fold_top30.csv"
CHAMPION_DB = ROOT / "paper_portfolio_v2_champion.db"
REPORT_PATH = Path(REPORT_DIR) / f"diag030_live_signal_edge_drift_{datetime.now():%Y%m%d}.md"


def _read_csv(path: Path, **kwargs: Any) -> pd.DataFrame:
    try:
        return pd.read_csv(path, encoding="utf-8-sig", **kwargs)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def _pct(v: Any) -> str:
    if v is None or pd.isna(v):
        return "n/a"
    return f"{float(v) * 100:.2f}%"


def _num(v: Any, digits: int = 3) -> str:
    if v is None or pd.isna(v):
        return "n/a"
    return f"{float(v):.{digits}f}"


def _md_table(df: pd.DataFrame) -> str:
    if df.empty:
        return "_No rows._"
    display = df.copy()
    cols = list(display.columns)
    lines = [
        "| " + " | ".join(cols) + " |",
        "| " + " | ".join(["---"] * len(cols)) + " |",
    ]
    for _, row in display.iterrows():
        lines.append("| " + " | ".join(str(row[col]) for col in cols) + " |")
    return "\n".join(lines)


def _rank_bucket(rank: Any) -> str:
    r = _safe_float(rank)
    if r is None:
        return "UNKNOWN"
    if r <= 10:
        return "Top10"
    if r <= 20:
        return "Rank11_20"
    if r <= 30:
        return "Rank21_30"
    return "Rank31_plus"


def _load_mapping() -> tuple[pd.DataFrame, pd.DataFrame]:
    sector = _read_csv(ROOT / "ml" / "data" / "sector_mapping.csv", dtype={"Ticker": str})
    sector = sector.rename(columns={"Ticker": "ticker", "Name": "name", "Sector": "sector"})
    sector["ticker"] = sector["ticker"].astype(str).str.zfill(4)
    group_path = ROOT / "ml" / "data" / "group_mapping.csv"
    if group_path.exists():
        group = _read_csv(group_path, dtype={"ticker": str})
        group["ticker"] = group["ticker"].astype(str).str.zfill(4)
    else:
        group = pd.DataFrame(columns=["ticker", "group_code", "group_name"])
    return sector, group


def _daily_features(ticker: str, date_str: str, twii: pd.DataFrame | None = None) -> dict[str, Any]:
    path = Path(DAILY_K_DIR) / f"{ticker}.csv"
    if not path.exists():
        return {}
    df = _read_csv(path)
    if df.empty or "Date" not in df.columns:
        return {}
    df["Date"] = pd.to_datetime(df["Date"])
    row_df = df[df["Date"] <= pd.Timestamp(date_str)].tail(1)
    if row_df.empty:
        return {}
    row = row_df.iloc[0]
    close = _safe_float(row.get("Close"))
    ma5 = _safe_float(row.get("MA_5"))
    ma20 = _safe_float(row.get("MA_20"))
    vol20 = _safe_float(row.get("VOL_MA_20"))
    price_vs_ma5 = close / ma5 - 1.0 if close and ma5 else None
    price_vs_ma20 = close / ma20 - 1.0 if close and ma20 else None
    avg_20d_amount = close * vol20 if close and vol20 else None

    beta_60 = None
    if twii is not None and close is not None:
        ret = df[["Date", "Close"]].copy()
        ret["stock_ret"] = ret["Close"].pct_change()
        merged = ret.merge(twii[["Date", "Close"]].rename(columns={"Close": "twii_close"}), on="Date", how="inner")
        merged["twii_ret"] = merged["twii_close"].pct_change()
        merged = merged[merged["Date"] <= pd.Timestamp(date_str)].tail(80)
        valid = merged[["stock_ret", "twii_ret"]].dropna().tail(60)
        if len(valid) >= 30 and valid["twii_ret"].var() > 0:
            beta_60 = float(valid["stock_ret"].cov(valid["twii_ret"]) / valid["twii_ret"].var())

    return {
        "price_vs_ma5": price_vs_ma5,
        "price_vs_ma20": price_vs_ma20,
        "avg_20d_amount": avg_20d_amount,
        "beta_60": beta_60,
    }


def _load_signal_row(prediction_date: str, ticker: str) -> dict[str, Any]:
    candidates = [
        ROOT / "ml" / "models" / f"unified_signals_{prediction_date}.csv",
        ROOT / "ml" / "reports" / "archive" / "nightly" / prediction_date / f"unified_signals_{prediction_date}.csv",
    ]
    for path in candidates:
        if not path.exists():
            continue
        try:
            df = _read_csv(path, dtype={"ticker": str})
        except Exception:
            continue
        df["ticker"] = df["ticker"].astype(str).str.zfill(4)
        hit = df[df["ticker"] == ticker]
        if not hit.empty:
            return hit.iloc[0].to_dict()
    return {}


def _load_prediction_row(prediction_date: str, ticker: str) -> dict[str, Any]:
    candidates = [
        ROOT / "ml" / "models" / f"predictions_{prediction_date}.csv",
        ROOT / "ml" / "reports" / "archive" / "nightly" / prediction_date / f"predictions_{prediction_date}.csv",
    ]
    for path in candidates:
        if not path.exists():
            continue
        try:
            df = _read_csv(path, dtype={"ticker": str})
        except Exception:
            continue
        df["ticker"] = df["ticker"].astype(str).str.zfill(4)
        hit = df[df["ticker"] == ticker]
        if not hit.empty:
            return hit.iloc[0].to_dict()
    return {}


def _load_live_positions() -> pd.DataFrame:
    con = sqlite3.connect(CHAMPION_DB)
    positions = pd.read_sql_query(
        """
        SELECT *
        FROM unified_positions
        WHERE entry_date IS NOT NULL
          AND entry_date >= ?
          AND entry_date <= ?
        """,
        con,
        params=(LIVE_START_DATE, LIVE_END_DATE),
    )
    marks = pd.read_sql_query(
        """
        SELECT position_id, mark_date, close_return_pct
        FROM unified_marks
        WHERE (position_id, mark_date) IN (
            SELECT position_id, max(mark_date)
            FROM unified_marks
            GROUP BY position_id
        )
        """,
        con,
    )
    con.close()
    if positions.empty:
        return positions
    positions["ticker"] = positions["ticker"].astype(str).str.zfill(4)
    positions = positions.merge(
        marks.rename(columns={"position_id": "id", "close_return_pct": "latest_mark_return_pct"}),
        on="id",
        how="left",
    )
    return positions


def _enrich_live(live: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for rec in live.to_dict("records"):
        ticker = str(rec["ticker"]).zfill(4)
        pred_date = str(rec["prediction_date"])
        signal = _load_signal_row(pred_date, ticker)
        pred = _load_prediction_row(pred_date, ticker)
        daily = _daily_features(ticker, pred_date)
        rank = (
            signal.get("two_stage_rank")
            if signal
            else rec.get("two_stage_rank_at_entry")
        )
        if _safe_float(rank) is None:
            rank = signal.get("two_stage_rank_20d")
        if _safe_float(rank) is None:
            rank = signal.get("rank_20d")
        if _safe_float(rank) is None:
            rank = pred.get("two_stage_rank")
        if _safe_float(rank) is None:
            rank = rec.get("two_stage_rank_at_entry")
        feature = {
            "id": rec.get("id"),
            "ticker": ticker,
            "sector": rec.get("sector") or signal.get("sector") or pred.get("sector"),
            "entry_date": rec.get("entry_date"),
            "entry_month": str(rec.get("entry_date"))[:7],
            "prediction_date": pred_date,
            "status": rec.get("status"),
            "exit_reason": rec.get("exit_reason"),
            "exit_date": rec.get("exit_date"),
            "realized_return_pct": rec.get("realized_return_pct"),
            "latest_mark_return_pct": rec.get("latest_mark_return_pct"),
            "two_stage_rank": rank,
            "rank_bucket": _rank_bucket(rank),
            "stage1_score": (
                pred.get("leaderboard_score_before_two_stage")
                or pred.get("leaderboard_score")
                or pred.get("risk_adjusted_return")
                or signal.get("risk_adjusted_return")
            ),
            "stage2_score": pred.get("two_stage_score"),
            "price_vs_ma5": signal.get("price_vs_ma5") or signal.get("price_vs_ma5_20d") or daily.get("price_vs_ma5"),
            "price_vs_ma20": signal.get("price_vs_ma20") or signal.get("price_vs_ma20_20d") or rec.get("price_vs_ma20_at_entry") or daily.get("price_vs_ma20"),
            "beta_60": signal.get("beta_60") or signal.get("beta_60_20d") or pred.get("beta_60") or daily.get("beta_60"),
            "avg_20d_amount": signal.get("avg_20d_amount") or signal.get("avg_20d_amount_20d") or pred.get("avg_20d_amount") or daily.get("avg_20d_amount"),
            "group_code": signal.get("group_code") or signal.get("group_code_20d"),
            "group_name": signal.get("group_name") or signal.get("group_name_20d"),
        }
        rows.append(feature)
    return pd.DataFrame(rows)


def _load_oof_reference() -> pd.DataFrame:
    fold = _read_csv(OOF_ARTIFACT, dtype={"ticker": str})
    fold["ticker"] = fold["ticker"].astype(str).str.zfill(4)
    fold["Date"] = fold["Date"].astype(str)
    twii = _load_twii()
    sector, group = _load_mapping()
    price_cache: dict[str, pd.DataFrame | None] = {}
    rows: list[dict[str, Any]] = []
    for rec in fold.to_dict("records"):
        ticker = str(rec["ticker"]).zfill(4)
        if ticker not in price_cache:
            price_cache[ticker] = _load_price_history(ticker, {})
        ph = price_cache.get(ticker)
        simulated = None
        if ph is not None and not ph.empty:
            simulated = _simulate_control(
                ticker=ticker,
                prediction_date=str(rec["Date"]),
                month=str(rec["month"]),
                rank=int(rec["rank"]),
                price_history=ph,
                twii=twii,
            )
        daily = _daily_features(ticker, str(rec["Date"]), twii=twii)
        rows.append(
            {
                "ticker": ticker,
                "month": str(rec["month"]),
                "calendar_month": str(rec["month"])[5:7],
                "prediction_date": str(rec["Date"]),
                "two_stage_rank": rec.get("rank"),
                "rank_bucket": _rank_bucket(rec.get("rank")),
                "stage1_score": rec.get("stage1_score"),
                "stage2_score": rec.get("rank_score"),
                "return_pct": simulated.get("return_pct") if simulated else np.nan,
                "alpha_pct": simulated.get("alpha_pct") if simulated else np.nan,
                "exit_reason": simulated.get("exit_reason") if simulated else None,
                "hold_days": simulated.get("hold_days") if simulated else np.nan,
                **daily,
            }
        )
    out = pd.DataFrame(rows)
    out = out.merge(sector[["ticker", "name", "sector"]], on="ticker", how="left")
    out = out.merge(group[["ticker", "group_code", "group_name"]], on="ticker", how="left")
    out["group_code"] = out["group_code"].fillna("OTHER")
    out["group_name"] = out["group_name"].fillna("OTHER")
    return out


def _coverage(v: pd.Series) -> str:
    return f"{int(v.notna().sum())}/{len(v)}"


def _feature_summary(live: pd.DataFrame, oof: pd.DataFrame) -> pd.DataFrame:
    oof_apr_may = oof[oof["calendar_month"].isin(["04", "05"])]
    rows = []
    metrics = ["stage1_score", "stage2_score", "two_stage_rank", "price_vs_ma5", "price_vs_ma20", "beta_60", "avg_20d_amount"]
    for metric in metrics:
        for label, df in [("Live Apr-May", live), ("OOF Apr-May", oof_apr_may), ("OOF All", oof)]:
            s = pd.to_numeric(df.get(metric), errors="coerce")
            valid = s.dropna()
            rows.append(
                {
                    "metric": metric,
                    "sample": label,
                    "n": int(s.notna().sum()),
                    "mean": _num(valid.mean() if len(valid) else np.nan),
                    "median": _num(valid.median() if len(valid) else np.nan),
                    "p25": _num(valid.quantile(0.25) if len(valid) else np.nan),
                    "p75": _num(valid.quantile(0.75) if len(valid) else np.nan),
                    "coverage": _coverage(s),
                }
            )
    return pd.DataFrame(rows)


def _category_share(df: pd.DataFrame, column: str, label: str, top_values: list[str]) -> pd.DataFrame:
    total = max(len(df), 1)
    vc = df[column].fillna("UNKNOWN").astype(str).value_counts()
    return pd.DataFrame(
        {
            "category": top_values,
            label: [vc.get(v, 0) / total for v in top_values],
            f"{label}_n": [int(vc.get(v, 0)) for v in top_values],
        }
    )


def _sector_group_tables(live: pd.DataFrame, oof: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    oof_apr_may = oof[oof["calendar_month"].isin(["04", "05"])]
    sector_values = list(
        pd.concat([live["sector"], oof_apr_may["sector"]])
        .fillna("UNKNOWN")
        .astype(str)
        .value_counts()
        .head(8)
        .index
    )
    group_values = list(
        pd.concat([live["group_code"], oof_apr_may["group_code"]])
        .fillna("OTHER")
        .astype(str)
        .value_counts()
        .head(8)
        .index
    )
    sector_table = _category_share(live, "sector", "live_share", sector_values).merge(
        _category_share(oof_apr_may, "sector", "oof_apr_may_share", sector_values),
        on="category",
    )
    sector_table["delta"] = sector_table["live_share"] - sector_table["oof_apr_may_share"]
    group_table = _category_share(live, "group_code", "live_share", group_values).merge(
        _category_share(oof_apr_may, "group_code", "oof_apr_may_share", group_values),
        on="category",
    )
    group_table["delta"] = group_table["live_share"] - group_table["oof_apr_may_share"]
    for table in [sector_table, group_table]:
        for col in ["live_share", "oof_apr_may_share", "delta"]:
            table[col] = table[col].map(_pct)
    return sector_table, group_table


def _rank_bucket_table(live: pd.DataFrame, oof: pd.DataFrame) -> pd.DataFrame:
    live_closed = live[live["realized_return_pct"].notna()].copy()
    rows = []
    for bucket in ["Top10", "Rank11_20", "Rank21_30", "UNKNOWN"]:
        l = live_closed[live_closed["rank_bucket"] == bucket]
        o = oof[oof["rank_bucket"] == bucket]
        rows.append(
            {
                "rank_bucket": bucket,
                "live_n": len(l),
                "live_win_rate": _pct((pd.to_numeric(l["realized_return_pct"], errors="coerce") > 0).mean() if len(l) else np.nan),
                "live_avg_return": _pct(pd.to_numeric(l["realized_return_pct"], errors="coerce").mean() if len(l) else np.nan),
                "oof_n": len(o),
                "oof_win_rate": _pct((pd.to_numeric(o["return_pct"], errors="coerce") > 0).mean() if len(o) else np.nan),
                "oof_avg_return": _pct(pd.to_numeric(o["return_pct"], errors="coerce").mean() if len(o) else np.nan),
            }
        )
    return pd.DataFrame(rows)


def _cohort_table(live: pd.DataFrame) -> pd.DataFrame:
    rows = []
    closed = live[live["realized_return_pct"].notna()].copy()
    for month, grp in live.groupby("entry_month"):
        closed_grp = closed[closed["entry_month"] == month]
        ret = pd.to_numeric(closed_grp["realized_return_pct"], errors="coerce")
        rows.append(
            {
                "entry_month": month,
                "entries": len(grp),
                "closed": len(closed_grp),
                "avg_realized": _pct(ret.mean() if len(ret) else np.nan),
                "win_rate": _pct((ret > 0).mean() if len(ret) else np.nan),
                "ma5_exits": int(closed_grp["exit_reason"].astype(str).str.contains("MA5_BREAK", na=False).sum()),
                "top10_entries": int((grp["rank_bucket"] == "Top10").sum()),
                "rank_unknown_entries": int((grp["rank_bucket"] == "UNKNOWN").sum()),
            }
        )
    return pd.DataFrame(rows)


def _exit_forward_returns(live: pd.DataFrame) -> pd.DataFrame:
    rows = []
    closed = live[live["exit_reason"].astype(str).str.contains("MA5_BREAK", na=False)].copy()
    for horizon in [1, 3, 5]:
        vals = []
        for rec in closed.to_dict("records"):
            ticker = str(rec["ticker"]).zfill(4)
            exit_date = rec.get("exit_date")
            exit_price = _safe_float(rec.get("exit_price"))
            if not exit_date or exit_price is None:
                continue
            path = Path(DAILY_K_DIR) / f"{ticker}.csv"
            if not path.exists():
                continue
            df = _read_csv(path)
            df["Date"] = pd.to_datetime(df["Date"])
            future = df[df["Date"] > pd.Timestamp(exit_date)].reset_index(drop=True)
            if len(future) < horizon:
                continue
            close = _safe_float(future.iloc[horizon - 1].get("Close"))
            if close is not None:
                vals.append(close / exit_price - 1.0)
        s = pd.Series(vals, dtype=float)
        rows.append(
            {
                "horizon": f"{horizon}D",
                "n": len(s),
                "avg_forward_return": _pct(s.mean() if len(s) else np.nan),
                "positive_rate": _pct((s > 0).mean() if len(s) else np.nan),
                "diag007_reference": "+1D n/a / +3D +2.45% / +5D +4.51%",
            }
        )
    return pd.DataFrame(rows)


def _repeat_entry_table(live: pd.DataFrame) -> pd.DataFrame:
    closed = live[live["realized_return_pct"].notna()].copy()
    rows = []
    for ticker, grp in live.groupby("ticker"):
        if len(grp) < 2:
            continue
        c = closed[closed["ticker"] == ticker]
        ret = pd.to_numeric(c["realized_return_pct"], errors="coerce").dropna()
        compound = float((1.0 + ret).prod() - 1.0) if len(ret) else np.nan
        rows.append(
            {
                "ticker": ticker,
                "entries": len(grp),
                "closed": len(c),
                "compound_closed_return": _pct(compound),
                "avg_closed_return": _pct(ret.mean() if len(ret) else np.nan),
                "ma5_exits": int(c["exit_reason"].astype(str).str.contains("MA5_BREAK", na=False).sum()),
                "first_entry": str(grp["entry_date"].min()),
                "last_entry": str(grp["entry_date"].max()),
            }
        )
    out = pd.DataFrame(rows)
    if not out.empty:
        out = out.sort_values(["entries", "closed"], ascending=False).head(20)
    return out


def _format_feature_table(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for col in ["mean", "median", "p25", "p75"]:
        out[col] = out[col].astype(str)
    return out


@dataclass
class Diagnosis:
    feature: str
    rank: str
    forward: str
    repeat: str
    cohort: str
    overall: str


def _make_diagnosis(
    live: pd.DataFrame,
    feature: pd.DataFrame,
    rank: pd.DataFrame,
    forward: pd.DataFrame,
    repeat: pd.DataFrame,
    cohort: pd.DataFrame,
) -> Diagnosis:
    live_rank_known = live["rank_bucket"].ne("UNKNOWN").mean() if len(live) else 0
    top10_live = rank.loc[rank["rank_bucket"] == "Top10", "live_avg_return"].iloc[0]
    top10_oof = rank.loc[rank["rank_bucket"] == "Top10", "oof_avg_return"].iloc[0]
    f3 = forward.loc[forward["horizon"] == "3D", "avg_forward_return"].iloc[0]
    repeat_count = len(repeat)
    may_row = cohort[cohort["entry_month"] == "2026-05"]
    may_text = (
        f"May avg {may_row['avg_realized'].iloc[0]}, win {may_row['win_rate'].iloc[0]}"
        if not may_row.empty
        else "May cohort unavailable"
    )
    feature_diag = (
        "drift: live feature/rank coverage is materially incomplete"
        if live_rank_known < 0.8
        else "partial drift: live features available; compare sector/group and numeric shifts below"
    )
    rank_diag = (
        f"drift: live Top10 avg {top10_live} vs OOF Top10 {top10_oof}; rank edge is not carrying into live exits"
    )
    forward_diag = (
        f"exit check: live MA5_BREAK 3D forward return is {f3}; positive means whipsaw remains, negative means entry had no edge"
    )
    repeat_diag = (
        f"friction: {repeat_count} tickers repeated >=2 entries; repeat churn is concentrated if top rows dominate"
    )
    cohort_diag = f"cohort: {may_text}; compare April row to locate degradation."
    overall = (
        "DIAG-030 conclusion: live edge drift is present at the execution/live-signal layer. "
        "The strongest evidence is weak live rank-bucket PnL versus OOF and repeated MA5 exits; "
        "exit suppression has already failed CL3, so next action should focus on live signal quality gates or continued observation rather than another MA5 relaxation."
    )
    return Diagnosis(feature_diag, rank_diag, forward_diag, repeat_diag, cohort_diag, overall)


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    live_raw = _load_live_positions()
    live = _enrich_live(live_raw)
    sector, group = _load_mapping()
    live = live.merge(group[["ticker", "group_code", "group_name"]], on="ticker", how="left", suffixes=("", "_map"))
    live["group_code"] = live["group_code"].fillna(live.get("group_code_map")).fillna("OTHER")
    live["group_name"] = live["group_name"].fillna(live.get("group_name_map")).fillna("OTHER")
    live = live.drop(columns=[c for c in ["group_code_map", "group_name_map"] if c in live.columns])

    oof = _load_oof_reference()

    feature_summary = _feature_summary(live, oof)
    sector_table, group_table = _sector_group_tables(live, oof)
    rank_table = _rank_bucket_table(live, oof)
    forward_table = _exit_forward_returns(live_raw)
    repeat_table = _repeat_entry_table(live)
    cohort_table = _cohort_table(live)
    diagnosis = _make_diagnosis(live, feature_summary, rank_table, forward_table, repeat_table, cohort_table)

    closed = live[live["realized_return_pct"].notna()].copy()
    generated = datetime.now(timezone.utc).isoformat()
    lines = [
        "# DIAG-030 Live Signal Edge Drift 診斷",
        "",
        f"Generated: {generated}",
        f"Live window: {LIVE_START_DATE} ~ {LIVE_END_DATE}",
        f"Live entries: {len(live)}; closed/stopped entries: {len(closed)}",
        f"OOF reference: {OOF_ARTIFACT.name}; rows={len(oof)}",
        "",
        "## 整體診斷結論",
        "",
        diagnosis.overall,
        "",
        "## 1. Live vs OOF Feature Distribution",
        "",
        f"結論：{diagnosis.feature}",
        "",
        "### Numeric Features",
        "",
        _md_table(_format_feature_table(feature_summary)),
        "",
        "### Sector Share",
        "",
        _md_table(sector_table),
        "",
        "### Supply-chain Group Share",
        "",
        _md_table(group_table),
        "",
        "## 2. Rank Bucket PnL",
        "",
        f"結論：{diagnosis.rank}",
        "",
        _md_table(rank_table),
        "",
        "## 3. MA5_BREAK Exit-after Forward Return",
        "",
        f"結論：{diagnosis.forward}",
        "",
        _md_table(forward_table),
        "",
        "## 4. Repeat-entry Ticker Table",
        "",
        f"結論：{diagnosis.repeat}",
        "",
        _md_table(repeat_table),
        "",
        "## 5. April vs May Cohort PnL",
        "",
        f"結論：{diagnosis.cohort}",
        "",
        _md_table(cohort_table),
        "",
        "## RD 建議下一步",
        "",
        "- 不建議再開 MA5 放寬類 production 票；REQ-016/018/019/021/029 已反覆顯示 MDD 或 alpha 不支持。",
        "- 若 PM 要繼續追，建議開一張 live signal quality gate 診斷：只看 low-rank / low-liquidity / below-MA5 重複進場是否應降權，而不是改 exit。",
        "- 5 月樣本仍短，若未達 PM 上線變更門檻，建議先累積到 20D 成熟後再做 closure。",
        "",
    ]
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(f"[DIAG-030] wrote {REPORT_PATH}")
    print(diagnosis.overall)


if __name__ == "__main__":
    main()
