"""
dht_storage.py — Project Antigravity
Disk-backed + LRU-memory local store for DHT chunks and manifests.
Thread-safe. Sharded directory layout for performance.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import OrderedDict
from typing import Dict, Optional

logger = logging.getLogger("antigravity.storage")

_BASE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_STORAGE_DIR  = os.path.join(_BASE, "dht_data")
DEFAULT_DOWNLOAD_DIR = os.path.join(_BASE, "downloads")
MAX_MEMORY_BYTES     = 128 * 1024 * 1024   # 128 MB LRU cache
DEFAULT_TTL          = 86_400              # 24 hours


class DHTStorage:
    """
    Thread-safe key-value store backed by disk with an in-memory LRU cache.

    Layout on disk::
        dht_data/
          ab/              ← shard = first 2 hex chars of key
            abcdef….bin    ← raw value bytes
            abcdef….meta   ← JSON: {expires_at, size}
    """

    def __init__(
        self,
        storage_dir: str = DEFAULT_STORAGE_DIR,
        max_memory_bytes: int = MAX_MEMORY_BYTES,
    ):
        self._dir      = storage_dir
        self._max_mem  = max_memory_bytes
        self._lock     = threading.Lock()
        self._cache: OrderedDict[str, bytes] = OrderedDict()
        self._cache_bytes = 0
        self._hits    = 0
        self._misses  = 0
        os.makedirs(self._dir, exist_ok=True)

    # ── Path helpers ────────────────────────────────────────────────────

    def _bin_path(self, key: str) -> str:
        shard = key[:2] if len(key) >= 2 else "00"
        d = os.path.join(self._dir, shard)
        os.makedirs(d, exist_ok=True)
        return os.path.join(d, f"{key}.bin")

    def _meta_path(self, key: str) -> str:
        return self._bin_path(key) + ".meta"

    # ── Public API ──────────────────────────────────────────────────────

    def store(self, key: str, value: bytes, ttl: int = DEFAULT_TTL) -> None:
        """Persist *value* under *key*. Thread-safe."""
        with self._lock:
            with open(self._bin_path(key), "wb") as fh:
                fh.write(value)
            with open(self._meta_path(key), "w") as fh:
                json.dump({"expires_at": time.time() + ttl, "size": len(value)}, fh)
            self._cache_put(key, value)
        logger.debug(f"[STORE] {key[:8]}… ({len(value)} bytes)")

    def get(self, key: str) -> Optional[bytes]:
        """Retrieve value for *key*. Returns None if missing or expired."""
        with self._lock:
            # 1. LRU cache hit
            if key in self._cache:
                self._cache.move_to_end(key)
                self._hits += 1
                return self._cache[key]

            # 2. Disk hit
            bin_path  = self._bin_path(key)
            meta_path = self._meta_path(key)
            if not os.path.exists(bin_path):
                self._misses += 1
                return None

            # Check TTL
            if os.path.exists(meta_path):
                try:
                    with open(meta_path) as fh:
                        meta = json.load(fh)
                    if time.time() > meta.get("expires_at", float("inf")):
                        self._evict_disk(key)
                        self._misses += 1
                        return None
                except Exception:
                    pass

            with open(bin_path, "rb") as fh:
                value = fh.read()
            self._cache_put(key, value)
            self._hits += 1
            return value

    def has(self, key: str) -> bool:
        return self.get(key) is not None

    def evict_expired(self) -> int:
        """Scan disk and remove expired entries. Returns count removed."""
        removed = 0
        now = time.time()
        for shard in os.listdir(self._dir):
            shard_dir = os.path.join(self._dir, shard)
            if not os.path.isdir(shard_dir):
                continue
            for fname in os.listdir(shard_dir):
                if not fname.endswith(".bin.meta"):
                    continue
                try:
                    with open(os.path.join(shard_dir, fname)) as fh:
                        meta = json.load(fh)
                    if now > meta.get("expires_at", float("inf")):
                        key = fname[:-9]          # strip ".bin.meta"
                        with self._lock:
                            self._evict_disk(key)
                            self._cache_evict(key)
                        removed += 1
                except Exception:
                    pass
        return removed

    def stats(self) -> dict:
        with self._lock:
            disk_bytes = 0
            disk_keys  = 0
            for shard in os.listdir(self._dir):
                sd = os.path.join(self._dir, shard)
                if not os.path.isdir(sd):
                    continue
                for fname in os.listdir(sd):
                    if fname.endswith(".bin"):
                        disk_keys  += 1
                        disk_bytes += os.path.getsize(os.path.join(sd, fname))
            return {
                "cache_entries": len(self._cache),
                "cache_bytes":   self._cache_bytes,
                "disk_keys":     disk_keys,
                "disk_bytes":    disk_bytes,
                "hits":          self._hits,
                "misses":        self._misses,
            }

    # ── Internal ────────────────────────────────────────────────────────

    def _cache_put(self, key: str, value: bytes) -> None:
        """Insert into LRU cache, evicting LRU entries to stay under limit."""
        if key in self._cache:
            self._cache_bytes -= len(self._cache[key])
            del self._cache[key]
        self._cache[key] = value
        self._cache.move_to_end(key)
        self._cache_bytes += len(value)
        # Evict LRU until under limit
        while self._cache_bytes > self._max_mem and self._cache:
            _, v = self._cache.popitem(last=False)
            self._cache_bytes -= len(v)

    def _cache_evict(self, key: str) -> None:
        if key in self._cache:
            self._cache_bytes -= len(self._cache[key])
            del self._cache[key]

    def _evict_disk(self, key: str) -> None:
        for p in (self._bin_path(key), self._meta_path(key)):
            try:
                os.remove(p)
            except FileNotFoundError:
                pass
