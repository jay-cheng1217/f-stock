from __future__ import annotations

import sqlite3
from datetime import date, timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.routers import model


def _client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setattr(model, "REPORT_DIR", str(tmp_path))
    (tmp_path / model.BACKTEST_SUMMARY_FILENAME).write_text(
        "\n".join(
            [
                "variant,months,monthly_return,monthly_alpha,mdd,sharpe,calmar,win_rate",
                "Control_Regression_Top30,28,0.0349,-0.0060,-0.0923,2.1144,5.5280,0.6428",
                "Treatment_TwoStage_N75,28,0.07637371874572896,0.035373298344089296,-0.05569411810414859,2.969360601268965,25.4705689785858,0.8571428571428571",
            ]
        ),
        encoding="utf-8",
    )
    (tmp_path / model.BACKTEST_MONTHLY_RETURNS_IMAGE).write_bytes(b"png")
    app = FastAPI()
    app.include_router(model.router)
    return TestClient(app)


def test_backtest_summary_endpoint_returns_two_stage_metrics(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)

    response = client.get("/api/model/backtest_summary")

    assert response.status_code == 200
    payload = response.json()
    assert payload["period"] == "2024-01 ~ 2026-04"
    assert payload["months"] == 28
    assert payload["monthly_excess_return_pct"] == 3.54
    assert payload["max_drawdown_pct"] == -5.57
    assert payload["sharpe"] == 2.97
    assert payload["calmar"] == 25.47
    assert payload["alpha_vs_twii_pct_per_month"] == 3.54
    assert payload["chart_url"] == "/api/model/backtest_summary/monthly_returns.png"
    assert payload["chart_title"] == "Two-Stage Champion 每月回測報酬"
    assert payload["chart_note"].startswith("藍線是舊版回歸 Top30")
    assert payload["chart_lines"] == [
        {
            "key": "control_return",
            "color": "#2563eb",
            "label": "藍線：舊版回歸 Top30",
            "description": "舊版 V2 regression 直接選 Top30 的每月報酬",
        },
        {
            "key": "treatment_return",
            "color": "#dc2626",
            "label": "紅線：Two-Stage N=75",
            "description": "現行 Two-Stage Champion N=75 重排後 Top30 的每月報酬",
        },
    ]


def test_backtest_monthly_returns_chart_endpoint_serves_png(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)

    response = client.get("/api/model/backtest_summary/monthly_returns.png")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content == b"png"


def _write_twii_calendar(tmp_path, start: date, days: int = 35) -> None:
    current = start
    rows = ["Date,Open,High,Low,Close"]
    trading_index = 0
    while trading_index < days:
        if current.weekday() < 5:
            close = 100 + trading_index
            rows.append(f"{current.isoformat()},{close},{close},{close},{close}")
            trading_index += 1
        current += timedelta(days=1)
    (tmp_path / "index_TWII.csv").write_text("\n".join(rows), encoding="utf-8")


def _write_oos_db(path, rows: list[tuple]) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE unified_positions (
                id INTEGER PRIMARY KEY,
                ticker TEXT,
                entry_date TEXT,
                exit_date TEXT,
                status TEXT,
                realized_return_pct REAL,
                target_weight REAL,
                exit_reason TEXT
            )
            """
        )
        conn.executemany(
            """
            INSERT INTO unified_positions (
                id,
                ticker,
                entry_date,
                exit_date,
                status,
                realized_return_pct,
                target_weight,
                exit_reason
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )


def _model_client_for_oos(tmp_path, monkeypatch, *, today: date) -> TestClient:
    monkeypatch.setattr(model, "REPORT_DIR", str(tmp_path))
    monkeypatch.setattr(model, "INDEX_DIR", str(tmp_path))
    monkeypatch.setattr(model, "CHAMPION_DB_PATH", str(tmp_path / "champion.db"))
    monkeypatch.setattr(model, "_today", lambda: today)
    app = FastAPI()
    app.include_router(model.router)
    return TestClient(app)


def test_oos_performance_returns_empty_until_positions_mature(tmp_path, monkeypatch) -> None:
    _write_twii_calendar(tmp_path, date(2026, 5, 11), days=25)
    _write_oos_db(
        tmp_path / "champion.db",
        [
            (
                1,
                "2330",
                "2026-05-11",
                "2026-05-20",
                "closed",
                0.10,
                0.0333,
                "TIME_20D",
            )
        ],
    )
    client = _model_client_for_oos(tmp_path, monkeypatch, today=date(2026, 5, 20))

    response = client.get("/api/model/oos_performance")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "empty"
    assert payload["mature_sample_count"] == 0
    assert payload["immature_closed_count"] == 1
    assert payload["expected_available_after"] == "2026-06-06"
    assert "OOS 資料累積中" in payload["empty_message"]
    assert payload["curve"] == []


def test_oos_performance_uses_only_mature_two_stage_positions(tmp_path, monkeypatch) -> None:
    _write_twii_calendar(tmp_path, date(2026, 5, 11), days=35)
    _write_oos_db(
        tmp_path / "champion.db",
        [
            (
                1,
                "2330",
                "2026-05-11",
                "2026-06-09",
                "closed",
                0.10,
                0.0333,
                "TIME_20D",
            ),
            (
                2,
                "2317",
                "2026-05-11",
                "2026-06-09",
                "stopped_out",
                -0.05,
                0.0333,
                "STOP_LOSS",
            ),
            (
                3,
                "1305",
                "2026-04-30",
                "2026-06-09",
                "closed",
                0.50,
                0.0333,
                "TIME_20D",
            ),
        ],
    )
    client = _model_client_for_oos(tmp_path, monkeypatch, today=date(2026, 6, 12))

    response = client.get("/api/model/oos_performance")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["strategy_start_date"] == "2026-05-09"
    assert payload["mature_sample_count"] == 2
    assert payload["total_closed_after_start"] == 2
    assert payload["win_rate_pct"] == 50.0
    assert payload["champion_cumulative_return_pct"] == 2.5
    assert payload["latest_mature_exit_date"] == "2026-06-09"
    assert payload["chart_lines"][0]["label"] == "綠線：Champion OOS"
    assert len(payload["curve"]) == 1
