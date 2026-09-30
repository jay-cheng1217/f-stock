from __future__ import annotations

import math
from pathlib import Path

import duckdb
import pandas as pd

from scripts.send_weekly_summary import (
    REPORT_NOTE,
    STRUCTURAL_BEHAVIOR_NOTE,
    build_weekly_summary_text,
    compute_champion_period_return,
    compute_strategy_fit_benchmark,
)


def _write_prediction(path: Path, tickers: list[str]) -> None:
    pd.DataFrame(
        {
            "date": [path.stem.replace("predictions_", "")] * len(tickers),
            "ticker": tickers,
            "close": [100.0] * len(tickers),
        }
    ).to_csv(path, index=False, encoding="utf-8-sig")


def test_strategy_fit_uses_prediction_union_and_filters_etfs(tmp_path):
    model_dir = tmp_path / "models"
    model_dir.mkdir()
    _write_prediction(model_dir / "predictions_2026-04-01.csv", ["2330", "0050"])
    _write_prediction(model_dir / "predictions_2026-04-02.csv", ["2317"])

    db_path = tmp_path / "stock.duckdb"
    con = duckdb.connect(str(db_path))
    con.execute(
        """
        CREATE TABLE daily_k (
            Ticker VARCHAR,
            Date DATE,
            Close DOUBLE
        )
        """
    )
    con.executemany(
        "INSERT INTO daily_k VALUES (?, ?, ?)",
        [
            ("2330", "2026-03-31", 100.0),
            ("2330", "2026-04-01", 110.0),
            ("2330", "2026-04-02", 121.0),
            ("2317", "2026-03-31", 50.0),
            ("2317", "2026-04-01", 50.0),
            ("2317", "2026-04-02", 55.0),
            ("0050", "2026-03-31", 10.0),
            ("0050", "2026-04-01", 20.0),
            ("0050", "2026-04-02", 30.0),
        ],
    )
    con.close()

    result = compute_strategy_fit_benchmark(
        "2026-04-01",
        "2026-04-02",
        stock_db_path=db_path,
        model_dir=model_dir,
    )

    # Union = 2330 + 2317; 0050 is filtered out as ETF.
    # Period return is start-close to end-close, so only 2026-04-02 is counted.
    assert result["universe_size"] == 2
    assert result["daily_return_days"] == 1
    assert math.isclose(result["return_pct"], 0.10, rel_tol=1e-9)


def test_weekly_summary_text_exposes_dual_benchmark_lines():
    summary = {
        "start_date": "2026-04-01",
        "end_date": "2026-04-30",
        "twii": {"return_pct": 0.25},
        "strategy_fit": {
            "return_pct": 0.12,
            "universe_size": 100,
            "prediction_days": ["2026-04-01"],
        },
        "champion": {
            "positions": 30,
            "daily_return_days": 5,
            "marked_return_days": 2,
            "cash_filled_days": 3,
        },
        "residual_pct": 0.13,
        "champion_alpha_vs_strategy_fit_pct": 0.02,
    }

    text = build_weekly_summary_text(summary)

    assert REPORT_NOTE in text
    assert STRUCTURAL_BEHAVIOR_NOTE in text
    assert "TWII         : +25.00%" in text
    assert "Strategy-fit : +12.00%" in text
    assert "Residual     : +13.00%" in text
    assert "Champion alpha: +2.00% vs strategy-fit" in text
    assert "5 NAV days (2 marked / 3 cash)" in text


def test_champion_return_fills_missing_trading_days_as_cash_nav(tmp_path):
    index_path = tmp_path / "index_TWII.csv"
    pd.DataFrame(
        {
            "Date": ["2026-04-01", "2026-04-02", "2026-04-03", "2026-04-06"],
            "Close": [100.0, 101.0, 102.0, 103.0],
        }
    ).to_csv(index_path, index=False)

    db_path = tmp_path / "champion.db"
    import sqlite3

    con = sqlite3.connect(db_path)
    con.executescript(
        """
        CREATE TABLE unified_positions (
            id INTEGER PRIMARY KEY,
            ticker TEXT,
            target_weight REAL
        );
        CREATE TABLE unified_marks (
            position_id INTEGER,
            mark_date TEXT,
            close_return_pct REAL
        );
        """
    )
    con.execute("INSERT INTO unified_positions VALUES (1, '2330', 1.0)")
    con.execute("INSERT INTO unified_marks VALUES (1, '2026-04-03', 0.10)")
    con.commit()
    con.close()

    result = compute_champion_period_return(
        "2026-04-01",
        "2026-04-06",
        champion_db_path=db_path,
        index_path=index_path,
    )

    assert result["daily_return_days"] == 3
    assert result["marked_return_days"] == 1
    assert result["cash_filled_days"] == 2
    assert math.isclose(result["return_pct"], 0.10, rel_tol=1e-9)
