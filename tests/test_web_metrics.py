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
        output_dir=tmp_path / "output",
        gemini_api_key="fake-key",
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


def test_metrics_exposes_in_flight_gauge_labeled_by_route_template(client: TestClient) -> None:
    """`HTTP_REQUESTS_IN_FLIGHT` must be labeled by the matched route
    *template*, never the raw interpolated path -- a real slug/job id in the
    label would be an unbounded-cardinality regression."""
    client.get("/api/jobs/999999")
    body = client.get("/metrics").text
    assert "api_ui_http_requests_in_flight" in body
    assert 'route="/api/jobs/{job_id}"' in body
    assert "999999" not in body.split("api_ui_http_requests_in_flight")[1].split("\n")[0]


def test_metrics_settles_back_to_zero_in_flight_after_request_completes(client: TestClient) -> None:
    client.get("/health")
    body = client.get("/metrics").text
    for line in body.splitlines():
        if line.startswith("api_ui_http_requests_in_flight{") and 'route="/health"' in line:
            assert line.strip().endswith(" 0.0")
            return
    pytest.fail("api_ui_http_requests_in_flight{route=\"/health\"} sample not found")


def test_outbound_rpc_duration_records_riot_api_resolve_puuid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`_verify_players_exist` (called from `POST /api/analyses`) must record
    `OUTBOUND_RPC_DURATION` around its `resolve_puuid` call -- this is the
    real gap the RFC calls out: `POST /api/analyses` makes a synchronous Riot
    API call with zero latency visibility today."""
    monkeypatch.setattr(
        web_app, "_build_precheck_client", lambda *a, **k: _FakeRiotClient()
    )
    config = WebConfig(output_dir=tmp_path / "output", gemini_api_key="fake-key")
    application = web_app.create_app(config, start_worker=False)
    with TestClient(application) as test_client:
        response = test_client.post(
            "/api/analyses", json={"riot_id": "Name", "tagline": "EUW", "region": "euw1"}
        )
        assert response.status_code == 200
        body = test_client.get("/metrics").text
    assert "api_ui_outbound_call_duration_seconds" in body
    assert 'target="riot_api"' in body
    assert 'operation="resolve_puuid"' in body
    assert 'outcome="ok"' in body


class _FakeRiotClient:
    def resolve_puuid(self, riot_id: str, tagline: str) -> str:
        return "fake-puuid"
