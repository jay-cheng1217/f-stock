# -*- coding: utf-8 -*-
"""動手前預檢(唯讀)——任何有副作用的操作(pipeline 重跑、git 切換/合併、DB 修改、
刪檔)之前必跑。2026-09-30 三連事故(日期誤判→盤中啟動 Phase-1→假棒連鎖污染;
git 合併擊落憑證)的機械化防線:把「先看時鐘、先看現況」變成一個指令。

用法:python -X utf8 scripts/preflight_check.py
輸出每項 OK / WARN / STOP;有 STOP 時退出碼 1,代表「不要做有副作用的操作,先回報」。
本腳本不寫任何檔案、不改任何狀態。
"""
from __future__ import annotations

import glob
import json
import re
import subprocess
import sys
import urllib.request
from datetime import date, datetime
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))

results: list[tuple[str, str, str]] = []
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def add(level: str, item: str, detail: str) -> None:
    results.append((level, item, detail))


def check_clock() -> None:
    from scripts.taiwan_trading_calendar import (is_taiwan_trading_day,
                                                 previous_taiwan_trading_day)
    now = datetime.now()
    trading = is_taiwan_trading_day(now.date())
    prev = previous_taiwan_trading_day(now.date())
    add("OK", "系統時鐘", f"{now:%Y-%m-%d %H:%M} ({'交易日' if trading else '非交易日'});"
        f"前一交易日 {prev}")
    if trading and (8, 30) <= (now.hour, now.minute) < (14, 30):
        add("STOP", "盤中時段", "08:30-14:30 禁止 Phase-1/twstock/任何抓取 run(會寫入未收盤假棒)")


def check_git() -> None:
    def g(*a):
        return subprocess.run(["git", *a], cwd=BASE, capture_output=True, text=True,
                              encoding="utf-8", errors="replace").stdout.strip()
    branch = g("rev-parse", "--abbrev-ref", "HEAD")
    dirty = [ln for ln in g("status", "--porcelain").splitlines() if ln.strip()]
    add("OK" if branch == "main" else "STOP", "git 分支",
        f"{branch}" + ("" if branch == "main" else " — 線上以 main 為準,先回報"))
    add("OK" if not dirty else "WARN", "工作區", "乾淨" if not dirty else f"{len(dirty)} 個未提交異動")
    add("WARN", "git 操作提醒",
        "checkout/merge/reset/rebase 會重寫追蹤檔 mtime → 擊落當日名單憑證(API 503);做了就必須立即重跑 Phase-2")


def check_processes() -> None:
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'python.exe' } | "
             "ForEach-Object { $_.CommandLine }"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30).stdout
    except Exception as exc:  # noqa: BLE001
        add("WARN", "執行中程序", f"無法查詢: {exc}")
        return
    keys = ("smart_update_auto", "twstock", "daily_pipeline", "shadow_dataA", "ingest_all", "run_weekly_retrain")
    running = sorted({k for line in out.splitlines() for k in keys if k in line})
    add("STOP" if running else "OK", "pipeline 程序",
        ("執行中: " + ", ".join(running) + " — 不要並行操作") if running else "無")


def check_api() -> None:
    try:
        r = urllib.request.urlopen("http://127.0.0.1:8001/api/entry/canonical", timeout=15)
        d = json.loads(r.read())
        add("OK", "名單 API", f"200, {len(d.get('rows', []))} 檔, trade_date={d.get('trade_date')}")
    except Exception as exc:  # noqa: BLE001
        add("WARN", "名單 API", f"不可用: {str(exc)[:80]}(憑證失效時正確修法=重跑 Phase-2,不可碰 mtime)")


def check_residue() -> None:
    today = date.today().isoformat()
    now = datetime.now()
    from scripts.taiwan_trading_calendar import is_taiwan_trading_day
    before_close = is_taiwan_trading_day(now.date()) and (now.hour, now.minute) < (14, 30)
    bad, unparsed = [], []
    for f in glob.glob(str(BASE / "日K資料" / "*.csv")):
        try:
            # 日K 每列含大量指標欄(>300 bytes),只讀檔尾會切到半行;讀 16KB 取最後完整一行
            with open(f, "rb") as fh:
                fh.seek(0, 2)
                size = fh.tell()
                fh.seek(max(0, size - 16384))
                lines = fh.read().decode("utf-8", "replace").strip().splitlines()
            last = lines[-1] if lines else ""
        except Exception:  # noqa: BLE001
            continue
        d = last[:10]
        if not DATE_RE.fullmatch(d):
            unparsed.append(Path(f).stem)
            continue
        if d > today or (before_close and d == today):
            bad.append(Path(f).stem)
    if unparsed:
        add("WARN", "日K 末行無法解析", f"{len(unparsed)} 檔(如 {', '.join(unparsed[:3])}),未納入判定")
    add("STOP" if bad else "OK", "日K 未收盤假棒",
        (f"{len(bad)} 檔含未收盤/未來日期列(如 {', '.join(bad[:5])}),ingest 前必須隔離清除") if bad else "無")
    models = [Path(p).name for p in glob.glob(str(BASE / "ml" / "models" / f"*{today}*"))]
    if before_close and models:
        add("STOP", "當日模型產物", f"盤中出現 {len(models)} 個當日檔(如 {models[0]}),疑似污染")


def main() -> int:
    for fn in (check_clock, check_git, check_processes, check_api, check_residue):
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            add("WARN", fn.__name__, f"檢查失敗: {exc}")
    width = max(len(i) for _, i, _ in results)
    for level, item, detail in results:
        print(f"[{level:4}] {item:<{width}}  {detail}")
    stops = [r for r in results if r[0] == "STOP"]
    print("\n結論:", "有 STOP — 不要做有副作用的操作,先回報用戶" if stops else "可進行(仍須逐步驗證)")
    return 1 if stops else 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
