# SA Design: REQ-034-v3 My Holdings Risk-Cut Report (2026-05-17)

## Decision Status

- PM dispatch accepted. No need to return this ticket to PM for rewrite.
- RD may run the required dry-run path first.
- Formal implementation waits for PM approval of dry-run output.
- This ticket does not change `my_holdings.db` schema, does not add auto-trading, and does not change the 14 guardrail thresholds.

## Business Goal

Send a daily 06:30 reminder for the real holdings stored in `my_holdings.db`, grouped as:

- Red: cut / exit / urgent manual review.
- Yellow: trim / watch / data degraded.
- Green: keep / add-ok only when all required data is present.

The report must reuse existing `prediction_explain` and my-holdings review logic where possible.

## SA Decisions

### 1. Module Placement

Create a standalone batch script, not a router extension and not a direct extension of `smart_update_auto.py`.

Selected module:

- `scripts/send_my_holdings_risk_cut_report.py`

Rationale:

- A 06:30 email is a scheduled batch concern, not an API concern.
- `smart_update_auto.py` is already a broad daily pipeline orchestrator; embedding personal holdings policy there would make ownership blurry.
- The repo already has the naming pattern `scripts/send_position_risk_cut_report.py`, so this keeps the batch/report surface consistent.

Implementation guidance:

- Reuse the same classification core as `/api/my_holdings/{id}/review`.
- If the shared logic is too tightly coupled to the router, RD may extract it into `backend/services/my_holdings_review.py` as a behavior-preserving refactor.
- Do not call the FastAPI endpoint over HTTP from the batch script.

### 2. ETF Rule Subset

ETF holdings must not call V2 prediction or `prediction_explain`.

ETF applicable rules:

| Rule Class | ETF Handling |
|---|---|
| Daily K missing/stale | Red data-quality review |
| Stop loss / below breakeven | Red |
| 20D crash | Red |
| Low liquidity | Red or yellow based on severity already used by shared review logic |
| Close below MA5/MA20 | Yellow unless combined with loss/stop condition |
| Price overheat / RSI / sharp 1D drop | Yellow |
| Leveraged/inverse ETF exposure | Yellow, red if portfolio exposure exceeds existing portfolio-review red threshold |

ETF excluded rules:

- Fundamental guardrails.
- Valuation guardrails.
- EPS / margin turnaround rules.
- V2 alpha model recommendation.
- SHAP or prediction contribution logic.

### 3. Data Fallback Policy

Use a two-level fallback:

| Missing Item | Policy |
|---|---|
| `my_holdings.db` missing or empty | Skip report, write log, send no empty email |
| Daily K unavailable for a holding | Include the holding as red data-quality review |
| All stock prediction / `prediction_explain` data unavailable | Send degraded report only if daily K exists; never emit green/add from model logic |
| One ticker outside prediction universe | Classify with technical and cost-basis rules, mark yellow `MODEL_UNAVAILABLE_REVIEW_REQUIRED` unless red rules fire |
| Unified signals missing | Do not fabricate rank/Top30 evidence; use shared review and mark report footer as degraded |

No data-fetch retry belongs inside this report. Freshness and retries stay in the upstream daily pipeline. The report may support `--force` and `--dry-run` for validation.

### 4. Email Failure Policy

Always write local artifacts before attempting email:

- `logs/my_holdings_risk_cut_<YYYYMMDD>.log`
- `logs/my_holdings_risk_cut_<YYYYMMDD>.json`
- `logs/my_holdings_risk_cut_<YYYYMMDD>.html`

Email sending should reuse `scripts.send_daily_email.send_email`, including its existing retry behavior.

If SMTP settings are missing or sending fails:

- Keep local artifacts.
- Print/log the failure.
- Exit non-zero only for send failure after artifacts are written.
- Do not add Slack or webhook fallback in this ticket.

### 5. Boundary With My Holdings MVP (#7)

This ticket owns only the daily batch reminder.

Allowed:

- Read `my_holdings.db`.
- Reuse or extract review/classification logic.
- Write report artifacts.
- Send one email.
- Add tests for report classification and dry-run behavior.

Not allowed:

- New web UI.
- New API route.
- Schema migration.
- Auto-order or broker integration.
- Changing thresholds.

## Dry-Run Gate

RD must run the dry-run before formal implementation.

Dry-run target:

- Use `my_holdings.db`.
- Use 2026-05-16 as the requested as-of date.
- Do not send email.
- Produce expected output for the 12 real holdings.

Coverage expectation:

- At least one red sample.
- At least one yellow sample.
- One ticker missing from prediction universe, if present in the real holdings; otherwise report coverage gap explicitly.
- At least one ETF processing result, if present in the real holdings; otherwise report coverage gap explicitly.

Do not fabricate real-holding outcomes. If the real 12 holdings do not naturally cover a case, RD must add a separate synthetic boundary appendix and label it clearly.

## RD Dispatch

RD is cleared for dry-run preparation only.

Expected output:

- `ml/reports/req034_v3_my_holdings_dry_run_20260517.md`
- `ml/reports/req034_v3_my_holdings_dry_run_20260517.json`

RD task list:

1. Inspect `my_holdings.db` row count, tickers, and asset types without printing private cost details into broad logs.
2. Build the dry-run expected report from existing review logic where possible.
3. Report red/yellow/green counts.
4. Report degraded-data cases separately from sell/trim recommendations.
5. Confirm whether ETF and prediction-universe fallback cases are covered by the real holdings.
6. Stop and wait for PM approval before formal implementation.

