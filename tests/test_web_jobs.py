"""Tests for the web job store: lifecycle, dedup, recovery, queue metrics."""

from __future__ import annotations

from pathlib import Path

import pytest

from league_stats.web import jobs
from league_stats.web.jobs import JobStore


@pytest.fixture()
def store(tmp_path: Path) -> JobStore:
    js = JobStore(tmp_path / "app.sqlite")
    yield js
    js.close()


def _enqueue(store: JobStore, slug: str = "test_euw") -> dict:
    job, created = store.enqueue(
        kind=jobs.JOB_KIND_ANALYZE,
        riot_id="Test",
        tagline="EUW",
        region="euw1",
        player_slug=slug,
    )
    assert created
    return job


def test_enqueue_and_get(store: JobStore) -> None:
    job = _enqueue(store)
    assert job["state"] == jobs.QUEUED
    loaded = store.get(int(job["id"]))
    assert loaded is not None
    assert loaded["riot_id"] == "Test"
    assert store.get(9999) is None


def test_enqueue_dedups_active_jobs(store: JobStore) -> None:
    first = _enqueue(store)
    second, created = store.enqueue(
        kind=jobs.JOB_KIND_ANALYZE,
        riot_id="Test",
        tagline="EUW",
        region="euw1",
        player_slug="test_euw",
    )
    assert not created
    assert second["id"] == first["id"]

    # A terminal job no longer blocks a new one.
    store.set_state(int(first["id"]), jobs.DONE)
    third, created = store.enqueue(
        kind=jobs.JOB_KIND_REFRESH,
        riot_id="Test",
        tagline="EUW",
        region="euw1",
        player_slug="test_euw",
    )
    assert created
    assert third["id"] != first["id"]


def test_claim_next_is_fifo_and_moves_to_fetching(store: JobStore) -> None:
    first = _enqueue(store, "one_euw")
    _enqueue(store, "two_euw")
    claimed = store.claim_next()
    assert claimed is not None
    assert claimed["id"] == first["id"]
    assert claimed["state"] == jobs.FETCHING
    assert claimed["started_at"] is not None

    second_claim = store.claim_next()
    assert second_claim is not None
    assert second_claim["player_slug"] == "two_euw"
    assert store.claim_next() is None


def test_state_transitions_and_progress(store: JobStore) -> None:
    job = _enqueue(store)
    job_id = int(job["id"])
    store.set_state(job_id, jobs.ANALYZING, detail="Analyzing Viktor mid (1/2)")
    store.update_progress(job_id, detail="Downloading (10/50)", current=10, total=50)
    loaded = store.get(job_id)
    assert loaded["state"] == jobs.ANALYZING
    assert loaded["stage_current"] == 10
    assert loaded["stage_total"] == 50

    store.set_state(job_id, jobs.FAILED, error="boom")
    loaded = store.get(job_id)
    assert loaded["state"] == jobs.FAILED
    assert loaded["error"] == "boom"
    assert loaded["finished_at"] is not None


def test_queue_position_counts_running_and_queued_ahead(store: JobStore) -> None:
    running = _enqueue(store, "a_euw")
    store.claim_next()
    assert store.queue_position(int(running["id"])) is None  # no longer queued

    second = _enqueue(store, "b_euw")
    third = _enqueue(store, "c_euw")
    assert store.queue_position(int(second["id"])) == 1  # one running ahead
    assert store.queue_position(int(third["id"])) == 2


def test_recover_orphans_fails_running_keeps_queued(store: JobStore) -> None:
    running = _enqueue(store, "a_euw")
    store.claim_next()
    queued = _enqueue(store, "b_euw")

    recovered = store.recover_orphans()
    assert recovered == 1
    assert store.get(int(running["id"]))["state"] == jobs.FAILED
    assert store.get(int(queued["id"]))["state"] == jobs.QUEUED


def test_player_registry_marks(store: JobStore) -> None:
    store.upsert_player(slug="test_euw", riot_id="Test", tagline="EUW", region="euw1")
    player = store.get_player("test_euw")
    assert player is not None
    assert player["base_completed_at"] is None

    store.mark_player_base_complete("test_euw")
    store.mark_player_peer_failed("test_euw")
    player = store.get_player("test_euw")
    assert player["base_completed_at"] is not None
    assert player["peer_failed"] == 1

    store.mark_player_peer_complete("test_euw")
    player = store.get_player("test_euw")
    assert player["peer_completed_at"] is not None
    assert player["peer_failed"] == 0


def test_encode_players_preserves_profile_icon_id() -> None:
    encoded = jobs.encode_players(
        [
            {"riot_id": "Alice", "tagline": "EUW", "profile_icon_id": 7},
            {"riot_id": "Bob", "tagline": "EUW"},
        ]
    )
    decoded = jobs.decode_players(encoded)
    assert decoded == [
        {"riot_id": "Alice", "tagline": "EUW", "profile_icon_id": 7},
        {"riot_id": "Bob", "tagline": "EUW"},
    ]


def test_player_registry_stores_group(store: JobStore) -> None:
    players = [
        {"riot_id": "Alice", "tagline": "EUW"},
        {"riot_id": "Bob", "tagline": "EUW"},
    ]
    store.upsert_player(
        slug="alice_euw__bob_euw",
        riot_id="Alice",
        tagline="EUW",
        region="euw1",
        players=players,
    )
    loaded = store.get_player("alice_euw__bob_euw")
    assert loaded is not None
    assert loaded["players"] == players

    job, created = store.enqueue(
        kind=jobs.JOB_KIND_ANALYZE,
        riot_id="Alice",
        tagline="EUW",
        region="euw1",
        player_slug="alice_euw__bob_euw",
        players=players,
    )
    assert created
    assert job["players"] == players


def test_average_duration_defaults_without_history(store: JobStore) -> None:
    assert store.average_duration_s() == jobs.DEFAULT_JOB_DURATION_S
