"""Content-addressed cache for derived analysis artifacts.

``RawMatchStore`` caches what Riot sent us. This caches what *we* computed from
it: parsed records, per-game review details, per-slice dashboard bundles.
Adding one new game should cost work proportional to one game, not to the
whole history.

The dangerous failure mode is serving an artifact computed by older code, which
is silent and produces wrong coaching. Every key therefore carries a
``code_version`` derived from a hash of the source files that produce that kind
of artifact (see :func:`code_version`), so a code change cannot hit a stale
entry -- it simply looks under a different key and recomputes.

Backed by ``pymongo.MongoClient`` (or ``mongomock.MongoClient`` in tests).
One ``derived`` collection, one document per
``(kind, key, code_version)``, keyed by ``_id = f"{kind}\\x1f{key}\\x1f
{code_version}"`` (same separator convention as
``PeerSampleStore._dedup_key``).
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from functools import lru_cache
from pathlib import Path
from types import TracebackType
from typing import Any, Final, Iterable, Sequence

import pymongo

from league_stats_common.infra.mongo import db_name_from_uri
from league_stats_common.utils import get_logger

# Artifact kinds. Each maps to the source paths whose contents define it.
KIND_RECORD: Final[str] = "record"
KIND_GAME_REVIEW: Final[str] = "game_review"
KIND_SLICE: Final[str] = "slice"

_PACKAGE_ROOT: Final[Path] = Path(__file__).resolve().parents[1]

# Source files that determine each artifact kind's contents. Deliberately
# generous: a false invalidation costs one recompute, a missed one serves wrong
# data.
_KIND_SOURCES: Final[dict[str, tuple[str, ...]]] = {
    KIND_RECORD: ("ingest", "core/models.py"),
    KIND_GAME_REVIEW: ("analysis/game_review", "pipeline/game_review.py"),
    KIND_SLICE: (
        "analysis",
        "pipeline/bundles.py",
        "presentation/view_models.py",
        "presentation/tones.py",
    ),
}

DEFAULT_MAX_BYTES: Final[int] = 512 * 1024 * 1024  # 512 MiB


def _iter_source_files(relative: str) -> Iterable[Path]:
    target = _PACKAGE_ROOT / relative
    if target.is_file():
        yield target
        return
    if target.is_dir():
        yield from sorted(target.rglob("*.py"))


@lru_cache(maxsize=None)
def code_version(kind: str) -> str:
    """Short hash of the sources that produce ``kind``.

    Computed once per process. An unknown kind hashes the whole package, which is
    conservative -- it invalidates on any change -- rather than silently sharing
    a key across code revisions.
    """
    relatives = _KIND_SOURCES.get(kind, (".",))
    digest = hashlib.sha256()
    for relative in relatives:
        for path in _iter_source_files(relative):
            digest.update(path.relative_to(_PACKAGE_ROOT).as_posix().encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()[:16]


def slice_fingerprint(match_ids: Sequence[str], *, salt: str = "") -> str:
    """Stable key for a set of games, independent of their order.

    ``salt`` carries anything outside the match set that changes the result --
    the role, the queue label, whether peer data was available.
    """
    digest = hashlib.sha256(salt.encode())
    for match_id in sorted(match_ids):
        digest.update(b"\x00")
        digest.update(match_id.encode())
    return digest.hexdigest()[:32]


class DerivedStore:
    """MongoDB cache of derived artifacts, keyed by content and code version.

    Reproduces ``DerivedStore``'s prior on-disk semantics:

    - ``put``/``put_many`` mirror the SQL ``ON CONFLICT ... DO UPDATE SET
      payload = excluded.payload, bytes = excluded.bytes, hit_at =
      excluded.hit_at``: ``created_at`` is stamped once, on first insert, and
      is never overwritten by a later overwrite of the same key (``$set``
      touches ``payload``/``bytes``/``hit_at``; ``$setOnInsert`` stamps
      ``created_at`` only when the document is created).
    - ``delete(kind, key)`` removes the artifact across *every*
      ``code_version`` (no version filter), matching the SQL version's
      ``DELETE FROM derived WHERE kind = ? AND key = ?``.
    - ``get``'s old on-disk path treated malformed JSON in a stored payload as
      a miss and deleted the row -- that failure mode does not carry over:
      the payload is stored as a native BSON document/array/scalar with no
      decode step to fail, so there's nothing analogous to catch. Real
      payload-shape corruption (the actual risk that mattered) is already
      handled independently by every real caller, which validates the
      payload into its own domain model and deletes on failure itself (see
      ``pipeline/fetch.py``'s ``MatchRecord.model_validate`` try/except and
      ``pipeline/game_review.py``'s identical pattern around
      ``GameReviewPayload.model_validate``) -- neither depended on
      ``DerivedStore.get()`` doing this for them.
    - ``evict_to_budget`` evicts least-recently-hit first (ascending
      ``hit_at`` scan) until under budget, same as the SQL version's
      ``ORDER BY hit_at ASC``.
    """

    def __init__(
        self,
        client: pymongo.MongoClient,
        db_name: str = "league_stats",
        *,
        max_bytes: int = DEFAULT_MAX_BYTES,
    ) -> None:
        """Open the store against an existing Mongo client.

        Args:
            client: A ``pymongo.MongoClient``-compatible client (real or
                ``mongomock.MongoClient`` in tests).
            db_name: Name of the database to use within the client.
            max_bytes: Byte budget enforced by :meth:`evict_to_budget`.
        """
        db = client[db_name]
        self._derived = db["derived"]
        # Mirrors the SQL `idx_derived_hit` index -- without it,
        # `evict_to_budget`'s ascending `hit_at` scan is unindexed on the
        # store's largest, longest-lived collection. `create_index` is
        # idempotent and `mongomock` supports it, so this is safe on every
        # construction, including in tests (same pattern as
        # `PeerSampleStore.__init__`).
        self._derived.create_index("hit_at")
        self._max_bytes = max_bytes
        self._log = get_logger("derived")

    def __enter__(self) -> "DerivedStore":
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

        The client is handed in from outside (see ``open_derived_store``
        below), mirroring ``RawMatchStore.close()``'s reasoning -- closing a
        shared client here would break every other user of it.
        """
        return None

    @staticmethod
    def _doc_id(kind: str, key: str, version: str) -> str:
        return f"{kind}\x1f{key}\x1f{version}"

    def get(self, kind: str, key: str) -> Any | None:
        """Return a cached artifact, or ``None`` on a miss."""
        doc = self._derived.find_one({"_id": self._doc_id(kind, key, code_version(kind))})
        if doc is None:
            return None
        self._touch(kind, key)
        return doc["payload"]

    def get_many(self, kind: str, keys: Sequence[str]) -> dict[str, Any]:
        """Return every cached artifact among ``keys``, skipping misses.

        One query instead of one per key, which is what makes warm loads cheap
        when a history has hundreds of games. Batched in chunks of 500 keys --
        not required by any Mongo `$in` size limit (verified: no
        placeholder-count cap like the old on-disk store had), but kept as a
        bounded batch size to avoid ever building one pathologically large
        query document.
        """
        if not keys:
            return {}
        version = code_version(kind)
        found: dict[str, Any] = {}
        chunk = 500
        for start in range(0, len(keys), chunk):
            batch = list(keys[start : start + chunk])
            ids = [self._doc_id(kind, key, version) for key in batch]
            for doc in self._derived.find({"_id": {"$in": ids}}):
                found[doc["key"]] = doc["payload"]
        if found:
            self._touch_many(kind, list(found))
        return found

    def put(self, kind: str, key: str, payload: Any) -> None:
        """Store one artifact, replacing any entry under the same key."""
        blob = json.dumps(payload, separators=(",", ":"), default=str)
        now = time.time()
        version = code_version(kind)
        self._derived.update_one(
            {"_id": self._doc_id(kind, key, version)},
            {
                "$set": {
                    "kind": kind,
                    "key": key,
                    "code_version": version,
                    "payload": payload,
                    "bytes": len(blob),
                    "hit_at": now,
                },
                "$setOnInsert": {"created_at": now},
            },
            upsert=True,
        )

    def put_many(self, kind: str, items: dict[str, Any]) -> None:
        """Store several artifacts.

        Looped `update_one` calls rather than `bulk_write`: this repo's
        pinned `mongomock` (4.3.0) is incompatible with `pymongo` 4.17's
        `UpdateOne` (it forwards a `sort` kwarg mongomock's
        `BulkOperationBuilder.add_update` doesn't accept -- verified
        directly against `mongomock.MongoClient()`, not assumed), and
        upgrading either package is out of scope for this task. A real
        MongoDB server would accept the bulk form; this loop is the
        version-compatible equivalent, at the cost of one round-trip per
        item instead of one batched write.
        """
        if not items:
            return
        now = time.time()
        version = code_version(kind)
        for key, payload in items.items():
            blob = json.dumps(payload, separators=(",", ":"), default=str)
            self._derived.update_one(
                {"_id": self._doc_id(kind, key, version)},
                {
                    "$set": {
                        "kind": kind,
                        "key": key,
                        "code_version": version,
                        "payload": payload,
                        "bytes": len(blob),
                        "hit_at": now,
                    },
                    "$setOnInsert": {"created_at": now},
                },
                upsert=True,
            )

    def delete(self, kind: str, key: str) -> None:
        """Drop one artifact across every code version."""
        self._derived.delete_many({"kind": kind, "key": key})

    def total_bytes(self) -> int:
        """Sum of stored payload sizes."""
        result = list(
            self._derived.aggregate([{"$group": {"_id": None, "total": {"$sum": "$bytes"}}}])
        )
        return int(result[0]["total"]) if result else 0

    def purge_stale_versions(self) -> int:
        """Drop entries whose ``code_version`` no longer matches current code.

        Only for kinds this process knows about, so a rollback to older code can
        still find its own entries until they are evicted by size.
        """
        removed = 0
        for kind in _KIND_SOURCES:
            result = self._derived.delete_many(
                {"kind": kind, "code_version": {"$ne": code_version(kind)}}
            )
            removed += result.deleted_count
        if removed:
            self._log.info("Purged %d stale derived artifact(s)", removed)
        return removed

    def evict_to_budget(self) -> int:
        """Evict least-recently-hit entries until under the byte budget."""
        total = self.total_bytes()
        if total <= self._max_bytes:
            return 0
        removed = 0
        candidates = list(
            self._derived.find({}, {"_id": 1, "bytes": 1}).sort("hit_at", 1)
        )
        for doc in candidates:
            if total <= self._max_bytes:
                break
            self._derived.delete_one({"_id": doc["_id"]})
            total -= int(doc["bytes"])
            removed += 1
        if removed:
            self._log.info("Evicted %d derived artifact(s) to stay under budget", removed)
        return removed

    def _touch(self, kind: str, key: str) -> None:
        self._derived.update_one(
            {"_id": self._doc_id(kind, key, code_version(kind))},
            {"$set": {"hit_at": time.time()}},
        )

    def _touch_many(self, kind: str, keys: Sequence[str]) -> None:
        """Looped `update_one`, not `bulk_write` -- see `put_many`'s docstring
        for why (`mongomock`/`pymongo` version incompatibility)."""
        now = time.time()
        version = code_version(kind)
        for key in keys:
            self._derived.update_one(
                {"_id": self._doc_id(kind, key, version)}, {"$set": {"hit_at": now}}
            )


# Process-wide Mongo clients keyed by URI, mirroring `worker.py`'s own
# `_SHARED_MONGO_CLIENTS`: `DerivedStore` is opened fresh in each of
# `pipeline/fetch.py`/`orchestrator.py`/`game_review.py`, but no `Services`/
# `AppConfig` link currently plumbs RUNNER's already-open Mongo client (built
# in `worker.py::_build_job_services` for `RawMatchStore`) down to any of
# those 3 call sites -- `AppConfig` carries no Mongo URI field at all, and
# `build_account_subset_views`/`build_game_review_views` are also called
# directly from `league_stats_api_ui/app.py`, a process with no `Services`
# object to plumb one from in the first place. Threading a `mongo_client`
# parameter through `run_analysis` -> `build_report_views` ->
# `build_account_subset_views` -> `api_ui/app.py`'s call site would ripple
# this task well outside its own file list. Resolving the URI from the same
# environment variables `WebConfig.runner_mongo_uri` already uses, and
# sharing one client per URI per process here, is a deliberate
# scope-narrowing choice for this task -- reusing RUNNER's *already-open*
# client across `RawMatchStore` and `DerivedStore` within one job is a
# legitimate follow-up, not done here.
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
    `open_derived_store`) so tests can monkeypatch this one function to
    return a `mongomock.MongoClient` instead of dialing a real Mongo --
    matching `worker.py`'s own `_build_mongo_client`.
    """
    with _SHARED_MONGO_CLIENTS_LOCK:
        client = _SHARED_MONGO_CLIENTS.get(mongo_uri)
        if client is None:
            client = pymongo.MongoClient(mongo_uri)
            _SHARED_MONGO_CLIENTS[mongo_uri] = client
        return client


def open_derived_store(*, max_bytes: int = DEFAULT_MAX_BYTES) -> DerivedStore:
    """Open the derived-artifact store against the process-wide Mongo client.

    The single production entry point for `pipeline/fetch.py`,
    `pipeline/orchestrator.py` and `pipeline/game_review.py` -- see the
    module comment above `_SHARED_MONGO_CLIENTS` for why this resolves its
    own client rather than receiving one from a caller.
    """
    uri = _resolve_mongo_uri()
    client = _build_mongo_client(uri)
    return DerivedStore(client, db_name=db_name_from_uri(uri), max_bytes=max_bytes)
