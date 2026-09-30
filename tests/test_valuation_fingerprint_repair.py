import pandas as pd
import pytest

from scripts import stage_valuation_fingerprint_family as repair


def frame(names=("Original", "Other"), pe=(10.0, 20.0)):
    return pd.DataFrame({"Ticker": ["2330", "6488"], "Name": list(names),
                         "Market": ["上市", "上櫃"], "PE_Ratio": list(pe),
                         "PB_Ratio": [2.0, 3.0], "Dividend_Yield": [0.0, float("nan")]})


def stage(tmp_path, monkeypatch, before, official):
    monkeypatch.setattr(repair, "OUT", tmp_path)
    monkeypatch.setattr(repair, "ROOT", tmp_path)
    (tmp_path / "before").mkdir()
    name = "valuation_20260302.csv"
    before.to_csv(tmp_path / "before" / name, index=False)
    source = tmp_path / "official.csv"
    official.to_csv(source, index=False)
    return repair.numeric_repair_bundle([{"target": "估值資料/" + name,
        "source": str(source), "before_sha256": "fixture", "after_sha256": repair.sha(source)}])


def test_value_repair_retains_historical_name_and_unknown(tmp_path, monkeypatch):
    items, skipped = stage(tmp_path, monkeypatch, frame(), frame(("Current rename", "Alias"), (11.0, 20.0)))
    assert skipped == [] and len(items) == 1
    actual = pd.read_csv(items[0]["source"], dtype={"Ticker": str})
    assert actual.Name.tolist() == ["Original", "Other"]
    assert actual.PE_Ratio.tolist() == [11.0, 20.0]
    assert actual.Dividend_Yield.iloc[0] == 0.0
    assert pd.isna(actual.Dividend_Yield.iloc[1])


def test_current_alias_only_is_not_historical_source_repair(tmp_path, monkeypatch):
    items, skipped = stage(tmp_path, monkeypatch, frame(), frame(("Current rename", "Alias")))
    assert items == [] and skipped == ["valuation_20260302.csv"]


def test_official_key_replacement_is_preserved(tmp_path, monkeypatch):
    candidate = frame()
    candidate.loc[1, ["Ticker", "Name"]] = ["7777", "New official company"]
    items, skipped = stage(tmp_path, monkeypatch, frame(), candidate)
    actual = pd.read_csv(items[0]["source"], dtype={"Ticker": str})
    assert actual.Ticker.tolist() == ["2330", "7777"] and skipped == []
    assert actual.Name.iloc[1] == "New official company"


def test_same_market_duplicate_is_rejected_but_cross_market_key_is_distinct():
    original = frame()
    with pytest.raises(ValueError, match="Duplicate"):
        repair.compare_frames(original, pd.concat([original, original.iloc[:1]]))
    crossover = original.copy()
    crossover.loc[1, "Ticker"] = "2330"
    delta = repair.compare_frames(original, crossover)
    assert delta["added_keys"] == [["2330", "上櫃"]]
    assert delta["removed_keys"] == [["6488", "上櫃"]]
