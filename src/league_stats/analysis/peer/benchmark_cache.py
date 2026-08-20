"""Mongo-primary, file-fallback cache for live-sampled peer benchmarks.

Cache entries are keyed by ``{platform}_{tier}_{champion_slug}`` (the same
string the old on-disk cache used as its filename stem -- see "Migration
history" below) inside ``LiveBenchmarkCacheStore``'s ``live_benchmark_cache``
collection (``infra/live_benchmark_cache_store.py``). A cached entry is
reused only when all three of these hold:

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
``data/benchmarks/live/`` (``{platform}_{tier}_{champion_slug}.json``). Task 3
moved it to Mongo (``infra.live_benchmark_cache_store.LiveBenchmarkCacheStore``),
with the exact same key shape and staleness semantics (patch + tier match,
``CACHE_TTL_S`` = 3 days) -- ``read_live_cache``/``write_live_cache`` keep
their original signatures, so no caller (``analysis/peer/baseline.py``)
needed to change.

Mongo-primary, file-fallback (Phase 5 final review, Finding 1)
-------------------------------------------------------------------
Task 3 made Mongo the *only* backend, which silently regressed the one real
deployment this app has: `deploy/run.sh` runs a bare systemd unit on a VPS
with no Mongo, and local dev (`uv run python main.py`) has no Mongo either --
both default to `peers_mode="in_process"`. On both, every read became a
guaranteed miss (after paying `_SERVER_SELECTION_TIMEOUT_MS` to find that
out) and every write became a silent no-op, so the file cache Task 3 removed
was actually the *only* working cache on that topology. The old on-disk
JSON cache is therefore back as a fallback layer, not a replacement:

* ``read_live_cache`` tries Mongo first; if Mongo returns a hit, that's used.
  If Mongo returns ``None`` -- whether a genuine miss or an unreachable
  server caught by the ``PyMongoError`` guard below -- it falls back to the
  file cache before finally reporting a miss.
* ``write_live_cache`` writes to *both* backends, each best-effort. Whichever
  one is actually reachable on a given deployment (Mongo in compose, the
  filesystem on the bare VPS) ends up with a working cache; the other write
  simply fails soft.

The old ``data/benchmarks/live/`` directory and its filename scheme
(``{platform}_{tier}_{champion_slug}.json``) are unchanged from before Task 3.

Fail-soft on a broken/unreachable Mongo, on both read and write
-------------------------------------------------------------------
The old file cache's read path caught ``(OSError, json.JSONDecodeError)`` and
degraded to a cache miss on any failure; its write path caught ``OSError``
and silently gave up. Both ``read_live_cache`` and ``write_live_cache`` here
do the Mongo equivalent (catching ``pymongo.errors.PyMongoError``), and for
the same reason: ``peers_mode`` defaults to ``in_process``, so a broken or
absent local Mongo (e.g. running ``uv run python main.py`` without a Mongo
instance up) must degrade level 2 to "no cached sample, go live-sample" --
never let a cache lookup itself blow up ``resolve_peer_baseline`` and skip
the static-benchmark fallback levels (3/4/5) a genuine cache miss would still
reach. ``_get_store``'s client is built with a short
``serverSelectionTimeoutMS`` for the same reason: a best-effort cache lookup
inside a user-facing request path must not stall for pymongo's ~30s default.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Final

import pymongo

from league_stats.analysis.peer.benchmark_fetcher import BenchmarkSnapshot, MIN_BENCHMARK_GAMES
from league_stats.core.champions import champion_slug
from league_stats.infra.live_benchmark_cache_store import LiveBenchmarkCacheStore
from league_stats.infra.mongo import db_name_from_uri as _db_name_from_uri

CACHE_TTL_S: Final[float] = 3 * 24 * 3600  # 3 days

# Fallback file cache -- see "Mongo-primary, file-fallback" above. Same
# directory/filename scheme the pre-Task-3 file cache used.
_LIVE_CACHE_DIR: Final[Path] = (
    Path(__file__).resolve().parents[2] / "data" / "benchmarks" / "live"
)

# Margin added on top of CACHE_TTL_S for the Mongo TTL index (see
# LiveBenchmarkCacheStore) -- purely a housekeeping backstop against unbounded
# collection growth, kept comfortably past CACHE_TTL_S so it only ever prunes
# entries read_live_cache would already refuse to serve as stale.
_TTL_INDEX_MARGIN_S: Final[float] = 3600

# How long to wait for server selection when building the Mongo client below.
# A best-effort cache lookup inside a user-facing request path must fail fast
# (and fall through to live sampling / static fallback) rather than stall for
# pymongo's ~30s default -- see this module's docstring.
_SERVER_SELECTION_TIMEOUT_MS: Final[int] = 4000

# Module-level singleton, built lazily against MONGO_URI on first use and
# reused across calls -- mirrors `peers/service.py`'s `_build_default_peer_store`
# (same env var, same fallback URI, same db-name-from-URI helper below).
# Tests monkeypatch this attribute directly with a mongomock-backed store,
# the same way the old file cache's tests monkeypatched `_LIVE_CACHE_DIR`.
_store: LiveBenchmarkCacheStore | None = None


def _get_store() -> LiveBenchmarkCacheStore:
    global _store
    if _store is None:
        mongo_uri = os.environ.get("MONGO_URI", "mongodb://localhost:27017/league_stats")
        client: pymongo.MongoClient = pymongo.MongoClient(
            mongo_uri, serverSelectionTimeoutMS=_SERVER_SELECTION_TIMEOUT_MS
        )
        _store = LiveBenchmarkCacheStore(
            client,
            db_name=_db_name_from_uri(mongo_uri),
            ttl_seconds=CACHE_TTL_S + _TTL_INDEX_MARGIN_S,
        )
    return _store


def _cache_key(platform: str, tier: str, champion: str, role: str) -> str:
    slug = champion_slug(champion, role)
    return f"{platform.lower()}_{tier.upper()}_{slug}"


def _cache_path(platform: str, tier: str, champion: str, role: str) -> Path:
    key = _cache_key(platform, tier, champion, role)
    return _LIVE_CACHE_DIR / f"{key}.json"


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


def _snapshot_from_data(
    data: dict[str, Any], platform: str, *, patch: str
) -> BenchmarkSnapshot | None:
    """Turn a raw cached document into a `BenchmarkSnapshot`, or None if it's stale/thin.

    Shared by both backends (Mongo and file) so the patch/TTL/min-games gates
    stay identical regardless of which one actually served the hit.
    """
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


def _read_file_cache(
    platform: str, tier: str, champion: str, role: str, *, patch: str
) -> BenchmarkSnapshot | None:
    path = _cache_path(platform, tier, champion, role)
    if not path.is_file():
        return None
    try:
        with path.open(encoding="utf-8") as fh:
            data: dict[str, Any] = json.load(fh)
    except (OSError, json.JSONDecodeError):
        # Fail-soft, same as the pre-Task-3 file cache: a broken/missing file
        # degrades to a miss, it never blows up resolve_peer_baseline.
        return None
    return _snapshot_from_data(data, platform, patch=patch)


def _write_file_cache(
    platform: str, tier: str, champion: str, role: str, data: dict[str, Any]
) -> None:
    try:
        _LIVE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path = _cache_path(platform, tier, champion, role)
        with path.open("w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
    except OSError:
        # Best-effort, mirroring the old file cache's `except OSError: pass`
        # -- a failed cache write must never break a successful live sample.
        pass


def read_live_cache(
    platform: str,
    tier: str,
    champion: str,
    role: str,
    *,
    patch: str = "",
) -> BenchmarkSnapshot | None:
    """Return a cached benchmark snapshot if it is still valid.

    Tries the Mongo-backed store first; if that misses -- a genuine miss, or
    an unreachable/broken Mongo caught by the `PyMongoError` guard below --
    falls back to the on-disk JSON cache before finally reporting a miss. See
    this module's docstring ("Mongo-primary, file-fallback").
    """
    key = _cache_key(platform, tier, champion, role)
    try:
        data: dict[str, Any] | None = _get_store().read(key)
    except pymongo.errors.PyMongoError:
        # Fail-soft, mirroring the old file cache's `except (OSError,
        # json.JSONDecodeError): return None` -- a broken/unreachable Mongo
        # must degrade to a cache miss, not blow up resolve_peer_baseline and
        # skip the static-benchmark fallback levels a real miss would still
        # reach. See this module's docstring.
        data = None

    if data is not None:
        snapshot = _snapshot_from_data(data, platform, patch=patch)
        if snapshot is not None:
            return snapshot

    return _read_file_cache(platform, tier, champion, role, patch=patch)


def write_live_cache(
    platform: str,
    tier: str,
    champion: str,
    role: str,
    snapshot: BenchmarkSnapshot,
    *,
    patch: str = "",
) -> None:
    """Persist a live-sampled benchmark snapshot to both cache backends.

    Writes to Mongo and to the on-disk JSON cache, each best-effort --
    whichever backend is actually reachable on a given deployment ends up
    with a working cache. See this module's docstring ("Mongo-primary,
    file-fallback").
    """
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
        _get_store().write(key, data)
    except pymongo.errors.PyMongoError:
        # Best-effort: mirrors the old file cache's `except OSError: pass` --
        # a failed cache write must never break a successful live sample.
        pass

    _write_file_cache(platform, tier, champion, role, data)
