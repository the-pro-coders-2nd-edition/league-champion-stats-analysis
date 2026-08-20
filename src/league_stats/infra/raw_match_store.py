"""MongoDB-backed raw match/timeline store for the RUNNER service.

Mirrors the subset of ``infra.cache.MatchStore``'s method surface (see
``src/league_stats/infra/cache.py:104``) that RUNNER needs: ``has_match``,
``save_match``, ``save_timeline``, ``load_match``, ``load_timeline``,
``claim_ownership``, ``iter_all_match_ids``, ``count``. Backed by
``pymongo.MongoClient`` (or ``mongomock.MongoClient`` in tests) instead of
SQLite.

Ownership model differs deliberately from ``MatchStore``: ``MatchStore``
tracks ownership as a many-to-many ``match_players`` table (a match can be
associated with several puuids), and its ``has_match``/``count`` require a
match *and* its timeline to both be present (a SQL JOIN). RawMatchStore
instead gives each match a single, permanent owner -- whichever puuid's
``save_match``/``claim_ownership`` call is the first to see a given
``match_id`` wins, and later claims by a different puuid are rejected. This
is the "first-writer-wins" lock RUNNER needs to stop concurrent workers from
double-processing the same match; it does not need MatchStore's broader
multi-owner index. ``has_match``/``count`` here reflect only whether the
match document itself has been saved, independent of whether a timeline has
been attached yet.
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
        """Whether a match document has been saved.

        Args:
            match_id: Riot match id (e.g. ``EUW1_1234``).

        Returns:
            ``True`` when a match document is stored, regardless of whether
            its timeline has been saved yet.
        """
        return self._matches.find_one({"_id": match_id}, {"_id": 1}) is not None

    def save_match(self, match_id: str, puuid: str, match: dict[str, Any]) -> None:
        """Persist a raw match document, claiming ownership if unclaimed.

        Args:
            match_id: Riot match id.
            puuid: PUUID of the player whose fetch triggered this save. Only
                recorded as the match's owner if no owner is set yet.
            match: Raw match-v5 JSON document.
        """
        existing = self._matches.find_one({"_id": match_id}, {"owner": 1})
        owner = existing["owner"] if existing else puuid
        self._matches.update_one(
            {"_id": match_id},
            {"$set": {"payload": match, "owner": owner}},
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
        """Claim already-stored matches for a player, first-writer-wins.

        A match with no owner yet is claimed by ``puuid``. A match already
        owned by ``puuid`` is reported as claimed (it is already theirs). A
        match owned by a different puuid is left alone and excluded from the
        result, so concurrent workers cannot double-claim the same match.

        Args:
            puuid: The player's PUUID.
            match_ids: Match ids to claim when present locally.

        Returns:
            Match ids owned by ``puuid`` after this call.
        """
        claimed: list[str] = []
        for match_id in match_ids:
            doc = self._matches.find_one({"_id": match_id}, {"owner": 1})
            if doc is None:
                continue
            owner = doc.get("owner")
            if owner is None:
                self._matches.update_one({"_id": match_id}, {"$set": {"owner": puuid}})
                claimed.append(match_id)
            elif owner == puuid:
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
        """Number of saved match documents.

        Returns:
            The count of matches with a saved match document, regardless of
            whether a timeline has been saved for them.
        """
        return self._matches.count_documents({})
