# TDCC 免費歷史查詢收集器驗收與續跑

> 2026-09-06 晚間續跑：固定 11,659 筆計畫已全部走訪，11,609 筆取得完整分級，50 筆官方無資料各自另查一次且保留兩次 HTML，未解抓取／解析錯誤 0。原先失敗四筆及暫時假空的 2347、000815 均已找回。下方下午 PID、命令、剩餘數是執行沿革，不是目前仍在執行的工作。完成覆蓋與發布依據以 `tdcc_collection_status_complete_2145.json`、`tdcc_history_completion.json/md`、`tdcc_history_promotion_plan.json` 為準；不要由本歷程的小批或舊 PID 判斷整週完成。

> 本機解析續跑保留第一期完整結果並核 SHA，新單次解析後端以 414 个真實回應逐值對照原方法零差、18 個 focused fixtures 通過；證據 `tdcc_normalization_backend.json`。這是本機解析效率調整，沒有增加官方請求。50 筆無資料是兩次來源事實，不能直接推定券種資格或擅自填零。

公開來源已實測可查最近一年單券歷史。官方 [TDCC 表單](https://www.tdcc.com.tw/portal/zh/smWeb/qryStock) 提供逐券／日期查詢；[data.gov 資料集 11452](https://data.gov.tw/dataset/11452) 只有現期 CSV 資源，[OpenAPI](https://openapi.tdcc.com.tw/tdcc-opendata-api-docs) 的 `/v1/opendata/1-5` 沒有日期參數。本輪沒有查到官方歷史 bulk export。這不是認定近期資料需要付費。

既有 FinMind token 已實測 2021-09-10 整批請求，HTTP 400、API 400，回覆帳號層級 register、無此資料權限。詳見 `tdcc_finmind_access.json`。token 僅送原 API 的 Authorization header，沒有輸出或改帳號，也沒有購買。來源 [FinMind 官方資料契約](https://finmind.github.io/tutor/TaiwanMarket/Chip/) 確認此資料集可按單一日期整批取得，但需要適用授權。

收集器：`scripts/backfill_tdcc_public_history.py`。每週先凍結前、後相鄰正式 TDCC raw 的券碼聯集及來源 SHA；包含非模型股票與其他證券，並未用最新模型篩選名單縮小歷史範圍。此聯集是透明、可驗證的收集範圍，仍不能宣稱等於官方當期證券清冊。

| 官方資料日 | 凍結目標券數 | 小批驗證 | 初始剩餘 |
|---|---:|---:|---:|
| 2025-10-09 | 3,861 | 4 | 3,857 |
| 2025-10-23 | 3,863 | 4 | 3,859 |
| 2026-02-26 | 3,935 | 4 | 3,931 |

小批是 2330、2357、2603、6488 × 三期，共 12 次真實查詢；另驗證非模型券 000218 一次成功。每個回應均核對回應中的券碼／民國日期、15 個唯一級距及級距文字、非負正常數值、全部人數與股數總和、官方差異數調整、每級百分比與股數相符。2026-02-26 的2330含真實 -4,999 股調整，並未因為負值就誤刪。

12 次平均查詢耗時 0.1244 秒，加每次完成後 1 秒節流，估算剩餘全範圍約 3.64 小時；這是小樣本吞吐估計，實際會受回應速度、驗證與 checkpoint I/O 影響。第二次同 12 券次執行查詢數為零，既有 raw 和 table SHA 重新核驗，並沒有重抓或新增重複紀錄。

每券保留原 HTML、研究用 15 級表格、來源／表格 SHA、request 日期／券碼、attempt 與驗證結果。每次進度原子寫入 checkpoint；transport/parser 失敗維持 unresolved，HTTP200 且 form 回顯券碼／期別精確的「查無此資料」另列 SOURCE_NO_DATA，不能填零或假裝整週完成。no-data 不會因 `--retry-unresolved` 而反覆請求；合法缺券原因仍需證據。HTTP 401／403 或重試後429、連續三個未解錯誤停下。輸出不會自動覆寫正式 raw 或重建 summary。

目前 TDCC 嚴格解析、resume hash、pacing、Retry-After 與 canonical 16/17 語意共 17 個 focused tests，與估值 9 個共 26 passed，receipt `valuation_tdcc_contract_fixtures_local_log`；先前外資 parser／staging 20 個已通過。所有驗收均在 guard 實體副本，pytest log 明確指向副本路徑。

15:20 初始全量 leaf PID9544 使用保守自訂1秒間隔；這不是官方公告限制，也沒有實測限流。依 root 授權，核對PID/parent/命令後停下、加入單session每次完成後0.25秒且任意1秒最多3次request開始的節流。先跑50筆（51次HTTP）無429/503、無transport error，receipt `tdcc_pacing_50.json`，再resume全11,659固定plan。429/503退回至少1秒並指數退避、遵守更長Retry-After；網路錯誤最多3次重試，POST不確定結果後透過正常GET更新CSRF。沒有增加併發。

15:52 再短停已核身的 leaf51752 以單session取四券9/4實際HTML，之後立即續跑。現在 leaf PID8444、runner52656、label `tdcc_public_full_history_fast_resume`，timeout21,600秒；日誌 `F:/stock/output/pipeline_acceptance_20260906/tdcc_public_full_history_fast_resume.log`。實體副本內 `output/historical_gaps_20260906/tdcc_public_collect/progress.json` 是進度，`plan.json` 固定SHA為 `c5df9a6d723986b141dd60ded68c2ee2fbff4e369797aa55f0fc38e38a41a878`。被明確中止兩次的runner FAIL是驗收操作中斷，不是資料語意失敗，保留原receipt不掩蓋。現在命令：

```powershell
python -B -X utf8 scripts/pipeline_acceptance.py run --label tdcc_public_full_history_fast_resume --timeout 21600 -- scripts/backfill_tdcc_public_history.py collect --limit 0 --delay 0.25 --retry-unresolved
```

若停下，使用新的 runner label 執行同一 `collect` 命令即可只抓未嘗試券；`--retry-unresolved` 明確重試未解券，仍保留舊 attempt。不要同時開兩個 collector。小批驗收完成不等於全週回補完成；正式發布前必須驗整週覆蓋、合理的當期券碼差異、raw→summary 欄位對帳，並用同一 9/4 原始日K比較補前後全部最新 TDCC 特徵（包含長前綴累積週數）。目前沒有因此改模型、策略、DB 或帳本。

舊未觀測格的最終分類另見 `tdcc_calendar_reconciliation.md/json`：35 格有交易日、1 格只有結算日、4 格全週正式休市、3 格近期可查。合法假期沒有填造 raw；研究分類亦未改變正式特徵格點。

`scripts/normalize_tdcc_public_history.py` 會由保存HTML重建canonical raw語意：正常1–15、實際差異調整16、實際合計17。若HTML的合計序號為16，仍依「合計」欄意映為17；未出現差異列時不造level16零列。`tdcc_source_contract_reference.json` 記錄真實9/4四券對照：public65列、官方bulk68列；正常15級及合計所有數值exact，actual `_summarize_tdcc` 的 Retail_Pct／Whale_Pct／Total_Holders 亦exact。6488的HTML差異調整為-4000股、人數空白，bulk則+4000股、人數1，兩來源第16列不能宣稱逐值等同；保存HTML實際負號与空白，summary按正式契約排除16/17，總戶數以15正常級之和計算。2330/2357/2603的bulk零差異列在HTML省略，不硬補。這個四券驗收是來源格式證據，並不代表全週收集已完成。

`scripts/audit_tdcc_collection_status.py --label 唯一標籤` 可在不中斷收集的情況下凍結讀一份checkpoint，逐一核對no-data原HTML SHA及鄰週存在性，輸出只讀報告。15:58 snapshot顯示第一週3282/3861已查（3264有效、18官方no-data），其餘兩週各4小批；當時未解transport/parser為0、已記錄事件2539次中429/503為0。這不是最終進度；以checkpoint及後續status receipt為準。
