"""REQ-004 signal-level same-snapshot replay for sector OVERHEAT thresholds.

Research-only. It rebuilds walk-forward monthly prediction snapshots, applies
high-risk sector OVERHEAT thresholds before Top30 selection, and compares the
actual selected baskets on the exact same model scores.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import lightgbm as lgb
import numpy as np
import pandas as pd

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))

from ml.config import REPORT_DIR  # noqa: E402
from ml.dataset import build_dataset, get_feature_columns  # noqa: E402
from ml.predict import apply_sector_cap  # noqa: E402
from scripts.train_v2 import V2_EARLY_STOPPING, V2_EMBARGO_TRADING_DAYS, V2_NUM_ROUNDS, V2_PARAMS, V2_TRAIN_MONTHS  # noqa: E402

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
THRESHOLDS = [0.20, 0.25, 0.30, 0.40]


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


def _load_sector_map() -> dict[str, str]:
    path = BASE_DIR / "ml" / "data" / "sector_mapping.csv"
    df = pd.read_csv(path, dtype={"Ticker": str})
    return dict(zip(df["Ticker"].astype(str), df["Sector"].fillna("").astype(str)))


def _risk_group(sector: str) -> str | None:
    for group, meta in HIGH_RISK_GROUPS.items():
        if any(keyword in sector for keyword in meta["keywords"]):
            return group
    return None


def _max_drawdown(returns: pd.Series) -> float:
    if returns.empty:
        return 0.0
    equity = (1.0 + returns.fillna(0.0)).cumprod()
    drawdown = equity / equity.cummax() - 1.0
    return float(drawdown.min())


def _train_month_snapshot(dataset: pd.DataFrame, feature_cols: list[str], month_start: pd.Timestamp) -> pd.DataFrame:
    target_col = "trade_excess_return_20d"
    test_end = month_start + pd.offsets.MonthEnd(0)
    train_start = month_start - pd.DateOffset(months=V2_TRAIN_MONTHS)
    unique_dates = pd.Index(dataset["Date"].drop_duplicates().sort_values())
    test = dataset[(dataset["Date"] >= month_start) & (dataset["Date"] <= test_end)]
    if test.empty:
        raise ValueError(f"No test rows for {month_start:%Y-%m}")
    first_test_date = pd.Timestamp(test["Date"].min())
    test_start_idx = unique_dates.get_indexer([first_test_date])[0]
    if test_start_idx < V2_EMBARGO_TRADING_DAYS:
        raise ValueError(f"Not enough embargo history for {month_start:%Y-%m}")
    embargo_cutoff = unique_dates[test_start_idx - V2_EMBARGO_TRADING_DAYS]
    train = dataset[(dataset["Date"] >= train_start) & (dataset["Date"] < embargo_cutoff)]
    if len(train) < 10000:
        raise ValueError(f"Too few train rows for {month_start:%Y-%m}: {len(train)}")

    params = V2_PARAMS.copy()
    params.pop("device", None)
    params["n_jobs"] = -1
    train_data = lgb.Dataset(train[feature_cols].values, label=train[target_col].values)
    val_data = lgb.Dataset(test[feature_cols].values, label=test[target_col].values, reference=train_data)
    model = lgb.train(
        params,
        train_data,
        num_boost_round=V2_NUM_ROUNDS,
        valid_sets=[val_data],
        callbacks=[lgb.early_stopping(V2_EARLY_STOPPING, verbose=False)],
    )
    scored = test[
        [
            "ticker",
            "Date",
            "Close",
            "price_vs_ma60",
            "trade_return_20d",
            "trade_excess_return_20d",
        ]
    ].copy()
    scored["leaderboard_score"] = model.predict(test[feature_cols].values)
    snapshot = scored[scored["Date"] == scored["Date"].max()].copy()
    return snapshot.reset_index(drop=True)


def _select_variant(snapshot: pd.DataFrame, threshold: float) -> pd.DataFrame:
    candidate = snapshot.copy()
    block = (
        candidate["risk_group"].notna()
        & pd.to_numeric(candidate["price_vs_ma60"], errors="coerce").gt(threshold)
    )
    candidate = candidate.loc[~block].copy()
    selected = apply_sector_cap(candidate, top_n=30).head(30).copy()
    selected["threshold"] = threshold
    return selected


def run(args: argparse.Namespace) -> dict[str, str]:
    dataset = build_dataset(verbose=False).dropna(subset=["trade_excess_return_20d"]).copy()
    feature_cols = [c for c in get_feature_columns() if c in dataset.columns]
    sector_map = _load_sector_map()
    test_starts = pd.date_range(args.start, args.end, freq="MS")

    selection_rows: list[pd.DataFrame] = []
    trigger_rows: list[dict[str, Any]] = []
    diff_rows: list[dict[str, Any]] = []

    for month_start in test_starts:
        if month_start > dataset["Date"].max():
            continue
        month = month_start.strftime("%Y-%m")
        print(f"[sector-signal-replay] training {month}")
        snapshot = _train_month_snapshot(dataset, feature_cols, month_start)
        prediction_date = pd.Timestamp(snapshot["Date"].max()).date().isoformat()
        snapshot["ticker"] = snapshot["ticker"].astype(str)
        snapshot["sector"] = snapshot["ticker"].map(sector_map).fillna("")
        snapshot["recommendation"] = "建議買進"
        snapshot["risk_group"] = snapshot["sector"].map(_risk_group)
        snapshot["risk_group_label"] = snapshot["risk_group"].map(
            {name: meta["label"] for name, meta in HIGH_RISK_GROUPS.items()}
        )
        raw_top30 = set(apply_sector_cap(snapshot, top_n=30).head(30)["ticker"].astype(str))

        selected_by_threshold: dict[float, set[str]] = {}
        for threshold in THRESHOLDS:
            overheat = snapshot["risk_group"].notna() & pd.to_numeric(
                snapshot["price_vs_ma60"], errors="coerce"
            ).gt(threshold)
            trigger_rows.append(
                {
                    "month": month,
                    "prediction_date": prediction_date,
                    "threshold": threshold,
                    "trigger_count": int(overheat.sum()),
                    "raw_top30_trigger_count": int(snapshot.loc[overheat, "ticker"].isin(raw_top30).sum()),
                }
            )
            selected = _select_variant(snapshot, threshold)
            selected["month"] = month
            selected["prediction_date"] = prediction_date
            selected["risk_group_label"] = selected["risk_group_label"].fillna("")
            selected_by_threshold[threshold] = set(selected["ticker"].astype(str))
            selection_rows.append(selected)

        baseline = selected_by_threshold[0.40]
        for threshold in [0.20, 0.25, 0.30]:
            chosen = selected_by_threshold[threshold]
            diff_rows.append(
                {
                    "month": month,
                    "prediction_date": prediction_date,
                    "threshold": threshold,
                    "overlap_vs_40": len(chosen & baseline),
                    "added_vs_40": ",".join(sorted(chosen - baseline)),
                    "removed_vs_40": ",".join(sorted(baseline - chosen)),
                }
            )

    selections = pd.concat(selection_rows, ignore_index=True) if selection_rows else pd.DataFrame()
    triggers = pd.DataFrame(trigger_rows)
    diffs = pd.DataFrame(diff_rows)

    summary_rows: list[dict[str, Any]] = []
    for threshold, group in selections.groupby("threshold"):
        monthly = (
            group.groupby("month")
            .agg(
                basket_return=("trade_return_20d", "mean"),
                basket_alpha=("trade_excess_return_20d", "mean"),
                selected_count=("ticker", "count"),
            )
            .reset_index()
        )
        trigger_subset = triggers[triggers["threshold"] == threshold]
        summary_rows.append(
            {
                "threshold": threshold,
                "months": int(monthly["month"].nunique()),
                "trigger_count": int(trigger_subset["trigger_count"].sum()),
                "raw_top30_trigger_count": int(trigger_subset["raw_top30_trigger_count"].sum()),
                "avg_monthly_return": float(monthly["basket_return"].mean()),
                "avg_monthly_alpha": float(monthly["basket_alpha"].mean()),
                "median_selected_return": float(group["trade_return_20d"].median()),
                "positive_rate": float((group["trade_return_20d"] > 0).mean()),
                "mdd": _max_drawdown(monthly["basket_return"]),
            }
        )
    summary = pd.DataFrame(summary_rows).sort_values("threshold")

    out_base = Path(REPORT_DIR) / args.output_prefix
    summary_path = out_base.with_name(f"{args.output_prefix}_summary.csv")
    selections_path = out_base.with_name(f"{args.output_prefix}_selections.csv")
    triggers_path = out_base.with_name(f"{args.output_prefix}_triggers.csv")
    diffs_path = out_base.with_name(f"{args.output_prefix}_diffs.csv")
    md_path = out_base.with_suffix(".md")
    json_path = out_base.with_suffix(".json")

    summary.to_csv(summary_path, index=False, encoding="utf-8-sig")
    selections.to_csv(selections_path, index=False, encoding="utf-8-sig")
    triggers.to_csv(triggers_path, index=False, encoding="utf-8-sig")
    diffs.to_csv(diffs_path, index=False, encoding="utf-8-sig")
    json_path.write_text(
        json.dumps(
            {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "summary_csv": str(summary_path),
                "selections_csv": str(selections_path),
                "triggers_csv": str(triggers_path),
                "diffs_csv": str(diffs_path),
                "summary": summary.to_dict("records"),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    lines = [
        "# REQ-004 Sector OVERHEAT Signal-Level Replay",
        "",
        f"- Window: {args.start} to {args.end}",
        "- Same monthly model-score snapshot for all threshold variants.",
        "- `40%` is included as the current/default context baseline.",
        "",
        "| Threshold | Triggers | Raw Top30 triggers | Monthly ret | Alpha | Median selected | Positive | MDD |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in summary.to_dict("records"):
        lines.append(
            f"| {int(row['threshold'] * 100)}% | {row['trigger_count']} | {row['raw_top30_trigger_count']} | "
            f"{_fmt_pct(row['avg_monthly_return'])} | {_fmt_pct(row['avg_monthly_alpha'])} | "
            f"{_fmt_pct(row['median_selected_return'])} | {_fmt_pct(row['positive_rate'])} | {_fmt_pct(row['mdd'])} |"
        )
    lines.extend(
        [
            "",
            "## Notes",
            "",
            "- This is signal-level A/B on regenerated walk-forward folds, not a live artifact replay.",
            "- Thresholds apply only to high-risk sectors: plastics/petrochemical, shipping, steel/metal.",
            "- Selection diffs versus the 40% context baseline are in the `_diffs.csv` artifact.",
        ]
    )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[sector-signal-replay] wrote {md_path}")
    return {
        "md": str(md_path),
        "summary_csv": str(summary_path),
        "selections_csv": str(selections_path),
        "triggers_csv": str(triggers_path),
        "diffs_csv": str(diffs_path),
        "json": str(json_path),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="REQ-004 sector OVERHEAT signal-level replay.")
    parser.add_argument("--start", default="2024-01-01")
    parser.add_argument("--end", default="2026-04-01")
    parser.add_argument("--output-prefix", default="req004_sector_overheat_signal_replay_20260508")
    return parser.parse_args()


if __name__ == "__main__":
    run(parse_args())
