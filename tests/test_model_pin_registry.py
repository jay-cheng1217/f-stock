import json
import subprocess
import sys
from pathlib import Path

from scripts import model_pin_registry


def _write_fake_bundle(tmp_path: Path, name: str, content: str = "model") -> tuple[Path, Path]:
    model_path = tmp_path / f"{name}.txt"
    meta_path = tmp_path / f"{name}_meta.json"
    model_path.write_text(content, encoding="utf-8")
    meta_path.write_text(json.dumps({"model_file": str(model_path)}), encoding="utf-8")
    return meta_path, model_path


def test_registry_refresh_and_validate_detects_sha_drift(tmp_path: Path) -> None:
    meta_path, model_path = _write_fake_bundle(tmp_path, "fake_model")
    manifest_path = tmp_path / "manifest.yaml"
    registry_path = tmp_path / "registry.json"
    checksum_dir = tmp_path / "checksums"
    manifest_path.write_text(
        f"""
version: test
model_era: test
champion:
  stage1_key: fake
  stage2_key: fake
pins:
  - key: fake
    family: test
    role: champion_stage1
    meta_path: "{meta_path.as_posix()}"
    model_path: "{model_path.as_posix()}"
    pin_reason: test pin
    pinned_at: "2026-05-17"
    retire_after: null
""",
        encoding="utf-8",
    )

    model_pin_registry.write_registry(
        manifest_path=manifest_path,
        registry_path=registry_path,
        checksum_dir=checksum_dir,
    )
    assert model_pin_registry.validate_registry(
        manifest_path=manifest_path,
        registry_path=registry_path,
    )["passed"]

    model_path.write_text("mutated", encoding="utf-8")
    result = model_pin_registry.validate_registry(
        manifest_path=manifest_path,
        registry_path=registry_path,
    )
    assert not result["passed"]
    assert any("sha mismatch" in failure for failure in result["failures"])


def test_cleanup_precheck_blocks_real_pinned_stage1() -> None:
    blocked = model_pin_registry.blocked_cleanup_paths(["ml/models/lgbm_v2_20260508_200128.txt"])

    assert blocked


def test_cleanup_precheck_blocks_model_selection_reference() -> None:
    blocked = model_pin_registry.blocked_cleanup_paths(["ml/models/lgbm_20260516_112903.txt"])

    assert blocked


def test_cleanup_precheck_blocks_unpinned_weekly_retrain_bundle() -> None:
    blocked = model_pin_registry.blocked_cleanup_paths(["ml/models/lgbm_v2_20260516_113451.txt"])

    assert blocked


def test_cleanup_precheck_blocks_model_directory() -> None:
    blocked = model_pin_registry.blocked_cleanup_paths(["ml/models"])

    assert blocked


def test_cleanup_precheck_cli_aborts_pinned_model() -> None:
    proc = subprocess.run(
        [sys.executable, "scripts/model_cleanup_precheck.py", "ml/models/lgbm_v2_20260508_200128.txt"],
        text=True,
        capture_output=True,
    )

    assert proc.returncode == 1
    assert "ABORT" in proc.stdout
