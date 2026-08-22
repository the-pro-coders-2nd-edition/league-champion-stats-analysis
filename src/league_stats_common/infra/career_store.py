"""MongoDB persistence for Career mode ladders.

Career state cannot be re-derived from match data alone: ``hit=12, need=15`` is
``In progress`` for a goal that never cleared and ``At risk`` for one that did,
and rung targets are frozen at generation time so they never move under a player
who is closing in on them. Both live here, keyed by champion + role + the
primary account slug.

Backed by ``pymongo.MongoClient`` (or ``mongomock.MongoClient`` in tests).
Reproduces the 3 SQL tables' semantics as 3 collections:

- ``career_goals``: one document per ``(build_key, slot, goal_index)``,
  enforced by a compound unique index on those three fields (``_id`` is a
  Mongo-assigned ``ObjectId``). Indexed on ``build_key``, since every
  read/delete/move filters on it.
- ``career_used_tracks``: the SQL primary key is the *full 3-tuple*
  ``(build_key, track_key, cleared_at)``, not just ``(build_key,
  track_key)`` -- a track cleared, un-cleared and re-cleared gets a second
  row because ``cleared_at`` differs. A compound unique index on that triple
  preserves this exactly; collapsing to a 2-field key would silently change
  behavior on that clear/un-clear/re-clear sequence.
- ``career_flags``: one document per ``build_key`` (a unique index on that
  field), a straightforward single-row-per-key table.

``peek_pending_drop``'s SQL `-1` sentinel ("nothing queued", since the SQL
column is `NOT NULL`) is replaced with a Mongo-native "field absent" check:
``request_drop`` sets ``pending_drop_slot``, ``clear_pending_drop`` `$unset`s
it, and ``peek_pending_drop`` returns ``None`` when the field (or the whole
document) is missing. Verified safe: ``CareerDropRequest.slot`` is
``Field(ge=0, lt=BLOCK_SLOTS)`` at the FastAPI layer (the only real caller of
``request_drop``), so no real caller can ever pass a negative slot -- the old
`-1`-means-nothing behavior and the new absence-means-nothing behavior are
observably identical for every real caller.

``write_slot``'s "delete then insert" (SQL: ``DELETE`` + ``executemany``
INSERT inside one implicit transaction) becomes ``delete_many`` +
``insert_many`` -- verified directly that ``insert_many`` (unlike
``bulk_write(UpdateOne(...))``, see the module-level note below) works fine
under this repo's pinned ``pymongo``/``mongomock`` versions, since it never
sends the `sort` kwarg that broke ``bulk_write``'s update path. This drops
the old single-transaction atomicity (a crash between the two calls could
leave a slot with no goals), judged harmless the same way Task 2 judged
``DerivedStore``: every reader already treats a slot with no goals as "not
seeded yet," not as corruption, so a mid-write crash degrades to a state
every caller already handles.

This repo's pinned ``pymongo`` 4.17.0 + ``mongomock`` 4.3.0 combination is
incompatible with `bulk_write(UpdateOne(...))` (`TypeError: ...
add_update() got an unexpected keyword argument 'sort'`, reproduced
directly). Every batch *update* in this module therefore uses a loop of
individual ``update_one`` calls instead of ``bulk_write`` (``save_goal_states``,
``move_slot``).
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from types import TracebackType
from typing import Any, Sequence

import pymongo

from league_stats_common.infra.mongo import db_name_from_uri
from league_stats_runner.analysis.career.models import Comparator, Rung, StoredGoal
from league_stats_common.utils import get_logger

_KNOWN_COMPARATORS: frozenset[str] = frozenset({"at_least", "under", "at_most"})


def _load_comparator(value: object) -> Comparator:
    text = str(value)
    if text in _KNOWN_COMPARATORS:
        return text  # type: ignore[return-value]
    return "at_least"


def build_key(player_slug: str, champion: str, role: str) -> str:
    """Ladder identity: one ladder per champion + role per tracked player."""
    return f"{player_slug}|{champion}|{role.upper()}"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class CareerStore:
    """MongoDB store of Career goals, retired tracks and pending banners."""

    def __init__(self, client: pymongo.MongoClient, db_name: str = "league_stats") -> None:
        """Open the store against an existing Mongo client.

        Args:
            client: A ``pymongo.MongoClient``-compatible client (real or
                ``mongomock.MongoClient`` in tests).
            db_name: Name of the database to use within the client.
        """
        db = client[db_name]
        self._goals = db["career_goals"]
        self._used_tracks = db["career_used_tracks"]
        self._flags = db["career_flags"]
        # Mirrors the SQL implicit index on the primary key's leading column --
        # every load_goals/delete_slot/move_slot query filters on build_key.
        # create_index is idempotent and mongomock supports it, so this is
        # safe on every construction, including in tests.
        self._goals.create_index("build_key")
        self._goals.create_index(
            [("build_key", 1), ("slot", 1), ("goal_index", 1)], unique=True
        )
        self._used_tracks.create_index("build_key")
        self._used_tracks.create_index(
            [("build_key", 1), ("track_key", 1), ("cleared_at", 1)], unique=True
        )
        self._flags.create_index("build_key", unique=True)
        self._log = get_logger("career_store")

    def __enter__(self) -> "CareerStore":
        """Enter a context manager scope."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Close the store on context exit."""
        self.close()

    def close(self) -> None:
        """No-op: this store never owns its ``pymongo.MongoClient``.

        The client is handed in from outside (see ``open_career_store``
        below), mirroring ``RawMatchStore``/``DerivedStore``'s reasoning --
        closing a shared client here would break every other user of it.
        """
        return None

    def load_goals(self, key: str) -> list[StoredGoal]:
        """Every persisted goal for a ladder, ordered by slot then goal index."""
        docs = self._goals.find({"build_key": key}).sort([("slot", 1), ("goal_index", 1)])
        return [
            StoredGoal(
                slot=int(doc["slot"]),
                goal_index=int(doc["goal_index"]),
                track_key=str(doc["track_key"]),
                rung=Rung(
                    text=str(doc["text"]),
                    column=str(doc["column_name"]),
                    comparator=_load_comparator(doc["comparator"]),
                    target=float(doc["target"]),
                    need=int(doc["need"]),
                    why=str(doc.get("why") or ""),
                ),
                state=str(doc["state"]),
                since_ms=int(doc.get("since_ms") or 0),
                peer_seeded=bool(doc.get("peer_seeded") or False),
            )
            for doc in docs
        ]

    def write_slot(
        self,
        key: str,
        slot: int,
        track_key: str,
        rungs: Sequence[Rung],
        states: Sequence[str],
        since_ms: int = 0,
        peer_seeded: bool = False,
    ) -> None:
        """Replace a slot with a freshly generated track and its frozen rungs.

        ``peer_seeded`` records whether peer percentiles were available when the
        rungs were frozen. A slot written without them is provisional: the next
        run that has peers rebuilds it, unless the player has already started it.
        """
        self.delete_slot(key, slot)
        if not rungs:
            return
        self._goals.insert_many(
            [
                {
                    "build_key": key,
                    "slot": slot,
                    "goal_index": index,
                    "track_key": track_key,
                    "text": rung.text,
                    "column_name": rung.column,
                    "comparator": rung.comparator,
                    "target": float(rung.target),
                    "need": int(rung.need),
                    "state": states[index],
                    "since_ms": int(since_ms),
                    "peer_seeded": bool(peer_seeded),
                    "why": rung.why,
                }
                for index, rung in enumerate(rungs)
            ]
        )

    def save_goal_states(self, key: str, states: dict[tuple[int, int], str]) -> None:
        """Persist recomputed states for ``(slot, goal_index)`` pairs."""
        if not states:
            return
        for (slot, index), state in states.items():
            self._goals.update_one(
                {"build_key": key, "slot": slot, "goal_index": index}, {"$set": {"state": state}}
            )

    def delete_slot(self, key: str, slot: int) -> None:
        """Drop every goal in a slot."""
        self._goals.delete_many({"build_key": key, "slot": slot})

    def move_slot(self, key: str, src: int, dst: int, *, since_ms: int | None = None) -> None:
        """Shift a slot's goals left, replacing whatever sat at the destination.

        ``since_ms`` re-stamps the start line, which matters on promotion to the
        live slot: a queued block must not inherit credit from the games that
        cleared the block ahead of it.
        """
        self.delete_slot(key, dst)
        sets: dict[str, Any] = {"slot": dst}
        if since_ms is not None:
            sets["since_ms"] = int(since_ms)
        for doc in self._goals.find({"build_key": key, "slot": src}):
            self._goals.update_one(
                {"build_key": key, "slot": src, "goal_index": doc["goal_index"]},
                {"$set": sets},
            )

    def record_used_track(self, key: str, track_key: str) -> None:
        """Mark a track as retired so fresh tracks are preferred over recycling."""
        cleared_at = _now()
        self._used_tracks.update_one(
            {"build_key": key, "track_key": track_key, "cleared_at": cleared_at},
            {"$setOnInsert": {"build_key": key}},
            upsert=True,
        )

    def used_track_keys(self, key: str) -> set[str]:
        """Track keys this ladder has already retired at least once."""
        return {str(value) for value in self._used_tracks.distinct("track_key", {"build_key": key})}

    def set_pending_congrats(self, key: str, track_key: str) -> None:
        """Queue the block-complete banner for the next render."""
        self._flags.update_one(
            {"build_key": key}, {"$set": {"pending_congrats_track": track_key}}, upsert=True
        )

    def peek_pending_congrats(self, key: str) -> str:
        """Read the pending banner without consuming it.

        Consuming at build time was safe while every rebuild was user-initiated.
        Under group watch a background rebuild the reader never opens would
        swallow the banner, so the flag now survives until a reader acknowledges
        it via :meth:`clear_pending_congrats`.
        """
        doc = self._flags.find_one({"build_key": key})
        if doc is None:
            return ""
        return str(doc.get("pending_congrats_track") or "")

    def clear_pending_congrats(self, key: str) -> None:
        """Mark the block-complete banner as seen."""
        self._flags.update_one(
            {"build_key": key}, {"$set": {"pending_congrats_track": ""}}, upsert=True
        )

    def peek_recap_ack(self, key: str) -> tuple[str, int, dict[str, int], str]:
        """Last acknowledged recap: match id, its game_creation_ms, goal hit counts, track key.

        Empty/zero/empty-dict/empty when this ladder has never acknowledged a recap.
        """
        doc = self._flags.find_one({"build_key": key})
        if doc is None:
            return "", 0, {}, ""
        match_id = str(doc.get("recap_acked_match_id") or "")
        game_ms = int(doc.get("recap_acked_game_ms") or 0)
        hits_json = str(doc.get("recap_acked_hits_json") or "")
        track_key = str(doc.get("recap_acked_track_key") or "")
        hits: dict[str, int] = {}
        if hits_json:
            try:
                hits = {str(k): int(v) for k, v in json.loads(hits_json).items()}
            except (ValueError, TypeError, json.JSONDecodeError):
                hits = {}
        return match_id, game_ms, hits, track_key

    def ack_recap(
        self,
        key: str,
        *,
        match_id: str,
        game_ms: int,
        hits: dict[str, int],
        track_key: str,
    ) -> None:
        """Record the newest game, goal-hit counts and track a reader has seen recapped."""
        self._flags.update_one(
            {"build_key": key},
            {
                "$set": {
                    "recap_acked_match_id": match_id,
                    "recap_acked_game_ms": int(game_ms),
                    "recap_acked_hits_json": json.dumps(hits),
                    "recap_acked_track_key": track_key,
                }
            },
            upsert=True,
        )

    def request_drop(self, key: str, slot: int) -> None:
        """Queue a manual block drop for the next analysis run.

        The HTTP route that offers the button has no match data, so it cannot
        restamp a promoted block's window or generate a replacement itself.
        Recording the intent here lets :func:`advance_career` perform the drop
        with the real ``TrackContext`` on the run the request kicks off.
        """
        self._flags.update_one(
            {"build_key": key}, {"$set": {"pending_drop_slot": int(slot)}}, upsert=True
        )

    def peek_pending_drop(self, key: str) -> int | None:
        """The slot a reader asked to drop, or ``None`` when nothing is queued."""
        doc = self._flags.find_one({"build_key": key})
        if doc is None or "pending_drop_slot" not in doc:
            return None
        return int(doc["pending_drop_slot"])

    def clear_pending_drop(self, key: str) -> None:
        """Mark a queued drop as performed."""
        self._flags.update_one({"build_key": key}, {"$unset": {"pending_drop_slot": ""}})

    def clear_all(self) -> dict[str, int]:
        """Delete every ladder, retired track and pending flag.

        Returns row counts per table before deletion. Safe on an empty store.
        """
        counts = self.row_counts()
        self._goals.delete_many({})
        self._used_tracks.delete_many({})
        self._flags.delete_many({})
        return counts

    def row_counts(self) -> dict[str, int]:
        """Row counts for each Career table."""
        return {
            "career_goals": self._goals.count_documents({}),
            "career_used_tracks": self._used_tracks.count_documents({}),
            "career_flags": self._flags.count_documents({}),
        }


# Process-wide Mongo clients keyed by URI, mirroring `derived.py`'s own
# `_SHARED_MONGO_CLIENTS`: neither `AppConfig` (RUNNER's job pipeline,
# `pipeline/bundles.py::build_career_bundle`) nor `api_ui/app.py`'s
# `_career_ladder_ref` helper carries a Mongo URI field -- both only ever
# resolved a `career_db_path: Path` off `AppConfig`. Threading a Mongo client
# through either call path would ripple this task well outside its file
# list, the same situation Task 2 hit with `DerivedStore`'s 3 real callers.
# Resolving the URI from the same environment variables
# `WebConfig.runner_mongo_uri` already uses, and sharing one client per URI
# per process here, is the same deliberate scope-narrowing choice Task 2
# made for `open_derived_store`.
_SHARED_MONGO_CLIENTS: dict[str, pymongo.MongoClient] = {}
_SHARED_MONGO_CLIENTS_LOCK = threading.Lock()


def _resolve_mongo_uri() -> str:
    return (
        os.environ.get("RUNNER_MONGO_URI")
        or os.environ.get("MONGO_URI")
        or "mongodb://localhost:27017/league_stats"
    )


def _build_mongo_client(mongo_uri: str) -> pymongo.MongoClient:
    """Return the process-wide Mongo client for this URI, creating it once.

    A separate seam (rather than calling `pymongo.MongoClient` directly from
    `open_career_store`) so tests can monkeypatch this one function to
    return a `mongomock.MongoClient` instead of dialing a real Mongo --
    matching `derived.py`'s own `_build_mongo_client`.
    """
    with _SHARED_MONGO_CLIENTS_LOCK:
        client = _SHARED_MONGO_CLIENTS.get(mongo_uri)
        if client is None:
            client = pymongo.MongoClient(mongo_uri)
            _SHARED_MONGO_CLIENTS[mongo_uri] = client
        return client


def open_career_store() -> CareerStore:
    """Open the Career store against the process-wide Mongo client.

    The single production entry point for `pipeline/bundles.py` and
    `api_ui/app.py` -- see the module comment above `_SHARED_MONGO_CLIENTS`
    for why this resolves its own client rather than receiving one from a
    caller.
    """
    uri = _resolve_mongo_uri()
    client = _build_mongo_client(uri)
    return CareerStore(client, db_name=db_name_from_uri(uri))
