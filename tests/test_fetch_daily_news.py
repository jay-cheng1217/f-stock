import pandas as pd

from scripts import fetch_daily_news


def test_main_creates_new_month_file_without_existing_archive(tmp_path, monkeypatch):
    monkeypatch.setattr(fetch_daily_news, "NEWS_DIR", str(tmp_path))
    monkeypatch.setattr(fetch_daily_news.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(fetch_daily_news.random, "uniform", lambda start, end: 0)

    def fake_fetch(typek):
        if typek == "sii":
            return [
                {
                    "Ticker": "2330",
                    "Name": "台積電",
                    "Date": "2026-07-01",
                    "Time": "18:30:00",
                    "Title": "公告測試",
                    "Market": "上市",
                }
            ]
        return []

    monkeypatch.setattr(fetch_daily_news, "fetch_latest_announcements", fake_fetch)

    fetch_daily_news.main()

    output = tmp_path / "announcements_202607.csv"
    assert output.exists()
    saved = pd.read_csv(output, encoding="utf-8-sig")
    assert saved[["Ticker", "Name", "Date", "Time", "Title", "Market"]].to_dict(
        "records"
    ) == [
        {
            "Ticker": 2330,
            "Name": "台積電",
            "Date": "2026-07-01",
            "Time": "18:30:00",
            "Title": "公告測試",
            "Market": "上市",
        }
    ]
