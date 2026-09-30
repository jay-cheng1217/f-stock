"""Abort cleanup if a target path is a protected model artifact.

This guard covers production pins, model-selection references, and model
bundles produced by `TW_Stock_Weekly_Retrain`.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.model_pin_registry import blocked_cleanup_paths


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Pre-check cleanup targets against pinned model artifacts.")
    parser.add_argument("paths", nargs="+", help="Candidate files that a cleanup job wants to delete")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    blocked = blocked_cleanup_paths(args.paths)
    if blocked:
        print("[cleanup-precheck] ABORT: cleanup target includes protected model artifacts")
        for path in blocked:
            print(f"- {path}")
        return 1
    print(f"[cleanup-precheck] PASS: {len(args.paths)} cleanup target(s) are not protected model artifacts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
