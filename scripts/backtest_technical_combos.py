# -*- coding: utf-8 -*-
"""BT-technical-combos:常見技術條件「單獨」與「兩兩搭配」的選股效果(探索性,不改任何 gate)。

問題(PM 2026-10-02):哪些技術分析相互搭配才真正有用?

方法:
- 股票池:4 碼個股、20 日均量 >= 500 張的每個交易日。
- 進場 = 訊號日次日收盤;報酬 = 其後 20 / 60 個交易棒收盤(含期間除權息加回),不設停損、不計成本。
- **超額報酬 = 個股報酬 − 同一訊號日全股票池平均**。這樣扣掉「那段時間大盤好不好」,只看選股本身。
- 穩定性:分 2020-2022、2023-2026 兩段各自計算;兩段同號才算穩定。
- 樣本每日重疊(同一檔連續多日成立),n 很大但不獨立,**不做顯著性檢定**,只看幅度與兩段是否一致。

用法:python -X utf8 scripts/backtest_technical_combos.py
"""
from __future__ import annotations

import io
import itertools
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
BASE = Path(__file__).resolve().parents[1]
DAILY = BASE / "日K資料"
START = "2020-01-01"

cal = pd.read_csv(BASE / "ml" / "data" / "ex_dividend_calendar.csv", dtype={"stock_id": str}, encoding="utf-8-sig")
cal["dv"] = pd.to_numeric(cal["stock_and_cache_dividend"], errors="coerce").fillna(0.0)
cal["date"] = cal["date"].astype(str).str[:10]
CAL = {tk: dict(zip(g["date"], g["dv"])) for tk, g in cal.groupby("stock_id")}

COLS = ["Date", "High", "Low", "Close", "Volume", "Foreign_BuySell", "Trust_BuySell", "Margin_Balance",
        "MA_5", "MA_20", "MA_60", "RSI_14", "BBL_20_2.0", "BBU_20_2.0", "MACDh_12_26_9", "K", "D", "VOL_MA_20"]

CONDS = {
    "站上季線": None, "季線上揚": None, "均線多頭排列": None, "離季線>=5%": None,
    "貼近月線±5%": None, "創60日新高": None, "20日漲>=15%": None, "自10日高回落>=8%": None,
    "量縮<0.7倍": None, "爆量>=2倍且收紅": None, "RSI<30超賣": None, "RSI>70過熱": None,
    "MACD柱翻正": None, "KD低檔黃金交叉": None, "布林通道壓縮": None, "跌破布林下軌": None,
    "外資5日買超": None, "投信5日買超": None, "融資5日減少": None,
}
NAMES = list(CONDS)
frames = []
for f in sorted(DAILY.glob("*.csv")):
    tk = f.stem
    if not (tk.isdigit() and len(tk) == 4 and tk[0] != "0"):
        continue
    try:
        k = pd.read_csv(f, usecols=COLS, encoding="utf-8-sig")
    except Exception:
        continue
    if len(k) < 150:
        continue
    k["Date"] = k["Date"].astype(str).str[:10]
    k = k.sort_values("Date").reset_index(drop=True)
    num = {c: pd.to_numeric(k[c], errors="coerce") for c in COLS if c != "Date"}
    c, vol, vma = num["Close"], num["Volume"], num["VOL_MA_20"]
    ma5, ma20, ma60 = num["MA_5"], num["MA_20"], num["MA_60"]
    div = k["Date"].map(CAL.get(tk, {})).fillna(0.0)
    cumdiv = div.cumsum()
    out = pd.DataFrame({"date": k["Date"]})
    for h in (20, 60):
        entry = c.shift(-1)
        out[f"r{h}"] = (c.shift(-1 - h) + (cumdiv.shift(-1 - h) - cumdiv.shift(-1))) / entry - 1
    hmacd = num["MACDh_12_26_9"]
    kd_k, kd_d = num["K"], num["D"]
    bbw = (num["BBU_20_2.0"] - num["BBL_20_2.0"]) / ma20
    ret1 = c / c.shift(1) - 1
    feats = {
        "站上季線": c > ma60,
        "季線上揚": ma60 > ma60.shift(20),
        "均線多頭排列": (ma5 > ma20) & (ma20 > ma60),
        "離季線>=5%": c / ma60 - 1 >= 0.05,
        "貼近月線±5%": (c / ma20 - 1).abs() <= 0.05,
        "創60日新高": c >= c.rolling(60).max(),
        "20日漲>=15%": c / c.shift(20) - 1 >= 0.15,
        "自10日高回落>=8%": 1 - c / c.rolling(10).max() >= 0.08,
        "量縮<0.7倍": vol / vma < 0.7,
        "爆量>=2倍且收紅": (vol / vma >= 2.0) & (ret1 > 0),
        "RSI<30超賣": num["RSI_14"] < 30,
        "RSI>70過熱": num["RSI_14"] > 70,
        "MACD柱翻正": (hmacd > 0) & (hmacd.shift(1) <= 0),
        "KD低檔黃金交叉": (kd_k > kd_d) & (kd_k.shift(1) <= kd_d.shift(1)) & (kd_k < 30),
        "布林通道壓縮": bbw <= bbw.rolling(120).quantile(0.2),
        "跌破布林下軌": c < num["BBL_20_2.0"],
        "外資5日買超": num["Foreign_BuySell"].fillna(0).rolling(5).sum() > 0,
        "投信5日買超": num["Trust_BuySell"].fillna(0).rolling(5).sum() > 0,
        "融資5日減少": num["Margin_Balance"] < num["Margin_Balance"].shift(5),
    }
    for n in NAMES:
        out[n] = feats[n].fillna(False).to_numpy(bool)
    ok = (k["Date"] >= START) & (vma >= 500_000) & out["r60"].notna() & ma60.notna()
    frames.append(out[ok.to_numpy()])

df = pd.concat(frames, ignore_index=True)
for h in (20, 60):
    df[f"x{h}"] = df[f"r{h}"] - df.groupby("date")[f"r{h}"].transform("mean")
df["half"] = np.where(df["date"] < "2023-01-01", "H1", "H2")
N = len(df)
print(f"=== BT-technical-combos|樣本 {N:,} 個股日,{df.date.min()} ~ {df.date.max()} ===")
print(f"全股票池基準:20日平均 {df.r20.mean()*100:+.2f}%、60日平均 {df.r60.mean()*100:+.2f}%、60日上漲比例 {(df.r60>0).mean()*100:.1f}%\n")


def stat(mask):
    v = df[mask]
    h1, h2 = v[v.half == "H1"]["x60"], v[v.half == "H2"]["x60"]
    return {"n": len(v), "share": len(v) / N * 100, "x20": v["x20"].mean() * 100, "x60": v["x60"].mean() * 100,
            "win": (v["x60"] > 0).mean() * 100, "tail": (v["r60"] < -0.20).mean() * 100,
            "big": (v["r60"] > 0.30).mean() * 100,
            "h1": h1.mean() * 100 if len(h1) else np.nan, "h2": h2.mean() * 100 if len(h2) else np.nan}


def fmt(name, s):
    stable = "穩定" if (s["h1"] > 0) == (s["h2"] > 0) else "不穩"
    return (f"{name:32} 占比{s['share']:5.1f}% | 超額20日{s['x20']:+5.2f}% 60日{s['x60']:+5.2f}% | 贏過平均{s['win']:5.1f}% | "
            f"大虧{s['tail']:4.1f}% 大賺{s['big']:4.1f}% | 前段{s['h1']:+5.2f}% 後段{s['h2']:+5.2f}% {stable}")


base_tail, base_big = (df.r60 < -0.20).mean() * 100, (df.r60 > 0.30).mean() * 100
print(f"(全股票池:大虧<-20% 占 {base_tail:.1f}%、大賺>+30% 占 {base_big:.1f}%;「贏過平均」基準約 {(df.x60>0).mean()*100:.1f}%)\n")
print("--- 單一條件(依 60 日超額報酬排序)---")
singles = {n: stat(df[n]) for n in NAMES}
for n, s in sorted(singles.items(), key=lambda kv: -kv[1]["x60"]):
    print(fmt(n, s))

pairs = []
for a, b in itertools.combinations(NAMES, 2):
    m = df[a] & df[b]
    if m.sum() < 20_000:
        continue
    s = stat(m)
    s["lift"] = s["x60"] - max(singles[a]["x60"], singles[b]["x60"])   # 搭配後比兩者中較好的那個多多少
    pairs.append((f"{a} + {b}", s))
print(f"\n--- 兩兩搭配:前 15 名(n>=20,000,且前後兩段都為正)---")
good = [p for p in pairs if p[1]["h1"] > 0 and p[1]["h2"] > 0]
for name, s in sorted(good, key=lambda kv: -kv[1]["x60"])[:15]:
    print(fmt(name, s) + f" | 比單用多{s['lift']:+.2f}pp")
print(f"\n--- 兩兩搭配:最差 8 名 ---")
for name, s in sorted(pairs, key=lambda kv: kv[1]["x60"])[:8]:
    print(fmt(name, s))
print(f"\n--- 搭配後「加分最多」的 8 組(lift = 搭配超額 − 兩者中較好者,且兩段皆正)---")
for name, s in sorted(good, key=lambda kv: -kv[1]["lift"])[:8]:
    print(fmt(name, s) + f" | 比單用多{s['lift']:+.2f}pp")

print("\n--- 逐年 60 日超額報酬(幾個代表條件)---")
df["year"] = df["date"].str[:4]
KEYS = [("RSI>70過熱", df["RSI>70過熱"]), ("創60日新高", df["創60日新高"]), ("季線上揚", df["季線上揚"]),
        ("RSI>70+投信買超", df["RSI>70過熱"] & df["投信5日買超"]), ("貼近月線±5%", df["貼近月線±5%"]),
        ("RSI<30超賣", df["RSI<30超賣"]), ("KD低檔黃金交叉", df["KD低檔黃金交叉"])]
print("年度   " + " | ".join(f"{n:>14}" for n, _ in KEYS))
for y in sorted(df["year"].unique()):
    print(f"{y}   " + " | ".join(f"{df[m & (df.year == y)]['x60'].mean()*100:>+13.2f}%" for _, m in KEYS))
