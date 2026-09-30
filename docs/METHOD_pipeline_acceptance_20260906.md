# 流程驗收方法（2026-09-06，實作前鎖定）

使用者要求現在驗收。使用 2026-09-04 已收盤真實資料，目標交易日 2026-09-07。
本次是隔離環境的整合重播：使用正式程式的葉節點，實際建立 DuckDB、運算特徵、
執行既有模型推論、產生訊號與名單、更新帳本複本、建置卡片及郵件預覽。
不以 mock 的成功回傳當作流程通過，不重訓、不改模型或 rere 策略。

## 隔離

- 真正複製程式與原始資料至 output 下獨立 workspace，不使用 junction、symlink 或 hardlink。
- 不複製 .env／郵件憑證；所有輸出、DuckDB、SQLite 帳本與快取指向副本。
- 不執行 smart_update_auto 總入口、smart_update.exec_ingest 或 daily_pipeline --predict-only：
  它們有固定 port 8001 的服務控制、正式帳本、寄信／發佈副作用。
- 逐步執行相同底層實作；服務重啟、SMTP 實寄、GitHub／Artifact 發佈、Windows 排程觸發
  明列未驗證，不偽稱完整無人值守營運驗收。
- 來源模型／帳本／日K等記 SHA256；結束核對原始檔未變。保護既有工作樹異動。

## 事前通過標準

1. 官方日K校驗（最近5日）及處置契約辨識雙市場、日期與完整性；真失敗列 FAIL。
2. 財報 audit 不 heal，必要資料 gaps 必須為空才稱正常鏈通過；外部選配來源與限制另列。
3. 實際 ingest 的五張資料表皆存在、非空，日K最新有效日期為9/4，沒有 ticker/date 重複。
4. 從同一組已校驗原始資料建立新 raw snapshot，不讀舊 snapshot 當作新推論。
   實際 `ml.dataset` 特徵來源以 CSV 為主，DuckDB 供 API／部分 unified 風控使用；兩者均驗收，
   不把「DB 匯入完成」冒充「ML 必定讀 DB」。既有模型檔 hash 不變。
5. predictions／dataA／unified／候選的資料日須對齊；候選交易日9/7、逐股來源9/4。
   風險閘正確阻擋而沒有候選是合法業務結果，不用強制放行來製造成功。
6. rere、shadow、Champion 帳本僅更新複本；重跑不重複新增；不能有未來日期成交。
7. 卡片與 canonical JSON 一致、日期明示；郵件只生成 UTF-8 預覽，不寄送交易報告。
8. 結案分開列 PASS／FAIL／BLOCKED／NOT_RUN。發現真缺陷先重現及量化，再依既有授權修復；
   不因验收不過而調整策略門檻、偽造來源或繞過 gate。

本次可驗資料到卡片的整合行為；下一個真交易日的新資料發布、盤中觸發與长期策略績效，
仍不能由週末重播證明。

## 執行中範圍補充（保留原始方法與失敗證據）

- 早期官方來源／ingest 階段已使用實體複本；Python audit guard 隨後安裝，從 guard preflight 起
  留存每個子程序的安裝／拒絕紀錄。此 guard 不是作業系統 sandbox，不能宣稱攔截所有 native I/O。
- model metadata 含正式模型絕對路徑，因此模型內容僅唯讀使用原始已驗 SHA 的 pin；未改 metadata
  或重新註冊。其餘特徵、帳本、輸出皆使用複本。正式 registry 另行唯讀 validate。
- 首次 input check 在 helper 尚未複製時失敗；初始 config copy 漏 retired_tickers.csv 已補正並核對
  無數值影響；新帳本驗收第一次對 fill_status 大小寫的假設錯誤，後改用 canonical PENDING 常數。
  資金診斷首跑另因沒有複製AGENTS.md而失敗，補檔後以新label重跑。
  這些是驗收準備缺陷，保留三份失敗 receipt，不當成正式策略缺陷，也不刪除或覆寫原失敗帳本。
- 新空白 Champion SQLite 額外驗證真正新增／鎖定／去重。資料截止9/4，僅可新增 pending；
  Date > 9/4 的 mark／成交／出場分支不以假行情補出。
- 真實驗收發現 health reader、pipeline exit、郵件語意及 AgentArena DB reader 問題後，
  依既有修復授權進行最小修復與對應驗證，不調模型、rere 或資金配置。
- 13:28 左右嘗試將已測試 health 修復載入原 Web；Windows 拒絕停止 PID 21164，後续启动未執行。
  正式 Web／tunnel 保持原程序。改驗實際 ASGI middleware／routes＋共用 RW DuckDB，明列部署 BLOCKED。
- 空帳本單一候選配置接近100%的結果另列資金集中風險。僅產同訊號10%／20%上限敏感度情境，
  不把情境計算稱回測，不改正式資金規則；資金上限需使用者作最終決策。
