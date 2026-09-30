"""Read-only allocation diagnosis and arithmetic sensitivities in a guarded copy.

No sync, order, source data or portfolio write is performed. Only research
JSON/Markdown reports are written. This is not a historical backtest.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
AS_OF = "2026-09-04"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def clean(value):
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def main():
    assert ROOT == Path(os.environ["STOCK_ACCEPTANCE_ROOT"]).resolve()
    assert Path.cwd().resolve().is_relative_to(ROOT)
    sys.path.insert(0, str(ROOT))
    from scripts.pipeline_acceptance_guard import _INSTALLED
    assert _INSTALLED is not None and _INSTALLED.root == ROOT
    import pandas as pd
    from scripts.update_unified_portfolio import _cap_delta_weight

    report_dir = ROOT / "ml/reports/research/pipeline_acceptance_20260906"
    signals = ROOT / f"ml/models/unified_signals_{AS_OF}.csv"
    database = ROOT / "paper_portfolio_v2_champion.db"
    protected = [database, ROOT / "shadow_portfolio_v2_baseline.db", signals]
    before = {str(p): sha(p) for p in protected}
    frame = pd.read_csv(signals, dtype={"ticker": str})
    assert len(frame) == 1 and str(frame.iloc[0]["ticker"]) == "2603"
    assert set(frame.prediction_date) == {AS_OF}
    signal = frame.iloc[0].to_dict()
    with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        active = [dict(r) for r in conn.execute(
            "SELECT * FROM unified_positions WHERE status IN ('pending','open') ORDER BY target_weight DESC")]
        status_summary = [dict(r) for r in conn.execute(
            "SELECT status,COUNT(*) AS count,SUM(target_weight) AS nominal_weight_sum FROM unified_positions GROUP BY status")]
        date_run = [dict(r) for r in conn.execute(
            "SELECT * FROM unified_runs WHERE prediction_date=?", (AS_OF,))]
        active_run_ids = sorted({int(p["opened_by_run_id"]) for p in active})
        origin_runs = [dict(conn.execute("SELECT * FROM unified_runs WHERE id=?", (rid,)).fetchone())
                       for rid in active_run_ids]
        mark_dates = conn.execute("SELECT MIN(mark_date),MAX(mark_date) FROM unified_marks").fetchone()
    invested = sum(float(p["target_weight"]) for p in active)
    available = max(0.0, 1.0 - invested)
    by_name, by_sector = {}, {}
    for p in active:
        by_name[p["ticker"]] = by_name.get(p["ticker"], 0.0) + float(p["target_weight"])
        sector = p["sector"] or "UNKNOWN"
        by_sector[sector] = by_sector.get(sector, 0.0) + float(p["target_weight"])
    current_weight = _cap_delta_weight(1.0, signal["target_weight_ratio"], existing_weight=0.0)
    assert math.isclose(current_weight, 0.9985, abs_tol=1e-12)
    fresh = json.loads((report_dir / "portfolio_acceptance.json").read_text(encoding="utf-8"))
    assert fresh["status"] == "PASS" and fresh["input"]["sha256"] == sha(signals)
    assert math.isclose(fresh["first_sync"]["cash_deployed"], current_weight, abs_tol=1e-12)
    sensitivity = []
    for label, cap in (("current", None), ("single_name_10pct", 0.10), ("single_name_20pct", 0.20)):
        allocation = current_weight if cap is None else min(current_weight, cap)
        sensitivity.append({"scenario": label, "single_name_cap": cap, "ticker": "2603",
                            "initial_nominal_cash": 1.0, "allocated_pending_weight": allocation,
                            "cash_left_unallocated": 1.0-allocation,
                            "nominal_capital_at_risk_at_minus10pct": allocation*0.10,
                            "nominal_loss_per_TWD_1000000_if_exact_minus10pct_exit": allocation*0.10*1000000})
    cash_release = [{"available_nominal_cash": cash,
                     "new_2603_weight_current_rule": _cap_delta_weight(cash, signal["target_weight_ratio"], 0.0),
                     "interpretation": "Arithmetic only: new unlocked run, no existing 2603, sole positive delta-unit request"}
                    for cash in (0.0, 0.10, 0.20, 0.50, 1.0)]
    references = {
        "AGENTS.md": {"line": 282, "role": "20% Top-N sector-count policy"},
        "ml/thresholds.py": {"line": 94, "role": "sector20%, group15%, expensive-momentum3% constants"},
        "ml/predict.py": {"line": 1636, "role": "sector cap counts names against configured top_n"},
        "scripts/build_unified_signals.py": {"line": 946, "role": "penalty sector-count cap; line1414 units normalization; line2481 risk overlays after gates"},
        "scripts/update_unified_portfolio.py": {"line": 1397, "role": "clip by signal upper bound; line1422 allocator; line1697 active nominal capital"},
        "scripts/order_simulation.py": {"line": 166, "role": "gap/slippage handling means nominal10% is not maximum executable loss"},
        "backend/services/warroom_service.py": {"line": 931, "role": "target_weight also enters cash/equity and position presentation"},
    }
    for name, ref in references.items():
        ref["sha256"] = sha(ROOT / name)
    after = {str(p): sha(p) for p in protected}
    assert before == after, "Read-only source or book changed during diagnosis"
    guard_log = ROOT / f"output/acceptance_guard_{os.getpid()}.jsonl"
    assert not guard_log.read_text(encoding="utf-8").strip()
    output = clean({
        "assessment": "OPEN_ALLOCATION_RISK", "generated_at": datetime.now().isoformat(), "as_of": AS_OF,
        "method": "Read-only copied-ledger and source diagnosis; same-signal arithmetic sensitivity, not backtest",
        "workspace": str(ROOT), "guard_pid": os.getpid(), "guard_denials": 0,
        "input_hashes_unchanged": after, "source_references": references,
        "current_copied_champion": {
            "database": str(database), "active_positions": active, "status_summary": status_summary,
            "active_count": len(active), "nominal_active_weight": invested,
            "nominal_available_cash": available, "by_ticker_nominal_weight": by_name,
            "by_sector_nominal_weight": by_sector,
            "max_single_name_nominal_weight": max(by_name.values(), default=0.0),
            "max_sector_nominal_weight": max(by_sector.values(), default=0.0),
            "nominal_capital_at_risk_if_all_active_positions_exit_exact_minus10pct": invested*0.10,
            "locked_asof_run": date_run, "active_origin_runs": origin_runs,
            "min_max_mark_dates": list(mark_dates),
            "measurement_limit": "Weights are ledger allocations relative to a nominal1 capital budget, not current marked-to-market NAV fractions or a live brokerage account. Pending reserves cash. Realized P&L is not used in allocator available_cash formula.",
        },
        "fresh_same_signal": {"ticker": signal["ticker"], "sector": signal.get("sector"),
            "target_units": signal["target_units"], "target_weight_ratio": signal["target_weight_ratio"],
            "before_macro": signal.get("target_weight_ratio_before_macro_event_cap"),
            "macro_multiplier": signal.get("macro_event_weight_multiplier"),
            "expensive_momentum_risk": signal.get("expensive_momentum_risk"),
            "futures_multiplier": signal.get("futures_settlement_weight_multiplier"),
            "proof": "portfolio_acceptance.json: actual canonical fresh DB pending insert0.9985, no fill"},
        "allocation_formula": {
            "cash": "C=max(0,1-sum(target_weight for pending/open positions))",
            "delta": "d_i=max(signal_target_units_i-existing_target_units_i,0)",
            "raw_new_weight": "a_i=C*d_i/sum(d), if sum(d)>0",
            "final_new_weight_for_positive_signal_cap": "min(a_i,max(0,signal_target_weight_ratio_i-existing_weight_i))",
            "signal_weight_source": "target_units/sum(target_units) over surviving daily signal pool, then conditional risk reductions",
            "nonpositive_or_nonfinite_cap_behavior": "_cap_delta_weight currently returns raw allocation for <=0 or nonfinite cap. Research caps here are positive; do not assume setting zero freezes allocation.",
        },
        "controls": [
            {"control": "sector20% / theme15%", "layer": "candidate name counts", "capital_cap": False,
             "limit": "counts use configured top_n, not final surviving pool or current ledger sectors; unknown themes uncapped"},
            {"control": "generic single-name capital cap", "layer": "allocator", "present": False},
            {"control": "portfolio sector capital cap", "layer": "allocator", "present": False},
            {"control": "portfolio max active slots / fixed minimum denominator", "layer": "allocator", "present": False},
            {"control": "total nominal active capital100%", "layer": "allocator", "present": True,
             "limit": "No diversification or mandatory cash buffer; cash is nominal, not actual NAV cash"},
            {"control": "expensive momentum3%", "layer": "signal upper bound", "present": True,
             "limit": "Only PE>50 AND MA60 deviation>25%; current2603 does not trigger"},
            {"control": "settlement/macro reduction", "layer": "signal multipliers", "present": True,
             "limit": "Conditional, not universal concentration protection; current macro leaves0.9985"},
            {"control": "CAUTION market state", "layer": "candidate ranking and route gate", "present": True,
             "limit": "Limits20D to Top10 and blocksT1; does not impose a total exposure budget"},
        ],
        "trigger_scenarios": [
            "New/empty or correctly recovered empty ledger + unlocked run + sole new candidate + near100% signal cap: may reserve near100% in one name.",
            "Closed/stopped/missed positions leave active sum, releasing nominal cash; next unlocked sparse-signal run can concentrate most released cash in one new name.",
            "Partial cash release means new concentration is limited by that cash and signal cap; no forced sell/rebalance of other open names occurs.",
            "Existing same ticker without delta-unit increase gets no top-up; upgrades can add if delta>0 and cap headroom exists.",
            "Already locked9/4 run is skipped; freed cash does not cause that same date to rerun. Existingcopied9/4 ledger did not buy2603.",
            "Recovery against an empty DB while actual positions still exist would lose cash-reservation context; do not treat empty rebuild as safe live-book recovery.",
        ],
        "sensitivity_fresh_one_candidate": sensitivity, "cash_release_arithmetic": cash_release,
        "stop_assumption": "Nominal capital_at_risk=allocated_weight*10%. Assumes exact entry-to-exit -10%; excludes fees, tax, gap, slippage, liquidity and unfilled-order risks. This is not a loss ceiling, forecast, backtest or guaranteed stop fill.",
        "impact_scope": "Champion and baseline Shadow use the same canonical allocator; existing paper cash/equity/warroom views consume target_weight. This diagnosis does not change rere selection or any trade rule.",
        "sa_recommendation_pending_user_decision": "Add explicit post-gate capital policy at allocator boundary: generic single-name cap, current-portfolio sector cap and retained cash; preserve candidates.10%/20% are comparison points, not approved thresholds. Define existing-book handling separately; no retroactive rebalance is performed.",
    })
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "allocation_risk.json").write_text(json.dumps(output, ensure_ascii=False, indent=2, allow_nan=False)+"\n", encoding="utf-8")
    active_text = "；".join(f"{p['ticker']} {p['sector']}：{p['target_weight']:.2%}，{p['status']}，進場{p['entry_date']}，mark至{p['last_mark_date']}" for p in active)
    lines = ["# Champion 資金集中風險診斷 — 2026-09-06", "",
        "**未解風險：候選檔數上限沒有形成持倉資金上限。已在全新驗收帳本重現單股99.85%待成交配置；既有副本帳本也已存在單股100%名目配置。** 本輪僅讀副本與產生研究報告，未改 production 規則、門檻、訊號或帳本。", "",
        f"既有 Champion 副本：{active_text}。active名目配置={invested:.2%}，可用名目資金={available:.2%}，pending=0。9/4原 run 已鎖定、cash_before=0、new_positions=0；**2603不是既有帳本的100%部位**。帳本 target_weight 是對名目1資金預算的配置，並非券商帳戶或即時市值/NAV占比。", "",
        "## 根因與控制缺口", "",
        "1. surviving unified 名單先依 `target_units / Σtarget_units` 正規化；9/4僅2603、2單位，因此先得到100%，再經macro0.9985折減成99.85%。",
        "2. allocator令 C=1−所有pending/open的target_weight，依新增單位d分配 `C×d/Σd`，再以 `signal.target_weight_ratio−既有同股weight` 截頂。它會把結果寫入持倉target_weight；pending也立即保留資金。此欄不是純展示占比。",
        "3. AGENTS#12的20%位於Top-N候選檔數層：最多floor(設定top_n×20%)檔同產業（至少1檔）。候選稀少或後續gate縮減時，不保證最終名單比例，更不檢查目前帳本產業資金。theme15%同樣是檔數層，未知theme不設限。",
        "4. canonical allocator沒有通用單股資金上限、持倉產業資金上限、固定資金槽位分母或強制現金保留。只有總名目active配置不超過100%的現金公式，以及signal本身的個別上限。",
        "5. 既有3%高估值動能限制只在PE>50且MA60乖離>25%觸發，2603未觸發；結算/macro是條件折減，CAUTION只限制Top10與T1路徑，沒有總曝險預算。另 `_cap_delta_weight` 遇非正/非有限target上限會退回raw allocation，不能用0作為安全停配開關。",
        "6. entry_allocation_version記錄sector_cap=0.20/group_cap=0.15，但這是上游檔數控制的metadata，不是帳本已執行資金控制的證據。warroom現金/權益與持倉畫面會使用真正target_weight，影響不只文字。", "",
        "## 同一份9/4固定訊號的敏感度", "",
        "假設全新空帳本、名目資金100%、2603仍為唯一候選。只截頂單股配置，其餘留現金，不改候選、不重新正規化分配。不是歷史回測。", "",
        "| 情境 | 2603待成交配置 | 保留現金 | 若精確-10%出場，名目本金損失 | 每100萬元名目本金 |",
        "|---|---:|---:|---:|---:|"]
    labels = {"current": "現行", "single_name_10pct": "單股上限10%（研究）", "single_name_20pct": "單股上限20%（研究）"}
    for row in sensitivity:
        lines.append(f"| {labels[row['scenario']]} | {row['allocated_pending_weight']:.2%} | {row['cash_left_unallocated']:.2%} | {row['nominal_capital_at_risk_at_minus10pct']:.3%} | {row['nominal_loss_per_TWD_1000000_if_exact_minus10pct_exit']:,.0f}元 |")
    lines.extend(["", f"既有副本2357的名目配置為{invested:.2%}，以同一純算術-10%假設，曝險為名目本金{invested*0.10:.2%}。這不是今天之後的預測跌幅，也不是停損一定可成交；實際gap、滑價、費稅與流動性都可能擴大損失。", "",
        "## 何時觸發，以及下一步", "",
        "全新空帳本／空倉重建，或舊部位結束釋出大額名目資金後，**下一個未鎖定run**只剩一檔有新增單位、且signal上限接近100%，便可接近全額配置。若只釋出20%或50%，同一算式最多分配該20%或50%；不會自動賣其他股來湊滿。既有同股無delta_units增加則不補款；升級有delta與上限空間才會加碼。已鎖定9/4不因後續資金釋出自動重做。", "",
        "若實際仍持有部位卻以空DB做recovery，會失去原資金保留狀態，應另列恢復流程风险。此診斷只讀副本，沒有做這種重建或實盤交易。", "",
        "SA建議在配置邊界明訂單股資金、現有持倉產業資金與現金預算，保留原選股訊號。10%/20%是供比較的研究值，需由使用者決定資金配置政策；既有集中部位如何處理應獨立決策，不能把新上限自動追溯成強制賣出。Champion與baseline Shadow共用此allocator；rere選股邏輯本輪完全未改。", "",
        "來源：`ml/predict.py:1636`、`build_unified_signals.py:946/1414/2481`、`update_unified_portfolio.py:376/1397/1422/1697`、`order_simulation.py:166`。精確copy source與DB/訊號SHA、既有run/position、gate與各可用現金情境見JSON。受檢帳本及訊號hash前後一致，guard無拒絕事件。"])
    (report_dir / "allocation_risk.md").write_text("\n".join(lines)+"\n", encoding="utf-8")
    print(json.dumps({"assessment": output["assessment"], "current_names": by_name,
                      "current_nominal_cash": available, "sensitivity": sensitivity,
                      "report": str(report_dir / "allocation_risk.json")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
