# Research Output Layer

This layer turns local model and portfolio artifacts into PM-readable research
outputs. It is an output/reporting layer only.

## Scope

Allowed:

- Daily investment memo from `unified_signals_latest.json`
- Catalyst tracker from official public-market context snapshots
- Weekly V2 retrain PM pack from `v2_backtest_*`, model meta, and feature
  importance
- A/B review pack from overlay decision and agent arena reports
- Watchlist context from a local CSV or future Google Sheet export
- Holdings summary from `my_holdings.db` without cost/share fields
- Attaching the reporting step to the existing nightly/weekly pipeline after
  prediction artifacts are generated

Forbidden:

- Changing model scores
- Changing production gates
- Changing scheduler timing or trigger semantics
- Writing to portfolio databases
- Treating Gmail/Drive research notes as LightGBM features without historical
  backfill, leakage review, fixed-snapshot A/B, and PM/SA approval

## Command

```powershell
python scripts/research_output_layer.py
```

Optional local watchlist:

```powershell
python scripts/research_output_layer.py --watchlist-csv config/research_watchlist.csv
```

Expected watchlist columns:

- `ticker`
- `label`
- `thesis`
- `risk_note`
- `owner`
- `updated_at`

## Outputs

Latest artifacts are written under `ml/reports`:

- `research_output_layer_latest.md`
- `research_output_layer_latest.json`
- `research_daily_investment_memo_latest.md`
- `research_daily_investment_memo_latest.json`
- `research_catalyst_tracker_latest.md`
- `research_catalyst_tracker_latest.csv`
- `research_weekly_v2_pm_pack_latest.md`
- `research_weekly_v2_pm_pack_latest.json`
- `research_ab_review_pack_latest.md`
- `research_ab_review_pack_latest.json`
- `research_watchlist_context_latest.md`
- `research_watchlist_context_latest.json`
- `research_holdings_memo_latest.md`
- `research_holdings_memo_latest.json`

These files are daily-mutating report artifacts and are intentionally covered
by existing `ml/reports/*_latest.*` ignore rules.

## Google Workspace Handoff

The repo does not store Gmail or Google Drive credentials. The generated JSON
includes a `google_workspace_handoff` section that maps local artifacts to
suggested destinations:

- Daily memo -> Google Doc / Gmail draft
- Catalyst tracker CSV -> Google Sheet
- Weekly V2 pack -> Google Doc
- A/B review pack -> Google Doc

Connector upload/send should stay interactive or be handled by a separate
credential-managed workflow.
