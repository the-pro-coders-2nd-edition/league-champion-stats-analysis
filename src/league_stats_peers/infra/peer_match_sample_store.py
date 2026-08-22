"""MongoDB-backed shared cross-champion, cross-tier match-participant cache.

RFC "Batched, Round-Robin Live Sampling for PEERS", Phase 2 (`peer_match_samples`):
every match downloaded during ANY sampling task's snowball scan has ~10 participants;
before this store existed, only the task's own (champion, role) target was extracted
and the rest were thrown away even though the match was already fully paid for (one
Riot API call) and sitting in memory. This store keeps one document per
``(match_id, participant puuid)``, holding the raw per-participant stat row -- keyed
for lookup by ``(platform, patch, champion, role)`` -- so a *different* task, sampling
a *different* champion (or the same champion at a different tier), can find rows for
its own target for free instead of re-downloading the same match from Riot.

Deliberately tier-agnostic at write time (no rank field until resolved on read): most
rows will never be read by anything, so resolving every participant's rank up front
would spend one extra Riot call per participant for data that is thrown away far more
often than it is used. Rank is resolved lazily, only when a later task's own scan
queries this store for its ``(platform, patch, champion, role)`` key and needs to know
whether a candidate's rank falls inside its tier scope (see
``analysis.peer.sampling_task.SamplingTask._check_shared_cache``).

Expiry mirrors ``analysis.peer.benchmark_cache``'s existing pattern: a `ttl_seconds`
TTL index on a BSON `Date` field, kept past the point any patch-based staleness check
would still trust it, purely as a housekeeping backstop against unbounded growth.
"""

from __future__ import annotations

import datetime
from typing import Any, Iterable

import pymongo


class PeerMatchSampleStore:
    """MongoDB-backed store for cross-champion/cross-tier raw participant rows."""

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
                ``stored_at_dt`` so documents older than this many seconds are
                dropped automatically -- callers should pass some margin above
                their own staleness window (patch changes make an entry
                useless well before this fires), same convention as
                ``LiveBenchmarkCacheStore``.
        """
        db = client[db_name]
        self._samples = db["peer_match_samples"]
        # Primary lookup shape: "give me every row seen for this champion+role
        # on this platform+patch" (RFC §6) -- unscoped by tier, since rank is
        # resolved lazily on read, not at write time.
        self._samples.create_index([("platform", 1), ("patch", 1), ("champion", 1), ("role", 1)])
        self._samples.create_index([("match_id", 1), ("puuid", 1)], unique=True)
        if ttl_seconds is not None:
            self._samples.create_index("stored_at_dt", expireAfterSeconds=int(ttl_seconds))

    def upsert_rows(
        self,
        match_id: str,
        patch: str,
        platform: str,
        rows: Iterable[dict[str, Any]],
    ) -> None:
        """Store one row per participant, each already tagged with ``champion``/``role``.

        Re-storing the same ``(match_id, puuid)`` pair (e.g. a second task
        downloading the same match) is an idempotent upsert, not a duplicate.
        """
        now = datetime.datetime.now(datetime.timezone.utc)
        for row in rows:
            puuid = str(row.get("puuid", ""))
            if not puuid:
                continue
            doc = {
                "match_id": match_id,
                "puuid": puuid,
                "platform": platform.lower(),
                "patch": patch,
                "champion": row["champion"],
                "role": row["role"],
                "row": dict(row),
                "stored_at_dt": now,
            }
            self._samples.replace_one(
                {"match_id": match_id, "puuid": puuid}, doc, upsert=True
            )

    def find_candidates(
        self,
        *,
        platform: str,
        patch: str,
        champion: str,
        role: str,
    ) -> list[dict[str, Any]]:
        """Return every stored row for a ``(platform, patch, champion, role)`` key.

        Not scoped by tier -- rank is resolved lazily by the caller for
        whichever candidates it actually wants to use (RFC §5.2).
        """
        cursor = self._samples.find(
            {"platform": platform.lower(), "patch": patch, "champion": champion, "role": role}
        )
        return [
            {
                "match_id": doc["match_id"],
                "puuid": doc["puuid"],
                "row": dict(doc["row"]),
            }
            for doc in cursor
        ]
