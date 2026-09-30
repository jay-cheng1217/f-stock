"""Short-wave entry overlay for production prediction artifacts.

This layer translates the discretionary observation workflow into auditable
features. The 2026-06-23 version makes the friend pullback + model-support
combo the primary production entry overlay; the environment flag can still
disable reranking for rollback.
"""

from __future__ import annotations

import os
from typing import Any

import numpy as np
import pandas as pd

SHORTWAVE_VERSION = "shortwave_entry_overlay_v3_intraday_zone_20260624"
TACTICAL_BOUNCE_VERSION = "ma60_support_bounce_tactical_v1_20260624"
TACTICAL_BOUNCE_BACKTEST_REF = "ma60_support_bounce_backtest_20260624"
MOMENTUM_CONTINUATION_VERSION = "momentum_continuation_v1_20260625"
MOMENTUM_CONTINUATION_BACKTEST_REF = "momentum_continuation_backtest_20260625"
SHORTWAVE_PROD_ENV = "SHORTWAVE_PROD_RERANK_ENABLED"
SHORTWAVE_ALLOW_NO_CHIPK_HISTORY_ENV = "SHORTWAVE_ALLOW_NO_CHIPK_HISTORY"
FRIEND_COMBO_BACKTEST_REF = "friend_logic_combo_search_20260623_combo_search"
FRIEND_COMBO_MIN_SCORE = 0.99
FRIEND_COMBO_DEVELOPING_SCORE = 0.74
FRIEND_COMBO_SECTOR_HOLD_DAYS = {
    "電子零組件業": 60,
    "半導體業": 60,
    "其他電子業": 60,
    "電腦及週邊設備業": 60,
    "光電業": 60,
    "通信網路業": 60,
    "化學工業": 60,
    "資訊服務業": 60,
    "電子通路業": 20,
    "橡膠工業": 20,
    "鋼鐵工業": 20,
    "建材營造業": 10,
    "電機機械": 10,
}

SHORTWAVE_OUTPUT_COLS = [
    "shortwave_version",
    "shortwave_data_status",
    "shortwave_score",
    "shortwave_score_pre_chipk",
    "shortwave_action",
    "shortwave_reason",
    "shortwave_risk_flags",
    "shortwave_invalid_level",
    "shortwave_strategy_tags",
    "shortwave_missing_strategy_tags",
    "shortwave_entry_strategy_advice",
    "shortwave_entry_zone_low",
    "shortwave_entry_zone_high",
    "shortwave_entry_zone_status",
    "shortwave_entry_zone_note",
    "shortwave_watch_note",
    "shortwave_preferred_hold_days",
    "shortwave_friend_combo_score",
    "shortwave_friend_combo_match",
    "shortwave_model_combo_support",
    "shortwave_ma_score",
    "shortwave_volume_score",
    "shortwave_momentum_score",
    "shortwave_chipk_score",
    "shortwave_chipk_used_in_production",
    "shortwave_rerank_enabled",
    "leaderboard_score_before_shortwave",
    "chipk_asof_date",
    "chipk_source_snapshot_date",
    "chipk_date_alignment",
    "chipk_main_force_1d",
    "chipk_main_force_5d",
    "chipk_main_force_20d",
    "chipk_main_force_score",
    "chipk_history_available",
    "chipk_score_version",
    "chipk_score_orientation",
    "tactical_strategy_name",
    "tactical_version",
    "tactical_action",
    "tactical_score",
    "tactical_reason",
    "tactical_missing_strategy_tags",
    "tactical_entry_strategy_advice",
    "tactical_entry_limit",
    "tactical_entry_zone_low",
    "tactical_entry_zone_high",
    "tactical_stop_loss",
    "tactical_take_profit_2p",
    "tactical_take_profit_4p",
    "tactical_max_hold_days",
    "tactical_position_size_hint",
    "tactical_backtest_ref",
    "momentum_strategy_name",
    "momentum_version",
    "momentum_action",
    "momentum_score",
    "momentum_reason",
    "momentum_missing_strategy_tags",
    "momentum_entry_strategy_advice",
    "momentum_entry_limit",
    "momentum_entry_zone_low",
    "momentum_entry_zone_high",
    "momentum_stop_loss",
    "momentum_take_profit_3p",
    "momentum_take_profit_6p",
    "momentum_max_hold_days",
    "momentum_position_size_hint",
    "momentum_backtest_ref",
    "swing_strategy_name",
    "swing_action",
    "swing_score",
    "swing_entry_zone_low",
    "swing_entry_zone_high",
    "swing_entry_zone_status",
    "swing_entry_strategy_advice",
    "swing_preferred_hold_days",
    "swing_reason",
    "swing_missing_strategy_tags",
]


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def shortwave_prod_rerank_enabled() -> bool:
    return _env_bool(SHORTWAVE_PROD_ENV, default=True)


def shortwave_allow_no_chipk_history() -> bool:
    return _env_bool(SHORTWAVE_ALLOW_NO_CHIPK_HISTORY_ENV, default=False)


def _num(df: pd.DataFrame, *columns: str, default: float = np.nan) -> pd.Series:
    for col in columns:
        if col in df.columns:
            return pd.to_numeric(df[col], errors="coerce")
    return pd.Series(default, index=df.index, dtype="float64")


def _num_coalesce(df: pd.DataFrame, *columns: str, default: float = np.nan) -> pd.Series:
    out = pd.Series(default, index=df.index, dtype="float64")
    for col in columns:
        if col not in df.columns:
            continue
        candidate = pd.to_numeric(df[col], errors="coerce")
        out = out.where(out.notna(), candidate)
    return out


def _text(df: pd.DataFrame, *columns: str) -> pd.Series:
    for col in columns:
        if col in df.columns:
            return df[col].fillna("").astype(str)
    return pd.Series("", index=df.index, dtype="object")


def _clip_score(value: pd.Series) -> pd.Series:
    return value.astype("float64").clip(lower=0.0, upper=1.0).fillna(0.5)


def _append_reason(base: pd.Series, mask: pd.Series, text: str) -> pd.Series:
    out = base.copy()
    if not mask.any():
        return out
    existing = out.loc[mask].fillna("").astype(str)
    out.loc[mask] = [
        "; ".join(part for part in [old.strip(), text] if part)
        for old in existing.tolist()
    ]
    return out


def _preferred_hold_days(sector: pd.Series) -> pd.Series:
    days = sector.map(FRIEND_COMBO_SECTOR_HOLD_DAYS)
    return pd.to_numeric(days, errors="coerce").fillna(20).astype("int16")


def _build_missing_strategy_tags(
    support_pullback: pd.Series,
    volume_quiet: pd.Series,
    macd_positive: pd.Series,
    macd_improving: pd.Series,
) -> pd.Series:
    missing = pd.Series("", index=support_pullback.index, dtype="object")
    missing = _append_reason(missing, ~support_pullback, "missing_friend_pullback_support")
    missing = _append_reason(missing, ~volume_quiet, "missing_volume_quiet")
    missing = _append_reason(missing, ~macd_positive, "missing_macd_hist_positive")
    missing = _append_reason(missing, ~macd_improving, "missing_macd_improving")
    return missing.str.strip()


def _infer_ma_from_price_vs(close: pd.Series, explicit_ma: pd.Series, price_vs_ma: pd.Series) -> pd.Series:
    denom = (1.0 + price_vs_ma).replace(0, np.nan)
    inferred = (close / denom).replace([np.inf, -np.inf], np.nan)
    return explicit_ma.where(explicit_ma.notna(), inferred)


def _build_entry_zone(
    close: pd.Series,
    ma5: pd.Series,
    ma20: pd.Series,
    ma60: pd.Series,
) -> tuple[pd.Series, pd.Series, pd.Series, pd.Series]:
    lower = pd.concat(
        [
            ma5 * 0.97,
            ma20 * 0.90,
            ma60 * 0.95,
        ],
        axis=1,
    ).max(axis=1, skipna=True)
    upper = pd.concat(
        [
            ma5 * 1.08,
            ma20 * 1.06,
            ma60 * 1.10,
            close * 1.055,
        ],
        axis=1,
    ).min(axis=1, skipna=True)
    valid = close.notna() & lower.notna() & upper.notna() & lower.le(upper)

    status = pd.Series("zone_unavailable", index=close.index, dtype="object")
    status.loc[valid & close.between(lower, upper)] = "in_entry_zone"
    status.loc[valid & close.gt(upper)] = "wait_pullback_to_zone"
    status.loc[valid & close.lt(lower)] = "wait_reclaim_zone"

    note = pd.Series("entry_zone_unavailable_missing_ma_or_price", index=close.index, dtype="object")
    note.loc[valid & close.between(lower, upper)] = "price_already_inside_entry_zone"
    note.loc[valid & close.gt(upper)] = "buy_only_if_intraday_pullback_reaches_zone; no_chase_above_zone"
    note.loc[valid & close.lt(lower)] = "buy_only_if_price_reclaims_zone; avoid_buying_below_support_band"
    return lower.where(valid), upper.where(valid), status, note


def _zone_text(low: float, high: float) -> str:
    if pd.isna(low) or pd.isna(high):
        return "entry_zone_unavailable"
    return f"{float(low):.2f}-{float(high):.2f}"


def _build_entry_strategy_advice(
    action: pd.Series,
    missing_strategy_tags: pd.Series,
    preferred_hold_days: pd.Series,
    model_support_count: pd.Series,
    entry_zone_low: pd.Series,
    entry_zone_high: pd.Series,
    entry_zone_status: pd.Series,
) -> pd.Series:
    advice = pd.Series("observe_only", index=action.index, dtype="object")
    miss = missing_strategy_tags.fillna("").astype(str)
    advice.loc[action.eq("NORMAL_ENTRY")] = [
        f"normal_entry_allowed; entry_zone={_zone_text(low, high)}; use planned size; preferred_hold={int(days)}D; monitor_trailing_risk"
        for low, high, days in zip(
            entry_zone_low.loc[action.eq("NORMAL_ENTRY")].tolist(),
            entry_zone_high.loc[action.eq("NORMAL_ENTRY")].tolist(),
            preferred_hold_days.loc[action.eq("NORMAL_ENTRY")].tolist(),
        )
    ]
    small_mask = action.eq("SMALL_ENTRY")
    advice.loc[small_mask] = [
        f"small_entry_only; trigger_if_price_in_entry_zone={_zone_text(low, high)}; zone_status={status}; missing={missing or 'none'}; keep_size_small; no_chase_above_zone; preferred_hold={int(days)}D; supports={int(supports)}"
        for missing, low, high, status, days, supports in zip(
            miss.loc[small_mask].tolist(),
            entry_zone_low.loc[small_mask].tolist(),
            entry_zone_high.loc[small_mask].tolist(),
            entry_zone_status.loc[small_mask].tolist(),
            preferred_hold_days.loc[small_mask].tolist(),
            model_support_count.loc[small_mask].tolist(),
        )
    ]
    watch_mask = action.eq("WATCH_DEVELOPING")
    advice.loc[watch_mask] = [
        f"watch_only; missing={missing or 'none'}; alert_when_combo_reaches_4of4; no_position_yet"
        for missing in miss.loc[watch_mask].tolist()
    ]
    wait_mask = action.eq("WAIT_NEW_STRATEGY")
    advice.loc[wait_mask] = [
        f"wait_new_strategy; trigger_if_price_in_entry_zone={_zone_text(low, high)}; zone_status={status}; missing={missing or 'new_strategy_not_ready'}; do_not_enter_until_at_least_3of4"
        for missing, low, high, status in zip(
            miss.loc[wait_mask].tolist(),
            entry_zone_low.loc[wait_mask].tolist(),
            entry_zone_high.loc[wait_mask].tolist(),
            entry_zone_status.loc[wait_mask].tolist(),
        )
    ]
    advice.loc[action.eq("NO_CHASE")] = "no_chase; wait_for_pullback_or_volume_cooldown"
    advice.loc[action.eq("BLOCK")] = "blocked; do_not_enter"
    advice.loc[action.eq("EXIT_BIAS")] = "exit_bias; avoid_new_entry"
    return advice


def _build_tactical_missing_tags(conditions: dict[str, pd.Series]) -> pd.Series:
    missing = pd.Series("", index=next(iter(conditions.values())).index, dtype="object")
    for tag, mask in conditions.items():
        missing = _append_reason(missing, ~mask.fillna(False), f"missing_{tag}")
    return missing.str.strip()


def _build_tactical_bounce_payload(
    *,
    close_price: pd.Series,
    low_price: pd.Series,
    volume_today: pd.Series,
    vol_ratio: pd.Series,
    ma5: pd.Series,
    ma20: pd.Series,
    ma60: pd.Series,
    ret_1d: pd.Series,
    macd_hist: pd.Series,
    macd_hist_delta: pd.Series,
    rsi14: pd.Series,
    kd_k: pd.Series,
    kd_d: pd.Series,
) -> dict[str, pd.Series]:
    """Build the separate MA60 tactical support-bounce advisory layer.

    The backtest showed this is not strong enough as a standalone hard gate, so
    the output is intentionally a short-term limit-zone strategy, not a ranking
    override.
    """

    index = close_price.index
    low_or_close = low_price.where(low_price.notna(), close_price)
    liquid = close_price.ge(10.0) & volume_today.ge(250_000)
    support_touched = low_or_close.ge(ma60 * 0.94) & low_or_close.le(ma60 * 1.015)
    close_near_support = close_price.ge(ma60 * 0.965) & close_price.le(ma60 * 1.065)
    not_ma20_chase = close_price.le(ma20 * 1.02)
    volume_dryup = vol_ratio.between(0.15, 0.80)
    rsi_tactical = rsi14.between(35, 56)
    kd_tactical = kd_k.le(25) | kd_k.gt(kd_d)
    macd_not_broken = macd_hist.ge(-3.0)
    not_crash_day = ret_1d.fillna(0.0).ge(-0.065)
    close_not_upper_chase = close_price.le(ma5 * 1.06)

    conditions = {
        "liquid": liquid,
        "ma60_support_touch": support_touched,
        "close_near_ma60": close_near_support,
        "no_ma20_chase": not_ma20_chase,
        "volume_dryup": volume_dryup,
        "rsi_35_56": rsi_tactical,
        "kd_oversold_or_cross": kd_tactical,
        "macd_not_broken": macd_not_broken,
        "not_crash_day": not_crash_day,
        "not_upper_chase": close_not_upper_chase,
    }
    setup = pd.Series(True, index=index)
    for mask in conditions.values():
        setup &= mask.fillna(False)

    support_distance_score = (1.0 - (close_price / ma60.replace(0, np.nan) - 1.0).abs() / 0.065).clip(0, 1)
    low_touch_score = (1.0 - (low_or_close / ma60.replace(0, np.nan) - 1.0).abs() / 0.06).clip(0, 1)
    volume_score = (1.0 - (vol_ratio - 0.45).abs() / 0.35).clip(0, 1)
    rsi_score = (1.0 - (rsi14 - 45.0).abs() / 20.0).clip(0, 1)
    kd_score = pd.Series(np.where(kd_k.gt(kd_d), 1.0, np.where(kd_k.le(25), 0.7, 0.0)), index=index)
    macd_delta_score = ((macd_hist_delta.clip(-2, 2).fillna(0.0) + 2.0) / 4.0).clip(0, 1)
    score = (
        0.25 * support_distance_score.fillna(0.0)
        + 0.20 * low_touch_score.fillna(0.0)
        + 0.18 * volume_score.fillna(0.0)
        + 0.14 * rsi_score.fillna(0.0)
        + 0.13 * kd_score.fillna(0.0)
        + 0.10 * macd_delta_score.fillna(0.0)
    ).clip(0, 1)

    entry_limit = ma60
    entry_zone_low = ma60 * 0.99
    entry_zone_high = ma60 * 1.01
    inside_zone = close_price.between(entry_zone_low, entry_zone_high)
    above_zone = close_price.gt(entry_zone_high)

    action = pd.Series("TACTICAL_NO_TRADE", index=index, dtype="object")
    action.loc[setup & inside_zone.fillna(False)] = "TACTICAL_LIMIT_BUY"
    action.loc[setup & above_zone.fillna(False)] = "TACTICAL_WAIT_PULLBACK"
    action.loc[~setup & score.ge(0.70) & close_near_support.fillna(False)] = "TACTICAL_WATCH"

    reason = pd.Series("", index=index, dtype="object")
    reason = _append_reason(reason, setup, "ma60_support_bounce_setup")
    reason = _append_reason(reason, support_touched.fillna(False), "ma60_support_touched")
    reason = _append_reason(reason, volume_dryup.fillna(False), "volume_dryup")
    reason = _append_reason(reason, rsi_tactical.fillna(False), "rsi_tactical")
    reason = _append_reason(reason, kd_tactical.fillna(False), "kd_tactical")
    reason = _append_reason(reason, macd_not_broken.fillna(False), "macd_not_broken")
    reason = _append_reason(reason, action.eq("TACTICAL_WAIT_PULLBACK"), "valid_only_if_intraday_pullback_reaches_zone")
    reason = _append_reason(reason, action.eq("TACTICAL_LIMIT_BUY"), "current_price_inside_ma60_zone")

    missing = _build_tactical_missing_tags(conditions)
    advice = pd.Series("observe_only", index=index, dtype="object")
    actionable = action.isin(["TACTICAL_LIMIT_BUY", "TACTICAL_WAIT_PULLBACK"])
    advice.loc[actionable] = [
        (
            "short_trade_advisory; buy_only_if_price_in_ma60_zone="
            f"{float(low):.2f}-{float(high):.2f}; stop_loss={float(stop):.2f}; "
            f"take_profit={float(tp2):.2f}/{float(tp4):.2f}; max_hold=3D; "
            "small_size_only_backtest_negative; no_chase_above_zone"
        )
        for low, high, stop, tp2, tp4 in zip(
            entry_zone_low.loc[actionable].tolist(),
            entry_zone_high.loc[actionable].tolist(),
            (entry_limit * 0.98).loc[actionable].tolist(),
            (entry_limit * 1.02).loc[actionable].tolist(),
            (entry_limit * 1.04).loc[actionable].tolist(),
        )
    ]
    watch = action.eq("TACTICAL_WATCH")
    advice.loc[watch] = [
        f"watch_only; close_near_ma60_but_missing={tag or 'none'}; wait_for_full_setup"
        for tag in missing.loc[watch].tolist()
    ]

    size_hint = pd.Series("none", index=index, dtype="object")
    size_hint.loc[action.eq("TACTICAL_WATCH")] = "watch_only"
    size_hint.loc[action.isin(["TACTICAL_LIMIT_BUY", "TACTICAL_WAIT_PULLBACK"])] = "small_only"

    inactive = action.eq("TACTICAL_NO_TRADE")
    entry_limit = entry_limit.where(~inactive)
    entry_zone_low = entry_zone_low.where(~inactive)
    entry_zone_high = entry_zone_high.where(~inactive)

    return {
        "tactical_strategy_name": pd.Series("MA60 support-bounce short trade", index=index, dtype="object"),
        "tactical_version": pd.Series(TACTICAL_BOUNCE_VERSION, index=index, dtype="object"),
        "tactical_action": action,
        "tactical_score": score.round(6),
        "tactical_reason": reason.str.strip(),
        "tactical_missing_strategy_tags": missing,
        "tactical_entry_strategy_advice": advice.where(
            ~inactive,
            "not_applicable; ma60_support_strategy_inactive; do_not_use_stale_support_zone",
        ),
        "tactical_entry_limit": entry_limit.round(4),
        "tactical_entry_zone_low": entry_zone_low.round(4),
        "tactical_entry_zone_high": entry_zone_high.round(4),
        "tactical_stop_loss": (entry_limit * 0.98).round(4),
        "tactical_take_profit_2p": (entry_limit * 1.02).round(4),
        "tactical_take_profit_4p": (entry_limit * 1.04).round(4),
        "tactical_max_hold_days": pd.Series(3, index=index, dtype="int16"),
        "tactical_position_size_hint": size_hint,
        "tactical_backtest_ref": pd.Series(TACTICAL_BOUNCE_BACKTEST_REF, index=index, dtype="object"),
    }


def _build_momentum_continuation_payload(
    *,
    close_price: pd.Series,
    volume_today: pd.Series,
    vol_ratio: pd.Series,
    ma5: pd.Series,
    ma20: pd.Series,
    ma60: pd.Series,
    ret_1d: pd.Series,
    ret_5d: pd.Series,
    macd_hist: pd.Series,
    macd_hist_delta: pd.Series,
    rsi14: pd.Series,
    kd_k: pd.Series,
    kd_d: pd.Series,
    beta_60: pd.Series,
    is_special_status: pd.Series,
    macro_ok: pd.Series,
) -> dict[str, pd.Series]:
    """Build a separate short-horizon momentum continuation advisory layer."""

    index = close_price.index
    price_vs_ma5 = close_price / ma5.replace(0, np.nan) - 1.0
    price_vs_ma20 = close_price / ma20.replace(0, np.nan) - 1.0
    price_vs_ma60 = close_price / ma60.replace(0, np.nan) - 1.0
    ma5_vs_ma20 = ma5 / ma20.replace(0, np.nan) - 1.0
    ma20_vs_ma60 = ma20 / ma60.replace(0, np.nan) - 1.0

    liquid = close_price.ge(10.0) & volume_today.ge(500_000)
    near_ma5 = price_vs_ma5.between(-0.035, 0.075)
    trend_stack = ma5_vs_ma20.ge(0.01) & ma20_vs_ma60.ge(0.02)
    strong_not_vertical = price_vs_ma20.between(0.055, 0.36) & price_vs_ma60.between(0.18, 1.25)
    volume_confirmed = vol_ratio.between(0.75, 5.5)
    macd_positive = macd_hist.gt(0)
    macd_ok = macd_positive & (macd_hist_delta.ge(-0.75) | macd_hist_delta.isna())
    rsi_ok = rsi14.between(48, 86)
    kd_ok = kd_k.gt(kd_d) | kd_k.between(45, 92)
    beta_ok = beta_60.le(2.6) | beta_60.isna()
    not_crash_day = ret_1d.fillna(0.0).ge(-0.04)
    not_blowoff = ~(ret_1d.gt(0.095).fillna(False) & vol_ratio.gt(5.0).fillna(False))

    conditions = {
        "liquid": liquid,
        "near_5ma": near_ma5,
        "ma5_ma20_ma60_trend": trend_stack,
        "strong_but_not_vertical": strong_not_vertical,
        "volume_confirmed": volume_confirmed,
        "macd_positive": macd_positive,
        "macd_not_rolling_over": macd_ok,
        "rsi_not_overheated": rsi_ok,
        "kd_not_bearish": kd_ok,
        "beta_not_extreme": beta_ok,
        "not_special_status": ~is_special_status.fillna(False),
        "macro_ok": macro_ok.fillna(True),
        "not_blowoff_gap": not_blowoff,
        "not_crash_day": not_crash_day,
    }
    setup = pd.Series(True, index=index)
    for mask in conditions.values():
        setup &= mask.fillna(False)

    ma5_score = (1.0 - price_vs_ma5.abs() / 0.075).clip(0, 1).fillna(0.0)
    trend_score = ((ma5_vs_ma20.clip(0, 0.12) / 0.12) * 0.55 + (ma20_vs_ma60.clip(0, 0.55) / 0.55) * 0.45).clip(0, 1).fillna(0.0)
    volume_score = (1.0 - (vol_ratio - 1.7).abs() / 3.8).clip(0, 1).fillna(0.0)
    macd_score = pd.Series(np.where(macd_ok.fillna(False), 1.0, np.where(macd_positive.fillna(False), 0.65, 0.0)), index=index)
    rsi_score = (1.0 - (rsi14 - 66.0).abs() / 28.0).clip(0, 1).fillna(0.0)
    continuation_score = (
        0.22 * ma5_score
        + 0.22 * trend_score
        + 0.20 * volume_score
        + 0.18 * macd_score.fillna(0.0)
        + 0.10 * rsi_score
        + 0.08 * kd_ok.fillna(False).astype(float)
    ).clip(0, 1)

    ma5_anchor_low = pd.concat([ma5 * 0.995, close_price * 0.965], axis=1).max(axis=1, skipna=True)
    close_anchor_low = close_price * 0.985
    entry_zone_low = ma5_anchor_low.where(close_price.ge(ma5), close_anchor_low)
    entry_zone_high = close_price * 1.015
    entry_zone_low = pd.concat([entry_zone_low, entry_zone_high], axis=1).min(axis=1, skipna=True)
    entry_limit = close_price
    stop_loss = pd.concat([ma5 * 0.965, close_price * 0.94], axis=1).max(axis=1, skipna=True)
    take_profit_3p = entry_limit * 1.03
    take_profit_6p = entry_limit * 1.06

    action = pd.Series("MOMENTUM_NO_TRADE", index=index, dtype="object")
    action.loc[setup] = "MOMENTUM_LIMIT_BUY"
    action.loc[~setup & continuation_score.ge(0.68) & near_ma5.fillna(False) & macd_positive.fillna(False)] = "MOMENTUM_WATCH"
    action.loc[setup & close_price.gt(entry_zone_high).fillna(False)] = "MOMENTUM_WAIT_PULLBACK"

    reason = pd.Series("", index=index, dtype="object")
    reason = _append_reason(reason, near_ma5.fillna(False), "price_holding_5ma")
    reason = _append_reason(reason, trend_stack.fillna(False), "ma5_ma20_ma60_trend")
    reason = _append_reason(reason, strong_not_vertical.fillna(False), "strong_continuation_zone")
    reason = _append_reason(reason, volume_confirmed.fillna(False), "volume_confirmed")
    reason = _append_reason(reason, macd_positive.fillna(False), "macd_positive")
    reason = _append_reason(reason, macd_ok.fillna(False), "macd_not_rolling_over")
    reason = _append_reason(reason, rsi_ok.fillna(False), "rsi_not_overheated")
    reason = _append_reason(reason, action.eq("MOMENTUM_LIMIT_BUY"), "buy_only_near_5ma_continuation_zone")

    missing = _build_tactical_missing_tags(conditions)
    advice = pd.Series("observe_only", index=index, dtype="object")
    actionable = action.isin(["MOMENTUM_LIMIT_BUY", "MOMENTUM_WAIT_PULLBACK"])
    advice.loc[actionable] = [
        (
            "momentum_continuation_advisory; buy_only_if_price_in_5ma_zone="
            f"{float(low):.2f}-{float(high):.2f}; stop_loss={float(stop):.2f}; "
            f"take_profit={float(tp3):.2f}/{float(tp6):.2f}; max_hold=5D; "
            "confirm_chipk_1d_5d_main_force; no_chase_above_zone"
        )
        for low, high, stop, tp3, tp6 in zip(
            entry_zone_low.loc[actionable].tolist(),
            entry_zone_high.loc[actionable].tolist(),
            stop_loss.loc[actionable].tolist(),
            take_profit_3p.loc[actionable].tolist(),
            take_profit_6p.loc[actionable].tolist(),
        )
    ]
    watch = action.eq("MOMENTUM_WATCH")
    advice.loc[watch] = [
        f"watch_only; momentum_setup_near_miss={tag or 'none'}; wait_for_full_setup"
        for tag in missing.loc[watch].tolist()
    ]

    inactive = action.eq("MOMENTUM_NO_TRADE")
    entry_limit = entry_limit.where(~inactive)
    entry_zone_low = entry_zone_low.where(~inactive)
    entry_zone_high = entry_zone_high.where(~inactive)
    stop_loss = stop_loss.where(~inactive)
    take_profit_3p = take_profit_3p.where(~inactive)
    take_profit_6p = take_profit_6p.where(~inactive)

    size_hint = pd.Series("none", index=index, dtype="object")
    size_hint.loc[action.eq("MOMENTUM_WATCH")] = "watch_only"
    size_hint.loc[action.eq("MOMENTUM_LIMIT_BUY")] = "small_to_medium"
    size_hint.loc[action.eq("MOMENTUM_WAIT_PULLBACK")] = "small_only"

    return {
        "momentum_strategy_name": pd.Series("5MA momentum continuation", index=index, dtype="object"),
        "momentum_version": pd.Series(MOMENTUM_CONTINUATION_VERSION, index=index, dtype="object"),
        "momentum_action": action,
        "momentum_score": continuation_score.round(6),
        "momentum_reason": reason.str.strip(),
        "momentum_missing_strategy_tags": missing,
        "momentum_entry_strategy_advice": advice.where(
            ~inactive,
            "not_applicable; momentum_continuation_strategy_inactive",
        ),
        "momentum_entry_limit": entry_limit.round(4),
        "momentum_entry_zone_low": entry_zone_low.round(4),
        "momentum_entry_zone_high": entry_zone_high.round(4),
        "momentum_stop_loss": stop_loss.round(4),
        "momentum_take_profit_3p": take_profit_3p.round(4),
        "momentum_take_profit_6p": take_profit_6p.round(4),
        "momentum_max_hold_days": pd.Series(5, index=index, dtype="int16"),
        "momentum_position_size_hint": size_hint,
        "momentum_backtest_ref": pd.Series(MOMENTUM_CONTINUATION_BACKTEST_REF, index=index, dtype="object"),
    }


def _build_watch_note(
    action: pd.Series,
    friend_combo_match: pd.Series,
    friend_combo_developing: pd.Series,
    preferred_hold_days: pd.Series,
    strategy_tags: pd.Series,
    model_support_count: pd.Series,
) -> pd.Series:
    notes = pd.Series("", index=action.index, dtype="object")
    notes.loc[friend_combo_match] = [
        f"primary_entry_combo_match; preferred_hold={int(days)}D; supports={int(supports)}"
        for days, supports in zip(
            preferred_hold_days.loc[friend_combo_match].tolist(),
            model_support_count.loc[friend_combo_match].tolist(),
        )
    ]
    notes.loc[friend_combo_developing] = [
        f"developing_watch; wait_for_missing_leg; preferred_hold={int(days)}D; tags={tags}"
        for days, tags in zip(
            preferred_hold_days.loc[friend_combo_developing].tolist(),
            strategy_tags.loc[friend_combo_developing].tolist(),
        )
    ]
    notes.loc[action.eq("WAIT_NEW_STRATEGY")] = "model_buy_but_new_entry_combo_not_ready"
    notes.loc[action.eq("NO_CHASE")] = "new_entry_combo_not_actionable_due_to_chase_risk"
    notes.loc[action.eq("BLOCK")] = "blocked_by_existing_risk_filter"
    notes.loc[action.eq("EXIT_BIAS")] = "existing_model_exit_bias"
    return notes


def _normalize_ticker_series(series: pd.Series) -> pd.Series:
    def normalize(value: object) -> str:
        text = str(value or "").strip()
        if text.isdigit() and len(text) <= 4:
            return text.zfill(4)
        return text

    return series.map(normalize)


def _attach_chipk(pred_df: pd.DataFrame, chipk_df: pd.DataFrame | None) -> pd.DataFrame:
    out = pred_df.copy()
    if chipk_df is None or chipk_df.empty or "ticker" not in out.columns:
        for col in [
            "chipk_asof_date",
            "chipk_source_snapshot_date",
            "chipk_date_alignment",
            "chipk_main_force_1d",
            "chipk_main_force_5d",
            "chipk_main_force_20d",
            "chipk_main_force_score",
            "chipk_history_available",
            "chipk_score_version",
            "chipk_score_orientation",
        ]:
            if col not in out.columns:
                out[col] = np.nan if col != "chipk_history_available" else False
        return out

    left = out.copy()
    left["_shortwave_ticker_key"] = _normalize_ticker_series(left["ticker"])
    right = chipk_df.copy()
    right["_shortwave_ticker_key"] = _normalize_ticker_series(right["ticker"])
    keep = [
        "_shortwave_ticker_key",
        "chipk_asof_date",
        "chipk_source_snapshot_date",
        "chipk_date_alignment",
        "chipk_main_force_1d",
        "chipk_main_force_5d",
        "chipk_main_force_20d",
        "chipk_main_force_score",
        "chipk_history_available",
        "chipk_score_version",
        "chipk_score_orientation",
    ]
    merged = left.merge(right[[col for col in keep if col in right.columns]], on="_shortwave_ticker_key", how="left")
    for col in keep:
        if col == "_shortwave_ticker_key" or col in merged.columns:
            continue
        merged[col] = False if col == "chipk_history_available" else np.nan
    return merged.drop(columns=["_shortwave_ticker_key"], errors="ignore")


def _align_chipk_dates_to_prediction(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if "chipk_asof_date" not in out.columns:
        out["chipk_asof_date"] = np.nan

    source = out["chipk_asof_date"].copy()
    if "chipk_source_snapshot_date" not in out.columns:
        out["chipk_source_snapshot_date"] = source
    else:
        out["chipk_source_snapshot_date"] = out["chipk_source_snapshot_date"].fillna(source)

    chipk_date = pd.to_datetime(source, errors="coerce")
    if "date" in out.columns:
        pred_date = pd.to_datetime(out["date"], errors="coerce")
    else:
        pred_date = pd.Series(pd.NaT, index=out.index)

    alignment = pd.Series("missing", index=out.index, dtype="object")
    has_chipk = chipk_date.notna()
    has_pred = pred_date.notna()
    alignment.loc[has_chipk & ~has_pred] = "prediction_date_missing"
    alignment.loc[has_chipk & has_pred & chipk_date.eq(pred_date)] = "aligned"
    stale = has_chipk & has_pred & chipk_date.lt(pred_date)
    alignment.loc[stale] = "stale_before_prediction"
    source_after = has_chipk & has_pred & chipk_date.gt(pred_date)
    alignment.loc[source_after] = "source_after_prediction_adjusted"
    if source_after.any():
        out.loc[source_after, "chipk_asof_date"] = pred_date.loc[source_after].dt.strftime("%Y-%m-%d")

    out["chipk_date_alignment"] = alignment
    return out


def compute_shortwave_overlay(
    pred_df: pd.DataFrame,
    snapshot: pd.DataFrame | None = None,
    chipk_df: pd.DataFrame | None = None,
    *,
    rerank_enabled: bool | None = None,
) -> pd.DataFrame:
    """Attach short-wave scores/actions to a prediction frame."""

    if pred_df.empty:
        out = pred_df.copy()
        for col in SHORTWAVE_OUTPUT_COLS:
            if col not in out.columns:
                out[col] = []
        return out

    out = pred_df.copy()
    context = snapshot.reindex(out.index) if snapshot is not None and len(snapshot) == len(out) else out
    out = _attach_chipk(out, chipk_df)
    out = _align_chipk_dates_to_prediction(out)

    price_vs_ma5 = _num(context, "price_vs_ma5", "price_vs_ma5_20d")
    price_vs_ma20 = _num(context, "price_vs_ma20", "price_vs_ma20_20d")
    price_vs_ma60 = _num(context, "price_vs_ma60", "price_vs_ma60_20d")
    ma_slope_5 = _num(context, "ma_slope_5")
    ret_1d = _num(context, "return_1d")
    ret_5d = _num(context, "return_5d")
    vol_ratio = _num(context, "vol_ratio")
    volume_today = pd.concat(
        [
            _num_coalesce(context, "volume_today"),
            _num_coalesce(context, "Volume"),
        ],
        axis=1,
    ).max(axis=1, skipna=True)
    avg_20d_volume = pd.concat(
        [
            _num_coalesce(context, "avg_20d_volume", "volume_ma20"),
            _num_coalesce(context, "VOL_MA_20"),
        ],
        axis=1,
    ).max(axis=1, skipna=True)
    liquidity_volume_today = volume_today.where(volume_today.ge(500_000) | volume_today.lt(500), volume_today * 1000.0)
    liquidity_avg_20d_volume = avg_20d_volume.where(
        avg_20d_volume.ge(500_000) | avg_20d_volume.lt(500),
        avg_20d_volume * 1000.0,
    )
    normalized_vol_ratio = (
        liquidity_volume_today / liquidity_avg_20d_volume.replace(0, np.nan)
    ).replace([np.inf, -np.inf], np.nan)
    if vol_ratio.isna().all():
        vol_ratio = normalized_vol_ratio
    else:
        vol_ratio = vol_ratio.where(vol_ratio.notna(), normalized_vol_ratio)
        vol_ratio = vol_ratio.mask(vol_ratio.gt(50) & normalized_vol_ratio.notna(), normalized_vol_ratio)
    macd_hist = _num(context, "macd_hist", "MACDh_12_26_9")
    macd_hist_delta = _num(context, "macd_hist_delta_3d", "macd_hist_delta")
    macd_turn = _num(context, "macd_turn_positive", default=0.0)
    macd_bear = _num(context, "macd_bearish_div", default=0.0)
    macd_bull = _num(context, "macd_bullish_div", default=0.0)
    rsi14 = _num(context, "rsi_14", "RSI_14")
    kd_k = _num_coalesce(context, "kd_k", "K_9_3", "K")
    kd_d = _num_coalesce(context, "kd_d", "D_9_3", "D")
    beta_60 = _num(context, "beta_60", "beta_60_20d")
    sector = _text(out, "sector", "industry", "sector_name")
    close_price = _num(context, "close", "Close")
    if close_price.isna().all():
        close_price = _num(out, "close", "Close")
    low_price = _num_coalesce(context, "low", "Low")
    ma5 = _infer_ma_from_price_vs(close_price, _num(context, "MA_5", "ma5", "ma_5"), price_vs_ma5)
    ma20 = _infer_ma_from_price_vs(close_price, _num(context, "MA_20", "ma20", "ma_20"), price_vs_ma20)
    ma60 = _infer_ma_from_price_vs(close_price, _num(context, "MA_60", "ma60", "ma_60"), price_vs_ma60)
    entry_zone_low, entry_zone_high, entry_zone_status, entry_zone_note = _build_entry_zone(
        close_price,
        ma5,
        ma20,
        ma60,
    )

    ma_score = pd.Series(0.45, index=out.index, dtype="float64")
    ma_score += price_vs_ma5.ge(0).fillna(False).astype(float) * 0.16
    ma_score += price_vs_ma20.ge(0).fillna(False).astype(float) * 0.16
    ma_score += price_vs_ma60.ge(-0.03).fillna(False).astype(float) * 0.10
    ma_score += ma_slope_5.gt(0).fillna(False).astype(float) * 0.08
    ma_score -= price_vs_ma20.lt(-0.08).fillna(False).astype(float) * 0.20
    ma_score -= price_vs_ma20.gt(0.18).fillna(False).astype(float) * 0.12
    ma_score = _clip_score(ma_score)

    volume_score = pd.Series(0.45, index=out.index, dtype="float64")
    volume_score += (vol_ratio.between(0.8, 2.8) & ret_1d.ge(0)).fillna(False).astype(float) * 0.25
    volume_score += (vol_ratio.lt(0.85) & ret_5d.between(-0.08, 0.03)).fillna(False).astype(float) * 0.12
    volume_score -= (vol_ratio.gt(2.5) & ret_1d.lt(-0.02)).fillna(False).astype(float) * 0.28
    volume_score -= vol_ratio.gt(4.0).fillna(False).astype(float) * 0.12
    volume_score = _clip_score(volume_score)

    momentum_score = pd.Series(0.45, index=out.index, dtype="float64")
    momentum_score += macd_hist.gt(0).fillna(False).astype(float) * 0.18
    momentum_score += macd_turn.gt(0).fillna(False).astype(float) * 0.12
    momentum_score += rsi14.between(45, 72).fillna(False).astype(float) * 0.14
    momentum_score += (kd_k.gt(kd_d) & kd_k.lt(85)).fillna(False).astype(float) * 0.10
    momentum_score += macd_bull.gt(0).fillna(False).astype(float) * 0.08
    momentum_score -= macd_bear.gt(0).fillna(False).astype(float) * 0.18
    momentum_score -= rsi14.gt(82).fillna(False).astype(float) * 0.12
    momentum_score = _clip_score(momentum_score)

    support_pullback = (
        price_vs_ma5.between(-0.03, 0.08)
        & price_vs_ma20.between(-0.10, 0.06)
        & price_vs_ma60.between(-0.05, 0.10)
        & ret_1d.le(0.055)
    ).fillna(False)
    volume_quiet = vol_ratio.between(0.25, 0.90).fillna(False)
    macd_positive = macd_hist.gt(0).fillna(False)
    macd_delta_known = macd_hist_delta.notna()
    macd_improving = (
        (macd_delta_known & macd_hist_delta.gt(0))
        | (~macd_delta_known & (macd_turn.gt(0) | (macd_hist.gt(0) & macd_bear.le(0))))
    ).fillna(False)
    friend_combo_score = (
        support_pullback.astype(float)
        + volume_quiet.astype(float)
        + macd_positive.astype(float)
        + macd_improving.astype(float)
    ) / 4.0
    friend_combo_match = friend_combo_score.ge(FRIEND_COMBO_MIN_SCORE)
    friend_combo_developing = friend_combo_score.ge(FRIEND_COMBO_DEVELOPING_SCORE) & ~friend_combo_match

    raw_chipk_score = _num(out, "chipk_main_force_score")
    chipk_score = raw_chipk_score.fillna(0.5).clip(0, 1)
    score_pre_chipk = (
        0.42 * friend_combo_score
        + 0.18 * ma_score
        + 0.20 * volume_score
        + 0.20 * momentum_score
    )
    chipk_history = out.get("chipk_history_available", pd.Series(False, index=out.index)).fillna(False).astype(bool)
    can_use_chipk_for_prod = chipk_history | shortwave_allow_no_chipk_history()
    chipk_alignment = out.get("chipk_date_alignment", pd.Series("missing", index=out.index)).fillna("missing").astype(str)
    chipk_date_valid = ~chipk_alignment.isin(["missing", "stale_before_prediction"])
    use_chipk = can_use_chipk_for_prod & raw_chipk_score.notna() & chipk_date_valid
    score = score_pre_chipk.where(
        ~use_chipk,
        0.38 * friend_combo_score
        + 0.16 * ma_score
        + 0.18 * volume_score
        + 0.18 * momentum_score
        + 0.10 * chipk_score,
    )
    score = _clip_score(score)

    recommendation = _text(out, "recommendation", "recommendation_20d")
    is_buy = recommendation.isin(["強力買進", "建議買進"])
    is_watch_or_blocked = recommendation.str.startswith("觀望", na=False)
    is_sell = recommendation.str.contains("賣出", na=False)
    hot = price_vs_ma20.gt(0.18).fillna(False) | vol_ratio.gt(3.5).fillna(False) | rsi14.gt(82).fillna(False)
    weak = price_vs_ma20.lt(-0.08).fillna(False) | macd_bear.gt(0).fillna(False)

    two_stage_rank = _num(out, "two_stage_rank", "two_stage_rank_20d")
    two_stage_candidate = _num(out, "two_stage_candidate", "is_two_stage_candidate", default=0.0)
    alpha_win_pct = _num(out, "alpha_win_prob_percentile", "alpha_win_prob_20d_percentile")
    pred_return_20d = _num(out, "pred_return_20d", "expected_return_20d")
    risk_adjusted_return = _num(out, "risk_adjusted_return")
    prob_edge = _num(out, "prob_edge")
    macro_action = _text(out, "macro_stock_action", "macro_action")
    macro_block = macro_action.str.contains("block|risk-off-block|risk_off_block", case=False, na=False)
    special_status_flags = [
        _num(out, "is_disposition", default=0.0),
        _num(out, "is_attention", default=0.0),
        _num(out, "is_full_delivery", default=0.0),
        _num(out, "is_suspended", default=0.0),
    ]
    is_special_status = pd.concat(special_status_flags, axis=1).max(axis=1, skipna=True).fillna(0).gt(0)
    model_buy_support = is_buy
    two_stage_support = two_stage_rank.le(15).fillna(False) | two_stage_candidate.gt(0).fillna(False)
    alpha_support = (
        alpha_win_pct.ge(0.70).fillna(False)
        | pred_return_20d.ge(0.02).fillna(False)
        | risk_adjusted_return.ge(0.01).fillna(False)
    )
    prob_edge_support = prob_edge.gt(0).fillna(False)
    macro_ok = ~macro_block
    model_support_count = (
        model_buy_support.astype(int)
        + two_stage_support.astype(int)
        + alpha_support.astype(int)
        + prob_edge_support.astype(int)
        + macro_ok.astype(int)
    )

    strategy_tags = pd.Series("", index=out.index, dtype="object")
    strategy_tags = _append_reason(strategy_tags, support_pullback, "friend_pullback_support")
    strategy_tags = _append_reason(strategy_tags, volume_quiet, "volume_quiet")
    strategy_tags = _append_reason(strategy_tags, macd_positive, "macd_hist_positive")
    strategy_tags = _append_reason(strategy_tags, macd_improving, "macd_improving")
    strategy_tags = _append_reason(strategy_tags, friend_combo_match, "friend_combo_primary")
    strategy_tags = _append_reason(strategy_tags, friend_combo_developing, "friend_combo_developing")
    strategy_tags = _append_reason(strategy_tags, model_buy_support, "ml_buy_signal")
    strategy_tags = _append_reason(strategy_tags, two_stage_support, "two_stage_supported")
    strategy_tags = _append_reason(strategy_tags, alpha_support, "alpha_supported")
    strategy_tags = _append_reason(strategy_tags, prob_edge_support, "positive_prob_edge")
    strategy_tags = _append_reason(strategy_tags, macro_ok, "macro_ok")
    preferred_hold_days = _preferred_hold_days(sector)
    missing_strategy_tags = _build_missing_strategy_tags(
        support_pullback,
        volume_quiet,
        macd_positive,
        macd_improving,
    )

    action = pd.Series("OBSERVE", index=out.index, dtype="object")
    action.loc[is_watch_or_blocked | weak] = "BLOCK"
    action.loc[is_sell] = "EXIT_BIAS"
    entry_base = is_buy & ~(is_watch_or_blocked | weak | is_sell)
    action.loc[entry_base & ~friend_combo_match & ~friend_combo_developing] = "WAIT_NEW_STRATEGY"
    action.loc[entry_base & friend_combo_developing & ~hot] = "SMALL_ENTRY"
    action.loc[entry_base & friend_combo_match & ~hot] = "NORMAL_ENTRY"
    action.loc[entry_base & hot] = "NO_CHASE"
    action.loc[~entry_base & friend_combo_developing & ~(is_watch_or_blocked | weak | is_sell)] = "WATCH_DEVELOPING"
    entry_strategy_advice = _build_entry_strategy_advice(
        action,
        missing_strategy_tags,
        preferred_hold_days,
        model_support_count,
        entry_zone_low,
        entry_zone_high,
        entry_zone_status,
    )

    reason = pd.Series("", index=out.index, dtype="object")
    reason = _append_reason(reason, friend_combo_match, "friend_combo_backtest_ref:" + FRIEND_COMBO_BACKTEST_REF)
    reason = _append_reason(reason, friend_combo_developing, "friend_combo_developing")
    reason = _append_reason(reason, price_vs_ma5.ge(0).fillna(False) & price_vs_ma20.ge(0).fillna(False), "price_above_ma5_ma20")
    reason = _append_reason(reason, price_vs_ma60.ge(-0.03).fillna(False), "ma60_structure_intact")
    reason = _append_reason(reason, vol_ratio.between(0.8, 2.8).fillna(False), "volume_confirmed")
    reason = _append_reason(reason, macd_hist.gt(0).fillna(False), "macd_positive")
    reason = _append_reason(reason, rsi14.between(45, 72).fillna(False), "rsi_healthy")
    reason = _append_reason(reason, chipk_score.ge(0.70).fillna(False), "chipk_main_force_support")

    risk = pd.Series("", index=out.index, dtype="object")
    risk = _append_reason(risk, price_vs_ma20.gt(0.18).fillna(False), "ma20_overheat_no_chase")
    risk = _append_reason(risk, vol_ratio.gt(3.5).fillna(False), "volume_spike_chase_risk")
    risk = _append_reason(risk, macd_bear.gt(0).fillna(False), "macd_bearish_divergence")
    risk = _append_reason(risk, rsi14.gt(82).fillna(False), "rsi_overheated")
    risk = _append_reason(risk, chipk_score.le(0.25).fillna(False), "chipk_main_force_weak")
    risk = _append_reason(risk, chipk_alignment.eq("stale_before_prediction"), "chipk_snapshot_stale")
    risk = _append_reason(risk, ~chipk_history, "chipk_snapshot_not_historical_backtested")

    invalid_values = [
        f"below_ma_support:{min([v for v in [m5, m20] if pd.notna(v)]):.2f}"
        if any(pd.notna(v) for v in [m5, m20])
        else ""
        for m5, m20 in zip(ma5.tolist(), ma20.tolist())
    ]
    invalid = pd.Series(invalid_values, index=out.index, dtype="object")

    out["shortwave_version"] = SHORTWAVE_VERSION
    out["shortwave_data_status"] = "ok"
    out["shortwave_ma_score"] = ma_score.round(6)
    out["shortwave_volume_score"] = volume_score.round(6)
    out["shortwave_momentum_score"] = momentum_score.round(6)
    out["shortwave_chipk_score"] = chipk_score.round(6)
    out["shortwave_score_pre_chipk"] = score_pre_chipk.round(6)
    out["shortwave_score"] = score.round(6)
    out["shortwave_action"] = action
    out["shortwave_reason"] = reason.str.strip()
    out["shortwave_risk_flags"] = risk.str.strip()
    out["shortwave_invalid_level"] = invalid
    out["shortwave_strategy_tags"] = strategy_tags.str.strip()
    out["shortwave_missing_strategy_tags"] = missing_strategy_tags
    out["shortwave_entry_strategy_advice"] = entry_strategy_advice
    out["shortwave_entry_zone_low"] = entry_zone_low.round(4)
    out["shortwave_entry_zone_high"] = entry_zone_high.round(4)
    out["shortwave_entry_zone_status"] = entry_zone_status
    out["shortwave_entry_zone_note"] = entry_zone_note
    out["shortwave_preferred_hold_days"] = preferred_hold_days
    out["shortwave_friend_combo_score"] = friend_combo_score.round(6)
    out["shortwave_friend_combo_match"] = friend_combo_match.astype(np.int8)
    out["shortwave_model_combo_support"] = model_support_count.astype(np.int8)
    out["shortwave_watch_note"] = _build_watch_note(
        action,
        friend_combo_match,
        friend_combo_developing,
        preferred_hold_days,
        strategy_tags,
        model_support_count,
    )
    out["shortwave_chipk_used_in_production"] = use_chipk.astype(np.int8)
    out["swing_strategy_name"] = "20D/60D model + ChipK swing"
    out["swing_action"] = action
    out["swing_score"] = score.round(6)
    out["swing_entry_zone_low"] = entry_zone_low.round(4)
    out["swing_entry_zone_high"] = entry_zone_high.round(4)
    out["swing_entry_zone_status"] = entry_zone_status
    out["swing_entry_strategy_advice"] = entry_strategy_advice
    out["swing_preferred_hold_days"] = preferred_hold_days
    out["swing_reason"] = reason.str.strip()
    out["swing_missing_strategy_tags"] = missing_strategy_tags

    tactical_payload = _build_tactical_bounce_payload(
        close_price=close_price,
        low_price=low_price,
        volume_today=liquidity_volume_today,
        vol_ratio=vol_ratio,
        ma5=ma5,
        ma20=ma20,
        ma60=ma60,
        ret_1d=ret_1d,
        macd_hist=macd_hist,
        macd_hist_delta=macd_hist_delta,
        rsi14=rsi14,
        kd_k=kd_k,
        kd_d=kd_d,
    )
    for column, values in tactical_payload.items():
        out[column] = values

    momentum_payload = _build_momentum_continuation_payload(
        close_price=close_price,
        volume_today=liquidity_volume_today,
        vol_ratio=vol_ratio,
        ma5=ma5,
        ma20=ma20,
        ma60=ma60,
        ret_1d=ret_1d,
        ret_5d=ret_5d,
        macd_hist=macd_hist,
        macd_hist_delta=macd_hist_delta,
        rsi14=rsi14,
        kd_k=kd_k,
        kd_d=kd_d,
        beta_60=beta_60,
        is_special_status=is_special_status,
        macro_ok=macro_ok,
    )
    for column, values in momentum_payload.items():
        out[column] = values

    enabled = bool(shortwave_prod_rerank_enabled() if rerank_enabled is None else rerank_enabled)
    if "leaderboard_score" in out.columns:
        out["leaderboard_score_before_shortwave"] = out["leaderboard_score"]
        prod_scope = enabled & is_buy
        if prod_scope.any():
            base = pd.to_numeric(out["leaderboard_score"], errors="coerce").fillna(0.0)
            multiplier = pd.Series(0.82, index=out.index, dtype="float64")
            multiplier.loc[friend_combo_developing] = (
                0.95 + out.loc[friend_combo_developing, "shortwave_score"].astype(float) * 0.08
            )
            multiplier.loc[friend_combo_match] = (
                1.05 + out.loc[friend_combo_match, "shortwave_score"].astype(float) * 0.20
            )
            multiplier.loc[action.eq("NO_CHASE")] = 0.90
            multiplier = multiplier.clip(0.75, 1.25)
            out.loc[prod_scope, "leaderboard_score"] = base.loc[prod_scope] * multiplier.loc[prod_scope]
    else:
        out["leaderboard_score_before_shortwave"] = np.nan
    out["shortwave_rerank_enabled"] = bool(enabled)
    return out


def summarize_shortwave_distribution(df: pd.DataFrame) -> dict[str, Any]:
    if df.empty or "shortwave_action" not in df.columns:
        return {"rows": int(len(df)), "actions": {}}
    return {
        "rows": int(len(df)),
        "actions": {str(k): int(v) for k, v in df["shortwave_action"].value_counts().items()},
        "avg_score": float(pd.to_numeric(df["shortwave_score"], errors="coerce").mean()),
    }
