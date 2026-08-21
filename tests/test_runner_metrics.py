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
        _generate_latest_default_registry(), "runner_jobs_total", {"status": "success"}
    ) or 0.0
    before_hist_count = _sample_value(
        _generate_latest_default_registry(), "runner_job_duration_seconds_count"
    ) or 0.0

    servicer = RunnerServicer(web_config=WebConfig(peers_mode="grpc"))
    events: "queue.SimpleQueue" = queue.SimpleQueue()
    servicer._run_job({"id": 1}, object(), events, "1")

    after_text = _generate_latest_default_registry()
    after_count = _sample_value(after_text, "runner_jobs_total", {"status": "success"})
    after_hist_count = _sample_value(after_text, "runner_job_duration_seconds_count")

    assert after_count == before_count + 1
    assert after_hist_count == before_hist_count + 1


def test_run_job_records_failed_counter_on_crash(monkeypatch) -> None:
    def _boom(job, adapter, web_config):
        raise RuntimeError("boom")

    monkeypatch.setattr(runner_service, "execute_job", _boom)

    before_count = _sample_value(
        _generate_latest_default_registry(), "runner_jobs_total", {"status": "failed"}
    ) or 0.0

    servicer = RunnerServicer(web_config=WebConfig(peers_mode="grpc"))
    events: "queue.SimpleQueue" = queue.SimpleQueue()
    servicer._run_job({"id": 1}, object(), events, "1")

    after_count = _sample_value(
        _generate_latest_default_registry(), "runner_jobs_total", {"status": "failed"}
    )
    assert after_count == before_count + 1

    result = events.get_nowait()
    assert result["final"] is True
    assert "boom" in result["error"]


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
