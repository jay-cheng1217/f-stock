# -*- coding: utf-8 -*-
"""Research scoreboard of technical signal samples, not full-strategy P&L.

The five historical proxy equations retain their names for continuity but are
not canonical main or full rere. `main_pattern` calls the current canonical
technical predicate with its exact stored fields. All outcomes use historical
liquidity, independent 20/60D maturity and corporate-action economic factors.
No model, OM, ChipK, theme, stop, intraday fills or portfolio overlays are
invented. Results do not automatically authorize a strategy downgrade.
"""
from __future__ import annotations

import argparse
import glob
import hashlib
from io import BytesIO
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))
from ml.corporate_actions import cumulative_action_factor, load_action_calendar
from scripts.generate_entry_candidates import _pattern_ok
DAILY_DIR = BASE_DIR / "日K資料"
REPORT_DIR = BASE_DIR / "ml" / "reports"
FRICTION = 0.004
MIN_AVG_VOL = 500_000
HOLDS = (20, 60)
STRATEGIES = ("momentum", "ma20_combo", "rere_lane", "chase", "ma5_wash", "main_pattern")


def _signals_for_frame(d: pd.DataFrame) -> dict:
    if not {"Date", "Close", "Volume"}.issubset(d):
        raise ValueError("price frame missing Date/Close/Volume")
    ccol, vcol = "Close", "Volume"
    c = pd.to_numeric(d[ccol], errors="coerce")
    v = pd.to_numeric(d[vcol], errors="coerce")
    fb = pd.to_numeric(d.get("Foreign_BuySell", pd.Series(np.nan, index=d.index)), errors="coerce") / 1000
    ma5 = c.rolling(5).mean(); ma20 = c.rolling(20).mean(); ma60 = c.rolling(60).mean()
    v5 = c / ma5 - 1; v20 = c / ma20 - 1
    vol_ma20 = v.rolling(20).mean(); vr = v / vol_ma20
    up = (ma5 > ma20) & (ma20 > ma60) & (c > ma60)
    hi10 = c.rolling(10).max()
    fb5_prev = fb.rolling(5).sum().shift(1)
    ema12 = c.ewm(span=12, adjust=False).mean(); ema26 = c.ewm(span=26, adjust=False).mean()
    hist = (ema12 - ema26) - (ema12 - ema26).ewm(span=9, adjust=False).mean()
    rsi_delta = c.diff()
    up_g = rsi_delta.clip(lower=0).rolling(14).mean(); dn_g = (-rsi_delta.clip(upper=0)).rolling(14).mean()
    rsi = 100 - 100 / (1 + up_g / dn_g)
    signals = {
        "close": c,
        "momentum": (up & v5.between(-0.035, 0.075) & vr.between(0.75, 5.5)
                     & hist.gt(0) & rsi.between(48, 86)).fillna(False),
        "ma20_combo": ((c > ma60) & v20.between(0.05, 0.10) & (vr < 1.5) & (fb > 0)).fillna(False),
        "rere_lane": (((hi10 / c - 1) >= 0.10) & v20.between(-0.05, 0.05) & (vr < 1.2)
                      & (c > ma60) & (fb5_prev < 0) & (fb > 0)).fillna(False),
        "chase": (up & (v5 > 0.04)).fillna(False),
        "ma5_wash": (up & v5.between(-0.03, 0.01) & (v5.rolling(5).max() > 0.04)).fillna(False),
    }
    liquid = v.rolling(20, min_periods=20).mean().ge(MIN_AVG_VOL)
    # Preserve the five legacy proxy equations for an old-method diagnostic.
    signals.update({f"unmasked_{name}": signals[name].copy() for name in STRATEGIES[:-1]})
    old_fb = fb.fillna(0)
    signals["unmasked_rere_lane"] = ((hi10 / c - 1).ge(.10) & v20.between(-.05, .05) & vr.lt(1.2)
        & c.gt(ma60) & old_fb.rolling(5).sum().shift(1).lt(0) & old_fb.gt(0)).fillna(False)
    for name in STRATEGIES[:-1]:
        signals[name] &= liquid
    columns = ("MA_20", "MA_60", "VOL_MA_20", "RSI_14", "MACDh_12_26_9")
    signals["main_inputs_available"] = all(column in d for column in columns)
    if signals["main_inputs_available"]:
        m20, m60, vm20, rsi14, macd = [pd.to_numeric(d[column], errors="coerce") for column in columns]
        rows = zip(c / m20.replace(0, np.nan) - 1, c / m60.replace(0, np.nan) - 1,
                   v / vm20.replace(0, np.nan), rsi14, (macd - macd.shift(3)) / c)
        keys = ("v20", "v60", "vol_ratio", "rsi", "macd_delta_rel")
        signals["main_pattern"] = pd.Series([_pattern_ok(dict(zip(keys, values))) for values in rows], index=d.index)
        signals["main_pattern"] &= liquid & pd.Series(np.arange(len(d)) >= 59, index=d.index)
    else:
        signals["main_pattern"] = pd.Series(False, index=d.index)
    return signals


def trades_for_frame(d, ticker, sig, calendar, friction=FRICTION):
    c = sig["close"].to_numpy(dtype=float)
    dates = pd.to_datetime(d.Date).dt.strftime("%Y-%m-%d").to_numpy()
    factors = cumulative_action_factor(pd.to_datetime(d.Date), ticker, calendar).to_numpy()
    records = []
    for name in STRATEGIES:
        for i in np.flatnonzero(sig[name].to_numpy()):
            entry = int(i + 1)
            for h in HOLDS:
                end = entry + h
                if end >= len(c) or c[entry] <= 0 or not np.isfinite(c[entry]) or not np.isfinite(c[end]):
                    continue
                gross = c[end] / c[entry] * factors[entry] / factors[end] - 1
                records.append((name, ticker, dates[i], dates[entry], dates[end], h, gross, gross - friction))
    return pd.DataFrame(records, columns=["s", "ticker", "signal_date", "d", "exit_date", "h", "gross", "r"])


def nonoverlapping(df):
    selected = []
    for _, group in df.groupby(["s", "ticker", "h"], sort=False):
        last_exit = ""
        for row in group.sort_values("d").itertuples():
            if row.d > last_exit:
                selected.append(row.Index)
                last_exit = row.exit_date
    return df.loc[selected].copy()


def stats(sub):
    if sub.empty:
        return {"n": 0, "unique_dates": 0, "unique_tickers": 0}
    r = sub.r
    return {"n": len(sub), "unique_dates": sub.d.nunique(), "unique_tickers": sub.ticker.nunique(),
            "mean": round(float(r.mean()) * 100, 2), "gross_mean": round(float(sub.gross.mean()) * 100, 2),
            "net_at_0_8pct_cost": round(float(sub.gross.mean() - .008) * 100, 2),
            "median": round(float(r.median()) * 100, 2), "win": round(float(r.gt(0).mean()) * 100, 1)}


def common_stock_cohort(df, no, cutoff):
    # Descriptive fixed subgroup, not a new production universe/filter.
    def subset(frame):
        return frame[(frame.d >= "2020-01-01") & frame.ticker.str.fullmatch(r"[1-9][0-9]{3}")]
    sub, non = subset(df), subset(no)
    return {name: {f"{h}d": {
        "full": stats(sub[(sub.s == name) & (sub.h == h)]),
        "recent": stats(sub[(sub.s == name) & (sub.h == h) & (sub.d >= cutoff)]),
        "nonoverlap_recent": stats(non[(non.s == name) & (non.h == h) & (non.d >= cutoff)]),
    } for h in HOLDS} for name in STRATEGIES}


def run(recent_months: int = 6, *, daily_dir=DAILY_DIR, end_date=None,
        friction=FRICTION, artifacts_dir=None) -> dict:
    if recent_months < 1 or not 0 <= friction < 1:
        raise ValueError("invalid recent window or friction")
    rec, legacy, rejected, main_missing = [], [], [], []
    hashes, data_end = {}, None
    signals_n = {name: 0 for name in STRATEGIES}
    calendar = load_action_calendar()
    if calendar.empty:
        raise ValueError("corporate-action calendar unavailable")
    for fp in sorted(glob.glob(str(Path(daily_dir) / "*.csv"))):
        t = os.path.basename(fp).replace(".csv", "")
        if not t.isdigit():
            continue
        try:
            raw = Path(fp).read_bytes()
            hashes[Path(fp).name] = hashlib.sha256(raw).hexdigest()
            d = pd.read_csv(BytesIO(raw))
            d.Date = pd.to_datetime(d.Date, errors="raise")
            d = d.sort_values("Date").reset_index(drop=True)
            if d.Date.duplicated().any():
                raise ValueError("duplicate dates")
            if end_date:
                d = d[d.Date <= pd.Timestamp(end_date)].reset_index(drop=True)
            if d.empty:
                continue
            sig = _signals_for_frame(d)
        except (ValueError, KeyError, AttributeError, TypeError) as exc:
            rejected.append({"ticker": t, "error": str(exc)})
            continue
        latest = d.Date.max().strftime("%Y-%m-%d")
        data_end = max(data_end or latest, latest)
        if not sig["main_inputs_available"]:
            main_missing.append(t)
        for name in STRATEGIES:
            signals_n[name] += int(sig[name].sum())
        rec.append(trades_for_frame(d, t, sig, calendar, friction))
        # Same raw frame, exact old liquidity/maturity/raw-return sampling.
        if "Foreign_BuySell" in d and len(d) >= 140 and pd.to_numeric(d.Volume, errors="coerce").tail(60).mean() >= MIN_AVG_VOL:
            c = sig["close"].to_numpy()
            for name in STRATEGIES[:-1]:
                for i in np.flatnonzero(sig[f"unmasked_{name}"]):
                    if i + 61 >= len(c) or not c[i + 1] > 0:
                        continue
                    for h in HOLDS:
                        legacy.append((name, h, c[i + 1 + h] / c[i + 1] - 1 - friction))
    if not rec:
        raise ValueError("no valid historical frames")
    df = pd.concat([frame for frame in rec if not frame.empty], ignore_index=True)
    if df.empty:
        raise ValueError("no mature signals; cannot publish a verdict")
    no = nonoverlapping(df)
    cutoff = (pd.Timestamp(data_end) - pd.DateOffset(months=recent_months)).strftime("%Y-%m-%d")
    board = {}
    for s in STRATEGIES:
        board[s] = {}
        for h in HOLDS:
            full = stats(df[(df.s == s) & (df.h == h)])
            recent = stats(df[(df.s == s) & (df.h == h) & (df.d >= cutoff)])
            decay = None
            if full.get("n") and recent.get("n", 0) >= 30:
                decay = round(recent["mean"] - full["mean"], 2)
            sub = df[(df.s == s) & (df.h == h)]
            non = no[(no.s == s) & (no.h == h)]
            board[s][f"{h}d"] = {"full": full, "recent": recent, "decay_pp": decay,
                "nonoverlap_full": stats(non), "nonoverlap_recent": stats(non[non.d >= cutoff]),
                "latest_mature_entry_date": None if sub.empty else sub.d.max(),
                "signal_n": signals_n[s], "unmatured_or_unexecutable_n": signals_n[s] - len(sub),
                "yearly": {year: stats(group) for year, group in sub.groupby(sub.d.str[:4])}}
    old = pd.DataFrame(legacy, columns=["s", "h", "r"])
    old_summary = [{"strategy": key[0], "hold": int(key[1]), "n": len(group), "net_mean": round(float(group.r.mean()) * 100, 2)}
                   for key, group in old.groupby(["s", "h"])]
    manifest = {"daily_csv_sha256": hashes, "calendar_frame_sha256": hashlib.sha256(calendar.to_csv(index=False).encode()).hexdigest(),
                "generator_sha256": hashlib.sha256((BASE_DIR / 'scripts/generate_entry_candidates.py').read_bytes()).hexdigest()}
    if artifacts_dir:
        artifacts_dir = Path(artifacts_dir)
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        df.to_csv(artifacts_dir / "scoreboard_signal_returns.csv.gz", index=False, compression={"method": "gzip", "mtime": 0})
        no.to_csv(artifacts_dir / "scoreboard_nonoverlap_returns.csv.gz", index=False, compression={"method": "gzip", "mtime": 0})
        (artifacts_dir / "scoreboard_input_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return {"generated_at": datetime.now().isoformat(timespec="seconds"),
            "method_version": "scoreboard_research_v2_20260906", "friction": friction,
            "recent_months": recent_months,
            "data_end": data_end, "recent_cutoff": cutoff, "board": board,
            "four_digit_nonzero_2020_subgroup": common_stock_cohort(df, no, cutoff),
            "price_files": len(hashes), "rejected_files": rejected, "main_missing_input_tickers": main_missing,
            "input_manifest_sha256": hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest(),
            "legacy_method_fixed_frame_reference": old_summary,
            "scope": "technical samples only; not full canonical main/rere/Champion or portfolio P&L",
            "assumptions": ["next-session close entry; independent20/60D exit; no stop; aggregate friction0.4% default",
                "factor-adjusted economic returns; exact stored technical fields for main_pattern",
                "model/OM/ChipK/disposition/theme/intraday fill and discretionary exits absent; foreign missing data stays unknown",
                "nonoverlap per ticker is not statistical independence or a funded portfolio",
                "local-file universe may omit delisted history; no survivorship-free claim",
                "no automatic lane downgrade from this research scoreboard"]}


def write_report(payload: dict, output_dir=REPORT_DIR) -> tuple[Path, Path]:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d")
    jp = output_dir / f"strategy_scoreboard_{stamp}.json"
    mp = output_dir / f"strategy_scoreboard_{stamp}.md"
    jp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [f"# Strategy Scoreboard {stamp}", "",
             f"- data_end: {payload['data_end']}  recent window: {payload['recent_months']} months",
             "- 僅技術訊號研究，不代表完整主策略、rere 或 Champion 績效；不可據此自動降權。",
             f"- 近期起日 {payload['recent_cutoff']}；淨值扣總摩擦 {payload['friction']:.2%}；無停損或人工觸發。",
             "",
             "| 策略 | 持有 | 全期 N/平均/勝率 | 近期 N/平均/勝率 | 衰減(pp) |",
             "|---|---|---|---|---|"]
    names = {"momentum": "舊動能代理", "ma20_combo": "舊MA20+外資代理",
             "rere_lane": "舊rere单型態代理(無停損)", "chase": "追高(對照)", "ma5_wash": "5MA洗盤(對照)",
             "main_pattern": "目前主策略型態門檻(僅技術)"}
    for s, hs in payload["board"].items():
        for h, x in hs.items():
            f, rct = x["full"], x["recent"]
            lines.append(
                f"| {names.get(s, s)} | {h} | {f.get('n')}/{f.get('mean')}%/{f.get('win')}% "
                f"| {rct.get('n')}/{rct.get('mean', '-')}%/{rct.get('win', '-')}% | {x['decay_pp']} |")
    lines += ["", "非重疊樣本（同股票持有到期後才收下一筆，仍非獨立樣本／投資組合）：", "",
              "| 策略 | 持有 | 全期 N/淨均值 | 近期 N/淨均值 | 成熟進場截止 |", "|---|---|---|---|---|"]
    for name, holds in payload['board'].items():
        for hold, value in holds.items():
            f, r = value['nonoverlap_full'], value['nonoverlap_recent']
            lines.append(f"| {names[name]} | {hold} | {f['n']}/{f.get('mean')}% | {r['n']}/{r.get('mean')}% | {value['latest_mature_entry_date']} |")
    lines += ["", "限制：", ""] + [f"- {value}" for value in payload['assumptions']]
    lines += [f"- 拒絕檔 {len(payload['rejected_files'])}；主型態缺欄檔 {len(payload['main_missing_input_tickers'])}；年度、訊號/日期數、0.8%成本敏感度見JSON。"]
    mp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return jp, mp


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--recent-months", type=int, default=6)
    ap.add_argument("--end-date")
    ap.add_argument("--output-dir", type=Path, default=REPORT_DIR)
    ap.add_argument("--daily-dir", type=Path, default=DAILY_DIR)
    ap.add_argument("--friction", type=float, default=FRICTION)
    args = ap.parse_args()
    payload = run(args.recent_months, daily_dir=args.daily_dir, end_date=args.end_date,
                  friction=args.friction, artifacts_dir=args.output_dir if args.output_dir != REPORT_DIR else None)
    jp, mp = write_report(payload, args.output_dir)
    print(json.dumps({"json": str(jp), "md": str(mp)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
