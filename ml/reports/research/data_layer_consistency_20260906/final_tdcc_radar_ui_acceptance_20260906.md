# 最終HTML實際瀏覽器驗收

結果：**PASS**，觀測 2026-09-06T23:11:33.921515+08:00。只測成功resume收據對應的新HTML。
正式／測試副本／HTTP回傳SHA一致：`2f42e26080846e6bf0691b1b1d49529e16b130025fca242214e99c8485c28113`，9,489,910 bytes。

|檢查|1440×1000桌面|390×844手機|
|---|---|---|
|實際卡片数|18|18|
|rere六檔|3605、3189、3066、2364、3176、6472|相同|
|資料／模型／集保／雷達日期|2026-09-04|相同|
|觀察日期|2026-09-07|相同|
|頁面水平溢出|無，document/body=1440|無，document/body=390|
|18卡opacity|全部1|全部1|
|JS／console errors|0|0|

已真實點擊展開首張卡片，讀回open=true，再點擊收合；手機量測全部收合。卡片呈現有日期的盤後收盤與相對區間，仍要求盤中15–30分守穩與人工籌碼確認，沒有寫成已成交或即時買點。rere六檔保留60交易日、小試單、模型不否決與非KOL本人推薦的界線。

3605、3189、2364的最新籌碼發散提示保持完整亮度，沒有因skip而變淡；其餘rere條件觀察卡亦保留模型僅參考。頁首模型分歧警示與Champion unified正式放行區分。

手機下方部分既有表格使用容器內水平捲動，沒有撐寬整個頁面。本次為實際Chromium152瀏覽器兩個viewport驗收，不宣稱已測所有瀏覽器或實體手機。

首次HTML直接CLI import錯誤收據保留；root修正root importpath後重跑成功才截圖。既有Artifact仍須原網址獨立重發；私有GitHub repo has_pages=false，push不能稱作Artifact更新。正式Web/API由root另驗，當前app-only修補不改本次HTML。

截圖：
- [final_tdcc_desktop_expanded_card_20260906.png](F:/stock/output/playwright/final_tdcc_desktop_expanded_card_20260906.png)
- [final_tdcc_desktop_header_20260906.png](F:/stock/output/playwright/final_tdcc_desktop_header_20260906.png)
- [final_tdcc_desktop_rere_20260906.png](F:/stock/output/playwright/final_tdcc_desktop_rere_20260906.png)
- [final_tdcc_mobile_card_20260906.png](F:/stock/output/playwright/final_tdcc_mobile_card_20260906.png)
- [final_tdcc_mobile_header_20260906.png](F:/stock/output/playwright/final_tdcc_mobile_header_20260906.png)
- [final_tdcc_mobile_risk_20260906.png](F:/stock/output/playwright/final_tdcc_mobile_risk_20260906.png)

未改正式HTML、模型、策略、帳本；未push、寄信或commit。僅寫本報告與ignored測試／截圖檔。
