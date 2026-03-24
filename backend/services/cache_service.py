"""TTL 快取服務."""

import time
from functools import wraps
from backend.config import CACHE_TTL_SECONDS

_cache: dict[str, tuple[float, object]] = {}


def cached(key_prefix: str, ttl: int = CACHE_TTL_SECONDS):
    """裝飾器：結果快取 ttl 秒."""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            cache_key = f"{key_prefix}:{args}:{kwargs}"
            now = time.time()
            if cache_key in _cache:
                ts, val = _cache[cache_key]
                if now - ts < ttl:
                    return val
            result = func(*args, **kwargs)
            _cache[cache_key] = (now, result)
            return result
        return wrapper
    return decorator


def invalidate(key_prefix: str | None = None):
    """清除快取。若指定 prefix 則只清除該前綴。"""
    global _cache
    if key_prefix is None:
        _cache = {}
    else:
        _cache = {k: v for k, v in _cache.items() if not k.startswith(key_prefix)}
