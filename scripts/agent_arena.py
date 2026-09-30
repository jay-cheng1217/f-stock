"""Multi-agent paper investing arena for Taiwan stock strategies.

The arena is intentionally additive: it does not change the production entry
gate, model files, schedulers, or Champion paper portfolio.  Each agent must
clear a no-lookahead backtest before it is admitted to the daily paper contest.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import sqlite3
import sys
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd

BASE_DIR = Path(os.environ.get("STOCK_BASE_DIR") or Path(__file__).resolve().parents[1])
sys.path.insert(0, str(BASE_DIR))

from ml.config import REPORT_DIR  # noqa: E402
from ml.features.tdcc import TDCC_FEATURE_COLS, compute_tdcc_features  # noqa: E402

STARTING_CAPITAL = 300_000.0
TARGET_CAPITAL = 10_000_000.0
BOARD_LOT_SIZE = 1000
MIN_TRADE_NOTIONAL = 1_000.0
BUY_COST = 0.002
SELL_COST = 0.002
ROUND_TRIP_COST = BUY_COST + SELL_COST
OPEN_FILL_SLIPPAGE_PCT = 0.05
MAX_BUY_OPEN_GAP_PCT = 2.0
MIN_BUY_INTRADAY_RETURN_PCT = 0.0
MIN_BUY_CLOSE_VS_SIGNAL_PCT = 0.0
DEFAULT_BACKTEST_START = "2024-01-01"
DEFAULT_DB_PATH = BASE_DIR / "agent_arena.db"
DEFAULT_DUCKDB_PATH = BASE_DIR / "stock.duckdb"
LATEST_JSON_PATH = Path(REPORT_DIR) / "agent_arena_latest.json"
BACKTEST_JSON_PATH = Path(REPORT_DIR) / "agent_arena_backtest_latest.json"
SECTOR_MAPPING_PATH = BASE_DIR / "ml" / "data" / "sector_mapping.csv"
TDCC_SUMMARY_PATH = BASE_DIR / "集保分散" / "tdcc_summary.csv"

SIZING_POLICY_BY_AGENT: dict[str, str] = {
    "tquant_vam_momentum": "conviction_momentum",
    "dual_ma_trend": "trend_core",
    "institutional_flow": "flow_conviction",
    "low_vol_quality_proxy": "volatility_budget",
    "volume_breakout": "breakout_conviction",
    "risk_parity_momentum": "inverse_volatility",
    "ml_edge_proxy": "ml_confidence",
    "mean_reversion_rsi": "rebound_probe",
    "vam_fast_5d": "fast_swing_scaled",
    "vam_slow_20d": "trend_core",
    "ma_fast_trend": "trend_core",
    "ma_slow_trend": "trend_core",
    "macd_acceleration": "fast_swing_scaled",
    "foreign_flow_rider": "flow_conviction",
    "trust_flow_rider": "flow_conviction",
    "dealer_flow_swing": "fast_flow_probe",
    "volume_surge_swing": "breakout_conviction",
    "quiet_breakout": "low_vol_breakout",
    "high_beta_momentum": "high_beta_capped",
    "low_beta_momentum": "volatility_budget",
    "gap_safe_momentum": "gap_risk_capped",
    "atr_breakout": "atr_breakout_budget",
    "bb_upper_trend": "breakout_conviction",
    "rsi_strength": "oscillator_strength",
    "kd_strength": "oscillator_strength",
    "defensive_flow": "defensive_flow",
    "short_swing_momentum": "fast_swing_scaled",
    "long_compounder": "compounder_core",
    "contrarian_reversal": "rebound_probe",
    "consensus_pullback_addon": "meta_consensus_scaled",
}

MOMENTUM_EXECUTION_AGENT_IDS = {
    "tquant_vam_momentum",
    "dual_ma_trend",
    "institutional_flow",
    "volume_breakout",
    "ml_edge_proxy",
    "vam_fast_5d",
    "vam_slow_20d",
    "ma_fast_trend",
    "ma_slow_trend",
    "macd_acceleration",
    "foreign_flow_rider",
    "trust_flow_rider",
    "dealer_flow_swing",
    "volume_surge_swing",
    "quiet_breakout",
    "high_beta_momentum",
    "atr_breakout",
    "bb_upper_trend",
    "rsi_strength",
    "kd_strength",
    "short_swing_momentum",
    "long_compounder",
}

EXECUTION_POLICY_BY_AGENT: dict[str, str] = {
    **{agent_id: "momentum_relaxed_open" for agent_id in MOMENTUM_EXECUTION_AGENT_IDS},
    "consensus_pullback_addon": "consensus_relaxed_open",
}

EXECUTION_POLICY_CONFIGS: dict[str, dict[str, float | bool]] = {
    "conservative_open_confirm": {
        "max_buy_open_gap_pct": MAX_BUY_OPEN_GAP_PCT,
        "min_buy_intraday_return_pct": MIN_BUY_INTRADAY_RETURN_PCT,
        "min_buy_close_vs_signal_pct": MIN_BUY_CLOSE_VS_SIGNAL_PCT,
        "allow_partial_open_fade": False,
        "open_fade_notional_multiplier": 0.0,
    },
    "momentum_relaxed_open": {
        "max_buy_open_gap_pct": 3.5,
        "min_buy_intraday_return_pct": MIN_BUY_INTRADAY_RETURN_PCT,
        "min_buy_close_vs_signal_pct": MIN_BUY_CLOSE_VS_SIGNAL_PCT,
        "allow_partial_open_fade": True,
        "open_fade_notional_multiplier": 0.50,
    },
    "consensus_relaxed_open": {
        "max_buy_open_gap_pct": 3.5,
        "min_buy_intraday_return_pct": MIN_BUY_INTRADAY_RETURN_PCT,
        "min_buy_close_vs_signal_pct": MIN_BUY_CLOSE_VS_SIGNAL_PCT,
        "allow_partial_open_fade": True,
        "open_fade_notional_multiplier": 0.50,
    },
}

EXECUTION_POLICY_LABELS: dict[str, str] = {
    "conservative_open_confirm": "Conservative execution: 2.0pp open-gap cap and full follow-through confirmation before entry.",
    "momentum_relaxed_open": "Momentum execution: 3.5pp open-gap cap; open-fade days can continue at 50% size if close stays above signal.",
    "consensus_relaxed_open": "Consensus execution: 3.5pp open-gap cap; cross-agent consensus can enter 50% size on open-fade days when close stays above signal.",
}

SIZING_POLICY_LABELS: dict[str, str] = {
    "conviction_momentum": "Momentum conviction sizing: stronger rank/20D momentum can receive a larger slot.",
    "trend_core": "Trend core sizing: stable MA alignment gets core exposure, weak MA structure is clipped.",
    "flow_conviction": "Flow conviction sizing: institutional accumulation increases size, flow reversal clips it.",
    "volatility_budget": "Volatility budget sizing: lower realized volatility receives more capital.",
    "inverse_volatility": "Inverse volatility sizing: risk-parity style capital budget.",
    "breakout_conviction": "Breakout conviction sizing: high-proximity and volume confirmation can expand size.",
    "ml_confidence": "ML-confidence sizing: higher interpretable model score gets more capital within caps.",
    "rebound_probe": "Rebound probe sizing: mean-reversion ideas start smaller until confirmed.",
    "fast_swing_scaled": "Fast swing sizing: short-horizon signals are capped and scaled by momentum.",
    "fast_flow_probe": "Fast flow probe sizing: short dealer-flow ideas start as smaller probes.",
    "low_vol_breakout": "Low-vol breakout sizing: breakout exposure is reduced when volatility rises.",
    "high_beta_capped": "High beta capped sizing: aggressive names are intentionally smaller per trade.",
    "gap_risk_capped": "Gap-risk capped sizing: volume-spike and gap-sensitive names are clipped.",
    "atr_breakout_budget": "ATR breakout sizing: allocation is normalized by ATR-style risk.",
    "oscillator_strength": "Oscillator strength sizing: RSI/KD strength scales within a medium cap.",
    "defensive_flow": "Defensive flow sizing: low-vol flow names get steadier capital.",
    "compounder_core": "Compounder core sizing: longer-hold compounders may receive a larger core slot.",
    "meta_consensus_scaled": "Meta-consensus sizing: larger slots require stronger cross-agent agreement and intact risk controls.",
    "equal_slot": "Equal slot sizing fallback.",
}

SIZING_POLICY_CAPS: dict[str, float] = {
    "compounder_core": 0.20,
    "trend_core": 0.18,
    "conviction_momentum": 0.16,
    "ml_confidence": 0.16,
    "flow_conviction": 0.15,
    "breakout_conviction": 0.14,
    "low_vol_breakout": 0.13,
    "defensive_flow": 0.13,
    "volatility_budget": 0.12,
    "inverse_volatility": 0.12,
    "atr_breakout_budget": 0.12,
    "oscillator_strength": 0.11,
    "fast_swing_scaled": 0.10,
    "fast_flow_probe": 0.09,
    "high_beta_capped": 0.09,
    "gap_risk_capped": 0.09,
    "rebound_probe": 0.08,
    "meta_consensus_scaled": 0.11,
    "equal_slot": 0.125,
}


def _is_duckdb_lock_error(exc: Exception) -> bool:
    message = str(exc)
    return (
        "different configuration than existing connections" in message
        or "another process" in message
        or "正由另一個程序使用" in message
        or "程序無法存取檔案" in message
        or "WinError 32" in message
    )


def _connect_duckdb_readonly(duckdb_path: Path) -> tuple[duckdb.DuckDBPyConnection, Path | None]:
    from backend.db import engine

    if duckdb_path.resolve() == Path(engine.DUCKDB_PATH).resolve():
        # Ingest may already own a read/write connection in this process. Reuse
        # its configuration and return only a cursor that this caller can close.
        return engine.get_conn(read_only=True).cursor(), None

    try:
        return duckdb.connect(str(duckdb_path), read_only=True), None
    except (duckdb.IOException, duckdb.ConnectionException) as exc:
        if not _is_duckdb_lock_error(exc):
            raise
        snapshot_path = Path(tempfile.gettempdir()) / (
            f"agent_arena_duckdb_snapshot_{os.getpid()}_{int(datetime.now(timezone.utc).timestamp())}.duckdb"
        )
        try:
            shutil.copy2(duckdb_path, snapshot_path)
        except PermissionError as copy_exc:
            raise RuntimeError(
                "DuckDB is locked by the web process; release the web DB connection "
                "before running Agent Arena standalone."
            ) from copy_exc
        return duckdb.connect(str(snapshot_path), read_only=True), snapshot_path


def _close_duckdb_readonly(conn: duckdb.DuckDBPyConnection, snapshot_path: Path | None = None) -> None:
    conn.close()
    if snapshot_path is not None:
        try:
            snapshot_path.unlink()
        except OSError:
            pass


@dataclass(frozen=True)
class StrategySpec:
    agent_id: str
    name: str
    style: str
    source_family: str
    source_repos: str
    thesis: str
    exit_policy: str
    holding_days: int
    rebalance_every: int
    max_positions: int
    min_turnover_m: float = 20.0
    min_price: float = 10.0
    max_price: float = 500.0


@dataclass(frozen=True)
class SourceModelSpec:
    repo: str
    family: str
    status: str
    mapped_agents: tuple[str, ...]
    note: str


AGENTS: tuple[StrategySpec, ...] = (
    StrategySpec(
        agent_id="tquant_vam_momentum",
        name="TQuant VAM Momentum",
        style="short/medium momentum",
        source_family="TQuant-Lab / FactorLibrary style",
        source_repos="tejtw/TQuant-Lab, tejtw/FactorLibrary-manual",
        thesis="Volatility-adjusted 20D momentum with MA60 and institutional support.",
                exit_policy="momentum_swing",
holding_days=10,
        rebalance_every=10,
        max_positions=8,
    ),
    StrategySpec(
        agent_id="dual_ma_trend",
        name="Dual MA Trend",
        style="trend following",
        source_family="classic technical strategy",
        source_repos="fmzquant/strategies, DaveSkender/Stock.Indicators, gbeced/pyalgotrade",
        thesis="MA20/MA60 trend alignment plus 60D persistence.",
                exit_policy="trend_trailing",
holding_days=20,
        rebalance_every=20,
        max_positions=8,
    ),
    StrategySpec(
        agent_id="institutional_flow",
        name="Institutional Flow Rider",
        style="Taiwan chip flow",
        source_family="local Taiwan market microstructure",
        source_repos="kevin801221/stock-strategies-only, matthewHsieh/Stock",
        thesis="Follow foreign/trust/dealer accumulation only when price trend is not broken.",
                exit_policy="flow_decay",
holding_days=10,
        rebalance_every=10,
        max_positions=8,
    ),
    StrategySpec(
        agent_id="low_vol_quality_proxy",
        name="Low Vol Quality Proxy",
        style="defensive trend",
        source_family="Riskfolio/skfolio/cvxportfolio inspired",
        source_repos="dcajasn/Riskfolio-Lib, skfolio/skfolio, cvxgrp/cvxportfolio",
        thesis="Prefer positive momentum names with lower realized volatility.",
                exit_policy="risk_control",
holding_days=20,
        rebalance_every=20,
        max_positions=8,
    ),
    StrategySpec(
        agent_id="volume_breakout",
        name="Volume Breakout",
        style="breakout swing",
        source_family="indicator and discretionary breakout",
        source_repos="CasualTrader, je-suis-tm/quant-trading, StockSharp/StockSharp",
        thesis="Near 60D highs with volume confirmation and no severe pullback.",
                exit_policy="breakout_trailing",
holding_days=10,
        rebalance_every=10,
        max_positions=8,
    ),
    StrategySpec(
        agent_id="risk_parity_momentum",
        name="Risk-Parity Momentum",
        style="risk-adjusted ranking",
        source_family="portfolio construction",
        source_repos="Riskfolio-Lib, skfolio, pybroker",
        thesis="Rank by 20D/60D return per unit volatility.",
                exit_policy="risk_control",
holding_days=20,
        rebalance_every=20,
        max_positions=8,
    ),
    StrategySpec(
        agent_id="ml_edge_proxy",
        name="ML Edge Proxy",
        style="machine-learning proxy",
        source_family="ML4T / FinRL / PyBroker inspired",
        source_repos=(
            "stefan-jansen/machine-learning-for-trading, AI4Finance-Foundation/FinRL-Trading, "
            "edtechre/pybroker"
        ),
        thesis="Blend momentum, flow, volatility and liquidity as an interpretable ML-like score.",
                exit_policy="ml_edge_swing",
holding_days=20,
        rebalance_every=20,
        max_positions=8,
    ),
    StrategySpec(
        agent_id="mean_reversion_rsi",
        name="Mean Reversion RSI",
        style="short mean reversion",
        source_family="classic oscillator strategy",
        source_repos="huseinzol05/Stock-Prediction-Models, ranaroussi/qtpylib, tensortrade-org/tensortrade",
        thesis="Oversold RSI/Bollinger rebound candidate. Kept honest by the admission backtest.",
                exit_policy="oscillator_rebound",
holding_days=5,
        rebalance_every=5,
        max_positions=8,
    ),
    StrategySpec(
        agent_id="vam_fast_5d",
        name="VAM Fast 5D",
        style="fast momentum",
        source_family="TQuant-Lab variant",
        source_repos="tejtw/TQuant-Lab, FactorLibrary-manual",
        thesis="Short-horizon volatility-adjusted 10D momentum.",
                exit_policy="fast_swing",
holding_days=5,
        rebalance_every=5,
        max_positions=8,
    ),
    StrategySpec(
        agent_id="vam_slow_20d",
        name="VAM Slow 20D",
        style="medium momentum",
        source_family="TQuant-Lab variant",
        source_repos="tejtw/TQuant-Lab, FactorLibrary-manual",
        thesis="60D momentum with 20D volatility confirmation.",
                exit_policy="trend_trailing",
holding_days=20,
        rebalance_every=20,
        max_positions=8,
    ),
    StrategySpec(
        agent_id="ma_fast_trend",
        name="MA Fast Trend",
        style="trend following",
        source_family="technical trend",
        source_repos="fmzquant/strategies, Stock.Indicators, qtpylib",
        thesis="Fast MA20 trend plus short acceleration.",
                exit_policy="trend_trailing",
holding_days=10,
        rebalance_every=10,
        max_positions=8,
    ),
    StrategySpec(
        agent_id="ma_slow_trend",
        name="MA Slow Trend",
        style="trend following",
        source_family="technical trend",
        source_repos="fmzquant/strategies, Stock.Indicators, qtpylib",
        thesis="Slower MA60 trend persistence and 60D strength.",
                exit_policy="trend_trailing",
holding_days=20,
        rebalance_every=20,
        max_positions=8,
    ),
    StrategySpec(
        agent_id="macd_acceleration",
        name="MACD Acceleration",
        style="short momentum",
        source_family="indicator strategy",
        source_repos="DaveSkender/Stock.Indicators, ranaroussi/qtpylib",
        thesis="MACD histogram acceleration with short momentum.",
                exit_policy="fast_swing",
holding_days=5,
        rebalance_every=5,
        max_positions=8,
    ),
    StrategySpec(
        agent_id="foreign_flow_rider",
        name="Foreign Flow Rider",
        style="chip-flow momentum",
        source_family="Taiwan institutional flow",
        source_repos="kevin801221/stock-strategies-only, matthewHsieh/Stock",
        thesis="Foreign net-buy pressure plus intact price trend.",
                exit_policy="flow_decay",
holding_days=10,
        rebalance_every=10,
        max_positions=8,
    ),
    StrategySpec(
        agent_id="trust_flow_rider",
        name="Trust Flow Rider",
        style="chip-flow momentum",
        source_family="Taiwan institutional flow",
        source_repos="kevin801221/stock-strategies-only, matthewHsieh/Stock",
        thesis="Investment trust net-buy pressure with trend support.",
                exit_policy="flow_decay",
holding_days=10,
        rebalance_every=10,
        max_positions=8,
    ),
    StrategySpec(
        agent_id="dealer_flow_swing",
        name="Dealer Flow Swing",
        style="short chip-flow swing",
        source_family="Taiwan institutional flow",
        source_repos="kevin801221/stock-strategies-only, matthewHsieh/Stock",
        thesis="Dealer flow as short-horizon swing confirmation.",
                exit_policy="fast_swing",
holding_days=5,
        rebalance_every=5,
        max_positions=8,
    ),
    StrategySpec(
        agent_id="volume_surge_swing",
        name="Volume Surge Swing",
        style="volume swing",
        source_family="breakout volume",
        source_repos="CasualTrader, StockSharp/StockSharp, je-suis-tm/quant-trading",
        thesis="Volume surge and short price strength.",
                exit_policy="fast_swing",
holding_days=5,
        rebalance_every=5,
        max_positions=8,
    ),
    StrategySpec(
        agent_id="quiet_breakout",
        name="Quiet Breakout",
        style="breakout trend",
        source_family="breakout volume",
        source_repos="CasualTrader, StockSharp/StockSharp, je-suis-tm/quant-trading",
        thesis="Near highs with less realized volatility.",
                exit_policy="breakout_trailing",
holding_days=10,
        rebalance_every=10,
        max_positions=8,
    ),
    StrategySpec(
        agent_id="high_beta_momentum",
        name="High Beta Momentum",
        style="aggressive momentum",
        source_family="momentum sleeve",
        source_repos="ML4T, FinRL, pybroker",
        thesis="Aggressive momentum names where volatility is accepted rather than avoided.",
                exit_policy="aggressive_momentum",
holding_days=10,
        rebalance_every=10,
        max_positions=8,
    ),
    StrategySpec(
        agent_id="low_beta_momentum",
        name="Low Beta Momentum",
        style="defensive momentum",
        source_family="risk-adjusted sleeve",
        source_repos="Riskfolio-Lib, skfolio, cvxportfolio",
        thesis="Positive momentum with lower realized volatility.",
                exit_policy="risk_control",
holding_days=20,
        rebalance_every=20,
        max_positions=8,
    ),
    StrategySpec(
        agent_id="gap_safe_momentum",
        name="Gap-Safe Momentum",
        style="risk-filtered momentum",
        source_family="production guardrail variant",
        source_repos="local Champion guardrails, TQuant-Lab",
        thesis="Momentum with volume spike risk damped.",
                exit_policy="risk_control",
holding_days=10,
        rebalance_every=10,
        max_positions=8,
    ),
    StrategySpec(
        agent_id="atr_breakout",
        name="ATR Breakout",
        style="breakout trend",
        source_family="volatility breakout",
        source_repos="Stock.Indicators, qtpylib, fmzquant/strategies",
        thesis="Momentum normalized by ATR with high-proximity confirmation.",
                exit_policy="breakout_trailing",
holding_days=10,
        rebalance_every=10,
        max_positions=8,
    ),
    StrategySpec(
        agent_id="bb_upper_trend",
        name="Bollinger Upper Trend",
        style="trend continuation",
        source_family="indicator strategy",
        source_repos="Stock.Indicators, qtpylib",
        thesis="Upper-band trend continuation with 20D strength.",
                exit_policy="breakout_trailing",
holding_days=10,
        rebalance_every=10,
        max_positions=8,
    ),
    StrategySpec(
        agent_id="rsi_strength",
        name="RSI Strength",
        style="momentum oscillator",
        source_family="indicator strategy",
        source_repos="huseinzol05/Stock-Prediction-Models, Stock.Indicators",
        thesis="RSI strength, not oversold reversion.",
                exit_policy="oscillator_strength",
holding_days=10,
        rebalance_every=10,
        max_positions=8,
    ),
    StrategySpec(
        agent_id="kd_strength",
        name="KD Strength",
        style="momentum oscillator",
        source_family="indicator strategy",
        source_repos="huseinzol05/Stock-Prediction-Models, Stock.Indicators",
        thesis="KD positive structure with 20D momentum.",
                exit_policy="oscillator_strength",
holding_days=10,
        rebalance_every=10,
        max_positions=8,
    ),
    StrategySpec(
        agent_id="defensive_flow",
        name="Defensive Flow",
        style="defensive chip-flow",
        source_family="risk allocation sleeve",
        source_repos="Riskfolio-Lib, skfolio, local institutional data",
        thesis="Low volatility names with institutional support.",
                exit_policy="risk_control",
holding_days=20,
        rebalance_every=20,
        max_positions=8,
    ),
    StrategySpec(
        agent_id="short_swing_momentum",
        name="Short Swing Momentum",
        style="short swing",
        source_family="short-term strategy",
        source_repos="CasualTrader, fmzquant/strategies, pyalgotrade",
        thesis="5D momentum with volume and flow support.",
                exit_policy="fast_swing",
holding_days=5,
        rebalance_every=5,
        max_positions=8,
    ),
    StrategySpec(
        agent_id="long_compounder",
        name="Long Compounder",
        style="longer trend",
        source_family="trend and risk-adjusted sleeve",
        source_repos="ML4T, Riskfolio-Lib, pybroker",
        thesis="60D compounder trend with volatility adjustment.",
                exit_policy="trend_trailing",
holding_days=20,
        rebalance_every=20,
        max_positions=8,
    ),
    StrategySpec(
        agent_id="contrarian_reversal",
        name="Contrarian Reversal",
        style="contrarian",
        source_family="mean-reversion stress test",
        source_repos="tensortrade, qtpylib, Stock-Prediction-Models",
        thesis="Negative momentum reversal. Kept to show whether the data rejects contrarian ideas.",
                exit_policy="oscillator_rebound",
holding_days=5,
        rebalance_every=5,
        max_positions=8,
    ),
    StrategySpec(
        agent_id="consensus_pullback_addon",
        name="Consensus Difference Adjuster",
        style="multi-model consensus",
        source_family="local ensemble / TQuant / risk-aware technical",
        source_repos="local Agent Arena ensemble, tejtw/TQuant-Lab, DaveSkender/Stock.Indicators, Riskfolio-Lib",
        thesis=(
            "Aggregate all existing Arena agents and buy only when at least three independent models "
            "or two models plus TDCC accumulation converge on the same ticker, then downsize when trend "
            "or volatility controls weaken."
        ),
        exit_policy="meta_consensus",
        holding_days=10,
        rebalance_every=5,
        max_positions=8,
    ),
)


SOURCE_LIBRARY: tuple[SourceModelSpec, ...] = (
    SourceModelSpec(
        "local/agent-arena-consensus",
        "multi-model ensemble",
        "CONVERTED",
        ("consensus_pullback_addon",),
        "Synthesizes all landed Arena model families into a cross-agent consensus adjuster.",
    ),
    SourceModelSpec("kevin801221/stock-strategies-only", "Taiwan strategy rules", "CONVERTED", ("institutional_flow", "foreign_flow_rider", "trust_flow_rider", "dealer_flow_swing", "defensive_flow"), "Taiwan flow ideas converted into deterministic chip-flow agents."),
    SourceModelSpec("sacahan/CasualTrader", "discretionary breakout", "CONVERTED", ("volume_breakout", "volume_surge_swing", "quiet_breakout", "short_swing_momentum"), "Breakout/swing concepts translated to Taiwan liquidity-filtered rules."),
    SourceModelSpec("matthewHsieh/Stock", "Taiwan market scripts", "CONVERTED", ("institutional_flow", "foreign_flow_rider", "trust_flow_rider", "dealer_flow_swing"), "Used as local Taiwan flow/technical reference."),
    SourceModelSpec("rkuo2000/AI-stocks", "AI stock analysis", "NOT_LANDED", (), "Needs Taiwan-data feature mapping and leakage review before becoming an agent."),
    SourceModelSpec("TauricResearch/TradingAgents", "LLM trading committee", "CONCEPT_ONLY", (), "Persona/review layer only; no LLM trade without deterministic backtest-backed rules."),
    SourceModelSpec("hsliuping/TradingAgents-CN", "LLM trading committee", "CONCEPT_ONLY", (), "China-market agent workflow reference; not used as direct Taiwan trade logic."),
    SourceModelSpec("HKUDS/Vibe-Trading", "LLM trading committee", "CONCEPT_ONLY", (), "Useful for analyst-panel UX, not an executable Taiwan strategy yet."),
    SourceModelSpec("ZhuLinsen/daily_stock_analysis", "news intelligence", "CONCEPT_ONLY", (), "News/provider fallback ideas are useful for advisory summaries, not arena trade rules."),
    SourceModelSpec("ErikThiart/ai-stock-dashboard", "dashboard / technical UI", "CONCEPT_ONLY", (), "UI and indicator presentation reference; no direct agent."),
    SourceModelSpec("quantopian/zipline", "backtesting engine", "INFRA_REFERENCE", (), "Heavy engine reference; local event replay remains lighter for Taiwan data."),
    SourceModelSpec("QuantConnect/Lean", "backtesting engine", "INFRA_REFERENCE", (), "Framework reference only; not merged into local scheduler or production gate."),
    SourceModelSpec("gbeced/pyalgotrade", "backtesting engine", "CONVERTED", ("dual_ma_trend", "short_swing_momentum"), "Classic replay ideas reflected in local no-lookahead backtest loop."),
    SourceModelSpec("edtechre/pybroker", "ML/backtest framework", "CONVERTED", ("risk_parity_momentum", "ml_edge_proxy", "long_compounder"), "Used as reference for interpretable proxy and portfolio-style agents."),
    SourceModelSpec("scrtlabs/catalyst", "backtesting engine", "INFRA_REFERENCE", (), "Crypto-oriented engine; no Taiwan agent mapping."),
    SourceModelSpec("Lumiwealth/lumibot", "trading bot framework", "INFRA_REFERENCE", (), "Execution framework reference, not needed for paper arena."),
    SourceModelSpec("coding-kitties/investing-algorithm-framework", "framework", "INFRA_REFERENCE", (), "Architecture reference only."),
    SourceModelSpec("stefan-jansen/machine-learning-for-trading", "ML trading", "CONVERTED", ("ml_edge_proxy", "high_beta_momentum", "long_compounder"), "Converted into interpretable ML-like proxy until local labels mature."),
    SourceModelSpec("AI4Finance-Foundation/FinRL-Trading", "RL trading", "CONVERTED", ("ml_edge_proxy", "high_beta_momentum"), "RL ideas reduced to auditable proxy signals for now."),
    SourceModelSpec("TradeMaster-NTU/TradeMaster", "RL trading", "NOT_LANDED", (), "Requires environment/label adaptation before Taiwan backtest."),
    SourceModelSpec("tensortrade-org/tensortrade", "RL trading", "CONVERTED", ("mean_reversion_rsi", "contrarian_reversal"), "Stress-test ideas mapped into oscillator/contrarian deterministic agents."),
    SourceModelSpec("huseinzol05/Stock-Prediction-Models", "ML/technical prediction", "CONVERTED", ("mean_reversion_rsi", "rsi_strength", "kd_strength", "contrarian_reversal"), "Indicator/ML ideas converted into oscillator agents."),
    SourceModelSpec("firmai/financial-machine-learning", "financial ML", "NOT_LANDED", (), "Needs leakage-controlled labels and local feature parity."),
    SourceModelSpec("grananqvist/Awesome-Quant-Machine-Learning-Trading", "awesome list", "CONCEPT_ONLY", (), "Research index only; no direct executable rule."),
    SourceModelSpec("cbailes/awesome-deep-trading", "awesome list", "CONCEPT_ONLY", (), "Research index only."),
    SourceModelSpec("BlackArbsCEO/Adv_Fin_ML_Exercises", "financial ML exercises", "NOT_LANDED", (), "Conceptual AFML material, not an executable Taiwan agent."),
    SourceModelSpec("robertmartin8/MachineLearningStocks", "ML stock prediction", "NOT_LANDED", (), "Needs local retraining and leakage checks."),
    SourceModelSpec("PacktPublishing/Hands-On-Machine-Learning-for-Algorithmic-Trading", "ML trading book", "CONCEPT_ONLY", (), "Book/reference material; no direct imported model."),
    SourceModelSpec("PacktPublishing/Machine-Learning-for-Algorithmic-Trading-Second-Edition_Original", "ML trading book", "CONCEPT_ONLY", (), "Book/reference material; no direct imported model."),
    SourceModelSpec("Rachnog/Deep-Trading", "deep learning trading", "NOT_LANDED", (), "Requires local GPU/runtime validation before agent conversion."),
    SourceModelSpec("Ceruleanacg/Personae", "agent personas", "CONCEPT_ONLY", (), "Persona architecture reference only."),
    SourceModelSpec("jankrepl/deepdow", "portfolio deep learning", "CONVERTED", ("risk_parity_momentum", "low_beta_momentum"), "Portfolio/risk ideas approximated by deterministic risk-adjusted rankings."),
    SourceModelSpec("0xemmkty/QuantMuse", "quant research", "NOT_LANDED", (), "No deterministic Taiwan mapping yet."),
    SourceModelSpec("TraderAlice/OpenAlice", "agent trading", "CONCEPT_ONLY", (), "Agent orchestration reference only."),
    SourceModelSpec("brokermr810/QuantDinger", "quant framework", "NOT_LANDED", (), "Needs Taiwan-data adapter before backtest."),
    SourceModelSpec("StockSharp/StockSharp", "technical/discretionary strategies", "CONVERTED", ("volume_breakout", "volume_surge_swing", "quiet_breakout", "atr_breakout"), "Breakout/indicator concepts converted to local rules."),
    SourceModelSpec("je-suis-tm/quant-trading", "technical/discretionary strategies", "CONVERTED", ("volume_breakout", "volume_surge_swing", "quiet_breakout"), "Discretionary strategy ideas converted to breakout sleeves."),
    SourceModelSpec("fmzquant/strategies", "technical strategies", "CONVERTED", ("dual_ma_trend", "ma_fast_trend", "ma_slow_trend", "atr_breakout", "short_swing_momentum"), "Classic technical strategy family converted to multiple agents."),
    SourceModelSpec("ranaroussi/qtpylib", "indicator toolkit", "CONVERTED", ("dual_ma_trend", "ma_fast_trend", "ma_slow_trend", "mean_reversion_rsi", "bb_upper_trend", "rsi_strength", "kd_strength"), "Indicator patterns mapped to local daily_k features."),
    SourceModelSpec("DaveSkender/Stock.Indicators", "indicator library", "CONVERTED", ("macd_acceleration", "atr_breakout", "bb_upper_trend", "rsi_strength", "kd_strength"), "Indicator formulas inspired technical agents."),
    SourceModelSpec("dcajasn/Riskfolio-Lib", "portfolio optimization", "CONVERTED", ("low_vol_quality_proxy", "risk_parity_momentum", "low_beta_momentum", "defensive_flow"), "Risk-aware ideas mapped to deterministic ranking; allocation remains equal-notional."),
    SourceModelSpec("skfolio/skfolio", "portfolio optimization", "CONVERTED", ("low_vol_quality_proxy", "risk_parity_momentum", "low_beta_momentum", "defensive_flow"), "Risk-aware ideas mapped to deterministic ranking."),
    SourceModelSpec("cvxgrp/cvxportfolio", "portfolio optimization", "CONVERTED", ("low_vol_quality_proxy", "risk_parity_momentum", "low_beta_momentum"), "Portfolio constraints kept as future allocation work; ranking proxy exists."),
    SourceModelSpec("chrisconlan/algorithmic-trading-with-python", "technical trading reference", "CONCEPT_ONLY", (), "Reference examples only."),
    SourceModelSpec("nickmccullum/algorithmic-trading-python", "technical trading reference", "CONCEPT_ONLY", (), "Reference examples only."),
    SourceModelSpec("chrisworsey55/atlas-gic", "macro/portfolio", "NOT_LANDED", (), "No Taiwan daily stock mapping yet."),
    SourceModelSpec("51bitquant/bitquant", "quant framework", "NOT_LANDED", (), "No local adapter."),
    SourceModelSpec("JerBouma/AlgorithmicTrading", "algorithmic trading reference", "CONCEPT_ONLY", (), "Reference material only."),
    SourceModelSpec("0xfdf/toraniko", "factor analytics", "NOT_LANDED", (), "Could become factor diagnostics after local validation."),
    SourceModelSpec("boyboi86/AFML", "financial ML", "CONCEPT_ONLY", (), "AFML reference only."),
    SourceModelSpec("jjakimoto/finance_ml", "financial ML", "NOT_LANDED", (), "Needs local label pipeline."),
    SourceModelSpec("dzitkowskik/StockPredictionRNN", "RNN stock prediction", "NOT_LANDED", (), "No validated Taiwan retraining yet."),
    SourceModelSpec("JordiCorbilla/stock-prediction-deep-neural-learning", "deep learning stock prediction", "NOT_LANDED", (), "No validated Taiwan retraining yet."),
    SourceModelSpec("llSourcell/Reinforcement_Learning_for_Stock_Prediction", "RL stock prediction", "NOT_LANDED", (), "No validated Taiwan environment yet."),
    SourceModelSpec("Quantweb3-com/NexusTrader", "trading framework", "NOT_LANDED", (), "Framework not adapted to local paper contest."),
    SourceModelSpec("fulifeng/Temporal_Relational_Stock_Ranking", "relational stock ranking", "NOT_LANDED", (), "Interesting cross-sectional ranking idea; needs Taiwan graph/features."),
    SourceModelSpec("sebastianheinz/stockprediction", "stock prediction", "NOT_LANDED", (), "No local validation yet."),
    SourceModelSpec("kimber-chen/Tensorflow-for-stock-prediction", "TensorFlow stock prediction", "NOT_LANDED", (), "No local validation yet."),
    SourceModelSpec("timestocome/Test-stock-prediction-algorithms", "prediction algorithm benchmark", "CONCEPT_ONLY", (), "Benchmarking reference only."),
    SourceModelSpec("zshicode/Attention-CLX-stock-prediction", "attention stock prediction", "NOT_LANDED", (), "No local validation yet."),
    SourceModelSpec("moyuweiqing/A-stock-prediction-algorithm-based-on-machine-learning", "A-share ML prediction", "NOT_LANDED", (), "A-share specific; needs Taiwan feature conversion."),
    SourceModelSpec("saeed349/Deep-Reinforcement-Learning-in-Trading", "deep RL trading", "NOT_LANDED", (), "No validated Taiwan environment yet."),
    SourceModelSpec("CFMTech/Deep-RL-for-Portfolio-Optimization", "deep RL portfolio", "NOT_LANDED", (), "No validated Taiwan environment yet."),
    SourceModelSpec("pipiku915/FinMem-LLM-StockTrading", "LLM memory trading", "CONCEPT_ONLY", (), "LLM memory concept only; not a direct trade signal."),
    SourceModelSpec("tejtw/TQuant-Lab", "Taiwan TQuant/factor", "CONVERTED", ("tquant_vam_momentum", "vam_fast_5d", "vam_slow_20d", "gap_safe_momentum"), "Core Taiwan quant ideas converted to VAM/factor agents."),
    SourceModelSpec("tejtw/FactorLibrary-manual", "Taiwan factor library", "CONVERTED", ("tquant_vam_momentum", "vam_fast_5d", "vam_slow_20d"), "Factor concepts mapped to available daily_k fields."),
    SourceModelSpec("tejtw/TEJ_TOOL_API", "TEJ data API", "CONCEPT_ONLY", (), "Data-source reference; no TEJ credential-dependent agent in local arena."),
    SourceModelSpec("tejtw/TQuant-manual", "Taiwan TQuant docs", "CONCEPT_ONLY", (), "Documentation reference supporting TQuant-style agents."),
    SourceModelSpec("tejtw/zipline-tej", "TEJ backtesting engine", "INFRA_REFERENCE", (), "TEJ/zipline reference; not used in local replay."),
    SourceModelSpec("tejtw/EN-TEJAPI", "TEJ data docs", "CONCEPT_ONLY", (), "Data documentation reference."),
    SourceModelSpec("tejtw/exchange_calendars", "calendar tooling", "INFRA_REFERENCE", (), "Calendar reference; local Taiwan calendar remains in existing scripts."),
    SourceModelSpec("tejtw/pyfolio-tej", "performance analytics", "CONCEPT_ONLY", (), "Could inform future arena analytics, not an agent."),
    SourceModelSpec("tejtw/TEJAPI_Python_Medium_Application", "TEJ tutorials", "CONCEPT_ONLY", (), "Tutorial/data reference only."),
    SourceModelSpec("tejtw/TEJAPI_Python_Medium_Quant", "TEJ quant tutorials", "CONCEPT_ONLY", (), "Quant tutorial reference only."),
    SourceModelSpec("tejtw/TEJAPI_Python_Medium_DataAnalysis", "TEJ data analysis", "CONCEPT_ONLY", (), "Data-analysis reference only."),
    SourceModelSpec("tejtw/WelcomeToTejApi", "TEJ onboarding", "CONCEPT_ONLY", (), "Onboarding reference only."),
    SourceModelSpec("tejtw/TEJAPI_Python_Medium_Rookies", "TEJ tutorials", "CONCEPT_ONLY", (), "Tutorial reference only."),
    SourceModelSpec("tejtw/TEJ_API_Python_WarrantTStandard_ProgramSample", "TEJ warrant sample", "NOT_LANDED", (), "Warrant domain out of current Taiwan stock arena."),
    SourceModelSpec("tejtw/TEJ_API_Python_Efficient_Frontier_ProgramSample", "efficient frontier", "CONCEPT_ONLY", (), "Portfolio optimization reference; not executable yet."),
    SourceModelSpec("tejtw/TEJ_API_Python_EPS_dividend_check", "fundamental data", "NOT_LANDED", (), "Could feed future quality/value agent after local data parity."),
    SourceModelSpec("tejtw/TEJ_API_Python_Crossing_price", "price crossing sample", "CONCEPT_ONLY", (), "Technical-rule reference only."),
    SourceModelSpec("tejtw/TEJ_API_Python_RealEstateTransfer_ProgramSample", "real estate data", "NOT_LANDED", (), "Outside current stock arena."),
    SourceModelSpec("tejtw/TEJ_API_Python_VaRStandard_ProgramSample", "risk analytics", "CONCEPT_ONLY", (), "Risk-reporting reference only."),
    SourceModelSpec("tejtw/TEJ_API_Python_FinancialdatawithReceivable", "fundamental data", "NOT_LANDED", (), "Could feed future quality/fundamental agents."),
    SourceModelSpec("tejtw/TEJ_API_Python_FinancialdatawithLoan", "fundamental data", "NOT_LANDED", (), "Could feed future balance-sheet agents."),
    SourceModelSpec("tejtw/TEJ_API_Python_EPS", "fundamental data", "NOT_LANDED", (), "Could feed future EPS/value agent."),
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _as_pct(value: float | None) -> float | None:
    if value is None or not np.isfinite(value):
        return None
    return float(value) * 100.0


def _safe_float(value: Any) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(numeric):
        return None
    return numeric


def _numeric_series(frame: pd.DataFrame, column: str, default: float | None = 0.0) -> pd.Series:
    if column in frame.columns:
        series = pd.to_numeric(frame[column], errors="coerce")
    else:
        series = pd.Series(np.nan, index=frame.index, dtype="float64")
    if default is not None:
        series = series.fillna(default)
    return series.astype("float64")


def _clip_float(value: float, low: float, high: float) -> float:
    return float(min(high, max(low, value)))


def _sizing_policy_for_spec(spec: StrategySpec | None) -> str:
    if spec is None:
        return "equal_slot"
    return SIZING_POLICY_BY_AGENT.get(spec.agent_id, "equal_slot")


def _sizing_policy_explanation(policy: str) -> str:
    return SIZING_POLICY_LABELS.get(policy, SIZING_POLICY_LABELS["equal_slot"])


def _execution_policy_for_spec(spec: StrategySpec | None) -> str:
    if spec is None:
        return "conservative_open_confirm"
    return EXECUTION_POLICY_BY_AGENT.get(spec.agent_id, "conservative_open_confirm")


def _execution_policy_config(policy: str) -> dict[str, float | bool]:
    return {
        **EXECUTION_POLICY_CONFIGS["conservative_open_confirm"],
        **EXECUTION_POLICY_CONFIGS.get(policy, {}),
    }


def _execution_policy_explanation(policy: str) -> str:
    return EXECUTION_POLICY_LABELS.get(policy, EXECUTION_POLICY_LABELS["conservative_open_confirm"])


@lru_cache(maxsize=1)
def _stock_name_map() -> dict[str, str]:
    if not SECTOR_MAPPING_PATH.exists():
        return {}
    try:
        df = pd.read_csv(SECTOR_MAPPING_PATH, dtype={"Ticker": str})
    except Exception:
        return {}
    if "Ticker" not in df.columns or "Name" not in df.columns:
        return {}
    clean = df[["Ticker", "Name"]].dropna().copy()
    clean["Ticker"] = clean["Ticker"].astype(str).str.zfill(4)
    clean["Name"] = clean["Name"].astype(str).str.strip()
    return dict(zip(clean["Ticker"], clean["Name"]))


def _lookup_stock_name(ticker: str) -> str:
    clean = str(ticker or "").strip().zfill(4)
    return _stock_name_map().get(clean, "")


def _score_factor(score: float | None) -> float:
    if score is None:
        return 1.0
    return _clip_float(1.0 + math.tanh(score / 8.0) * 0.25, 0.75, 1.25)


def _rank_factor(rank_index: int, total: int) -> float:
    if total <= 1:
        return 1.0
    return _clip_float(1.18 - 0.36 * (rank_index / max(1, total - 1)), 0.82, 1.18)


def _row_factor(row: pd.Series, policy: str, *, rank_index: int, total: int) -> tuple[float, str]:
    score = _safe_float(row.get("score"))
    ret_5 = _safe_float(row.get("ret_5")) or 0.0
    ret_10 = _safe_float(row.get("ret_10")) or 0.0
    ret_20 = _safe_float(row.get("ret_20")) or 0.0
    ret_60 = _safe_float(row.get("ret_60")) or 0.0
    vol_20 = _safe_float(row.get("vol_20")) or 0.025
    vol_ratio = _safe_float(row.get("vol_ratio")) or 1.0
    close_to_high = _safe_float(row.get("close_to_high_60")) or 0.0
    price_vs_ma20 = _safe_float(row.get("price_vs_ma20")) or 0.0
    price_vs_ma60 = _safe_float(row.get("price_vs_ma60")) or 0.0
    inst_5_norm = _safe_float(row.get("inst_5_norm")) or 0.0
    rsi14 = _safe_float(row.get("rsi14")) or 50.0

    if policy in {"volatility_budget", "inverse_volatility", "defensive_flow"}:
        raw = 0.025 / max(vol_20, 0.006)
        if policy == "defensive_flow":
            raw *= 1.0 + _clip_float(inst_5_norm * 80.0, -0.12, 0.18)
        return _clip_float(raw, 0.55, 1.28), f"{policy}: inverse vol_20 {vol_20:.3f}"

    if policy == "trend_core":
        raw = 0.95 + _clip_float(price_vs_ma20 * 2.0, -0.20, 0.25) + _clip_float(price_vs_ma60, -0.10, 0.18)
        raw *= _rank_factor(rank_index, total)
        return _clip_float(raw, 0.65, 1.30), f"trend_core: MA20 {price_vs_ma20:.1%}, MA60 {price_vs_ma60:.1%}"

    if policy == "conviction_momentum":
        raw = _score_factor(score) * _rank_factor(rank_index, total) * (1.0 + _clip_float(ret_20, -0.08, 0.12))
        return _clip_float(raw, 0.70, 1.35), f"conviction_momentum: rank {rank_index + 1}/{total}, ret20 {ret_20:.1%}"

    if policy == "flow_conviction":
        raw = 0.95 + _clip_float(inst_5_norm * 160.0, -0.25, 0.35)
        raw *= _rank_factor(rank_index, total)
        return _clip_float(raw, 0.60, 1.35), f"flow_conviction: inst_5_norm {inst_5_norm:.4f}"

    if policy == "breakout_conviction":
        raw = 0.90 + _clip_float(close_to_high, -0.08, 0.15) + _clip_float((vol_ratio - 1.0) * 0.16, -0.10, 0.22)
        raw *= _rank_factor(rank_index, total)
        return _clip_float(raw, 0.65, 1.32), f"breakout_conviction: close_to_high {close_to_high:.1%}, vol_ratio {vol_ratio:.2f}"

    if policy == "low_vol_breakout":
        raw = (0.90 + _clip_float(close_to_high, -0.05, 0.12)) * (0.028 / max(vol_20, 0.008))
        return _clip_float(raw, 0.55, 1.20), f"low_vol_breakout: close_to_high {close_to_high:.1%}, vol20 {vol_20:.3f}"

    if policy == "ml_confidence":
        raw = _score_factor(score) * (1.0 - _clip_float(vol_20 - 0.025, 0.0, 0.04) * 3.0)
        return _clip_float(raw, 0.70, 1.30), f"ml_confidence: score {score:.3f}" if score is not None else "ml_confidence: neutral score"

    if policy in {"fast_swing_scaled", "fast_flow_probe"}:
        raw = 0.78 + _clip_float(ret_5 + ret_10, -0.10, 0.18) - _clip_float(vol_20 - 0.03, 0.0, 0.04) * 2.0
        if policy == "fast_flow_probe":
            raw *= 0.88
        return _clip_float(raw, 0.45, 1.05), f"{policy}: ret5 {ret_5:.1%}, ret10 {ret_10:.1%}, vol20 {vol_20:.3f}"

    if policy == "high_beta_capped":
        raw = 0.70 + _clip_float(ret_20, -0.08, 0.14) - _clip_float(vol_20 - 0.035, 0.0, 0.06) * 2.2
        return _clip_float(raw, 0.42, 0.95), f"high_beta_capped: ret20 {ret_20:.1%}, vol20 {vol_20:.3f}"

    if policy == "gap_risk_capped":
        raw = 0.88 + _clip_float(ret_20, -0.08, 0.10) - _clip_float(vol_ratio - 1.8, 0.0, 2.0) * 0.16
        return _clip_float(raw, 0.45, 1.00), f"gap_risk_capped: vol_ratio {vol_ratio:.2f}, ret20 {ret_20:.1%}"

    if policy == "atr_breakout_budget":
        raw = 0.95 + _clip_float(close_to_high, -0.05, 0.14) - _clip_float(vol_20 - 0.03, 0.0, 0.05) * 1.8
        return _clip_float(raw, 0.55, 1.18), f"atr_breakout_budget: close_to_high {close_to_high:.1%}, vol20 {vol_20:.3f}"

    if policy == "oscillator_strength":
        raw = 0.80 + _clip_float((rsi14 - 50.0) / 100.0, -0.10, 0.16) + _clip_float(ret_20, -0.06, 0.08)
        return _clip_float(raw, 0.55, 1.12), f"oscillator_strength: rsi14 {rsi14:.1f}, ret20 {ret_20:.1%}"

    if policy == "rebound_probe":
        raw = 0.55 + _clip_float((45.0 - rsi14) / 100.0, -0.05, 0.16) - _clip_float(-ret_20, 0.0, 0.20) * 0.2
        return _clip_float(raw, 0.38, 0.82), f"rebound_probe: rsi14 {rsi14:.1f}, ret20 {ret_20:.1%}"

    if policy == "meta_consensus_scaled":
        meta_votes = _safe_float(row.get("meta_votes")) or 0.0
        meta_rank_score = _safe_float(row.get("meta_rank_score")) or 0.0
        trend_support = _clip_float(price_vs_ma60, -0.06, 0.10)
        flow_support = _clip_float(inst_5_norm * 120.0, -0.10, 0.16)
        volatility_penalty = _clip_float(vol_20 - 0.035, 0.0, 0.05) * 1.6
        agreement_bonus = _clip_float((meta_votes - 3.0) * 0.05 + meta_rank_score * 0.025, 0.0, 0.22)
        raw = 0.70 + agreement_bonus + trend_support + flow_support - volatility_penalty
        raw *= _rank_factor(rank_index, total)
        return (
            _clip_float(raw, 0.45, 1.15),
            f"meta_consensus_scaled: votes {meta_votes:.0f}, rank_score {meta_rank_score:.2f}, MA60 {price_vs_ma60:.1%}, flow {inst_5_norm:.4f}",
        )

    if policy == "compounder_core":
        raw = 1.02 + _clip_float(ret_60, -0.08, 0.20) + _clip_float(price_vs_ma60, -0.08, 0.16)
        raw *= 0.95 + min(0.12, max(0.0, 0.035 - vol_20))
        return _clip_float(raw, 0.75, 1.38), f"compounder_core: ret60 {ret_60:.1%}, MA60 {price_vs_ma60:.1%}"

    return 1.0, "equal_slot: fallback"


def _build_sizing_plan(spec: StrategySpec, buy_rows: pd.DataFrame, cash: float) -> list[dict[str, Any]]:
    if buy_rows.empty or cash <= MIN_TRADE_NOTIONAL:
        return []
    policy = _sizing_policy_for_spec(spec)
    capital_base = max(float(cash), STARTING_CAPITAL)
    cap_notional = capital_base * SIZING_POLICY_CAPS.get(policy, SIZING_POLICY_CAPS["equal_slot"])
    raw_items: list[dict[str, Any]] = []
    total = len(buy_rows)
    for rank_index, (_, row) in enumerate(buy_rows.iterrows()):
        factor, reason = _row_factor(row, policy, rank_index=rank_index, total=total)
        raw_items.append({"row": row, "raw": factor, "reason": reason})
    raw_sum = sum(float(item["raw"]) for item in raw_items)
    if raw_sum <= 0:
        return []
    available_gross = cash / (1.0 + BUY_COST)
    decisions: list[dict[str, Any]] = []
    for item in raw_items:
        row = item["row"]
        raw_weight = float(item["raw"]) / raw_sum
        gross_notional = min(available_gross * raw_weight, cap_notional)
        target_weight = gross_notional / capital_base
        decisions.append(
            {
                "row": row,
                "sizing_policy": policy,
                "target_notional": float(gross_notional),
                "target_weight": float(target_weight),
                "sizing_reason": str(item["reason"]),
            }
        )
    return decisions


def _quantize_tw_lot_trade(target_notional: float, fill_price: float) -> dict[str, Any]:
    if target_notional <= 0 or fill_price <= 0:
        return {
            "shares": 0.0,
            "actual_notional": 0.0,
            "board_lots": 0,
            "odd_lot_shares": 0,
            "board_lot_mode": "invalid",
        }
    max_shares = int(target_notional // fill_price)
    if max_shares <= 0:
        return {
            "shares": 0.0,
            "actual_notional": 0.0,
            "board_lots": 0,
            "odd_lot_shares": 0,
            "board_lot_mode": "insufficient_for_one_share",
        }
    board_lots = max_shares // BOARD_LOT_SIZE
    if board_lots >= 1:
        shares = board_lots * BOARD_LOT_SIZE
        odd_lot_shares = 0
        mode = "board_lot"
    else:
        shares = max_shares
        odd_lot_shares = max_shares
        mode = "odd_lot_fallback"
    return {
        "shares": float(shares),
        "actual_notional": float(shares * fill_price),
        "board_lots": int(board_lots),
        "odd_lot_shares": int(odd_lot_shares),
        "board_lot_mode": mode,
    }


def _lot_summary(row: dict[str, Any] | sqlite3.Row) -> dict[str, Any]:
    shares = _safe_float(row["shares"] if "shares" in row.keys() else None) if isinstance(row, sqlite3.Row) else _safe_float(row.get("shares"))
    board_lots_raw = row["board_lots"] if isinstance(row, sqlite3.Row) and "board_lots" in row.keys() else (row.get("board_lots") if isinstance(row, dict) else None)
    odd_lot_raw = row["odd_lot_shares"] if isinstance(row, sqlite3.Row) and "odd_lot_shares" in row.keys() else (row.get("odd_lot_shares") if isinstance(row, dict) else None)
    mode_raw = row["board_lot_mode"] if isinstance(row, sqlite3.Row) and "board_lot_mode" in row.keys() else (row.get("board_lot_mode") if isinstance(row, dict) else None)
    if board_lots_raw is not None:
        board_lots = int(board_lots_raw or 0)
        odd_lot_shares = int(odd_lot_raw or 0)
        mode = str(mode_raw or ("board_lot" if board_lots else "odd_lot_fallback"))
    elif shares is not None:
        whole = int(shares)
        board_lots = whole // BOARD_LOT_SIZE
        odd_lot_shares = max(0, whole - board_lots * BOARD_LOT_SIZE)
        mode = "legacy_fractional" if abs(shares - whole) > 1e-6 else ("board_lot" if board_lots else "odd_lot_fallback")
    else:
        board_lots = 0
        odd_lot_shares = 0
        mode = "unknown"
    summary = f"{board_lots}張" if board_lots else ""
    if odd_lot_shares:
        summary = f"{summary} + {odd_lot_shares}股" if summary else f"{odd_lot_shares}股"
    if not summary:
        summary = "0張"
    if mode == "legacy_fractional" and shares is not None:
        summary = f"{summary} legacy"
    return {
        "board_lots": board_lots,
        "odd_lot_shares": odd_lot_shares,
        "board_lot_mode": mode,
        "lot_summary": summary,
    }


def _attach_tdcc_features(
    df: pd.DataFrame,
    *,
    tdcc_summary_path: Path = TDCC_SUMMARY_PATH,
) -> pd.DataFrame:
    if df.empty:
        return df

    if not tdcc_summary_path.exists():
        out = df.copy()
        for column in TDCC_FEATURE_COLS:
            out[column] = np.nan
        return out

    enriched: list[pd.DataFrame] = []
    for ticker, group in df.groupby("ticker", sort=False):
        daily = group.copy()
        daily["Date"] = pd.to_datetime(daily["dt"]).astype("datetime64[ns]")
        daily = compute_tdcc_features(daily, str(ticker).zfill(4), str(tdcc_summary_path))
        daily = daily.drop(columns=["Date"], errors="ignore")
        enriched.append(daily)

    if not enriched:
        out = df.copy()
    else:
        out = pd.concat(enriched, ignore_index=True)

    out["dt"] = pd.to_datetime(out["dt"]).astype("datetime64[ns]")
    for column in TDCC_FEATURE_COLS:
        if column not in out.columns:
            out[column] = np.nan
        else:
            out[column] = pd.to_numeric(out[column], errors="coerce")
    return out.sort_values(["ticker", "dt"]).reset_index(drop=True)


def _load_feature_frame(
    *,
    duckdb_path: Path = DEFAULT_DUCKDB_PATH,
    start_date: str = "2023-01-01",
    end_date: str | None = None,
) -> pd.DataFrame:
    end_clause = f"AND Date <= DATE '{end_date}'" if end_date else ""
    query = f"""
        SELECT
            Ticker AS ticker,
            Date AS dt,
            "Open" AS open_px,
            High AS high_px,
            Low AS low_px,
            "Close" AS close_px,
            Volume AS volume,
            Foreign_BuySell AS foreign_bs,
            Trust_BuySell AS trust_bs,
            Dealer_BuySell AS dealer_bs,
            MA_5 AS ma5,
            MA_20 AS ma20,
            MA_60 AS ma60,
            RSI_14 AS rsi14,
            "BBU_20_2.0" AS bb_up,
            "BBL_20_2.0" AS bb_low,
            MACD_12_26_9 AS macd,
            MACDs_12_26_9 AS macd_signal,
            MACDh_12_26_9 AS macd_hist,
            K AS k,
            D AS d,
            ATR_14 AS atr14,
            VOL_MA_20 AS vol_ma20
        FROM daily_k
        WHERE Date >= DATE '{start_date}'
        {end_clause}
    """
    conn, snapshot_path = _connect_duckdb_readonly(duckdb_path)
    try:
        df = conn.execute(query).fetchdf()
    finally:
        _close_duckdb_readonly(conn, snapshot_path)
    if df.empty:
        raise RuntimeError("daily_k returned no rows for agent arena")

    df["dt"] = pd.to_datetime(df["dt"])
    for column in df.columns:
        if column not in {"ticker", "dt"}:
            df[column] = pd.to_numeric(df[column], errors="coerce")
    df["ticker"] = df["ticker"].astype(str).str.zfill(4)
    df = df.sort_values(["ticker", "dt"]).reset_index(drop=True)

    grouped = df.groupby("ticker", group_keys=False)
    df["ret_3"] = grouped["close_px"].pct_change(3)
    df["ret_5"] = grouped["close_px"].pct_change(5)
    df["ret_10"] = grouped["close_px"].pct_change(10)
    df["ret_20"] = grouped["close_px"].pct_change(20)
    df["ret_60"] = grouped["close_px"].pct_change(60)
    df["daily_ret"] = grouped["close_px"].pct_change()
    df["vol_20"] = grouped["daily_ret"].rolling(20).std().reset_index(level=0, drop=True)
    df["vol_60"] = grouped["daily_ret"].rolling(60).std().reset_index(level=0, drop=True)
    df["turnover_m"] = df["close_px"] * df["volume"] / 1_000_000.0
    df["avg_turnover_20m"] = grouped["turnover_m"].rolling(20).mean().reset_index(level=0, drop=True)
    df["vol_ratio"] = df["volume"] / df["vol_ma20"].replace(0, np.nan)
    df["price_vs_ma20"] = df["close_px"] / df["ma20"].replace(0, np.nan) - 1.0
    df["price_vs_ma60"] = df["close_px"] / df["ma60"].replace(0, np.nan) - 1.0
    df["close_to_high_60"] = (
        df["close_px"]
        / grouped["high_px"].rolling(60).max().reset_index(level=0, drop=True).replace(0, np.nan)
        - 1.0
    )
    df["bb_position"] = (df["close_px"] - df["bb_low"]) / df["close_px"].replace(0, np.nan)
    df["inst_total"] = df[["foreign_bs", "trust_bs", "dealer_bs"]].fillna(0.0).sum(axis=1)
    df["inst_5_norm"] = (
        grouped["inst_total"].rolling(5).sum().reset_index(level=0, drop=True)
        / grouped["volume"].rolling(20).sum().reset_index(level=0, drop=True).replace(0, np.nan)
    )
    df = _attach_tdcc_features(df)
    grouped = df.groupby("ticker", group_keys=False)
    df["entry_open"] = grouped["open_px"].shift(-1)
    df["next_date"] = grouped["dt"].shift(-1)
    for holding_days in sorted({spec.holding_days for spec in AGENTS}):
        df[f"exit_close_{holding_days}"] = grouped["close_px"].shift(-holding_days)
        df[f"future_return_{holding_days}"] = (
            df[f"exit_close_{holding_days}"] / df["entry_open"].replace(0, np.nan)
            - 1.0
            - ROUND_TRIP_COST
        )
    return df.replace([np.inf, -np.inf], np.nan)


def _base_universe(df: pd.DataFrame, spec: StrategySpec) -> pd.DataFrame:
    return df[
        (df["avg_turnover_20m"] >= spec.min_turnover_m)
        & (df["close_px"] >= spec.min_price)
        & (df["close_px"] <= spec.max_price)
        & (df["volume"] >= 100_000)
    ].copy()


def score_agent_candidates(df: pd.DataFrame, spec: StrategySpec) -> pd.DataFrame:
    candidates = _base_universe(df, spec)
    if candidates.empty:
        return candidates.assign(score=pd.Series(dtype="float64"), reason=pd.Series(dtype="object"))

    if spec.agent_id == "tquant_vam_momentum":
        score = (
            candidates["ret_20"] / (candidates["vol_20"] + 0.005)
            + 0.50 * candidates["price_vs_ma60"]
            + 0.20 * candidates["inst_5_norm"]
        )
        mask = (candidates["price_vs_ma20"] > -0.08) & (candidates["ret_20"] > -0.25)
        reason = "vol-adjusted 20D momentum + MA60 + institutional support"
    elif spec.agent_id == "dual_ma_trend":
        score = (
            2.00 * candidates["price_vs_ma20"]
            + candidates["price_vs_ma60"]
            + 0.50 * candidates["ret_60"]
            + 0.20 * candidates["macd_hist"] / candidates["close_px"].replace(0, np.nan)
        )
        mask = (candidates["price_vs_ma20"] > -0.08) & (candidates["ret_20"] > -0.25)
        reason = "MA20/MA60 alignment and MACD persistence"
    elif spec.agent_id == "institutional_flow":
        score = 2.00 * candidates["inst_5_norm"] + 0.80 * candidates["ret_20"] + 0.20 * candidates["price_vs_ma20"]
        mask = (candidates["price_vs_ma20"] > -0.08) & (candidates["ret_20"] > -0.25)
        reason = "foreign/trust/dealer accumulation with trend intact"
    elif spec.agent_id == "low_vol_quality_proxy":
        score = (
            candidates["ret_20"] / (candidates["vol_60"] + 0.010)
            - 0.80 * candidates["vol_60"]
            + 0.50 * candidates["price_vs_ma60"]
        )
        mask = (candidates["price_vs_ma20"] > -0.08) & (candidates["ret_20"] > -0.25)
        reason = "positive momentum per realized volatility"
    elif spec.agent_id == "volume_breakout":
        score = (
            candidates["close_to_high_60"]
            + 0.50 * candidates["vol_ratio"]
            + 0.40 * candidates["ret_20"]
            + 0.20 * candidates["inst_5_norm"]
        )
        mask = (candidates["price_vs_ma20"] > -0.08) & (candidates["ret_20"] > -0.25)
        reason = "near 60D high with volume expansion"
    elif spec.agent_id == "risk_parity_momentum":
        score = (
            candidates["ret_60"] / (candidates["vol_60"] + 0.010)
            + 0.50 * candidates["ret_20"] / (candidates["vol_20"] + 0.010)
        )
        mask = (candidates["price_vs_ma20"] > -0.08) & (candidates["ret_20"] > -0.25)
        reason = "return per volatility across 20D and 60D horizons"
    elif spec.agent_id == "ml_edge_proxy":
        score = (
            0.35 * candidates["ret_20"] / (candidates["vol_20"] + 0.010)
            + 0.25 * candidates["inst_5_norm"]
            + 0.20 * candidates["price_vs_ma60"]
            + 0.20 * candidates["vol_ratio"]
        )
        mask = (candidates["price_vs_ma20"] > -0.08) & (candidates["ret_20"] > -0.25)
        reason = "interpretable blend of momentum, flow, volatility and volume"
    elif spec.agent_id == "vam_fast_5d":
        score = (
            candidates["ret_10"] / (candidates["vol_20"] + 0.005)
            + 0.35 * candidates["price_vs_ma20"]
            + 0.20 * candidates["inst_5_norm"]
        )
        mask = (candidates["ret_10"] > -0.12) & (candidates["price_vs_ma20"] > -0.06)
        reason = "fast volatility-adjusted 10D momentum"
    elif spec.agent_id == "vam_slow_20d":
        score = (
            candidates["ret_60"] / (candidates["vol_60"] + 0.010)
            + 0.25 * candidates["ret_20"] / (candidates["vol_20"] + 0.010)
            + 0.20 * candidates["price_vs_ma60"]
        )
        mask = (candidates["ret_20"] > -0.18) & (candidates["price_vs_ma60"] > -0.12)
        reason = "slow volatility-adjusted 60D momentum"
    elif spec.agent_id == "ma_fast_trend":
        score = (
            1.50 * candidates["price_vs_ma20"]
            + 0.80 * candidates["ret_20"]
            + 0.30 * candidates["ret_5"]
            + 0.10 * candidates["vol_ratio"]
        )
        mask = (candidates["price_vs_ma20"] > 0.0) & (candidates["ret_20"] > -0.05)
        reason = "MA20 trend and short acceleration"
    elif spec.agent_id == "ma_slow_trend":
        score = 2.00 * candidates["price_vs_ma60"] + candidates["ret_60"] + 0.50 * candidates["ret_20"]
        mask = (candidates["price_vs_ma60"] > -0.03) & (candidates["ret_60"] > 0.0)
        reason = "MA60 trend persistence"
    elif spec.agent_id == "macd_acceleration":
        score = (
            candidates["macd_hist"] / candidates["close_px"].replace(0, np.nan)
            + 0.40 * candidates["ret_10"]
            + 0.20 * candidates["vol_ratio"]
        )
        mask = (candidates["macd_hist"] > 0.0) & (candidates["ret_10"] > -0.08)
        reason = "MACD histogram acceleration"
    elif spec.agent_id == "foreign_flow_rider":
        score = (
            candidates["foreign_bs"].fillna(0.0) / candidates["volume"].replace(0, np.nan)
            + candidates["inst_5_norm"]
            + 0.40 * candidates["ret_20"]
        )
        mask = (candidates["price_vs_ma20"] > -0.08) & (candidates["ret_20"] > -0.20)
        reason = "foreign net-buy pressure with trend intact"
    elif spec.agent_id == "trust_flow_rider":
        score = (
            candidates["trust_bs"].fillna(0.0) / candidates["volume"].replace(0, np.nan)
            + 0.50 * candidates["inst_5_norm"]
            + 0.30 * candidates["ret_20"]
        )
        mask = (candidates["price_vs_ma20"] > -0.08) & (candidates["ret_20"] > -0.20)
        reason = "investment trust net-buy pressure"
    elif spec.agent_id == "dealer_flow_swing":
        score = (
            candidates["dealer_bs"].fillna(0.0) / candidates["volume"].replace(0, np.nan)
            + 0.30 * candidates["inst_5_norm"]
            + 0.20 * candidates["ret_10"]
        )
        mask = (candidates["price_vs_ma20"] > -0.10) & (candidates["ret_10"] > -0.15)
        reason = "dealer flow short swing"
    elif spec.agent_id == "volume_surge_swing":
        score = (
            candidates["vol_ratio"]
            + 0.50 * candidates["ret_5"]
            + 0.30 * candidates["ret_20"]
            + 0.20 * candidates["inst_5_norm"]
        )
        mask = (candidates["vol_ratio"] > 1.20) & (candidates["ret_5"] > -0.08)
        reason = "volume surge with short strength"
    elif spec.agent_id == "quiet_breakout":
        score = candidates["close_to_high_60"] - 0.60 * candidates["vol_20"] + 0.50 * candidates["ret_20"]
        mask = (candidates["close_to_high_60"] > -0.08) & (candidates["ret_20"] > 0.0)
        reason = "near-high breakout with lower volatility"
    elif spec.agent_id == "high_beta_momentum":
        score = (
            candidates["ret_20"] / (candidates["vol_20"] + 0.005)
            + candidates["ret_60"] / (candidates["vol_60"] + 0.010)
            + 0.50 * candidates["vol_ratio"]
        )
        mask = (candidates["ret_20"] > 0.0) & (candidates["vol_20"] > 0.015)
        reason = "aggressive high-volatility momentum"
    elif spec.agent_id == "low_beta_momentum":
        score = (
            candidates["ret_20"] / (candidates["vol_60"] + 0.010)
            - candidates["vol_60"]
            + 0.30 * candidates["price_vs_ma60"]
        )
        mask = (candidates["ret_20"] > 0.0) & (candidates["vol_60"] < 0.04)
        reason = "lower-volatility positive momentum"
    elif spec.agent_id == "gap_safe_momentum":
        score = (
            candidates["ret_20"] / (candidates["vol_20"] + 0.010)
            + 0.50 * candidates["price_vs_ma20"]
            - 0.40 * candidates["vol_ratio"]
        )
        mask = (candidates["ret_20"] > 0.0) & (candidates["vol_ratio"] < 3.0)
        reason = "momentum with volume-spike risk damped"
    elif spec.agent_id == "atr_breakout":
        score = (
            candidates["ret_20"] / (candidates["atr14"] / candidates["close_px"].replace(0, np.nan) + 0.010)
            + 0.30 * candidates["close_to_high_60"]
        )
        mask = (candidates["ret_20"] > 0.0) & (candidates["close_to_high_60"] > -0.12)
        reason = "ATR-normalized breakout momentum"
    elif spec.agent_id == "bb_upper_trend":
        score = (
            candidates["close_px"] / candidates["bb_up"].replace(0, np.nan)
            - 1.0
            + 0.80 * candidates["ret_20"]
            + 0.20 * candidates["vol_ratio"]
        )
        mask = (candidates["ret_20"] > 0.0) & (candidates["price_vs_ma20"] > 0.0)
        reason = "Bollinger upper-band continuation"
    elif spec.agent_id == "rsi_strength":
        score = (
            -((candidates["rsi14"] - 62.0).abs() / 50.0)
            + 0.50 * candidates["ret_20"]
            + 0.20 * candidates["price_vs_ma20"]
        )
        mask = candidates["rsi14"].between(50.0, 75.0) & (candidates["ret_20"] > 0.0)
        reason = "RSI strength, not oversold reversion"
    elif spec.agent_id == "kd_strength":
        score = (
            -((candidates["k"] - candidates["d"]).abs() / 50.0)
            + 0.50 * candidates["ret_20"]
            + 0.20 * candidates["price_vs_ma20"]
        )
        mask = (candidates["k"] > candidates["d"]) & (candidates["ret_20"] > 0.0)
        reason = "KD positive structure"
    elif spec.agent_id == "defensive_flow":
        score = candidates["inst_5_norm"] - candidates["vol_20"] + 0.20 * candidates["ret_20"]
        mask = (candidates["vol_20"] < 0.04) & (candidates["price_vs_ma20"] > -0.05)
        reason = "low volatility with institutional support"
    elif spec.agent_id == "short_swing_momentum":
        score = (
            candidates["ret_5"] / (candidates["vol_20"] + 0.010)
            + 0.20 * candidates["vol_ratio"]
            + 0.20 * candidates["inst_5_norm"]
        )
        mask = (candidates["ret_5"] > 0.0) & (candidates["price_vs_ma20"] > -0.05)
        reason = "5D swing momentum with volume and flow support"
    elif spec.agent_id == "long_compounder":
        score = (
            candidates["ret_60"] / (candidates["vol_60"] + 0.010)
            + 0.50 * candidates["price_vs_ma60"]
            + 0.20 * candidates["inst_5_norm"]
        )
        mask = (candidates["ret_60"] > 0.0) & (candidates["price_vs_ma60"] > -0.04)
        reason = "60D compounder trend"
    elif spec.agent_id == "contrarian_reversal":
        score = (
            -candidates["ret_20"]
            - candidates["price_vs_ma20"]
            - ((candidates["rsi14"] - 30.0).abs() / 50.0)
        )
        mask = (candidates["rsi14"] < 45.0) & (candidates["price_vs_ma20"] < 0.02)
        reason = "contrarian negative-momentum reversal"
    elif spec.agent_id == "consensus_pullback_addon":
        candidates = candidates.set_index("ticker", drop=False)
        candidates["meta_votes"] = 0.0
        candidates["meta_rank_score"] = 0.0
        candidates["meta_best_rank"] = np.nan
        base_specs = [item for item in AGENTS if item.agent_id != spec.agent_id]
        for base_spec in base_specs:
            base_scored = score_agent_candidates(df, base_spec).head(30)
            if base_scored.empty:
                continue
            rank_denominator = max(1, len(base_scored))
            for rank_index, (_, base_row) in enumerate(base_scored.iterrows(), start=1):
                ticker = str(base_row["ticker"]).zfill(4)
                if ticker not in candidates.index:
                    continue
                candidates.loc[ticker, "meta_votes"] += 1.0
                candidates.loc[ticker, "meta_rank_score"] += (rank_denominator - rank_index + 1) / rank_denominator
                previous_best = candidates.loc[ticker, "meta_best_rank"]
                candidates.loc[ticker, "meta_best_rank"] = (
                    rank_index if pd.isna(previous_best) else min(float(previous_best), float(rank_index))
                )
        candidates = candidates.reset_index(drop=True)
        whale_pct_chg = _numeric_series(candidates, "whale_pct_chg")
        retail_pct_chg = _numeric_series(candidates, "retail_pct_chg")
        whale_trend_4w = _numeric_series(candidates, "whale_trend_4w")
        whale_trend_8w = _numeric_series(candidates, "whale_trend_8w")
        whale_acc_weeks = _numeric_series(candidates, "whale_acc_weeks")
        whale_retail_diverge = _numeric_series(candidates, "whale_retail_diverge")
        tdcc_vote = (
            (
                (whale_trend_4w > 0.0)
                & (whale_retail_diverge > 0.0)
                & (whale_pct_chg > 0.0)
            )
            | (
                (whale_acc_weeks >= 2.0)
                & (retail_pct_chg <= 0.0)
                & ((whale_trend_4w > 0.0) | (whale_trend_8w > 0.0))
            )
        ).astype(float)
        tdcc_signal_strength = (
            0.35 * whale_retail_diverge.clip(-2.0, 2.0)
            + 0.25 * whale_trend_4w.clip(-1.5, 1.5)
            + 0.20 * whale_trend_8w.clip(-1.0, 1.0)
            + 0.15 * (whale_acc_weeks.clip(0.0, 4.0) / 4.0)
            - 0.15 * retail_pct_chg.clip(-2.0, 2.0)
        )
        candidates["tdcc_votes"] = tdcc_vote
        candidates["tdcc_signal_strength"] = tdcc_signal_strength
        candidates["consensus_total_votes"] = candidates["meta_votes"] + candidates["tdcc_votes"]
        score = (
            1.80 * candidates["meta_votes"]
            + 1.10 * candidates["tdcc_votes"]
            + candidates["meta_rank_score"]
            + 0.40 * candidates["tdcc_signal_strength"]
            + 0.35 * candidates["ret_20"] / (candidates["vol_20"] + 0.015)
            + 0.25 * candidates["ret_60"] / (candidates["vol_60"] + 0.015)
            + 0.20 * candidates["inst_5_norm"]
            - 0.20 * candidates["vol_20"]
        )
        mask = (
            (
                (candidates["meta_votes"] >= 3.0)
                | ((candidates["meta_votes"] >= 2.0) & (candidates["tdcc_votes"] >= 1.0))
            )
            & (candidates["price_vs_ma60"] > -0.050)
            & (candidates["ret_20"] > -0.050)
            & (candidates["vol_20"] < 0.070)
        )
        reason = pd.Series(
            [
                (
                    "cross-agent meta consensus + TDCC vote: "
                    f"model_votes={int(meta_votes)}, tdcc_votes={int(tdcc_votes)}, "
                    f"total_votes={total_votes:.0f}; "
                    f"whale_retail_diverge={diverge:.2f}pp, whale_trend_4w={trend_4w:.2f}pp/week"
                )
                for meta_votes, tdcc_votes, total_votes, diverge, trend_4w in zip(
                    candidates["meta_votes"],
                    candidates["tdcc_votes"],
                    candidates["consensus_total_votes"],
                    whale_retail_diverge,
                    whale_trend_4w,
                    strict=True,
                )
            ],
            index=candidates.index,
        )
    elif spec.agent_id == "mean_reversion_rsi":
        score = (
            -((candidates["rsi14"].fillna(50.0) - 35.0).abs() / 50.0)
            - candidates["price_vs_ma20"]
            + (candidates["rsi14"] < 45.0).astype(float) * 0.50
        )
        mask = (candidates["rsi14"] < 45.0) & (candidates["price_vs_ma20"] < 0.02)
        reason = "oversold RSI and below-MA20 rebound attempt"
    else:
        score = pd.Series(np.nan, index=candidates.index)
        mask = pd.Series(False, index=candidates.index)
        reason = "unknown"

    out = candidates.assign(score=score, reason=reason)
    return out.loc[mask].dropna(subset=["score"]).sort_values("score", ascending=False)


def _max_drawdown_pct(equity_values: list[float]) -> float:
    if not equity_values:
        return 0.0
    arr = np.asarray(equity_values, dtype="float64")
    peaks = np.maximum.accumulate(arr)
    drawdowns = arr / np.where(peaks == 0, np.nan, peaks) - 1.0
    return float(np.nanmin(drawdowns) * 100.0)


def _admission_verdict(total_return_pct: float, avg_trade_return_pct: float, trade_count: int, max_drawdown_pct: float) -> tuple[str, str]:
    if trade_count < 80:
        return "BENCH_FAILED", "trade_count below 80; sample too small"
    if total_return_pct <= 5.0:
        return "BENCH_FAILED", "total return did not clear +5% admission floor"
    if avg_trade_return_pct <= 0.0:
        return "BENCH_FAILED", "average trade return is not positive"
    if max_drawdown_pct <= -65.0:
        return "BENCH_FAILED", "max drawdown worse than -65%"
    return "ADMITTED", "backtest profitable with adequate sample and drawdown control"


def _exit_policy_thresholds(policy: str) -> dict[str, float]:
    defaults = {
        "take_profit": 0.18,
        "stop_loss": -0.10,
        "trail_giveback": 0.10,
        "min_hold": 2,
    }
    overrides = {
        "fast_swing": {"take_profit": 0.08, "stop_loss": -0.045, "trail_giveback": 0.04, "min_hold": 1},
        "momentum_swing": {"take_profit": 0.14, "stop_loss": -0.075, "trail_giveback": 0.07, "min_hold": 2},
        "ml_edge_swing": {"take_profit": 0.15, "stop_loss": -0.085, "trail_giveback": 0.08, "min_hold": 2},
        "trend_trailing": {"take_profit": 0.30, "stop_loss": -0.12, "trail_giveback": 0.09, "min_hold": 4},
        "breakout_trailing": {"take_profit": 0.20, "stop_loss": -0.075, "trail_giveback": 0.065, "min_hold": 2},
        "flow_decay": {"take_profit": 0.16, "stop_loss": -0.08, "trail_giveback": 0.08, "min_hold": 2},
        "risk_control": {"take_profit": 0.12, "stop_loss": -0.055, "trail_giveback": 0.055, "min_hold": 3},
        "aggressive_momentum": {"take_profit": 0.24, "stop_loss": -0.13, "trail_giveback": 0.11, "min_hold": 2},
        "oscillator_strength": {"take_profit": 0.10, "stop_loss": -0.06, "trail_giveback": 0.05, "min_hold": 2},
        "oscillator_rebound": {"take_profit": 0.07, "stop_loss": -0.05, "trail_giveback": 0.04, "min_hold": 1},
        "meta_consensus": {"take_profit": 0.115, "stop_loss": -0.060, "trail_giveback": 0.055, "min_hold": 2},
    }
    return {**defaults, **overrides.get(policy, {})}


def _exit_decision(
    *,
    spec: StrategySpec,
    elapsed_days: int,
    entry_price: float,
    current_price: float,
    high_water_price: float,
    row: pd.Series | None = None,
) -> tuple[bool, str]:
    if elapsed_days >= spec.holding_days:
        return True, "max_hold"
    if entry_price <= 0 or current_price <= 0:
        return False, "hold"

    thresholds = _exit_policy_thresholds(spec.exit_policy)
    trade_return = current_price / entry_price - 1.0
    high_return = high_water_price / entry_price - 1.0 if high_water_price > 0 else trade_return
    giveback = high_return - trade_return
    min_hold = int(thresholds["min_hold"])

    if trade_return <= thresholds["stop_loss"]:
        return True, f"{spec.exit_policy}:stop_loss"
    if elapsed_days >= min_hold and trade_return >= thresholds["take_profit"]:
        return True, f"{spec.exit_policy}:take_profit"
    if elapsed_days >= min_hold and high_return > 0 and giveback >= thresholds["trail_giveback"]:
        return True, f"{spec.exit_policy}:trailing_giveback"

    if row is not None:
        price_vs_ma20 = _safe_float(row.get("price_vs_ma20"))
        rsi14 = _safe_float(row.get("rsi14"))
        inst_5_norm = _safe_float(row.get("inst_5_norm"))
        macd_hist = _safe_float(row.get("macd_hist"))
        if spec.exit_policy == "trend_trailing" and elapsed_days >= min_hold and price_vs_ma20 is not None and price_vs_ma20 < -0.04:
            return True, "trend_trailing:ma20_break"
        if spec.exit_policy == "breakout_trailing" and elapsed_days >= min_hold and price_vs_ma20 is not None and price_vs_ma20 < -0.03:
            return True, "breakout_trailing:failed_breakout"
        if spec.exit_policy == "flow_decay" and elapsed_days >= min_hold and inst_5_norm is not None and inst_5_norm < -0.002:
            return True, "flow_decay:flow_reversal"
        if spec.exit_policy.startswith("oscillator") and elapsed_days >= min_hold and rsi14 is not None and rsi14 >= 70:
            return True, f"{spec.exit_policy}:oscillator_hot"
        if spec.exit_policy == "fast_swing" and elapsed_days >= min_hold and macd_hist is not None and macd_hist < 0:
            return True, "fast_swing:momentum_fade"
        if spec.exit_policy == "meta_consensus" and elapsed_days >= min_hold:
            if price_vs_ma20 is not None and price_vs_ma20 < -0.055:
                return True, "meta_consensus:ma20_structure_failed"
            if inst_5_norm is not None and inst_5_norm < -0.0025:
                return True, "meta_consensus:flow_reversal"

    return False, "hold"


def _row_lookup(feature_df: pd.DataFrame) -> dict[tuple[str, str], pd.Series]:
    return {
        (str(row["ticker"]), str(pd.Timestamp(row["dt"]).date())): row
        for _, row in feature_df.iterrows()
    }


def backtest_agent(
    feature_df: pd.DataFrame,
    spec: StrategySpec,
    *,
    start_date: str,
    end_date: str,
) -> dict[str, Any]:
    start = pd.Timestamp(start_date)
    end = pd.Timestamp(end_date)
    subset = feature_df[(feature_df["dt"] >= start) & (feature_df["dt"] <= end)].copy()
    dates = sorted(subset["dt"].dropna().unique())
    all_date_values = sorted(feature_df["dt"].dropna().unique())
    date_index = {str(pd.Timestamp(item).date()): index for index, item in enumerate(all_date_values)}
    day_frames: dict[str, pd.DataFrame] = {}
    day_indexed: dict[str, pd.DataFrame] = {}
    for dt_value, frame in subset.groupby("dt", sort=True):
        key = str(pd.Timestamp(dt_value).date())
        day_frames[key] = frame.reset_index(drop=True)
        day_indexed[key] = frame.set_index("ticker", drop=False)
    cash = STARTING_CAPITAL
    equity_curve: list[float] = []
    trade_returns: list[float] = []
    positions: list[dict[str, Any]] = []
    trade_count = 0
    win_count = 0

    for index, signal_date in enumerate(dates):
        signal_date_str = str(pd.Timestamp(signal_date).date())
        day_lookup = day_indexed.get(signal_date_str)
        for position in list(positions):
            ticker = str(position["ticker"])
            if day_lookup is None or ticker not in day_lookup.index:
                continue
            row = day_lookup.loc[ticker]
            current_price = _safe_float(row.get("close_px"))
            if current_price is None:
                continue
            position["last_price"] = current_price
            position["high_water_price"] = max(float(position["high_water_price"]), current_price)
            elapsed = date_index.get(signal_date_str, 0) - date_index.get(str(position["entry_date"]), 0)
            should_exit, _exit_reason = _exit_decision(
                spec=spec,
                elapsed_days=elapsed,
                entry_price=float(position["entry_price"]),
                current_price=current_price,
                high_water_price=float(position["high_water_price"]),
                row=row,
            )
            if not should_exit:
                continue
            shares = float(position["shares"])
            cash += shares * current_price * (1.0 - SELL_COST)
            realized = (current_price * (1.0 - SELL_COST)) / (float(position["entry_price"]) * (1.0 + BUY_COST)) - 1.0
            trade_returns.append(realized)
            trade_count += 1
            win_count += int(realized > 0)
            positions.remove(position)

        if index % spec.rebalance_every != 0:
            market_value = 0.0
            for position in positions:
                ticker = str(position["ticker"])
                row = day_lookup.loc[ticker] if day_lookup is not None and ticker in day_lookup.index else None
                price = _safe_float(row.get("close_px")) if row is not None else _safe_float(position.get("last_price"))
                if price is not None:
                    position["last_price"] = price
                    market_value += float(position["shares"]) * price
            equity_curve.append(cash + market_value)
            continue
        day = day_frames.get(signal_date_str, pd.DataFrame())
        held = {str(position["ticker"]) for position in positions}
        picks = score_agent_candidates(day, spec).head(spec.max_positions)
        if held:
            picks = picks[~picks["ticker"].isin(held)]
        free_slots = max(0, spec.max_positions - len(positions))
        picks = picks.head(free_slots)
        if picks.empty:
            market_value = 0.0
            for position in positions:
                ticker = str(position["ticker"])
                row = day_lookup.loc[ticker] if day_lookup is not None and ticker in day_lookup.index else None
                price = _safe_float(row.get("close_px")) if row is not None else _safe_float(position.get("last_price"))
                if price is not None:
                    position["last_price"] = price
                    market_value += float(position["shares"]) * price
            equity_curve.append(cash + market_value)
            continue

        for decision in _build_sizing_plan(spec, picks, cash):
            pick = decision["row"]
            entry_price = _safe_float(pick.get("entry_open"))
            if entry_price is None or entry_price <= 0:
                continue
            gross_notional = min(float(decision["target_notional"]), cash / (1.0 + BUY_COST))
            lot_plan = _quantize_tw_lot_trade(gross_notional, entry_price)
            shares = float(lot_plan["shares"])
            actual_notional = float(lot_plan["actual_notional"])
            if (
                actual_notional < MIN_TRADE_NOTIONAL
                or shares <= 0
                or cash < actual_notional * (1.0 + BUY_COST)
            ):
                continue
            cash -= actual_notional * (1.0 + BUY_COST)
            # Backtest signal T enters at Open[T+1]; next_date is precomputed per ticker.
            next_date = pick.get("next_date")
            entry_date = str(pd.Timestamp(next_date).date()) if not pd.isna(next_date) else signal_date_str
            positions.append(
                {
                    "ticker": str(pick["ticker"]),
                    "entry_date": entry_date,
                    "entry_price": entry_price,
                    "shares": shares,
                    "board_lots": int(lot_plan["board_lots"]),
                    "odd_lot_shares": int(lot_plan["odd_lot_shares"]),
                    "board_lot_mode": str(lot_plan["board_lot_mode"]),
                    "notional": actual_notional,
                    "target_weight": float(decision["target_weight"]),
                    "sizing_policy": str(decision["sizing_policy"]),
                    "sizing_reason": str(decision["sizing_reason"]),
                    "high_water_price": entry_price,
                    "last_price": entry_price,
                }
            )

        market_value = 0.0
        for position in positions:
            ticker = str(position["ticker"])
            row = day_lookup.loc[ticker] if day_lookup is not None and ticker in day_lookup.index else None
            price = _safe_float(row.get("close_px")) if row is not None else _safe_float(position.get("last_price"))
            if price is not None:
                position["last_price"] = price
                market_value += float(position["shares"]) * price
        equity_curve.append(cash + market_value)

    if trade_returns:
        avg_trade_return_pct = float(np.mean(trade_returns) * 100.0)
        win_rate_pct = float(win_count / trade_count * 100.0) if trade_count else 0.0
    else:
        avg_trade_return_pct = 0.0
        win_rate_pct = 0.0
    ending_equity = equity_curve[-1] if equity_curve else STARTING_CAPITAL
    total_return_pct = float((ending_equity / STARTING_CAPITAL - 1.0) * 100.0)
    max_dd = _max_drawdown_pct(equity_curve)
    status, reason = _admission_verdict(total_return_pct, avg_trade_return_pct, trade_count, max_dd)

    return {
        **asdict(spec),
        "backtest_start": str(start.date()),
        "backtest_end": str(end.date()),
        "starting_capital": STARTING_CAPITAL,
        "ending_equity": round(float(ending_equity), 2),
        "total_return_pct": round(total_return_pct, 4),
        "avg_trade_return_pct": round(avg_trade_return_pct, 4),
        "win_rate_pct": round(win_rate_pct, 4),
        "max_drawdown_pct": round(max_dd, 4),
        "trade_count": int(trade_count),
        "exit_policy": spec.exit_policy,
        "sizing_policy": _sizing_policy_for_spec(spec),
        "sizing_policy_explanation": _sizing_policy_explanation(_sizing_policy_for_spec(spec)),
        "execution_policy": _execution_policy_for_spec(spec),
        "execution_policy_explanation": _execution_policy_explanation(_execution_policy_for_spec(spec)),
        "board_lot_size": BOARD_LOT_SIZE,
        "admission_status": status,
        "admission_reason": reason,
    }


def _connect_arena(db_path: Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_backtests (
            agent_id TEXT PRIMARY KEY,
            payload_json TEXT NOT NULL,
            admission_status TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS arena_daily_equity (
            agent_id TEXT NOT NULL,
            dt TEXT NOT NULL,
            cash REAL NOT NULL,
            market_value REAL NOT NULL,
            equity REAL NOT NULL,
            daily_return_pct REAL,
            open_positions INTEGER NOT NULL,
            closed_positions INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (agent_id, dt)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS arena_positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id TEXT NOT NULL,
            ticker TEXT NOT NULL,
            entry_date TEXT NOT NULL,
            entry_price REAL NOT NULL,
            high_water_price REAL,
            shares REAL NOT NULL,
            board_lots INTEGER,
            odd_lot_shares INTEGER,
            board_lot_mode TEXT,
            notional REAL NOT NULL,
            target_weight REAL,
            sizing_policy TEXT,
            sizing_reason TEXT,
            source_score REAL,
            source_reason TEXT,
            exit_policy TEXT,
            status TEXT NOT NULL,
            exit_date TEXT,
            exit_price REAL,
            exit_reason TEXT,
            realized_return_pct REAL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS arena_latest_candidates (
            agent_id TEXT NOT NULL,
            dt TEXT NOT NULL,
            ticker TEXT NOT NULL,
            score REAL,
            reason TEXT,
            rank INTEGER NOT NULL,
            PRIMARY KEY (agent_id, dt, ticker)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS arena_pending_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id TEXT NOT NULL,
            signal_date TEXT NOT NULL,
            target_trade_date TEXT,
            side TEXT NOT NULL,
            ticker TEXT NOT NULL,
            status TEXT NOT NULL,
            target_notional REAL,
            signal_close REAL,
            max_open_gap_pct REAL,
            signal_score REAL,
            signal_reason TEXT,
            exit_policy TEXT,
            position_id INTEGER,
            created_at TEXT NOT NULL,
            filled_date TEXT,
            open_price REAL,
            fill_price REAL,
            open_gap_pct REAL,
            confirmed_date TEXT,
            confirmed_close REAL,
            intraday_return_pct REAL,
            close_vs_signal_pct REAL,
            skipped_reason TEXT
            ,
            target_weight REAL,
            sizing_policy TEXT,
            sizing_reason TEXT,
            board_lot_mode TEXT
        )
        """
    )
    _ensure_column(conn, "arena_positions", "high_water_price", "REAL")
    _ensure_column(conn, "arena_positions", "exit_policy", "TEXT")
    _ensure_column(conn, "arena_positions", "exit_reason", "TEXT")
    _ensure_column(conn, "arena_positions", "board_lots", "INTEGER")
    _ensure_column(conn, "arena_positions", "odd_lot_shares", "INTEGER")
    _ensure_column(conn, "arena_positions", "board_lot_mode", "TEXT")
    _ensure_column(conn, "arena_positions", "target_weight", "REAL")
    _ensure_column(conn, "arena_positions", "sizing_policy", "TEXT")
    _ensure_column(conn, "arena_positions", "sizing_reason", "TEXT")
    _ensure_column(conn, "arena_pending_orders", "target_trade_date", "TEXT")
    _ensure_column(conn, "arena_pending_orders", "confirmed_date", "TEXT")
    _ensure_column(conn, "arena_pending_orders", "confirmed_close", "REAL")
    _ensure_column(conn, "arena_pending_orders", "intraday_return_pct", "REAL")
    _ensure_column(conn, "arena_pending_orders", "close_vs_signal_pct", "REAL")
    _ensure_column(conn, "arena_pending_orders", "target_weight", "REAL")
    _ensure_column(conn, "arena_pending_orders", "sizing_policy", "TEXT")
    _ensure_column(conn, "arena_pending_orders", "sizing_reason", "TEXT")
    _ensure_column(conn, "arena_pending_orders", "board_lot_mode", "TEXT")
    conn.commit()
    return conn


def _ensure_column(conn: sqlite3.Connection, table_name: str, column_name: str, ddl: str) -> None:
    columns = {
        str(row["name"])
        for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    }
    if column_name not in columns:
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {ddl}")


def _write_backtests(conn: sqlite3.Connection, results: list[dict[str, Any]]) -> None:
    now = _utc_now()
    for result in results:
        conn.execute(
            """
            INSERT INTO agent_backtests(agent_id, payload_json, admission_status, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(agent_id) DO UPDATE SET
                payload_json=excluded.payload_json,
                admission_status=excluded.admission_status,
                updated_at=excluded.updated_at
            """,
            (
                result["agent_id"],
                json.dumps(result, ensure_ascii=False, sort_keys=True),
                result["admission_status"],
                now,
            ),
        )
    conn.commit()


def run_backtests(
    *,
    start_date: str = DEFAULT_BACKTEST_START,
    end_date: str | None = None,
    db_path: Path = DEFAULT_DB_PATH,
    duckdb_path: Path = DEFAULT_DUCKDB_PATH,
) -> dict[str, Any]:
    feature_df = _load_feature_frame(duckdb_path=duckdb_path, start_date="2023-01-01", end_date=end_date)
    latest_date = feature_df["dt"].max()
    if end_date is None:
        latest_exit_horizon = max(spec.holding_days for spec in AGENTS)
        all_dates = sorted(feature_df["dt"].dropna().unique())
        end_index = max(0, len(all_dates) - latest_exit_horizon - 1)
        end_date = str(pd.Timestamp(all_dates[end_index]).date())

    results = [
        backtest_agent(feature_df, spec, start_date=start_date, end_date=end_date)
        for spec in AGENTS
    ]
    conn = _connect_arena(db_path)
    try:
        _write_backtests(conn, results)
    finally:
        conn.close()

    payload = {
        "status": "ok",
        "generated_at": _utc_now(),
        "latest_market_date": str(pd.Timestamp(latest_date).date()),
        "backtest_start": start_date,
        "backtest_end": end_date,
        "starting_capital": STARTING_CAPITAL,
        "target_capital": TARGET_CAPITAL,
        "admitted_count": sum(1 for item in results if item["admission_status"] == "ADMITTED"),
        "agent_count": len(results),
        "results": results,
    }
    BACKTEST_JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
    BACKTEST_JSON_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def _load_backtest_map(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    rows = conn.execute("SELECT agent_id, payload_json FROM agent_backtests").fetchall()
    return {row["agent_id"]: json.loads(row["payload_json"]) for row in rows}


def _source_library_payload(backtests: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    agent_names = {spec.agent_id: spec.name for spec in AGENTS}
    rows: list[dict[str, Any]] = []
    for item in SOURCE_LIBRARY:
        mapped_agents = list(item.mapped_agents)
        admitted = [
            agent_id
            for agent_id in mapped_agents
            if backtests.get(agent_id, {}).get("admission_status") == "ADMITTED"
        ]
        failed = [
            agent_id
            for agent_id in mapped_agents
            if agent_id in backtests and backtests.get(agent_id, {}).get("admission_status") != "ADMITTED"
        ]
        rows.append(
            {
                "repo": item.repo,
                "family": item.family,
                "status": item.status,
                "mapped_agents": mapped_agents,
                "mapped_agent_names": [agent_names.get(agent_id, agent_id) for agent_id in mapped_agents],
                "admitted_agents": admitted,
                "bench_failed_agents": failed,
                "note": item.note,
            }
        )
    return rows


def _last_equity(conn: sqlite3.Connection, agent_id: str) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT * FROM arena_daily_equity
        WHERE agent_id=?
        ORDER BY dt DESC
        LIMIT 1
        """,
        (agent_id,),
    ).fetchone()


def _last_equity_before(conn: sqlite3.Connection, agent_id: str, as_of_date: str) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT * FROM arena_daily_equity
        WHERE agent_id=? AND dt < ?
        ORDER BY dt DESC
        LIMIT 1
        """,
        (agent_id, as_of_date),
    ).fetchone()


def _date_rank_map(feature_df: pd.DataFrame) -> dict[str, dict[str, int]]:
    ranks: dict[str, dict[str, int]] = {}
    for ticker, group in feature_df[["ticker", "dt"]].drop_duplicates().groupby("ticker"):
        dates = [str(pd.Timestamp(item).date()) for item in sorted(group["dt"].unique())]
        ranks[str(ticker)] = {date_value: index for index, date_value in enumerate(dates)}
    return ranks


def _open_positions(conn: sqlite3.Connection, agent_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT * FROM arena_positions
        WHERE agent_id=? AND status='OPEN'
        ORDER BY entry_date, ticker
        """,
        (agent_id,),
    ).fetchall()


def _open_gap_pct(open_price: float | None, signal_close: float | None) -> float | None:
    if open_price is None or signal_close is None or signal_close <= 0:
        return None
    return (open_price / signal_close - 1.0) * 100.0


def _return_pct(later_price: float | None, earlier_price: float | None) -> float | None:
    if later_price is None or earlier_price is None or earlier_price <= 0:
        return None
    return (later_price / earlier_price - 1.0) * 100.0


def _open_fill_price(open_price: float, side: str) -> float:
    slippage = OPEN_FILL_SLIPPAGE_PCT / 100.0
    if side.upper() == "BUY":
        return open_price * (1.0 + slippage)
    return open_price * (1.0 - slippage)


def _pending_order_exists(
    conn: sqlite3.Connection,
    *,
    agent_id: str,
    side: str,
    ticker: str,
    signal_date: str | None = None,
    position_id: int | None = None,
) -> bool:
    if side == "SELL" and position_id is not None:
        return bool(
            conn.execute(
                """
                SELECT 1 FROM arena_pending_orders
                WHERE agent_id=? AND side='SELL' AND position_id=? AND status IN ('PENDING', 'READY_OPEN')
                LIMIT 1
                """,
                (agent_id, position_id),
            ).fetchone()
        )
    return bool(
        conn.execute(
            """
            SELECT 1 FROM arena_pending_orders
            WHERE agent_id=? AND side=? AND ticker=? AND status IN ('PENDING', 'READY_OPEN')
            LIMIT 1
            """,
            (agent_id, side, ticker),
        ).fetchone()
    )


def _pending_buy_count(conn: sqlite3.Connection, agent_id: str) -> int:
    return int(
        conn.execute(
            """
            SELECT COUNT(*) FROM arena_pending_orders
            WHERE agent_id=? AND side='BUY' AND status IN ('PENDING', 'READY_OPEN')
            """,
            (agent_id,),
        ).fetchone()[0]
    )


def _queue_pending_order(
    conn: sqlite3.Connection,
    *,
    agent_id: str,
    signal_date: str,
    side: str,
    ticker: str,
    target_notional: float | None,
    signal_close: float | None,
    signal_score: float | None,
    signal_reason: str,
    exit_policy: str,
    target_weight: float | None = None,
    sizing_policy: str | None = None,
    sizing_reason: str | None = None,
    board_lot_mode: str | None = None,
    target_trade_date: str | None = None,
    position_id: int | None = None,
    max_open_gap_pct: float = MAX_BUY_OPEN_GAP_PCT,
) -> bool:
    if _pending_order_exists(
        conn,
        agent_id=agent_id,
        side=side,
        ticker=ticker,
        signal_date=signal_date,
        position_id=position_id,
    ):
        return False
    conn.execute(
        """
        INSERT INTO arena_pending_orders(
            agent_id, signal_date, target_trade_date, side, ticker, status,
            target_notional, signal_close, max_open_gap_pct, signal_score,
            signal_reason, exit_policy, position_id, target_weight, sizing_policy,
            sizing_reason, board_lot_mode, created_at
        )
        VALUES (?, ?, ?, ?, ?, 'PENDING', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            agent_id,
            signal_date,
            target_trade_date,
            side,
            ticker,
            target_notional,
            signal_close,
            max_open_gap_pct,
            signal_score,
            signal_reason,
            exit_policy,
            position_id,
            target_weight,
            sizing_policy,
            sizing_reason,
            board_lot_mode,
            _utc_now(),
        ),
    )
    return True


def _fill_pending_orders_at_open(
    conn: sqlite3.Connection,
    *,
    spec: StrategySpec,
    as_of_date: str,
    latest_rows: dict[str, pd.Series],
    cash: float,
) -> tuple[float, int, int, int]:
    closed_count = 0
    filled_count = 0
    skipped_count = 0
    execution_policy = _execution_policy_for_spec(spec)
    execution_config = _execution_policy_config(execution_policy)
    min_intraday_return_pct = float(execution_config["min_buy_intraday_return_pct"])
    min_close_vs_signal_pct = float(execution_config["min_buy_close_vs_signal_pct"])
    allow_partial_open_fade = bool(execution_config["allow_partial_open_fade"])
    open_fade_notional_multiplier = float(execution_config["open_fade_notional_multiplier"])
    orders = conn.execute(
        """
        SELECT * FROM arena_pending_orders
        WHERE agent_id=?
          AND (
            (status='PENDING' AND signal_date < ?)
            OR (status='READY_OPEN' AND confirmed_date < ?)
          )
        ORDER BY signal_date, id
        """,
        (spec.agent_id, as_of_date, as_of_date),
    ).fetchall()
    for order in orders:
        ticker = str(order["ticker"])
        row = latest_rows.get(ticker)
        open_price = _safe_float(row.get("open_px")) if row is not None else None
        close_price = _safe_float(row.get("close_px")) if row is not None else None
        side = str(order["side"]).upper()
        order_status = str(order["status"]).upper()
        gap_base = (
            _safe_float(order["confirmed_close"])
            if side == "BUY" and order_status == "READY_OPEN"
            else _safe_float(order["signal_close"])
        )
        signal_close = _safe_float(order["signal_close"])
        gap_pct = _open_gap_pct(open_price, gap_base)
        intraday_return_pct = _return_pct(close_price, open_price)
        close_vs_signal_pct = _return_pct(close_price, signal_close)
        if open_price is None or open_price <= 0:
            conn.execute(
                """
                UPDATE arena_pending_orders
                SET status='SKIPPED', filled_date=?, open_gap_pct=?, skipped_reason=?
                WHERE id=?
                """,
                (as_of_date, gap_pct, "missing_open_price", int(order["id"])),
            )
            skipped_count += 1
            continue

        if side == "BUY":
            max_gap = _safe_float(order["max_open_gap_pct"])
            if gap_pct is not None and max_gap is not None and gap_pct > max_gap:
                conn.execute(
                    """
                    UPDATE arena_pending_orders
                    SET status='SKIPPED', filled_date=?, open_price=?, open_gap_pct=?, skipped_reason=?
                    WHERE id=?
                    """,
                    (as_of_date, open_price, gap_pct, f"open_gap_above_{max_gap:.2f}pp", int(order["id"])),
                )
                skipped_count += 1
                continue

            if order_status == "PENDING":
                if close_price is None or close_price <= 0:
                    conn.execute(
                        """
                        UPDATE arena_pending_orders
                        SET status='SKIPPED', filled_date=?, open_price=?, open_gap_pct=?, skipped_reason=?
                        WHERE id=?
                        """,
                        (as_of_date, open_price, gap_pct, "missing_close_for_follow_through", int(order["id"])),
                    )
                    skipped_count += 1
                    continue
                intraday_failed = intraday_return_pct is not None and intraday_return_pct < min_intraday_return_pct
                signal_failed = close_vs_signal_pct is not None and close_vs_signal_pct < min_close_vs_signal_pct
                allow_partial = (
                    intraday_failed
                    and not signal_failed
                    and allow_partial_open_fade
                    and open_fade_notional_multiplier > 0
                )
                if intraday_failed or signal_failed:
                    if allow_partial:
                        adjusted_notional = max(
                            MIN_TRADE_NOTIONAL,
                            (_safe_float(order["target_notional"]) or 0.0) * open_fade_notional_multiplier,
                        )
                        conn.execute(
                            """
                            UPDATE arena_pending_orders
                            SET status='READY_OPEN', confirmed_date=?, confirmed_close=?,
                                open_price=?, open_gap_pct=?, intraday_return_pct=?,
                                close_vs_signal_pct=?, target_notional=?, skipped_reason=?
                            WHERE id=?
                            """,
                            (
                                as_of_date,
                                close_price,
                                open_price,
                                gap_pct,
                                intraday_return_pct,
                                close_vs_signal_pct,
                                adjusted_notional,
                                f"{execution_policy}:partial_open_fade_{open_fade_notional_multiplier:.2f}x_wait_next_open",
                                int(order["id"]),
                            ),
                        )
                        continue
                    conn.execute(
                        """
                        UPDATE arena_pending_orders
                        SET status='SKIPPED', filled_date=?, open_price=?, open_gap_pct=?,
                            intraday_return_pct=?, close_vs_signal_pct=?, skipped_reason=?
                        WHERE id=?
                        """,
                        (
                            as_of_date,
                            open_price,
                            gap_pct,
                            intraday_return_pct,
                            close_vs_signal_pct,
                            "open_fade_no_follow_through",
                            int(order["id"]),
                        ),
                    )
                    skipped_count += 1
                    continue
                conn.execute(
                    """
                    UPDATE arena_pending_orders
                    SET status='READY_OPEN', confirmed_date=?, confirmed_close=?,
                        open_price=?, open_gap_pct=?, intraday_return_pct=?,
                        close_vs_signal_pct=?, skipped_reason=?
                    WHERE id=?
                    """,
                    (
                        as_of_date,
                        close_price,
                        open_price,
                        gap_pct,
                        intraday_return_pct,
                        close_vs_signal_pct,
                        "follow_through_confirmed_wait_next_open",
                        int(order["id"]),
                    ),
                )
                continue

            target_notional = _safe_float(order["target_notional"]) or 0.0
            gross_notional = min(target_notional, cash / (1.0 + BUY_COST))
            if gross_notional < MIN_TRADE_NOTIONAL:
                conn.execute(
                    """
                    UPDATE arena_pending_orders
                    SET status='SKIPPED', filled_date=?, open_price=?, open_gap_pct=?, skipped_reason=?
                    WHERE id=?
                    """,
                    (as_of_date, open_price, gap_pct, "insufficient_cash", int(order["id"])),
                )
                skipped_count += 1
                continue
            fill_price = _open_fill_price(open_price, side)
            lot_plan = _quantize_tw_lot_trade(gross_notional, fill_price)
            shares = float(lot_plan["shares"])
            actual_notional = float(lot_plan["actual_notional"])
            if shares <= 0 or actual_notional < MIN_TRADE_NOTIONAL:
                conn.execute(
                    """
                    UPDATE arena_pending_orders
                    SET status='SKIPPED', filled_date=?, open_price=?, fill_price=?,
                        open_gap_pct=?, skipped_reason=?, board_lot_mode=?
                    WHERE id=?
                    """,
                    (
                        as_of_date,
                        open_price,
                        fill_price,
                        gap_pct,
                        "insufficient_cash_after_tw_lot_rounding",
                        str(lot_plan["board_lot_mode"]),
                        int(order["id"]),
                    ),
                )
                skipped_count += 1
                continue
            cash -= actual_notional * (1.0 + BUY_COST)
            conn.execute(
                """
                INSERT INTO arena_positions(
                    agent_id, ticker, entry_date, entry_price, high_water_price,
                    shares, board_lots, odd_lot_shares, board_lot_mode, notional,
                    target_weight, sizing_policy, sizing_reason,
                    source_score, source_reason, exit_policy, status
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN')
                """,
                (
                    spec.agent_id,
                    ticker,
                    as_of_date,
                    fill_price,
                    fill_price,
                    shares,
                    int(lot_plan["board_lots"]),
                    int(lot_plan["odd_lot_shares"]),
                    str(lot_plan["board_lot_mode"]),
                    actual_notional,
                    _safe_float(order["target_weight"]),
                    str(order["sizing_policy"] or _sizing_policy_for_spec(spec)),
                    str(order["sizing_reason"] or ""),
                    _safe_float(order["signal_score"]),
                    str(order["signal_reason"] or ""),
                    spec.exit_policy,
                ),
            )
            conn.execute(
                """
                UPDATE arena_pending_orders
                SET status='FILLED', filled_date=?, open_price=?, fill_price=?,
                    open_gap_pct=?, board_lot_mode=?
                WHERE id=?
                """,
                (as_of_date, open_price, fill_price, gap_pct, str(lot_plan["board_lot_mode"]), int(order["id"])),
            )
            filled_count += 1
            continue

        if side == "SELL":
            position_id = int(order["position_id"]) if order["position_id"] is not None else -1
            position = conn.execute(
                "SELECT * FROM arena_positions WHERE id=? AND status='OPEN'",
                (position_id,),
            ).fetchone()
            if not position:
                conn.execute(
                    """
                    UPDATE arena_pending_orders
                    SET status='SKIPPED', filled_date=?, open_price=?, open_gap_pct=?, skipped_reason=?
                    WHERE id=?
                    """,
                    (as_of_date, open_price, gap_pct, "stale_position", int(order["id"])),
                )
                skipped_count += 1
                continue
            fill_price = _open_fill_price(open_price, side)
            shares = float(position["shares"])
            cash += shares * fill_price * (1.0 - SELL_COST)
            realized = (fill_price * (1.0 - SELL_COST)) / (float(position["entry_price"]) * (1.0 + BUY_COST)) - 1.0
            reason = str(order["signal_reason"] or "pending_sell")
            conn.execute(
                """
                UPDATE arena_positions
                SET status='CLOSED', exit_date=?, exit_price=?, exit_reason=?, realized_return_pct=?
                WHERE id=?
                """,
                (as_of_date, fill_price, reason, realized * 100.0, position_id),
            )
            conn.execute(
                """
                UPDATE arena_pending_orders
                SET status='FILLED', filled_date=?, open_price=?, fill_price=?, open_gap_pct=?
                WHERE id=?
                """,
                (as_of_date, open_price, fill_price, gap_pct, int(order["id"])),
            )
            closed_count += 1
            filled_count += 1
    return cash, closed_count, filled_count, skipped_count


def _select_latest_candidates(feature_df: pd.DataFrame, spec: StrategySpec, as_of_date: str, exclude: set[str]) -> pd.DataFrame:
    day = feature_df[feature_df["dt"] == pd.Timestamp(as_of_date)].copy()
    candidates = score_agent_candidates(day, spec)
    if exclude:
        candidates = candidates[~candidates["ticker"].isin(exclude)]
    return candidates.head(spec.max_positions)


def _feature_maps_for_date(feature_df: pd.DataFrame, as_of_date: str) -> tuple[dict[str, float], dict[str, pd.Series]]:
    day = feature_df[feature_df["dt"] == pd.Timestamp(as_of_date)]
    latest_prices = day.set_index("ticker")["close_px"].to_dict()
    latest_rows = {str(row["ticker"]): row for _, row in day.iterrows()}
    return latest_prices, latest_rows


def _run_competition_day_for_agent(
    conn: sqlite3.Connection,
    *,
    feature_df: pd.DataFrame,
    spec: StrategySpec,
    as_of_date: str,
    ranks: dict[str, dict[str, int]],
) -> dict[str, Any]:
    latest_prices, latest_rows = _feature_maps_for_date(feature_df, as_of_date)
    previous = _last_equity_before(conn, spec.agent_id, as_of_date)
    cash = float(previous["cash"]) if previous else STARTING_CAPITAL
    previous_equity = float(previous["equity"]) if previous else STARTING_CAPITAL
    cash, closed_count, filled_order_count, skipped_order_count = _fill_pending_orders_at_open(
        conn,
        spec=spec,
        as_of_date=as_of_date,
        latest_rows=latest_rows,
        cash=cash,
    )

    for position in _open_positions(conn, spec.agent_id):
        ticker = str(position["ticker"])
        price = _safe_float(latest_prices.get(ticker))
        if price is None:
            continue
        entry_date = str(position["entry_date"])
        elapsed = ranks.get(ticker, {}).get(as_of_date, 0) - ranks.get(ticker, {}).get(entry_date, 0)
        high_water = max(float(position["high_water_price"] or position["entry_price"]), price)
        conn.execute(
            "UPDATE arena_positions SET high_water_price=? WHERE id=?",
            (high_water, int(position["id"])),
        )
        should_exit, exit_reason = _exit_decision(
            spec=spec,
            elapsed_days=elapsed,
            entry_price=float(position["entry_price"]),
            current_price=price,
            high_water_price=high_water,
            row=latest_rows.get(ticker),
        )
        if should_exit:
            _queue_pending_order(
                conn,
                agent_id=spec.agent_id,
                signal_date=as_of_date,
                side="SELL",
                ticker=ticker,
                target_notional=float(position["shares"]) * price,
                signal_close=price,
                signal_score=_safe_float(position["source_score"]),
                signal_reason=exit_reason,
                exit_policy=spec.exit_policy,
                position_id=int(position["id"]),
            )

    open_after_sales = _open_positions(conn, spec.agent_id)
    held_tickers = {str(row["ticker"]) for row in open_after_sales}
    pending_buys = _pending_buy_count(conn, spec.agent_id)
    free_slots = max(0, spec.max_positions - len(open_after_sales) - pending_buys)
    execution_policy = _execution_policy_for_spec(spec)
    execution_config = _execution_policy_config(execution_policy)
    latest_candidates = _select_latest_candidates(feature_df, spec, as_of_date, held_tickers)
    conn.execute("DELETE FROM arena_latest_candidates WHERE agent_id=? AND dt=?", (spec.agent_id, as_of_date))
    for rank, (_, row) in enumerate(latest_candidates.iterrows(), start=1):
        conn.execute(
            """
            INSERT OR REPLACE INTO arena_latest_candidates(agent_id, dt, ticker, score, reason, rank)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                spec.agent_id,
                as_of_date,
                str(row["ticker"]),
                _safe_float(row.get("score")),
                str(row.get("reason") or ""),
                rank,
            ),
        )

    if free_slots > 0 and cash > 1_000.0:
        buy_rows = latest_candidates.head(free_slots)
        for decision in _build_sizing_plan(spec, buy_rows, cash):
            row = decision["row"]
            ticker = str(row["ticker"])
            signal_close = _safe_float(row.get("close_px"))
            if signal_close is None or signal_close <= 0:
                continue
            gross_notional = float(decision["target_notional"])
            if gross_notional < 1_000.0:
                continue
            next_date = row.get("next_date")
            target_trade_date = str(pd.Timestamp(next_date).date()) if not pd.isna(next_date) else None
            _queue_pending_order(
                conn,
                agent_id=spec.agent_id,
                signal_date=as_of_date,
                target_trade_date=target_trade_date,
                side="BUY",
                ticker=ticker,
                target_notional=gross_notional,
                signal_close=signal_close,
                signal_score=_safe_float(row.get("score")),
                signal_reason=str(row.get("reason") or ""),
                exit_policy=spec.exit_policy,
                target_weight=float(decision["target_weight"]),
                sizing_policy=str(decision["sizing_policy"]),
                sizing_reason=str(decision["sizing_reason"]),
                board_lot_mode="pending_open_quantize",
                max_open_gap_pct=float(execution_config["max_buy_open_gap_pct"]),
            )

    market_value = 0.0
    for position in _open_positions(conn, spec.agent_id):
        price = _safe_float(latest_prices.get(str(position["ticker"])))
        if price is None:
            continue
        market_value += float(position["shares"]) * price
    equity = cash + market_value
    daily_return_pct = (equity / previous_equity - 1.0) * 100.0 if previous_equity else 0.0
    conn.execute(
        """
        INSERT OR REPLACE INTO arena_daily_equity(
            agent_id, dt, cash, market_value, equity, daily_return_pct,
            open_positions, closed_positions, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            spec.agent_id,
            as_of_date,
            cash,
            market_value,
            equity,
            daily_return_pct,
            len(_open_positions(conn, spec.agent_id)),
            closed_count,
            _utc_now(),
        ),
    )
    return {
        "agent_id": spec.agent_id,
        "dt": as_of_date,
        "cash": cash,
        "market_value": market_value,
        "equity": equity,
        "daily_return_pct": daily_return_pct,
        "open_positions": len(_open_positions(conn, spec.agent_id)),
        "closed_positions": closed_count,
        "filled_orders": filled_order_count,
        "skipped_orders": skipped_order_count,
        "pending_buys": _pending_buy_count(conn, spec.agent_id),
    }


def _load_ticker_feature_frame(
    tickers: set[str],
    *,
    duckdb_path: Path = DEFAULT_DUCKDB_PATH,
    duckdb_conn: Any | None = None,
    lookback_days: int = 180,
) -> pd.DataFrame:
    clean = sorted({str(ticker).zfill(4) for ticker in tickers if str(ticker).strip()})
    if not clean:
        return pd.DataFrame()

    owns_conn = duckdb_conn is None
    snapshot_path: Path | None = None
    if duckdb_conn is not None:
        conn = duckdb_conn
    else:
        conn, snapshot_path = _connect_duckdb_readonly(duckdb_path)
    cursor = conn.cursor()
    try:
        latest_date = cursor.execute("SELECT MAX(Date) FROM daily_k").fetchone()[0]
        if latest_date is None:
            return pd.DataFrame()
        start_date = (pd.Timestamp(latest_date) - pd.Timedelta(days=lookback_days)).date()
        quoted = ", ".join(f"'{ticker}'" for ticker in clean)
        query = f"""
            SELECT
                Ticker AS ticker,
                Date AS dt,
                "Open" AS open_px,
                High AS high_px,
                Low AS low_px,
                "Close" AS close_px,
                Volume AS volume,
                Foreign_BuySell AS foreign_bs,
                Trust_BuySell AS trust_bs,
                Dealer_BuySell AS dealer_bs,
                MA_5 AS ma5,
                MA_20 AS ma20,
                MA_60 AS ma60,
                RSI_14 AS rsi14,
                MACDh_12_26_9 AS macd_hist,
                K AS k,
                D AS d,
                ATR_14 AS atr14,
                VOL_MA_20 AS vol_ma20
            FROM daily_k
            WHERE Ticker IN ({quoted})
              AND Date >= DATE '{start_date}'
            ORDER BY Ticker, Date
        """
        df = cursor.execute(query).fetchdf()
    finally:
        cursor.close()
        if owns_conn:
            _close_duckdb_readonly(conn, snapshot_path)
    if df.empty:
        return df
    df["ticker"] = df["ticker"].astype(str).str.zfill(4)
    df["dt"] = pd.to_datetime(df["dt"])
    for column in df.columns:
        if column not in {"ticker", "dt"}:
            df[column] = pd.to_numeric(df[column], errors="coerce")
    df = df.sort_values(["ticker", "dt"]).reset_index(drop=True)
    grouped = df.groupby("ticker", group_keys=False)
    df["daily_ret"] = grouped["close_px"].pct_change()
    df["ret_5"] = grouped["close_px"].pct_change(5)
    df["ret_20"] = grouped["close_px"].pct_change(20)
    df["ret_60"] = grouped["close_px"].pct_change(60)
    df["vol_20"] = grouped["daily_ret"].rolling(20).std().reset_index(level=0, drop=True)
    df["price_vs_ma20"] = df["close_px"] / df["ma20"].replace(0, np.nan) - 1.0
    df["price_vs_ma60"] = df["close_px"] / df["ma60"].replace(0, np.nan) - 1.0
    df["vol_ratio"] = df["volume"] / df["vol_ma20"].replace(0, np.nan)
    df["inst_total"] = df[["foreign_bs", "trust_bs", "dealer_bs"]].fillna(0.0).sum(axis=1)
    df["inst_5_norm"] = (
        grouped["inst_total"].rolling(5).sum().reset_index(level=0, drop=True)
        / grouped["volume"].rolling(20).sum().reset_index(level=0, drop=True).replace(0, np.nan)
    )
    return df.replace([np.inf, -np.inf], np.nan)


def _clip_score(value: float | None, low: float = 0.0, high: float = 100.0) -> float:
    numeric = _safe_float(value)
    if numeric is None:
        return 50.0
    return float(max(low, min(high, numeric)))


def _dimension_scores(row: pd.Series | None, position: sqlite3.Row | dict[str, Any] | None = None) -> dict[str, float]:
    if row is None:
        return {"technical": 50.0, "flow": 50.0, "risk": 50.0, "strategy": 50.0}
    ret_20 = _safe_float(row.get("ret_20")) or 0.0
    price_vs_ma20 = _safe_float(row.get("price_vs_ma20")) or 0.0
    price_vs_ma60 = _safe_float(row.get("price_vs_ma60")) or 0.0
    rsi14 = _safe_float(row.get("rsi14"))
    inst_5_norm = _safe_float(row.get("inst_5_norm")) or 0.0
    vol_20 = _safe_float(row.get("vol_20")) or 0.0
    vol_ratio = _safe_float(row.get("vol_ratio")) or 1.0
    source_score = _safe_float(position["source_score"] if position is not None else None) if position is not None else None
    entry_price = _safe_float(position["entry_price"] if position is not None else None) if position is not None else None
    close_px = _safe_float(row.get("close_px")) or entry_price
    unrealized = (close_px / entry_price - 1.0) if close_px is not None and entry_price else 0.0

    technical = 50.0 + ret_20 * 180.0 + price_vs_ma20 * 120.0 + price_vs_ma60 * 80.0
    if rsi14 is not None:
        technical += 8.0 if 45.0 <= rsi14 <= 72.0 else -8.0
    flow = 50.0 + inst_5_norm * 8000.0
    risk = 75.0 - vol_20 * 900.0 - max(0.0, vol_ratio - 2.0) * 8.0 + min(0.0, unrealized) * 80.0
    strategy = 50.0 + unrealized * 250.0
    if source_score is not None:
        strategy += min(20.0, max(-20.0, source_score / 2.5))
    return {
        "technical": round(_clip_score(technical), 2),
        "flow": round(_clip_score(flow), 2),
        "risk": round(_clip_score(risk), 2),
        "strategy": round(_clip_score(strategy), 2),
    }


def _exit_policy_explanation(policy: str) -> str:
    thresholds = _exit_policy_thresholds(policy)
    return (
        f"{policy}: stop {thresholds['stop_loss'] * 100:.1f}%, "
        f"take {thresholds['take_profit'] * 100:.1f}%, "
        f"trail giveback {thresholds['trail_giveback'] * 100:.1f}%, "
        f"min hold {int(thresholds['min_hold'])}D; max hold remains agent-specific."
    )


def _exit_policy_explanation_zh(policy: str, holding_days: int | None = None) -> str:
    thresholds = _exit_policy_thresholds(policy)
    max_hold = f"最長持有 {holding_days} 個交易日；" if holding_days else ""
    base = (
        f"{max_hold}硬停損 {thresholds['stop_loss'] * 100:.1f}%；"
        f"達 {thresholds['take_profit'] * 100:.1f}% 可停利；"
        f"高點回落 {thresholds['trail_giveback'] * 100:.1f}% 觸發移動停利；"
        f"最短持有 {int(thresholds['min_hold'])} 日後才啟用停利/移動停利。"
    )
    extras = {
        "trend_trailing": "趨勢破壞加速出場：持有達最短天數後，若跌破 MA20 約 -4% 就賣出。",
        "breakout_trailing": "突破失敗加速出場：持有達最短天數後，若跌破 MA20 約 -3% 就賣出。",
        "flow_decay": "籌碼轉弱加速出場：持有達最短天數後，若近 5 日法人流向轉弱就賣出。",
        "fast_swing": "短線動能轉弱加速出場：持有達最短天數後，若 MACD hist 轉負就賣出。",
        "oscillator_strength": "震盪強勢出場：持有達最短天數後，RSI 過熱到 70 以上就賣出。",
        "oscillator_rebound": "反彈單出場：持有達最短天數後，RSI 過熱到 70 以上就賣出。",
        "meta_consensus": "多模型共識單出場：持有達最短天數後，若 MA20 結構破壞或法人流向轉弱就賣出，不因單一模型硬拗續抱。",
    }
    return f"{base} {extras.get(policy, '')}".strip()


def _exit_reason_zh(reason: str | None) -> str:
    text = str(reason or "hold")
    mapping = {
        "hold": "續抱：尚未觸發出場條件。",
        "max_hold": "時間到期：已達本模型最長持有天數，下一次出場檢查會賣出。",
        "stop_loss": "硬停損：跌幅觸及模型停損線。",
        "take_profit": "停利：漲幅達模型停利門檻。",
        "trailing_giveback": "移動停利：曾經獲利後從高點回落達門檻。",
        "ma20_break": "趨勢破壞：價格跌破 MA20 容忍區。",
        "failed_breakout": "突破失敗：突破型模型確認失敗。",
        "flow_reversal": "籌碼反轉：法人流向轉弱。",
        "oscillator_hot": "震盪過熱：RSI 過熱，反彈/強勢單出場。",
        "momentum_fade": "短線動能轉弱：MACD hist 轉負。",
        "ma20_structure_failed": "結構失敗：價格跌破多模型共識的 MA20 容忍區。",
    }
    for key, label in mapping.items():
        if key in text:
            return label
    return text


def export_agent_detail(
    agent_id: str,
    *,
    db_path: Path = DEFAULT_DB_PATH,
    duckdb_path: Path = DEFAULT_DUCKDB_PATH,
    duckdb_conn: Any | None = None,
) -> dict[str, Any]:
    conn = _connect_arena(db_path)
    try:
        backtests = _load_backtest_map(conn)
        agent = backtests.get(agent_id)
        if not agent:
            return {"status": "not_found", "agent_id": agent_id, "message": "agent backtest metadata not found"}

        equity_rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT dt, cash, market_value, equity, daily_return_pct, open_positions, closed_positions
                FROM arena_daily_equity
                WHERE agent_id=?
                ORDER BY dt
                """,
                (agent_id,),
            ).fetchall()
        ]
        open_rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT *
                FROM arena_positions
                WHERE agent_id=? AND status='OPEN'
                ORDER BY notional DESC, ticker
                """,
                (agent_id,),
            ).fetchall()
        ]
        closed_rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT *
                FROM arena_positions
                WHERE agent_id=? AND status='CLOSED'
                ORDER BY exit_date DESC, ticker
                LIMIT 30
                """,
                (agent_id,),
            ).fetchall()
        ]
        candidate_rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT *
                FROM arena_latest_candidates
                WHERE agent_id=?
                ORDER BY dt DESC, rank
                LIMIT 20
                """,
                (agent_id,),
            ).fetchall()
        ]
    finally:
        conn.close()

    tickers = {str(row["ticker"]) for row in open_rows + closed_rows + candidate_rows}
    feature_df = (
        _load_ticker_feature_frame(tickers, duckdb_path=duckdb_path, duckdb_conn=duckdb_conn)
        if tickers
        else pd.DataFrame()
    )
    latest_date = str(pd.Timestamp(feature_df["dt"].max()).date()) if not feature_df.empty else None
    latest_lookup: dict[str, pd.Series] = {}
    ticker_dates: dict[str, list[str]] = {}
    if not feature_df.empty:
        for ticker, group in feature_df.groupby("ticker"):
            ordered = group.sort_values("dt")
            ticker_dates[ticker] = [str(pd.Timestamp(value).date()) for value in ordered["dt"].tolist()]
            latest_lookup[ticker] = ordered.iloc[-1]

    spec = next((item for item in AGENTS if item.agent_id == agent_id), None)
    agent = dict(agent)
    sizing_policy = str(agent.get("sizing_policy") or _sizing_policy_for_spec(spec))
    execution_policy = str(agent.get("execution_policy") or _execution_policy_for_spec(spec))
    agent["sizing_policy"] = sizing_policy
    agent["sizing_policy_explanation"] = _sizing_policy_explanation(sizing_policy)
    agent["execution_policy"] = execution_policy
    agent["execution_policy_explanation"] = _execution_policy_explanation(execution_policy)
    agent["board_lot_size"] = BOARD_LOT_SIZE
    holding_days = int(agent.get("holding_days") or (spec.holding_days if spec else 0))
    dimensions: list[dict[str, Any]] = []
    enriched_holdings: list[dict[str, Any]] = []
    for row in open_rows:
        ticker = str(row["ticker"])
        latest = latest_lookup.get(ticker)
        latest_price = _safe_float(latest.get("close_px")) if latest is not None else None
        entry_price = _safe_float(row.get("entry_price"))
        unrealized_pct = (latest_price / entry_price - 1.0) * 100.0 if latest_price and entry_price else None
        dates = ticker_dates.get(ticker, [])
        entry_date = str(row.get("entry_date") or "")
        elapsed = max(0, dates.index(entry_date) if entry_date in dates else 0)
        planned_exit_date = dates[min(len(dates) - 1, dates.index(entry_date) + holding_days)] if dates and entry_date in dates else None
        high_water = max(_safe_float(row.get("high_water_price")) or entry_price or 0.0, latest_price or 0.0)
        should_exit, exit_reason = (False, "hold")
        if spec and latest is not None and latest_price and entry_price:
            should_exit, exit_reason = _exit_decision(
                spec=spec,
                elapsed_days=elapsed,
                entry_price=entry_price,
                current_price=latest_price,
                high_water_price=high_water,
                row=latest,
            )
        scores = _dimension_scores(latest, row)
        lot_payload = _lot_summary(row)
        row_sizing_policy = str(row.get("sizing_policy") or sizing_policy)
        row_exit_policy = str(row.get("exit_policy") or agent.get("exit_policy") or "")
        dimensions.append(scores)
        enriched_holdings.append(
            {
                **row,
                **lot_payload,
                "stock_name": _lookup_stock_name(ticker),
                "sizing_policy": row_sizing_policy,
                "sizing_policy_explanation": _sizing_policy_explanation(row_sizing_policy),
                "sizing_reason": row.get("sizing_reason") or "",
                "latest_price": latest_price,
                "latest_date": latest_date,
                "unrealized_return_pct": round(unrealized_pct, 4) if unrealized_pct is not None else None,
                "elapsed_days": elapsed,
                "planned_exit_date": planned_exit_date,
                "exit_check": "exit_now" if should_exit else "hold",
                "exit_check_reason": exit_reason,
                "exit_check_reason_zh": _exit_reason_zh(exit_reason),
                "exit_policy_rule_zh": _exit_policy_explanation_zh(row_exit_policy, holding_days),
                "dimension_scores": scores,
            }
        )

    if dimensions:
        avg_dimensions = {
            key: round(float(np.mean([item[key] for item in dimensions])), 2)
            for key in ["technical", "flow", "risk", "strategy"]
        }
    else:
        avg_dimensions = {"technical": 50.0, "flow": 50.0, "risk": 50.0, "strategy": 50.0}

    return {
        "status": "ok",
        "generated_at": _utc_now(),
        "agent": agent,
        "latest_date": latest_date,
        "exit_policy": agent.get("exit_policy"),
        "exit_policy_explanation": _exit_policy_explanation(str(agent.get("exit_policy") or "")),
        "exit_policy_explanation_zh": _exit_policy_explanation_zh(str(agent.get("exit_policy") or ""), holding_days),
        "sizing_policy": sizing_policy,
        "sizing_policy_explanation": _sizing_policy_explanation(sizing_policy),
        "execution_policy": execution_policy,
        "execution_policy_explanation": _execution_policy_explanation(execution_policy),
        "board_lot_size": BOARD_LOT_SIZE,
        "equity_curve": equity_rows,
        "holdings": enriched_holdings,
        "closed_positions": closed_rows,
        "latest_candidates": candidate_rows,
        "dimension_summary": avg_dimensions,
        "charts": {
            "dimension_labels": ["技術面", "籌碼面", "風險面", "策略/模型面"],
            "dimension_values": [
                avg_dimensions["technical"],
                avg_dimensions["flow"],
                avg_dimensions["risk"],
                avg_dimensions["strategy"],
            ],
            "equity_dates": [row["dt"] for row in equity_rows],
            "equity_values": [round(float(row["equity"]), 2) for row in equity_rows],
            "holding_tickers": [row["ticker"] for row in enriched_holdings],
            "holding_unrealized_pct": [row["unrealized_return_pct"] for row in enriched_holdings],
            "holding_technical_scores": [row["dimension_scores"]["technical"] for row in enriched_holdings],
            "holding_flow_scores": [row["dimension_scores"]["flow"] for row in enriched_holdings],
            "holding_risk_scores": [row["dimension_scores"]["risk"] for row in enriched_holdings],
            "holding_strategy_scores": [row["dimension_scores"]["strategy"] for row in enriched_holdings],
            "allocation_labels": [row["ticker"] for row in enriched_holdings],
            "allocation_values": [round(float(row["notional"]), 2) for row in enriched_holdings],
        },
    }


def run_daily_competition(
    *,
    db_path: Path = DEFAULT_DB_PATH,
    duckdb_path: Path = DEFAULT_DUCKDB_PATH,
    refresh_backtest: bool = False,
) -> dict[str, Any]:
    if refresh_backtest or not db_path.exists():
        run_backtests(db_path=db_path, duckdb_path=duckdb_path)

    feature_df = _load_feature_frame(duckdb_path=duckdb_path, start_date="2023-01-01")
    as_of_date = str(pd.Timestamp(feature_df["dt"].max()).date())
    ranks = _date_rank_map(feature_df)

    conn = _connect_arena(db_path)
    try:
        backtests = _load_backtest_map(conn)
        if not backtests:
            backtest_payload = run_backtests(db_path=db_path, duckdb_path=duckdb_path)
            backtests = {item["agent_id"]: item for item in backtest_payload["results"]}

        for spec in AGENTS:
            backtest = backtests.get(spec.agent_id)
            if not backtest or backtest.get("admission_status") != "ADMITTED":
                continue
            already = conn.execute(
                "SELECT 1 FROM arena_daily_equity WHERE agent_id=? AND dt=?",
                (spec.agent_id, as_of_date),
            ).fetchone()
            if already:
                continue

            _run_competition_day_for_agent(
                conn,
                feature_df=feature_df,
                spec=spec,
                as_of_date=as_of_date,
                ranks=ranks,
            )
        conn.commit()
    finally:
        conn.close()

    return export_latest_status(db_path=db_path)


def _delete_agent_competition_state(conn: sqlite3.Connection, agent_id: str) -> None:
    for table_name in ("arena_daily_equity", "arena_positions", "arena_pending_orders", "arena_latest_candidates"):
        conn.execute(f"DELETE FROM {table_name} WHERE agent_id=?", (agent_id,))


def backfill_agent_competition(
    agent_id: str,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    db_path: Path = DEFAULT_DB_PATH,
    duckdb_path: Path = DEFAULT_DUCKDB_PATH,
    reset_existing: bool = True,
) -> dict[str, Any]:
    spec = next((item for item in AGENTS if item.agent_id == agent_id), None)
    if spec is None:
        return {"status": "not_found", "agent_id": agent_id, "message": "agent is not defined"}

    conn = _connect_arena(db_path)
    try:
        backtests = _load_backtest_map(conn)
        if not backtests:
            backtest_payload = run_backtests(db_path=db_path, duckdb_path=duckdb_path)
            backtests = {item["agent_id"]: item for item in backtest_payload["results"]}
        backtest = backtests.get(agent_id)
        if not backtest or backtest.get("admission_status") != "ADMITTED":
            return {
                "status": "not_admitted",
                "agent_id": agent_id,
                "admission_status": backtest.get("admission_status") if backtest else None,
                "message": "only admitted agents can be backfilled into the live paper contest",
            }

        if start_date is None:
            start_row = conn.execute("SELECT MIN(dt) AS dt FROM arena_daily_equity").fetchone()
            start_date = str(start_row["dt"]) if start_row and start_row["dt"] else None
        if end_date is None:
            end_row = conn.execute("SELECT MAX(dt) AS dt FROM arena_daily_equity").fetchone()
            end_date = str(end_row["dt"]) if end_row and end_row["dt"] else None

        if reset_existing:
            _delete_agent_competition_state(conn, agent_id)
            conn.commit()
    finally:
        conn.close()

    feature_df = _load_feature_frame(duckdb_path=duckdb_path, start_date="2023-01-01")
    latest_feature_date = str(pd.Timestamp(feature_df["dt"].max()).date())
    if start_date is None:
        start_date = latest_feature_date
    if end_date is None:
        end_date = latest_feature_date

    start_ts = pd.Timestamp(start_date)
    end_ts = pd.Timestamp(end_date)
    replay_dates = [
        str(pd.Timestamp(item).date())
        for item in sorted(feature_df["dt"].dropna().unique())
        if start_ts <= pd.Timestamp(item) <= end_ts
    ]

    ranks = _date_rank_map(feature_df)
    day_summaries: list[dict[str, Any]] = []
    conn = _connect_arena(db_path)
    try:
        for as_of_date in replay_dates:
            if not reset_existing:
                already = conn.execute(
                    "SELECT 1 FROM arena_daily_equity WHERE agent_id=? AND dt=?",
                    (agent_id, as_of_date),
                ).fetchone()
                if already:
                    continue
            day_summaries.append(
                _run_competition_day_for_agent(
                    conn,
                    feature_df=feature_df,
                    spec=spec,
                    as_of_date=as_of_date,
                    ranks=ranks,
                )
            )
        conn.commit()
    finally:
        conn.close()

    payload = export_latest_status(db_path=db_path)
    payload["backfill"] = {
        "agent_id": agent_id,
        "start_date": start_date,
        "end_date": end_date,
        "reset_existing": reset_existing,
        "replayed_days": len(day_summaries),
        "days": day_summaries,
    }
    return payload


def export_latest_status(*, db_path: Path = DEFAULT_DB_PATH) -> dict[str, Any]:
    conn = _connect_arena(db_path)
    try:
        backtests = _load_backtest_map(conn)
        admitted_ids = {
            agent_id
            for agent_id, payload in backtests.items()
            if payload.get("admission_status") == "ADMITTED"
        }
        latest_dates = conn.execute("SELECT MAX(dt) AS dt FROM arena_daily_equity").fetchone()
        latest_date = latest_dates["dt"] if latest_dates else None
        standings: list[dict[str, Any]] = []
        if latest_date:
            rows = conn.execute(
                """
                SELECT e.*
                FROM arena_daily_equity e
                WHERE e.dt=?
                ORDER BY e.equity DESC, e.daily_return_pct DESC, e.agent_id ASC
                """,
                (latest_date,),
            ).fetchall()
            admitted_rows = [row for row in rows if row["agent_id"] in admitted_ids]
            daily_rank_rows = sorted(
                admitted_rows,
                key=lambda row: (
                    -float(row["daily_return_pct"] or 0.0),
                    -float(row["equity"]),
                    str(row["agent_id"]),
                ),
            )
            daily_return_rank_by_agent = {
                str(row["agent_id"]): rank for rank, row in enumerate(daily_rank_rows, start=1)
            }
            for competition_rank, row in enumerate(admitted_rows, start=1):
                backtest = backtests.get(row["agent_id"], {})
                spec = next((item for item in AGENTS if item.agent_id == row["agent_id"]), None)
                sizing_policy = str(backtest.get("sizing_policy") or _sizing_policy_for_spec(spec))
                execution_policy = str(backtest.get("execution_policy") or _execution_policy_for_spec(spec))
                progress = (float(row["equity"]) - STARTING_CAPITAL) / (TARGET_CAPITAL - STARTING_CAPITAL)
                standings.append(
                    {
                        "agent_id": row["agent_id"],
                        "rank": competition_rank,
                        "competition_rank": competition_rank,
                        "daily_return_rank": daily_return_rank_by_agent.get(str(row["agent_id"])),
                        "rank_basis": "latest_paper_equity_desc",
                        "daily_return_rank_basis": "latest_daily_return_pct_desc",
                        "name": backtest.get("name", row["agent_id"]),
                        "style": backtest.get("style", "-"),
                        "source_family": backtest.get("source_family", "-"),
                        "equity": round(float(row["equity"]), 2),
                        "cash": round(float(row["cash"]), 2),
                        "market_value": round(float(row["market_value"]), 2),
                        "total_return_pct": round((float(row["equity"]) / STARTING_CAPITAL - 1.0) * 100.0, 4),
                        "daily_return_pct": round(float(row["daily_return_pct"] or 0.0), 4),
                        "progress_to_target_pct": round(max(0.0, progress) * 100.0, 4),
                        "open_positions": int(row["open_positions"]),
                        "closed_positions_today": int(row["closed_positions"]),
                        "backtest_return_pct": backtest.get("total_return_pct"),
                        "backtest_max_drawdown_pct": backtest.get("max_drawdown_pct"),
                        "sizing_policy": sizing_policy,
                        "sizing_policy_explanation": _sizing_policy_explanation(sizing_policy),
                        "execution_policy": execution_policy,
                        "execution_policy_explanation": _execution_policy_explanation(execution_policy),
                    }
                )

        holdings = []
        for item in conn.execute(
            """
            SELECT
                agent_id, ticker, entry_date, entry_price, high_water_price,
                shares, board_lots, odd_lot_shares, board_lot_mode, notional,
                target_weight, sizing_policy, sizing_reason,
                source_score, source_reason, exit_policy
            FROM arena_positions
            WHERE status='OPEN'
            ORDER BY agent_id, notional DESC
            """
        ).fetchall():
            if item["agent_id"] not in admitted_ids:
                continue
            row = dict(item)
            spec = next((agent for agent in AGENTS if agent.agent_id == row["agent_id"]), None)
            row["stock_name"] = _lookup_stock_name(str(row.get("ticker") or ""))
            row["sizing_policy"] = row.get("sizing_policy") or _sizing_policy_for_spec(spec)
            row["sizing_policy_explanation"] = _sizing_policy_explanation(str(row["sizing_policy"]))
            row.update(_lot_summary(row))
            holdings.append(row)
        candidates = [
            dict(item)
            for item in conn.execute(
                """
                SELECT agent_id, dt, ticker, score, reason, rank
                FROM arena_latest_candidates
                ORDER BY agent_id, rank
                """
            ).fetchall()
            if item["agent_id"] in admitted_ids
        ]
        pending_orders = [
            dict(item)
            for item in conn.execute(
                """
                SELECT *
                FROM arena_pending_orders
                WHERE status IN ('PENDING', 'READY_OPEN')
                ORDER BY signal_date DESC, agent_id, side, ticker
                """
            ).fetchall()
            if item["agent_id"] in admitted_ids
        ]
        order_events = [
            dict(item)
            for item in conn.execute(
                """
                SELECT *
                FROM arena_pending_orders
                WHERE status IN ('FILLED', 'SKIPPED')
                ORDER BY COALESCE(filled_date, signal_date) DESC, id DESC
                LIMIT 100
                """
            ).fetchall()
            if item["agent_id"] in admitted_ids
        ]
    finally:
        conn.close()

    backtest_results = []
    for spec in AGENTS:
        item = dict(backtests.get(spec.agent_id, {**asdict(spec), "admission_status": "PENDING"}))
        policy = str(item.get("sizing_policy") or _sizing_policy_for_spec(spec))
        execution_policy = str(item.get("execution_policy") or _execution_policy_for_spec(spec))
        item["sizing_policy"] = policy
        item["sizing_policy_explanation"] = _sizing_policy_explanation(policy)
        item["execution_policy"] = execution_policy
        item["execution_policy_explanation"] = _execution_policy_explanation(execution_policy)
        item["board_lot_size"] = BOARD_LOT_SIZE
        backtest_results.append(item)
    watchlist = sorted(
        [item for item in backtest_results if item.get("admission_status") != "ADMITTED"],
        key=lambda item: _safe_float(item.get("total_return_pct")) if _safe_float(item.get("total_return_pct")) is not None else -9999.0,
        reverse=True,
    )
    source_library = _source_library_payload(backtests)
    source_status_counts: dict[str, int] = {}
    for item in source_library:
        status = str(item.get("status") or "UNKNOWN")
        source_status_counts[status] = source_status_counts.get(status, 0) + 1
    leader = standings[0] if standings else None
    payload = {
        "status": "ok",
        "generated_at": _utc_now(),
        "latest_date": latest_date,
        "starting_capital": STARTING_CAPITAL,
        "target_capital": TARGET_CAPITAL,
        "standing_rank_basis": "latest_paper_equity_desc",
        "daily_return_rank_basis": "latest_daily_return_pct_desc",
        "agent_count": len(AGENTS),
        "admitted_count": sum(1 for item in backtest_results if item.get("admission_status") == "ADMITTED"),
        "watchlist_count": len(watchlist),
        "source_library_count": len(source_library),
        "source_status_counts": source_status_counts,
        "leader": leader,
        "standings": standings,
        "backtests": backtest_results,
        "watchlist": watchlist,
        "holdings": holdings,
        "latest_candidates": candidates,
        "pending_orders": pending_orders,
        "pending_order_count": len(pending_orders),
        "order_events": order_events,
        "order_event_count": len(order_events),
        "source_library": source_library,
        "source_review": [
            {
                "bucket": "Multi-agent LLM decision committee",
                "repos": "TauricResearch/TradingAgents, hsliuping/TradingAgents-CN, HKUDS/Vibe-Trading",
                "arena_use": "Agent persona and review layer only; no LLM output is a trade without backtest-backed rules.",
            },
            {
                "bucket": "Backtesting engines",
                "repos": "QuantConnect/Lean, quantopian/zipline, pyalgotrade, pybroker",
                "arena_use": "No heavy engine dependency in v1; use local no-lookahead event replay for Taiwan data.",
            },
            {
                "bucket": "ML / RL trading",
                "repos": "ML4T, FinRL, tensortrade, Stock-Prediction-Models",
                "arena_use": "Converted to interpretable ML Edge Proxy until same-snapshot A/B and local labels are ready.",
            },
            {
                "bucket": "Portfolio optimization",
                "repos": "Riskfolio-Lib, skfolio, cvxportfolio, deepdow",
                "arena_use": "Risk-adjusted ranking plus agent-specific sizing policies; TW board-lot metadata is recorded for every new fill.",
            },
            {
                "bucket": "Technical / discretionary strategies",
                "repos": "fmzquant/strategies, StockSharp, qtpylib, Stock.Indicators",
                "arena_use": "Mapped into trend, breakout, RSI and volume agents with Taiwan liquidity filters.",
            },
        ],
    }
    LATEST_JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
    LATEST_JSON_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run the multi-agent paper investing arena.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    backtest_parser = subparsers.add_parser("run-backtest")
    backtest_parser.add_argument("--start-date", default=DEFAULT_BACKTEST_START)
    backtest_parser.add_argument("--end-date")
    backtest_parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH))
    backtest_parser.add_argument("--duckdb-path", default=str(DEFAULT_DUCKDB_PATH))

    daily_parser = subparsers.add_parser("run-daily")
    daily_parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH))
    daily_parser.add_argument("--duckdb-path", default=str(DEFAULT_DUCKDB_PATH))
    daily_parser.add_argument("--refresh-backtest", action="store_true")

    backfill_parser = subparsers.add_parser("backfill-agent")
    backfill_parser.add_argument("agent_id")
    backfill_parser.add_argument("--start-date")
    backfill_parser.add_argument("--end-date")
    backfill_parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH))
    backfill_parser.add_argument("--duckdb-path", default=str(DEFAULT_DUCKDB_PATH))
    backfill_parser.add_argument("--keep-existing", action="store_true")

    export_parser = subparsers.add_parser("export")
    export_parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH))

    args = parser.parse_args(argv)
    if args.command == "run-backtest":
        payload = run_backtests(
            start_date=args.start_date,
            end_date=args.end_date,
            db_path=Path(args.db_path),
            duckdb_path=Path(args.duckdb_path),
        )
    elif args.command == "run-daily":
        payload = run_daily_competition(
            db_path=Path(args.db_path),
            duckdb_path=Path(args.duckdb_path),
            refresh_backtest=bool(args.refresh_backtest),
        )
    elif args.command == "backfill-agent":
        payload = backfill_agent_competition(
            args.agent_id,
            start_date=args.start_date,
            end_date=args.end_date,
            db_path=Path(args.db_path),
            duckdb_path=Path(args.duckdb_path),
            reset_existing=not bool(args.keep_existing),
        )
    else:
        payload = export_latest_status(db_path=Path(args.db_path))
    print(json.dumps({k: payload.get(k) for k in ["status", "latest_date", "admitted_count", "agent_count"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
