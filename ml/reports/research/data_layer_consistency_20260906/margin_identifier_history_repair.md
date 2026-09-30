# 歷史融資代碼對帳與來源修復

已實際對帳 2020 年起 1,622 個日期、3,244 份官方雙市場原始檔，全部日期、schema、數值與唯一鍵檢查通過。範圍限先前盤點的 28 組前導零代碼及其別名，不宣稱是所有證券全歷史數值稽核。原始 SHA 固定，沒有重新抓行情或建立假價格棒。

總表在這些代碼有 18,480 個已配對数值格不符官方，30,139 個本地键無當日官方對應，40,856 個官方鍵缺席。僅重建該代碼/日期區域，其餘鍵與數值逐值不變；全表 3,014,577 → 3,025,294 列。

同時補正 34 個既有日 K 檔案的 53,099 個融資/融券格，26 檔為 ETF，8 檔為普通公司。只修改存在價格棒且官方有觀測的格子；原未知沒有通通補零，未變行保留 bytes，非融資欄位逐值相等。8 檔股票為 6201、6203、6204、6205、6206、6207、6208、9802。

兩分支共享每檔原始 CSV frame，用真正 `compute_institutional_features` 計算。8 檔普通股各 46 個最新特徵，共 368 格 exact 差異 0；最晚歷史特徵受影響日為 2026-07-28。26 檔 ETF 由既有 `filter_stock_universe` 排除，其中 22 檔最新特徵有變化，不能宣稱全證券特徵不變。沒有改模型 universe、公式、rere 門檻或重訓，也沒有以截面等價冒稱歷史回測結果不變。

5 個 focused fixtures 通過：多日期修補、保留精確價格/原始零/其餘缺值/未變行，以及拒絕改非融資欄、無價格棒日期、過期舊值和未知官方替代值。

2026-09-06 16:32:04 已經 hash preflight、不可變原檔備份與 compare-and-swap 正式發布 35 個來源檔。計畫 SHA `78d82c4898127f7c92d7a397b49b2c5a3432b0677e87a39d31a3d64201726727`，receipt 見同目錄 `margin_identifier_history_promotion.json`。DB 與下游整體重建另外驗收，來源發布不等於全系統完成。

原始證據：`F:/stock/output/margin_identifier_history_20260906`；候選與特徵驗證：`F:/stock/output/margin_identifier_history_staged_20260906`。可重現程式為 `scripts/audit_margin_identifier_history.py`、`scripts/stage_margin_identifier_history.py`；重跑 before/after 須用 promotion 備份還原到獨立研究目錄，不應覆蓋正式來源。
