"""Source lineage for the canonical, pre-model latest feature snapshot.

Reuse checks inspect the full file set and nanosecond stat metadata, without
rehashing gigabytes for every API request. Publication records full source hashes
and the serialized snapshot hash. Model selection is a separate prediction
context: model weights do not participate in build_latest_snapshot.
"""
from __future__ import annotations

from datetime import datetime, timezone
import glob
import hashlib
import importlib
import json
import os
from pathlib import Path
import pickle
import stat
import tempfile

SCHEMA = 1


def canonical_source_patterns() -> list[str]:
    from ml import dataset, corporate_actions, universe
    from ml.config import BASE_DIR
    from ml.features import sector, industry
    base = Path(BASE_DIR)
    code = Path(__file__).resolve().parents[1]
    paths = list(dataset._raw_cache_source_patterns())
    paths.extend(str(path) for path in [
        corporate_actions.DEFAULT_CALENDAR_PATH,
        corporate_actions.DEFAULT_CORPORATE_ACTION_PATH,
        universe.ETF_UNIVERSE_PATH, universe.RETIRED_TICKERS_PATH,
        sector.SECTOR_MAPPING_PATH, industry.SECTOR_MAPPING_PATH,
        base/'清理後資料/*.csv',
        code/'ml/features/*.py', code/'backend/features/*.py',
        code/'ml/config.py', code/'ml/dataset.py', code/'ml/target.py',
        code/'ml/universe.py', code/'ml/corporate_actions.py',
        code/'ml/snapshot_lineage.py',
    ])
    return sorted(set(os.path.normcase(os.path.abspath(path)) for path in paths))


def _json_hash(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')).hexdigest()


def _file_hash(path) -> str:
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for block in iter(lambda: handle.read(2**20), b''):
            digest.update(block)
    return digest.hexdigest()


def source_state(*, include_hashes=False, patterns=None) -> dict:
    """A missing/new file changes the set even if dates and row counts agree."""
    from ml.features.registry import get_available_features
    patterns = sorted(set(patterns if patterns is not None else canonical_source_patterns()))
    paths = sorted({os.path.abspath(path) for pattern in patterns for path in glob.glob(pattern)})
    files = []
    for path in paths:
        before = os.stat(path)
        if not stat.S_ISREG(before.st_mode):
            continue
        item = {'path': path, 'bytes': before.st_size, 'mtime_ns': before.st_mtime_ns}
        if include_hashes:
            item['sha256'] = _file_hash(path)
            after = Path(path).stat()
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise RuntimeError(f'Snapshot source changed while hashing: {path}')
        files.append(item)
    directories = [{'path': directory, 'exists':Path(directory).is_dir()} for directory in sorted({str(Path(pattern).parent) for pattern in patterns})]
    fast = [{key:value for key,value in item.items() if key != 'sha256'} for item in files]
    available_groups = sorted(get_available_features())
    return {'schema': SCHEMA, 'patterns': patterns, 'files': files,
            'available_feature_groups': available_groups,
            'signature': _json_hash({'patterns': patterns, 'directories':directories, 'files': fast,
                                     'available_feature_groups': available_groups}),
            'content_signature': _json_hash(files) if include_hashes else None}


def assert_sources_unchanged(before: dict) -> None:
    after = source_state(patterns=before['patterns'])
    if before['signature'] != after['signature']:
        raise RuntimeError('Canonical sources changed during snapshot construction; refusing to certify this snapshot')


def clear_source_memo_caches() -> None:
    """Discard source IO memoization before rebuilding in a long-lived API."""
    dictionaries = {
        'ml.features.fundamental': '_FINANCIAL_CACHE',
        'ml.features.eps': '_EPS_CACHE', 'ml.features.balance_sheet': '_BS_CACHE',
        'ml.features.market': '_MARKET_FEATURES_CACHE',
        'ml.features.valuation': '_VALUATION_CACHE',
        'ml.features.news': '_NEWS_CACHE', 'ml.features.tdcc': '_TDCC_CACHE',
    }
    for name, attribute in dictionaries.items():
        getattr(importlib.import_module(name), attribute).clear()
    from ml import dataset, corporate_actions, universe
    from backend.features.market_regime import build_market_regime_features
    dataset._ISSUED_SHARES_CACHE = None
    corporate_actions.load_action_calendar.cache_clear()
    corporate_actions.load_price_continuity_calendar.cache_clear()
    universe.load_etf_universe.cache_clear()
    universe.load_retired_tickers.cache_clear()
    build_market_regime_features.cache_clear()


def save_snapshot(snapshot, path, before: dict, *, manifest_path=None, metadata=None) -> dict:
    """Atomically publish bytes then their certificate; interrupted pairs reject."""
    path = Path(path)
    manifest_path = Path(manifest_path or str(path)+'.manifest.json')
    if not before.get('content_signature'):
        raise ValueError('Snapshot publication requires full source hash evidence')
    assert_sources_unchanged(before)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, tmp_name = tempfile.mkstemp(prefix=path.name+'.', suffix='.tmp', dir=path.parent)
    os.close(descriptor)
    tmp_path = Path(tmp_name)
    meta_tmp = Path(tmp_name+'.json')
    try:
        snapshot.to_pickle(tmp_path)
        manifest = {'schema': SCHEMA, 'scope': 'canonical_pre_model_snapshot',
                    'created_at': datetime.now(timezone.utc).isoformat(),
                    'rows': int(len(snapshot)), 'sources': before,
                    'snapshot_sha256': _file_hash(tmp_path), 'metadata': metadata or {}}
        meta_tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
        assert_sources_unchanged(before)
        os.replace(tmp_path, path)
        os.replace(meta_tmp, manifest_path)
        return manifest
    finally:
        tmp_path.unlink(missing_ok=True)
        meta_tmp.unlink(missing_ok=True)


def load_snapshot(path, *, manifest_path=None, expected_metadata=None):
    """Return None on legacy, changed, corrupt or incomplete cache artifacts."""
    path = Path(path)
    manifest_path = Path(manifest_path or str(path)+'.manifest.json')
    try:
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        if manifest.get('schema') != SCHEMA or manifest.get('scope') != 'canonical_pre_model_snapshot':
            return None
        if expected_metadata is not None and manifest.get('metadata') != expected_metadata:
            return None
        sources = manifest['sources']
        if sources.get('content_signature') != _json_hash(sources['files']) or any(len(item.get('sha256', '')) != 64 for item in sources['files']):
            return None
        current = source_state()
        if current['signature'] != manifest['sources']['signature']:
            return None
        blob = path.read_bytes()
        if hashlib.sha256(blob).hexdigest() != manifest['snapshot_sha256']:
            return None
        snapshot = pickle.loads(blob)
        if len(snapshot) != manifest['rows']:
            return None
        assert_sources_unchanged(current)
        return snapshot
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RuntimeError, pickle.UnpicklingError, EOFError):
        return None
