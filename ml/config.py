"""ML 預測模型設定檔"""
import os


DEFAULT_LGBM_NUM_THREADS = 8


def coerce_lgbm_num_threads(value, default: int = DEFAULT_LGBM_NUM_THREADS) -> int:
    """Return a safe positive LightGBM CPU thread cap."""
    try:
        threads = int(value)
    except (TypeError, ValueError):
        return default
    return threads if threads > 0 else default


def get_lgbm_num_threads(default: int = DEFAULT_LGBM_NUM_THREADS) -> int:
    """Resolve the LightGBM CPU thread cap from environment variables."""
    for env_name in ("STOCK_LGBM_THREADS", "LGBM_NUM_THREADS"):
        raw = os.environ.get(env_name, "").strip()
        if raw:
            return coerce_lgbm_num_threads(raw, default=default)
    return default


LGBM_NUM_THREADS = get_lgbm_num_threads()

# === 路徑 ===
# 優先吃 STOCK_BASE_DIR env var；否則由本檔位置往上推一層（ml/ 的父）。
BASE_DIR = os.environ.get("STOCK_BASE_DIR") or os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))
)
DAILY_K_DIR = os.path.join(BASE_DIR, "日K資料")
REVENUE_DIR = os.path.join(BASE_DIR, "月營收")
FINANCIAL_DIR = os.path.join(BASE_DIR, "季報財務")
INDEX_DIR = os.path.join(BASE_DIR, "大盤指數")
VALUATION_DIR = os.path.join(BASE_DIR, "估值資料")
MODEL_DIR = os.path.join(BASE_DIR, "ml", "models")
REPORT_DIR = os.path.join(BASE_DIR, "ml", "reports")

# === 預測目標 (超額報酬 = 個股報酬 - 大盤報酬) ===
FORWARD_DAYS = 5            # 預測 N 日後報酬
UP_THRESHOLD = 0.015        # 超額報酬 > +1.5% 為 UP
DOWN_THRESHOLD = -0.015     # 超額報酬 < -1.5% 為 DOWN
TARGET_CLASSES = {0: "DOWN", 1: "FLAT", 2: "UP"}

# === 過濾條件 ===
MIN_AVG_VOLUME = 250_000    # 最低日均量 (股)
MIN_PRICE = 10.0            # 最低股價
MIN_HISTORY_DAYS = 120      # 最少需要的歷史交易日

# === 訓練設定 ===
WALK_FORWARD_TRAIN_MONTHS = 36   # 訓練窗口 (月)
WALK_FORWARD_VAL_MONTHS = 3     # 驗證窗口 (月)
WALK_FORWARD_TEST_MONTHS = 1    # 測試窗口 (月)
RETRAIN_EVERY_MONTHS = 1        # 每 N 月重新訓練

# === LightGBM 預設參數 ===
LGBM_PARAMS = {
    "objective": "multiclass",
    "num_class": 3,
    "metric": "multi_logloss",
    "boosting_type": "gbdt",
    "device": "gpu",
    "num_leaves": 63,
    "learning_rate": 0.05,
    "feature_fraction": 0.7,  # V2.3: 從 0.8 收緊，強制模型探索更多特徵組合
    "bagging_fraction": 0.8,
    "bagging_freq": 5,
    "verbose": -1,
    "n_jobs": LGBM_NUM_THREADS,
    "seed": 42,
}
LGBM_NUM_ROUNDS = 1000
LGBM_EARLY_STOPPING = 50

# === 特徵窗口 ===
ROLLING_WINDOWS = [5, 10, 20]

# === 市場體制特徵防過擬合 ===
REGIME_FEATURE_FRACTION_CAP = 0.15
REGIME_FEATURE_COLS = [
    "taiex_20d_return_pct",
    "taiex_vs_ma60_pct",
    "market_breadth_20d_pct",
]

# === AUDIT-002 / REQ-010 two-stage ranking defaults ===
# Stage 1 remains the V2 regression model. Stage 2 is an optional LambdaRank
# reranker that is only active when a trained stage-2 model is explicitly
# enabled by the caller.
TWO_STAGE_CANDIDATE_SIZE = 75
TWO_STAGE_LABEL_BINS = 5
TWO_STAGE_TOP_N = 30
