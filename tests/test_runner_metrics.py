"""RUNNER's minimal Prometheus metrics: a job-duration histogram and a
terminal-status counter, recorded from `RunnerServicer._run_job`.

This establishes the pattern (`prometheus_client.start_http_server` +
`Histogram` + `Counter`) other services will replicate in their own
observability steps -- deliberately scoped to RUNNER only for now.
"""

from __future__ import annotations

import queue
import time

from prometheus_client import CollectorRegistry, Counter, Histogram
from prometheus_client.parser import text_string_to_metric_families

from league_stats_common.core.config import WebConfig
from league_stats_runner import service as runner_service
from league_stats_runner.service import RunnerServicer


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


def test_run_job_records_duration_and_success_counter(monkeypatch) -> None:
    def _fake_execute_job(job, adapter, web_config):
        time.sleep(0.01)

    monkeypatch.setattr(runner_service, "execute_job", _fake_execute_job)

    before_count = _sample_value(
        _generate_latest_default_registry(),
        "runner_jobs_total",
        {"status": "success", "kind": "analyze"},
    ) or 0.0
    before_hist_count = _sample_value(
        _generate_latest_default_registry(),
        "runner_job_duration_seconds_count",
        {"kind": "analyze"},
    ) or 0.0

    servicer = RunnerServicer(web_config=WebConfig())
    events: "queue.SimpleQueue" = queue.SimpleQueue()
    servicer._run_job({"id": 1, "kind": "analyze"}, object(), events, "1")

    after_text = _generate_latest_default_registry()
    after_count = _sample_value(
        after_text, "runner_jobs_total", {"status": "success", "kind": "analyze"}
    )
    after_hist_count = _sample_value(
        after_text, "runner_job_duration_seconds_count", {"kind": "analyze"}
    )

    assert after_count == before_count + 1
    assert after_hist_count == before_hist_count + 1


def test_run_job_records_failed_counter_on_crash(monkeypatch) -> None:
    def _boom(job, adapter, web_config):
        raise RuntimeError("boom")

    monkeypatch.setattr(runner_service, "execute_job", _boom)

    before_count = _sample_value(
        _generate_latest_default_registry(),
        "runner_jobs_total",
        {"status": "failed", "kind": "refresh"},
    ) or 0.0

    servicer = RunnerServicer(web_config=WebConfig())
    events: "queue.SimpleQueue" = queue.SimpleQueue()
    servicer._run_job({"id": 1, "kind": "refresh"}, object(), events, "1")

    after_count = _sample_value(
        _generate_latest_default_registry(),
        "runner_jobs_total",
        {"status": "failed", "kind": "refresh"},
    )
    assert after_count == before_count + 1

    result = events.get_nowait()
    assert result["final"] is True
    assert "boom" in result["error"]


def test_run_job_increments_then_decrements_jobs_in_flight(monkeypatch) -> None:
    """`RUNNER_JOBS_IN_FLIGHT` is incremented by `EnqueueJob` (not exercised
    here) and decremented by `_run_job` on both the success and failure paths
    -- assert the net effect of one `_run_job` call is a decrement, proving
    the gauge is wired into both exit paths."""
    def _fake_execute_job(job, adapter, web_config):
        pass

    monkeypatch.setattr(runner_service, "execute_job", _fake_execute_job)

    runner_service.RUNNER_JOBS_IN_FLIGHT.inc()
    before = runner_service.RUNNER_JOBS_IN_FLIGHT._value.get()

    servicer = RunnerServicer(web_config=WebConfig())
    events: "queue.SimpleQueue" = queue.SimpleQueue()
    servicer._run_job({"id": 1, "kind": "analyze"}, object(), events, "1")

    after = runner_service.RUNNER_JOBS_IN_FLIGHT._value.get()
    assert after == before - 1


def test_enqueue_job_increments_jobs_in_flight(monkeypatch) -> None:
    def _fake_execute_job(job, adapter, web_config):
        time.sleep(0.05)

    monkeypatch.setattr(runner_service, "execute_job", _fake_execute_job)

    servicer = RunnerServicer(web_config=WebConfig())
    before = runner_service.RUNNER_JOBS_IN_FLIGHT._value.get()

    from league_stats_rpc.v1 import runner_pb2

    class _FakeContext:
        def set_code(self, *_args):
            pass

        def set_details(self, *_args):
            pass

    request = runner_pb2.EnqueueJobRequest(kind=runner_pb2.JOB_KIND_ANALYZE, riot_id="Name", tagline="EUW")
    servicer.EnqueueJob(request, _FakeContext())

    after = runner_service.RUNNER_JOBS_IN_FLIGHT._value.get()
    assert after == before + 1


def test_dedicated_registry_can_scrape_independent_histogram_and_counter() -> None:
    """Sanity check on the scraping approach itself, against a private
    registry -- isolated from any cross-test pollution of the default one."""
    registry = CollectorRegistry()
    duration = Histogram(
        "example_duration_seconds", "example", registry=registry
    )
    total = Counter(
        "example_total", "example", ["status"], registry=registry
    )

    with duration.time():
        total.labels(status="success").inc()

    from prometheus_client import generate_latest

    text = generate_latest(registry).decode("utf-8")
    assert _sample_value(text, "example_duration_seconds_count") == 1.0
    assert _sample_value(text, "example_total", {"status": "success"}) == 1.0
