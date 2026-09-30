# -*- coding: utf-8 -*-
"""重訓 runner(release/reconnect 版):不殺 web,改用 /api/db/release 解 DuckDB 鎖。

背景:run_weekly_retrain_guard 以 taskkill 停 web;本機 web 由排程以較高權限啟動,
taskkill 會「存取被拒」(2026-07-02 DATA-PR-009 Stage1 首啟失敗)。夜間 pipeline 的
正規做法是呼叫 web 的 /api/db/release 釋放連線、跑完 /api/db/reconnect —— 本 runner
沿用該機制,web 全程存活。

流程:release → daily_pipeline --retrain --force --retrain-v2-only → reconnect。
"""
from __future__ import annotations

import os
import subprocess
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from scripts.smart_update import _api_post  # noqa: E402


def main() -> int:
    released = _api_post("/api/db/release", timeout=30)
    print(f"[retrain-runner] /api/db/release → {released}")

    try:
        cmd = [
            sys.executable,
            os.path.join(BASE_DIR, "scripts", "daily_pipeline.py"),
            "--retrain", "--force", "--retrain-v2-only",
        ]
        print("[retrain-runner] run:", " ".join(cmd), flush=True)
        rc = subprocess.run(cmd, cwd=BASE_DIR, check=False).returncode
        print(f"[retrain-runner] pipeline exit={rc}")
        return rc
    finally:
        reconnected = _api_post("/api/db/reconnect", timeout=30)
        print(f"[retrain-runner] /api/db/reconnect → {reconnected}")


if __name__ == "__main__":
    raise SystemExit(main())
