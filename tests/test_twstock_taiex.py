from datetime import date

from twstock import _parse_twse_roc_date, _parse_twse_taiex_history_payload


def test_parse_twse_roc_date():
    assert _parse_twse_roc_date("115/05/04") == date(2026, 5, 4)


def test_parse_twse_taiex_history_payload_uses_official_close():
    payload = {
        "stat": "OK",
        "fields": ["日期", "開盤指數", "最高指數", "最低指數", "收盤指數"],
        "data": [["115/05/04", "39,228.39", "40,755.52", "39,228.39", "40,705.14"]],
    }

    assert _parse_twse_taiex_history_payload(payload) == [
        {
            "Date": "2026-05-04",
            "Close": 40705.14,
            "High": 40755.52,
            "Low": 39228.39,
            "Open": 39228.39,
            "Volume": 0,
        }
    ]
