# -*- coding: utf-8 -*-
"""季報基本面三類本機缺陷唯讀掃描(2026-10-17,比照 #37 月營收對帳)。

缺陷定義(docs/STRATEGY_overview.md #36/#37):
  1. 歷史深度不足:股票已上市、該季應有財報,本機卻沒有這一列
     (月營收案例=建檔只抓近期、從未回補)。
  2. 列在但值是 NaN:列存在、關鍵欄位為空,而更新程式只看「檔案/列數」所以永不重抓。
  3. 事後更正沒跟上:本機只在某次抓取時寫入,官方現值已變。

兩個子命令:
  fetch  向 MOPS 彙總表(t163sb04/t163sb05/t163sb06,與正式抓取同一端點、同一解析器)
         抓官方現值,**只寫** ml/reports/cache/fundamentals_defect_scan/(gitignore 研究快取)。
  scan   完全離線:讀 季報財務/、資產負債/、日K資料/、研究快取與 stock.duckdb(read_only),
         輸出 ml/reports/fundamentals_defect_scan_<date>.json 與明細 CSV。

本腳本不寫 季報財務/、資產負債/、DuckDB 或任何正式目錄。
用法:
  python -X utf8 scripts/scan_fundamentals_defects.py fetch [--quarters 2025Q3,2025Q4,...]
  python -X utf8 scripts/scan_fundamentals_defects.py scan
"""
from __future__ import annotations

import argparse
import glob
import json
import math
import os
import random
import re
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

BASE = Path(os.environ.get("STOCK_BASE_DIR") or Path(__file__).resolve().parents[1])
sys.path.insert(0, str(BASE))

FIN_DIR = BASE / "季報財務"
BS_DIR = BASE / "資產負債"
DAILY_K_DIR = BASE / "日K資料"
CACHE_DIR = BASE / "ml" / "reports" / "cache" / "fundamentals_defect_scan"
REPORT_DIR = BASE / "ml" / "reports"

KINDS = {
    # kind: (directory, file prefix, primary metric, metrics)
    "eps": (FIN_DIR, "eps_", "EPS_Basic", ["EPS_Basic"]),
    "financial": (FIN_DIR, "financial_", "Operating_Margin_Pct",
                  ["Revenue_M", "Gross_Margin_Pct", "Operating_Margin_Pct",
                   "Pretax_Margin_Pct", "Net_Margin_Pct"]),
    "bs": (BS_DIR, "bs_", "Total_Assets",
           ["Current_Assets", "Total_Assets", "Current_Liabilities", "Total_Liabilities",
            "Share_Capital", "Parent_Equity", "Total_Equity", "Book_Value_Per_Share",
            "Debt_Ratio", "Current_Ratio"]),
}
# 比對容差:來源皆為 MOPS 文字表,同版本應逐字相同;容差只吸收浮點表示差。
ABS_TOL = {"EPS_Basic": 0.005, "Revenue_M": 0.005, "Book_Value_Per_Share": 0.005,
           "Debt_Ratio": 0.005, "Current_Ratio": 0.005}
DEFAULT_ABS_TOL_PCT = 0.005   # *_Margin_Pct
DEFAULT_ABS_TOL_UNIT = 0.5    # 資產負債表金額(千元)

# 即時預測會讀到的季度(今天 2026-10-17,最新應完整季=2026Q2;2026Q3 於 11/14 才到期)。
# EPS:單季=本季累計-上季累計,yoy 要去年同季單季 → 2025Q1..2026Q2;
# 損益表:yoy 要 2025Q2、margin_trend 要 2026Q1;資產負債:roe_trend 要 2026Q1。
LIVE_WINDOW = {"eps": [20251, 20252, 20253, 20254, 20261, 20262],
               "financial": [20252, 20253, 20254, 20261, 20262],
               "bs": [20253, 20254, 20261, 20262]}
RECENT4 = [20253, 20254, 20261, 20262]
LATEST_DUE = 20262
SAMPLE_SEED = 20261017
# 本機日K從 2020-01-02 起;第一根K棒在這之前者視為 2020Q1 前已上市。
LOCAL_HISTORY_GRACE = date(2020, 1, 10)
API_BASE = "http://127.0.0.1:8001"


def yq_label(yq: int) -> str:
    return f"{yq // 10}Q{yq % 10}"


def quarter_bounds(yq: int) -> tuple[date, date]:
    y, q = yq // 10, yq % 10
    start = date(y, 3 * q - 2, 1)
    end = {1: date(y, 3, 31), 2: date(y, 6, 30), 3: date(y, 9, 30), 4: date(y, 12, 31)}[q]
    return start, end


def available_date(yq: int) -> date:
    """與 ml/features/eps.py::_QUARTER_AVAILABLE 一致的保守公布日。"""
    y, q = yq // 10, yq % 10
    return {1: date(y, 5, 15), 2: date(y, 8, 14), 3: date(y, 11, 14), 4: date(y + 1, 3, 31)}[q]


def all_quarters(last: int = LATEST_DUE) -> list[int]:
    out, y, q = [], 2020, 1
    while y * 10 + q <= last:
        out.append(y * 10 + q)
        y, q = (y + 1, 1) if q == 4 else (y, q + 1)
    return out


def load_kind(kind: str, root: Path | None = None) -> pd.DataFrame:
    directory, prefix, _, metrics = KINDS[kind]
    if root is not None:
        directory = root
    frames = []
    for path in sorted(glob.glob(str(directory / f"{prefix}*.csv"))):
        match = re.search(rf"{prefix}(\d{{4}})Q([1-4])\.csv$", os.path.basename(path))
        if not match:
            continue
        df = pd.read_csv(path, dtype={"Ticker": str})
        df["yq"] = int(match[1]) * 10 + int(match[2])
        frames.append(df)
    if not frames:
        return pd.DataFrame(columns=["Ticker", "Name", "Market", "yq", *metrics])
    out = pd.concat(frames, ignore_index=True)
    for metric in metrics:
        if metric in out.columns:
            out[metric] = pd.to_numeric(out[metric], errors="coerce")
    return out


def daily_k_spans() -> pd.DataFrame:
    """每檔日K第一/最後一根K棒日期,當作「已上市且有交易」的區間。"""
    rows = {}
    for path in glob.glob(str(DAILY_K_DIR / "*.csv")):
        ticker = os.path.basename(path)[:-4]
        with open(path, encoding="utf-8-sig") as fh:
            fh.readline()
            first = fh.readline()[:10]
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - 4000))
            tail = fh.read().decode("utf-8", "replace").strip().splitlines()
        last = tail[-1][:10] if tail else ""
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", first) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", last):
            rows[ticker] = (date.fromisoformat(first), date.fromisoformat(last))
    frame = pd.DataFrame.from_dict(rows, orient="index", columns=["dk_first", "dk_last"])
    frame.index.name = "Ticker"
    return frame


def stock_universe(spans: pd.DataFrame) -> pd.DataFrame:
    from ml.universe import is_etf_ticker
    keep = [t for t in spans.index if re.fullmatch(r"[1-9]\d{3}", t) and not is_etf_ticker(t)]
    return spans.loc[keep]


# ---------------------------------------------------------------------------
# fetch(研究快取)
# ---------------------------------------------------------------------------
def _fetchers():
    from scripts import backfill_eps, backfill_balance_sheet
    import twstock
    return {
        "eps": (backfill_eps.fetch_mops_eps, backfill_eps.parse_mops_eps_tables),
        "financial": (twstock._fetch_mops_batch, twstock._parse_mops_ratio_tables),
        "bs": (backfill_balance_sheet.fetch_balance_sheet, backfill_balance_sheet.parse_balance_sheet),
    }


def cmd_fetch(quarters: list[int], kinds: list[str], pause: float) -> int:
    from scripts.quarterly_source_contract import validate_html_period
    fetchers = _fetchers()
    raw_dir = CACHE_DIR / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = CACHE_DIR / "fetch_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    failures = 0
    for yq in quarters:
        y, q = yq // 10, yq % 10
        for kind in kinds:
            out_csv = CACHE_DIR / f"{KINDS[kind][1]}{y}Q{q}.csv"
            if out_csv.exists() and manifest.get(out_csv.name, {}).get("status") == "ok":
                continue
            fetch, parse = fetchers[kind]
            frames, evidence = [], {}
            for typek, market in (("sii", "TWSE"), ("otc", "OTC")):
                html = None
                for attempt in range(4):
                    try:
                        html = fetch(y - 1911, q, typek)
                    except Exception as exc:  # noqa: BLE001
                        print(f"  {kind} {y}Q{q} {market} attempt {attempt + 1}: {exc}")
                        html = None
                    frame = parse(html) if html else pd.DataFrame()
                    if not frame.empty:
                        break
                    snippet = re.sub(r"\s+", " ", (html or "")[:300])
                    print(f"  {kind} {y}Q{q} {market} empty (attempt {attempt + 1}): {snippet!r}")
                    time.sleep(pause * (3 + attempt * 3))
                time.sleep(pause + random.uniform(0, 2))
                if frame.empty:
                    evidence[market] = {"status": "empty"}
                    continue
                try:
                    proof = validate_html_period(html, kind, y, q, market)
                except ValueError as exc:
                    evidence[market] = {"status": "period_mismatch", "detail": str(exc)}
                    continue
                (raw_dir / f"{kind}_{y}Q{q}_{market}.html").write_text(html, encoding="utf-8")
                frame["Market"] = market
                frames.append(frame)
                evidence[market] = {"status": "ok", "rows": len(frame), "title": proof["title"]}
            ok = all(evidence.get(m, {}).get("status") == "ok" for m in ("TWSE", "OTC"))
            if frames:
                result = pd.concat(frames, ignore_index=True)
                result["Year"], result["Season"] = y, q
                result.to_csv(out_csv, index=False, encoding="utf-8-sig")
            manifest[out_csv.name] = {"status": "ok" if ok else "partial", "evidence": evidence,
                                      "fetched_at_utc": datetime.now(timezone.utc).isoformat()}
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"{kind} {y}Q{q}: {'ok' if ok else 'PARTIAL'} "
                  f"{ {m: e.get('rows', e.get('status')) for m, e in evidence.items()} }", flush=True)
            failures += not ok
    return 1 if failures else 0


# ---------------------------------------------------------------------------
# scan(離線)
# ---------------------------------------------------------------------------
def _tol(metric: str) -> float:
    if metric in ABS_TOL:
        return ABS_TOL[metric]
    return DEFAULT_ABS_TOL_PCT if metric.endswith("_Pct") else DEFAULT_ABS_TOL_UNIT


def scan_history_depth(local: dict[str, pd.DataFrame], universe: pd.DataFrame,
                       official: dict[str, pd.DataFrame]) -> tuple[dict, pd.DataFrame]:
    """缺陷 1:股票整季都在交易、公布日時仍在交易,但本機該季沒有列。"""
    quarters = all_quarters()
    present = {k: set(zip(df.Ticker, df.yq)) for k, df in local.items()}
    official_present = {k: set(zip(df.Ticker, df.yq)) for k, df in official.items()}
    official_quarters = {k: set(df.yq) for k, df in official.items()}
    rows = []
    for ticker, span in universe.iterrows():
        for yq in quarters:
            q_start, _ = quarter_bounds(yq)
            if not (span.dk_first <= max(q_start, LOCAL_HISTORY_GRACE) and span.dk_last >= available_date(yq)):
                continue
            for kind in KINDS:
                if (ticker, yq) in present[kind]:
                    continue
                other = [k for k in KINDS if k != kind and (ticker, yq) in present[k]]
                in_official = None
                if yq in official_quarters.get(kind, set()):
                    in_official = (ticker, yq) in official_present[kind]
                rows.append({"kind": kind, "Ticker": ticker, "yq": yq,
                             "dk_first": span.dk_first.isoformat(),
                             "present_in_other_local_kinds": ",".join(other),
                             "present_in_official_now": in_official})
    detail = pd.DataFrame(rows)
    # 本機有日K、上市滿一季,但三類季報任何一季都沒有
    never = {}
    for kind, df in local.items():
        have = set(df.Ticker)
        never[kind] = sorted(t for t, s in universe.iterrows()
                             if t not in have and s.dk_first <= quarter_bounds(LATEST_DUE)[0])
    # 跨類別比對:同一個 MOPS 彙總來源家族,a 有而 b 沒有的 (ticker, 季);
    # 只算 b 至少有一列的股票(排除 b 端點結構上不收的產業)。多半落在上市前的半年報/年報。
    cross, cross_rows = {}, []
    for a in KINDS:
        for b in KINDS:
            if a == b:
                continue
            tickers_b = set(local[b].Ticker)
            diff = sorted((t, yq) for t, yq in present[a] - present[b] if t in tickers_b)
            official_has = [(t, yq) for t, yq in diff if yq in official_quarters.get(b, set())
                            and (t, yq) in official_present[b]]
            live = sorted({t for t, yq in diff if yq in LIVE_WINDOW[b]})
            cross[f"{a}_not_{b}"] = {"rows": len(diff), "tickers": len({t for t, _ in diff}),
                                     "official_b_has_row_now": len(official_has),
                                     "by_quarter": {yq_label(k): v for k, v in
                                                    pd.Series([yq for _, yq in diff], dtype=int).value_counts().sort_index().items()},
                                     "live_window_tickers": live}
            for t, yq in diff:
                span = universe.loc[t] if t in universe.index else None
                cross_rows.append({"have": a, "missing_in": b, "Ticker": t, "yq": yq,
                                   "dk_first": span.dk_first.isoformat() if span is not None else "",
                                   "pre_listing": bool(span is not None and quarter_bounds(yq)[1] < span.dk_first),
                                   "official_missing_kind_has_row_now": (t, yq) in set(official_has)})
    # b 端點整檔不收的股票:官方現值也沒有 → 來源涵蓋範圍,不是本機漏抓
    for kind, tickers in never.items():
        if kind in official and not official[kind].empty:
            off_t = set(official[kind].Ticker)
            never[kind] = [{"Ticker": t, "official_has_any_row_now": t in off_t} for t in tickers]
        else:
            never[kind] = [{"Ticker": t, "official_has_any_row_now": None} for t in tickers]
    summary = {"never_any_row": {k: {"count": len(v), "tickers": v} for k, v in never.items()},
               "cross_kind_missing": cross}
    detail_extra = pd.DataFrame(cross_rows)
    if not detail.empty:
        for kind, g in detail.groupby("kind"):
            confirmed = g[g.present_in_official_now.eq(True)]
            summary[kind] = {
                "missing_rows": len(g), "tickers": g.Ticker.nunique(),
                "official_has_row_now": {"rows": len(confirmed), "tickers": confirmed.Ticker.nunique()},
                "official_also_missing": int(g.present_in_official_now.eq(False).sum()),
                "official_not_checked": int(g.present_in_official_now.isna().sum()),
                "other_local_kind_has_row": int(g.present_in_other_local_kinds.ne("").sum()),
                "by_quarter": {yq_label(k): int(v) for k, v in g.groupby("yq").size().items()},
                "live_window": sorted(g[g.yq.isin(LIVE_WINDOW[kind])].Ticker.unique().tolist()),
            }
    return summary, detail, detail_extra


def scan_nan(local: dict[str, pd.DataFrame], universe: pd.DataFrame) -> tuple[dict, pd.DataFrame]:
    """缺陷 2:列存在、值為 NaN。區分「該股所有季度都 NaN(產業格式無此欄)」與「零星 NaN」。"""
    summary, details = {}, []
    for kind, df in local.items():
        metrics = [m for m in KINDS[kind][3] if m in df.columns]
        kind_summary = {"rows": len(df), "all_metric_nan_rows": int(df[metrics].isna().all(axis=1).sum())}
        for metric in metrics:
            nan = df[df[metric].isna()]
            if nan.empty:
                kind_summary[metric] = {"nan_rows": 0}
                continue
            ticker_share = df.groupby("Ticker")[metric].apply(lambda s: s.isna().mean())
            structural = set(ticker_share[ticker_share.eq(1.0)].index)
            sporadic = nan[~nan.Ticker.isin(structural)]
            kind_summary[metric] = {
                "nan_rows": len(nan), "nan_tickers": nan.Ticker.nunique(),
                "structural_rows": int(nan.Ticker.isin(structural).sum()),
                "structural_tickers": len(structural & set(nan.Ticker)),
                "sporadic_rows": len(sporadic), "sporadic_tickers": sporadic.Ticker.nunique(),
                "sporadic_by_quarter": {yq_label(k): int(v) for k, v in sporadic.groupby("yq").size().items()},
                "sporadic_live_window": sorted(sporadic[sporadic.yq.isin(LIVE_WINDOW[kind])].Ticker.unique().tolist()),
            }
            for _, r in nan.iterrows():
                details.append({"kind": kind, "metric": metric, "Ticker": r.Ticker, "Name": r.get("Name"),
                                "yq": r.yq, "pattern": "structural" if r.Ticker in structural else "sporadic",
                                "all_metrics_nan": bool(df.loc[_, metrics].isna().all())})
        summary[kind] = kind_summary
    return summary, pd.DataFrame(details)


def compare_official(local: dict[str, pd.DataFrame], official: dict[str, pd.DataFrame]) -> tuple[dict, pd.DataFrame]:
    """缺陷 3:同季同股,本機值 vs 官方彙總表現值。"""
    summary, details = {}, []
    for kind, off in official.items():
        if off.empty:
            continue
        loc = local[kind]
        metrics = [m for m in KINDS[kind][3] if m in off.columns and m in loc.columns]
        merged = loc.merge(off, on=["Ticker", "yq"], how="outer", suffixes=("_local", "_official"),
                           indicator=True)
        merged = merged[merged.yq.isin(set(off.yq))]
        both = merged[merged._merge.eq("both")]
        kind_summary = {"quarters": [yq_label(x) for x in sorted(set(off.yq))],
                        "overlap_rows": len(both),
                        "local_only_rows": int(merged._merge.eq("left_only").sum()),
                        "official_only_rows": int(merged._merge.eq("right_only").sum())}
        mismatch_keys = set()
        for metric in metrics:
            a, b = both[f"{metric}_local"], both[f"{metric}_official"]
            nan_fixed = a.isna() & b.notna()
            nan_lost = a.notna() & b.isna()
            diff = a.notna() & b.notna() & ((a - b).abs() > _tol(metric))
            kind_summary[metric] = {"local_nan_official_value": int(nan_fixed.sum()),
                                    "local_value_official_nan": int(nan_lost.sum()),
                                    "value_differs": int(diff.sum())}
            for idx in both.index[nan_fixed | nan_lost | diff]:
                r = both.loc[idx]
                mismatch_keys.add((r.Ticker, r.yq))
                details.append({"kind": kind, "Ticker": r.Ticker, "yq": int(r.yq), "metric": metric,
                                "local": r[f"{metric}_local"], "official": r[f"{metric}_official"],
                                "type": "nan_in_local" if nan_fixed[idx] else
                                        ("nan_in_official" if nan_lost[idx] else "value_differs")})
        mk = pd.DataFrame(list(mismatch_keys), columns=["Ticker", "yq"])
        kind_summary["mismatch_rows"] = len(mk)
        kind_summary["mismatch_tickers"] = mk.Ticker.nunique() if not mk.empty else 0
        kind_summary["mismatch_by_quarter"] = ({yq_label(k): int(v) for k, v in mk.groupby("yq").size().items()}
                                               if not mk.empty else {})
        kind_summary["live_window_tickers"] = (sorted(mk[mk.yq.isin(LIVE_WINDOW[kind])].Ticker.unique().tolist())
                                               if not mk.empty else [])
        official_only = merged[merged._merge.eq("right_only")]
        kind_summary["official_only_detail"] = [
            f"{r.Ticker}@{yq_label(int(r.yq))}" for r in official_only.itertuples()]
        kind_summary["official_only_live_window_tickers"] = sorted(
            official_only[official_only.yq.isin(LIVE_WINDOW[kind])].Ticker.unique().tolist())
        kind_summary["local_only_detail"] = [
            f"{r.Ticker}@{yq_label(int(r.yq))}" for r in merged[merged._merge.eq("left_only")].itertuples()]
        summary[kind] = kind_summary
    return summary, pd.DataFrame(details)


def sample_20(local: dict[str, pd.DataFrame], official: dict[str, pd.DataFrame], universe: pd.DataFrame) -> dict:
    """任務指定的隨機 20 檔 × 最近 4 季 抽樣(全量比對的子集,固定亂數種子可重現)。"""
    pool = sorted(t for t in universe.index if t in set(local["eps"].Ticker))
    picks = sorted(random.Random(SAMPLE_SEED).sample(pool, 20))
    out = {"seed": SAMPLE_SEED, "tickers": picks, "cells": 0, "matched": 0, "mismatches": []}
    for kind, off in official.items():
        if off.empty:
            continue
        loc = local[kind]
        metrics = [m for m in KINDS[kind][3] if m in off.columns and m in loc.columns]
        fetched = set(off.yq)
        for t in picks:
            for yq in (x for x in RECENT4 if x in fetched):
                lo = loc[(loc.Ticker == t) & (loc.yq == yq)]
                of = off[(off.Ticker == t) & (off.yq == yq)]
                if lo.empty and of.empty:
                    continue
                for m in metrics:
                    out["cells"] += 1
                    a = lo[m].iloc[0] if not lo.empty else math.nan
                    b = of[m].iloc[0] if not of.empty else math.nan
                    same = (pd.isna(a) and pd.isna(b)) or (pd.notna(a) and pd.notna(b) and abs(a - b) <= _tol(m))
                    if same:
                        out["matched"] += 1
                    else:
                        out["mismatches"].append(f"{kind}:{t}@{yq_label(yq)}:{m} local={a} official={b}")
    return out


def compare_duckdb(local_fin: pd.DataFrame) -> dict:
    """financials 表 vs financial_*.csv(DuckDB 只匯入損益表;EPS/資產負債特徵直接讀 CSV)。"""
    import duckdb
    db = BASE / "stock.duckdb"
    try:
        con = duckdb.connect(str(db), read_only=True)
    except Exception as exc:  # noqa: BLE001
        # 常駐網頁伺服器獨占 DuckDB 檔時,改用它的唯讀 GET /api/stocks/{t}/financials
        # (SELECT * FROM financials WHERE Ticker=...),不呼叫任何會釋放連線的端點。
        out = {"status": "via_api", "direct_open_error": f"{type(exc).__name__}: {str(exc)[:200]}"}
        db_df, meta = _financials_via_api(sorted(set(local_fin.Ticker))), pd.DataFrame()
        if db_df is None:
            out["status"] = "unavailable"
            return out
    else:
        try:
            tables = {r[0] for r in con.execute("SELECT table_name FROM information_schema.tables").fetchall()}
            out = {"status": "ok", "fundamental_tables": sorted(t for t in tables if re.search(r"financ|eps|balance|bs_", t))}
            if "financials" not in tables:
                out["status"] = "no_financials_table"
                return out
            db_df = con.execute("SELECT * FROM financials").df()
            meta = con.execute("SELECT * FROM ingest_meta WHERE table_name = 'financials'").df() \
                if "ingest_meta" in tables else pd.DataFrame()
        finally:
            con.close()
    db_df["Ticker"] = db_df["Ticker"].astype(str)
    db_df["yq"] = db_df["Year"].astype(int) * 10 + db_df["Season"].astype(int)
    metrics = KINDS["financial"][3]
    m = local_fin.merge(db_df, on=["Ticker", "yq"], how="outer", suffixes=("_csv", "_db"), indicator=True)
    both = m[m._merge.eq("both")]
    diffs = {}
    for metric in metrics:
        a, b = both[f"{metric}_csv"], pd.to_numeric(both[f"{metric}_db"], errors="coerce")
        diffs[metric] = int((~((a.isna() & b.isna()) | ((a - b).abs() <= 1e-9))).sum())
    ticker_type = str(db_df["Ticker"].dtype)
    out.update({"csv_rows": len(local_fin), "db_rows": len(db_df),
                "csv_only": int(m._merge.eq("left_only").sum()), "db_only": int(m._merge.eq("right_only").sum()),
                "value_diffs": diffs, "db_ticker_dtype": ticker_type,
                "db_quarters": [yq_label(x) for x in sorted(db_df.yq.unique())][-3:],
                "ingest_meta": meta.astype(str).to_dict(orient="records")})
    return out


def _financials_via_api(tickers: list[str]) -> pd.DataFrame | None:
    import urllib.request
    frames = []
    for ticker in tickers:
        try:
            with urllib.request.urlopen(f"{API_BASE}/api/stocks/{ticker}/financials", timeout=15) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            print(f"  financials API {ticker}: {exc}")
            if not frames:
                return None
            continue
        if payload.get("data"):
            frames.append(pd.DataFrame(payload["data"]))
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def cmd_scan(stamp: str) -> int:
    local = {k: load_kind(k) for k in KINDS}
    official = {k: load_kind(k, CACHE_DIR) for k in KINDS}
    spans = daily_k_spans()
    universe = stock_universe(spans)
    depth, depth_detail, cross_detail = scan_history_depth(local, universe, official)
    nan_summary, nan_detail = scan_nan(local, universe)
    corr_summary, corr_detail = compare_official(local, official)
    result = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "universe": {"daily_k_files": len(spans), "common_stocks": len(universe)},
        "local_files": {k: {"tickers": int(df.Ticker.nunique()), "rows": len(df),
                            "quarters": f"{yq_label(df.yq.min())}..{yq_label(df.yq.max())}"}
                        for k, df in local.items()},
        "official_cache": {k: [yq_label(x) for x in sorted(set(df.yq))] for k, df in official.items()},
        "defect1_history_depth": depth,
        "defect2_nan": nan_summary,
        "defect3_corrections": corr_summary,
        "sample_20x4": sample_20(local, official, universe),
        "duckdb_financials": compare_duckdb(local["financial"]),
    }
    out = REPORT_DIR / f"fundamentals_defect_scan_{stamp}.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    detail_dir = CACHE_DIR / "scan_detail"
    detail_dir.mkdir(parents=True, exist_ok=True)
    depth_detail.to_csv(detail_dir / "defect1_missing_rows.csv", index=False, encoding="utf-8-sig")
    cross_detail.to_csv(detail_dir / "defect1_cross_kind_rows.csv", index=False, encoding="utf-8-sig")
    nan_detail.to_csv(detail_dir / "defect2_nan_rows.csv", index=False, encoding="utf-8-sig")
    corr_detail.to_csv(detail_dir / "defect3_mismatch_cells.csv", index=False, encoding="utf-8-sig")
    print(f"wrote {out}")
    return 0


def build_patched_dirs() -> tuple[Path, Path]:
    """研究快取內組一份「官方現值」季報:官方列 + 官方已不列的本機舊列(同 write_verified_quarter 保留規則)。"""
    fin_out, bs_out = CACHE_DIR / "patched" / "季報財務", CACHE_DIR / "patched" / "資產負債"
    for out_dir in (fin_out, bs_out):
        out_dir.mkdir(parents=True, exist_ok=True)
    for kind in KINDS:
        directory, prefix, _, metrics = KINDS[kind]
        out_dir = bs_out if kind == "bs" else fin_out
        for yq in all_quarters():
            name = f"{prefix}{yq_label(yq)}.csv"
            local_df = pd.read_csv(directory / name, dtype={"Ticker": str})
            official_path = CACHE_DIR / name
            if not official_path.exists():
                local_df.to_csv(out_dir / name, index=False, encoding="utf-8-sig")
                continue
            off = pd.read_csv(official_path, dtype={"Ticker": str})
            if kind == "bs":
                off["Debt_Ratio"] = (off.Total_Liabilities / off.Total_Assets.replace(0, math.nan) * 100).round(2)
                off["Current_Ratio"] = (off.Current_Assets / off.Current_Liabilities.replace(0, math.nan) * 100).round(2)
            retained = local_df[~local_df.Ticker.isin(off.Ticker)]
            merged = pd.concat([off[local_df.columns], retained], ignore_index=True).sort_values("Ticker")
            merged.to_csv(out_dir / name, index=False, encoding="utf-8-sig")
    return fin_out, bs_out


def cmd_feature_impact(stamp: str, as_of: str) -> int:
    """同一套特徵函式、同一個日期,只換季報目錄:本機 vs 官方現值,看即時特徵差多少。"""
    import numpy as np
    from ml.features.eps import compute_eps_features, EPS_FEATURE_COLS
    from ml.features.fundamental import compute_fundamental_features, FUNDAMENTAL_FEATURE_COLS
    from ml.features.balance_sheet import compute_balance_sheet_features, BALANCE_SHEET_FEATURE_COLS
    report_path = REPORT_DIR / f"fundamentals_defect_scan_{stamp}.json"
    scan = json.loads(report_path.read_text(encoding="utf-8"))
    corr = scan["defect3_corrections"]
    affected = set()
    for kind in KINDS:
        k = corr.get(kind, {})
        affected.update(x.split("@")[0] for x in k.get("official_only_detail", []))
        affected.update(k.get("live_window_tickers", []))
    universe = stock_universe(daily_k_spans())
    pool = sorted(set(universe.index) - affected)
    controls = sorted(random.Random(SAMPLE_SEED).sample(pool, 100))
    fin_patch, bs_patch = build_patched_dirs()
    day = pd.DataFrame({"Date": [pd.Timestamp(as_of)]})

    def features(ticker: str, fin_dir: Path, bs_dir: Path) -> dict:
        row = compute_eps_features(day, ticker, str(fin_dir))
        row = compute_fundamental_features(row, ticker, str(fin_dir))
        row = compute_balance_sheet_features(row, ticker, str(bs_dir), str(fin_dir))
        cols = [*EPS_FEATURE_COLS, *FUNDAMENTAL_FEATURE_COLS, *BALANCE_SHEET_FEATURE_COLS]
        return {c: float(row[c].iloc[0]) if c in row and pd.notna(row[c].iloc[0]) else math.nan for c in cols}

    rows = []
    for group, tickers in (("affected", sorted(affected)), ("control", controls)):
        for ticker in tickers:
            a, b = features(ticker, FIN_DIR, BS_DIR), features(ticker, fin_patch, bs_patch)
            for col in a:
                x, y = a[col], b[col]
                if (math.isnan(x) and math.isnan(y)) or (not math.isnan(x) and not math.isnan(y) and abs(x - y) <= 1e-6):
                    continue
                rows.append({"group": group, "Ticker": ticker, "feature": col, "local": x, "official": y,
                             "change": "nan_to_value" if math.isnan(x) else ("value_to_nan" if math.isnan(y) else "value_changed")})
    diff = pd.DataFrame(rows, columns=["group", "Ticker", "feature", "local", "official", "change"])
    detail_dir = CACHE_DIR / "scan_detail"
    detail_dir.mkdir(parents=True, exist_ok=True)
    diff.to_csv(detail_dir / f"feature_impact_{as_of}.csv", index=False, encoding="utf-8-sig")
    out = {"as_of": as_of, "affected_tickers": len(affected), "control_tickers": len(controls),
           "affected_with_feature_change": int(diff[diff.group.eq("affected")].Ticker.nunique()),
           "control_with_feature_change": int(diff[diff.group.eq("control")].Ticker.nunique()),
           "by_change": diff.groupby(["group", "change"]).size().astype(int).to_dict() if not diff.empty else {},
           "by_feature": diff[diff.group.eq("affected")].groupby("feature").Ticker.nunique().astype(int).to_dict()
           if not diff.empty else {},
           "tickers": {t: sorted(g.feature.tolist()) for t, g in diff[diff.group.eq("affected")].groupby("Ticker")}}
    out["by_change"] = {f"{g}:{c}": v for (g, c), v in out["by_change"].items()}
    scan["live_feature_impact"] = out
    report_path.write_text(json.dumps(scan, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(json.dumps({k: v for k, v in out.items() if k != "tickers"}, ensure_ascii=False, indent=1))
    return 0


def _parse_quarters(text: str | None) -> list[int]:
    if not text:
        return list(reversed(all_quarters()))  # 最近的先抓,被節流時至少拿到即時視窗
    out = []
    for token in text.split(","):
        match = re.fullmatch(r"(\d{4})Q([1-4])", token.strip())
        if not match:
            raise SystemExit(f"bad quarter: {token}")
        out.append(int(match[1]) * 10 + int(match[2]))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch")
    f.add_argument("--quarters")
    f.add_argument("--kinds", default="eps,financial,bs")
    f.add_argument("--pause", type=float, default=5.0)
    s = sub.add_parser("scan")
    s.add_argument("--stamp", default=date.today().strftime("%Y%m%d"))
    fi = sub.add_parser("feature-impact")
    fi.add_argument("--stamp", default=date.today().strftime("%Y%m%d"))
    fi.add_argument("--as-of", required=True, help="特徵日期(最近交易日),例:2026-10-16")
    args = ap.parse_args()
    if args.cmd == "fetch":
        return cmd_fetch(_parse_quarters(args.quarters), args.kinds.split(","), args.pause)
    if args.cmd == "feature-impact":
        return cmd_feature_impact(args.stamp, args.as_of)
    return cmd_scan(args.stamp)


if __name__ == "__main__":
    raise SystemExit(main())
