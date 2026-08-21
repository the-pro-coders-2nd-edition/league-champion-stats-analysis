"""Tests for dynamic benchmark fetching from the Riot API."""

from __future__ import annotations

from unittest.mock import MagicMock

from league_stats_peers.analysis.peer import benchmark_fetcher as benchmark_fetcher_module
from league_stats_peers.analysis.peer.benchmark_fetcher import (
    extract_champion_role_for_puuid,
    fetch_benchmark_from_api,
)
from league_stats_common.core.models import RankedEntry
from tests.fixtures import CombinedMatchAndPeerStore, make_match


def _league_entry(puuid: str) -> dict[str, str]:
    return {"puuid": puuid, "tier": "GOLD", "rank": "II"}


def _match_for(puuid: str, champion: str = "Zac", role: str = "JUNGLE") -> dict:
    match = make_match()
    participant = match["info"]["participants"][1]
    participant["puuid"] = puuid
    participant["championName"] = champion
    participant["teamPosition"] = role
    return match


def test_extract_champion_role_for_puuid_finds_player() -> None:
    """A matching participant row is returned for the requested player."""
    row = extract_champion_role_for_puuid(
        _match_for("peer-1"), "peer-1", "Zac", "JUNGLE"
    )
    assert row is not None
    assert row["puuid"] == "peer-1"
    assert row["dpm"] > 0


def test_extract_champion_role_for_puuid_filters_lane() -> None:
    """Wrong lane returns None."""
    row = extract_champion_role_for_puuid(
        _match_for("peer-1", role="TOP"), "peer-1", "Zac", "JUNGLE"
    )
    assert row is None


def test_fetch_benchmark_from_api_aggregates_league_sample(tmp_path, monkeypatch) -> None:
    """League entries are scanned until enough champion games are found."""
    monkeypatch.setattr("league_stats_peers.analysis.peer.benchmark_fetcher.MIN_BENCHMARK_GAMES", 3)
    monkeypatch.setattr("league_stats_peers.analysis.peer.benchmark_fetcher.TARGET_PEER_GAMES", 3)
    monkeypatch.setattr("league_stats_peers.analysis.peer.benchmark_fetcher.MAX_MATCH_DOWNLOADS", 10)

    store = CombinedMatchAndPeerStore()
    client = MagicMock()
    client.configure_mock(platform="euw1")
    client.fetch_league_entries_pages.return_value = [
        _league_entry(f"peer-{index}") for index in range(5)
    ]

    def match_ids(puuid: str, count: int, queue_id: int | None = None) -> list[str]:
        return [f"EUW1_{puuid}"]

    client.fetch_match_ids.side_effect = match_ids
    client.fetch_match.side_effect = lambda match_id: _match_for(match_id.removeprefix("EUW1_"))
    client.fetch_solo_rank.return_value = RankedEntry(
        tier="GOLD", rank="II", league_points=45, wins=10, losses=10
    )

    ranked = RankedEntry(tier="GOLD", rank="II", league_points=45, wins=10, losses=10)
    snapshot = fetch_benchmark_from_api(client, store, ranked, "Zac", "JUNGLE")
    assert snapshot is not None
    assert snapshot.games_sampled >= 3
    assert snapshot.metrics["kda"] > 0
    assert store.count_peer_games(champion="Zac", role="JUNGLE", platform="euw1") >= 3


def test_fetch_benchmark_from_api_records_riot_call_and_download_metrics(
    tmp_path, monkeypatch
) -> None:
    """PEERS_RIOT_API_CALLS_TOTAL must be incremented for each of the 4 live-
    sampling endpoints (league_entries/match_ids/match/solo_rank), and
    PEERS_LIVE_SAMPLE_MATCH_DOWNLOADS must observe the number of matches
    downloaded by the sample -- these were previously entirely invisible
    (the existing `downloads` counter was only ever used for a progress bar)."""
    monkeypatch.setattr("league_stats_peers.analysis.peer.benchmark_fetcher.MIN_BENCHMARK_GAMES", 1)
    monkeypatch.setattr("league_stats_peers.analysis.peer.benchmark_fetcher.TARGET_PEER_GAMES", 1)

    store = CombinedMatchAndPeerStore()
    client = MagicMock()
    client.configure_mock(platform="euw1")
    client.fetch_league_entries_pages.return_value = [_league_entry("peer-1")]
    client.fetch_match_ids.return_value = ["EUW1_peer-1"]
    client.fetch_match.return_value = _match_for("peer-1")
    client.fetch_solo_rank.return_value = RankedEntry(
        tier="GOLD", rank="II", league_points=45, wins=10, losses=10
    )

    before_league = benchmark_fetcher_module.PEERS_RIOT_API_CALLS_TOTAL.labels(
        endpoint="league_entries"
    )._value.get()
    before_match_ids = benchmark_fetcher_module.PEERS_RIOT_API_CALLS_TOTAL.labels(
        endpoint="match_ids"
    )._value.get()
    before_match = benchmark_fetcher_module.PEERS_RIOT_API_CALLS_TOTAL.labels(
        endpoint="match"
    )._value.get()
    before_solo_rank = benchmark_fetcher_module.PEERS_RIOT_API_CALLS_TOTAL.labels(
        endpoint="solo_rank"
    )._value.get()
    before_downloads_sum = benchmark_fetcher_module.PEERS_LIVE_SAMPLE_MATCH_DOWNLOADS._sum.get()

    ranked = RankedEntry(tier="GOLD", rank="II", league_points=45, wins=10, losses=10)
    snapshot = fetch_benchmark_from_api(client, store, ranked, "Zac", "JUNGLE")
    assert snapshot is not None

    # `_gather_seeds` calls `fetch_league_entries_pages` once per (tier,
    # division) pair in the widened rank scope, so this is ">=" rather than
    # "== +1" -- the point of this assertion is that the counter moved at
    # all, not the exact call count of an unrelated helper.
    assert (
        benchmark_fetcher_module.PEERS_RIOT_API_CALLS_TOTAL.labels(endpoint="league_entries")._value.get()
        >= before_league + 1
    )
    assert (
        benchmark_fetcher_module.PEERS_RIOT_API_CALLS_TOTAL.labels(endpoint="match_ids")._value.get()
        >= before_match_ids + 1
    )
    assert (
        benchmark_fetcher_module.PEERS_RIOT_API_CALLS_TOTAL.labels(endpoint="match")._value.get()
        >= before_match + 1
    )
    # solo_rank is deliberately not asserted here: `_resolve_rank` serves from
    # `seed_ranks` (already populated from the league-v4 response) whenever
    # the puuid is a seed itself, so it's never called on this fixture's
    # single-seed, no-snowball path -- see `test_seed_rank_skips_fetch_solo_rank`
    # for that documented behavior, and the dedicated
    # `test_resolve_rank_records_solo_rank_metric_on_cache_miss` below for
    # the endpoint="solo_rank" call site itself.
    assert (
        benchmark_fetcher_module.PEERS_LIVE_SAMPLE_MATCH_DOWNLOADS._sum.get()
        > before_downloads_sum
    )


def test_resolve_rank_records_solo_rank_metric_on_cache_miss() -> None:
    """`_resolve_rank` must record `PEERS_RIOT_API_CALLS_TOTAL{endpoint="solo_rank"}`
    only on a genuine cache miss (a puuid not already known from a league-v4
    seed) -- `test_seed_rank_skips_fetch_solo_rank` covers the cache-hit path
    where this call site must NOT fire."""
    client = MagicMock()
    client.fetch_solo_rank.return_value = RankedEntry(
        tier="PLATINUM", rank="I", league_points=10, wins=1, losses=1
    )
    store = CombinedMatchAndPeerStore()
    rank_cache: dict[str, tuple[str, str]] = {}

    before = benchmark_fetcher_module.PEERS_RIOT_API_CALLS_TOTAL.labels(
        endpoint="solo_rank"
    )._value.get()

    result = benchmark_fetcher_module._resolve_rank("peer-unseen", rank_cache, client, store)

    assert result == ("PLATINUM", "I")
    after = benchmark_fetcher_module.PEERS_RIOT_API_CALLS_TOTAL.labels(
        endpoint="solo_rank"
    )._value.get()
    assert after == before + 1


def test_fetch_benchmark_persists_downloaded_matches(tmp_path, monkeypatch) -> None:
    """Downloaded peer matches are stored for later runs."""
    monkeypatch.setattr("league_stats_peers.analysis.peer.benchmark_fetcher.MIN_BENCHMARK_GAMES", 1)
    monkeypatch.setattr("league_stats_peers.analysis.peer.benchmark_fetcher.TARGET_PEER_GAMES", 1)

    store = CombinedMatchAndPeerStore()
    client = MagicMock()
    client.configure_mock(platform="euw1")
    client.fetch_league_entries_pages.return_value = [_league_entry("peer-1")]
    client.fetch_match_ids.return_value = ["EUW1_peer-1"]
    client.fetch_match.return_value = _match_for("peer-1")
    client.fetch_solo_rank.return_value = RankedEntry(
        tier="GOLD", rank="II", league_points=45, wins=10, losses=10
    )

    ranked = RankedEntry(tier="GOLD", rank="II", league_points=45, wins=10, losses=10)
    snapshot = fetch_benchmark_from_api(client, store, ranked, "Zac", "JUNGLE")
    assert snapshot is not None
    assert store.load_match("EUW1_peer-1") is not None
