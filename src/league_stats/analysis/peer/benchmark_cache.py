"""Mongo-backed cache for live-sampled peer benchmarks.

Cache entries are keyed by ``{platform}_{tier}_{champion_slug}`` (the same
string the old on-disk cache used as its filename stem -- see "Migration
history" below) inside ``PeerSampleStore``'s ``live_benchmark_cache``
collection. A cached entry is reused only when all three of these hold:

* the game patch it was sampled on matches the patch being analysed,
* the tracked player is still in the same tier (encoded in the key), and
* it was fetched less than ``CACHE_TTL_S`` ago.

Patch is the one that matters. Peer metrics move with gameplay patches, and a
cold sample costs up to ~750 Riot requests per build, so the alternative to
patch-keying is either serving stale peers or paying that cost on a timer.

Division and LP are deliberately *not* part of the key: ``build_exact_scope``
accepts every division inside a tier and ``rank_matches`` never looks at LP, so
moving from Gold IV to Gold I does not change who your peers are.

Migration history (Phase 5, Task 3)
------------------------------------
This cache used to be a directory of JSON files under
``data/benchmarks/live/`` (``{platform}_{tier}_{champion_slug}.json``). It is
now backed by Mongo (``PeerSampleStore.read_benchmark_cache``/
``write_benchmark_cache``) instead, with the exact same key shape and
staleness semantics (patch + tier match, ``CACHE_TTL_S`` = 3 days) --
``read_live_cache``/``write_live_cache`` keep their original signatures, so
no caller (``analysis/peer/baseline.py``) needed to change. The old
``data/benchmarks/live/`` directory and any JSON files already in it are left
untouched on disk -- simply unused from now on.
"""

from __future__ import annotations

import os
import time
from typing import Any, Final

import pymongo
from pymongo import uri_parser as mongo_uri_parser

from league_stats.analysis.peer.benchmark_fetcher import BenchmarkSnapshot, MIN_BENCHMARK_GAMES
from league_stats.core.champions import champion_slug
from league_stats.infra.peer_sample_store import PeerSampleStore

CACHE_TTL_S: Final[float] = 3 * 24 * 3600  # 3 days

# Module-level singleton, built lazily against MONGO_URI on first use and
# reused across calls -- mirrors `peers/service.py`'s `_build_default_peer_store`
# (same env var, same fallback URI, same db-name-from-URI helper below).
# Tests monkeypatch this attribute directly with a mongomock-backed store,
# the same way the old file cache's tests monkeypatched `_LIVE_CACHE_DIR`.
_store: PeerSampleStore | None = None


def _db_name_from_uri(mongo_uri: str) -> str:
    """Extract the database name from a Mongo connection URI.

    Mirrors `peers/service.py`'s `_db_name_from_uri` -- uses pymongo's own URI
    parser rather than a naive `rsplit`, which breaks on query params or a
    bare host with no db path.
    """
    return mongo_uri_parser.parse_uri(mongo_uri).get("database") or "league_stats"


def _get_store() -> PeerSampleStore:
    global _store
    if _store is None:
        mongo_uri = os.environ.get("MONGO_URI", "mongodb://localhost:27017/league_stats")
        client: pymongo.MongoClient = pymongo.MongoClient(mongo_uri)
        _store = PeerSampleStore(client, db_name=_db_name_from_uri(mongo_uri))
    return _store


def _cache_key(platform: str, tier: str, champion: str, role: str) -> str:
    slug = champion_slug(champion, role)
    return f"{platform.lower()}_{tier.upper()}_{slug}"


def _patch_is_stale(stored: str, wanted: str) -> bool:
    """Whether a cached sample's patch disqualifies it.

    An unknown *wanted* patch (no records to read one from) falls back to the TTL
    alone rather than discarding a usable sample. An unknown *stored* patch means
    the entry predates patch tracking, so it is discarded as soon as we do know
    which patch we want -- fail closed, at the cost of one re-sample after upgrade.
    """
    if not wanted:
        return False
    return stored != wanted


def read_live_cache(
    platform: str,
    tier: str,
    champion: str,
    role: str,
    *,
    patch: str = "",
) -> BenchmarkSnapshot | None:
    """Return a cached benchmark snapshot if it is still valid."""
    key = _cache_key(platform, tier, champion, role)
    data: dict[str, Any] | None = _get_store().read_benchmark_cache(key)
    if data is None:
        return None

    if _patch_is_stale(str(data.get("patch", "")), patch):
        return None

    fetched_at = float(data.get("fetched_at", 0))
    if time.time() - fetched_at > CACHE_TTL_S:
        return None

    games = int(data.get("games", 0))
    if games < MIN_BENCHMARK_GAMES:
        return None

    return BenchmarkSnapshot(
        metrics=dict(data.get("metrics", {})),
        games_sampled=games,
        players_sampled=int(data.get("players", 0)),
        from_cache=True,
        platform=platform,
    )


def write_live_cache(
    platform: str,
    tier: str,
    champion: str,
    role: str,
    snapshot: BenchmarkSnapshot,
    *,
    patch: str = "",
) -> None:
    """Persist a live-sampled benchmark snapshot to the Mongo-backed cache."""
    key = _cache_key(platform, tier, champion, role)
    data = {
        "metrics": snapshot.metrics,
        "games": snapshot.games_sampled,
        "players": snapshot.players_sampled,
        "fetched_at": time.time(),
        "tier": tier.upper(),
        "platform": platform.lower(),
        "patch": patch,
    }
    try:
        _get_store().write_benchmark_cache(key, data)
    except pymongo.errors.PyMongoError:
        # Best-effort: mirrors the old file cache's `except OSError: pass` --
        # a failed cache write must never break a successful live sample.
        pass
