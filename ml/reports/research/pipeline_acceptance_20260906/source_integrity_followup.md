# KOL source-integrity follow-up

Verified 2026-09-06T13:35:16.371038+08:00. Status: **explained internal counter change**.

All five business tables (31,242 rows), schema SQL and timestamps match exactly by full all-column row hashes. No private message content is included.

| Table | Original rows | Backup rows | All-column content equal |
|---|---:|---:|---|
| mentions | 608 | 608 | Yes |
| posts | 347 | 347 | Yes |
| price_history | 30,134 | 30,134 | Yes |
| sources | 1 | 1 | Yes |
| sqlite_sequence | 2 | 2 | No: internal sequence +1 |
| symbol_meta | 152 | 152 | Yes |

The only logical difference is internal `sqlite_sequence.sources`: **1373 to 1374**; mentions remains 608. This is a real internal state change, not a changed post, classification, price, source identity or symbol metadata.

**Provenance:** Windows task `SerenityFeedCollector` ran **2026-09-06 13:15:01 +08:00**, result **0**. The collector log records **13:15:02 feed=80 new=0 total=347**, matching the original database mtime. Its code executes `INSERT OR IGNORE` on the AUTOINCREMENT sources table and commits each successful poll. Attribution to that independent task is strongly supported; no OS per-process writer trace was captured.

**Byte versus data:** preparation used source `mode=ro` and SQLite backup, whose header bytes need not match the original file. Current original/backup differ at six bytes: five header bytes and one sequence byte. A reconstruction entirely in memory, using the backup data and previous original transaction counters, reproduces the **initial source manifest SHA-256 exactly**. Both databases use `journal_mode=delete` and have no observed WAL/SHM/journal sidecars; there is no evidence for WAL checkpoint as the cause.

**Freshness, identical in both:** latest post `2026-09-02T14:16:19+00:00`, collection `2026-09-02T15:15:01.917987+00:00`, classification `2026-09-02T23:00:46.611457+00:00`, and price date **2026-09-04**. Hourly successful polling with `new=0` does not prove external feed completeness.

Source hash at preparation: `d2097a78403d17925228e39d366031205ef8b4f61962e7656cab1e37e1f29098`
Current source hash: `5bf0b56c684b032f05da693ff27b9735a4a3731d6c861f294e97de742b3166eb`
Backup hash: `e3d65f6a45a173350e409f0f7cdfcd5fca37af8830e847c63184c1c81a616dd6`

Both DB files stayed unchanged throughout this audit. No collector, pipeline, fetch, DB mutation, notification or commit occurred. Full schemas, counts, timestamps and hashes: [source_integrity_followup.json](source_integrity_followup.json).
