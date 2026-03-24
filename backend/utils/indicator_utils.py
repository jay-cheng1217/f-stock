"""技術指標訊號判斷工具."""

import math


def _safe(val) -> float | None:
    """將可能的 NaN/None 轉為 None."""
    if val is None:
        return None
    try:
        f = float(val)
        return None if math.isnan(f) else f
    except (ValueError, TypeError):
        return None


def score_ma_alignment(close, ma5, ma20, ma60) -> tuple[float, str]:
    """均線排列評分."""
    c, m5, m20, m60 = _safe(close), _safe(ma5), _safe(ma20), _safe(ma60)
    if None in (c, m5, m20, m60):
        return 50, "均線資料不足"

    if c > m5 > m20 > m60:
        return 100, "完美多頭排列(價>MA5>MA20>MA60)"
    elif c > m5 > m20:
        return 80, "短中期多頭排列"
    elif c > m20:
        return 60, "站上月線"
    elif m5 < m20 < m60:
        return 0, "空頭排列"
    elif c < m20:
        return 20, "跌破月線"
    else:
        return 50, "均線糾結整理"


def score_rsi(rsi14) -> tuple[float, str]:
    """RSI 評分."""
    v = _safe(rsi14)
    if v is None:
        return 50, "RSI資料不足"
    if v < 30:
        return 80, f"RSI={v:.0f} 超賣區(反彈機會)"
    elif v < 50:
        return 60, f"RSI={v:.0f} 偏弱"
    elif v < 70:
        return 60, f"RSI={v:.0f} 正常區間"
    else:
        return 30, f"RSI={v:.0f} 超買區(回檔風險)"


def score_macd(macd_hist, prev_hist=None) -> tuple[float, str]:
    """MACD 柱體評分."""
    h = _safe(macd_hist)
    ph = _safe(prev_hist)
    if h is None:
        return 50, "MACD資料不足"

    if h > 0:
        if ph is not None and ph <= 0:
            return 90, "MACD柱體翻正(黃金交叉)"
        elif ph is not None and h > ph:
            return 80, "MACD柱體擴大"
        else:
            return 70, "MACD柱體為正"
    else:
        if ph is not None and ph >= 0:
            return 10, "MACD柱體翻負(死亡交叉)"
        elif ph is not None and h < ph:
            return 20, "MACD柱體縮小"
        else:
            return 30, "MACD柱體為負"


def score_kd(k_val, d_val, prev_k=None, prev_d=None) -> tuple[float, str]:
    """KD 指標評分."""
    k, d = _safe(k_val), _safe(d_val)
    pk, pd_ = _safe(prev_k), _safe(prev_d)
    if None in (k, d):
        return 50, "KD資料不足"

    if k < 20 and d < 20:
        if pk is not None and pd_ is not None and pk < pd_ and k > d:
            return 100, f"K={k:.0f}/D={d:.0f} 低檔黃金交叉"
        return 80, f"K={k:.0f}/D={d:.0f} 超賣區"
    elif k > 80 and d > 80:
        if pk is not None and pd_ is not None and pk > pd_ and k < d:
            return 0, f"K={k:.0f}/D={d:.0f} 高檔死亡交叉"
        return 20, f"K={k:.0f}/D={d:.0f} 超買區"
    elif k > d:
        return 65, f"K={k:.0f}/D={d:.0f} K在D之上"
    else:
        return 40, f"K={k:.0f}/D={d:.0f} K在D之下"


def score_bollinger(close, bb_upper, bb_lower, bb_middle) -> tuple[float, str]:
    """布林通道評分."""
    c, u, l, m = _safe(close), _safe(bb_upper), _safe(bb_lower), _safe(bb_middle)
    if None in (c, u, l, m) or u == l:
        return 50, "布林通道資料不足"

    pct = (c - l) / (u - l)  # 0=下軌, 1=上軌
    if pct < 0.1:
        return 85, "觸及布林下軌(超跌反彈機會)"
    elif pct < 0.3:
        return 75, "靠近布林下軌"
    elif pct > 0.9:
        return 25, "觸及布林上軌(過熱風險)"
    elif pct > 0.7:
        return 35, "靠近布林上軌"
    else:
        return 55, "布林通道中軌附近"


def score_volume(vol, vol_ma5, vol_ma20, close_chg_pct=None) -> tuple[float, str]:
    """量能評分."""
    v, m5, m20 = _safe(vol), _safe(vol_ma5), _safe(vol_ma20)
    chg = _safe(close_chg_pct)

    if None in (v, m5, m20) or m20 == 0:
        return 50, "量能資料不足"

    vol_ratio = m5 / m20

    if vol_ratio > 1.5 and chg is not None and chg > 0:
        return 90, f"量能大增{vol_ratio:.1f}倍且價漲(量價齊揚)"
    elif vol_ratio > 1.2 and chg is not None and chg > 0:
        return 75, "量增價漲"
    elif vol_ratio < 0.6:
        return 40, "量能萎縮"
    elif vol_ratio > 1.5 and chg is not None and chg < 0:
        return 25, "爆量下跌(警戒)"
    else:
        return 55, "量能正常"
