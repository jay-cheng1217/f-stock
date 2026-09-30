from datetime import date
import hashlib
import json

import numpy as np
import pandas as pd
import pytest

from scripts.audit_margin_day_repair import classify_value, patch_daily_bytes, replace_merged_day, parse_complete_twse_day, materialize_daily_patch
from scripts.latest_bar_coverage import CoverageContractError


def _patch_fixture():
    raw = b"Date,Close,Volume,Margin_Balance,Short_Balance\n2026-03-20,10.5,12000,,\n"
    patch = {"Date": "2026-03-20", "source_sha256": hashlib.sha256(raw).hexdigest(),
             "old": {"Margin_Balance": None, "Short_Balance": None},
             "new": {"Margin_Balance": 633, "Short_Balance": 0}}
    return raw, patch


def test_exact_unknown_to_observed_patch_preserves_price_and_zero():
    raw, patch = _patch_fixture()
    result = patch_daily_bytes(raw, patch)
    assert result.Close.iloc[0] == 10.5
    assert result.Volume.iloc[0] == 12000
    assert result.Margin_Balance.iloc[0] == 633
    assert result.Short_Balance.iloc[0] == 0
    assert classify_value(np.nan, 0) == "unknown_to_observed_zero"
    assert classify_value(0, 633) == "incorrect_zero"
    assert classify_value(9, 633) == "incorrect_nonzero"


def test_patch_rejects_source_hash_and_old_cell_mismatch():
    raw, patch = _patch_fixture()
    with pytest.raises(CoverageContractError, match="hash"):
        patch_daily_bytes(raw + b"\n", patch)
    patch["old"]["Margin_Balance"] = 0
    with pytest.raises(CoverageContractError, match="cell"):
        patch_daily_bytes(raw, patch)


def test_materialized_csv_keeps_other_lines_and_nonmargin_float_text():
    raw = b'\xef\xbb\xbfDate,Close,Volume,Margin_Balance,Short_Balance\r\n2026-03-19,10.500000000000001,12000,12,1\r\n2026-03-20,10.500000000000001,12000,,\r\n'
    _, patch = _patch_fixture()
    patch["source_sha256"] = hashlib.sha256(raw).hexdigest()
    result = materialize_daily_patch(raw, patch)
    assert result == raw.replace(b'12000,,\r\n', b'12000,633,0\r\n')


def test_patch_rejects_missing_date_and_unknown_replacement():
    raw, patch = _patch_fixture()
    patch["Date"] = "2026-03-21"
    with pytest.raises(CoverageContractError, match="existing price bar"):
        patch_daily_bytes(raw, patch)
    patch["Date"] = "2026-03-20"
    patch["new"]["Margin_Balance"] = None
    with pytest.raises(CoverageContractError, match="observed"):
        patch_daily_bytes(raw, patch)


def test_replace_day_keeps_etf_distinct_from_stock_and_other_days():
    cols = ["Date", "Ticker", "Margin_Balance", "Short_Balance"]
    before = pd.DataFrame([["2026-03-19", "006203", 4, 0], ["2026-03-20", "6203", 0, 0]], columns=cols)
    fresh = pd.DataFrame([["2026-03-20", "006203", 0, 0], ["2026-03-20", "6203", 633, 1]], columns=cols)
    result = replace_merged_day(before, fresh, "2026-03-20")
    assert result.Ticker.tolist() == ["006203", "006203", "6203"]
    assert result.Margin_Balance.tolist() == [4, 0, 633]
    before.Ticker = [6203, 6203]
    with pytest.raises(CoverageContractError, match="identifiers"):
        replace_merged_day(before, fresh, "2026-03-20")


def test_full_day_candidate_retains_alphanumeric_etfs(tmp_path):
    path = tmp_path / "twse.csv"
    text = '115年03月20日 融資融券\n代號,名稱,今日餘額,今日餘額.1\n'
    text += '="006203",ETF,0,0\n="00631L",Leveraged,10,1\n'
    text += ''.join(f'="{1000+i}",Company,{i},0\n' for i in range(60))
    path.write_text(text, encoding="utf-8")
    result = parse_complete_twse_day(path, "2026-03-20").set_index("Ticker")
    assert len(result) == 62
    assert result.loc["006203", "Margin_Balance"] == 0
    assert result.loc["00631L", "Margin_Balance"] == 10
    with pytest.raises(CoverageContractError, match="date mismatch"):
        parse_complete_twse_day(path, "2026-03-21")


def test_real_incremental_merge_preserves_old_etf_leading_zero(tmp_path, monkeypatch):
    import twstock

    raw = tmp_path / "raw"
    raw.mkdir()
    cleaned = tmp_path / "cleaned.csv"
    pd.DataFrame([
        {"Date": "2026-03-19", "Ticker": "006203", "Margin_Balance": 0, "Short_Balance": 0},
        {"Date": "2026-03-19", "Ticker": "6203", "Margin_Balance": 600, "Short_Balance": 1},
    ]).to_csv(cleaned, index=False)
    header = '代號,名稱,今日餘額,今日餘額.1\n'
    lines = [f'="{1000+i}",Company,{i},0\n' for i in range(60)]
    lines += ['="00632R",ETF,10,0\n', '="006203",ETF,0,0\n', '="6203",Stock,633,1\n']
    (raw / "raw_margin_twse_20260320.csv").write_text(header + "".join(lines), encoding="utf-8")
    rows = [[str(3000+i), "Company", 0, 0, 0, 0, i, 0, 0, 0, 0, 0, 0, 0, 1] for i in range(60)]
    (raw / "raw_margin_tpex_20260320.json").write_text(json.dumps({"tables": [{"data": rows}]}), encoding="utf-8")
    monkeypatch.setattr(twstock, "RAW_MARGIN_DIR", str(raw))
    monkeypatch.setattr(twstock, "CLEANED_MARGIN_FILE", str(cleaned))
    monkeypatch.setattr(twstock, "MARGIN_MERGE_STATUS_FILE", str(tmp_path / "status.json"))
    twstock._clean_and_merge_margin(date(2026, 3, 20), date(2026, 3, 20))
    result = pd.read_csv(cleaned, dtype={"Ticker": str})
    old = result[result.Date.eq("2026-03-19")].set_index("Ticker")
    assert set(old.index) == {"006203", "6203"}
    assert old.loc["6203", "Margin_Balance"] == 600
    fresh = result[result.Date.eq("2026-03-20")].set_index("Ticker")
    assert fresh.loc["00632R", "Margin_Balance"] == 10
    assert fresh.loc["006203", "Margin_Balance"] == 0
    assert fresh.loc["6203", "Margin_Balance"] == 633
    assert len(result) == 125
