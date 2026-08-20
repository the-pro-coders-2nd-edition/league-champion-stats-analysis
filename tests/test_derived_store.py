"""Derived artifact store: keying, code-version isolation, eviction."""

from __future__ import annotations

from pathlib import Path

import pytest

from league_stats_common.core.config import AppConfig
from league_stats_runner.infra.derived import (
    KIND_GAME_REVIEW,
    KIND_RECORD,
    KIND_SLICE,
    DerivedStore,
    code_version,
    slice_fingerprint,
)


@pytest.fixture()
def store(tmp_path: Path):
    with DerivedStore(tmp_path / "derived.sqlite") as handle:
        yield handle


def test_config_exposes_the_derived_path(tmp_path: Path) -> None:
    config = AppConfig(
        riot_id="A", tagline="EUW", api_key="RGAPI-test", cache_dir=tmp_path / "c"
    )
    assert config.derived_db_path == tmp_path / "c" / "derived.sqlite"


def test_round_trip(store: DerivedStore) -> None:
    store.put(KIND_RECORD, "EUW1_1", {"match_id": "EUW1_1", "cs": 188})
    assert store.get(KIND_RECORD, "EUW1_1") == {"match_id": "EUW1_1", "cs": 188}


def test_miss_returns_none(store: DerivedStore) -> None:
    assert store.get(KIND_RECORD, "nope") is None


def test_kinds_do_not_collide(store: DerivedStore) -> None:
    store.put(KIND_RECORD, "same", {"a": 1})
    store.put(KIND_GAME_REVIEW, "same", {"a": 2})

    assert store.get(KIND_RECORD, "same") == {"a": 1}
    assert store.get(KIND_GAME_REVIEW, "same") == {"a": 2}


def test_get_many_returns_hits_and_skips_misses(store: DerivedStore) -> None:
    store.put_many(KIND_RECORD, {"a": {"n": 1}, "b": {"n": 2}})
    found = store.get_many(KIND_RECORD, ["a", "b", "c"])

    assert found == {"a": {"n": 1}, "b": {"n": 2}}


def test_get_many_handles_more_keys_than_one_query_chunk(store: DerivedStore) -> None:
    items = {f"k{i}": {"n": i} for i in range(1200)}
    store.put_many(KIND_RECORD, items)
    found = store.get_many(KIND_RECORD, list(items))

    assert len(found) == 1200
    assert found["k1199"] == {"n": 1199}


def test_get_many_of_nothing(store: DerivedStore) -> None:
    assert store.get_many(KIND_RECORD, []) == {}


def test_put_overwrites(store: DerivedStore) -> None:
    store.put(KIND_RECORD, "a", {"v": 1})
    store.put(KIND_RECORD, "a", {"v": 2})

    assert store.get(KIND_RECORD, "a") == {"v": 2}


def test_a_different_code_version_is_a_miss(
    store: DerivedStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    store.put(KIND_RECORD, "a", {"v": 1})
    assert store.get(KIND_RECORD, "a") == {"v": 1}

    monkeypatch.setattr(
        "league_stats_runner.infra.derived.code_version", lambda kind: "deadbeefdeadbeef"
    )
    assert store.get(KIND_RECORD, "a") is None


def test_code_version_is_stable_and_kind_specific() -> None:
    assert code_version(KIND_RECORD) == code_version(KIND_RECORD)
    assert code_version(KIND_RECORD) != code_version(KIND_SLICE)
    assert len(code_version(KIND_RECORD)) == 16


def test_purge_stale_versions(store: DerivedStore, monkeypatch: pytest.MonkeyPatch) -> None:
    store.put(KIND_RECORD, "a", {"v": 1})
    monkeypatch.setattr(
        "league_stats_runner.infra.derived.code_version", lambda kind: "deadbeefdeadbeef"
    )
    assert store.purge_stale_versions() >= 1
    monkeypatch.undo()
    assert store.get(KIND_RECORD, "a") is None


def test_eviction_drops_least_recently_hit_first(tmp_path: Path) -> None:
    with DerivedStore(tmp_path / "d.sqlite", max_bytes=200) as store:
        store.put(KIND_RECORD, "old", {"pad": "x" * 100})
        store.put(KIND_RECORD, "new", {"pad": "y" * 100})
        store.get(KIND_RECORD, "new")  # newer hit_at

        assert store.total_bytes() > 200
        assert store.evict_to_budget() >= 1
        assert store.total_bytes() <= 200
        assert store.get(KIND_RECORD, "new") is not None
        assert store.get(KIND_RECORD, "old") is None


def test_eviction_is_a_noop_under_budget(store: DerivedStore) -> None:
    store.put(KIND_RECORD, "a", {"v": 1})
    assert store.evict_to_budget() == 0


def test_corrupt_payload_is_dropped_not_raised(store: DerivedStore) -> None:
    store.put(KIND_RECORD, "a", {"v": 1})
    store._conn.execute("UPDATE derived SET payload = 'not json' WHERE key = 'a'")
    store._conn.commit()

    assert store.get(KIND_RECORD, "a") is None
    assert store.get(KIND_RECORD, "a") is None


def test_slice_fingerprint_ignores_order_but_not_membership() -> None:
    assert slice_fingerprint(["a", "b", "c"]) == slice_fingerprint(["c", "a", "b"])
    assert slice_fingerprint(["a", "b"]) != slice_fingerprint(["a", "b", "c"])
    assert slice_fingerprint(["a"], salt="solo") != slice_fingerprint(["a"], salt="flex")
    assert len(slice_fingerprint(["a"])) == 32
