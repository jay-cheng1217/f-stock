# -*- coding: utf-8 -*-
"""MACD 門檻價格比化回測(Method Lock 2026-08-28,PM 核准執行)。

## Method Lock: MACD-price-relative
- 現行算法 A: `macd_hist_delta_3d >= -1.0`,其中 delta = MACDh(12,26,9) - MACDh.shift(3),
  **絕對 MACD 單位**(隨股價縮放)。generate_entry_candidates.py:53、:280。
- 候選算法 B: `macd_hist_delta_3d / close >= -1.0%`(價格比)。等價換算點:-1.0 絕對值
  在 100 元股 = -1% → 主候選 floor_rel=-1.0%;敏感度 -0.5%/-1.5%(有界網格)。
- 唯一變因: _pattern_ok 的 MACD 項;其餘條件(貼MA20 -2%~+8%、>MA60、量比≤2.5、
  RSI 45-80、均量≥500張)兩分支完全相同。模型 overlay 兩分支等同,不納入。
- 快照: 日K資料/*.csv 同一份;前瞻報酬 20 日,除權息比例還原(教條4)。
- 評估(事前寫死): (1)兩分支通過集合 20D 報酬均值/中位/勝率 (2)flip 樣本:
  B新剔除者 vs 共同保留者;B新放行者 vs 共同保留者 (3)分價位帶(<50/50-200/>200)。
- 通過條件(事前寫死): B 集合均報酬 >= A - 0.10pp,且(B剔除者報酬 < 共同保留者,
  或 B放行者報酬 >= 共同保留者 - 0.5pp);皆不成立 → 維持現狀。
- 回測期: 2024-01-02 ~ 2026-07-30。
"""
from __future__ import annotations

import glob
import io
import os
import sys

import numpy as np
import pandas as pd

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
START, END = "2024-01-02", "2026-07-30"
FWD = 20
FLOOR_ABS = -1.0
FLOORS_REL = (-0.005, -0.010, -0.015)   # 有界網格;主候選 -1.0%
MIN_LOTS = 500


def _load_factors() -> dict[str, pd.Series]:
    cal = pd.read_csv(os.path.join(BASE, "ml/data/ex_dividend_calendar.csv"),
                      dtype={"stock_id": str}, encoding="utf-8-sig")
    cal = cal.dropna(subset=["before_price", "after_price"])
    cal = cal[(cal["before_price"] > 0) & (cal["after_price"] > 0)]
    cal["factor"] = cal["after_price"] / cal["before_price"]
    out: dict[str, pd.Series] = {}
    for tk, g in cal.groupby("stock_id"):
        out[tk] = g.set_index("date")["factor"]
    return out


def main() -> int:
    factors = _load_factors()
    rows = []
    files = sorted(glob.glob(os.path.join(BASE, "日K資料", "*.csv")))
    for i, p in enumerate(files):
        tk = os.path.splitext(os.path.basename(p))[0]
        if not (tk.isdigit() and len(tk) == 4):
            continue
        try:
            k = pd.read_csv(p, usecols=["Date", "Close", "Volume", "MA_20", "MA_60",
                                        "RSI_14", "MACDh_12_26_9", "VOL_MA_20"],
                            encoding="utf-8-sig")
        except Exception:
            continue
        if len(k) < 80:
            continue
        k["Date"] = k["Date"].astype(str).str[:10]
        c = pd.to_numeric(k["Close"], errors="coerce")
        vol = pd.to_numeric(k["Volume"], errors="coerce")
        ma20 = pd.to_numeric(k["MA_20"], errors="coerce")
        ma60 = pd.to_numeric(k["MA_60"], errors="coerce")
        rsi = pd.to_numeric(k["RSI_14"], errors="coerce")
        mh = pd.to_numeric(k["MACDh_12_26_9"], errors="coerce")
        vma20 = pd.to_numeric(k["VOL_MA_20"], errors="coerce")

        v20 = c / ma20 - 1
        vr = vol / vma20
        mdelta = mh - mh.shift(3)          # A: 絕對單位(NaN→0 比照 production fillna)
        mdelta_f = mdelta.fillna(0.0)
        mdelta_rel = (mdelta / c).fillna(0.0)  # B: 價格比
        liq = vol.rolling(20).mean() >= MIN_LOTS * 1000

        # 共同四條 + 流動性(兩分支相同)
        common = (
            liq
            & v20.between(-0.02, 0.08)
            & (c > ma60)
            & (vr.isna() | (vr <= 2.5))
            & (rsi.isna() | rsi.between(45, 80))
        )
        passA = mdelta_f >= FLOOR_ABS

        # 除權息比例還原 20 日前瞻報酬
        fwd_close = c.shift(-FWD)
        fac = factors.get(tk)
        adj = pd.Series(1.0, index=k.index)
        if fac is not None and len(fac):
            # 視窗 (t, t+FWD] 內事件的 factor 乘積
            ev_dates = fac.index.to_numpy()
            ev_fac = fac.to_numpy(dtype=float)
            dates = k["Date"].to_numpy()
            cum = np.ones(len(k))
            for d_ev, f_ev in zip(ev_dates, ev_fac):
                # 事件落在 (dates[t], dates[t+FWD]] → 影響樣本 t
                idx_ev = np.searchsorted(dates, d_ev)
                lo_t = max(0, idx_ev - FWD)
                cum[lo_t:idx_ev] *= f_ev
            adj = pd.Series(cum, index=k.index)
        fwd_ret = fwd_close / (c * adj) - 1

        m = common & (k["Date"] >= START) & (k["Date"] <= END) & fwd_ret.notna()
        if not m.any():
            continue
        sub = pd.DataFrame({
            "ticker": tk, "date": k["Date"][m], "close": c[m],
            "passA": passA[m], "mdelta_rel": mdelta_rel[m], "fwd20": fwd_ret[m],
        })
        rows.append(sub)
        if (i + 1) % 500 == 0:
            print(f"  ... {i+1}/{len(files)} files", flush=True)

    df = pd.concat(rows, ignore_index=True)
    print(f"\n樣本: {len(df):,}(通過共同四條+流動性,{df['ticker'].nunique()} 檔,"
          f"{df['date'].nunique()} 交易日)")

    lines = ["# MACD 門檻價格比化回測(2026-08-28)", "",
             f"樣本 {len(df):,} / {df['ticker'].nunique()} 檔 / {df['date'].nunique()} 日;"
             f"回測期 {START}~{END};前瞻 {FWD} 日除息還原報酬", ""]

    def stat(s: pd.Series) -> str:
        return f"n={len(s):>7,}  mean={s.mean()*100:+.3f}%  med={s.median()*100:+.3f}%  win={(s>0).mean()*100:.1f}%"

    for floor_rel in FLOORS_REL:
        passB = df["mdelta_rel"] >= floor_rel
        A, B = df["passA"], passB
        both = df.loc[A & B, "fwd20"]
        a_only = df.loc[A & ~B, "fwd20"]   # B 新剔除
        b_only = df.loc[~A & B, "fwd20"]   # B 新放行
        setA = df.loc[A, "fwd20"]
        setB = df.loc[B, "fwd20"]
        tag = " ← 主候選(等價換算點)" if abs(floor_rel + 0.010) < 1e-9 else ""
        lines += [f"## floor_rel = {floor_rel*100:.1f}%{tag}", "",
                  f"- A(現行絕對-1.0)  {stat(setA)}",
                  f"- B(價格比)        {stat(setB)}",
                  f"- 共同保留          {stat(both)}",
                  f"- B新剔除(A過B擋)  {stat(a_only) if len(a_only) else 'n=0'}",
                  f"- B新放行(A擋B過)  {stat(b_only) if len(b_only) else 'n=0'}", ""]
        # 分價位帶
        for lo, hi, nm in ((0, 50, "<50元"), (50, 200, "50-200元"), (200, 1e9, ">200元")):
            band = df["close"].between(lo, hi, inclusive="left")
            ao = df.loc[A & ~B & band, "fwd20"]; bo = df.loc[~A & B & band, "fwd20"]
            lines.append(f"  - {nm:<9} B剔除 {stat(ao) if len(ao) else 'n=0':<55} B放行 {stat(bo) if len(bo) else 'n=0'}")
        lines.append("")

    # 主候選裁決(事前判準)
    fB = df["mdelta_rel"] >= -0.010
    setA = df.loc[df["passA"], "fwd20"]; setB = df.loc[fB, "fwd20"]
    both = df.loc[df["passA"] & fB, "fwd20"]
    aonly = df.loc[df["passA"] & ~fB, "fwd20"]; bonly = df.loc[~df["passA"] & fB, "fwd20"]
    c1 = setB.mean() >= setA.mean() - 0.001
    c2a = len(aonly) > 0 and aonly.mean() < both.mean()
    c2b = len(bonly) > 0 and bonly.mean() >= both.mean() - 0.005
    verdict = "PASS → 建議採 B(價格比 -1.0%)" if (c1 and (c2a or c2b)) else "FAIL → 維持現狀 A"
    lines += ["## 裁決(事前判準)",
              f"- 條件1 B均值>=A-0.10pp: {'成立' if c1 else '不成立'}"
              f"(A {setA.mean()*100:+.3f}% vs B {setB.mean()*100:+.3f}%)",
              f"- 條件2a B剔除者<共同保留: {'成立' if c2a else '不成立'}"
              f"({aonly.mean()*100:+.3f}% vs {both.mean()*100:+.3f}%)" if len(aonly) else "- 條件2a: 無剔除樣本",
              f"- 條件2b B放行者>=共同保留-0.5pp: {'成立' if c2b else '不成立'}"
              f"({bonly.mean()*100:+.3f}% vs {both.mean()*100:+.3f}%)" if len(bonly) else "- 條件2b: 無放行樣本",
              f"", f"**{verdict}**"]

    report = "\n".join(lines)
    print("\n" + report)
    out = os.path.join(BASE, "ml", "reports", "backtest_macd_price_relative_20260828.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write(report + "\n")
    print(f"\n報告 -> {out}")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.exit(main())
