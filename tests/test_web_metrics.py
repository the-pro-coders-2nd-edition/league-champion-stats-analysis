"""Tests for API-UI's own Prometheus /metrics endpoint."""

from __future__ import annotations

from pathlib import Path
from typing import Iterator

import pytest
from fastapi.testclient import TestClient

from league_stats_common.core.config import WebConfig
import league_stats_api_ui.app as web_app


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setattr(web_app, "_verify_players_exist", lambda *args, **kwargs: None)
    config = WebConfig(
        app_db_path=tmp_path / "app.sqlite",
        output_dir=tmp_path / "output",
        gemini_api_key="fake-key",
        runner_storage_mode="sqlite",
    )
    application = web_app.create_app(config, start_worker=False)
    with TestClient(application) as test_client:
        yield test_client


def test_metrics_endpoint_exposes_prometheus_text(client: TestClient) -> None:
    response = client.get("/metrics")
    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]
    body = response.text
    assert "api_ui_http_request_duration_seconds" in body
    assert "api_ui_http_requests_total" in body


def test_metrics_records_request_count_by_route_and_status(client: TestClient) -> None:
    client.get("/health")
    body = client.get("/metrics").text
    assert 'route="/health"' in body
    assert 'method="GET"' in body
    assert 'status_code="200"' in body


def test_metrics_records_404_status_code_on_matched_route(client: TestClient) -> None:
    client.get("/api/jobs/999999")
    body = client.get("/metrics").text
    assert 'route="/api/jobs/{job_id}"' in body
    assert 'status_code="404"' in body
