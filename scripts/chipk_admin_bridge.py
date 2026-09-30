"""Local elevated bridge for controlling the CMoney ChipK desktop app.

Run this process with Windows administrator privileges when ChipK/AppViewer is
also elevated.  The bridge binds to localhost only and requires a bearer-style
token for every request.  It does not read browser cookies, app credentials, or
CMoney session storage.
"""

from __future__ import annotations

import argparse
import base64
import ctypes
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = BASE_DIR / "tmp" / "chipk_admin_bridge"
MAX_TEXT_LEN = 128
MAX_ACTIONS = 50


def _is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _foreground_title() -> str:
    user32 = ctypes.windll.user32
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return ""
    length = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value


def _safe_name(value: str | None, default: str = "screenshot.png") -> str:
    name = (value or default).strip().replace("\\", "_").replace("/", "_")
    if not name:
        name = default
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")
    name = "".join(ch if ch in allowed else "_" for ch in name)
    if not name.lower().endswith(".png"):
        name += ".png"
    return name[:96]


def _screen_size() -> dict[str, int | None]:
    try:
        import pyautogui

        size = pyautogui.size()
        return {"width": int(size.width), "height": int(size.height)}
    except Exception:
        return {"width": None, "height": None}


def _do_action(action: dict[str, Any]) -> dict[str, Any]:
    import pyautogui

    kind = str(action.get("action", "")).lower()
    pyautogui.PAUSE = float(action.get("pause", 0.05))

    if kind == "click":
        x = int(action["x"])
        y = int(action["y"])
        clicks = int(action.get("clicks", 1))
        button = str(action.get("button", "left"))
        pyautogui.click(x=x, y=y, clicks=clicks, button=button)
        return {"ok": True, "action": kind, "x": x, "y": y, "clicks": clicks}

    if kind == "move":
        x = int(action["x"])
        y = int(action["y"])
        duration = float(action.get("duration", 0.05))
        pyautogui.moveTo(x=x, y=y, duration=duration)
        return {"ok": True, "action": kind, "x": x, "y": y}

    if kind == "write":
        text = str(action.get("text", ""))
        if len(text) > MAX_TEXT_LEN:
            raise ValueError(f"text too long: {len(text)} > {MAX_TEXT_LEN}")
        interval = float(action.get("interval", 0.02))
        pyautogui.write(text, interval=interval)
        return {"ok": True, "action": kind, "chars": len(text)}

    if kind == "press":
        key = str(action["key"])
        presses = int(action.get("presses", 1))
        interval = float(action.get("interval", 0.02))
        pyautogui.press(key, presses=presses, interval=interval)
        return {"ok": True, "action": kind, "key": key, "presses": presses}

    if kind == "hotkey":
        keys = action.get("keys")
        if not isinstance(keys, list) or not keys:
            raise ValueError("hotkey requires non-empty keys list")
        keys = [str(k) for k in keys]
        pyautogui.hotkey(*keys)
        return {"ok": True, "action": kind, "keys": keys}

    if kind == "screenshot":
        from PIL import ImageGrab

        out_dir = DEFAULT_OUTPUT_DIR
        out_dir.mkdir(parents=True, exist_ok=True)
        name = _safe_name(action.get("name"))
        path = out_dir / name
        ImageGrab.grab(all_screens=True).save(path)
        return {"ok": True, "action": kind, "path": str(path)}

    raise ValueError(f"unsupported action: {kind}")


class ChipKBridgeHandler(BaseHTTPRequestHandler):
    server_version = "ChipKAdminBridge/1.0"

    def _json_response(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        if length > 64_000:
            raise ValueError("request body too large")
        raw = self.rfile.read(length)
        return json.loads(raw.decode("utf-8"))

    def _authorized(self) -> bool:
        expected = self.server.token  # type: ignore[attr-defined]
        provided = self.headers.get("X-ChipK-Bridge-Token", "")
        return bool(expected) and provided == expected

    def do_GET(self) -> None:  # noqa: N802
        if self.path != "/health":
            self._json_response(404, {"ok": False, "error": "not_found"})
            return
        if not self._authorized():
            self._json_response(401, {"ok": False, "error": "unauthorized"})
            return
        self._json_response(
            200,
            {
                "ok": True,
                "admin": _is_admin(),
                "foreground_title": _foreground_title(),
                "screen": _screen_size(),
                "output_dir": str(DEFAULT_OUTPUT_DIR),
            },
        )

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/action":
            self._json_response(404, {"ok": False, "error": "not_found"})
            return
        if not self._authorized():
            self._json_response(401, {"ok": False, "error": "unauthorized"})
            return
        try:
            payload = self._read_json()
            if "actions" in payload:
                actions = payload["actions"]
                if not isinstance(actions, list):
                    raise ValueError("actions must be a list")
                if len(actions) > MAX_ACTIONS:
                    raise ValueError(f"too many actions: {len(actions)} > {MAX_ACTIONS}")
                results = [_do_action(dict(item)) for item in actions]
                self._json_response(200, {"ok": True, "results": results})
                return
            result = _do_action(payload)
            self._json_response(200, result)
        except Exception as exc:
            self._json_response(400, {"ok": False, "error": str(exc)})

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"{self.address_string()} - {fmt % args}")


def _load_token(args: argparse.Namespace) -> str:
    token = args.token or os.environ.get("CHIPK_BRIDGE_TOKEN", "")
    if token:
        return token
    return base64.urlsafe_b64encode(os.urandom(24)).decode("ascii").rstrip("=")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run localhost admin bridge for CMoney ChipK GUI control")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--token", default="")
    parser.add_argument("--token-file", default="")
    args = parser.parse_args()

    if args.host not in {"127.0.0.1", "localhost"}:
        raise SystemExit("Refusing to bind non-localhost host")

    token = _load_token(args)
    if args.token_file:
        token_path = Path(args.token_file)
        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text(token + "\n", encoding="utf-8")

    httpd = ThreadingHTTPServer((args.host, args.port), ChipKBridgeHandler)
    httpd.token = token  # type: ignore[attr-defined]
    print(
        json.dumps(
            {
                "status": "listening",
                "host": args.host,
                "port": args.port,
                "admin": _is_admin(),
                "token_file": args.token_file or None,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    httpd.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
