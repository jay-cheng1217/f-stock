# -*- coding: utf-8 -*-
"""日K收盤/量 歷史正確性抽樣稽核(上市 TWSE + 上櫃 TPEX 官方逐日對照)。

回答:「是不是好幾天的收盤都有問題?」——抽樣 N 檔,對照官方近月逐日,
輸出每日 mismatch 率。research/audit-only。
"""
from __future__ import annotations

import json
import random
import subprocess
import time
from pathlib import Path

import pandas as pd

BASE = Path(__file__).resolve().parents[1]
DAILY = BASE / "日K資料"
random.seed(42)

TPEX_URL = "https://www.tpex.org.tw/www/zh-tw/afterTrading/tradingStock?code={code}&date={y}/{m:02d}/01&response=json"
TWSE_URL = "https://www.twse.com.tw/exchangeReport/STOCK_DAY?response=json&date={y}{m:02d}01&stockNo={code}"


def curl_json(url: str):
    out = subprocess.run(["curl", "-sL", "--max-time", "30", "-H", "User-Agent: Mozilla/5.0", url],
                         capture_output=True, timeout=45)
    return json.loads(out.stdout.decode("utf-8"))


def num(s):
    try:
        return float(str(s).replace(",", ""))
    except Exception:
        return None


def official_rows(code: str, market: str) -> dict:
    """回 {date_iso: (close, vol_shares)};抓 6、7 兩個月。"""
    out = {}
    for (y, m) in [(2026, 6), (2026, 7)]:
        try:
            if market == "tpex":
                d = curl_json(TPEX_URL.format(code=code, y=y, m=m))
                data = (d.get("tables") or [{}])[0].get("data") or []
                for r in data:
                    roc = r[0].split("/")
                    iso = f"{int(roc[0])+1911}-{roc[1]}-{roc[2]}"
                    vol = num(r[1])
                    close = num(r[6])
                    if close is not None:
                        out[iso] = (close, (vol or 0) * 1000)  # 張→股
            else:
                d = curl_json(TWSE_URL.format(code=code, y=y, m=m))
                for r in d.get("data") or []:
                    roc = r[0].split("/")
                    iso = f"{int(roc[0])+1911}-{roc[1]}-{roc[2]}"
                    vol = num(r[1])          # 股數
                    close = num(r[6])
                    if close is not None:
                        out[iso] = (close, vol or 0)
        except Exception:
            pass
        time.sleep(0.12)
    return out


def main() -> None:
    # 抽樣:用 sector_mapping 分市場
    sec = pd.read_csv(BASE / "ml" / "data" / "sector_mapping.csv", dtype=str)
    otc = [t for t, mk in zip(sec["Ticker"], sec["Market"]) if mk == "上櫃" and (DAILY / f"{t}.csv").exists()]
    lst = [t for t, mk in zip(sec["Ticker"], sec["Market"]) if mk == "上市" and (DAILY / f"{t}.csv").exists()]
    sample = [(t, "tpex") for t in random.sample(otc, min(40, len(otc)))] + \
             [(t, "twse") for t in random.sample(lst, min(30, len(lst)))]

    recs = []
    for i, (tk, mkt) in enumerate(sample):
        off = official_rows(tk, mkt)
        if not off:
            continue
        k = pd.read_csv(DAILY / f"{tk}.csv", usecols=["Date", "Close", "Volume"])
        k["Date"] = k["Date"].astype(str).str[:10]
        loc = dict(zip(k["Date"], zip(k["Close"], k["Volume"])))
        for d, (oc, ov) in off.items():
            if d not in loc or d < "2026-06-18":
                continue
            lc, lv = loc[d]
            close_bad = abs(lc - oc) > max(0.05, oc * 0.001)
            vol_bad = ov > 0 and abs(lv - ov) > ov * 0.02 + 2000
            recs.append({"date": d, "mkt": mkt, "tk": tk,
                         "close_bad": close_bad, "vol_bad": vol_bad,
                         "lc": lc, "oc": oc})
        if (i + 1) % 20 == 0:
            print(f"  {i+1}/{len(sample)}")
    df = pd.DataFrame(recs)
    df.to_csv(BASE / "logs" / "close_history_audit.csv", index=False, encoding="utf-8-sig")
    print("\n=== 每日 mismatch 率(收盤價/量,樣本 70 檔)===")
    g = df.groupby(["date", "mkt"]).agg(n=("tk", "count"),
                                        close_bad=("close_bad", "sum"),
                                        vol_bad=("vol_bad", "sum")).reset_index()
    for _, r in g.iterrows():
        flag = " <<WARN" if r["close_bad"] > r["n"] * 0.1 or r["vol_bad"] > r["n"] * 0.1 else ""
        print(f"{r['date']} {r['mkt']:4s} n={r['n']:3d} 收盤錯 {r['close_bad']:3d} 量錯 {r['vol_bad']:3d}{flag}")
    bad = df[df["close_bad"]]
    if len(bad):
        print("\n收盤錯樣本(前10):")
        print(bad.head(10).to_string(index=False))
    df.to_csv(BASE / "logs" / "close_history_audit.csv", index=False, encoding="utf-8-sig")
    print("\n明細 -> logs/close_history_audit.csv")


if __name__ == "__main__":
    main()
