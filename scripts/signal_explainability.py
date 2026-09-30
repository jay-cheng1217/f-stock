"""Advisory explanation labels for daily signal presentation.

This module borrows the lightweight "why BUY/WATCH/SKIP" presentation idea
from small rule-based screeners, but it is deliberately reporting-only. It
must not be used as a production gate, model target, or portfolio allocator.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from typing import Any

RULE_POOL = "signal_explainability_v1_advisory_only"

_BUY_WORDS = ("buy", "建議買進", "買進", "強力買進")
_BLOCK_WORDS = ("block", "blocked", "hard", "halt", "不進場", "停用", "熔斷")

_PENALTY_LABELS = {
    "foreign_selling": "外資賣壓 penalty",
    "gap_risk": "跳空風險 penalty",
    "quality": "品質 penalty",
    "beta": "高 beta penalty",
    "liquidity": "流動性 penalty",
}


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, float):
        return math.isnan(value)
    text = str(value).strip()
    return text == "" or text.lower() in {"nan", "none", "null", "<na>"}


def _as_float(row: Mapping[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = row.get(key)
        if _is_missing(value):
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _as_bool(row: Mapping[str, Any], *keys: str) -> bool:
    for key in keys:
        value = row.get(key)
        if _is_missing(value):
            continue
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        text = str(value).strip().lower()
        if text in {"true", "1", "yes", "y"}:
            return True
        if text in {"false", "0", "no", "n"}:
            return False
    return False


def _clean_text(value: Any) -> str:
    if _is_missing(value):
        return ""
    return str(value).strip()


def _to_pct(value: float | None) -> float | None:
    if value is None or math.isnan(value):
        return None
    return value * 100.0 if abs(value) <= 3.0 else value


def _fmt_pct(value: float | None, digits: int = 1) -> str:
    pct = _to_pct(value)
    if pct is None:
        return ""
    return f"{pct:+.{digits}f}%"


def _append_unique(items: list[str], value: str | None) -> None:
    text = (value or "").strip()
    if text and text not in items:
        items.append(text)


def _split_reason_tokens(*values: Any) -> list[str]:
    tokens: list[str] = []
    for value in values:
        text = _clean_text(value)
        if not text:
            continue
        for token in re.split(r"[;,/|]+", text):
            token = token.strip()
            if token:
                tokens.append(token)
    return tokens


def _recommendation_is_buy(row: Mapping[str, Any]) -> bool:
    text = " ".join(
        _clean_text(row.get(key))
        for key in ("recommendation", "recommendation_20d", "signal", "signal_20d")
    ).lower()
    return any(word.lower() in text for word in _BUY_WORDS)


def _has_explicit_block(row: Mapping[str, Any]) -> bool:
    if _as_bool(row, "tradability_blocked", "penalty_hard_block", "guardrail_blocked", "guardrail_blocked_20d"):
        return True
    text = " ".join(
        _clean_text(row.get(key))
        for key in (
            "production_gate_status",
            "tradability_status",
            "signal_type",
            "signal_type_before_gate",
            "penalty_overlay_reason",
        )
    ).lower()
    return any(word in text for word in _BLOCK_WORDS)


def explain_signal_row(row: Mapping[str, Any], *, source: str = "unified") -> dict[str, Any]:
    """Return a user-facing, reporting-only explanation for one signal row."""

    signal_type = _clean_text(row.get("signal_type"))
    target_units = _as_float(row, "target_units")
    has_units = target_units is not None
    active_units = bool(has_units and target_units > 0)
    explicit_block = _has_explicit_block(row)
    rec_buy = _recommendation_is_buy(row)

    if active_units:
        action = "ENTRY_READY"
        action_label = "可進場"
    elif explicit_block:
        action = "BLOCKED"
        action_label = "不進場"
    elif rec_buy and source == "prediction":
        action = "MODEL_CANDIDATE"
        action_label = "模型候選"
    elif rec_buy or signal_type in {"Dual", "20D_only", "T1_only"}:
        action = "WATCH"
        action_label = "觀察"
    else:
        action = "SKIP"
        action_label = "略過"

    drivers: list[str] = []
    warnings: list[str] = []

    rank_20d = _as_float(row, "rank_20d", "two_stage_rank", "two_stage_rank_20d")
    if rank_20d is not None and rank_20d <= 10:
        _append_unique(drivers, f"20D rank #{int(rank_20d)}")

    rank_t1 = _as_float(row, "rank_t1")
    if signal_type == "Dual":
        _append_unique(drivers, "20D + T+1 dual confirmation")
    elif rank_t1 is not None and rank_t1 <= 10:
        _append_unique(drivers, f"T+1 rank #{int(rank_t1)}")

    pred_return = _as_float(row, "pred_return_20d", "risk_adjusted_return", "penalty_overlay_score")
    if pred_return is not None:
        pred_pct = _to_pct(pred_return)
        if pred_pct is not None and pred_pct >= 3.0:
            _append_unique(drivers, f"20D expected {_fmt_pct(pred_return)}")
        elif pred_pct is not None and pred_pct < 1.0:
            _append_unique(warnings, f"20D edge thin {_fmt_pct(pred_return)}")

    historical_win_rate = _as_float(row, "historical_win_rate")
    if historical_win_rate is not None:
        win_rate = historical_win_rate if historical_win_rate > 1 else historical_win_rate * 100.0
        if win_rate >= 60:
            _append_unique(drivers, f"historical win rate {win_rate:.0f}%")
        elif win_rate < 50:
            _append_unique(warnings, f"historical win rate {win_rate:.0f}%")

    beta = _as_float(row, "beta_60", "beta_60_20d", "beta_60_t1")
    if beta is not None:
        if beta <= 0.8:
            _append_unique(drivers, f"lower beta {beta:.2f}")
        elif beta >= 1.3:
            _append_unique(warnings, f"high beta {beta:.2f}")

    price_vs_ma20 = _as_float(row, "price_vs_ma20", "price_vs_ma20_20d")
    if price_vs_ma20 is not None:
        ma20_pct = _to_pct(price_vs_ma20)
        if ma20_pct is not None and ma20_pct >= 10:
            _append_unique(warnings, f"short-term stretch vs MA20 {_fmt_pct(price_vs_ma20)}")
        elif ma20_pct is not None and ma20_pct <= -8:
            _append_unique(warnings, f"below MA20 {_fmt_pct(price_vs_ma20)}")
        elif ma20_pct is not None and ma20_pct > 0:
            _append_unique(drivers, "above MA20")

    price_vs_ma60 = _as_float(row, "price_vs_ma60", "price_vs_ma60_20d", "price_vs_ma60_t1")
    if price_vs_ma60 is not None:
        ma60_pct = _to_pct(price_vs_ma60)
        if ma60_pct is not None and ma60_pct >= 25:
            _append_unique(warnings, f"stretched vs MA60 {_fmt_pct(price_vs_ma60)}")
        elif ma60_pct is not None and ma60_pct > 0:
            _append_unique(drivers, "above MA60")

    gap_pct = _as_float(row, "gap_pct", "gap_pct_20d")
    if gap_pct is not None and (_to_pct(gap_pct) or 0.0) >= 2.0:
        _append_unique(warnings, f"gap risk {_fmt_pct(gap_pct)}")

    roe = _as_float(row, "roe_annualized", "roe_annualized_20d")
    if roe is not None:
        roe_pct = _to_pct(roe)
        if roe_pct is not None and roe_pct >= 15:
            _append_unique(drivers, f"ROE {roe_pct:.1f}%")
        elif roe_pct is not None and roe_pct < 0:
            _append_unique(warnings, f"ROE {roe_pct:.1f}%")

    pe_ratio = _as_float(row, "pe_ratio", "pe_ratio_20d")
    if pe_ratio is not None and pe_ratio >= 80:
        _append_unique(warnings, f"high PE {pe_ratio:.1f}")

    if _as_bool(row, "futures_settlement_window"):
        action_text = _clean_text(row.get("futures_settlement_action"))
        _append_unique(warnings, f"futures settlement window{f' / {action_text}' if action_text else ''}")

    if _as_bool(row, "expensive_momentum_risk"):
        _append_unique(warnings, "expensive momentum cap")

    for token in _split_reason_tokens(
        row.get("penalty_overlay_reason"),
        row.get("tradability_reason"),
        row.get("production_gate_reason"),
        row.get("t1_block_reason"),
    ):
        _append_unique(warnings, _PENALTY_LABELS.get(token, token))

    risk_text = _clean_text(row.get("risk_tags_20d")) or _clean_text(row.get("risk_tags"))
    if risk_text:
        _append_unique(warnings, risk_text)

    if not drivers:
        _append_unique(drivers, "no strong positive driver surfaced")
    if not warnings:
        _append_unique(warnings, "no major advisory warning")

    lead = {
        "ENTRY_READY": "正式 unified gate 仍開放",
        "MODEL_CANDIDATE": "模型排序候選，需再看 unified gate",
        "WATCH": "接近條件，保留觀察",
        "BLOCKED": "被風控或可交易性條件擋下",
        "SKIP": "沒有足夠進場訊號",
    }[action]
    summary = f"{action_label}: {lead}; {drivers[0]}; {warnings[0]}"

    return {
        "rule_pool": RULE_POOL,
        "advisory_only": True,
        "action": action,
        "action_label": action_label,
        "summary": summary,
        "drivers": drivers[:5],
        "warnings": warnings[:6],
    }
