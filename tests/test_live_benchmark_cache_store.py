"""Tests for the PEERS live-benchmark cache's Mongo document shape."""

from __future__ import annotations

import mongomock
from bson import ObjectId

from league_stats_peers.infra.live_benchmark_cache_store import LiveBenchmarkCacheStore


def test_write_uses_a_real_objectid_not_the_cache_key() -> None:
    store = LiveBenchmarkCacheStore(mongomock.MongoClient())
    store.write("GOLD|Viktor|MIDDLE", {"win": 0.5})
    doc = store._cache.find_one({"cache_key": "GOLD|Viktor|MIDDLE"})
    assert isinstance(doc["_id"], ObjectId)


def test_read_round_trips_without_leaking_internal_fields() -> None:
    store = LiveBenchmarkCacheStore(mongomock.MongoClient())
    store.write("GOLD|Viktor|MIDDLE", {"win": 0.5, "kda": 2.4})
    result = store.read("GOLD|Viktor|MIDDLE")
    assert result == {"win": 0.5, "kda": 2.4}


def test_read_missing_key_returns_none() -> None:
    store = LiveBenchmarkCacheStore(mongomock.MongoClient())
    assert store.read("nothing-here") is None


def test_write_is_idempotent_upsert() -> None:
    store = LiveBenchmarkCacheStore(mongomock.MongoClient())
    store.write("k", {"win": 0.4})
    store.write("k", {"win": 0.6})
    assert store.read("k") == {"win": 0.6}
    assert store._cache.count_documents({"cache_key": "k"}) == 1
