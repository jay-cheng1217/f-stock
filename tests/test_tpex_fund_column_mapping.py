"""防退化:TPEX 法人 24 欄映射(2026-07-02 投信/自營錯位事故)。

佈局(三欄一組 買/賣/淨):2-4 外資不含自營 | 5-7 外資自營 | 8-10 外資合計
| 11-13 投信 | 14-16 自營自行 | 17-19 自營避險 | 20-22 自營合計 | 23 三大合計。
舊 bug:Trust 取 col7(外資自營淨,常年0)、Dealer 取 col10+col13(外資合計+投信)。
"""
import json
from unittest.mock import patch

import pandas as pd

import twstock as tw


def _fake_row():
    # 模擬原相 2026-07-01:外資不含自營 +1,725,665;投信 +959,592;
    # 自營自行 +66,000;避險 +53,909(淨在每組第 3 欄)
    row = ["3227", "原相"]
    row += ["5,632,530", "3,906,865", "1,725,665"]    # 2-4 外資不含自營
    row += ["0", "0", "0"]                             # 5-7 外資自營
    row += ["5,632,530", "3,906,865", "1,725,665"]    # 8-10 外資合計
    row += ["1,000,000", "40,408", "959,592"]          # 11-13 投信
    row += ["100,000", "34,000", "66,000"]             # 14-16 自營自行
    row += ["60,000", "6,091", "53,909"]               # 17-19 自營避險
    row += ["160,000", "40,091", "119,909"]            # 20-22 自營合計
    row += ["2,805,166"]                                # 23 三大合計
    return row


class _FakeResp:
    def __init__(self, payload):
        self._p = payload

    def json(self):
        return self._p


def _run_fetch(payload, tmp_path, monkeypatch):
    monkeypatch.setattr(tw, "FUND_CACHE_DIR", str(tmp_path))
    with patch.object(tw, "_fast_get", return_value=_FakeResp(payload)):
        status = tw._fetch_tpex_fund("2026-07-01")
    assert status == "ok"
    df = pd.read_csv(tmp_path / "fund_tpex_20260701.csv")
    df["Ticker"] = df["Ticker"].astype(str).str.strip()
    return df[df["Ticker"] == "3227"].iloc[0]


def test_tables_shape_maps_trust_and_dealer_correctly(tmp_path, monkeypatch):
    r = _run_fetch({"tables": [{"data": [_fake_row()]}]}, tmp_path, monkeypatch)
    assert r["Foreign_BuySell"] == 1_725_665
    assert r["Trust_BuySell"] == 959_592      # 舊 bug 會是 0(col7)
    assert r["Dealer_BuySell"] == 119_909     # 舊 bug 會是 2,685,257(col10+13)


def test_aadata_shape_maps_same_layout(tmp_path, monkeypatch):
    r = _run_fetch({"aaData": [_fake_row()]}, tmp_path, monkeypatch)
    assert r["Trust_BuySell"] == 959_592
    assert r["Dealer_BuySell"] == 119_909
