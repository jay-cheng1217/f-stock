# ChipK Model Diagnosis Runbook

Status as of 2026-07-03: **desktop ChipK automation is retired by default**.
Do not recreate `TW_Stock_ChipK_Update`, do not page the user for stale
desktop snapshots, and do not use archived desktop ChipK files as an automatic
entry gate unless `CHIPK_DESKTOP_ENABLED=1` is explicitly set and the data is
fresh. Mobile ChipK screenshots remain useful as a manual veto layer.

Purpose: use local CMoney ChipK market-data snapshots to refine current model picks.

This is not a historical backtest until daily ChipK history exists. It is a same-day
diagnostic layer for model-selected names.

## Data Levels

Level 1 is implemented:

- ChipK main-force 1D / 5D / 20D rank
- Main-force score and bucket
- Trap-risk labels inferred from 1D / 5D / 20D shape
- Model-pick cross-check report

Level 2 is not implemented because the export is not available yet:

- Signed main-force buy/sell lots
- Foreign / investment-trust / dealer buy-sell from ChipK view
- Retail ratio / retail flow

Level 3 app API probing is implemented, but production use requires successful
row-level data capture:

- Top broker branch buy/sell lots
- Top broker net concentration
- Repeated broker accumulation
- Same broker next-day sell / day-trade trap verification

The desktop app stores only Level 1 in the readable local CP950 snapshot.  Level
3 must be requested through the logged-in AppViewer WebView session and the
ChipK app API.  The adapter uses session values only in memory and does not
print or store cookies, JWTs, passwords, or account identifiers.

## Daily Commands

Archive the current local ChipK snapshot:

```powershell
python scripts\archive_chipk_snapshot.py
```

Build the model-pick cross-check:

```powershell
python scripts\chipk_model_diagnosis.py --output-prefix 20260618
```

Standalone scheduled update:

```powershell
scripts\run_chipk_update.bat
```

Windows Task Scheduler task:

- `TW_Stock_ChipK_Update`
- Monday-Friday at 20:25 Asia/Taipei
- Runs `F:\stock\scripts\run_chipk_update.bat`
- Archives the latest desktop snapshot, writes `chipk_snapshot_status_latest.json`,
  and builds `chipk_model_diagnosis_<asof>.{md,json,csv}`
- Stale or missing desktop snapshots are reported as `DEGRADED` with a user
  action to reopen/refresh ChipK; recoverable stale states exit 0 to avoid
  noisy `0x1` scheduler incidents

Probe broker-detail rows from the logged-in ChipK desktop app:

```powershell
python scripts\probe_chipk_app_api.py --ticker 8011 --date 20260618 --period 1 --period 5 --period 20 --write
```

Broker Top15 app defaults are `1/5/10`; the Level 1 main-force snapshot is
`1/5/20`.  These are different app surfaces.  Pass `--period 20` explicitly
only when a 20-day broker-detail probe is needed.

Outputs:

- `ml/data/chipk/archive/chipk_main_force_<date>.csv` (git-ignored raw archive)
- `ml/data/chipk/latest/chipk_main_force_latest.csv` (git-ignored latest pointer)
- `ml/data/chipk/app_api/chipk_app_api_<date>_<time>.json` (git-ignored sanitized API probe summary)
- `ml/data/chipk/app_api/chipk_app_api_<date>_<time>_broker_rows.csv` (git-ignored broker rows when returned)
- `ml/reports/chipk_model_diagnosis_<date>.md`
- `ml/reports/chipk_model_diagnosis_<date>.json`
- `ml/reports/chipk_model_diagnosis_<date>.csv`

## Interpretation

Current rank assumption:

- `1` means strongest
- `5` means weakest
- 1D / 5D / 20D are scored into `chipk_main_force_score`

Diagnostic patterns:

- `sustained_accumulation`: 1D, 5D, and 20D are all strong
- `near_term_accumulation`: 1D and 5D are strong, 20D is not weak
- `constructive_support`: 5D and 20D are strong, 1D is not weak
- `constructive_pullback`: 1D weak but 5D and 20D still strong
- `short_term_chase_trap`: 1D strong but 5D and 20D weak
- `distribution_pressure`: 1D, 5D, and 20D are all weak
- `short_term_fade`: 1D and 5D weak while 20D is still strong

Operational read:

- `CONFIRM_ENTRY`: model buy and ChipK structure is strong
- `SUPPORTIVE_ENTRY`: model buy and ChipK structure supports entry
- `WATCH`: model buy but ChipK has no clear confirmation
- `WAIT_FOR_TURN`: possible pullback; wait for 1D turn
- `DO_NOT_CHASE`: short-term trap risk
- `AVOID`: weak/distribution pressure

Broker-detail probe interpretation:

- `status=ok`: app API returned row-level broker data; rows can be archived and used for offline diagnosis.
- `status=no_rows` with decoded header: API route and zip decoding worked, but the active session did not return broker rows.  Likely causes are missing app SID/permission/page activation or a trial/product entitlement boundary.
- `api_no_data`: endpoint responded but rejected the request or returned no payload.
- `decode_error`: API returned a payload but the zip/password/decode contract changed.

Do not treat `no_rows` as proof that the stock has no broker activity.  It is a
data-access state until at least one known active symbol returns rows.

## Promotion Boundary

Do not turn ChipK into a production hard gate until:

1. At least 180 daily ChipK snapshots are archived, or historical exports are imported.
2. Signed main-force buy/sell and broker-detail data are available through repeatable app API captures or exports.
3. A point-in-time backtest verifies the ChipK fields without look-ahead leakage.
4. Same-snapshot A/B confirms the model-pick ranking change is beneficial.
