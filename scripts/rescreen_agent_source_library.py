"""Re-screen GitHub source models against current Taiwan data coverage.

The output is a decision artifact for SA/PM review. It does not add agents to
the live arena by itself; every new candidate still needs deterministic rules,
no-lookahead backtest, and the existing admission gate before Main League entry.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

if sys.platform == "win32":
    import io

    def _ensure_utf8_stream(stream: Any) -> Any:
        try:
            stream.reconfigure(encoding="utf-8")
            return stream
        except Exception:
            pass
        buffer = getattr(stream, "buffer", None)
        if buffer is None:
            return stream
        try:
            return io.TextIOWrapper(buffer, encoding="utf-8")
        except Exception:
            return stream

    sys.stdout = _ensure_utf8_stream(sys.stdout)
    sys.stderr = _ensure_utf8_stream(sys.stderr)

BASE_DIR = Path(os.environ.get("STOCK_BASE_DIR") or Path(__file__).resolve().parents[1])
sys.path.insert(0, str(BASE_DIR))

from scripts.agent_arena import AGENTS, BACKTEST_JSON_PATH, LATEST_JSON_PATH, SOURCE_LIBRARY  # noqa: E402

REPORT_DIR = BASE_DIR / "ml" / "reports"


P0_NEW_CANDIDATES: dict[str, dict[str, str]] = {
    "chrisworsey55/atlas-gic": {
        "candidate_agent": "taifex_macro_regime_beta_overlay",
        "why": "Public TAIFEX put/call, institutional positioning, and futures OI now support a market-regime/beta overlay candidate.",
        "needed_data": "TAIFEX public context + existing daily_k beta/volatility.",
    },
    "tejtw/TEJ_API_Python_VaRStandard_ProgramSample": {
        "candidate_agent": "var_budget_momentum",
        "why": "Existing daily returns plus new TAIFEX regime context can support a VaR-aware risk-budget sleeve.",
        "needed_data": "daily_k returns, TAIFEX regime flags, realized volatility.",
    },
    "tejtw/TEJ_API_Python_Efficient_Frontier_ProgramSample": {
        "candidate_agent": "efficient_frontier_allocator",
        "why": "Current daily_k return covariance is enough for an allocation-style paper agent, as long as unequal weights are explicitly audited.",
        "needed_data": "daily_k returns, sector caps, realized covariance.",
    },
    "tejtw/TEJ_API_Python_EPS": {
        "candidate_agent": "eps_growth_value",
        "why": "Local EPS/financial update path exists; can become a point-in-time EPS growth/value stock-selection candidate after coverage audit.",
        "needed_data": "EPS, PE/PB/dividend yield, point-in-time availability date.",
    },
    "tejtw/TEJ_API_Python_EPS_dividend_check": {
        "candidate_agent": "dividend_eps_quality",
        "why": "New TWSE/TPEx ex-right context plus local EPS/valuation can support dividend-quality and corporate-action-safe selection.",
        "needed_data": "EPS, dividend/ex-right events, valuation, adjusted-return policy.",
    },
}

P1_NEW_CANDIDATES: dict[str, dict[str, str]] = {
    "rkuo2000/AI-stocks": {
        "candidate_agent": "ai_news_technical_risk_veto",
        "why": "Can use existing technical features and official news as advisory/risk-veto inputs, but LLM/news output must not be the sole trade signal.",
        "needed_data": "daily_k indicators, official news, leakage-safe feature mapping.",
    },
    "firmai/financial-machine-learning": {
        "candidate_agent": "afml_triple_barrier_ranker",
        "why": "AFML ideas can be implemented with local labels, but need a leakage-controlled label pipeline before contest entry.",
        "needed_data": "same-snapshot labels, daily_k, public risk context.",
    },
    "robertmartin8/MachineLearningStocks": {
        "candidate_agent": "ml_price_feature_ranker",
        "why": "Existing daily_k plus public context can train an interpretable ML ranker; requires fixed-snapshot A/B before admission.",
        "needed_data": "daily_k, public context features, model artifact governance.",
    },
    "0xfdf/toraniko": {
        "candidate_agent": "factor_diagnostics_ranker",
        "why": "Can become a factor-ranking candidate using current technical/fundamental/context features; not a direct imported model.",
        "needed_data": "factor library mapping, feature IC report, no-lookahead backtest.",
    },
    "jjakimoto/finance_ml": {
        "candidate_agent": "finance_ml_event_label_ranker",
        "why": "Event-label and cross-sectional ML ideas are feasible after local label definitions are locked.",
        "needed_data": "event labels, daily_k, public context, leakage audit.",
    },
    "moyuweiqing/A-stock-prediction-algorithm-based-on-machine-learning": {
        "candidate_agent": "a_share_style_ml_ranker_tw",
        "why": "A-share ML pattern can be adapted to Taiwan features, but China-specific fields need replacement.",
        "needed_data": "Taiwan feature mapping, same-snapshot A/B, backtest.",
    },
    "tejtw/TEJ_API_Python_FinancialdatawithReceivable": {
        "candidate_agent": "receivable_quality_risk",
        "why": "Can become an accounting-quality risk agent if MOPS point-in-time financial coverage is verified.",
        "needed_data": "receivable metrics, revenue, income statement, announcement dates.",
    },
    "tejtw/TEJ_API_Python_FinancialdatawithLoan": {
        "candidate_agent": "leverage_quality_risk",
        "why": "Can become a balance-sheet leverage/loan-risk candidate after local financial coverage audit.",
        "needed_data": "balance sheet fields, cash-flow/debt proxies, announcement dates.",
    },
    "tejtw/TEJ_API_Python_Crossing_price": {
        "candidate_agent": "price_crossing_event_swing",
        "why": "Crossing-price logic is feasible with daily_k; likely overlaps existing MA/breakout agents and should start in Watchlist.",
        "needed_data": "daily_k, volume confirmation, no-lookahead backtest.",
    },
}

ADVISORY_OR_INFRA = {
    "TauricResearch/TradingAgents",
    "hsliuping/TradingAgents-CN",
    "HKUDS/Vibe-Trading",
    "ZhuLinsen/daily_stock_analysis",
    "ErikThiart/ai-stock-dashboard",
    "quantopian/zipline",
    "QuantConnect/Lean",
    "scrtlabs/catalyst",
    "Lumiwealth/lumibot",
    "coding-kitties/investing-algorithm-framework",
    "grananqvist/Awesome-Quant-Machine-Learning-Trading",
    "cbailes/awesome-deep-trading",
    "PacktPublishing/Hands-On-Machine-Learning-for-Algorithmic-Trading",
    "PacktPublishing/Machine-Learning-for-Algorithmic-Trading-Second-Edition_Original",
    "Ceruleanacg/Personae",
    "TraderAlice/OpenAlice",
    "chrisconlan/algorithmic-trading-with-python",
    "nickmccullum/algorithmic-trading-python",
    "JerBouma/AlgorithmicTrading",
    "boyboi86/AFML",
    "pipiku915/FinMem-LLM-StockTrading",
    "tejtw/TEJ_TOOL_API",
    "tejtw/TQuant-manual",
    "tejtw/zipline-tej",
    "tejtw/EN-TEJAPI",
    "tejtw/exchange_calendars",
    "tejtw/pyfolio-tej",
    "tejtw/TEJAPI_Python_Medium_Application",
    "tejtw/TEJAPI_Python_Medium_Quant",
    "tejtw/TEJAPI_Python_Medium_DataAnalysis",
    "tejtw/WelcomeToTejApi",
    "tejtw/TEJAPI_Python_Medium_Rookies",
    "tejtw/TEJ_API_Python_Efficient_Frontier_ProgramSample",
    "tejtw/TEJ_API_Python_VaRStandard_ProgramSample",
}

BLOCKED = {
    "TradeMaster-NTU/TradeMaster": "Still needs a validated Taiwan RL environment; public context helps state features but not the simulator.",
    "Rachnog/Deep-Trading": "Requires sequence-model runtime and retraining validation before contest conversion.",
    "brokermr810/QuantDinger": "Needs a Taiwan data adapter and local execution mapping.",
    "51bitquant/bitquant": "Framework adapter is not present.",
    "dzitkowskik/StockPredictionRNN": "Needs sequence training pipeline, GPU/runtime validation, and leakage audit.",
    "JordiCorbilla/stock-prediction-deep-neural-learning": "Needs local retraining and model governance.",
    "llSourcell/Reinforcement_Learning_for_Stock_Prediction": "Needs a validated Taiwan RL environment.",
    "Quantweb3-com/NexusTrader": "Framework not adapted to the local paper contest.",
    "fulifeng/Temporal_Relational_Stock_Ranking": "Still needs Taiwan relational graph/supply-chain features.",
    "sebastianheinz/stockprediction": "No local validated retraining path yet.",
    "kimber-chen/Tensorflow-for-stock-prediction": "Needs TensorFlow training/runtime validation and leakage audit.",
    "zshicode/Attention-CLX-stock-prediction": "Needs attention-model training/runtime validation and Taiwan feature mapping.",
    "saeed349/Deep-Reinforcement-Learning-in-Trading": "Needs a validated Taiwan RL environment.",
    "CFMTech/Deep-RL-for-Portfolio-Optimization": "Needs portfolio RL simulator and action-space validation.",
    "tejtw/TEJ_API_Python_WarrantTStandard_ProgramSample": "Warrant domain is outside the current stock-only arena.",
    "tejtw/TEJ_API_Python_RealEstateTransfer_ProgramSample": "Real estate data is outside current stock arena scope.",
}


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _backtest_lookup() -> dict[str, dict[str, Any]]:
    latest = _load_json(Path(LATEST_JSON_PATH))
    rows = latest.get("backtests") or []
    if not rows:
        rows = (_load_json(Path(BACKTEST_JSON_PATH)).get("backtests") or [])
    return {str(item.get("agent_id")): item for item in rows if item.get("agent_id")}


def _public_context_summary() -> dict[str, Any]:
    report = _load_json(REPORT_DIR / "public_market_context_latest.json")
    datasets = report.get("datasets") or []
    return {
        "status": report.get("status", "missing"),
        "dataset_count": report.get("dataset_count", 0),
        "ok_count": report.get("ok_count", 0),
        "required_failures": report.get("required_failures", []),
        "available_dataset_ids": [item.get("dataset_id") for item in datasets if item.get("status") == "ok"],
    }


def _classify_source(source: Any, backtests: dict[str, dict[str, Any]]) -> dict[str, Any]:
    mapped = list(source.mapped_agents)
    admitted = [
        agent_id
        for agent_id in mapped
        if backtests.get(agent_id, {}).get("admission_status") == "ADMITTED"
    ]
    bench_failed = [
        agent_id
        for agent_id in mapped
        if backtests.get(agent_id, {}).get("admission_status") == "BENCH_FAILED"
    ]
    item = {
        **asdict(source),
        "mapped_agents": mapped,
        "admitted_agents": admitted,
        "bench_failed_agents": bench_failed,
        "rescreen_bucket": "",
        "candidate_agent": "",
        "decision": "",
        "needed_data": "",
        "why": "",
        "next_action": "",
    }
    if mapped:
        if admitted:
            item.update(
                {
                    "rescreen_bucket": "already_in_main_league_source",
                    "decision": "already represented by admitted agent(s)",
                    "why": f"Mapped agent(s) passed admission: {', '.join(admitted)}.",
                    "next_action": "Keep in Main League; new public context may be tested as optional risk annotation only.",
                }
            )
        else:
            item.update(
                {
                    "rescreen_bucket": "existing_watchlist_source",
                    "decision": "already converted but failed admission",
                    "why": f"Mapped agent(s) exist but failed backtest: {', '.join(bench_failed) or 'none admitted'}.",
                    "next_action": "Keep in Watchlist unless a redesigned rule clears the same admission gate.",
                }
            )
        return item

    if source.repo in P0_NEW_CANDIDATES:
        meta = P0_NEW_CANDIDATES[source.repo]
        item.update(
            {
                "rescreen_bucket": "new_p0_backtest_candidate",
                "candidate_agent": meta["candidate_agent"],
                "decision": "can enter Candidate Lab / Watchlist backtest now",
                "why": meta["why"],
                "needed_data": meta["needed_data"],
                "next_action": "Design deterministic rules and run no-lookahead backtest before Main League.",
            }
        )
    elif source.repo in P1_NEW_CANDIDATES:
        meta = P1_NEW_CANDIDATES[source.repo]
        item.update(
            {
                "rescreen_bucket": "new_p1_design_candidate",
                "candidate_agent": meta["candidate_agent"],
                "decision": "feasible after feature/label design",
                "why": meta["why"],
                "needed_data": meta["needed_data"],
                "next_action": "Write SA method lock, build fixed-snapshot features, then backtest.",
            }
        )
    elif source.repo in BLOCKED:
        item.update(
            {
                "rescreen_bucket": "still_blocked",
                "decision": "do not enter contest yet",
                "why": BLOCKED[source.repo],
                "next_action": "Revisit only after data adapter/runtime/label gap is closed.",
            }
        )
    elif source.repo in ADVISORY_OR_INFRA:
        item.update(
            {
                "rescreen_bucket": "advisory_or_infra_only",
                "decision": "not a direct trading agent",
                "why": "Useful as UI, research, framework, LLM committee, or performance tooling, but not a deterministic stock-selection rule.",
                "next_action": "Use as support layer; do not count as an Arena competitor.",
            }
        )
    else:
        item.update(
            {
                "rescreen_bucket": "still_blocked",
                "decision": "needs manual SA review",
                "why": "No safe deterministic Taiwan-stock mapping was identified in this automated rescreen.",
                "next_action": "Review source-specific data requirements before adding to Candidate Lab.",
            }
        )
    return item


def build_rescreen_report() -> dict[str, Any]:
    backtests = _backtest_lookup()
    rows = [_classify_source(source, backtests) for source in SOURCE_LIBRARY]
    bucket_counts: dict[str, int] = {}
    for row in rows:
        bucket = row["rescreen_bucket"]
        bucket_counts[bucket] = bucket_counts.get(bucket, 0) + 1
    current_main_agents = sorted(
        agent_id
        for agent_id, item in backtests.items()
        if item.get("admission_status") == "ADMITTED"
    )
    current_watchlist_agents = sorted(
        agent_id
        for agent_id, item in backtests.items()
        if item.get("admission_status") != "ADMITTED"
    )
    return {
        "status": "ok",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "scope": "GitHub source library rescreen against current local data + public_market_context",
        "decision_policy": {
            "candidate_lab": "May be implemented as deterministic rules and backtested.",
            "main_league": "Only after the same no-lookahead admission gate passes.",
            "no_direct_llm_trade": "LLM/news/persona sources can advise or veto, but cannot be a standalone trade signal.",
        },
        "public_context": _public_context_summary(),
        "source_count": len(rows),
        "agent_count": len(AGENTS),
        "current_main_league_agents": current_main_agents,
        "current_watchlist_agents": current_watchlist_agents,
        "bucket_counts": bucket_counts,
        "rows": rows,
    }


def _table(rows: list[dict[str, Any]], columns: list[tuple[str, str]]) -> list[str]:
    lines = ["| " + " | ".join(title for title, _ in columns) + " |"]
    lines.append("| " + " | ".join("---" for _ in columns) + " |")
    for row in rows:
        values = []
        for _, key in columns:
            value = row.get(key, "")
            if isinstance(value, list):
                value = ", ".join(str(x) for x in value)
            value = str(value).replace("|", "/").replace("\n", " ")
            values.append(value)
        lines.append("| " + " | ".join(values) + " |")
    return lines


def write_reports(report: dict[str, Any], *, asof: str) -> tuple[Path, Path]:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    json_path = REPORT_DIR / f"agent_source_rescreen_{asof}.json"
    md_path = REPORT_DIR / f"agent_source_rescreen_{asof}.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    rows = report["rows"]
    p0 = [row for row in rows if row["rescreen_bucket"] == "new_p0_backtest_candidate"]
    p1 = [row for row in rows if row["rescreen_bucket"] == "new_p1_design_candidate"]
    main_sources = [row for row in rows if row["rescreen_bucket"] == "already_in_main_league_source"]
    watch_sources = [row for row in rows if row["rescreen_bucket"] == "existing_watchlist_source"]
    blocked = [row for row in rows if row["rescreen_bucket"] == "still_blocked"]
    advisory = [row for row in rows if row["rescreen_bucket"] == "advisory_or_infra_only"]

    lines = [
        f"# Agent Source Library Rescreen ({asof})",
        "",
        "## Verdict",
        "",
        "- Existing Main League remains unchanged: new sources are not auto-promoted.",
        f"- Current admitted agents: {len(report['current_main_league_agents'])}.",
        f"- Existing converted but failed/watchlist agents: {len(report['current_watchlist_agents'])}.",
        f"- New P0 Candidate Lab sources: {len(p0)}.",
        f"- New P1 design candidates: {len(p1)}.",
        f"- Public context health: {report['public_context']['status']} "
        f"({report['public_context']['ok_count']}/{report['public_context']['dataset_count']} OK).",
        "",
        "## Admission Rule",
        "",
        "A source can enter Candidate Lab/Watchlist when it can be mapped to deterministic Taiwan-stock rules using point-in-time data. "
        "It can enter Main League only after the existing no-lookahead admission gate passes.",
        "",
        "## P0 Candidate Lab",
        "",
        *_table(p0, [("Repo", "repo"), ("Candidate Agent", "candidate_agent"), ("Needed Data", "needed_data"), ("Why", "why")]),
        "",
        "## P1 Design Candidates",
        "",
        *_table(p1, [("Repo", "repo"), ("Candidate Agent", "candidate_agent"), ("Needed Data", "needed_data"), ("Why", "why")]),
        "",
        "## Existing Main League Sources",
        "",
        *_table(main_sources, [("Repo", "repo"), ("Admitted Agents", "admitted_agents"), ("Watchlist Agents", "bench_failed_agents")]),
        "",
        "## Existing Watchlist Sources",
        "",
        *_table(watch_sources, [("Repo", "repo"), ("Failed Agents", "bench_failed_agents"), ("Why", "why")]),
        "",
        "## Advisory / Infra Only",
        "",
        *_table(advisory, [("Repo", "repo"), ("Family", "family"), ("Decision", "decision")]),
        "",
        "## Still Blocked",
        "",
        *_table(blocked, [("Repo", "repo"), ("Family", "family"), ("Why", "why")]),
        "",
    ]
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return json_path, md_path


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Re-screen GitHub source models for Agent Arena readiness.")
    parser.add_argument("--asof", default=datetime.now().strftime("%Y%m%d"))
    args = parser.parse_args(argv)
    report = build_rescreen_report()
    json_path, md_path = write_reports(report, asof=args.asof)
    print(
        json.dumps(
            {
                "status": report["status"],
                "source_count": report["source_count"],
                "bucket_counts": report["bucket_counts"],
                "json_path": str(json_path.relative_to(BASE_DIR)),
                "md_path": str(md_path.relative_to(BASE_DIR)),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
