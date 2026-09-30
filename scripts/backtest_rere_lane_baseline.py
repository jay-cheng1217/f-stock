# -*- coding: utf-8 -*-
"""Research rere baseline: frozen canonical predicates, mature cohorts and cost sensitivity.

Conditional pattern samples only; no historical top-six portfolio reconstruction.
Entry next close; MA60*0.97 stop starts after entry; expiry entry+60 sessions.
"""
from __future__ import annotations

import argparse
import hashlib
import inspect
import io
import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
from scripts.generate_entry_candidates import MIN_LIQUIDITY_LOTS, _rere_shakeout_ok, _rere_ignition_ok

DAILY = BASE / "日K資料"
CAL_PATH = BASE / "ml/data/ex_dividend_calendar.csv"
HOLD = 60
MODES = ("legacy", "wash_only", "liquidity_only", "volume_ratio_only", "maturity_only", "aligned")
REQUIRED = ["Date", "Close", "Volume", "Foreign_BuySell", "Trust_BuySell", "VOL_MA_20", "MA_20", "MA_60"]


def load_calendar(path: Path) -> dict:
    cal = pd.read_csv(path, dtype={"stock_id": str}, encoding="utf-8-sig")
    cal["dv"] = pd.to_numeric(cal["stock_and_cache_dividend"], errors="raise")
    if cal["dv"].isna().any():
        raise ValueError("Missing corporate-action values")
    cal["date"] = pd.to_datetime(cal["date"], errors="raise").dt.strftime("%Y-%m-%d")
    if cal.duplicated(["stock_id", "date"]).any():
        raise ValueError("Duplicate corporate-action event")
    return {tk: list(zip(g["date"], g["dv"])) for tk, g in cal.groupby("stock_id")}


def signal_masks(k: pd.DataFrame) -> dict:
    """Single-variable A/B masks; aligned final checks call unchanged production predicates."""
    c = pd.to_numeric(k["Close"], errors="coerce")
    vol = pd.to_numeric(k["Volume"], errors="coerce")
    ma20 = pd.to_numeric(k["MA_20"], errors="coerce")
    ma60 = pd.to_numeric(k["MA_60"], errors="coerce")
    fr = pd.to_numeric(k["Foreign_BuySell"], errors="coerce").fillna(0)
    tr = pd.to_numeric(k["Trust_BuySell"], errors="coerce").fillna(0)
    hi10 = c.rolling(10).max()
    volume20 = vol.rolling(20).mean()
    states = pd.DataFrame({
        "close": c, "ma20": ma20, "v20": c / ma20 - 1,
        "v60": c / ma60 - 1, "wash_from_hi10": hi10 / c - 1,
        "vr_today": vol / volume20.replace(0, np.nan),
        "foreign_prev5": fr.shift(1).rolling(5).sum() / 1000,
        "foreign_today": fr / 1000, "trust_today": tr / 1000,
        "prev_below_ma20": (c / ma20 - 1).shift(1) < 0,
        "day_ret": c / c.shift(1) - 1,
    })
    liquid = volume20 >= MIN_LIQUIDITY_LOTS * 1000
    enough = pd.Series(np.arange(len(k)) >= 59, index=k.index)
    plausible = liquid & enough & (states["wash_from_hi10"] >= .1) & (states["v60"] > 0)
    aligned = {name: pd.Series(False, index=k.index) for name in ("shakeout", "ignition")}
    for index, state in states.loc[plausible].iterrows():
        aligned["shakeout"].loc[index] = _rere_shakeout_ok(state.to_dict())
        aligned["ignition"].loc[index] = _rere_ignition_ok(state.to_dict())
    old_wash = 1 - c / hi10
    old_vr = vol / pd.to_numeric(k["VOL_MA_20"], errors="coerce").replace(0, np.nan)
    common = (states["v20"].abs() <= .05) & (c > ma60) & (states["foreign_prev5"] < 0) & (fr > 0)
    masks = {}
    for mode in MODES:
        if mode == "aligned":
            for subtype, mask in aligned.items():
                masks[(mode, subtype)] = mask.fillna(False)
            continue
        wash = states["wash_from_hi10"] if mode == "wash_only" else old_wash
        liquidity = liquid if mode == "liquidity_only" else vol >= 500_000
        vr = states["vr_today"] if mode == "volume_ratio_only" else old_vr
        masks[(mode, "shakeout")] = ((wash >= .10) & common & (vr < 1.2) & liquidity).fillna(False)
    return masks


def evaluate_trade(k: pd.DataFrame, signal_index: int, events: list,
                   *, require_mature: bool = True, adjusted: bool = True) -> dict | None:
    entry_index = signal_index + 1
    if entry_index >= len(k) or (require_mature and entry_index + HOLD >= len(k)):
        return None
    c = pd.to_numeric(k["Close"], errors="coerce").to_numpy(dtype=float)
    entry = c[entry_index]
    end = min(entry_index + HOLD, len(k) - 1)
    if not np.isfinite(c[entry_index:end + 1]).all() or entry <= 0:
        return None
    dates = k["Date"].astype(str).str[:10].to_numpy()
    stop = float(k["MA_60"].iloc[signal_index]) * .97
    prices = c[entry_index:end + 1].copy()
    if adjusted:
        for event_date, dividend in events:
            if dates[entry_index] < event_date <= dates[end]:
                prices += (dates[entry_index:end + 1] >= event_date) * float(dividend)
    hits = np.flatnonzero(prices[1:] < stop) if np.isfinite(stop) else np.array([])
    offset = int(hits[0] + 1) if len(hits) else end - entry_index
    return {"entry_date": dates[entry_index], "exit_date": dates[entry_index + offset],
            "entry_close": float(entry), "stop": float(stop), "days_held": offset,
            "complete_cohort": bool(entry_index + HOLD < len(k)),
            "status": "stopped_out" if len(hits) else ("matured" if end - entry_index == HOLD else "immature_mark"),
            "gross_return": float(prices[offset] / entry - 1)}


def cost_adjusted_return(gross, slippage_per_side: float = .001):
    """Assumption: regular-stock sell tax + undiscounted commission + slippage."""
    return (1 + gross) * (1 - .003 - .001425 - slippage_per_side) / (1 + .001425 + slippage_per_side) - 1


def run_one(tk: str, k: pd.DataFrame, rows: list, events: list | None = None) -> None:
    k = k.copy()
    k["Date"] = pd.to_datetime(k["Date"], errors="raise").dt.strftime("%Y-%m-%d")
    k = k.sort_values("Date").reset_index(drop=True)
    if k["Date"].duplicated().any():
        raise ValueError(f"Duplicate trading date: {tk}")
    for (mode, subtype), mask in signal_masks(k).items():
        for t in np.flatnonzero(mask.to_numpy()):
            for adjustment in ("raw", "adj"):
                result = evaluate_trade(k, int(t), events or [], require_mature=mode in ("aligned", "maturity_only"), adjusted=adjustment == "adj")
                if result is None:
                    continue
                gross = result["gross_return"]
                rows.append({"ticker": tk, "signal_date": k["Date"].iloc[t], "year": k["Date"].iloc[t][:4],
                             "mode": mode, "subtype": subtype, "adjustment": adjustment, **result,
                             "net_return_0bps": cost_adjusted_return(gross, 0),
                             "net_return_10bps": cost_adjusted_return(gross, .001),
                             "net_return_25bps": cost_adjusted_return(gross, .0025)})


def summarize(group: pd.DataFrame) -> dict:
    gross = group["gross_return"]
    return {"signals": len(group), "tickers": group["ticker"].nunique(), "entry_dates": group["entry_date"].nunique(),
            "complete_cohort": int(group["complete_cohort"].sum()), "mean_gross_pct": float(gross.mean() * 100),
            "median_gross_pct": float(gross.median() * 100), "win_gross_pct": float((gross > 0).mean() * 100),
            "mean_net_0bps_pct": float(group["net_return_0bps"].mean() * 100),
            "mean_net_10bps_pct": float(group["net_return_10bps"].mean() * 100),
            "mean_net_25bps_pct": float(group["net_return_25bps"].mean() * 100),
            "win_net_10bps_pct": float((group["net_return_10bps"] > 0).mean() * 100),
            "stop_pct": float((group["status"] == "stopped_out").mean() * 100)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--daily-dir", type=Path, default=DAILY)
    parser.add_argument("--calendar", type=Path, default=CAL_PATH)
    parser.add_argument("--as-of", default=datetime.now().date().isoformat())
    parser.add_argument("--output-prefix", type=Path, default=BASE / "ml/reports/rere_baseline_integrity_20260906")
    parser.add_argument("--tickers", nargs="+")
    args = parser.parse_args()
    calendar = load_calendar(args.calendar)
    rows: list[dict] = []
    predicate_source = inspect.getsource(_rere_shakeout_ok) + inspect.getsource(_rere_ignition_ok)
    manifest = {"as_of": args.as_of, "calendar_sha256": hashlib.sha256(args.calendar.read_bytes()).hexdigest(),
                "canonical_predicates_sha256": hashlib.sha256(predicate_source.encode("utf-8")).hexdigest(),
                "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                "files": {}, "excluded": []}
    paths = sorted(args.daily_dir.glob("*.csv"))
    for i, path in enumerate(paths):
        tk = path.stem
        if not (tk.isdigit() and len(tk) == 4) or (args.tickers and tk not in args.tickers):
            continue
        payload = path.read_bytes()
        manifest["files"][tk] = hashlib.sha256(payload).hexdigest()
        try:
            k = pd.read_csv(io.BytesIO(payload), usecols=REQUIRED)
            k = k[k["Date"].astype(str).str[:10] <= args.as_of]
            if len(k) < 90:
                manifest["excluded"].append({"ticker": tk, "reason": "fewer than 90 observations"})
                continue
            run_one(tk, k, rows, calendar.get(tk, []))
        except (ValueError, KeyError) as exc:
            manifest["excluded"].append({"ticker": tk, "reason": str(exc)})
        if (i + 1) % 250 == 0:
            print(f"Scanned {i + 1}/{len(paths)} files; outcome rows={len(rows)}", flush=True)
    frame = pd.DataFrame(rows)
    if frame.empty:
        raise ValueError("No eligible observations; no baseline published")
    result = {"as_of": args.as_of, "scope": "conditional pattern samples, not portfolio or significance evidence", "summaries": [], "annual": []}
    for keys, group in frame.groupby(["mode", "subtype", "adjustment"]):
        result["summaries"].append(dict(zip(["mode", "subtype", "adjustment"], keys)) | summarize(group))
    aligned = frame[(frame["mode"] == "aligned") & (frame["adjustment"] == "adj")]
    for keys, group in aligned.groupby(["subtype", "year"]):
        result["annual"].append(dict(zip(["subtype", "year"], keys)) | summarize(group))
    prefix = args.output_prefix
    prefix.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(prefix.with_suffix(".csv.gz"), index=False, encoding="utf-8-sig", compression={"method": "gzip", "mtime": 0})
    prefix.with_suffix(".json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    prefix.with_name(prefix.name + "_manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    lines = [f"# rere 驗證基準修復研究（as-of {args.as_of}）", "", "本研究不更動 rere 訊號門檻、候選排序或生產帳本。完整 60 日 cohort，訊號隔日收盤進場，收盤跌破 MA60×0.97 停損；進場當日不檢查停損。", "",
             "| 模式 | 型態 | 還原 | 樣本 | 完整 cohort | 平均毛報酬 | 毛勝率 | 平均淨報酬（每邊10bps滑價） |", "|---|---|---|---:|---:|---:|---:|---:|"]
    for r in result["summaries"]:
        lines.append(f"| {r['mode']} | {r['subtype']} | {r['adjustment']} | {r['signals']} | {r['complete_cohort']} | {r['mean_gross_pct']:+.2f}% | {r['win_gross_pct']:.1f}% | {r['mean_net_10bps_pct']:+.2f}% |")
    lines += ["", "## 完整 cohort 逐年（還原）", "", "| 型態 | 年 | 樣本 | 毛報酬 | 淨報酬10bps | 毛勝率 |", "|---|---|---:|---:|---:|---:|"]
    for r in result["annual"]:
        lines.append(f"| {r['subtype']} | {r['year']} | {r['signals']} | {r['mean_gross_pct']:+.2f}% | {r['mean_net_10bps_pct']:+.2f}% | {r['win_gross_pct']:.1f}% |")
    lines += ["", "## 解讀限制", "", "- aligned 直接調用 canonical rere predicate；舊版各單因子模式用於定位偏差，不能選最好看的數字當新及格線。",
              "- 成本為情境假設：一般股票賣出稅 0.3%、每邊手續費 0.1425%、每邊滑價 0/10/25bps；未計最低手續費、零股價差和市場衝擊。",
              "- 每個符合訊號日都算一筆，有重複股票及重疊持有；樣本數不是獨立試驗數，也沒有組合 MDD、資金利用率或可交易容量結論。",
              "- 缺少歷史模型 universe、每日 sector 排序、top-six 和特殊股 gate；這是有條件型態樣本，不是正式候選名單績效，更不是 rere 本人實績。",
              "- 保留既有加回權息值慣例以隔離驗證缺陷；股票分割、減資及持股數變動的經濟報酬仍需額外驗證。",
              "- 現存日K檔案 universe 存在下市/停牌覆蓋限制；歷史技術欄位依賴目前資料修復版本，並非當日原始快照。",
              f"- 已讀 {len(manifest['files'])} 檔；排除/不足資料 {len(manifest['excluded'])} 檔，詳 input manifest。輸入 SHA-256 與逐筆 CSV.gz 可重現。",
              "- 舊 +4.58% / 35.8% 只保留為 2026-07-15 歷史參考；本研究不自動提升、降低或淘汰 rere lane。", ""]
    prefix.with_suffix(".md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"Research outputs: {prefix}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
