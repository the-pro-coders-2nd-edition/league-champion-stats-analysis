"""PeerSampleStore: MongoDB-backed peer-game cache, mirroring the subset of
infra.cache.MatchStore's peer-game behavior needed by the peer-sampling
service. Tested against mongomock -- no real MongoDB connection."""

import mongomock
import pytest

from league_stats.infra.peer_sample_store import PeerSampleStore


@pytest.fixture
def store():
    client = mongomock.MongoClient()
    return PeerSampleStore(client, db_name="league_stats_test")


def _row(**overrides):
    row = {
        "match_id": "EUW1_1",
        "puuid": "puuid-a",
        "champion": "Ahri",
        "role": "MIDDLE",
        "platform": "euw1",
        "queue_id": 420,
        "metrics": {"kda": 3.5},
        "ingested_at": 1000.0,
    }
    row.update(overrides)
    return row


def test_upsert_peer_game_inserts_new_row_and_returns_true(store):
    assert store.upsert_peer_game(_row()) is True

    rows = store.load_peer_games(champion="Ahri", role="MIDDLE", platform="euw1")
    assert len(rows) == 1
    assert rows[0]["match_id"] == "EUW1_1"
    assert rows[0]["puuid"] == "puuid-a"
    assert rows[0]["metrics"] == {"kda": 3.5}
    assert rows[0]["rank_verified"] is False
    assert rows[0]["tier"] == ""
    assert rows[0]["rank"] == ""
    assert rows[0]["patch"] == ""


def test_upsert_peer_game_dedups_on_match_puuid_champion_role(store):
    assert store.upsert_peer_game(_row()) is True
    # Exact same (match_id, puuid, champion, role) tuple -> ignored, even
    # with different metrics/tier/etc. Mirrors the SQL UNIQUE constraint.
    assert store.upsert_peer_game(_row(metrics={"kda": 9.9}, tier="GOLD")) is False

    rows = store.load_peer_games(champion="Ahri", role="MIDDLE", platform="euw1")
    assert len(rows) == 1
    assert rows[0]["metrics"] == {"kda": 3.5}


def test_upsert_peer_game_allows_same_match_and_puuid_with_different_role(store):
    # Same match_id + puuid but different role is a distinct row (e.g. a
    # differently-keyed extraction bug, or role recomputed differently).
    assert store.upsert_peer_game(_row()) is True
    assert store.upsert_peer_game(_row(role="TOP", champion="Darius")) is True


def test_upsert_peer_game_allows_same_puuid_different_champion_same_match(store):
    assert store.upsert_peer_game(_row(champion="Ahri", role="MIDDLE")) is True
    assert store.upsert_peer_game(_row(champion="Zed", role="MIDDLE")) is True


def test_load_peer_games_filters_by_champion_role_platform(store):
    store.upsert_peer_game(_row(champion="Ahri", role="MIDDLE", platform="euw1"))
    store.upsert_peer_game(_row(champion="Ahri", role="MIDDLE", platform="na1", puuid="puuid-b"))
    store.upsert_peer_game(_row(champion="Zed", role="MIDDLE", platform="euw1", puuid="puuid-c"))
    store.upsert_peer_game(_row(champion="Ahri", role="TOP", platform="euw1", puuid="puuid-d"))

    rows = store.load_peer_games(champion="Ahri", role="MIDDLE", platform="euw1")
    assert len(rows) == 1
    assert rows[0]["puuid"] == "puuid-a"


def test_load_peer_games_returns_empty_list_when_no_match(store):
    assert store.load_peer_games(champion="Ahri", role="MIDDLE", platform="euw1") == []


def test_count_peer_games_matches_load_peer_games_length(store):
    store.upsert_peer_game(_row(puuid="puuid-a"))
    store.upsert_peer_game(_row(puuid="puuid-b"))
    store.upsert_peer_game(_row(champion="Zed", puuid="puuid-c"))

    assert store.count_peer_games(champion="Ahri", role="MIDDLE", platform="euw1") == 2
    assert store.count_peer_games(champion="Zed", role="MIDDLE", platform="euw1") == 1
    assert store.count_peer_games(champion="Yasuo", role="MIDDLE", platform="euw1") == 0


def test_iter_unverified_puuids_returns_only_unverified_distinct_puuids(store):
    store.upsert_peer_game(_row(puuid="puuid-a"))
    store.upsert_peer_game(_row(puuid="puuid-a", role="TOP", champion="Darius"))
    store.upsert_peer_game(_row(puuid="puuid-b"))
    store.set_puuid_rank("puuid-b", "gold", "ii")

    assert store.iter_unverified_puuids() == ["puuid-a"]


def test_iter_unverified_puuids_respects_limit(store):
    for i in range(5):
        store.upsert_peer_game(_row(puuid=f"puuid-{i}", match_id=f"EUW1_{i}"))

    assert len(store.iter_unverified_puuids(limit=3)) == 3


def test_iter_unverified_puuids_for_build_scopes_to_champion_role_platform(store):
    store.upsert_peer_game(_row(puuid="puuid-a", champion="Ahri", role="MIDDLE", platform="euw1"))
    store.upsert_peer_game(_row(puuid="puuid-b", champion="Zed", role="MIDDLE", platform="euw1"))
    store.upsert_peer_game(_row(puuid="puuid-c", champion="Ahri", role="MIDDLE", platform="na1"))

    result = store.iter_unverified_puuids_for_build("Ahri", "MIDDLE", "euw1")
    assert result == ["puuid-a"]


def test_iter_unverified_puuids_for_build_respects_limit(store):
    for i in range(5):
        store.upsert_peer_game(_row(puuid=f"puuid-{i}", match_id=f"EUW1_{i}"))

    result = store.iter_unverified_puuids_for_build("Ahri", "MIDDLE", "euw1", limit=2)
    assert len(result) == 2


def test_set_puuid_rank_updates_every_row_for_that_puuid(store):
    store.upsert_peer_game(_row(puuid="puuid-a", champion="Ahri", role="MIDDLE"))
    store.upsert_peer_game(_row(puuid="puuid-a", champion="Ahri", role="TOP", match_id="EUW1_2"))
    store.upsert_peer_game(_row(puuid="puuid-b", champion="Zed", role="MIDDLE"))

    changed = store.set_puuid_rank("puuid-a", "gold", "ii")
    assert changed == 2

    rows = store.load_peer_games(champion="Ahri", role="MIDDLE", platform="euw1")
    assert rows[0]["tier"] == "GOLD"
    assert rows[0]["rank"] == "II"
    assert rows[0]["rank_verified"] is True

    other_rows = store.load_peer_games(champion="Zed", role="MIDDLE", platform="euw1")
    assert other_rows[0]["tier"] == ""
    assert other_rows[0]["rank_verified"] is False


def test_set_puuid_rank_is_idempotent_and_still_reports_matched_rows(store):
    store.upsert_peer_game(_row(puuid="puuid-a"))

    first = store.set_puuid_rank("puuid-a", "gold", "ii")
    second = store.set_puuid_rank("puuid-a", "gold", "ii")

    assert first == 1
    assert second == 1


def test_set_puuid_rank_returns_zero_when_puuid_unknown(store):
    assert store.set_puuid_rank("nobody", "gold", "ii") == 0
