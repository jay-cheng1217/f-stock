# -*- coding: utf-8 -*-
"""#41 AB-quarterly-fundamentals-backfill:季報補資料+改正的暫存、硬停檢查與固定快照 A/B(2026-10-17,方法鎖定 d272a03a)。

**全程不寫正式 季報財務/、資產負債/、DuckDB、快照、預測檔或帳本**:
  stage       暫存 = 官方現值全部列 + 官方已不列的本機舊列原樣保留;欄位與順序完全同本機原檔;
              資產負債 Debt_Ratio/Current_Ratio 依 backfill_balance_sheet 同式重算。
              寫到 ml/reports/cache/fundamentals_backfill_stage/{季報財務,資產負債}/,並做硬停 (2):
              本機與官方相同的列,暫存值須與本機逐格相同。
  spot-check  硬停 (1):從「新增+改正」列隨機抽 20 筆(seed 20261017),MOPS 單一公司查詢(t164sb04 損益/EPS、
              t164sb03 資產負債)現值須與彙總現值一致 >= 19/20。原始 HTML 存研究快取。
  ingest-test 硬停 (3):`_ingest_financials` 同句 read_csv_auto(記憶體內 DuckDB,不開 stock.duckdb)分別跑現行與暫存:
              列數不減、代號數不減、0 個帶小數點代號、最新季不倒退;並掃每個季報檔 Ticker 欄的推斷型別。
  live-ab     同一份日K、同一組正式模型,只把 FINANCIAL_DIR 與資產負債目錄換成暫存,逐檔最新列 → 評分 → 對齊比較。
  history     受影響股票全期日列,季報特徵 NaN→有值 / 值改變 的格數與月份分布。
  report      彙整成 ml/reports/ab_quarterly_fundamentals_backfill_<日期>.json / .md 的數據部分。

官方來源 = scripts/scan_fundamentals_defects.py fetch 的研究快取(MOPS t163sb04/05/06,正式抓取的同一端點與解析器)。
用法:python -X utf8 scripts/ab_quarterly_fundamentals_backfill.py {stage|spot-check|ingest-test|live-ab|history}
"""
from __future__ import annotations

import io
import json
import math
import random
import re
import sys
import time
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

import scan_fundamentals_defects as scan  # noqa: E402

OFFICIAL = scan.CACHE_DIR
STAGE_ROOT = REPO / "ml" / "reports" / "cache" / "fundamentals_backfill_stage"
STAGE = {"eps": STAGE_ROOT / "季報財務", "financial": STAGE_ROOT / "季報財務", "bs": STAGE_ROOT / "資產負債"}
LOCAL = {"eps": scan.FIN_DIR, "financial": scan.FIN_DIR, "bs": scan.BS_DIR}
WORK = STAGE_ROOT / "_work"
SEED = 20261017
AS_OF = "2026-10-16"


def _num_cols(kind: str) -> list[str]:
    cols = list(scan.KINDS[kind][3])
    return cols


def _equal(a, b, tol: float) -> bool:
    if pd.isna(a) and pd.isna(b):
        return True
    if pd.isna(a) or pd.isna(b):
        return False
    return abs(float(a) - float(b)) <= tol


def stage() -> dict:
    """建暫存並做硬停 (2)。回傳逐類統計與「新增/改正」清單。"""
    for d in set(STAGE.values()) | {WORK}:
        d.mkdir(parents=True, exist_ok=True)
    summary, changes = {}, []
    identity_fail = []
    for kind in scan.KINDS:
        prefix = scan.KINDS[kind][1]
        metrics = _num_cols(kind)
        k = {"added": 0, "corrected": 0, "name_only": 0, "retained_local_only": 0, "unchanged": 0, "files_changed": 0,
             "tiny_diff_cells": 0}
        for yq in scan.all_quarters():
            name = f"{prefix}{scan.yq_label(yq)}.csv"
            local = pd.read_csv(LOCAL[kind] / name, dtype={"Ticker": str})
            off_path = OFFICIAL / name
            if not off_path.exists():
                raise SystemExit(f"官方快取缺 {name},停止")
            off = pd.read_csv(off_path, dtype={"Ticker": str})
            if kind == "bs":
                off["Debt_Ratio"] = (off.Total_Liabilities / off.Total_Assets.replace(0, np.nan) * 100).round(2)
                off["Current_Ratio"] = (off.Current_Assets / off.Current_Liabilities.replace(0, np.nan) * 100).round(2)
            missing_cols = [c for c in local.columns if c not in off.columns]
            if missing_cols:
                raise SystemExit(f"{name} 官方缺欄位 {missing_cols},停止")
            off = off[local.columns]
            lidx, oidx = local.set_index("Ticker"), off.set_index("Ticker")
            retained = local[~local.Ticker.isin(off.Ticker)]
            staged = pd.concat([off, retained], ignore_index=True).sort_values("Ticker").reset_index(drop=True)
            file_changed = False
            corrected_here = set()
            for t in off.Ticker:
                if t not in lidx.index:
                    k["added"] += 1
                    file_changed = True
                    changes.append({"kind": kind, "yq": yq, "Ticker": t, "type": "added"})
                    continue
                lr, orow = lidx.loc[t], oidx.loc[t]
                diff_cols = [c for c in metrics if not _equal(lr[c], orow[c], scan._tol(c))]
                if diff_cols:
                    k["corrected"] += 1
                    corrected_here.add(t)
                    file_changed = True
                    changes.append({"kind": kind, "yq": yq, "Ticker": t, "type": "corrected", "cols": diff_cols,
                                    "local": {c: lr[c] for c in diff_cols}, "official": {c: orow[c] for c in diff_cols}})
                    continue
                tiny = [c for c in metrics if not (pd.isna(lr[c]) and pd.isna(orow[c])) and lr[c] != orow[c]]
                k["tiny_diff_cells"] += len(tiny)
                if str(lr["Name"]) != str(orow["Name"]):
                    k["name_only"] += 1
                    file_changed = True
                else:
                    k["unchanged"] += 1
            k["retained_local_only"] += len(retained)
            # 硬停 (2):除了改正列,本機每一列在暫存中逐格相同(容差內;保留列為原樣複製)
            sidx = staged.set_index("Ticker")
            for t in local.Ticker:
                if t in corrected_here:
                    continue
                for c in metrics:
                    if not _equal(lidx.loc[t, c], sidx.loc[t, c], scan._tol(c)):
                        identity_fail.append(f"{kind}:{name}:{t}:{c}")
            for t in retained.Ticker:
                if not lidx.loc[t].equals(sidx.loc[t]):
                    identity_fail.append(f"{kind}:{name}:{t}:retained_row_differs")
            if file_changed:
                k["files_changed"] += 1
            staged.to_csv(STAGE[kind] / name, index=False, encoding="utf-8-sig")
        summary[kind] = k
    out = {"summary": summary, "identity_check_failures": identity_fail[:50], "identity_check_pass": not identity_fail,
           "changes": changes}
    (WORK / "stage.json").write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(json.dumps({"summary": summary, "identity_check_pass": not identity_fail,
                      "identity_failures": len(identity_fail)}, ensure_ascii=False, indent=1))
    if identity_fail:
        raise SystemExit("硬停 (2):未改列與本機不同,停止")
    return out


# ---------------------------------------------------------------------------
# 硬停 (1):單一公司查詢
# ---------------------------------------------------------------------------
def _single_company(endpoint: str, co: str, yq: int) -> str:
    import requests
    import urllib3
    urllib3.disable_warnings()
    y, q = yq // 10, yq % 10
    payload = {"encodeURIComponent": "1", "step": "1", "firstin": "1", "off": "1", "keyword4": "", "code1": "",
               "TYPEK2": "", "checkbtn": "", "queryName": "co_id", "inpuType": "co_id", "TYPEK": "all",
               "isnew": "false", "co_id": co, "year": str(y - 1911), "season": f"{q:02d}"}
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
               "Referer": f"https://mopsov.twse.com.tw/mops/web/{endpoint}"}
    for attempt in range(3):
        try:
            r = requests.post(f"https://mopsov.twse.com.tw/mops/web/ajax_{endpoint}", data=payload, headers=headers,
                              timeout=60, verify=False)
            r.encoding = "utf-8"
            if r.status_code == 200 and "查詢過於頻繁" not in r.text:
                return r.text
        except Exception as exc:  # noqa: BLE001
            print(f"  retry {attempt+1}: {exc}", flush=True)
        time.sleep(15)
    return ""


def _rows(html: str) -> tuple[list[str], list[list[str]]]:
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html or "", "lxml")
    header, rows = [], []
    for tr in soup.find_all("tr"):
        cells = [x.get_text(strip=True) for x in tr.find_all(["td", "th"])]
        if not cells:
            continue
        if cells[0] == "會計項目" and not header:
            header = cells
        rows.append(cells)
    return header, rows


def _value(rows: list, header: list, labels: tuple, pct: bool = False) -> float:
    """取「年初至今」那一組的數值(Q1/Q4 只有當期組時取第一組);pct=True 取該組的百分比欄。"""
    group = 1
    for h, text in enumerate(header):
        if "01月01日至" in text:
            group = h
            break
    col = 1 + 2 * (group - 1) + (1 if pct else 0)
    for cells in rows:
        if cells and any(cells[0].startswith(lb) for lb in labels) and len(cells) > col and cells[col] not in ("", "--"):
            try:
                return float(cells[col].replace(",", ""))
            except ValueError:
                continue
    return math.nan


def spot_check() -> dict:
    st = json.loads((WORK / "stage.json").read_text(encoding="utf-8"))
    pool = sorted({(c["kind"], c["yq"], c["Ticker"]) for c in st["changes"]})
    picks = random.Random(SEED).sample(pool, min(20, len(pool)))
    raw = WORK / "single_company"
    raw.mkdir(parents=True, exist_ok=True)
    results, agree = [], 0
    for kind, yq, t in picks:
        staged = pd.read_csv(STAGE[kind] / f"{scan.KINDS[kind][1]}{scan.yq_label(yq)}.csv", dtype={"Ticker": str})
        row = staged[staged.Ticker == t].iloc[0]
        endpoint = "t164sb03" if kind == "bs" else "t164sb04"
        html = _single_company(endpoint, t, yq)
        (raw / f"{endpoint}_{t}_{yq}.html").write_text(html, encoding="utf-8")
        header, rows = _rows(html)
        if kind == "eps":
            checks = [("EPS_Basic", row.EPS_Basic, _value(rows, header, ("基本每股盈餘",)), 0.005)]
        elif kind == "financial":
            rev = _value(rows, header, ("營業收入合計", "收入合計", "淨收益"))
            checks = [("Revenue_M", row.Revenue_M, rev / 1000 if not math.isnan(rev) else math.nan, 0.01),
                      ("Operating_Margin_Pct", row.Operating_Margin_Pct,
                       _value(rows, header, ("營業利益", "營業淨利"), pct=True), 0.01)]
        else:
            checks = [("Total_Assets", row.Total_Assets, _value(rows, header, ("資產總計", "資產總額")), 1.0),
                      ("Total_Equity", row.Total_Equity, _value(rows, header, ("權益總計", "權益總額")), 1.0)]
        ok = all(_equal(a, b, tol) for _, a, b, tol in checks)
        agree += ok
        results.append({"kind": kind, "yq": scan.yq_label(yq), "Ticker": t, "ok": ok,
                        "checks": [[c, float(a) if pd.notna(a) else None, None if math.isnan(b) else b] for c, a, b, _ in checks]})
        print(f"{kind} {t} {scan.yq_label(yq)} -> {'OK' if ok else 'MISMATCH'} {results[-1]['checks']}", flush=True)
        time.sleep(6)
    out = {"n": len(picks), "agree": agree, "pass": agree >= 19, "rows": results}
    (WORK / "spot_check.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"spot check {agree}/{len(picks)} -> {'PASS' if agree >= 19 else 'STOP'}")
    return out


# ---------------------------------------------------------------------------
# 硬停 (3):正式匯入 SQL 實測(記憶體內 DuckDB)
# ---------------------------------------------------------------------------
def ingest_test() -> dict:
    import duckdb

    def run(directory: Path) -> dict:
        files = sorted(str(p).replace("\\", "/") for p in directory.glob("financial_*.csv"))
        con = duckdb.connect()
        try:
            q = f"SELECT * FROM read_csv_auto({files}, header=true, union_by_name=true)"
            con.execute(f"CREATE TABLE financials AS {q}")
            r = con.execute("""SELECT count(*), count(DISTINCT CAST(Ticker AS VARCHAR)),
                               sum(CASE WHEN CAST(Ticker AS VARCHAR) LIKE '%.%' THEN 1 ELSE 0 END),
                               max(Year * 10 + Season), any_value(typeof(Ticker)) FROM financials""").fetchone()
            cols = [c[0] for c in con.execute("DESCRIBE financials").fetchall()]
        finally:
            con.close()
        per_file = {}
        for f in files:
            c2 = duckdb.connect()
            try:
                per_file[Path(f).name] = c2.execute(
                    f"SELECT any_value(typeof(Ticker)) FROM read_csv_auto('{f}', header=true)").fetchone()[0]
            finally:
                c2.close()
        return {"rows": r[0], "tickers": r[1], "decimal_tickers": int(r[2] or 0), "max_period": r[3],
                "ticker_type": r[4], "columns": cols, "per_file_ticker_types": sorted(set(per_file.values()))}

    a, b = run(scan.FIN_DIR), run(STAGE["financial"])
    checks = {"rows_not_shrinking": b["rows"] >= a["rows"], "tickers_not_shrinking": b["tickers"] >= a["tickers"],
              "no_decimal_tickers": b["decimal_tickers"] == 0, "freshness_not_regressing": b["max_period"] >= a["max_period"],
              "same_columns": a["columns"] == b["columns"]}
    out = {"current": a, "staged": b, "checks": checks, "pass": all(checks.values())}
    (WORK / "ingest_test.json").write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=1, default=str))
    return out


# ---------------------------------------------------------------------------
# 固定快照 A/B
# ---------------------------------------------------------------------------
QFEATS = None


def _qfeats() -> list[str]:
    from ml.features.balance_sheet import BALANCE_SHEET_FEATURE_COLS
    from ml.features.eps import EPS_FEATURE_COLS
    from ml.features.fundamental import FUNDAMENTAL_FEATURE_COLS
    return [*EPS_FEATURE_COLS, *FUNDAMENTAL_FEATURE_COLS, *BALANCE_SHEET_FEATURE_COLS]


def live_ab() -> dict:
    import ml.dataset as ds
    import ml.predict as predict
    from ab_live_universe_exclusion import _postprocess, _score

    feats = _qfeats()
    affected = set(json.loads((REPO / "ml" / "reports" / "fundamentals_defect_scan_20261017.json")
                              .read_text(encoding="utf-8"))["live_feature_impact"]["tickers"])
    twii = ds._load_twii()
    tickers = ds._list_daily_tickers()
    orig_fin, orig_bs = ds.FINANCIAL_DIR, ds.compute_balance_sheet_features

    def latest_rows(fin_dir: Path, bs_dir: Path) -> dict[str, pd.DataFrame]:
        ds.FINANCIAL_DIR = str(fin_dir)
        ds.compute_balance_sheet_features = lambda df, t, _bs, fin: orig_bs(df, t, str(bs_dir), fin)
        rows = {}
        for tk in tickers:
            df = ds.load_single_stock(tk, twii_df=twii, eligibility_mode="latest")
            if df is not None and len(df):
                rows[tk] = df.iloc[[-1]].copy()
        return rows

    try:
        t0 = time.time()
        ra = latest_rows(scan.FIN_DIR, scan.BS_DIR)
        print(f"A rows {len(ra)} in {time.time() - t0:.0f}s", flush=True)
        rb = latest_rows(STAGE["financial"], STAGE["bs"])
        print(f"B rows {len(rb)} in {time.time() - t0:.0f}s", flush=True)
    finally:
        ds.FINANCIAL_DIR, ds.compute_balance_sheet_features = orig_fin, orig_bs
    feat_diff = []
    for tk in ra:
        if tk in rb:
            for f in feats:
                a = ra[tk][f].iloc[0] if f in ra[tk] else np.nan
                b = rb[tk][f].iloc[0] if f in rb[tk] else np.nan
                if not ((pd.isna(a) and pd.isna(b)) or (not pd.isna(a) and not pd.isna(b) and abs(a - b) < 1e-6)):
                    feat_diff.append([tk, f, None if pd.isna(a) else float(a), None if pd.isna(b) else float(b)])
    pa, pb = _score(_postprocess(list(ra.values()))), _score(_postprocess(list(rb.values())))
    m = pa.merge(pb, on=["ticker", "date"], suffixes=("_a", "_b"))
    changed_feat_tickers = sorted({r[0] for r in feat_diff})
    out = {"rows_a": len(pa), "rows_b": len(pb), "joined": len(m), "latest_feature_cells_changed": len(feat_diff),
           "latest_feature_changed_tickers": changed_feat_tickers,
           "feature_changed_outside_expected_48": sorted(set(changed_feat_tickers) - affected),
           "expected_48_without_feature_change": sorted(affected - set(changed_feat_tickers)),
           "latest_feature_changes": feat_diff[:80]}
    for col in ("up_prob", "pred_return_20d", "prob_edge"):
        if f"{col}_a" in m:
            d = (pd.to_numeric(m[f"{col}_b"], errors="coerce") - pd.to_numeric(m[f"{col}_a"], errors="coerce")).abs()
            inside = m["ticker"].isin(affected)
            out[col] = {"max_abs_delta": float(d.max()), "mean_abs_delta": float(d.mean()),
                        "rows_changed": int((d > 1e-9).sum()),
                        "affected_rows_changed": int((d[inside] > 1e-9).sum()),
                        "affected_max_abs_delta": float(d[inside].max()) if inside.any() else 0.0,
                        "others_rows_changed": int((d[~inside] > 1e-9).sum()),
                        "others_max_abs_delta": float(d[~inside].max()) if (~inside).any() else 0.0}
            top = m.assign(_d=d)[d > 1e-9].sort_values("_d", ascending=False)
            out[col]["top_changes"] = top[["ticker", f"{col}_a", f"{col}_b"]].head(40).values.tolist()
    if "recommendation_a" in m:
        ch = m[m["recommendation_a"] != m["recommendation_b"]]
        out["recommendation_changes"] = ch[["ticker", "recommendation_a", "recommendation_b"]].values.tolist()
    ta = predict.apply_sector_cap(pa, top_n=30)["ticker"].tolist()
    tb = predict.apply_sector_cap(pb, top_n=30)["ticker"].tolist()
    out["top30_out"], out["top30_in"] = [t for t in ta if t not in tb], [t for t in tb if t not in ta]
    canonical = pa["date"].astype(str).value_counts().idxmax()
    prod = REPO / "ml" / "models" / f"predictions_{canonical}.csv"
    if prod.exists():
        p = pd.read_csv(prod, dtype={"ticker": str})
        j = pa.merge(p, on=["ticker", "date"], suffixes=("", "_prod"))
        out["baseline_sanity"] = {"production_file": prod.name, "joined": len(j),
                                  "up_prob_max_abs_delta": float((j["up_prob"] - j["up_prob_prod"]).abs().max())}
    pa.to_csv(WORK / "live_ab_pred_a.csv", index=False, encoding="utf-8-sig")
    pb.to_csv(WORK / "live_ab_pred_b.csv", index=False, encoding="utf-8-sig")
    (WORK / "live_ab.json").write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(json.dumps({k: v for k, v in out.items() if k not in ("latest_feature_changes",)}, ensure_ascii=False,
                     indent=1, default=str)[:6000])
    return out


def history() -> dict:
    from ml.features.balance_sheet import compute_balance_sheet_features
    from ml.features.eps import compute_eps_features
    from ml.features.fundamental import compute_fundamental_features

    st = json.loads((WORK / "stage.json").read_text(encoding="utf-8"))
    tickers = sorted({c["Ticker"] for c in st["changes"]})
    feats = _qfeats()
    n2v = chg = total = 0
    by_month: dict[str, int] = {}
    per_ticker = {}
    for tk in tickers:
        kp = REPO / "日K資料" / f"{tk}.csv"
        if not kp.exists():
            continue
        k = pd.read_csv(kp, usecols=["Date"])
        k = k[k["Date"].astype(str) >= "2020-01-01"]

        def feats_for(fin: Path, bs: Path) -> pd.DataFrame:
            x = compute_eps_features(k, tk, str(fin))
            x = compute_fundamental_features(x, tk, str(fin))
            return compute_balance_sheet_features(x, tk, str(bs), str(fin))

        a, b = feats_for(scan.FIN_DIR, scan.BS_DIR), feats_for(STAGE["financial"], STAGE["bs"])
        total += len(k)
        tn = tc = 0
        for f in feats:
            av, bv = a[f].to_numpy(float), b[f].to_numpy(float)
            m1 = np.isnan(av) & ~np.isnan(bv)
            m2 = ~np.isnan(av) & ~np.isnan(bv) & (np.abs(av - bv) > 1e-6)
            tn, tc = tn + int(m1.sum()), tc + int(m2.sum())
            for d in a.loc[m1 | m2, "Date"].dt.strftime("%Y-%m"):
                by_month[d] = by_month.get(d, 0) + 1
        n2v, chg = n2v + tn, chg + tc
        per_ticker[tk] = {"nan_to_value": tn, "value_changed": tc}
    out = {"tickers": len(tickers), "daily_rows": total, "feature_cells_nan_to_value": n2v,
           "feature_cells_value_changed": chg, "by_month": dict(sorted(by_month.items())), "per_ticker": per_ticker}
    (WORK / "history.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in out.items() if k != "per_ticker"}, ensure_ascii=False, indent=1)[:3000])
    return out


if __name__ == "__main__":
    {"stage": stage, "spot-check": spot_check, "ingest-test": ingest_test, "live-ab": live_ab,
     "history": history}[sys.argv[1]]()
