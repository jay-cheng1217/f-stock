"""Quantify RETRAIN-01 label changes on the historical training universe."""

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

from ml.config import DAILY_K_DIR, DOWN_THRESHOLD, UP_THRESHOLD
from ml.corporate_actions import action_window_factor, load_action_calendar
from ml.dataset import _asof_eligibility_mask, _list_daily_tickers, _load_twii
from ml.target import V2_FORWARD_DAYS, compute_target


@dataclass
class ImpactSummary:
    status: str
    scope_start: str
    scope_end: str
    tickers: int
    samples_20d: int
    affected_20d_samples: int
    affected_20d_pct: float
    affected_20d_mean_delta_pp: float
    affected_20d_median_delta_pp: float
    affected_20d_gt_3pp_pct: float
    affected_20d_gt_5pp_pct: float
    v1_samples: int
    v1_any_class_flip_pct: float
    v1_up_down_direct_flip_pct: float
    zero_event_max_abs_delta: float
    prior_affected_pct: float
    prior_mean_delta_pp: float


def _classify(excess: pd.Series) -> pd.Series:
    target = pd.Series(1, index=excess.index, dtype="int8")
    target.loc[excess <= DOWN_THRESHOLD] = 0
    target.loc[excess >= UP_THRESHOLD] = 2
    target.loc[excess.isna()] = -1
    return target


def run_audit(start: str, end: str, max_stocks: int = 0) -> tuple[ImpactSummary, dict]:
    start_ts = pd.Timestamp(start)
    end_ts = pd.Timestamp(end)
    calendar = load_action_calendar()
    twii = _load_twii(require_total_return=True)
    twii = twii.loc[twii["Date"].between(start_ts, end_ts)].copy()
    twii_price_only = twii[["Date", "twii_close"]].copy()

    tickers = _list_daily_tickers()
    if max_stocks > 0:
        tickers = tickers[:max_stocks]

    delta_20d_parts: list[pd.Series] = []
    event_20d_parts: list[pd.Series] = []
    old_v1_parts: list[pd.Series] = []
    new_v1_parts: list[pd.Series] = []
    zero_event_max = 0.0
    used_tickers = 0

    for ticker in tickers:
        path = Path(DAILY_K_DIR) / f"{ticker}.csv"
        try:
            prices = pd.read_csv(
                path,
                usecols=["Date", "Open", "Close", "Volume"],
                dtype={"Date": str},
            )
        except (FileNotFoundError, ValueError):
            continue
        prices["Date"] = pd.to_datetime(prices["Date"], errors="coerce")
        prices = (
            prices.dropna(subset=["Date"])
            .sort_values("Date")
            .drop_duplicates("Date", keep="last")
            .reset_index(drop=True)
        )
        if prices.empty:
            continue

        old = compute_target(prices, twii_price_only)
        new = compute_target(
            prices,
            twii,
            ticker=ticker,
            exdiv_df=calendar,
        )
        eligible = _asof_eligibility_mask(prices)
        scope = prices["Date"].between(start_ts, end_ts) & eligible

        old_20d = pd.to_numeric(old["forward_return_20d"], errors="coerce")
        new_20d = pd.to_numeric(new["forward_return_20d"], errors="coerce")
        valid_20d = scope & old_20d.notna() & new_20d.notna()
        if valid_20d.any():
            delta = (new_20d - old_20d).loc[valid_20d]
            factor = action_window_factor(
                prices["Date"], ticker, 0, V2_FORWARD_DAYS, calendar
            ).loc[valid_20d]
            event_mask = ~np.isclose(factor.to_numpy(dtype=float), 1.0, atol=1e-12)
            delta_20d_parts.append(delta.reset_index(drop=True))
            event_20d_parts.append(pd.Series(event_mask))
            if (~event_mask).any():
                zero_event_max = max(
                    zero_event_max,
                    float(np.abs(delta.to_numpy()[~event_mask]).max(initial=0.0)),
                )

        old_excess = pd.to_numeric(old["excess_return"], errors="coerce")
        new_excess = pd.to_numeric(new["excess_return"], errors="coerce")
        valid_v1 = scope & old_excess.notna() & new_excess.notna()
        if valid_v1.any():
            old_v1_parts.append(_classify(old_excess.loc[valid_v1]).reset_index(drop=True))
            new_v1_parts.append(_classify(new_excess.loc[valid_v1]).reset_index(drop=True))
        used_tickers += 1

    if not delta_20d_parts or not old_v1_parts:
        raise RuntimeError("audit produced no valid training-label samples")

    delta_20d = pd.concat(delta_20d_parts, ignore_index=True)
    event_20d = pd.concat(event_20d_parts, ignore_index=True).astype(bool)
    event_delta_pp = delta_20d.loc[event_20d] * 100.0
    old_v1 = pd.concat(old_v1_parts, ignore_index=True)
    new_v1 = pd.concat(new_v1_parts, ignore_index=True)
    any_flip = old_v1.ne(new_v1)
    direct_flip = ((old_v1 == 0) & (new_v1 == 2)) | ((old_v1 == 2) & (new_v1 == 0))

    affected_pct = float(event_20d.mean() * 100.0)
    mean_delta_pp = float(event_delta_pp.mean())
    prior_affected = 9.51
    prior_delta = 3.37
    ratios = [
        affected_pct / prior_affected if prior_affected else np.nan,
        abs(mean_delta_pp) / prior_delta if prior_delta else np.nan,
    ]
    status = "PASS" if all(0.5 <= ratio <= 2.0 for ratio in ratios) else "STOP_REVIEW"

    summary = ImpactSummary(
        status=status,
        scope_start=start_ts.strftime("%Y-%m-%d"),
        scope_end=end_ts.strftime("%Y-%m-%d"),
        tickers=used_tickers,
        samples_20d=int(len(delta_20d)),
        affected_20d_samples=int(event_20d.sum()),
        affected_20d_pct=affected_pct,
        affected_20d_mean_delta_pp=mean_delta_pp,
        affected_20d_median_delta_pp=float(event_delta_pp.median()),
        affected_20d_gt_3pp_pct=float(event_delta_pp.abs().gt(3.0).mean() * 100.0),
        affected_20d_gt_5pp_pct=float(event_delta_pp.abs().gt(5.0).mean() * 100.0),
        v1_samples=int(len(old_v1)),
        v1_any_class_flip_pct=float(any_flip.mean() * 100.0),
        v1_up_down_direct_flip_pct=float(direct_flip.mean() * 100.0),
        zero_event_max_abs_delta=zero_event_max,
        prior_affected_pct=prior_affected,
        prior_mean_delta_pp=prior_delta,
    )
    details = {
        "comparison_ratios": {
            "affected_sample_ratio_vs_prior": ratios[0],
            "mean_delta_ratio_vs_prior": ratios[1],
        },
        "interpretation": {
            "absolute_20d": "stock action-factor change only",
            "v1_excess": "stock action factor plus official TAIEX total-return benchmark",
            "zero_event_contract": (
                "applies to absolute stock labels; excess labels can change when the "
                "official total-return benchmark differs from the price index"
            ),
        },
    }
    return summary, details


def _write_report(summary: ImpactSummary, details: dict, report_prefix: Path) -> None:
    payload = {"summary": asdict(summary), **details}
    report_prefix.parent.mkdir(parents=True, exist_ok=True)
    report_prefix.with_suffix(".json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    ratio = details["comparison_ratios"]
    md = f"""# RETRAIN-01 除權息標籤影響量化

## Verdict

**{summary.status}** — 事前預測差距門檻為 0.5x~2.0x；超出即停止重訓並回 PM。

## Scope

- 日期：{summary.scope_start} ~ {summary.scope_end}
- 股票：{summary.tickers:,}
- 20D 有效樣本：{summary.samples_20d:,}
- 官方基準：TWSE MFI94U 發行量加權股價報酬指數
- 公司行動：`before_price/after_price` 比例因子

## Impact

| 指標 | 實測 | 2026-07-15 事前預測 | 比率 |
|---|---:|---:|---:|
| 20D 視窗含公司行動樣本 | {summary.affected_20d_pct:.2f}% ({summary.affected_20d_samples:,}) | 9.51% | {ratio['affected_sample_ratio_vs_prior']:.2f}x |
| 受影響 20D 平均位移 | {summary.affected_20d_mean_delta_pp:+.2f}pp | +3.37pp | {ratio['mean_delta_ratio_vs_prior']:.2f}x |
| 受影響 20D 中位位移 | {summary.affected_20d_median_delta_pp:+.2f}pp | - | - |
| 位移絕對值 >3pp | {summary.affected_20d_gt_3pp_pct:.2f}% | 45.2% | - |
| 位移絕對值 >5pp | {summary.affected_20d_gt_5pp_pct:.2f}% | 22.3% | - |
| V1 任意分類翻轉 | {summary.v1_any_class_flip_pct:.3f}% | - | - |
| V1 UP↔DOWN 直接翻轉 | {summary.v1_up_down_direct_flip_pct:.3f}% | - | - |

## Invariants

- 無個股公司行動的絕對 20D 標籤最大差：`{summary.zero_event_max_abs_delta:.3e}`（門檻 `<1e-9`）。
- V1 excess 採雙邊含息後，即使個股無事件，也可能因官方報酬指數與價格指數不同而改變；
  這是 PM 指定的 benchmark basis 修正，不應誤列為零事件迴歸。
- `trade_return_20d` 視窗為 `(t+1, t+20]`；close-based 視窗為 `(t, t+N]`。

## Reproduction

```powershell
python scripts/update_taiex_total_return_index.py --backfill
python scripts/audit_exdiv_training_labels.py --start {summary.scope_start} --end {summary.scope_end}
```
"""
    report_prefix.with_suffix(".md").write_text(md, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default="2020-01-01")
    parser.add_argument("--end", default=str(date.today()))
    parser.add_argument("--max-stocks", type=int, default=0)
    parser.add_argument(
        "--report-prefix",
        type=Path,
        default=BASE_DIR / "ml" / "reports" / "retrain01_exdiv_label_impact_20260717",
    )
    args = parser.parse_args()
    summary, details = run_audit(args.start, args.end, args.max_stocks)
    _write_report(summary, details, args.report_prefix)
    print(json.dumps(asdict(summary), ensure_ascii=False, indent=2))
    if summary.status != "PASS":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
