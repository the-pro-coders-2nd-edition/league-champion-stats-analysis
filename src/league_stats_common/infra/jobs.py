"""MongoDB-backed job queue and player registry for the web app.

One :class:`JobStore` instance is shared between the FastAPI request threads
and the background worker thread(s), and across the ``api-ui``/``cron-watch``
processes (both point at the same Mongo database). Jobs survive restarts:
queued jobs are picked up again, while jobs that were mid-run are marked
failed by :meth:`JobStore.recover_orphans`.

Backed by ``pymongo.MongoClient`` (or ``mongomock.MongoClient`` in tests),
following the same pattern as ``CareerStore``/``DerivedStore``. Reproduces
the 2 SQL tables' semantics as 2 collections plus one id-allocation
collection:

- ``jobs``: one document per job. ``_id`` is an ``int``, allocated from the
  ``counters`` collection (see below) rather than a Mongo ``ObjectId`` --
  ``job_id`` is a public HTTP/SPA contract (``GET /api/jobs/{job_id}``,
  ``POST /api/jobs/{job_id}/cancel`` type it as ``int``), and keeping it a
  real, total-ordered integer keeps ``queue_position``'s ``id < ?`` and
  ``average_duration_s``'s ``ORDER BY id DESC`` meaningful the same way the
  old auto-incrementing primary key did. Every other SQL column becomes a
  document field 1:1. Every read path restores an ``"id"`` key from ``_id``
  so the returned dict shape matches the old row-mapping's dict shape
  exactly.
- ``players``: one document per group/player, ``_id = slug``. Every read
  path restores a ``"slug"`` key from ``_id``.
- ``counters``: a single ``{_id: "jobs", value: <last-issued-id>}`` document,
  incremented via ``find_one_and_update(..., {"$inc": {"value": 1}},
  upsert=True, return_document=ReturnDocument.AFTER)`` -- the direct
  Mongo-native equivalent of the old auto-incrementing primary key.

**``enqueue``'s atomicity (replaces the old ``BEGIN IMMEDIATE`` transaction):**
the real invariant is "at most one active job per ``player_slug``, enforced
atomically across OS processes" -- this exact race was the subject of a real
historical production bug (Phase 2's ``BEGIN IMMEDIATE`` fix, closing a
6-duplicate-jobs race). Rather than a redundant "claim" flag (which every
terminal-state transition -- ``set_state``, ``cancel``, ``recover_orphans``
-- would then have to remember to release, a real stale-lock risk class),
this store declares the invariant directly to MongoDB as a **partial unique
index**:

    self._jobs.create_index(
        "player_slug", unique=True,
        partialFilterExpression={"state": {"$in": list(ACTIVE_STATES)}},
    )

``enqueue`` allocates a new id and ``insert_one``s the job document directly.
If another active job already exists for that ``player_slug``, the index
rejects the insert with ``pymongo.errors.DuplicateKeyError`` -- atomically,
at the database layer, verified directly against this repo's pinned
``mongomock`` 4.3.0. No release bookkeeping is needed anywhere: the instant
a job's ``state`` is updated out of ``ACTIVE_STATES``, the partial index
stops covering that document and the slug is immediately free for a new
active job -- also verified directly. See
``tests/test_web_jobs.py::test_enqueue_is_atomic_under_concurrent_writers``
for the concurrent-writers proof this design was built to satisfy.

**``claim_next``** is a single atomic ``find_one_and_update`` (``sort``
picks the oldest queued job, filter+update happen as one op) -- unlike the
SQL version's ``SELECT`` + conditional ``UPDATE`` + retry loop, this cannot
lose the race the retry loop guarded against, so no retry loop is needed.
See ``tests/test_web_jobs.py::test_claim_next_is_atomic_under_concurrent_claimers``.

**No migration step**: Mongo has no ``ALTER TABLE``. Every read path
defaults missing fields the same way the SQL ``DEFAULT``/nullable columns
did (see each ``_doc_to_job``/``_doc_to_player`` call site) instead of
running a schema migration on open.

This repo's pinned ``pymongo`` 4.17.0 + ``mongomock`` 4.3.0 combination is
incompatible with ``bulk_write(UpdateOne(...))`` (``TypeError: ...
add_update() got an unexpected keyword argument 'sort'``, reproduced
directly, see Tasks 2/3's equivalent notes). ``recover_orphans`` uses a
single ``update_many`` with one uniform filter/update (not a per-document
``bulk_write``), which does not hit this incompatibility.
"""

from __future__ import annotations

import json
import os
import threading
import time
from typing import Any

import pymongo
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from league_stats_common.infra.mongo import db_name_from_uri
from league_stats_common.utils import get_logger

# Job lifecycle states.
QUEUED = "queued"
FETCHING = "fetching"
ANALYZING = "analyzing"
REPORT_READY = "report_ready"
PEER_RUNNING = "peer_running"
DONE = "done"
FAILED = "failed"
CANCELLED = "cancelled"

ACTIVE_STATES: tuple[str, ...] = (QUEUED, FETCHING, ANALYZING, REPORT_READY, PEER_RUNNING)
RUNNING_STATES: tuple[str, ...] = (FETCHING, ANALYZING, REPORT_READY, PEER_RUNNING)
TERMINAL_STATES: tuple[str, ...] = (DONE, FAILED, CANCELLED)

JOB_KIND_ANALYZE = "analyze"
JOB_KIND_REFRESH = "refresh"
JOB_KIND_REGENERATE = "regenerate"

# Default gap between watch checks for one group.
DEFAULT_WATCH_INTERVAL_S = 60

# Used for ETA display until enough completed jobs exist to average.
DEFAULT_JOB_DURATION_S = 20 * 60


def encode_players(players: list[dict[str, Any]]) -> str:
    """Serialize tracked players for storage."""
    from league_stats_common.core.models import solo_rank_fields

    payload: list[dict[str, Any]] = []
    for player in players:
        entry: dict[str, Any] = {
            "riot_id": player["riot_id"],
            "tagline": player["tagline"],
        }
        raw_icon = player.get("profile_icon_id")
        if raw_icon is not None:
            try:
                entry["profile_icon_id"] = int(raw_icon)
            except (TypeError, ValueError):
                pass
        entry.update(solo_rank_fields(player))
        payload.append(entry)
    return json.dumps(payload, separators=(",", ":"))


def decode_players(
    raw: str | None, *, riot_id: str = "", tagline: str = ""
) -> list[dict[str, Any]]:
    """Deserialize tracked players, falling back to the primary identity."""
    from league_stats_common.core.models import solo_rank_fields

    if raw:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, list) and parsed:
            players: list[dict[str, Any]] = []
            for item in parsed:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("riot_id", "")).strip()
                tag = str(item.get("tagline", "")).strip()
                if not name or not tag:
                    continue
                entry: dict[str, Any] = {"riot_id": name, "tagline": tag}
                raw_icon = item.get("profile_icon_id")
                if raw_icon is not None:
                    try:
                        entry["profile_icon_id"] = int(raw_icon)
                    except (TypeError, ValueError):
                        pass
                entry.update(solo_rank_fields(item))
                players.append(entry)
            if players:
                return players
    if riot_id and tagline:
        return [{"riot_id": riot_id, "tagline": tagline}]
    return []


def players_label(players: list[dict[str, Any]]) -> str:
    """Comma-separated display label for tracked players."""
    return ", ".join(f"{p['riot_id']}#{p['tagline']}" for p in players)


class JobStore:
    """MongoDB store for analysis jobs and known players."""

    def __init__(self, client: pymongo.MongoClient, db_name: str = "league_stats") -> None:
        """Open the store against an existing Mongo client.

        Args:
            client: A ``pymongo.MongoClient``-compatible client (real or
                ``mongomock.MongoClient`` in tests).
            db_name: Name of the database to use within the client.
        """
        db = client[db_name]
        self._jobs = db["jobs"]
        self._players = db["players"]
        self._counters = db["counters"]
        # Mirrors the SQL indexes (`idx_jobs_state`, `idx_jobs_player`).
        self._jobs.create_index("state")
        # The at-most-one-active-job-per-player invariant, enforced by Mongo
        # itself -- see the module docstring's "enqueue's atomicity" section.
        self._jobs.create_index(
            "player_slug",
            unique=True,
            partialFilterExpression={"state": {"$in": list(ACTIVE_STATES)}},
        )
        self._log = get_logger("jobs")
        # Real MongoDB's `find_one_and_update`/unique-index inserts are
        # atomic server-side regardless of how many OS processes call them
        # concurrently -- that is the actual cross-process correctness
        # mechanism this store relies on (see the module docstring). This
        # lock is a separate, additional layer: it only serialises calls
        # *within one process*. It is required because `mongomock` (the test
        # double used everywhere in this suite) has no internal locking in
        # its `_find_and_modify` implementation -- verified directly by
        # reading `mongomock/collection.py` -- so two genuine OS threads
        # calling `claim_next`/`enqueue` against the SAME `mongomock` client
        # can interleave and both "win" a claim/insert that real MongoDB
        # would have serialised. Reproduced directly: without this lock,
        # `tests/test_web_jobs.py::test_claim_next_is_atomic_under_concurrent_claimers`
        # flakes (~1 run in 7) with a job claimed twice. A real `pymongo`
        # client against real MongoDB does not need this lock for
        # correctness, but it costs nothing there either (Mongo's own
        # operations are already atomic; this just adds one more process
        # spends waiting for its own turn).
        self._lock = threading.Lock()

    def close(self) -> None:
        """No-op: this store never owns its ``pymongo.MongoClient``.

        The client is handed in from outside (see ``open_jobs_store`` below),
        mirroring ``CareerStore``/``DerivedStore``'s reasoning -- closing a
        shared client here would break every other user of it.
        """
        return None

    # ------------------------------------------------------------- decoding

    @staticmethod
    def _doc_to_job(doc: dict[str, Any]) -> dict[str, Any]:
        data = dict(doc)
        data["id"] = data.pop("_id")
        data.setdefault("filter_champion", None)
        data.setdefault("filter_role", None)
        data.setdefault("min_games", None)
        data.setdefault("stage_detail", "")
        data.setdefault("stage_current", None)
        data.setdefault("stage_total", None)
        data.setdefault("error", "")
        data.setdefault("trace_id", "")
        data.setdefault("started_at", None)
        data.setdefault("finished_at", None)
        data["players"] = decode_players(
            data.get("players_json"),
            riot_id=str(data.get("riot_id", "")),
            tagline=str(data.get("tagline", "")),
        )
        return data

    @staticmethod
    def _doc_to_player(doc: dict[str, Any]) -> dict[str, Any]:
        data = dict(doc)
        data["slug"] = data.pop("_id")
        data.setdefault("players_json", "[]")
        data.setdefault("last_job_id", None)
        data.setdefault("base_completed_at", None)
        data.setdefault("peer_completed_at", None)
        data.setdefault("peer_failed", 0)
        data.setdefault("watch_enabled", 0)
        data.setdefault("watch_interval_s", DEFAULT_WATCH_INTERVAL_S)
        data.setdefault("last_watch_at", None)
        data.setdefault("last_watch_error", "")
        data.setdefault("watch_seen_json", "{}")
        data["players"] = decode_players(
            data.get("players_json"),
            riot_id=str(data.get("riot_id", "")),
            tagline=str(data.get("tagline", "")),
        )
        return data

    def _next_job_id(self) -> int:
        doc = self._counters.find_one_and_update(
            {"_id": "jobs"},
            {"$inc": {"value": 1}},
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        return int(doc["value"])

    # ------------------------------------------------------------------ jobs

    def enqueue(
        self,
        *,
        kind: str,
        riot_id: str,
        tagline: str,
        region: str,
        player_slug: str,
        players: list[dict[str, Any]] | None = None,
        filter_champion: str | None = None,
        filter_role: str | None = None,
        min_games: int | None = None,
        trace_id: str | None = None,
    ) -> tuple[dict[str, Any], bool]:
        """Queue a job, deduplicating against an existing active job.

        Optional ``filter_champion`` / ``filter_role`` scope analysis to one
        build (used by per-champion report refresh). ``min_games`` overrides
        the config threshold for how many ranked games a build needs.
        ``trace_id`` is the originating trace for this job (the HTTP request's
        or CronWatch detection's own trace_id) -- persisted so
        ``AnalysisWorker`` can restore it on the worker thread when the job is
        claimed, letting it survive the hand-off to a long-lived worker thread
        that has no contextvars link back to whatever set it.

        Returns:
            ``(job, created)`` — the active or newly created job row.
        """
        tracked = players or [{"riot_id": riot_id, "tagline": tagline}]
        champion = (filter_champion or "").strip() or None
        role = (filter_role or "").strip() or None
        games_threshold = int(min_games) if min_games is not None else None
        trace = trace_id or ""

        with self._lock:
            while True:
                new_id = self._next_job_id()
                now = time.time()
                doc = {
                    "_id": new_id,
                    "kind": kind,
                    "player_slug": player_slug,
                    "riot_id": riot_id,
                    "tagline": tagline,
                    "region": region,
                    "players_json": encode_players(tracked),
                    "filter_champion": champion,
                    "filter_role": role,
                    "min_games": games_threshold,
                    "state": QUEUED,
                    "stage_detail": "",
                    "stage_current": None,
                    "stage_total": None,
                    "error": "",
                    "trace_id": trace,
                    "created_at": now,
                    "started_at": None,
                    "finished_at": None,
                    "updated_at": now,
                }
                try:
                    self._jobs.insert_one(doc)
                except DuplicateKeyError:
                    existing = self._active_job_for(player_slug)
                    if existing is not None:
                        return existing, False
                    # The blocking job finished between our failed insert and
                    # this read -- the slug is free again, retry with a fresh id.
                    continue
                self._players.update_one(
                    {"_id": player_slug}, {"$set": {"last_job_id": new_id}}
                )
                return self._doc_to_job(doc), True

    def get(self, job_id: int) -> dict[str, Any] | None:
        """Load one job by id."""
        with self._lock:
            doc = self._jobs.find_one({"_id": job_id})
            if doc is None:
                return None
            return self._doc_to_job(doc)

    def _get(self, job_id: int) -> dict[str, Any]:
        doc = self._jobs.find_one({"_id": job_id})
        if doc is None:
            raise LookupError(f"job {job_id} not found")
        return self._doc_to_job(doc)

    def _active_job_for(self, player_slug: str) -> dict[str, Any] | None:
        """Caller must hold ``self._lock`` -- see ``enqueue``/``active_job_for_player``."""
        cursor = (
            self._jobs.find({"player_slug": player_slug, "state": {"$in": list(ACTIVE_STATES)}})
            .sort("_id", -1)
            .limit(1)
        )
        doc = next(iter(cursor), None)
        if doc is None:
            return None
        return self._doc_to_job(doc)

    def active_job_for_player(self, player_slug: str) -> dict[str, Any] | None:
        """Return the queued or running job for a player, if any."""
        with self._lock:
            return self._active_job_for(player_slug)

    def list_active_jobs(self) -> list[dict[str, Any]]:
        """Return every queued or running job (newest active job per player)."""
        with self._lock:
            cursor = self._jobs.find({"state": {"$in": list(ACTIVE_STATES)}}).sort("_id", -1)
            seen: set[str] = set()
            jobs: list[dict[str, Any]] = []
            for doc in cursor:
                slug = str(doc["player_slug"])
                if slug in seen:
                    continue
                seen.add(slug)
                jobs.append(self._doc_to_job(doc))
            return jobs

    def claim_next(self) -> dict[str, Any] | None:
        """Atomically claim the oldest queued job, moving it to ``fetching``."""
        with self._lock:
            now = time.time()
            doc = self._jobs.find_one_and_update(
                {"state": QUEUED},
                {"$set": {"state": FETCHING, "started_at": now, "updated_at": now}},
                sort=[("_id", 1)],
                return_document=ReturnDocument.AFTER,
            )
            if doc is None:
                return None
            return self._doc_to_job(doc)

    def set_state(
        self,
        job_id: int,
        state: str,
        *,
        detail: str | None = None,
        error: str | None = None,
    ) -> bool:
        """Transition a job to a new state, optionally updating detail/error.

        Returns:
            ``False`` when the job is already cancelled and ``state`` is not
            ``cancelled`` (so the worker cannot overwrite a user cancel).
        """
        with self._lock:
            doc = self._jobs.find_one({"_id": job_id}, {"state": 1})
            if doc is None:
                return False
            if doc["state"] == CANCELLED and state != CANCELLED:
                return False
            now = time.time()
            sets: dict[str, Any] = {"state": state, "updated_at": now}
            if detail is not None:
                sets["stage_detail"] = detail
                sets["stage_current"] = None
                sets["stage_total"] = None
            if error is not None:
                sets["error"] = error
            if state in TERMINAL_STATES:
                sets["finished_at"] = now
            self._jobs.update_one({"_id": job_id}, {"$set": sets})
            return True

    def update_progress(
        self,
        job_id: int,
        *,
        detail: str,
        current: int | None = None,
        total: int | None = None,
    ) -> None:
        """Update progress fields without changing the job state."""
        with self._lock:
            self._jobs.update_one(
                {"_id": job_id, "state": {"$ne": CANCELLED}},
                {
                    "$set": {
                        "stage_detail": detail,
                        "stage_current": current,
                        "stage_total": total,
                        "updated_at": time.time(),
                    }
                },
            )

    def is_cancelled(self, job_id: int) -> bool:
        """Return whether the job has been cancelled."""
        with self._lock:
            doc = self._jobs.find_one({"_id": job_id}, {"state": 1})
            return doc is not None and doc["state"] == CANCELLED

    def cancel(self, job_id: int) -> dict[str, Any] | None:
        """Cancel a queued or running job without deleting on-disk reports.

        Returns:
            The updated job row, or ``None`` if the job is missing or already
            in a terminal state.
        """
        with self._lock:
            now = time.time()
            doc = self._jobs.find_one_and_update(
                {"_id": job_id, "state": {"$in": list(ACTIVE_STATES)}},
                {
                    "$set": {
                        "state": CANCELLED,
                        "stage_detail": "Cancelled by user",
                        "error": "",
                        "finished_at": now,
                        "updated_at": now,
                    }
                },
                return_document=ReturnDocument.AFTER,
            )
            if doc is None:
                return None
            return self._doc_to_job(doc)

    def queue_position(self, job_id: int) -> int | None:
        """0-based number of queued jobs ahead; ``None`` unless still queued."""
        with self._lock:
            doc = self._jobs.find_one({"_id": job_id}, {"state": 1})
            if doc is None or doc["state"] != QUEUED:
                return None
            ahead = self._jobs.count_documents({"state": QUEUED, "_id": {"$lt": job_id}})
            running = self._jobs.count_documents({"state": {"$in": list(RUNNING_STATES)}})
            return int(ahead) + int(running)

    def average_duration_s(self) -> float:
        """Rolling average duration of recently completed jobs."""
        with self._lock:
            cursor = (
                self._jobs.find(
                    {"state": DONE, "started_at": {"$ne": None}, "finished_at": {"$ne": None}}
                )
                .sort("_id", -1)
                .limit(5)
            )
            durations = [
                float(doc["finished_at"]) - float(doc["started_at"])
                for doc in cursor
            ]
        durations = [d for d in durations if d > 0]
        if not durations:
            return float(DEFAULT_JOB_DURATION_S)
        return sum(durations) / len(durations)

    def recover_orphans(self) -> int:
        """Fail jobs left mid-run by a previous process (queued jobs survive).

        A single ``update_many`` with one uniform filter/update -- not a
        per-document ``bulk_write`` -- so it does not hit this repo's pinned
        pymongo/mongomock ``bulk_write`` incompatibility (see module
        docstring), and stays a single atomic multi-document write.
        """
        with self._lock:
            now = time.time()
            result = self._jobs.update_many(
                {"state": {"$in": list(RUNNING_STATES)}},
                {
                    "$set": {
                        "state": FAILED,
                        "error": "Server restarted while this job was running. Submit it again.",
                        "finished_at": now,
                        "updated_at": now,
                    }
                },
            )
            return int(result.modified_count)

    # --------------------------------------------------------------- players

    def upsert_player(
        self,
        *,
        slug: str,
        riot_id: str,
        tagline: str,
        region: str,
        players: list[dict[str, Any]] | None = None,
    ) -> None:
        """Register (or update the identity of) a player or group."""
        tracked = players or [{"riot_id": riot_id, "tagline": tagline}]
        with self._lock:
            self._players.update_one(
                {"_id": slug},
                {
                    "$set": {
                        "riot_id": riot_id,
                        "tagline": tagline,
                        "region": region,
                        "players_json": encode_players(tracked),
                    }
                },
                upsert=True,
            )

    def get_player(self, slug: str) -> dict[str, Any] | None:
        """Load one player/group row with a decoded ``players`` list."""
        with self._lock:
            doc = self._players.find_one({"_id": slug})
            if doc is None:
                return None
            return self._doc_to_player(doc)

    def set_watch(
        self, slug: str, *, enabled: bool, interval_s: int | None = None
    ) -> bool:
        """Turn watching on or off for a group. Returns ``False`` if unknown."""
        with self._lock:
            doc = self._players.find_one({"_id": slug}, {"_id": 1})
            if doc is None:
                return False
            sets: dict[str, Any] = {
                "watch_enabled": 1 if enabled else 0,
                "last_watch_error": "",
            }
            if interval_s is not None:
                sets["watch_interval_s"] = max(60, int(interval_s))
            self._players.update_one({"_id": slug}, {"$set": sets})
            return True

    def list_watched_players(self) -> list[dict[str, Any]]:
        """Every group with watching enabled, identities decoded."""
        with self._lock:
            cursor = list(self._players.find({"watch_enabled": 1}).sort("_id", 1))
        watched: list[dict[str, Any]] = []
        for doc in cursor:
            data = self._doc_to_player(doc)
            try:
                data["watch_seen"] = json.loads(data.get("watch_seen_json") or "{}")
            except json.JSONDecodeError:
                data["watch_seen"] = {}
            watched.append(data)
        return watched

    def record_watch_tick(
        self,
        slug: str,
        *,
        seen: dict[str, str] | None = None,
        error: str = "",
        at: float | None = None,
    ) -> None:
        """Stamp a watch check, storing the newest match id seen per account.

        ``at`` lets the poller supply its own clock, so the timestamp it writes
        and the one it compares against when deciding whether a group is due
        cannot disagree.
        """
        stamp = time.time() if at is None else float(at)
        sets: dict[str, Any] = {"last_watch_at": stamp, "last_watch_error": error}
        if seen is not None:
            sets["watch_seen_json"] = json.dumps(seen)
        with self._lock:
            self._players.update_one({"_id": slug}, {"$set": sets})

    def mark_player_base_complete(self, slug: str) -> None:
        """Record that the base (pre-peer) report finished for a player."""
        with self._lock:
            self._players.update_one(
                {"_id": slug}, {"$set": {"base_completed_at": time.time(), "peer_failed": 0}}
            )

    def mark_player_peer_complete(self, slug: str) -> None:
        """Record that peer analysis finished for a player."""
        with self._lock:
            self._players.update_one(
                {"_id": slug}, {"$set": {"peer_completed_at": time.time(), "peer_failed": 0}}
            )

    def mark_player_peer_failed(self, slug: str) -> None:
        """Record that peer analysis failed (base report remains available)."""
        with self._lock:
            self._players.update_one({"_id": slug}, {"$set": {"peer_failed": 1}})

    def recent_players(self, limit: int = 50) -> list[dict[str, Any]]:
        """Players ordered by most recent activity."""
        with self._lock:
            cursor = list(self._players.find({}).sort("base_completed_at", -1).limit(limit))
            return [self._doc_to_player(doc) for doc in cursor]


# Process-wide Mongo clients keyed by URI, mirroring `career_store.py`'s own
# `_SHARED_MONGO_CLIENTS`: neither `api_ui/app.py` nor `cron_watch/__main__.py`
# carries a Mongo client for `JobStore` today -- both only ever resolved an
# `app_db_path: Path` off `WebConfig`. Threading a Mongo client through either
# call path would ripple this task well outside its file list, the same
# situation Tasks 2/3 hit with `DerivedStore`/`CareerStore`'s real callers.
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
    `open_jobs_store`) so tests can monkeypatch this one function to return a
    `mongomock.MongoClient` instead of dialing a real Mongo -- matching
    `career_store.py`/`derived.py`'s own `_build_mongo_client`.
    """
    with _SHARED_MONGO_CLIENTS_LOCK:
        client = _SHARED_MONGO_CLIENTS.get(mongo_uri)
        if client is None:
            client = pymongo.MongoClient(mongo_uri)
            _SHARED_MONGO_CLIENTS[mongo_uri] = client
        return client


def open_jobs_store() -> JobStore:
    """Open the Job store against the process-wide Mongo client.

    The single production entry point for `api_ui/app.py` and
    `cron_watch/__main__.py` -- see the module comment above
    `_SHARED_MONGO_CLIENTS` for why this resolves its own client rather than
    receiving one from a caller.
    """
    uri = _resolve_mongo_uri()
    client = _build_mongo_client(uri)
    return JobStore(client, db_name=db_name_from_uri(uri))
