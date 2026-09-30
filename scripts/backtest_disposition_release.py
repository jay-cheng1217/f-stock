# -*- coding: utf-8 -*-
"""BT-disposition-release: 處置股出關行情回測。

假說(rere 擎亞 0708 案例): 出關(解除處置)=流動性解禁,若同時撞上營收公布窗
(月初 1-10 日)且營收動能強,雙引信釋放。

事件: disposition_periods.period_end 後首個交易日 = 出關日(entry, 以收盤進)。
條件組:
  ALL          出關全事件
  REV_WINDOW   出關日落在每月 1-10 日(下旬營收公布 deadline 前窗)
  REV_MOM      進場時已知的最近一期月營收 YoY > 30%(無前視:只用 entry 前已公布月份)
  DUAL         REV_WINDOW + REV_MOM(rere 雙引信)
持有 5/10/20 日,報酬與同窗 TWII 超額。附:處置期間漲跌 vs 出關後表現的相關性。
注意: 處置股全是暴漲暴量股,選擇偏誤大——結論只用於「出關事件內部的條件比較」。
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path(__file__).resolve().parents[1]
DAILY = BASE / "日K資料"
REV = BASE / "月營收"
OUT = BASE / "ml" / "reports"

HOLDS = [5, 10, 20]


def load_k(tk: str) -> pd.DataFrame | None:
    f = DAILY / f"{tk}.csv"
    if not f.exists():
        return None
    try:
        k = pd.read_csv(f, usecols=["Date", "Close", "Volume"])
    except Exception:
        return None
    k["Date"] = pd.to_datetime(k["Date"].astype(str).str[:10], errors="coerce")
    k = k.dropna(subset=["Date"]).sort_values("Date").reset_index(drop=True)
    return k if len(k) > 60 else None


def load_twii() -> pd.DataFrame:
    f = BASE / "大盤指數" / "index_TWII.csv"
    k = pd.read_csv(f)
    k["Date"] = pd.to_datetime(k["Date"].astype(str).str[:10], errors="coerce")
    # 大盤指數 CSV 欄序 Date,Close,High,Low,Open,Volume
    return k[["Date", "Close"]].dropna().sort_values("Date").reset_index(drop=True)


def load_rev_yoy(tk: str) -> dict[str, float]:
    f = REV / f"revenue_{tk}.csv"
    if not f.exists():
        return {}
    try:
        r = pd.read_csv(f, usecols=["Date", "YoY_pct_change"])
    except Exception:
        return {}
    return dict(zip(r["Date"].astype(str), pd.to_numeric(r["YoY_pct_change"], errors="coerce")))


def known_yoy(rev: dict[str, float], entry: pd.Timestamp) -> float | None:
    """entry 當下已公布的最近一期月營收 YoY(月營收於次月10日前公布)。"""
    # entry 日 dom>=11 → 上月已公布;dom<=10 → 只能保證上上月
    if entry.day >= 11:
        known = entry - pd.DateOffset(months=1)
    else:
        known = entry - pd.DateOffset(months=2)
    v = rev.get(f"{known.year}-{known.month:02d}")
    return None if v is None or pd.isna(v) else float(v)


def main() -> None:
    disp = pd.read_csv(BASE / "ml" / "data" / "disposition_periods.csv", dtype={"stock_id": str})
    disp["period_start"] = pd.to_datetime(disp["period_start"])
    disp["period_end"] = pd.to_datetime(disp["period_end"])
    twii = load_twii()
    tdates = twii["Date"].values
    tclose = twii["Close"].values

    def twii_ret(d0: pd.Timestamp, n: int) -> float | None:
        i = np.searchsorted(tdates, np.datetime64(d0))
        if i >= len(tdates) or tdates[i] != np.datetime64(d0):
            return None
        j = i + n
        if j >= len(tdates):
            return None
        return float(tclose[j] / tclose[i] - 1)

    rows = []
    cache_k: dict[str, pd.DataFrame | None] = {}
    cache_r: dict[str, dict] = {}
    for _, ev in disp.iterrows():
        tk = ev["stock_id"].strip()
        if tk not in cache_k:
            cache_k[tk] = load_k(tk)
        k = cache_k[tk]
        if k is None:
            continue
        after = k[k["Date"] > ev["period_end"]]
        if after.empty:
            continue
        e = after.index[0]
        entry_date = k.at[e, "Date"]
        if (entry_date - ev["period_end"]).days > 7:
            continue  # 出關後長期停牌等異常
        entry_px = float(k.at[e, "Close"])
        if entry_px <= 0:
            continue
        # 處置期間報酬
        pre = k[(k["Date"] >= ev["period_start"]) & (k["Date"] <= ev["period_end"])]
        disp_ret = float(pre["Close"].iloc[-1] / pre["Close"].iloc[0] - 1) if len(pre) >= 2 else np.nan
        if tk not in cache_r:
            cache_r[tk] = load_rev_yoy(tk)
        yoy = known_yoy(cache_r[tk], entry_date)
        row = {
            "ticker": tk, "entry": str(entry_date.date()), "entry_dom": int(entry_date.day),
            "disp_days": int((ev["period_end"] - ev["period_start"]).days),
            "disp_ret": disp_ret, "known_yoy": yoy, "year": int(entry_date.year),
        }
        ok = False
        for n in HOLDS:
            j = e + n
            if j >= len(k):
                row[f"ret_{n}"] = np.nan
                continue
            r = float(k.at[j, "Close"] / entry_px - 1)
            m = twii_ret(entry_date, n)
            row[f"ret_{n}"] = r
            row[f"exc_{n}"] = r - m if m is not None else np.nan
            ok = True
        if ok:
            rows.append(row)

    df = pd.DataFrame(rows)
    conds = {
        "ALL": pd.Series(True, index=df.index),
        "REV_WINDOW": df["entry_dom"].between(1, 10),
        "REV_MOM": df["known_yoy"] > 30,
        "DUAL": df["entry_dom"].between(1, 10) & (df["known_yoy"] > 30),
    }
    res: dict = {"generated": datetime.now().strftime("%Y-%m-%d %H:%M"), "n_events_total": len(df), "conds": {}}
    lines = ["# BT-disposition-release 處置出關行情回測", "",
             f"事件數 {len(df)}(處置段 {len(disp)},缺日K/停牌剔除)。進場=出關後首日收盤;超額=對TWII同窗。",
             "**選擇偏誤警告:處置股皆為暴漲暴量股,本回測只回答『出關事件內部,哪個條件更好』,不回答『出關股 vs 一般股』。**", ""]
    for name, mask in conds.items():
        sub = df[mask]
        c: dict = {"n": len(sub)}
        lines.append(f"## {name} (N={len(sub)})")
        for n in HOLDS:
            r = sub[f"ret_{n}"].dropna()
            x = sub[f"exc_{n}"].dropna()
            if len(r) < 5:
                continue
            c[f"h{n}"] = {"n": len(r), "win": round(float((r > 0).mean()) * 100, 1),
                          "avg": round(float(r.mean()) * 100, 2), "med": round(float(r.median()) * 100, 2),
                          "exc_avg": round(float(x.mean()) * 100, 2), "exc_med": round(float(x.median()) * 100, 2)}
            lines.append(f"- 持有{n}日: 勝率 {c[f'h{n}']['win']}% | 平均 {c[f'h{n}']['avg']}% (中位 {c[f'h{n}']['med']}%) | 超額平均 {c[f'h{n}']['exc_avg']}% (中位 {c[f'h{n}']['exc_med']}%)")
        res["conds"][name] = c
        lines.append("")
    # 處置期間表現 vs 出關後(慣性 or 均值回歸?)
    v = df.dropna(subset=["disp_ret", "ret_10"])
    if len(v) > 30:
        strong = v[v["disp_ret"] > 0]
        weak = v[v["disp_ret"] <= 0]
        lines += ["## 處置期間漲跌 → 出關後10日",
                  f"- 處置期間收漲 (N={len(strong)}): 出關後10日平均 {strong['ret_10'].mean()*100:.2f}% 勝率 {(strong['ret_10']>0).mean()*100:.1f}%",
                  f"- 處置期間收跌 (N={len(weak)}): 出關後10日平均 {weak['ret_10'].mean()*100:.2f}% 勝率 {(weak['ret_10']>0).mean()*100:.1f}%", ""]
        res["disp_momentum"] = {"strong_n": len(strong), "strong_avg10": round(float(strong["ret_10"].mean()) * 100, 2),
                                "weak_n": len(weak), "weak_avg10": round(float(weak["ret_10"].mean()) * 100, 2)}
    # 年度分佈(regime)
    lines.append("## 年度分佈(ALL,10日超額)")
    for y, g in df.groupby("year"):
        x = g["exc_10"].dropna()
        if len(x) >= 10:
            lines.append(f"- {y}: N={len(x)} 超額平均 {x.mean()*100:.2f}% 勝率(絕對) {(g['ret_10'].dropna()>0).mean()*100:.1f}%")
    stamp = datetime.now().strftime("%Y%m%d")
    (OUT / f"backtest_disposition_release_{stamp}.md").write_text("\n".join(lines), encoding="utf-8")
    (OUT / f"backtest_disposition_release_{stamp}.json").write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    df.to_csv(OUT / f"backtest_disposition_release_events_{stamp}.csv", index=False, encoding="utf-8-sig")
    print("\n".join(lines))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
