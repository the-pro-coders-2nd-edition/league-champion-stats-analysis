"""Prometheus visibility for `SamplingScheduler` (dashboard-observability
follow-up to the RFC "Batched, Round-Robin Live Sampling for PEERS" -- the
scheduler shipped with zero metrics; see `deploy/grafana/dashboards/peers.json`).

Mirrors `tests/test_cron_watch_metrics.py`'s structure -- same
`_sample_value`/`_generate_latest_default_registry` helpers against
Prometheus's own default registry. Uses a minimal fake task (matching
`scheduler.py`'s own "free of any Mongo/gRPC dependency ... tested directly
against fake SamplingTasks" convention) rather than a real `SamplingTask`,
since these tests only exercise the scheduler's own bookkeeping.
"""

from __future__ import annotations

from prometheus_client.parser import text_string_to_metric_families

from league_stats_peers.analysis.peer.scheduler import SamplingScheduler


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


class _FakeTask:
    """Minimal stand-in for `SamplingTask`: enough surface for the scheduler."""

    def __init__(self, key: tuple[str, str, str, str, str], *, batches_to_finish: int = 1) -> None:
        self.key = key
        self.games = 0
        self.downloads = 0
        self.batches_run = 0
        self._batches_to_finish = batches_to_finish
        self.reached_interim = False
        self.exhausted = False
        self.failing = False

    @property
    def reached_target(self) -> bool:
        return self.batches_run >= self._batches_to_finish

    def run_batch(self) -> None:
        if self.failing:
            raise RuntimeError("boom")
        self.batches_run += 1
        self.downloads += 1
        self.games += 1

    @property
    def done(self) -> bool:
        return self.reached_target or self.exhausted


def test_enqueue_sets_queued_and_active_by_role_gauges() -> None:
    scheduler = SamplingScheduler(num_workers=1)
    task = _FakeTask(("euw1", "GOLD", "Zac", "JUNGLE", "15.1"), batches_to_finish=99)

    scheduler.get_or_create(task.key, lambda: task)

    text = _generate_latest_default_registry()
    assert _sample_value(text, "peers_scheduler_queued_tasks") == 1.0
    assert _sample_value(text, "peers_scheduler_active_tasks", {"role": "JUNGLE"}) >= 1.0
    # Every VALID_ROLES member is always set, including roles with no active
    # tasks -- an explicit zero, not a missing series.
    assert _sample_value(text, "peers_scheduler_active_tasks", {"role": "TOP"}) is not None


def test_finalized_full_batch_increments_counter_and_clears_gauges() -> None:
    scheduler = SamplingScheduler(num_workers=1)
    task = _FakeTask(("euw1", "GOLD", "Ahri", "MIDDLE", "15.1"), batches_to_finish=1)
    scheduler.get_or_create(task.key, lambda: task)

    before = _sample_value(
        _generate_latest_default_registry(), "peers_scheduler_batches_total", {"outcome": "finalized_full"}
    ) or 0.0

    assert scheduler.step()

    text = _generate_latest_default_registry()
    after = _sample_value(text, "peers_scheduler_batches_total", {"outcome": "finalized_full"})
    assert after == before + 1.0
    assert _sample_value(text, "peers_scheduler_queued_tasks") == 0.0
    assert _sample_value(text, "peers_scheduler_active_tasks", {"role": "MIDDLE"}) == 0.0


def test_re_enqueued_batch_increments_counter_and_keeps_task_queued() -> None:
    scheduler = SamplingScheduler(num_workers=1)
    task = _FakeTask(("euw1", "GOLD", "LeeSin", "JUNGLE", "15.1"), batches_to_finish=5)
    scheduler.get_or_create(task.key, lambda: task)

    before = _sample_value(
        _generate_latest_default_registry(), "peers_scheduler_batches_total", {"outcome": "re_enqueued"}
    ) or 0.0

    assert scheduler.step()

    text = _generate_latest_default_registry()
    after = _sample_value(text, "peers_scheduler_batches_total", {"outcome": "re_enqueued"})
    assert after == before + 1.0
    assert _sample_value(text, "peers_scheduler_queued_tasks") == 1.0


def test_failed_batch_finalizes_partial_and_increments_counter() -> None:
    scheduler = SamplingScheduler(num_workers=1)
    task = _FakeTask(("euw1", "GOLD", "Yasuo", "MIDDLE", "15.1"), batches_to_finish=99)
    task.failing = True
    scheduler.get_or_create(task.key, lambda: task)

    before = _sample_value(
        _generate_latest_default_registry(), "peers_scheduler_batches_total", {"outcome": "finalized_partial"}
    ) or 0.0

    assert scheduler.step()

    text = _generate_latest_default_registry()
    after = _sample_value(text, "peers_scheduler_batches_total", {"outcome": "finalized_partial"})
    assert after == before + 1.0
    assert not scheduler.is_active(task.key)


def test_batch_duration_histogram_records_a_sample() -> None:
    scheduler = SamplingScheduler(num_workers=1)
    task = _FakeTask(("euw1", "GOLD", "Jinx", "BOTTOM", "15.1"), batches_to_finish=99)
    scheduler.get_or_create(task.key, lambda: task)

    before = _sample_value(
        _generate_latest_default_registry(), "peers_scheduler_batch_duration_seconds_count"
    ) or 0.0

    assert scheduler.step()

    after = _sample_value(
        _generate_latest_default_registry(), "peers_scheduler_batch_duration_seconds_count"
    )
    assert after == before + 1.0
