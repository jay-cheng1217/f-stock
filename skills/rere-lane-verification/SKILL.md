---
name: rere-lane-verification
description: Verify rere system-proxy forward cohorts and backtests while preserving the KOL playbook. Use for rere 驗證、帳本、追蹤進度或是否應調整權重。
---

# rere 前瞻驗證

系統掃描只是 rere 方法的可計算代理，並非 KOL 本人薦股或實際成交。五個子型態分開記錄：
蹲點(shakeout)、發動(ignition)、淺洗盤(shallow，2026-09-24 PM 核准，8%≤洗盤<10% 其餘同
蹲點，BT-rere-shallow-wash 背書)、**蹲點v2(shakeout_v2)、淺洗盤v2(shallow_v2)**(2026-10-07 PM
核可，BT-rere-no-foreign-turn 12/12 PASS：同帶但不要求「外資前5日賣→當日買」；只在現行型態不成立時
掛 v2 標籤，另計 6 檔上限，從零累積、與現行子型態分開評估，現行 forward cohort 零變動)；
模型分數不能否決 rere，固定小倉。訊號與主力成本／盤中人工確認顺序見
`skills/stock-entry-decision-workflow/SKILL.md`。

## 唯一帳本介面

```
python -X utf8 scripts/rere_lane_tracker.py record --plan logs/entry_list_<YYYYMMDD>.json
python -X utf8 scripts/rere_lane_tracker.py check
```

**並列模擬(2026-10-07,唯讀對照、不作自動判決)**:`check` 另寫 `rere_lane_ledger_nostop.csv`(同批訊號不停損抱 60 棒)與 `rere_public_campaign_book.csv`(`ml/data/rere_public_campaigns.csv` 所列她已公開標的 ∩ 系統訊號,公開日 ≤ 訊號日),報告內並列。評估停損規則時兩組都要報;campaign 樣本極小且偏向獲利者,不得當選股依據。

正式帳本 `ml/reports/rere_lane_ledger.csv` 不得手編。只記 go/small 的 rere lane，
veto/watch 不入新倉；依 source_date+ticker 去重，保留 subtype，舊未知欄位不得猜測回填。
測試與歷史重播必須用獨立 `--ledger`、`--report`；`--as-of` 不得覆寫正式帳本。

## 計算慣例

- 進場＝訊號後的 trade_date 收盤，是觀察帳本代理，不是盤中區間成交。
- 失敗＝進場次日起，至第60個後續交易棒內，還原收盤低於訊號當時 MA60×0.97。
- 到期＝第60個後續交易棒收盤；補跑不能讓第61日以後停損取代已到期結果。
- 現行帳本沿用 close＋持有期間累計權息值的毛報酬；進場日事件排除。
  分割／減資／股數變動仍有已知限制，不得冒稱完整公司行動投資組合績效。
- 歷史研究需完整60日後續資料才列入可比樣本，另列成本情境。

## 公平比較

不能用「提前停損已結算、其餘仍持有」的勝率判定策略失效。可比 cohort 必須每筆自進場起
都已有60個後續交易棒的觀察機會，包含同梯早停與到期結果。

1. 先報持有、提前結算、待進場與完整 cohort 數。closed-only 統計只作進度描述。
2. 完整 cohort <30：結論「累積中」，可報風險，不能自動降權。
3. ≥30也非獨立樣本充分證據：分蹲點／發動／未知、同期間市場、成本、重複股票與重疊日期
   評估。權重或出場變更仍需事前方法鎖與對應驗證。
4. 舊 +4.58%/35.8%（2026-07-15）及 +9.40%/50.6% 只保留為歷史參考，不是現行及格線。
   2026-09-06 確認旧基準 wash公式、日量/均量、未成熟樣本與現行 generator 不一致。
5. 新研究 `ml/reports/rere_baseline_integrity_20260906.md`：蹲點9,154成熟訊號，毛均+3.40%、
   毛勝率34.2%，每邊10bps滑價情境淨均+2.59%；發動僅13筆，樣本不足。
   不含歷史模型股票池/top6/族群排序/盤中執行，不能直接當正式名單績效及格線。

重現研究：`scripts/backtest_rere_lane_baseline.py --help`；隔離帳本A/B：
`scripts/audit_rere_verification.py --help`。方法見 `docs/METHOD_rere_verification_20260906.md`。

回覆用淺白中文，包含資料日、完整 cohort 數、型態別結果與限制。不混淆觀察帳本、
歷史型態樣本、KOL實績與資金組合NAV。
