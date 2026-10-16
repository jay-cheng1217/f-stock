"""Historical high-risk-sector OVERHEAT threshold audit.

Research-only. This script does not use production prediction scores. It asks:
for high-risk cyclical sectors, what happened after price_vs_ma60 exceeded
20%, 25%, or 30%?

Each event uses the canonical trade window:
    Entry = Open[t+1], Exit = Close[t+20]
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))

from ml.config import DAILY_K_DIR, INDEX_DIR, REPORT_DIR  # noqa: E402

HIGH_RISK_GROUPS = {
    "petrochemical": {
        "label": "塑化 / 石化",
        "keywords": ["塑膠", "化工", "化學", "石化"],
    },
    "steel_metal": {
        "label": "鋼鐵 / 金屬",
        "keywords": ["鋼鐵", "金屬"],
    },
    "shipping": {
        "label": "航運",
        "keywords": ["航運", "貨運"],
    },
}
THRESHOLDS = [0.20, 0.25, 0.30]


def _safe_float(value: Any) -> float | None:
    try:
        number = float(value)
    except Exception:
        return None
    return number if math.isfinite(number) else None


def _fmt_pct(value: Any, digits: int = 2) -> str:
    number = _safe_float(value)
    if number is None:
        return "-"
    return f"{number * 100:+.{digits}f}%"


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _json_safe(val) for key, val in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    return value


def _load_sector_map() -> pd.DataFrame:
    path = BASE_DIR / "ml" / "data" / "sector_mapping.csv"
    df = pd.read_csv(path, dtype={"Ticker": str})
    df["Ticker"] = df["Ticker"].astype(str).str.upper()
    df["Sector"] = df["Sector"].fillna("").astype(str)
    rows = []
    for row in df.to_dict("records"):
        sector = row["Sector"]
        for group, meta in HIGH_RISK_GROUPS.items():
            if any(keyword in sector for keyword in meta["keywords"]):
                rows.append(
                    {
                        "ticker": row["Ticker"],
                        "name": row.get("Name"),
                        "sector": sector,
                        "risk_group": group,
                        "risk_group_label": meta["label"],
                    }
                )
                break
    return pd.DataFrame(rows)


def _load_twii() -> pd.DataFrame:
    path = Path(INDEX_DIR) / "index_TWII.csv"
    df = pd.read_csv(path, dtype={"Date": str})
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    return df.dropna(subset=["Date"]).sort_values("Date").reset_index(drop=True)


def _twii_forward_return(twii: pd.DataFrame, prediction_date: pd.Timestamp, days: int = 20) -> tuple[float | None, str | None]:
    future = twii[twii["Date"] > prediction_date].reset_index(drop=True)
    if len(future) < days:
        return None, None
    entry_open = _safe_float(future.iloc[0].get("Open", future.iloc[0].get("Close")))
    exit_close = _safe_float(future.iloc[days - 1].get("Close"))
    if entry_open is None or entry_open <= 0 or exit_close is None:
        return None, None
    return exit_close / entry_open - 1.0, pd.Timestamp(future.iloc[days - 1]["Date"]).date().isoformat()


def _ticker_events(row: dict[str, Any], start: str, end: str, twii: pd.DataFrame) -> list[dict[str, Any]]:
    path = Path(DAILY_K_DIR) / f"{row['ticker']}.csv"
    if not path.exists():
        return []
    try:
        px = pd.read_csv(path, dtype={"Date": str})
    except Exception:
        return []
    px["Date"] = pd.to_datetime(px["Date"], errors="coerce")
    for col in ["Open", "Close", "MA_60"]:
        if col in px.columns:
            px[col] = pd.to_numeric(px[col], errors="coerce")
    if "MA_60" not in px.columns:
        px["MA_60"] = px["Close"].rolling(60, min_periods=60).mean()
    px = px.dropna(subset=["Date", "Open", "Close", "MA_60"]).sort_values("Date").reset_index(drop=True)
    px["price_vs_ma60"] = px["Close"] / px["MA_60"] - 1.0
    mask = (px["Date"] >= pd.Timestamp(start)) & (px["Date"] <= pd.Timestamp(end))
    px = px.loc[mask].reset_index(drop=True)
    events: list[dict[str, Any]] = []
    for idx, current in px.iterrows():
        prediction_date = pd.Timestamp(current["Date"])
        future = px[px["Date"] > prediction_date].reset_index(drop=True)
        if len(future) < 20:
            continue
        entry_open = _safe_float(future.iloc[0]["Open"])
        exit_close = _safe_float(future.iloc[19]["Close"])
        if entry_open is None or entry_open <= 0 or exit_close is None:
            continue
        stock_return = exit_close / entry_open - 1.0
        twii_return, exit_date = _twii_forward_return(twii, prediction_date, 20)
        if twii_return is None:
            continue
        events.append(
            {
                "ticker": row["ticker"],
                "name": row.get("name"),
                "sector": row["sector"],
                "risk_group": row["risk_group"],
                "risk_group_label": row["risk_group_label"],
                "prediction_date": prediction_date.date().isoformat(),
                "exit_date": exit_date,
                "price_vs_ma60": float(current["price_vs_ma60"]),
                "forward_20d_return_pct": stock_return,
                "twii_20d_return_pct": twii_return,
                "alpha_pct": stock_return - twii_return,
            }
        )
    return events


def _max_drawdown(equity: pd.Series) -> float:
    if equity.empty:
        return 0.0
    peak = equity.cummax()
    return float((equity / peak - 1.0).min())


def _summarize(events: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows = []
    daily_rows = []
    for threshold in THRESHOLDS:
        sub = events[events["price_vs_ma60"] > threshold].copy()
        daily = (
            sub.groupby("prediction_date", as_index=False)
            .agg(
                event_count=("ticker", "count"),
                basket_return_pct=("forward_20d_return_pct", "mean"),
                basket_alpha_pct=("alpha_pct", "mean"),
            )
            .sort_values("prediction_date")
        )
        daily["threshold"] = threshold
        daily_rows.append(daily)
        monthly = pd.Series(dtype=float)
        if not daily.empty:
            month_index = pd.to_datetime(daily["prediction_date"]).dt.to_period("M")
            monthly = daily.groupby(month_index)["basket_return_pct"].mean()
        equity = (1.0 + monthly).cumprod() if not monthly.empty else pd.Series(dtype=float)
        rows.append(
            {
                "threshold": threshold,
                "trigger_count": int(len(sub)),
                "unique_tickers": int(sub["ticker"].nunique()) if not sub.empty else 0,
                "signal_days": int(daily["prediction_date"].nunique()) if not daily.empty else 0,
                "avg_forward_20d_return_pct": float(sub["forward_20d_return_pct"].mean()) if not sub.empty else None,
                "median_forward_20d_return_pct": float(sub["forward_20d_return_pct"].median()) if not sub.empty else None,
                "positive_rate": float((sub["forward_20d_return_pct"] > 0).mean()) if not sub.empty else None,
                "avg_alpha_pct": float(sub["alpha_pct"].mean()) if not sub.empty else None,
                "daily_basket_compound_return_pct": float(equity.iloc[-1] - 1.0) if not equity.empty else None,
                "mdd_pct": _max_drawdown(equity),
            }
        )
        for group, group_sub in sub.groupby("risk_group_label"):
            rows.append(
                {
                    "threshold": threshold,
                    "risk_group_label": group,
                    "trigger_count": int(len(group_sub)),
                    "unique_tickers": int(group_sub["ticker"].nunique()),
                    "signal_days": int(group_sub["prediction_date"].nunique()),
                    "avg_forward_20d_return_pct": float(group_sub["forward_20d_return_pct"].mean()),
                    "median_forward_20d_return_pct": float(group_sub["forward_20d_return_pct"].median()),
                    "positive_rate": float((group_sub["forward_20d_return_pct"] > 0).mean()),
                    "avg_alpha_pct": float(group_sub["alpha_pct"].mean()),
                    "daily_basket_compound_return_pct": None,
                    "mdd_pct": None,
                }
            )
    return pd.DataFrame(rows), pd.concat(daily_rows, ignore_index=True) if daily_rows else pd.DataFrame()


def _write_md(path: Path, *, summary: pd.DataFrame, metadata: dict[str, Any]) -> None:
    overall = summary[summary["risk_group_label"].isna()] if "risk_group_label" in summary.columns else summary
    by_group = summary[summary["risk_group_label"].notna()] if "risk_group_label" in summary.columns else pd.DataFrame()
    lines = [
        "# High-Risk Sector OVERHEAT Threshold Historical Audit",
        "",
        f"- Generated at: `{metadata['generated_at']}`",
        f"- Window: `{metadata['start']}` to `{metadata['end']}`",
        f"- High-risk tickers scanned: `{metadata['ticker_count']}`",
        f"- Full 20D event rows: `{metadata['event_count']}`",
        "- Canonical event return: `Open[t+1] -> Close[t+20]`",
        "",
        "## Threshold Summary",
        "",
        "| threshold | triggers | tickers | days | avg 20D | median 20D | positive | avg alpha | MDD |",
        "| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in overall.to_dict("records"):
        lines.append(
            f"| {_fmt_pct(row['threshold'], 0)} | {row['trigger_count']} | {row['unique_tickers']} | "
            f"{row['signal_days']} | {_fmt_pct(row['avg_forward_20d_return_pct'])} | "
            f"{_fmt_pct(row['median_forward_20d_return_pct'])} | {_fmt_pct(row['positive_rate'])} | "
            f"{_fmt_pct(row['avg_alpha_pct'])} | {_fmt_pct(row['mdd_pct'])} |"
        )
    lines.extend(
        [
            "",
            "## By Group",
            "",
            "| threshold | group | triggers | tickers | avg 20D | positive | avg alpha |",
            "| ---: | --- | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for row in by_group.to_dict("records"):
        lines.append(
            f"| {_fmt_pct(row['threshold'], 0)} | {row['risk_group_label']} | {row['trigger_count']} | "
            f"{row['unique_tickers']} | {_fmt_pct(row['avg_forward_20d_return_pct'])} | "
            f"{_fmt_pct(row['positive_rate'])} | {_fmt_pct(row['avg_alpha_pct'])} |"
        )
    lines.extend(
        [
            "",
            "## Notes",
            "",
            "- This is an event study over high-risk sectors, not a production signal replay.",
            "- It answers whether lower sector thresholds historically captured bad forward returns in these sectors.",
            "- MDD is computed on monthly averaged event baskets to avoid double-counting heavily overlapping 20D event windows.",
            "- Production promotion still needs same-snapshot signal-level A/B if this gate changes live selection.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def run(args: argparse.Namespace) -> dict[str, Any]:
    sectors = _load_sector_map()
    twii = _load_twii()
    rows: list[dict[str, Any]] = []
    for idx, row in enumerate(sectors.to_dict("records"), start=1):
        if idx % 50 == 0:
            print(f"[sector-overheat] scanned {idx}/{len(sectors)}", flush=True)
        rows.extend(_ticker_events(row, args.start, args.end, twii))
    events = pd.DataFrame(rows)
    summary, daily = _summarize(events)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    prefix = args.output_prefix
    events_path = output_dir / f"{prefix}_events.csv"
    summary_path = output_dir / f"{prefix}_summary.csv"
    daily_path = output_dir / f"{prefix}_daily.csv"
    md_path = output_dir / f"{prefix}.md"
    json_path = output_dir / f"{prefix}.json"
    metadata = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "start": args.start,
        "end": args.end,
        "ticker_count": int(len(sectors)),
        "event_count": int(len(events)),
        "events_csv": str(events_path),
        "summary_csv": str(summary_path),
        "daily_csv": str(daily_path),
    }
    events.to_csv(events_path, index=False, encoding="utf-8-sig")
    summary.to_csv(summary_path, index=False, encoding="utf-8-sig")
    daily.to_csv(daily_path, index=False, encoding="utf-8-sig")
    payload = _json_safe({"metadata": metadata, "summary": summary.to_dict("records")})
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_md(md_path, summary=summary, metadata=metadata)
    print(summary[summary.get("risk_group_label").isna()].to_string(index=False))
    print(f"[sector-overheat] wrote {md_path}")
    return metadata


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Historical high-risk sector OVERHEAT threshold audit.")
    parser.add_argument("--start", default="2024-01-01")
    parser.add_argument("--end", default="2026-05-08")
    parser.add_argument("--output-dir", default=REPORT_DIR)
    parser.add_argument("--output-prefix", default="sector_overheat_threshold_history_20260508")
    return parser.parse_args()


if __name__ == "__main__":
    run(parse_args())
