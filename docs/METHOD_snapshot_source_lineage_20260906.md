# 最新快照的來源一致性

本次 L1 修復針對同日期來源更新後，API 仍沿用舊 pipeline snapshot 的已重現缺陷。9/4 同股票池重新推論 A 相對既有正式 CSV 有 7 個推薦改變，不能歸因為新增 84 檔。實際 A 的 1,076 個 9/4 OHLCV 與既有隔離 DuckDB 逐值相等；舊正式快照只有 33 檔成交量與此資料庫相等。

`ml/snapshot_lineage.py` 以 canonical dataset 的 source patterns 為起點，涵蓋全日K檔案集合、月營收、處理後季度 financial/EPS/BS、估值、集保 summary、新聞、外資發行股數、指數及完整公司行動、除權息、ETF/退市 universe、產業 mapping、相關特徵程式。清理後 CSV 也保守列入。空目錄的存在狀態納入，避免「來源由不可用變可用」被忽略。

快照是模型計算前的特徵 frame，因此其來源證據不包含不參與特徵計算的模型權重；API 另以實際選中模型檔、metadata、selection 與既有 context 欄位使模型快取失效。此修復不改選模、數值公式、策略門檻或 rere 排序方法。

發布快照前保存完整來源 SHA256、完整檔案集合、size / mtime_ns 與 snapshot SHA256，原子替換資料和 manifest。計算前後與發布前均要求來源指紋一致。來源 IO memo caches 在重建前清除，避免長活 API 重算時讀到記憶體中的舊季報或舊週資料。

API reuse 只列檔和讀 stat metadata，不重讀 GB 級原始 CSV；實測 7,342 個來源、1.36 GB，暖路徑 0.137 秒。完整來源 SHA 是發布時證據，讀快取時另對實際 snapshot bytes 校驗 SHA。缺 manifest、同日修正、加檔、刪檔、壞 pickle / manifest、建置中更新均拒絕舊快取，沿用原 CSV fallback / explain 重建路徑。

邊界：日常 freshness 依檔案 size / mtime_ns 和完整集合；刻意保留相同時間戳及大小的就地竄改須用全 hash 稽核發現。此處不是數位簽章或敵對檔案安全機制。現有無 manifest 快照會重建一次，不把缺證據的舊快照自動補標成新來源。沒有重訓或改寫正式快照的驗證執行；正式發布由 root 在來源 promotion 完成後協調。

聚焦驗證：`python -B -X utf8 -m pytest tests/test_snapshot_lineage.py tests/test_source_universe_snapshot_ab.py -q`，21 tests PASS。真實暫存檔涵蓋同日期同大小修正、加刪檔、缺 / 壞 manifest、壞 snapshot、計算中更新、API adapter / 記憶體 context、真正 predict 保存邊界、來源 memo 清除與公司行動 / universe 依賴。

可用特徵群組也納入快速指紋，直接呼叫原 registry 判斷，涵蓋既有空目錄新增非 summary CSV 導致群組啟用的情況；不需把所有 TDCC 原始大檔加進逐次內容 hash。

同轮交叉審查另確認 API 記憶體快取的來源切換競態：context key 換新後若保留舊 snapshot/model，另一請求或建置失敗後重試會把舊值當作新 key 命中。新來源開始建置時現在原子清除舊值，同時只允許一個 builder；不同來源 key 在既有建置結束前也不得搶入，避免舊 builder 清除或發布到新 key。失敗保留明確錯誤且清空快取，不回傳舊模型解釋。

`tests/test_prediction_context_cache_transition.py` 直接擷取並執行實際 app 函式，隔離 DB/服務啟動；涵蓋失敗後再次請求、同 key 並發 barrier、K2 建置途中改 K3 的 barrier。與 `tests/test_snapshot_lineage.py` 合跑 21 passed，不寫正式來源或快照。
