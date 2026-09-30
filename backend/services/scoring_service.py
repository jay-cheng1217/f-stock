"""四面向評分引擎.

總分 = 技術面(30%) + 基本面(25%) + 籌碼面(30%) + 消息面(15%)
"""

import math
from backend.db.engine import query_df
from backend.services.cache_service import cached
from backend.services.news_service import score_stock_news_sentiment
from backend.utils.indicator_utils import (
    _safe, score_ma_alignment, score_rsi, score_macd,
    score_kd, score_bollinger, score_volume,
)


def _technical_score(ticker: str) -> tuple[float, str]:
    """技術面評分 (30%)."""
    df = query_df("""
        SELECT * FROM daily_k
        WHERE Ticker = $1
        ORDER BY Date DESC
        LIMIT 5
    """, [ticker])

    if df.empty:
        return 50, "無日K資料"

    row = df.iloc[0]
    prev = df.iloc[1] if len(df) > 1 else None

    scores = []
    details = []

    # 均線排列
    s, d = score_ma_alignment(row.get("Close"), row.get("MA_5"), row.get("MA_20"), row.get("MA_60"))
    scores.append(s)
    details.append(d)

    # RSI
    s, d = score_rsi(row.get("RSI_14"))
    scores.append(s)
    details.append(d)

    # MACD
    prev_hist = prev.get("MACDh_12_26_9") if prev is not None else None
    s, d = score_macd(row.get("MACDh_12_26_9"), prev_hist)
    scores.append(s)
    details.append(d)

    # KD
    prev_k = prev.get("K") if prev is not None else None
    prev_d = prev.get("D") if prev is not None else None
    s, d = score_kd(row.get("K"), row.get("D"), prev_k, prev_d)
    scores.append(s)
    details.append(d)

    # 布林
    s, d = score_bollinger(row.get("Close"), row.get("BBU_20_2.0"), row.get("BBL_20_2.0"), row.get("BBM_20_2.0"))
    scores.append(s)
    details.append(d)

    # 量能
    chg = _safe(row.get("Change_Pct"))
    s, d = score_volume(row.get("Volume"), row.get("VOL_MA_5"), row.get("VOL_MA_20"), chg)
    scores.append(s)
    details.append(d)

    avg = sum(scores) / len(scores)
    return round(avg, 1), "、".join(details)


def _fundamental_score(ticker: str) -> tuple[float, str]:
    """基本面評分 (25%)."""
    # 月營收 YoY
    rev_df = query_df("""
        SELECT * FROM revenue
        WHERE Ticker = $1
        ORDER BY Date DESC
        LIMIT 6
    """, [ticker])

    scores = []
    details = []

    if not rev_df.empty:
        latest_yoy = _safe(rev_df.iloc[0].get("YoY_pct_change"))
        if latest_yoy is not None:
            if latest_yoy > 20:
                scores.append(90)
                details.append(f"營收年增率{latest_yoy:.1f}%(高成長)")
            elif latest_yoy > 10:
                scores.append(70)
                details.append(f"營收年增率{latest_yoy:.1f}%(穩定成長)")
            elif latest_yoy > 0:
                scores.append(50)
                details.append(f"營收年增率{latest_yoy:.1f}%(微幅成長)")
            else:
                scores.append(20)
                details.append(f"營收年增率{latest_yoy:.1f}%(衰退)")

        # 營收動能: 近3月趨勢
        if len(rev_df) >= 3:
            yoys = [_safe(rev_df.iloc[i].get("YoY_pct_change")) for i in range(3)]
            yoys = [y for y in yoys if y is not None]
            if len(yoys) >= 2:
                if yoys[0] > yoys[-1]:
                    scores.append(65)
                    details.append("營收動能加速")
                else:
                    scores.append(35)
                    details.append("營收動能減速")

    # 季報毛利率/營益率
    fin_df = query_df("""
        SELECT * FROM financials
        WHERE CAST(Ticker AS VARCHAR) = $1
        ORDER BY Year DESC, Season DESC
        LIMIT 4
    """, [ticker])

    if not fin_df.empty:
        gm = _safe(fin_df.iloc[0].get("Gross_Margin_Pct"))
        om = _safe(fin_df.iloc[0].get("Operating_Margin_Pct"))

        if gm is not None:
            # 毛利率評分 (用簡化百分位)
            if gm > 40:
                scores.append(85)
            elif gm > 25:
                scores.append(65)
            elif gm > 10:
                scores.append(45)
            else:
                scores.append(25)
            details.append(f"毛利率{gm:.1f}%")

        if om is not None:
            if om > 20:
                scores.append(85)
            elif om > 10:
                scores.append(65)
            elif om > 0:
                scores.append(45)
            else:
                scores.append(20)
            details.append(f"營益率{om:.1f}%")

        # 利潤趨勢
        if len(fin_df) >= 2:
            prev_om = _safe(fin_df.iloc[1].get("Operating_Margin_Pct"))
            if om is not None and prev_om is not None:
                if om > prev_om:
                    scores.append(60)
                    details.append("利潤率改善中")
                else:
                    scores.append(40)
                    details.append("利潤率下滑")

    if not scores:
        return 50, "基本面資料不足"

    avg = sum(scores) / len(scores)
    return round(avg, 1), "、".join(details)


def _chip_score(ticker: str) -> tuple[float, str]:
    """籌碼面評分 (30%)."""
    df = query_df("""
        SELECT Date, Foreign_BuySell, Trust_BuySell, Dealer_BuySell,
               Margin_Balance, Short_Balance
        FROM daily_k
        WHERE Ticker = $1
        ORDER BY Date DESC
        LIMIT 20
    """, [ticker])

    if df.empty:
        return 50, "無籌碼資料"

    scores = []
    details = []

    # 外資5日累計
    foreign_5d = sum(
        _safe(df.iloc[i].get("Foreign_BuySell")) or 0
        for i in range(min(5, len(df)))
    )
    if foreign_5d > 1000000:
        scores.append(90)
        details.append(f"外資5日大量買超{foreign_5d/1000:.0f}張")
    elif foreign_5d > 0:
        scores.append(65)
        details.append(f"外資5日小幅買超")
    elif foreign_5d > -1000000:
        scores.append(40)
        details.append(f"外資5日小幅賣超")
    else:
        scores.append(10)
        details.append(f"外資5日大量賣超{foreign_5d/1000:.0f}張")

    # 投信5日累計
    trust_5d = sum(
        _safe(df.iloc[i].get("Trust_BuySell")) or 0
        for i in range(min(5, len(df)))
    )
    if trust_5d > 500000:
        scores.append(90)
        details.append("投信連續買超")
    elif trust_5d > 0:
        scores.append(65)
        details.append("投信小幅買超")
    elif trust_5d > -500000:
        scores.append(40)
        details.append("投信小幅賣超")
    else:
        scores.append(10)
        details.append("投信大量賣超")

    # 外資+投信同步
    if foreign_5d > 0 and trust_5d > 0:
        scores.append(100)
        details.append("外資投信同步買超(強勢訊號)")
    elif foreign_5d < 0 and trust_5d < 0:
        scores.append(0)
        details.append("外資投信同步賣超(弱勢訊號)")
    else:
        scores.append(50)
        details.append("法人看法分歧")

    # 融資變化
    if len(df) >= 5:
        margin_now = _safe(df.iloc[0].get("Margin_Balance"))
        margin_5d = _safe(df.iloc[4].get("Margin_Balance"))
        if margin_now is not None and margin_5d is not None and margin_5d > 0:
            margin_chg = (margin_now - margin_5d) / margin_5d
            if margin_chg < -0.05:
                scores.append(75)
                details.append("融資減少(去槓桿)")
            elif margin_chg > 0.05:
                scores.append(30)
                details.append("融資增加(散戶追高)")
            else:
                scores.append(55)
                details.append("融資持平")

    # 大戶持股 (集保)
    tdcc_df = query_df("""
        SELECT * FROM tdcc
        WHERE CAST(Ticker AS VARCHAR) = $1
        ORDER BY Date DESC
        LIMIT 2
    """, [ticker])

    if not tdcc_df.empty:
        whale = _safe(tdcc_df.iloc[0].get("Whale_Pct"))
        if whale is not None:
            if len(tdcc_df) >= 2:
                prev_whale = _safe(tdcc_df.iloc[1].get("Whale_Pct"))
                if prev_whale is not None and whale > prev_whale:
                    scores.append(85)
                    details.append(f"大戶持股{whale:.1f}%(上升)")
                elif prev_whale is not None and whale < prev_whale:
                    scores.append(30)
                    details.append(f"大戶持股{whale:.1f}%(下降)")
                else:
                    scores.append(55)
                    details.append(f"大戶持股{whale:.1f}%")
            else:
                scores.append(55)
                details.append(f"大戶持股{whale:.1f}%")

    if not scores:
        return 50, "籌碼資料不足"

    avg = sum(scores) / len(scores)
    return round(avg, 1), "、".join(details)


def _sentiment_score(ticker: str) -> tuple[float, str]:
    """Sentiment score (15%): combine market context with recent announcements."""
    scores = []
    details = []

    # Market context: VIX + TWII 5-day move
    vix_df = query_df("""
        SELECT Close FROM indices
        WHERE Index_Name = 'VIX'
        ORDER BY Date DESC
        LIMIT 1
    """)
    twii_df = query_df("""
        SELECT Close FROM indices
        WHERE Index_Name = 'TWII'
        ORDER BY Date DESC
        LIMIT 5
    """)

    if not vix_df.empty:
        vix = _safe(vix_df.iloc[0]["Close"])
        if vix is not None:
            if vix < 15:
                scores.append(80)
                details.append(f"VIX={vix:.1f}(低檔，市場樂觀)")
            elif vix < 25:
                scores.append(55)
                details.append(f"VIX={vix:.1f}(正常)")
            else:
                scores.append(25)
                details.append(f"VIX={vix:.1f}(高檔，市場偏保守)")

    if not twii_df.empty and len(twii_df) >= 2:
        now = _safe(twii_df.iloc[0]["Close"])
        prev = _safe(twii_df.iloc[-1]["Close"])
        if now is not None and prev is not None and prev > 0:
            chg = (now - prev) / prev * 100
            if chg > 1:
                scores.append(75)
                details.append(f"大盤近5日漲{chg:.1f}%")
            elif chg > -1:
                scores.append(55)
                details.append("大盤近5日持平")
            else:
                scores.append(30)
                details.append(f"大盤近5日跌{chg:.1f}%")

    # Company-specific event context: recent 3-day announcements
    news_score, news_detail = score_stock_news_sentiment(ticker, days=3, limit=3)
    scores.append(news_score)
    details.append(news_detail)

    if not scores:
        return 50, "消息面資料不足"

    avg = sum(scores) / len(scores)
    return round(avg, 1), "、".join(details)

def _make_signal(total: float, tech: float, fund: float, chip: float, sent: float) -> str:
    """依總分與各面向分數產生投資訊號."""
    dims_above_70 = sum(1 for s in [tech, fund, chip, sent] if s >= 70)
    if total >= 80 and dims_above_70 >= 3:
        return "強力買進"
    elif total >= 65:
        return "買進"
    elif total >= 40:
        return "持有"
    else:
        return "賣出"


@cached("score", ttl=600)
def score_stock(ticker: str) -> dict:
    """計算單支股票的四面向評分."""
    # 取得名稱
    name_df = query_df("""
        SELECT Name FROM stock_list WHERE Ticker = $1 LIMIT 1
    """, [ticker])
    name = name_df.iloc[0]["Name"] if not name_df.empty else ""

    tech_score, tech_detail = _technical_score(ticker)
    fund_score, fund_detail = _fundamental_score(ticker)
    chip_score, chip_detail = _chip_score(ticker)
    sent_score, sent_detail = _sentiment_score(ticker)

    total = round(
        tech_score * 0.30 +
        fund_score * 0.25 +
        chip_score * 0.30 +
        sent_score * 0.15,
        1
    )
    signal = _make_signal(total, tech_score, fund_score, chip_score, sent_score)

    summary = (
        f"{ticker} {name} — 評分: {total} ({signal})\n"
        f"- 技術面({tech_score}): {tech_detail}\n"
        f"- 基本面({fund_score}): {fund_detail}\n"
        f"- 籌碼面({chip_score}): {chip_detail}\n"
        f"- 消息面({sent_score}): {sent_detail}"
    )

    return {
        "ticker": ticker,
        "name": name,
        "total_score": total,
        "signal": signal,
        "technical": {"score": tech_score, "details": tech_detail},
        "fundamental": {"score": fund_score, "details": fund_detail},
        "chip": {"score": chip_score, "details": chip_detail},
        "sentiment": {"score": sent_score, "details": sent_detail},
        "summary": summary,
    }
