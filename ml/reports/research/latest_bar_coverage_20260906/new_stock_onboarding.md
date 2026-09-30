# New-company source onboarding — 2026-09-06

84 companies were missing daily CSVs because Step 1 enumerated existing files only. This batch stages 16,024 actual official bars, all with positive OHLC and volume, at or after official listing date. 44 companies listed in2026,37 inSep–Dec2025,3 have earlier listings; four TDRs remain separately excluded. No production source/DB/model/ledger promotion was performed by this subtask.

- Source evidence: 1,622 cached daily pairs, both markets validated; 3,244 source hashes, zero parse/contract failures. OHLCV and per-file hashes in `output/new_stock_sources_20260906/manifest.json`. CSVs total658,508bytes.
- 3718 has only9/3–9/4 (two bars); no5371 splice. 83companies have last9/4,7782 last9/3 because official9/4 has noOHLC/volume0. Across all history92 no-OHLC days remain absent; six additional7780 absences are exactly the officially announced1/9–1/17 face-value halt. Existing corporate-action price/label factor0.1 on1/19 was verified.
- Enrichment frozen under `output/new_stock_sources_enriched_v2_20260906/`: all16,024 OHLCV/key values unchanged. Institutional columns each14,636 observed /1,388 unknown; margin columns each6,980 observed /9,044 unknown. No zero-fill or forward-fill. Raw/merged matching values conflict0.
-18companies pass the unchanged history/volume/price eligibility gate. All18 have full institutional source observations in their latest60 sessions; margin newest row absent only for7610. Several recent listings have unknown pre-margin periods; source absence alone is not proof of ineligibility. Existing downstream model handling of NaN remains for root A/B review.
-Found one existing raw source defect: TPEx2026-03-20 cached stat=ok with zero rows. One isolated official query supplied889 rows and recovered3 new-company margin rows. Original failed-stage receipt is preserved; v2 has source_errors0. Production files remain unchanged; all existing-company impact is a separate follow-up.
-Canonical ISIN sector parser skipped the official「創新板」section. Minimal repair includes that section with common-share CFI, using the same official industry taxonomy. Staged mapping keeps1,924 existing rows unchanged and appends39 (total1,963); no missing metadata among84. 4590=電機機械,7610=綠能環保.
-Future Step1 discovery now validates both official company lists and current tapes before atomically creating any new CSV. It seeds only real current positive-volume bars, retains no-OHLC/zero-volume cases as pending, never overwrites existing files or aliases predecessor tickers, and records source/date/hash receipts. Exceptions propagate rather than disappear.
-44focused tests passed. Actual function leaf with live official company HTTPS requests and frozen9/4 tapes ran in12.872seconds in a clean isolated directory:1,940 real seeds,34pending; this blank-universe leaf is separate from84-company staging. No network mocks or production writes.

Reproduce:

```powershell
python -B -m pytest tests/test_listed_company_discovery.py tests/test_new_stock_enrichment.py tests/test_latest_bar_coverage.py -q
python -B scripts/stage_new_stock_sources.py --output-dir output/new_stock_sources_replay --candidates ml/reports/research/latest_bar_coverage_20260906/official_universe_without_csv.json --target-date 2026-09-04
python -B scripts/enrich_new_stock_sources.py --source-dir output/new_stock_sources_20260906 --output-dir output/new_stock_sources_enriched_replay --raw-overrides output/new_stock_sources_20260906/raw_margin_overrides
```

Official metadata sources: [TWSE company data](https://openapi.twse.com.tw/v1/opendata/t187ap03_L), [TPEx company data](https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O), [TWSE ISIN](https://isin.twse.com.tw/isin/C_public.jsp?strMode=2), [TPEx ISIN](https://isin.twse.com.tw/isin/C_public.jsp?strMode=4). Root owns frozen-model A/B and serial promotion/commit.
