"""Tests for `NotifyingJobStore`: a `JobStore` that publishes on every mutation.

A fake/spy bus is enough here (per the design doc) -- no real asyncio needed;
`mongomock` matches this repo's established `JobStore` test pattern
(`tests/test_web_jobs.py`).
"""

from __future__ import annotations

import mongomock

from league_stats_common.infra.jobs import JOB_KIND_ANALYZE, DONE
from league_stats_api_ui.notifying_job_store import NotifyingJobStore


class _SpyBus:
    def __init__(self) -> None:
        self.published: list[str] = []

    def publish(self, slug: str) -> None:
        self.published.append(slug)


def _make_store() -> tuple[NotifyingJobStore, _SpyBus]:
    bus = _SpyBus()
    store = NotifyingJobStore(mongomock.MongoClient(), db_name="test_notifying", bus=bus)
    return store, bus


def test_set_state_publishes_the_jobs_player_slug() -> None:
    store, bus = _make_store()
    job, _ = store.enqueue(
        kind=JOB_KIND_ANALYZE, riot_id="Test", tagline="EUW", region="euw1",
        player_slug="test_euw",
    )
    bus.published.clear()

    store.set_state(job["id"], DONE, detail="Complete")

    assert bus.published == ["test_euw"]


def test_set_state_on_a_cancelled_job_does_not_publish() -> None:
    """`set_state` returns `False` (no-op) once a job is cancelled -- must not publish."""
    store, bus = _make_store()
    job, _ = store.enqueue(
        kind=JOB_KIND_ANALYZE, riot_id="Test", tagline="EUW", region="euw1",
        player_slug="test_euw",
    )
    store.cancel(job["id"])
    bus.published.clear()

    changed = store.set_state(job["id"], DONE, detail="Complete")

    assert changed is False
    assert bus.published == []


def test_update_progress_publishes_the_jobs_player_slug() -> None:
    store, bus = _make_store()
    job, _ = store.enqueue(
        kind=JOB_KIND_ANALYZE, riot_id="Test", tagline="EUW", region="euw1",
        player_slug="test_euw",
    )
    bus.published.clear()

    store.update_progress(job["id"], detail="Fetching…", current=1, total=5)

    assert bus.published == ["test_euw"]


def test_cancel_publishes_the_jobs_player_slug() -> None:
    store, bus = _make_store()
    job, _ = store.enqueue(
        kind=JOB_KIND_ANALYZE, riot_id="Test", tagline="EUW", region="euw1",
        player_slug="test_euw",
    )
    bus.published.clear()

    store.cancel(job["id"])

    assert bus.published == ["test_euw"]


def test_cancel_of_an_already_terminal_job_does_not_publish() -> None:
    store, bus = _make_store()
    job, _ = store.enqueue(
        kind=JOB_KIND_ANALYZE, riot_id="Test", tagline="EUW", region="euw1",
        player_slug="test_euw",
    )
    store.cancel(job["id"])
    bus.published.clear()

    result = store.cancel(job["id"])

    assert result is None
    assert bus.published == []


def test_upsert_player_publishes_the_slug() -> None:
    store, bus = _make_store()

    store.upsert_player(slug="test_euw", riot_id="Test", tagline="EUW", region="euw1")

    assert bus.published == ["test_euw"]


def test_set_watch_publishes_the_slug() -> None:
    store, bus = _make_store()
    store.upsert_player(slug="test_euw", riot_id="Test", tagline="EUW", region="euw1")
    bus.published.clear()

    store.set_watch("test_euw", enabled=True)

    assert bus.published == ["test_euw"]


def test_set_watch_on_an_unknown_player_does_not_publish() -> None:
    store, bus = _make_store()

    changed = store.set_watch("unknown_slug", enabled=True)

    assert changed is False
    assert bus.published == []


def test_record_watch_tick_publishes_the_slug() -> None:
    store, bus = _make_store()
    store.upsert_player(slug="test_euw", riot_id="Test", tagline="EUW", region="euw1")
    bus.published.clear()

    store.record_watch_tick("test_euw", seen={"acc1": "MATCH1"})

    assert bus.published == ["test_euw"]


def test_mark_player_base_complete_publishes_the_slug() -> None:
    store, bus = _make_store()
    store.upsert_player(slug="test_euw", riot_id="Test", tagline="EUW", region="euw1")
    bus.published.clear()

    store.mark_player_base_complete("test_euw")

    assert bus.published == ["test_euw"]


def test_mark_player_peer_complete_publishes_the_slug() -> None:
    store, bus = _make_store()
    store.upsert_player(slug="test_euw", riot_id="Test", tagline="EUW", region="euw1")
    bus.published.clear()

    store.mark_player_peer_complete("test_euw")

    assert bus.published == ["test_euw"]


def test_mark_player_peer_failed_publishes_the_slug() -> None:
    store, bus = _make_store()
    store.upsert_player(slug="test_euw", riot_id="Test", tagline="EUW", region="euw1")
    bus.published.clear()

    store.mark_player_peer_failed("test_euw")

    assert bus.published == ["test_euw"]
