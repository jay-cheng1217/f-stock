"""Bind a successfully generated live canonical plan to its actual inputs."""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
import uuid

import pandas as pd

from ml import snapshot_lineage as lineage
from ml.prediction_provenance import load_prediction_csv

SCHEMA = 1
INTERNAL_RESULT = "_entry_generation_certificate"
INTERNAL_PAYLOAD = "_entry_publication_certificate"
PROOF = "artifact_lineage"


class EntryArtifactLineageError(ValueError):
    """The plan cannot be established as the current generated artifact."""


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":")).encode("utf-8")).hexdigest()


def _receipt(path):
    path = Path(path).resolve()
    if not path.is_file():
        return {"path": str(path), "exists": False}
    before = path.stat()
    blob = path.read_bytes()
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise EntryArtifactLineageError(f"Input changed while reading: {path}")
    return {"path": str(path), "exists": True, "bytes": len(blob), "mtime_ns": after.st_mtime_ns,
            "sha256": hashlib.sha256(blob).hexdigest()}


def _generator():
    from scripts import generate_entry_candidates
    return generate_entry_candidates


def _live_dates():
    from scripts.taiwan_trading_calendar import is_taiwan_trading_day, previous_taiwan_trading_day, next_taiwan_trading_day
    now = datetime.now()
    close = now.date() if is_taiwan_trading_day(now) and now.hour >= 18 else previous_taiwan_trading_day(now)
    return close.isoformat(), next_taiwan_trading_day(close).isoformat()


def _extra_patterns():
    gen = _generator()
    from scripts.disposition_contract import CORRECTIONS_PATH
    from scripts.taiwan_trading_calendar import ADHOC_CLOSURES_PATH
    code = Path(__file__).resolve().parents[1]
    paths = [gen.SECTOR_MAP, CORRECTIONS_PATH, ADHOC_CLOSURES_PATH,
        gen.BASE_DIR / "ml/data/sector_strength.json", gen.BASE_DIR / "ml/data/disposition_radar.json",
        gen.BASE_DIR / "ml/data/disposition_periods.csv", gen.BASE_DIR / "ml/reports/special_stock_status_latest.json",
        gen.BASE_DIR / "logs/entry_watchlist.json", gen.BASE_DIR / "logs/entry_watchlist_archive.json",
        gen.BASE_DIR / "新聞資料/announcements_*.csv", gen.BASE_DIR / "大盤指數/index_TWII.csv"]
    paths += [code / name for name in ("scripts/generate_entry_candidates.py", "scripts/entry_artifact_lineage.py",
        "scripts/momentum_continuation_backtest.py", "scripts/disposition_contract.py",
        "scripts/taiwan_trading_calendar.py", "ml/snapshot_lineage.py", "ml/prediction_provenance.py")]
    if gen._chipk_desktop_enabled():
        paths += [gen.CHIPK_ARCHIVE_DIR / "chipk_main_force_*.csv",
                  gen.REPORT_DIR / "chipk_model_diagnosis_*.csv", gen.MODEL_DIR / "chipk_model_diagnosis_*.csv"]
    return sorted(set(str(Path(path).resolve()) for path in paths))


def _context():
    gen = _generator()
    return {"base_dir": str(gen.BASE_DIR.resolve()), "chipk_desktop_enabled": gen._chipk_desktop_enabled()}


def _validate_state(state):
    if (state.get("content_signature") != lineage._json_hash(state["files"])
            or any(len(row.get("sha256", "")) != 64 for row in state["files"])):
        raise EntryArtifactLineageError("Incomplete source evidence")
    lineage.assert_sources_unchanged(state)


def _validate_selected(run):
    gen = _generator()
    selected = gen._latest_predictions_path(pd.Timestamp(run["as_of_date"]).date())
    if not selected or str(Path(selected).resolve()) != run["prediction"]["path"]:
        raise EntryArtifactLineageError("Selected prediction artifact changed")
    for name in ("prediction", "prediction_manifest", "snapshot", "snapshot_manifest"):
        if _receipt(run[name]["path"]) != run[name]:
            raise EntryArtifactLineageError(f"Canonical {name} changed")
    loaded = load_prediction_csv(selected, expected_model_slot=run["model_slot"])
    snapshot = lineage.load_snapshot(run["snapshot"]["path"])
    if loaded is None or snapshot is None:
        raise EntryArtifactLineageError("Canonical prediction/snapshot certificate rejected")
    if pd.to_datetime(snapshot["Date"]).max().strftime("%Y-%m-%d") != run["as_of_date"]:
        raise EntryArtifactLineageError("Canonical snapshot date mismatch")
    if run["as_of_date"] not in Path(selected).name:
        raise EntryArtifactLineageError("Canonical prediction filename date mismatch")


def _assert_current(run, *, check_extras=True):
    if run.get("schema") != SCHEMA or run.get("scope") != "successful_live_entry_generation":
        raise EntryArtifactLineageError("Invalid entry generation certificate")
    if (run["as_of_date"], run["trade_date"]) != _live_dates():
        raise EntryArtifactLineageError("Historical or noncurrent entry plan cannot be used as a live artifact")
    if run["context"] != _context():
        raise EntryArtifactLineageError("Entry generation context changed")
    if lineage.source_state()["signature"] != run["sources"]["signature"]:
        raise EntryArtifactLineageError("Canonical entry sources changed")
    _validate_state(run["sources"])
    if run["extra_inputs"]["patterns"] != _extra_patterns():
        raise EntryArtifactLineageError("Entry dependency set changed")
    if check_extras:
        _validate_state(run.get("extras_current", run["extra_inputs"]))
    _validate_selected(run)


def capture_generation(as_of, trade_date, *, persist_watchlist=False, include_live=True):
    gen = _generator()
    from scripts.taiwan_trading_calendar import previous_taiwan_trading_day
    target = pd.Timestamp(trade_date if trade_date else _live_dates()[1]).date()
    required = pd.Timestamp(as_of).date() if as_of else previous_taiwan_trading_day(target)
    if (required.isoformat(), target.isoformat()) != _live_dates():
        raise EntryArtifactLineageError("Certified generation is restricted to the current live close and next trade date")
    selected = gen._latest_predictions_path(required)
    if not selected:
        raise EntryArtifactLineageError("No current selected prediction artifact")
    selected = Path(selected).resolve()
    snapshot = gen.MODEL_DIR / "snapshot_cache.pkl"
    run = {"schema": SCHEMA, "scope": "successful_live_entry_generation", "run_id": uuid.uuid4().hex,
        "started_at": datetime.now().astimezone().isoformat(), "as_of_date": required.isoformat(),
        "trade_date": target.isoformat(), "context": _context(),
        "model_slot": "dataA" if selected.name.startswith("dataA_predictions_") else "production",
        "prediction": _receipt(selected), "prediction_manifest": _receipt(str(selected) + ".manifest.json"),
        "snapshot": _receipt(snapshot), "snapshot_manifest": _receipt(str(snapshot) + ".manifest.json"),
        "sources": lineage.source_state(include_hashes=True),
        "extra_inputs": lineage.source_state(include_hashes=True, patterns=_extra_patterns()),
        "persist_watchlist": persist_watchlist, "include_live": include_live, "authorized_outputs": []}
    _assert_current(run)
    return run


@contextmanager
def observe_watchlist_writes(run):
    """Receipt only the existing generator's two authorized feedback writes."""
    if not run["persist_watchlist"]:
        yield
        return
    gen = _generator()
    allowed = {str((gen.BASE_DIR / "logs" / name).resolve()) for name in
               ("entry_watchlist.json", "entry_watchlist_archive.json")}
    original = Path.write_text
    owner = threading.get_ident()
    def observed(path, data, encoding=None, errors=None, newline=None):
        resolved = str(path.resolve())
        if resolved not in allowed:
            return original(path, data, encoding=encoding, errors=errors, newline=newline)
        if threading.get_ident() != owner:
            raise EntryArtifactLineageError("Concurrent watchlist writer during generation")
        before = _receipt(path)
        prior = next((row["after"] for row in reversed(run["authorized_outputs"]) if row["path"] == resolved), None)
        if prior is None:
            evidence = next((row for row in run["extra_inputs"]["files"] if row["path"] == resolved), None)
            if evidence is None:
                if before["exists"]:
                    raise EntryArtifactLineageError("Watchlist appeared during generation")
            elif (before.get("sha256"), before.get("mtime_ns")) != (evidence["sha256"], evidence["mtime_ns"]):
                raise EntryArtifactLineageError("Watchlist input changed before its authorized write")
        elif before != prior:
            raise EntryArtifactLineageError("Watchlist changed between authorized writes")
        translated = data.replace("\n", os.linesep if newline is None else newline) if newline not in ("", "\n") else data
        expected_sha = hashlib.sha256(translated.encode(encoding or "utf-8", errors or "strict")).hexdigest()
        result = original(path, data, encoding=encoding, errors=errors, newline=newline)
        after = _receipt(path)
        if after.get("sha256") != expected_sha:
            raise EntryArtifactLineageError("Watchlist write differs from the generator's requested bytes")
        run["authorized_outputs"].append({"path": resolved, "before": before, "after": after,
                                          "expected_written_sha256": expected_sha})
        return result
    Path.write_text = observed
    try:
        yield
    finally:
        Path.write_text = original


def bind_generation(run, result):
    _assert_current(run, check_extras=False)
    after = lineage.source_state(include_hashes=True, patterns=_extra_patterns())
    authorized = {row["path"]: row for row in run["authorized_outputs"]}
    before_files = {row["path"]: row for row in run["extra_inputs"]["files"]}
    after_files = {row["path"]: row for row in after["files"]}
    for path in set(before_files) | set(after_files):
        before, current = before_files.get(path), after_files.get(path)
        if before == current:
            continue
        own = authorized.get(path)
        if own is None or current is None or (current["sha256"], current["mtime_ns"]) != (own["after"]["sha256"], own["after"]["mtime_ns"]):
            raise EntryArtifactLineageError(f"Entry dependency changed during generation: {path}")
    if (result.get("as_of_date"), result.get("trade_date")) != (run["as_of_date"], run["trade_date"]):
        raise EntryArtifactLineageError("Actual generated result dates mismatch")
    if not result.get("as_of_close") or str(Path(result["as_of_close"]).resolve()) != run["prediction"]["path"]:
        raise EntryArtifactLineageError("Actual generation used a different prediction artifact")
    if not isinstance(result.get("rows"), list):
        raise EntryArtifactLineageError("Actual generated result lacks candidate rows")
    bound = deepcopy(run)
    bound.update(extras_current=after, result_sha256=_digest(result), rows=len(result["rows"]),
                 completed_at=datetime.now().astimezone().isoformat())
    _assert_current(bound)
    return {**result, INTERNAL_RESULT: bound}


def bind_payload(result, payload):
    run = result.get(INTERNAL_RESULT)
    if run is None:
        return payload  # Raw research conversion is unverified and cannot publish.
    raw = {key: value for key, value in result.items() if key != INTERNAL_RESULT}
    if _digest(raw) != run["result_sha256"]:
        raise EntryArtifactLineageError("Generated result was changed before conversion")
    _assert_current(run)
    if (payload.get("as_of_date"), payload.get("trade_date"), payload.get("model_source_date")) != (
            run["as_of_date"], run["trade_date"], run["as_of_date"]):
        raise EntryArtifactLineageError("Published payload dates mismatch")
    if payload.get("model_source") != Path(run["prediction"]["path"]).name or len(payload.get("rows", [])) != run["rows"]:
        raise EntryArtifactLineageError("Published payload model source or candidate count mismatch")
    certificate = deepcopy(run)
    certificate["payload_sha256"] = _digest(payload)
    compact = {key: certificate[key] for key in ("schema", "scope", "run_id", "as_of_date", "trade_date", "result_sha256", "payload_sha256")}
    return {**payload, PROOF: compact, INTERNAL_PAYLOAD: certificate}


def publish_entry_artifact(path, payload):
    certificate = payload.get(INTERNAL_PAYLOAD)
    if certificate is None:
        raise EntryArtifactLineageError("Cannot publish or certify a plan without successful generation evidence")
    clean = {key: value for key, value in payload.items() if key != INTERNAL_PAYLOAD}
    body = {key: value for key, value in clean.items() if key != PROOF}
    if _digest(body) != certificate["payload_sha256"]:
        raise EntryArtifactLineageError("Entry payload changed before publication")
    if clean.get(PROOF) != {key: certificate[key] for key in
            ("schema", "scope", "run_id", "as_of_date", "trade_date", "result_sha256", "payload_sha256")}:
        raise EntryArtifactLineageError("Entry compact proof changed before publication")
    _assert_current(certificate)
    blob = json.dumps(clean, ensure_ascii=False, indent=2).encode("utf-8")
    certificate = {**certificate, "json_sha256": hashlib.sha256(blob).hexdigest()}
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, filename = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    os.close(fd)
    temporary, metadata = Path(filename), Path(filename + ".json")
    try:
        temporary.write_bytes(blob)
        metadata.write_text(json.dumps(certificate, ensure_ascii=False, indent=2), encoding="utf-8")
        _assert_current(certificate)
        os.replace(temporary, path)
        os.replace(metadata, Path(str(path) + ".manifest.json"))
    finally:
        temporary.unlink(missing_ok=True)
        metadata.unlink(missing_ok=True)


def load_entry_artifact(path):
    """Return only the verified payload; never convert stale data to empty rows."""
    path = Path(path)
    try:
        manifest = Path(str(path) + ".manifest.json")
        meta_blob = manifest.read_bytes()
        certificate = json.loads(meta_blob)
        _assert_current(certificate)
        blob = path.read_bytes()
        if hashlib.sha256(blob).hexdigest() != certificate["json_sha256"]:
            raise EntryArtifactLineageError("Entry JSON differs from its generation certificate")
        payload = json.loads(blob)
        body = {key: value for key, value in payload.items() if key != PROOF}
        if _digest(body) != certificate["payload_sha256"] or payload.get(PROOF) != {
                key: certificate[key] for key in ("schema", "scope", "run_id", "as_of_date", "trade_date", "result_sha256", "payload_sha256")}:
            raise EntryArtifactLineageError("Entry payload proof is missing or inconsistent")
        if (payload.get("as_of_date"), payload.get("model_source_date"), payload.get("trade_date")) != (
                certificate["as_of_date"], certificate["as_of_date"], certificate["trade_date"]):
            raise EntryArtifactLineageError("Entry plan source/trade dates mismatch")
        if payload.get("model_source") != Path(certificate["prediction"]["path"]).name or len(payload.get("rows", [])) != certificate["rows"]:
            raise EntryArtifactLineageError("Entry plan model source or candidate count mismatch")
        _assert_current(certificate)
        if manifest.read_bytes() != meta_blob or path.read_bytes() != blob:
            raise EntryArtifactLineageError("Entry artifact changed during validation")
        return payload
    except EntryArtifactLineageError:
        raise
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RuntimeError) as exc:
        raise EntryArtifactLineageError(f"Entry generation lineage rejected or absent: {exc}") from exc
