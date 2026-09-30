"""Prepare and execute real pipeline leaf commands in a physical isolated copy.

No scheduler, model training, production ledger write or external publish is run.
Every child starts in the isolated tree; receipts distinguish command execution
from semantic acceptance. Run --help for the deliberately small control surface.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

BASE = Path(__file__).resolve().parents[1]
DEFAULT_RUN = BASE / "output" / "pipeline_acceptance_20260906"
RAW_DIRS = ["日K資料", "月營收", "季報財務", "資產負債", "集保分散", "大盤指數",
            "法人快取", "清理後資料", "估值資料", "新聞資料", "原始融資資料", "外資持股"]


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def workspace(run: Path) -> Path:
    run = run.resolve()
    allowed = (BASE / "output").resolve()
    if not run.is_relative_to(allowed) or run == allowed:
        raise ValueError("acceptance run must be a dedicated directory inside this repository's output")
    return run / "workspace"


def prepare(run: Path) -> None:
    target = workspace(run)
    if target.exists():
        raise FileExistsError(f"refusing to overwrite existing acceptance workspace: {target}")
    target.mkdir(parents=True)
    manifest = []

    def copy(source: Path) -> None:
        if source.is_symlink() or not source.resolve().is_relative_to(BASE.resolve()):
            raise ValueError(f"symlink input is not accepted: {source}")
        relative = source.relative_to(BASE)
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        sha = digest(source)
        if digest(destination) != sha:
            raise RuntimeError(f"source changed while copying: {relative}")
        manifest.append({"path": relative.as_posix(), "size": source.stat().st_size, "sha256": sha})

    # Code and non-secret root lookup tables. No .env files or database copy.
    for source in BASE.iterdir():
        if source.is_file() and source.suffix.lower() in {".py", ".csv", ".json", ".yaml", ".yml"} and not source.name.startswith("."):
            copy(source)
    copy(BASE / "AGENTS.md")
    for name in ("backend", "scripts", "config", "ml"):
        for source in (BASE / name).rglob("*"):
            if not source.is_file() or any(p in {"__pycache__", "models", "reports", "data", "node_modules"} for p in source.relative_to(BASE / name).parts[:-1]):
                continue
            if source.suffix.lower() in {".py", ".json", ".yaml", ".yml", ".csv"}:
                copy(source)
    for name in [*RAW_DIRS, "ml/data", "ml/models", "frontend/static"]:
        for source in (BASE / name).rglob("*"):
            if not source.is_file() or "__pycache__" in source.parts:
                continue
            # Fresh inference must build its own cache, not inherit the old frame.
            if name == "ml/models" and (source.suffix == ".pkl" or "snapshot_cache" in source.name):
                continue
            copy(source)
    for source in (BASE / "ml/reports").glob("*"):
        if source.is_file():
            copy(source)
    for name in ("entry_watchlist.json", "entry_list_20260907.json"):
        source = BASE / "logs" / name
        if source.exists():
            copy(source)
    # SQLite backup API gives a consistent copy if a reader has the source open.
    import sqlite3
    for source in BASE.glob("*.db"):
        destination = target / source.name
        with sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True) as src, sqlite3.connect(destination) as dst:
            src.backup(dst)
        manifest.append({"path": source.name, "size": source.stat().st_size, "sha256": digest(source), "copy_method": "sqlite_backup"})
    for name in ("logs", "temp", "output"):
        (target / name).mkdir(exist_ok=True)
    (target / "sitecustomize.py").write_text("from scripts.pipeline_acceptance_guard import install\ninstall()\n", encoding="utf-8")
    write_json(run / "input_manifest.json", {"source": str(BASE), "workspace": str(target), "copied_at": datetime.now().isoformat(), "files": manifest})
    print(json.dumps({"workspace": str(target), "files": len(manifest), "bytes": sum(x["size"] for x in manifest)}))


def child_env(run: Path) -> dict[str, str]:
    target = workspace(run)
    env = dict(os.environ)
    for key in list(env):
        if any(token in key.upper() for token in ("SMTP", "NTFY", "EMAIL", "TUNNEL")):
            env.pop(key)
    for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
        env.pop(key, None)
    env.update(STOCK_BASE_DIR=str(target), PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1",
               PYTHONPATH=str(target), TEMP=str(target / "temp"), TMP=str(target / "temp"),
               STOCK_ACCEPTANCE_ROOT=str(target), STOCK_LGBM_THREADS="8",
               MPLCONFIGDIR=str(target / "temp" / "matplotlib"), XDG_CACHE_HOME=str(target / "temp" / "cache"))
    model_contract = run / "production_model_contract.json"
    if model_contract.exists():
        contract = json.loads(model_contract.read_text(encoding="utf-8"))
        if not contract["validation"]["passed"]:
            raise ValueError("actual production model registry validation failed")
        env.update(contract["environment"])
    for key in ("V2_CHAMPION_DB_PATH", "UNIFIED_DB_PATH", "V2_BASELINE_SHADOW_DB_PATH", "UNIFIED_SHADOW_DB_PATH", "UNIFIED_ARCHIVE_ROOT"):
        env.pop(key, None)
    return env


def execute(run: Path, label: str, command: list[str], timeout: int) -> int:
    target = workspace(run)
    if not (run / "input_manifest.json").exists():
        raise ValueError("prepare the isolated inputs first")
    if not (target / "sitecustomize.py").exists() or not (target / "scripts/pipeline_acceptance_guard.py").exists():
        raise ValueError("acceptance guard must be installed before executing child commands")
    if command[0].endswith(".py"):
        leaf = (target / command[0]).resolve()
        if not leaf.is_relative_to(target.resolve()):
            raise ValueError("Python leaf script must belong to the physical acceptance copy")
    started = datetime.now().isoformat()
    started_clock = time.monotonic()
    logfile = run / f"{label}.log"
    with logfile.open("w", encoding="utf-8") as out:
        try:
            proc = subprocess.run([sys.executable, "-B", "-X", "utf8", *command], cwd=target, env=child_env(run), stdout=out, stderr=subprocess.STDOUT, timeout=timeout)
            code = proc.returncode
        except subprocess.TimeoutExpired:
            code = 124
            out.write(f"\nACCEPTANCE TIMEOUT after {timeout}s\n")
    receipt = {"label": label, "started": started, "seconds": round(time.monotonic() - started_clock, 2), "command": command,
               "cwd": str(target), "exit_code": code, "execution_status": "PASS" if code == 0 else "FAIL", "log": str(logfile),
               "note": "Process exit is not semantic or full scheduler acceptance; inspect stage outputs and gates."}
    write_json(run / f"{label}.json", receipt)
    print(json.dumps(receipt, ensure_ascii=False))
    print(logfile.read_text(encoding="utf-8")[-5000:])
    return code


def verify_originals(run: Path) -> None:
    manifest = json.loads((run / "input_manifest.json").read_text(encoding="utf-8"))
    changed = []
    for item in manifest["files"]:
        path = BASE / item["path"]
        if not path.exists() or digest(path) != item["sha256"]:
            changed.append(item["path"])
    result = {"checked": len(manifest["files"]), "changed": changed, "note": "Concurrent scheduled outputs may change independently; inspect provenance before attribution."}
    write_json(run / "source_integrity.json", result)
    print(json.dumps(result, ensure_ascii=False))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("mode", choices=["prepare", "run", "verify-originals"])
    ap.add_argument("--run-dir", type=Path, default=DEFAULT_RUN)
    ap.add_argument("--label", default="stage")
    ap.add_argument("--timeout", type=int, default=1200)
    argv = sys.argv[1:]
    split = argv.index("--") if "--" in argv else len(argv)
    command = argv[split + 1:]
    args = ap.parse_args(argv[:split])
    if not args.label.replace("_", "").replace("-", "").isalnum():
        ap.error("label must contain only letters, numbers, underscores or hyphens")
    if args.mode == "prepare":
        prepare(args.run_dir)
    elif args.mode == "verify-originals":
        verify_originals(args.run_dir)
    else:
        if not command:
            ap.error("run requires -- followed by an explicit reviewed Python leaf command")
        return execute(args.run_dir, args.label, command, args.timeout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
