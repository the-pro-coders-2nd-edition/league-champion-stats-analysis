"""Shared pytest fixtures."""

from __future__ import annotations

import mongomock
import pytest

from league_stats_peers.analysis.peer import benchmark_cache as _benchmark_cache
from league_stats_common.infra import career_store as _career_store
from league_stats_common.infra.ddragon_assets import DDragonAssets
from league_stats_peers.infra.live_benchmark_cache_store import LiveBenchmarkCacheStore
from league_stats_runner.infra import derived as _derived


@pytest.fixture(autouse=True)
def _skip_ddragon_downloads(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Avoid bulk Data Dragon downloads during tests."""
    if request.module.__name__.endswith("test_ddragon_assets"):
        return
    monkeypatch.setattr(DDragonAssets, "ensure_downloaded", lambda self, force=False: "")


@pytest.fixture(autouse=True)
def _peer_live_cache_uses_mongomock(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Default every test's PEERS live-benchmark cache (Phase 5, Task 3) to an
    in-memory mongomock store instead of a real Mongo connection.

    `analysis.peer.benchmark_cache.read_live_cache`/`write_live_cache` lazily
    build a real `pymongo.MongoClient` against `MONGO_URI` (falling back to
    `localhost:27017`) on first use. Without this fixture, any test that
    exercises `resolve_peer_baseline`'s level-2 fallback -- even one that
    never intended to touch the live cache at all -- would try a real network
    connection and hang until `pymongo`'s server-selection timeout, since no
    real Mongo instance runs in this test environment. Tests that specifically
    assert on live-cache behavior (`test_peer_cache_invalidation.py`, some of
    `test_peer_blend.py`) install their own dedicated mongomock store per test,
    which simply runs after this one and overrides it.

    Also redirects the file-cache fallback (Phase 5 final review, Finding 1 --
    `write_live_cache` now writes to both Mongo and an on-disk JSON cache) at
    `_LIVE_CACHE_DIR` into a per-test tmp directory, so running the suite
    never litters the real `data/benchmarks/live/` directory with cache files.
    """
    monkeypatch.setattr(
        _benchmark_cache,
        "_store",
        LiveBenchmarkCacheStore(mongomock.MongoClient(), db_name="test_default_live_cache"),
    )
    monkeypatch.setattr(_benchmark_cache, "_LIVE_CACHE_DIR", tmp_path / "live_cache")


@pytest.fixture(autouse=True)
def _derived_store_uses_mongomock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default every test's RUNNER derived-artifact cache (Phase 8, Task 2) to
    an in-memory mongomock store instead of a real Mongo connection.

    `derived.open_derived_store` lazily builds a real `pymongo.MongoClient`
    against `RUNNER_MONGO_URI`/`MONGO_URI` (falling back to
    `localhost:27017`) on first use via `_build_mongo_client`. Without this
    fixture, any test that exercises `load_all_records`/`build_report_views`/
    `build_game_review_views` would try a real network connection and hang
    until pymongo's server-selection timeout, since no real Mongo instance
    runs in this test environment. One fresh client per test (function-scoped
    fixture) mirrors the old per-test `tmp_path` SQLite file isolation --
    tests that need to inspect the store's raw documents install their own
    dedicated mongomock client and override this fixture's monkeypatch (it
    simply runs after this one).
    """
    client = mongomock.MongoClient()
    monkeypatch.setattr(_derived, "_build_mongo_client", lambda uri: client)


@pytest.fixture(autouse=True)
def _career_store_uses_mongomock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default every test's Career store (Phase 8, Task 3) to an in-memory
    mongomock store instead of a real Mongo connection.

    `career_store.open_career_store` lazily builds a real `pymongo.MongoClient`
    against `RUNNER_MONGO_URI`/`MONGO_URI` (falling back to
    `localhost:27017`) on first use via `_build_mongo_client`, the same seam
    `derived.py` uses. Without this fixture, any test that exercises a real
    HTTP route touching Career (`career/ack`, `career/recap/ack`,
    `career/drop`) or `pipeline/bundles.py::build_career_bundle` would try a
    real network connection and hang. One fresh client per test
    (function-scoped fixture) mirrors the old per-test `tmp_path` SQLite file
    isolation -- tests that need a specific client (e.g. to seed data the
    real route must also see, or to prove two different stores are isolated)
    construct their own `CareerStore`/`mongomock.MongoClient()` directly and
    either reuse this fixture's client (via `open_career_store()`) or build a
    separate one, same as `_derived_store_uses_mongomock` above.
    """
    client = mongomock.MongoClient()
    monkeypatch.setattr(_career_store, "_build_mongo_client", lambda uri: client)
