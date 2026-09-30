"""T+1 模型監控腳本 — Rolling IC、勝率追蹤、校準度、衰變偵測。

用法：
    python scripts/monitor_t1.py              # 產出報告
    python scripts/monitor_t1.py --days 20    # 指定回看天數
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sqlite3
import sys
from datetime import datetime

import numpy as np
import pandas as pd
from scipy import stats

BASE_DIR = os.environ.get("STOCK_BASE_DIR") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from ml.config import DAILY_K_DIR, MODEL_DIR

PORTFOLIO_DB = os.path.join(BASE_DIR, "paper_portfolio_t1.db")
REPORT_DIR = os.path.join(BASE_DIR, "ml", "reports")
os.makedirs(REPORT_DIR, exist_ok=True)

# --- 衰變警戒門檻 ---
IC_DECAY_THRESHOLD = 0.02       # Rolling IC < 0.02 → 模型可能失效
WIN_RATE_ALERT = 0.45           # 勝率 < 45% 警告
CONSECUTIVE_LOSS_ALERT = 3      # 連續虧損天數警告
CALIBRATION_DRIFT = 0.15        # 校準誤差 > 15% 警告


# ==============================================================================
# 1. Rolling IC (Information Coefficient)
# ==============================================================================

def _load_prediction_csv(path: str) -> pd.DataFrame | None:
    """載入單日預測 CSV。"""
    try:
        df = pd.read_csv(path, dtype={"ticker": str})
        if "hit_prob_3pct" not in df.columns or "ticker" not in df.columns:
            return None
        return df
    except Exception:
        return None


def _get_next_day_returns(pred_date: str) -> pd.DataFrame | None:
    """從日K資料取得 pred_date 之後第一個交易日的 open→close 報酬。"""
    returns = []
    sample_files = sorted(glob.glob(os.path.join(DAILY_K_DIR, "*.csv")))
    # 抽樣 — 全讀太慢，用預測檔中的 ticker 去查
    return None  # 由 compute_daily_ic 直接處理


def compute_daily_ic(pred_csv: str) -> dict | None:
    """計算單日 IC：hit_prob vs 次日實際報酬（Spearman rank correlation）。"""
    pred_df = _load_prediction_csv(pred_csv)
    if pred_df is None or pred_df.empty:
        return None

    pred_date = str(pred_df["date"].iloc[0])
    pred_ts = pd.Timestamp(pred_date)
    tickers = pred_df["ticker"].tolist()

    actual_returns = {}
    for ticker in tickers:
        kpath = os.path.join(DAILY_K_DIR, f"{ticker}.csv")
        if not os.path.exists(kpath):
            continue
        try:
            kdf = pd.read_csv(kpath, usecols=["Date", "Open", "Close"],
                              dtype={"Date": str})
            kdf["Date"] = pd.to_datetime(kdf["Date"])
            kdf = kdf.sort_values("Date")
            future = kdf[kdf["Date"] > pred_ts].head(1)
            if future.empty:
                continue
            row = future.iloc[0]
            o = float(row["Open"])
            c = float(row["Close"])
            if o > 0 and np.isfinite(o) and np.isfinite(c):
                actual_returns[ticker] = c / o - 1.0
        except Exception:
            continue

    if len(actual_returns) < 30:
        return None

    merged = pred_df[pred_df["ticker"].isin(actual_returns)].copy()
    merged["actual_return"] = merged["ticker"].map(actual_returns)
    merged = merged.dropna(subset=["hit_prob_3pct", "actual_return"])

    if len(merged) < 30:
        return None

    ic_spearman, p_value = stats.spearmanr(merged["hit_prob_3pct"], merged["actual_return"])

    # 分層統計：前 10% vs 後 10%
    n = len(merged)
    top_10pct = merged.nlargest(max(n // 10, 10), "hit_prob_3pct")
    bot_10pct = merged.nsmallest(max(n // 10, 10), "hit_prob_3pct")

    return {
        "date": pred_date,
        "ic_spearman": round(float(ic_spearman), 4),
        "p_value": round(float(p_value), 4),
        "n_stocks": len(merged),
        "top_10pct_avg_return": round(float(top_10pct["actual_return"].mean()), 4),
        "bot_10pct_avg_return": round(float(bot_10pct["actual_return"].mean()), 4),
        "spread": round(
            float(top_10pct["actual_return"].mean() - bot_10pct["actual_return"].mean()), 4
        ),
    }


def compute_rolling_ic(lookback_days: int = 20) -> list[dict]:
    """計算最近 N 天的每日 IC。"""
    pred_files = sorted(glob.glob(os.path.join(MODEL_DIR, "predictions_t1_*.csv")))
    if not pred_files:
        return []

    # 取最近 N 個檔案
    recent = pred_files[-lookback_days:]
    results = []
    for f in recent:
        ic = compute_daily_ic(f)
        if ic is not None:
            results.append(ic)
    return results


# ==============================================================================
# 2. Portfolio Metrics (from paper_portfolio_t1.db)
# ==============================================================================

def compute_portfolio_metrics() -> dict:
    """從紙上帳本計算績效指標。"""
    if not os.path.exists(PORTFOLIO_DB):
        return {"error": "paper_portfolio_t1.db not found"}

    conn = sqlite3.connect(PORTFOLIO_DB)
    conn.row_factory = sqlite3.Row

    # 取所有已結算的 position
    positions = pd.read_sql_query(
        "SELECT prediction_date, ticker, sector, hit_prob, t1_score, "
        "realized_return_pct, max_intraday_gain_pct, max_intraday_drawdown_pct, "
        "hit_3pct, selection_rank "
        "FROM t1_positions WHERE status = 'closed' AND realized_return_pct IS NOT NULL "
        "ORDER BY prediction_date, selection_rank",
        conn,
    )
    conn.close()

    if positions.empty:
        return {"error": "no closed positions"}

    n = len(positions)
    wins = (positions["realized_return_pct"] > 0).sum()
    win_rate = wins / n

    # 日統計
    daily = positions.groupby("prediction_date").agg(
        n_trades=("ticker", "count"),
        avg_return=("realized_return_pct", "mean"),
        win_count=("realized_return_pct", lambda x: (x > 0).sum()),
        avg_hit_prob=("hit_prob", "mean"),
    )
    daily["win_rate"] = daily["win_count"] / daily["n_trades"]

    # 權益曲線
    equity = (1 + daily["avg_return"]).cumprod()
    peak = equity.cummax()
    drawdown = (equity - peak) / peak
    mdd = drawdown.min()

    # 連續虧損
    daily_returns = daily["avg_return"]
    is_loss = (daily_returns < 0).astype(int)
    streaks = is_loss * (is_loss.groupby((is_loss != is_loss.shift()).cumsum()).cumcount() + 1)
    max_consec_loss = int(streaks.max())

    # Rolling 勝率
    rolling_5d = daily["win_rate"].rolling(5, min_periods=3).mean()
    rolling_10d = daily["win_rate"].rolling(10, min_periods=5).mean()

    # 校準度
    calibration = _compute_calibration(positions)

    # Execution Gap
    exec_gap = _compute_execution_gap(positions)

    return {
        "total_positions": n,
        "trading_days": len(daily),
        "overall_win_rate": round(win_rate, 4),
        "avg_return": round(float(positions["realized_return_pct"].mean()), 4),
        "median_return": round(float(positions["realized_return_pct"].median()), 4),
        "avg_hit_prob": round(float(positions["hit_prob"].mean()), 4),
        "hit_3pct_rate": round(float(positions["hit_3pct"].mean()), 4),
        "mdd": round(float(mdd), 4),
        "max_consecutive_losses": max_consec_loss,
        "equity_latest": round(float(equity.iloc[-1]), 4) if not equity.empty else 1.0,
        "latest_rolling_5d_wr": round(float(rolling_5d.iloc[-1]), 4) if rolling_5d.notna().any() else None,
        "latest_rolling_10d_wr": round(float(rolling_10d.iloc[-1]), 4) if rolling_10d.notna().any() else None,
        "daily_returns": [
            {"date": str(d), "return": round(float(r), 4), "win_rate": round(float(w), 4)}
            for d, r, w in zip(daily.index, daily["avg_return"], daily["win_rate"])
        ],
        "calibration": calibration,
        "execution_gap": exec_gap,
    }


def _compute_calibration(positions: pd.DataFrame) -> list[dict]:
    """校準度：預測機率分箱 vs 實際勝率。"""
    if positions.empty or "hit_prob" not in positions.columns:
        return []

    bins = [0.0, 0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 1.01]
    labels = ["<50", "50-55", "55-60", "60-65", "65-70", "70-75", "75-80", "80+"]
    positions = positions.copy()
    positions["prob_bin"] = pd.cut(positions["hit_prob"], bins=bins, labels=labels, right=False)
    positions["win"] = (positions["realized_return_pct"] > 0).astype(int)

    cal = positions.groupby("prob_bin", observed=True).agg(
        n=("win", "count"),
        actual_win_rate=("win", "mean"),
        avg_prob=("hit_prob", "mean"),
        avg_return=("realized_return_pct", "mean"),
    )
    return [
        {
            "bin": str(idx),
            "n": int(row["n"]),
            "predicted_prob": round(float(row["avg_prob"]), 3),
            "actual_win_rate": round(float(row["actual_win_rate"]), 3),
            "avg_return": round(float(row["avg_return"]), 4),
            "gap": round(float(row["actual_win_rate"] - row["avg_prob"]), 3),
        }
        for idx, row in cal.iterrows()
        if row["n"] >= 1
    ]


def _compute_execution_gap(positions: pd.DataFrame) -> dict:
    """Execution Gap：模型預期 vs 實際報酬偏差。"""
    if positions.empty:
        return {}

    # 模型期望值（回測 avg_expectancy = +0.84%）
    backtest_expectancy = 0.0084
    actual_avg = float(positions["realized_return_pct"].mean())
    gap = actual_avg - backtest_expectancy

    return {
        "backtest_expectancy": round(backtest_expectancy, 4),
        "actual_avg_return": round(actual_avg, 4),
        "execution_gap": round(gap, 4),
        "gap_pct_of_expected": round(gap / backtest_expectancy * 100, 1) if backtest_expectancy != 0 else 0,
    }


# ==============================================================================
# 3. Anomaly Detection
# ==============================================================================

def detect_anomalies(ic_series: list[dict], portfolio: dict) -> list[dict]:
    """偵測模型衰變與異常。"""
    warnings = []

    # IC 衰變
    if len(ic_series) >= 3:
        recent_ics = [x["ic_spearman"] for x in ic_series[-5:]]
        avg_ic = np.mean(recent_ics)
        if avg_ic < IC_DECAY_THRESHOLD:
            warnings.append({
                "level": "CRITICAL",
                "type": "IC_DECAY",
                "message": f"近 {len(recent_ics)} 日平均 IC={avg_ic:.4f} < {IC_DECAY_THRESHOLD}，模型可能失效",
                "value": round(avg_ic, 4),
                "threshold": IC_DECAY_THRESHOLD,
            })

        # IC 趨勢（最近 5 天 vs 之前）
        if len(ic_series) >= 10:
            recent_5 = np.mean([x["ic_spearman"] for x in ic_series[-5:]])
            prior_5 = np.mean([x["ic_spearman"] for x in ic_series[-10:-5]])
            if prior_5 > 0 and recent_5 < prior_5 * 0.5:
                warnings.append({
                    "level": "WARNING",
                    "type": "IC_TREND_DOWN",
                    "message": f"IC 下降趨勢：近 5 日 {recent_5:.4f} vs 前 5 日 {prior_5:.4f}",
                    "recent": round(recent_5, 4),
                    "prior": round(prior_5, 4),
                })

    # Top/Bottom spread 消失
    if len(ic_series) >= 3:
        recent_spreads = [x["spread"] for x in ic_series[-5:]]
        avg_spread = np.mean(recent_spreads)
        if avg_spread < 0.001:
            warnings.append({
                "level": "WARNING",
                "type": "SPREAD_COLLAPSE",
                "message": f"Top/Bottom 10% 報酬差距消失 (spread={avg_spread:.4f})，模型無區分力",
                "value": round(avg_spread, 4),
            })

    # Portfolio 警告
    if isinstance(portfolio, dict) and "error" not in portfolio:
        wr = portfolio.get("overall_win_rate", 0.5)
        if wr < WIN_RATE_ALERT:
            warnings.append({
                "level": "WARNING",
                "type": "LOW_WIN_RATE",
                "message": f"整體勝率 {wr:.1%} < {WIN_RATE_ALERT:.0%} 警戒線",
                "value": round(wr, 4),
                "threshold": WIN_RATE_ALERT,
            })

        consec = portfolio.get("max_consecutive_losses", 0)
        if consec >= CONSECUTIVE_LOSS_ALERT:
            warnings.append({
                "level": "WARNING",
                "type": "CONSECUTIVE_LOSSES",
                "message": f"連續虧損 {consec} 天 >= {CONSECUTIVE_LOSS_ALERT} 天警戒",
                "value": consec,
                "threshold": CONSECUTIVE_LOSS_ALERT,
            })

        # 校準漂移
        for cal in portfolio.get("calibration", []):
            if cal["n"] >= 5 and abs(cal["gap"]) > CALIBRATION_DRIFT:
                warnings.append({
                    "level": "INFO",
                    "type": "CALIBRATION_DRIFT",
                    "message": f"機率區間 {cal['bin']}: 預測 {cal['predicted_prob']:.1%} vs 實際 {cal['actual_win_rate']:.1%}",
                    "bin": cal["bin"],
                    "gap": cal["gap"],
                })

    return warnings


# ==============================================================================
# 4. Report Generation
# ==============================================================================

def generate_report(lookback_days: int = 20) -> dict:
    """產出完整監控報告。"""
    print("Computing Rolling IC...")
    ic_series = compute_rolling_ic(lookback_days)

    print("Computing Portfolio Metrics...")
    portfolio = compute_portfolio_metrics()

    print("Detecting Anomalies...")
    anomalies = detect_anomalies(ic_series, portfolio)

    report = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "lookback_days": lookback_days,
        "rolling_ic": ic_series,
        "portfolio": portfolio,
        "anomalies": anomalies,
        "summary": {
            "ic_count": len(ic_series),
            "avg_ic": round(np.mean([x["ic_spearman"] for x in ic_series]), 4) if ic_series else None,
            "avg_spread": round(np.mean([x["spread"] for x in ic_series]), 4) if ic_series else None,
            "anomaly_count": len(anomalies),
            "critical_count": sum(1 for a in anomalies if a["level"] == "CRITICAL"),
        },
    }

    # 存檔
    report_path = os.path.join(REPORT_DIR, "monitor_t1_latest.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"Report saved: {report_path}")

    return report


def print_summary(report: dict) -> None:
    """印出精簡摘要。"""
    print("\n" + "=" * 60)
    print("  T+1 Model Monitor Report")
    print("=" * 60)

    s = report["summary"]
    if s["ic_count"] > 0:
        print(f"\n  Rolling IC ({s['ic_count']} days):")
        print(f"    Avg IC:     {s['avg_ic']}")
        print(f"    Avg Spread: {s['avg_spread']}")
        for ic in report["rolling_ic"]:
            flag = "  " if ic["ic_spearman"] >= IC_DECAY_THRESHOLD else "!!"
            print(f"    {flag} {ic['date']} | IC={ic['ic_spearman']:+.4f} | "
                  f"spread={ic['spread']:+.4f} | n={ic['n_stocks']}")

    p = report["portfolio"]
    if isinstance(p, dict) and "error" not in p:
        print(f"\n  Portfolio ({p['trading_days']} days, {p['total_positions']} trades):")
        print(f"    Win Rate:    {p['overall_win_rate']:.1%}")
        print(f"    Avg Return:  {p['avg_return']:+.2%}")
        print(f"    Hit 3%:      {p['hit_3pct_rate']:.1%}")
        print(f"    MDD:         {p['mdd']:.1%}")
        print(f"    Max Consec Loss: {p['max_consecutive_losses']}")
        print(f"    Equity:      {p['equity_latest']:.4f}")

        if p.get("calibration"):
            print(f"\n  Calibration:")
            for c in p["calibration"]:
                marker = " " if abs(c["gap"]) <= CALIBRATION_DRIFT else "*"
                print(f"   {marker} [{c['bin']:>5}] n={c['n']:3d} | "
                      f"pred={c['predicted_prob']:.1%} actual={c['actual_win_rate']:.1%} "
                      f"gap={c['gap']:+.1%} | ret={c['avg_return']:+.2%}")

        eg = p.get("execution_gap", {})
        if eg:
            print(f"\n  Execution Gap:")
            print(f"    Backtest expect: {eg['backtest_expectancy']:+.2%}")
            print(f"    Actual avg:      {eg['actual_avg_return']:+.2%}")
            print(f"    Gap:             {eg['execution_gap']:+.2%} ({eg['gap_pct_of_expected']:+.0f}%)")

    anomalies = report["anomalies"]
    if anomalies:
        print(f"\n  Anomalies ({len(anomalies)}):")
        for a in anomalies:
            icon = {"CRITICAL": "!!!", "WARNING": " ! ", "INFO": " i "}[a["level"]]
            print(f"    [{icon}] {a['message']}")
    else:
        print("\n  No anomalies detected.")

    print("\n" + "=" * 60)


# ==============================================================================
# Main
# ==============================================================================

def main() -> int:
    parser = argparse.ArgumentParser(description="T+1 Model Monitor")
    parser.add_argument("--days", type=int, default=20, help="Lookback days for rolling IC")
    args = parser.parse_args()

    report = generate_report(lookback_days=args.days)
    print_summary(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
