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
    # 5% 以下不收(2026-10-16 #29 起下限 5%;8% 以下為 shallow5_v2 帶)
    assert gen._rere_lane_ok(_rt(wash=0.04)) is None   # 2026-10-16 起下限為 5%(#29)
    assert gen._rere_lane_ok(_rt(wash=0.079)) == "shallow5_v2"   # 5~8% 帶(2026-10-16 起)


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
    assert gen._rere_lane_ok(_rt(wash=0.06, prev5=500)) == "shallow5_v2"      # 2026-10-16 起 5~8% 帶(#29)
    assert gen._rere_lane_ok(_rt(wash=0.04, prev5=500)) is None               # <5% 仍不是型態
    assert gen._rere_lane_ok(_rt()) == "shakeout"  # 現行標籤優先,forward cohort 不變
    assert gen._rere_lane_ok(_rt(wash=0.09)) == "shallow"


def test_rere_lane_v2_keeps_other_gates():
    assert gen._rere_lane_ok(_rt(wash=0.04, prev5=500)) is None
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


def test_rere_stop_is_40pct_below_signal_close_and_pattern_line_is_reported():
    assert gen.RERE_STOP_LOSS_PCT == 0.40 and gen.RERE_PATTERN_LINE_MULT == 0.97
    row = {"ticker": "6278", "sector": "光電業", "lane": "rere", "ptype": "shallow_v2", "status": "rere·淺洗盤型v2·小倉(60日,配停損)",
           "kind": "small", "zone_low": 179.4, "zone_high": 185.8, "stop": round(183.0 * 0.6, 2), "pattern_line": 173.0,
           "pred20": float("nan"), "wash": 0.093, "v20": -0.044, "vol_ratio": 0.41}
    out = gen._format_entry_row(1, row, {"6278": "台表科"})
    assert out["stop"] == "109.8" and out["pattern_line"] == "173.0"
    assert "停損=訊號收盤−40%" in out["reason"] and "型態參考線 173.0" in out["reason"]
    main_row = {"ticker": "2330", "sector": "半導體業", "status": "可小倉觸發", "kind": "go", "zone_low": 1, "zone_high": 2, "stop": 0.9,
                "pred20": 0.01, "v20": 0.03, "vol_ratio": 0.8, "rsi": 55, "foreign20": 10, "bucket": ""}
    assert gen._format_entry_row(1, main_row, {})["pattern_line"] is None   # 主 lane 不受影響


def test_neutral_order_key_is_deterministic_bounded_and_changes_by_date():
    from datetime import date

    d1, d2 = date(2026, 10, 13), date(2026, 10, 14)
    tickers = [f"{2300 + i}" for i in range(40)]
    k1 = [gen._neutral_order_key(d1, t) for t in tickers]
    assert k1 == [gen._neutral_order_key(d1, t) for t in tickers]            # 同一天重跑相同
    assert all(0.0 <= k < 1.0 for k in k1) and len(set(k1)) == len(k1)
    order1 = sorted(tickers, key=lambda t: gen._neutral_order_key(d1, t))
    order2 = sorted(tickers, key=lambda t: gen._neutral_order_key(d2, t))
    assert order1 != order2                                                   # 不會長期偏向固定幾檔
    assert gen._neutral_order_key(d1, "278") == gen._neutral_order_key(d1, "0278")
    assert order1 != tickers and order1 != tickers[::-1]                      # 不是代號順序


def test_neutral_key_stays_bounded():
    from datetime import date

    keys = [gen._neutral_order_key(date(2026, 10, 13), f"{1000 + i}") for i in range(500)]
    # 2026-10-16 起分數=純中性鍵(#26 後族群分級移出排序,只作顯示);保留值域檢查,
    # 若未來再疊加其他整數級距,鍵 < 1 仍保證不越級。
    assert max(keys) < 1.0 and min(keys) >= 0.0


def _rere_pool():
    rows = ([dict(ticker=f"S{i}", ptype="shakeout", score=0.99 - i * 0.01) for i in range(8)]
            + [dict(ticker=f"A{i}", ptype="shakeout_v2", score=0.89 - i * 0.01) for i in range(5)]
            + [dict(ticker=f"B{i}", ptype="shallow_v2", score=0.79 - i * 0.01) for i in range(5)]
            + [dict(ticker=f"F{i}", ptype="shallow5_v2", score=0.69 - i * 0.01) for i in range(5)])
    return sorted(rows, key=lambda r: -r["score"])


def test_choose_rere_without_veto_matches_the_old_quota_rule():
    rows = _rere_pool()
    old = ([r for r in rows if r["ptype"] not in gen.RERE_NO_FOREIGN_PTYPES][:gen.RERE_MAX]
           + gen._pick_rere_v2(rows) + [r for r in rows if r["ptype"] == "shallow5_v2"][:gen.RERE_SHALLOW5_MAX])
    assert [r["ticker"] for r in gen._choose_rere(rows, set())] == [r["ticker"] for r in old]


def test_choose_rere_backfills_vetoed_slots_from_the_same_group():
    """#42:被否決者不佔名額,同組下一順位補上;原本會入選的被否決者附在最後(顯示成否決卡,不進帳本)。"""
    out = [r["ticker"] for r in gen._choose_rere(_rere_pool(), {"S2", "A0", "F1", "S7"})]
    assert out[:6] == ["S0", "S1", "S3", "S4", "S5", "S6"]               # 現行三型仍 6 張,S2 由 S6 補
    v2 = [t for t in out if t[0] in "AB"]
    assert v2[:6] == ["A1", "A2", "A3", "B0", "B1", "B2"]                # v2 仍兩型各半,A0 由 A3 補
    assert [t for t in out if t.startswith("F")][:3] == ["F0", "F2", "F3"]
    assert out[-3:] == ["S2", "A0", "F1"]                                  # S7 本來就不會入選,不顯示


def _main_pool(n=15):
    return [dict(ticker=f"M{i:02d}", kind="go", score=1.0 - i * 0.01) for i in range(n)]


def test_choose_main_without_veto_matches_the_old_cap():
    rows = _main_pool()
    assert gen._choose_main(rows, set()) == rows[:gen.MAIN_MAX]


def test_choose_main_backfills_vetoed_slots_from_rank_13():
    """#43:被否決者不佔 12 檔名額,由第 13 名起補上;原本會入選的被否決者附在最後(顯示成否決卡)。"""
    out = [r["ticker"] for r in gen._choose_main(_main_pool(), {"M02", "M13"})]
    assert out[:gen.MAIN_MAX] == [f"M{i:02d}" for i in range(13) if i != 2]   # M12 補上 M02 的名額
    assert out[gen.MAIN_MAX:] == ["M02"]                                       # M13 本來就不會入選,不顯示


def test_mops_alert_tickers_uses_the_same_window_and_keywords(tmp_path, monkeypatch):
    import datetime as _dt
    news = tmp_path / "新聞資料"
    news.mkdir()
    (news / "announcements_202610.csv").write_text(
        "Ticker,Name,Date,Time,Title,Market\n"
        "1711,永光,2026-10-15,17:00,本公司董事會決議辦理現金增資,上市\n"
        "2330,台積電,2026-10-16,17:00,本公司董事會決議買回庫藏股並辦理減資,上市\n"
        "3008,大立光,2026-10-12,17:00,本公司私募普通股定價,上市\n"
        "2454,聯發科,2026-10-16,15:00,本公司受邀參加法人說明會,上市\n", encoding="utf-8")
    monkeypatch.setattr(gen, "BASE_DIR", tmp_path)
    assert gen._mops_alert_tickers(_dt.date(2026, 10, 16)) == {"1711"}    # 庫藏股排除;10/12 超過 3 日
    monkeypatch.setattr(gen, "BASE_DIR", tmp_path / "missing")
    assert gen._mops_alert_tickers(_dt.date(2026, 10, 16)) == set()       # 檔案缺失不擋名單


def _ms_cand(tk, v20, kind="go"):
    return {"ticker": tk, "v20": v20, "kind": kind, "score": 0.0}


def test_ms_shadow_rank_filters_and_orders_by_momentum():
    """#45 MS 影子:距月線 <= +4%、非否決、非資料待確認、有動能值者,依 6-1 動能由高到低(同值依代號)。"""
    from datetime import date
    cands = [_ms_cand("A", 0.03), _ms_cand("B", 0.05), _ms_cand("C", 0.02), _ms_cand("D", 0.01, kind="watch"),
             _ms_cand("E", 0.01), _ms_cand("F", -0.01), _ms_cand("G", 0.04)]
    mom = {"A": 0.5, "B": 0.9, "C": 0.9, "D": 0.9, "E": None, "F": 0.6, "G": 0.5}
    out = gen._ms_shadow_rank(cands, {"C"}, date(2026, 10, 19), momentum=lambda tk, as_of: mom[tk])
    assert [r["ticker"] for r in out] == ["F", "A", "G"]                 # B 距月線>4%、C 否決、D 待確認、E 無動能
    assert all(r["lane"] == "main_ms" for r in out) and out[0]["mom_6_1"] == 0.6
    assert "lane" not in cands[0] and "mom_6_1" not in cands[0]          # 回傳副本,不改主 lane 列
    many = [_ms_cand(f"T{i:02d}", 0.0) for i in range(20)]
    assert len(gen._ms_shadow_rank(many, set(), date(2026, 10, 19), momentum=lambda tk, as_of: 0.1)) == gen.MAIN_MAX


def test_momentum_6_1_is_t20_over_t120_on_the_adjusted_basis_without_look_ahead(monkeypatch):
    import numpy as np
    dates = pd.bdate_range("2026-01-01", periods=140)
    close = np.where(np.arange(140) < 60, 100.0, 50.0)                  # 第 60 根 1 拆 2(價格減半)
    close[130:] = 999.0                                                 # as_of 之後的價格不得影響結果
    frame = pd.DataFrame({"Date": dates, "Close": close})
    monkeypatch.setattr(gen, "prepare_stock_frame", lambda path: frame.copy())
    split = pd.DataFrame({"stock_id": ["9999"], "date": [dates[60]], "factor": [0.5]})
    as_of = dates[129].date()
    monkeypatch.setattr(gen, "_ECON_CALENDAR", split)
    assert gen._momentum_6_1("9999", as_of) == pytest.approx(0.0)       # 還原後拆股不算虧損
    monkeypatch.setattr(gen, "_ECON_CALENDAR", split.iloc[0:0])
    assert gen._momentum_6_1("9999", as_of) == pytest.approx(-0.5)      # 未還原會誤算成 −50%
    assert gen._momentum_6_1("9999", dates[100].date()) is None          # 不足 121 根


def test_ms_shadow_rows_are_exported_with_momentum_and_their_own_lane(monkeypatch):
    monkeypatch.setattr(gen, "_name_map", lambda: {"2330": "台積電"})
    monkeypatch.setattr(gen, "_chipk_source_label", lambda result: "manual")
    row = {"ticker": "2330", "sector": "半導體業", "status": "可小倉觸發", "kind": "go", "zone_low": 1, "zone_high": 2,
           "stop": 0.9, "pred20": 0.01, "v20": 0.03, "vol_ratio": 0.8, "rsi": 55, "foreign20": 10, "bucket": "",
           "lane": "main_ms", "mom_6_1": 0.354}
    result = {"as_of_close": None, "as_of_date": "2026-10-19", "rows": [], "shadow_legacy_main": [],
              "shadow_ms_main": [row], "shadow_rere_pool": []}
    payload = gen.to_entry_json(result, "2026-10-20")
    ms = payload["shadow_ms_main"]
    assert ms[0]["lane"] == "main_ms" and ms[0]["mom_6_1"] == 35.4 and "MS 動能影子" in ms[0]["reason"]
    assert payload["rows"] == [] and "mom_6_1" not in gen._format_entry_row(1, {**row, "lane": "main"}, {})
