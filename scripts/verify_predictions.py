"""預測驗證腳本：自動追蹤模型預測的實際表現

每日執行，檢查 20+ 天前的 v2 預測和 5+ 天前的 v1 預測是否準確。
結果寫入 ml/reports/prediction_tracking.json，供前端和 API 讀取。

使用方式：
    python scripts/verify_predictions.py
"""
import os
import sys
import json
import glob
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

BASE_DIR = r"F:\stock"
sys.path.insert(0, BASE_DIR)

from ml.config import MODEL_DIR, REPORT_DIR, DAILY_K_DIR

TRACKING_PATH = os.path.join(REPORT_DIR, "prediction_tracking.json")
# v2 預測 20 個交易日，加上緩衝取 25 天曆日
V2_MIN_CALENDAR_DAYS = 28
# v1 預測 5 個交易日
V1_MIN_CALENDAR_DAYS = 8


def _load_tracking():
    """讀取既有的追蹤紀錄"""
    if os.path.exists(TRACKING_PATH):
        with open(TRACKING_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"verified": [], "summary": {}}


def _save_tracking(data):
    os.makedirs(REPORT_DIR, exist_ok=True)
    with open(TRACKING_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _get_actual_close(ticker: str, target_date: str) -> float | None:
    """取得某檔股票在指定日期（或之後最近交易日）的收盤價"""
    csv_path = os.path.join(DAILY_K_DIR, f"{ticker}.csv")
    if not os.path.exists(csv_path):
        return None
    try:
        df = pd.read_csv(csv_path, parse_dates=["Date"])
    except Exception:
        return None
    df = df.sort_values("Date")
    # 找 target_date 或之後最近的交易日
    mask = df["Date"] >= pd.Timestamp(target_date)
    if mask.any():
        return float(df.loc[mask, "Close"].iloc[0])
    return None


def _get_close_n_trading_days_later(ticker: str, from_date: str, n_days: int):
    """取得 N 個交易日後的收盤價和實際日期"""
    csv_path = os.path.join(DAILY_K_DIR, f"{ticker}.csv")
    if not os.path.exists(csv_path):
        return None, None
    try:
        df = pd.read_csv(csv_path, parse_dates=["Date"])
    except Exception:
        return None, None
    df = df.sort_values("Date").reset_index(drop=True)
    mask = df["Date"] >= pd.Timestamp(from_date)
    future = df[mask]
    if len(future) <= n_days:
        return None, None  # 資料不夠
    row = future.iloc[n_days]
    return float(row["Close"]), str(row["Date"].date())


def verify_one_prediction_file(pred_path: str, today: datetime):
    """驗證一個預測檔案"""
    pred_df = pd.read_csv(pred_path, encoding="utf-8-sig")
    if "date" not in pred_df.columns or "ticker" not in pred_df.columns:
        return None
    pred_date_str = pred_df["date"].iloc[0]
    pred_date = datetime.strptime(pred_date_str, "%Y-%m-%d")
    days_elapsed = (today - pred_date).days

    has_v2 = "pred_return_20d" in pred_df.columns and pred_df["pred_return_20d"].notna().any()
    has_recommendation = "recommendation" in pred_df.columns

    result = {
        "prediction_date": pred_date_str,
        "verified_at": today.strftime("%Y-%m-%d"),
        "days_elapsed": days_elapsed,
        "total_stocks": len(pred_df),
    }

    # === v2 驗證 (20 個交易日後) ===
    if has_v2 and days_elapsed >= V2_MIN_CALENDAR_DAYS:
        v2_records = []
        for _, row in pred_df.iterrows():
            if pd.isna(row.get("pred_return_20d")):
                continue
            ticker = str(row["ticker"])
            pred_close = float(row["close"])
            pred_ret = float(row["pred_return_20d"])
            rec = str(row.get("recommendation", ""))

            actual_close, actual_date = _get_close_n_trading_days_later(
                ticker, pred_date_str, 20
            )
            if actual_close is None:
                continue

            actual_ret = actual_close / pred_close - 1
            v2_records.append({
                "ticker": ticker,
                "pred_return": pred_ret,
                "actual_return": actual_ret,
                "recommendation": rec,
                "pred_close": pred_close,
                "actual_close": actual_close,
            })

        if v2_records:
            rdf = pd.DataFrame(v2_records)
            # 整體指標
            ic = rdf["pred_return"].corr(rdf["actual_return"], method="spearman")
            mae = (rdf["pred_return"] - rdf["actual_return"]).abs().mean()

            # 各推薦等級的實際表現
            rec_stats = {}
            for rec_name in ["強力買進", "建議買進", "觀望", "建議賣出", "強力賣出"]:
                sub = rdf[rdf["recommendation"] == rec_name]
                if len(sub) == 0:
                    continue
                rec_stats[rec_name] = {
                    "count": len(sub),
                    "avg_predicted_return": round(float(sub["pred_return"].mean()) * 100, 2),
                    "avg_actual_return": round(float(sub["actual_return"].mean()) * 100, 2),
                    "win_rate": round(float((sub["actual_return"] > 0).mean()) * 100, 1),
                    "hit_rate_direction": round(
                        float(((sub["pred_return"] > 0) == (sub["actual_return"] > 0)).mean()) * 100, 1
                    ),
                }

            # Top 30 vs Bottom 30
            rdf_sorted = rdf.sort_values("pred_return", ascending=False)
            top30 = rdf_sorted.head(30)
            bot30 = rdf_sorted.tail(30)

            result["v2"] = {
                "n_verified": len(rdf),
                "ic_spearman": round(float(ic), 4) if not pd.isna(ic) else None,
                "mae": round(float(mae) * 100, 2),
                "avg_predicted_return": round(float(rdf["pred_return"].mean()) * 100, 2),
                "avg_actual_return": round(float(rdf["actual_return"].mean()) * 100, 2),
                "overall_win_rate": round(float((rdf["actual_return"] > 0).mean()) * 100, 1),
                "top30_avg_actual": round(float(top30["actual_return"].mean()) * 100, 2),
                "bot30_avg_actual": round(float(bot30["actual_return"].mean()) * 100, 2),
                "spread": round(float((top30["actual_return"].mean() - bot30["actual_return"].mean()) * 100), 2),
                "recommendation_accuracy": rec_stats,
            }

    # === v1 驗證 (5 個交易日後) ===
    if days_elapsed >= V1_MIN_CALENDAR_DAYS:
        v1_records = []
        for _, row in pred_df.iterrows():
            ticker = str(row["ticker"])
            pred_close = float(row["close"])
            signal = str(row.get("signal", ""))
            up_prob = float(row.get("up_prob", 0))

            actual_close, _ = _get_close_n_trading_days_later(
                ticker, pred_date_str, 5
            )
            if actual_close is None:
                continue

            actual_ret = actual_close / pred_close - 1
            v1_records.append({
                "ticker": ticker,
                "signal": signal,
                "up_prob": up_prob,
                "actual_return": actual_ret,
            })

        if v1_records:
            vdf = pd.DataFrame(v1_records)
            # 信號準確率
            signal_stats = {}
            for sig in ["UP", "FLAT", "DOWN"]:
                sub = vdf[vdf["signal"] == sig]
                if len(sub) == 0:
                    continue
                # UP 預測: actual > 1.5% 算正確
                # DOWN 預測: actual < -1.5% 算正確
                if sig == "UP":
                    correct = (sub["actual_return"] > 0.015).sum()
                elif sig == "DOWN":
                    correct = (sub["actual_return"] < -0.015).sum()
                else:
                    correct = (sub["actual_return"].abs() <= 0.015).sum()
                signal_stats[sig] = {
                    "count": len(sub),
                    "correct": int(correct),
                    "hit_rate": round(float(correct / len(sub)) * 100, 1),
                    "avg_actual_return": round(float(sub["actual_return"].mean()) * 100, 2),
                }

            # Top 30 by up_prob
            top30 = vdf.sort_values("up_prob", ascending=False).head(30)
            result["v1"] = {
                "n_verified": len(vdf),
                "signal_accuracy": signal_stats,
                "top30_by_up_prob_avg_return": round(float(top30["actual_return"].mean()) * 100, 2),
                "top30_win_rate": round(float((top30["actual_return"] > 0).mean()) * 100, 1),
            }

    return result


def compute_summary(verified_list: list) -> dict:
    """從所有已驗證的預測中計算綜合統計"""
    v2_results = [v for v in verified_list if "v2" in v]
    v1_results = [v for v in verified_list if "v1" in v]

    summary = {"last_updated": datetime.now().strftime("%Y-%m-%d %H:%M")}

    if v2_results:
        ics = [v["v2"]["ic_spearman"] for v in v2_results if v["v2"].get("ic_spearman") is not None]
        spreads = [v["v2"]["spread"] for v in v2_results]
        win_rates = [v["v2"]["overall_win_rate"] for v in v2_results]

        # 彙整各推薦等級的歷史表現
        rec_totals = {}
        for v in v2_results:
            for rec_name, stats in v["v2"].get("recommendation_accuracy", {}).items():
                if rec_name not in rec_totals:
                    rec_totals[rec_name] = {"returns": [], "wins": 0, "total": 0}
                rec_totals[rec_name]["returns"].append(
                    stats["avg_actual_return"] * stats["count"]
                )
                rec_totals[rec_name]["wins"] += int(stats["win_rate"] / 100 * stats["count"])
                rec_totals[rec_name]["total"] += stats["count"]

        rec_summary = {}
        for rec_name, data in rec_totals.items():
            if data["total"] > 0:
                rec_summary[rec_name] = {
                    "total_predictions": data["total"],
                    "historical_win_rate": round(data["wins"] / data["total"] * 100, 1),
                    "avg_actual_return": round(sum(data["returns"]) / data["total"], 2),
                }

        summary["v2"] = {
            "n_periods_verified": len(v2_results),
            "avg_ic": round(float(np.mean(ics)), 4) if ics else None,
            "avg_spread": round(float(np.mean(spreads)), 2) if spreads else None,
            "avg_win_rate": round(float(np.mean(win_rates)), 1) if win_rates else None,
            "recommendation_historical": rec_summary,
        }

    if v1_results:
        summary["v1"] = {
            "n_periods_verified": len(v1_results),
        }

    return summary


def main():
    today = datetime.now()
    tracking = _load_tracking()
    already_verified = {v["prediction_date"] for v in tracking["verified"]}

    # 找所有預測檔
    pred_files = sorted(glob.glob(os.path.join(MODEL_DIR, "predictions_*.csv")))

    new_verifications = 0
    for pf in pred_files:
        # 從檔名取日期
        basename = os.path.basename(pf)
        date_part = basename.replace("predictions_", "").replace(".csv", "")
        if date_part in already_verified:
            # 檢查是否需要更新（可能之前只有 v1 驗證，現在可以加 v2）
            existing = next(v for v in tracking["verified"] if v["prediction_date"] == date_part)
            has_v2 = "v2" in existing
            pred_date = datetime.strptime(date_part, "%Y-%m-%d")
            days_elapsed = (today - pred_date).days
            if has_v2 or days_elapsed < V2_MIN_CALENDAR_DAYS:
                continue
            # 更新：加入 v2 驗證
            print(f"更新驗證: {date_part} (加入 v2)")
            result = verify_one_prediction_file(pf, today)
            if result and "v2" in result:
                existing.update(result)
                new_verifications += 1
            continue

        result = verify_one_prediction_file(pf, today)
        if result and ("v1" in result or "v2" in result):
            tracking["verified"].append(result)
            new_verifications += 1
            print(f"驗證完成: {date_part}")
            if "v2" in result:
                v2 = result["v2"]
                print(f"  v2: IC={v2.get('ic_spearman')}, Spread={v2.get('spread')}%, "
                      f"WinRate={v2.get('overall_win_rate')}%")
            if "v1" in result:
                v1 = result["v1"]
                print(f"  v1: {v1.get('signal_accuracy', {})}")
        else:
            print(f"跳過: {date_part} (尚未到期)")

    if new_verifications > 0 or not tracking.get("summary"):
        tracking["summary"] = compute_summary(tracking["verified"])
        _save_tracking(tracking)
        print(f"\n已更新 {new_verifications} 筆驗證，結果儲存至 {TRACKING_PATH}")
    else:
        print("無新的可驗證預測")

    # 印出摘要
    s = tracking.get("summary", {})
    if "v2" in s:
        v2s = s["v2"]
        print(f"\n=== v2 模型歷史表現 ({v2s.get('n_periods_verified', 0)} 期) ===")
        if v2s.get("avg_ic") is not None:
            print(f"  平均 IC: {v2s['avg_ic']}")
        if v2s.get("avg_spread") is not None:
            print(f"  平均 Top-Bot Spread: {v2s['avg_spread']}%")
        if v2s.get("avg_win_rate") is not None:
            print(f"  平均勝率: {v2s['avg_win_rate']}%")
        rec_hist = v2s.get("recommendation_historical", {})
        if rec_hist:
            print("  各等級歷史表現:")
            for rec_name in ["強力買進", "建議買進", "觀望", "建議賣出", "強力賣出"]:
                if rec_name in rec_hist:
                    rh = rec_hist[rec_name]
                    print(f"    {rec_name}: 勝率={rh['historical_win_rate']}%, "
                          f"平均報酬={rh['avg_actual_return']}%, "
                          f"樣本={rh['total_predictions']}")


if __name__ == "__main__":
    main()
