# dataA / Champion：修正交易時點的研究比較

資料截至 2026-09-04，來源帳本與模型原檔未修改。

- 訊號後一日開盤進、訊號後第20日收盤出；公司行動用既有 label factor；毛報酬與淨報酬分列。
- 淨報酬假設一般股票賣出稅0.3%、每邊手續費0.1425%、每邊10bps滑價；另輸出0/25bps情境。
- 僅比較同訊號日雙邊各30檔皆已成熟的完整籃子，缺值不得只刪輸家。
- 這是分數Top30比較，未複製主lane型態、盤中守穩、實際部位、Champion組合出場，不能當作帳戶績效。
- 舊帳本的生成日期未完整保留來源證據，歷史混日風險仍在；新分數供應已改逐股日期契約。
- 重疊訊號日彼此相關；非重疊 cohort 太少時無法主張模型優劣有統計把握，不自動升級。

```json
{
  "as_of": "2026-09-04",
  "ledger_sha256": "bfe4f53aff7650df797453ea3d9185c1cd283945b59ba57327631f1465ce624b",
  "rows": 2460,
  "status_counts": {
    "mature": 1556,
    "immature": 878,
    "missing_signal_bar": 26
  },
  "paired_dates": 24,
  "decision": "keep_current_model_no_promotion",
  "paired_mean": {
    "shadow_gross_pct": 0.3849464224856758,
    "shadow_net_pct": -0.40116907684495184,
    "champion_gross_pct": 3.1546343098411014,
    "champion_net_pct": 2.34682935751824,
    "delta_pp": -2.7479984343631916
  },
  "nonoverlap_dates": 2,
  "nonoverlap_mean_delta_pp": 2.8581755281780836
}
```
