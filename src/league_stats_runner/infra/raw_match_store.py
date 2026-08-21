"""MongoDB-backed raw match/timeline store for the RUNNER service.

Mirrors the subset of ``infra.cache.MatchStore``'s method surface (see
``src/league_stats/infra/cache.py:104``) that RUNNER needs: ``has_match``,
``save_match``, ``save_timeline``, ``load_match``, ``load_timeline``,
``claim_ownership``, ``iter_all_match_ids``, ``count``, ``iter_match_ids``,
``close``. Backed by ``pymongo.MongoClient`` (or ``mongomock.MongoClient`` in
tests).

Phase 5 Task 1 of the microservices migration added ``iter_match_ids``/
``close``: without them, RUNNER's job pipeline crashed with
``AttributeError`` inside ``discover_build_pools``/``load_all_records``
(stage A, called on every job unconditionally). Deliberately still NOT
implemented: ``iter_unverified_puuids``, ``iter_unverified_puuids_for_build``,
``set_puuid_rank``, ``upsert_peer_game``, ``load_peer_games``,
``count_peer_games`` -- those were only ever reachable through the
in-process peer path (``build_peer_for_pool``, deleted entirely in Phase 9
Task 4's final dead-code sweep: its apparent second caller,
``orchestrator.run_all_builds``, was confirmed to have zero production
callers of its own, the CLI shim that used to invoke it having been deleted
in commit ``33bd81b``, predating this migration). ``_run_stage_b`` (the only
place a ``RawMatchStore``-backed ``Services`` object's peer comparison is
resolved in a real deployment) never called that path either way: Phase 9
Task 3 removed the ``peers_mode`` flag that used to make this configurable,
so ``_run_stage_b`` now always resolves peers via PEERS over gRPC instead
(``_build_peer_for_pool_via_grpc``, which only touches
``iter_match_ids``/``load_match``, both implemented below). The gap is now
fully moot -- there is no in-process peer path left anywhere to hit it.

Reproduces ``MatchStore``'s real semantics:

- ``has_match``/``count`` require **both** the match document and its
  timeline to be stored (``cache.py:184-197``, ``cache.py:450-459`` join
  ``matches`` with ``timelines``) -- a match with no timeline yet still
  needs work, so it does not count.
- Ownership (``owners``) is a many-to-many association, not a single-owner
  lock: several different puuids can each independently own the same
  match_id (``cache.py``'s ``match_players`` table, used for teammates
  tracked together who share a match). ``save_match`` always associates its
  puuid with the match (``cache.py:220-223``, unconditional ``INSERT OR
  IGNORE``). ``claim_ownership`` reports a match_id as claimed only when
  that specific (match_id, puuid) association is newly created --
  idempotent per pair, exactly like ``cache.py:284-289``'s
  ``INSERT OR IGNORE`` + ``rowcount`` check -- and, like real
  ``claim_ownership`` (``cache.py:282``), only considers match_ids that
  already satisfy ``has_match`` (match + timeline both present).
"""

from collections.abc import Iterator
from typing import Any

import pymongo


class RawMatchStore:
    """MongoDB-backed store of raw match and timeline documents."""

    def __init__(self, client: pymongo.MongoClient, db_name: str = "league_stats") -> None:
        """Open the store against an existing Mongo client.

        Args:
            client: A ``pymongo.MongoClient``-compatible client (real or
                ``mongomock.MongoClient`` in tests).
            db_name: Name of the database to use within the client.
        """
        db = client[db_name]
        self._matches = db["matches"]
        self._timelines = db["timelines"]

    def has_match(self, match_id: str) -> bool:
        """Whether both the match and its timeline have been saved.

        Args:
            match_id: Riot match id (e.g. ``EUW1_1234``).

        Returns:
            ``True`` when the match never needs to be downloaded again,
            i.e. both its match document and its timeline are stored.
        """
        if self._matches.find_one({"_id": match_id}, {"_id": 1}) is None:
            return False
        return self._timelines.find_one({"_id": match_id}, {"_id": 1}) is not None

    def save_match(self, match_id: str, puuid: str, match: dict[str, Any]) -> None:
        """Persist a raw match document and associate it with ``puuid``.

        Args:
            match_id: Riot match id.
            puuid: PUUID of the tracked player triggering this save. Always
                added to the match's owner set (a match can have several
                owners, e.g. teammates tracked together).
            match: Raw match-v5 JSON document.
        """
        self._matches.update_one(
            {"_id": match_id},
            {"$set": {"payload": match}, "$addToSet": {"owners": puuid}},
            upsert=True,
        )

    def save_timeline(self, match_id: str, timeline: dict[str, Any]) -> None:
        """Persist a raw timeline document.

        Args:
            match_id: Riot match id.
            timeline: Raw match-v5 timeline JSON document.
        """
        self._timelines.update_one(
            {"_id": match_id},
            {"$set": {"payload": timeline}},
            upsert=True,
        )

    def load_match(self, match_id: str) -> dict[str, Any] | None:
        """Load a stored match document.

        Args:
            match_id: Riot match id.

        Returns:
            The raw match JSON, or ``None`` if absent.
        """
        doc = self._matches.find_one({"_id": match_id}, {"payload": 1})
        return doc["payload"] if doc else None

    def load_timeline(self, match_id: str) -> dict[str, Any] | None:
        """Load a stored timeline document.

        Args:
            match_id: Riot match id.

        Returns:
            The raw timeline JSON, or ``None`` if absent.
        """
        doc = self._timelines.find_one({"_id": match_id}, {"payload": 1})
        return doc["payload"] if doc else None

    def claim_ownership(self, puuid: str, match_ids: list[str]) -> list[str]:
        """Index already-stored matches for a player without re-downloading.

        When a match was fetched for another account (e.g. rank peers), the
        payload may already exist while this player's ownership row is
        missing. Several different puuids can independently own the same
        match_id.

        Args:
            puuid: The player's PUUID.
            match_ids: Match ids to claim when present locally.

        Returns:
            Match ids for which a new (match_id, puuid) ownership
            association was inserted. Re-claiming the same match_id with
            the same puuid a second time returns it excluded, matching
            ``MatchStore``'s ``INSERT OR IGNORE`` + rowcount idempotency.
        """
        claimed: list[str] = []
        for match_id in match_ids:
            if not self.has_match(match_id):
                continue
            result = self._matches.update_one(
                {"_id": match_id}, {"$addToSet": {"owners": puuid}}
            )
            if result.modified_count:
                claimed.append(match_id)
        return claimed

    def iter_all_match_ids(self) -> Iterator[str]:
        """Iterate over every stored match id.

        Yields:
            All match ids in the store.
        """
        for doc in self._matches.find({}, {"_id": 1}):
            yield doc["_id"]

    def count(self) -> int:
        """Number of fully stored matches (match + timeline).

        Returns:
            The count of matches with both documents present.
        """
        match_ids = {doc["_id"] for doc in self._matches.find({}, {"_id": 1})}
        timeline_ids = {doc["_id"] for doc in self._timelines.find({}, {"_id": 1})}
        return len(match_ids & timeline_ids)

    def iter_match_ids(self, puuid: str) -> Iterator[str]:
        """Iterate over every stored match id owned by a player.

        Mirrors ``MatchStore.iter_match_ids`` (``cache.py:435-448``):
        ownership is independent of whether a timeline has been saved yet
        (``save_match`` above always ``$addToSet``s ``puuid`` into
        ``owners``, unconditionally, exactly like ``cache.py``'s
        ``match_players`` insert), so this does not gate on ``has_match``.

        Args:
            puuid: The player's PUUID.

        Yields:
            Match ids owned by that player.
        """
        for doc in self._matches.find({"owners": puuid}, {"_id": 1}):
            yield doc["_id"]

    def close(self) -> None:
        """No-op: this store never owns its ``pymongo.MongoClient``.

        ``__init__`` receives the client from outside (in production, a
        process-wide client shared across jobs -- see ``web/worker.py``'s
        ``_build_mongo_client``, mirroring ``shared_rate_limiter``'s
        already-established sharing pattern). Closing a shared client here
        would break every other job/store still using it -- unlike its
        deleted predecessor, which owned a private connection nobody else
        held, this store has no equivalent connection of its own to release.
        """
        return None
