# -*- coding: utf-8 -*-
"""匯入預演(唯讀):用正式匯入程式,把現在的資料目錄匯進一個記憶體 DuckDB,確認下一次排程匯入不會失敗。

2026-10-17 月營收回補寫入後,所有營收檔都沒有 Ticker 欄,正式匯入 SQL 會 Binder Error 讓週一 Phase-2 整個失敗;
當時是手動用 SQL 實測才發現。本工具把那次手動檢查固定成一個指令:
  - 逐表呼叫 backend.db.ingest 的 _ingest_daily_k / _ingest_revenue / _ingest_financials / _ingest_tdcc /
    _ingest_indices 與 _build_stock_list,連線換成記憶體資料庫,正式 stock.duckdb 不會被讀寫;
  - 正式契約照跑(欄位、型別、空鍵、新鮮度不倒退、列數不低於前次 70%)。前次基準取正式 ingest_meta;
    資料庫被網頁持有時,改讀最近一次排程 log 的匯入列數,這時不比對新鮮度;
  - 另查代號欄沒有「1441.0」這種帶小數點的值。
什麼時候跑:手動寫入任何正式資料目錄(日K資料/、月營收/、季報財務/、集保分散/、大盤指數/)之後、下一次排程匯入之前。
preflight 會在「資料比上次匯入與上次預演都新」時提醒;merge_branch.sh 在改到匯入相關程式時自動執行。

用法:python -X utf8 scripts/ingest_dry_run.py
退出碼 0=PASS、1=FAIL;報告寫 logs/ingest_dry_run_latest.json(preflight 讀它)。
"""
from __future__ import annotations

import ast
import io
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))

STEPS = (("daily_k", "_ingest_daily_k"), ("revenue", "_ingest_revenue"), ("financials", "_ingest_financials"),
         ("tdcc", "_ingest_tdcc"), ("indices", "_ingest_indices"))
TICKER_TABLES = ("daily_k", "revenue", "financials", "tdcc")
REPORT = BASE / "logs" / "ingest_dry_run_latest.json"
COUNTS_RE = re.compile(r"\{'daily_k': \d+[^{}]*\}")


def baseline_from_logs(log_dir: Path) -> tuple[dict, str]:
    """最近一次排程匯入的列數(排程 stdio log 的「{'daily_k': N, ...}」);沒有新鮮度。"""
    files = list(log_dir.glob("smart_update_auto_stdio_*.log")) + list(log_dir.glob("daily_pipeline_stdio_*.log"))
    for p in sorted(files, key=lambda f: f.stat().st_mtime, reverse=True)[:14]:
        hits = COUNTS_RE.findall(p.read_bytes().decode("utf-8", "replace"))
        if hits:
            return {t: (int(n), None) for t, n in ast.literal_eval(hits[-1]).items()}, f"log:{p.name}"
    return {}, "none"


def production_baseline(db_path: Path, log_dir: Path) -> tuple[dict, str]:
    try:
        import duckdb

        con = duckdb.connect(str(db_path), read_only=True)
        try:
            rows = con.execute("SELECT table_name, rows, freshness_value FROM ingest_meta").fetchall()
        finally:
            con.close()
        return {t: (int(r), f) for t, r, f in rows}, "ingest_meta"
    except Exception:  # noqa: BLE001 - 網頁持有資料庫時讀不到,改用 log
        return baseline_from_logs(log_dir)


def dry_run(baseline: dict) -> list[dict]:
    import duckdb

    from backend.db import ingest

    conn = duckdb.connect()
    ingest._ensure_ingest_meta_schema(conn)
    for table, (rows, fresh) in baseline.items():
        conn.execute("INSERT OR REPLACE INTO ingest_meta VALUES (?, ?, CURRENT_TIMESTAMP, ?, 'baseline')",
                     [table, rows, fresh])
    original = ingest.get_conn
    ingest.get_conn = lambda *a, **k: conn
    results = []
    try:
        for table, fn in STEPS:
            t0 = time.time()
            base_rows, base_fresh = baseline.get(table, (None, None))
            rec = {"table": table, "baseline_rows": base_rows, "baseline_freshness": base_fresh}
            try:
                rec["rows"] = int(getattr(ingest, fn)())
                rec["freshness"] = conn.execute(
                    "SELECT freshness_value FROM ingest_meta WHERE table_name = ?", [table]).fetchone()[0]
                rec["ok"] = True
                if table in TICKER_TABLES:
                    rec["tickers"], rec["dotted_ticker_rows"] = conn.execute(
                        f"SELECT COUNT(DISTINCT CAST(Ticker AS VARCHAR)), "
                        f"COUNT(*) FILTER (WHERE CAST(Ticker AS VARCHAR) LIKE '%.%') FROM {table}").fetchone()
                    if rec["dotted_ticker_rows"]:
                        rec["ok"], rec["error"] = False, f"{rec['dotted_ticker_rows']} 列代號帶小數點(例如 1441.0)"
            except Exception as exc:  # noqa: BLE001 - 正式匯入會在這裡失敗,預演要把原因記下來
                rec["ok"], rec["error"] = False, f"{type(exc).__name__}: {exc}"
            rec["seconds"] = round(time.time() - t0, 1)
            results.append(rec)
        ok = {r["table"] for r in results if r["ok"]}
        if {"daily_k", "financials"} <= ok:
            try:
                ingest._build_stock_list()
                results.append({"table": "stock_list", "ok": True,
                                "rows": conn.execute("SELECT COUNT(*) FROM stock_list").fetchone()[0]})
            except Exception as exc:  # noqa: BLE001
                results.append({"table": "stock_list", "ok": False, "error": f"{type(exc).__name__}: {exc}"})
    finally:
        ingest.get_conn = original
        conn.close()
    return results


def main(report_path: Path = REPORT) -> int:
    from backend.config import DUCKDB_PATH

    started = datetime.now()
    baseline, source = production_baseline(Path(DUCKDB_PATH), BASE / "logs")
    results = dry_run(baseline)
    verdict = "PASS" if all(r["ok"] for r in results) else "FAIL"
    report = {"verdict": verdict, "started_at": started.isoformat(timespec="seconds"),
              "finished_at": datetime.now().isoformat(timespec="seconds"), "finished_ts": time.time(),
              "baseline_source": source, "tables": results}
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"匯入預演(基準:{source})")
    for r in results:
        detail = (f"{r.get('rows', 0):>10,} 列" + (f"(前次 {r['baseline_rows']:,})" if r.get("baseline_rows") else "")
                  + (f" 新鮮度 {r['freshness']}" if r.get("freshness") else "")
                  + (f" 代號 {r['tickers']}" if r.get("tickers") is not None else "")) if r["ok"] else r["error"]
        print(f"[{'OK' if r['ok'] else 'FAIL':4}] {r['table']:<10} {detail}")
    print(f"\n結論:{verdict}" + ("" if verdict == "PASS" else " — 下一次排程匯入會失敗,先修資料或匯入程式再讓排程跑"))
    print(f"報告:{report_path}")
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    raise SystemExit(main())
