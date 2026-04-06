"""Helpers for selecting production and shadow base models."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from ml.config import MODEL_DIR

MODEL_SELECTION_PATH = os.path.join(MODEL_DIR, "model_selection.json")
BASE_MODEL_META_RE = re.compile(r"^lgbm_\d{8}_\d{6}_meta\.json$")


def list_base_model_meta_paths() -> list[str]:
    model_dir = Path(MODEL_DIR)
    if not model_dir.exists():
        return []
    return sorted(
        str(path.resolve())
        for path in model_dir.iterdir()
        if BASE_MODEL_META_RE.match(path.name)
    )


def load_model_selection() -> dict:
    if not os.path.exists(MODEL_SELECTION_PATH):
        return {}
    with open(MODEL_SELECTION_PATH, "r", encoding="utf-8") as fh:
        return json.load(fh)


def get_slot_config(slot: str) -> dict:
    selection = load_model_selection()
    config = selection.get(slot, {})
    return config if isinstance(config, dict) else {}


def get_slot_label(slot: str) -> str:
    return str(get_slot_config(slot).get("label") or slot)


def resolve_base_meta_path(
    slot: str = "production",
    explicit_meta_path: str | None = None,
) -> str | None:
    if explicit_meta_path:
        return str(Path(explicit_meta_path).resolve())

    config = get_slot_config(slot)
    configured_path = config.get("meta_path")
    if configured_path:
        return str(Path(configured_path).resolve())

    meta_paths = list_base_model_meta_paths()
    if not meta_paths:
        return None

    if slot == "shadow":
        return None
    return meta_paths[-1]


def has_shadow_model_slot() -> bool:
    return resolve_base_meta_path("shadow") is not None
