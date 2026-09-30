# -*- coding: utf-8 -*-
"""盤前當沖研究篩選器（research/watchlist only，非買賣訊號）。

把 rere 自陳的當沖選股心法拆成客觀條件,產生「值得盤前做功課的觀察池」:
  1. 流動性 — 當沖第一鐵律:量能夠大才進得去出得來(她:周轉率高)
  2. 跌深 — 近 N 日高點回落幅度(她:跌深的股票,反彈空間)
  3. 量能放大 — 今日量 / 20 日均量(有人氣才有振幅)
  4. 波動 — ATR%(當沖靠日內振幅,太牛皮沒得做)
硬排除:處置股(分盤交易、流動性鎖死,當沖必死)、流動性不足。

**這只是縮小盤前研究範圍的工具,不是買賣建議。**
排出的每一檔仍需自行做功課(籌碼、消息、型態、五檔),且當沖屬高風險,
rere 本人強烈勸退新手。日內進出決策與風險由使用者自負。

用法: python scripts/daytrade_prep_screener.py [--top 25]
"""
from __future__ import annotations

import argparse
import glob
import io
import os
import sys

import numpy as np
import pandas as pd

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DAILY = os.path.join(BASE, "日K資料")

# 門檻(盤前研究用,非最佳化參數;保守取值)
MIN_LOTS = 3000            # 近 5 日均量 ≥ 3000 張(當沖需要的最低流動性)
MIN_VR = 1.2              # 今日量 / 20 日均量 ≥ 1.2(有量能放大)
MIN_WASH = 0.08          # 近 10 日高點回落 ≥ 8%(跌深)
MIN_ATR_PCT = 2.5        # ATR% ≥ 2.5%(有日內振幅可做)


def _load_names_sectors():
    try:
        sm = pd.read_csv(os.path.join(BASE, "ml/data/sector_mapping.csv"), dtype=str, encoding="utf-8-sig")
        return (dict(zip(sm["Ticker"].str.strip(), sm["Name"])),
                dict(zip(sm["Ticker"].str.strip(), sm["Sector"])))
    except Exception:
        return {}, {}


def _load_disposition() -> set[str]:
    p = os.path.join(BASE, "disposition_active.csv")
    try:
        d = pd.read_csv(p, dtype=str)
        return set(d.iloc[:, 0].astype(str).str.strip())
    except Exception:
        return set()


def screen() -> pd.DataFrame:
    """回傳符合條件的當沖研究觀察池 DataFrame(依 score 降序)。dashboard 共用。"""
    names, sectors = _load_names_sectors()
    disp = _load_disposition()
    rows = []
    for p in glob.glob(os.path.join(DAILY, "*.csv")):
        tk = os.path.splitext(os.path.basename(p))[0]
        if not tk.isdigit() or len(tk) != 4 or tk in disp:
            continue
        try:
            k = pd.read_csv(p, usecols=["Date", "Close", "High", "Low", "Volume",
                                        "VOL_MA_20", "ATR_14"], encoding="utf-8-sig")
        except Exception:
            continue
        if len(k) < 25:
            continue
        c = pd.to_numeric(k["Close"], errors="coerce")
        vol = pd.to_numeric(k["Volume"], errors="coerce")
        vma20 = pd.to_numeric(k["VOL_MA_20"], errors="coerce")
        atr = pd.to_numeric(k["ATR_14"], errors="coerce")
        last, lastvol = c.iloc[-1], vol.iloc[-1]
        if not (last > 0 and lastvol > 0):
            continue
        avg5_lots = vol.tail(5).mean() / 1000.0
        vr = lastvol / vma20.iloc[-1] if vma20.iloc[-1] > 0 else np.nan
        hi10 = c.tail(10).max()
        wash = 1 - last / hi10
        atr_pct = atr.iloc[-1] / last * 100 if last else np.nan
        if not (avg5_lots >= MIN_LOTS and vr >= MIN_VR
                and wash >= MIN_WASH and atr_pct >= MIN_ATR_PCT):
            continue
        score = (min(vr, 3) / 3) * 0.4 + (min(atr_pct, 8) / 8) * 0.3 + (min(wash, 0.3) / 0.3) * 0.3
        rows.append({
            "ticker": tk, "name": names.get(tk, ""), "sector": sectors.get(tk, ""),
            "close": round(float(last), 2), "avg5_lots": int(avg5_lots),
            "vr": round(float(vr), 2), "wash_pct": round(float(wash) * 100, 1),
            "atr_pct": round(float(atr_pct), 1), "score": round(float(score), 3),
        })
    return pd.DataFrame(rows).sort_values("score", ascending=False) if rows else pd.DataFrame()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=25)
    args = ap.parse_args()

    names, sectors = _load_names_sectors()
    disp = _load_disposition()
    rows = []
    for p in glob.glob(os.path.join(DAILY, "*.csv")):
        tk = os.path.splitext(os.path.basename(p))[0]
        if not tk.isdigit() or len(tk) != 4 or tk in disp:
            continue
        try:
            k = pd.read_csv(p, usecols=["Date", "Close", "High", "Low", "Volume",
                                        "VOL_MA_20", "ATR_14"], encoding="utf-8-sig")
        except Exception:
            continue
        if len(k) < 25:
            continue
        c = pd.to_numeric(k["Close"], errors="coerce")
        vol = pd.to_numeric(k["Volume"], errors="coerce")
        vma20 = pd.to_numeric(k["VOL_MA_20"], errors="coerce")
        atr = pd.to_numeric(k["ATR_14"], errors="coerce")
        last, lastvol = c.iloc[-1], vol.iloc[-1]
        if not (last > 0 and lastvol > 0):
            continue

        avg5_lots = vol.tail(5).mean() / 1000.0
        vr = lastvol / vma20.iloc[-1] if vma20.iloc[-1] > 0 else np.nan
        hi10 = c.tail(10).max()
        wash = 1 - last / hi10
        atr_pct = atr.iloc[-1] / last * 100 if last else np.nan

        if not (avg5_lots >= MIN_LOTS and vr >= MIN_VR
                and wash >= MIN_WASH and atr_pct >= MIN_ATR_PCT):
            continue

        # 綜合排序分數(等權標準化,純研究用):量能*振幅*跌深
        score = (min(vr, 3) / 3) * 0.4 + (min(atr_pct, 8) / 8) * 0.3 + (min(wash, 0.3) / 0.3) * 0.3
        rows.append({
            "ticker": tk, "name": names.get(tk, ""), "sector": sectors.get(tk, ""),
            "close": round(float(last), 2), "avg5_lots": int(avg5_lots),
            "vr": round(float(vr), 2), "wash_pct": round(float(wash) * 100, 1),
            "atr_pct": round(float(atr_pct), 1), "score": round(float(score), 3),
        })

    df = pd.DataFrame(rows).sort_values("score", ascending=False)
    asof = pd.read_csv(os.path.join(DAILY, "2330.csv"), usecols=["Date"]).iloc[-1, 0][:10]
    print(f"盤前當沖研究觀察池（asof {asof}）— 研究用途,非買賣訊號")
    print(f"條件: 均量≥{MIN_LOTS}張 且 量比≥{MIN_VR} 且 跌深≥{MIN_WASH*100:.0f}% 且 ATR≥{MIN_ATR_PCT}%；已排除處置股")
    print(f"符合 {len(df)} 檔,列前 {args.top}:\n")
    print(f"{'股票':<14}{'收盤':>8}{'均量張':>8}{'量比':>6}{'跌深%':>7}{'ATR%':>6}  {'產業'}")
    for _, r in df.head(args.top).iterrows():
        print(f"{r['ticker']+' '+str(r['name']):<14}{r['close']:>8.1f}{r['avg5_lots']:>8,}"
              f"{r['vr']:>6.2f}{r['wash_pct']:>7.1f}{r['atr_pct']:>6.1f}  {r['sector']}")
    out = os.path.join(BASE, "logs", f"daytrade_prep_{asof.replace('-','')}.csv")
    df.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"\n完整清單 -> {out}")
    print("\n⚠️ 這是縮小研究範圍的工具,不是買賣建議。每檔仍需自行做功課；當沖高風險,rere 本人勸退新手。")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.exit(main())
