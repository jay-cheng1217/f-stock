"""推薦/防呆層共用閾值 — 單一來源 (single source of truth).

這個模組只放數值常數、不做 import，因此 app.py 跟 ml/predict.py 都能在
module level 引用而不會付任何啟動成本。
"""
from __future__ import annotations

# === V1 機率邊際 (up_prob - down_prob) ===
# 實證 (2026-04-11 真實帳本回測)：
#   prob_edge ∈ (-0.05, 0]：勝率 51%、停損率 21%（可接受，列為「軟可買」）
#   prob_edge <= -0.05      ：勝率 23%、停損率 46%（災區，禁止）
SOFT_BUY_PROB_EDGE_MIN = -0.05  # 雙軌制軟通過門檻
BUY_PROB_EDGE_MIN = 0.0
STRONG_BUY_PROB_EDGE_MIN = 0.05

# === V2 迴歸預測報酬 ===
STRONG_BUY_PRED_RET_MIN = 0.03  # 強力買進需 V2 明顯看多
V2_OVERRIDE_PRED_RET_MIN = 0.08  # V2 強到可以覆蓋 V1 不同意

# === 強力賣出條件 ===
SELL_STRONG_PROB_EDGE_MAX = -0.05
SELL_STRONG_PRED_RET_MAX = -0.05

# === 雙軌否決條件 ===
DUAL_TRACK_VETO_PROB_EDGE_MAX = -0.10
DUAL_TRACK_VETO_DOWN_PROB_MIN = 0.55

# === 推薦層偏熱/超跌 (CLAUDE.md 規則 #10 的 recommendation/explain path) ===
# price_vs_ma20 > +0.30 → 加「短線偏熱」風險標籤並封鎖升級「強力買進」
# （ml/predict.py Step 4 price_ok）；不降級既有「建議買進」。< -0.30 超跌側
# 僅加標籤。（2026-08-29 校正：舊註解稱「強制降級觀望」強於實作；若要升級為
# Step-5 硬擋屬 production 變更，需 fixed-snapshot A/B + PM 核可。）
# 此值 ml/predict.py 與 app.py（prediction_explain 風險標籤/警示）必須一致；
# 歷史上 app.py 曾為 0.18 與 predict.py drift，造成「explain 顯示
# should-be-downgrade 但 recommendation 仍是強力買進」的對外矛盾，已修正。
#
# 注意：這不是 Champion unified/paper-book 的 production OVERHEAT_RISK gate。
# unified signals 的真實 production gate 在 scripts/build_unified_signals.py：
# price_vs_ma60 > OVERHEAT_DEFAULT_THRESHOLD → OVERHEAT_RISK，
# beta_60 > 1.80 → HIGH_BETA_RISK。
# 兩條路徑語意不同，audit / 回測 / 文件更新時不可混用。
RECOMMENDATION_OVERHEAT_THRESHOLD = 0.30
OVERHEAT_DEFAULT_THRESHOLD = 0.40

# === Champion entry technical quality ===
# Production unified gate: block weak 20D entries that are already more than
# 3% below MA5 at signal time. This is intentionally an entry-quality hard gate,
# separate from MA5_BREAK exit logic.
ENTRY_MA5_MIN_PCT = -0.03

# === 法人賣壓占量 ===
STRONG_BUY_MAX_INST_SELL_PCT = 20.0  # 強力買進過濾門檻
TOP30_EXCLUDE_INST_SELL_PCT = 30.0   # Top30 排除門檻

# === 處置股仍可放行的最低成交量 ===
DISPOSITION_OVERRIDE_MIN_VOLUME = 1_000_000

# === 籌碼背離（CLAUDE.md 規則 #13，兩級制）===
# >= WARN(1.0)：只加「⚠️籌碼頂部背離」標籤；>= BLOCK(2.0)：強制降級觀望
# 並封鎖強力買進。文件若稱「1.0 即降級」為舊敘述，以此處與 ml/predict.py 為準。
CHIP_DIVERGE_WARN_THRESHOLD = 1.0
CHIP_DIVERGE_BLOCK_THRESHOLD = 2.0

# === Guardrail 熔斷 ===
GUARDRAIL_MONITOR_TOP_N = 50
GUARDRAIL_INTERCEPT_RATE_LIMIT = 0.60
GUARDRAIL_DUAL_TRACK_RATE_LIMIT = 0.40

# === 排序加權 ===
LEADERBOARD_SOFT_PROB_WEIGHT = 0.06
LEADERBOARD_TURNAROUND_WEIGHT = 0.12
LEADERBOARD_TURNAROUND_CAP_RATIO = 0.04

# === 風險調整係數 ===
PROB_EDGE_CLIP_LOW = -0.15
PROB_EDGE_CLIP_HIGH = 0.30
RISK_ADJUST_BASE = 0.35
RISK_ADJUST_MIN_SCALE = 0.15
RISK_ADJUST_MAX_SCALE = 0.65

# === 其他 ===
VOLUME_SHARES_PER_LOT = 1000.0
BETA_LOOKBACK_DAYS = 60
BETA_MIN_OBSERVATIONS = 30

# === 硬擋閾值（CLAUDE.md 規則 #8、#9）===
# 20D 跌幅超過 -40% → 視為異常暴跌，禁止買進
CRASH_RETURN_20D_MIN = -0.40
# |預測報酬| > 30% → 視為模型失準，強制觀望
EXTREME_PRED_RETURN_ABS = 0.30
# 流動性下限：500 張 (= 500,000 股)
MIN_LIQUIDITY_SHARES = 500_000

# === 組合防呆 ===
# CLAUDE.md 規則 #12：單一產業佔 Top N 不得超過 20%
SECTOR_CAP_RATIO = 0.20

# Supply-chain/theme group concentration cap. OTHER/unknown groups are uncapped
# because the mapping is intentionally a living file and should not penalize
# unmapped tickers.
GROUP_CAP_RATIO = 0.15

# === Turnaround margin-recovery 訊號（CLAUDE.md 規則 #1/#3 修復辨識）===
# 本業仍小虧但毛利結構健康 → 視為「修復期」而非單純虧損。
# 這兩個字面量過去硬編碼於 app.py 與 ml/predict.py 各一份，抽出集中以避免 drift。
TURNAROUND_MARGIN_FLOOR = -0.08      # operating_margin 修復下限
TURNAROUND_GROSS_MARGIN_MIN = 0.20   # gross_margin 健康結構下限

# === 高估值動能股風險（REQ-002 / RD-2026-0506-001）===
# Champion unified production gate：PE > 50 且 price_vs_ma60 > 25% 視為
# 「高估值動能股」，敘事退潮容易出現 false positive。觸發後限制 target
# weight 上限為 3%，並由 prediction_explain 加標籤「高估值動能股風險」。
#
# 注意：本規則與 OVERHEAT_RISK (price_vs_ma60 > 40%) 為兩個獨立 gate；
# OVERHEAT_RISK 為硬擋，EXPENSIVE_MOMENTUM_RISK 為 weight cap，不互相
# 取代。實作見 scripts/build_unified_signals.py::_apply_expensive_momentum_gate
EXPENSIVE_MOMENTUM_PE_THRESHOLD = 50.0
EXPENSIVE_MOMENTUM_MA60_THRESHOLD = 0.25
EXPENSIVE_MOMENTUM_WEIGHT_CAP = 0.03
