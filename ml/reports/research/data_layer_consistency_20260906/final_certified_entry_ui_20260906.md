# 最終 certified entry UI 驗收

**PASS**。實際觀測：2026-09-07T00:02:34.301946+08:00（跨午夜，任務標籤仍為 20260906）。

新 HTML：`c951a38444bd5ac1ee24e1c2863528787faa27cb258d2b6d7a9102d4089787a9`，9,489,910 bytes；正式檔、測試副本與 8876 HTTP bytes SHA 全相同。來源為成功的 `02_html.finished.json`。

與該次 run 的備份相比：HTML/CSS/JS 範本完全相同，完整 payload 唯一差異是 `meta.generated` 從 `2026-09-06 23:04` 更新為 `2026-09-06 23:57`；候選、模型數值、风险文字與所有其他資料完全相同。

|檢查|桌面 1440×1000|手機 390×844|
|---|---|---|
|卡片／rere|18／6|18／6|
|資料、模型、集保、雷達日期|2026-09-04|2026-09-04|
|交易觀察日|2026-09-07|2026-09-07|
|整頁水平溢出|無|無|
|18 卡 opacity|全部 1|全部 1|
|JS／console errors|0|0|

rere 六檔依原 payload 順序仍為 3605、3189、3066、2364、3176、6472；60 交易日、模型不否決、人工確認與最小試單界線維持。3605、3189、2364 的風險文字保留正常亮度。卡片為觀察與待確認，沒有變成已成交訊號。

實際點擊 3066 卡片展開／收合；手機 DOM 讀回 `open=true`，收合後 `false`。已目視桌面頁首與手機展開卡片，文字完整、資訊層次清楚。手機下方既有帳本表格保留容器內水平捲動，不撐寬頁面。

本次使用實際 Chromium 的兩個 viewport，不宣稱全部瀏覽器或實體手機均已測。沒有對正式 Web 發冷啟動請求，Web/API 由 root 另驗。沒有改正式 HTML/程式/來源、發佈、寄信或 commit。固定 Artifact 尚需獨立原網址重發，不能把 GitHub push 稱為 Artifact 更新。

截圖：

- [final_certified_desktop_expanded_20260906.png](F:/stock/output/playwright/final_certified_desktop_expanded_20260906.png)
- [final_certified_desktop_header_20260906.png](F:/stock/output/playwright/final_certified_desktop_header_20260906.png)
- [final_certified_mobile_card_20260906.png](F:/stock/output/playwright/final_certified_mobile_card_20260906.png)
- [final_certified_mobile_expanded_20260906.png](F:/stock/output/playwright/final_certified_mobile_expanded_20260906.png)
- [final_certified_mobile_header_20260906.png](F:/stock/output/playwright/final_certified_mobile_header_20260906.png)
