# 歷史資料缺口盤點與隔離回補 — 2026-09-06

本檔保留最初隔離盤點證據，後續狀態以同目錄新報告為準：4個外資日期已由root正式promotion；數字零解析缺陷及29,557個可證零值亦已修復，詳見 `foreign_zero/README.md` 與 `promotion.json`。TDCC原43個未觀測格已按官方年度營業日重分類為36舊期未觀測、4合法休市週、3近期可查週，详見 `tdcc_calendar_reconciliation.md`。近期完整收集範圍已凍結11,659券次並啟動，不再沿用下方最初約6,000次的模型範圍估算；進度與方法見 `tdcc_public_collection.md`。本檔舊時點的「待promotion」「unknown未修復」不代表最終狀態。

外資持股舊110失敗日已不是現況：盤點1619檔，現有缺檔3日加單市場殘檔1日。已從TWSE+TPEx實抓並驗證4日共8,111列，僅寫入staging，待root原子promotion。

| 日期 | 舊列數 | 官方驗證列數 | 結果 |
|---|---:|---:|---|
| 20220915 | 0 | 1954 | 日期exact、雙市場、無重複、數值完整、股數/比率一致 |
| 20220916 | 0 | 1954 | 日期exact、雙市場、無重複、數值完整、股數/比率一致 |
| 20220919 | 0 | 1954 | 日期exact、雙市場、無重複、數值完整、股數/比率一致 |
| 20260807 | 889 | 2249 | 日期exact、雙市場、無重複、數值完整、股數/比率一致 |

9/4最新影響：1901檔×14項foreign/turnover特徵，共26,614格，A/B改變0、最大差0。相同完整as-of日K逐檔只讀一次，兩分支共用；唯一變因是4日ownership替代路徑。9/4latest檔與Issued_Shares/Foreign_Pct map逐值完全相同。

foreign_cumsum 1/3/5/10/20來自法人買賣超流量，並非外資持股存量。V1/V2/T1周轉率及API持股比率只讀ownership最新檔。raw dataset cache仍會因歷史CSV mtime變動而在下次load時自動失效，不能把本次零差異說成已重跑完整預測；本輪未寫快取/預測。

2021估值舊3殘檔票：目前244檔、244個2330實際交易日、缺日期0；全數雙市場、至少1689列，schema/數值/覆蓋檢查通過。另5/10有3092跨市場重複，需核對轉上市日期與來源；不能宣稱2021全無問題。舊報告未列3個檔名，故只結論舊partial缺陷目前不可重現，不宣稱全量官方逐值重抓。

TDCC：以raw實際資料日期轉W-FRI檢查，週四/其他營業日均正確入週。原43格仍未觀測，但不可等同43個可補週。近一年官方日期表及實際2330查詢證明10/9、10/23、2/26免費可得；2/20全週休市且官方無該週觀測。其餘超出官方一年保存窗，舊春節無交易週還需保留『未觀測』與『真缺檔』的區分。

旧bulk API返回404；現行官方逐券表單可用，3週全市場約需6000次限速/斷點請求，本輪只做3檔次可得性驗證，沒有把單券資料塞入全市場週檔，也未重建summary。這3週不能再標成必須付費或沒有免費來源。

另外既有外資資料1618日存在unknown，欄位計數={'Issued_Shares': 0, 'Foreign_Held': 0, 'Foreign_Pct': 29557}。來源未知值未填0、未宣稱全數修復；詳見JSON top ticker供另行來源查證。

官方來源：[TWSE外資持股](https://www.twse.com.tw/zh/trading/foreign/mi-qfiis.html)、[TPEx外資持股](https://www.tpex.org.tw/web/stock/3insti/qfii/qfii.php?l=zh-tw)、[TDCC歷史表單與一年保存說明](https://www.tdcc.com.tw/portal/zh/smWeb/qryStock)、[TWSE春節休市公告](https://www.twse.com.tw/staticFiles/news/news/tsecnews/8a8216d69bde495d019c0d7f8f0100e2.pdf)。

可promotion清單及source_before/after SHA見foreign_candidates；最新group比較CSV、raw來源回應、TDCC單券HTML、2021逐檔SHA均保存。沒有訓練、變動策略/權重/門檻、操作production DB或帳本。
