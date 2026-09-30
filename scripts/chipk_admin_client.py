"""Client for the local ChipK admin bridge."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any
from urllib import request


BASE_DIR = Path(__file__).resolve().parents[1]
DEFAULT_TOKEN_FILE = BASE_DIR / "tmp" / "chipk_admin_bridge" / "token.txt"


def _read_token(path: str) -> str:
    token_path = Path(path)
    if not token_path.exists():
        raise SystemExit(f"token file not found: {token_path}")
    return token_path.read_text(encoding="utf-8").strip()


def _call(method: str, url: str, token: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(url, data=data, method=method)
    req.add_header("X-ChipK-Bridge-Token", token)
    if data is not None:
        req.add_header("Content-Type", "application/json; charset=utf-8")
    with request.urlopen(req, timeout=10) as resp:  # noqa: S310 - localhost bridge only
        return json.loads(resp.read().decode("utf-8"))


def _action_from_args(args: argparse.Namespace) -> dict[str, Any]:
    if args.command == "click":
        return {"action": "click", "x": args.x, "y": args.y, "clicks": args.clicks, "button": args.button}
    if args.command == "move":
        return {"action": "move", "x": args.x, "y": args.y, "duration": args.duration}
    if args.command == "write":
        return {"action": "write", "text": args.text, "interval": args.interval}
    if args.command == "press":
        return {"action": "press", "key": args.key, "presses": args.presses, "interval": args.interval}
    if args.command == "hotkey":
        return {"action": "hotkey", "keys": args.keys}
    if args.command == "screenshot":
        return {"action": "screenshot", "name": args.name}
    raise SystemExit(f"unsupported command: {args.command}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Send actions to the local ChipK admin bridge")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--token-file", default=str(DEFAULT_TOKEN_FILE))
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("health")

    p_click = sub.add_parser("click")
    p_click.add_argument("x", type=int)
    p_click.add_argument("y", type=int)
    p_click.add_argument("--clicks", type=int, default=1)
    p_click.add_argument("--button", default="left")

    p_move = sub.add_parser("move")
    p_move.add_argument("x", type=int)
    p_move.add_argument("y", type=int)
    p_move.add_argument("--duration", type=float, default=0.05)

    p_write = sub.add_parser("write")
    p_write.add_argument("text")
    p_write.add_argument("--interval", type=float, default=0.02)

    p_press = sub.add_parser("press")
    p_press.add_argument("key")
    p_press.add_argument("--presses", type=int, default=1)
    p_press.add_argument("--interval", type=float, default=0.02)

    p_hotkey = sub.add_parser("hotkey")
    p_hotkey.add_argument("keys", nargs="+")

    p_screenshot = sub.add_parser("screenshot")
    p_screenshot.add_argument("--name", default="screenshot.png")

    p_sequence = sub.add_parser("sequence")
    p_sequence.add_argument("json_path")

    args = parser.parse_args()
    token = _read_token(args.token_file)
    base_url = f"http://{args.host}:{args.port}"
    if args.command == "health":
        result = _call("GET", f"{base_url}/health", token)
    elif args.command == "sequence":
        payload = json.loads(Path(args.json_path).read_text(encoding="utf-8"))
        if isinstance(payload, list):
            payload = {"actions": payload}
        result = _call("POST", f"{base_url}/action", token, payload)
    else:
        result = _call("POST", f"{base_url}/action", token, _action_from_args(args))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
