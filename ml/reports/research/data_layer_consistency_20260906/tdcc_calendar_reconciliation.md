# TDCC 歷史未觀測週的官方行事曆核對

原43個W-FRI未觀測格，重新分類為：

| 分類 | 格數 |
|---|---:|
| OLDER_UNOBSERVED_WITH_TRADING_DAYS | 35 |
| OLDER_UNOBSERVED_WITH_SETTLEMENT_DAYS | 1 |
| LEGAL_NO_BUSINESS_DAY_WEEK | 4 |
| PUBLIC_DATED_QUERY_AVAILABLE | 3 |

39個舊期格不能全部稱為可補缺週。其中2022-02-04、2023-01-27、2025-01-31的整個工作週均為正式春節假期；連同近年的2026-02-20，共4格可合法無觀測。這是依官方工作日與TDCC最後營業日定義的分類，不是捏造歷史TDCC回應。

2021-02-12那週雖然沒有交易，但2/8與2/9仍辦理結算交割，不能一併當作整週無營業；預期最後營業日2/9尚需歷史TDCC來源直接佐證。其餘舊格有交易日，繼續標記未觀測；現有FinMind register權限實測不允許歷史bulk。

近一年3週仍由公開單券查詢收集，不能將12筆小批驗證充當全週。分類不更改原raw、summary或feature缺週格點。

| 零交易週 | 分類 | 官方期別證據 |
|---|---|---|
| 2021-02-06/2021-02-12 | OLDER_UNOBSERVED_WITH_SETTLEMENT_DAYS | [TWSE 年度行事曆](https://www.twse.com.tw/holidaySchedule/holidaySchedule?date=20210101&response=json) |
| 2022-01-29/2022-02-04 | LEGAL_NO_BUSINESS_DAY_WEEK | [TWSE 年度行事曆](https://www.twse.com.tw/holidaySchedule/holidaySchedule?date=20220101&response=json) |
| 2023-01-21/2023-01-27 | LEGAL_NO_BUSINESS_DAY_WEEK | [TWSE 年度行事曆](https://www.twse.com.tw/holidaySchedule/holidaySchedule?date=20230101&response=json) |
| 2025-01-25/2025-01-31 | LEGAL_NO_BUSINESS_DAY_WEEK | [TWSE 年度行事曆](https://www.twse.com.tw/holidaySchedule/holidaySchedule?date=20250101&response=json) |
| 2026-02-14/2026-02-20 | LEGAL_NO_BUSINESS_DAY_WEEK | [TWSE 年度行事曆](https://www.twse.com.tw/holidaySchedule/holidaySchedule?date=20260101&response=json) |

TWSE年度查詢的實際參數是date=YYYY0101。以queryYear傳民國年會被忽略而返回最新年度；本報告驗證回應date與queryYear等於請求年度，避免拿今年行事曆解釋往年。
