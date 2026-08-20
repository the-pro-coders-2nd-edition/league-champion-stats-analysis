"""Shared pytest fixtures."""

from __future__ import annotations

import mongomock
import pytest

from league_stats.analysis.peer import benchmark_cache as _benchmark_cache
from league_stats.infra.ddragon_assets import DDragonAssets
from league_stats.infra.peer_sample_store import PeerSampleStore


@pytest.fixture(autouse=True)
def _skip_ddragon_downloads(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Avoid bulk Data Dragon downloads during tests."""
    if request.module.__name__.endswith("test_ddragon_assets"):
        return
    monkeypatch.setattr(DDragonAssets, "ensure_downloaded", lambda self, force=False: "")


@pytest.fixture(autouse=True)
def _peer_live_cache_uses_mongomock(monkeypatch: pytest.MonkeyPatch) -> None:
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
    """
    monkeypatch.setattr(
        _benchmark_cache,
        "_store",
        PeerSampleStore(mongomock.MongoClient(), db_name="test_default_live_cache"),
    )
