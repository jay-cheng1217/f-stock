"""Backtest strong momentum-continuation entry setups.

This offline artifact mirrors the production shortwave momentum lane in
``ml.entry_shortwave``. It models a practical next-session entry:

1. Detect a setup after the close.
2. Place a next-session limit near the prior close / MA5 support area.
3. Count the trade only if the next session trades through the limit.

Gap-up chases above the allowed zone are treated as missed trades.

.. warning:: **Research-only — 結果不得當正式策略績效**（2026-09-23 審查判定）:
   本回測與正式規則存在已知口徑差異:(1) 跌幅門檻/風險過濾/排序與 production
   shortwave lane 不同步;(2) 正式介面只允許訊號首日新進場,本回測對連續訊號日
   全數納入;(3) 報酬使用未還原價格(無公司行動調整)。對齊前只能作方向性參考。
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

BASE_DIR = Path(__file__).resolve().parents[1]
DEFAULT_DAILY_DIR = BASE_DIR / "\u65e5K\u8cc7\u6599"
REPORT_DIR = BASE_DIR / "ml" / "reports"
SECTOR_MAPPING_PATH = BASE_DIR / "ml" / "data" / "sector_mapping.csv"

DEFAULT_HOLD_DAYS = (1, 2, 3, 5, 10, 20)
DEFAULT_FRICTION = 0.004
DEFAULT_MAX_SIGNALS_PER_DAY = 10
STRATEGY_VERSION = "momentum_continuation_v1_20260625"


@dataclass(frozen=True)
class BacktestConfig:
    start_date: str = "2024-01-01"
    end_date: str | None = None
    max_signals_per_day: int = DEFAULT_MAX_SIGNALS_PER_DAY
    friction: float = DEFAULT_FRICTION
    hold_days: tuple[int, ...] = DEFAULT_HOLD_DAYS
    asof: str = "20260625"


def _num(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame.columns:
        return pd.Series(np.nan, index=frame.index, dtype="float64")
    return pd.to_numeric(frame[column], errors="coerce")


def _fmt_pct(value: float | None) -> str:
    if value is None or pd.isna(value):
        return "-"
    return f"{float(value) * 100:+.2f}%"


def _fmt_num(value: float | None, digits: int = 2) -> str:
    if value is None or pd.isna(value):
        return "-"
    return f"{float(value):.{digits}f}"


def load_stock_metadata(path: Path = SECTOR_MAPPING_PATH) -> tuple[dict[str, str], dict[str, str]]:
    if not path.exists():
        return {}, {}
    mapping = pd.read_csv(path, dtype={"Ticker": str})
    required = {"Ticker", "Name", "Sector"}
    if not required.issubset(mapping.columns):
        return {}, {}
    tickers = mapping["Ticker"].astype(str).str.strip().str.zfill(4)
    names = dict(zip(tickers, mapping["Name"].fillna("").astype(str).str.strip()))
    sectors = dict(zip(tickers, mapping["Sector"].fillna("").astype(str).str.strip()))
    return names, sectors


def prepare_stock_frame(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    if "Date" not in df.columns:
        return pd.DataFrame()
    required = {
        "Open",
        "High",
        "Low",
        "Close",
        "Volume",
        "MA_5",
        "MA_20",
        "MA_60",
        "RSI_14",
        "MACDh_12_26_9",
        "K",
        "D",
        "VOL_MA_20",
    }
    if not required.issubset(df.columns):
        return pd.DataFrame()

    out = df.copy()
    out["Date"] = pd.to_datetime(out["Date"], errors="coerce")
    out = out.dropna(subset=["Date"]).sort_values("Date").reset_index(drop=True)
    for column in required:
        out[column] = pd.to_numeric(out[column], errors="coerce")
    ticker = path.stem.strip()
    if ticker.isdigit() and len(ticker) <= 4:
        ticker = ticker.zfill(4)
    out["ticker"] = ticker
    return out


def annotate_momentum_continuation(df: pd.DataFrame, friction: float = DEFAULT_FRICTION) -> pd.DataFrame:
    out = df.copy()
    if out.empty:
        return out

    close = _num(out, "Close")
    open_ = _num(out, "Open")
    high = _num(out, "High")
    low = _num(out, "Low")
    volume = _num(out, "Volume")
    vol_ma20 = _num(out, "VOL_MA_20").replace(0, np.nan)
    ma5 = _num(out, "MA_5")
    ma20 = _num(out, "MA_20")
    ma60 = _num(out, "MA_60")
    macd = _num(out, "MACDh_12_26_9")
    rsi14 = _num(out, "RSI_14")
    kd_k = _num(out, "K")
    kd_d = _num(out, "D")

    out["return_1d"] = close / close.shift(1).replace(0, np.nan) - 1.0
    out["return_5d"] = close / close.shift(5).replace(0, np.nan) - 1.0
    out["volume_ratio_20d"] = volume / vol_ma20
    out["macd_hist_delta_3d"] = macd - macd.shift(3)
    out["price_vs_ma5"] = close / ma5.replace(0, np.nan) - 1.0
    out["price_vs_ma20"] = close / ma20.replace(0, np.nan) - 1.0
    out["price_vs_ma60"] = close / ma60.replace(0, np.nan) - 1.0

    trend_stack = (ma5.gt(ma20 * 1.01) & ma20.gt(ma60 * 1.02)).fillna(False)
    near_ma5 = out["price_vs_ma5"].between(-0.035, 0.075).fillna(False)
    strong_not_vertical = (
        out["price_vs_ma20"].between(0.055, 0.36)
        & out["price_vs_ma60"].between(0.18, 1.25)
    ).fillna(False)
    volume_confirmed = out["volume_ratio_20d"].between(0.75, 5.5).fillna(False)
    macd_positive = macd.gt(0).fillna(False)
    macd_ok = (out["macd_hist_delta_3d"].ge(-0.75) | out["macd_hist_delta_3d"].isna()).fillna(False)
    rsi_ok = rsi14.between(48, 86).fillna(False)
    kd_ok = (kd_k.gt(kd_d) | kd_k.between(45, 92)).fillna(False)
    liquid = close.ge(10) & volume.ge(500_000)
    blowoff = out["return_1d"].gt(0.095) & close.gt(ma5 * 1.08) & out["volume_ratio_20d"].gt(3.0)
    crash_day = out["return_1d"].lt(-0.08)

    out["momentum_setup"] = (
        liquid.fillna(False)
        & near_ma5
        & trend_stack
        & strong_not_vertical
        & volume_confirmed
        & macd_positive
        & macd_ok
        & rsi_ok
        & kd_ok
        & ~blowoff.fillna(False)
        & ~crash_day.fillna(False)
    )
    setup = out["momentum_setup"].fillna(False).astype(bool)
    setup_group = (~setup).cumsum()
    out["momentum_setup_age_days"] = setup.groupby(setup_group).cumsum() - 1
    out.loc[~setup, "momentum_setup_age_days"] = np.nan

    trend_score = (
        (out["price_vs_ma20"].clip(0.055, 0.36) - 0.055) / (0.36 - 0.055)
    ).clip(0, 1).fillna(0.0)
    ma5_score = (1.0 - out["price_vs_ma5"].abs() / 0.075).clip(0, 1).fillna(0.0)
    volume_score = (1.0 - (out["volume_ratio_20d"] - 1.8).abs() / 3.7).clip(0, 1).fillna(0.0)
    macd_score = (macd.gt(0).astype(float) * 0.65 + out["macd_hist_delta_3d"].ge(0).astype(float) * 0.35)
    rsi_score = (1.0 - (rsi14 - 67.0).abs() / 30.0).clip(0, 1).fillna(0.0)
    kd_score = np.where(kd_k.gt(kd_d), 1.0, np.where(kd_k.between(45, 92), 0.65, 0.0))
    out["momentum_score"] = (
        0.24 * trend_score
        + 0.20 * ma5_score
        + 0.18 * volume_score
        + 0.16 * macd_score
        + 0.12 * rsi_score
        + 0.10 * pd.Series(kd_score, index=out.index)
    ).round(6)

    out["momentum_entry_limit"] = close
    out["momentum_entry_high"] = close * 1.015
    ma5_anchor_low = np.maximum(ma5 * 0.995, close * 0.965)
    close_anchor_low = close * 0.985
    out["momentum_entry_low"] = np.where(close.ge(ma5), ma5_anchor_low, close_anchor_low)
    out["momentum_entry_low"] = np.minimum(out["momentum_entry_low"], out["momentum_entry_high"])
    out["entry_date"] = out["Date"].shift(-1)
    next_open = open_.shift(-1)
    next_high = high.shift(-1)
    next_low = low.shift(-1)

    fill_open = next_open.between(out["momentum_entry_low"], out["momentum_entry_high"])
    fill_limit = next_low.le(out["momentum_entry_limit"]) & next_high.ge(out["momentum_entry_limit"])
    out["momentum_filled"] = out["momentum_setup"] & (fill_open | fill_limit).fillna(False)
    out["momentum_entry_price"] = np.where(fill_open, next_open, out["momentum_entry_limit"])
    out.loc[~out["momentum_filled"], "momentum_entry_price"] = np.nan
    # 高開後盤中回踩到 limit 仍算成交;「錯過」= 高開且全日未回踩(與 filled 互斥,
    # 2026-09-23 審查修正:原本兩者重疊,成交/錯過統計雙重計數)
    out["missed_gap_up"] = (out["momentum_setup"]
                            & next_open.gt(out["momentum_entry_high"]).fillna(False)
                            & ~out["momentum_filled"])

    entry_price = pd.Series(out["momentum_entry_price"], index=out.index).replace(0, np.nan)
    for hold in DEFAULT_HOLD_DAYS:
        exit_close = close.shift(-hold)
        future_high = high.shift(-1).rolling(hold, min_periods=1).max().shift(-(hold - 1))
        future_low = low.shift(-1).rolling(hold, min_periods=1).min().shift(-(hold - 1))
        out[f"gross_return_{hold}d"] = exit_close / entry_price - 1.0
        out[f"net_return_{hold}d"] = out[f"gross_return_{hold}d"] - friction
        out[f"mfe_{hold}d"] = future_high / entry_price - 1.0
        out[f"mae_{hold}d"] = future_low / entry_price - 1.0
    return out


def load_universe(daily_dir: Path, config: BacktestConfig) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    start = pd.Timestamp(config.start_date)
    end = pd.Timestamp(config.end_date) if config.end_date else None
    for path in sorted(daily_dir.glob("*.csv")):
        frame = prepare_stock_frame(path)
        if frame.empty:
            continue
        frame = annotate_momentum_continuation(frame, friction=config.friction)
        frame = frame.loc[frame["Date"].ge(start)]
        if end is not None:
            frame = frame.loc[frame["Date"].le(end)]
        if not frame.empty:
            frames.append(frame)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def select_daily_trades(universe: pd.DataFrame, config: BacktestConfig) -> pd.DataFrame:
    if universe.empty:
        return pd.DataFrame()
    setups = universe.loc[universe["momentum_setup"]].copy()
    if setups.empty:
        return setups
    setups = setups.sort_values(["Date", "momentum_score"], ascending=[True, False])
    setups["daily_rank"] = setups.groupby("Date").cumcount() + 1
    return setups.loc[setups["daily_rank"].le(config.max_signals_per_day)].copy()


def summarize_trades(trades: pd.DataFrame, config: BacktestConfig) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    filled = trades.loc[trades["momentum_filled"]].copy() if not trades.empty else pd.DataFrame()
    for hold in config.hold_days:
        column = f"net_return_{hold}d"
        if filled.empty or column not in filled.columns:
            rows.append({"hold_days": hold, "n": 0})
            continue
        returns = pd.to_numeric(filled[column], errors="coerce").dropna()
        rows.append(
            {
                "hold_days": hold,
                "n": int(len(returns)),
                "mean_return": float(returns.mean()) if len(returns) else None,
                "median_return": float(returns.median()) if len(returns) else None,
                "win_rate": float(returns.gt(0).mean()) if len(returns) else None,
                "p25": float(returns.quantile(0.25)) if len(returns) else None,
                "p75": float(returns.quantile(0.75)) if len(returns) else None,
            }
        )
    return rows


def latest_setups(universe: pd.DataFrame, names: dict[str, str], sectors: dict[str, str], limit: int = 30) -> pd.DataFrame:
    if universe.empty:
        return pd.DataFrame()
    latest_date = universe["Date"].max()
    rows = universe.loc[
        universe["Date"].eq(latest_date)
        & universe["momentum_setup"]
        & pd.to_numeric(universe.get("momentum_setup_age_days"), errors="coerce").fillna(0).eq(0)
    ].copy()
    if rows.empty:
        return rows
    rows["name"] = rows["ticker"].map(names).fillna("")
    rows["sector"] = rows["ticker"].map(sectors).fillna("")
    rows = rows.sort_values("momentum_score", ascending=False).head(limit)
    keep = [
        "Date",
        "ticker",
        "name",
        "sector",
        "Close",
        "momentum_score",
        "momentum_setup_age_days",
        "momentum_entry_low",
        "momentum_entry_limit",
        "momentum_entry_high",
        "price_vs_ma5",
        "price_vs_ma20",
        "price_vs_ma60",
        "volume_ratio_20d",
        "RSI_14",
        "MACDh_12_26_9",
        "missed_gap_up",
    ]
    return rows.loc[:, [column for column in keep if column in rows.columns]]


def _records(df: pd.DataFrame) -> list[dict[str, Any]]:
    if df.empty:
        return []
    out = df.copy()
    for column in out.columns:
        if pd.api.types.is_datetime64_any_dtype(out[column]):
            out[column] = out[column].dt.strftime("%Y-%m-%d")
    return json.loads(out.replace({np.nan: None}).to_json(orient="records", force_ascii=False))


def write_reports(
    universe: pd.DataFrame,
    trades: pd.DataFrame,
    summary: list[dict[str, Any]],
    latest: pd.DataFrame,
    config: BacktestConfig,
) -> tuple[Path, Path, Path]:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    json_path = REPORT_DIR / f"momentum_continuation_backtest_{config.asof}.json"
    md_path = REPORT_DIR / f"momentum_continuation_backtest_{config.asof}.md"
    csv_path = REPORT_DIR / f"momentum_continuation_backtest_{config.asof}_latest_setups.csv"

    payload = {
        "strategy_version": STRATEGY_VERSION,
        "config": asdict(config),
        "rows": int(len(universe)),
        "setup_count": int(trades["momentum_setup"].sum()) if "momentum_setup" in trades.columns else 0,
        "filled_count": int(trades["momentum_filled"].sum()) if "momentum_filled" in trades.columns else 0,
        "missed_gap_up_count": int(trades["missed_gap_up"].sum()) if "missed_gap_up" in trades.columns else 0,
        "summary": summary,
        "latest_setups": _records(latest),
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    latest.to_csv(csv_path, index=False, encoding="utf-8-sig")

    lines = [
        f"# Momentum Continuation Backtest {config.asof}",
        "",
        f"- strategy_version: `{STRATEGY_VERSION}`",
        f"- rows: {payload['rows']}",
        f"- setups selected: {payload['setup_count']}",
        f"- filled trades: {payload['filled_count']}",
        f"- missed gap-up trades: {payload['missed_gap_up_count']}",
        "",
        "## Hold Return Summary",
        "",
        "| Hold | N | Mean | Median | Win Rate | P25 | P75 |",
        "|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in summary:
        lines.append(
            "| {hold_days} | {n} | {mean} | {median} | {win} | {p25} | {p75} |".format(
                hold_days=row.get("hold_days"),
                n=row.get("n", 0),
                mean=_fmt_pct(row.get("mean_return")),
                median=_fmt_pct(row.get("median_return")),
                win=_fmt_pct(row.get("win_rate")),
                p25=_fmt_pct(row.get("p25")),
                p75=_fmt_pct(row.get("p75")),
            )
        )
    lines.extend(["", "## Latest Setups", ""])
    if latest.empty:
        lines.append("No latest setup.")
    else:
        lines.extend(
            [
                "| Ticker | Name | Sector | Close | Score | Entry Zone | MA5% | MA20% | MA60% | Vol Ratio | RSI | MACD |",
                "|---|---|---|---:|---:|---|---:|---:|---:|---:|---:|---:|",
            ]
        )
        for _, row in latest.head(20).iterrows():
            lines.append(
                "| {ticker} | {name} | {sector} | {close} | {score} | {low}-{high} | {ma5} | {ma20} | {ma60} | {vol} | {rsi} | {macd} |".format(
                    ticker=row.get("ticker", ""),
                    name=row.get("name", ""),
                    sector=row.get("sector", ""),
                    close=_fmt_num(row.get("Close")),
                    score=_fmt_num(row.get("momentum_score"), 3),
                    low=_fmt_num(row.get("momentum_entry_low")),
                    high=_fmt_num(row.get("momentum_entry_high")),
                    ma5=_fmt_pct(row.get("price_vs_ma5")),
                    ma20=_fmt_pct(row.get("price_vs_ma20")),
                    ma60=_fmt_pct(row.get("price_vs_ma60")),
                    vol=_fmt_num(row.get("volume_ratio_20d"), 2),
                    rsi=_fmt_num(row.get("RSI_14"), 1),
                    macd=_fmt_num(row.get("MACDh_12_26_9"), 2),
                )
            )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, md_path, csv_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--daily-dir", type=Path, default=DEFAULT_DAILY_DIR)
    parser.add_argument("--start-date", default="2024-01-01")
    parser.add_argument("--end-date", default=None)
    parser.add_argument("--max-signals-per-day", type=int, default=DEFAULT_MAX_SIGNALS_PER_DAY)
    parser.add_argument("--friction", type=float, default=DEFAULT_FRICTION)
    parser.add_argument("--asof", default="20260625")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = BacktestConfig(
        start_date=args.start_date,
        end_date=args.end_date,
        max_signals_per_day=args.max_signals_per_day,
        friction=args.friction,
        asof=args.asof,
    )
    names, sectors = load_stock_metadata()
    universe = load_universe(args.daily_dir, config)
    trades = select_daily_trades(universe, config)
    summary = summarize_trades(trades, config)
    latest = latest_setups(universe, names, sectors)
    json_path, md_path, csv_path = write_reports(universe, trades, summary, latest, config)
    print(json.dumps({"json": str(json_path), "md": str(md_path), "latest_csv": str(csv_path)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
