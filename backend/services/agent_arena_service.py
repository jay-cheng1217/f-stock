from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from backend.db.engine import get_conn
from scripts.agent_arena import LATEST_JSON_PATH, export_agent_detail, export_latest_status


def load_agent_arena_status() -> dict[str, Any]:
    path = Path(LATEST_JSON_PATH)
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return export_latest_status()


def load_agent_arena_detail(agent_id: str) -> dict[str, Any]:
    return export_agent_detail(agent_id, duckdb_conn=get_conn(read_only=True))
