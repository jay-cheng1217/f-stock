"""Research exit policies for unified execution replay.

The production ledger keeps baseline 20D exits as the default. These policy
objects are intentionally small and stateful so replay can add early exits
without hard-coding every experiment into the ledger loop.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

ENTRY_TAG_NARRATIVE_DRIVEN = "NARRATIVE_DRIVEN"
ENTRY_TAG_FUNDAMENTAL_DRIVEN = "FUNDAMENTAL_DRIVEN"

NARRATIVE_ENTRY_HEAT_THRESHOLD = 0.30
NARRATIVE_DECAY_SLOPE_THRESHOLD = -0.30
NARRATIVE_DECAY_HEAT_THRESHOLD = 0.20
NARRATIVE_DECAY_MIN_HOLD_DAYS = 5

EXIT_POLICY_BASELINE_20D = "baseline_20d"
EXIT_POLICY_BASELINE_WITH_MA20 = "baseline_with_ma20"
EXIT_POLICY_TRAILING_STOP_HWM_8PCT = "trailing_stop_hwm_8pct"
EXIT_POLICY_MA20_PLUS_HWM_8PCT = "ma20_plus_hwm_8pct"
EXIT_POLICY_MA20_PLUS_HWM_10PCT = "ma20_plus_hwm_10pct"
EXIT_POLICY_MA20_PLUS_HWM_12PCT = "ma20_plus_hwm_12pct"
EXIT_POLICY_MA10_BREAK_PLUS_HWM_8PCT = "ma10_break_plus_hwm_8pct"
EXIT_POLICY_MA5_BREAK_PLUS_HWM_8PCT = "ma5_break_plus_hwm_8pct"
EXIT_POLICY_STOP_ONLY_20D = "stop_only_20d"
EXIT_POLICY_ASYMMETRIC_V2 = "asymmetric_v2"
EXIT_POLICY_PURE_TIME_20D_ONLY = "pure_time_20d_only"
EXIT_POLICY_PURE_HWM_8PCT = "pure_hwm_8pct"
EXIT_POLICY_PURE_HWM_10PCT = "pure_hwm_10pct"
EXIT_POLICY_PURE_HWM_12PCT = "pure_hwm_12pct"
EXIT_POLICY_PURE_MA10_BREAK = "pure_ma10_break"
EXIT_POLICY_PURE_MA5_BREAK = "pure_ma5_break"

EXIT_REASON_TRAILING_STOP_HWM_8PCT = "TRAILING_STOP_HWM_8PCT"
EXIT_REASON_TRAILING_STOP_HWM_10PCT = "TRAILING_STOP_HWM_10PCT"
EXIT_REASON_TRAILING_STOP_HWM_12PCT = "TRAILING_STOP_HWM_12PCT"
EXIT_REASON_MA10_BREAK = "MA10_BREAK"
EXIT_REASON_MA5_BREAK = "MA5_BREAK"
EXIT_REASON_NARRATIVE_DECAY = "NARRATIVE_DECAY"

ASYMMETRIC_V2_PRICE_VS_MA20_THRESHOLD = 0.05
ASYMMETRIC_V2_RANK_THRESHOLD = 10
ASYMMETRIC_V2_ENTRY_MA5_STOP_ONLY_THRESHOLD = 0.0
ASYMMETRIC_V2_ENTRY_MA5_GROUP_B_LOWER_THRESHOLD = -0.05

REGIME_EXIT_VARIANT_ENV = "REGIME_EXIT_VARIANT"
REGIME_EXIT_VARIANT_NONE = "none"
REGIME_EXIT_VARIANT_MA20_3PCT = "regime_ma20_3pct"
REGIME_EXIT_VARIANT_MA20_5PCT = "regime_ma20_5pct"
REGIME_EXIT_VARIANT_20D_RETURN_8PCT = "regime_20d_return_8pct"
REGIME_EXIT_VARIANT_MA20_5PCT_HWM = "regime_ma20_5pct_hwm"
REGIME_EXIT_VARIANTS = {
    REGIME_EXIT_VARIANT_NONE,
    REGIME_EXIT_VARIANT_MA20_3PCT,
    REGIME_EXIT_VARIANT_MA20_5PCT,
    REGIME_EXIT_VARIANT_20D_RETURN_8PCT,
    REGIME_EXIT_VARIANT_MA20_5PCT_HWM,
}
REGIME_TWII_MA20_3PCT_THRESHOLD = 0.03
REGIME_TWII_MA20_5PCT_THRESHOLD = 0.05
REGIME_TWII_20D_RETURN_8PCT_THRESHOLD = 0.08

MA_BREAK_EXIT_ENABLED_ENV = "EXIT_POLICY_MA_BREAK_ENABLED"
DEFAULT_MA_BREAK_EXITS_ENABLED = True


def ma_break_exits_enabled() -> bool:
    """Return whether legacy MA-break exits are enabled.

    MA5/MA10 exits are enabled by default after the StopOnly CL3 rollback.
    Set EXIT_POLICY_MA_BREAK_ENABLED=0 to disable them for research/replay.
    """

    value = os.environ.get(MA_BREAK_EXIT_ENABLED_ENV)
    if value is None:
        return DEFAULT_MA_BREAK_EXITS_ENABLED
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _numeric_or_none(value: Any) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(numeric):
        return None
    return numeric


def _has_active_narrative_labels(value: Any) -> bool:
    if value is None or pd.isna(value):
        return False
    text = str(value).strip()
    return text not in {"", "[]", "nan", "None", "null"}


def classify_entry_tag(active_labels: Any, heat_3d: Any) -> tuple[str, float | None]:
    """Classify an entry snapshot into narrative/fundamental exit routing."""

    heat = _numeric_or_none(heat_3d)
    if _has_active_narrative_labels(active_labels) and heat is not None and heat > NARRATIVE_ENTRY_HEAT_THRESHOLD:
        return ENTRY_TAG_NARRATIVE_DRIVEN, heat
    return ENTRY_TAG_FUNDAMENTAL_DRIVEN, heat


def is_narrative_decay_exit(entry_tag: str | None, heat_3d: Any, decay_slope: Any, hold_days: int) -> bool:
    """Return true when a narrative-driven position should exit after close."""

    if entry_tag != ENTRY_TAG_NARRATIVE_DRIVEN:
        return False
    if int(hold_days) < NARRATIVE_DECAY_MIN_HOLD_DAYS:
        return False
    heat = _numeric_or_none(heat_3d)
    slope = _numeric_or_none(decay_slope)
    if heat is None or slope is None:
        return False
    return slope < NARRATIVE_DECAY_SLOPE_THRESHOLD and heat < NARRATIVE_DECAY_HEAT_THRESHOLD


def is_asymmetric_v2_strong_trend(two_stage_rank: Any, price_vs_ma20: Any) -> bool:
    """Return true when asymmetric_v2 should disable MA-break exits."""

    rank = _numeric_or_none(two_stage_rank)
    price_gap = _numeric_or_none(price_vs_ma20)
    if rank is None or price_gap is None:
        return False
    return rank <= ASYMMETRIC_V2_RANK_THRESHOLD and price_gap > ASYMMETRIC_V2_PRICE_VS_MA20_THRESHOLD


def is_asymmetric_v2_below_ma5_stop_only(price_vs_ma5: Any) -> bool:
    """Return true when asymmetric_v2 research split should route to stop_only_20d."""

    price_gap = _numeric_or_none(price_vs_ma5)
    if price_gap is None:
        return False
    return price_gap < ASYMMETRIC_V2_ENTRY_MA5_STOP_ONLY_THRESHOLD


def is_asymmetric_v2_group_b_stop_only(price_vs_ma5: Any) -> bool:
    """Return true for the REQ-019 micro-below-MA5 stop-only research route."""

    price_gap = _numeric_or_none(price_vs_ma5)
    if price_gap is None:
        return False
    return ASYMMETRIC_V2_ENTRY_MA5_GROUP_B_LOWER_THRESHOLD < price_gap < ASYMMETRIC_V2_ENTRY_MA5_STOP_ONLY_THRESHOLD


def resolve_regime_exit_variant(value: str | None = None) -> str:
    """Resolve a market-regime exit overlay variant.

    The production default is intentionally `none`; research scripts may pass a
    variant explicitly, and nightly jobs can opt in through REGIME_EXIT_VARIANT.
    """

    raw = os.environ.get(REGIME_EXIT_VARIANT_ENV) if value is None else value
    resolved = (raw or REGIME_EXIT_VARIANT_NONE).strip().lower()
    if resolved in {"", "0", "false", "off", "disabled"}:
        resolved = REGIME_EXIT_VARIANT_NONE
    if resolved not in REGIME_EXIT_VARIANTS:
        raise ValueError(
            f"Unsupported regime exit variant: {value}. "
            f"Expected one of: {', '.join(sorted(REGIME_EXIT_VARIANTS))}"
        )
    return resolved


def is_regime_exit_overlay_active(
    variant: str | None,
    twii_price_vs_ma20: Any,
    twii_return_20d: Any,
) -> bool:
    """Return whether a market-regime overlay should override MA5 exits."""

    resolved = resolve_regime_exit_variant(variant)
    price_gap = _numeric_or_none(twii_price_vs_ma20)
    ret_20d = _numeric_or_none(twii_return_20d)
    if resolved == REGIME_EXIT_VARIANT_MA20_3PCT:
        return price_gap is not None and price_gap > REGIME_TWII_MA20_3PCT_THRESHOLD
    if resolved in {REGIME_EXIT_VARIANT_MA20_5PCT, REGIME_EXIT_VARIANT_MA20_5PCT_HWM}:
        return price_gap is not None and price_gap > REGIME_TWII_MA20_5PCT_THRESHOLD
    if resolved == REGIME_EXIT_VARIANT_20D_RETURN_8PCT:
        return ret_20d is not None and ret_20d > REGIME_TWII_20D_RETURN_8PCT_THRESHOLD
    return False


def resolve_regime_exit_route(
    variant: str | None,
    *,
    base_strong_trend_no_ma5: bool,
    twii_price_vs_ma20: Any,
    twii_return_20d: Any,
) -> tuple[str, bool, str]:
    """Resolve exit policy, MA-break override, and reason for a regime variant.

    Variants A/B/C route active regime positions to pure stop-only 20D exits.
    Variant D keeps the asymmetric_v2 HWM protection but disables MA5_BREAK.
    Inactive regimes fall back to the existing asymmetric_v2 route.
    """

    resolved = resolve_regime_exit_variant(variant)
    active = is_regime_exit_overlay_active(resolved, twii_price_vs_ma20, twii_return_20d)
    if active and resolved in {
        REGIME_EXIT_VARIANT_MA20_3PCT,
        REGIME_EXIT_VARIANT_MA20_5PCT,
        REGIME_EXIT_VARIANT_20D_RETURN_8PCT,
    }:
        return EXIT_POLICY_STOP_ONLY_20D, False, resolved.upper()
    if active and resolved == REGIME_EXIT_VARIANT_MA20_5PCT_HWM:
        return EXIT_POLICY_ASYMMETRIC_V2, True, resolved.upper()
    reason = "BASE_STRONG_TREND_NO_MA5" if base_strong_trend_no_ma5 else "BASE_ASYMMETRIC_V2"
    return EXIT_POLICY_ASYMMETRIC_V2, bool(base_strong_trend_no_ma5), reason


@dataclass(frozen=True)
class ExitPolicyConfig:
    name: str
    enable_ma20_take_profit: bool
    trailing_stop_pct: float | None = None
    trailing_exit_reason: str | None = None
    ma_break_window: int | None = None
    ma_break_exit_reason: str | None = None


EXIT_POLICY_CONFIGS = {
    EXIT_POLICY_BASELINE_20D: ExitPolicyConfig(
        name=EXIT_POLICY_BASELINE_20D,
        enable_ma20_take_profit=True,
    ),
    EXIT_POLICY_BASELINE_WITH_MA20: ExitPolicyConfig(
        name=EXIT_POLICY_BASELINE_WITH_MA20,
        enable_ma20_take_profit=True,
    ),
    EXIT_POLICY_TRAILING_STOP_HWM_8PCT: ExitPolicyConfig(
        name=EXIT_POLICY_TRAILING_STOP_HWM_8PCT,
        enable_ma20_take_profit=True,
        trailing_stop_pct=0.08,
        trailing_exit_reason=EXIT_REASON_TRAILING_STOP_HWM_8PCT,
    ),
    EXIT_POLICY_MA20_PLUS_HWM_8PCT: ExitPolicyConfig(
        name=EXIT_POLICY_MA20_PLUS_HWM_8PCT,
        enable_ma20_take_profit=True,
        trailing_stop_pct=0.08,
        trailing_exit_reason=EXIT_REASON_TRAILING_STOP_HWM_8PCT,
    ),
    EXIT_POLICY_MA20_PLUS_HWM_10PCT: ExitPolicyConfig(
        name=EXIT_POLICY_MA20_PLUS_HWM_10PCT,
        enable_ma20_take_profit=True,
        trailing_stop_pct=0.10,
        trailing_exit_reason=EXIT_REASON_TRAILING_STOP_HWM_10PCT,
    ),
    EXIT_POLICY_MA20_PLUS_HWM_12PCT: ExitPolicyConfig(
        name=EXIT_POLICY_MA20_PLUS_HWM_12PCT,
        enable_ma20_take_profit=True,
        trailing_stop_pct=0.12,
        trailing_exit_reason=EXIT_REASON_TRAILING_STOP_HWM_12PCT,
    ),
    EXIT_POLICY_MA10_BREAK_PLUS_HWM_8PCT: ExitPolicyConfig(
        name=EXIT_POLICY_MA10_BREAK_PLUS_HWM_8PCT,
        enable_ma20_take_profit=False,
        trailing_stop_pct=0.08,
        trailing_exit_reason=EXIT_REASON_TRAILING_STOP_HWM_8PCT,
        ma_break_window=10,
        ma_break_exit_reason=EXIT_REASON_MA10_BREAK,
    ),
    EXIT_POLICY_MA5_BREAK_PLUS_HWM_8PCT: ExitPolicyConfig(
        name=EXIT_POLICY_MA5_BREAK_PLUS_HWM_8PCT,
        enable_ma20_take_profit=False,
        trailing_stop_pct=0.08,
        trailing_exit_reason=EXIT_REASON_TRAILING_STOP_HWM_8PCT,
        ma_break_window=5,
        ma_break_exit_reason=EXIT_REASON_MA5_BREAK,
    ),
    EXIT_POLICY_STOP_ONLY_20D: ExitPolicyConfig(
        name=EXIT_POLICY_STOP_ONLY_20D,
        enable_ma20_take_profit=False,
    ),
    EXIT_POLICY_ASYMMETRIC_V2: ExitPolicyConfig(
        name=EXIT_POLICY_ASYMMETRIC_V2,
        enable_ma20_take_profit=False,
        trailing_stop_pct=0.08,
        trailing_exit_reason=EXIT_REASON_TRAILING_STOP_HWM_8PCT,
        ma_break_window=5,
        ma_break_exit_reason=EXIT_REASON_MA5_BREAK,
    ),
    EXIT_POLICY_PURE_TIME_20D_ONLY: ExitPolicyConfig(
        name=EXIT_POLICY_PURE_TIME_20D_ONLY,
        enable_ma20_take_profit=False,
    ),
    EXIT_POLICY_PURE_HWM_8PCT: ExitPolicyConfig(
        name=EXIT_POLICY_PURE_HWM_8PCT,
        enable_ma20_take_profit=False,
        trailing_stop_pct=0.08,
        trailing_exit_reason=EXIT_REASON_TRAILING_STOP_HWM_8PCT,
    ),
    EXIT_POLICY_PURE_HWM_10PCT: ExitPolicyConfig(
        name=EXIT_POLICY_PURE_HWM_10PCT,
        enable_ma20_take_profit=False,
        trailing_stop_pct=0.10,
        trailing_exit_reason=EXIT_REASON_TRAILING_STOP_HWM_10PCT,
    ),
    EXIT_POLICY_PURE_HWM_12PCT: ExitPolicyConfig(
        name=EXIT_POLICY_PURE_HWM_12PCT,
        enable_ma20_take_profit=False,
        trailing_stop_pct=0.12,
        trailing_exit_reason=EXIT_REASON_TRAILING_STOP_HWM_12PCT,
    ),
    EXIT_POLICY_PURE_MA10_BREAK: ExitPolicyConfig(
        name=EXIT_POLICY_PURE_MA10_BREAK,
        enable_ma20_take_profit=False,
        ma_break_window=10,
        ma_break_exit_reason=EXIT_REASON_MA10_BREAK,
    ),
    EXIT_POLICY_PURE_MA5_BREAK: ExitPolicyConfig(
        name=EXIT_POLICY_PURE_MA5_BREAK,
        enable_ma20_take_profit=False,
        ma_break_window=5,
        ma_break_exit_reason=EXIT_REASON_MA5_BREAK,
    ),
}
SUPPORTED_EXIT_POLICIES = set(EXIT_POLICY_CONFIGS)


@dataclass(frozen=True)
class ExitDecision:
    """A policy-requested exit that executes at the current day's open."""

    exit_price: float
    exit_reason: str
    trigger_date: str | None = None
    trigger_close: float | None = None
    high_water_close: float | None = None
    drawdown_from_high_water_pct: float | None = None


@dataclass(frozen=True)
class PendingOpenExit:
    """Exit signal generated after a close and filled on the next open."""

    exit_reason: str
    trigger_date: str
    trigger_close: float
    high_water_close: float
    drawdown_from_high_water_pct: float


def resolve_exit_policy_name(policy_name: str | None) -> str:
    resolved = (policy_name or EXIT_POLICY_BASELINE_20D).strip().lower()
    if resolved not in EXIT_POLICY_CONFIGS:
        raise ValueError(
            f"Unsupported exit policy: {policy_name}. "
            f"Expected one of: {', '.join(sorted(SUPPORTED_EXIT_POLICIES))}"
        )
    return resolved


def get_exit_policy_config(policy_name: str | None) -> ExitPolicyConfig:
    return EXIT_POLICY_CONFIGS[resolve_exit_policy_name(policy_name)]


def _positive_float(value: Any) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(numeric) or numeric <= 0:
        return None
    return numeric


def _row_date(row: pd.Series) -> str | None:
    value = row.get("Date")
    if pd.isna(value):
        return None
    return pd.Timestamp(value).date().isoformat()


class ExitManager:
    """Stateful exit policy evaluator for one position replay."""

    def __init__(
        self,
        policy_name: str | None,
        entry_price: float,
        enable_ma_break_exits: bool | None = None,
        disable_ma_break_for_position: bool = False,
    ) -> None:
        self.config = get_exit_policy_config(policy_name)
        self.policy_name = self.config.name
        self.entry_price = float(entry_price)
        base_enable_ma_break_exits = (
            ma_break_exits_enabled() if enable_ma_break_exits is None else bool(enable_ma_break_exits)
        )
        self.enable_ma_break_exits = base_enable_ma_break_exits and not bool(disable_ma_break_for_position)
        self.high_water_close = self.entry_price
        self.pending_open_exit: PendingOpenExit | None = None

    @property
    def is_baseline(self) -> bool:
        return self.policy_name == EXIT_POLICY_BASELINE_20D

    @property
    def enable_ma20_take_profit(self) -> bool:
        return self.config.enable_ma20_take_profit

    def before_open(self, mark_row: pd.Series) -> ExitDecision | None:
        """Return a pending open-fill exit before same-day stop checks."""

        if self.pending_open_exit is None:
            return None

        open_price = _positive_float(mark_row.get("Open"))
        if open_price is None:
            return None

        pending = self.pending_open_exit
        self.pending_open_exit = None
        return ExitDecision(
            exit_price=open_price,
            exit_reason=pending.exit_reason,
            trigger_date=pending.trigger_date,
            trigger_close=pending.trigger_close,
            high_water_close=pending.high_water_close,
            drawdown_from_high_water_pct=pending.drawdown_from_high_water_pct,
        )

    def after_close(self, mark_row: pd.Series) -> None:
        """Observe the close and queue a next-open exit when policy triggers."""

        close_price = _positive_float(mark_row.get("Close"))
        if close_price is None:
            return

        if self.enable_ma_break_exits and self.config.ma_break_window is not None:
            ma_value = _positive_float(mark_row.get(f"MA{self.config.ma_break_window}"))
            if ma_value is not None and close_price < ma_value:
                self.pending_open_exit = PendingOpenExit(
                    exit_reason=self.config.ma_break_exit_reason or f"MA{self.config.ma_break_window}_BREAK",
                    trigger_date=_row_date(mark_row) or "",
                    trigger_close=close_price,
                    high_water_close=self.high_water_close,
                    drawdown_from_high_water_pct=close_price / self.high_water_close - 1.0
                    if self.high_water_close > 0
                    else 0.0,
                )
                return

        if self.config.trailing_stop_pct is None:
            return

        if close_price is None or self.high_water_close <= 0:
            return

        self.high_water_close = max(self.high_water_close, close_price)
        drawdown = close_price / self.high_water_close - 1.0
        threshold = self.config.trailing_stop_pct
        if drawdown <= -threshold:
            self.pending_open_exit = PendingOpenExit(
                exit_reason=self.config.trailing_exit_reason or EXIT_REASON_TRAILING_STOP_HWM_8PCT,
                trigger_date=_row_date(mark_row) or "",
                trigger_close=close_price,
                high_water_close=self.high_water_close,
                drawdown_from_high_water_pct=drawdown,
            )
