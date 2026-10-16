"""One-off export: mark history + MA5_BREAK post-exit window for external review.

Produces two CSVs:

1. champion_position_marks.csv
   - For every position, one row per mark_date
   - Joins unified_marks + unified_positions context + daily K (MA5/MA10/MA20/MA60)
   - Lets the consultant verify MA5_BREAK timing against actual MA values

2. champion_ma5break_post_exit.csv
   - For every closed position with exit_reason = MA5_BREAK
   - Next 10 trading days of OHLC + MA + return-since-exit / max gain / max loss
   - Lets the consultant classify each MA5_BREAK as true-loser / whipsaw / late-winner

Read-only against production data.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd

BASE = Path(__file__).resolve().parents[1]
DB_PATH = BASE / "paper_portfolio_v2_champion.db"
DAILY_K_DIR = BASE / "日K資料"
OUT_DIR = BASE / "exports"
POST_EXIT_HORIZON = 10  # trading days


def load_daily_k(ticker: str) -> pd.DataFrame | None:
    path = DAILY_K_DIR / f"{ticker}.csv"
    if not path.exists():
        return None
    cols = ["Date", "Open", "High", "Low", "Close", "Volume", "MA_5", "MA_20", "MA_60"]
    try:
        df = pd.read_csv(path, usecols=cols)
    except ValueError:
        df = pd.read_csv(path)
        cols = [c for c in cols if c in df.columns]
        df = df[cols]
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce").dt.date
    df = df.dropna(subset=["Date", "Close"]).sort_values("Date").reset_index(drop=True)
    if "Close" in df.columns:
        df["MA_10"] = df["Close"].rolling(10, min_periods=10).mean()
    return df


def export_position_marks() -> pd.DataFrame:
    with sqlite3.connect(str(DB_PATH)) as conn:
        positions = pd.read_sql_query(
            """
            SELECT
                id AS position_id, ticker, sector, status, exit_policy AS champion_policy,
                entry_signal_type, prediction_date AS entry_signal_date,
                entry_date, entry_price, fill_price, entry_slippage_pct,
                target_weight, hold_days_target,
                exit_date, exit_price, exit_reason, realized_return_pct,
                max_drawdown_pct, two_stage_rank_at_entry, price_vs_ma20_at_entry
            FROM unified_positions
            """,
            conn,
        )
        marks = pd.read_sql_query(
            """
            SELECT position_id, mark_date, trading_day_number,
                   open AS mark_open, high AS mark_high, low AS mark_low, close AS mark_close,
                   close_return_pct AS unrealized_return_pct, intraday_drawdown_pct,
                   is_exit_day
            FROM unified_marks
            """,
            conn,
        )

    df = marks.merge(positions, on="position_id", how="left")
    df["mark_date"] = pd.to_datetime(df["mark_date"], errors="coerce").dt.date

    # Attach MA from daily K
    ma_rows: list[dict] = []
    ticker_cache: dict[str, pd.DataFrame | None] = {}
    for ticker, group in df.groupby("ticker"):
        if ticker not in ticker_cache:
            ticker_cache[ticker] = load_daily_k(str(ticker))
        k = ticker_cache[ticker]
        if k is None:
            continue
        merged = group[["position_id", "mark_date"]].merge(
            k[["Date", "MA_5", "MA_10", "MA_20", "MA_60", "Volume"]],
            left_on="mark_date", right_on="Date", how="left",
        )
        for _, row in merged.iterrows():
            ma_rows.append(
                {
                    "position_id": row["position_id"],
                    "mark_date": row["mark_date"],
                    "ma_5": row.get("MA_5"),
                    "ma_10": row.get("MA_10"),
                    "ma_20": row.get("MA_20"),
                    "ma_60": row.get("MA_60"),
                    "k_volume": row.get("Volume"),
                }
            )
    ma_df = pd.DataFrame(ma_rows)
    if not ma_df.empty:
        df = df.merge(ma_df, on=["position_id", "mark_date"], how="left")

    df = df.sort_values(["position_id", "mark_date"]).reset_index(drop=True)
    out = OUT_DIR / "champion_position_marks.csv"
    df.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"[marks] wrote rows={len(df)} positions={df['position_id'].nunique()} path={out}")
    return df


def export_ma5break_post_exit() -> pd.DataFrame:
    with sqlite3.connect(str(DB_PATH)) as conn:
        closed = pd.read_sql_query(
            """
            SELECT id AS position_id, ticker, sector,
                   entry_date, entry_price, exit_date, exit_price, exit_reason,
                   realized_return_pct, target_weight, two_stage_rank_at_entry
            FROM unified_positions
            WHERE exit_reason = 'MA5_BREAK' AND exit_date IS NOT NULL
            """,
            conn,
        )

    closed["exit_date"] = pd.to_datetime(closed["exit_date"], errors="coerce").dt.date
    rows: list[dict] = []
    ticker_cache: dict[str, pd.DataFrame | None] = {}
    for r in closed.itertuples(index=False):
        ticker = str(r.ticker)
        exit_date = r.exit_date
        if exit_date is None:
            continue
        if ticker not in ticker_cache:
            ticker_cache[ticker] = load_daily_k(ticker)
        k = ticker_cache[ticker]
        if k is None:
            continue
        idx = k.index[k["Date"] == exit_date]
        if len(idx) == 0:
            continue
        i = int(idx[0])
        exit_close = float(k.at[i, "Close"])
        if exit_close <= 0:
            continue
        running_high = exit_close
        running_low = exit_close
        for j in range(1, POST_EXIT_HORIZON + 1):
            pos = i + j
            if pos >= len(k):
                break
            row_k = k.iloc[pos]
            day_close = float(row_k["Close"])
            day_high = float(row_k["High"])
            day_low = float(row_k["Low"])
            running_high = max(running_high, day_high)
            running_low = min(running_low, day_low)
            rows.append(
                {
                    "original_position_id": r.position_id,
                    "ticker": ticker,
                    "sector": r.sector,
                    "exit_date": exit_date,
                    "exit_reason": r.exit_reason,
                    "exit_price": r.exit_price,
                    "realized_return_pct": r.realized_return_pct,
                    "target_weight": r.target_weight,
                    "post_exit_date": row_k["Date"],
                    "post_exit_day_number": j,
                    "post_open": float(row_k["Open"]),
                    "post_high": day_high,
                    "post_low": day_low,
                    "post_close": day_close,
                    "post_volume": float(row_k["Volume"]) if "Volume" in row_k else None,
                    "post_ma5": float(row_k["MA_5"]) if pd.notna(row_k.get("MA_5")) else None,
                    "post_ma10": float(row_k["MA_10"]) if pd.notna(row_k.get("MA_10")) else None,
                    "post_ma20": float(row_k["MA_20"]) if pd.notna(row_k.get("MA_20")) else None,
                    "return_since_exit": day_close / exit_close - 1.0,
                    "max_gain_since_exit": running_high / exit_close - 1.0,
                    "max_loss_since_exit": running_low / exit_close - 1.0,
                    "close_above_ma5": (
                        bool(day_close > float(row_k["MA_5"])) if pd.notna(row_k.get("MA_5")) else None
                    ),
                }
            )
    out_df = pd.DataFrame(rows)
    out = OUT_DIR / "champion_ma5break_post_exit.csv"
    out_df.to_csv(out, index=False, encoding="utf-8-sig")
    print(
        f"[post-exit] wrote rows={len(out_df)} "
        f"original_positions={out_df['original_position_id'].nunique() if not out_df.empty else 0} "
        f"path={out}"
    )
    return out_df


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    export_position_marks()
    export_ma5break_post_exit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
