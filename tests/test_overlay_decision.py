from __future__ import annotations

import json
import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from scripts.v2_overlay_decision import (
    STATUS_CHAMPION_INSUFFICIENT,
    STATUS_FAIL,
    STATUS_INSUFFICIENT,
    STATUS_MISSING_INPUTS,
    STATUS_PASS,
    DecisionPaths,
    build_decision,
    _maybe_send_ntfy,
)


AS_OF = "2026-04-28"


def _dates(count: int = 60) -> list[str]:
    start = date.fromisoformat(AS_OF) - timedelta(days=count - 1)
    return [(start + timedelta(days=i)).isoformat() for i in range(count)]


def _make_paths(tmp_path: Path) -> DecisionPaths:
    report_dir = tmp_path / "reports"
    report_dir.mkdir()
    return DecisionPaths(
        report_dir=report_dir,
        prefix="watch",
        champion_db=tmp_path / "champion.db",
        archive_dir=report_dir / "archive" / "v2_overlay_decision",
        latest_json=report_dir / "v2_overlay_decision_latest.json",
        latest_md=report_dir / "v2_overlay_decision_latest.md",
    )


def _write_shadow(
    paths: DecisionPaths,
    *,
    returns: list[float],
    completed: int = 20,
    wins: int = 14,
) -> None:
    dates = _dates(len(returns))
    (paths.report_dir / f"{paths.prefix}.json").write_text(
        json.dumps({"metadata": {"prediction_end": AS_OF}}),
        encoding="utf-8",
    )
    pd.DataFrame([
        {"overlay": "candidate", "calmar_proxy": 1.0, "basket_cum_return": 0.1},
    ]).to_csv(paths.shadow_summary, index=False)
    pd.DataFrame([
        {
            "prediction_date": d,
            "overlay": "candidate",
            "basket_return": r,
            "trade_count": 1,
            "avg_hold_days": 1,
            "avg_mfe": 0.0,
            "avg_mae": -0.01,
            "equity": 1.0,
            "cum_return": 0.0,
            "running_peak": 1.0,
            "drawdown": 0.0,
        }
        for d, r in zip(dates, returns)
    ]).to_csv(paths.shadow_equity, index=False)
    trade_rows = []
    window_dates = dates[-20:]
    for i in range(completed):
        trade_rows.append(
            {
                "ticker": f"{1000 + i}",
                "prediction_date": window_dates[i % len(window_dates)],
                "overlay": "candidate",
                "entry_date": window_dates[i % len(window_dates)],
                "exit_date": window_dates[i % len(window_dates)],
                "exit_reason": "timeout",
                "net_return": 0.02 if i < wins else -0.01,
                "mae": -0.02,
            }
        )
    pd.DataFrame(trade_rows).to_csv(paths.shadow_trades, index=False)


def _write_champion(
    paths: DecisionPaths,
    *,
    returns: list[float],
    completed: int = 20,
    wins: int = 10,
    date_count: int | None = None,
) -> None:
    date_count = date_count or len(returns)
    dates = _dates(date_count)
    con = sqlite3.connect(paths.champion_db)
    con.execute(
        """
        create table unified_positions (
            id integer primary key,
            entry_date text,
            exit_date text,
            target_weight real,
            realized_return_pct real,
            max_drawdown_pct real
        )
        """
    )
    con.execute(
        """
        create table unified_marks (
            id integer primary key,
            position_id integer not null,
            mark_date text not null,
            close_return_pct real,
            intraday_drawdown_pct real,
            is_exit_day integer not null default 0
        )
        """
    )
    for i, r in enumerate(returns, start=1):
        date_value = dates[(i - 1) % len(dates)]
        window_start = max(1, len(returns) - 19)
        window_rank = i - window_start + 1
        exit_date = date_value if i >= window_start and window_rank <= completed else None
        realized = 0.01 if i >= window_start and window_rank <= wins else -0.01
        con.execute(
            """
            insert into unified_positions
            (id, entry_date, exit_date, target_weight, realized_return_pct, max_drawdown_pct)
            values (?, ?, ?, ?, ?, ?)
            """,
            (i, date_value, exit_date, 1.0, realized, -0.02),
        )
        con.execute(
            """
            insert into unified_marks
            (position_id, mark_date, close_return_pct, intraday_drawdown_pct, is_exit_day)
            values (?, ?, ?, ?, ?)
            """,
            (i, date_value, r, -0.01, 1 if exit_date else 0),
        )
    con.commit()
    con.close()


def _decision(tmp_path, *, shadow_returns, champion_returns, completed=20, shadow_wins=14, champion_wins=10):
    paths = _make_paths(tmp_path)
    _write_shadow(paths, returns=shadow_returns, completed=completed, wins=shadow_wins)
    _write_champion(paths, returns=champion_returns, wins=champion_wins)
    return build_decision(paths=paths, today=date.fromisoformat(AS_OF))


def test_pass_clean(tmp_path):
    result = _decision(
        tmp_path,
        shadow_returns=[0.01] * 60,
        champion_returns=[0.001] * 60,
        shadow_wins=16,
        champion_wins=10,
    )
    assert result["status"] == STATUS_PASS
    assert sum(v["pass"] for v in result["sufficient"].values()) == 3


def test_pass_minimal_two_of_three(tmp_path):
    result = _decision(
        tmp_path,
        shadow_returns=[0.001] * 60,
        champion_returns=[0.001] * 60,
        shadow_wins=16,
        champion_wins=10,
    )
    assert result["status"] == STATUS_PASS
    assert sum(v["pass"] for v in result["sufficient"].values()) == 2


def test_fail_mdd(tmp_path):
    shadow = [0.0] * 40 + [0.05, -0.10] + [0.0] * 18
    champion = [0.0] * 60
    result = _decision(tmp_path, shadow_returns=shadow, champion_returns=champion, shadow_wins=16)
    assert result["status"] == STATUS_FAIL
    assert result["necessary"]["mdd_20d_within_tolerance"]["pass"] is False


def test_fail_sufficient_only_one_of_three(tmp_path):
    result = _decision(
        tmp_path,
        shadow_returns=[0.001] * 60,
        champion_returns=[0.001] * 60,
        shadow_wins=10,
        champion_wins=10,
    )
    assert result["status"] == STATUS_FAIL
    assert sum(v["pass"] for v in result["sufficient"].values()) == 1


def test_insufficient_sample(tmp_path):
    result = _decision(
        tmp_path,
        shadow_returns=[0.01] * 60,
        champion_returns=[0.001] * 60,
        completed=14,
        shadow_wins=14,
    )
    assert result["status"] == STATUS_INSUFFICIENT


def test_champion_insufficient(tmp_path):
    paths = _make_paths(tmp_path)
    _write_shadow(paths, returns=[0.01] * 60, completed=20, wins=16)
    _write_champion(paths, returns=[0.001], completed=1, wins=1)
    result = build_decision(paths=paths, today=date.fromisoformat(AS_OF))
    assert result["status"] == STATUS_CHAMPION_INSUFFICIENT


def test_champion_insufficient_same_shadow_window_has_fewer_than_15_mark_dates(tmp_path):
    paths = _make_paths(tmp_path)
    _write_shadow(paths, returns=[0.01] * 60, completed=20, wins=16)
    _write_champion(paths, returns=[0.001] * 20, completed=20, wins=10, date_count=2)

    result = build_decision(paths=paths, today=date.fromisoformat(AS_OF))

    assert result["status"] == STATUS_CHAMPION_INSUFFICIENT
    assert result["audit"]["champion_mark_dates_20d"] == 2
    assert result["audit"]["shadow_20d_mdd"] is not None
    assert result["audit"]["champion_20d_mdd_partial"] is not None


def test_missing_inputs(tmp_path):
    paths = _make_paths(tmp_path)
    _write_shadow(paths, returns=[0.01] * 60, completed=20, wins=16)
    paths.shadow_trades.unlink()
    _write_champion(paths, returns=[0.001] * 60)
    result = build_decision(paths=paths, today=date.fromisoformat(AS_OF))
    assert result["status"] == STATUS_MISSING_INPUTS
    assert result["audit"]["missing_inputs"]


def test_promotion_ready_after_three_pass_days(tmp_path):
    paths = _make_paths(tmp_path)
    paths.archive_dir.mkdir(parents=True)
    for day in ["2026-04-26", "2026-04-27"]:
        (paths.archive_dir / f"{day}.json").write_text(
            json.dumps({"status": STATUS_PASS}),
            encoding="utf-8",
        )
    _write_shadow(paths, returns=[0.01] * 60, completed=20, wins=16)
    _write_champion(paths, returns=[0.001] * 60, wins=10)

    result = build_decision(paths=paths, today=date.fromisoformat(AS_OF))

    assert result["status"] == STATUS_PASS
    assert result["promotion_ready"] is True


def test_no_notify_when_status_unchanged(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr("scripts.notify_ntfy.send_ntfy", lambda **kwargs: calls.append(kwargs) or {"status": "sent"})
    paths = _make_paths(tmp_path)
    paths.archive_dir.mkdir(parents=True)
    (paths.archive_dir / "2026-04-27.json").write_text(
        json.dumps({"status": STATUS_PASS, "promotion_ready": False}),
        encoding="utf-8",
    )
    result = {"status": STATUS_PASS, "promotion_ready": False, "audit": {}, "necessary": {}, "sufficient": {}, "footer": "footer"}

    out = _maybe_send_ntfy(result, paths, today=date.fromisoformat(AS_OF))

    assert out["status"] == "skipped"
    assert calls == []


def test_notify_on_status_change(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr("scripts.notify_ntfy.send_ntfy", lambda **kwargs: calls.append(kwargs) or {"status": "sent"})
    paths = _make_paths(tmp_path)
    paths.archive_dir.mkdir(parents=True)
    (paths.archive_dir / "2026-04-27.json").write_text(
        json.dumps({"status": STATUS_FAIL, "promotion_ready": False}),
        encoding="utf-8",
    )
    result = {
        "status": STATUS_CHAMPION_INSUFFICIENT,
        "promotion_ready": False,
        "audit": {"chosen_overlay": "atr3_tp6", "shadow_20d_mdd": 34.3, "champion_20d_mdd_partial": 0.0},
        "necessary": {},
        "sufficient": {},
        "footer": "footer line",
    }

    out = _maybe_send_ntfy(result, paths, today=date.fromisoformat(AS_OF))

    assert out["status"] == "sent"
    assert calls[0]["title"] == "[stock] V2 Overlay Decision: CHAMPION_INSUFFICIENT"
    assert calls[0]["tags"] == "warning"
    assert "footer line" in calls[0]["message"]


def test_high_priority_on_first_promotion_ready(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr("scripts.notify_ntfy.send_ntfy", lambda **kwargs: calls.append(kwargs) or {"status": "sent"})
    paths = _make_paths(tmp_path)
    paths.archive_dir.mkdir(parents=True)
    (paths.archive_dir / "2026-04-27.json").write_text(
        json.dumps({"status": STATUS_PASS, "promotion_ready": False}),
        encoding="utf-8",
    )
    result = {"status": STATUS_PASS, "promotion_ready": True, "audit": {}, "necessary": {}, "sufficient": {}, "footer": "footer"}

    out = _maybe_send_ntfy(result, paths, today=date.fromisoformat(AS_OF))

    assert out["status"] == "sent"
    assert calls[0]["priority"] == "high"
    assert calls[0]["tags"] == "rocket"


def test_no_previous_archive_treated_as_state_change(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr("scripts.notify_ntfy.send_ntfy", lambda **kwargs: calls.append(kwargs) or {"status": "sent"})
    paths = _make_paths(tmp_path)
    result = {"status": STATUS_PASS, "promotion_ready": False, "audit": {}, "necessary": {}, "sufficient": {}, "footer": "footer"}

    out = _maybe_send_ntfy(result, paths, today=date.fromisoformat(AS_OF))

    assert out["status"] == "sent"
    assert len(calls) == 1


def test_no_ntfy_flag_bypasses_everything(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr("scripts.notify_ntfy.send_ntfy", lambda **kwargs: calls.append(kwargs) or {"status": "sent"})
    paths = _make_paths(tmp_path)
    result = {"status": STATUS_PASS, "promotion_ready": True, "audit": {}, "necessary": {}, "sufficient": {}, "footer": "footer"}

    out = _maybe_send_ntfy(result, paths, no_ntfy=True, today=date.fromisoformat(AS_OF))

    assert out["status"] == "skipped"
    assert calls == []
