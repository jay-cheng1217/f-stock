"""Python audit guard for physical pipeline acceptance copies.

sitecustomize.py: ``from scripts.pipeline_acceptance_guard import install; install()``
STOCK_ACCEPTANCE_ROOT must identify the copy. Failed installation raises SystemExit
because Python otherwise swallows ordinary sitecustomize exceptions. This is an
accidental-side-effect guard, NOT an OS sandbox: native extension I/O, inherited
file descriptors and remote HTTPS application semantics are outside its coverage.
Actual inference functions are not patched.

Self-test: python -B -X utf8 scripts/pipeline_acceptance_guard.py --self-test
"""
from __future__ import annotations

import atexit
import ipaddress
import json
import os
from pathlib import Path
import re
import sys
from urllib.parse import unquote, urlsplit, parse_qs

_INSTALLED = None
_TRAINING = re.compile(r"(^|[\\/_.-])(retrain|train|training)([\\/_.-]|$)", re.I)
_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC
_SMTP_PORTS = {25, 465, 587, 2525}


def _windows_argv(command: str) -> list[str]:
    """Windows emits the serialized command even when Popen received a safe list."""
    import ctypes
    from ctypes import wintypes
    argc = ctypes.c_int()
    parser = ctypes.windll.shell32.CommandLineToArgvW
    parser.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
    parser.restype = ctypes.POINTER(wintypes.LPWSTR)
    parsed = parser(command, ctypes.byref(argc))
    if not parsed:
        raise ValueError("Cannot decode Windows command line")
    try:
        return [parsed[index] for index in range(argc.value)]
    finally:
        ctypes.windll.kernel32.LocalFree(ctypes.cast(parsed, ctypes.c_void_p))


class AcceptanceViolation(PermissionError):
    pass


class AcceptanceGuard:
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve(strict=True)
        if not self.root.is_dir() or self.root == Path(self.root.anchor):
            raise ValueError("acceptance root must be an existing dedicated directory")
        self.violations: list[dict] = []
        self.log_fd = None

    def deny(self, event: str, detail) -> None:
        record = {"event": event, "detail": str(detail), "pid": os.getpid()}
        self.violations.append(record)
        if self.log_fd is not None:
            os.write(self.log_fd, (json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8"))
        raise AcceptanceViolation(f"Acceptance guard denied {event}: {detail}")

    def resolved(self, path, dir_fd=None) -> Path | None:
        # fd creation is audited separately; inherited writable fds are a stated limitation.
        if isinstance(path, int):
            return None
        if dir_fd not in (None, -1):
            self.deny("dir_fd operation", path)
        return Path(os.fsdecode(path)).resolve()

    def protected(self, path: Path) -> bool:
        try:
            rel = path.relative_to(self.root)
        except ValueError:
            return False
        parts = tuple(part.casefold() for part in rel.parts)
        name = path.name.casefold()
        return (
            (len(parts) >= 3 and parts[:2] == ("ml", "models") and
             (name.startswith("lgbm") or name in {"model_selection.json", "registry.json"}))
            or parts[:2] == ("archive", "model_pins")
            or parts == ("config", "champion_pin_manifest.yaml")
            or parts == ("sitecustomize.py",)
            or parts == ("scripts", "pipeline_acceptance_guard.py")
        )

    def write(self, path, *, event="write", dir_fd=None, directory=False) -> None:
        target = self.resolved(path, dir_fd)
        if target is None:
            return
        if not target.is_relative_to(self.root):
            self.deny(event, target)
        if self.protected(target):
            self.deny(event + " protected artifact", target)
        if directory:
            # Renaming/removing a parent also modifies the protected descendants.
            for relative in ("ml", "ml/models", "archive", "archive/model_pins", "config"):
                if (self.root / relative).is_relative_to(target):
                    self.deny(event + " protected parent", target)

    def network(self, address, event="socket.connect") -> None:
        if not isinstance(address, tuple) or len(address) < 2:
            self.deny(event + " unknown socket address", address)
        host, port = str(address[0]).strip("[]"), address[1]
        try:
            port = int(port)
        except (ValueError, TypeError):
            self.deny(event + " invalid port", port)
        if port in _SMTP_PORTS:
            self.deny(event + " SMTP", address)
        loopback = host.casefold().rstrip(".") == "localhost"
        try:
            ip = ipaddress.ip_address(host.split("%")[0])
            loopback = loopback or ip.is_loopback or bool(getattr(ip, "ipv4_mapped", None) and ip.ipv4_mapped.is_loopback)
        except ValueError:
            pass
        if port == 8001 and loopback:
            self.deny(event + " production dashboard", address)

    def training(self, argv) -> None:
        for token in argv:
            text = str(token).casefold()
            if _TRAINING.search(text) or text in {"--retrain", "--retrain-v2-only", "--train"}:
                self.deny("training command", token)

    def subprocess(self, executable, argv, cwd, env) -> None:
        if isinstance(argv, str):
            if os.name != "nt":
                self.deny("opaque subprocess command", argv)
            argv = _windows_argv(argv)
        argv = [os.fsdecode(arg) for arg in argv]
        self.training(argv)
        if cwd is not None and not Path(cwd).resolve().is_relative_to(self.root):
            self.deny("subprocess working directory", cwd)
        name = Path(os.fsdecode(executable or argv[0])).name.casefold()
        child_env = os.environ if env is None else env
        if re.fullmatch(r"python(?:w)?(?:\d+(?:\.\d+)*)?(?:\.exe)?", name):
            if any(re.fullmatch(r"-[A-Za-z]*[SIE][A-Za-z]*", arg) for arg in argv[1:]):
                self.deny("Python guard bypass flag", argv)
            root_env = child_env.get("STOCK_ACCEPTANCE_ROOT", "")
            paths = [Path(p).resolve() for p in child_env.get("PYTHONPATH", "").split(os.pathsep) if p]
            if not root_env or Path(root_env).resolve() != self.root or self.root not in paths:
                self.deny("Python child missing inherited guard", argv)
            return
        if name in {"curl", "curl.exe"}:
            # Only reviewed read-style curl fallbacks; no shells, Git publish or services.
            no_value = {"-k", "-L", "-s", "-S", "-f", "-sS", "-fsS", "--insecure", "--location", "--fail", "--silent", "--show-error", "--compressed", "--http1.1", "--http2", "--no-progress-meter"}
            takes_value = {"-X", "--request", "-o", "--output", "--output-dir", "-D", "--dump-header", "--stderr", "--max-time", "--connect-timeout", "--retry", "--retry-delay", "-A", "--user-agent", "-H", "--header", "--url"}
            urls = []
            i = 1
            while i < len(argv):
                arg = argv[i]
                option = arg.split("=", 1)[0]
                if option in takes_value:
                    if "=" in arg:
                        value = arg.split("=", 1)[1]
                    else:
                        i += 1
                        if i >= len(argv):
                            self.deny("curl missing option value", arg)
                        value = argv[i]
                    if option in {"-X", "--request"} and value.upper() not in {"GET", "HEAD"}:
                        self.deny("curl non-read method", value)
                    if option in {"-o", "--output", "--output-dir", "-D", "--dump-header", "--stderr"} and value != "-":
                        self.write(value, event="curl output")
                    if option == "--url":
                        urls.append(value)
                elif arg not in no_value:
                    if arg.startswith("-"):
                        self.deny("curl unreviewed option", arg)
                    urls.append(arg)
                i += 1
            if not urls:
                self.deny("curl missing explicit official URL", argv)
            for url in urls:
                parsed = urlsplit(url)
                host = parsed.hostname or ""
                official = any(host == domain or host.endswith("." + domain) for domain in ("twse.com.tw", "tpex.org.tw", "tdcc.com.tw", "taifex.com.tw"))
                if parsed.scheme != "https" or not official:
                    self.deny("curl unreviewed endpoint", url)
            return
        self.deny("unreviewed subprocess", executable)

    def audit(self, event: str, args: tuple) -> None:
        if event == "open":
            path, mode, flags = args
            if (isinstance(mode, str) and any(flag in mode for flag in "wax+")) or (isinstance(flags, int) and flags & _WRITE_FLAGS):
                self.write(path, event=event)
        elif event in {"os.remove", "os.rmdir", "os.mkdir", "os.chmod", "os.chown", "os.utime", "os.truncate"}:
            dir_fd = args[-1] if event in {"os.remove", "os.rmdir", "os.mkdir", "os.chmod", "os.chown", "os.utime"} else None
            self.write(args[0], event=event, dir_fd=dir_fd, directory=event == "os.rmdir")
        elif event == "os.rename":
            self.write(args[0], event=event, dir_fd=args[2], directory=True)
            self.write(args[1], event=event, dir_fd=args[3], directory=True)
        elif event in {"os.symlink", "os.link"}:
            self.deny(event + " aliases forbidden", args)
        elif event in {"os.system", "os.exec", "os.posix_spawn", "os.spawn"}:
            self.deny(event + " unmanaged child", args)
        elif event == "subprocess.Popen":
            self.subprocess(*args)
        elif event in {"socket.connect", "socket.sendto"}:
            self.network(args[-1], event)
        elif event == "socket.getaddrinfo" and len(args) > 1:
            if args[1] is not None:
                self.network((args[0], args[1]), event)
        elif event.startswith("smtplib."):
            self.deny(event + " email disabled", "SMTP")
        elif event == "sqlite3.connect":
            database = os.fsdecode(args[0])
            if database == ":memory:" or "mode=memory" in database:
                return
            if database.startswith("file:"):
                parsed = urlsplit(database)
                if parse_qs(parsed.query).get("mode") == ["ro"]:
                    return
                database = unquote(parsed.path)
                if os.name == "nt" and re.match(r"^/[A-Za-z]:", database):
                    database = database[1:]
            self.write(database, event=event)


def install() -> AcceptanceGuard:
    global _INSTALLED
    if _INSTALLED is not None:
        return _INSTALLED
    try:
        raw = os.environ.get("STOCK_ACCEPTANCE_ROOT")
        if not raw:
            raise ValueError("STOCK_ACCEPTANCE_ROOT is required")
        guard = AcceptanceGuard(raw)
        guard.training(getattr(sys, "orig_argv", sys.argv))
        output = guard.root / "output"
        output.mkdir(exist_ok=True)
        if not output.resolve().is_relative_to(guard.root):
            raise ValueError("guard receipt path escapes root")
        guard.log_fd = os.open(output / f"acceptance_guard_{os.getpid()}.jsonl", os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        sys.addaudithook(guard.audit)
        _INSTALLED = guard
        os.environ["STOCK_ACCEPTANCE_GUARD_ACTIVE"] = str(guard.root)
        receipt = {"pid": os.getpid(), "root": str(guard.root), "installed": True,
                   "scope": "Python audit events; native extension I/O and inherited fds are not an OS sandbox"}
        (output / f"acceptance_guard_{os.getpid()}.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
        atexit.register(lambda: os.close(guard.log_fd))
        return guard
    except Exception as exc:
        raise SystemExit(f"Acceptance guard installation failed: {exc}") from exc


def _probe(root: Path) -> None:
    import socket
    import sqlite3
    import smtplib
    import subprocess
    (root / "ml/models").mkdir(parents=True)
    (root / "config").mkdir()
    guard = install()
    passed = []
    def blocked(name, function):
        try:
            function()
        except AcceptanceViolation:
            passed.append(name)
        else:
            raise AssertionError(f"Expected rejection: {name}")
    (root / "allowed.txt").write_text("ok")
    with sqlite3.connect(root / "isolated.db") as db:
        db.execute("create table smoke (value int)")
    blocked("outside write", lambda: (root.parent / "escaped.txt").write_text("bad"))
    blocked("model write", lambda: (root / "ml/models/lgbm_test.txt").write_text("bad"))
    blocked("pin write", lambda: (root / "config/champion_pin_manifest.yaml").write_text("bad"))
    blocked("outside SQLite", lambda: sqlite3.connect(root.parent / "escaped.db"))
    blocked("SMTP", lambda: smtplib.SMTP().connect("127.0.0.1", 25))
    sock = socket.socket()
    try:
        blocked("production dashboard", lambda: sock.connect(("127.0.0.1", 8001)))
    finally:
        sock.close()
    blocked("training subprocess", lambda: subprocess.run([sys.executable, "scripts/train_v2.py"], cwd=root))
    blocked("shell subprocess", lambda: subprocess.run(["cmd.exe", "/c", "echo forbidden"], cwd=root))
    blocked("protected directory rename", lambda: (root / "ml/models").rename(root / "moved"))
    blocked("unqualified curl localhost", lambda: guard.subprocess("curl.exe", ["curl.exe", "127.0.0.1:8001"], root, None))
    blocked("curl compact POST", lambda: guard.subprocess("curl.exe", ["curl.exe", "-XPOST", "https://www.twse.com.tw/"], root, None))
    blocked("low-level outside open", lambda: os.open(root.parent / "escaped-low.txt", os.O_WRONLY | os.O_CREAT))
    blocked("outside rename", lambda: (root / "allowed.txt").rename(root.parent / "escaped-rename.txt"))
    guard.network(("www.twse.com.tw", 443))
    print(json.dumps({"checks_passed": passed, "allowed": ["copy write", "copy SQLite", "official HTTPS policy"]}))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--probe":
        _probe(Path(sys.argv[2]))
    elif len(sys.argv) > 1 and sys.argv[1] == "--self-test":
        import subprocess
        import tempfile
        with tempfile.TemporaryDirectory(prefix="stock-acceptance-guard-") as temporary:
            root = Path(temporary) / "copy"
            root.mkdir()
            env = dict(os.environ, STOCK_ACCEPTANCE_ROOT=str(root), PYTHONDONTWRITEBYTECODE="1")
            env.pop("PYTHONPATH", None)
            subprocess.run([sys.executable, "-B", "-X", "utf8", str(Path(__file__).resolve()), "--probe", str(root)], env=env, check=True)
            (root / "scripts").mkdir()
            (root / "scripts/pipeline_acceptance_guard.py").write_bytes(Path(__file__).read_bytes())
            (root / "sitecustomize.py").write_text("from scripts.pipeline_acceptance_guard import install\ninstall()\n", encoding="utf-8")
            env["PYTHONPATH"] = str(root)
            child = "import os; assert os.environ.get('STOCK_ACCEPTANCE_GUARD_ACTIVE'); print('nested guard startup PASS')"
            code = "import os,sys,subprocess; assert os.environ.get('STOCK_ACCEPTANCE_GUARD_ACTIVE'); subprocess.run([sys.executable,'-B','-c'," + repr(child) + "],check=True)"
            subprocess.run([sys.executable, "-B", "-c", code], cwd=root, env=env, check=True)
    else:
        raise SystemExit("Use --self-test or install() from the acceptance copy's sitecustomize")
