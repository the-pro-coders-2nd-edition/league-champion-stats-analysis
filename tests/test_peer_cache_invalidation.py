"""Peer sample cache: reuse rules across patch, tier and age."""

from __future__ import annotations

import time as _time

import mongomock
import pytest

import league_stats.analysis.peer.benchmark_cache as benchmark_cache
from league_stats.analysis.peer.benchmark_cache import (
    CACHE_TTL_S,
    read_live_cache,
    write_live_cache,
)
from league_stats.analysis.peer.benchmark_fetcher import BenchmarkSnapshot
from league_stats.analysis.peer.comparison import current_patch
from league_stats.infra.peer_sample_store import PeerSampleStore


@pytest.fixture()
def cache_dir(monkeypatch: pytest.MonkeyPatch) -> PeerSampleStore:
    """Point the Mongo-backed live cache at a fresh mongomock store for this test.

    Named ``cache_dir`` (rather than e.g. ``cache_store``) to keep the diff
    against the old file-cache tests minimal -- it no longer names a
    directory, but every test below only uses it to trigger the fixture.
    """
    store = PeerSampleStore(mongomock.MongoClient(), db_name="test_live_cache")
    monkeypatch.setattr(benchmark_cache, "_store", store)
    return store


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


def test_same_patch_and_tier_is_a_hit(cache_dir: PeerSampleStore) -> None:
    write_live_cache("euw1", "GOLD", "Zac", "JUNGLE", _snapshot(), patch="14.23")
    cached = read_live_cache("euw1", "GOLD", "Zac", "JUNGLE", patch="14.23")

    assert cached is not None
    assert cached.from_cache is True
    assert cached.games_sampled == 50


def test_patch_change_forces_a_resample(cache_dir: PeerSampleStore) -> None:
    write_live_cache("euw1", "GOLD", "Zac", "JUNGLE", _snapshot(), patch="14.23")

    assert read_live_cache("euw1", "GOLD", "Zac", "JUNGLE", patch="14.24") is None


def test_tier_change_forces_a_resample(cache_dir: PeerSampleStore) -> None:
    write_live_cache("euw1", "GOLD", "Zac", "JUNGLE", _snapshot(), patch="14.23")

    assert read_live_cache("euw1", "PLATINUM", "Zac", "JUNGLE", patch="14.23") is None


def test_division_change_within_a_tier_is_still_a_hit(cache_dir: PeerSampleStore) -> None:
    # Peers are tier-scoped: build_exact_scope accepts every division and
    # rank_matches never reads LP, so Gold IV -> Gold I must not re-sample.
    write_live_cache("euw1", "GOLD", "Zac", "JUNGLE", _snapshot(), patch="14.23")

    assert read_live_cache("euw1", "GOLD", "Zac", "JUNGLE", patch="14.23") is not None


def test_entry_older_than_the_ttl_is_ignored(
    cache_dir: PeerSampleStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_live_cache("euw1", "GOLD", "Zac", "JUNGLE", _snapshot(), patch="14.23")
    later = _time.time() + CACHE_TTL_S + 60
    monkeypatch.setattr(
        "league_stats.analysis.peer.benchmark_cache.time.time", lambda: later
    )

    assert read_live_cache("euw1", "GOLD", "Zac", "JUNGLE", patch="14.23") is None


def test_entry_just_inside_the_ttl_is_kept(
    cache_dir: PeerSampleStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_live_cache("euw1", "GOLD", "Zac", "JUNGLE", _snapshot(), patch="14.23")
    later = _time.time() + CACHE_TTL_S - 60
    monkeypatch.setattr(
        "league_stats.analysis.peer.benchmark_cache.time.time", lambda: later
    )

    assert read_live_cache("euw1", "GOLD", "Zac", "JUNGLE", patch="14.23") is not None


def test_a_pre_patch_tracking_entry_is_discarded(cache_dir: PeerSampleStore) -> None:
    # Entries written before patch tracking have no patch recorded; once we know
    # which patch we want, they must not be served.
    write_live_cache("euw1", "GOLD", "Zac", "JUNGLE", _snapshot())

    assert read_live_cache("euw1", "GOLD", "Zac", "JUNGLE", patch="14.23") is None


def test_unknown_wanted_patch_falls_back_to_the_ttl(cache_dir: PeerSampleStore) -> None:
    # No records to read a patch from: rely on the TTL rather than throwing away
    # a sample we have no evidence against.
    write_live_cache("euw1", "GOLD", "Zac", "JUNGLE", _snapshot(), patch="14.23")

    assert read_live_cache("euw1", "GOLD", "Zac", "JUNGLE") is not None


def test_current_patch_reads_the_newest_game() -> None:
    from tests.fixtures import FAKE_ITEMS, MY_PUUID, make_match, make_timeline
    from league_stats.ingest.parser import ItemCatalog, MatchParser

    base = MatchParser(ItemCatalog(FAKE_ITEMS)).parse(make_match(), make_timeline(), MY_PUUID)
    old = base.model_copy(update={"patch": "14.22", "game_creation_ms": 1_000})
    new = base.model_copy(update={"patch": "14.23", "game_creation_ms": 2_000})

    assert current_patch([old, new]) == "14.23"
    assert current_patch([new, old]) == "14.23"
    assert current_patch([]) == ""
