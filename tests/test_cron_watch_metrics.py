"""CRON-watch's minimal Prometheus metrics: a tick-duration histogram and a
new-game-detected counter, recorded from `CronWatchServicer`.

Mirrors `tests/test_runner_metrics.py`'s structure -- same
`_sample_value`/`_generate_latest_default_registry` helpers, and a dedicated
`CollectorRegistry` sanity check isolated from any cross-test pollution of
Prometheus's default global registry.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from prometheus_client import CollectorRegistry, Counter, Histogram
from prometheus_client.parser import text_string_to_metric_families

from league_stats.core.config import RANKED_SOLO_QUEUE_ID
from league_stats.cron_watch.service import CronWatchServicer
from league_stats.web.jobs import JobStore
from tests.test_watch import FakeClient

SLUG = "hugros"


def _sample_value(registry_text: str, metric_name: str, labels: dict[str, str] | None = None) -> float | None:
    labels = labels or {}
    for family in text_string_to_metric_families(registry_text):
        for sample in family.samples:
            if sample.name == metric_name and all(
                sample.labels.get(k) == v for k, v in labels.items()
            ):
                return sample.value
    return None


def _generate_latest_default_registry() -> str:
    from prometheus_client import generate_latest, REGISTRY

    return generate_latest(REGISTRY).decode("utf-8")


@pytest.fixture()
def store(tmp_path: Path):
    handle = JobStore(tmp_path / "app.sqlite")
    handle.upsert_player(
        slug=SLUG,
        riot_id="Hugros",
        tagline="EUW",
        region="euw1",
        players=[{"riot_id": "Hugros", "tagline": "EUW"}],
    )
    handle.set_watch(SLUG, enabled=True, interval_s=60)
    yield handle
    handle.close()


def test_tick_records_duration_histogram(store: JobStore) -> None:
    client = FakeClient({"puuid-hugros": {RANKED_SOLO_QUEUE_ID: ["EUW1_1"]}})
    servicer = CronWatchServicer(store, lambda region: client)

    before_count = _sample_value(
        _generate_latest_default_registry(), "cron_watch_tick_duration_seconds_count"
    ) or 0.0

    asyncio.run(servicer._poller.tick())  # noqa: SLF001 -- exercising the instrumented tick

    after_count = _sample_value(
        _generate_latest_default_registry(), "cron_watch_tick_duration_seconds_count"
    )
    assert after_count == before_count + 1


def test_new_game_detection_increments_counter(store: JobStore) -> None:
    """Drives `WatchPoller._check_group` directly (as `ForceRefresh` does,
    see `tests/test_cron_watch_service.py`) rather than `tick()`, since
    `tick()`'s due-interval gating would need a controllable clock
    `CronWatchServicer` doesn't expose -- irrelevant here, since the counter
    is recorded from `_on_new_game`, not from the tick wrapper."""
    client = FakeClient({"puuid-hugros": {RANKED_SOLO_QUEUE_ID: ["EUW1_1"]}})
    servicer = CronWatchServicer(store, lambda region: client)
    row = servicer._watched_row(SLUG)  # noqa: SLF001

    asyncio.run(servicer._poller._check_group(row, SLUG))  # noqa: SLF001 -- baseline

    before_count = _sample_value(
        _generate_latest_default_registry(), "cron_watch_new_games_detected_total"
    ) or 0.0

    client.newest["puuid-hugros"] = {RANKED_SOLO_QUEUE_ID: ["EUW1_2"]}
    row = servicer._watched_row(SLUG)  # noqa: SLF001 -- re-fetch: watch_seen changed
    asyncio.run(servicer._poller._check_group(row, SLUG))  # noqa: SLF001

    after_count = _sample_value(
        _generate_latest_default_registry(), "cron_watch_new_games_detected_total"
    )
    assert after_count == before_count + 1


def test_dedicated_registry_can_scrape_independent_histogram_and_counter() -> None:
    """Sanity check on the scraping approach itself, against a private
    registry -- isolated from any cross-test pollution of the default one."""
    registry = CollectorRegistry()
    duration = Histogram(
        "example_tick_duration_seconds", "example", registry=registry
    )
    total = Counter(
        "example_new_games_total", "example", registry=registry
    )

    with duration.time():
        total.inc()

    from prometheus_client import generate_latest

    text = generate_latest(registry).decode("utf-8")
    assert _sample_value(text, "example_tick_duration_seconds_count") == 1.0
    assert _sample_value(text, "example_new_games_total") == 1.0
