# 匯入與 API 清單一致性修復

分級：L1 匯入 / health 修復，不改模型、特徵與策略。

已確認 `ingest_all()` 在 2026-08-18 改成逐表交易，卻只對 daily_k 失敗拋錯。revenue、financials、tdcc、indices 或 stock_list 失敗會留在 log，但呼叫端仍收到成功；同日期檔名與 daily_k 新鮮度無法證明其餘資料已匯入。保留逐表交易的隔離能力，所有必要匯入失敗均在其他表完成後彙總拋錯，讓既有 retry 及本輪 pipeline 依賴阻擋真正生效。成功資料不被抹除，混合世代也不冒充完整成功。

另一個實際問題是 stock_list 以歷年 DISTINCT(Ticker, Name) JOIN，名稱更動會把一個股票展開多列。改為最新年 / 季名稱，每代號唯一；沒有季報名稱的新公司使用官方 sector metadata 補名。metadata 重複代號會在替換前被擋，stock_list 以 UNIQUE INDEX 強制唯一性。

Web daily health 新增必要表 ingest_meta 世代一致、紀錄列數與真實列數、stock_list 與 daily_k 代號 / 最新日期一對一檢查。未知或缺 metadata 不能綠燈。這是資料庫與 API 清單的有界檢查，不宣稱歷史來源已全部完整。

驗證：`tests/test_pipeline_health_connection.py`、`tests/test_ingest_guards.py`、`tests/test_daily_pipeline_exit_status.py` 共 53 passed。使用真實 DuckDB fixtures 重現 mixed generation、列數錯置、stock_list 重複 / 缺列 / 過時、partial commit 保留成功表且 caller 失敗、財報改名與新股補名。原本期待全表 rollback 的舊測試已按現行逐表契約更新，並為所有匯入 stub，避免測試誤讀正式來源。

正式來源 promotion、整次 ingest 與 live health 的最後驗收另見本次資料一致性總報告；此文件不將單元 fixture 當成正式部署完成。
