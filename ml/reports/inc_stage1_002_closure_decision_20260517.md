# INC-Stage1-002 Closure Decision — Champion v2.6 Incident Edition

Date: 2026-05-17

## Decision

PM/RD lock the path as **AA + BB**:

- **AA:** Accept the current incident pin as the operational Champion and name it **Champion v2.6 incident edition**.
- **BB:** Open **INC-Stage1-003 Stage1 Alpha Decay Investigation** as a research-only ticket.

No additional short-term rebuild / re-pin attempts should be made unless INC-Stage1-003 produces a testable remediation path that passes CL3.

## Why

INC-Stage1-001 and INC-Stage1-002 proved that the pre-cleanup Champion behavior cannot be restored from available artifacts.

| Attempt | Result |
|---|---|
| Restore original Stage1 binary | Failed: no backup found |
| INC-Stage1-001: regenerated Stage1 + retrained Stage2 | Failed: Jaccard 0.429 vs current production |
| INC-Stage1-002: fresh v3 Stage1 + v3 Stage2 | Failed: Jaccard 0.132 vs current incident pin, CL3 failed |

INC-Stage1-002 failed on the core promotion gates:

| Metric | Result |
|---|---:|
| current incident vs v3 overlap | 7 / 53 |
| current incident vs v3 Jaccard | 0.132 |
| monthly alpha delta | -1.31pp |
| MDD delta | -2.50pp |
| Sharpe regression | 13.66% |
| Calmar regression | 50.48% |
| Stage1 holdout median | -0.0264 |

This means the problem is no longer only incident remediation. It is also a Stage1 alpha health problem.

## Production Handling

- Keep current production pins unchanged.
- Treat production from 2026-05-16 onward as **Champion v2.6 incident edition**.
- Start a new active attribution baseline from 2026-05-16.
- Preserve all v1.2 freeze numbers as historical / pre-cleanup forensic baseline only.
- Do not use v1.2 selection -11pp, allocation +4.20pp, or related numbers as active source of truth for new RCA decisions.

## Workpath Impact

| Workpath | Status |
|---|---|
| #6 Selection Structural RCA | Pause active RCA until v2.6/v1.3 baseline has enough post-incident data |
| #2 Stage A oracle slippage diagnostic | May continue; it does not depend on model pin |
| #2 Stage B production-safe A/B | Pause until v2.6/v1.3 baseline is accepted |
| REQ-034 Daily Position Risk Cut List | Continue; footer already marks `model_era=v2_incident_window` |
| #3 diagnostic-only | No change |
| #5 maintenance | No change |

## INC-Stage1-003 Ticket

### Goal

Explain why fresh Stage1 training now produces weak / negative holdout score distribution and poorer CL3, without changing production.

### Type

Research-only RCA. No production model, gate, feature, or portfolio change.

### Required Diagnostics

1. **Training-window sensitivity**
   - Compare Stage1 trained on at least three bounded windows:
     - pre-2026-04 shock window
     - current full window
     - recent data excluded / shock-excluded window
   - Report holdout score median, Universe IC, Top75 candidate quality, and Top30 downstream alpha.

2. **Feature drift**
   - Compare feature distributions for OOF baseline vs current data.
   - Highlight top drifted features by PSI or comparable bounded metric.
   - Identify whether drift is concentrated in liquidity, price momentum, institutional flow, valuation, or guardrail-related features.

3. **Target / label regime**
   - Compare `trade_excess_return_20d` distribution before and after 2026-04.
   - Report whether the target became globally more negative for Champion's small/mid-cap hunting ground.

4. **Regime cohort split**
   - Split performance by large-cap-led market, small/mid participation, CAUTION, OPEN, and liquidity breadth cohorts where data exists.
   - Determine whether Stage1 decay is a regime mismatch rather than a training bug.

5. **Pipeline integrity**
   - Confirm no feature processor, target construction, or dataset-cache schema drift caused the median score collapse.
   - If any model or feature-processor change is proposed, the Model Incident Closure Rule requires same-snapshot A/B before production consideration.

### Acceptance

- Identify top 3 plausible Stage1 alpha decay sources, each with evidence and estimated impact.
- Each source must map to a testable remediation path.
- Do not submit descriptive statistics only.
- No production changes in this ticket.
- Any remediation must become a separate CL3 ticket.

## Reviewer Notice Summary

The reviewer-facing message is stored at:

`ml/reports/reviewer_notice_stage1_incident_round3_20260517.md`

