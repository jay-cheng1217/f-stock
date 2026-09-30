# Entry tracker filename compatibility

The new canonical publication adds `entry_list_YYYYMMDD.json.manifest.json` certificates. The old broad `entry_list_*.json` backfill glob also selects these certificates; their integer `rows` field is not a candidate list. Backfill must only dispatch exact `entry_list_` plus eight ASCII date digits plus `.json` basenames. Existing historical date-only plans remain accepted.

Scope is filename dispatch only. Keep `record`, `check`, entry/exit/trigger/horizon rules and the ledger unchanged. Validate using temporary files and intercepted record/check calls; never run a real backfill or write the operational ledger for this fix.
