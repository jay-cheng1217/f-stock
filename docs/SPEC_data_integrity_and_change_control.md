# 規格書：資料完整性與變更管制（Claude / Codex 共用單一真相）

**地位**：本文件是 2026-07-15~17 全系統審查戰役的成果沉澱，為**常設規格**（非交接紀錄）。
任何 agent 在動資料層、特徵層、production 規則前必須遵守。戰役當下的 backlog 狀態見
`docs/HANDOVER_20260716_system_audit.md`；本文件只規範**怎麼做才算對**。

---

## 第一部：資料完整性教條（Data Integrity Doctrine）

本專案六年來的重大事故全部屬於同一族：**pipeline 綠燈、資料錯誤**。以下四條由實際事故推導，
違反其一即視為代碼缺陷，不需再辯論。

### 教條 1：檔案存在 ≠ 資料完整

任何「檔案存在就跳過」的完成判斷都是缺陷。抓取器的完成判斷**至少**要驗：

| 驗證面 | 具體要求 |
|---|---|
| 列數 | 對比同族前期（前一季/前一日/前一週），低於門檻視為殘檔並重抓 |
| 覆蓋 | 雙市場來源（TWSE+TPEx）必須都到齊，單邊成功不得寫檔 |
| 期別 | 檔案自帶的資料日期/期別必須等於預期值，不可信任 API 給什麼就存什麼 |
| 內容 | 必要欄位存在、非全 NULL、型別正確 |

**事故實證**：2026Q1 季報（申報截止前抓到 104 家即凍結兩個月，94% 個股 EPS/PE 失真）、
上櫃融資凍結 46 日（TPEx 改版後回空殼 JSON，長度檢查看不出來）、
外資持股凍結 2 個月（排程提早於官方發布，「檔案 >1000 bytes」誤判完成）。

**門檻設定原則**：殘檔門檻是**下限而非目標**。80% 門檻代表「低於 80% 必定是殘檔」，
不代表「高於 80% 必定完整」——申報期內 80~99% 覆蓋率仍可能凍結數百檔（已知殘留破口，
見 HANDOVER §4 P0-C）。凡是「首次跨過門檻即永久凍結」的邏輯都必須改為時間窗內持續重驗。

### 教條 2：空資料 ≠ 合法的「沒有結果」

靜默失敗是本專案最毒的缺陷類型。以下寫法一律禁止：

```python
except Exception:
    continue            # ❌ 整季資料靜默蒸發
    return None         # ❌ 呼叫端無法區分「無資料」與「壞掉」
    return pd.DataFrame()  # ❌ 空表被當成「無持倉/無訊號」寫進報告
```

正確做法：**失敗要大聲**（拋專用例外或明確錯誤碼），呼叫端才能重試或阻擋。
`raise_on_fail=False` 只能用在**真正可選**的步驟，且該步驟失敗必須反映在 step ledger 與告警，
不得記為 DONE。

**事故實證**：`ml/features/balance_sheet.py` 的 `except: continue` 讓一列壞列蒸發整季資產負債表，
且因 `merge_asof(direction='backward')` 靜默沿用上一季（比 NaN 更毒，NaN 至少監控得到）；
`engine.query_df` 遇 DB 鎖回空 DataFrame，56 個呼叫點無法區分「無資料」與「被鎖」。

### 教條 3：語意正確 ≠ 數值看起來正常

數字在合理範圍內、有漲有跌、不噴 NaN——這些都**不代表**欄位意義正確。

**事故實證**：TDCC 持股分級被字串排序（1,10,11,…,2,3…），「千張大戶%」實際加總的是
30-100 張中實戶。台積電顯示 2.63%（真值 85.01%），錯了六年、比 git 歷史還老，
每代模型都吃過，期間還有人 commit 過「fix TDCC whale percentage calculation」卻沒發現。

**強制對策——語意不變量檢查**（已實作於 `scripts/fundamentals_completeness_audit.py`）：
每個關鍵衍生欄位都要有至少一條「外部常識鐵律」，錯置時必然爆炸性違反。
範例：台積電千張大戶必須 >50%；散戶%+大戶% ≤ 100%。
**新增任何彙整/衍生欄位時，必須同時新增其語意不變量**，否則不得上線。

**強制對策——原始檔對帳**：新增或修改彙整邏輯時，必須手工抽樣一檔股票、從原始檔逐級加總、
與輸出比對。這個 5 分鐘的動作，是六年來沒人做過、也是唯一能抓到 TDCC 事故的方法。

### 教條 4：跨層日期語意必須一致

除權息、快照日期、point-in-time 在每一層都必須是同一個意思。

- **日K 為未還原價**：除息日的跳空是機械性價格調整，**不是跌幅**。凡是持有期間報酬、
  停損判定、報酬標籤，都必須用還原價（`scripts/exdiv_utils.py`）。
- **point-in-time**：任何「用最新值決定歷史樣本」的邏輯都是 look-ahead（如用最新均量決定
  該股整段歷史是否入 universe＝存活偏誤）。篩選必須逐日 as-of。
- **公布日 vs 期間日**：財報用**公布日**對齊日K，不可用財報期間日（會 look-ahead）。

**事故實證**：原相 7/13 除息 10.006 元被當 -4.7% 跌幅，持股健檢誤報 -16.1%（真值 -12.4%）。

---

## 第二部：變更管制（Change Control）

### 2.1 分級：什麼改動要走什麼流程

| 級別 | 範圍 | 要求 |
|---|---|---|
| **L0 自由** | 純新增診斷腳本、log、文件、測試 | 直接做 + commit |
| **L1 修復** | 資料抓取/匯入層缺陷修復（不改資料語意） | 修復 + **partial-content fixture 測試** + commit |
| **L2 語意** | 改變資料語意或衍生欄位定義 | 上述 + **原始檔對帳** + **語意不變量** + 影響量化（下游翻轉率）|
| **L3 production** | 模型、feature processor、guardrail、filter、threshold、exit policy | 上述 + **method lock** + **fixed-snapshot A/B** + **PM 核可** |
| **L4 禁區** | `ml/models/lgbm*`、`model_selection.json`、registry、pin manifest、`archive/model_pins` | 先讀 model-artifact-protection skill；PM/SA 明確核可才可動 |

**判定原則**：不確定是 L2 還是 L3 時，一律當 L3。

### 2.2 Method Lock（L3 前置，不可略）

改 production 規則前，先寫下**不可事後修改**的方法定義：

```markdown
## Method Lock: <ticket>
- 現行算法：<精確描述 + 代碼位置 + 行號>
- 候選算法：<精確描述>
- 唯一變因：<必須只有一個>
- 資料快照：<同一 DuckDB as-of frame,兩分支共用>
- 評估指標與判準：<事前寫死,如 status flip 數、報酬 delta、MDD、turnover>
- 通過條件：<事前寫死,例如「MDD 不惡化且 status flip 全部經濟上正確」>
```

**為什麼**：事後才決定看哪個指標＝先射箭再畫靶。本專案已有多次「四次 CL3 全失敗」的
昂貴教訓（MA5 gate、regime overlay、re-entry），method lock 是防止第五次的唯一機制。

### 2.3 Fixed-Snapshot A/B（L3 必要證據）

- 兩分支必須用**同一份** DuckDB as-of raw feature frame，唯一變因是被測改動
- 用環境變數切分支（如 `EXDIV_STOP_ADJUST=0/1`），不可用兩份代碼
- 結果按 `ticker` + `date` join，報告 `prob_edge` delta、status flip、報酬/MDD
- **零差異也是有效結論**（證明行為等價，可安全上線）——如除息停損 A/B：248 倉零 status 變動、
  1 筆經濟上正確的報酬修正
- 報告存 `ml/reports/`，作為**結案 artifact**，commit message 引用

### 2.4 基準（Baseline）管制

任何被當成及格線的數字必須：

1. **可重現**：產生它的腳本必須在 repo 裡，任何人任何時候可重跑得到同樣數字
2. **慣例一致**：基準的計算慣例必須與被比較的實績**完全相同**（停損、除息還原、進場定義）
3. **來源標註**：文件引用時必須註明腳本路徑與重定日期

**事故實證**：rere lane 的 +9.4%/50.6% 基準腳本從未入 repo、且是**無停損**算出來的，
卻拿來當**有停損**的前瞻帳本的及格線——等於用開書考分數當閉書考及格線。
重定後為 +4.58%/35.8%（`scripts/backtest_rere_lane_baseline.py`，停損+除息還原，與帳本同慣例）。

### 2.5 審查發現的處理協定

多代理審查產出的候選發現**不是 bug**，處理順序：

1. **對抗驗證**：先假設它是誤報，讀實際代碼推翻或確認，必須引具體行號
2. **可重現證據**：能寫出 fixture/測試重現才算確認
3. **分級**（見 2.1）後才動手
4. 誤報要記錄「為什麼是誤報」，避免下輪重複驗證

**實績**：本戰役 ~100 項候選中，對抗驗證後確認約 20 項、駁回 62 項——**驗證比發現更重要**。

---

## 第三部：各層契約（Layer Contracts）

### 3.1 抓取層（`twstock.py` steps、`scripts/backfill_*.py`）

- 寫檔前驗：列數 vs 前期、雙市場到齊、資料日期＝預期、必要欄非空
- 官方 API 回應「stat=OK 但 data 空」**不等於**休市——必須有獨立求證機制才可斷定
  （見 `_twse_confirms_no_trading`；且求證失敗一律維持阻擋，寧可誤擋不可誤放）
- 寫檔用 tmp + `os.replace`（原子），不可直接寫最終檔
- 排程時點必須晚於官方發布時間（法人 ~16:30、TWSE 收盤 ~18:00、融資 ~21:00、
  TDCC 週六、季報見申報行事曆）——排程改時間前必須查此表
- **來源格式漂移**(2026-07-24 事故):TAIFEX OpenAPI 的三大法人端點從回 JSON 改為回 **CSV**
  (HTTP 仍 200),json.loads 拋錯導致必要資料集連日失敗。解析層必須**雙格式容忍**(JSON→CSV fallback),
  仍無法解析才拋錯。教訓:HTTP 200 不代表格式沒變;連續失敗要先看**實際例外**再判斷,
  不可憑「連不上」直覺誤診(本次前一日即因此誤修為重試,重試 3 次全敗才發現真因)。
- **失敗必須留下可診斷證據**:manifest/報告寫入時要保留 `error` 與 `http_status`,
  否則失敗只看得到 status=failed 查不出原因(本次誤診主因)。
- TPEx 憑證缺 Subject Key Identifier,Python 3.14 requests 會拒絕 → 照專案慣例退回 curl fallback
  (見 `update_ex_dividend_calendar.py`、`fetch_public_market_context.py`)

### 3.2 匯入層（`backend/db/ingest.py`）

現行契約（`0dce60bc` 建立 staging；2026-08-18 改逐表交易，2026-09-06 補齊失敗回報）：

- **禁用 `ignore_errors=true`**：壞列必須讓 CREATE 失敗，不可靜默丟棄
- **staging → 驗證 → swap**：先建 temp stage 表 → 驗 schema/列數/期別 → 通過才
  `CREATE OR REPLACE` 正式表。**絕不可先覆蓋再檢查**
- `ingest_meta` 記錄每表列數、時間戳、`generation_id`——世代標記讓「混合世代」可稽核
- 每表 staging 驗證與 swap 在獨立交易內；失敗表回滾，成功表保留，避免一個外部指數來源失敗抹掉已驗證的台股資料。
- 任一必要表或 `stock_list` 失敗 → 整次 `ingest_all()` 拋錯，排程不可記成功或繼續發布依賴該次匯入的預測/報告。`ingest_meta.generation_id` 必須揭露部分落地造成的混合世代；修復重跑全部通過後才完成驗收。
- `stock_list` 必須一代號一列，財報名稱按最新年/季選一筆，沒有財報名称的新公司可用官方股票 metadata 補名；不得用歷年 distinct 名稱一對多展開。
- 回傳**真實列數**，不是檔案數

### 3.3 特徵層（`ml/dataset.py`、`ml/features/*`）

- 來源損毀必須 fail loud，不可 `except: continue`
- `merge_asof(direction='backward')` 會沿用舊期資料——用它時必須有 staleness 上限
- 快取（`dataset_cache_*.parquet`、模組級 `_*_CACHE`）的 invalidation 必須涵蓋**所有**來源
- universe 篩選逐日 as-of，不可用最新值套整段歷史

### 3.4 排程層（`scripts/smart_update_auto.py`）

- freshness gate 必須涵蓋所有**當日預測會用到**的來源（日K sentinel + TWII 不夠：
  法人/融資/國際指數過期照樣出預測——已知破口，見 HANDOVER §5）
- 每個 step 的成功判定必須反映真實結果；`raise_on_fail=False` 的步驟失敗必須記 FAIL + 告警
- 單一時點觸發（如「只在交易日週五抓 TDCC」）必須配 catch-up 機制，否則錯過即永久缺口

### 3.5 帳本層

- rere 正式帳本**只能**由 `scripts/rere_lane_tracker.py` 更新，不可手編
- 帳本慣例必須與其基準回測完全一致（見 2.4）
- 測試不得寫入 production ledger / SQLite / DuckDB / 前端 static artifact

---

## 第四部：工作節奏

1. **每完成一項單獨 commit**，格式 `type(scope): summary`；type ∈ feat/fix/chore/refactor/docs/test
2. **新知識同 commit 進共享檔**（AGENTS.md 或 docs/）——Codex 看不到 Claude memory，反之亦然
3. **排程異常先讀 log**（`logs/smart_update_auto_YYYYMMDD.log`），不可憑猜測改代碼
4. **UI 改動**必須貼固定 dashboard URL + 寄通知（見 dashboard-interface-update skill）
5. **結案報告**要含：commit hash、message、`git status --short --branch` 是否 clean
6. **不宣稱未驗證的事**：測試沒跑就說沒跑，指令不存在就說不存在

---

## 附錄：本戰役事故索引（供未來查證同族病）

| 事故 | 根因族 | 教條 | 修復 commit |
|---|---|---|---|
| 原相除息誤判 | 跨層日期語意 | 4 | `efa57c7d` `f8dc7791` |
| 2026Q1 季報凍結 | 檔案存在=完成 | 1 | `de0784ba` `e72e4025` `ca1f4721` |
| 上櫃融資凍結 46 日 | 空殼回應+長度檢查 | 1,2 | `4f8ab947` |
| 外資持股凍結 2 月 | 排程過早+大小判斷 | 1 | `4f8ab947` |
| TDCC 六年錯置 | 語意 vs 數值 | 3 | `db6f4d1e` `dced9641` |
| rere 基準不可重現 | 基準管制 | 2.4 | `9e860b34` |
| ingest 靜默丟列 | 空資料當合法 | 2 | `5071a8d5` `0dce60bc` |
| BS 整季蒸發 | except: continue | 2 | `080ecd50` |
| DB 鎖偽裝空表 | 空資料當合法 | 2 | `4565c339` |
| universe look-ahead | point-in-time | 4 | `097987ef` |

---

## 附錄 C：手動補跑的操作危害（2026-07-19 實例）

**教訓**：`python twstock.py`（完整跑）會用 **yfinance** 重抓日K並覆蓋既有值。
排程流程中這沒問題，因為 Phase-1 的下一步就是 `Verify daily bars vs official`
（`scripts/verify_otc_bars.py`）會用 TWSE/TPEx 官方值校正回來。

但**手動單獨補跑 twstock 會留下 yfinance 髒資料**。2026-07-19 實例：
補跑後 2330 的 7/17 成交量從官方 97,362,670 被覆蓋為 yfinance 的 95,088,728，
校驗器隨後修復 **1,855 個檔案**才復原。

**規則**：任何手動執行 `twstock.py`（完整或 step 1）後，**必須接著跑**：

```bash
python scripts/verify_otc_bars.py     # 官方校正,不可省
python -c "import sys; sys.path.insert(0,'.'); from scripts import smart_update; smart_update.exec_ingest()"
```

只跑單一非日K步驟（如 `--step 3` 融資、`--step 9` TDCC）不受此限。
