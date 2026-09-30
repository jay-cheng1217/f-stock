# -*- coding: utf-8 -*-
"""BT-whale-radar-streak(research-only,2026-09-05 開票)。

問題:TDCC 千張大戶週增雷達(大戶週增 ≥3pp 且散戶週減;scripts/tdcc_whale_weekly_scan.py)
的「連續上榜」是否比單週命中有更好的 20 / 60 日前瞻報酬?
背景:2026-06~09 前瞻 1 週僅 47% 勝率、中位 −0.55%(擲銅板);專案已驗證 edge 只在
20–60 日持有(STRATEGY_overview),且連續上榜者在 1 週尺度兩邊甩(鈺創×5 僅 1 次大漲)。

口徑:
- 雷達:與 tdcc_whale_weekly_scan.py 同口徑(級距 15 = 千張大戶、級距 1–9 = 散戶<100張),
  對 集保分散/tdcc_YYYYMMDD.csv 全部週檔逐對重算,不受排程漏跑影響。
- streak:同一檔在連續週檔上榜的次數;桶 = 1(單週/首週)、2、3+。
- 進場:雷達資料日之後第一個交易日「開盤」(TDCC 週五夜間公布 → 週一才可執行);
  f20 / f60 = 第 20 / 60 根收盤 ÷ 進場 − 1;無停損;流動性 VOL_MA_20 ≥ 500,000(訊號日)。
- 基準:同一訊號日、同流動性條件的全市場等權前瞻報酬;excess = 事件 − 同日基準
  (消除 regime / 市況影響,否則多頭年所有桶都會「漲」)。
- 防呆:進場日距雷達日 >7 天(停牌/暫停交易)排除;價格為混合序列(STRATEGY_overview §四)。
- 註:連續週事件的持有期重疊,各桶 n 為事件數非獨立樣本;結論以 excess 與逐年一致性為準。

輸出:ml/reports/bt_whale_radar_streak.csv(逐筆)+ 終端統計。不寫任何生產檔。
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path(__file__).resolve().parents[1]
TDCC_DIR = BASE / "集保分散"
DAILY = BASE / "日K資料"
OUT = BASE / "ml" / "reports" / "bt_whale_radar_streak.csv"

WHALE_MIN_DELTA = 3.0        # 大戶週增 ≥ 3pp(雷達預設 --min-delta)
LIQ_MIN_VOL_MA20 = 500_000
MAX_ENTRY_GAP_DAYS = 7
H20, H60 = 20, 60


def load_week(path: Path) -> pd.DataFrame | None:
    """週檔 → ticker / whale1000 / retail100;欄位以名稱辨識,失敗退回位置(舊檔 schema 容錯)。"""
    try:
        df = pd.read_csv(path, dtype=str)
    except Exception:
        return None
    cols = list(df.columns)
    def pick(*keys):
        for c in cols:
            if any(k in str(c) for k in keys):
                return c
        return None
    c_tk, c_lv, c_pct = pick("代號", "ticker"), pick("分級", "level"), pick("比例", "pct")
    if not (c_tk and c_lv and c_pct):
        if len(cols) < 6:
            return None
        c_tk, c_lv, c_pct = cols[1], cols[2], cols[5]
    d = pd.DataFrame({
        "ticker": df[c_tk].astype(str).str.strip(),
        "level": pd.to_numeric(df[c_lv], errors="coerce"),
        "pct": pd.to_numeric(df[c_pct], errors="coerce"),
    })
    d = d[d["ticker"].str.fullmatch(r"\d{4}")]
    if d.empty:
        return None
    p = d.pivot_table(index="ticker", columns="level", values="pct", aggfunc="sum").fillna(0)
    return pd.DataFrame({
        "whale1000": (p[15] if 15 in p.columns else 0.0),
        "retail100": p[[c for c in p.columns if 1 <= c <= 9]].sum(axis=1),
    }).round(2).reset_index()


def _num(s: pd.Series) -> np.ndarray:
    return pd.to_numeric(s, errors="coerce").values.astype(float)


def load_panel() -> dict:
    panel = {}
    for p in sorted(DAILY.glob("*.csv")):
        if not (p.stem.isdigit() and len(p.stem) == 4):
            continue
        try:
            k = pd.read_csv(p, usecols=["Date", "Open", "Close", "VOL_MA_20"])
        except Exception:
            continue
        if len(k) < 80:
            continue
        k = k.sort_values("Date").reset_index(drop=True)
        panel[p.stem] = {
            "d8": k["Date"].astype(str).str[:10].str.replace("-", "").values,
            "o": _num(k["Open"]), "c": _num(k["Close"]), "vma": _num(k["VOL_MA_20"]),
        }
    return panel


def _days(a: str, b: str) -> int:
    return (datetime.strptime(a, "%Y%m%d") - datetime.strptime(b, "%Y%m%d")).days


def forward(k: dict, D: str):
    """回傳 (f20, f60, liquid, entry_d8) 或 None(停牌/資料不足)。"""
    d8, o, c, vma = k["d8"], k["o"], k["c"], k["vma"]
    pos = int(np.searchsorted(d8, D, side="right"))          # 第一個 > D 的交易日
    if pos >= len(d8) or pos < 1 or _days(d8[pos], D) > MAX_ENTRY_GAP_DAYS:
        return None
    entry = o[pos]
    if not (np.isfinite(entry) and entry > 0):
        return None
    f20 = c[pos + H20] / entry - 1 if pos + H20 < len(c) else np.nan
    f60 = c[pos + H60] / entry - 1 if pos + H60 < len(c) else np.nan
    liquid = bool(np.isfinite(vma[pos - 1]) and vma[pos - 1] >= LIQ_MIN_VOL_MA20)
    return f20, f60, liquid, d8[pos]


def main() -> int:
    files = sorted(TDCC_DIR.glob("tdcc_2???????.csv"))
    dates = [f.stem.split("_")[1] for f in files]
    print(f"TDCC 週檔 {len(files)} 個:{dates[0]} → {dates[-1]}")
    weeks = []
    prev = None
    bad = 0
    for f, D in zip(files, dates):
        cur = load_week(f)
        if cur is None:
            bad += 1
            prev = None
            continue
        if prev is not None:
            m = cur.merge(prev, on="ticker", suffixes=("", "_prev"))
            m["wd"] = (m["whale1000"] - m["whale1000_prev"]).round(2)
            m["rd"] = (m["retail100"] - m["retail100_prev"]).round(2)
            hit = m[(m["wd"] >= WHALE_MIN_DELTA) & (m["rd"] < 0)]
            weeks.append((D, {r.ticker: (r.whale1000, r.wd, r.rd) for r in hit.itertuples()}))
        prev = cur
    print(f"可用週對 {len(weeks)} 個(schema 讀取失敗 {bad} 檔)…載入日K 面板")
    panel = load_panel()
    print(f"日K 面板 {len(panel)} 檔")

    # 同日全市場基準(等權,流動性過濾)
    base = {}
    for D, _ in weeks:
        s20 = s60 = n20 = n60 = 0.0
        for k in panel.values():
            r = forward(k, D)
            if r is None or not r[2]:
                continue
            if np.isfinite(r[0]):
                s20 += r[0]; n20 += 1
            if np.isfinite(r[1]):
                s60 += r[1]; n60 += 1
        base[D] = (s20 / n20 if n20 else np.nan, s60 / n60 if n60 else np.nan, int(n20))

    rows = []
    streak: dict[str, int] = {}
    for D, hits in weeks:
        streak = {tk: (streak.get(tk, 0) + 1) for tk in hits}     # 不在本週者自動歸零
        b20, b60, _ = base[D]
        for tk, (wh, wd, rd) in hits.items():
            k = panel.get(tk)
            if k is None:
                continue
            r = forward(k, D)
            if r is None or not r[2]:
                continue
            f20, f60, _, ed = r
            rows.append({"radar_date": D, "year": D[:4], "ticker": tk, "streak": streak[tk],
                         "whale": wh, "wd": wd, "rd": rd, "entry_date": ed,
                         "f20": f20, "f60": f60, "b20": b20, "b60": b60,
                         "x20": f20 - b20, "x60": f60 - b60})
    df = pd.DataFrame(rows)
    df.to_csv(OUT, index=False, encoding="utf-8-sig")
    print(f"事件 {len(df):,} 筆(流動性過濾後)→ {OUT}\n")

    def st(x: pd.Series) -> str:
        x = x.dropna()
        if not len(x):
            return "n=0"
        return f"n={len(x):5d} 平均{x.mean()*100:+6.2f}% 中位{x.median()*100:+6.2f}% 勝率{(x>0).mean()*100:4.1f}%"

    df["bucket"] = np.where(df["streak"] >= 3, "3+", df["streak"].astype(str))
    print("=== 依 streak 桶(絕對報酬 / 對同日全市場基準的 excess)===")
    for b in ("1", "2", "3+"):
        g = df[df["bucket"] == b]
        print(f"[streak {b}]")
        for col, lab in (("f20", "20日"), ("x20", "20日excess"), ("f60", "60日"), ("x60", "60日excess")):
            print(f"   {lab:10s}", st(g[col]))
    print("\n=== 全部雷達事件 vs 同日基準 ===")
    print("   20日 事件:", st(df["f20"]), "| 基準均值:", f"{df['b20'].mean()*100:+.2f}%")
    print("   60日 事件:", st(df["f60"]), "| 基準均值:", f"{df['b60'].mean()*100:+.2f}%")
    print("\n=== 逐年:streak≥2 vs streak=1 的 60日 excess(平均/勝率)===")
    for y, g in df.groupby("year"):
        a, b_ = g[g["streak"] >= 2]["x60"].dropna(), g[g["streak"] == 1]["x60"].dropna()
        if len(a) >= 10 and len(b_) >= 10:
            print(f"   {y}: 連續 n={len(a):4d} {a.mean()*100:+6.2f}%/{(a>0).mean()*100:4.1f}%"
                  f"   單週 n={len(b_):4d} {b_.mean()*100:+6.2f}%/{(b_>0).mean()*100:4.1f}%")
    print("\n=== 大戶週增幅度分層(全部事件,60日 excess)===")
    for lo, hi in ((3, 5), (5, 8), (8, 99)):
        g = df[(df["wd"] >= lo) & (df["wd"] < hi)]
        print(f"   wd {lo}~{hi}pp:", st(g["x60"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
