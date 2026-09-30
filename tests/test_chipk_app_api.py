from __future__ import annotations

import base64
import io
import zipfile
from pathlib import Path

from ml.chipk_app_api import (
    ChipKCookieStore,
    ChipKSession,
    _decode_zip_csv_payload,
    chipk_broker_top_params,
    iter_default_auth_pairs,
    normalize_broker_top_rows,
)


def _zip_b64(text: str) -> str:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("data.txt", text.encode("cp950"))
    return base64.b64encode(buf.getvalue()).decode("ascii")


def test_decode_chipk_zip_csv_payload_normalizes_cp950_rows():
    text = "\n".join(
        [
            "排名^分點代號^分點名稱^買張數^賣張數^買金額^賣金額^區間損益^買股數^賣股數^買金額(元)^賣金額(元)",
            "1^9200^台北-測試^123^20^1000^200^50^123000^20000^1000000^200000",
        ]
    )

    decoded = _decode_zip_csv_payload(_zip_b64(text), query_number="12345")

    assert decoded.header[:3] == ["排名", "分點代號", "分點名稱"]
    assert decoded.raw_line_count == 2
    assert decoded.rows[0]["分點名稱"] == "台北-測試"
    assert decoded.rows[0]["買張數"] == "123"


def test_normalize_broker_top_rows_adds_ticker_date_period():
    rows = [
        {
            "排名": "1",
            "分點代號": "9200",
            "分點名稱": "台北-測試",
            "買張數": "123",
            "賣張數": "20",
            "買金額": "1000",
            "賣金額": "200",
            "區間損益": "50",
            "買股數": "123000",
            "賣股數": "20000",
            "買金額(元)": "1000000",
            "賣金額(元)": "200000",
        }
    ]

    out = normalize_broker_top_rows(rows, ticker="8011", asof_date="20260618", period=5)

    assert out == [
        {
            "ticker": "8011",
            "asof_date": "20260618",
            "period": "5",
            "rank": "1",
            "broker_code": "9200",
            "broker_name": "台北-測試",
            "buy_lots": "123",
            "sell_lots": "20",
            "buy_amount": "1000",
            "sell_amount": "200",
            "interval_pnl": "50",
            "buy_shares": "123000",
            "sell_shares": "20000",
            "buy_amount_twd": "1000000",
            "sell_amount_twd": "200000",
        }
    ]


def test_default_auth_pairs_use_labels_without_exposing_in_report_layer():
    session = ChipKSession(
        cookies={"idp.deviceId": "device-secret", "__lt__cid": "lt-secret", "cm_at": "jwt-secret"},
        user_guid="guid-secret",
        source=ChipKCookieStore(Path("Local State"), Path("Cookies")),
    )

    pairs = list(iter_default_auth_pairs(session))

    assert ("device", "device", "device-secret", "device-secret") in pairs
    assert ("user_guid", "device", "guid-secret", "device-secret") in pairs
    assert all(pair[0] in {"device", "user_guid", "ltcid"} for pair in pairs)


def test_chipk_broker_top_params_matches_desktop_contract():
    assert chipk_broker_top_params("811", "20260618", 20) == "0811,20260618,20,1"
    assert chipk_broker_top_params("8011", "20260618", "5") == "8011,20260618,5,1"
