# 2026-09-06 Web 與正式推論的模型一致性

## 已證明的問題

正式驗收首次 driver 直接呼叫 `run_prediction`，省略 nightly 的 Champion defaults：V2 選到 `20260905_114605`，且沒有 ranker，與固定 E 的 pinned 模型不同。原 FAIL receipt 和錯誤環境的 CSV/證書已由根任務保留；不能把這次差異歸因於資料或 provenance 改變計算公式。

Web 有相同入口缺漏：`start_web.bat` 直接啟動 `app.py`，原 lifespan 未套 defaults。`prediction_explain` 自行載入 V2 計算分數，再從 CSV 取建議，可能把兩套模型混用。

## 持久修復與不變邊界

- `lifespan` 的第一個動作，使用既有 `scripts.model_pin_registry.champion_defaults()` 與 `validate_registry()`；驗證失敗則啟動失敗。與 nightly 一樣透過 `os.environ.setdefault` 套 manifest 預設，保留明確環境值。沒有 import 或執行 daily/smart pipeline、訓練、通知或帳本步驟。
- CSV fallback 逐一核對目前選定的 base/V2/alpha/ranker 角色、metadata 路徑、weights 路徑與 certificate。載入 base metadata 後再驗一次，並使用既有 helper 核對模型/來源 SHA 與完整 file-set/stat 證據。錯配不能返回 CSV 搭另份 metadata。
- Explain 先取得同一個已驗證 CSV run，再載入 V2；模型載入後與計算後都核對實際配對。建議從該次已驗證 frame 取得。缺證、模型失配或計算期間來源改變時回傳 `status=data_error`，不拼接不同模型的分數與建議。
- 此修復僅修改 `app.py`。沒有改模型 pin、selection、`ml/predict.py`、`ml/prediction_provenance.py`、特徵或策略。既有成功 certificate 的 producer dependencies 不因本次 Web 修復失效。

如果操作者明確覆寫 Web 模型環境，setdefault 仍尊重該值；它不因此獲准解釋另一套模型的 CSV。正式 Web 部署應使用既有 Champion 預設，並由根任務驗收與正式 CSV 的實際模型配對。

## 隔離驗證

`tests/test_web_prediction_runtime.py` 使用實際 app 函式 AST、共用真 certificate helper 與 temp artifacts；不啟動伺服器、不載入正式 LightGBM、不碰 DB 或正式資料。

2026-09-06 22:52:28：`web_prediction_runtime_v1_tests` **64 PASS**（本次 16 + provenance/lineage/cache 48）。

- 預設環境符合 nightly；明確 override 保留；pin 驗證失敗不寫環境；實際 lifespan 在 DB/warmup 之前執行驗證。
- 缺 ranker、不同 metadata/weights 路徑、實際載入不同 weights 皆拒絕。
- CSV 正常 frame/metadata 與原 parser 結果逐值一致；錯配在載入另一模型之前就拒絕。
- 同一 run 的 explain 保留原 recommendation、0.1 fixture 預測報酬及 0.04 fixture 特徵貢獻；模型載入途中換檔、計算途中換模型或來源，會拋明確錯誤或回 `data_error`，不回傳混用結果。

Receipt：`output/pipeline_acceptance_20260906/web_prediction_runtime_v1_tests.json` 與 `.log`。這是邊界契約驗證；正式 Web 重啟、真實股票 explain/CSV 逐值驗收由根任務執行。
