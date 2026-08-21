"""Regression test for a real production bug: `RiotApiClient.download_matches`
used to call `ingest_match(self._store, ...)` -> `store.upsert_peer_game(row)`
unconditionally on every downloaded match. RUNNER (the only real caller of
this method, per `pipeline/fetch.py`) passes a `RawMatchStore`, which has
never implemented `upsert_peer_game` (that method lives only on
`PeerSampleStore`/the old `MatchStore`) -- so every real job that downloaded
even one match crashed with `AttributeError: 'RawMatchStore' object has no
attribute 'upsert_peer_game'`. Caught live in production (not by any prior
test, since every existing `ingest_match` test called it directly against a
peer-game-capable store, never through `download_matches`). Fixed by
removing the call entirely -- see `riot_api.py::download_matches`'s
docstring for the full reasoning.
"""

import mongomock
import pytest

from league_stats_common.core.config import AppConfig
from league_stats_common.infra.cache import HttpCache
from league_stats_common.infra.riot_api import RiotApiClient
from league_stats_runner.infra.raw_match_store import RawMatchStore


@pytest.fixture
def raw_match_store():
    client = mongomock.MongoClient()
    return RawMatchStore(client, db_name="league_stats_test")


@pytest.fixture
def client(tmp_path, raw_match_store, monkeypatch):
    config = AppConfig(
        riot_id="Test", tagline="EUW", api_key="RGAPI-test", region="europe", platform="euw1"
    )
    http_cache = HttpCache(tmp_path / "http_cache.sqlite")
    riot_client = RiotApiClient(config, http_cache, raw_match_store)

    # A real, non-empty participant is required: extract_peer_rows() skips
    # participants missing puuid/championName/role, and if it extracts zero
    # rows, ingest_match's `for row in extract_peer_rows(...)` loop never
    # runs -- meaning `store.upsert_peer_game` never gets called either, and
    # the whole regression this test exists to catch would silently not be
    # exercised at all. queueId must be RANKED_SOLO_QUEUE_ID (420) and
    # gameDuration must exceed the remake threshold for
    # match_duration_minutes() to return non-None in the first place.
    participant = {
        "puuid": "puuid-a",
        "championName": "Ahri",
        "teamPosition": "MIDDLE",
        "teamId": 100,
        "kills": 5,
        "deaths": 2,
        "assists": 7,
        "totalDamageDealtToChampions": 20000,
        "goldEarned": 12000,
        "totalMinionsKilled": 180,
        "neutralMinionsKilled": 10,
        "timeCCingOthers": 15,
        "win": True,
        "challenges": {},
    }
    match_payload = {
        "metadata": {"matchId": "EUW1_1", "participants": ["puuid-a"]},
        "info": {
            "gameId": 1,
            "gameDuration": 1800,
            "gameVersion": "14.1.1",
            "queueId": 420,
            "participants": [participant],
        },
    }
    timeline_payload = {"info": {"frameInterval": 60000, "frames": []}}

    def fake_get(url, params=None, ttl_s=None, use_cache=True):
        if url.endswith("/timeline"):
            return timeline_payload
        return match_payload

    monkeypatch.setattr(riot_client, "_get", fake_get)
    return riot_client


def test_raw_match_store_genuinely_lacks_upsert_peer_game(raw_match_store):
    """Sanity check the test setup: confirm this is the real gap, not a
    stale assumption -- if RawMatchStore ever grows this method, this
    regression test's premise (and the fix's reasoning) needs revisiting."""
    assert not hasattr(raw_match_store, "upsert_peer_game")


def test_download_matches_does_not_crash_against_raw_match_store(client):
    """The actual regression: this used to raise AttributeError on the
    first pending match downloaded, for every real RUNNER job."""
    new_ids = client.download_matches("puuid-a", ["EUW1_1"])
    assert new_ids == {"EUW1_1"}


def test_download_matches_saves_the_match_and_timeline(client, raw_match_store):
    client.download_matches("puuid-a", ["EUW1_1"])
    assert raw_match_store.has_match("EUW1_1") is True
    assert raw_match_store.load_match("EUW1_1") is not None
    assert raw_match_store.load_timeline("EUW1_1") is not None


def test_download_matches_also_does_not_crash_for_already_cached_matches(client, raw_match_store):
    """The `cached` branch (ownership-claim path) also used to call
    `ingest_match` -- exercise it too, not just the `pending`-download loop."""
    raw_match_store.save_match("EUW1_2", "puuid-b", {"metadata": {"matchId": "EUW1_2"}, "info": {}})
    raw_match_store.save_timeline("EUW1_2", {"info": {"frames": []}})

    new_ids = client.download_matches("puuid-a", ["EUW1_2"])
    assert new_ids == {"EUW1_2"}
