"""Tests for the FastAPI endpoints (worker disabled, no network)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator

import pytest
from fastapi.testclient import TestClient

from league_stats.core.config import WebConfig
from league_stats.web import app as web_app
from league_stats.web import jobs


def _write_report(output_dir: Path, slug: str, build_slug: str, **meta: Any) -> Path:
    """Create a minimal on-disk report (meta.json + report.html + summary.json)."""
    report_dir = output_dir / "reports" / slug / build_slug
    report_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "player": "Test#EUW",
        "riot_id": "Test",
        "tagline": "EUW",
        "champion": "Viktor",
        "role": "MIDDLE",
        "role_display": "mid",
        "build_label": "Viktor mid",
        "games": 42,
        "winrate": 0.55,
        "generated_at": "2026-08-01T10:00:00Z",
        **meta,
    }
    (report_dir / "meta.json").write_text(json.dumps(payload), encoding="utf-8")
    (report_dir / "report.html").write_text("<html>report</html>", encoding="utf-8")
    (report_dir / "summary.json").write_text(
        json.dumps({"player": "Test#EUW", "build_label": "Viktor mid", "games": 42}),
        encoding="utf-8",
    )
    return report_dir


@pytest.fixture()
def client(tmp_path: Path) -> Iterator[TestClient]:
    config = WebConfig(
        app_db_path=tmp_path / "app.sqlite",
        output_dir=tmp_path / "output",
        gemini_api_key="fake-key",
    )
    application = web_app.create_app(config, start_worker=False)
    with TestClient(application) as test_client:
        test_client.web_config = config  # type: ignore[attr-defined]
        test_client.job_store = application.state.job_store  # type: ignore[attr-defined]
        yield test_client


def test_health(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok"}


def test_landing_page_lists_reports(client: TestClient) -> None:
    _write_report(client.web_config.output_dir, "test_euw", "viktor_middle")
    response = client.get("/")
    assert response.status_code == 200
    assert "Test#EUW" in response.text


def test_landing_shows_busy_dot_for_active_jobs(client: TestClient) -> None:
    _write_report(client.web_config.output_dir, "test_euw", "viktor_middle")
    client.post("/api/analyses", json={"riot_id": "Test#EUW", "region": "euw1"})
    response = client.get("/")
    assert response.status_code == 200
    assert 'data-slug="test_euw"' in response.text
    assert "is-busy" in response.text

    activity = client.get("/api/activity").json()
    assert activity["items"][0]["slug"] == "test_euw"
    assert activity["items"][0]["state"] == jobs.QUEUED
    assert activity["items"][0]["has_report"] is True


def test_activity_includes_queued_players_without_reports(client: TestClient) -> None:
    client.post("/api/analyses", json={"riot_id": "New#EUW", "region": "euw1"})
    activity = client.get("/api/activity").json()
    assert activity["items"][0]["slug"] == "new_euw"
    assert activity["items"][0]["has_report"] is False

    landing = client.get("/")
    assert "New#EUW" in landing.text
    assert "is-busy" in landing.text
    assert "Queued for analysis" in landing.text


def test_submit_analysis_creates_job_and_dedups(client: TestClient) -> None:
    response = client.post(
        "/api/analyses", json={"riot_id": "Test#EUW", "region": "euw1"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["created"] is True
    assert body["player_slug"] == "test_euw"
    assert body["job"]["state"] == jobs.QUEUED
    assert body["has_report"] is False

    duplicate = client.post(
        "/api/analyses", json={"riot_id": "Test", "tagline": "EUW", "region": "euw1"}
    ).json()
    assert duplicate["created"] is False
    assert duplicate["job"]["id"] == body["job"]["id"]


def test_submit_analysis_validates_input(client: TestClient) -> None:
    assert client.post("/api/analyses", json={"riot_id": "NoTagline"}).status_code == 422
    assert (
        client.post(
            "/api/analyses", json={"riot_id": "A#B", "region": "narnia"}
        ).status_code
        == 422
    )


def test_job_status_includes_queue_position(client: TestClient) -> None:
    job_id = client.post(
        "/api/analyses", json={"riot_id": "Test#EUW", "region": "euw1"}
    ).json()["job"]["id"]
    body = client.get(f"/api/jobs/{job_id}").json()
    assert body["job"]["state"] == jobs.QUEUED
    assert body["job"]["queue_position"] == 0
    assert body["job"]["eta_s"] is not None
    assert client.get("/api/jobs/999").status_code == 404


def test_player_status_serves_existing_reports(client: TestClient) -> None:
    _write_report(client.web_config.output_dir, "test_euw", "viktor_middle")
    response = client.get("/api/players/test_euw")
    assert response.status_code == 200
    body = response.json()
    assert body["has_report"] is True
    assert body["builds"][0]["slug"] == "viktor_middle"
    assert body["builds"][0]["href"] == "/out/reports/test_euw/viktor_middle/report.html"
    assert body["active_job"] is None

    assert client.get("/api/players/unknown_player").status_code == 404


def test_report_served_statically(client: TestClient) -> None:
    _write_report(client.web_config.output_dir, "test_euw", "viktor_middle")
    response = client.get("/out/reports/test_euw/viktor_middle/report.html")
    assert response.status_code == 200
    assert "report" in response.text


def test_refresh_recovers_identity_from_disk(client: TestClient) -> None:
    _write_report(client.web_config.output_dir, "test_euw", "viktor_middle")
    response = client.post("/api/players/test_euw/refresh")
    assert response.status_code == 200
    body = response.json()
    assert body["job"]["kind"] == jobs.JOB_KIND_REFRESH
    assert body["job"]["state"] == jobs.QUEUED

    assert client.post("/api/players/nobody/refresh").status_code == 404


def test_player_page_renders(client: TestClient) -> None:
    _write_report(client.web_config.output_dir, "test_euw", "viktor_middle")
    response = client.get("/players/test_euw")
    assert response.status_code == 200
    assert "test_euw" in response.text


def test_chat_proxy(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_report(client.web_config.output_dir, "test_euw", "viktor_middle")

    captured: dict[str, Any] = {}

    def fake_reply(api_key: str, **kwargs: Any) -> str:
        captured["api_key"] = api_key
        captured["stats"] = kwargs["stats"]
        captured["history"] = kwargs["history"]
        return "You are doing great."

    monkeypatch.setattr(web_app, "gemini_reply", fake_reply)
    response = client.post(
        "/api/chat",
        json={
            "report": "test_euw/viktor_middle",
            "history": [{"role": "user", "parts": [{"text": "How is my CS?"}]}],
        },
    )
    assert response.status_code == 200
    assert response.json() == {"text": "You are doing great."}
    assert captured["api_key"] == "fake-key"
    assert captured["stats"]["build_label"] == "Viktor mid"


def test_submit_group_analysis(client: TestClient) -> None:
    response = client.post(
        "/api/analyses",
        json={"players": ["Alice#EUW", "Bob#EUW"], "region": "euw1"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["created"] is True
    assert body["player_slug"] == "alice_euw__bob_euw"
    job = client.job_store.get(int(body["job"]["id"]))
    assert job is not None
    assert job["players"] == [
        {"riot_id": "Alice", "tagline": "EUW"},
        {"riot_id": "Bob", "tagline": "EUW"},
    ]
    player = client.job_store.get_player("alice_euw__bob_euw")
    assert player is not None
    assert player["players"] == job["players"]


def test_submit_analysis_dedups_duplicate_group_members(client: TestClient) -> None:
    body = client.post(
        "/api/analyses",
        json={"players": ["Alice#EUW", "alice#euw", "Bob#EUW"], "region": "euw1"},
    ).json()
    assert body["player_slug"] == "alice_euw__bob_euw"
    job = client.job_store.get(int(body["job"]["id"]))
    assert len(job["players"]) == 2


def test_refresh_recovers_group_from_disk(client: TestClient) -> None:
    _write_report(
        client.web_config.output_dir,
        "alice_euw__bob_euw",
        "viktor_middle",
        player="Alice#EUW, Bob#EUW",
        riot_id="Alice",
        tagline="EUW",
        players=[
            {"riot_id": "Alice", "tagline": "EUW"},
            {"riot_id": "Bob", "tagline": "EUW"},
        ],
    )
    response = client.post("/api/players/alice_euw__bob_euw/refresh")
    assert response.status_code == 200
    job = client.job_store.get(int(response.json()["job"]["id"]))
    assert job["players"] == [
        {"riot_id": "Alice", "tagline": "EUW"},
        {"riot_id": "Bob", "tagline": "EUW"},
    ]


def test_landing_page_mentions_group_reports(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "Add another player" in response.text
    assert "players" in response.text


def test_chat_rejects_bad_requests(client: TestClient) -> None:
    # Unknown report.
    response = client.post(
        "/api/chat",
        json={
            "report": "test_euw/viktor_middle",
            "history": [{"role": "user", "parts": [{"text": "hi"}]}],
        },
    )
    assert response.status_code == 404

    # Path traversal in the report ref.
    response = client.post(
        "/api/chat",
        json={
            "report": "../secrets/whatever",
            "history": [{"role": "user", "parts": [{"text": "hi"}]}],
        },
    )
    assert response.status_code == 400

    # History must end with a user message.
    _write_report(client.web_config.output_dir, "test_euw", "viktor_middle")
    response = client.post(
        "/api/chat",
        json={
            "report": "test_euw/viktor_middle",
            "history": [{"role": "model", "parts": [{"text": "hello"}]}],
        },
    )
    assert response.status_code == 400
