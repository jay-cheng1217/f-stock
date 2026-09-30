import json
import os
import time

from backend.routers.warroom import _cache_is_newer_than_sources, _load_or_build


def test_load_or_build_uses_cache_when_fresh(tmp_path):
    cache = tmp_path / "feed.json"
    cache.write_text(json.dumps({"status": "cached"}), encoding="utf-8")

    result = _load_or_build(str(cache), lambda: {"status": "rebuilt"}, lambda payload: True)

    assert result == {"status": "cached"}


def test_load_or_build_rebuilds_when_stale(tmp_path):
    cache = tmp_path / "feed.json"
    cache.write_text(json.dumps({"status": "stale"}), encoding="utf-8")

    result = _load_or_build(str(cache), lambda: {"status": "rebuilt"}, lambda payload: False)

    assert result == {"status": "rebuilt"}


def test_load_or_build_rebuilds_when_cache_json_is_corrupt(tmp_path):
    cache = tmp_path / "feed.json"
    cache.write_text("{not json", encoding="utf-8")

    result = _load_or_build(str(cache), lambda: {"status": "rebuilt"})

    assert result == {"status": "rebuilt"}


def test_cache_is_newer_than_sources_uses_mtime(tmp_path):
    cache = tmp_path / "cache.json"
    source = tmp_path / "source.csv"
    cache.write_text("{}", encoding="utf-8")
    source.write_text("ticker\n2330\n", encoding="utf-8")

    now = time.time()
    os.utime(cache, (now - 10, now - 10))
    os.utime(source, (now, now))
    assert not _cache_is_newer_than_sources(str(cache), [str(source)])

    os.utime(cache, (now + 10, now + 10))
    assert _cache_is_newer_than_sources(str(cache), [str(source)])
