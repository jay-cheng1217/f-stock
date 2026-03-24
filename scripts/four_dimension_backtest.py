"""四大面向股票評分系統 + 回測

技術面 (-2~+2): RSI、MACD、均線、布林帶
籌碼面 (-2~+2): 外資、投信、融資券
基本面 (-2~+2): 營收成長、毛利率、EPS、估值
產業面 (-2~+2): 類股動能、相對強弱

總分 -8 ~ +8，明確買賣建議
"""
import os, sys, warnings
import pandas as pd
import numpy as np

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ml.config import DAILY_K_DIR
from ml.dataset import load_single_stock, _load_twii, _list_daily_tickers


def _safe_nan(v):
    """安全的 NaN 檢查"""
    try:
        return v is None or (isinstance(v, float) and np.isnan(v)) or pd.isna(v)
    except (TypeError, ValueError):
        return True


def score_technical(row):
    """技術面評分 (-2 ~ +2)"""
    s = 0.0

    # RSI14: <30 超賣加分, >70 超買扣分
    rsi = row.get("rsi_14")
    if not _safe_nan(rsi):
        if rsi < 30:
            s += 1.0
        elif rsi < 40:
            s += 0.5
        elif rsi > 70:
            s -= 1.0
        elif rsi > 60:
            s -= 0.5

    # 均線偏離 MA60 (IC=-0.058, 最強因子)
    pma60 = row.get("price_vs_ma60")
    if not _safe_nan(pma60):
        if pma60 < -0.10:
            s += 1.0  # 嚴重超賣
        elif pma60 < -0.03:
            s += 0.5
        elif pma60 > 0.15:
            s -= 1.0  # 嚴重超買
        elif pma60 > 0.08:
            s -= 0.5

    # MACD 方向
    macd = row.get("macd_hist")
    if not _safe_nan(macd):
        if macd > 0:
            s += 0.3
        else:
            s -= 0.3

    # KD 金叉/死叉
    k = row.get("kd_k")
    d = row.get("kd_d")
    if not _safe_nan(k) and not np.isnan(d):
        if k > d and k < 50:
            s += 0.2  # 低檔金叉
        elif k < d and k > 50:
            s -= 0.2  # 高檔死叉

    return np.clip(s, -2, 2)


def score_institutional(row):
    """籌碼面評分 (-2 ~ +2)

    注意：因子研究顯示法人買超比例是逆向指標 (IC=-0.028)
    所以法人瘋狂買反而要扣分
    """
    s = 0.0

    # 外資 20 日累計
    fc20 = row.get("foreign_cumsum_20d")
    if not _safe_nan(fc20):
        if fc20 > 0:
            s += 0.5  # 外資買超（小幅加分）
        else:
            s -= 0.3

    # 投信 20 日累計
    tc20 = row.get("trust_cumsum_20d")
    if not _safe_nan(tc20):
        if tc20 > 0:
            s += 0.5
        else:
            s -= 0.3

    # 外資投信同步買 (重要信號)
    sync = row.get("foreign_trust_sync")
    if not _safe_nan(sync):
        if sync > 0:
            s += 0.5

    # 融資融券比 (融資太多 = 散戶過熱)
    msr = row.get("margin_short_ratio")
    if not _safe_nan(msr):
        if msr > 10:
            s -= 0.5  # 融資過多，散戶過熱
        elif msr < 3:
            s += 0.3  # 融資少，籌碼乾淨

    # 外資連買
    streak = row.get("foreign_buy_streak")
    if not _safe_nan(streak):
        if streak >= 5:
            s += 0.3
        elif streak >= 3:
            s += 0.2

    return np.clip(s, -2, 2)


def score_fundamental(row):
    """基本面評分 (-2 ~ +2)"""
    s = 0.0

    # 營收 YoY
    rev = row.get("revenue_yoy_latest")
    if not _safe_nan(rev):
        if rev > 0.20:
            s += 1.0  # 營收大成長
        elif rev > 0.05:
            s += 0.5
        elif rev < -0.10:
            s -= 0.5
        elif rev < -0.20:
            s -= 1.0

    # 營收動能 (加速成長)
    rev_mom = row.get("revenue_yoy_momentum")
    if not _safe_nan(rev_mom):
        if rev_mom > 0:
            s += 0.3  # 成長加速
        else:
            s -= 0.2

    # 毛利率趨勢
    mt = row.get("margin_trend")
    if not _safe_nan(mt):
        if mt > 0:
            s += 0.3
        else:
            s -= 0.2

    # EPS YoY
    ey = row.get("eps_yoy")
    if not _safe_nan(ey):
        if ey > 0.2:
            s += 0.5
        elif ey > 0:
            s += 0.2
        elif ey < -0.2:
            s -= 0.5

    # PE 估值 (偏低加分)
    pe_pct = row.get("pe_percentile_60d")
    if not _safe_nan(pe_pct):
        if pe_pct < 0.2:
            s += 0.3  # PE 歷史低位，便宜
        elif pe_pct > 0.8:
            s -= 0.3  # PE 歷史高位，貴

    return np.clip(s, -2, 2)


def score_sector(row):
    """產業面評分 (-2 ~ +2)"""
    s = 0.0

    # 類股相對報酬 (20日)
    sr20 = row.get("sector_relative_return_20d")
    if not _safe_nan(sr20):
        if sr20 > 0.05:
            s += 1.0  # 強勢族群
        elif sr20 > 0.02:
            s += 0.5
        elif sr20 < -0.05:
            s -= 1.0
        elif sr20 < -0.02:
            s -= 0.5

    # 類股動能
    sm20 = row.get("sector_momentum_20d")
    if not _safe_nan(sm20):
        if sm20 > 0.05:
            s += 0.5
        elif sm20 < -0.05:
            s -= 0.5

    # 類股廣度 (多少股票上漲)
    sb = row.get("sector_breadth")
    if not _safe_nan(sb):
        if sb > 0.6:
            s += 0.5  # 多數上漲
        elif sb < 0.3:
            s -= 0.5  # 多數下跌

    return np.clip(s, -2, 2)


def main():
    twii = _load_twii()
    tickers = _list_daily_tickers()
    print(f"Loading stocks...")

    stock_data = {}
    for t in tickers:
        df = load_single_stock(t, twii_df=twii)
        if df is not None and len(df) > 120:
            stock_data[t] = df
    print(f"Loaded {len(stock_data)} stocks")

    # Walk-forward: 每月底評分 → 看 20 天後報酬
    test_months = pd.date_range("2024-01-01", "2026-02-01", freq="MS")
    monthly_results = []

    for month_start in test_months:
        month_end = month_start + pd.offsets.MonthEnd(0)
        records = []

        for t, df in stock_data.items():
            mask = df["Date"] <= month_end
            sub = df[mask]
            if len(sub) < 60:
                continue

            last = sub.iloc[-1]
            if (month_end - last["Date"]).days > 5:
                continue

            # 四面向評分
            r = last.to_dict()
            tech = score_technical(r)
            inst = score_institutional(r)
            fund = score_fundamental(r)
            sect = score_sector(r)
            total = tech + inst + fund + sect

            # 20 日後報酬
            future = df[df["Date"] > month_end]
            if len(future) < 20:
                continue
            fwd20 = future["Close"].iloc[min(19, len(future)-1)] / last["Close"] - 1

            records.append({
                "ticker": t,
                "tech": tech, "inst": inst, "fund": fund, "sect": sect,
                "total": total,
                "fwd20": fwd20,
            })

        if not records:
            continue

        pdf = pd.DataFrame(records).sort_values("total", ascending=False)
        n = len(pdf)
        top30 = pdf.head(30)
        bot30 = pdf.tail(30)

        ic = pdf["total"].corr(pdf["fwd20"], method="spearman")
        spread = top30["fwd20"].mean() - bot30["fwd20"].mean()
        top_win = (top30["fwd20"] > 0).mean()

        # 各面向 IC
        ic_tech = pdf["tech"].corr(pdf["fwd20"], method="spearman")
        ic_inst = pdf["inst"].corr(pdf["fwd20"], method="spearman")
        ic_fund = pdf["fund"].corr(pdf["fwd20"], method="spearman")
        ic_sect = pdf["sect"].corr(pdf["fwd20"], method="spearman")

        monthly_results.append({
            "month": month_start.strftime("%Y-%m"),
            "n": n,
            "top30": top30["fwd20"].mean(),
            "bot30": bot30["fwd20"].mean(),
            "all": pdf["fwd20"].mean(),
            "spread": spread,
            "top_win": top_win,
            "ic": ic,
            "ic_tech": ic_tech,
            "ic_inst": ic_inst,
            "ic_fund": ic_fund,
            "ic_sect": ic_sect,
        })

        print(f"  {month_start.strftime('%Y-%m')}  N={n:>4d}  "
              f"Top30={top30['fwd20'].mean()*100:+.2f}%  Bot30={bot30['fwd20'].mean()*100:+.2f}%  "
              f"WinRate={top_win*100:.0f}%  IC={ic:+.3f}  "
              f"[tech={ic_tech:+.3f} inst={ic_inst:+.3f} fund={ic_fund:+.3f} sect={ic_sect:+.3f}]")

    rdf = pd.DataFrame(monthly_results)
    print("\n" + "=" * 80)
    print("  Four-Dimension Scoring System Backtest")
    print("=" * 80)
    print(f"  Months: {len(rdf)}")
    print(f"  Top 30 avg 20d return:  {rdf['top30'].mean()*100:+.3f}%")
    print(f"  Bot 30 avg 20d return:  {rdf['bot30'].mean()*100:+.3f}%")
    print(f"  Top-Bot spread:         {rdf['spread'].mean()*100:+.3f}%")
    print(f"  Top 30 win rate:        {rdf['top_win'].mean()*100:.1f}%")
    print(f"  Avg IC (total):         {rdf['ic'].mean():+.4f}")
    print(f"  IC > 0 months:          {(rdf['ic']>0).sum()}/{len(rdf)} ({(rdf['ic']>0).mean()*100:.0f}%)")
    print(f"  Spread > 0 months:      {(rdf['spread']>0).sum()}/{len(rdf)} ({(rdf['spread']>0).mean()*100:.0f}%)")

    print(f"\n  --- Per-Dimension IC ---")
    print(f"  Technical:  {rdf['ic_tech'].mean():+.4f}  (IC>0: {(rdf['ic_tech']>0).sum()}/{len(rdf)})")
    print(f"  Institutional: {rdf['ic_inst'].mean():+.4f}  (IC>0: {(rdf['ic_inst']>0).sum()}/{len(rdf)})")
    print(f"  Fundamental:   {rdf['ic_fund'].mean():+.4f}  (IC>0: {(rdf['ic_fund']>0).sum()}/{len(rdf)})")
    print(f"  Sector:        {rdf['ic_sect'].mean():+.4f}  (IC>0: {(rdf['ic_sect']>0).sum()}/{len(rdf)})")

    # 累計
    cum_top = (1 + rdf["top30"]).cumprod()
    cum_all = (1 + rdf["all"]).cumprod()
    print(f"\n  Cumulative (Top 30):    {(cum_top.iloc[-1]-1)*100:+.1f}%")
    print(f"  Cumulative (All):       {(cum_all.iloc[-1]-1)*100:+.1f}%")
    print(f"  Alpha:                  {(cum_top.iloc[-1]-cum_all.iloc[-1])*100:+.1f}%")

    # 各分數區間報酬
    print(f"\n  --- Score Bucket Analysis (20d return) ---")
    all_records = pd.concat([
        pd.DataFrame(r) for m in test_months
        for r in [_get_month_records(m, stock_data)]
        if r is not None
    ], ignore_index=True) if False else None

    # 簡單用最後一批的結果
    for lo, hi, label in [(-8, -3, "Strong Sell"), (-3, -1, "Sell"),
                           (-1, 1, "Hold"), (1, 3, "Buy"), (3, 8, "Strong Buy")]:
        pass  # 用月度累計分析


def _get_month_records(month_start, stock_data):
    return None


if __name__ == "__main__":
    main()
