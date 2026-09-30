from datetime import date

import pandas as pd

from twstock import (
    _existing_etf_daily_k_tickers,
    _merge_daily_k_rows,
    _month_starts,
    _parse_finmind_stock_price_rows,
    _parse_twse_stock_day_rows,
    _resolve_etf_backfill_start,
)


def test_parse_twse_stock_day_rows_supports_etf_codes():
    rows = _parse_twse_stock_day_rows(
        [
            [
                "115/04/30",
                "172,580,528",
                "4,991,490,951",
                "29.30",
                "29.32",
                "28.50",
                "28.61",
                "-0.21",
                "51,347",
                "",
            ]
        ]
    )

    assert rows == [
        {
            "Date": "2026-04-30",
            "Open": 29.3,
            "High": 29.32,
            "Low": 28.5,
            "Close": 28.61,
            "Volume": 172580528,
        }
    ]


def test_parse_finmind_stock_price_rows_supports_listing_history():
    rows = _parse_finmind_stock_price_rows(
        [
            {
                "date": "2003-06-30",
                "stock_id": "0050",
                "Trading_Volume": 9930000,
                "Trading_money": 367884760,
                "open": 37.1,
                "max": 37.4,
                "min": 36.92,
                "close": 37.08,
                "spread": 0.12,
                "Trading_turnover": 2047,
            }
        ]
    )

    assert rows == [
        {
            "Date": "2003-06-30",
            "Open": 37.1,
            "High": 37.4,
            "Low": 36.92,
            "Close": 37.08,
            "Volume": 9930000,
        }
    ]


def test_month_starts_are_inclusive():
    assert list(_month_starts(date(2026, 3, 15), date(2026, 5, 1))) == [
        date(2026, 3, 1),
        date(2026, 4, 1),
        date(2026, 5, 1),
    ]


def test_resolve_etf_backfill_start_rechecks_latest_month(tmp_path):
    path = tmp_path / "0050.csv"
    pd.DataFrame(
        [
            {"Date": "2026-04-29", "Open": 90, "High": 91, "Low": 89, "Close": 90, "Volume": 1},
        ]
    ).to_csv(path, index=False)

    assert _resolve_etf_backfill_start(path) == date(2026, 4, 1)


def test_resolve_etf_backfill_start_uses_listing_month_for_missing_file(tmp_path):
    assert _resolve_etf_backfill_start(tmp_path / "00940.csv", "00940") == date(2024, 4, 1)


def test_existing_etf_daily_k_tickers_only_returns_local_non_empty_etfs(tmp_path, monkeypatch):
    import twstock

    monkeypatch.setattr(twstock, "ETF_TICKERS", frozenset({"0050", "00679B", "00981A"}))
    (tmp_path / "0050.csv").write_text("Date,Open\n2026-04-30,1\n", encoding="utf-8")
    (tmp_path / "00679B.csv").write_text("", encoding="utf-8")
    (tmp_path / "00981A.txt").write_text("not a csv", encoding="utf-8")
    (tmp_path / "2330.csv").write_text("Date,Open\n2026-04-30,1\n", encoding="utf-8")

    assert _existing_etf_daily_k_tickers(tmp_path) == ["0050"]


def test_merge_daily_k_rows_deduplicates_by_date(tmp_path):
    path = tmp_path / "0050.csv"
    pd.DataFrame(
        [
            {"Date": "2026-04-29", "Open": 90, "High": 91, "Low": 89, "Close": 90, "Volume": 1},
        ]
    ).to_csv(path, index=False)

    added, latest = _merge_daily_k_rows(
        path,
        [
            {"Date": "2026-04-29", "Open": 91, "High": 92, "Low": 90, "Close": 91, "Volume": 2},
            {"Date": "2026-04-30", "Open": 92, "High": 93, "Low": 91, "Close": 92, "Volume": 3},
        ],
    )
    out = pd.read_csv(path, dtype={"Date": str})

    assert added == 1
    assert latest == "2026-04-30"
    assert out["Date"].tolist() == ["2026-04-29", "2026-04-30"]
    assert out.loc[out["Date"] == "2026-04-29", "Close"].item() == 91
