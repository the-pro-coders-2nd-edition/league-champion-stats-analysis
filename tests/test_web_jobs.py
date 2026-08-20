"""Tests for the web job store: lifecycle, dedup, recovery, queue metrics."""

from __future__ import annotations

import sqlite3
import threading
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
    assert loaded.get("filter_champion") is None
    assert loaded.get("filter_role") is None
    assert loaded.get("min_games") is None
    assert store.get(9999) is None


def test_enqueue_stores_build_filter(store: JobStore) -> None:
    job, created = store.enqueue(
        kind=jobs.JOB_KIND_REFRESH,
        riot_id="Test",
        tagline="EUW",
        region="euw1",
        player_slug="test_euw",
        filter_champion="Fiora",
        filter_role="TOP",
    )
    assert created
    assert job["filter_champion"] == "Fiora"
    assert job["filter_role"] == "TOP"
    loaded = store.get(int(job["id"]))
    assert loaded is not None
    assert loaded["filter_champion"] == "Fiora"
    assert loaded["filter_role"] == "TOP"


def test_enqueue_stores_min_games(store: JobStore) -> None:
    job, created = store.enqueue(
        kind=jobs.JOB_KIND_ANALYZE,
        riot_id="Test",
        tagline="EUW",
        region="euw1",
        player_slug="test_euw",
        min_games=10,
    )
    assert created
    assert job["min_games"] == 10
    loaded = store.get(int(job["id"]))
    assert loaded is not None
    assert loaded["min_games"] == 10


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


def test_encode_players_preserves_solo_rank() -> None:
    encoded = jobs.encode_players(
        [
            {
                "riot_id": "Alice",
                "tagline": "EUW",
                "solo_tier": "DIAMOND",
                "solo_rank": "IV",
                "solo_lp": 83,
            },
            {"riot_id": "Bob", "tagline": "EUW", "solo_tier": "master", "solo_lp": 420},
        ]
    )
    decoded = jobs.decode_players(encoded)
    assert decoded == [
        {
            "riot_id": "Alice",
            "tagline": "EUW",
            "solo_tier": "DIAMOND",
            "solo_rank": "IV",
            "solo_lp": 83,
        },
        {"riot_id": "Bob", "tagline": "EUW", "solo_tier": "MASTER", "solo_lp": 420},
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


def test_cancel_queued_job(store: JobStore) -> None:
    job = _enqueue(store)
    cancelled = store.cancel(int(job["id"]))
    assert cancelled is not None
    assert cancelled["state"] == jobs.CANCELLED
    assert cancelled["finished_at"] is not None
    assert store.claim_next() is None
    assert store.active_job_for_player("test_euw") is None


def test_cancel_running_job_blocks_further_state_updates(store: JobStore) -> None:
    job = _enqueue(store)
    claimed = store.claim_next()
    assert claimed is not None
    job_id = int(claimed["id"])

    cancelled = store.cancel(job_id)
    assert cancelled is not None
    assert cancelled["state"] == jobs.CANCELLED
    assert store.is_cancelled(job_id)

    assert store.set_state(job_id, jobs.ANALYZING, detail="should not apply") is False
    assert store.get(job_id)["state"] == jobs.CANCELLED
    store.update_progress(job_id, detail="ignored", current=1, total=2)
    assert store.get(job_id)["stage_detail"] == "Cancelled by user"


def test_cancel_terminal_job_returns_none(store: JobStore) -> None:
    job = _enqueue(store)
    store.set_state(int(job["id"]), jobs.DONE)
    assert store.cancel(int(job["id"])) is None
    assert store.cancel(9999) is None


def test_cancel_allows_new_enqueue(store: JobStore) -> None:
    first = _enqueue(store)
    store.cancel(int(first["id"]))
    second, created = store.enqueue(
        kind=jobs.JOB_KIND_ANALYZE,
        riot_id="Test",
        tagline="EUW",
        region="euw1",
        player_slug="test_euw",
    )
    assert created
    assert second["id"] != first["id"]


def _write_pre_migration_schema(db_path: Path) -> None:
    """A database as it looked before `_migrate`'s columns existed, so
    opening a `JobStore` against it exercises the real ALTER TABLE path.

    Sets WAL mode up front: converting a database to WAL for the first time
    needs a brief exclusive lock, which is a separate, pre-existing hazard
    for two connections racing to open the SAME file for the very first
    time -- unrelated to `_migrate`'s check-then-ALTER race this test
    targets, and out of this fix's scope. Pre-establishing WAL here isolates
    the test to the race this finding is actually about.
    """
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(
        """
        CREATE TABLE jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL,
            player_slug TEXT NOT NULL,
            riot_id TEXT NOT NULL,
            tagline TEXT NOT NULL,
            region TEXT NOT NULL,
            state TEXT NOT NULL DEFAULT 'queued',
            stage_detail TEXT NOT NULL DEFAULT '',
            stage_current INTEGER,
            stage_total INTEGER,
            error TEXT NOT NULL DEFAULT '',
            created_at REAL NOT NULL,
            started_at REAL,
            finished_at REAL,
            updated_at REAL NOT NULL
        );
        CREATE TABLE players (
            slug TEXT PRIMARY KEY,
            riot_id TEXT NOT NULL,
            tagline TEXT NOT NULL,
            region TEXT NOT NULL,
            last_job_id INTEGER,
            base_completed_at REAL,
            peer_completed_at REAL,
            peer_failed INTEGER NOT NULL DEFAULT 0
        );
        """
    )
    conn.commit()
    conn.close()


def test_migrate_is_safe_when_two_processes_open_a_pre_migration_db_concurrently(
    tmp_path: Path,
) -> None:
    """Regression test for the cross-process TOCTOU in `JobStore._migrate`:
    two `JobStore` instances opening the SAME pre-migration database file at
    the same time (modeling `app` and `cron-watch` racing on startup against
    a shared `app.sqlite` volume, per `docker-compose.yml`) must not crash
    with `sqlite3.OperationalError: duplicate column name`, now that
    `_migrate` wraps its check-then-ALTER sequence in `BEGIN IMMEDIATE`.

    Uses real OS threads (not just sequential opens) to exercise genuine
    concurrent access to one sqlite file, which is what the fix's
    write-lock-based serialization actually has to handle.
    """
    db_path = tmp_path / "app.sqlite"
    _write_pre_migration_schema(db_path)

    barrier = threading.Barrier(2)
    errors: list[BaseException] = []
    stores: list[JobStore] = []
    stores_lock = threading.Lock()

    def _open() -> None:
        barrier.wait(timeout=5)
        try:
            store = JobStore(db_path)
        except BaseException as exc:  # noqa: BLE001 - captured for the assertion below
            with stores_lock:
                errors.append(exc)
            return
        with stores_lock:
            stores.append(store)

    threads = [threading.Thread(target=_open) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    try:
        assert errors == [], f"concurrent migration raised: {errors!r}"
        assert len(stores) == 2

        # Both stores must see the fully migrated schema, not just "no crash".
        job, created = stores[0].enqueue(
            kind=jobs.JOB_KIND_ANALYZE,
            riot_id="Test",
            tagline="EUW",
            region="euw1",
            player_slug="p1",
            filter_champion="Fiora",
            filter_role="TOP",
            min_games=5,
        )
        assert created
        assert job["filter_champion"] == "Fiora"
        assert job["min_games"] == 5

        stores[1].upsert_player(slug="p2", riot_id="Test2", tagline="EUW", region="euw1")
        assert stores[1].set_watch("p2", enabled=True, interval_s=120)
        row = stores[1].get_player("p2")
        assert row is not None
        assert row["watch_enabled"] == 1
    finally:
        for store in stores:
            store.close()
