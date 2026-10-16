"""Notify the user that a Codex task is ready for review."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))


def _git_value(args: list[str]) -> str:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=BASE_DIR,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except Exception:
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def _default_message(summary: str, status: str) -> str:
    branch = _git_value(["branch", "--show-current"]) or "unknown"
    commit = _git_value(["log", "--oneline", "-n", "1"]) or "no commit"
    lines = [
        f"Status: {status}",
        f"Repo: {BASE_DIR}",
        f"Branch: {branch}",
        f"Latest commit: {commit}",
    ]
    if summary:
        lines.insert(1, f"Summary: {summary}")
    return "\n".join(lines)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Send a Codex completion ntfy notification.")
    parser.add_argument("--summary", default="", help="Short human-readable completion summary.")
    parser.add_argument("--status", default="DONE", help="Completion status label.")
    parser.add_argument("--title", default="", help="Override ntfy title.")
    parser.add_argument("--priority", default="default")
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    from scripts.notify_ntfy import load_ntfy_settings, send_ntfy

    title = args.title or f"Codex {args.status}: F:\\stock"
    message = _default_message(args.summary, args.status)
    settings = load_ntfy_settings()

    if args.dry_run:
        print(
            {
                "configured": settings is not None,
                "title": title,
                "message": message,
                "priority": args.priority,
            }
        )
        return 0 if settings is not None or not args.strict else 1

    try:
        result = send_ntfy(
            title=title,
            message=message,
            priority=args.priority,
            tags="bell",
            settings=settings,
        )
    except Exception as exc:
        print(f"[codex-ntfy] failed: {exc}")
        return 1 if args.strict else 0

    status = result.get("status")
    if status == "sent":
        print("[codex-ntfy] sent")
    else:
        print(f"[codex-ntfy] skipped: {result.get('reason') or result}")
    return 0 if status == "sent" or not args.strict else 1


if __name__ == "__main__":
    raise SystemExit(main())
