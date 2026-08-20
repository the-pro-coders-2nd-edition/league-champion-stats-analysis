"""RawMatchStore: MongoDB-backed raw match/timeline persistence, mirroring
the subset of infra.cache.MatchStore's behavior that RUNNER needs. Tested
against mongomock — no real MongoDB connection."""

import mongomock
import pytest

from league_stats.infra.raw_match_store import RawMatchStore


@pytest.fixture
def store():
    client = mongomock.MongoClient()
    return RawMatchStore(client, db_name="league_stats_test")


def test_has_match_false_when_absent(store):
    assert store.has_match("EUW1_1") is False


def test_has_match_false_until_timeline_is_also_saved(store):
    match = {"metadata": {"matchId": "EUW1_1"}, "info": {"gameId": 1}}
    store.save_match("EUW1_1", "puuid-a", match)
    assert store.has_match("EUW1_1") is False

    store.save_timeline("EUW1_1", {"info": {"frames": []}})
    assert store.has_match("EUW1_1") is True


def test_save_and_load_match_round_trips(store):
    match = {"metadata": {"matchId": "EUW1_1"}, "info": {"gameId": 1}}
    store.save_match("EUW1_1", "puuid-a", match)
    assert store.load_match("EUW1_1") == match


def test_load_match_returns_none_when_absent(store):
    assert store.load_match("EUW1_999") is None


def test_save_and_load_timeline_round_trips(store):
    store.save_match("EUW1_1", "puuid-a", {"info": {}})
    timeline = {"info": {"frames": [{"timestamp": 0}]}}
    store.save_timeline("EUW1_1", timeline)
    assert store.load_timeline("EUW1_1") == timeline


def test_load_timeline_returns_none_when_absent(store):
    assert store.load_timeline("EUW1_1") is None


def test_claim_ownership_allows_multiple_independent_owners(store):
    store.save_match("EUW1_1", "puuid-a", {"info": {}})
    store.save_timeline("EUW1_1", {"info": {}})
    store.save_match("EUW1_2", "puuid-a", {"info": {}})
    store.save_timeline("EUW1_2", {"info": {}})

    claimed = store.claim_ownership("puuid-b", ["EUW1_1", "EUW1_2"])
    assert set(claimed) == {"EUW1_1", "EUW1_2"}


def test_claim_ownership_is_idempotent_for_the_same_puuid(store):
    store.save_match("EUW1_1", "puuid-a", {"info": {}})
    store.save_timeline("EUW1_1", {"info": {}})

    first = store.claim_ownership("puuid-b", ["EUW1_1"])
    assert first == ["EUW1_1"]

    second = store.claim_ownership("puuid-b", ["EUW1_1"])
    assert second == []


def test_claim_ownership_skips_matches_without_a_timeline(store):
    store.save_match("EUW1_1", "puuid-a", {"info": {}})
    assert store.claim_ownership("puuid-b", ["EUW1_1"]) == []


def test_iter_all_match_ids_returns_every_saved_match(store):
    store.save_match("EUW1_1", "puuid-a", {"info": {}})
    store.save_match("EUW1_2", "puuid-a", {"info": {}})
    assert set(store.iter_all_match_ids()) == {"EUW1_1", "EUW1_2"}


def test_count_reflects_fully_stored_matches(store):
    assert store.count() == 0

    store.save_match("EUW1_1", "puuid-a", {"info": {}})
    assert store.count() == 0

    store.save_timeline("EUW1_1", {"info": {}})
    assert store.count() == 1


def test_iter_match_ids_returns_matches_owned_by_that_puuid(store):
    store.save_match("EUW1_1", "puuid-a", {"info": {}})
    store.save_match("EUW1_2", "puuid-a", {"info": {}})
    store.save_match("EUW1_3", "puuid-b", {"info": {}})

    assert set(store.iter_match_ids("puuid-a")) == {"EUW1_1", "EUW1_2"}
    assert set(store.iter_match_ids("puuid-b")) == {"EUW1_3"}


def test_iter_match_ids_does_not_require_a_timeline(store):
    """Mirrors `MatchStore.iter_match_ids`: ownership (`match_players`) is
    written unconditionally by `save_match`, independent of whether a
    timeline has been saved yet."""
    store.save_match("EUW1_1", "puuid-a", {"info": {}})
    assert list(store.iter_match_ids("puuid-a")) == ["EUW1_1"]


def test_iter_match_ids_returns_nothing_for_unknown_puuid(store):
    store.save_match("EUW1_1", "puuid-a", {"info": {}})
    assert list(store.iter_match_ids("puuid-nobody")) == []


def test_iter_match_ids_sees_ownership_claimed_via_claim_ownership(store):
    store.save_match("EUW1_1", "puuid-a", {"info": {}})
    store.save_timeline("EUW1_1", {"info": {}})
    store.claim_ownership("puuid-b", ["EUW1_1"])

    assert list(store.iter_match_ids("puuid-b")) == ["EUW1_1"]


def test_close_is_a_noop_and_does_not_raise(store):
    """`RawMatchStore` never owns its `pymongo.MongoClient` (received
    externally in `__init__`), so `close()` must not close the shared
    client -- calling it must simply not raise, and the store must remain
    usable afterwards."""
    store.close()
    store.save_match("EUW1_1", "puuid-a", {"info": {}})
    assert store.load_match("EUW1_1") == {"info": {}}
