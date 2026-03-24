"""每日收盤後自動更新資料 + 重新預測

使用方式:
    python scripts/daily_pipeline.py           # 更新資料 + 預測
    python scripts/daily_pipeline.py --predict-only  # 只做預測
    python scripts/daily_pipeline.py --retrain  # 更新 + 重新訓練模型 + 預測

建議排程：每個交易日 17:00 後執行（台股收盤 13:30，資料約 15:00-16:00 更新完畢）
"""
import os
import sys
import subprocess
import argparse
import json
import logging
from datetime import datetime, date

BASE_DIR = r"F:\stock"
sys.path.insert(0, BASE_DIR)

# Pipeline log
LOG_DIR = os.path.join(BASE_DIR, "logs")
os.makedirs(LOG_DIR, exist_ok=True)
_log_handler = logging.FileHandler(
    os.path.join(LOG_DIR, "pipeline.log"), encoding="utf-8", mode="a"
)
_log_handler.setFormatter(logging.Formatter(
    "%(asctime)s | %(levelname)-5s | %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
))
pipeline_log = logging.getLogger("pipeline")
pipeline_log.setLevel(logging.INFO)
if not pipeline_log.handlers:
    pipeline_log.addHandler(_log_handler)
    pipeline_log.addHandler(logging.StreamHandler())


def is_trading_day():
    """簡單判斷今日是否為交易日（排除週末）"""
    today = date.today()
    return today.weekday() < 5  # 0=Mon, 4=Fri


def run_step(name, func):
    """執行步驟並處理例外"""
    print(f"\n{'='*60}")
    print(f"  {name} ({datetime.now().strftime('%H:%M:%S')})")
    print(f"{'='*60}")
    pipeline_log.info(f"開始: {name}")
    try:
        func()
        pipeline_log.info(f"完成: {name}")
        return True
    except Exception as e:
        pipeline_log.error(f"失敗: {name} — {e}")
        print(f"  [錯誤] {name}: {e}")
        return False


def run_data_update():
    """執行 twstock.py 更新最新資料（Step 1-7）"""
    result = subprocess.run(
        [sys.executable, os.path.join(BASE_DIR, "twstock.py")],
        cwd=BASE_DIR,
        capture_output=False,
    )
    if result.returncode != 0:
        print(f"  [警告] twstock.py 回傳錯誤碼 {result.returncode}")


def run_valuation_update():
    """更新今日估值資料 (PE/PB/殖利率)"""
    today_str = date.today().strftime("%Y-%m-%d")
    result = subprocess.run(
        [sys.executable, os.path.join(BASE_DIR, "scripts", "backfill_valuation.py"),
         "--start-date", today_str],
        cwd=BASE_DIR,
        capture_output=False,
    )
    if result.returncode != 0:
        print(f"  [警告] backfill_valuation.py 回傳錯誤碼 {result.returncode}")


def run_news_update():
    """更新 MOPS 重大訊息公告 (消息面)"""
    result = subprocess.run(
        [sys.executable, os.path.join(BASE_DIR, "scripts", "fetch_daily_news.py")],
        cwd=BASE_DIR,
        capture_output=False,
    )
    if result.returncode != 0:
        print(f"  [警告] fetch_daily_news.py 回傳錯誤碼 {result.returncode}")


def run_tdcc_update():
    """更新 TDCC 集保股權分散表 (每週五收盤後更新)"""
    if date.today().weekday() != 4:  # 只在週五執行
        print("  非週五，跳過 TDCC 更新")
        return
    result = subprocess.run(
        [sys.executable, os.path.join(BASE_DIR, "scripts", "fetch_tdcc_weekly.py")],
        cwd=BASE_DIR,
        capture_output=False,
    )
    if result.returncode != 0:
        print(f"  [警告] fetch_tdcc_weekly.py 回傳錯誤碼 {result.returncode}")


def run_ingest():
    """將 CSV 資料匯入 DuckDB (供網頁讀取)"""
    from backend.db.ingest import ingest_all
    from backend.db.engine import close_conn
    # 關閉舊連線，確保乾淨匯入
    close_conn()
    ingest_all()


def run_retrain():
    """重新訓練 ML 模型"""
    from ml.train import run_training
    run_training()


def run_backtest():
    """模擬投資回測"""
    from ml.backtest import run_backtest as _run_backtest
    _run_backtest(top_n=10)


def run_predict():
    """執行預測"""
    from ml.predict import run_prediction
    run_prediction(top_n=30)


def run_paper_portfolio():
    """Lock the daily Top 30 into the paper portfolio ledger."""
    from scripts.update_paper_portfolio import sync_paper_portfolio
    sync_paper_portfolio(top_n=30)


def run_verify():
    """驗證歷史預測的實際表現"""
    from scripts.verify_predictions import main as verify_main
    verify_main()


def run_email_report():
    """寄送每日 ML 預測與帳本觀察信件。"""
    from scripts.send_daily_email import send_latest_email
    send_latest_email()


def main():
    parser = argparse.ArgumentParser(description="每日自動更新 + 預測 Pipeline")
    parser.add_argument("--predict-only", action="store_true", help="只做預測，不更新資料")
    parser.add_argument("--retrain", action="store_true", help="重新訓練模型")
    parser.add_argument("--force", action="store_true", help="非交易日也強制執行")
    args = parser.parse_args()

    if not args.force and not is_trading_day():
        print("今日非交易日，跳過。使用 --force 強制執行。")
        return

    start = datetime.now()
    pipeline_log.info(f"===== 每日 Pipeline 啟動 ({start.strftime('%Y-%m-%d %H:%M')}) =====")
    print(f"{'='*60}")
    print(f"  台股每日 Pipeline - {start.strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*60}")

    # 追蹤各步驟狀態，供前端顯示資料鮮度
    step_status = {}

    if not args.predict_only:
        step_status["日K/法人/融資券/營收"] = run_step("更新台股日K/法人/融資券/營收", run_data_update)
        step_status["估值(PE/PB/殖利率)"] = run_step("更新估值資料 (PE/PB/殖利率)", run_valuation_update)
        step_status["MOPS重大訊息"] = run_step("更新 MOPS 重大訊息 (消息面)", run_news_update)
        step_status["TDCC集保分散"] = run_step("更新 TDCC 集保分散 (週五)", run_tdcc_update)
        step_status["DuckDB匯入"] = run_step("匯入資料到 DuckDB", run_ingest)

    if args.retrain:
        step_status["模型訓練"] = run_step("重新訓練預測模型", run_retrain)
        step_status["回測"] = run_step("模擬投資回測", run_backtest)

    step_status["預測"] = run_step("產生預測", run_predict)
    step_status["實戰觀測帳本"] = run_step("更新實戰觀測帳本", run_paper_portfolio)
    step_status["預測驗證"] = run_step("驗證歷史預測表現", run_verify)
    step_status["Email推播"] = run_step("寄送每日 ML 預測與帳本觀察", run_email_report)

    elapsed = datetime.now() - start

    # 寫入 pipeline 狀態檔，供前端讀取資料鮮度
    freshness_path = os.path.join(BASE_DIR, "ml", "models", "pipeline_status.json")
    freshness = {
        "last_run": start.strftime("%Y-%m-%d %H:%M:%S"),
        "elapsed_min": round(elapsed.total_seconds() / 60, 1),
        "steps": {k: ("ok" if v else "failed") for k, v in step_status.items()},
        "all_ok": all(step_status.values()),
    }
    with open(freshness_path, "w", encoding="utf-8") as f:
        json.dump(freshness, f, ensure_ascii=False, indent=2)

    pipeline_log.info(f"===== Pipeline 完成 (耗時 {elapsed.total_seconds()/60:.1f} 分鐘) =====")
    print(f"\n全部完成，耗時 {elapsed.total_seconds()/60:.1f} 分鐘")


if __name__ == "__main__":
    main()
