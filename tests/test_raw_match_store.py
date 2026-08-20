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


def test_save_and_load_match_round_trips(store):
    match = {"metadata": {"matchId": "EUW1_1"}, "info": {"gameId": 1}}
    store.save_match("EUW1_1", "puuid-a", match)
    assert store.has_match("EUW1_1") is True
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


def test_claim_ownership_first_writer_wins(store):
    store.save_match("EUW1_1", "puuid-a", {"info": {}})
    store.save_match("EUW1_2", "puuid-a", {"info": {}})
    claimed = store.claim_ownership("puuid-a", ["EUW1_1", "EUW1_2"])
    assert set(claimed) == {"EUW1_1", "EUW1_2"}

    reclaimed = store.claim_ownership("puuid-b", ["EUW1_1"])
    assert reclaimed == []


def test_iter_all_match_ids_returns_every_saved_match(store):
    store.save_match("EUW1_1", "puuid-a", {"info": {}})
    store.save_match("EUW1_2", "puuid-a", {"info": {}})
    assert set(store.iter_all_match_ids()) == {"EUW1_1", "EUW1_2"}


def test_count_reflects_saved_matches(store):
    assert store.count() == 0
    store.save_match("EUW1_1", "puuid-a", {"info": {}})
    assert store.count() == 1
