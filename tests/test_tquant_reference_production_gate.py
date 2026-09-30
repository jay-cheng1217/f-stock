from __future__ import annotations

import pandas as pd

from scripts.build_unified_signals import (
    SIGNAL_TYPE_20D_ONLY,
    SIGNAL_TYPE_DUAL,
    SIGNAL_TYPE_NONE,
    SIGNAL_TYPE_T1_ONLY,
    TRADABILITY_REASON_TQUANT_REFERENCE_GATE,
    TRADABILITY_STATUS_OPEN,
    _apply_tquant_reference_gate,
    _build_tquant_reference_frame,
)


def _base_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "ticker": "1111",
                "signal_type": SIGNAL_TYPE_20D_ONLY,
                "rank_20d": 1,
                "rank_t1": None,
                "tradability_status": TRADABILITY_STATUS_OPEN,
                "tradability_blocked": False,
                "tradability_reason": "",
            },
            {
                "ticker": "2222",
                "signal_type": SIGNAL_TYPE_20D_ONLY,
                "rank_20d": 2,
                "rank_t1": None,
                "tradability_status": TRADABILITY_STATUS_OPEN,
                "tradability_blocked": False,
                "tradability_reason": "",
            },
            {
                "ticker": "3333",
                "signal_type": SIGNAL_TYPE_DUAL,
                "rank_20d": 3,
                "rank_t1": 1,
                "tradability_status": TRADABILITY_STATUS_OPEN,
                "tradability_blocked": False,
                "tradability_reason": "",
            },
        ]
    )


def _fake_tquant_frame(consensus_by_ticker: dict[str, str]):
    def fake(unified: pd.DataFrame, prediction_date: str) -> pd.DataFrame:
        out = unified.copy()
        out["tquant_consensus"] = out["ticker"].map(consensus_by_ticker).fillna("reject")
        out["tquant_support_votes"] = out["tquant_consensus"].map({"strong": 6, "watch": 3}).fillna(1).astype(int)
        out["tquant_risk_flags"] = out["tquant_consensus"].map({"reject": 3, "watch": 1, "strong": 0}).astype(int)
        out["tquant_support_reasons"] = out["tquant_consensus"].map(
            {"strong": "vam;mrat;baz;revenue;inst;ml_edge", "watch": "vam;mrat;ml_edge"}
        ).fillna("ml_edge")
        out["tquant_risk_reasons"] = out["tquant_consensus"].map({"reject": "gap;beta;crowding"}).fillna("")
        return out

    return fake


def test_tquant_reference_gate_blocks_20d_reject_and_keeps_watch(monkeypatch):
    monkeypatch.delenv("TQUANT_REFERENCE_GATE_ENABLED", raising=False)
    monkeypatch.setenv("TQUANT_REFERENCE_GATE_MIN_CONSENSUS", "watch")
    monkeypatch.setattr(
        "scripts.build_unified_signals._build_tquant_reference_frame",
        _fake_tquant_frame({"1111": "reject", "2222": "watch", "3333": "strong"}),
    )

    out = _apply_tquant_reference_gate(_base_df(), prediction_date="2026-05-29")
    by_ticker = {row["ticker"]: row for row in out.to_dict("records")}

    assert by_ticker["1111"]["signal_type"] == SIGNAL_TYPE_NONE
    assert bool(by_ticker["1111"]["tradability_blocked"])
    assert TRADABILITY_REASON_TQUANT_REFERENCE_GATE in by_ticker["1111"]["tradability_reason"]
    assert not bool(by_ticker["1111"]["tquant_reference_gate_pass"])

    assert by_ticker["2222"]["signal_type"] == SIGNAL_TYPE_20D_ONLY
    assert not bool(by_ticker["2222"]["tradability_blocked"])
    assert bool(by_ticker["2222"]["tquant_reference_gate_pass"])


def test_tquant_reference_gate_downgrades_dual_to_t1_only(monkeypatch):
    monkeypatch.delenv("TQUANT_REFERENCE_GATE_ENABLED", raising=False)
    monkeypatch.setattr(
        "scripts.build_unified_signals._build_tquant_reference_frame",
        _fake_tquant_frame({"1111": "strong", "2222": "strong", "3333": "reject"}),
    )

    out = _apply_tquant_reference_gate(_base_df(), prediction_date="2026-05-29")
    row = out.loc[out["ticker"].eq("3333")].iloc[0]

    assert row["signal_type"] == SIGNAL_TYPE_T1_ONLY
    assert not bool(row["tradability_blocked"])
    assert TRADABILITY_REASON_TQUANT_REFERENCE_GATE in row["tradability_reason"]
    assert not bool(row["tquant_reference_gate_pass"])


def test_tquant_reference_gate_can_be_disabled(monkeypatch):
    monkeypatch.setenv("TQUANT_REFERENCE_GATE_ENABLED", "0")
    monkeypatch.setattr(
        "scripts.build_unified_signals._build_tquant_reference_frame",
        _fake_tquant_frame({"1111": "reject", "2222": "reject", "3333": "reject"}),
    )

    out = _apply_tquant_reference_gate(_base_df(), prediction_date="2026-05-29")

    assert out["signal_type"].tolist() == [SIGNAL_TYPE_20D_ONLY, SIGNAL_TYPE_20D_ONLY, SIGNAL_TYPE_DUAL]
    assert not out["tradability_blocked"].astype(bool).any()
    assert out["tquant_reference_gate_enabled"].eq(False).all()
    assert out["tquant_reference_gate_pass"].eq(False).all()


def test_tquant_reference_gate_strong_mode_rejects_watch(monkeypatch):
    monkeypatch.delenv("TQUANT_REFERENCE_GATE_ENABLED", raising=False)
    monkeypatch.setenv("TQUANT_REFERENCE_GATE_MIN_CONSENSUS", "strong")
    monkeypatch.setattr(
        "scripts.build_unified_signals._build_tquant_reference_frame",
        _fake_tquant_frame({"1111": "watch", "2222": "strong", "3333": "strong"}),
    )

    out = _apply_tquant_reference_gate(_base_df(), prediction_date="2026-05-29")
    by_ticker = {row["ticker"]: row for row in out.to_dict("records")}

    assert by_ticker["1111"]["signal_type"] == SIGNAL_TYPE_NONE
    assert by_ticker["1111"]["tquant_reference_gate_min_consensus"] == "strong"
    assert not bool(by_ticker["1111"]["tquant_reference_gate_pass"])
    assert by_ticker["2222"]["signal_type"] == SIGNAL_TYPE_20D_ONLY


def test_tquant_reference_frame_adds_signal_date_for_rank_helpers(monkeypatch):
    seen: dict[str, object] = {}

    monkeypatch.setattr(
        "scripts.build_unified_signals._tquant_load_price_history",
        lambda tickers, start, end, db_path: pd.DataFrame(),
    )
    monkeypatch.setattr(
        "scripts.build_unified_signals._tquant_point_in_time_features",
        lambda ticker, prediction_date, prices: {
            "tquant_data_status": "ok",
            "tquant_vam_21d": 1.0,
            "tquant_mrat_21_200": 0.1,
            "tquant_baz": 0.2,
            "tquant_expanded_momentum": 3.0,
            "tquant_price_structure_vote": True,
            "tquant_financing_crowding_risk": False,
            "tquant_institutional_vote": True,
        },
    )
    monkeypatch.setattr(
        "scripts.build_unified_signals._tquant_latest_revenue_features",
        lambda ticker, prediction_date: {"revenue_data_status": "ok", "revenue_yoy_3m_avg": 0.2},
    )

    def fake_rank(frame: pd.DataFrame) -> pd.DataFrame:
        seen["signal_date"] = frame["signal_date"].tolist()
        return frame

    def fake_votes(frame: pd.DataFrame) -> pd.DataFrame:
        out = frame.copy()
        out["tquant_consensus"] = "strong"
        return out

    monkeypatch.setattr("scripts.build_unified_signals._tquant_annotate_cross_section_ranks", fake_rank)
    monkeypatch.setattr("scripts.build_unified_signals._tquant_annotate_votes", fake_votes)

    out = _build_tquant_reference_frame(_base_df().head(1), prediction_date="2026-05-29")

    assert seen["signal_date"] == ["2026-05-29"]
    assert out.loc[0, "tquant_consensus"] == "strong"
