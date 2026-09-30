import pytest

from scripts import send_position_risk_cut_report as report
from scripts.send_position_risk_cut_report import classify_position


def _position(ticker="2330"):
    return {
        "ticker": ticker,
        "entry_date": "2026-05-15",
        "entry_price": 100.0,
        "target_weight": 0.05,
    }


def _signal(**overrides):
    base = {
        "ticker": "2330",
        "recommendation_20d": "建議買進",
        "rank_20d": 6,
        "guardrail_blocked": 0,
        "guardrail_trigger_reason": "",
        "guardrail_blocked_20d": 0,
        "guardrail_trigger_reason_20d": "",
        "tradability_blocked": 0,
        "tradability_reason": "",
    }
    base.update(overrides)
    return base


def _k(**overrides):
    base = {
        "date": "2026-07-01",
        "close": 110.0,
        "ma5": 105.0,
        "ma20": 100.0,
        "ma60": 90.0,
        "rsi14": 55.0,
        "return_1d": 0.01,
    }
    base.update(overrides)
    return base


def test_missing_signal_pool_is_not_negative_by_itself():
    decision = classify_position(_position(), None, _k())

    assert decision.category == "green"
    assert not any("signal pool" in reason for reason in decision.reasons)


def test_red_priority_beats_yellow():
    decision = classify_position(
        _position(),
        _signal(rank_20d=35),
        _k(close=104.0, ma5=105.0, ma20=100.0, ma60=90.0),
    )

    assert decision.category == "red"
    assert any("rank_20d" in reason for reason in decision.reasons)
    assert any("短線跌破" not in reason for reason in decision.reasons)


def test_yellow_for_bull_but_below_ma5():
    decision = classify_position(
        _position(),
        _signal(),
        _k(close=104.0, ma5=105.0, ma20=100.0, ma60=90.0),
    )

    assert decision.category == "yellow"
    assert any("BULL alignment" in reason for reason in decision.reasons)


def test_bull_pullback_below_ma5_and_ma20_is_yellow_without_guardrail():
    decision = classify_position(
        _position(),
        _signal(),
        _k(close=97.0, ma5=104.0, ma20=100.0, ma60=90.0),
    )

    assert decision.category == "yellow"
    assert any("BULL alignment 下回檔" in reason for reason in decision.reasons)


def test_guardrail_keeps_bull_pullback_red():
    decision = classify_position(
        _position(),
        _signal(tradability_blocked=1, tradability_reason="OVERHEAT_RISK"),
        _k(close=97.0, ma5=104.0, ma20=100.0, ma60=90.0),
    )

    assert decision.category == "red"
    assert decision.guardrail_reasons == ["tradability_reason=OVERHEAT_RISK"]


def test_green_for_bull_buy_top30_above_ma5():
    decision = classify_position(_position(), _signal(rank_20d=8), _k())

    assert decision.category == "green"
    assert any("rank_20d=8" in reason for reason in decision.reasons)
    assert any("recommendation_20d=建議買進" in reason for reason in decision.reasons)


def test_hard_guardrail_traceable_to_unified_signal_reason():
    decision = classify_position(
        _position(),
        _signal(tradability_blocked=1, tradability_reason="LOW_LIQUIDITY"),
        _k(),
    )

    assert decision.category == "red"
    assert decision.guardrail_reasons == ["tradability_reason=LOW_LIQUIDITY"]


def test_red_for_unrealized_loss_near_stop_even_without_signal_row(monkeypatch):
    monkeypatch.setattr(report, "cum_dividend", lambda *args: 0.0)
    decision = classify_position(
        _position(),
        None,
        _k(close=89.0, ma5=95.0, ma20=90.0, ma60=80.0),
    )

    assert decision.category == "red"
    assert any("逼近硬停損" in reason for reason in decision.reasons)


def test_dividend_adjustment_stops_at_k_snapshot_date(monkeypatch):
    calls = []

    def fake_cum_dividend(ticker, after_date, until_date):
        calls.append((ticker, after_date, until_date))
        return 10.0

    monkeypatch.setattr(report, "cum_dividend", fake_cum_dividend)
    decision = classify_position(
        _position(),
        None,
        _k(date="2026-06-30", close=89.0, ma5=95.0, ma20=90.0, ma60=80.0),
    )

    assert calls == [("2330", "2026-05-15", "2026-06-30")]
    assert decision.unrealized_return == pytest.approx(-0.01)
    assert not any("逼近硬停損" in reason for reason in decision.reasons)


def test_dividend_failure_never_falls_back_to_raw_stop(monkeypatch):
    monkeypatch.setattr(report, "cum_dividend", lambda *args: (_ for _ in ()).throw(ValueError("bad calendar")))

    decision = classify_position(
        _position(),
        None,
        _k(close=89.0, ma5=95.0, ma20=90.0, ma60=80.0),
    )

    assert decision.unrealized_return is None
    assert decision.category == "yellow"
    assert any("除權息還原失敗" in reason for reason in decision.reasons)
    assert not any("逼近硬停損" in reason for reason in decision.reasons)
