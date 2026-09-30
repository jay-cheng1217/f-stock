# 選股卡片修復與驗證（2026-09-06）

本次為呈現層修復，不更動產生器的型態門檻、排序分數、區間、停損或模型選擇。
使用者要求保留 rere 方法；rere 的模型不否決、最小試單與 60 日觀察慣例均保留。

## 修復範圍

- 以「狀態 → 下一步 → 觀察區間／不追價／失效參考 → 證據 → 缺少確認」呈現。
  盤後 `go` 不再標成盤中已可觸發；來源日期缺失、跨日或資料品質未確認均清楚提示。
- `kind=veto` 原先被 `state_of` 默認成 small，現有獨立暫緩區。
- rere 區註明是系統依既有方法篩選，並非 KOL 本人推薦；不再附加
  「標準倉／七成倉」等與最小試單矛盾的信念分數升級文字。
- 模型值、完整理由與補充籌碼資料折疊保留。硬風險仍可見，型態門檻不變。
- 已平倉的混合策略勝率不再當首頁成效：早停損先結束會造成截尾偏誤。
  個別模擬交易與持有中損益仍可檢視，並明示不代表實際成交。
- 個股財務比率使用比例轉百分比：`ml/features/fundamental.py` 與 `revenue.py`
  的欄位是小數，原畫面直接加 `%` 造成 100 倍縮小。法人成本推估也明確區別於分點主力成本。
- 最終易讀性補強：Champion 自動策略風險閘用中文解釋實際原因，原始精確診斷留在折疊區，
  並與 rere 觀察條件區別。卡片補上有日期的上次收盤價及其區間位置；低於區間等站回、
  高於區間不追、落在區間也仍需盤中確認。未確認日期或晚於名單基準的價格不比較，
  這個描述不改任何 kind、順位或狀態。

## 補充籌碼日期規則（獨立呈現修復）

`ml/data/tdcc_whale_snapshot.csv` 實際停在 2026-07-03，舊卡片仍以該檔週變化
否決 2026-09-07 的 rere 候選。補充四面向／正式標籤／大戶雷達各自按現有
日頻或週頻 freshness 規則檢查；缺失、過期或超出名單日期的來源不得參與卡片否決。
缺資料時轉人工確認，不以空資料宣稱籌碼通過；當期大戶負向證據仍可否決。
過期大戶雷達不再渲染。此修復不修改名單、風控帳本或任何模型數值。

## 共通測試與早期固定輸入驗證

- `python -B -X utf8 -m pytest tests/test_entry_dashboard_cards.py tests/test_entry_dashboard_freshness.py -q`
  ：17 passed。涵蓋 veto、legacy unknown、跨日、未來日期、失效區間、rere 不受模型分數否決、
  最小試單、當期／過期籌碼差異。
- 相同 720 組 lane／法人／大戶／散戶／市場條件，純文字修復前後 `_verdict` 分類差異 0。
- 早期固定輸入為 `logs/entry_list_20260907.json` 的修復前版本，SHA-256
  `5eed4a2d7425f1c1c117cdc4e770ee3daff4d48f0c1b26afbba246fbb66e9f12`。
  該版本的 18 列 ticker、kind、priority、zone、stop、no_chase、strategy 在呈現修復前後完全相同。
  日期修復僅改變 7 個卡片補充判斷：3189 skip→check；5471、3044、3661、1434
  ok→check；4949、5434 check→warn。後兩者不再用過期模型／大戶資料製造強勢抵銷。
  這是隔離 UI 變因的早期 A/B，不能當成整合後名單的變動統計。
- 早期 fixture 缺少 `data_status`，所以預覽顯示待資料確認，並非新增策略否決。
  其摘要保存在 `output/playwright/entry_cards_qa.json`；同名預覽 HTML 和截圖後來已更新為下列正式版本。
- 3605 原始比率：營收年增 0.2981、毛利率 0.2516、營益率 0.0638；實際面板顯示
  29.8%、25.2%、6.4%。瀏覽器無 JavaScript 錯誤。

## 最後正式名單重建與實際 QA

整合者完成資料對齊後，本分工依授權執行
`python -B -X utf8 scripts/entry_dashboard.py --date 20260907`，
使用更新後的 `logs/entry_list_20260907.json`，已重建正式檔案
`ml/reports/entry_dashboard_latest.html`，並複製到同名本地預覽。
該正式名單的 SHA-256 為
`02f657a10432073ac411c75039accd423f776e4289f0970f65774e2a06fdf65e`。

- 最後正式 HTML SHA-256：
  `0d3e7bd4853d480d431e73e21f2e9cb6477aa744faa748b88f756af94cc49732`。
  已再次讀檔核對；本地預覽與正式 HTML 位元組完全相同。
- 共 18 檔，所有卡片型態日期、模型日期與有日期的上次收盤價均為 2026-09-04。
  rere 六檔為 3605、3189、3066、2364、3176、6472，皆保留 small，顯示「觀察 · 待盤中確認」。
- 2026-07-03 大戶雷達在來源列明確標示過期，雷達列表為 0；rere 沒有被該舊來源否決，
  3189 顯示需人工確認。這不代表已完成盤中或人工籌碼確認。
- 白話風險閘顯示實際的名單攔截比例與模型分歧原因，原始診斷折疊保留，並區分 Champion 自動層。
  3605 的 9/04 收盤 118 低於區間下緣 118.04，卡片提示先站回；3066 收盤 24.7 位於區間內，
  卡片明示僅為上次收盤位置，盤中仍待確認。價格位置不改 kind、順位或卡片狀態。
- Playwright 實際檢查 1440×1080 桌面與 390×844 手機；18 張卡片完整，手機
  `scrollWidth=innerWidth=390`。折疊可開關且不誤開面板，個股面板正常，Console Errors 0 / Warnings 0。
  缺資料 watch 的渲染 fixture 顯示警示色，沒有被畫成通過狀態。
- 最後 QA 檔案：`output/playwright/entry_cards_final_qa.json`、
  `entry_cards_20260906.html`、`entry_cards_desktop.png`、`entry_cards_mobile.png`。

## 發佈交接

本分工已完成上述正式 HTML 重建與本地 QA；沒有對外部署或寄信。
發佈與使用者通知由 root 整合者處理，依 dashboard-interface-update skill 回報既有網址的實際更新狀態，
並提醒 Ctrl+F5。正式檔案建置完成不等於遠端 Artifact 已更新。
固定網址：https://claude.ai/code/artifact/8705d1fe-77ce-411e-a162-895c7c8fb22b
