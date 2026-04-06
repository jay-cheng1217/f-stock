from __future__ import annotations

import atexit
import json
import os
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass
class LockHandle:
    path: Path

    def release(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            return


def _pid_exists(pid: int) -> bool:
    if pid <= 0:
        return False

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _read_lock(path: Path) -> dict:
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def acquire_lock(
    lock_path: str,
    label: str,
    stale_after_seconds: int = 12 * 60 * 60,
) -> tuple[LockHandle | None, dict | None]:
    path = Path(lock_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "label": label,
        "pid": os.getpid(),
        "started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }

    while True:
        try:
            fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            existing = _read_lock(path)
            existing_pid = int(existing.get("pid") or 0)

            try:
                age_seconds = time.time() - path.stat().st_mtime
            except FileNotFoundError:
                continue

            if existing_pid and _pid_exists(existing_pid):
                return None, existing

            if age_seconds < stale_after_seconds and not existing:
                return None, existing

            try:
                path.unlink()
            except FileNotFoundError:
                continue
            continue

        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)

        handle = LockHandle(path=path)
        atexit.register(handle.release)
        return handle, payload
