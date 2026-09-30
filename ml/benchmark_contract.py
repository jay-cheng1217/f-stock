"""Canonical benchmark contracts used by reports and replay artifacts."""

from __future__ import annotations

from typing import Any


TRADE_ALPHA_BENCHMARK_KEY = "twii_close_to_close"
TRADE_ALPHA_BENCHMARK_LABEL = "TWII close-to-close"

SELECTION_ALPHA_BENCHMARK_KEY = "equal_weight_universe_excess"
SELECTION_ALPHA_BENCHMARK_LABEL = "Equal-weight universe excess"


def build_trade_alpha_benchmark_contract(
    *,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict[str, Any]:
    contract: dict[str, Any] = {
        "kind": "trade_alpha",
        "benchmark_key": TRADE_ALPHA_BENCHMARK_KEY,
        "benchmark_label": TRADE_ALPHA_BENCHMARK_LABEL,
        "alpha_label": "Alpha vs TWII",
        "window_definition": "same-window close-to-close over replay mark range",
        "trade_aligned": True,
        "notes": [
            "Use this benchmark for unified replay, war room MTM, and paper-book alpha.",
            "The benchmark window follows the replay mark range, not the prediction date alone.",
        ],
    }
    if start_date:
        contract["start_date"] = start_date
    if end_date:
        contract["end_date"] = end_date
    return contract


def build_selection_alpha_benchmark_contract() -> dict[str, Any]:
    return {
        "kind": "selection_alpha",
        "benchmark_key": SELECTION_ALPHA_BENCHMARK_KEY,
        "benchmark_label": SELECTION_ALPHA_BENCHMARK_LABEL,
        "alpha_label": "Selection alpha vs equal-weight universe",
        "base_return_label": "Portfolio excess return vs TWII",
        "trade_aligned": True,
        "notes": [
            "Backtest fold returns are already excess returns versus TWII.",
            "The equal-weight universe benchmark measures stock-selection lift inside the same cross-section.",
        ],
    }
