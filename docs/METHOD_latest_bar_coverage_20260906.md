# Latest daily-bar coverage: method lock

Target market date: **2026-09-04**. Knowledge/verification date: **2026-09-06**. Scope is source → CSV → DuckDB → latest ML row dates, with special attention to 5371, 1563, 6949 and 6129. No production DB, model, paper ledger or rere-rule changes; raw official downloads and any repair candidates stay in isolated staging until root coordinates promotion.

Classify every one of the 36 numeric nonretired CSVs lacking 9/4, using the original full TWSE/TPEx tape (including rows with missing OHLC), explicit official halt/delisting/corporate-action evidence, and actual layer dates. A missing official OHLC is not a missing downloaded bar. A disappeared symbol without an explicit verified event remains unknown. Report zero-volume/no-OHLC and positive-volume/no-OHLC separately; neither permits invented OHLC, flat bars, date substitution or zero filling.

Before any health classification is considered complete, require both official markets, matching source dates, recognized named schemas, unique ticker rows and credible coverage. An explicit official event must include an effective interval and publication/knowledge provenance. Apply the event only at/after its effective start and only when available by the caller's knowledge date; do not inject current findings into earlier backtests.

The proposed repair changes completeness classification and evidence, not market prices or signal formulas. Preserve the actual last-bar date and distinguish source-missing bars, DB lag, ML row lag, legitimate nontrading and unresolved status. Announced future resumption is a plan, not evidence of a completed trade.

Validation: fixed official tapes and row/date manifests; tests for wrong schema/date, partial/failed market, conflicting duplicate/events, prepublication/effective-date boundaries, true missing bars, legitimate no-OHLC rows and downstream lag. Reports must list unverified gaps rather than return an empty-success result. If a real source gap is found, stage official raw rows with field provenance and hashes, then request root's promotion coordination.
