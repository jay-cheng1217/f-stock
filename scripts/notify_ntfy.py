"""Small ntfy notification helper for local jobs and agent completions."""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parents[1]
DEFAULT_SERVER = "https://ntfy.sh"
DEFAULT_TIMEOUT_SECONDS = 10


@dataclass(frozen=True)
class NtfySettings:
    url: str
    token: str = ""
    username: str = ""
    password: str = ""
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
    enabled: bool = True


def _parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _env_value(key: str, env_map: dict[str, str], default: str = "") -> str:
    value = os.environ.get(key)
    if value is not None and value != "":
        return value
    return env_map.get(key, default)


def _as_bool(value: str, default: bool = True) -> bool:
    if value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _as_int(value: str, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _build_topic_url(server: str, topic: str) -> str:
    server = (server or DEFAULT_SERVER).rstrip("/")
    topic = topic.strip().strip("/")
    if not topic:
        return ""
    return f"{server}/{urllib.parse.quote(topic, safe='')}"


def load_ntfy_settings() -> NtfySettings | None:
    """Load ntfy settings from .env.ntfy, then .env, then process env.

    Minimal setup:
        NTFY_TOPIC=your-private-topic

    Optional:
        NTFY_SERVER=https://ntfy.sh
        NTFY_TOKEN=...
        NTFY_USERNAME=...
        NTFY_PASSWORD=...
        NTFY_TIMEOUT_SECONDS=10
        NTFY_ENABLED=true
    """
    env_map: dict[str, str] = {}
    for path in (BASE_DIR / ".env.ntfy", BASE_DIR / ".env"):
        env_map.update(_parse_env_file(path))

    enabled = _as_bool(_env_value("NTFY_ENABLED", env_map, "true"), default=True)
    if not enabled:
        return None

    direct_url = _env_value("NTFY_URL", env_map)
    topic = _env_value("NTFY_TOPIC", env_map)
    server = _env_value("NTFY_SERVER", env_map, DEFAULT_SERVER)
    url = direct_url.strip() if direct_url else _build_topic_url(server, topic)
    if not url:
        return None

    return NtfySettings(
        url=url,
        token=_env_value("NTFY_TOKEN", env_map),
        username=_env_value("NTFY_USERNAME", env_map),
        password=_env_value("NTFY_PASSWORD", env_map),
        timeout_seconds=_as_int(
            _env_value("NTFY_TIMEOUT_SECONDS", env_map, str(DEFAULT_TIMEOUT_SECONDS)),
            DEFAULT_TIMEOUT_SECONDS,
        ),
        enabled=True,
    )


def _auth_header(settings: NtfySettings) -> str | None:
    if settings.token:
        return f"Bearer {settings.token}"
    if settings.username and settings.password:
        raw = f"{settings.username}:{settings.password}".encode("utf-8")
        return "Basic " + base64.b64encode(raw).decode("ascii")
    return None


def send_ntfy(
    *,
    title: str,
    message: str,
    priority: str = "default",
    tags: str = "",
    click: str = "",
    settings: NtfySettings | None = None,
) -> dict[str, Any]:
    settings = settings or load_ntfy_settings()
    if settings is None:
        return {"status": "skipped", "reason": "ntfy not configured"}

    headers = {
        "Title": title,
        "Priority": priority,
    }
    if tags:
        headers["Tags"] = tags
    if click:
        headers["Click"] = click
    auth = _auth_header(settings)
    if auth:
        headers["Authorization"] = auth

    request = urllib.request.Request(
        settings.url,
        data=message.encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=settings.timeout_seconds) as response:
        body = response.read().decode("utf-8", errors="replace")
        try:
            parsed_body: Any = json.loads(body) if body else None
        except json.JSONDecodeError:
            parsed_body = body
        return {
            "status": "sent",
            "http_status": int(getattr(response, "status", 0) or 0),
            "response": parsed_body,
        }


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Send an optional ntfy notification.")
    parser.add_argument("--title", default="Stock ML notification")
    parser.add_argument("--message", default="")
    parser.add_argument("--priority", default="default")
    parser.add_argument("--tags", default="")
    parser.add_argument("--click", default="")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero when ntfy is not configured or delivery fails.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    message = args.message or sys.stdin.read().strip()
    settings = load_ntfy_settings()

    if args.dry_run:
        payload = {
            "configured": settings is not None,
            "url": settings.url if settings else None,
            "title": args.title,
            "message": message,
            "priority": args.priority,
            "tags": args.tags,
            "click": args.click,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if settings is not None or not args.strict else 1

    try:
        result = send_ntfy(
            title=args.title,
            message=message,
            priority=args.priority,
            tags=args.tags,
            click=args.click,
            settings=settings,
        )
    except (OSError, urllib.error.URLError) as exc:
        print(f"[ntfy] failed: {exc}", file=sys.stderr)
        return 1 if args.strict else 0

    print(json.dumps(result, ensure_ascii=False))
    if args.strict and result.get("status") != "sent":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
