"""REQ-024 pre-CL3 replay for supply-chain/theme group cap.

This uses the available Two-Stage OOF Top30 artifact as the baseline and the
Stage1 OOF candidate table as the same-snapshot fallback pool. Stage2 scores
for beyond-Top30 alternatives are not present in current artifacts, so fallback
fill order is Stage1 rank. The report states this limitation explicitly.
"""

from __future__ import annotations

import argparse
import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ml.config import REPORT_DIR
from ml.features.group import annotate_group_columns
from ml.features.sector import load_sector_mapping
from ml.predict import apply_group_cap
from ml.thresholds import GROUP_CAP_RATIO, SECTOR_CAP_RATIO


DEFAULT_TOP30 = Path(REPORT_DIR) / "two_stage_cl3_backtest_20260509_treatment_top30.csv"
DEFAULT_STAGE1 = Path(REPORT_DIR) / "audit002_step4_two_stage_20260509_stage1_oof_candidates.csv"


def _load_top30(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, dtype={"ticker": str}, encoding="utf-8-sig")
    df["ticker"] = df["ticker"].astype(str).str.zfill(4)
    rank_col = "treatment_rank" if "treatment_rank" in df.columns else "rank"
    df["rank"] = pd.to_numeric(df[rank_col], errors="coerce")
    df["recommendation"] = "建議買進"
    df["risk_adjusted_return"] = 1_000_000.0 - df["rank"]
    df["inst_net_10d"] = 0.0
    df["avg_20d_volume"] = 1_000_000.0
    return df


def _load_pool(stage1_path: Path, top30: pd.DataFrame) -> pd.DataFrame:
    stage1 = pd.read_csv(stage1_path, dtype={"ticker": str}, encoding="utf-8-sig")
    stage1["ticker"] = stage1["ticker"].astype(str).str.zfill(4)
    stage1["rank"] = pd.to_numeric(stage1["stage1_rank"], errors="coerce")
    stage1["recommendation"] = "建議買進"
    stage1["risk_adjusted_return"] = 100_000.0 - stage1["rank"]
    stage1["inst_net_10d"] = 0.0
    stage1["avg_20d_volume"] = 1_000_000.0

    top = top30.copy()
    top["risk_adjusted_return"] = 1_000_000.0 - top["rank"]
    common_cols = [
        "rebalance_month",
        "ticker",
        "Date",
        "rank",
        "recommendation",
        "risk_adjusted_return",
        "inst_net_10d",
        "avg_20d_volume",
        "trade_return_20d",
        "trade_excess_return_20d",
    ]
    pool = pd.concat([top[common_cols], stage1[common_cols]], ignore_index=True)
    pool = pool.drop_duplicates(subset=["rebalance_month", "ticker"], keep="first")

    sector = load_sector_mapping()
    if sector is not None and not sector.empty:
        sector = sector.rename(columns={"Ticker": "ticker", "Sector": "sector"})
        pool = pool.merge(sector[["ticker", "sector"]], on="ticker", how="left")
    if "sector" not in pool.columns:
        pool["sector"] = "其他"
    pool["sector"] = pool["sector"].fillna("其他")
    return annotate_group_columns(pool)


def _mdd(monthly_returns: pd.Series) -> float:
    if monthly_returns.empty:
        return math.nan
    equity = (1.0 + monthly_returns.fillna(0.0)).cumprod()
    drawdown = equity / equity.cummax() - 1.0
    return float(drawdown.min())


def _summarize(monthly: pd.DataFrame, prefix: str) -> dict[str, float]:
    ret = monthly[f"{prefix}_return"]
    alpha = monthly[f"{prefix}_alpha"]
    return {
        "monthly_return": float(ret.mean()),
        "monthly_alpha": float(alpha.mean()),
        "mdd": _mdd(ret),
        "months": int(ret.notna().sum()),
    }


def _fmt_pct(value: float | None) -> str:
    if value is None or not math.isfinite(float(value)):
        return "n/a"
    return f"{float(value) * 100:.2f}%"


def run(args: argparse.Namespace) -> dict[str, Any]:
    top30 = annotate_group_columns(_load_top30(Path(args.top30_csv)))
    pool = _load_pool(Path(args.stage1_oof_csv), top30)

    rows: list[dict[str, Any]] = []
    selected_rows: list[pd.DataFrame] = []
    for month, baseline in top30.groupby("rebalance_month", sort=True):
        month_pool = pool.loc[pool["rebalance_month"].eq(month)].copy()
        treatment = apply_group_cap(
            month_pool,
            top_n=args.top_n,
            cap_ratio=args.group_cap_ratio,
            initial_df=baseline,
            sector_cap_ratio=SECTOR_CAP_RATIO,
        )
        treatment["rebalance_month"] = month
        selected_rows.append(treatment)

        baseline_counts = baseline.loc[
            baseline["group_code"].ne("OTHER"), "group_code"
        ].value_counts()
        max_per_group = max(1, int(args.top_n * args.group_cap_ratio))
        baseline_violations = int((baseline_counts > max_per_group).sum())
        rows.append(
            {
                "rebalance_month": month,
                "baseline_return": pd.to_numeric(baseline["trade_return_20d"], errors="coerce").mean(),
                "baseline_alpha": pd.to_numeric(baseline["trade_excess_return_20d"], errors="coerce").mean(),
                "treatment_return": pd.to_numeric(treatment["trade_return_20d"], errors="coerce").mean(),
                "treatment_alpha": pd.to_numeric(treatment["trade_excess_return_20d"], errors="coerce").mean(),
                "baseline_group_violations": baseline_violations,
                "group_cap_replacements": int(treatment["group_cap_applied"].fillna(False).sum()),
                "treatment_count": int(len(treatment)),
            }
        )

    monthly = pd.DataFrame(rows)
    selected = pd.concat(selected_rows, ignore_index=True) if selected_rows else pd.DataFrame()
    baseline_summary = _summarize(monthly, "baseline")
    treatment_summary = _summarize(monthly, "treatment")
    delta = {
        "monthly_alpha": treatment_summary["monthly_alpha"] - baseline_summary["monthly_alpha"],
        "mdd": treatment_summary["mdd"] - baseline_summary["mdd"],
        "monthly_return": treatment_summary["monthly_return"] - baseline_summary["monthly_return"],
    }
    pass_alpha = abs(delta["monthly_alpha"]) <= args.max_alpha_abs_delta
    pass_mdd = treatment_summary["mdd"] >= baseline_summary["mdd"] - 1e-12
    overall = bool(pass_alpha and pass_mdd)

    output_prefix = args.output_prefix or f"group_cap_cl3_{datetime.now().strftime('%Y%m%d')}"
    report_dir = Path(REPORT_DIR)
    report_dir.mkdir(parents=True, exist_ok=True)
    monthly_path = report_dir / f"{output_prefix}_monthly.csv"
    selected_path = report_dir / f"{output_prefix}_selected.csv"
    json_path = report_dir / f"{output_prefix}.json"
    md_path = report_dir / f"{output_prefix}.md"
    monthly.to_csv(monthly_path, index=False, encoding="utf-8-sig")
    selected.to_csv(selected_path, index=False, encoding="utf-8-sig")
    payload = {
        "baseline": baseline_summary,
        "treatment": treatment_summary,
        "delta": delta,
        "checks": {
            "alpha_abs_delta_le_0_3pp": pass_alpha,
            "mdd_not_worse": pass_mdd,
            "overall_pass": overall,
        },
        "paths": {
            "monthly_csv": str(monthly_path),
            "selected_csv": str(selected_path),
        },
        "method_note": (
            "Beyond-Top30 replacement candidates are filled by Stage1 rank because "
            "historical Stage2 scores for non-Top30 rows are not present in current artifacts."
        ),
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# REQ-024 Group Cap Pre-CL3 Replay",
        "",
        "## Method Note",
        "",
        payload["method_note"],
        "",
        "## Summary",
        "",
        "| Metric | Baseline | Group Cap | Delta | Pass? |",
        "|---|---:|---:|---:|---:|",
        (
            f"| Monthly alpha | {_fmt_pct(baseline_summary['monthly_alpha'])} | "
            f"{_fmt_pct(treatment_summary['monthly_alpha'])} | {_fmt_pct(delta['monthly_alpha'])} | "
            f"{'PASS' if pass_alpha else 'FAIL'} |"
        ),
        (
            f"| MDD | {_fmt_pct(baseline_summary['mdd'])} | {_fmt_pct(treatment_summary['mdd'])} | "
            f"{_fmt_pct(delta['mdd'])} | {'PASS' if pass_mdd else 'FAIL'} |"
        ),
        (
            f"| Monthly return | {_fmt_pct(baseline_summary['monthly_return'])} | "
            f"{_fmt_pct(treatment_summary['monthly_return'])} | {_fmt_pct(delta['monthly_return'])} |  |"
        ),
        "",
        f"**Overall:** `{'PASS' if overall else 'FAIL'}`",
        "",
        "## Artifacts",
        "",
        f"- Monthly CSV: `{monthly_path}`",
        f"- Selected CSV: `{selected_path}`",
        f"- JSON: `{json_path}`",
    ]
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    payload["paths"]["md"] = str(md_path)
    print(f"[group-cap-cl3] overall={'PASS' if overall else 'FAIL'} wrote {md_path}")
    return payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run REQ-024 group cap pre-CL3 replay.")
    parser.add_argument("--top30-csv", default=str(DEFAULT_TOP30))
    parser.add_argument("--stage1-oof-csv", default=str(DEFAULT_STAGE1))
    parser.add_argument("--top-n", type=int, default=30)
    parser.add_argument("--group-cap-ratio", type=float, default=GROUP_CAP_RATIO)
    parser.add_argument("--max-alpha-abs-delta", type=float, default=0.003)
    parser.add_argument("--output-prefix", default="")
    return parser.parse_args()


def main() -> int:
    payload = run(parse_args())
    return 0 if payload["checks"]["overall_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
