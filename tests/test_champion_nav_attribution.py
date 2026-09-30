import math
import sqlite3
from pathlib import Path

import pandas as pd

from scripts.champion_nav_attribution import (
    _build_reconciliation,
    _latest_open_winners,
    _split_attribution_residual,
    build_daily_nav,
)


def _index_csv(path: Path) -> None:
    pd.DataFrame(
        {
            "Date": ["2026-04-23", "2026-04-24", "2026-04-27"],
            "Close": [100.0, 110.0, 121.0],
        }
    ).to_csv(path, index=False)


def test_daily_nav_reconciles_realized_and_open_marks(tmp_path: Path) -> None:
    index_path = tmp_path / "index_TWII.csv"
    _index_csv(index_path)
    positions = pd.DataFrame(
        [
            {
                "id": 1,
                "ticker": "1111",
                "sector": "A",
                "target_weight": 0.5,
                "status": "closed",
                "entry_date": "2026-04-23",
                "exit_date": "2026-04-24",
                "realized_return_pct": 0.10,
                "entry_slippage_pct": 0.01,
            },
            {
                "id": 2,
                "ticker": "2222",
                "sector": "B",
                "target_weight": 0.25,
                "status": "open",
                "entry_date": "2026-04-24",
                "exit_date": None,
                "realized_return_pct": None,
                "entry_slippage_pct": 0.0,
            },
        ]
    )
    marks = pd.DataFrame(
        [
            {
                "position_id": 1,
                "mark_date": "2026-04-24",
                "close_return_pct": 0.10,
                "is_exit_day": 1,
            },
            {
                "position_id": 2,
                "mark_date": "2026-04-24",
                "close_return_pct": 0.04,
                "is_exit_day": 0,
            },
            {
                "position_id": 2,
                "mark_date": "2026-04-27",
                "close_return_pct": 0.08,
                "is_exit_day": 0,
            },
        ]
    )

    nav = build_daily_nav(
        positions,
        marks,
        "2026-04-23",
        "2026-04-27",
        index_path=index_path,
    )

    # 0.5*10% realized + 0.25*8% open MTM.
    assert math.isclose(nav["nav_return_pct"].iloc[-1], 0.07, rel_tol=1e-9)
    assert math.isclose(nav["realized_pnl_pct"].sum(), 0.05, rel_tol=1e-9)
    assert math.isclose(nav["open_mtm_pct"].iloc[-1], 0.02, rel_tol=1e-9)
    assert math.isclose(nav["execution_pnl_pct"].sum(), 0.005, rel_tol=1e-9)
    assert nav["cash_drag_vs_twii_pct"].sum() < 0


def test_ledger_reconciliation_is_window_aware(tmp_path: Path) -> None:
    index_path = tmp_path / "index_TWII.csv"
    _index_csv(index_path)
    positions = pd.DataFrame(
        [
            {
                "id": 1,
                "ticker": "1111",
                "sector": "A",
                "target_weight": 1.0,
                "status": "open",
                "entry_date": "2026-04-23",
                "exit_date": None,
                "realized_return_pct": None,
                "entry_slippage_pct": 0.0,
            },
        ]
    )
    marks = pd.DataFrame(
        [
            {"position_id": 1, "mark_date": "2026-04-23", "close_return_pct": -0.10, "is_exit_day": 0},
            {"position_id": 1, "mark_date": "2026-04-24", "close_return_pct": -0.05, "is_exit_day": 0},
        ]
    )

    nav = build_daily_nav(positions, marks, "2026-04-24", "2026-04-24", index_path=index_path)
    reconciliation = _build_reconciliation(positions, marks, nav, "2026-04-24", "2026-04-24")

    assert math.isclose(nav["nav_return_pct"].iloc[-1], 0.05, rel_tol=1e-9)
    assert math.isclose(reconciliation["ledger_baseline_pnl_pct"], -0.10, rel_tol=1e-9)
    assert math.isclose(reconciliation["ledger_cumulative_pnl_pct"], -0.05, rel_tol=1e-9)
    assert math.isclose(reconciliation["ledger_total_pnl_pct"], 0.05, rel_tol=1e-9)
    assert math.isclose(reconciliation["reconciliation_error_pct"], 0.0, abs_tol=1e-12)
    assert math.isclose(reconciliation["legacy_cumulative_reconciliation_error_pct"], 0.10, rel_tol=1e-9)


def test_open_winners_are_reported_even_when_not_marked_in_window() -> None:
    positions = pd.DataFrame(
        [
            {"id": 1, "ticker": "8027", "prediction_date": "2026-04-23", "target_weight": 0.05, "status": "open"},
            {"id": 2, "ticker": "6426", "prediction_date": "2026-04-29", "target_weight": 0.10, "status": "open"},
        ]
    )
    marks = pd.DataFrame(
        [
            {"position_id": 1, "mark_date": "2026-04-29", "close_return_pct": 0.15},
        ]
    )

    rows = _latest_open_winners(positions, marks, "2026-04-29")
    by_ticker = {row["ticker"]: row for row in rows}

    assert set(by_ticker) == {"8027", "3450", "6426", "5228"}
    assert math.isclose(by_ticker["8027"]["weighted_open_mtm_pct"], 0.0075, rel_tol=1e-9)
    assert by_ticker["6426"]["mark_date"] is None
    assert by_ticker["3450"]["status"] == "not_in_ledger"


def test_residual_split_keeps_unexplained_bucket_explicit() -> None:
    nav = pd.DataFrame(
        {
            "execution_pnl_pct": [0.01, -0.002],
            "exit_pnl_pct": [-0.015, 0.005],
        }
    )

    split = _split_attribution_residual(nav, residual_total=-0.05)

    assert math.isclose(split["execution_slippage_effect_pct"], 0.008, rel_tol=1e-9)
    assert math.isclose(split["exit_policy_effect_pct"], -0.010, rel_tol=1e-9)
    assert math.isclose(split["unexplained_reconciliation_residual_pct"], -0.048, rel_tol=1e-9)
    assert math.isclose(
        split["execution_slippage_effect_pct"]
        + split["exit_policy_effect_pct"]
        + split["unexplained_reconciliation_residual_pct"],
        split["residual_total_pct"],
        rel_tol=1e-9,
    )
