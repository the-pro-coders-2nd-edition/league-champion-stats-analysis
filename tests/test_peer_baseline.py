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


class _FakeSchedulerLeavesTaskActive:
    """Fake scheduler simulating a `SamplingTask` still running in the
    background: `wait_for_signal` returns immediately (as if the wait ceiling
    elapsed with nothing in the cache yet), and `is_active` reports the task
    as not yet exhausted."""

    def get_or_create(self, key, factory):
        return None

    def start(self) -> None:
        return None

    def wait_for_signal(self, key, timeout=None) -> None:
        return None

    def is_active(self, key) -> bool:
        return True


class _FakeSchedulerTaskExhausted(_FakeSchedulerLeavesTaskActive):
    """Same as above, but the task has already finalized -- no more updates
    are ever coming."""

    def is_active(self, key) -> bool:
        return False


def test_static_fallback_marks_still_refining_when_live_task_still_active(
    tmp_path, ranked: RankedEntry
) -> None:
    """Regression: a report must not freeze at a crude static-fallback
    comparison forever while its `SamplingTask` keeps improving in the
    background -- confirmed live in production (pabanakujihar_euw's
    Aatrox/TOP build stayed at fallback_level=4/confidence=low/peer_games=0
    even after the underlying task went on to sample 16+ real games).

    `still_refining=True` on the level-4/5 answer is what makes
    `PeersServicer._on_resolved` register a progressive listener
    (service.py `if baseline.still_refining:`) so later interim/finalize
    snapshots keep reaching RUNNER via `NotifyPeerBaselineReady` instead of
    the report being finalized on this one crude answer.
    """
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
        scheduler=_FakeSchedulerLeavesTaskActive(),
    )
    assert baseline is not None
    assert baseline.fallback_level in {4, 5}
    assert baseline.still_refining is True


def test_static_fallback_does_not_claim_refining_once_task_is_exhausted(
    tmp_path, ranked: RankedEntry
) -> None:
    """The flip side: once the SamplingTask has genuinely finished (target
    reached, ceiling spent, or snowball exhausted), there is nothing left to
    listen for -- `still_refining` must stay False so
    `register_progressive_listener` is never called for a dead task."""
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
        scheduler=_FakeSchedulerTaskExhausted(),
    )
    assert baseline is not None
    assert baseline.fallback_level in {4, 5}
    assert baseline.still_refining is False


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


def test_on_task_interim_reports_full_confidence_once_target_reached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_on_task_interim must switch confidence from 'low' to 'full' the
    moment task.reached_target flips True -- still_refining stays True
    (there's more sampling to come toward CEILING)."""
    from types import SimpleNamespace

    from league_stats_peers.analysis.peer import baseline as peer_baseline_module

    class _FakeTaskForInterim:
        def __init__(self, reached_target: bool) -> None:
            self.reached_target = reached_target
            self.client = MagicMock(platform="euw1")
            self.ranked = RankedEntry(tier="GOLD", rank="II", league_points=0, wins=0, losses=0)
            self.champion = "Zac"
            self.role = "JUNGLE"
            self.patch = "16.16"
            self.key = ("euw1", "GOLD", "zac", "JUNGLE", "16.16")

        def build_snapshot(self, *, confidence: str, still_refining: bool):
            return SimpleNamespace(confidence=confidence, still_refining=still_refining)

    task = _FakeTaskForInterim(reached_target=True)
    dispatched = []
    monkeypatch.setattr(
        peer_baseline_module, "_dispatch_progressive_listeners",
        lambda key, snapshot: dispatched.append(snapshot),
    )
    monkeypatch.setattr(peer_baseline_module, "write_live_cache", lambda *a, **k: None)
    peer_baseline_module._on_task_interim(task)
    assert dispatched[0].confidence == "full"
    assert dispatched[0].still_refining is True
