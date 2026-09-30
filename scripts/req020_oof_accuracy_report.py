"""REQ-020 historical OOF accuracy report for Two-Stage Champion.

This analysis consumes the existing 28-month walk-forward OOF Top30 artifact.
It does not retrain or rewrite predictions; it only answers model accuracy
questions at position, rank-bucket, month, and entry-technical-state levels.
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path
from typing import Any

import pandas as pd
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ml.config import REPORT_DIR
from scripts.backtest_exit_v2_cl3 import _load_price_history
from scripts.backtest_ma5_exit_split_cl3 import _bucket as _ma5_group
from scripts.backtest_ma5_exit_split_cl3 import _entry_price_vs_ma5

DEFAULT_FOLD_ARTIFACT = Path(REPORT_DIR) / "audit002_step5_grid_search_20260509_N75_bins5_fold_top30.csv"
DEFAULT_PREFIX = "req020_oof_accuracy_report_20260509"


def _safe_float(value: Any) -> float | None:
    try:
        number = float(value)
    except Exception:
        return None
    if not math.isfinite(number):
        return None
    return number


def _fmt_pct(value: Any, *, signed: bool = True) -> str:
    number = _safe_float(value)
    if number is None:
        return "-"
    if signed:
        return f"{number * 100:+.2f}%"
    return f"{number * 100:.2f}%"


def _fmt_num(value: Any, digits: int = 2) -> str:
    number = _safe_float(value)
    if number is None:
        return "-"
    return f"{number:.{digits}f}"


def _summarize_positions(df: pd.DataFrame) -> dict[str, Any]:
    total = len(df)
    if total == 0:
        return {
            "positions": 0,
            "avg_return": None,
            "avg_excess": None,
            "win_rate": None,
            "beat_twii_rate": None,
        }
    return {
        "positions": int(total),
        "avg_return": float(df["trade_return_20d"].mean()),
        "avg_excess": float(df["trade_excess_return_20d"].mean()),
        "win_rate": float((df["trade_return_20d"] > 0).mean()),
        "beat_twii_rate": float((df["trade_excess_return_20d"] > 0).mean()),
    }


def _rank_bucket(rank: int) -> str:
    if rank <= 10:
        return "Rank 1-10"
    if rank <= 20:
        return "Rank 11-20"
    return "Rank 21-30"


def _to_table(rows: list[dict[str, Any]], columns: list[tuple[str, str, str]]) -> list[str]:
    header = "| " + " | ".join(label for label, _, _ in columns) + " |"
    sep = "| " + " | ".join("---:" if kind in {"int", "num", "pct", "rate"} else "---" for _, _, kind in columns) + " |"
    lines = [header, sep]
    for row in rows:
        values: list[str] = []
        for _, key, kind in columns:
            value = row.get(key)
            if kind == "pct":
                values.append(_fmt_pct(value))
            elif kind == "rate":
                values.append(_fmt_pct(value, signed=False))
            elif kind == "int":
                values.append(str(int(value)) if value is not None else "-")
            elif kind == "num":
                values.append(_fmt_num(value))
            else:
                values.append(str(value if value is not None else "-"))
        lines.append("| " + " | ".join(values) + " |")
    return lines


def _plot_monthly(monthly: pd.DataFrame, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    width, height = 1400, 620
    left, right, top, bottom = 90, 30, 70, 110
    chart_w = width - left - right
    chart_h = height - top - bottom
    values = [float(value) * 100.0 for value in monthly["alpha"]]
    min_y = min(values + [0.0])
    max_y = max(values + [0.0])
    pad = max((max_y - min_y) * 0.12, 1.0)
    min_y -= pad
    max_y += pad
    y_range = max(max_y - min_y, 1.0)

    def x_pos(index: int) -> float:
        if len(values) <= 1:
            return left + chart_w / 2
        return left + index * chart_w / (len(values) - 1)

    def y_pos(value: float) -> float:
        return top + (max_y - value) / y_range * chart_h

    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    draw.text((left, 25), "REQ-020 Two-Stage OOF Monthly Alpha", fill="#111827")

    # Grid and axis labels.
    for tick in range(5):
        value = min_y + tick * y_range / 4
        y = y_pos(value)
        draw.line((left, y, width - right, y), fill="#e5e7eb", width=1)
        draw.text((12, y - 8), f"{value:+.1f}%", fill="#64748b")
    zero_y = y_pos(0.0)
    draw.line((left, zero_y, width - right, zero_y), fill="#64748b", width=2)

    bar_slot = chart_w / max(len(values), 1)
    bar_w = max(10, min(28, bar_slot * 0.58))
    for index, value in enumerate(values):
        x = x_pos(index)
        y = y_pos(value)
        color = "#10b981" if value > 0 else "#ef4444"
        x0, x1 = x - bar_w / 2, x + bar_w / 2
        y0, y1 = min(y, zero_y), max(y, zero_y)
        draw.rectangle((x0, y0, x1, y1), fill=color)
        if index % 2 == 0 or index == len(values) - 1:
            draw.text((x - 28, height - 85), str(monthly.iloc[index]["month"]), fill="#475569")

    draw.text((left, height - 35), "Green = positive alpha vs TWII; red = negative alpha.", fill="#334155")
    image.save(output_path)


def build_report(args: argparse.Namespace) -> dict[str, Path]:
    fold_path = Path(args.fold_artifact)
    df = pd.read_csv(fold_path, dtype={"ticker": str})
    df["ticker"] = df["ticker"].astype(str).str.zfill(4)
    df["rank"] = pd.to_numeric(df["rank"], errors="coerce").astype("Int64")
    df["trade_return_20d"] = pd.to_numeric(df["trade_return_20d"], errors="coerce")
    df["trade_excess_return_20d"] = pd.to_numeric(df["trade_excess_return_20d"], errors="coerce")
    df["month"] = df["month"].astype(str)
    df = df.dropna(subset=["rank", "trade_return_20d", "trade_excess_return_20d"]).copy()
    df["rank"] = df["rank"].astype(int)
    df["rank_bucket"] = df["rank"].map(_rank_bucket)

    price_cache: dict[str, pd.DataFrame | None] = {}
    df["entry_price_vs_ma5"] = [
        _entry_price_vs_ma5(_load_price_history(row.ticker, price_cache), str(row.Date))
        for row in df.itertuples(index=False)
    ]
    df["entry_ma5_group"] = df["entry_price_vs_ma5"].map(_ma5_group)

    position_summary = _summarize_positions(df)
    rank_rows = [
        {"rank_bucket": bucket, **_summarize_positions(group)}
        for bucket, group in df.groupby("rank_bucket", sort=False)
    ]
    rank_rows = sorted(rank_rows, key=lambda row: int(str(row["rank_bucket"]).split()[1].split("-")[0]))
    top10 = df[df["rank"] <= 10]
    rank11_30 = df[(df["rank"] >= 11) & (df["rank"] <= 30)]
    rank_compare_rows = [
        {"rank_bucket": "Rank 1-10", **_summarize_positions(top10)},
        {"rank_bucket": "Rank 11-30", **_summarize_positions(rank11_30)},
    ]

    monthly = (
        df.groupby("month", as_index=False)
        .agg(
            positions=("ticker", "size"),
            avg_return=("trade_return_20d", "mean"),
            alpha=("trade_excess_return_20d", "mean"),
            win_rate=("trade_return_20d", lambda series: float((series > 0).mean())),
            beat_twii_rate=("trade_excess_return_20d", lambda series: float((series > 0).mean())),
        )
        .sort_values("month")
        .reset_index(drop=True)
    )
    positive_months = int((monthly["alpha"] > 0).sum())
    negative_months = int((monthly["alpha"] <= 0).sum())
    worst_month = monthly.loc[monthly["alpha"].idxmin()].to_dict()
    best_month = monthly.loc[monthly["alpha"].idxmax()].to_dict()

    ma5_rows = [
        {"entry_ma5_group": group, **_summarize_positions(group_df)}
        for group, group_df in df.groupby("entry_ma5_group", sort=False)
    ]
    group_order = {
        "A_price_vs_ma5_at_entry_gt_0": 0,
        "B_minus5pct_to_0": 1,
        "C_le_minus5pct": 2,
        "MISSING": 3,
    }
    ma5_rows = sorted(ma5_rows, key=lambda row: group_order.get(str(row["entry_ma5_group"]), 99))

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    prefix = args.output_prefix
    enriched_path = output_dir / f"{prefix}_positions.csv"
    rank_path = output_dir / f"{prefix}_rank_buckets.csv"
    monthly_path = output_dir / f"{prefix}_monthly.csv"
    ma5_path = output_dir / f"{prefix}_ma5_groups.csv"
    chart_path = output_dir / f"{prefix}_monthly_alpha.png"
    md_path = output_dir / f"{prefix}.md"

    df.to_csv(enriched_path, index=False, encoding="utf-8-sig")
    pd.DataFrame(rank_compare_rows).to_csv(rank_path, index=False, encoding="utf-8-sig")
    monthly.to_csv(monthly_path, index=False, encoding="utf-8-sig")
    pd.DataFrame(ma5_rows).to_csv(ma5_path, index=False, encoding="utf-8-sig")
    _plot_monthly(monthly, chart_path)

    lines = [
        "# REQ-020 歷史 OOF 精準率分析報告",
        "",
        f"- Data source: `{fold_path}`",
        "- Model: `Two-Stage Champion N=75 / 5-level LambdaRank`",
        f"- Window: `{monthly['month'].min()}` to `{monthly['month'].max()}` ({len(monthly)} months)",
        f"- Positions: `{len(df)}`",
        "",
        "## 直接答案",
        "",
        (
            f"過去 28 個月 walk-forward OOF Top30 中，"
            f"**{_fmt_pct(position_summary['win_rate'], signed=False)} 的倉位 20D 報酬為正**，"
            f"**{_fmt_pct(position_summary['beat_twii_rate'], signed=False)} 跑贏同期 TWII**。"
        ),
        "",
        "## 1. Position-Level 勝率",
        "",
        *_to_table(
            [{"bucket": "Top30 all", **position_summary}],
            [
                ("範圍", "bucket", "str"),
                ("樣本數", "positions", "int"),
                ("平均 20D 報酬", "avg_return", "pct"),
                ("平均 alpha vs TWII", "avg_excess", "pct"),
                ("正報酬勝率", "win_rate", "rate"),
                ("跑贏 TWII 比率", "beat_twii_rate", "rate"),
            ],
        ),
        "",
        "## 2. Rank 準確率",
        "",
        *_to_table(
            rank_compare_rows,
            [
                ("Rank bucket", "rank_bucket", "str"),
                ("樣本數", "positions", "int"),
                ("平均 20D 報酬", "avg_return", "pct"),
                ("平均 alpha vs TWII", "avg_excess", "pct"),
                ("正報酬勝率", "win_rate", "rate"),
                ("跑贏 TWII 比率", "beat_twii_rate", "rate"),
            ],
        ),
        "",
        "### Rank 10 段分布",
        "",
        *_to_table(
            rank_rows,
            [
                ("Rank bucket", "rank_bucket", "str"),
                ("樣本數", "positions", "int"),
                ("平均 20D 報酬", "avg_return", "pct"),
                ("平均 alpha", "avg_excess", "pct"),
                ("勝率", "win_rate", "rate"),
            ],
        ),
        "",
        "## 3. 月份分布",
        "",
        f"- Alpha 為正月份: `{positive_months}` / `{len(monthly)}`",
        f"- Alpha 非正月份: `{negative_months}` / `{len(monthly)}`",
        f"- 最好月: `{best_month['month']}` alpha `{_fmt_pct(best_month['alpha'])}`",
        f"- 最差月: `{worst_month['month']}` alpha `{_fmt_pct(worst_month['alpha'])}`",
        "",
        *_to_table(
            monthly.to_dict("records"),
            [
                ("月份", "month", "str"),
                ("樣本數", "positions", "int"),
                ("平均 20D 報酬", "avg_return", "pct"),
                ("Alpha", "alpha", "pct"),
                ("勝率", "win_rate", "rate"),
                ("跑贏 TWII", "beat_twii_rate", "rate"),
            ],
        ),
        "",
        f"![Monthly Alpha]({chart_path.name})",
        "",
        "## 4. 進場技術狀態 × 報酬",
        "",
        *_to_table(
            ma5_rows,
            [
                ("進場 MA5 group", "entry_ma5_group", "str"),
                ("樣本數", "positions", "int"),
                ("平均 20D 報酬", "avg_return", "pct"),
                ("平均 alpha", "avg_excess", "pct"),
                ("正報酬勝率", "win_rate", "rate"),
                ("跑贏 TWII", "beat_twii_rate", "rate"),
            ],
        ),
        "",
        "## PM Notes",
        "",
        "- 這是 walk-forward OOF，不是 in-sample 訓練內評估。",
        "- 報酬欄位使用 fixed OOF artifact 的 `trade_return_20d` / `trade_excess_return_20d`。",
        "- MA5 group 使用 prediction date 後一個交易日 open 相對 signal date MA5，對齊 entry timing。",
    ]
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    return {
        "md": md_path,
        "chart": chart_path,
        "positions": enriched_path,
        "rank": rank_path,
        "monthly": monthly_path,
        "ma5": ma5_path,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build REQ-020 OOF accuracy report.")
    parser.add_argument("--fold-artifact", default=str(DEFAULT_FOLD_ARTIFACT))
    parser.add_argument("--output-dir", default=REPORT_DIR)
    parser.add_argument("--output-prefix", default=DEFAULT_PREFIX)
    return parser.parse_args()


if __name__ == "__main__":
    outputs = build_report(parse_args())
    print(f"[req020] wrote {outputs['md']}")
