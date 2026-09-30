# -*- coding: utf-8 -*-
"""Shadow-lite:乾淨資料新 Stage1(DATA-PR-009)每日影子選股帳本。

背景:2026-07-02 乾淨重訓 CL3 FAIL 不升級,但新 Stage1 holdout 明顯較好
(-0.017 vs 舊 -0.054)。本 shadow 以活資料驗證:每晚用新 Stage1 對當日
snapshot 打分記 Top30;check 對已滿 20 交易日者計 forward 報酬,並與
champion 當日 predictions Top30 對比 → 累積證據供日後升級決策。
不動 production 任何檔案;僅讀 snapshot_cache.pkl 與 predictions CSV。

用法:
  python -X utf8 scripts/shadow_dataA_tracker.py record
  python -X utf8 scripts/shadow_dataA_tracker.py check
"""
from __future__ import annotations

import argparse
import glob
import os
import re
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))

SHADOW_META = BASE_DIR / "ml" / "models" / "lgbm_v2_20260702_131745_meta.json"
SNAPSHOT_PKL = BASE_DIR / "ml" / "models" / "snapshot_cache.pkl"
LEDGER = BASE_DIR / "ml" / "reports" / "shadow_dataA_ledger.csv"
REPORT = BASE_DIR / "ml" / "reports" / "shadow_dataA_tracking_latest.md"
DAILY = BASE_DIR / "日K資料"
TOP_N = 30
HOLD = 20


def _load_ledger() -> pd.DataFrame:
    if LEDGER.exists():
        d = pd.read_csv(LEDGER, dtype={"ticker": str})
        d["ticker"] = d["ticker"].str.zfill(4)
        return d
    return pd.DataFrame(columns=["date", "side", "rank", "ticker", "score", "ret20_pct"])


def score_snapshot(snapshot: pd.DataFrame, meta: dict, booster) -> pd.DataFrame:
    """同一 raw frame 推論；保留個股日期，不能用批次最大日蓋掉停牌舊列。"""
    from ml.dataset import apply_snapshot_zscore
    if not {"ticker", "Date"}.issubset(snapshot.columns) or snapshot.empty:
        raise ValueError("snapshot requires ticker and Date")
    normalized = snapshot["ticker"].astype(str).str.zfill(4)
    if normalized.duplicated().any() or pd.to_datetime(snapshot["Date"], errors="coerce").isna().any():
        raise ValueError("snapshot ticker/date is ambiguous")
    missing = sorted(set(meta["feature_columns"]) - set(snapshot.columns))
    if missing:
        raise ValueError(f"snapshot is missing model feature columns: {missing}")
    zs = apply_snapshot_zscore(snapshot) if meta.get("cross_sectional_zscore") else snapshot
    pred = booster.predict(zs.reindex(columns=meta["feature_columns"]).astype(np.float32))
    if len(pred) != len(snapshot) or not np.isfinite(pred).all():
        raise ValueError("model scores are incomplete or nonfinite")
    return pd.DataFrame({"ticker": snapshot["ticker"].astype(str).str.zfill(4),
                         "source_date": pd.to_datetime(snapshot["Date"]).dt.strftime("%Y-%m-%d"),
                         "pred_return_20d": pred,
                         "operating_margin_latest": snapshot.get("operating_margin_latest", np.nan)})


def _atomic_csv(frame: pd.DataFrame, path: Path) -> None:
    from tempfile import NamedTemporaryFile
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(mode="w", encoding="utf-8-sig", newline="", dir=path.parent, delete=False, suffix=".tmp") as handle:
        frame.to_csv(handle, index=False, lineterminator="\n")
        temporary = handle.name
    os.replace(temporary, path)


def record(*, predictions_only: bool = False, refresh_snapshot: bool = False, expected_asof: str | None = None) -> None:
    import json
    import lightgbm as lgb
    from ml.dataset import build_latest_snapshot
    from ml.snapshot_lineage import load_snapshot, clear_source_memo_caches
    from ml.prediction_provenance import capture_run, bind_snapshot, assert_run_unchanged, publish_prediction_csv

    # Resolve the actual fixed model path before capture; read scoring metadata
    # again inside the captured run so a concurrent replacement is rejected.
    model_name = Path(json.loads(SHADOW_META.read_text(encoding="utf-8"))["model_file"]).name
    model_path = BASE_DIR / "ml" / "models" / model_name
    provenance = capture_run("dataA", model_paths={"dataA": {
        "meta_path": str(SHADOW_META), "model_path": str(model_path)}})
    meta = json.loads(SHADOW_META.read_text(encoding="utf-8"))
    if Path(meta["model_file"]).name != model_name:
        raise ValueError("DataA model metadata changed during capture")
    snapshot_manifest = None
    if refresh_snapshot:
        # 夜間 ingest 後只建立本次 shadow 的 raw frame，不覆寫 Champion 快照。
        clear_source_memo_caches()
        snapshot = build_latest_snapshot(verbose=False)
    else:
        snapshot_manifest_path = Path(str(SNAPSHOT_PKL) + ".manifest.json")
        snapshot_manifest_bytes = snapshot_manifest_path.read_bytes()
        snapshot = load_snapshot(SNAPSHOT_PKL)
        if snapshot is None:
            raise ValueError("DataA canonical snapshot lineage rejected or absent")
        if snapshot_manifest_path.read_bytes() != snapshot_manifest_bytes:
            raise ValueError("DataA snapshot manifest changed during loading")
        snapshot_manifest = json.loads(snapshot_manifest_bytes)
    if "Date" not in snapshot or snapshot.empty:
        raise ValueError("snapshot has no usable date")
    snap_date = pd.to_datetime(snapshot["Date"]).max().date().isoformat()
    if expected_asof and snap_date != expected_asof:
        raise ValueError(f"snapshot date {snap_date} != required {expected_asof}")

    provenance = bind_snapshot(provenance, snapshot, snapshot_manifest=snapshot_manifest)
    led = _load_ledger()
    booster = lgb.Booster(model_file=str(model_path))
    assert_run_unchanged(provenance)
    full = score_snapshot(snapshot, meta, booster)
    # 全 universe 分數輸出:canonical 產生器的模型確認來源(PM 2026-07-02 裁示:
    # 日常名單改用新模型+rere lane;champion 帳本與其 pin 不動,對決帳本續跑)
    full_path = BASE_DIR / "ml" / "models" / f"dataA_predictions_{snap_date}.csv"
    publish_prediction_csv(full, full_path, provenance, csv_kwargs={"lineterminator": "\n"})
    print(f"[shadow-dataA] predictions {snap_date}: {len(full)} rows")
    if predictions_only:
        return
    out = full[full["source_date"] == snap_date].rename(columns={"pred_return_20d": "score"})
    out = out.sort_values("score", ascending=False).head(TOP_N).reset_index(drop=True)
    if not ((led["date"] == snap_date) & (led["side"] == "shadow")).any():
        for i, r in out.iterrows():
            led.loc[len(led)] = {"date": snap_date, "side": "shadow", "rank": i + 1,
                                 "ticker": r["ticker"], "score": round(float(r["score"]), 5), "ret20_pct": np.nan}

    # champion 同日 Top30(直接取當日 predictions 排名前 30 之買進池,對照用)
    champion_path = BASE_DIR / "ml" / "models" / f"predictions_{snap_date}.csv"
    if champion_path.exists():
        ch = pd.read_csv(champion_path)
        ch["ticker"] = ch["ticker"].astype(str).str.zfill(4)
        date_map = dict(zip(full["ticker"], full["source_date"]))
        ch = ch[ch["ticker"].map(date_map).eq(snap_date)]
        ch = ch.sort_values("pred_return_20d", ascending=False).head(TOP_N).reset_index(drop=True)
        if not ((led["date"] == snap_date) & (led["side"] == "champion")).any():
            for i, r in ch.iterrows():
                led.loc[len(led)] = {"date": snap_date, "side": "champion", "rank": i + 1,
                                     "ticker": r["ticker"], "score": round(float(r["pred_return_20d"]), 5),
                                     "ret20_pct": np.nan}
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    _atomic_csv(led, LEDGER)
    print(f"[shadow-dataA] record {snap_date}: champion same-date available={champion_path.exists()}")


def check() -> None:
    # Keep legacy ledger returns as provenance; do not mix target conventions.
    # The canonical paired evaluator requires complete baskets and reports costs.
    from scripts.evaluate_shadow_dataA_integrity import run
    from scripts.taiwan_trading_calendar import previous_taiwan_trading_day, is_taiwan_trading_day
    today = datetime.now()
    as_of = today.date() if is_taiwan_trading_day(today) and today.hour >= 18 else previous_taiwan_trading_day(today)
    output = REPORT.parent / "shadow_dataA_evaluation_latest"
    result = run(as_of.isoformat(), output)
    temporary = REPORT.with_suffix(".tmp")
    temporary.write_text((output / "shadow_target_replay.md").read_text(encoding="utf-8"), encoding="utf-8")
    os.replace(temporary, REPORT)
    print(f"[shadow-dataA] paired evaluation: {result['paired_dates']} complete dates -> {REPORT.name}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("mode", choices=["record", "check"])
    ap.add_argument("--predictions-only", action="store_true", help="只重建分數，不寫帳本")
    ap.add_argument("--refresh-snapshot", action="store_true", help="ingest後建立shadow専用最新frame")
    ap.add_argument("--as-of", help="必須匹配的資料收盤日 YYYY-MM-DD")
    a = ap.parse_args()
    if a.mode == "record":
        record(predictions_only=a.predictions_only, refresh_snapshot=a.refresh_snapshot, expected_asof=a.as_of)
    else:
        check()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
