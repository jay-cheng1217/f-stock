"""Model pin manifest, checksum registry, and cleanup safety helpers."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "config" / "champion_pin_manifest.yaml"
REGISTRY_PATH = ROOT / "ml" / "models" / "registry.json"
CHECKSUM_DIR = ROOT / "archive" / "model_pins"
MODEL_SELECTION_PATH = ROOT / "ml" / "models" / "model_selection.json"
MODEL_DIR = ROOT / "ml" / "models"
MODEL_BUNDLE_PATTERNS = (
    "lgbm*.txt",
    "lgbm*_meta.json",
)


@dataclass(frozen=True)
class PinnedModel:
    key: str
    family: str
    role: str
    meta_path: Path
    model_path: Path
    pin_reason: str
    pinned_at: str
    retire_after: str | None


class PinValidationError(RuntimeError):
    """Raised when production pin artifacts are missing or modified."""


def _resolve(path: str | Path) -> Path:
    path = Path(path)
    if not path.is_absolute():
        path = ROOT / path
    return path.resolve()


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_manifest(path: str | Path = MANIFEST_PATH) -> dict[str, Any]:
    manifest_path = _resolve(path)
    with manifest_path.open("r", encoding="utf-8") as fh:
        payload = yaml.safe_load(fh) or {}
    if not isinstance(payload, dict):
        raise PinValidationError(f"manifest must be a mapping: {manifest_path}")
    if "pins" not in payload or not isinstance(payload["pins"], list):
        raise PinValidationError(f"manifest missing pins list: {manifest_path}")
    return payload


def load_pins(path: str | Path = MANIFEST_PATH) -> list[PinnedModel]:
    payload = load_manifest(path)
    pins: list[PinnedModel] = []
    seen: set[str] = set()
    for raw in payload["pins"]:
        if not isinstance(raw, dict):
            raise PinValidationError("each pin must be a mapping")
        key = str(raw.get("key") or "").strip()
        if not key:
            raise PinValidationError("pin key is required")
        if key in seen:
            raise PinValidationError(f"duplicate pin key: {key}")
        seen.add(key)
        pins.append(
            PinnedModel(
                key=key,
                family=str(raw.get("family") or ""),
                role=str(raw.get("role") or ""),
                meta_path=_resolve(str(raw.get("meta_path") or "")),
                model_path=_resolve(str(raw.get("model_path") or "")),
                pin_reason=str(raw.get("pin_reason") or ""),
                pinned_at=str(raw.get("pinned_at") or ""),
                retire_after=raw.get("retire_after"),
            )
        )
    return pins


def champion_defaults(path: str | Path = MANIFEST_PATH) -> dict[str, str]:
    payload = load_manifest(path)
    champion = payload.get("champion") or {}
    pins = {pin.key: pin for pin in load_pins(path)}
    stage1 = pins.get(str(champion.get("stage1_key") or ""))
    stage2 = pins.get(str(champion.get("stage2_key") or ""))
    if stage1 is None or stage2 is None:
        raise PinValidationError("champion stage1_key/stage2_key must reference manifest pins")
    return {
        "V2_MODEL_META": str(stage1.meta_path),
        "TWO_STAGE_RANKER_ENABLED": "1" if champion.get("two_stage_enabled", True) else "0",
        "TWO_STAGE_RANKER_META": str(stage2.meta_path),
        "UNIFIED_EXIT_POLICY": str(champion.get("exit_policy") or "asymmetric_v2"),
        "SECTOR_OVERHEAT_THRESHOLDS_ENABLED": (
            "1" if champion.get("sector_overheat_thresholds_enabled", True) else "0"
        ),
        "MODEL_ERA": str(payload.get("model_era") or ""),
    }


def _meta_model_file(meta_path: Path) -> Path | None:
    try:
        payload = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    model_file = payload.get("model_file")
    return _resolve(str(model_file)) if model_file else None


def _model_selection_paths(path: str | Path = MODEL_SELECTION_PATH) -> set[Path]:
    selection_path = _resolve(path)
    paths: set[Path] = {selection_path}
    if not selection_path.exists():
        return paths
    try:
        payload = json.loads(selection_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return paths
    if not isinstance(payload, dict):
        return paths
    for raw in payload.values():
        if not isinstance(raw, dict):
            continue
        meta_path = raw.get("meta_path")
        if not meta_path:
            continue
        resolved_meta = _resolve(str(meta_path))
        paths.add(resolved_meta)
        model_file = _meta_model_file(resolved_meta) if resolved_meta.exists() else None
        if model_file is not None:
            paths.add(model_file)
    return paths


def _weekly_retrain_model_bundle_paths(model_dir: str | Path = MODEL_DIR) -> set[Path]:
    """Return model bundles that normal cleanup must not delete.

    `TW_Stock_Weekly_Retrain` writes model binaries and metadata under
    `ml/models`. These files may be untracked because the binaries are large,
    but they are operational artifacts and can later be referenced by
    `model_selection.json`, model pins, incident forensics, or A/B reports.
    """
    root = _resolve(model_dir)
    if not root.exists():
        return set()
    paths: set[Path] = set()
    for pattern in MODEL_BUNDLE_PATTERNS:
        paths.update(path.resolve() for path in root.glob(pattern) if path.is_file())
    return paths


def build_registry(path: str | Path = MANIFEST_PATH) -> dict[str, Any]:
    manifest = load_manifest(path)
    entries: list[dict[str, Any]] = []
    for pin in load_pins(path):
        meta_sha = sha256_file(pin.meta_path) if pin.meta_path.exists() else None
        model_sha = sha256_file(pin.model_path) if pin.model_path.exists() else None
        entries.append(
            {
                "key": pin.key,
                "family": pin.family,
                "role": pin.role,
                "meta_path": str(pin.meta_path),
                "model_path": str(pin.model_path),
                "meta_sha256": meta_sha,
                "model_sha256": model_sha,
                "pin_reason": pin.pin_reason,
                "pinned_at": pin.pinned_at,
                "retire_after": pin.retire_after,
                "checksum_path": str(CHECKSUM_DIR / f"{pin.model_path.name}.sha256.txt"),
            }
        )
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "manifest_path": str(_resolve(path)),
        "manifest_version": manifest.get("version"),
        "model_era": manifest.get("model_era"),
        "pins": entries,
    }


def write_registry(
    *,
    manifest_path: str | Path = MANIFEST_PATH,
    registry_path: str | Path = REGISTRY_PATH,
    checksum_dir: str | Path = CHECKSUM_DIR,
    backup_dir: str | Path | None = None,
) -> dict[str, Any]:
    registry = build_registry(manifest_path)
    registry_target = _resolve(registry_path)
    registry_target.parent.mkdir(parents=True, exist_ok=True)
    registry_target.write_text(json.dumps(registry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    checksum_root = _resolve(checksum_dir)
    checksum_root.mkdir(parents=True, exist_ok=True)
    optional_backup = _resolve(backup_dir) if backup_dir else None
    if optional_backup:
        optional_backup.mkdir(parents=True, exist_ok=True)

    for entry in registry["pins"]:
        model_path = Path(entry["model_path"])
        checksum_path = checksum_root / f"{model_path.name}.sha256.txt"
        checksum_path.write_text(
            f"{entry['model_sha256']}  {model_path.name}\n",
            encoding="utf-8",
        )
        entry["checksum_path"] = str(checksum_path)
        if optional_backup and model_path.exists():
            shutil.copy2(model_path, optional_backup / model_path.name)
            meta_path = Path(entry["meta_path"])
            if meta_path.exists():
                shutil.copy2(meta_path, optional_backup / meta_path.name)

    registry_target.write_text(json.dumps(registry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return registry


def load_registry(path: str | Path = REGISTRY_PATH) -> dict[str, Any]:
    registry_path = _resolve(path)
    with registry_path.open("r", encoding="utf-8") as fh:
        payload = json.load(fh)
    if not isinstance(payload, dict) or not isinstance(payload.get("pins"), list):
        raise PinValidationError(f"invalid registry: {registry_path}")
    return payload


def validate_registry(
    *,
    manifest_path: str | Path = MANIFEST_PATH,
    registry_path: str | Path = REGISTRY_PATH,
) -> dict[str, Any]:
    pins = {pin.key: pin for pin in load_pins(manifest_path)}
    registry = load_registry(registry_path)
    failures: list[str] = []
    details: list[dict[str, Any]] = []
    for entry in registry.get("pins", []):
        key = str(entry.get("key") or "")
        pin = pins.get(key)
        if pin is None:
            failures.append(f"registry key not in manifest: {key}")
            continue
        row = {"key": key, "meta_path": str(pin.meta_path), "model_path": str(pin.model_path)}
        for label, path, expected in [
            ("meta", pin.meta_path, entry.get("meta_sha256")),
            ("model", pin.model_path, entry.get("model_sha256")),
        ]:
            if not path.exists():
                failures.append(f"{key}: missing {label} {path}")
                row[f"{label}_ok"] = False
                continue
            actual = sha256_file(path)
            row[f"{label}_sha256"] = actual
            row[f"{label}_expected_sha256"] = expected
            row[f"{label}_ok"] = actual == expected
            if actual != expected:
                failures.append(f"{key}: {label} sha mismatch {path}")
        meta_model_file = _meta_model_file(pin.meta_path)
        if meta_model_file and meta_model_file != pin.model_path:
            failures.append(f"{key}: meta model_file {meta_model_file} != manifest model_path {pin.model_path}")
            row["meta_model_file_match"] = False
        else:
            row["meta_model_file_match"] = True
        details.append(row)
    missing_in_registry = set(pins) - {str(entry.get("key") or "") for entry in registry.get("pins", [])}
    for key in sorted(missing_in_registry):
        failures.append(f"manifest key missing from registry: {key}")
    return {"passed": not failures, "failures": failures, "details": details}


def pinned_paths(
    *,
    manifest_path: str | Path = MANIFEST_PATH,
    registry_path: str | Path = REGISTRY_PATH,
) -> set[Path]:
    paths: set[Path] = {_resolve(manifest_path), _resolve(registry_path)}
    for pin in load_pins(manifest_path):
        paths.add(pin.meta_path)
        paths.add(pin.model_path)
    if _resolve(registry_path).exists():
        registry = load_registry(registry_path)
        for entry in registry.get("pins", []):
            for key in ("meta_path", "model_path", "checksum_path"):
                value = entry.get(key)
                if value:
                    paths.add(_resolve(str(value)))
    paths.update(_model_selection_paths())
    paths.update(_weekly_retrain_model_bundle_paths())
    return paths


def blocked_cleanup_paths(paths: list[str | Path]) -> list[Path]:
    protected = pinned_paths()
    blocked: list[Path] = []
    for raw in paths:
        target = _resolve(raw)
        for protected_path in protected:
            if (
                target == protected_path
                or protected_path.is_relative_to(target)
                or target.is_relative_to(protected_path)
            ):
                blocked.append(target)
                break
    return blocked


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Manage production model pin registry.")
    parser.add_argument("--manifest", default=str(MANIFEST_PATH))
    parser.add_argument("--registry", default=str(REGISTRY_PATH))
    parser.add_argument("--refresh", action="store_true", help="Write registry and checksum files")
    parser.add_argument("--validate", action="store_true", help="Validate registry SHA256 values")
    parser.add_argument("--backup-dir", default=os.environ.get("MODEL_PIN_BACKUP_DIR", ""))
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if args.refresh:
        registry = write_registry(
            manifest_path=args.manifest,
            registry_path=args.registry,
            backup_dir=args.backup_dir or None,
        )
        print(f"[model-pin] registry={args.registry} pins={len(registry['pins'])}")
    if args.validate:
        result = validate_registry(manifest_path=args.manifest, registry_path=args.registry)
        if result["passed"]:
            print("[model-pin] validation PASS")
        else:
            print("[model-pin] validation FAIL")
            for failure in result["failures"]:
                print(f"- {failure}")
            return 1
    if not args.refresh and not args.validate:
        defaults = champion_defaults(args.manifest)
        print(json.dumps(defaults, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
