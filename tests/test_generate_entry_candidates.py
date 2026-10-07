import sys

import pandas as pd
import pytest

import scripts.generate_entry_candidates as gen


def _certified_snapshot(frame, tmp_path, monkeypatch):
    from ml import snapshot_lineage as lineage
    monkeypatch.setattr(lineage, "canonical_source_patterns", lambda: [])
    lineage.save_snapshot(frame, tmp_path / "snapshot_cache.pkl",
                          lineage.source_state(include_hashes=True))


def _t(v20=0.04, v60=0.10, rsi=60, vol=1.0, macd_rel=0.001):
    return {"v20": v20, "v60": v60, "rsi": rsi, "vol_ratio": vol, "macd_delta_rel": macd_rel,
            "close": 100.0, "ma20": 96.0}


def test_pattern_gate_accepts_clean_pullback():
    assert gen._pattern_ok(_t(v20=0.04, vol=1.0, rsi=58)) is True


def test_pattern_gate_rejects_chase():
    # 乖離 MA20 +9% = 追高,超過上限,不進(ChipK 也救不了)
    assert gen._pattern_ok(_t(v20=0.09)) is False


def test_pattern_gate_rejects_breakdown():
    # 跌破 MA20 太深 = 接刀
    assert gen._pattern_ok(_t(v20=-0.16)) is False


def test_pattern_gate_rejects_volume_blowoff():
    assert gen._pattern_ok(_t(vol=3.3)) is False


def test_pattern_gate_rejects_overheated_rsi():
    assert gen._pattern_ok(_t(rsi=85)) is False


def test_pattern_gate_rejects_below_ma60():
    # 收在 MA60 之下 = 長期趨勢不成立
    assert gen._pattern_ok(_t(v60=-0.05)) is False


def _rt(wash=0.15, v20=0.0, vr=0.8, v60=0.10, prev5=-500, today=200,
        trust=300, close=100.0, ma20=96.0, day_ret=0.01, prev_below=False):
    return {"wash_from_hi10": wash, "v20": v20, "vr_today": vr, "v60": v60,
            "foreign_prev5": prev5, "foreign_today": today,
            "trust_today": trust, "close": close, "ma20": ma20,
            "day_ret": day_ret, "prev_below_ma20": prev_below}


def test_rere_lane_accepts_washout_turnbuy():
    # 蹲點型:深洗盤+貼MA20+量縮+外資轉買 → 回 'shakeout'
    assert gen._rere_lane_ok(_rt()) == "shakeout"


def test_rere_lane_rejects_wash_below_shallow_floor():
    # 8% 以下連淺洗盤型都不收(2026-09-24 三型擴充後的下限)
    assert gen._rere_lane_ok(_rt(wash=0.05)) is None
    assert gen._rere_lane_ok(_rt(wash=0.079)) is None


def test_rere_lane_shallow_band_returns_shallow():
    # 淺洗盤型(2026-09-24 PM 核准):8%<=洗盤<10%,其餘同蹲點型 → 'shallow'
    # 案例:鈦昇 2020 四次進場 8.7-9.8%、台表科 9/15-17 8-9.75%
    assert gen._rere_lane_ok(_rt(wash=0.09)) == "shallow"
    assert gen._rere_lane_ok(_rt(wash=0.0801)) == "shallow"


def test_rere_lane_wash_10pct_stays_shakeout():
    # 邊界:>=10% 仍歸蹲點型(現行 forward 樣本口徑不變)
    assert gen._rere_lane_ok(_rt(wash=0.10)) == "shakeout"


def test_rere_lane_without_foreign_turn_falls_to_v2():
    # 2026-10-07 PM 核可:外資拐點不再是門檻;只差拐點的訊號改掛 v2 標籤(帳本分開累積)
    assert gen._rere_lane_ok(_rt(prev5=500)) == "shakeout_v2"
    assert gen._rere_lane_ok(_rt(today=-100)) == "shakeout_v2"
    assert gen._rere_lane_ok(_rt(wash=0.09, prev5=500)) == "shallow_v2"
    assert gen._rere_lane_ok(_rt()) == "shakeout"  # 現行標籤優先,forward cohort 不變
    assert gen._rere_lane_ok(_rt(wash=0.09)) == "shallow"


def test_rere_lane_v2_keeps_other_gates():
    assert gen._rere_lane_ok(_rt(wash=0.05, prev5=500)) is None
    assert gen._rere_lane_ok(_rt(prev5=500, vr=1.3)) is None
    assert gen._rere_lane_ok(_rt(prev5=500, v60=-0.03)) is None
    assert gen._rere_lane_ok(_rt(prev5=500, v20=0.07)) is None


def test_legacy_main_rank_uses_model_filter_and_pred_score():
    rows = [dict(ticker="1", model_buy=True, clean=-0.05, pred20=0.02, chip_bonus=0.0, kind="go"),
            dict(ticker="2", model_buy=True, clean=-0.01, pred20=0.001, chip_bonus=0.0, kind="go"),
            dict(ticker="3", model_buy=False, clean=0.0, pred20=0.05, chip_bonus=0.0, kind="go"),
            dict(ticker="4", model_buy=True, clean=0.0, pred20=0.05, chip_bonus=0.0, kind="watch")]
    out = gen._legacy_main_rank(rows, top_n=12)
    assert [r["ticker"] for r in out] == ["2", "1"]
    assert all(r["lane"] == "main_legacy" for r in out)
    rows[0]["kind"] = "veto"
    assert out[1]["kind"] == "go"  # 獨立副本,後續 overlay 不影響影子名單
    assert gen._legacy_main_rank(rows, top_n=1)[0]["ticker"] == "2"


def test_rere_lane_shakeout_rejects_volume_not_quiet():
    # 量比 2.0 不符蹲點型(需量縮);但可能落入發動型 → 用低於發動門檻的 1.3 測純拒絕
    assert gen._rere_lane_ok(_rt(vr=1.3)) is None


def test_rere_lane_rejects_below_ma60():
    assert gen._rere_lane_ok(_rt(v60=-0.03)) is None


def test_rere_lane_accepts_ignition():
    # 發動型:放量(vr>=1.5)站回MA20(收>MA20且昨在MA20下)+外資轉買+投信買 → 'ignition'
    t = _rt(vr=1.8, close=100.0, ma20=98.0, prev_below=True, trust=500)
    assert gen._rere_lane_ok(t) == "ignition"


def test_rere_ignition_rejects_no_trust():
    # 發動型缺投信同買(edge 關鍵味)→ 不成立
    t = _rt(vr=1.8, close=100.0, ma20=98.0, prev_below=True, trust=-100)
    assert gen._rere_lane_ok(t) is None


def test_load_chipk_retired_by_default_ignores_archive(monkeypatch, tmp_path):
    archive = tmp_path / "archive"
    archive.mkdir()
    (archive / "chipk_main_force_2999-01-01.csv").write_text(
        "ticker,chipk_bucket\n2330,CONFIRM_ENTRY\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(gen, "CHIPK_ARCHIVE_DIR", archive)
    monkeypatch.delenv("CHIPK_DESKTOP_ENABLED", raising=False)
    monkeypatch.delenv("STOCK_ENABLE_CHIPK_DESKTOP", raising=False)

    assert gen._load_chipk().empty


def test_load_chipk_reads_archive_when_explicitly_enabled(monkeypatch, tmp_path):
    archive = tmp_path / "archive"
    archive.mkdir()
    (archive / "chipk_main_force_2999-01-01.csv").write_text(
        "ticker,chipk_bucket\n2330,CONFIRM_ENTRY\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(gen, "CHIPK_ARCHIVE_DIR", archive)
    monkeypatch.setenv("CHIPK_DESKTOP_ENABLED", "1")

    rows = gen._load_chipk()

    assert rows.iloc[0]["ticker"] == "2330"
    assert rows.iloc[0]["chipk_src"] == "chipk_main_force_2999-01-01.csv"


def test_prediction_source_must_align_with_snapshot_date(monkeypatch, tmp_path):
    snapshot = pd.DataFrame({"Date": ["2026-07-16"], "ticker": ["2330"]})
    _certified_snapshot(snapshot, tmp_path, monkeypatch)
    for name in (
        "dataA_predictions_2026-07-15.csv",
        "dataA_predictions_2026-07-16.csv",
        "dataA_predictions_latest.csv",
        "predictions_2026-07-16.csv",
    ):
        (tmp_path / name).write_text("ticker,pred_return_20d\n2330,0.01\n", encoding="utf-8")
    monkeypatch.setattr(gen, "MODEL_DIR", tmp_path)

    assert gen._latest_predictions_path() == str(
        tmp_path / "dataA_predictions_2026-07-16.csv"
    )


def test_prediction_source_falls_back_to_same_date_champion(monkeypatch, tmp_path):
    snapshot = pd.DataFrame({"Date": ["2026-07-16"], "ticker": ["2330"]})
    _certified_snapshot(snapshot, tmp_path, monkeypatch)
    (tmp_path / "dataA_predictions_2026-07-15.csv").write_text(
        "ticker,pred_return_20d\n2330,0.01\n", encoding="utf-8"
    )
    (tmp_path / "predictions_2026-07-16.csv").write_text(
        "ticker,pred_return_20d\n2330,0.01\n", encoding="utf-8"
    )
    monkeypatch.setattr(gen, "MODEL_DIR", tmp_path)

    assert gen._latest_predictions_path() == str(tmp_path / "predictions_2026-07-16.csv")


def test_prediction_source_does_not_fall_back_to_stale_model(monkeypatch, tmp_path):
    snapshot = pd.DataFrame({"Date": ["2026-07-16"], "ticker": ["2330"]})
    _certified_snapshot(snapshot, tmp_path, monkeypatch)
    (tmp_path / "dataA_predictions_2026-07-15.csv").write_text(
        "ticker,pred_return_20d\n2330,0.01\n", encoding="utf-8"
    )
    (tmp_path / "predictions_2026-07-15.csv").write_text(
        "ticker,pred_return_20d\n2330,0.01\n", encoding="utf-8"
    )
    monkeypatch.setattr(gen, "MODEL_DIR", tmp_path)

    assert gen._latest_predictions_path() is None


def test_cli_refuses_to_publish_without_aligned_prediction(monkeypatch):
    monkeypatch.setattr(gen, "generate_certified", lambda *a, **kw: {"as_of_close": None, "rows": []})
    monkeypatch.setattr(sys, "argv", ["generate_entry_candidates.py", "--dry"])

    assert gen.main() == 2


def test_watchlist_stale_detection():
    """觀察區間過期判定(2026-08 原相事故):現價偏離區間 >12% 標記過期,區內不標。"""
    # 低於下緣 >12%(方向矛盾)→ 過期。原相實例:198 vs 228-232
    msg = gen._watchlist_stale_msg(198.0, 228.0, 232.0)
    assert msg and "已過期" in msg and "低於區間下緣" in msg
    # 高於上緣 >12%(setup 早該追)→ 過期
    msg2 = gen._watchlist_stale_msg(300.0, 228.0, 232.0)
    assert msg2 and "已過期" in msg2 and "高於區間上緣" in msg2
    # 區間內 → 不標(界霖實例:93 在 92-95)
    assert gen._watchlist_stale_msg(93.0, 92.0, 95.0) == ""
    # 剛好在 12% 容忍邊界內 → 不標(228*0.88=200.6,201 不算過期)
    assert gen._watchlist_stale_msg(201.0, 228.0, 232.0) == ""
    # NaN 現價 → 不判定(不誤標)
    assert gen._watchlist_stale_msg(float("nan"), 228.0, 232.0) == ""


def test_watchlist_stop_breach_flags_failed_setup():
    """2026-08-27 稽核補強:破停損=setup失敗,優先於12%容忍帶(界霖案例:84破88停損但距區間僅-8.7%)。"""
    msg = gen._watchlist_stale_msg(84.0, 92.0, 95.0, stop=88.0)
    assert msg and "已破停損" in msg
    # 未破停損、在容忍帶內 → 正常
    assert gen._watchlist_stale_msg(89.0, 92.0, 95.0, stop=88.0) == ""
    # stop=0(未設)→ 退回容忍帶邏輯,不誤判
    assert gen._watchlist_stale_msg(89.0, 92.0, 95.0, stop=0.0) == ""


def test_macd_floor_is_price_relative_not_absolute():
    """2026-08-28 PM 核准價格比化(回測 PASS,見 backtest_macd_price_relative_20260828.md)。

    尺度案例:舊絕對口徑 -1.0 會錯擋高價股(1000元股 delta -1.02 僅 -0.1% 價比)、
    放行低價股崩壞(20元股 delta -0.3 已達 -1.5% 價比)。價格比口徑修正兩者。
    """
    # 高價股:delta/價 = -0.1%,遠在 -1.0% 之上 → 通過(舊絕對口徑會錯擋)
    assert gen._pattern_ok(_t(macd_rel=-0.001)) is True
    # 低價股崩壞:delta/價 = -1.5%,破 -1.0% → 剔除(舊絕對口徑會放行)
    assert gen._pattern_ok(_t(macd_rel=-0.015)) is False
    # 邊界:恰為 -1.0% → 通過(>= 含邊界)
    assert gen._pattern_ok(_t(macd_rel=-0.010)) is True


def test_watchlist_stop_breach_message_carries_prune_prefix():
    """破線失效訊息必須以 WATCHLIST_STOP_BREACH_PREFIX 開頭——generate() 的自動移除
    (PM 2026-08-28:失效卡直接下架歸檔)靠此前綴判定;前綴斷裂=失效卡永遠掛在版面。"""
    msg = gen._watchlist_stale_msg(84.0, 92.0, 95.0, stop=88.0)
    assert msg.startswith(gen.WATCHLIST_STOP_BREACH_PREFIX)
    # 區間漂移型 stale 不帶此前綴(保留標紅,不自動移除)
    drift = gen._watchlist_stale_msg(198.0, 228.0, 232.0)
    assert drift and not drift.startswith(gen.WATCHLIST_STOP_BREACH_PREFIX)


# --- watchlist 時間過期(2026-09 PM 裁示 30 天剔除;原相筆記兩個月仍上卡事故)---
from datetime import date as _date


def test_watchlist_age_expired_after_max_days():
    today = _date(2026, 9, 4)
    msg = gen._watchlist_age_msg("2026-07-01", today=today)
    assert msg.startswith(gen.WATCHLIST_AGE_EXPIRED_PREFIX)
    assert "2026-07-01" in msg


def test_watchlist_age_boundary_is_strict_greater():
    today = _date(2026, 9, 4)
    assert gen._watchlist_age_msg("2026-08-05", today=today) == ""  # 剛好 30 天:保留
    assert gen._watchlist_age_msg("2026-08-04", today=today) != ""  # 31 天:剔除


def test_watchlist_age_missing_or_bad_date_is_not_judged():
    assert gen._watchlist_age_msg(None) == ""
    assert gen._watchlist_age_msg("") == ""
    assert gen._watchlist_age_msg("not-a-date") == ""


def test_missing_snapshot_never_selects_latest_prediction(monkeypatch, tmp_path):
    (tmp_path / "dataA_predictions_2026-09-04.csv").write_text("ticker,pred_return_20d\n2330,.1\n")
    monkeypatch.setattr(gen, "MODEL_DIR", tmp_path)
    with pytest.raises(ValueError, match="snapshot lineage rejected"):
        gen._latest_predictions_path()


def test_old_prediction_does_not_invent_dates_from_current_snapshot(monkeypatch, tmp_path):
    frame = pd.DataFrame({"ticker": ["2330", "1234"], "Date": ["2026-09-04", "2026-09-02"],
                          "operating_margin_latest": [30., None]})
    _certified_snapshot(frame, tmp_path, monkeypatch)
    (tmp_path / "dataA_predictions_2026-09-04.csv").write_text("ticker,pred_return_20d\n2330,.1\n1234,.2\n")
    monkeypatch.setattr(gen, "MODEL_DIR", tmp_path)
    with pytest.raises(ValueError, match="prediction lineage rejected"):
        gen._load_predictions(_date(2026, 9, 4))
    assert gen._snapshot_fields(_date(2026, 9, 4))[1] == {"2330": 30.}


def test_unknown_and_disposition_cannot_be_entry_eligible():
    required = _date(2026, 9, 4)
    row = {"ticker": "2330", "lane": "rere", "kind": "small", "status": "rere",
           "source_date": "2026-09-04"}
    gen._apply_data_quality(row, required, {"complete": False, "blocked_tickers": []}, True)
    assert row["kind"] == "watch" and row["data_status"] == "UNKNOWN"
    row["kind"] = "small"
    gen._apply_data_quality(row, required, {"complete": True, "blocked_tickers": ["2330"]}, True)
    assert row["kind"] == "veto"


def test_rere_does_not_require_positive_or_fresh_model_score():
    row = {"ticker": "2330", "lane": "rere", "kind": "small", "status": "rere",
           "source_date": "2026-09-04", "model_source_date": "2026-09-03", "pred20": -.5}
    gen._apply_data_quality(row, _date(2026, 9, 4), {"complete": True, "blocked_tickers": []}, True)
    assert row["kind"] == "small" and row["data_status"] == "OK"


def test_main_unknown_om_or_wrong_date_is_pending():
    row = {"ticker": "2330", "kind": "go", "status": "go", "source_date": "2026-09-04",
           "model_source_date": "2026-09-03", "data_warnings": ["OM unknown"]}
    gen._apply_data_quality(row, _date(2026, 9, 4), {"complete": True, "blocked_tickers": []}, True)
    assert row["kind"] == "watch" and len(row["data_warnings"]) == 2


def test_stale_chipk_cannot_veto(monkeypatch, tmp_path):
    (tmp_path / "chipk_main_force_2026-09-03.csv").write_text("ticker,chipk_risk_flags\n2330,high\n")
    monkeypatch.setattr(gen, "CHIPK_ARCHIVE_DIR", tmp_path)
    monkeypatch.setenv("CHIPK_DESKTOP_ENABLED", "1")
    assert gen._load_chipk(_date(2026, 9, 4)).empty


def test_wrong_asof_rejected_before_reading_sources():
    import pytest
    with pytest.raises(ValueError, match="不一致"):
        gen.generate("2026-09-03", trade_date="2026-09-07", include_live=False)


def test_technical_asof_clamps_before_rolling(monkeypatch, tmp_path):
    import numpy as np
    (tmp_path / "2330.csv").touch()
    dates = pd.bdate_range("2026-06-01", periods=70)
    frame = pd.DataFrame({"Date": dates, "Close": [100.] * 69 + [500.], "Volume": 1e6,
                          "Foreign_BuySell": 100., "Trust_BuySell": 100., "price_vs_ma20": .04,
                          "price_vs_ma60": .10, "RSI_14": 60., "volume_ratio_20d": 1.,
                          "macd_hist_delta_3d": .1})
    monkeypatch.setattr(gen, "DAILY_DIR", tmp_path)
    monkeypatch.setattr(gen, "prepare_stock_frame", lambda p: frame.copy())
    monkeypatch.setattr(gen, "annotate_momentum_continuation", lambda f: f)
    out = gen._technical("2330", dates[-2].date())
    assert out["close"] == 100. and out["wash_from_hi10"] == 0
    assert out["signal_date"] == dates[-2].date().isoformat()


def test_pick_rere_v2_splits_cap_between_subtypes():
    rows = ([dict(ticker=f"A{i}", ptype="shakeout_v2", score=3.0 - i * 0.01) for i in range(8)]
            + [dict(ticker=f"B{i}", ptype="shallow_v2", score=2.09 - i * 0.01) for i in range(5)]
            + [dict(ticker="C0", ptype="shakeout", score=9.0)])
    out = gen._pick_rere_v2(rows)
    assert [r["ticker"] for r in out] == ["A0", "A1", "A2", "B0", "B1", "B2"]
    only_deep = gen._pick_rere_v2([r for r in rows if r["ptype"] == "shakeout_v2"])
    assert len(only_deep) == 6                      # 另一型缺席時補滿
    one_shallow = gen._pick_rere_v2(rows[:8] + rows[8:9])
    assert [r["ptype"] for r in one_shallow].count("shallow_v2") == 1 and len(one_shallow) == 6


def test_action_adjusted_close_removes_par_value_discontinuity():
    import pandas as pd
    dates = pd.Series(pd.to_datetime(["2026-08-25", "2026-08-26", "2026-09-07", "2026-09-08"]))
    close = pd.Series([1355.0, 1490.0, 67.1, 60.4])
    cal = pd.DataFrame({"stock_id": ["6949"], "date": [pd.Timestamp("2026-09-07")], "factor": [0.05]})
    adj = gen._action_adjusted_close(dates, close, "6949", cal)
    assert adj.round(2).tolist() == [67.75, 74.5, 67.1, 60.4]
    assert round(float(adj.max() / close.iloc[-1] - 1), 3) == 0.233   # 真實回落 23%,不是 2367%
    same = gen._action_adjusted_close(dates, close, "1111", cal)
    assert same.tolist() == close.tolist()


def test_archived_zone_alerts_flags_price_back_in_zone(tmp_path):
    import json
    from datetime import date
    arch = tmp_path / "archive.json"
    arch.write_text(json.dumps([
        {"ticker": "8150", "zone_low": "86.5", "zone_high": "89.5", "stop": "82.5", "pruned_at": "2026-09-04", "prune_reason": "整批歸檔"},
        {"ticker": "5285", "zone_low": "90", "zone_high": "95", "stop": "88", "pruned_at": "2026-08-28", "prune_reason": gen.WATCHLIST_STOP_BREACH_PREFIX + ":現價84"},
        {"ticker": "2049", "zone_low": "200", "zone_high": "210", "stop": "190", "pruned_at": "2026-09-04", "prune_reason": "整批歸檔"},
        {"ticker": "3227", "zone_low": "188", "zone_high": "193", "stop": "184", "pruned_at": "2026-06-01", "prune_reason": "逾期"},
        {"ticker": "6532", "zone_low": "96.5", "zone_high": "100", "stop": "91.5", "pruned_at": "2026-09-04", "prune_reason": "整批歸檔"},
    ]), encoding="utf-8")
    closes = {"8150": 84.4, "5285": 92.0, "2049": 230.0, "3227": 190.0, "6532": 98.0}
    tech = lambda tk, as_of: {"close": closes[tk]}
    out = gen._archived_zone_alerts(date(2026, 9, 15), {"6532"}, archive_path=arch, technical=tech)
    assert [a["ticker"] for a in out] == ["8150"]      # 低於下緣但未破失效價仍提示
    assert out[0]["zone_high"] == 89.5 and out[0]["pruned_at"] == "2026-09-04"
    closes["8150"] = 82.0                               # 破失效價 → 不提示
    assert gen._archived_zone_alerts(date(2026, 9, 15), set(), archive_path=arch, technical=tech) == [
        a for a in gen._archived_zone_alerts(date(2026, 9, 15), set(), archive_path=arch, technical=tech) if a["ticker"] == "6532"]
    assert gen._archived_zone_alerts(date(2026, 9, 15), set(), archive_path=tmp_path / "missing.json", technical=tech) == []
