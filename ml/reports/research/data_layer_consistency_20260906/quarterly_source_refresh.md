# 季報來源刷新防復發驗收

已修正三個 producer 的最新應申報季度選擇：即使現有檔案覆蓋超過 80%，每次仍重新查官方來源。新資料按字串 Ticker 原子合併，保留當前官方清單已無、但過往已申報公司的該季舊列；官方真 NaN 不補 0。來源需通過雙市場、唯一鍵、期別與完整 schema，失敗保留原檔。

financial 的實際 h2 必须包含 ROC 年、季及市場。EPS/BS 的 h2 只有季度/市場，最新季另外在同次執行取得官方 ci OpenAPI 的明確年季，核對每市場 5 個共同 ticker 的 EPS，或資產/負債/權益/每股淨值。保存完整 HTML、API JSON 的 SHA 與請求/知識時間，不捏造 HTML 年份證明。歷史 EPS/BS 仍須誠實標示 request-year 限制；最新 API 不用來認證歷史年。

heal=True 即使原有覆蓋率綠燈也調用三 producer，子程序非零/timeout 保留為 gaps；heal=False 不網路、不啟動 producer。EPS CLI 失敗現在回非零。取捨：financial 夜間 Step8 與 audit 可能各抓一次，採已同意的正確性優先方案。

59 個 focused tests 通過。另用真實 canonical SESSION、已凍結 Q2 HTML、當日官方 API 執行四組 EPS/BS 雙市場年份/值錨點，全部 PASS；原始證據為 output/quarterly_refresh_contract_20260906/real_source_anchor_acceptance.json。沒有執行正式 pipeline、重訓或修改 DB/模型/帳本，Q2 C 候選不變。完整命令、證據 hash 及白名單見相鄰 JSON。

補充：官方完整新資料可修復舊檔錯欄/數字；僅未被官方覆蓋的舊申報列若無法驗證則保留原檔並失敗。BS 已知資產＝負債＋權益需誤差不超過 2 原始單位，缺科目維持 unknown。audit 子程序失敗另保存最多各 16,000 字的 stdout/stderr 診斷。
