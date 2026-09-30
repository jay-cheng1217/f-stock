# -*- coding: utf-8 -*-
"""盤中隨叫隨查:抓 TWSE MIS 即時報價,對照進場名單判斷。

特性:隨叫隨查、不背景輪詢、不寫檔(除非 --save)、自動判上市/上櫃。

用法:
  python scripts/intraday_quote.py 2330 3227 2428        # 查現價 + 五檔
  python scripts/intraday_quote.py --plan logs/entry_list_20260701.json   # 對照名單判斷
  python scripts/intraday_quote.py 3227 --plan logs/entry_list_20260701.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time

import requests

# 確保中文/emoji 輸出不因 Windows cp950 主控台崩潰
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        try:
            _s.reconfigure(encoding="utf-8")
        except Exception:
            pass

MIS_URL = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp"
MIS_INDEX = "https://mis.twse.com.tw/stock/index.jsp"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": MIS_INDEX,
}


def _num(x):
    try:
        if x in (None, "", "-"):
            return None
        return float(x)
    except (TypeError, ValueError):
        return None


def fetch_quotes(tickers: list[str], timeout: float = 8.0, retries: int = 3) -> dict[str, dict]:
    """查 MIS。每檔同時掛 tse_ 與 otc_,取有資料那個。回 {ticker: quote}。"""
    ex_ch = "|".join(f"{mkt}_{t}.tw" for t in tickers for mkt in ("tse", "otc"))
    sess = requests.Session()
    sess.headers.update(HEADERS)
    last_err = None
    for attempt in range(retries):
        try:
            if attempt == 0:
                sess.get(MIS_INDEX, timeout=timeout)  # 取 cookie
            r = sess.get(
                MIS_URL,
                params={"ex_ch": ex_ch, "json": "1", "delay": "0", "_": str(int(time.time() * 1000))},
                timeout=timeout,
            )
            r.raise_for_status()
            body = r.json()
            break
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            time.sleep(0.6 * (attempt + 1))
    else:
        raise RuntimeError(f"MIS 查詢失敗:{last_err}")

    out: dict[str, dict] = {}
    for m in body.get("msgArray", []):
        code = m.get("c")
        if not code:
            continue
        z = _num(m.get("z"))
        y = _num(m.get("y"))
        bid1 = _num((m.get("b") or "").split("_")[0])
        ask1 = _num((m.get("a") or "").split("_")[0])
        # MIS 對鎖死盤會給 0/空的五檔,0 不是有效價
        if bid1 == 0:
            bid1 = None
        if ask1 == 0:
            ask1 = None
        high = _num(m.get("h"))
        low = _num(m.get("l"))
        # 成交價缺(z='-')時的 fallback(2026-09-30 瑞耘事故修正:漲停鎖死被
        # 誤顯示成開盤價,把鎖漲停讀成回吐)。鎖死判定:單邊五檔全空。
        locked = None
        if z is None:
            if ask1 is None and bid1 is not None:
                price, locked = bid1, "limit_up"      # 掛買無賣 = 鎖漲停
            elif bid1 is None and ask1 is not None:
                price, locked = ask1, "limit_down"    # 掛賣無買 = 鎖跌停
            elif ask1 is None and bid1 is None and high is not None and y and high >= y * 1.085:
                price, locked = high, "limit_up"      # 五檔全空但日高已達漲停幅度
            else:
                price = bid1 or _num(m.get("o")) or y
        else:
            price = z
        prev = out.get(code)
        # 若同代號 tse/otc 都回,保留有成交價那筆
        if prev is not None and prev.get("price") is not None and price is None:
            continue
        chg = (price - y) if (price is not None and y is not None) else None
        pct = (chg / y * 100) if (chg is not None and y) else None
        out[code] = {
            "ticker": code,
            "name": m.get("n"),
            "market": m.get("ex"),
            "price": price,
            "prev_close": y,
            "chg": chg,
            "pct": pct,
            "open": _num(m.get("o")),
            "high": high,
            "low": low,
            "locked": locked,
            "vol": m.get("v"),
            "bid1": bid1,
            "ask1": ask1,
            "time": m.get("t"),
            "has_trade": z is not None,
        }
    return out


def _first_two_nums(s: str) -> tuple[float | None, float | None]:
    nums = [float(x) for x in re.findall(r"\d+\.?\d*", str(s or ""))]
    return (nums[0] if nums else None, nums[1] if len(nums) > 1 else None)


def _first_num(s: str) -> float | None:
    nums = re.findall(r"\d+\.?\d*", str(s or ""))
    return float(nums[0]) if nums else None


def verdict(price: float | None, low, high, stop, no_chase, kind: str) -> str:
    if price is None:
        return "無成交價"
    if stop is not None and price < stop:
        return f"❌ 跌破停損 {stop} → 失效"
    if no_chase is not None and price > no_chase:
        return f"⚠️ 噴過上緣 {no_chase} → 不追"
    if low is not None and high is not None:
        if price < low:
            return f"⏳ 未到區間(需回 {low}–{high})"
        if price <= high:
            base = "✅ 進區間·可觸發" if kind in ("go", "small") else "👀 進區間(此檔僅觀察)"
            return f"{base}(守 15–30 分)"
        return f"⚠️ 高於區間上緣 {high} → 別追"
    return "—"


def load_plan(path: str) -> list[dict]:
    data = json.load(open(path, encoding="utf-8"))
    rows = []
    for r in data.get("rows", []):
        code = None
        mcode = re.search(r"\d{4}", str(r.get("stock", "")))
        if mcode:
            code = mcode.group(0)
        low, high = _first_two_nums(r.get("zone"))
        rows.append({
            "ticker": code,
            "stock": r.get("stock"),
            "kind": r.get("kind"),
            "status": r.get("status"),
            "low": low, "high": high,
            "stop": _first_num(r.get("stop")),
            "no_chase": _first_num(r.get("no_chase")),
        })
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description="盤中即時報價(TWSE MIS)")
    ap.add_argument("tickers", nargs="*", help="股票代號,可多個")
    ap.add_argument("--plan", help="進場名單 JSON,對照判斷")
    ap.add_argument("--save", help="(選配)把這次快照寫到此路徑,預設不寫檔")
    args = ap.parse_args()

    plan_rows = load_plan(args.plan) if args.plan else []
    tickers = list(args.tickers)
    if plan_rows and not tickers:
        tickers = [r["ticker"] for r in plan_rows if r["ticker"]]
    if not tickers:
        ap.error("給我代號,或用 --plan 帶名單")
    tickers = list(dict.fromkeys(tickers))

    q = fetch_quotes(tickers)

    lines = []
    if plan_rows:
        plan_by = {r["ticker"]: r for r in plan_rows}
        for t in tickers:
            info = q.get(t)
            row = plan_by.get(t, {})
            if not info:
                lines.append(f"{t} {row.get('stock','')}: 查無報價")
                continue
            v = verdict(info["price"], row.get("low"), row.get("high"),
                        row.get("stop"), row.get("no_chase"), row.get("kind", ""))
            pct = f"{info['pct']:+.2f}%" if info["pct"] is not None else "—"
            zone = f"{row.get('low')}–{row.get('high')}" if row.get("low") else "—"
            lines.append(f"{t} {info['name'] or ''} 現價 {info['price']} ({pct}) | 區間 {zone} 停損 {row.get('stop')} | {v}")
    else:
        for t in tickers:
            info = q.get(t)
            if not info:
                lines.append(f"{t}: 查無報價")
                continue
            pct = f"{info['pct']:+.2f}%" if info["pct"] is not None else "—"
            lines.append(
                f"{t} {info['name'] or ''} 現價 {info['price']} ({pct})"
                f"{'🔒漲停鎖死' if info.get('locked') == 'limit_up' else '🔒跌停鎖死' if info.get('locked') == 'limit_down' else ''} "
                f"開{info['open']} 高{info['high']} 低{info['low']} 買一{info['bid1']} 賣一{info['ask1']} {info['time'] or ''}"
            )

    text = "\n".join(lines)
    print(text)
    if args.save:
        with open(args.save, "w", encoding="utf-8") as f:
            json.dump({"quotes": q, "fetched": time.strftime("%Y-%m-%d %H:%M:%S")}, f, ensure_ascii=False, indent=2)
        print(f"(已存 {args.save})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
