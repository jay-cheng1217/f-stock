"""Fetch latest weekly TDCC holding distribution data.

This scheduled entry point stores the raw weekly file and then rebuilds
tdcc_summary.csv from all raw TDCC files. It never appends directly to the
summary, which prevents mixed schemas and Date/Ticker column drift.
"""
from __future__ import annotations

import io
import contextlib
import os
import secrets
import shutil
import sys
from pathlib import Path

import pandas as pd
import requests
import urllib3

if sys.platform == "win32":
    def _ensure_utf8_stream(stream):
        try:
            stream.reconfigure(encoding="utf-8")
            return stream
        except Exception:
            pass
        buffer = getattr(stream, "buffer", None)
        if buffer is None:
            return stream
        try:
            return io.TextIOWrapper(buffer, encoding="utf-8")
        except Exception:
            return stream

    sys.stdout = _ensure_utf8_stream(sys.stdout)
    sys.stderr = _ensure_utf8_stream(sys.stderr)

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BASE_DIR = os.environ.get("STOCK_BASE_DIR") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from scripts.backfill_tdcc import rebuild_summary
from scripts.tdcc_whale_radar import TDCCRadarDataError, date8, validate_raw_week, aggregate

TDCC_DIR = os.path.join(BASE_DIR, "集保分散")
os.makedirs(TDCC_DIR, exist_ok=True)

TDCC_API = "https://openapi.tdcc.com.tw/v1/opendata/1-5"

RAW_COLUMN_MAP = {
    "Date": "資料日期",
    "Ticker": "證券代號",
    "Level": "持股分級",
    "Holders": "人數",
    "Shares": "股數",
    "Pct": "占集保庫存數比例%",
}


def _strict_api_frame(raw: pd.DataFrame) -> pd.DataFrame:
    """Rename recognized headers without silently dropping rows or filling NaNs."""
    aliases = {
        "Date": ("資料日期", "Date"), "Ticker": ("證券代號", "Ticker"),
        "Level": ("持股分級", "Level", "HoldingSharesLevel"),
        "Holders": ("人數", "Holders", "People", "Numpeople"),
        "Shares": ("股數", "Shares", "HoldingShares"),
        "Pct": ("占集保庫存數比例%", "Percent", "Pct", "HoldingSharesPer"),
    }
    columns = {}
    for target, candidates in aliases.items():
        source = next((name for name in candidates if name in raw.columns), None)
        if source is None:
            raise TDCCRadarDataError(f"API required field missing: {target}")
        columns[source] = RAW_COLUMN_MAP[target]
    out = raw.rename(columns=columns)[list(RAW_COLUMN_MAP.values())].copy()
    out["資料日期"] = out["資料日期"].map(date8)
    return out


def _restore_inherited_acl(path):
    """Best-effort: put ``path`` back on the data directory's inherited ACL.

    Heals raw weeks that an elevated run published through tempfile.mkdtemp
    (Administrators-owned, unreadable to non-elevated sessions and the Codex
    sandbox). Only an owner/administrator can reset the DACL, so a failure here
    is logged and never blocks the fetch.
    """
    if os.name != "nt":
        return
    import subprocess
    try:
        result = subprocess.run(["icacls", str(path), "/reset"], capture_output=True, text=True, timeout=30)
    except Exception as exc:  # noqa: BLE001 - permission healing is advisory
        print(f"  ACL reset skipped for {path}: {exc}")
        return
    if result.returncode != 0:
        print(f"  ACL reset failed for {path} (rc={result.returncode}); needs an elevated run")


@contextlib.contextmanager
def _inherited_acl_staging_dir(directory, *, prefix):
    """Staging directory that inherits the parent's ACL (unlike tempfile.mkdtemp).

    Python 3.12+ ``tempfile.mkdtemp`` restricts the directory on Windows to
    SYSTEM / Administrators / owner. When the nightly task runs elevated the
    owner becomes the Administrators group, and a raw week published through
    ``os.replace`` keeps that DACL, so non-elevated sessions and the Codex
    sandbox can no longer read it (2026-09-07: tdcc_20260904.csv). A plain
    ``os.mkdir`` inherits the data directory's permissions instead.
    """
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    staging = directory / f"{prefix}{os.getpid()}_{secrets.token_hex(4)}"
    staging.mkdir()
    try:
        yield staging
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def fetch_and_save() -> bool:
    print("Fetching latest TDCC weekly data...", flush=True)
    response = requests.get(TDCC_API, timeout=60, verify=False)
    response.raise_for_status()
    data = response.json()
    if not data:
        print("  TDCC API returned no rows.")
        return False

    raw_df = pd.DataFrame(data)
    raw_df.columns = [str(col).replace("\ufeff", "").strip() for col in raw_df.columns]
    output = _strict_api_frame(raw_df)
    data_date = str(output["資料日期"].iloc[0])
    directory = Path(TDCC_DIR)
    raw_path = directory / f"tdcc_{data_date}.csv"
    history = sorted(directory.glob("tdcc_2???????.csv"))
    if history and data_date < history[-1].stem[5:]:
        raise TDCCRadarDataError(f"API week {data_date} is older than local {history[-1].stem[5:]}")
    # Validate the new response before replacing even an existing same-week file.
    # Re-fetching alone cannot heal a partially frozen file when existence is the gate.
    with _inherited_acl_staging_dir(directory, prefix=".tdcc_validate_") as staging:
        staged = Path(staging) / raw_path.name
        output.to_csv(staged, index=False, encoding="utf-8-sig")
        four, source = validate_raw_week(staged)
        aggregate(four)  # Semantic invariant, same level definitions as the radar.
        previous = [p for p in history if p.stem[5:] < data_date]
        if previous:
            _, prior = validate_raw_week(previous[-1])
            if source["raw_rows"] < prior["raw_rows"] * 0.8 or source["raw_tickers"] < prior["raw_tickers"] * 0.8:
                raise TDCCRadarDataError("API weekly market coverage below previous-week 80% floor")
        if raw_path.exists() and raw_path.read_bytes() == staged.read_bytes():
            print(f"  Raw file verified unchanged: {raw_path}")
        else:
            os.replace(staged, raw_path)
            print(f"  Validated raw file published: {raw_path} ({len(output):,} rows)", flush=True)
        _restore_inherited_acl(raw_path)

    rebuild_summary()
    return True


if __name__ == "__main__":
    try:
        ok = fetch_and_save()
        sys.exit(0 if ok else 1)
    except Exception as exc:
        print(f"  [ERROR] {exc}")
        sys.exit(1)
