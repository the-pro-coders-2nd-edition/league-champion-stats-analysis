"""Peer sample cache: reuse rules across patch, tier and age."""

from __future__ import annotations

import time as _time

import mongomock
import pymongo
import pytest

import league_stats.analysis.peer.benchmark_cache as benchmark_cache
from league_stats.analysis.peer.benchmark_cache import (
    CACHE_TTL_S,
    read_live_cache,
    write_live_cache,
)
from league_stats.analysis.peer.benchmark_fetcher import BenchmarkSnapshot
from league_stats.analysis.peer.comparison import current_patch
from league_stats.infra.live_benchmark_cache_store import LiveBenchmarkCacheStore


@pytest.fixture()
def cache_store(monkeypatch: pytest.MonkeyPatch) -> LiveBenchmarkCacheStore:
    """Point the Mongo-backed live cache at a fresh mongomock store for this test."""
    store = LiveBenchmarkCacheStore(mongomock.MongoClient(), db_name="test_live_cache")
    monkeypatch.setattr(benchmark_cache, "_store", store)
    return store


class _RaisingStore:
    """A `LiveBenchmarkCacheStore` stand-in whose `read` always raises, simulating an
    unreachable/broken Mongo (e.g. `peers_mode=in_process` with no local Mongo up)."""

    def read(self, key: str):
        raise pymongo.errors.ServerSelectionTimeoutError("no server available")

    def write(self, key: str, data: dict) -> None:
        raise pymongo.errors.ServerSelectionTimeoutError("no server available")


def _snapshot(games: int = 50) -> BenchmarkSnapshot:
    return BenchmarkSnapshot(
        metrics={"win": 0.52, "cspm": 7.4},
        games_sampled=games,
        players_sampled=20,
        from_cache=False,
        platform="euw1",
    )


def test_ttl_is_three_days() -> None:
    assert CACHE_TTL_S == 3 * 24 * 3600


def test_same_patch_and_tier_is_a_hit(cache_store: LiveBenchmarkCacheStore) -> None:
    write_live_cache("euw1", "GOLD", "Zac", "JUNGLE", _snapshot(), patch="14.23")
    cached = read_live_cache("euw1", "GOLD", "Zac", "JUNGLE", patch="14.23")

    assert cached is not None
    assert cached.from_cache is True
    assert cached.games_sampled == 50


def test_patch_change_forces_a_resample(cache_store: LiveBenchmarkCacheStore) -> None:
    write_live_cache("euw1", "GOLD", "Zac", "JUNGLE", _snapshot(), patch="14.23")

    assert read_live_cache("euw1", "GOLD", "Zac", "JUNGLE", patch="14.24") is None


def test_tier_change_forces_a_resample(cache_store: LiveBenchmarkCacheStore) -> None:
    write_live_cache("euw1", "GOLD", "Zac", "JUNGLE", _snapshot(), patch="14.23")

    assert read_live_cache("euw1", "PLATINUM", "Zac", "JUNGLE", patch="14.23") is None


def test_division_change_within_a_tier_is_still_a_hit(cache_store: LiveBenchmarkCacheStore) -> None:
    # Peers are tier-scoped: build_exact_scope accepts every division and
    # rank_matches never reads LP, so Gold IV -> Gold I must not re-sample.
    write_live_cache("euw1", "GOLD", "Zac", "JUNGLE", _snapshot(), patch="14.23")

    assert read_live_cache("euw1", "GOLD", "Zac", "JUNGLE", patch="14.23") is not None


def test_entry_older_than_the_ttl_is_ignored(
    cache_store: LiveBenchmarkCacheStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_live_cache("euw1", "GOLD", "Zac", "JUNGLE", _snapshot(), patch="14.23")
    later = _time.time() + CACHE_TTL_S + 60
    monkeypatch.setattr(
        "league_stats.analysis.peer.benchmark_cache.time.time", lambda: later
    )

    assert read_live_cache("euw1", "GOLD", "Zac", "JUNGLE", patch="14.23") is None


def test_entry_just_inside_the_ttl_is_kept(
    cache_store: LiveBenchmarkCacheStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_live_cache("euw1", "GOLD", "Zac", "JUNGLE", _snapshot(), patch="14.23")
    later = _time.time() + CACHE_TTL_S - 60
    monkeypatch.setattr(
        "league_stats.analysis.peer.benchmark_cache.time.time", lambda: later
    )

    assert read_live_cache("euw1", "GOLD", "Zac", "JUNGLE", patch="14.23") is not None


def test_a_pre_patch_tracking_entry_is_discarded(cache_store: LiveBenchmarkCacheStore) -> None:
    # Entries written before patch tracking have no patch recorded; once we know
    # which patch we want, they must not be served.
    write_live_cache("euw1", "GOLD", "Zac", "JUNGLE", _snapshot())

    assert read_live_cache("euw1", "GOLD", "Zac", "JUNGLE", patch="14.23") is None


def test_unknown_wanted_patch_falls_back_to_the_ttl(cache_store: LiveBenchmarkCacheStore) -> None:
    # No records to read a patch from: rely on the TTL rather than throwing away
    # a sample we have no evidence against.
    write_live_cache("euw1", "GOLD", "Zac", "JUNGLE", _snapshot(), patch="14.23")

    assert read_live_cache("euw1", "GOLD", "Zac", "JUNGLE") is not None


def test_read_live_cache_degrades_to_a_miss_on_a_broken_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A raising store (unreachable/broken Mongo) must return None, not propagate.

    Mirrors the old file cache's `except (OSError, json.JSONDecodeError): return
    None` -- without this, `peers_mode=in_process` with no local Mongo running
    would raise out of `read_live_cache` and skip the static-benchmark fallback
    levels a genuine cache miss would still reach.
    """
    monkeypatch.setattr(benchmark_cache, "_store", _RaisingStore())

    assert read_live_cache("euw1", "GOLD", "Zac", "JUNGLE", patch="14.23") is None


def test_write_live_cache_swallows_errors_from_a_broken_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A raising store must not propagate out of write_live_cache either."""
    monkeypatch.setattr(benchmark_cache, "_store", _RaisingStore())

    write_live_cache("euw1", "GOLD", "Zac", "JUNGLE", _snapshot(), patch="14.23")


class TestFileFallbackWithNoMongo:
    """Phase 5 final review, Finding 1: prove the file cache actually works when
    Mongo is unreachable, on the real production topology (`deploy/run.sh`'s bare
    systemd unit, and local `uv run python main.py`) -- both default to
    `peers_mode="in_process"` with no Mongo instance available at all.

    This bypasses the autouse `_peer_live_cache_uses_mongomock` fixture's
    mongomock store by overriding `benchmark_cache._store` with `_RaisingStore`,
    which raises on every `read`/`write` exactly like a real unreachable Mongo
    caught by pymongo's `serverSelectionTimeoutMS` guard would. The autouse
    fixture's `_LIVE_CACHE_DIR` -> tmp_path redirect stays in effect, so the
    file cache used here is real file I/O against a throwaway directory, not a
    mock -- this genuinely exercises the on-disk read/write path, not just
    Mongo's fail-soft guard.
    """

    def test_write_then_read_round_trips_via_the_file_cache(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(benchmark_cache, "_store", _RaisingStore())

        write_live_cache("euw1", "GOLD", "Zac", "JUNGLE", _snapshot(games=63), patch="14.23")
        cached = read_live_cache("euw1", "GOLD", "Zac", "JUNGLE", patch="14.23")

        assert cached is not None
        assert cached.from_cache is True
        assert cached.games_sampled == 63
        assert cached.metrics == {"win": 0.52, "cspm": 7.4}

    def test_the_round_trip_actually_touched_the_on_disk_file(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(benchmark_cache, "_store", _RaisingStore())

        write_live_cache("euw1", "GOLD", "Zac", "JUNGLE", _snapshot(), patch="14.23")

        path = benchmark_cache._cache_path("euw1", "GOLD", "Zac", "JUNGLE")
        assert path.is_file()

    def test_a_stale_file_entry_is_still_rejected_with_mongo_unreachable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(benchmark_cache, "_store", _RaisingStore())

        write_live_cache("euw1", "GOLD", "Zac", "JUNGLE", _snapshot(), patch="14.23")

        assert read_live_cache("euw1", "GOLD", "Zac", "JUNGLE", patch="14.24") is None


def test_current_patch_reads_the_newest_game() -> None:
    from tests.fixtures import FAKE_ITEMS, MY_PUUID, make_match, make_timeline
    from league_stats.ingest.parser import ItemCatalog, MatchParser

    base = MatchParser(ItemCatalog(FAKE_ITEMS)).parse(make_match(), make_timeline(), MY_PUUID)
    old = base.model_copy(update={"patch": "14.22", "game_creation_ms": 1_000})
    new = base.model_copy(update={"patch": "14.23", "game_creation_ms": 2_000})

    assert current_patch([old, new]) == "14.23"
    assert current_patch([new, old]) == "14.23"
    assert current_patch([]) == ""
