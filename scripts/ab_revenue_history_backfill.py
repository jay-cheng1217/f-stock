# -*- coding: utf-8 -*-
"""AB-revenue-history-backfill:月營收歷史回補的轉檔、對帳與固定快照 A/B(2026-10-17 PM「做轉檔對帳和A/B,報告給我再決定」)。

背景:本機 月營收/ 1,988 檔中 1,246 檔建立於 2026-03-14/15(擴大股票池),建檔時只抓 2025-09 起,從未回補更早歷史;
模型營收特徵(ml/features/revenue.py)因此在 2025-09 前全為 NaN。來源改用 MOPS 全市場月報 t21sc03
(#36 研究快取 ml/reports/cache/revenue_t21sc03/)。

**本腳本全程不寫正式 月營收/、不寫快照/預測/帳本**:
  1. 轉檔:t21sc03 → 本機 CSV 同欄位(Date, Name, Monthly_Revenue, Cumulative_Revenue, YoY_pct_change, Cumulative_YoY_pct_change)。
  2. 對帳:t21sc03 與本機重疊的 (代號,月) 逐欄比對。
  3. 暫存:ml/reports/cache/revenue_backfill_stage/月營收/ —— 每個既有檔 = 本機原列(一列不改)+ 本機沒有的月份(2020-01 起);
     不新增本機沒有的代號(股票池一致性)。
  4. A/B:同一份日K、同一組正式模型,只把 REVENUE_DIR 指向正式目錄(A)或暫存目錄(B),建逐檔最新列 → 評分 → 對齊比較。
  5. 歷史影響:受影響股票全期營收特徵 A vs B,量化「NaN→有值」與「已有值但改變」的列數。

事前鎖定(2026-10-17,先於執行提交;見 docs/STRATEGY_overview.md「AB-revenue-history-backfill」):
  對帳門檻:營收數字相符(差 <= 0.5 千元)與年增率差 <= 0.01pp 皆 >= 99.0%,未達=停止,不產生暫存與 A/B;
  不一致全部逐筆輸出並分類交 PM 判斷。**揭露**:此門檻訂於 #36 已顯示「年增率差 <= 0.05pp 一致 99.76%」之後,
  所以不是盲設;它只擋「來源整體不可用」,是否可信由 PM 看不一致清單決定。
  A/B 報告指標:up_prob / pred_return_20d / prob_edge 最大與平均絕對差;建議分類變動;Top30(產業上限)名單變動;
  基準健全性(A 與正式 predictions 檔的 up_prob 最大差)。事前預期:即時預測差異 ≈ 0(特徵最多回看 4 個月,缺口股票
  2025-09 起資料齊全);若非 0 必須逐檔解釋。
  歷史影響指標:受影響檔數、日列中四個營收特徵「NaN→有值」列數與占比、「原本有值但改變」列數(預期集中在 2025-10~2026-01)。
本腳本不做判決;結果交 PM 決定是否寫入正式目錄(寫入另走備份+合併流程+重跑 Phase-2)。

用法(主工作目錄):python -X utf8 scripts/ab_revenue_history_backfill.py
輸出:ml/reports/ab_revenue_history_backfill_<日期>.json / .md
"""
from __future__ import annotations

import io
import json
import shutil
import sys
from datetime import date
from io import StringIO
from pathlib import Path

import numpy as np
import pandas as pd

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

T21 = REPO / "ml" / "reports" / "cache" / "revenue_t21sc03"
LOCAL = REPO / "月營收"
STAGE = REPO / "ml" / "reports" / "cache" / "revenue_backfill_stage" / "月營收"
OUT_DIR = REPO / "ml" / "reports"
COLS = ["Date", "Name", "Monthly_Revenue", "Cumulative_Revenue", "YoY_pct_change", "Cumulative_YoY_pct_change"]
FEATS = ["revenue_yoy_latest", "revenue_yoy_3m_avg", "revenue_yoy_momentum", "revenue_cumulative_yoy"]
FILL_FROM = "2020-01"


def parse_t21() -> pd.DataFrame:
    rows = []
    for fp in sorted(T21.glob("*.html")):
        ym = fp.stem.split("_")[1]
        try:
            tabs = pd.read_html(StringIO(fp.read_bytes().decode("big5", errors="replace")))
        except ValueError:
            continue
        for t in tabs:
            if t.shape[1] < 10:
                continue
            t = t.copy()
            t.columns = [" ".join(map(str, c)) if isinstance(c, tuple) else str(c) for c in t.columns]
            pick = lambda *keys: next((c for c in t.columns if all(k in c for k in keys)), None)  # noqa: E731
            cc = {"code": pick("代號"), "name": pick("公司名稱"), "rev": pick("當月營收"),
                  "yoy": pick("去年同月", "增減"), "cum": pick("當月累計營收"), "cumyoy": pick("前期比較", "增減")}
            if any(v is None for v in cc.values()):
                continue
            sub = pd.DataFrame({"ticker": t[cc["code"]].astype(str).str.strip(), "Name": t[cc["name"]].astype(str).str.strip(),
                                "Monthly_Revenue": pd.to_numeric(t[cc["rev"]], errors="coerce"),
                                "Cumulative_Revenue": pd.to_numeric(t[cc["cum"]], errors="coerce"),
                                "YoY_pct_change": pd.to_numeric(t[cc["yoy"]], errors="coerce"),
                                "Cumulative_YoY_pct_change": pd.to_numeric(t[cc["cumyoy"]], errors="coerce")})
            sub = sub[sub["ticker"].str.fullmatch(r"\d{4}") & sub["Monthly_Revenue"].notna()]
            sub["Date"] = f"{ym[:4]}-{ym[4:]}"
            rows.append(sub)
    return pd.concat(rows, ignore_index=True).drop_duplicates(["ticker", "Date"], keep="first")


def load_local() -> dict[str, pd.DataFrame]:
    out = {}
    for f in sorted(LOCAL.glob("revenue_*.csv")):
        d = pd.read_csv(f, dtype={"Date": str})
        d["Date"] = d["Date"].astype(str).str[:7]
        out[f.stem.split("_", 1)[1]] = d
    return out


def reconcile(T: pd.DataFrame, local: dict[str, pd.DataFrame]) -> dict:
    L = pd.concat([d.assign(ticker=tk) for tk, d in local.items()], ignore_index=True)
    J = L.merge(T, on=["ticker", "Date"], suffixes=("_l", "_t"))
    res = {"overlap_rows": len(J)}
    for col, tol in (("Monthly_Revenue", 0.5), ("Cumulative_Revenue", 0.5), ("YoY_pct_change", 0.01),
                     ("Cumulative_YoY_pct_change", 0.01)):
        a, b = pd.to_numeric(J[f"{col}_l"], errors="coerce"), pd.to_numeric(J[f"{col}_t"], errors="coerce")
        ok = ((a - b).abs() <= tol) | (a.isna() & b.isna())
        res[f"{col}_match_pct"] = round(float(ok.mean() * 100), 4)
        res[f"{col}_mismatch_n"] = int((~ok).sum())
        bad = J[~ok][["ticker", "Date", f"{col}_l", f"{col}_t"]]
        res[f"{col}_mismatch_examples"] = bad.head(30).astype(str).values.tolist()
        if col == "Monthly_Revenue" and len(bad):
            rel = ((a - b).abs() / b.abs().replace(0, np.nan))[~ok]
            res["revenue_mismatch_rel_diff"] = {"le_1pct": int((rel <= 0.01).sum()), "1_10pct": int(((rel > 0.01) & (rel <= 0.1)).sum()),
                                                "gt_10pct": int((rel > 0.1).sum()), "local_missing": int(a[~ok].isna().sum())}
            res["revenue_mismatch_by_year"] = bad["Date"].str[:4].value_counts().sort_index().to_dict()
    return res


def build_stage(T: pd.DataFrame, local: dict[str, pd.DataFrame]) -> dict:
    if STAGE.parent.exists():
        shutil.rmtree(STAGE.parent)
    STAGE.mkdir(parents=True)
    Tg = {tk: g for tk, g in T[T["Date"] >= FILL_FROM].groupby("ticker")}
    affected, added_rows = {}, 0
    for tk, d in local.items():
        add = Tg.get(tk)
        if add is not None:
            add = add[~add["Date"].isin(set(d["Date"]))]
        if add is not None and len(add):
            out = pd.concat([d[COLS], add[COLS]], ignore_index=True).sort_values("Date")
            affected[tk] = {"local_first": str(d["Date"].min()), "added": len(add), "new_first": str(out["Date"].min())}
            added_rows += len(add)
        else:
            out = d[COLS]
        out.to_csv(STAGE / f"revenue_{tk}.csv", index=False, encoding="utf-8-sig")
    return {"files": len(local), "affected_files": len(affected), "added_rows": added_rows, "affected": affected}


def live_ab(as_of: date) -> dict:
    import ml.dataset as ds
    import ml.predict as predict
    from ab_live_universe_exclusion import _postprocess, _score

    twii = ds._load_twii()
    tickers = ds._list_daily_tickers()

    def latest_rows(rev_dir: Path) -> dict[str, pd.DataFrame]:
        ds.REVENUE_DIR = str(rev_dir)
        rows = {}
        for tk in tickers:
            df = ds.load_single_stock(tk, twii_df=twii, eligibility_mode="latest")
            if df is not None and len(df):
                rows[tk] = df.iloc[[-1]].copy()
        return rows

    orig = ds.REVENUE_DIR
    try:
        ra, rb = latest_rows(LOCAL), latest_rows(STAGE)
    finally:
        ds.REVENUE_DIR = orig
    feat_diff = []
    for tk in ra:
        if tk in rb:
            for f in FEATS:
                a, b = ra[tk][f].iloc[0] if f in ra[tk] else np.nan, rb[tk][f].iloc[0] if f in rb[tk] else np.nan
                if not ((pd.isna(a) and pd.isna(b)) or (not pd.isna(a) and not pd.isna(b) and abs(a - b) < 1e-6)):
                    feat_diff.append([tk, f, None if pd.isna(a) else float(a), None if pd.isna(b) else float(b)])
    pa, pb = _score(_postprocess(list(ra.values()))), _score(_postprocess(list(rb.values())))
    m = pa.merge(pb, on=["ticker", "date"], suffixes=("_a", "_b"))
    out = {"rows_a": len(pa), "rows_b": len(pb), "joined": len(m), "latest_feature_cells_changed": len(feat_diff),
           "latest_feature_changes": feat_diff[:40]}
    for col in ("up_prob", "pred_return_20d", "prob_edge"):
        if f"{col}_a" in m:
            d = (pd.to_numeric(m[f"{col}_b"], errors="coerce") - pd.to_numeric(m[f"{col}_a"], errors="coerce")).abs()
            out[f"{col}_max_abs_delta"], out[f"{col}_mean_abs_delta"] = float(d.max()), float(d.mean())
            out[f"{col}_rows_changed"] = int((d > 1e-9).sum())
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
    return out


def history_impact(stage_info: dict) -> dict:
    from ml.features.revenue import compute_revenue_features

    na_to_val = changed = total = 0
    by_period: dict[str, int] = {}
    for tk in stage_info["affected"]:
        kp = REPO / "日K資料" / f"{tk}.csv"
        if not kp.exists():
            continue
        k = pd.read_csv(kp, usecols=["Date"])
        k = k[k["Date"].astype(str) >= "2020-01-01"]
        a = compute_revenue_features(k, pd.read_csv(LOCAL / f"revenue_{tk}.csv", dtype={"Date": str}))
        b = compute_revenue_features(k, pd.read_csv(STAGE / f"revenue_{tk}.csv", dtype={"Date": str}))
        total += len(k)
        for f in FEATS:
            av, bv = a[f].to_numpy(float), b[f].to_numpy(float)
            n2v = np.isnan(av) & ~np.isnan(bv)
            chg = ~np.isnan(av) & ~np.isnan(bv) & (np.abs(av - bv) > 1e-6)
            na_to_val += int(n2v.sum())
            changed += int(chg.sum())
            for d in a.loc[chg, "Date"].dt.strftime("%Y-%m"):
                by_period[d] = by_period.get(d, 0) + 1
    return {"daily_rows_affected_tickers": total, "feature_cells_nan_to_value": na_to_val,
            "feature_cells_value_changed": changed, "value_changed_by_month": dict(sorted(by_period.items()))}


def main() -> None:
    today = date.today()
    T = parse_t21()
    local = load_local()
    print(f"t21sc03 {T['ticker'].nunique()} 檔 {T['Date'].min()}~{T['Date'].max()} {len(T)} 列;本機 {len(local)} 檔")
    rec = reconcile(T, local)
    print("對帳:", {k: v for k, v in rec.items() if not k.endswith("examples")})
    report = {"date": today.isoformat(), "reconcile": rec}
    passed = rec["Monthly_Revenue_match_pct"] >= 99.0 and rec["YoY_pct_change_match_pct"] >= 99.0
    if not passed:
        report["verdict"] = "STOP: 對帳未達 99.0%,不產生暫存與 A/B"
        print(report["verdict"])
        for k, v in rec.items():
            if k.endswith("examples") and v:
                print(k, v[:8])
    else:
        st = build_stage(T, local)
        print(f"暫存:{st['files']} 檔,受影響 {st['affected_files']} 檔,補入 {st['added_rows']} 列 → {STAGE}")
        report["stage"] = {k: v for k, v in st.items() if k != "affected"}
        report["stage"]["affected_first_month_after"] = pd.Series(
            [v["new_first"] for v in st["affected"].values()]).value_counts().head(8).to_dict()
        report["live_ab"] = live_ab(today)
        print("即時 A/B:", {k: v for k, v in report["live_ab"].items() if k not in ("latest_feature_changes",)})
        report["history"] = history_impact(st)
        print("歷史影響:", {k: v for k, v in report["history"].items() if k != "value_changed_by_month"})
        print("  改變集中月份:", report["history"]["value_changed_by_month"])
    out = OUT_DIR / f"ab_revenue_history_backfill_{today:%Y%m%d}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print("report:", out)


if __name__ == "__main__":
    main()
