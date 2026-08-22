"""Tests for peer baseline resolution."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from league_stats_peers.analysis.peer import baseline as peer_baseline_module
from league_stats_peers.analysis.peer.baseline import resolve_peer_baseline
from league_stats_peers.analysis.peer.ingest import ingest_match
from league_stats_common.core.models import RankedEntry
from tests.fixtures import CombinedMatchAndPeerStore, make_match


@pytest.fixture
def ranked() -> RankedEntry:
    return RankedEntry(tier="EMERALD", rank="II", league_points=45, wins=10, losses=10)


def test_resolve_peer_baseline_uses_static_fallback(tmp_path, ranked: RankedEntry) -> None:
    """When store and live sampling are empty, static benchmarks are used."""
    store = CombinedMatchAndPeerStore()
    client = MagicMock()
    client.configure_mock(platform="euw1")

    baseline = resolve_peer_baseline(
        client,
        store,
        ranked,
        "Ornn",
        "TOP",
        exclude_puuid="puuid-me",
    )
    assert baseline is not None
    # Static champion JSON is level 4, static role JSON is level 5
    assert baseline.fallback_level in {4, 5}
    assert baseline.confidence == "low"
    assert baseline.metrics["dpm"] > 0


def test_resolve_peer_baseline_records_resolutions_by_level_counter(
    tmp_path, ranked: RankedEntry
) -> None:
    """`PEERS_BASELINE_RESOLUTIONS_BY_LEVEL_TOTAL` must be incremented, labeled
    by whichever fallback_level actually answered the request -- this is the
    resolution-mix visibility metric the RFC calls for, distinct from the
    duration histogram's `source` label."""
    store = CombinedMatchAndPeerStore()
    client = MagicMock()
    client.configure_mock(platform="euw1")

    before = (
        peer_baseline_module.PEERS_BASELINE_RESOLUTIONS_BY_LEVEL_TOTAL.labels(
            fallback_level="4"
        )._value.get()
    )

    baseline = resolve_peer_baseline(
        client, store, ranked, "Ornn", "TOP", exclude_puuid="puuid-me"
    )
    assert baseline is not None

    after = (
        peer_baseline_module.PEERS_BASELINE_RESOLUTIONS_BY_LEVEL_TOTAL.labels(
            fallback_level=str(baseline.fallback_level)
        )._value.get()
    )
    if baseline.fallback_level == 4:
        assert after == before + 1
    else:
        assert after >= 1


def test_resolve_peer_baseline_uses_role_only_when_champion_missing(
    tmp_path, ranked: RankedEntry, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Role-only static benchmarks are the last fallback."""
    import league_stats_peers.analysis.peer.baseline as peer_baseline

    monkeypatch.setattr(peer_baseline, "try_static_benchmark", lambda *args, **kwargs: None)
    store = CombinedMatchAndPeerStore()
    client = MagicMock()
    client.configure_mock(platform="euw1")

    baseline = resolve_peer_baseline(
        client,
        store,
        ranked,
        "Ornn",
        "TOP",
        exclude_puuid="puuid-me",
    )
    assert baseline is not None
    assert baseline.fallback_level == 5


def test_resolve_peer_baseline_uses_store_when_enough_games(
    tmp_path, ranked: RankedEntry, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exact-rank store samples are preferred when the target count is met."""
    import league_stats_peers.analysis.peer.baseline as peer_baseline

    monkeypatch.setattr(peer_baseline, "MIN_EXACT_GAMES", 2)
    store = CombinedMatchAndPeerStore()
    for index in range(2):
        match = make_match()
        match["info"]["participants"][1]["puuid"] = f"peer-{index}"
        ingest_match(store, f"EUW1_{index}", match, "euw1")
        store.set_puuid_rank(f"peer-{index}", "EMERALD", "II")

    client = MagicMock()
    client.configure_mock(platform="euw1")
    client.fetch_solo_rank.return_value = ranked

    baseline = resolve_peer_baseline(
        client,
        store,
        ranked,
        "LeeSin",
        "JUNGLE",
        exclude_puuid="puuid-me",
    )
    assert baseline is not None
    assert baseline.fallback_level == 0
    # With 2 games (below MIN_EXACT_GAMES=50) confidence stays medium when threshold is patched low
    assert baseline.confidence == "medium"
    assert baseline.games >= 2


def test_resolve_peer_baseline_high_confidence_at_hundred_games(
    tmp_path, ranked: RankedEntry, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exact-rank store achieves high confidence once HIGH_CONFIDENCE_GAMES is met."""
    import league_stats_peers.analysis.peer.baseline as peer_baseline

    monkeypatch.setattr(peer_baseline, "MIN_EXACT_GAMES", 2)
    monkeypatch.setattr(peer_baseline, "HIGH_CONFIDENCE_GAMES", 2)
    store = CombinedMatchAndPeerStore()
    for index in range(2):
        match = make_match()
        match["info"]["participants"][1]["puuid"] = f"peer-{index}"
        ingest_match(store, f"EUW1_{index}", match, "euw1")
        store.set_puuid_rank(f"peer-{index}", "EMERALD", "II")

    client = MagicMock()
    client.configure_mock(platform="euw1")
    client.fetch_solo_rank.return_value = ranked

    baseline = resolve_peer_baseline(
        client,
        store,
        ranked,
        "LeeSin",
        "JUNGLE",
        exclude_puuid="puuid-me",
    )
    assert baseline is not None
    assert baseline.fallback_level == 0
    assert baseline.confidence == "high"


def test_resolve_peer_baseline_wider_scope_requires_fifty_games(
    tmp_path, ranked: RankedEntry, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fewer than 50 verified games falls through to static benchmarks."""
    import league_stats_peers.analysis.peer.baseline as peer_baseline

    monkeypatch.setattr(peer_baseline, "try_static_benchmark", lambda *args, **kwargs: None)
    monkeypatch.setattr(peer_baseline, "try_role_benchmark", lambda *args, **kwargs: None)

    store = CombinedMatchAndPeerStore()
    # Only 4 games — below the 50-game floor
    for index in range(4):
        match = make_match()
        match["info"]["participants"][1]["puuid"] = f"far-peer-{index}"
        ingest_match(store, f"EUW1_{index}", match, "euw1")
        store.set_puuid_rank(f"far-peer-{index}", "GOLD", "II")

    client = MagicMock()
    client.configure_mock(platform="euw1")
    client.fetch_league_entries_pages.return_value = []

    baseline = resolve_peer_baseline(
        client,
        store,
        ranked,  # EMERALD II
        "LeeSin",
        "JUNGLE",
        exclude_puuid="puuid-me",
    )
    assert baseline is None
