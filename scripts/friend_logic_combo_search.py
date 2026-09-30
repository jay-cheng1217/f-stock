"""Search combinations around the friend ChipK-style entry logic.

This research script treats the shared pullback/flow logic as a candidate
generator and tests additional local filters already used elsewhere in the
system: institutional flow, MACD, MA structure, volume, RSI/KD, and financing
crowding proxies. It is offline/read-only and does not alter production gates.
"""

from __future__ import annotations

import argparse
import itertools
import json
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SECTOR_MAPPING_PATH = ROOT / "ml" / "data" / "sector_mapping.csv"

from scripts.friend_chipk_logic_backtest import (
    BacktestConfig,
    DEFAULT_MAX_SIGNALS_PER_DAY,
    FRICTION,
    REPORT_DIR,
    _format_pct,
    _load_universe,
    _resolve_daily_dir,
)

BASE_RULES = ["friend_pullback_limit", "friend_base", "friend_loose", "friend_strict"]
DEFAULT_HOLD = 60
DEFAULT_MIN_TRADES = 200
DEFAULT_MAX_OVERLAYS = 3


@dataclass(frozen=True)
class ComboResult:
    combo_id: str
    base_rule: str
    overlays: tuple[str, ...]
    trades: int
    signal_days: int
    avg_net_return: float | None
    median_net_return: float | None
    win_rate: float | None
    basket_cum_return: float | None
    basket_max_drawdown: float | None
    p10_net_return: float | None
    worst_net_return: float | None
    tail_loss_rate: float | None
    robust_score: float | None


def _num(data: pd.DataFrame, column: str, default: float = np.nan) -> pd.Series:
    if column in data.columns:
        return pd.to_numeric(data[column], errors="coerce")
    return pd.Series(default, index=data.index, dtype="float64")


def _text_value(value: object) -> str:
    if value is None or pd.isna(value):
        return ""
    return str(value)


def load_stock_metadata(path: Path = SECTOR_MAPPING_PATH) -> tuple[dict[str, str], dict[str, str]]:
    if not path.exists():
        return {}, {}
    mapping = pd.read_csv(path, dtype=str).fillna("")
    required = {"Ticker", "Name", "Sector"}
    if not required <= set(mapping.columns):
        return {}, {}
    tickers = mapping["Ticker"].astype(str).str.strip().str.zfill(4)
    names = dict(zip(tickers, mapping["Name"].astype(str).str.strip()))
    sectors = dict(zip(tickers, mapping["Sector"].astype(str).str.strip()))
    return names, sectors


def enrich_combo_features(data: pd.DataFrame) -> pd.DataFrame:
    out = data.copy()
    ticker_key = out["ticker"].astype(str).str.strip().str.zfill(4)
    stock_names, sectors = load_stock_metadata()
    if "stock_name" not in out.columns:
        out["stock_name"] = ticker_key.map(stock_names).fillna("")
    if "sector" not in out.columns:
        out["sector"] = ticker_key.map(sectors).fillna("其他")
    out["volume_ratio_20d"] = _num(out, "Volume") / _num(out, "VOL_MA_20").replace(0, np.nan)
    out["ma60_distance"] = _num(out, "Close") / _num(out, "MA_60").replace(0, np.nan) - 1.0
    out["ma20_distance"] = _num(out, "Close") / _num(out, "MA_20").replace(0, np.nan) - 1.0
    out["macd_hist"] = _num(out, "MACDh_12_26_9")
    out["rsi14"] = _num(out, "RSI_14")
    out["kd_k"] = _num(out, "K")
    out["kd_d"] = _num(out, "D")
    out["margin_balance"] = _num(out, "Margin_Balance")
    out["margin_5d_change"] = (
        out.sort_values(["ticker", "Date"])
        .groupby("ticker", observed=True)["margin_balance"]
        .diff(5)
        .reindex(out.index)
    )
    out["flow_strength"] = _num(out, "inst_total") / _num(out, "Volume").replace(0, np.nan)
    return out


def build_overlay_masks(data: pd.DataFrame) -> dict[str, pd.Series]:
    masks = {
        "flow_inst_positive": _num(data, "inst_total").gt(0),
        "flow_foreign_positive": _num(data, "Foreign_BuySell").gt(0),
        "flow_dealer_nonnegative": _num(data, "Dealer_BuySell").fillna(0).ge(0),
        "flow_strength_positive": _num(data, "flow_strength").gt(0.02),
        "macd_improving": data.get("macd_improving", pd.Series(False, index=data.index)).fillna(False).astype(bool),
        "macd_3d_delta_positive": _num(data, "macd_hist_delta_3d").gt(0),
        "macd_hist_gt_neg1": _num(data, "macd_hist").ge(-1.0),
        "macd_hist_positive": _num(data, "macd_hist").ge(0),
        "ma5_reclaim": _num(data, "Close").ge(_num(data, "MA_5")),
        "ma60_tight_support": _num(data, "ma60_distance").between(-0.01, 0.045),
        "ma20_not_far_below": _num(data, "ma20_distance").ge(-0.08),
        "volume_quiet": _num(data, "volume_ratio_20d").between(0.25, 0.80),
        "volume_confirmed": _num(data, "volume_ratio_20d").between(0.40, 1.20),
        "rsi_mid": _num(data, "rsi14").between(40, 62),
        "rsi_not_hot": _num(data, "rsi14").le(70),
        "kd_bullish": _num(data, "kd_k").gt(_num(data, "kd_d")),
        "margin_not_rising": _num(data, "margin_5d_change").le(0),
        "no_chase_return_2pct": _num(data, "return_1d").le(0.02),
    }
    return {name: mask.fillna(False).astype(bool) for name, mask in masks.items()}


def _apply_pullback_execution(signals: pd.DataFrame) -> pd.DataFrame:
    out = signals.copy()
    out["entry_open"] = out["pullback_entry_price"]
    for hold in [5, 10, 20, 60]:
        pullback_col = f"pullback_gross_return_{hold}d"
        if pullback_col in out.columns:
            out[f"gross_return_{hold}d"] = out[pullback_col]
    return out


def select_combo(
    data: pd.DataFrame,
    *,
    base_rule: str,
    overlays: tuple[str, ...],
    overlay_masks: dict[str, pd.Series],
    max_signals_per_day: int,
) -> pd.DataFrame:
    mask = data[base_rule].fillna(False).astype(bool)
    for overlay in overlays:
        mask &= overlay_masks[overlay]
    signals = data[mask].copy()
    if base_rule == "friend_pullback_limit" and not signals.empty:
        signals = _apply_pullback_execution(signals)
    signals = signals.dropna(subset=["entry_open"])
    if signals.empty:
        return signals
    ordered = signals.sort_values(["Date", "friend_logic_score", "ticker"], ascending=[True, False, True])
    if max_signals_per_day > 0:
        ordered = ordered.groupby("Date", observed=True).head(max_signals_per_day)
    return ordered.reset_index(drop=True)


def summarize_combo(
    selected: pd.DataFrame,
    *,
    hold: int = DEFAULT_HOLD,
    friction: float = FRICTION,
) -> dict[str, Any]:
    ret_col = f"gross_return_{hold}d"
    trades = selected.dropna(subset=[ret_col]).copy()
    if trades.empty:
        return {
            "trades": 0,
            "signal_days": 0,
            "avg_net_return": None,
            "median_net_return": None,
            "win_rate": None,
            "basket_cum_return": None,
            "basket_max_drawdown": None,
            "p10_net_return": None,
            "worst_net_return": None,
            "tail_loss_rate": None,
            "robust_score": None,
        }
    trades["net_return"] = trades[ret_col] - friction
    daily = trades.groupby("Date", observed=True)["net_return"].mean().sort_index()
    returns = trades["net_return"]
    avg = float(trades["net_return"].mean())
    median = float(trades["net_return"].median())
    p10 = float(returns.quantile(0.10))
    worst = float(returns.min())
    tail_loss_rate = float(returns.lt(-0.10).mean())
    win_rate = float(trades["net_return"].gt(0).mean())
    robust_score = avg + 0.50 * median + 0.25 * p10 + 0.10 * (win_rate - 0.45) - 0.05 * tail_loss_rate
    return {
        "trades": int(len(trades)),
        "signal_days": int(daily.shape[0]),
        "avg_net_return": avg,
        "median_net_return": median,
        "win_rate": win_rate,
        "basket_cum_return": None,
        "basket_max_drawdown": None,
        "p10_net_return": p10,
        "worst_net_return": worst,
        "tail_loss_rate": tail_loss_rate,
        "robust_score": robust_score,
    }


def extreme_trades_for_combo(
    selected: pd.DataFrame,
    *,
    hold: int = DEFAULT_HOLD,
    friction: float = FRICTION,
    top_n: int = 10,
) -> dict[str, list[dict[str, Any]]]:
    ret_col = f"gross_return_{hold}d"
    exit_date_col = f"exit_date_{hold}d"
    exit_close_col = f"exit_close_{hold}d"
    trades = selected.dropna(subset=[ret_col, "entry_open"]).copy()
    if trades.empty:
        return {"top": [], "bottom": []}
    trades["net_return"] = trades[ret_col] - friction
    ordered = trades.sort_values(["net_return", "Date", "ticker"], ascending=[False, True, True])

    def payload(row: pd.Series) -> dict[str, Any]:
        return {
            "ticker": str(row["ticker"]).zfill(4),
            "stock_name": _text_value(row.get("stock_name")),
            "sector": _text_value(row.get("sector")),
            "signal_date": pd.Timestamp(row["Date"]).date().isoformat(),
            "entry_date": pd.Timestamp(row["entry_date"]).date().isoformat()
            if pd.notna(row.get("entry_date"))
            else None,
            "entry_price": float(row["entry_open"]) if pd.notna(row.get("entry_open")) else None,
            "exit_date": pd.Timestamp(row[exit_date_col]).date().isoformat()
            if exit_date_col in row and pd.notna(row.get(exit_date_col))
            else None,
            "exit_close": float(row[exit_close_col])
            if exit_close_col in row and pd.notna(row.get(exit_close_col))
            else None,
            "net_return": float(row["net_return"]),
            "foreign": float(row["Foreign_BuySell"]) if pd.notna(row.get("Foreign_BuySell")) else None,
            "dealer": float(row["Dealer_BuySell"]) if pd.notna(row.get("Dealer_BuySell")) else None,
            "inst_total": float(row["inst_total"]) if pd.notna(row.get("inst_total")) else None,
            "macd_hist": float(row["MACDh_12_26_9"]) if pd.notna(row.get("MACDh_12_26_9")) else None,
            "volume_ratio_20d": float(row["volume_ratio_20d"]) if pd.notna(row.get("volume_ratio_20d")) else None,
        }

    return {
        "top": [payload(row) for _, row in ordered.head(top_n).iterrows()],
        "bottom": [
            payload(row)
            for _, row in ordered.tail(top_n).sort_values(["net_return", "Date", "ticker"]).iterrows()
        ],
    }


def search_combinations(
    data: pd.DataFrame,
    *,
    max_overlays: int = DEFAULT_MAX_OVERLAYS,
    max_signals_per_day: int = DEFAULT_MAX_SIGNALS_PER_DAY,
    hold: int = DEFAULT_HOLD,
    friction: float = FRICTION,
) -> tuple[pd.DataFrame, dict[str, pd.DataFrame]]:
    overlay_masks = build_overlay_masks(data)
    overlay_names = sorted(overlay_masks)
    rows: list[dict[str, Any]] = []
    selected_by_combo: dict[str, pd.DataFrame] = {}
    for base_rule in BASE_RULES:
        for size in range(0, max_overlays + 1):
            for overlays in itertools.combinations(overlay_names, size):
                selected = select_combo(
                    data,
                    base_rule=base_rule,
                    overlays=overlays,
                    overlay_masks=overlay_masks,
                    max_signals_per_day=max_signals_per_day,
                )
                stats = summarize_combo(selected, hold=hold, friction=friction)
                combo_id = base_rule if not overlays else f"{base_rule}+" + "+".join(overlays)
                rows.append(
                    {
                        "combo_id": combo_id,
                        "base_rule": base_rule,
                        "overlays": ",".join(overlays),
                        **stats,
                    }
                )
                selected_by_combo[combo_id] = selected
    results = pd.DataFrame(rows)
    return results, selected_by_combo


def _row_to_public(row: pd.Series) -> dict[str, Any]:
    out = row.to_dict()
    for key, value in list(out.items()):
        if pd.isna(value):
            out[key] = None
    return out


def _top_rows(results: pd.DataFrame, sort_col: str, min_trades: int, n: int = 15) -> list[dict[str, Any]]:
    eligible = results[pd.to_numeric(results["trades"], errors="coerce").fillna(0).ge(min_trades)].copy()
    eligible = eligible.dropna(subset=[sort_col])
    if eligible.empty:
        return []
    ordered = eligible.sort_values([sort_col, "trades"], ascending=[False, False]).head(n)
    return [_row_to_public(row) for _, row in ordered.iterrows()]


def write_report(
    *,
    asof: str,
    daily_dir: Path,
    config: BacktestConfig,
    results: pd.DataFrame,
    selected_by_combo: dict[str, pd.DataFrame],
    min_trades: int,
    max_overlays: int,
) -> dict[str, str]:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    json_path = REPORT_DIR / f"friend_logic_combo_search_{asof}.json"
    md_path = REPORT_DIR / f"friend_logic_combo_search_{asof}.md"
    csv_path = REPORT_DIR / f"friend_logic_combo_search_{asof}.csv"
    latest_json = REPORT_DIR / "friend_logic_combo_search_latest.json"
    latest_md = REPORT_DIR / "friend_logic_combo_search_latest.md"
    latest_csv = REPORT_DIR / "friend_logic_combo_search_latest.csv"

    max_avg = _top_rows(results, "avg_net_return", min_trades, n=15)
    robust = _top_rows(results, "robust_score", min_trades, n=15)
    baseline = [
        _row_to_public(row)
        for _, row in results[results["overlays"].eq("")]
        .sort_values(["avg_net_return", "trades"], ascending=[False, False])
        .iterrows()
    ]
    best_combo_id = max_avg[0]["combo_id"] if max_avg else None
    robust_combo_id = robust[0]["combo_id"] if robust else None
    best_extremes = (
        extreme_trades_for_combo(selected_by_combo[best_combo_id], hold=DEFAULT_HOLD, friction=config.friction)
        if best_combo_id
        else {"top": [], "bottom": []}
    )
    robust_extremes = (
        extreme_trades_for_combo(selected_by_combo[robust_combo_id], hold=DEFAULT_HOLD, friction=config.friction)
        if robust_combo_id
        else {"top": [], "bottom": []}
    )

    payload = {
        "asof": asof,
        "daily_dir": str(daily_dir),
        "config": {
            "start_date": config.start_date,
            "end_date": config.end_date,
            "max_signals_per_day": config.max_signals_per_day,
            "friction": config.friction,
            "hold_days": list(config.hold_days),
            "min_trades": min_trades,
        },
        "baseline_rules": baseline,
        "max_avg_60d_top": max_avg,
        "robust_60d_top": robust,
        "best_combo_extremes_60d": best_extremes,
        "robust_combo_extremes_60d": robust_extremes,
    }
    json_text = json.dumps(payload, ensure_ascii=False, indent=2)
    json_path.write_text(json_text, encoding="utf-8")
    latest_json.write_text(json_text, encoding="utf-8")
    results.to_csv(csv_path, index=False, encoding="utf-8-sig")
    results.to_csv(latest_csv, index=False, encoding="utf-8-sig")

    def combo_table(rows: list[dict[str, Any]]) -> list[str]:
        lines = [
            "| Rank | Base | Overlays | Trades | Avg 60D | Median | P10 | Win | Robust | Worst |",
            "|---:|---|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
        for idx, row in enumerate(rows[:10], start=1):
            lines.append(
                f"| {idx} | {row['base_rule']} | {row.get('overlays') or '-'} | {row['trades']} | "
                f"{_format_pct(row['avg_net_return'])} | {_format_pct(row['median_net_return'])} | "
                f"{_format_pct(row['p10_net_return'])} | "
                f"{_format_pct(row['win_rate'])} | {_format_pct(row['robust_score'])} | "
                f"{_format_pct(row['worst_net_return'])} |"
            )
        return lines

    def baseline_table(rows: list[dict[str, Any]]) -> list[str]:
        lines = [
            "| Base Rule | Trades | Avg 60D | Median | P10 | Win | Robust | Worst |",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
        for row in rows:
            lines.append(
                f"| {row['base_rule']} | {row['trades']} | {_format_pct(row['avg_net_return'])} | "
                f"{_format_pct(row['median_net_return'])} | {_format_pct(row['p10_net_return'])} | "
                f"{_format_pct(row['win_rate'])} | {_format_pct(row['robust_score'])} | "
                f"{_format_pct(row['worst_net_return'])} |"
            )
        return lines

    def extreme_table(title: str, rows: list[dict[str, Any]]) -> list[str]:
        lines = [
            f"### {title}",
            "",
            "| Name | Sector | Ticker | Signal | Entry | Entry Px | Exit | Exit Px | Net 60D | Foreign | Dealer | Inst Total | MACD | Vol/20D |",
            "|---|---|---|---|---|---:|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
        for row in rows:
            lines.append(
                f"| {row['stock_name'] or '-'} | {row['sector'] or '-'} | {row['ticker']} | "
                f"{row['signal_date']} | {row['entry_date']} | "
                f"{row['entry_price']:.2f} | {row['exit_date']} | {row['exit_close']:.2f} | "
                f"{_format_pct(row['net_return'])} | {row['foreign']:.0f} | {row['dealer']:.0f} | "
                f"{row['inst_total']:.0f} | {row['macd_hist']:.3f} | {row['volume_ratio_20d']:.2f} |"
            )
        return lines

    lines = [
        "# Friend Logic Combination Search",
        "",
        f"- Generated: `{asof}`",
        f"- Daily data dir: `{daily_dir}`",
        f"- Window: `{config.start_date}` to `{config.end_date or 'latest'}`",
        f"- Objective: maximize `{DEFAULT_HOLD}D` net return after `{config.friction:.2%}` friction",
        f"- Minimum trades for ranking: `{min_trades}`",
        f"- Max overlays tested: `{max_overlays}`",
        f"- Max selected per signal day: `{config.max_signals_per_day}`",
        "",
        "## Max Avg 60D",
        "",
        *combo_table(max_avg),
        "",
        "## Robust 60D",
        "",
        *combo_table(robust),
        "",
        "## Baseline Rules",
        "",
        *baseline_table(baseline),
        "",
        "## SA Interpretation",
        "",
        f"- Best average and robust combo: `{best_combo_id or 'n/a'}`.",
        "- The profitable combination is not generic ChipK chasing. It is a support-limit entry after a pullback, then only when MACD histogram is already positive, MACD is improving, and volume is quiet rather than crowded.",
        "- Compared with the raw `friend_pullback_limit` baseline, the best combo trades far less often but materially improves average 60D return and robust score.",
        "- Median 60D return remains slightly negative and tail loss is still meaningful, so this is a high-upside swing filter, not a standalone all-in production gate.",
        "",
        "## Best Avg Combo Extremes",
        "",
    ]
    if best_combo_id:
        lines.append(f"- Combo: `{best_combo_id}`")
        lines.append("")
        lines.extend(extreme_table("Top 10", best_extremes["top"]))
        lines.append("")
        lines.extend(extreme_table("Bottom 10", best_extremes["bottom"]))
    lines.extend(
        [
            "",
            "## Robust Combo Extremes",
            "",
        ]
    )
    if robust_combo_id:
        lines.append(f"- Combo: `{robust_combo_id}`")
        lines.append("")
        lines.extend(extreme_table("Top 10", robust_extremes["top"]))
        lines.append("")
        lines.extend(extreme_table("Bottom 10", robust_extremes["bottom"]))
    lines.extend(
        [
            "",
            "## Guardrails",
            "",
            "- Offline research only; no production gate, scheduler, model, DB schema, or paper portfolio mutation.",
            "- The search intentionally caps overlays at 3 and requires minimum sample size to reduce overfit.",
            "- `Max Avg 60D` can still be right-tail dominated. Treat `Robust 60D` as the safer operational candidate.",
            "- Risk columns use per-trade P10 and worst 60D return; overlapping 60D trades are not compounded as an equity curve.",
            "- Historical ChipK app broker/main-force fields are unavailable; overlays use local institutional flow, technical, volume, RSI/KD, and margin proxies.",
        ]
    )
    md_text = "\n".join(lines) + "\n"
    md_path.write_text(md_text, encoding="utf-8")
    latest_md.write_text(md_text, encoding="utf-8")
    return {
        "json": str(json_path),
        "md": str(md_path),
        "csv": str(csv_path),
        "latest_json": str(latest_json),
        "latest_md": str(latest_md),
        "latest_csv": str(latest_csv),
    }


def run_search(
    *,
    daily_dir: Path,
    config: BacktestConfig,
    min_trades: int,
    max_overlays: int,
    asof: str | None = None,
) -> dict[str, Any]:
    data = enrich_combo_features(_load_universe(daily_dir, config))
    results, selected_by_combo = search_combinations(
        data,
        max_overlays=max_overlays,
        max_signals_per_day=config.max_signals_per_day,
        hold=DEFAULT_HOLD,
        friction=config.friction,
    )
    asof_token = asof or datetime.now().strftime("%Y%m%d_%H%M%S")
    paths = write_report(
        asof=asof_token,
        daily_dir=daily_dir,
        config=config,
        results=results,
        selected_by_combo=selected_by_combo,
        min_trades=min_trades,
        max_overlays=max_overlays,
    )
    return {"asof": asof_token, "paths": paths}


def main() -> None:
    parser = argparse.ArgumentParser(description="Search overlays around friend ChipK-style logic.")
    parser.add_argument("--daily-dir", default=None)
    parser.add_argument("--start-date", default="2024-01-01")
    parser.add_argument("--end-date", default=None)
    parser.add_argument("--max-signals-per-day", type=int, default=DEFAULT_MAX_SIGNALS_PER_DAY)
    parser.add_argument("--min-trades", type=int, default=DEFAULT_MIN_TRADES)
    parser.add_argument("--max-overlays", type=int, default=DEFAULT_MAX_OVERLAYS)
    parser.add_argument("--asof", default=None)
    args = parser.parse_args()

    config = BacktestConfig(
        start_date=args.start_date,
        end_date=args.end_date,
        max_signals_per_day=args.max_signals_per_day,
    )
    result = run_search(
        daily_dir=_resolve_daily_dir(args.daily_dir),
        config=config,
        min_trades=args.min_trades,
        max_overlays=args.max_overlays,
        asof=args.asof,
    )
    print(json.dumps(result["paths"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
