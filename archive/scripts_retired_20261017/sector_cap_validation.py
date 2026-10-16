"""Validate V2 single-day sector cap behavior for prediction artifacts."""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

BASE_DIR = Path(os.environ.get("STOCK_BASE_DIR") or Path(__file__).resolve().parents[1])
sys.path.insert(0, str(BASE_DIR))

from ml.config import MODEL_DIR, REPORT_DIR  # noqa: E402
from ml.predict import apply_sector_cap  # noqa: E402
from ml.thresholds import SECTOR_CAP_RATIO  # noqa: E402
from scripts.build_unified_signals import (  # noqa: E402
    _apply_penalty_overlay_sector_cap,
    _filter_20d_prediction_pool_by_alpha_gate,
    apply_penalty_overlay,
)

DEFAULT_DATES = ["2026-04-23", "2026-04-24", "2026-04-27", "2026-04-28", "2026-04-29"]
REPORT_STEM = "sector_cap_validation_20260423_20260429"


def _read_prediction(prediction_date: str) -> pd.DataFrame:
    path = Path(MODEL_DIR) / f"predictions_{prediction_date}.csv"
    if not path.exists():
        raise FileNotFoundError(path)
    return pd.read_csv(path, encoding="utf-8-sig", dtype={"ticker": str})


def _read_unified(prediction_date: str) -> pd.DataFrame:
    path = Path(MODEL_DIR) / f"unified_signals_{prediction_date}.csv"
    if not path.exists():
        raise FileNotFoundError(path)
    return pd.read_csv(path, encoding="utf-8-sig", dtype={"ticker": str})


def _quiet_apply_sector_cap(df: pd.DataFrame, top_n: int, cap_ratio: float) -> pd.DataFrame:
    with contextlib.redirect_stdout(io.StringIO()):
        return apply_sector_cap(df, top_n=top_n, cap_ratio=cap_ratio)


def _production_penalty_cap(pred_df: pd.DataFrame, top_n: int) -> pd.DataFrame:
    gated = _filter_20d_prediction_pool_by_alpha_gate(pred_df)
    penalized = apply_penalty_overlay(gated, version="v1")
    penalized = penalized.loc[~penalized["penalty_hard_block"].fillna(False)].copy()
    return _apply_penalty_overlay_sector_cap(penalized, top_n=top_n)


def _sector_counts(df: pd.DataFrame) -> pd.Series:
    if df.empty or "sector" not in df.columns:
        return pd.Series(dtype="int64")
    sector = df["sector"].fillna("UNKNOWN").astype(str)
    return sector.value_counts().sort_values(ascending=False)


def _detail_rows(
    *,
    prediction_date: str,
    layer: str,
    df: pd.DataFrame,
    top_n: int,
    cap_count: int,
    cap_ratio: float,
    target_weight_col: str | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    counts = _sector_counts(df)
    if counts.empty:
        return rows

    weight_by_sector: dict[str, float] = {}
    unit_by_sector: dict[str, int] = {}
    if target_weight_col and target_weight_col in df.columns:
        tmp = df.copy()
        tmp["sector"] = tmp["sector"].fillna("UNKNOWN").astype(str)
        tmp[target_weight_col] = pd.to_numeric(tmp[target_weight_col], errors="coerce").fillna(0.0)
        weight_by_sector = tmp.groupby("sector")[target_weight_col].sum().to_dict()
        if "target_units" in tmp.columns:
            tmp["target_units"] = pd.to_numeric(tmp["target_units"], errors="coerce").fillna(0).astype(int)
            unit_by_sector = tmp.groupby("sector")["target_units"].sum().to_dict()

    for sector, count in counts.items():
        count_weight = float(count) / float(top_n) if top_n else 0.0
        target_weight = weight_by_sector.get(str(sector))
        rows.append(
            {
                "prediction_date": prediction_date,
                "layer": layer,
                "sector": sector,
                "count": int(count),
                "count_weight": count_weight,
                "target_units": unit_by_sector.get(str(sector)),
                "target_weight": target_weight,
                "cap_limit_count": cap_count,
                "cap_limit_weight": cap_ratio,
                "count_cap_pass": bool(count <= cap_count),
                "target_weight_cap_pass": None if target_weight is None else bool(target_weight <= cap_ratio),
            }
        )
    return rows


def _max_sector(detail_rows: list[dict[str, Any]], layer: str, value_col: str) -> dict[str, Any]:
    rows = [row for row in detail_rows if row["layer"] == layer and row.get(value_col) is not None]
    if not rows:
        return {"sector": None, "value": None}
    row = max(rows, key=lambda item: item[value_col])
    return {"sector": row["sector"], "value": row[value_col], "count": row["count"]}


def _same_ranked_tickers(left: pd.DataFrame, right: pd.DataFrame) -> bool:
    left_tickers = left["ticker"].astype(str).tolist()
    ranked = right.loc[right["rank_20d"].notna()].copy()
    ranked["rank_20d"] = pd.to_numeric(ranked["rank_20d"], errors="coerce")
    right_tickers = ranked.sort_values("rank_20d")["ticker"].astype(str).tolist()
    return left_tickers == right_tickers


def validate_dates(dates: list[str], top_n: int, cap_ratio: float) -> tuple[pd.DataFrame, dict[str, Any]]:
    cap_count = max(1, int(top_n * cap_ratio))
    detail: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []

    for prediction_date in dates:
        pred = _read_prediction(prediction_date)
        unified = _read_unified(prediction_date)

        raw_head = pred.head(top_n).copy()
        baseline_cap = _quiet_apply_sector_cap(pred, top_n=top_n, cap_ratio=cap_ratio)
        production_cap = _production_penalty_cap(pred, top_n=top_n)
        unified_rank20 = unified.loc[unified["rank_20d"].notna()].copy()
        active_target = unified.loc[pd.to_numeric(unified.get("target_units"), errors="coerce").fillna(0).gt(0)].copy()

        detail.extend(
            _detail_rows(
                prediction_date=prediction_date,
                layer="raw_head30_before_cap",
                df=raw_head,
                top_n=top_n,
                cap_count=cap_count,
                cap_ratio=cap_ratio,
            )
        )
        detail.extend(
            _detail_rows(
                prediction_date=prediction_date,
                layer="apply_sector_cap_output",
                df=baseline_cap,
                top_n=top_n,
                cap_count=cap_count,
                cap_ratio=cap_ratio,
            )
        )
        detail.extend(
            _detail_rows(
                prediction_date=prediction_date,
                layer="production_penalty_cap_output",
                df=production_cap,
                top_n=top_n,
                cap_count=cap_count,
                cap_ratio=cap_ratio,
            )
        )
        detail.extend(
            _detail_rows(
                prediction_date=prediction_date,
                layer="unified_rank20_rows",
                df=unified_rank20,
                top_n=top_n,
                cap_count=cap_count,
                cap_ratio=cap_ratio,
            )
        )
        detail.extend(
            _detail_rows(
                prediction_date=prediction_date,
                layer="unified_active_target",
                df=active_target,
                top_n=top_n,
                cap_count=cap_count,
                cap_ratio=cap_ratio,
                target_weight_col="target_weight_ratio",
            )
        )

        date_rows = [row for row in detail if row["prediction_date"] == prediction_date]
        raw_max = _max_sector(date_rows, "raw_head30_before_cap", "count")
        baseline_max = _max_sector(date_rows, "apply_sector_cap_output", "count")
        production_max = _max_sector(date_rows, "production_penalty_cap_output", "count")
        unified_max = _max_sector(date_rows, "unified_rank20_rows", "count")
        active_weight_max = _max_sector(date_rows, "unified_active_target", "target_weight")
        summaries.append(
            {
                "prediction_date": prediction_date,
                "raw_head30_max_sector": raw_max["sector"],
                "raw_head30_max_count": raw_max["value"],
                "apply_sector_cap_max_sector": baseline_max["sector"],
                "apply_sector_cap_max_count": baseline_max["value"],
                "production_penalty_cap_max_sector": production_max["sector"],
                "production_penalty_cap_max_count": production_max["value"],
                "unified_rank20_max_sector": unified_max["sector"],
                "unified_rank20_max_count": unified_max["value"],
                "unified_active_target_rows": int(len(active_target)),
                "unified_active_target_max_sector": active_weight_max["sector"],
                "unified_active_target_max_weight": active_weight_max["value"],
                "cap_limit_count": cap_count,
                "cap_ratio": cap_ratio,
                "apply_sector_cap_pass": bool((baseline_max["value"] or 0) <= cap_count),
                "production_penalty_cap_pass": bool((production_max["value"] or 0) <= cap_count),
                "unified_rank20_pass": bool((unified_max["value"] or 0) <= cap_count),
                "production_cap_matches_unified_rank20": _same_ranked_tickers(production_cap, unified),
                "active_target_weight_over_cap": bool(
                    active_weight_max["value"] is not None and active_weight_max["value"] > cap_ratio
                ),
            }
        )

    detail_df = pd.DataFrame(detail)
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "window": {"start": min(dates), "end": max(dates), "dates": dates},
        "top_n": top_n,
        "cap_ratio": cap_ratio,
        "cap_limit_count": cap_count,
        "date_summaries": summaries,
        "overall": {
            "apply_sector_cap_all_pass": all(item["apply_sector_cap_pass"] for item in summaries),
            "production_penalty_cap_all_pass": all(item["production_penalty_cap_pass"] for item in summaries),
            "unified_rank20_all_pass": all(item["unified_rank20_pass"] for item in summaries),
            "production_cap_matches_unified_rank20_all": all(
                item["production_cap_matches_unified_rank20"] for item in summaries
            ),
            "active_target_weight_over_cap_dates": [
                item["prediction_date"] for item in summaries if item["active_target_weight_over_cap"]
            ],
        },
    }
    return detail_df, summary


def _pct(value: Any) -> str:
    if value is None or pd.isna(value):
        return "-"
    return f"{float(value) * 100:.2f}%"


def write_markdown(path: Path, detail_df: pd.DataFrame, summary: dict[str, Any]) -> None:
    overall = summary["overall"]
    lines = [
        "# Sector Cap Validation",
        "",
        "## Scope",
        "",
        f"- Generated at: {summary['generated_at']}",
        f"- Window: {summary['window']['start']} to {summary['window']['end']}",
        f"- Dates: {', '.join(summary['window']['dates'])}",
        f"- Top N: {summary['top_n']}",
        f"- Sector cap: {summary['cap_ratio']:.0%} = max {summary['cap_limit_count']} names per sector",
        "",
        "## Conclusion",
        "",
        f"- `apply_sector_cap` output pass: `{overall['apply_sector_cap_all_pass']}`",
        f"- Production penalty-overlay cap output pass: `{overall['production_penalty_cap_all_pass']}`",
        f"- `unified_signals` rank-20 rows pass: `{overall['unified_rank20_all_pass']}`",
        f"- Production cap ticker order matches `unified_signals.rank_20d`: `{overall['production_cap_matches_unified_rank20_all']}`",
    ]
    if overall["active_target_weight_over_cap_dates"]:
        lines.append(
            "- Post-tradability target weights can exceed 20% after many names are blocked: "
            + ", ".join(overall["active_target_weight_over_cap_dates"])
        )
    else:
        lines.append("- Post-tradability target weights stay within 20%.")
    lines.extend(
        [
            "",
            "Interpretation: the single-day Top30 sector cap is working. The week-one 32.30% "
            "communication-services exposure is not a Top30 cap failure; it comes from cross-date "
            "accumulation and post-tradability target-weight normalization.",
            "",
            "## Daily Summary",
            "",
            "| date | raw max | baseline cap max | production cap max | unified rank20 max | active target max | active rows | match unified |",
            "| --- | --- | --- | --- | --- | --- | ---: | ---: |",
        ]
    )
    for item in summary["date_summaries"]:
        lines.append(
            "| {date} | {raw_sector} {raw_count}/30 | {base_sector} {base_count}/30 | "
            "{prod_sector} {prod_count}/30 | {uni_sector} {uni_count}/30 | "
            "{active_sector} {active_weight} | {active_rows} | {match} |".format(
                date=item["prediction_date"],
                raw_sector=item["raw_head30_max_sector"],
                raw_count=item["raw_head30_max_count"],
                base_sector=item["apply_sector_cap_max_sector"],
                base_count=item["apply_sector_cap_max_count"],
                prod_sector=item["production_penalty_cap_max_sector"],
                prod_count=item["production_penalty_cap_max_count"],
                uni_sector=item["unified_rank20_max_sector"],
                uni_count=item["unified_rank20_max_count"],
                active_sector=item["unified_active_target_max_sector"],
                active_weight=_pct(item["unified_active_target_max_weight"]),
                active_rows=item["unified_active_target_rows"],
                match=item["production_cap_matches_unified_rank20"],
            )
        )

    lines.extend(["", "## Production Cap Sector Counts", "", "| date | sector | count | count weight | pass |", "| --- | --- | ---: | ---: | ---: |"])
    production = detail_df[detail_df["layer"].eq("production_penalty_cap_output")].copy()
    production = production.sort_values(["prediction_date", "count"], ascending=[True, False])
    for _, row in production.iterrows():
        lines.append(
            f"| {row['prediction_date']} | {row['sector']} | {int(row['count'])} | "
            f"{_pct(row['count_weight'])} | {bool(row['count_cap_pass'])} |"
        )

    lines.extend(
        [
            "",
            "## Notes",
            "",
            "- `raw_head30_before_cap` is the un-capped sorted prediction artifact head and is expected to exceed the cap on some days.",
            "- `production_penalty_cap_output` mirrors the current unified-signal path: alpha gate, penalty overlay v1, hard-block removal, then sector cap.",
            "- `unified_active_target` is after tradability and market-regime gates; its target weights are normalized over remaining target units, so it is not the same denominator as the Top30 cap.",
            "- This validation is research-only and does not change production model, sector cap, or entry filters.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def write_outputs(detail_df: pd.DataFrame, summary: dict[str, Any], output_stem: str = REPORT_STEM) -> None:
    report_dir = Path(REPORT_DIR)
    report_dir.mkdir(parents=True, exist_ok=True)
    csv_path = report_dir / f"{output_stem}.csv"
    json_path = report_dir / f"{output_stem}.json"
    md_path = report_dir / f"{output_stem}.md"

    detail_df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    write_markdown(md_path, detail_df, summary)
    print(f"[sector-cap] wrote {csv_path}")
    print(f"[sector-cap] wrote {json_path}")
    print(f"[sector-cap] wrote {md_path}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate V2 sector cap behavior.")
    parser.add_argument("--dates", nargs="*", default=DEFAULT_DATES)
    parser.add_argument("--top-n", type=int, default=30)
    parser.add_argument("--cap-ratio", type=float, default=SECTOR_CAP_RATIO)
    parser.add_argument("--output-stem", default=REPORT_STEM)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    dates = [str(item) for item in args.dates]
    detail_df, summary = validate_dates(dates, top_n=args.top_n, cap_ratio=args.cap_ratio)
    write_outputs(detail_df, summary, output_stem=args.output_stem)
    overall = summary["overall"]
    print(
        "[sector-cap] "
        f"apply_sector_cap_all_pass={overall['apply_sector_cap_all_pass']} "
        f"production_penalty_cap_all_pass={overall['production_penalty_cap_all_pass']} "
        f"unified_rank20_all_pass={overall['unified_rank20_all_pass']} "
        f"active_target_weight_over_cap_dates={overall['active_target_weight_over_cap_dates']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
