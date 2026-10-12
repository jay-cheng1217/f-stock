# -*- coding: utf-8 -*-
"""AB-live-universe-exclusion:即時推論股票池排除「不能交易的列」的固定快照 A/B(2026-10-12,PM 核可「做吧」)。

同一批逐檔最新列(只建一次)→ 三種股票池各自做完全相同的截面後處理 → 同一組正式模型評分 → 以 ticker/date 對齊比較。
  A 現況:全部列(= 今天 07:51 的正式快照)。
  B 第 1 項:扣掉官方名冊 config/trading_status_events.json 已生效且已可得知的「終止上市櫃」。
  C 第 3 項候選:扣掉資料日不等於整批資料日的所有列(終止、減資停牌、長期停牌、名冊未登錄的停更)。
報告:被拿掉的列、其餘列的 up_prob / pred_return_20d 差異(截面百分位、z-score 會因母體變小而微動)、
建議分類變動、Top30(含產業上限)名單變動。唯讀:不寫快照、不寫預測檔、不寫帳本。

用法(在主工作目錄跑,讀正式資料與模型):
  python -X utf8 scripts/ab_live_universe_exclusion.py --out-dir ml/reports
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
BASE = Path(os.environ.get("STOCK_BASE_DIR") or Path(__file__).resolve().parents[1])
sys.path.insert(0, str(BASE))

import ml.dataset as ds  # noqa: E402
import ml.predict as predict  # noqa: E402


def _terminated(as_of: date) -> set[str]:
    reg = json.loads((BASE / "config" / "trading_status_events.json").read_text(encoding="utf-8"))
    day = as_of.isoformat()
    out = set()
    for e in reg["events"]:
        if e["status"] == "terminated" and e["available_on"] <= day and e["effective_from"] <= day and (
                not e.get("effective_until") or day < e["effective_until"]):
            out.add(str(e["ticker"]))
    return out


def _postprocess(rows: list[pd.DataFrame]) -> pd.DataFrame:
    """與 ml.dataset.build_latest_snapshot 的後處理逐行相同(截面百分位 → 產業 → 產業聚合 → ATR 排名 → 體制 → 截斷)。"""
    snapshot = pd.concat(rows, ignore_index=True).sort_values(["Date", "ticker"]).reset_index(drop=True)
    snapshot = ds._apply_percentile_ranking(snapshot)
    available = ds.get_available_features()
    if "sector" in available:
        snapshot = ds.compute_sector_features(snapshot)
    snapshot = ds.compute_industry_features(snapshot)
    snapshot["atr_pct_rank"] = (snapshot["atr_pct"].rank(pct=True).astype(np.float32)
                                if "atr_pct" in snapshot.columns else np.nan)
    if "market_regime" in available:
        snapshot = ds.add_market_regime_features(snapshot)
    snapshot = ds._winsorize_features(snapshot)
    snapshot.attrs["zscore_applied"] = False
    return snapshot


def _score(snapshot: pd.DataFrame) -> pd.DataFrame:
    original = predict.build_latest_snapshot
    predict.build_latest_snapshot = lambda verbose=False: snapshot.copy()
    try:
        pred, _ = predict.predict_all(top_n=30, model_slot="production", save_snapshot=False, capture_provenance=False)
    finally:
        predict.build_latest_snapshot = original
    pred["ticker"] = pred["ticker"].astype(str)
    return pred


def _compare(a: pd.DataFrame, b: pd.DataFrame, label: str) -> dict:
    keys = ["ticker", "date"]
    m = a.merge(b, on=keys, how="outer", suffixes=("_a", "_b"), indicator=True)
    removed = m[m["_merge"] == "left_only"]
    both = m[m["_merge"] == "both"]
    out = {"variant": label, "rows_a": len(a), "rows_b": len(b), "removed": removed["ticker"].tolist(),
           "added": m[m["_merge"] == "right_only"]["ticker"].tolist()}
    for col in ("up_prob", "pred_return_20d", "prob_edge"):
        if f"{col}_a" in both.columns:
            d = (pd.to_numeric(both[f"{col}_b"], errors="coerce") - pd.to_numeric(both[f"{col}_a"], errors="coerce")).abs()
            out[f"{col}_max_abs_delta"] = float(d.max())
            out[f"{col}_mean_abs_delta"] = float(d.mean())
    if "recommendation_a" in both.columns:
        changed = both[both["recommendation_a"] != both["recommendation_b"]]
        out["recommendation_changes"] = changed[["ticker", "recommendation_a", "recommendation_b"]].values.tolist()
    top_a = predict.apply_sector_cap(a, top_n=30)["ticker"].tolist() if "pred_return_20d" in a.columns else a.head(30).ticker.tolist()
    top_b = predict.apply_sector_cap(b, top_n=30)["ticker"].tolist() if "pred_return_20d" in b.columns else b.head(30).ticker.tolist()
    out["top30_out"] = [t for t in top_a if t not in top_b]
    out["top30_in"] = [t for t in top_b if t not in top_a]
    out["removed_detail"] = removed[["ticker", "date"] + [c for c in ("recommendation_a", "up_prob_a") if c in removed.columns]].values.tolist()
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default=str(BASE / "ml" / "reports"))
    ap.add_argument("--as-of", default=date.today().isoformat())
    args = ap.parse_args()
    as_of = date.fromisoformat(args.as_of)

    twii = ds._load_twii()
    rows: dict[str, pd.DataFrame] = {}
    for tk in ds._list_daily_tickers():
        df = ds.load_single_stock(tk, twii_df=twii, eligibility_mode="latest")
        if df is not None and len(df):
            rows[tk] = df.iloc[[-1]].copy()
    dates = pd.Series({tk: pd.Timestamp(r["Date"].iloc[0]).strftime("%Y-%m-%d") for tk, r in rows.items()})
    canonical = dates.value_counts().idxmax()
    terminated = _terminated(as_of)
    variants = {
        "A_current": list(rows),
        "B_drop_terminated": [tk for tk in rows if tk not in terminated],
        "C_drop_all_stale_rows": [tk for tk in rows if dates[tk] == canonical],
    }
    print(f"latest rows {len(rows)} | canonical date {canonical} | stale rows {sorted((dates[dates != canonical]).items())}")
    preds = {name: _score(_postprocess([rows[tk] for tk in tks])) for name, tks in variants.items()}

    sanity = None
    prod_path = BASE / "ml" / "models" / f"predictions_{canonical}.csv"
    if prod_path.exists():
        prod = pd.read_csv(prod_path, dtype={"ticker": str})
        j = preds["A_current"].merge(prod, on=["ticker", "date"], suffixes=("", "_prod"))
        sanity = {"production_file": prod_path.name, "joined": len(j), "rows_prod": len(prod),
                  "up_prob_max_abs_delta": float((j["up_prob"] - j["up_prob_prod"]).abs().max())}
    report = {"as_of": as_of.isoformat(), "canonical_date": canonical, "terminated_registry": sorted(terminated),
              "stale_rows": {tk: d for tk, d in dates.items() if d != canonical}, "baseline_sanity": sanity,
              "comparisons": [_compare(preds["A_current"], preds["B_drop_terminated"], "B_drop_terminated"),
                              _compare(preds["A_current"], preds["C_drop_all_stale_rows"], "C_drop_all_stale_rows")]}
    out = Path(args.out_dir) / f"ab_live_universe_exclusion_{as_of:%Y%m%d}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "comparisons"}, ensure_ascii=False, default=str))
    for c in report["comparisons"]:
        print({k: v for k, v in c.items() if k != "removed_detail"})
        print("  removed:", c["removed_detail"])
    print("report:", out)


if __name__ == "__main__":
    main()
