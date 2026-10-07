"""Run and publish the daily KOL dashboards without stale fallbacks."""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from datetime import date, datetime
from pathlib import Path
from typing import Sequence


BASE_DIR = Path(__file__).resolve().parents[1]
LOG_PATH = BASE_DIR / "logs" / "kol_tracker.log"
PUBLISH_REPO = Path(os.environ.get("KOL_PUBLISH_REPO", r"F:\kol-watch"))
KOL_ARTIFACT = BASE_DIR / "ml" / "reports" / "kol_dashboard_latest.html"
ENTRY_ARTIFACT = BASE_DIR / "ml" / "reports" / "entry_dashboard_latest.html"
KOL_STEPS: tuple[tuple[str, tuple[str, ...], int], ...] = (
    ("Classify KOL posts", (sys.executable, "-X", "utf8", "scripts/kol_classify.py"), 900),
    ("Build KOL dashboard", (sys.executable, "-X", "utf8", "scripts/kol_dashboard.py"), 600),
)
ENTRY_STEPS: tuple[tuple[str, tuple[str, ...], int], ...] = (
    (
        "Backfill entry tracker",
        (sys.executable, "-X", "utf8", "scripts/entry_filter_tracker.py", "backfill"),
        900,
    ),
    (   # 2026-10-07 起:舊主 lane 規則(模型過濾+分數)的影子名單,獨立帳本並行 ≥60 日
        "Backfill legacy-main shadow tracker",
        (sys.executable, "-X", "utf8", "scripts/entry_filter_tracker.py", "backfill", "--legacy"),
        900,
    ),
    ("Build entry dashboard", (sys.executable, "-X", "utf8", "scripts/entry_dashboard.py"), 600),
)


def _append_log(message: str, *, log_path: Path = LOG_PATH) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(message.rstrip() + "\n")


def _run_step(
    name: str,
    command: Sequence[str],
    timeout: int,
    *,
    log_path: Path = LOG_PATH,
) -> None:
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    try:
        result = subprocess.run(
            list(command),
            cwd=BASE_DIR,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout or ""
        if isinstance(output, bytes):
            output = output.decode("utf-8", errors="replace")
        if output:
            _append_log(output, log_path=log_path)
        raise RuntimeError(f"{name} timed out after {timeout}s") from exc

    if result.stdout:
        _append_log(result.stdout, log_path=log_path)
    if result.returncode != 0:
        raise RuntimeError(f"{name} failed with exit code {result.returncode}")


def _validate_artifacts(
    artifacts: Sequence[Path],
    *,
    not_before_ns: int,
    minimum_bytes: int = 1024,
) -> None:
    for artifact in artifacts:
        if not artifact.is_file():
            raise RuntimeError(f"dashboard artifact missing: {artifact}")
        stat = artifact.stat()
        if stat.st_size < minimum_bytes:
            raise RuntimeError(f"dashboard artifact too small: {artifact} ({stat.st_size} bytes)")
        if stat.st_mtime_ns < not_before_ns:
            stamp = datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds")
            raise RuntimeError(f"dashboard artifact is stale: {artifact} (mtime={stamp})")


def _copy_atomic(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
    try:
        shutil.copy2(source, temporary)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _run_git(args: Sequence[str], *, log_path: Path = LOG_PATH) -> None:
    result = subprocess.run(
        ["git", "-C", str(PUBLISH_REPO), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.stdout:
        _append_log(result.stdout, log_path=log_path)
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed with exit code {result.returncode}")


def _publish(artifacts: Sequence[Path], *, log_path: Path = LOG_PATH) -> None:
    if not (PUBLISH_REPO / ".git").exists():
        raise RuntimeError(f"publish repository unavailable: {PUBLISH_REPO}")

    names = [artifact.name for artifact in artifacts]
    for artifact in artifacts:
        _copy_atomic(artifact, PUBLISH_REPO / artifact.name)

    status = subprocess.run(
        ["git", "-C", str(PUBLISH_REPO), "status", "--porcelain", "--", *names],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if status.returncode != 0:
        raise RuntimeError(f"git status failed with exit code {status.returncode}")
    if status.stdout.strip():
        _run_git(["add", "--", *names], log_path=log_path)
        _run_git(
            ["commit", "--only", "-m", f"auto: dashboard {date.today():%Y-%m-%d}", "--", *names],
            log_path=log_path,
        )
    else:
        _append_log("dashboard artifacts unchanged; no commit created", log_path=log_path)

    # Always push so a prior transient network failure can recover on the next run.
    _run_git(["push"], log_path=log_path)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--entry-only",
        action="store_true",
        help="Build and publish only the entry dashboard after Phase-2 predictions.",
    )
    args = parser.parse_args(argv)
    steps = ENTRY_STEPS if args.entry_only else KOL_STEPS
    artifacts = (ENTRY_ARTIFACT,) if args.entry_only else (KOL_ARTIFACT,)
    label = "entry dashboard" if args.entry_only else "kol dashboard"
    started_ns = time.time_ns() - 2_000_000_000
    _append_log(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {label} start")
    try:
        for name, command, timeout in steps:
            _append_log(f"[step] {name}")
            _run_step(name, command, timeout)
        _validate_artifacts(artifacts, not_before_ns=started_ns)
        _publish(artifacts)
    except Exception as exc:  # noqa: BLE001 - every scheduler failure must become nonzero.
        _append_log(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {label} FAILED: {exc}")
        return 1

    _append_log(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {label} done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
