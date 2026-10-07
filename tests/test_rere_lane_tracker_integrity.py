from __future__ import annotations

import json

import pandas as pd

from scripts import rere_lane_tracker as tracker


def _configure(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(tracker, "LEDGER", tmp_path / "rere_lane_ledger.csv")
    monkeypatch.setattr(tracker, "REPORT", tmp_path / "rere_lane_tracking_latest.md")
    daily = tmp_path / "daily"
    daily.mkdir()
    monkeypatch.setattr(tracker, "DAILY", daily)
    monkeypatch.setattr(
        tracker,
        "_DIV_CAL",
        pd.DataFrame(columns=["stock_id", "date", "dv"]),
    )


def _write_plan(path, *, trade_date: str, source_date: str) -> None:
    path.write_text(
        json.dumps(
            {
                "trade_date": trade_date,
                "rows": [
                    {
                        "stock": "2399 映泰",
                        "lane": "rere",
                        "status": "rere lane",
                        "reason": "rere lane",
                        "source_date": source_date,
                        "zone": "43.0-45.0",
                        "stop": "42.0",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_record_deduplicates_same_market_source_across_closure(tmp_path, monkeypatch):
    _configure(tmp_path, monkeypatch)
    first = tmp_path / "entry_list_20260710.json"
    second = tmp_path / "entry_list_20260713.json"
    _write_plan(first, trade_date="2026/07/10", source_date="2026-07-09")
    _write_plan(second, trade_date="2026/07/13", source_date="2026-07-09")

    tracker.record(str(first))
    tracker.record(str(second))

    ledger = pd.read_csv(tracker.LEDGER, dtype={"ticker": str})
    assert len(ledger) == 1
    assert ledger.iloc[0]["source_date"] == "2026-07-09"


def test_record_treats_legacy_signal_date_as_source_date(tmp_path, monkeypatch):
    _configure(tmp_path, monkeypatch)
    pd.DataFrame(
        [
            {
                "signal_date": "2026-07-09",
                "ticker": "2399",
                "name": "legacy",
                "zone_low": 43.0,
                "zone_high": 45.0,
                "stop": 42.0,
                "entry_date": "",
                "entry_close": None,
                "last_date": "",
                "last_close": None,
                "days_held": 0,
                "ret_pct": None,
                "status": "pending_entry",
            }
        ]
    ).to_csv(tracker.LEDGER, index=False, encoding="utf-8-sig")
    plan = tmp_path / "entry_list_20260713.json"
    _write_plan(plan, trade_date="2026/07/13", source_date="2026-07-09")

    tracker.record(str(plan))

    ledger = pd.read_csv(tracker.LEDGER, dtype={"ticker": str})
    assert len(ledger) == 1


def test_check_starts_stop_scan_after_entry_day(tmp_path, monkeypatch):
    _configure(tmp_path, monkeypatch)
    pd.DataFrame(
        [
            {
                "signal_date": "2026-07-13",
                "source_date": "2026-07-09",
                "ticker": "2399",
                "name": "映泰",
                "zone_low": 43.0,
                "zone_high": 45.0,
                "stop": 44.92,
                "entry_date": "",
                "entry_close": None,
                "last_date": "",
                "last_close": None,
                "days_held": 0,
                "ret_pct": None,
                "status": "pending_entry",
                "duplicate_of": "",
            }
        ]
    ).to_csv(tracker.LEDGER, index=False, encoding="utf-8-sig")
    pd.DataFrame(
        [
            {"Date": "2026-07-13", "Close": 44.0},
            {"Date": "2026-07-14", "Close": 46.0},
        ]
    ).to_csv(tracker.DAILY / "2399.csv", index=False)

    tracker.check()

    row = pd.read_csv(tracker.LEDGER, dtype={"ticker": str}).iloc[0]
    assert row["entry_close"] == 44.0
    assert row["status"] == "holding"
    assert row["days_held"] == 1
    assert row["ret_pct"] == 4.55


def test_check_reopens_legacy_day_zero_stop(tmp_path, monkeypatch):
    _configure(tmp_path, monkeypatch)
    pd.DataFrame(
        [
            {
                "signal_date": "2026-07-10",
                "source_date": "2026-07-09",
                "ticker": "2399",
                "name": "legacy",
                "zone_low": 43.0,
                "zone_high": 45.0,
                "stop": 44.92,
                "entry_date": "2026-07-13",
                "entry_close": 44.0,
                "last_date": "2026-07-13",
                "last_close": 44.0,
                "days_held": 0,
                "ret_pct": 0.0,
                "status": "stopped_out",
                "duplicate_of": "",
            }
        ]
    ).to_csv(tracker.LEDGER, index=False, encoding="utf-8-sig")
    pd.DataFrame(
        [
            {"Date": "2026-07-13", "Close": 44.0},
            {"Date": "2026-07-14", "Close": 46.0},
        ]
    ).to_csv(tracker.DAILY / "2399.csv", index=False)

    tracker.check()

    row = pd.read_csv(tracker.LEDGER, dtype={"ticker": str}).iloc[0]
    assert row["status"] == "holding"
    assert row["days_held"] == 1
    assert row["ret_pct"] == 4.55


def test_legacy_duplicate_is_preserved_but_excluded(tmp_path, monkeypatch):
    _configure(tmp_path, monkeypatch)
    common = {
        "source_date": "",
        "ticker": "2399",
        "name": "映泰",
        "zone_low": 43.0,
        "zone_high": 45.0,
        "stop": 42.0,
        "entry_date": "2026-07-13",
        "entry_close": 44.0,
        "last_date": "2026-07-14",
        "last_close": 46.0,
        "days_held": 1,
        "ret_pct": 4.55,
        "status": "holding",
        "duplicate_of": "",
    }
    pd.DataFrame(
        [
            {**common, "signal_date": "2026-07-10"},
            {**common, "signal_date": "2026-07-13"},
        ]
    ).to_csv(tracker.LEDGER, index=False, encoding="utf-8-sig")
    pd.DataFrame(
        [
            {"Date": "2026-07-13", "Close": 44.0},
            {"Date": "2026-07-14", "Close": 46.0},
        ]
    ).to_csv(tracker.DAILY / "2399.csv", index=False)

    tracker.check()

    ledger = pd.read_csv(tracker.LEDGER, dtype={"ticker": str})
    duplicate = ledger[ledger["status"] == "duplicate_signal"].iloc[0]
    assert duplicate["signal_date"] == "2026-07-13"
    assert duplicate["duplicate_of"] == "2026-07-10:2399"
    assert pd.isna(duplicate["ret_pct"])


def test_record_rejects_veto_and_watch_and_retains_subtype(tmp_path, monkeypatch):
    _configure(tmp_path, monkeypatch)
    plan = tmp_path / "entry.json"
    base = {"lane": "rere", "source_date": "2026-07-01", "zone": "99-101", "stop": "90"}
    plan.write_text(json.dumps({"trade_date": "2026-07-02", "rows": [
        {**base, "stock": "1111 blocked", "kind": "veto"},
        {**base, "stock": "2222 waiting", "kind": "watch"},
        {**base, "stock": "3333 accepted", "kind": "small", "ptype": "ignition"},
    ]}), encoding="utf-8")
    tracker.record(str(plan))
    ledger = pd.read_csv(tracker.LEDGER, dtype={"ticker": str})
    assert ledger["ticker"].tolist() == ["3333"]
    assert ledger.iloc[0]["subtype"] == "ignition"
    assert ledger.iloc[0]["recorded_kind"] == "small"


def _expiry_fixture(tmp_path, monkeypatch, crash_day):
    _configure(tmp_path, monkeypatch)
    dates = pd.bdate_range("2026-01-02", periods=64).strftime("%Y-%m-%d")
    prices = [100.] * len(dates)
    prices[crash_day] = 80.
    pd.DataFrame({"Date": dates, "Close": prices}).to_csv(tracker.DAILY / "2399.csv", index=False)
    plan = tmp_path / "entry.json"
    _write_plan(plan, trade_date=dates[0], source_date="2026-01-01")
    payload = json.loads(plan.read_text(encoding="utf-8"))
    payload["rows"][0]["stop"] = "90"
    plan.write_text(json.dumps(payload), encoding="utf-8")
    tracker.record(str(plan))
    tracker.check()
    return pd.read_csv(tracker.LEDGER).iloc[0]


def test_catchup_crash_after_expiry_cannot_override_maturity(tmp_path, monkeypatch):
    row = _expiry_fixture(tmp_path, monkeypatch, crash_day=61)
    assert row["status"] == "matured"
    assert row["days_held"] == 60
    assert row["ret_pct"] == 0
    assert row["followup_sessions"] == 63


def test_stop_on_expiry_day_still_applies(tmp_path, monkeypatch):
    row = _expiry_fixture(tmp_path, monkeypatch, crash_day=60)
    assert row["status"] == "stopped_out"
    assert row["days_held"] == 60
    assert row["ret_pct"] == -20


def test_existing_post_expiry_stop_is_repaired_through_tracker(tmp_path, monkeypatch):
    _expiry_fixture(tmp_path, monkeypatch, crash_day=61)
    ledger = pd.read_csv(tracker.LEDGER, dtype={"ticker": str})
    ledger.loc[0, ["status", "days_held", "ret_pct", "last_close"]] = ["stopped_out", 61, -20, 80]
    ledger.to_csv(tracker.LEDGER, index=False)
    tracker.check()
    row = pd.read_csv(tracker.LEDGER).iloc[0]
    assert row["status"] == "matured"
    assert row["days_held"] == 60
    assert row["ret_pct"] == 0


def test_historical_replay_does_not_expose_future_entry(tmp_path, monkeypatch):
    _expiry_fixture(tmp_path, monkeypatch, crash_day=61)
    tracker.check(as_of="2025-12-31")
    row = pd.read_csv(tracker.LEDGER).iloc[0]
    assert row["status"] == "pending_entry"
    assert pd.isna(row["entry_close"])
    assert row["followup_sessions"] == 0


def test_cohort_waits_for_stopped_and_surviving_peers():
    ledger = pd.DataFrame([
        {"status": "stopped_out", "followup_sessions": 40, "ret_pct": -15},
        {"status": "holding", "followup_sessions": 40, "ret_pct": 30},
        {"status": "stopped_out", "followup_sessions": 65, "ret_pct": -10},
        {"status": "matured", "followup_sessions": 65, "ret_pct": 20},
        {"status": "duplicate_signal", "followup_sessions": 65, "ret_pct": 20},
    ])
    cohort = tracker.comparable_cohort(ledger)
    assert cohort.index.tolist() == [2, 3]
    assert cohort["ret_pct"].mean() == 5


def test_early_stop_only_report_does_not_downgrade(tmp_path, monkeypatch):
    _configure(tmp_path, monkeypatch)
    dates = pd.bdate_range("2026-01-02", periods=10).strftime("%Y-%m-%d")
    pd.DataFrame({"Date": dates, "Close": [100.] + [80.] * 9}).to_csv(tracker.DAILY / "2399.csv", index=False)
    pd.DataFrame([{
        "signal_date": dates[0], "ticker": "2399", "name": "fixture", "stop": 90,
        "zone_low": 99, "zone_high": 101, "entry_date": dates[0], "entry_close": 100,
        "last_date": dates[1], "last_close": 80, "days_held": 1,
        "ret_pct": -20, "status": "stopped_out", "source_date": "2026-01-01",
    }]).to_csv(tracker.LEDGER, index=False)
    tracker.check()
    report = tracker.REPORT.read_text(encoding="utf-8")
    assert "完整 60 日觀察 cohort：0 筆" in report
    assert "累積中" in report
    assert "低於基準,樣本夠 30 筆時考慮降權" not in report


def test_subtype_recognises_v2_labels():
    from scripts import rere_lane_tracker as t
    assert t._subtype({"ptype": "shakeout_v2"}) == "shakeout_v2"
    assert t._subtype({"ptype": "shallow_v2"}) == "shallow_v2"
    assert t._subtype({"status": "rere·淺洗盤型v2·小倉(60日,配停損)"}) == "shallow_v2"
    assert t._subtype({"status": "rere·蹲點型v2·小倉(60日,配停損)"}) == "shakeout_v2"
    assert t._subtype({"status": "rere·蹲點型·小倉(60日,配停損)"}) == "shakeout"
    assert t._subtype({"status": "rere·淺洗盤型·小倉(60日,配停損)"}) == "shallow"


def test_no_stop_shadow_and_campaign_book(tmp_path, monkeypatch):
    _configure(tmp_path, monkeypatch)
    dates = pd.bdate_range("2026-01-02", periods=70).strftime("%Y-%m-%d")
    prices = [100.0] * 5 + [80.0] * 5 + [130.0] * 60          # 先破停損、之後大漲
    pd.DataFrame({"Date": dates, "Close": prices}).to_csv(tracker.DAILY / "2399.csv", index=False)
    plan = tmp_path / "entry.json"
    _write_plan(plan, trade_date=dates[0], source_date="2026-01-01")
    payload = json.loads(plan.read_text(encoding="utf-8"))
    payload["rows"][0]["stop"] = "90"
    plan.write_text(json.dumps(payload), encoding="utf-8")
    tracker.record(str(plan))
    tracker.check()

    ledger = pd.read_csv(tracker.LEDGER, dtype={"ticker": str})
    assert ledger.iloc[0]["status"] == "stopped_out" and ledger.iloc[0]["ret_pct"] == -20.0   # 正式帳本照規則
    nostop_path, campaign_path = tracker._shadow_paths()
    assert nostop_path.parent == tracker.LEDGER.parent
    shadow = pd.read_csv(nostop_path, dtype={"ticker": str, "signal_date": str})
    assert shadow.iloc[0]["nostop_ret_pct"] == 30.0 and shadow.iloc[0]["nostop_status"] == "matured"
    assert shadow.iloc[0]["rule_ret_pct"] == -20.0

    camp = tmp_path / "camp.csv"
    header = "ticker,name,first_public_date,exit_public_date,kind,source" + "\n"
    camp.write_text(header + "2399,A,2025-12-31,,campaign,x" + "\n" + "2400,B,2025-12-31,,campaign,x" + "\n", encoding="utf-8")
    book, missed = tracker.campaign_book(shadow, camp)
    assert book["ticker"].tolist() == ["2399"] and missed == ["2400 B(公開 2025-12-31)"]
    camp.write_text(header + "2399,A,2026-03-01,,campaign,x" + "\n", encoding="utf-8")
    book, missed = tracker.campaign_book(shadow, camp)          # 公開日晚於訊號日 → 不算(避免事後得知)
    assert book.empty and missed == ["2399 A(公開 2026-03-01)"]
    assert "並列模擬：同一批訊號不停損" in tracker.REPORT.read_text(encoding="utf-8")
