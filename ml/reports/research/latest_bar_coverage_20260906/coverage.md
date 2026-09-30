# Latest-bar coverage audit — 2026-09-06

Market date: 2026-09-04; knowledge date: 2026-09-06. The existing 1,947 numeric, nonretired CSVs have 1,911 current bars and 36 older last bars. All 36 older dates have an explicit official explanation; their live DuckDB dates match CSV dates, and the four retained ML rows match their own actual last-bar dates. This verifies latest-date completeness inside the existing file universe; it is not a whole-market or historical price-integrity verdict.

**Separate unresolved coverage issue:** 88 four-digit official securities have no local CSV (45 TWSE, 43 TPEx; 87 have valid 9/4 OHLC and one has no OHLC). Four are TDRs; listing/type validation and source-only staging are separate from this 36-stock result. See official_universe_without_csv.json.

## Classified old rows

| Ticker | CSV = live DB | ML actual date | Official reason |
|---|---|---|---|
| 1538 | 2026-08-24 | No model row | official_no_ohlc_reported_volume; volume=218 |
| 1563 | 2026-08-26 | 2026-08-26 | official_suspended; 現金減資換股；原 PDF 第 1 頁人工核對；減資 25%，每股退 2.5 元; [official](https://dsp.twse.com.tw/public/static/downloads/announcement/official/ABS-11500154501-1.pdf) |
| 1589 | 2026-04-02 | No model row | official_suspended; 未如期申報財報；PDF第1頁及9/6官方停止買賣表交叉核對; [official](https://dsp.twse.com.tw/public/static/downloads/announcement/official/S-11517009561-1.pdf) |
| 2724 | 2026-09-03 | No model row | official_no_ohlc_zero_volume; volume=0 |
| 2867 | 2026-08-19 | No model row | official_terminated; 股份轉換為2884子公司，終止上市；PDF第1頁人工核對; [official](https://dsp.twse.com.tw/public/static/downloads/announcement/official/S-11500135581-1.pdf) |
| 2949 | 2026-09-01 | No model row | official_no_ohlc_zero_volume; volume=0 |
| 3064 | 2026-08-26 | No model row | official_no_ohlc_zero_volume; volume=0 |
| 3067 | 2026-09-03 | No model row | official_no_ohlc_zero_volume; volume=0 |
| 3632 | 2026-09-03 | No model row | official_no_ohlc_zero_volume; volume=0 |
| 4130 | 2026-07-21 | No model row | official_terminated; 股份轉換為1799子公司，終止上櫃; [official](https://www.tpex.org.tw/storage/eb_data/11507/11502017631.html) |
| 4154 | 2026-09-03 | No model row | official_no_ohlc_zero_volume; volume=0 |
| 4406 | 2026-09-03 | No model row | official_no_ohlc_zero_volume; volume=0 |
| 4550 | 2026-09-03 | No model row | official_no_ohlc_zero_volume; volume=0 |
| 4804 | 2026-04-13 | No model row | official_suspended; 繼續經營重大不確定性三年未消除; [official](https://www.tpex.org.tw/storage/eb_data/11504/11502007761.html) |
| 5205 | 2026-09-03 | No model row | official_no_ohlc_zero_volume; volume=0 |
| 5310 | 2026-09-02 | No model row | official_no_ohlc_zero_volume; volume=0 |
| 5371 | 2026-08-21 | 2026-08-21 | official_terminated; 官方終止上櫃表；未含原公告日，以首次驗證日作保守available_on；新投控3718不得自行串接舊5371價歷史; [official](https://www.tpex.org.tw/www/zh-tw/company/deListed?date=2026&reason=-1&response=json&start=0&length=100) |
| 5520 | 2026-09-02 | No model row | official_no_ohlc_zero_volume; volume=0 |
| 5703 | 2026-09-03 | No model row | official_no_ohlc_zero_volume; volume=0 |
| 5906 | 2026-09-03 | No model row | official_no_ohlc_reported_volume; volume=716 |
| 6129 | 2026-09-02 | 2026-09-02 | official_suspended; 減資彌補虧損換股；官方停交易9/3至9/11，9/12-13非交易日; [official](https://www.tpex.org.tw/storage/eb_data/11508/11500054161.html) |
| 6228 | 2026-08-27 | No model row | official_no_ohlc_zero_volume; volume=0 |
| 6236 | 2026-09-01 | No model row | official_no_ohlc_zero_volume; volume=0 |
| 6461 | 2026-09-01 | No model row | official_suspended; 減資彌補虧損換股; [official](https://www.tpex.org.tw/storage/eb_data/11508/11500054121.html) |
| 6496 | 2026-09-03 | No model row | official_no_ohlc_zero_volume; volume=0 |
| 6615 | 2026-09-03 | No model row | official_no_ohlc_zero_volume; volume=0 |
| 6762 | 2026-09-03 | No model row | official_no_ohlc_zero_volume; volume=0 |
| 6767 | 2026-09-01 | No model row | official_no_ohlc_zero_volume; volume=0 |
| 6904 | 2026-09-02 | No model row | official_no_ohlc_zero_volume; volume=0 |
| 6949 | 2026-08-26 | 2026-08-26 | official_suspended; 面額 10 元改 0.5 元，1 股換 20 股；來源未列公告日，以首次驗證日作保守 available_on; [official](https://www.twse.com.tw/exchangeReport/TWTB7U?response=json) |
| 6997 | 2026-09-03 | No model row | official_no_ohlc_zero_volume; volume=0 |
| 8077 | 2026-09-03 | No model row | official_no_ohlc_zero_volume; volume=0 |
| 8342 | 2026-09-02 | No model row | official_no_ohlc_zero_volume; volume=0 |
| 8416 | 2026-09-02 | No model row | official_no_ohlc_zero_volume; volume=0 |
| 8923 | 2026-09-01 | No model row | official_no_ohlc_zero_volume; volume=0 |
| 8929 | 2026-09-03 | No model row | official_no_ohlc_zero_volume; volume=0 |

25 TPEx rows officially report zero volume and no OHLC. TWSE 1538 and 5906 report 218 / 716 shares respectively but no OHLC; no odd-lot inference or substitute price is made. Six are suspended and three terminated. Scheduled 1563/6949 resumption is 9/7, 6461 is 9/9, 6129 is 9/14; these are announced plans, not evidence of executed trading. New 3718 is not automatically stitched to old 5371.

## Implementation and evidence

- `scripts/latest_bar_coverage.py`: full-tape named fields, exact source dates, successful status, both markets, minimum stock coverage, duplicate rejection, event provenance and nonoverlapping effective intervals. Unknown absence remains warning; source gaps and downstream mismatches fail.
- `config/trading_status_events.json`: 12 dated events covering nine tickers. Publication dates govern knowledge when verified from original notices. 6949 and the 5371 termination-list record use conservative first verification 9/6 where the source does not expose original publication date. Current knowledge is never silently inserted into earlier backtests.
- `load_local_latest_dates`: true Date maximum, path/mtime/size cache, invalid/empty/changed-during-read CSVs fail. Measured 1,947 files: cold 9.093 s, warm 0.164 s.
- 25 focused tests passed, including source/date/schema/partial failures, real missing bars, positive-volume no-OHLC, independent DB/ML lag, effective/publication boundaries, conflicting revisions, cache invalidation and malformed local data.
- Live DB evidence is read through 36 GET `/api/stocks/{ticker}/daily?days=1` calls in the running service; no direct DB fallback or service interruption. Raw HTTP responses, CSV hashes, exact official tape bytes and source notices are frozen under `output/latest_bar_coverage_20260906/staging/` and indexed in `coverage.json`.
- Original TWSE notices for 1563, 2867 and 1589 are image PDFs. PDF image pages were visually read and checked; empty OCR text was not used as evidence. The registry hashes original PDF bytes.
- No price/CSV/production DB/model/ledger/prediction date or rere rule was changed. `prob_edge` A/B is N/A: completeness labels only; predictions unchanged. Root owns runtime health integration and serial commit.

Reproduce without network or database access:

```powershell
python -B -m pytest tests/test_latest_bar_coverage.py -q
python -B scripts/audit_latest_bar_coverage.py --snapshot-dir output/latest_bar_coverage_20260906/staging --registry config/trading_status_events.json --target-date 2026-09-04 --knowledge-date 2026-09-06
```

Official tape sources: [TWSE MI_INDEX](https://www.twse.com.tw/exchangeReport/MI_INDEX?response=json&date=20260904&type=ALLBUT0999), [TPEx daily quotes](https://www.tpex.org.tw/web/stock/aftertrading/otc_quotes_no1430/stk_wn1430_result.php?l=zh-tw&d=115/09/04&se=EW). TLS retrieval preserved certificate/hostname verification; Python 3.14 legacy-CA strict-extension validation required disabling only `VERIFY_X509_STRICT`, not TLS verification.
