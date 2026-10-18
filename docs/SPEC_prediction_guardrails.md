# ML 預測解釋 — 財務常識防呆規則(規則 1-14)

2026-10-18 自 `AGENTS.md` 逐字搬移。修改 `app.py` 的 `prediction_explain`、`ml/predict.py` 推薦分類、
`ml/thresholds.py` 防呆常數、組合防呆或任何過濾器之前必讀;各規則由 `tests/test_recommendation_rules.py` 鎖住,
改閾值前必須跑回測或固定快照 A/B(見 `docs/SPEC_data_integrity_and_change_control.md`)。

## ML 預測解釋 — 財務常識防呆規則

修改 `app.py` 的 `prediction_explain` 或相關解釋邏輯時，**必須遵守三層架構**：

### 第一層：模型計算層
- SHAP / pred_contrib 照常算，不動數值

### 第二層：財務常識防呆層（Domain Logic Guardrail）

**核心原則：事實的多空分類必須基於財務常識，不可被 SHAP 方向翻轉。**

強制規則：

1. **本業虧損原則上是利空，但要辨識轉機修復**
   - `operating_margin < 0` → 預設分類為 negative
   - 若同時滿足明確修復訊號（如 `eps_qoq > 0`、`revenue_yoy_latest > 15%`、`margin_trend > 0.03` 中至少 2 項），且 `operating_margin > -8%`
     → 應表述為「本業仍虧損，但處於修復期」，保留 negative 同時可補充 turnaround positive
   - 不可把單純虧損包裝為「低基期反轉空間」

2. **估值虛胖辨識**
   - `PE > 50` 且 `operating_margin < 0` → EPS 分母趨近零導致，分類為 negative
   - `PE > 50` 且 `EPS TTM < 1` 且本業虧損 → 同上，不可說「反映高成長預期」
   - 高 PE 只有在營收/營業利益同步高成長時才可能是真正的成長溢價

2b. **PE 虛胖豁免條款**（僅限 `operating_margin > 0` 的股票）
   - 當 `PE > 50` 且 `0 < EPS TTM < 1` 但本業有賺錢時，依序檢查：
     - 豁免 1：`revenue_yoy_3m_avg > 30%` → 營收動能豁免，標記轉機訊號
     - 豁免 2：`gross_margin > 20%` 且 `operating_margin > 0` → 本業獲利結構豁免
     - 豁免 3：`PB < 1.5` → 資產保護傘豁免，下檔有撐
     - 無豁免：維持原始「估值虛胖」警告
   - **本業虧損（operating_margin < 0）永遠不可豁免**

3. **低基期/業外收入過濾**
   - `EPS YoY > 500%` → 低基期效應，分類為 negative（非實質成長）
   - `operating_margin < 0` 且 `EPS YoY > 0` → 預設視為獲利來源可疑（業外收入），強制從利多移到利空
   - 但若 turnaround 訊號成立，應改寫為「虧損收斂 / 轉機修復」，不可直接當作實質獲利成長
   - eps_momentum 在本業虧損下不可直接稱為「成長動能加速」；turnaround 成立時可稱為「修復動能加速」

4. **利潤率下滑永遠是利空**
   - `margin_trend < -0.03` → 分類為 negative
   - 不可包裝為「跌勢接近尾聲」

5. **缺失資料不是投資理由**
   - NaN 特徵一律不顯示，不可用「模型根據歷史缺失模式判斷」當理由

6. **技術指標例外**
   - 技術面指標（RSI/MACD/KD 等）方向不固定，可由 SHAP 決定
   - 籌碼面買賣超有固有方向（買超=positive, 賣超=negative）

7. **流動性不足永遠是風險（硬擋）**
   - 近日成交量 < 500 張（500,000 股）→ 推薦**強制降級**為「觀望」
   - 流動性不足 = 執行風險，買賣價差大、不易出場

8. **異常暴跌不是買進理由（硬擋）**
   - 20 日跌幅 > 40% → 推薦**強制降級**為「觀望」
   - 模型看到的「超跌」可能只是反映暴跌事件，不代表反轉

9. **極端預測值代表模型失準（硬擋）**
    - `|pred_return_20d| > 30%` → 推薦**強制降級**為「觀望」
    - 20 天漲跌超過 30% 已極罕見，說明模型被異常資料誤導

9b. **ATR 報酬天花板（硬擋）**
    - 預測報酬不得超過 `ATR% × 10`（最低 10%）
    - 物理限制：股票的短期報酬受波動能力約束

10. **極端乖離率是回歸均值風險（分層實作；推薦層為升級封鎖非降級，2026-08-29 校正）**
   - 推薦層 / `prediction_explain`：`price_vs_ma20 > +30%` → 加「⚠️短線偏熱」風險標籤並**封鎖升級「強力買進」**（`ml/predict.py` Step 4 的 `price_ok`）；**不會**把既有「建議買進」降級為觀望。`< -30%` 超跌側僅加「⚠️乖離過大(超跌)」標籤（常數：`ml/thresholds.py::RECOMMENDATION_OVERHEAT_THRESHOLD`）。若要升級為 Step-5 強制降級硬擋，屬 production 變更，需 fixed-snapshot A/B + PM 核可後才可落地。
   - Champion unified signals / paper book production gate：`price_vs_ma60 > +40%` → `OVERHEAT_RISK`，並搭配 `beta_60 > 1.80` → `HIGH_BETA_RISK`（實作：`scripts/build_unified_signals.py::_apply_overheat_risk_gate`）
   - 注意：週報歸因、gate funnel、Champion 帳本中的 `OVERHEAT_RISK` 指的是 unified production gate 的 `price_vs_ma60 > +40%`，不是推薦層的 `price_vs_ma20 > 30%`
   - 回測 / audit 註記：任何 overheat 閾值調整前，必須先確認調的是哪一層，並跑 fixed-snapshot A/B 或對應回測，不可只依文件敘述調 production

11. **本業虧損（預設硬擋，轉機股例外）**
   - `operating_margin < 0` 且 turnaround 不成立 → 推薦**強制降級**為「觀望」
   - `operating_margin < 0` 但 turnaround 成立 → 不可列為「強力買進」，但可保留「建議買進」並加註修復型風險標籤
   - 回測驗證：對一般虧損股維持硬擋仍有效，轉機股則需交由修復訊號額外辨識

12. **產業集中度上限（組合防呆）**
   - 單一產業佔 Top N 不得超過 20%（2026-04 實戰驗證後從 30% 收緊）
   - 超過上限時，從該產業排名最低的開始替換為次佳不同產業標的
   - 回測驗證：MDD 改善 0.5%、Calmar +0.421，月均報酬僅少 0.04%
   - 實戰教訓：30% 上限時電子類佔 70% 持倉，空頭集體崩潰

14. **波段強制停損 -10%（硬擋）**
   - 持倉收盤報酬跌破 -10% → 強制平倉（status = stopped_out）
   - 不再等待 20 天到期，提前止血
   - 實戰教訓：270 檔持倉中 94 檔跌超 -10%，最深 -35%，無停損機制導致虧損失控

13. **籌碼頂部背離（兩級制，2026-08-29 校正）**
   - `chip_diverge_bear >= 1.0`（WARN）→ 加「⚠️籌碼頂部背離」風險標籤，不降級
   - `chip_diverge_bear >= 2.0`（BLOCK）→ 推薦**強制降級**為「觀望（籌碼頂部背離）」並封鎖強力買進（常數：`ml/thresholds.py::CHIP_DIVERGE_WARN_THRESHOLD / CHIP_DIVERGE_BLOCK_THRESHOLD`）
   - 股價漲 + 法人倒貨 + 散戶融資追買 = 主力出貨格局，追高風險極大
   - 底部籌碼背離（`chip_diverge_bull >= 1.0`）為純提示（💡），不降級

### 第三層：衝突報告層

- 當某面向的 SHAP group_dir > 0（模型看多）但個別事實 negative > positive 時 → 標註「⚠️ 訊號矛盾」
- `domain_warnings` 欄位彙整所有被防呆層攔截的矛盾
- **絕對不可以「先射箭再畫靶」**：不可以為了讓文字與模型結論一致，而把利空事實包裝成利多
