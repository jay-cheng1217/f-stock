"""Archive the latest local CMoney ChipK main-force snapshot.

This reads only non-secret UBSKChart market-data files.  The raw archive is
kept under ml/data/chipk and is git-ignored because it comes from the user's
local/member desktop app.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))

from ml.chipk import load_latest_chipk_main_force, redact_chipk_source_path  # noqa: E402

DEFAULT_ARCHIVE_DIR = BASE_DIR / "ml" / "data" / "chipk" / "archive"
DEFAULT_LATEST_DIR = BASE_DIR / "ml" / "data" / "chipk" / "latest"


def archive_latest_chipk_snapshot(
    *,
    archive_dir: str | Path = DEFAULT_ARCHIVE_DIR,
    latest_dir: str | Path = DEFAULT_LATEST_DIR,
    update_latest: bool = True,
) -> dict[str, object]:
    """Archive the latest local ChipK snapshot and return redacted metadata."""

    df, meta = load_latest_chipk_main_force()
    payload: dict[str, object] = {
        **meta.to_dict(),
        "source_path": redact_chipk_source_path(meta.source_path),
        "archived": False,
    }
    if meta.status != "ok" or df.empty:
        return payload

    asof = meta.asof_date or "unknown"
    archive_root = Path(archive_dir)
    archive_root.mkdir(parents=True, exist_ok=True)
    csv_path = archive_root / f"chipk_main_force_{asof}.csv"
    json_path = archive_root / f"chipk_main_force_{asof}.json"
    df.to_csv(csv_path, index=False, encoding="utf-8-sig")

    payload.update(
        {
            "archived": True,
            "archive_csv": str(csv_path),
            "archive_json": str(json_path),
        }
    )
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if update_latest:
        latest_root = Path(latest_dir)
        latest_root.mkdir(parents=True, exist_ok=True)
        latest_csv = latest_root / "chipk_main_force_latest.csv"
        latest_json = latest_root / "chipk_main_force_latest.json"
        df.to_csv(latest_csv, index=False, encoding="utf-8-sig")
        payload.update({"latest_csv": str(latest_csv), "latest_json": str(latest_json)})
        latest_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Archive latest CMoney ChipK main-force snapshot")
    parser.add_argument("--archive-dir", default=str(DEFAULT_ARCHIVE_DIR))
    parser.add_argument("--latest-dir", default=str(DEFAULT_LATEST_DIR))
    parser.add_argument("--no-latest", action="store_true", help="Do not update latest pointer files")
    args = parser.parse_args()

    payload = archive_latest_chipk_snapshot(
        archive_dir=args.archive_dir,
        latest_dir=args.latest_dir,
        update_latest=not args.no_latest,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("archived") else 1


if __name__ == "__main__":
    raise SystemExit(main())
