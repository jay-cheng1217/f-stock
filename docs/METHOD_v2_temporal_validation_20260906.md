# Method lock：V2 訓練驗證邊界修復（2026-09-06）

## 授權與範圍

使用者授權修復既有缺陷並回測驗證。這張票只修復未來訓練程式的評估方法與測試；
本次不執行 `train_v2()`，不產生／替換模型，不改 Champion pin、selection、registry。
現有模型績效數字仍是舊方法，不能因程式修好而重新聲稱為無洩漏 OOS。

## 現行方法

`scripts/train_v2.py` 每月 test 同時作 LightGBM `valid_sets` 的 early stopping 依據，
再用同一 test 報 OOS；這會用到 test 標籤選擇迭代次數。月底截面按每檔最後一日選取，
停牌／缺資料股票可與其他日期混合。最終模型固定 500 rounds，與折內選定輪數不一致。

## 候選方法（在實作前鎖定）

唯一核心變因是時間切分與輪數選擇的資訊邊界，保持 target、特徵、LightGBM 超參數、
訓練回看 24 個月與每月 test 不变。

1. 使用資料集原始交易日期（過濾無標籤列之前），避免標籤缺失壓縮交易日曆。
2. test 首交易日前保留 20 個交易日的 label purge。其前 60 個交易日作 validation。
3. validation 首交易日前再保留 20 個交易日 purge；其前資料才是 train。
   `train最後訊號日 + 20 < validation首日`，`validation最後訊號日 + 20 < test首日`。
   同時用過濾前每檔第 20 個後續觀測日檢查 label end；稀疏／停牌序列不得跨越下一分區。
   上游已刪去的日期可能使此 end 偏晚，會保守多排除樣本，不放行跨界標籤。
4. early stopping 僅接 train 與 validation。test 僅用於預測／評估，不輸入 callback。
5. 月底截面使用該 fold 共同的最後有效交易日，沒有該日資料的股票不以更早日期代替。
6. 最終訓練輪數由各有效 fold 的 validation-selected best_iteration 中位數決定；
   不以 test IC／報酬挑模型或輪數。

## 事前通過條件

- 含缺席個股及稀疏標籤的人工日期 fixture 通過兩個嚴格 horizon 邊界不變量。
- 假 LightGBM adapter 捕捉 Dataset／train callback：validation 標籤與 test 標籤完全分離；
  改變 test 標籤不影響訓練輸入、早停輪數或最終選定輪數。
- 月底 cohort 日期唯一；最終輪數只依 validation 輪數。
- 既有執行緒設定測試通過。不呼叫真實訓練，也不新增 protected artifact。

這些驗證證明程式的資訊隔離，不證明新模型有投資超額報酬。真正績效需另行授權重訓並
使用修復後的完整 walk-forward 報告。LightGBM 的 early stopping 本來就以 validation
metric 決定最佳輪數，因此 test 不能充當 validation：
https://lightgbm.readthedocs.io/en/stable/pythonapi/lightgbm.early_stopping.html

## 實作與 fixture 結果

- `_prepare_temporal_frame` 保存標籤過濾前日曆並建立逐股保守 label end；重複 ticker/date 或
  無效日期直接拒絕。`_temporal_fold` 產生三分區與稽核用邊界日期。
- `_fit_validated_model` 的介面完全不接收 test；共用 `_fit_fold` 僅把 test 特徵交給 predict。
  月度／Top30 報告新增 validation 日期、樣本數與選定輪數，model meta 新增 protocol/version
  與 final-round-selection 來源。上述欄位只會在未來獲授權訓練時產生。
- `python -B -X utf8 -m pytest tests/test_train_v2_temporal_validation.py tests/test_lgbm_thread_config.py -q`
  ：10 passed。
- 人工完整日曆兩分區相隔 21 個索引位置（中間正好 20 個 purge 日），兩側 label end
  都嚴格早於下個分區。每三日一筆的稀疏股票也不能跨界。
- 假 LightGBM callback 捕捉到的 train 標籤全為 1、validation 全為 2；test 標籤
  從 +9999 改成 -12345 後，訓練輸入、callback、37 輪選擇與預測結果完全相同。
- 月底 fixture 的停牌／缺席股票不以更早日期混入；final rounds 的中位數選擇不接收
  IC、報酬或任何 test 指標。

本次未執行 `train_v2()`，未呼叫真實 LightGBM 訓練，未寫任何模型或 protected artifact。
