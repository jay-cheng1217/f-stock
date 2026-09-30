"""Shared order-fill and intraday stop simulation rules."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

ORDER_TYPE_LIMIT_102 = "LIMIT_1.02"
ORDER_TYPE_OPEN = "OPEN"
ORDER_TYPE_INTRADAY_CHASE = "INTRADAY_CHASE"
ORDER_TYPE_HIGH_FALLBACK = "HIGH_FALLBACK"

FILL_STATUS_PENDING = "PENDING"
FILL_STATUS_FILLED = "FILLED"
FILL_STATUS_MISSED = "MISSED"

POSITION_STATUS_MISSED_ENTRY_GAP_UP = "missed_entry_gap_up"

EXIT_REASON_MISSED_ENTRY_GAP_UP = "MISSED_ENTRY_GAP_UP"
EXIT_REASON_GAP_STOP_LOSS = "GAP_STOP_LOSS"
EXIT_REASON_STOP_LOSS_SLIPPAGE = "STOP_LOSS_SLIPPAGE"
EXIT_REASON_TIME_T1 = "TIME_T1"
EXIT_REASON_TIME_20D = "TIME_20D"
EXIT_REASON_TAKE_PROFIT_MA20 = "TAKE_PROFIT_MA20"

T1_ENTRY_LIMIT_PCT = 0.02
TWENTY_D_OPEN_MAX_PREMIUM = 0.01
STOP_LOSS_PCT = 0.10
STOP_LOSS_SLIPPAGE_PCT = 0.005


@dataclass(frozen=True)
class EntryFill:
    order_type: str
    fill_status: str
    fill_price: float | None
    entry_slippage_pct: float | None
    limit_price: float | None = None
    missed_reason: str | None = None


@dataclass(frozen=True)
class StopExit:
    exit_price: float
    exit_reason: str
    stop_price: float


def _positive(value: object) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(numeric) or numeric <= 0:
        return None
    return numeric


def _slippage(fill_price: float | None, reference_price: float | None) -> float | None:
    if fill_price is None or reference_price is None or reference_price <= 0:
        return None
    return fill_price / reference_price - 1.0


def simulate_t1_limit_entry(
    reference_price: object,
    open_price: object,
    low_price: object,
) -> EntryFill:
    """Simulate a T+1 buy with a 2% limit over the signal-day close."""

    ref = _positive(reference_price)
    open_ = _positive(open_price)
    low = _positive(low_price)
    if ref is None or open_ is None or low is None:
        return EntryFill(
            order_type=ORDER_TYPE_LIMIT_102,
            fill_status=FILL_STATUS_MISSED,
            fill_price=None,
            entry_slippage_pct=None,
            limit_price=ref * (1.0 + T1_ENTRY_LIMIT_PCT) if ref else None,
            missed_reason=EXIT_REASON_MISSED_ENTRY_GAP_UP,
        )

    limit_price = ref * (1.0 + T1_ENTRY_LIMIT_PCT)
    if open_ <= limit_price:
        return EntryFill(
            order_type=ORDER_TYPE_LIMIT_102,
            fill_status=FILL_STATUS_FILLED,
            fill_price=open_,
            entry_slippage_pct=_slippage(open_, ref),
            limit_price=limit_price,
        )
    if low <= limit_price:
        return EntryFill(
            order_type=ORDER_TYPE_LIMIT_102,
            fill_status=FILL_STATUS_FILLED,
            fill_price=limit_price,
            entry_slippage_pct=_slippage(limit_price, ref),
            limit_price=limit_price,
        )

    return EntryFill(
        order_type=ORDER_TYPE_LIMIT_102,
        fill_status=FILL_STATUS_MISSED,
        fill_price=None,
        entry_slippage_pct=None,
        limit_price=limit_price,
        missed_reason=EXIT_REASON_MISSED_ENTRY_GAP_UP,
    )


def simulate_20d_entry(
    reference_price: object,
    open_price: object,
    high_price: object,
) -> EntryFill:
    """Simulate a 20D entry with open fill or conservative intraday chase."""

    ref = _positive(reference_price)
    open_ = _positive(open_price)
    high = _positive(high_price)
    if open_ is None:
        return EntryFill(
            order_type=ORDER_TYPE_OPEN,
            fill_status=FILL_STATUS_MISSED,
            fill_price=None,
            entry_slippage_pct=None,
        )

    if ref is not None and open_ <= ref * (1.0 + TWENTY_D_OPEN_MAX_PREMIUM):
        return EntryFill(
            order_type=ORDER_TYPE_OPEN,
            fill_status=FILL_STATUS_FILLED,
            fill_price=open_,
            entry_slippage_pct=_slippage(open_, ref),
        )

    if high is None:
        return EntryFill(
            order_type=ORDER_TYPE_OPEN,
            fill_status=FILL_STATUS_FILLED,
            fill_price=open_,
            entry_slippage_pct=_slippage(open_, ref),
        )

    if high < open_:
        high = open_
    fill_price = (open_ + high) / 2.0
    order_type = ORDER_TYPE_INTRADAY_CHASE if ref is not None else ORDER_TYPE_HIGH_FALLBACK
    return EntryFill(
        order_type=order_type,
        fill_status=FILL_STATUS_FILLED,
        fill_price=fill_price,
        entry_slippage_pct=_slippage(fill_price, ref),
    )


def simulate_intraday_stop(
    entry_price: object,
    open_price: object,
    low_price: object,
    dividend_adjust: float = 0.0,
) -> StopExit | None:
    """Simulate a 10% intraday stop with gap and slippage handling.

    dividend_adjust: 進場後累計權值+息值(元/股)。除權息使價格機械性下移,
    停損價需同步下移,否則除息缺口會被誤判為跌破 -10%(2026-07-15 除息還原修正)。
    """

    entry = _positive(entry_price)
    open_ = _positive(open_price)
    low = _positive(low_price)
    if entry is None:
        return None

    stop_price = entry * (1.0 - STOP_LOSS_PCT) - max(float(dividend_adjust or 0.0), 0.0)
    if open_ is not None and open_ <= stop_price:
        return StopExit(
            exit_price=open_,
            exit_reason=EXIT_REASON_GAP_STOP_LOSS,
            stop_price=stop_price,
        )
    if low is not None and low <= stop_price:
        return StopExit(
            exit_price=stop_price * (1.0 - STOP_LOSS_SLIPPAGE_PCT),
            exit_reason=EXIT_REASON_STOP_LOSS_SLIPPAGE,
            stop_price=stop_price,
        )
    return None
