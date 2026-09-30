import json
from pathlib import Path

from ml.model_selection import MODEL_SELECTION_PATH, resolve_base_meta_path


def test_configured_model_selection_paths_exist() -> None:
    selection = json.loads(Path(MODEL_SELECTION_PATH).read_text(encoding="utf-8"))

    for slot in ("production", "shadow"):
        meta_path = Path(selection[slot]["meta_path"])
        assert meta_path.exists(), f"{slot} meta_path missing: {meta_path}"

        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        model_path = Path(meta["model_file"])
        assert model_path.exists(), f"{slot} model_file missing: {model_path}"


def test_resolved_model_selection_paths_exist() -> None:
    for slot in ("production", "shadow"):
        resolved = resolve_base_meta_path(slot)
        assert resolved is not None
        assert Path(resolved).exists()
