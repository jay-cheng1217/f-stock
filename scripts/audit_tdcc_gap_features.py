"""Quantify RETRAIN-02 gap-aware TDCC feature changes."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd


BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from ml.dataset import _list_daily_tickers
from ml.features.tdcc import (
    TDCC_FEATURE_COLS,
    _load_tdcc_cache,
    compute_tdcc_features,
)


TDCC_PATH = BASE_DIR / "集保分散" / "tdcc_summary.csv"
WEEK_FREQ = "W-FRI"


@dataclass
class ImpactSummary:
    status: str
    source_first_date: str
    source_last_date: str
    source_distinct_weeks: int
    complete_grid_weeks: int
    global_missing_weeks: int
    training_tickers: int
    tickers_with_gaps: int
    ticker_gap_weeks: int
    observed_weekly_samples: int
    affected_observed_samples: int
    affected_observed_pct: float
    global_gap_neighborhood_pct: float
    old_value_to_unknown_cells: int
    no_gap_tickers: int
    no_gap_max_abs_delta: float
    no_gap_nan_mismatches: int
    continuous_prefix_samples: int
    continuous_prefix_max_abs_delta: float
    continuous_prefix_nan_mismatches: int


def _legacy_features(rows: pd.DataFrame) -> pd.DataFrame:
    tk = rows.sort_values("tdcc_date").reset_index(drop=True).copy()
    tk["retail_pct"] = tk["Retail_Pct"].astype(np.float32)
    tk["whale_pct"] = tk["Whale_Pct"].astype(np.float32)
    tk["whale_pct_chg"] = tk["whale_pct"].diff().astype(np.float32)
    tk["retail_pct_chg"] = tk["retail_pct"].diff().astype(np.float32)
    holders = tk["Total_Holders"].astype(np.float64)
    tk["holders_chg_pct"] = holders.ffill().pct_change(fill_method=None).astype(np.float32)
    tk["whale_retail_ratio"] = np.where(
        tk["retail_pct"] > 0.01,
        tk["whale_pct"] / tk["retail_pct"],
        np.nan,
    ).astype(np.float32)
    tk["whale_trend_4w"] = (
        tk["whale_pct"]
        .rolling(4, min_periods=2)
        .apply(lambda values: np.polyfit(range(len(values)), values, 1)[0], raw=False)
        .astype(np.float32)
    )
    changes = tk["whale_pct_chg"].fillna(0)
    streak = pd.Series(0, index=tk.index, dtype=np.int8)
    for index in range(1, len(streak)):
        streak.iloc[index] = (
            streak.iloc[index - 1] + 1 if changes.iloc[index] > 0 else 0
        )
    tk["whale_acc_weeks"] = streak.astype(np.float32)
    tk["whale_trend_8w"] = (
        tk["whale_pct"]
        .rolling(8, min_periods=4)
        .apply(lambda values: np.polyfit(range(len(values)), values, 1)[0], raw=False)
        .astype(np.float32)
    )
    tk["whale_acc_momentum"] = tk["whale_trend_4w"].diff().astype(np.float32)
    tk["whale_pct_rank_12w"] = (
        tk["whale_pct"].rolling(12, min_periods=4).rank(pct=True).astype(np.float32)
    )
    tk["whale_retail_diverge"] = (
        tk["whale_pct_chg"].fillna(0) - tk["retail_pct_chg"].fillna(0)
    ).astype(np.float32)
    tk["retail_capitulation"] = (
        tk["retail_pct"].rolling(12, min_periods=4).rank(pct=True).astype(np.float32)
    )
    return tk


def _changed_mask(old: pd.Series, new: pd.Series) -> np.ndarray:
    old_values = pd.to_numeric(old, errors="coerce").to_numpy(dtype=np.float64)
    new_values = pd.to_numeric(new, errors="coerce").to_numpy(dtype=np.float64)
    return ~np.isclose(old_values, new_values, atol=1e-9, rtol=0.0, equal_nan=True)


def _global_gap_neighborhood_pct(periods: pd.PeriodIndex) -> float:
    unique = periods.unique().sort_values()
    full = pd.period_range(unique.min(), unique.max(), freq=WEEK_FREQ)
    missing = full.difference(unique)
    affected = set()
    observed = set(unique)
    for gap in missing:
        gap_ordinal = gap.ordinal
        for candidate in unique:
            distance = candidate.ordinal - gap_ordinal
            if 1 <= distance <= 12 and candidate in observed:
                affected.add(candidate)
    return len(affected) / len(unique) * 100.0 if len(unique) else 0.0


def run_audit(max_tickers: int = 0) -> tuple[ImpactSummary, dict]:
    source = _load_tdcc_cache(str(TDCC_PATH))
    if source.empty:
        raise RuntimeError(f"TDCC summary is missing or invalid: {TDCC_PATH}")

    source = source.copy()
    source["_week"] = source["tdcc_date"].dt.to_period(WEEK_FREQ)
    source_periods = pd.PeriodIndex(source["_week"].dropna().unique(), freq=WEEK_FREQ)
    full_periods = pd.period_range(source_periods.min(), source_periods.max(), freq=WEEK_FREQ)
    global_missing = full_periods.difference(source_periods)

    training = set(_list_daily_tickers())
    available = sorted(training.intersection(source["Ticker"].astype(str).unique()))
    if max_tickers > 0:
        available = available[:max_tickers]

    affected_rows = 0
    observed_rows = 0
    old_to_unknown = 0
    ticker_gap_weeks = 0
    tickers_with_gaps = 0
    no_gap_tickers = 0
    no_gap_nan_mismatches = 0
    no_gap_max_abs_delta = 0.0
    continuous_prefix_samples = 0
    continuous_prefix_nan_mismatches = 0
    continuous_prefix_max_abs_delta = 0.0
    per_feature = {
        feature: {"affected_rows": 0, "old_value_to_unknown": 0}
        for feature in TDCC_FEATURE_COLS
    }

    grouped = source.loc[source["Ticker"].isin(available)].groupby("Ticker", sort=False)
    for ticker, rows in grouped:
        rows = rows.sort_values("tdcc_date").drop_duplicates("_week", keep="last")
        periods = pd.PeriodIndex(rows["_week"], freq=WEEK_FREQ)
        expected = periods.max().ordinal - periods.min().ordinal + 1
        gaps = int(expected - len(periods.unique()))
        period_ordinals = periods.asi8
        gap_after = np.flatnonzero(np.diff(period_ordinals) > 1)
        prefix_length = int(gap_after[0] + 1) if gap_after.size else len(periods)
        continuous_prefix_samples += prefix_length
        ticker_gap_weeks += gaps
        tickers_with_gaps += int(gaps > 0)
        no_gap = gaps == 0
        no_gap_tickers += int(no_gap)

        legacy = _legacy_features(rows)
        actual = compute_tdcc_features(
            pd.DataFrame({"Date": rows["tdcc_date"].to_numpy()}),
            str(ticker),
            str(TDCC_PATH),
        )
        observed_rows += len(legacy)
        row_changed = np.zeros(len(legacy), dtype=bool)

        for feature in TDCC_FEATURE_COLS:
            changed = _changed_mask(legacy[feature], actual[feature])
            row_changed |= changed
            per_feature[feature]["affected_rows"] += int(changed.sum())
            unknown = legacy[feature].notna().to_numpy() & actual[feature].isna().to_numpy()
            per_feature[feature]["old_value_to_unknown"] += int(unknown.sum())
            old_to_unknown += int(unknown.sum())

            if no_gap:
                old_values = pd.to_numeric(legacy[feature], errors="coerce").to_numpy(float)
                new_values = pd.to_numeric(actual[feature], errors="coerce").to_numpy(float)
                no_gap_nan_mismatches += int(
                    np.logical_xor(np.isnan(old_values), np.isnan(new_values)).sum()
                )
                both = np.isfinite(old_values) & np.isfinite(new_values)
                if both.any():
                    no_gap_max_abs_delta = max(
                        no_gap_max_abs_delta,
                        float(np.max(np.abs(old_values[both] - new_values[both]))),
                    )

            old_prefix = pd.to_numeric(
                legacy[feature].iloc[:prefix_length], errors="coerce"
            ).to_numpy(float)
            new_prefix = pd.to_numeric(
                actual[feature].iloc[:prefix_length], errors="coerce"
            ).to_numpy(float)
            continuous_prefix_nan_mismatches += int(
                np.logical_xor(np.isnan(old_prefix), np.isnan(new_prefix)).sum()
            )
            prefix_both = np.isfinite(old_prefix) & np.isfinite(new_prefix)
            if prefix_both.any():
                continuous_prefix_max_abs_delta = max(
                    continuous_prefix_max_abs_delta,
                    float(
                        np.max(
                            np.abs(old_prefix[prefix_both] - new_prefix[prefix_both])
                        )
                    ),
                )

        affected_rows += int(row_changed.sum())

    status = (
        "PASS"
        if len(global_missing) == 43
        and affected_rows > 0
        and continuous_prefix_samples > 0
        and continuous_prefix_nan_mismatches == 0
        and continuous_prefix_max_abs_delta < 1e-9
        else "STOP_REVIEW"
    )
    summary = ImpactSummary(
        status=status,
        source_first_date=source["tdcc_date"].min().strftime("%Y-%m-%d"),
        source_last_date=source["tdcc_date"].max().strftime("%Y-%m-%d"),
        source_distinct_weeks=len(source_periods),
        complete_grid_weeks=len(full_periods),
        global_missing_weeks=len(global_missing),
        training_tickers=len(available),
        tickers_with_gaps=tickers_with_gaps,
        ticker_gap_weeks=ticker_gap_weeks,
        observed_weekly_samples=observed_rows,
        affected_observed_samples=affected_rows,
        affected_observed_pct=(affected_rows / observed_rows * 100.0),
        global_gap_neighborhood_pct=_global_gap_neighborhood_pct(source_periods),
        old_value_to_unknown_cells=old_to_unknown,
        no_gap_tickers=no_gap_tickers,
        no_gap_max_abs_delta=no_gap_max_abs_delta,
        no_gap_nan_mismatches=no_gap_nan_mismatches,
        continuous_prefix_samples=continuous_prefix_samples,
        continuous_prefix_max_abs_delta=continuous_prefix_max_abs_delta,
        continuous_prefix_nan_mismatches=continuous_prefix_nan_mismatches,
    )
    details = {
        "global_missing_periods": [str(period) for period in global_missing],
        "per_feature": per_feature,
        "definitions": {
            "affected_observed_sample": "at least one TDCC feature changed on an observed week",
            "global_gap_neighborhood_pct": "observed global weeks within 12 weeks after a missing global week",
            "old_value_to_unknown": "legacy numeric value becomes NaN because a gap invalidates the calculation",
        },
    }
    return summary, details


def write_report(summary: ImpactSummary, details: dict, prefix: Path) -> None:
    prefix.parent.mkdir(parents=True, exist_ok=True)
    payload = {"summary": asdict(summary), **details}
    prefix.with_suffix(".json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    feature_rows = "\n".join(
        f"| `{feature}` | {values['affected_rows']:,} | "
        f"{values['old_value_to_unknown']:,} |"
        for feature, values in details["per_feature"].items()
    )
    markdown = f"""# RETRAIN-02 TDCC gap-aware feature impact

## Verdict

**{summary.status}**

## Scope

- Source: {summary.source_first_date} through {summary.source_last_date}
- Source weeks / complete weekly grid: {summary.source_distinct_weeks} / {summary.complete_grid_weeks}
- Global missing weeks: {summary.global_missing_weeks}
- Training tickers: {summary.training_tickers:,}
- Tickers with at least one gap: {summary.tickers_with_gaps:,}
- Total ticker-level missing weeks: {summary.ticker_gap_weeks:,}

## Impact

- Observed ticker-week samples: {summary.observed_weekly_samples:,}
- Affected observed samples: {summary.affected_observed_samples:,}
  ({summary.affected_observed_pct:.2f}%)
- Global 43-gap 12-week neighborhood share: {summary.global_gap_neighborhood_pct:.2f}%
- Legacy numeric cells correctly converted to unknown: {summary.old_value_to_unknown_cells:,}

| Feature | Affected observed rows | Old numeric to NaN |
|---|---:|---:|
{feature_rows}

## Invariants

- Tickers with no weekly gaps: {summary.no_gap_tickers:,}
- Maximum absolute delta on no-gap tickers: `{summary.no_gap_max_abs_delta:.3e}`
- NaN mismatches on no-gap tickers: {summary.no_gap_nan_mismatches}
- Observed samples before each ticker's first gap: {summary.continuous_prefix_samples:,}
- Maximum absolute delta in continuous prefixes: `{summary.continuous_prefix_max_abs_delta:.3e}`
- NaN mismatches in continuous prefixes: {summary.continuous_prefix_nan_mismatches}
- Thursday holiday observations retain their actual effective date; only missing weeks use Friday placeholders.

## Reproduction

```powershell
python scripts/audit_tdcc_gap_features.py
```
"""
    prefix.with_suffix(".md").write_text(markdown, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-tickers", type=int, default=0)
    parser.add_argument(
        "--report-prefix",
        type=Path,
        default=BASE_DIR / "ml" / "reports" / f"retrain02_tdcc_gap_impact_{date.today():%Y%m%d}",
    )
    args = parser.parse_args()
    summary, details = run_audit(max_tickers=args.max_tickers)
    write_report(summary, details, args.report_prefix)
    print(json.dumps(asdict(summary), ensure_ascii=False, indent=2))
    if summary.status != "PASS":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
