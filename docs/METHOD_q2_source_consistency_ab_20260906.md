# 2026Q2 官方來源補齊與欄位修復方法鎖定

本方法在候選資料接入模型前鎖定。L1 來源抓取 / 解析修復：原模型、feature processor、金融常識 guardrail、rere 型態、主策略、倉位及退出方法均不改，不重訓。

價格 as-of 為 2026-09-04，官方來源的 knowledge-time 為實際 2026-09-06 查詢時間，名單供 9/7 觀察。沒有逐公司實際公告 / 修訂時間的來源，不宣稱 9/4 當時必然已知，也不將新抓的修訂資料作歷史績效結論。

## 固定輸入與分支

- A / B 為既定缺股來源比較：一份 as-of union DuckDB，A 原股票池，B 加官方 84 公司；既有季度來源固定不變。
- 先完成 A/B，再凍結官方 Q2 財報 HTML / JSON、request 與 response 期別證據、舊三張 Q2 CSV、來源解析器版本與 hash。
- 另建 C 分支：以 B 的股票池和同份 union DuckDB，僅替換已驗證的 Q2 季報來源。財報來源維度分別列 financial / EPS / BS 的新增、官方修訂、unknown 與既有值改變；不得將 C 的影響歸因為新增 84 檔。
- 對 BS 另做同一份官方 HTML 的舊位置 parser / 新具名 parser 比較，獨立列出欄位錯置與來源新增公司兩種效應；不將錯欄候選直接發佈。
- 可重用 B 未受影響股票的凍結股內 feature rows，但必須逐一確認依賴；受影響股票以相同 raw frame 重算全部特徵，清除季度來源 cache。每枝仍各自執行完整 canonical cross-sectional / sector / winsor / market-regime 後處理及 pinned inference。
- 一對一 ticker/date 比較 prob_edge、pred_return_20d、推薦、TopN、dataA 分數與 canonical 主策略 / rere 名單，另比 A 與原正式 CSV 的基準漂移。

## 來源驗收條件

1. 必須雙市場回應完整、ticker 唯一、期別與查詢一致；財務年度只有 request receipt 可證而 HTML 無年度時，要明列限制，並用官方當期 OpenAPI 年度 / 季別及抽樣數值對帳，不捏造頁面證據。
2. 最新官方清單不再列出已終止上市公司，不能刪除舊 CSV 中已申報的同季歷史。保留舊有效 ticker，官方實際回應列則以新值更新；保留 before bytes 與修訂明細。
3. BS 每張業別表按實際欄名定位，適當展開 colspan / rowspan；資產、負債、總權益用總額，不拿無形資產或母公司權益冒充。合法不適用流動科目保留 NaN，來源不提供的金融業營益比率不補零。
4. 檢查資產與負債加總權益的一致性、官方每股參考淨值的逐值對帳、數值 / 單位 / 缺值一致；保留負權益等真實財務狀態，不以合理範圍猜資料。
5. 所有模型輸出必須有限，全部差異量化並說明來源；若有 schema / 數值 / 期別 / pin 違規，停止該批 promotion，不能調模型或門檻讓測試過關。

每次 promotion 都以 verified_source_promotion 的 before / candidate / evidence SHA、原始備份與 receipt 驗證。此方法不授權模型挑選或策略修訂。
