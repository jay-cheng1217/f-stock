"""大盤概況 API."""

import math
import os

from fastapi import APIRouter, Query

from backend.db.engine import query_df
from ml.config import INDEX_DIR

router = APIRouter(prefix="/api/market", tags=["market"])


@router.get("/indices")
def market_indices():
    """大盤指數最新資料."""
    df = query_df("""
        WITH latest AS (
            SELECT Index_Name, Close, Date,
                   ROW_NUMBER() OVER (PARTITION BY Index_Name ORDER BY Date DESC) AS rn
            FROM indices
        ),
        prev AS (
            SELECT Index_Name, Close AS Prev_Close,
                   ROW_NUMBER() OVER (PARTITION BY Index_Name ORDER BY Date DESC) AS rn
            FROM indices
        )
        SELECT l.Index_Name, l.Close, l.Date,
               p.Prev_Close
        FROM latest l
        LEFT JOIN prev p ON l.Index_Name = p.Index_Name AND p.rn = 2
        WHERE l.rn = 1
    """)

    # 固定顯示順序
    display_order = ["TWII", "GSPC", "SOX", "VIX", "USDTWDX"]
    name_map = {
        "TWII": "加權指數",
        "GSPC": "S&P 500",
        "SOX": "費城半導體",
        "VIX": "VIX恐慌指數",
        "USDTWDX": "美元/台幣",
    }

    row_map = {}
    for _, row in df.iterrows():
        close = _float(row["Close"])
        prev = _float(row.get("Prev_Close"))
        change = round(close - prev, 2) if close and prev else None
        change_pct = round(change / prev * 100, 2) if change and prev else None
        code = row["Index_Name"]
        row_map[code] = {
            "name": name_map.get(code, code),
            "code": code,
            "last_close": close,
            "change": change,
            "change_pct": change_pct,
            "last_date": str(row["Date"]),
        }

    results = [row_map[k] for k in display_order if k in row_map]
    # 未在 display_order 中的指數放最後
    for code in row_map:
        if code not in display_order:
            results.append(row_map[code])

    return results


@router.get("/overview")
def market_overview():
    """市場概況: 漲跌幅最大、成交量最大."""
    gainers = query_df("""
        SELECT Ticker, Name, Last_Close, Last_Change_Pct, Last_Volume
        FROM stock_list
        WHERE Last_Change_Pct IS NOT NULL
        ORDER BY Last_Change_Pct DESC
        LIMIT 10
    """)
    losers = query_df("""
        SELECT Ticker, Name, Last_Close, Last_Change_Pct, Last_Volume
        FROM stock_list
        WHERE Last_Change_Pct IS NOT NULL
        ORDER BY Last_Change_Pct ASC
        LIMIT 10
    """)
    volume = query_df("""
        SELECT Ticker, Name, Last_Close, Last_Change_Pct, Last_Volume
        FROM stock_list
        WHERE Last_Volume IS NOT NULL
        ORDER BY Last_Volume DESC
        LIMIT 10
    """)

    def _to_list(df):
        return [
            {
                "ticker": row["Ticker"],
                "name": row.get("Name", ""),
                "last_close": _float(row.get("Last_Close")),
                "last_change_pct": _float(row.get("Last_Change_Pct")),
                "last_volume": _int(row.get("Last_Volume")),
            }
            for _, row in df.iterrows()
        ]

    return {
        "top_gainers": _to_list(gainers),
        "top_losers": _to_list(losers),
        "top_volume": _to_list(volume),
    }


def _float(val):
    if val is None:
        return None
    try:
        f = float(val)
        return None if math.isnan(f) else round(f, 2)
    except (ValueError, TypeError):
        return None


def _int(val):
    if val is None:
        return None
    try:
        f = float(val)
        return None if math.isnan(f) else int(f)
    except (ValueError, TypeError):
        return None


# ==============================================================================
# 大盤多空燈號 — ML 預測的保護傘
# ==============================================================================
@router.get("/regime")
def market_regime():
    """大盤多空燈號 — ML 預測的保護傘。

    綠燈：正常進場
    黃燈：減碼操作（建議資金部位砍半）
    紅燈：禁止進場（大盤系統性風險過高）
    """
    import pandas as pd
    import numpy as np

    indicators = {}
    reasons = []

    twii_path = os.path.join(INDEX_DIR, "index_TWII.csv")
    vix_path = os.path.join(INDEX_DIR, "index_VIX.csv")
    sox_path = os.path.join(INDEX_DIR, "index_SOX.csv")

    try:
        twii = pd.read_csv(twii_path, dtype={"Date": str})
        twii["Date"] = pd.to_datetime(twii["Date"])
        twii = twii.sort_values("Date").tail(60)
        twii_close = twii["Close"].values
        twii_latest = float(twii_close[-1])
        twii_ma20 = float(np.mean(twii_close[-20:]))
        twii_ma5 = float(np.mean(twii_close[-5:]))
        twii_5d_ret = (twii_close[-1] / twii_close[-6] - 1) * 100 if len(twii_close) >= 6 else 0
        twii_1d_ret = (twii_close[-1] / twii_close[-2] - 1) * 100 if len(twii_close) >= 2 else 0
        twii_vs_ma20 = (twii_latest / twii_ma20 - 1) * 100

        indicators["twii"] = round(twii_latest, 0)
        indicators["twii_ma20"] = round(twii_ma20, 0)
        indicators["twii_vs_ma20"] = round(twii_vs_ma20, 2)
        indicators["twii_5d_ret"] = round(twii_5d_ret, 2)
        indicators["twii_1d_ret"] = round(twii_1d_ret, 2)
        indicators["twii_date"] = twii["Date"].iloc[-1].strftime("%Y-%m-%d")
    except Exception:
        twii_vs_ma20 = 0
        twii_5d_ret = 0
        twii_1d_ret = 0

    try:
        vix = pd.read_csv(vix_path, dtype={"Date": str})
        vix["Date"] = pd.to_datetime(vix["Date"])
        vix = vix.sort_values("Date").tail(10)
        vix_latest = float(vix["Close"].iloc[-1])
        vix_prev = float(vix["Close"].iloc[-2]) if len(vix) >= 2 else vix_latest
        vix_5d_ago = float(vix["Close"].iloc[-6]) if len(vix) >= 6 else vix_latest
        vix_1d_chg = vix_latest - vix_prev
        vix_5d_chg = vix_latest - vix_5d_ago

        indicators["vix"] = round(vix_latest, 1)
        indicators["vix_1d_chg"] = round(vix_1d_chg, 1)
        indicators["vix_5d_chg"] = round(vix_5d_chg, 1)
    except Exception:
        vix_latest = 15
        vix_1d_chg = 0
        vix_5d_chg = 0

    try:
        sox = pd.read_csv(sox_path, dtype={"Date": str})
        sox["Date"] = pd.to_datetime(sox["Date"])
        sox = sox.sort_values("Date").tail(10)
        sox_latest = float(sox["Close"].iloc[-1])
        sox_5d_ago = float(sox["Close"].iloc[-6]) if len(sox) >= 6 else sox_latest
        sox_5d_ret = (sox_latest / sox_5d_ago - 1) * 100

        indicators["sox_5d_ret"] = round(sox_5d_ret, 2)
    except Exception:
        sox_5d_ret = 0

    # --- 紅綠燈判定邏輯 ---
    red_flags = 0
    yellow_flags = 0

    if vix_latest >= 30:
        red_flags += 2
        reasons.append(f"VIX={vix_latest:.1f} 極度恐慌（>30），市場處於恐慌拋售狀態")
    elif vix_latest >= 25:
        red_flags += 1
        reasons.append(f"VIX={vix_latest:.1f} 偏高（>25），市場恐慌升溫")
    elif vix_latest >= 20:
        yellow_flags += 1
        reasons.append(f"VIX={vix_latest:.1f} 警戒區（>20），市場不安情緒升高")

    if vix_1d_chg >= 5:
        red_flags += 1
        reasons.append(f"VIX 單日暴漲 {vix_1d_chg:+.1f} 點，可能有突發利空事件")
    elif vix_1d_chg >= 3:
        yellow_flags += 1
        reasons.append(f"VIX 單日上升 {vix_1d_chg:+.1f} 點，恐慌情緒升溫中")

    if twii_vs_ma20 < -3:
        red_flags += 1
        reasons.append(f"加權指數跌破月線 {twii_vs_ma20:+.1f}%，中期趨勢轉空")
    elif twii_vs_ma20 < 0:
        yellow_flags += 1
        reasons.append(f"加權指數低於月線 {twii_vs_ma20:+.1f}%，注意趨勢轉弱")

    if twii_5d_ret < -5:
        red_flags += 1
        reasons.append(f"大盤近5日跌 {twii_5d_ret:+.1f}%，短線急殺")
    elif twii_5d_ret < -3:
        yellow_flags += 1
        reasons.append(f"大盤近5日跌 {twii_5d_ret:+.1f}%，下行壓力增加")

    if sox_5d_ret < -7:
        red_flags += 1
        reasons.append(f"費半近5日跌 {sox_5d_ret:+.1f}%，半導體系統性風險")
    elif sox_5d_ret < -4:
        yellow_flags += 1
        reasons.append(f"費半近5日跌 {sox_5d_ret:+.1f}%，半導體轉弱")

    # --- 綜合判定 ---
    if red_flags >= 2:
        signal = "red"
        label = "紅燈 — 禁止進場"
        advice = "系統性風險過高，建議暫停所有新買進操作，持股考慮減碼"
    elif red_flags >= 1:
        signal = "yellow"
        label = "黃燈 — 減碼操作"
        advice = "市場出現風險訊號，建議新進場資金減半，嚴格執行停損"
    elif yellow_flags >= 2:
        signal = "yellow"
        label = "黃燈 — 減碼操作"
        advice = "多項指標轉弱，建議縮減部位、謹慎操作"
    elif yellow_flags >= 1:
        signal = "light_green"
        label = "淺綠燈 — 正常但留意"
        advice = "大盤大致正常，但有輕微警訊，正常操作但注意風控"
    else:
        signal = "green"
        label = "綠燈 — 正常進場"
        advice = "大盤環境穩定，ML 預測可信度較高，正常執行策略"
        if not reasons:
            reasons.append("大盤站穩月線，VIX 平穩，無系統性風險訊號")

    return {
        "signal": signal,
        "label": label,
        "advice": advice,
        "reasons": reasons,
        "indicators": indicators,
    }
