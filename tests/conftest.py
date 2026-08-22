"""Shared pytest fixtures."""

from __future__ import annotations

import mongomock
import pytest

from league_stats_api_ui import app as _api_ui_app
from league_stats_peers import service as _peers_service
from league_stats_peers.analysis.peer import baseline as _peer_baseline
from league_stats_peers.analysis.peer import benchmark_cache as _benchmark_cache
from league_stats_common.infra import career_store as _career_store
from league_stats_common.infra import jobs as _jobs
from league_stats_common.infra import report_store as _report_store
from league_stats_common.infra.ddragon_assets import DDragonAssets
from league_stats_peers.infra.live_benchmark_cache_store import LiveBenchmarkCacheStore
from league_stats_peers.infra.peer_match_sample_store import PeerMatchSampleStore
from league_stats_runner import worker as _worker
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

    Calls `.stop()` on the OUTGOING scheduler first, if one exists:
    `SamplingScheduler.start()` spawns `daemon=True` worker threads that
    `_worker_loop` forever until `.stop()` sets `self._stopped`, and nothing
    else in this codebase ever calls it. Without this, every test that
    reaches level 2 leaks its scheduler's idle-polling background threads
    for the rest of the whole test *session* (not just the process -- these
    threads never join, they just keep waking up on `_IDLE_POLL_INTERVAL_S`
    to check for work), accumulating across every test file that touches
    live sampling. Confirmed via a real full-suite run: with dozens of these
    ghost threads built up by the time later test files run, GIL contention
    alone was enough to occasionally blow past tight timing assumptions in
    an otherwise-correct, otherwise-isolated test
    (`test_peers_service.py::test_resolve_peer_baseline_via_live_sampling_survives_the_noop_store_methods`)
    -- a real resource leak, not just a "some stale state" leak.
    """
    store = PeerMatchSampleStore(mongomock.MongoClient(), db_name="test_default_match_samples")
    monkeypatch.setattr(_peers_service, "_build_default_match_sample_store", lambda *a, **k: store)
    if _peer_baseline._default_scheduler is not None:
        _peer_baseline._default_scheduler.stop()
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
    """Default every test's report store (report.json/meta.json/manifest.json/
    summary.json/progression.json/progression.md migration) to an in-memory
    mongomock store instead of a real Mongo connection.

    `report_store.open_report_store` lazily builds a real `pymongo.MongoClient`
    against `RUNNER_MONGO_URI`/`MONGO_URI` (falling back to `localhost:27017`)
    on first use via `_build_mongo_client`, the same seam `derived.py`/
    `career_store.py`/`jobs.py` use. Without this fixture, `run_analysis`,
    `app.py`'s report-serving routes, and `chat.py`'s `load_report_summary`
    would try a real network connection and hang. One fresh client per test
    (function-scoped fixture) mirrors the old per-test `tmp_path` on-disk file
    isolation -- tests that need to seed report data a real route must also
    see, or to prove two stores are isolated, construct their own
    `ReportStore`/`mongomock.MongoClient()` directly and either reuse this
    fixture's client (via `open_report_store()`) or build a separate one,
    same as `_career_store_uses_mongomock`/`_jobs_store_uses_mongomock` above.
    """
    client = mongomock.MongoClient()
    monkeypatch.setattr(_report_store, "_build_mongo_client", lambda uri: client)


_REAL_SEAM_TESTS = frozenset(
    {
        "test_build_mongo_client_reuses_the_same_client_for_the_same_uri",
        "test_build_mongo_client_returns_a_different_client_for_a_different_uri",
    }
)


@pytest.fixture(autouse=True)
def _api_ui_raw_match_store_uses_mongomock(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Default every test's api-ui precheck client to an in-memory mongomock
    store instead of a real Mongo connection.

    `app.py`'s `_build_precheck_client` (reached from `_verify_players_exist`
    on `POST /api/analyses` and from `_hydrate_tracked_ranks`'s flex-rank
    refresh) builds a `RawMatchStore` off `app.py`'s own `_build_mongo_client`
    seam, lazily dialing a real `pymongo.MongoClient` against
    `RUNNER_MONGO_URI`/`MONGO_URI` (falling back to `localhost:27017`). This
    was previously harmless in tests because `RawMatchStore.__init__` never
    touched the client -- since the ObjectId migration added `create_index`
    calls there, construction now forces an immediate real connection
    attempt, hanging on pymongo's server-selection timeout with no real
    Mongo instance running. Mirrors `_derived_store_uses_mongomock` above.

    Skipped for the two tests that deliberately exercise this seam's own
    caching behavior against the real (still-lazy, non-connecting)
    `pymongo.MongoClient` -- same reasoning as `_skip_ddragon_downloads`'s
    module-name bypass above.
    """
    if request.node.name in _REAL_SEAM_TESTS:
        return
    client = mongomock.MongoClient()
    monkeypatch.setattr(_api_ui_app, "_build_mongo_client", lambda uri: client)


@pytest.fixture(autouse=True)
def _worker_raw_match_store_uses_mongomock(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Default every test's RUNNER worker Mongo client to an in-memory
    mongomock store instead of a real Mongo connection.

    `worker.py`'s `_build_job_services` builds a `RawMatchStore` off
    `worker.py`'s own `_build_mongo_client` seam (separate from `app.py`'s),
    lazily dialing a real `pymongo.MongoClient`. Same fix as
    `_api_ui_raw_match_store_uses_mongomock` above, for the other call site.
    Tests that need a specific client patch this seam themselves (e.g.
    `test_web_worker.py`'s scenario tests), which simply overrides this
    fixture's monkeypatch. Skipped for the test that deliberately exercises
    this seam's own caching behavior against the real client, same as above.
    """
    if request.node.name in _REAL_SEAM_TESTS:
        return
    client = mongomock.MongoClient()
    monkeypatch.setattr(_worker, "_build_mongo_client", lambda uri: client)
