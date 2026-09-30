"""智慧更新腳本 — 根據目前日期時間判斷需要執行哪些更新

雙擊 smart_update.bat 即可執行。
自動檢查各資料來源的最新日期，決定要更新哪些項目。

進階用法：
    python scripts/smart_update.py --force              # 強制重跑所有任務
    python scripts/smart_update.py --force twstock predict  # 只強制跑指定任務
"""

import os
import sys
import glob
import time
import argparse
import subprocess
from datetime import datetime, date, timedelta

BASE_DIR = os.environ.get("STOCK_BASE_DIR") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from scripts.taiwan_trading_calendar import (
    is_taiwan_trading_day,
    previous_taiwan_trading_day,
)

# 資料目錄
DAILY_K_DIR = os.path.join(BASE_DIR, "日K資料")
VALUATION_DIR = os.path.join(BASE_DIR, "估值資料")
TDCC_DIR = os.path.join(BASE_DIR, "集保分散")
FINANCIAL_DIR = os.path.join(BASE_DIR, "季報財務")
NEWS_DIR = os.path.join(BASE_DIR, "新聞資料")
INDEX_DIR = os.path.join(BASE_DIR, "大盤指數")
MODEL_DIR = os.path.join(BASE_DIR, "ml", "models")
LOG_DIR = os.path.join(BASE_DIR, "logs")
os.makedirs(LOG_DIR, exist_ok=True)

TODAY = date.today()
NOW = datetime.now()


# ==============================================================================
# 資料新鮮度檢查
# ==============================================================================

def get_latest_kline_date():
    """檢查日K最新日期 (抽樣 2330.csv)"""
    sample = os.path.join(DAILY_K_DIR, "2330.csv")
    if not os.path.exists(sample):
        return None
    try:
        import pandas as pd
        df = pd.read_csv(sample, usecols=["Date"], dtype={"Date": str})
        return pd.to_datetime(df["Date"]).max().date()
    except Exception:
        return None


def get_latest_valuation_date():
    """檢查估值資料最新日期"""
    files = sorted(glob.glob(os.path.join(VALUATION_DIR, "valuation_*.csv")))
    if not files:
        return None
    try:
        fname = os.path.basename(files[-1]).replace("valuation_", "").replace(".csv", "")
        return datetime.strptime(fname, "%Y%m%d").date()
    except Exception:
        return None


def get_latest_tdcc_date():
    """檢查集保分散最新日期"""
    files = sorted([f for f in os.listdir(TDCC_DIR)
                     if f.startswith("tdcc_") and f != "tdcc_summary.csv" and f.endswith(".csv")])
    if not files:
        return None
    try:
        fname = files[-1].replace("tdcc_", "").replace(".csv", "")
        return datetime.strptime(fname, "%Y%m%d").date()
    except Exception:
        return None


def get_latest_financial_quarter():
    """檢查季報最新季度"""
    files = sorted(glob.glob(os.path.join(FINANCIAL_DIR, "financial_*Q*.csv")))
    if not files:
        return None, None
    try:
        fname = os.path.basename(files[-1]).replace("financial_", "").replace(".csv", "")
        year, q = fname.split("Q")
        return int(year), int(q)
    except Exception:
        return None, None


def get_latest_eps_quarter():
    """檢查 EPS 最新季度"""
    files = sorted(glob.glob(os.path.join(FINANCIAL_DIR, "eps_*Q*.csv")))
    if not files:
        return None, None
    try:
        fname = os.path.basename(files[-1]).replace("eps_", "").replace(".csv", "")
        year, q = fname.split("Q")
        return int(year), int(q)
    except Exception:
        return None, None


def get_latest_news_month():
    """檢查新聞公告最新月份"""
    files = sorted(glob.glob(os.path.join(NEWS_DIR, "announcements_*.csv")))
    if not files:
        return None
    try:
        fname = os.path.basename(files[-1]).replace("announcements_", "").replace(".csv", "")
        return fname  # YYYYMM
    except Exception:
        return None


def get_latest_model_date():
    """檢查模型訓練日期"""
    meta_files = sorted(glob.glob(os.path.join(MODEL_DIR, "*_meta.json")))
    if not meta_files:
        return None
    try:
        import json
        with open(meta_files[-1], "r", encoding="utf-8") as f:
            meta = json.load(f)
        return datetime.strptime(meta.get("trained_at", "")[:10], "%Y-%m-%d").date()
    except Exception:
        # 從檔名推斷
        fname = os.path.basename(meta_files[-1])
        try:
            ts = fname.split("_")[1]
            return datetime.strptime(ts, "%Y%m%d").date()
        except Exception:
            return None


def get_expected_quarter():
    """根據今日日期，回傳應已公布的最新季度"""
    y, m = TODAY.year, TODAY.month
    if m >= 11:
        return y, 3
    elif m >= 8:
        return y, 2
    elif m >= 5:
        return y, 1
    else:
        return y - 1, 3


def is_weekday():
    return is_taiwan_trading_day(TODAY)


def prev_trading_day():
    """回傳「此刻應已有資料」的最新交易日。
    - 平日 17:00 後 → 今天（收盤+法人+融資券資料皆已公布）
    - 平日 17:00 前 → 上一個交易日
    - 假日         → 上一個台股交易日
    """
    if is_weekday() and NOW.hour >= 17:
        return TODAY
    return previous_taiwan_trading_day(TODAY)


# ==============================================================================
# 判斷需要執行的任務
# ==============================================================================

def analyze_tasks():
    """分析目前資料狀態，回傳所有任務（永遠全列出，由用戶選擇）"""
    # 所有任務固定順序，全部列出
    tasks = list(TASK_MAP.keys())
    reasons = []

    # --- 狀態檢查（僅作為參考資訊顯示） ---

    # 1. 台股日K
    kline_date = get_latest_kline_date()
    if kline_date is None:
        reasons.append(("台股資料 (日K/法人/融資券/營收)", "無資料", "必要"))
    elif kline_date < prev_trading_day():
        reasons.append(("台股資料 (日K/法人/融資券/營收)",
                        f"最新: {kline_date}，落後 {(TODAY - kline_date).days} 天", "建議"))
    else:
        reasons.append(("台股資料 (日K/法人/融資券/營收)", f"最新: {kline_date}", "已最新"))

    # 2. 國際指數
    vix_path = os.path.join(INDEX_DIR, "index_VIX.csv")
    try:
        import pandas as _pd
        _vix = _pd.read_csv(vix_path, usecols=["Date"], dtype={"Date": str})
        vix_date = _pd.to_datetime(_vix["Date"]).max().date()
        reasons.append(("國際指數 (VIX/費半/S&P500/匯率)", f"最新: {vix_date}", "可更新"))
    except Exception:
        reasons.append(("國際指數 (VIX/費半/S&P500/匯率)", "無資料", "建議"))

    # 3. 估值
    val_date = get_latest_valuation_date()
    if val_date and val_date >= prev_trading_day():
        reasons.append(("估值資料 (PE/PB/殖利率)", f"最新: {val_date}", "已最新"))
    else:
        reasons.append(("估值資料 (PE/PB/殖利率)", f"最新: {val_date or '無'}", "建議"))

    # 4. EPS
    eps_y, eps_q = get_latest_eps_quarter()
    exp_y, exp_q = get_expected_quarter()
    if eps_y and (eps_y, eps_q) >= (exp_y, exp_q):
        reasons.append(("EPS 季報", f"最新: {eps_y}Q{eps_q}", "已最新"))
    else:
        reasons.append(("EPS 季報", f"最新: {eps_y}Q{eps_q}" if eps_y else "無", "建議"))

    # 5. 新聞
    news_ym = get_latest_news_month()
    reasons.append(("MOPS 重大訊息公告", f"最新: {news_ym or '無'}", "已最新" if news_ym == TODAY.strftime("%Y%m") else "建議"))

    # 6. 集保
    tdcc_date = get_latest_tdcc_date()
    reasons.append(("TDCC 集保股權分散", f"最新: {tdcc_date or '無'}", "已最新" if tdcc_date and (TODAY - tdcc_date).days < 7 else "建議"))

    # 7. 模型
    model_date = get_latest_model_date()
    if model_date:
        age = (TODAY - model_date).days
        reasons.append(("ML 模型", f"{model_date} ({age}天前)", "已最新" if age < 7 else "建議"))
    else:
        reasons.append(("ML 模型", "無模型", "必要"))

    # 8. 預測
    today_pred = os.path.join(MODEL_DIR, f"predictions_{TODAY.strftime('%Y-%m-%d')}.csv")
    reasons.append(("今日預測", "已完成" if os.path.exists(today_pred) else "尚未預測",
                     "已完成" if os.path.exists(today_pred) else "建議"))

    # 9. 驗證
    tracking_path = os.path.join(BASE_DIR, "ml", "reports", "prediction_tracking.json")
    if os.path.exists(tracking_path):
        try:
            import json
            with open(tracking_path, "r", encoding="utf-8") as f:
                tracking = json.load(f)
            last_updated = tracking.get("summary", {}).get("last_updated", "")
            today_str = TODAY.strftime("%Y-%m-%d")
            if last_updated.startswith(today_str):
                reasons.append(("預測驗證", "今日已驗證", "已完成"))
            else:
                reasons.append(("預測驗證", f"上次: {last_updated or '無'}", "建議"))
        except Exception:
            reasons.append(("預測驗證", "讀取失敗", "建議"))
    else:
        reasons.append(("預測驗證", "尚無紀錄", "建議"))

    return tasks, reasons


# ==============================================================================
# 執行任務
# ==============================================================================

def run_task(name, description, func):
    """執行單一任務，顯示耗時"""
    print(f"\n{'='*60}")
    print(f"  >> {description}")
    print(f"     開始時間: {datetime.now().strftime('%H:%M:%S')}")
    print(f"{'='*60}")
    start = time.time()
    try:
        func()
        elapsed = time.time() - start
        print(f"  [OK] 完成 ({elapsed/60:.1f} 分鐘)")
        return True
    except Exception as e:
        elapsed = time.time() - start
        print(f"  [FAIL] 失敗 ({elapsed/60:.1f} 分鐘): {e}")
        return False


from scripts.job_runner import run_job


def exec_twstock():
    run_job("twstock",
            [sys.executable, os.path.join(BASE_DIR, "twstock.py")],
            timeout="twstock_full", cwd=BASE_DIR, raise_on_fail=True)


def exec_twstock_daily():
    """Run the nightly daily-data refresh without the slow monthly revenue pass."""
    run_job("twstock_daily",
            [
                sys.executable,
                os.path.join(BASE_DIR, "twstock.py"),
                "--skip-revenue",
                "--skip-snapshot-cache",
            ],
            timeout="twstock_daily", cwd=BASE_DIR, raise_on_fail=True)


def exec_monthly_revenue():
    """月營收:每晚呼叫,但 twstock 內部只在每月 11-15 公布視窗實際抓取,其餘日子秒跳過。
    補上排程原本缺的自動月營收步驟(exec_twstock_daily 用 --skip-revenue,兩者互補)。"""
    run_job("monthly_revenue",
            [
                sys.executable,
                os.path.join(BASE_DIR, "twstock.py"),
                "--monthly-revenue",
            ],
            timeout="twstock_full", cwd=BASE_DIR, raise_on_fail=False)


def exec_strategy_scoreboard():
    """策略計分板:每月 1-3 號重測所有已驗證策略(全期 vs 近6月 edge 衰減),
    其餘日子秒跳過。原則:策略排名是時點快照,需定期重評,edge 衰減就降權。"""
    from datetime import date as _date
    if _date.today().day > 3:
        print("  非月度重評窗(每月1-3號),跳過 strategy scoreboard")
        return
    run_job("strategy_scoreboard",
            [sys.executable, os.path.join(BASE_DIR, "scripts", "strategy_scoreboard.py")],
            timeout="data_fetch", cwd=BASE_DIR, raise_on_fail=False)


def exec_valuation():
    # 找到最新估值日期之後的資料
    val_date = get_latest_valuation_date()
    start = val_date + timedelta(days=1) if val_date else TODAY - timedelta(days=7)
    run_job("valuation",
            [sys.executable,
             os.path.join(BASE_DIR, "scripts", "backfill_valuation.py"),
             "--start-date", start.strftime("%Y-%m-%d")],
            timeout="valuation", cwd=BASE_DIR, raise_on_fail=True)


def exec_eps():
    run_job("eps",
            [sys.executable, os.path.join(BASE_DIR, "scripts", "backfill_eps.py")],
            timeout="data_fetch", cwd=BASE_DIR, raise_on_fail=True)


def exec_news():
    # 每日抓取最新公告，追加到對應月份檔案
    run_job("news",
            [sys.executable, os.path.join(BASE_DIR, "scripts", "fetch_daily_news.py")],
            timeout="news", cwd=BASE_DIR, raise_on_fail=True)
    run_job("finnhub_news_sentiment",
            [sys.executable, os.path.join(BASE_DIR, "scripts", "fetch_finnhub_news_sentiment.py")],
            timeout="news", cwd=BASE_DIR, raise_on_fail=False)
    # 產業新聞日報(白名單制:官方+一線財經媒體;同學會/部落格=出貨文防線,一律排除)
    run_job("industry_news",
            [sys.executable, "-X", "utf8", os.path.join(BASE_DIR, "scripts", "fetch_industry_news.py")],
            timeout="news", cwd=BASE_DIR, raise_on_fail=False)
    # 敘事 shadow(RSS/cnyes → DuckDB news_articles);研究用,失敗不可拖垮生產管線
    # 預設抓 today-2~today 窄窗,不會踩到 cnyes 深度分頁 422 上限
    run_job("narrative_news_shadow",
            [sys.executable, "-X", "utf8", os.path.join(BASE_DIR, "scripts", "fetch_news_daily.py"), "--source", "all"],
            timeout="news", cwd=BASE_DIR, raise_on_fail=False)


def exec_tdcc():
    run_job("tdcc",
            [sys.executable, os.path.join(BASE_DIR, "scripts", "fetch_tdcc_weekly.py")],
            timeout="data_fetch", cwd=BASE_DIR, raise_on_fail=True)


def exec_retrain():
    # v1 分類模型
    run_job("retrain_v1",
            [sys.executable, "-m", "ml.train"],
            timeout="train", cwd=BASE_DIR, raise_on_fail=True)
    # v2 迴歸模型
    run_job("retrain_v2",
            [sys.executable, os.path.join(BASE_DIR, "scripts", "train_v2_backtest.py")],
            timeout="train", cwd=BASE_DIR, raise_on_fail=True)


def exec_retrain_t1():
    """Daily T+1 refresh for today's open-entry signal list."""
    device = os.environ.get("T1_RETRAIN_DEVICE", "gpu").strip().lower() or "gpu"
    if device not in {"gpu", "cpu"}:
        device = "gpu"

    args = [
        sys.executable,
        os.path.join(BASE_DIR, "scripts", "train_t1_backtest.py"),
        "--device",
        device,
        "--skip-reports",
    ]

    max_stocks = os.environ.get("T1_RETRAIN_MAX_STOCKS", "").strip()
    if max_stocks.isdigit() and int(max_stocks) > 0:
        args.extend(["--max-stocks", max_stocks])

    run_job("retrain_t1", args, timeout="train_t1", cwd=BASE_DIR, raise_on_fail=True)


def _api_post(path: str, timeout: int = 10) -> bool:
    """POST to local web server API. Returns True on success."""
    import urllib.request
    import json as _json
    try:
        url = f"http://127.0.0.1:8001{path}"
        req = urllib.request.Request(url, method="POST", data=b"",
                                     headers=_admin_api_headers())
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            result = _json.loads(resp.read())
            return bool(result.get("success"))
    except Exception:
        return False


def _load_admin_token() -> str:
    token = os.environ.get("ADMIN_TOKEN", "").strip()
    if token:
        return token

    env_path = os.path.join(BASE_DIR, ".env")
    if not os.path.exists(env_path):
        return ""

    try:
        with open(env_path, encoding="utf-8") as fh:
            for raw_line in fh:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                if key.strip() == "ADMIN_TOKEN":
                    return value.strip().strip("\"'")
    except OSError:
        return ""
    return ""


def _admin_api_headers() -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    token = _load_admin_token()
    if token:
        headers["X-Admin-Token"] = token
    return headers


def _api_post_json(path: str, timeout: int = 10) -> dict:
    """POST to local web server API and return the decoded JSON payload."""
    import json as _json
    import urllib.request

    url = f"http://127.0.0.1:8001{path}"
    req = urllib.request.Request(
        url,
        method="POST",
        data=b"",
        headers=_admin_api_headers(),
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return _json.loads(resp.read())


def _env_int(name: str, default: int, *, minimum: int = 0) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        print(f"[ingest] warning: invalid {name}={raw!r}; using {default}")
        return default
    return max(minimum, value)


def exec_ingest():
    """Refresh DuckDB in a dedicated subprocess after releasing the web connection."""
    released = _api_post("/api/db/release", timeout=30)
    if released:
        print("[ingest] released web server DuckDB connection")
    else:
        print("[ingest] web server release unavailable; trying standalone ingest")

    cmd = [
        sys.executable,
        "-c",
        "from backend.db.ingest import ingest_all; "
        "from backend.db.engine import close_conn; "
        "close_conn(); ingest_all()",
    ]
    last_exc: subprocess.CalledProcessError | None = None

    try:
        max_attempts = _env_int("SMART_UPDATE_INGEST_RETRIES", 3, minimum=1)
        retry_delay = _env_int("SMART_UPDATE_INGEST_RETRY_DELAY_SECONDS", 20, minimum=0)
        for attempt in range(1, max(1, max_attempts) + 1):
            try:
                run_job(f"ingest_attempt_{attempt}", cmd,
                        timeout="ingest", cwd=BASE_DIR, raise_on_fail=True)
                return
            except subprocess.CalledProcessError as exc:
                last_exc = exc
                if attempt < max_attempts:
                    print(
                        f"[ingest] standalone ingest failed with exit code {exc.returncode}; "
                        f"retrying in {retry_delay}s ({attempt}/{max_attempts})"
                    )
                    time.sleep(max(0, retry_delay))
            except subprocess.TimeoutExpired as exc:
                # timeout 視為 retry 機會（非永久失敗）
                last_exc = subprocess.CalledProcessError(124, cmd)
                if attempt < max_attempts:
                    print(
                        f"[ingest] standalone ingest timed out; "
                        f"retrying in {retry_delay}s ({attempt}/{max_attempts})"
                    )
                    time.sleep(max(0, retry_delay))

        exc = last_exc
        print(
            f"[ingest] standalone ingest failed with exit code {exc.returncode if exc else 'unknown'}; "
            "trying server-side ingest fallback"
        )
        try:
            result = _api_post_json("/api/pipeline/ingest", timeout=900)
        except Exception as api_exc:
            raise RuntimeError(
                "standalone ingest failed and API fallback was unavailable"
            ) from api_exc

        if not result.get("success"):
            raise RuntimeError(f"API ingest failed: {result}") from exc

        print(f"[ingest] via API OK: {result.get('results')}")
    finally:
        if released:
            if _api_post("/api/db/reconnect", timeout=30):
                print("[ingest] reconnected web server DuckDB connection")
            else:
                print("[ingest] warning: failed to reconnect web server DuckDB connection")


def exec_predict():
    run_job("predict_v1v2",
            [sys.executable, "-m", "ml.predict"],
            timeout="predict", cwd=BASE_DIR, raise_on_fail=True)
    return
    run_job("predict_t1",
            [sys.executable, "-m", "ml.predict_t1"],
            timeout="predict", cwd=BASE_DIR, raise_on_fail=True)
    # V4 rank-15 shadow: V3 sector-capped Top 30 → re-ranked by T+1 → Top 15
    # shadow failure must not block production pipeline → raise_on_fail=False
    run_job("predict_v4_rank15_shadow",
            [sys.executable, os.path.join(BASE_DIR, "scripts", "build_v4_rank15_shadow.py")],
            timeout="shadow_overlay", cwd=BASE_DIR, raise_on_fail=False)


def exec_verify():
    run_job("verify_predictions",
            [sys.executable, os.path.join(BASE_DIR, "scripts", "verify_predictions.py")],
            timeout="verify", cwd=BASE_DIR, raise_on_fail=True)


def exec_indices():
    """只更新大盤指數 + 國際指標（VIX/費半/S&P500/美元台幣），幾秒就好"""
    run_job("indices",
            [sys.executable, os.path.join(BASE_DIR, "twstock.py"), "--step", "10"],
            timeout="news", cwd=BASE_DIR, raise_on_fail=True)


def exec_margin_backfill():
    """補抓前一交易日融資券（TWSE 約 21:00 公布，Phase-1 19:30 抓不到當日，
    Phase-2 早晨補齊讓當日預測的融資特徵與日K同步，消除 T+1 落差）"""
    run_job("margin_backfill",
            [sys.executable, os.path.join(BASE_DIR, "twstock.py"), "--step", "3"],
            timeout="news", cwd=BASE_DIR, raise_on_fail=False)


def exec_exdiv_calendar():
    """更新除權息日曆與雙口徑公司行動因子。"""
    run_job("exdiv_calendar",
            [sys.executable, os.path.join(BASE_DIR, "scripts", "update_ex_dividend_calendar.py")],
            timeout="news", cwd=BASE_DIR, raise_on_fail=True)
    run_job(
        "corporate_action_factors",
        [
            sys.executable,
            os.path.join(BASE_DIR, "scripts", "update_corporate_action_factors.py"),
        ],
        timeout="news",
        cwd=BASE_DIR,
        raise_on_fail=True,
    )


def exec_taiex_total_return_index():
    """更新官方 TAIEX 含息報酬指數，供重訓標籤雙邊同口徑比較。"""
    output = os.path.join(BASE_DIR, "大盤指數", "index_TWII_total_return.csv")
    command = [
        sys.executable,
        os.path.join(BASE_DIR, "scripts", "update_taiex_total_return_index.py"),
    ]
    if not os.path.exists(output):
        command.append("--backfill")
    run_job(
        "taiex_total_return_index",
        command,
        timeout="news",
        cwd=BASE_DIR,
        raise_on_fail=True,
    )


def exec_macro_strategy_context():
    """Refresh macro calendar + market sentiment context used by selection overlays."""
    run_job("macro_strategy_context",
            [sys.executable, os.path.join(BASE_DIR, "scripts", "update_macro_strategy_context.py")],
            timeout="news", cwd=BASE_DIR, raise_on_fail=False)


TASK_MAP = {
    "twstock":   ("更新台股資料 (日K/法人/融資券/營收/季報/集保/大盤)", exec_twstock),
    "indices":   ("更新國際指數 (VIX/費半/S&P500/匯率)", exec_indices),
    "macro_strategy_context": ("更新國際局勢/事件風險選股情境", exec_macro_strategy_context),
    "valuation": ("更新估值資料 (PE/PB/殖利率)", exec_valuation),
    "eps":       ("更新 EPS 季報", exec_eps),
    "news":      ("更新 MOPS 重大訊息公告", exec_news),
    "tdcc":      ("更新 TDCC 集保股權分散 (週五)", exec_tdcc),
    "retrain":   ("重新訓練 ML 預測模型 (v1+v2)", exec_retrain),
    "predict":   ("產生今日股票預測 (Top 30)", exec_predict),
    "verify":    ("驗證歷史預測準確率", exec_verify),
}


# ==============================================================================
# 主程式
# ==============================================================================

def main():
    parser = argparse.ArgumentParser(description="台股智慧更新系統")
    parser.add_argument("--force", nargs="*", default=None,
                        help="強制重跑。不帶參數=全部重跑，帶任務名=只跑指定任務 "
                             f"(可選: {', '.join(TASK_MAP.keys())})")
    args = parser.parse_args()

    print("=" * 60)
    print("  台股智慧更新系統")
    print(f"  日期: {TODAY} ({['一','二','三','四','五','六','日'][TODAY.weekday()]})")
    print(f"  時間: {NOW.strftime('%H:%M')}")
    print("=" * 60)

    # --force 模式
    if args.force is not None:
        if len(args.force) == 0:
            # --force 不帶參數 → 全部重跑
            tasks = list(TASK_MAP.keys())
            print("\n  [強制模式] 重跑所有任務")
        else:
            # --force twstock predict → 只跑指定的
            invalid = [t for t in args.force if t not in TASK_MAP]
            if invalid:
                print(f"\n  [錯誤] 不認識的任務: {', '.join(invalid)}")
                print(f"  可選任務: {', '.join(TASK_MAP.keys())}")
                return
            tasks = args.force
            print(f"\n  [強制模式] 重跑: {', '.join(tasks)}")
    else:
        # 時間提醒
        if is_weekday() and NOW.hour < 17:
            print("\n  [注意] 目前台股可能尚未收盤或資料尚未完整公布")
            print("         建議 17:00 後再執行，以取得完整當日資料")

        # 分析所有任務 + 狀態
        tasks, reasons = analyze_tasks()

        # 顯示狀態報告
        print(f"\n{'-'*60}")
        print("  資料狀態")
        print(f"{'-'*60}")
        for name, status, priority in reasons:
            icon = "V" if "已" in priority else ("!" if priority == "必要" else "~")
            print(f"  [{icon}] {name}: {status}")

    # 顯示執行計畫，讓用戶選擇
    print(f"\n{'-'*60}")
    print(f"  待執行 {len(tasks)} 項更新（可個別選擇）:")
    print(f"{'-'*60}")
    for i, task_key in enumerate(tasks, 1):
        desc, _ = TASK_MAP[task_key]
        print(f"  {i}. [{task_key}] {desc}")

    print()
    print("  操作方式:")
    print("    Enter     = 全部執行")
    print("    1 3 5     = 只執行第 1, 3, 5 項（空格分隔）")
    print("    -2        = 排除第 2 項，其餘全跑")
    print("    q         = 取消")
    print()
    choice = input("  請選擇: ").strip().lower()
    if choice == "q":
        print("  已取消。")
        return

    # 解析選擇
    if choice == "":
        selected_tasks = tasks[:]
    elif choice.startswith("-"):
        # 排除模式: -2 -3
        excludes = set()
        for tok in choice.split():
            try:
                excludes.add(int(tok.replace("-", "")) - 1)
            except ValueError:
                pass
        selected_tasks = [t for i, t in enumerate(tasks) if i not in excludes]
    else:
        # 選取模式: 1 3 5
        selected_tasks = []
        for tok in choice.split():
            try:
                idx = int(tok) - 1
                if 0 <= idx < len(tasks):
                    selected_tasks.append(tasks[idx])
            except ValueError:
                pass

    if not selected_tasks:
        print("  未選擇任何任務。")
        return

    print(f"\n  將執行 {len(selected_tasks)} 項: {', '.join(selected_tasks)}")

    # 執行
    total_start = time.time()
    results = []
    data_updated = False

    for task_key in selected_tasks:
        desc, func = TASK_MAP[task_key]
        success = run_task(task_key, desc, func)
        results.append((desc, success))
        if success and task_key in ("twstock", "indices", "valuation", "eps", "news", "tdcc"):
            data_updated = True

    # 資料有更新時，自動匯入 DuckDB
    if data_updated:
        success = run_task("ingest", "匯入資料到 DuckDB (供網頁讀取)", exec_ingest)
        results.append(("匯入資料到 DuckDB (供網頁讀取)", success))

    # 結果摘要
    total_elapsed = time.time() - total_start
    print(f"\n{'='*60}")
    print(f"  執行完畢  (總耗時 {total_elapsed/60:.1f} 分鐘)")
    print(f"{'='*60}")
    for desc, success in results:
        icon = "OK" if success else "FAIL"
        print(f"  [{icon}] {desc}")

    print()
    if sys.stdin.isatty():
        input("按 Enter 結束...")


if __name__ == "__main__":
    main()
