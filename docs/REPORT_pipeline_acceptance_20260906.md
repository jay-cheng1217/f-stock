# 9/6 即時流程驗收

可以現在驗收，而且本輪已用 9/4 真實收盤資料，實際重跑到 9/7 觀察名單、帳本複本、卡片及郵件預覽。
核心資料到輸出鏈通過；**不能核定「全系統健康完善」**，因為單股資金集中風險仍待處理。
**14:15補驗：使用者結束舊程序後，新版Web已成功啟動，健康API的8项檢查全數正常，原部署阻擋已解除。**
這不是必須等開盤才能查的問題；下文保留最初權限阻擋的歷史證據。

## 已執行的證據

| 範圍 | 結果 | 實際證據 |
|---|---|---|
| 官方資料 | PASS | 最新5交易日 TWSE／TPEx 實際官方校验；修補0列。財報 audit gaps=[]；處置契約完整 |
| 資料匯入 | PASS | 從 CSV 新建 DuckDB，daily_k 2,974,036列；五張主表皆非空且同代匯入 |
| CSV／DB 一致 | PASS | 9/4有效1,911檔完整 outer join，OHLCV逐值差異0、重複0、必要欄位空值0 |
| 特徵／模型 | PASS | 新建 raw snapshot；既有 Champion 實際推論1,080檔，耗時1,055.91秒；dataA用同新快照 |
| 日期契約 | PASS | 新raw含1,076檔9/4與4檔舊日期，保留各股實際日期；18候選全部來源9/4、交易日9/7 |
| 主策略訊號 | PASS | 正式既有風控後 unified 1檔2603；未因候選少而放寬門檻 |
| rere／shadow 帳本 | PASS | rere 236筆、二次record/check位元組穩定；6筆9/7仍pending。shadow二次無重複、24個完整配對日期 |
| 新 Champion 帳本 | PASS（有限範圍） | 真正新增run=1／pending=1，二次無新增，SQLite完整性與唯一鍵通過。只有9/4行情，不能驗未來成交後mark／exit |
| 卡片 | PASS | 新建18張觀察卡，rere獨立6張；模型不否決rere。桌面／手機呈現見UI附報 |
| 郵件 | PASS（預覽） | 真正composer產生UTF-8 plain/html base64郵件；使用.invalid測試地址，沒有寄出交易報告 |
| 健康API修正 | PASS（隔離及現行服務） | 14:14新PID13804啟動；14:15真HTTP健康8項全ok，首頁／股票API均200 |
| 整套Windows排程 | NOT_RUN | 已唯讀查實際排程與歷史log，沒有藉验收觸發重訓或正式帳本重算 |

「執行 exit 0」與「業務語意通過」分開留存。所有步驟的命令、時間、回傳碼與log SHA在
`ml/reports/research/pipeline_acceptance_20260906/stage_receipts.json`；詳細輸入、帳本、UI、隔離guard
與輸出雜湊也在同目錄。前置方法見 `docs/METHOD_pipeline_acceptance_20260906.md`。

## 實際發現與修復

1. **健康API誤判。** 它另開DuckDB唯讀連線，與同程序既有RW連線設定衝突，導致健康查詢fail；
   一般股票API仍可讀9/4。改用共用engine的獨立cursor，只關cursor，不改資料或模型。
   真實temp DB回歸3項通過，含真stale仍fail；實際ASGI另外驗401／200及9/4的1,911檔。
2. **排程假成功。** 9/5 weekly metadata已完整寫all_ok=false、AgentArena failed，Windows卻顯示0。
   修正main在metadata完成後回0／1，CLI轉交；finally與服務復原保留。10項回歸通過，包含失敗不重訓第二次。
   此修復只涵蓋已記入step_status的失敗；若上游wrapper自己吞掉run_job結果，仍需另補契約。
3. **郵件語意混淆。** 「目標權重」改為明確配置上限並揭露集中風險；momentum研究表不再標為正式放行；
   日K碰區間不再說成已證明盤中守穩；退役ChipK不再宣稱已更新。未動任何數值、排序或策略gate。
   19項聚焦測試通過，修後重新走實際composer與MIME驗收。
4. **AgentArena讀取衝突。** 真temp DB重現同程序RW／RO設定衝突、copy又被Windows鎖住的鏈條。
   configured path改用共用cursor，自訂路徑保留原行為。26項測試通過；83.35秒真資料leaf讀到
   兩股250列及9/4全市場1,911列，13項檢查全過、DB hash／mtime不變。歷史log僅留下外層錯誤，
   根因由程式與重現強支持，沒有冒稱歷史trace已直接證明。未重跑完整競賽或重訓。

所有相關修復合併驗證：8個相關測試檔共 **71 passed in 2.46s**。這是聚焦回歸，沒有宣稱全repository suite。
卡片再補正模型診斷與unified放行的界線；手機郵件來源長檔名的18px水平溢出已修復。
動能研究表改為每股主列＋全寬結論，手機垂直標示欄名與數值，不再把結論擠成每行2–3字。
390px手機的同5股表格由7,041.9px降至3,598.7px，縮短48.9%；結論可用寬346px。
逐列比對5股×7欄的值／日期／判斷／順序相同，其他郵件區段相同；1440／390px皆無水平溢出。
使用真正HTML欄名與class／media樣式，避開未列入[Gmail官方支援](https://developers.google.com/workspace/gmail/design/css)
的pseudo-element生成欄名；這是相容性設計，未冒稱所有郵件客戶端均已實機驗證。

## 未解風險與營運邊界

**資金集中是本輪最重要的新問題。** 同一份9/4訊號，用正式asymmetric_v2配置器建立空白帳本，
會為2603建立名目權重99.85%的pending部位。既有Champion複本的9/4 run已鎖定、可用帳本資金0，
所以沒有新買2603；但原帳本本身僅有2357一筆open、名目配置100%。這是紙上帳本配置，不能当成券商實際部位
或即時市值權重。推薦名單的20%產業上限並未等同於配置器的總資金硬上限。

建議將單股／產業配置上限獨立放在資金分配與加倉層，候選不足時保留現金；rere條件與模型選股順序保持原樣。
固定9/4訊號下10%／20%上限的純配置敏感度見 `allocation_risk.md`；那不是歷史回測、沒有勝率／MDD改善的結論。
正式資金政策仍待使用者決策，再以包含費稅、停損、既有倉位和可用現金的歷史配置回測及fixed-snapshot A/B落地。
本輪沒有偷偷套20%上限，也沒有把顯示文字修正當成資金風險已消失。

**Web部署已完成（14:15補驗）。** 13:28左右停止8001 listener PID21164曾遭Windows拒絕；
使用者隨後親自結束舊程序。14:14:35以原host／port啟動新版PID13804，14:14:38完成startup。
14:15:27認證GET `/api/pipeline/status`回200、status=ok、all_ok=true、stale=false，8項檢查全ok；
首頁及2330近期日K API亦回200、最新資料9/4，共1,911檔當日日K。cloudflared仍是3624，未重啟隧道。
10個模型／metadata與6個帳本／名單檔的SHA共16項全相符；啟動讀取既有DB與snapshot快取，未重訓或重新ingest。
原始阻擋紀錄保留，最終真服務證據見 `web_live_deployment.json`、`health_deployment.json`。

**流程正確不等於策略有效。** rere目前完整60交易日cohort仍是0，不可只用135個早停損樣本判其無效，
也不能由這次重播聲稱獲利已驗證。主策略／rere回測與模型洩漏修復沿用前輪證據，見
`docs/REVIEW_strategy_integrity_20260906.md`。本輪未重訓、未升降模型、未調rere或績效基準。

其餘限制：特殊交易狀態自動資料本輪只驗處置；注意股／全額交割／暫停交易仍非完整覆蓋。
週末不能驗9/7新行情發布與盤中觸發；人工分點、主力成本仍需來源。正式Artifact同網址重發尚未完成，
不能把GitHub HTML更新或本機卡片驗收稱成Artifact已更新。

## 隔離與來源完整性

實體副本位於 `F:\stock\output\pipeline_acceptance_20260906\workspace`。CSV特徵鏈與DuckDB/API鏈分別驗收，
沒有誤認ML全部讀DuckDB。新DB採真ingest；SQLite採read-only backup API；沒複製郵件憑證或.env。
部分metadata內原本含正式模型絕對路徑，因此模型本體唯讀使用正式已驗pin；registry validate通過，沒有改模型文件。

來源manifest核對17,021檔：程式差異是本輪已授權修復／驗收工具；交易資料、保護模型、rere／shadow／Champion
帳本沒有被驗收改寫。唯一非程式byte變動是kol_tracker.db：13:15既有SerenityFeedCollector令internal
sqlite_sequence.sources由1373增至1374；五個業務表31,242行全欄hash完全相同。排程／log／mtime互相吻合，
高信心歸因外部collector；未取得OS逐process寫入trace，詳見source_integrity_followup。

早期官方／ingest步驟先以實體副本隔離，guard稍後才安裝，沒有將所有步驟冒稱為作業系統sandbox。
後續guard程序的拒絕紀錄為0；guard自我測試覆蓋13種禁止副作用及nested Python啟動。
驗收準備的三個失敗（helper尚未複製、PENDING大小寫假設、資金診斷缺AGENTS.md）均保留receipt，
修正後用新label重跑，不把研究腳本準備失誤混稱正式策略失敗。
正式UI是另外有意執行的呈現修復：以原9/7 canonical重建HTML，差異只有3行；canonical、watchlist與
三種觀察帳本、Champion SQLite的SHA不變。HTML同步既有kol-watch GitHub，不等於Artifact已重發。

## 本審查期間變更分類

| Commit | 訊息／範圍 | 白名單分類 |
|---|---|---|
| b8a39a52 | fix(strategy): enforce dated candidates and complete forward cohorts | 資料日期與策略驗證契約；rere方法未改 |
| ca05db95 | fix(research): align pattern backtests with dated inputs and costs | research-only回測修復 |
| 9b12d26e | fix(training): isolate V2 validation from forward test labels | training resource／驗證隔離；未重訓 |
| d2659bbd | fix(dashboard): clarify dated observation cards and strategy scope | production-adjacent呈現 |
| 67289eda | docs(skills): align shared strategy evidence and review handoff | skills／治理交接 |
| a492023（kol-watch） | fix(dashboard): publish dated strategy observation cards | 外部dashboard HTML發布；非Artifact重發 |
| 61db15ca | fix(pipeline): report failures and reuse the health database connection | infra/pipeline repair；未改模型或資金 |
| 1812711b | fix(email): distinguish research candidates and allocation risk | 呈現／通知修復；未改數值與gate |
| 19a92fe4 | fix(arena): reuse the configured shared database reader | infra/pipeline repair；未改策略或模型 |
| 5b08bc59 | fix(dashboard): clarify model warnings and wrap mobile email text | 呈現修復＋既有正式HTML重建 |
| ce3a7d1（kol-watch） | fix(dashboard): clarify prediction warning scope | 外部既有HTML同步；非Artifact重發 |
| ba74fca4 | fix(email): present mobile entry observations as labeled rows | 呈現修復；值／順序／判斷不變 |

報告、驗收工具與證據隨最終 `test(pipeline): record isolated acceptance and unresolved allocation risk` 提交；
該提交的hash在結案回覆。主工作樹仍保留原有其他日常產物與未提交模型metadata，不歸本輪所有且未混入commit；
任務相關修改均已提交。kol-watch發布後工作樹乾淨；既有排程的1fec542另有來源，不是本輪手動發布。

14:01已向既有設定收件者寄出1封UTF-8驗收結果通知，含資金風險、管理員重啟與Artifact待同步的限制。
這是維護通知；交易報告本輪只產預覽。SMTP結果見 `completion_notification.json`，沒有保存收件地址或憑證。
