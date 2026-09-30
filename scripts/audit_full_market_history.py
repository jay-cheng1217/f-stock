# -*- coding: utf-8 -*-
"""全市場×逐日 收盤價完整性稽核(date-oriented:每交易日 2 請求覆蓋全部股票)。

- 上市:TWSE MI_INDEX(type=ALLBUT0999)按日全市場收盤行情
- 上櫃:TPEX 上櫃行情 stk_wn1430 按日全市場
- 對照本地 日K資料/*.csv 的 Close;差 >0.1% 記為 mismatch
用法:
  python scripts/audit_full_market_history.py --start 2026-04-01   # 預設近3個月
輸出:logs/full_market_audit.csv(明細) + 每日彙總 stdout
禮貌節流:每請求間隔 1.2s(TWSE 有限流);65 交易日 ≈ 3 分鐘。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path

import pandas as pd

BASE = Path(__file__).resolve().parents[1]
DAILY = BASE / "日K資料"

TWSE = "https://www.twse.com.tw/exchangeReport/MI_INDEX?response=json&date={d8}&type=ALLBUT0999"
TPEX = "https://www.tpex.org.tw/web/stock/aftertrading/otc_quotes_no1430/stk_wn1430_result.php?l=zh-tw&d={roc}&se=EW"


def curl_json(url: str):
    out = subprocess.run(["curl", "-sL", "--max-time", "40", "-H", "User-Agent: Mozilla/5.0", url],
                         capture_output=True, timeout=60)
    try:
        return json.loads(out.stdout.decode("utf-8"))
    except Exception:
        return None


def num(s):
    try:
        v = float(str(s).replace(",", "").replace("--", "nan"))
        return v if v == v else None
    except Exception:
        return None


def official_closes(day: str) -> dict[str, float]:
    """day = YYYY-MM-DD → {ticker: official_close} 全市場(上市+上櫃)。"""
    out: dict[str, float] = {}
    d8 = day.replace("-", "")
    j = curl_json(TWSE.format(d8=d8))
    if j:
        tables = j.get("tables") or []
        if not tables and j.get("data9"):  # 舊格式
            tables = [{"fields": j.get("fields9"), "data": j.get("data9")}]
        for t in tables:
            fields = t.get("fields") or []
            if "證券代號" in fields and "收盤價" in fields:
                ci, cc = fields.index("證券代號"), fields.index("收盤價")
                for row in t.get("data") or []:
                    code = str(row[ci]).strip()
                    if code.isdigit() and len(code) == 4:
                        c = num(row[cc])
                        if c is not None:
                            out[code] = c
    time.sleep(1.2)
    y, m, dd = day.split("-")
    roc = f"{int(y)-1911}/{m}/{dd}"
    j = curl_json(TPEX.format(roc=roc))
    if j:
        rows = j.get("aaData") or (j.get("tables") or [{}])[0].get("data") or []
        for row in rows:
            code = str(row[0]).strip()
            if code.isdigit() and len(code) == 4:
                c = num(row[2])
                if c is not None:
                    out[code] = c
    time.sleep(1.2)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2026-04-01")
    ap.add_argument("--end", default=None)
    a = ap.parse_args()

    # 本地全部日K載入(只留 Date/Close,窗口內)
    print("載入本地 CSV…")
    local: dict[str, dict[str, float]] = {}
    for f in DAILY.glob("*.csv"):
        tk = f.stem
        if not (tk.isdigit() and len(tk) == 4):
            continue
        try:
            k = pd.read_csv(f, usecols=["Date", "Close"])
        except Exception:
            continue
        k["Date"] = k["Date"].astype(str).str[:10]
        k = k[k["Date"] >= a.start]
        if a.end:
            k = k[k["Date"] <= a.end]
        local[tk] = dict(zip(k["Date"], k["Close"]))
    days = sorted({d for m in local.values() for d in m})
    print(f"本地 {len(local)} 檔,窗口 {days[0]}~{days[-1]} 共 {len(days)} 交易日")

    recs = []
    for i, day in enumerate(days):
        off = official_closes(day)
        n_cmp = n_bad = 0
        for tk, c_off in off.items():
            c_loc = local.get(tk, {}).get(day)
            if c_loc is None:
                continue
            n_cmp += 1
            if abs(c_loc - c_off) > max(0.05, c_off * 0.001):
                n_bad += 1
                recs.append({"date": day, "ticker": tk, "local": c_loc, "official": c_off})
        flag = "  <<WARN" if n_cmp and n_bad > max(3, n_cmp * 0.01) else ""
        print(f"{day} 比對 {n_cmp:4d} 檔  收盤不符 {n_bad:3d}{flag}")
    out = BASE / "logs" / "full_market_audit.csv"
    pd.DataFrame(recs).to_csv(out, index=False, encoding="utf-8-sig")
    print(f"\n不符明細 {len(recs)} 筆 -> {out}")


if __name__ == "__main__":
    main()
