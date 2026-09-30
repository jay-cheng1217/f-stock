# -*- coding: utf-8 -*-
"""動態產業強度(取代寫死產業過濾)——rere 發動型的產業層。

不寫死「只做電子」;每次執行算各產業近 N 週的相對強度,產出當下強勢族群清單。
產業會輪動(2021 航運、2026 半導體),此層讓系統自動跟。

強度 = 該產業成分股近 20 日中位報酬的百分位(全市場產業間排序)。
資料源:日K資料 + sector_mapping。純資訊層,輸出 ml/data/sector_strength.json。
用法:python scripts/sector_strength.py
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path(__file__).resolve().parents[1]
DAILY = BASE / "日K資料"
OUT = BASE / "ml" / "data" / "sector_strength.json"


def main() -> None:
    sec = pd.read_csv(BASE / "ml" / "data" / "sector_mapping.csv", dtype=str)
    smap = dict(zip(sec["Ticker"].str.zfill(4), sec["Sector"]))

    rets: dict[str, list[float]] = {}
    for f in DAILY.glob("*.csv"):
        tk = f.stem
        if not (tk.isdigit() and len(tk) == 4):
            continue
        s = smap.get(tk)
        if not s:
            continue
        try:
            c = pd.read_csv(f, usecols=["Close"])["Close"].astype(float)
        except Exception:
            continue
        if len(c) < 25:
            continue
        r20 = c.iloc[-1] / c.iloc[-21] - 1
        if np.isfinite(r20):
            rets.setdefault(s, []).append(float(r20))

    rows = []
    for s, v in rets.items():
        if len(v) < 5:
            continue
        rows.append((s, len(v), float(np.median(v)), float(np.mean(v))))
    rows.sort(key=lambda r: -r[2])  # 依中位報酬降序
    n = len(rows)
    out = {}
    for rank, (s, cnt, med, mean) in enumerate(rows):
        pct = 1 - rank / max(1, n - 1)  # 0-1,越高越強
        tier = "strong" if pct >= 0.66 else ("weak" if pct <= 0.33 else "neutral")
        out[s] = {"rank": rank + 1, "pct": round(pct, 3), "tier": tier,
                  "median_20d": round(med, 4), "n": cnt}

    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"產業強度 {n} 類 -> {OUT}\n強勢族群(近20日中位報酬前1/3):")
    for s, d in out.items():
        if d["tier"] == "strong":
            print(f"  {s:<12} 中位{d['median_20d']*100:+.1f}% (第{d['rank']}名, pct {d['pct']})")


if __name__ == "__main__":
    main()
