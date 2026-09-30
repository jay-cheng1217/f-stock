# daily_pipeline 來源結果與資料世代修復

分級：L1 pipeline repair。方法與範圍見 `METHOD_data_layer_consistency_20260906.md`。

## 已確認問題

`run_job(..., raise_on_fail=False)` 的結果被 twstock、估值、處置與 TDCC wrapper 丟掉，`run_step` 因沒有例外而記成功。前一輪修好 CLI exit code 後，這種抓取失敗仍到不了 ledger。MOPS 與 Finnhub 同組也有相同問題。

另一個確定的路徑差異：獨立 `daily_pipeline` 沒有 Phase-1 的官方日K校正；直接執行 twstock 後即可 ingest，違反正式原始價格契約。來源或 ingest 失敗時仍繼續產生預測與帳本，會將不同世代混用。TDCC 仍限交易週五，無法接住週末公布資料。

## 修復後行為

- 必要抓取器拋出 nonzero / timeout / executable missing，沿用 job_runner 失敗告警，ledger failed、程序 exit 1。
- 兩個新聞來源都會嘗試，任一失敗即該組 failed；有效價格資料仍可往下處理，整體不可顯示成功。
- twstock 後新增正式 TWSE/TPEx 終盤校驗。必要來源成功才 ingest，使用既有 release / transaction / reconnect 流程。
- 必要來源或 ingest 失敗：預測、訓練、帳本、一般投資總結信均不執行；保留 `blocked_steps`，結束碼 1。不改選股策略或成功路徑計算。
- TDCC 每次資料更新嘗試最新公布期，與既有 Phase-1 catch-up 一致。

## 驗證

`python -B -X utf8 -m pytest -q tests/test_daily_pipeline_exit_status.py`：33 passed。

真實 main / run_step / job_runner 配合隔離子程序邊界 fixture，涵蓋五個抓取步驟的 nonzero、timeout、missing executable；雙新聞 partial failure、Friday/weekend/Monday catch-up；官方校驗→ingest→預測順序；ingest False 時禁止訓練、預測、帳本與正常信件。既有 weekly failure 僅跑一次並恢復服務測試維持通過。未實跑完整 production wrapper、未發測試告警或投資信件。

獨立複核補強：將產物依賴也明列。主預測/T+1失敗不得建 unified，unified失敗不得同步組合，必要預測/帳本/研究輸出失敗不得寄正常總結。訓練失敗不把舊模型回測冒充本次結果。Arena等可選步驟失敗仍不無差別阻擋獨立成功項。與財報 audit 的 NaN 首列重複 fixture 合併驗證為 50 passed。
