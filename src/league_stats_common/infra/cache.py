"""Persistence layer: HTTP response cache.

:class:`HttpCache` — a :mod:`diskcache` wrapper that memoises raw API
responses (account lookups, match-id pages, static data) with TTLs.

The permanent raw match/timeline store this module used to also hold
(``MatchStore``, a :mod:`sqlite3` database) was deleted in Phase 8, Task 1 of
the microservices migration -- every real call site now uses the Mongo-backed
``RawMatchStore`` (``league_stats_runner/infra/raw_match_store.py``) instead.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import diskcache


class HttpCache:
    """TTL-based cache for raw HTTP responses backed by :mod:`diskcache`."""

    def __init__(self, directory: Path) -> None:
        """Open (or create) the cache.

        Args:
            directory: Directory where the cache files live.
        """
        directory.mkdir(parents=True, exist_ok=True)
        self._cache = diskcache.Cache(str(directory))

    def get(self, key: str) -> Any | None:
        """Fetch a cached value.

        Args:
            key: Cache key (typically the full request URL).

        Returns:
            The cached JSON-decoded payload, or ``None`` on a miss.
        """
        return self._cache.get(key)

    def set(self, key: str, value: Any, ttl_s: float | None = None) -> None:
        """Store a value.

        Args:
            key: Cache key.
            value: JSON-serialisable payload.
            ttl_s: Optional time-to-live in seconds (``None`` = forever).
        """
        self._cache.set(key, value, expire=ttl_s)

    def clear(self) -> None:
        """Drop every cached entry."""
        self._cache.clear()

    def close(self) -> None:
        """Close the underlying cache handle."""
        self._cache.close()
