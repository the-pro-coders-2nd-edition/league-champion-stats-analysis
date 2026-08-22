"""MongoDB persistence for generated reports.

Replaces the old file tree under ``output_dir/reports/{player_slug}/{build_slug}/``
(``report.json``, ``meta.json``, ``manifest.json``, ``summary.json``,
``progression.json``, ``progression.md``). A missing volume mount used to lose a
whole report silently, a read-only mount crashed the write, and a fresh volume
had no reports at all -- all three go away once nothing report-shaped is a file.

Two collections, split by read pattern rather than by old filename:

- ``report_builds``: everything needed to list a player's builds (the old
  ``meta.json`` + ``manifest.json`` content) *without* loading a report body,
  which can be a hundred-plus MB for a multi-account group. One document per
  ``(player_slug, build_slug)``, enforced by a compound unique index on those
  two fields; ``_id`` is a Mongo-assigned ``ObjectId``. Indexed on
  ``player_slug`` since every player-hub/nav read filters on it. Carries
  ``match_ids`` so ``should_skip_unchanged_build`` can tell "no report yet"
  from "report exists" without touching the heavy body.
- ``report_bodies``: the rendered report (old ``report.json``), the chatbot
  summary (old ``summary.json``) and the progression export (old
  ``progression.json``/``progression.md``) for one build. Same key shape.
  Only read when a specific build's full report is requested.

This mirrors the existing intermediate-step caches in this codebase
(``DerivedStore``'s per-slice cache, ``CareerStore``'s ladder state): the
final assembled report is the *last* checkpoint, not an intermediate one, so
it gets its own collection rather than pretending to be a cache.
"""

from __future__ import annotations

import os
import threading
from types import TracebackType
from typing import Any, Iterable

import pymongo

from league_stats_common.infra.mongo import db_name_from_uri
from league_stats_common.utils import get_logger


class ReportStore:
    """MongoDB store of generated report metadata and bodies."""

    def __init__(self, client: pymongo.MongoClient, db_name: str = "league_stats") -> None:
        """Open the store against an existing Mongo client.

        Args:
            client: A ``pymongo.MongoClient``-compatible client (real or
                ``mongomock.MongoClient`` in tests).
            db_name: Name of the database to use within the client.
        """
        db = client[db_name]
        self._builds = db["report_builds"]
        self._bodies = db["report_bodies"]
        self._builds.create_index("player_slug")
        self._builds.create_index([("player_slug", 1), ("build_slug", 1)], unique=True)
        self._bodies.create_index([("player_slug", 1), ("build_slug", 1)], unique=True)
        self._view_slices = db["report_view_slices"]
        self._game_reviews = db["report_game_review"]
        self._view_slices.create_index(
            [("player_slug", 1), ("build_slug", 1), ("queue_key", 1), ("window_key", 1)],
            unique=True,
        )
        self._game_reviews.create_index([("player_slug", 1), ("build_slug", 1)], unique=True)
        self._log = get_logger("report_store")

    def __enter__(self) -> "ReportStore":
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

        The client is handed in from outside (see ``open_report_store``
        below), mirroring ``CareerStore``/``DerivedStore``'s reasoning --
        closing a shared client here would break every other user of it.
        """
        return None

    # -- report_builds (light: listing, existence, skip-unchanged checks) --

    def save_build(
        self,
        player_slug: str,
        build_slug: str,
        meta: dict[str, Any],
        *,
        match_ids: Iterable[str] = (),
    ) -> None:
        """Upsert one build's listing metadata (old ``meta.json``/manifest entry).

        ``match_ids`` is stored alongside so ``match_ids_for_build`` can answer
        "does this build already cover match X" without loading the report body.
        """
        doc = {**meta, "player_slug": player_slug,
               "build_slug": build_slug, "match_ids": sorted(set(match_ids))}
        self._builds.replace_one(
            {"player_slug": player_slug, "build_slug": build_slug}, doc, upsert=True
        )

    def get_build(self, player_slug: str, build_slug: str) -> dict[str, Any] | None:
        """One build's listing metadata, or ``None`` if it has never been written."""
        return self._builds.find_one({"player_slug": player_slug, "build_slug": build_slug})

    def has_build(self, player_slug: str, build_slug: str) -> bool:
        """Whether a build has ever been analysed and saved."""
        return self._builds.count_documents(
            {"player_slug": player_slug, "build_slug": build_slug}, limit=1
        ) > 0

    def match_ids_for_build(self, player_slug: str, build_slug: str) -> frozenset[str] | None:
        """Match ids the stored build was last analysed with, or ``None`` if unsaved."""
        doc = self._builds.find_one(
            {"player_slug": player_slug, "build_slug": build_slug}, {"match_ids": 1}
        )
        if doc is None:
            return None
        return frozenset(str(m) for m in doc.get("match_ids", []))

    def patch_build_fields(
        self, player_slug: str, build_slug: str, fields: dict[str, Any]
    ) -> bool:
        """Partially update specific keys in an existing build's LIGHT listing
        entry (``report_builds``, old ``meta.json``/manifest content).

        A progressive body patch (``patch_report_fields``) alone is not
        enough for a caller like `_run_stage_b`'s interim peer-comparison
        push to actually reach the browser: `discover_player_builds` (what
        `/api/players/{slug}`'s polling/SSE response is built from) reads
        `generated_at`/`has_peer_comparison` from THIS light document, not
        the heavy `report_bodies` one -- patching only the body would leave
        the frontend's `generated_at`-triggers-refetch logic (and the
        `peers_ready` flag) looking at stale Stage-A values forever, even
        though the body itself improved. Returns ``False`` if no listing
        entry exists yet -- callers should fall back to a full `save_build`
        in that case, same as `patch_report_fields`.
        """
        result = self._builds.update_one(
            {"player_slug": player_slug, "build_slug": build_slug}, {"$set": fields}
        )
        return result.matched_count > 0

    def list_builds(self, player_slug: str) -> list[dict[str, Any]]:
        """Every build's listing metadata for a player, most-played first.

        Mirrors the old ``discover_player_builds``'s sort key and ``href``
        field so callers that formatted the ``href`` themselves keep working.
        """
        docs = list(self._builds.find({"player_slug": player_slug}))
        for doc in docs:
            doc.setdefault("href", f"{doc['build_slug']}/report.json")
        docs.sort(key=lambda entry: (entry.get("games", 0), entry.get("generated_at", "")), reverse=True)
        return docs

    def list_player_slugs(self) -> list[str]:
        """Every player slug with at least one saved build, sorted.

        Replaces the old ``reports_dir.iterdir()`` directory scan the landing
        page used to enumerate which players have a report at all.
        """
        return sorted(str(slug) for slug in self._builds.distinct("player_slug"))

    def list_all_builds(self) -> list[dict[str, Any]]:
        """Every build's listing metadata across every player (admin/index use)."""
        return list(self._builds.find({}))

    def delete_player(self, player_slug: str) -> None:
        """Drop every build (listing + body + view slices + game review) for a player."""
        self._builds.delete_many({"player_slug": player_slug})
        self._bodies.delete_many({"player_slug": player_slug})
        self._view_slices.delete_many({"player_slug": player_slug})
        self._game_reviews.delete_many({"player_slug": player_slug})

    # -- report_bodies (heavy: full report/summary/progression payloads) --

    def save_body(
        self,
        player_slug: str,
        build_slug: str,
        *,
        report: dict[str, Any],
        summary: dict[str, Any],
        progression_json: dict[str, Any] | None = None,
        progression_md: str = "",
        view_slices: dict[tuple[str, str], dict[str, Any]] | None = None,
        game_review: dict[str, Any] | None = None,
    ) -> None:
        """Upsert one build's heavy report body, split across three collections.

        `view_slices` (one entry per (queue_key, window_key) combination -- the
        old `report["report_views"][queue]["windows"][window]` shape,
        flattened) and `game_review` (the old `report["game_review"]`, the full
        multi-queue payload) are stored in their own collections instead of
        inline: MongoDB's 16MB per-document cap is per-document, not
        per-field, so keeping every queue x window combination in one BSON
        document does not bound total size as games count grows (a build with
        598 games already produced a ~4MB body in the cheapest possible case;
        `report_view_slices` split by combination keeps each individual
        document small and bounded instead). `report` must NOT contain a
        `report_views` or `game_review` key -- callers build those as
        separate `view_slices`/`game_review` arguments instead of nesting
        them into `report`, so the oversized dict is never assembled in the
        first place.

        A `view_manifest` field (scalars only: `total_games`,
        `default_window`, `window_options`, `windows` -- the list of window
        keys that have a slice, not the slices themselves) is derived from
        `view_slices` and added to the head document, so callers reading
        `get_report` still know what other combinations exist without
        fetching every slice.
        """
        view_slices = view_slices or {}
        manifest: dict[str, dict[str, Any]] = {}
        for (queue_key, window_key), bundle in view_slices.items():
            entry = manifest.setdefault(
                queue_key, {"total_games": 0, "default_window": None, "window_options": [], "windows": []}
            )
            entry["windows"].append(window_key)
            entry["total_games"] = bundle.get("total_games", entry["total_games"])
            entry["default_window"] = bundle.get("default_window", entry["default_window"])
            entry["window_options"] = bundle.get("window_options", entry["window_options"])
        doc = {
            "player_slug": player_slug,
            "build_slug": build_slug,
            "report": {**report, "view_manifest": manifest},
            "summary": summary,
            "progression_json": progression_json,
            "progression_md": progression_md,
        }
        self._bodies.replace_one(
            {"player_slug": player_slug, "build_slug": build_slug}, doc, upsert=True
        )
        for (queue_key, window_key), bundle in view_slices.items():
            self._view_slices.replace_one(
                {
                    "player_slug": player_slug,
                    "build_slug": build_slug,
                    "queue_key": queue_key,
                    "window_key": window_key,
                },
                {
                    "player_slug": player_slug,
                    "build_slug": build_slug,
                    "queue_key": queue_key,
                    "window_key": window_key,
                    "bundle": bundle,
                },
                upsert=True,
            )
        if game_review is not None:
            self._game_reviews.replace_one(
                {"player_slug": player_slug, "build_slug": build_slug},
                {
                    "player_slug": player_slug,
                    "build_slug": build_slug,
                    "game_review": game_review,
                },
                upsert=True,
            )

    def patch_report_fields(
        self, player_slug: str, build_slug: str, fields: dict[str, Any]
    ) -> bool:
        """Partially update specific keys inside an existing build's report body.

        Used for cheap progressive updates (e.g. an interim peer-comparison
        push during live sampling, design "Progressive peer-comparison
        updates during live sampling" §3.2) that must not require the caller
        to round-trip the whole body (report + summary + progression) through
        Python just to change a few report fields. Returns ``False`` if no
        body exists yet for this build -- callers should fall back to a full
        ``save_body`` in that case, the same as they would for a brand-new
        build.
        """
        update = {f"report.{key}": value for key, value in fields.items()}
        result = self._bodies.update_one(
            {"player_slug": player_slug, "build_slug": build_slug}, {"$set": update}
        )
        return result.matched_count > 0

    def get_report(self, player_slug: str, build_slug: str) -> dict[str, Any] | None:
        """The rendered report payload (old ``report.json``), or ``None``."""
        doc = self._bodies.find_one(
            {"player_slug": player_slug, "build_slug": build_slug}, {"report": 1}
        )
        return doc.get("report") if doc else None

    def get_summary(self, player_slug: str, build_slug: str) -> dict[str, Any] | None:
        """The chatbot summary payload (old ``summary.json``), or ``None``."""
        doc = self._bodies.find_one(
            {"player_slug": player_slug, "build_slug": build_slug}, {"summary": 1}
        )
        return doc.get("summary") if doc else None

    def get_view_slice(
        self, player_slug: str, build_slug: str, queue_key: str, window_key: str
    ) -> dict[str, Any] | None:
        """One (queue, window) dashboard bundle, or `None` if never computed."""
        doc = self._view_slices.find_one(
            {
                "player_slug": player_slug,
                "build_slug": build_slug,
                "queue_key": queue_key,
                "window_key": window_key,
            },
            {"bundle": 1},
        )
        return doc.get("bundle") if doc else None

    def get_game_review(self, player_slug: str, build_slug: str) -> dict[str, Any] | None:
        """The full multi-queue game-review payload, or `None`."""
        doc = self._game_reviews.find_one(
            {"player_slug": player_slug, "build_slug": build_slug}, {"game_review": 1}
        )
        return doc.get("game_review") if doc else None

    def row_counts(self) -> dict[str, int]:
        """Document counts per collection, for parity with other stores' admin tooling."""
        return {
            "report_builds": self._builds.count_documents({}),
            "report_bodies": self._bodies.count_documents({}),
            "report_view_slices": self._view_slices.count_documents({}),
            "report_game_review": self._game_reviews.count_documents({}),
        }


# Process-wide Mongo clients keyed by URI, mirroring `career_store.py`'s own
# `_SHARED_MONGO_CLIENTS` -- neither `AppConfig` (RUNNER) nor `WebConfig`
# (api-ui) carries a dedicated Mongo client through every call path that
# needs report data, so this resolves its own client from the same
# environment variables every other store already uses.
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
    `open_report_store`) so tests can monkeypatch this one function to return
    a `mongomock.MongoClient` instead of dialing a real Mongo -- matching
    `career_store.py`/`derived.py`'s own `_build_mongo_client`.
    """
    with _SHARED_MONGO_CLIENTS_LOCK:
        client = _SHARED_MONGO_CLIENTS.get(mongo_uri)
        if client is None:
            client = pymongo.MongoClient(mongo_uri)
            _SHARED_MONGO_CLIENTS[mongo_uri] = client
        return client


def open_report_store() -> ReportStore:
    """Open the report store against the process-wide Mongo client."""
    uri = _resolve_mongo_uri()
    client = _build_mongo_client(uri)
    return ReportStore(client, db_name=db_name_from_uri(uri))
