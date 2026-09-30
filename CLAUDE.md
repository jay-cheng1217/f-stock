@AGENTS.md

# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 工作流程

1. **先檢查 git status，保留既有異動；完成後只提交本次任務的明確檔案**。遵循 `skills/git-commit-after-change/SKILL.md`，不得把其他任務或每日 runtime 產物一起提交。
2. 操作過程中產生的新代碼或資料，也要在適當節點 commit。
3. 長時間任務（回補資料、訓練模型等）要定期回報進度。
4. 不要問 yes/no 問題，直接做。只在真正需要用戶決策時才問。
5. 遇到錯誤或超時自動重試，不要叫用戶手動執行。
6. **排程/自動任務失敗時，先讀 `logs/smart_update_auto_YYYYMMDD.log`** 找 traceback 與時序再動手修；其他背景任務也應優先翻對應 log（`logs/pipeline.log`、`logs/retrain_*.log`、`logs/web_error.log` 等），不要憑猜測改代碼。

## 必讀 Skill / 共享知識

- **skill 對照與開工前必讀清單:見上方 import 的 AGENTS.md「Required Startup Step」**
  (單一來源,Claude 與 Codex 同一套;此處不再重複維護)。
- 策略/績效/資料缺口單一真相:`docs/STRATEGY_overview.md`;rere 側寫:`docs/rere_strategy.md`。
- **新知識/新工具/判決變更 → 同一個 commit 內同步進共享檔**(AGENTS.md 或 docs;
  Codex 看不到 Claude memory,memory 只放指標)。

## 溝通

- 使用繁體中文
- 簡潔回報，不囉嗦

---

## 共用系統知識(已移至 AGENTS.md,經 import 載入)

常用指令、系統架構、**ML 預測解釋財務常識防呆規則(規則 1-14,三層架構)**
全部在 AGENTS.md——Claude 經第一行 import 取得,Codex 直接讀取,單一來源。

---

## 開發規範

- Commit 格式與型別:見 AGENTS.md「Git Commit Policy」(範例:`feat(ml): add t1 momentum predictor`)。
- 過濾器 / 防呆層改動前，必須跑回測驗證效果，不可憑直覺調整。
