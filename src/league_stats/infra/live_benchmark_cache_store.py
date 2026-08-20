"""MongoDB-backed store for PEERS' live-sampled peer-benchmark cache.

Deliberately its own store rather than reusing `PeerSampleStore`
(`infra/peer_sample_store.py`), even though both talk to the same
`MONGO_URI`/database in production: `PeerSampleStore.__init__` creates
indexes on the unrelated `peer_games` collection as a side effect of
construction, which a plain live-cache read/write should never trigger
(finding of the review round after Phase 5's Task 3 first landed). This
store only ever touches `live_benchmark_cache`.

This is a plain key -> document store -- `find_one`/`replace_one(upsert=True)`
keyed on a caller-supplied string key. All staleness logic (patch match, tier
match via the key, TTL, min-games gate) stays in
`analysis.peer.benchmark_cache`, which is also the sole caller of this store;
see that module's docstring for the exact semantics it reproduces from the
pre-migration on-disk JSON cache.
"""

from __future__ import annotations

import datetime
from typing import Any

import pymongo


class LiveBenchmarkCacheStore:
    """MongoDB-backed key -> document store for the PEERS live-benchmark cache."""

    def __init__(
        self,
        client: pymongo.MongoClient,
        db_name: str = "league_stats",
        *,
        ttl_seconds: float | None = None,
    ) -> None:
        """Open the store against an existing Mongo client.

        Args:
            client: A ``pymongo.MongoClient``-compatible client (real or
                ``mongomock.MongoClient`` in tests).
            db_name: Name of the database to use within the client.
            ttl_seconds: When given, a MongoDB TTL index is created on
                ``fetched_at_dt`` so documents older than this many seconds
                are automatically dropped by Mongo itself -- the old on-disk
                JSON cache had no equivalent and simply accumulated files
                forever. Callers should pass some margin above their own
                staleness TTL (``analysis.peer.benchmark_cache.CACHE_TTL_S``)
                so this index only ever prunes entries the read-path logic
                would already refuse to serve, never one it would still
                consider fresh. ``create_index`` is idempotent and
                ``mongomock`` supports ``expireAfterSeconds``, so this is
                safe to call on every construction, including in tests.

                Note: this index is created on ``fetched_at_dt`` (a BSON
                ``Date`` `write` stamps on every document below), not on the
                caller-supplied ``fetched_at`` float the rest of this module's
                staleness comparisons use -- MongoDB's TTL monitor only acts
                on BSON ``Date`` values; a numeric field is accepted at
                index-creation time but silently never expires anything on a
                real MongoDB (found in Phase 5's final review; ``mongomock``
                does not reproduce this restriction, which is how the earlier
                float-only version went unnoticed).
        """
        db = client[db_name]
        self._cache = db["live_benchmark_cache"]
        if ttl_seconds is not None:
            self._cache.create_index("fetched_at_dt", expireAfterSeconds=int(ttl_seconds))

    def read(self, key: str) -> dict[str, Any] | None:
        """Return the raw cached document for `key`, or None if absent."""
        doc = self._cache.find_one({"_id": key})
        if doc is None:
            return None
        return {k: v for k, v in doc.items() if k not in ("_id", "fetched_at_dt")}

    def write(self, key: str, data: dict[str, Any]) -> None:
        """Upsert the cached document for `key`.

        Stamps ``fetched_at_dt`` (a BSON ``Date``, UTC) alongside whatever
        ``data`` carries, purely so the TTL index above has a field it can
        actually act on -- staleness itself is still decided by
        ``analysis.peer.benchmark_cache`` off the caller-supplied
        ``fetched_at`` float, unchanged.
        """
        doc = {"_id": key, **data, "fetched_at_dt": datetime.datetime.now(datetime.timezone.utc)}
        self._cache.replace_one({"_id": key}, doc, upsert=True)
