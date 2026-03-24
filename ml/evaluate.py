"""回測評估：模擬實際交易績效"""
import numpy as np
import pandas as pd
from ml.config import TARGET_CLASSES, FORWARD_DAYS


def simulate_trading(pred_df: pd.DataFrame, dataset: pd.DataFrame,
                     top_n: int = 10, initial_capital: float = 1_000_000):
    """模擬交易績效

    策略：每期買入 UP 機率最高的 top_n 支股票，等權重持有 FORWARD_DAYS 天

    Args:
        pred_df: 預測結果 (含 ticker, date, up_prob, signal)
        dataset: 完整資料集 (含 ticker, Date, Close, forward_return)
        top_n: 每期持有股票數
        initial_capital: 初始資金

    Returns:
        dict 包含績效指標
    """
    dataset = dataset.copy()
    dataset["date_str"] = dataset["Date"].dt.strftime("%Y-%m-%d")

    # 合併預測與實際報酬
    merged = pred_df.merge(
        dataset[["ticker", "date_str", "forward_return"]],
        left_on=["ticker", "date"],
        right_on=["ticker", "date_str"],
        how="inner",
    )

    if len(merged) == 0:
        return {"error": "無法合併預測與實際資料"}

    dates = sorted(merged["date"].unique())
    portfolio_returns = []

    for d in dates:
        day_preds = merged[merged["date"] == d].sort_values("up_prob", ascending=False)
        top_picks = day_preds.head(top_n)

        if len(top_picks) == 0:
            continue

        # 等權重報酬
        avg_return = top_picks["forward_return"].mean()
        portfolio_returns.append({
            "date": d,
            "return": avg_return,
            "n_picks": len(top_picks),
        })

    if not portfolio_returns:
        return {"error": "無有效交易日"}

    ret_df = pd.DataFrame(portfolio_returns)
    returns = ret_df["return"].values

    # 績效指標
    total_return = (1 + returns).prod() - 1
    n_periods = len(returns)
    periods_per_year = 252 / FORWARD_DAYS
    annual_return = (1 + total_return) ** (periods_per_year / max(n_periods, 1)) - 1
    volatility = returns.std() * np.sqrt(periods_per_year) if len(returns) > 1 else 0
    sharpe = annual_return / volatility if volatility > 0 else 0
    win_rate = (returns > 0).mean()
    max_drawdown = _max_drawdown(returns)

    return {
        "total_return": total_return,
        "annual_return": annual_return,
        "volatility": volatility,
        "sharpe_ratio": sharpe,
        "win_rate": win_rate,
        "max_drawdown": max_drawdown,
        "n_trades": n_periods,
        "avg_return_per_period": returns.mean(),
        "top_n": top_n,
    }


def _max_drawdown(returns):
    """計算最大回撤"""
    cumulative = (1 + returns).cumprod()
    peak = np.maximum.accumulate(cumulative)
    drawdown = (cumulative - peak) / peak
    return drawdown.min()


def print_evaluation(metrics: dict):
    """印出評估結果"""
    print(f"\n{'='*60}")
    print(f"  回測績效（Top {metrics.get('top_n', 'N')} 策略）")
    print(f"{'='*60}")
    print(f"  總報酬率:     {metrics['total_return']:>10.2%}")
    print(f"  年化報酬:     {metrics['annual_return']:>10.2%}")
    print(f"  年化波動:     {metrics['volatility']:>10.2%}")
    print(f"  Sharpe Ratio: {metrics['sharpe_ratio']:>10.2f}")
    print(f"  勝率:         {metrics['win_rate']:>10.2%}")
    print(f"  最大回撤:     {metrics['max_drawdown']:>10.2%}")
    print(f"  交易次數:     {metrics['n_trades']:>10d}")
    print(f"  平均每期報酬: {metrics['avg_return_per_period']:>10.4%}")
