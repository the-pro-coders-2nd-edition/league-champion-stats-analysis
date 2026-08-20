"""MongoDB-backed peer-game cache for the peer-sampling service.

Mirrors the subset of ``infra.cache.MatchStore``'s method surface (see
``src/league_stats/infra/cache.py:304-435``) needed by
``analysis.peer.ingest``: ``upsert_peer_game``, ``load_peer_games``,
``count_peer_games``, ``iter_unverified_puuids``,
``iter_unverified_puuids_for_build``, ``set_puuid_rank``. Backed by
``pymongo.MongoClient`` (or ``mongomock.MongoClient`` in tests) instead of
SQLite.

Reproduces ``MatchStore``'s real semantics:

- ``upsert_peer_game`` dedups on the same key as the SQL ``UNIQUE
  (match_id, puuid, champion, role)`` constraint (``cache.py:40-54``) via
  an ``INSERT OR IGNORE`` (``cache.py:315``): inserting the same
  (match_id, puuid, champion, role) tuple again is a no-op and reports
  ``False``, even if other fields (metrics, tier, ...) differ.
- ``iter_unverified_puuids``/``iter_unverified_puuids_for_build``
  (``cache.py:394-419``) return **distinct** puuids that still have at
  least one ``rank_verified = 0`` peer row, capped at ``limit`` -- "still
  need rank backfill", not "every row is unverified".
- ``set_puuid_rank`` (``cache.py:421-433``) uppercases tier/rank and marks
  *every* peer row owned by that puuid as verified, regardless of which
  champion/role/match it came from. Its return value mirrors SQLite's
  ``cursor.rowcount`` for the ``UPDATE`` -- the count of rows *matched* by
  the ``WHERE puuid = ?`` clause, not the count of rows whose values
  actually changed, so calling it again with the same tier/rank still
  reports the same count instead of dropping to zero.
"""

from typing import Any

import pymongo


class PeerSampleStore:
    """MongoDB-backed store of peer-game benchmark rows."""

    def __init__(self, client: pymongo.MongoClient, db_name: str = "league_stats") -> None:
        """Open the store against an existing Mongo client.

        Args:
            client: A ``pymongo.MongoClient``-compatible client (real or
                ``mongomock.MongoClient`` in tests).
            db_name: Name of the database to use within the client.
        """
        db = client[db_name]
        self._peer_games = db["peer_games"]

    @staticmethod
    def _dedup_key(match_id: str, puuid: str, champion: str, role: str) -> str:
        return f"{match_id}\x1f{puuid}\x1f{champion}\x1f{role}"

    def upsert_peer_game(self, row: dict[str, Any]) -> bool:
        """Insert a peer game row when it is not already stored.

        Args:
            row: Peer game fields including a ``metrics`` dict.

        Returns:
            ``True`` when a new row was inserted.
        """
        match_id = row["match_id"]
        puuid = row["puuid"]
        champion = row["champion"]
        role = row["role"]
        doc = {
            "_id": self._dedup_key(match_id, puuid, champion, role),
            "match_id": match_id,
            "puuid": puuid,
            "champion": champion,
            "role": role,
            "tier": row.get("tier", ""),
            "rank": row.get("rank", ""),
            "platform": row["platform"],
            "queue_id": int(row["queue_id"]),
            "metrics": row["metrics"],
            "ingested_at": float(row["ingested_at"]),
            "rank_verified": bool(int(row.get("rank_verified", 0))),
            "patch": str(row.get("patch", "")),
        }
        try:
            self._peer_games.insert_one(doc)
        except pymongo.errors.DuplicateKeyError:
            return False
        return True

    @staticmethod
    def _to_row(doc: dict[str, Any]) -> dict[str, Any]:
        return {
            "match_id": doc["match_id"],
            "puuid": doc["puuid"],
            "champion": doc["champion"],
            "role": doc["role"],
            "tier": doc["tier"],
            "rank": doc["rank"],
            "platform": doc["platform"],
            "queue_id": doc["queue_id"],
            "metrics": doc["metrics"],
            "ingested_at": doc["ingested_at"],
            "rank_verified": bool(doc["rank_verified"]),
            "patch": doc["patch"],
        }

    def load_peer_games(
        self,
        *,
        champion: str,
        role: str,
        platform: str,
    ) -> list[dict[str, Any]]:
        """Load peer game rows for a champion + lane on one platform."""
        cursor = self._peer_games.find({"champion": champion, "role": role, "platform": platform})
        return [self._to_row(doc) for doc in cursor]

    def count_peer_games(
        self,
        *,
        champion: str,
        role: str,
        platform: str,
    ) -> int:
        """Count stored peer games for a champion + lane on one platform."""
        return self._peer_games.count_documents(
            {"champion": champion, "role": role, "platform": platform}
        )

    def iter_unverified_puuids(self, limit: int = 100) -> list[str]:
        """Return PUUIDs whose peer rows still need rank backfill."""
        return list(self._peer_games.distinct("puuid", {"rank_verified": False}))[:limit]

    def iter_unverified_puuids_for_build(
        self, champion: str, role: str, platform: str, limit: int = 200
    ) -> list[str]:
        """Return unverified PUUIDs scoped to one champion+lane build."""
        puuids = self._peer_games.distinct(
            "puuid",
            {
                "rank_verified": False,
                "champion": champion,
                "role": role,
                "platform": platform,
            },
        )
        return list(puuids)[:limit]

    def set_puuid_rank(self, puuid: str, tier: str, rank: str) -> int:
        """Backfill rank metadata for every peer row owned by one player."""
        result = self._peer_games.update_many(
            {"puuid": puuid},
            {"$set": {"tier": tier.upper(), "rank": rank.upper(), "rank_verified": True}},
        )
        return int(result.matched_count)
