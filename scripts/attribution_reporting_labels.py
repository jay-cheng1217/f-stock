"""Pure helpers for RD-006 attribution reporting labels."""

from __future__ import annotations

from collections.abc import Iterable
from copy import deepcopy
from typing import Any

LABEL_VERSION = "rd006_minimal_v1"
DEFAULT_C1_BASIS_LABEL = "nav_unexplained_target"
ALLOWED_C1_BASIS_LABELS = frozenset({"nav_unexplained_target", "observable_timing_proxy"})


def b4_dominance_flag(b4_value: float, observable_timing_sum: float) -> bool:
    """Return True only when B4 is strictly larger than observable timing."""

    return abs(float(b4_value)) > abs(float(observable_timing_sum))


def cross_window_sign_stability(b4_values: Iterable[float | int | None]) -> str:
    """Classify B4 sign stability across comparable windows.

    Missing values are ignored. Fewer than two comparable values returns the
    explicit insufficient-data code, not False.
    """

    comparable = [float(value) for value in b4_values if value is not None]
    if len(comparable) < 2:
        return "insufficient_windows"
    positives = [value for value in comparable if value > 0]
    negatives = [value for value in comparable if value < 0]
    zeros = [value for value in comparable if value == 0]
    if positives and negatives:
        return "sign_flip"
    if not zeros and (positives or negatives):
        return "same_sign"
    return "mixed_or_zero"


def reporting_definition_labels(
    *,
    b4_value: float,
    observable_timing_sum: float,
    c1_basis_label: str = DEFAULT_C1_BASIS_LABEL,
    comparable_b4_values: Iterable[float | int | None] | None = None,
) -> dict[str, Any]:
    """Build the additive RD-006 label object for attribution reports."""

    if c1_basis_label not in ALLOWED_C1_BASIS_LABELS:
        allowed = ", ".join(sorted(ALLOWED_C1_BASIS_LABELS))
        raise ValueError(f"Unsupported c1_basis_label={c1_basis_label!r}; allowed: {allowed}")
    stability_values = [b4_value] if comparable_b4_values is None else list(comparable_b4_values)
    return {
        "c1_basis_label": c1_basis_label,
        "b4_dominance_flag": b4_dominance_flag(b4_value, observable_timing_sum),
        "cross_window_sign_stability": cross_window_sign_stability(stability_values),
        "label_version": LABEL_VERSION,
    }


def append_reporting_definition_labels(
    payload: dict[str, Any],
    *,
    b4_value: float,
    observable_timing_sum: float,
    c1_basis_label: str = DEFAULT_C1_BASIS_LABEL,
    comparable_b4_values: Iterable[float | int | None] | None = None,
) -> dict[str, Any]:
    """Return a copy of payload with additive RD-006 labels attached."""

    labeled = deepcopy(payload)
    labeled["reporting_definition_labels"] = reporting_definition_labels(
        b4_value=b4_value,
        observable_timing_sum=observable_timing_sum,
        c1_basis_label=c1_basis_label,
        comparable_b4_values=comparable_b4_values,
    )
    return labeled
