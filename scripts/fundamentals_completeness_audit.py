# -*- coding: utf-8 -*-
"""財報資料完整性稽核(2026Q1 殘檔凍結事故後建立,2026-07-16)。

稽核對象與規則(申報行事曆感知):
  月營收  月營收/revenue_*.csv   每月10日截止+2日寬限後,最新月覆蓋 >= 前月80%
  季EPS   季報財務/eps_YQq.csv    截止(Q1:5/15 Q2:8/14 Q3:11/14 Q4:3/31)+3日寬限後,
  損益表  季報財務/financial_*    檔案存在且列數 >= 前季80%
  資產負債 資產負債/bs_*.csv       同上

--heal:偵測到缺口時自動呼叫對應回補腳本(backfill_eps / twstock --step 8 /
backfill_balance_sheet / twstock --retry-revenue)後重新稽核。
月營收缺口不能只靠夜間 step6 自癒:step6 gate 在每月 11-15 公布視窗,若視窗內抓取
中斷(2026-08-16 事故:2026-07 覆蓋卡死 468/1893 十三天),視窗過後永遠跳過、
警報天天響但無人回補——故月營收缺口必須由此處 heal(--retry-revenue 不受視窗限制,
只補最新月缺漏的股票)。

用法:
  python scripts/fundamentals_completeness_audit.py [--heal]
importable:run_audit(heal=False) -> list[str](缺口清單,空=健康)
"""
from __future__ import annotations

import argparse
import csv
import glob
import io
import json
import math
import os
import subprocess
import time
import sys
from datetime import date, timedelta

BASE_DIR = os.environ.get("STOCK_BASE_DIR") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REVENUE_DIR = os.path.join(BASE_DIR, "月營收")
FIN_DIR = os.path.join(BASE_DIR, "季報財務")
BS_DIR = os.path.join(BASE_DIR, "資產負債")
COMPLETENESS_RATIO = 0.8

# 季報申報截止(月, 日) → 該截止日公布的是哪一季
QUARTER_DEADLINES = [
    ((3, 31), lambda y: (y - 1, 4)),   # 年報/Q4:3/31 公布前一年 Q4
    ((5, 15), lambda y: (y, 1)),
    ((8, 14), lambda y: (y, 2)),
    ((11, 14), lambda y: (y, 3)),
]
GRACE_DAYS_QUARTER = 3
GRACE_DAYS_MONTH = 2


def _csv_rows(path: str) -> int:
    try:
        with open(path, encoding="utf-8-sig", errors="replace") as fh:
            return max(sum(1 for _ in fh) - 1, 0)
    except OSError:
        return 0


def _latest_due_quarter(today: date) -> tuple[int, int]:
    """回傳今天「應該已完整」的最新季度(含寬限)。"""
    best = None
    for y in (today.year, today.year - 1):
        for (m, d), resolve in QUARTER_DEADLINES:
            deadline = date(y, m, d) + timedelta(days=GRACE_DAYS_QUARTER)
            if deadline <= today:
                q = resolve(y)
                if best is None or q > best:
                    best = q
    return best  # e.g. (2026, 1)


def _prev_quarter(y: int, q: int) -> tuple[int, int]:
    return (y - 1, 4) if q == 1 else (y, q - 1)


def _check_quarterly(label: str, dir_path: str, prefix: str, yq: tuple[int, int]) -> str | None:
    y, q = yq
    cur = os.path.join(dir_path, f"{prefix}{y}Q{q}.csv")
    py, pq = _prev_quarter(y, q)
    prev = os.path.join(dir_path, f"{prefix}{py}Q{pq}.csv")
    cur_rows, prev_rows = _csv_rows(cur), _csv_rows(prev)
    if cur_rows == 0:
        return f"{label} {y}Q{q} 缺檔(申報已截止)"
    content_gap = _quarter_content_gap(cur, prefix, yq)
    if content_gap:
        return f"{label} {y}Q{q} {content_gap}"
    if prev_rows and cur_rows < prev_rows * COMPLETENESS_RATIO:
        return f"{label} {y}Q{q} 殘檔({cur_rows} 列 < 前季 {prev_rows} 的 {COMPLETENESS_RATIO:.0%})"
    return None


def _quarter_content_gap(path: str, prefix: str, yq: tuple[int, int]) -> str | None:
    """Counts alone cannot certify a dated, complete two-market quarter."""
    import pandas as pd

    metrics = {"eps_": "EPS_Basic", "financial_": "Operating_Margin_Pct", "bs_": "Total_Assets"}
    metric = metrics[prefix]
    try:
        frame = pd.read_csv(path, dtype={"Ticker": str})
        required = {"Ticker", "Market", "Year", "Season", metric}
        if not required.issubset(frame.columns):
            return f"必要欄位缺失: {sorted(required - set(frame.columns))}"
        if frame.empty:
            return "空資料"
        if frame["Ticker"].isna().any() or frame["Ticker"].str.strip().eq("").any() or frame["Ticker"].duplicated().any():
            return "股票代碼空白或重複"
        if not {"TWSE", "OTC"}.issubset(set(frame["Market"].dropna())):
            return "雙市場未到齊"
        if not pd.to_numeric(frame["Year"], errors="coerce").eq(yq[0]).all() or not pd.to_numeric(frame["Season"], errors="coerce").eq(yq[1]).all():
            return "內容期別與檔名不一致"
        values = pd.to_numeric(frame[metric], errors="coerce")
        for market in ("TWSE", "OTC"):
            if not values[frame["Market"].eq(market)].map(lambda value: math.isfinite(value)).any():
                return f"{market} 必要數值 {metric} 全缺"
    except (OSError, ValueError, pd.errors.ParserError) as exc:
        return f"內容無法讀取: {type(exc).__name__}"
    return None


def _tail_line(path: str, max_bytes: int = 400) -> str:
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - max_bytes))
            chunk = fh.read().decode("utf-8", "replace")
        lines = [ln for ln in chunk.strip().splitlines() if ln.strip()]
        return lines[-1] if lines else ""
    except OSError:
        return ""


def _check_monthly_revenue(today: date) -> str | None:
    """最新應公布月份的覆蓋數 >= 前月覆蓋數的 80%。"""
    if today.day >= 10 + GRACE_DAYS_MONTH:
        due = date(today.year, today.month, 1) - timedelta(days=1)  # 上月
    else:
        first = date(today.year, today.month, 1)
        due = (first - timedelta(days=1)).replace(day=1) - timedelta(days=1)  # 上上月
    due_key = f"{due.year}-{due.month:02d}"
    prev = (due.replace(day=1) - timedelta(days=1))
    prev_key = f"{prev.year}-{prev.month:02d}"

    due_n = prev_n = 0
    for f in glob.glob(os.path.join(REVENUE_DIR, "revenue_*.csv")):
        # Early reporters may already have next month's row. Inspect the actual
        # due/prior rows; neither the last line nor due implies prior exists.
        available = set()
        seen = set()
        try:
            with open(f, newline="", encoding="utf-8-sig") as stream:
                reader = csv.DictReader(stream)
                if not {"Date", "Monthly_Revenue"}.issubset(reader.fieldnames or []):
                    return f"月營收 必要欄位缺失: {os.path.basename(f)}"
                for row in reader:
                    period = str(row.get("Date", ""))[:7]
                    if period not in {due_key, prev_key}:
                        continue
                    if period in seen:
                        return f"月營收 期別重複: {os.path.basename(f)} {period}"
                    seen.add(period)
                    if math.isfinite(float(row.get("Monthly_Revenue", ""))):
                        available.add(period)
        except (OSError, ValueError, csv.Error):
            return f"月營收 內容無法驗證: {os.path.basename(f)}"
        due_n += due_key in available
        prev_n += prev_key in available
    if prev_n == 0:
        return f"月營收 {prev_key} 全缺(異常)"
    if due_n < prev_n * COMPLETENESS_RATIO:
        return f"月營收 {due_key} 覆蓋不足({due_n}/{prev_n} 檔,截止 {due.month+1 if due.month<12 else 1}/10 已過)"
    return None


HEAL_RETRY_ATTEMPTS = 3
HEAL_RETRY_DELAY_SEC = 30

# label -> (cmd, timeout_sec)。月營收 retry 走 MOPS 逐檔(每檔 ~5-8 秒),
# 全 universe 缺口約 2-3 小時,故給 4 小時;季報回補維持原 30 分鐘。
HEAL_COMMANDS = {
    "季EPS": ([sys.executable, os.path.join(BASE_DIR, "scripts", "backfill_eps.py")], 1800),
    "損益表": ([sys.executable, os.path.join(BASE_DIR, "twstock.py"), "--step", "8"], 1800),
    "資產負債表": ([sys.executable, os.path.join(BASE_DIR, "scripts", "backfill_balance_sheet.py")], 1800),
    "月營收": ([sys.executable, os.path.join(BASE_DIR, "twstock.py"), "--retry-revenue"], 14400),
}


def _check_tdcc_freshness(today: date) -> str | None:
    """TDCC 週快照新鮮度。

    集保週資料在**週末**才發布(資料日=當週最後營業日,通常週五;遇假日落週四)。
    因此:週一起應已有「上一個完成週」的檔案;週五晚上還是上週資料屬正常。
    抓取步驟本身只印「Raw file already exists」就回報成功(無期別驗證),
    若 API 凍結或斷更不會有任何告警——本檢查即為該缺陷的守門。
    """
    files = sorted(glob.glob(os.path.join(BASE_DIR, "集保分散", "tdcc_2*.csv")))
    if not files:
        return "TDCC 無任何週檔"
    try:
        latest = date.fromisoformat(
            f"{os.path.basename(files[-1])[5:9]}-{os.path.basename(files[-1])[9:11]}-{os.path.basename(files[-1])[11:13]}"
        )
    except ValueError:
        return f"TDCC 最新檔名無法解析: {os.path.basename(files[-1])}"

    # 最近一個「已完成週」的週五(週一~週日都以上一個週五為準)
    last_friday = today - timedelta(days=(today.weekday() - 4) % 7 or 7)
    # 遇假日資料日會落當週週四,故容忍 last_friday 前 3 天
    if latest < last_friday - timedelta(days=3):
        return (
            f"TDCC 週快照過期: 最新 {latest},應已有 {last_friday} 當週資料"
            "(抓取步驟無期別驗證,可能 API 凍結或斷更)"
        )
    return None


def _check_semantic_invariants() -> list[str]:
    """語意不變量:抓「數字看起來正常但意義錯了」的 bug(TDCC 六年錯置事故疫苗)。

    原則:挑幾個外部常識鐵律,錯置時必然爆炸性違反。"""
    gaps: list[str] = []
    tdcc_path = os.path.join(BASE_DIR, "集保分散", "tdcc_summary.csv")
    try:
        import csv as _csv

        latest: dict[str, dict] = {}
        with open(tdcc_path, encoding="utf-8-sig") as fh:
            for row in _csv.DictReader(fh):
                if row.get("Ticker") in ("2330", "2317"):
                    cur = latest.get(row["Ticker"])
                    if cur is None or str(row.get("Date")) > str(cur.get("Date")):
                        latest[row["Ticker"]] = row
        w2330 = float(latest.get("2330", {}).get("Whale_Pct") or 0)
        if not math.isfinite(w2330) or w2330 < 50:
            gaps.append(
                f"TDCC 語意不變量違反: 2330 千張大戶僅 {w2330}%(常識應 >50%),疑似級距錯置")
        for tk, row in latest.items():
            r = float(row.get("Retail_Pct") or 0)
            w = float(row.get("Whale_Pct") or 0)
            if r + w > 100.5:
                gaps.append(f"TDCC 語意不變量違反: {tk} 散戶+大戶 = {r + w:.1f}% > 100%")
    except OSError:
        gaps.append("TDCC summary 缺檔,無法做語意檢查")
    except Exception as exc:  # noqa: BLE001
        gaps.append(f"TDCC 語意檢查執行失敗: {exc}")
    return gaps


def run_audit(heal: bool = False, today: date | None = None) -> list[str]:
    today = today or date.today()
    yq = _latest_due_quarter(today)

    def _collect() -> list[str]:
        gaps = []
        for label, dir_path, prefix in (
            ("季EPS", FIN_DIR, "eps_"),
            ("損益表", FIN_DIR, "financial_"),
            ("資產負債表", BS_DIR, "bs_"),
        ):
            gap = _check_quarterly(label, dir_path, prefix, yq)
            if gap:
                gaps.append(gap)
            # 全季歷史掃描(2025Q4 缺 301 檔在「只查最新季」下隱形的教訓):
            # 歷史季 vs 前一季門檻 90%(公司下市自然衰減不會超過 10%)
            prev_rows = None
            y0, q0 = 2020, 1
            while (y0, q0) <= yq:
                p = os.path.join(dir_path, f"{prefix}{y0}Q{q0}.csv")
                rows = _csv_rows(p)
                if (y0, q0) != yq:
                    if not rows:
                        gaps.append(f"{label} {y0}Q{q0} 歷史缺檔或空資料")
                    else:
                        content_gap = _quarter_content_gap(p, prefix, (y0, q0))
                        if content_gap:
                            gaps.append(f"{label} {y0}Q{q0} {content_gap}")
                if rows and prev_rows and rows < prev_rows * 0.9 and (y0, q0) != yq:
                    gaps.append(f"{label} {y0}Q{q0} 歷史殘檔({rows} 列 < 前季 {prev_rows} 的 90%)")
                if rows:
                    prev_rows = rows
                y0, q0 = (y0 + 1, 1) if q0 == 4 else (y0, q0 + 1)
        m_gap = _check_monthly_revenue(today)
        if m_gap:
            gaps.append(m_gap)
        t_gap = _check_tdcc_freshness(today)
        if t_gap:
            gaps.append(t_gap)
        gaps.extend(_check_semantic_invariants())
        return gaps

    gaps = _collect()
    refresh_results = []
    if heal:
        # The prior 80% coverage test cannot detect missing current reporters.
        # Always refresh all due quarterly sources, even when _collect is green.
        labels = {"季EPS", "損益表", "資產負債表"}
        labels.update(label for label in HEAL_COMMANDS if any(g.startswith(label) for g in gaps))
        refresh_failures = []
        for label, (cmd, timeout_sec) in HEAL_COMMANDS.items():
            if label not in labels:
                continue
            print(f"[fundamentals-audit] auto-heal: {label} -> {' '.join(os.path.basename(c) for c in cmd)}")
            try:
                # Exchange open-data endpoints throw transient 5xx (TPEx 520 on
                # 2026-09-08 right after the pipeline's own EPS step succeeded);
                # retry before turning one bad response into a DATA GAPS alert.
                attempts = 0
                while True:
                    attempts += 1
                    completed = subprocess.run(cmd, cwd=BASE_DIR, timeout=timeout_sec, capture_output=True)
                    if completed.returncode == 0 or attempts >= HEAL_RETRY_ATTEMPTS:
                        break
                    print(f"[fundamentals-audit] {label} refresh exit {completed.returncode}; retry {attempts}/{HEAL_RETRY_ATTEMPTS - 1} in {HEAL_RETRY_DELAY_SEC}s")
                    time.sleep(HEAL_RETRY_DELAY_SEC)
                result = {"label": label, "returncode": completed.returncode, "attempts": attempts}
                refresh_results.append(result)
                if completed.returncode:
                    diagnostic = os.path.join(BASE_DIR, "logs", f"fundamental_refresh_{today:%Y%m%d}_{label}.json")
                    os.makedirs(os.path.dirname(diagnostic), exist_ok=True)
                    def _tail(value):
                        if isinstance(value, bytes):
                            value = value.decode("utf-8", errors="replace")
                        return str(value or "")[-16000:]
                    with open(diagnostic, "w", encoding="utf-8") as stream:
                        json.dump({"label": label, "returncode": completed.returncode,
                                   "stdout_tail": _tail(getattr(completed, "stdout", "")),
                                   "stderr_tail": _tail(getattr(completed, "stderr", ""))},
                                  stream, ensure_ascii=False, indent=2)
                    result["diagnostic_path"] = diagnostic
                    refresh_failures.append(f"{label} source refresh failed (exit {completed.returncode}); existing coverage does not prove current source completeness")
            except Exception as exc:  # noqa: BLE001
                refresh_results.append({"label": label, "error": str(exc)})
                refresh_failures.append(f"{label} source refresh failed: {exc}")
        gaps = _collect() + refresh_failures

    report = {
        "date": today.isoformat(),
        "due_quarter": f"{yq[0]}Q{yq[1]}",
        "gaps": gaps,
        "refresh_attempts": refresh_results,
        "source_refresh_policy": "always_latest_due" if heal else "read_only_no_network",
        "status": "OK" if not gaps else "GAPS",
    }
    out = os.path.join(BASE_DIR, "logs", f"fundamentals_audit_{today:%Y%m%d}.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=1)
    return gaps


def main() -> int:
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--heal", action="store_true")
    args = ap.parse_args()
    gaps = run_audit(heal=args.heal)
    if gaps:
        print("[fundamentals-audit] GAPS:")
        for g in gaps:
            print("  -", g)
        return 1
    print("[fundamentals-audit] OK: 月營收/季EPS/損益表/資產負債表 完整")
    return 0


if __name__ == "__main__":
    sys.exit(main())
