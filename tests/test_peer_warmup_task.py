"""Tests for WarmupTask, the champion/role-blind pre-warm sampler."""

from __future__ import annotations

from unittest.mock import MagicMock

from league_stats_peers.analysis.peer.warmup_task import WarmupTask
from tests.fixtures import CombinedMatchAndPeerStore, make_match


def _client() -> MagicMock:
    client = MagicMock()
    client.configure_mock(platform="euw1")
    return client


def test_warmup_task_key_uses_prewarm_sentinel() -> None:
    task = WarmupTask(
        key=("euw1", "GOLD", "__prewarm__", "__prewarm__", "16.16"),
        client=_client(), store=CombinedMatchAndPeerStore(), tier="GOLD",
        patch="16.16", target_games=100,
    )
    assert task.key == ("euw1", "GOLD", "__prewarm__", "__prewarm__", "16.16")


def test_warmup_task_done_once_store_reports_enough_games(monkeypatch) -> None:
    store = CombinedMatchAndPeerStore()
    monkeypatch.setattr(store, "count_by_tier", lambda: {"GOLD": 100})
    task = WarmupTask(
        key=("euw1", "GOLD", "__prewarm__", "__prewarm__", "16.16"),
        client=_client(), store=store, tier="GOLD", patch="16.16", target_games=100,
    )
    assert task.exhausted is True
    assert task.done is True


def test_warmup_task_run_batch_downloads_without_champion_role_filtering(monkeypatch) -> None:
    """A WarmupTask must never call `_match_has_build`/`extract_champion_role_rows`
    -- every downloaded match counts, regardless of champion/role."""
    store = CombinedMatchAndPeerStore()
    monkeypatch.setattr(store, "count_by_tier", lambda: {"GOLD": 0})
    client = _client()
    client.fetch_league_entries_pages.return_value = [
        {"puuid": "seed-1", "tier": "GOLD", "rank": "II"}
    ]
    client.fetch_match_ids.return_value = ["EUW1_1"]
    client.fetch_match.return_value = make_match()
    task = WarmupTask(
        key=("euw1", "GOLD", "__prewarm__", "__prewarm__", "16.16"),
        client=client, store=store, tier="GOLD", patch="16.16", target_games=100,
    )
    task.run_batch()
    assert task.downloads >= 1


def test_warmup_task_priority_is_always_background() -> None:
    task = WarmupTask(
        key=("euw1", "GOLD", "__prewarm__", "__prewarm__", "16.16"),
        client=_client(), store=CombinedMatchAndPeerStore(), tier="GOLD",
        patch="16.16", target_games=100,
    )
    assert task.priority == "background"
