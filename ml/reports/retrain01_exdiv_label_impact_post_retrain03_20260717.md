# RETRAIN-01 除權息標籤影響量化

## Verdict

**PASS** — 事前預測差距門檻為 0.5x~2.0x；超出即停止重訓並回 PM。

## Scope

- 日期：2020-01-01 ~ 2026-07-17
- 股票：1,893
- 20D 有效樣本：1,477,036
- 官方基準：TWSE MFI94U 發行量加權股價報酬指數
- 公司行動：`before_price/after_price` 比例因子

## Impact

| 指標 | 實測 | 2026-07-15 事前預測 | 比率 |
|---|---:|---:|---:|
| 20D 視窗含公司行動樣本 | 7.72% (113,967) | 9.51% | 0.81x |
| 受影響 20D 平均位移 | +3.66pp | +3.37pp | 1.09x |
| 受影響 20D 中位位移 | +3.34pp | - | - |
| 位移絕對值 >3pp | 56.85% | 45.2% | - |
| 位移絕對值 >5pp | 28.99% | 22.3% | - |
| V1 任意分類翻轉 | 1.930% | - | - |
| V1 UP↔DOWN 直接翻轉 | 0.155% | - | - |

## Invariants

- 無個股公司行動的絕對 20D 標籤最大差：`0.000e+00`（門檻 `<1e-9`）。
- V1 excess 採雙邊含息後，即使個股無事件，也可能因官方報酬指數與價格指數不同而改變；
  這是 PM 指定的 benchmark basis 修正，不應誤列為零事件迴歸。
- `trade_return_20d` 視窗為 `(t+1, t+20]`；close-based 視窗為 `(t, t+N]`。

## Reproduction

```powershell
python scripts/update_taiex_total_return_index.py --backfill
python scripts/audit_exdiv_training_labels.py --start 2020-01-01 --end 2026-07-17
```
