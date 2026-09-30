"""Fixed DuckDB-frame candidate/dataA A/B; no production writes or training."""
from __future__ import annotations
import argparse
import ast
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from hashlib import sha256
import json
from pathlib import Path
import pickle
import shutil
import subprocess
import sys
import types
import numpy as np
import pandas as pd
import requests

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
from scripts import generate_entry_candidates as new
from scripts.shadow_dataA_tracker import SHADOW_META, score_snapshot
from scripts.disposition_contract import load_disposition_gate


def run(output: Path, as_of: str, trade_day: str) -> dict:
    import lightgbm as lgb
    from ml.dataset import apply_snapshot_zscore
    output.mkdir(parents=True, exist_ok=True)
    fixed = output / "frozen"
    fixed.mkdir(exist_ok=True)
    for source in (BASE / "ml/models/snapshot_cache.pkl", SHADOW_META):
        dest = fixed / source.name
        if not dest.exists():
            shutil.copy2(source, dest)
    raw_bytes = (fixed / "snapshot_cache.pkl").read_bytes()
    snapshot = pickle.loads(raw_bytes)
    snapshot = snapshot["df"] if isinstance(snapshot, dict) else snapshot
    meta = json.loads((fixed / SHADOW_META.name).read_text(encoding="utf-8"))
    model = BASE / "ml/models" / Path(meta["model_file"]).name
    booster = lgb.Booster(model_file=str(model))
    full = score_snapshot(snapshot, meta, booster)
    old_zs = apply_snapshot_zscore(snapshot) if meta.get("cross_sectional_zscore") else snapshot
    old_score = booster.predict(old_zs.reindex(columns=meta["feature_columns"]).astype(np.float32))
    score_delta = float(np.max(np.abs(old_score - full["pred_return_20d"].to_numpy())))
    full["recommendation"] = np.where(full["pred_return_20d"] > 0, "建議買進(dataA)", "觀望")
    tickers = sorted(full["ticker"].unique())
    cache = fixed / "duckdb_daily.pkl"
    if not cache.exists():
        before = (BASE / "stock.duckdb").stat()
        def fetch(ticker):
            response = requests.get(f"http://127.0.0.1:8001/api/stocks/{ticker}/daily", params={"days": 90}, timeout=45)
            response.raise_for_status()
            data = response.json()
            if not isinstance(data.get("data"), list):
                raise ValueError(f"bad DuckDB API frame: {ticker}")
            d = pd.DataFrame(data["data"])
            if not d.empty:
                d["Date"] = pd.to_datetime(d["Date"])
                d = d[d["Date"] <= pd.Timestamp(as_of)].reset_index(drop=True)
            return ticker, d
        with ThreadPoolExecutor(max_workers=4) as pool:
            frames = dict(pool.map(fetch, tickers))
        after = (BASE / "stock.duckdb").stat()
        if (before.st_mtime_ns, before.st_size) != (after.st_mtime_ns, after.st_size):
            raise RuntimeError("DuckDB changed while freezing frames; retry snapshot")
        cache.write_bytes(pickle.dumps(frames))
    frames = pickle.loads(cache.read_bytes())
    old_source = subprocess.check_output(["git", "show", "a1849103:scripts/generate_entry_candidates.py"], cwd=BASE).decode("utf-8")
    old = types.ModuleType("entry_before")
    old.__file__ = str(BASE / "scripts/generate_entry_candidates.py")
    exec(compile(old_source, old.__file__, "exec"), old.__dict__)
    required = date.fromisoformat(as_of)
    gate = load_disposition_gate(BASE, target_date=trade_day, required_as_of=as_of)
    if not gate["complete"]:
        raise ValueError(gate["reasons"])
    regime = new._twii_above_ma60(required)
    dates = dict(zip(full["ticker"], full["source_date"]))
    valid = full["source_date"].eq(as_of) & full["operating_margin_latest"].notna()
    om = dict(zip(full.loc[valid, "ticker"], full.loc[valid, "operating_margin_latest"]))
    fixture_root = fixed / "context"
    (fixture_root / "ml/data").mkdir(parents=True, exist_ok=True)
    sect = fixture_root / "ml/data/sector_strength.json"
    if not sect.exists():
        shutil.copy2(BASE / "ml/data/sector_strength.json", sect)
    for module in (old, new):
        module.BASE_DIR = fixture_root
        module.prepare_stock_frame = lambda path: frames.get(path.stem, pd.DataFrame()).copy()
        module._load_predictions = lambda *a, **kw: full.copy()
        module._latest_predictions_path = lambda *a, **kw: str(BASE / f"ml/models/dataA_predictions_{as_of}.csv")
        module._load_chipk = lambda *a, **kw: pd.DataFrame()
        module._load_om_map = lambda: om
        module._snapshot_fields = lambda *a: (dates, om)
        module._twii_above_ma60 = lambda *a: regime
        module._night_session_gap = lambda: None
    old._load_special = lambda: set()
    baseline = old.generate()["rows"]
    old._load_special = lambda: set(gate["blocked_tickers"])
    special_only = old.generate()["rows"]
    new.load_disposition_gate = lambda *a, **kw: gate
    candidate = new.generate(as_of, trade_date=trade_day, include_live=False)["rows"]
    def keys(rows):
        return {(r["ticker"], r.get("lane", "main")): r for r in rows}
    a, b = keys(baseline), keys(candidate)
    changes = []
    for key in sorted(set(a)|set(b)):
        aa, bb = a.get(key, {}), b.get(key, {})
        changes.append({"ticker": key[0], "date": as_of, "lane": key[1], "before": aa.get("kind"),
                        "after": bb.get("kind"), "warnings": ";".join(bb.get("data_warnings", [])),
                        "score_delta": bb.get("score", np.nan)-aa.get("score", np.nan)})
    def predicates(source):
        return {node.name: ast.dump(node, include_attributes=False) for node in ast.parse(source).body
                if isinstance(node, ast.FunctionDef) and node.name in ("_pattern_ok", "_rere_lane_ok", "_rere_shakeout_ok", "_rere_ignition_ok")}
    unchanged = predicates(old_source) == predicates((BASE / "scripts/generate_entry_candidates.py").read_text(encoding="utf-8"))
    result = {"as_of": as_of, "snapshot_sha256": sha256(raw_bytes).hexdigest(),
              "duckdb_frame_sha256": sha256(cache.read_bytes()).hexdigest(), "model_sha256": sha256(model.read_bytes()).hexdigest(),
              "raw_frame_rows": sum(len(f) for f in frames.values()), "stocks": len(full),
              "score_max_abs_delta": score_delta, "prob_edge_delta": "not applicable: regression source has no prob_edge",
              "strategy_predicates_unchanged": unchanged, "baseline_count": len(baseline),
              "special_only_count": len(special_only), "candidate_count": len(candidate),
              "added": sorted(set(b)-set(a)), "removed": sorted(set(a)-set(b)),
              "kind_flips": [r for r in changes if r["before"] != r["after"]],
              "special_blocked": gate["blocked_tickers"]}
    assert score_delta == 0 and unchanged
    pd.DataFrame(changes).to_csv(output / "entry_ab_rows.csv", index=False, encoding="utf-8-sig")
    (output / "entry_ab.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    return result


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--as-of", required=True)
    p.add_argument("--trade-date", required=True)
    args = p.parse_args()
    print(json.dumps(run(args.output, args.as_of, args.trade_date), ensure_ascii=False, indent=2))
