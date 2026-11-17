"""Tiny in-process TTL cache with request coalescing (ported from main.py).

Mutating endpoints call cache_invalidate() with key prefixes after writes.
"""
import asyncio
import time
from typing import Any

_cache: dict[str, tuple[float, Any]] = {}
# Locks are per (event loop, key) so coalescing works under any server mode.
_cache_locks: dict[tuple[int, str], asyncio.Lock] = {}


def cache_get(key: str, ttl: int):
    """Return the cached value for key if still within ttl, else None."""
    entry = _cache.get(key)
    if not entry:
        return None
    ts, value = entry
    if time.time() - ts > ttl:
        _cache.pop(key, None)
        return None
    return value


def cache_set(key: str, value: Any):
    _cache[key] = (time.time(), value)


def cache_invalidate(*prefixes: str):
    """Drop every cache entry whose key starts with any given prefix."""
    if not prefixes:
        _cache.clear()
        return
    for k in list(_cache.keys()):
        if any(k.startswith(p) for p in prefixes):
            _cache.pop(k, None)


async def cached_call(key: str, ttl: int, producer):
    """TTL cache with per-key locking to collapse duplicate concurrent misses.

    If several requests miss the same key at once, only ONE upstream call is
    made; the others await the same result (request coalescing)."""
    hit = cache_get(key, ttl)
    if hit is not None:
        return hit

    loop_key = (id(asyncio.get_running_loop()), key)
    lock = _cache_locks.setdefault(loop_key, asyncio.Lock())
    async with lock:
        # Re-check after acquiring the lock (another task may have filled it).
        hit = cache_get(key, ttl)
        if hit is not None:
            return hit
        value = await producer()
        cache_set(key, value)
        return value
