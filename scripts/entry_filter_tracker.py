# -*- coding: utf-8 -*-
"""每日進場過濾器帳本:記錄 canonical 名單 → 模擬觸發/出場 → 觀察每次進場的損益。

規則(與 skill 5 步紀律一致,確定性模擬):
- 只記 go/small/rere(watch 需人工回踩觸發,不入帳本)。
- 觸發:名單交易日當日 Low<=區間上緣 且 High>=區間下緣;
  進場價 = Open 若在區間內,否則從上方回踩=區間上緣、從下方進入=區間下緣。
- 出場:收盤 < 失效價(stop)→ 當日收盤停損;或持有到期(主 lane 20 交易日/rere 60)收盤結算。
- 未觸發 = 當日不成立(名單每日重生,不跨日等待)。

檔案:ml/reports/entry_filter_ledger.csv(帳本)
用法:
  python scripts/entry_filter_tracker.py record --plan logs/entry_list_20260703.json
  python scripts/entry_filter_tracker.py backfill      # 掃全部 entry_list_*.json
  python scripts/entry_filter_tracker.py check         # 更新持有中部位/觸發判定
"""
from __future__ import annotations

import argparse
import glob
import json
import re
import sys
from pathlib import Path

import pandas as pd

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))

from scripts.exdiv_utils import cum_dividend

LEDGER = BASE_DIR / "ml" / "reports" / "entry_filter_ledger.csv"
# 2026-10-07 起主 lane 排序不再用模型;舊規則(模型不反對 → clean+pred20)改為 plan 的 `shadow_legacy_main`
# 影子列,記入獨立帳本並行比較(≥60 日後 PM 裁決)。同一套觸發/失效價/到期規則。
LEGACY_LEDGER = BASE_DIR / "ml" / "reports" / "entry_filter_ledger_legacy_main.csv"
LEGACY_ROWS_KEY = "shadow_legacy_main"
DAILY = BASE_DIR / "日K資料"
STATIC_LEDGER = BASE_DIR / "frontend" / "static" / "entry_filter_ledger.json"
HORIZON = {"main": 20, "rere": 60, "main_legacy": 20}

COLS = ["trade_date", "ticker", "name", "lane", "state", "zone_low", "zone_high",
        "stop", "horizon", "status", "entry_date", "entry_price",
        "exit_date", "exit_price", "exit_reason", "days_held", "ret_pct",
        "last_date", "last_close", "exit_adjusted_price", "last_adjusted_close",
        "cum_dividend_per_share", "return_adjustment_status"]


def _names() -> dict:
    """sector_mapping 為名稱權威源(entry_list 的 stock 字串格式不保證含公司名)。"""
    try:
        sec = pd.read_csv(BASE_DIR / "ml" / "data" / "sector_mapping.csv", dtype=str)
        return dict(zip(sec["Ticker"].str.zfill(4), sec["Name"]))
    except Exception:
        return {}


NAMES = _names()


def _load(ledger: Path | None = None) -> pd.DataFrame:
    ledger = ledger or LEDGER
    if ledger.exists():
        d = pd.read_csv(ledger, dtype={"ticker": str, "trade_date": str})
        d["ticker"] = d["ticker"].str.zfill(4)
        return d
    return pd.DataFrame(columns=COLS)


def _lane(row: dict) -> str | None:
    lane = str(row.get("lane") or "")
    kind = str(row.get("kind") or "")
    if kind == "watch" or "watch" in lane:
        return None  # 觀察股需人工觸發,不入帳本
    if lane == "main_legacy":
        return "main_legacy"
    return "rere" if lane == "rere" else "main"


def record(plan_path: Path, *, rows_key: str = "rows", ledger: Path | None = None) -> int:
    plan_path = Path(plan_path)
    ledger = ledger or LEDGER
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    m = re.search(r"(\d{8})", plan_path.name)
    trade_date = f"{m.group(1)[:4]}-{m.group(1)[4:6]}-{m.group(1)[6:]}" if m else ""
    led = _load(ledger)
    added = 0
    for r in plan.get(rows_key, []):
        lane = _lane(r)
        if lane is None:
            continue
        mm = re.search(r"\d{4,6}", str(r.get("stock", "")))
        if not mm:
            continue
        tk = mm.group(0)
        if not led.empty and ((led["trade_date"] == trade_date) & (led["ticker"] == tk)).any():
            continue
        zm = re.match(r"([\d.]+)\s*[-–—~]\s*([\d.]+)", str(r.get("zone") or ""))
        if not zm:
            continue
        try:
            stop = float(r.get("stop"))
        except Exception:
            continue
        name = NAMES.get(tk) or str(r.get("stock", "")).replace(tk, "").strip()
        led = pd.concat([led, pd.DataFrame([{
            "trade_date": trade_date, "ticker": tk, "name": name, "lane": lane,
            "state": r.get("kind"), "zone_low": float(zm.group(1)), "zone_high": float(zm.group(2)),
            "stop": stop, "horizon": HORIZON[lane], "status": "pending",
        }])], ignore_index=True)
        added += 1
    led.to_csv(ledger, index=False, encoding="utf-8-sig")
    print(f"{plan_path.name}[{rows_key}]: 新增 {added} 筆(帳本 {ledger.name} 共 {len(led)})")
    return added


def check(*, ledger: Path | None = None) -> None:
    ledger = ledger or LEDGER
    led = _load(ledger)
    if led.empty:
        print("帳本為空")
        return
    led = led.astype(object)  # 混合型欄位,避免 pandas dtype 警告
    cache: dict[str, pd.DataFrame] = {}

    def kbars(tk: str) -> pd.DataFrame | None:
        if tk not in cache:
            p = DAILY / f"{tk}.csv"
            if not p.exists():
                cache[tk] = None
            else:
                k = pd.read_csv(p, usecols=lambda c: c in ("Date", "Open", "High", "Low", "Close"))
                k["Date"] = k["Date"].astype(str).str[:10]
                cache[tk] = k.sort_values("Date").reset_index(drop=True)
        return cache[tk]

    for i, r in led.iterrows():
        if r["status"] in ("stopped", "expired", "untriggered"):
            continue
        k = kbars(r["ticker"])
        if k is None:
            continue
        # 1) 觸發判定(pending → triggered/untriggered)
        if r["status"] == "pending":
            day = k[k["Date"] == r["trade_date"]]
            if day.empty:
                led.loc[i, "last_date"] = k["Date"].iloc[-1]
                continue  # 該交易日資料未到(未來名單)
            d0 = day.iloc[0]
            if d0["Low"] <= r["zone_high"] and d0["High"] >= r["zone_low"]:
                entry = d0["Open"]
                if entry > r["zone_high"]:
                    entry = r["zone_high"]
                elif entry < r["zone_low"]:
                    entry = r["zone_low"]
                led.loc[i, ["status", "entry_date", "entry_price"]] = ["holding", r["trade_date"], round(float(entry), 2)]
            else:
                led.loc[i, "status"] = "untriggered"
                continue
        # 2) 持有中 → 停損/到期/續抱
        row = led.loc[i]
        after = k[k["Date"] >= row["entry_date"]].reset_index(drop=True)
        entry_px = float(row["entry_price"])
        entry_date = str(row["entry_date"])
        done = False
        for j, b in after.iterrows():
            if j == 0:
                continue  # 進場日不判停損(收盤破停損隔日才反應,與紀律「盤中不凹單日」折衷)
            raw_close = float(b["Close"])
            dividend = cum_dividend(str(row["ticker"]), entry_date, str(b["Date"]))
            adjusted_close = raw_close + dividend
            if adjusted_close < float(row["stop"]):
                led.loc[i, [
                    "status", "exit_date", "exit_price", "exit_adjusted_price",
                    "cum_dividend_per_share", "return_adjustment_status", "exit_reason", "days_held",
                ]] = [
                    "stopped", b["Date"], round(raw_close, 2), round(adjusted_close, 4),
                    round(dividend, 4), "cash_dividend_adjusted", "破失效價", j,
                ]
                led.loc[i, "ret_pct"] = round((adjusted_close / entry_px - 1) * 100, 2)
                done = True
                break
            if j >= int(row["horizon"]):
                led.loc[i, [
                    "status", "exit_date", "exit_price", "exit_adjusted_price",
                    "cum_dividend_per_share", "return_adjustment_status", "exit_reason", "days_held",
                ]] = [
                    "expired", b["Date"], round(raw_close, 2), round(adjusted_close, 4),
                    round(dividend, 4), "cash_dividend_adjusted", f"{int(row['horizon'])}日到期", j,
                ]
                led.loc[i, "ret_pct"] = round((adjusted_close / entry_px - 1) * 100, 2)
                done = True
                break
        if not done:
            last = after.iloc[-1]
            raw_close = float(last["Close"])
            dividend = cum_dividend(str(row["ticker"]), entry_date, str(last["Date"]))
            adjusted_close = raw_close + dividend
            led.loc[i, [
                "days_held", "last_date", "last_close", "last_adjusted_close",
                "cum_dividend_per_share", "return_adjustment_status",
            ]] = [
                len(after) - 1, last["Date"], round(raw_close, 2), round(adjusted_close, 4),
                round(dividend, 4), "cash_dividend_adjusted",
            ]
            led.loc[i, "ret_pct"] = round((adjusted_close / entry_px - 1) * 100, 2)

    led.to_csv(ledger, index=False, encoding="utf-8-sig")
    # web app 用 static JSON(與 entry_canonical.json 同機制);影子帳本不發布 static
    if ledger == LEDGER:
        import json as _json
        try:
            STATIC_LEDGER.write_text(_json.dumps({
                "updated": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M"),
                "rows": led.fillna("").to_dict("records")}, ensure_ascii=False),
                encoding="utf-8")
        except Exception as e:  # noqa: BLE001
            print(f"[warn] static json 寫入失敗: {e}")
    trig = led[led["status"].isin(["holding", "stopped", "expired"])]
    closed = led[led["status"].isin(["stopped", "expired"])]
    print(f"帳本 {ledger.name} {len(led)} 筆:觸發 {len(trig)}、持有中 {(led['status']=='holding').sum()}、"
          f"停損 {(led['status']=='stopped').sum()}、到期 {(led['status']=='expired').sum()}、"
          f"未觸發 {(led['status']=='untriggered').sum()}、待判定 {(led['status']=='pending').sum()}")
    if len(closed):
        print(f"已平倉:平均 {closed['ret_pct'].mean():+.2f}%、勝率 {(closed['ret_pct']>0).mean()*100:.0f}%")


def backfill(*, rows_key: str = "rows", ledger: Path | None = None) -> None:
    # 預設路徑維持無參數呼叫(既有測試/呼叫者相容);影子帳本才帶 rows_key / ledger
    rec_kw = ({"rows_key": rows_key} if rows_key != "rows" else {}) | ({"ledger": ledger} if ledger else {})
    chk_kw = {"ledger": ledger} if ledger else {}
    for f in sorted(glob.glob(str(BASE_DIR / "logs" / "entry_list_*.json"))):
        if not re.fullmatch(r"entry_list_[0-9]{8}\.json", Path(f).name):
            continue
        record(Path(f), **rec_kw)
    check(**chk_kw)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["record", "check", "backfill"])
    ap.add_argument("--plan")
    ap.add_argument("--rows-key", default="rows", help="plan JSON 的列鍵;影子帳本用 shadow_legacy_main")
    ap.add_argument("--ledger", default=None, help="帳本路徑;省略=正式 entry_filter_ledger.csv")
    ap.add_argument("--legacy", action="store_true",
                    help=f"快捷:--rows-key {LEGACY_ROWS_KEY} --ledger {LEGACY_LEDGER.name}")
    a = ap.parse_args()
    rows_key = LEGACY_ROWS_KEY if a.legacy else a.rows_key
    ledger = LEGACY_LEDGER if a.legacy else (Path(a.ledger) if a.ledger else None)
    if a.cmd == "record":
        record(Path(a.plan), rows_key=rows_key, ledger=ledger)
        check(ledger=ledger)
    elif a.cmd == "backfill":
        backfill(rows_key=rows_key, ledger=ledger)
    else:
        check(ledger=ledger)
