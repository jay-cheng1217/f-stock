"""排程任務統一 runner — timeout + 失敗告警 + 結構化記錄.

設計原則：
- **不改 stdout/stderr 串流行為**：原本 subprocess.run(cmd, cwd=..., check=True) 會把
  子行程輸出直接打到終端 / log file（透過 redirect），這個 helper 維持相同行為，
  避免破壞既有 log 收集流程。
- **Timeout 必填**：避免 TWSE/MOPS/FinMind 半夜 hang 死整支 pipeline。
- **失敗時觸發 ntfy 告警**（如 .env.ntfy 已設定），訊息只放 job 名稱、退出碼、
  耗時、提示去看 ``logs/smart_update_auto_YYYYMMDD.log``，不嘗試夾帶 stderr。
- **回傳結構化 dict**（而非 raise CalledProcessError），caller 自己決定要不要繼續。

預設 timeout 表（單位：秒）：
- ``data_fetch``     : 1200  資料抓取（MOPS/TDCC 等）
- ``twstock_full``   : 14400 台股全量更新；保留 23:00→05:00 Phase-1/2 邊界
- ``twstock_daily``  : 7200  每日台股資料更新；跳過月營收全量補抓
- ``valuation``      : 600
- ``news``           : 300
- ``ingest``         : 900   CSV → DuckDB
- ``train``          : 3600  V1+V2 訓練
- ``train_t1``       : 5400
- ``predict``        : 3600  V1/V2 prediction can take 20-40 min on scheduled runs
- ``verify``         : 300
- ``shadow_overlay`` : 600
- ``default``        : 600
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from typing import Any

DEFAULT_TIMEOUTS = {
    "data_fetch": 1200,
    "twstock_full": 14400,
    "twstock_daily": 7200,
    "valuation": 600,
    "news": 300,
    "ingest": 900,
    "train": 3600,
    "train_t1": 5400,
    "predict": 3600,
    "verify": 300,
    "shadow_overlay": 600,
    "default": 600,
}

SCHEDULED_STDIO_LOG_ENV = "STOCK_SCHEDULED_STDIO_LOG"


def _open_scheduled_stdio_log(name: str):
    path = os.environ.get(SCHEDULED_STDIO_LOG_ENV)
    if not path:
        return None
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        handle = open(path, "a", encoding="utf-8", errors="replace")
        handle.write(f"\n[job_runner:{name}] child output start\n")
        handle.flush()
        return handle
    except Exception:
        return None


def _send_failure_alert(name: str, reason: str, elapsed_sec: float, returncode: int | None) -> None:
    """非阻塞送 ntfy；失敗就吞掉，不影響 caller。"""
    try:
        from scripts.notify_ntfy import send_ntfy
        from datetime import datetime
        log_hint = f"logs/smart_update_auto_{datetime.now().strftime('%Y%m%d')}.log"
        rc_str = f"rc={returncode}" if returncode is not None else "no-rc"
        msg = (
            f"job={name}\nreason={reason}\n{rc_str}\n"
            f"elapsed={elapsed_sec/60:.1f} min\n"
            f"see {log_hint}"
        )
        send_ntfy(
            title=f"[stock pipeline] {name} FAILED",
            message=msg,
            priority="high",
            tags="warning",
        )
    except Exception:
        # 通知失敗不能拖累主流程
        pass


def run_job(
    name: str,
    cmd: list[str],
    *,
    timeout: int | str = "default",
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    raise_on_fail: bool = False,
) -> dict[str, Any]:
    """跑外部命令，固定有 timeout、失敗時送 ntfy 告警。

    Parameters
    ----------
    name : str
        Job 識別名，用於 log / ntfy 訊息。
    cmd : list[str]
        要執行的命令（ ``sys.executable`` 等請由 caller 自己準備）。
    timeout : int | str
        秒數；或預設表的 key（``data_fetch``/``train``/``predict`` 等）。
    cwd : str | None
        工作目錄，預設 STOCK_BASE_DIR 或 caller cwd。
    env : dict[str, str] | None
        環境變數覆寫；None 用 inherit。
    raise_on_fail : bool
        True → 失敗時 raise；False → 回傳 dict，caller 自決。

    Returns
    -------
    dict with keys: ``name`` ``status`` (``ok``/``timeout``/``error``)
    ``returncode`` ``elapsed_sec`` ``cmd``。
    """
    if isinstance(timeout, str):
        timeout_sec = DEFAULT_TIMEOUTS.get(timeout, DEFAULT_TIMEOUTS["default"])
    else:
        timeout_sec = int(timeout)

    if cwd is None:
        cwd = os.environ.get("STOCK_BASE_DIR")

    start = time.time()
    print(f"[job_runner] start: {name}  (timeout={timeout_sec}s)")
    stdio_log = _open_scheduled_stdio_log(name)
    stdio_kwargs = (
        {"stdout": stdio_log, "stderr": subprocess.STDOUT}
        if stdio_log is not None
        else {}
    )

    try:
        try:
            result = subprocess.run(
                cmd, cwd=cwd, env=env,
                timeout=timeout_sec,
                check=False,  # 我們自己判斷，避免 caller 在不同地方 try CalledProcessError
                **stdio_kwargs,
            )
        except subprocess.TimeoutExpired as exc:
            elapsed = time.time() - start
            print(f"[job_runner] TIMEOUT after {timeout_sec}s: {name}")
            _send_failure_alert(name, "timeout", elapsed, None)
            if raise_on_fail:
                raise
            return {"name": name, "status": "timeout", "returncode": None,
                    "elapsed_sec": round(elapsed, 1), "cmd": cmd}
        except FileNotFoundError as exc:
            elapsed = time.time() - start
            print(f"[job_runner] EXEC NOT FOUND: {name}: {exc}")
            _send_failure_alert(name, f"exec-not-found: {exc}", elapsed, None)
            if raise_on_fail:
                raise
            return {"name": name, "status": "error", "returncode": None,
                    "elapsed_sec": round(elapsed, 1), "cmd": cmd}

        elapsed = time.time() - start
        if result.returncode == 0:
            print(f"[job_runner] OK: {name}  ({elapsed/60:.1f} min)")
            return {"name": name, "status": "ok", "returncode": 0,
                    "elapsed_sec": round(elapsed, 1), "cmd": cmd}

        print(f"[job_runner] FAIL rc={result.returncode}: {name}  ({elapsed/60:.1f} min)")
        _send_failure_alert(name, "non-zero exit", elapsed, result.returncode)
        if raise_on_fail:
            raise subprocess.CalledProcessError(result.returncode, cmd)
        return {"name": name, "status": "error", "returncode": result.returncode,
                "elapsed_sec": round(elapsed, 1), "cmd": cmd}
    finally:
        if stdio_log is not None:
            try:
                stdio_log.write(f"[job_runner:{name}] child output end\n")
                stdio_log.close()
            except Exception:
                pass
