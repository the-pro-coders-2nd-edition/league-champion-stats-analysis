"""Tests for the web job store: lifecycle, dedup, recovery, queue metrics."""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import mongomock
import pytest

import league_stats_common.infra.jobs as jobs
from league_stats_common.infra.jobs import DEFAULT_WATCH_INTERVAL_S, JobStore


@pytest.fixture()
def store() -> JobStore:
    js = JobStore(mongomock.MongoClient())
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


def test_enqueue_is_atomic_under_concurrent_writers() -> None:
    """Regression test for the exact race Phase 2's `BEGIN IMMEDIATE` fix
    closed (a real, historical production bug: 6 duplicate jobs queued for
    one player). Two threads racing `enqueue` for the SAME `player_slug`
    against the SAME `mongomock.MongoClient()` must yield exactly one
    `created=True` -- the Mongo port's partial-unique-index design (see
    `jobs.py`'s module docstring) must close this race, not just look like
    it does.

    Uses a `threading.Barrier` so both threads call `enqueue` as close to
    simultaneously as possible, and runs several rounds to make a flaky
    (non-atomic) implementation likely to be caught rather than getting
    lucky once.
    """
    client = mongomock.MongoClient()
    js = JobStore(client)
    try:
        for round_index in range(20):
            slug = f"race_{round_index}"
            barrier = threading.Barrier(8)
            results: list[bool] = []
            results_lock = threading.Lock()

            def _enqueue_once() -> None:
                barrier.wait(timeout=5)
                _, created = js.enqueue(
                    kind=jobs.JOB_KIND_ANALYZE,
                    riot_id="Test",
                    tagline="EUW",
                    region="euw1",
                    player_slug=slug,
                )
                with results_lock:
                    results.append(created)

            with ThreadPoolExecutor(max_workers=8) as pool:
                futures = [pool.submit(_enqueue_once) for _ in range(8)]
                for future in futures:
                    future.result(timeout=10)

            assert results.count(True) == 1, (
                f"round {round_index}: expected exactly one created=True, "
                f"got {results.count(True)} of {len(results)}"
            )
            assert js.active_job_for_player(slug) is not None
    finally:
        js.close()


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


def test_claim_next_is_atomic_under_concurrent_claimers() -> None:
    """`find_one_and_update` replaces the SQL retry loop entirely (see
    `jobs.py`'s module docstring) -- prove no job is claimed twice and no
    queued job is lost when several threads race `claim_next()` against
    more queued jobs than claimers.
    """
    client = mongomock.MongoClient()
    js = JobStore(client)
    try:
        job_count = 25
        claimer_count = 8
        for index in range(job_count):
            js.enqueue(
                kind=jobs.JOB_KIND_ANALYZE,
                riot_id="Test",
                tagline="EUW",
                region="euw1",
                player_slug=f"p{index}",
            )

        claimed_ids: list[int] = []
        claimed_lock = threading.Lock()
        barrier = threading.Barrier(claimer_count)

        def _claim_until_empty() -> None:
            barrier.wait(timeout=5)
            while True:
                job = js.claim_next()
                if job is None:
                    return
                with claimed_lock:
                    claimed_ids.append(int(job["id"]))

        with ThreadPoolExecutor(max_workers=claimer_count) as pool:
            futures = [pool.submit(_claim_until_empty) for _ in range(claimer_count)]
            for future in futures:
                future.result(timeout=10)

        from collections import Counter

        dupes = {k: v for k, v in Counter(claimed_ids).items() if v > 1}
        assert not dupes, f"jobs claimed more than once: {dupes} (all claims: {sorted(claimed_ids)})"
        assert len(claimed_ids) == job_count, (
            f"every queued job must be claimed exactly once, got {len(claimed_ids)} "
            f"claims for {job_count} jobs: {sorted(claimed_ids)}"
        )
    finally:
        js.close()


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


def test_list_active_jobs_dedups_by_player_newest_first(store: JobStore) -> None:
    """Each player's newest active job wins; result is ordered newest first."""
    first_a = _enqueue(store, "a_euw")
    _enqueue(store, "b_euw")
    store.set_state(int(first_a["id"]), jobs.DONE)
    second_a = _enqueue(store, "a_euw")
    third_b_slug_job = _enqueue(store, "c_euw")

    active = store.list_active_jobs()
    slugs = [job["player_slug"] for job in active]
    assert slugs.count("a_euw") == 1
    ids_by_slug = {job["player_slug"]: job["id"] for job in active}
    assert ids_by_slug["a_euw"] == second_a["id"]
    # Newest first.
    assert active[0]["id"] == third_b_slug_job["id"]
    assert [job["id"] for job in active] == sorted(
        (job["id"] for job in active), reverse=True
    )


def test_recover_orphans_fails_running_keeps_queued(store: JobStore) -> None:
    running = _enqueue(store, "a_euw")
    store.claim_next()
    queued = _enqueue(store, "b_euw")

    recovered = store.recover_orphans()
    assert recovered == 1
    assert store.get(int(running["id"]))["state"] == jobs.FAILED
    assert store.get(int(queued["id"]))["state"] == jobs.QUEUED


def test_recover_orphans_releases_the_active_slot_for_a_new_enqueue(store: JobStore) -> None:
    """`recover_orphans` moves a running job to `failed` via a batch
    `update_many`, not the per-job `set_state` path -- prove this batch path
    still frees the player's slot for a new active job, the same way
    `set_state`'s terminal transition does. This is the scenario the old
    flag-based design (rejected during this task, see `jobs.py`'s module
    docstring) would have silently broken had the flag not been released on
    every terminal-transition code path, including this batch one.
    """
    running = _enqueue(store, "a_euw")
    store.claim_next()
    store.recover_orphans()
    assert store.get(int(running["id"]))["state"] == jobs.FAILED

    _, created = store.enqueue(
        kind=jobs.JOB_KIND_ANALYZE,
        riot_id="Test",
        tagline="EUW",
        region="euw1",
        player_slug="a_euw",
    )
    assert created


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


def test_encode_players_preserves_flex_rank() -> None:
    encoded = jobs.encode_players(
        [
            {
                "riot_id": "Alice",
                "tagline": "EUW",
                "solo_tier": "DIAMOND",
                "solo_rank": "IV",
                "solo_lp": 83,
                "flex_tier": "GOLD",
                "flex_rank": "II",
                "flex_lp": 12,
            },
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
            "flex_tier": "GOLD",
            "flex_rank": "II",
            "flex_lp": 12,
        },
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
    _enqueue(store)
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


# --------------------------------------------------------------------------
# Defensive defaults: there is no `ALTER TABLE`-style migration for Mongo.
# Every read path must default a document's missing field the same way the
# old SQL `DEFAULT`/nullable columns did (Phase 8, Task 4, plan Step 3).
# Each test below constructs a raw mongomock document missing the field(s)
# under test and asserts the read path still returns the documented default.
# --------------------------------------------------------------------------


def test_job_and_player_ids_are_real_objectids(store: JobStore) -> None:
    from bson import ObjectId

    job = _enqueue(store)
    job_doc = store._jobs.find_one({"job_id": int(job["id"])})
    assert isinstance(job_doc["_id"], ObjectId)

    store.upsert_player(slug="test_euw", riot_id="Test", tagline="EUW", region="euw1")
    player_doc = store._players.find_one({"slug": "test_euw"})
    assert isinstance(player_doc["_id"], ObjectId)


def test_counter_id_is_a_real_objectid(store: JobStore) -> None:
    from bson import ObjectId

    _enqueue(store)
    counter_doc = store._counters.find_one({"name": "jobs"})
    assert isinstance(counter_doc["_id"], ObjectId)


def test_get_defaults_missing_job_fields(store: JobStore) -> None:
    now = time.time()
    store._jobs.insert_one(
        {
            "job_id": 1,
            "kind": jobs.JOB_KIND_ANALYZE,
            "player_slug": "legacy_euw",
            "riot_id": "Legacy",
            "tagline": "EUW",
            "region": "euw1",
            "players_json": "[]",
            "state": jobs.QUEUED,
            "created_at": now,
            "updated_at": now,
            # filter_champion, filter_role, min_games, stage_detail,
            # stage_current, stage_total, error, trace_id, started_at,
            # finished_at all deliberately absent.
        }
    )
    loaded = store.get(1)
    assert loaded is not None
    assert loaded["filter_champion"] is None
    assert loaded["filter_role"] is None
    assert loaded["min_games"] is None
    assert loaded["stage_detail"] == ""
    assert loaded["stage_current"] is None
    assert loaded["stage_total"] is None
    assert loaded["error"] == ""
    assert loaded["trace_id"] == ""
    assert loaded["started_at"] is None
    assert loaded["finished_at"] is None
    assert loaded["players"] == [{"riot_id": "Legacy", "tagline": "EUW"}]


def test_get_player_defaults_missing_player_fields(store: JobStore) -> None:
    store._players.insert_one(
        {
            "slug": "legacy_euw",
            "riot_id": "Legacy",
            "tagline": "EUW",
            "region": "euw1",
            # players_json, last_job_id, base_completed_at,
            # peer_completed_at, peer_failed, watch_enabled,
            # watch_interval_s, last_watch_at, last_watch_error,
            # watch_seen_json all deliberately absent.
        }
    )
    player = store.get_player("legacy_euw")
    assert player is not None
    assert player["slug"] == "legacy_euw"
    assert player["last_job_id"] is None
    assert player["base_completed_at"] is None
    assert player["peer_completed_at"] is None
    assert player["peer_failed"] == 0
    assert player["watch_enabled"] == 0
    assert player["watch_interval_s"] == DEFAULT_WATCH_INTERVAL_S
    assert player["last_watch_at"] is None
    assert player["last_watch_error"] == ""
    assert player["players"] == [{"riot_id": "Legacy", "tagline": "EUW"}]


def test_list_watched_players_defaults_missing_watch_seen(store: JobStore) -> None:
    store._players.insert_one(
        {
            "slug": "legacy_euw",
            "riot_id": "Legacy",
            "tagline": "EUW",
            "region": "euw1",
            "watch_enabled": 1,
            # watch_seen_json deliberately absent.
        }
    )
    watched = store.list_watched_players()
    assert len(watched) == 1
    assert watched[0]["watch_seen"] == {}
