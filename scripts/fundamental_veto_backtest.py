"""Backtest a fundamental veto overlay on the main-lane base signal.

Question (2026-07-02, Serenity chokepoint skill evaluation): does vetoing
loss-making / revenue-collapsing stocks improve the validated base entry
(貼MA20 ±5% + volume ratio <1.5 + foreign net buy)?

Design: generate base signals over the full universe, then SPLIT the same
signal population by a point-in-time fundamental veto:

- OM veto: latest available quarterly operating margin < 0
  (availability: Q1->5/15, Q2->8/14, Q3->11/14, Q4->3/31 next year).
- Revenue veto: 3-month average monthly-revenue YoY < -20%
  (month M available on the 10th of month M+1).

Compare forward 20d/60d returns (next-day open entry, hold-day close exit,
friction 0.4%) between pass and veto groups. Research-only: does not mutate
production gates, generators, schedulers, or portfolios.
"""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DAILY_DIR = ROOT / "日K資料"
DB_PATH = ROOT / "stock.duckdb"
REPORT_DIR = ROOT / "ml" / "reports"

START_DATE = "2021-01-01"
HOLD_DAYS = (20, 60)
FRICTION = 0.004
MA20_BAND = 0.05          # 貼MA20 ±5% (mirrors generate_entry_candidates main lane)
VOL_RATIO_MAX = 1.5       # 不爆量
COOLDOWN_DAYS = 20        # per-ticker signal cooldown to reduce overlap
REV_YOY_VETO = -20.0      # 3M avg monthly revenue YoY below this -> veto
MIN_VOLUME_SHARES = 500_000  # liquidity floor, same spirit as hard gate

REQUIRED = {"Date", "Open", "Close", "Volume", "MA_20", "VOL_MA_20", "Foreign_BuySell"}


def quarter_available(year: int, season: int) -> pd.Timestamp:
    """TW filing deadlines: Q1->5/15, Q2->8/14, Q3->11/14, Q4->3/31 next yr."""
    if season == 1:
        return pd.Timestamp(year, 5, 15)
    if season == 2:
        return pd.Timestamp(year, 8, 14)
    if season == 3:
        return pd.Timestamp(year, 11, 14)
    return pd.Timestamp(year + 1, 3, 31)


def load_fundamentals() -> tuple[dict, dict]:
    import duckdb

    con = duckdb.connect(str(DB_PATH), read_only=True)
    fin = con.execute(
        "select Ticker, Year, Season, Operating_Margin_Pct from financials"
    ).df()
    rev = con.execute(
        "select Ticker, Date, YoY_pct_change from revenue"
    ).df()
    con.close()

    fin["avail"] = [quarter_available(y, s) for y, s in zip(fin["Year"], fin["Season"])]
    fin["Ticker"] = fin["Ticker"].astype(str)
    fin = fin.dropna(subset=["Operating_Margin_Pct"]).sort_values("avail")
    fin_map = {t: g[["avail", "Operating_Margin_Pct"]].to_numpy() for t, g in fin.groupby("Ticker")}

    rev["Ticker"] = rev["Ticker"].astype(str)
    rev = rev.dropna(subset=["YoY_pct_change"])
    # month M (YYYY-MM) available on 10th of next month
    month = pd.to_datetime(rev["Date"] + "-01")
    rev["avail"] = month + pd.offsets.MonthBegin(1) + pd.Timedelta(days=9)
    rev = rev.sort_values("avail")
    rev_map = {}
    for t, g in rev.groupby("Ticker"):
        yoy = g["YoY_pct_change"].to_numpy()
        yoy3 = pd.Series(yoy).rolling(3, min_periods=3).mean().to_numpy()
        arr = np.empty((len(g), 2), dtype=object)
        arr[:, 0] = g["avail"].to_numpy()
        arr[:, 1] = yoy3
        rev_map[t] = arr
    return fin_map, rev_map


def latest_before(arr, ts):
    """arr: Nx2 [avail_datetime, value]; return latest value with avail <= ts."""
    idx = np.searchsorted(arr[:, 0].astype("datetime64[ns]"), np.datetime64(ts), side="right") - 1
    if idx < 0:
        return None
    return arr[idx, 1]


def run(start_date: str, out_path: Path) -> None:
    fin_map, rev_map = load_fundamentals()
    rows = []
    files = sorted(DAILY_DIR.glob("*.csv"))
    start = pd.Timestamp(start_date)
    for i, f in enumerate(files):
        ticker = f.stem
        if not ticker.isdigit() or len(ticker) != 4:
            continue  # skip ETF/special listings; universe = 4-digit common stocks
        try:
            df = pd.read_csv(f, parse_dates=["Date"])
        except Exception:
            continue
        if not REQUIRED.issubset(df.columns) or len(df) < 120:
            continue
        df = df.sort_values("Date").reset_index(drop=True)

        close, ma20 = df["Close"], df["MA_20"]
        vol_ratio = df["Volume"] / df["VOL_MA_20"]
        sig = (
            (df["Date"] >= start)
            & ma20.notna()
            & ((close / ma20 - 1).abs() <= MA20_BAND)
            & (vol_ratio < VOL_RATIO_MAX)
            & (df["Foreign_BuySell"] > 0)
            & (df["Volume"] >= MIN_VOLUME_SHARES)
        )
        idxs = np.flatnonzero(sig.to_numpy())
        last_taken = -10**9
        opens = df["Open"].to_numpy()
        closes = df["Close"].to_numpy()
        dates = df["Date"].to_numpy()
        n = len(df)
        for idx in idxs:
            if idx - last_taken < COOLDOWN_DAYS:
                continue
            if idx + 1 >= n or idx + max(HOLD_DAYS) >= n:
                continue
            entry = opens[idx + 1]
            if not np.isfinite(entry) or entry <= 0:
                continue
            last_taken = idx
            ts = pd.Timestamp(dates[idx])
            om = latest_before(fin_map[ticker], ts) if ticker in fin_map else None
            yoy3 = latest_before(rev_map[ticker], ts) if ticker in rev_map else None
            row = {
                "ticker": ticker,
                "date": ts,
                "om": om,
                "yoy3": yoy3,
                "veto_om": om is not None and om < 0,
                "veto_rev": yoy3 is not None and np.isfinite(float(yoy3)) and float(yoy3) < REV_YOY_VETO,
            }
            for h in HOLD_DAYS:
                row[f"ret{h}"] = closes[idx + h] / entry - 1 - FRICTION
            rows.append(row)
        if (i + 1) % 400 == 0:
            print(f"  scanned {i + 1}/{len(files)} files, signals={len(rows)}")

    df = pd.DataFrame(rows)
    df["veto_any"] = df["veto_om"] | df["veto_rev"]
    print(f"total signals: {len(df)}  (veto_any={df['veto_any'].sum()}, "
          f"om={df['veto_om'].sum()}, rev={df['veto_rev'].sum()}, "
          f"no-fundamental-data={(df['om'].isna() & df['yoy3'].isna()).sum()})")

    def stats(sub: pd.DataFrame, label: str) -> list[str]:
        out = [f"### {label}  (n={len(sub)})", ""]
        out.append("| horizon | mean | median | win rate |")
        out.append("| --- | --- | --- | --- |")
        for h in HOLD_DAYS:
            r = sub[f"ret{h}"]
            out.append(f"| {h}d | {r.mean():+.2%} | {r.median():+.2%} | {(r > 0).mean():.1%} |")
        out.append("")
        return out

    lines = [
        "# Fundamental Veto Backtest (Serenity overlay evaluation)",
        "",
        f"Generated: {datetime.now():%Y-%m-%d %H:%M}. Research-only.",
        f"Base signal: 貼MA20 ±{MA20_BAND:.0%} + vol ratio <{VOL_RATIO_MAX} + foreign net buy",
        f"+ volume ≥{MIN_VOLUME_SHARES:,} shares, cooldown {COOLDOWN_DAYS}d, friction {FRICTION:.1%},",
        f"entry next-day open, start {start_date}. Point-in-time fundamentals",
        f"(quarterly filing deadlines; monthly revenue +10d lag).",
        "",
    ]
    lines += stats(df, "All base signals")
    lines += stats(df[~df["veto_any"]], "PASS (no fundamental veto)")
    lines += stats(df[df["veto_any"]], "VETOED (OM<0 or rev 3M-avg YoY < -20%)")
    lines += stats(df[df["veto_om"]], "veto detail: operating margin < 0")
    lines += stats(df[df["veto_rev"]], f"veto detail: revenue 3M-avg YoY < {REV_YOY_VETO:.0f}%")

    # yearly breakdown of the pass-vs-veto delta at 20d
    lines += ["### Yearly delta (PASS mean - VETO mean, 20d)", "",
              "| year | pass n | veto n | pass 20d | veto 20d | delta |",
              "| --- | --- | --- | --- | --- | --- |"]
    for y, g in df.groupby(df["date"].dt.year):
        p, v = g[~g["veto_any"]], g[g["veto_any"]]
        if len(p) == 0 or len(v) == 0:
            continue
        lines.append(
            f"| {y} | {len(p)} | {len(v)} | {p['ret20'].mean():+.2%} | "
            f"{v['ret20'].mean():+.2%} | {p['ret20'].mean() - v['ret20'].mean():+.2%} |")
    lines.append("")

    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"report -> {out_path}")
    print("\n".join(lines[8:40]))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default=START_DATE)
    ap.add_argument("-o", "--out", default=None)
    args = ap.parse_args()
    out = Path(args.out) if args.out else REPORT_DIR / f"fundamental_veto_backtest_{datetime.now():%Y%m%d}.md"
    run(args.start, out)
