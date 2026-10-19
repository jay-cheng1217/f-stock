import json

import pandas as pd

from scripts import entry_filter_tracker as tracker


def _plan(tmp_path):
    plan = tmp_path / "entry_list_20261008.json"
    plan.write_text(json.dumps({
        "rows": [{"stock": "1111 A", "lane": "main", "kind": "go", "zone": "10-11", "stop": "9"}],
        "shadow_legacy_main": [{"stock": "2222 B", "lane": "main_legacy", "kind": "go", "zone": "20-21", "stop": "18"},
                               {"stock": "3333 C", "lane": "main_legacy", "kind": "watch", "zone": "30-31", "stop": "28"}],
    }), encoding="utf-8")
    return plan


def test_record_shadow_rows_into_separate_ledger(tmp_path, monkeypatch):
    monkeypatch.setattr(tracker, "LEDGER", tmp_path / "main.csv")
    monkeypatch.setattr(tracker, "STATIC_LEDGER", tmp_path / "static.json")
    monkeypatch.setattr(tracker, "DAILY", tmp_path)
    plan = _plan(tmp_path)
    legacy = tmp_path / "legacy.csv"

    assert tracker.record(plan) == 1
    assert tracker.record(plan, rows_key="shadow_legacy_main", ledger=legacy) == 1
    assert tracker.record(plan, rows_key="shadow_legacy_main", ledger=legacy) == 0  # 同日去重

    main = pd.read_csv(tracker.LEDGER, dtype=str)
    lg = pd.read_csv(legacy, dtype=str)
    assert main["ticker"].tolist() == ["1111"]
    assert lg["ticker"].tolist() == ["2222"]          # watch 列不入帳本
    assert lg["lane"].iloc[0] == "main_legacy" and lg["horizon"].iloc[0] == "20"

    tracker.check(ledger=legacy)
    assert not (tmp_path / "static.json").exists()    # 影子帳本不發布 static JSON
    tracker.check()
    assert (tmp_path / "static.json").exists()


def test_ms_shadow_rows_go_to_their_own_ledger_with_20_day_horizon(tmp_path, monkeypatch):
    monkeypatch.setattr(tracker, "LEDGER", tmp_path / "main.csv")
    monkeypatch.setattr(tracker, "STATIC_LEDGER", tmp_path / "static.json")
    monkeypatch.setattr(tracker, "DAILY", tmp_path)
    plan = tmp_path / "entry_list_20261020.json"
    plan.write_text(json.dumps({"rows": [], "shadow_ms_main": [
        {"stock": "4444 D", "lane": "main_ms", "kind": "go", "zone": "40-41", "stop": "38", "mom_6_1": 35.4}]}),
        encoding="utf-8")
    ms = tmp_path / "ms.csv"
    assert tracker.record(plan, rows_key=tracker.MS_ROWS_KEY, ledger=ms) == 1
    led = pd.read_csv(ms, dtype=str)
    assert led["ticker"].tolist() == ["4444"] and led["lane"].iloc[0] == "main_ms" and led["horizon"].iloc[0] == "20"
    assert tracker.record(plan) == 0                                    # 影子列不進正式帳本
    assert tracker.MS_LEDGER.name == "entry_filter_ledger_ms_shadow.csv" and tracker.MS_ROWS_KEY == "shadow_ms_main"
