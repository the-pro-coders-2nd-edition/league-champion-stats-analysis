"""Shared pytest fixtures."""

from __future__ import annotations

import mongomock
import pytest

from league_stats_peers import service as _peers_service
from league_stats_peers.analysis.peer import baseline as _peer_baseline
from league_stats_peers.analysis.peer import benchmark_cache as _benchmark_cache
from league_stats_common.infra import career_store as _career_store
from league_stats_common.infra import jobs as _jobs
from league_stats_common.infra import report_store as _report_store
from league_stats_common.infra.ddragon_assets import DDragonAssets
from league_stats_peers.infra.live_benchmark_cache_store import LiveBenchmarkCacheStore
from league_stats_peers.infra.peer_match_sample_store import PeerMatchSampleStore
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
def _peer_match_sample_store_uses_mongomock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default every test's Phase 2 shared match cache (`peer_match_samples`,
    RFC "Batched, Round-Robin Live Sampling for PEERS") to an in-memory
    mongomock store instead of a real Mongo connection.

    `service._build_default_match_sample_store` lazily builds a real
    `pymongo.MongoClient` against `MONGO_URI` (falling back to
    `localhost:27017`) the first time any `SamplingTask` actually reaches
    level-2 live sampling (`service._LazyMatchSampleStore`). Without this
    fixture, any such test would try a real network connection -- several
    seconds per attempt with nothing listening, easily blowing past
    `RequestBaseline`'s fast-path timeout in tests that expect a fast
    synchronous static-fallback result. Mirrors `_peer_live_cache_uses_mongomock`
    above, one level down the fallback ladder.

    Also force-resets `baseline._default_scheduler` (the process-wide
    `SamplingScheduler` singleton) to `None` on a *plain* assignment, not via
    `monkeypatch.setattr`: production code (`_get_default_scheduler`)
    reassigns that same global directly the first time any test reaches
    level 2, and `monkeypatch`'s revert-at-teardown restores whatever value
    it recorded *before this fixture ran* -- i.e. whatever a still-earlier
    test's `_get_default_scheduler()` call left behind, not `None`. Using
    `monkeypatch.setattr` here silently leaked a stale scheduler (and its
    already-running background batch-worker threads, and the state of
    whatever `SamplingTask`s they were still processing) into unrelated
    later tests, causing order-dependent flakiness across test files
    (`resolve_peer_baseline` intermittently reusing a scheduler instance
    left over from a previous test's `PeersServicer` instead of building a
    fresh one). A plain assignment forces every test to start with a clean
    slate regardless of what earlier tests' production code did.
    """
    store = PeerMatchSampleStore(mongomock.MongoClient(), db_name="test_default_match_samples")
    monkeypatch.setattr(_peers_service, "_build_default_match_sample_store", lambda *a, **k: store)
    _peer_baseline._default_scheduler = None


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
    fixture) mirrors the old per-test `tmp_path` on-disk file isolation --
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
    (function-scoped fixture) mirrors the old per-test `tmp_path` on-disk file
    isolation -- tests that need a specific client (e.g. to seed data the
    real route must also see, or to prove two different stores are isolated)
    construct their own `CareerStore`/`mongomock.MongoClient()` directly and
    either reuse this fixture's client (via `open_career_store()`) or build a
    separate one, same as `_derived_store_uses_mongomock` above.
    """
    client = mongomock.MongoClient()
    monkeypatch.setattr(_career_store, "_build_mongo_client", lambda uri: client)


@pytest.fixture(autouse=True)
def _jobs_store_uses_mongomock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default every test's job/player store (Phase 8, Task 4) to an
    in-memory mongomock store instead of a real Mongo connection.

    `jobs.open_jobs_store` lazily builds a real `pymongo.MongoClient` against
    `RUNNER_MONGO_URI`/`MONGO_URI` (falling back to `localhost:27017`) on
    first use via `_build_mongo_client`, the same seam `derived.py`/
    `career_store.py` use. Without this fixture, `create_app` (via
    `api_ui/app.py`'s `open_jobs_store()` call) would try a real network
    connection and hang. One fresh client per test (function-scoped fixture)
    mirrors the old per-test `tmp_path` on-disk file isolation -- tests
    needing two "processes" to observe the same store (e.g.
    `test_cron_watch_service.py`'s cross-boundary test) call
    `jobs.open_jobs_store()` from both sides so they share this fixture's
    client, same as `_career_store_uses_mongomock` above.
    """
    client = mongomock.MongoClient()
    monkeypatch.setattr(_jobs, "_build_mongo_client", lambda uri: client)


@pytest.fixture(autouse=True)
def _report_store_uses_mongomock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default every test's Report store to an in-memory mongomock store
    instead of a real Mongo connection.

    `report_store.open_report_store` lazily builds a real
    `pymongo.MongoClient` against `RUNNER_MONGO_URI`/`MONGO_URI` (falling back
    to `localhost:27017`) on first use via `_build_mongo_client`, the same
    seam `derived.py`/`career_store.py`/`jobs.py` use. Without this fixture,
    any test that exercises `run_analysis`, the web app's report-serving
    routes, or `should_skip_unchanged_build`/`report_needs_peer_comparison`
    would try a real network connection and hang. One fresh client per test
    (function-scoped fixture) mirrors the old per-test `tmp_path` on-disk file
    isolation -- tests that need to seed/inspect report data directly call
    `open_report_store()` (or construct their own
    `ReportStore(mongomock.MongoClient(), db_name=...)`) and either reuse this
    fixture's client or build a separate one, same as the stores above.
    """
    client = mongomock.MongoClient()
    monkeypatch.setattr(_report_store, "_build_mongo_client", lambda uri: client)
