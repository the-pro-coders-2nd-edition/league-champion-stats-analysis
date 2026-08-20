"""Tests for the FastAPI endpoints (worker disabled, no network)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator

import pytest
from fastapi.testclient import TestClient

from league_stats_common.core.config import WebConfig
import league_stats_api_ui.app as web_app
import league_stats_common.infra.jobs as jobs


def _write_report(output_dir: Path, slug: str, build_slug: str, **meta: Any) -> Path:
    """Create a minimal on-disk report (meta.json + report.json + summary.json)."""
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
    (report_dir / "report.json").write_text(json.dumps(payload), encoding="utf-8")
    (report_dir / "summary.json").write_text(
        json.dumps({"player": "Test#EUW", "build_label": "Viktor mid", "games": 42}),
        encoding="utf-8",
    )
    return report_dir


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    # Skip Riot account-v1 lookup; dedicated tests cover the precheck path.
    monkeypatch.setattr(web_app, "_verify_players_exist", lambda *args, **kwargs: None)
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


def test_riot_txt_verification(client: TestClient) -> None:
    response = client.get("/riot.txt")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert response.text.strip() == "71e571c1-efe7-4509-828e-f16ad603f8dd"


def test_landing_page_lists_reports(client: TestClient) -> None:
    _write_report(client.web_config.output_dir, "test_euw", "viktor_middle")
    groups = client.get("/api/groups").json()["groups"]
    by_slug = {group["slug"]: group for group in groups}
    assert by_slug["test_euw"]["player"] == "Test#EUW"
    assert by_slug["test_euw"]["has_report"] is True
    assert by_slug["test_euw"]["build_count"] == 1


def test_landing_page_shows_profile_icons(client: TestClient) -> None:
    icon_dir = client.web_config.output_dir / "assets" / "profile_icons"
    icon_dir.mkdir(parents=True, exist_ok=True)
    (icon_dir / "456.png").write_bytes(b"png")
    (icon_dir / "789.png").write_bytes(b"png")
    _write_report(
        client.web_config.output_dir,
        "alice_euw__bob_euw",
        "viktor_middle",
        player="Alice#EUW, Bob#EUW",
        riot_id="Alice",
        tagline="EUW",
        players=[
            {"riot_id": "Alice", "tagline": "EUW", "profile_icon_id": 456},
            {"riot_id": "Bob", "tagline": "EUW", "profile_icon_id": 789},
        ],
    )
    groups = client.get("/api/groups").json()["groups"]
    group = next(group for group in groups if group["slug"] == "alice_euw__bob_euw")
    labels = {member["label"] for member in group["players"]}
    assert labels == {"Alice#EUW", "Bob#EUW"}
    icons = {member["profile_icon"] for member in group["players"]}
    assert icons == {"/out/assets/profile_icons/456.png", "/out/assets/profile_icons/789.png"}
    assert group["is_group"] is True


def test_submit_analysis_persists_the_requests_trace_id_on_the_job(
    client: TestClient,
) -> None:
    """Phase 6 final review, Finding 1: the HTTP request's trace_id (minted or
    forwarded by `originate_trace_id`, echoed back as `X-Trace-Id`) must be
    persisted on the enqueued `JobStore` row -- not just echoed to the client
    -- so `AnalysisWorker` can later restore it before calling RUNNER."""
    response = client.post(
        "/api/analyses",
        json={"riot_id": "New#EUW", "region": "euw1"},
        headers={"x-trace-id": "caller-supplied-trace-abc"},
    )
    assert response.headers["X-Trace-Id"] == "caller-supplied-trace-abc"
    job_id = response.json()["job"]["id"]

    row = client.job_store.get(job_id)  # type: ignore[attr-defined]
    assert row["trace_id"] == "caller-supplied-trace-abc"


def test_refresh_player_persists_the_requests_trace_id_on_the_job(
    client: TestClient,
) -> None:
    """Same as above for `_enqueue_player_job`'s call site (used by
    `/refresh`, `/regenerate` and the career-ladder drop route)."""
    _write_report(client.web_config.output_dir, "test_euw", "viktor_middle")

    response = client.post(
        "/api/players/test_euw/refresh", headers={"x-trace-id": "refresh-trace-xyz"}
    )
    assert response.status_code == 200
    job_id = response.json()["job"]["id"]

    row = client.job_store.get(job_id)  # type: ignore[attr-defined]
    assert row["trace_id"] == "refresh-trace-xyz"


def test_groups_endpoint_matches_landing_page_data(client: TestClient) -> None:
    _write_report(client.web_config.output_dir, "test_euw", "viktor_middle")
    client.post("/api/analyses", json={"riot_id": "New#EUW", "region": "euw1"})

    groups = client.get("/api/groups").json()["groups"]
    slugs = {group["slug"] for group in groups}
    assert slugs == {"test_euw", "new_euw"}
    by_slug = {group["slug"]: group for group in groups}
    assert by_slug["test_euw"]["has_report"] is True
    assert by_slug["test_euw"]["busy"] is False
    assert by_slug["new_euw"]["has_report"] is False
    assert by_slug["new_euw"]["busy"] is True
    assert by_slug["new_euw"]["job_state"] == jobs.QUEUED


def test_landing_shows_busy_dot_for_active_jobs(client: TestClient) -> None:
    _write_report(client.web_config.output_dir, "test_euw", "viktor_middle")
    client.post("/api/analyses", json={"riot_id": "Test#EUW", "region": "euw1"})

    groups = client.get("/api/groups").json()["groups"]
    group = next(group for group in groups if group["slug"] == "test_euw")
    assert group["busy"] is True
    assert group["job_state"] == jobs.QUEUED

    activity = client.get("/api/activity").json()
    assert activity["items"][0]["slug"] == "test_euw"
    assert activity["items"][0]["state"] == jobs.QUEUED
    assert activity["items"][0]["has_report"] is True


def test_activity_includes_queued_players_without_reports(client: TestClient) -> None:
    client.post("/api/analyses", json={"riot_id": "New#EUW", "region": "euw1"})
    activity = client.get("/api/activity").json()
    assert activity["items"][0]["slug"] == "new_euw"
    assert activity["items"][0]["has_report"] is False
    assert activity["items"][0]["player_label"] == "New#EUW"
    assert activity["items"][0]["state"] == jobs.QUEUED

    groups = client.get("/api/groups").json()["groups"]
    group = next(group for group in groups if group["slug"] == "new_euw")
    assert group["busy"] is True
    assert group["has_report"] is False


def test_submit_analysis_creates_job_and_dedups(client: TestClient) -> None:
    response = client.post(
        "/api/analyses", json={"riot_id": "Test#EUW", "region": "euw1"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["created"] is True
    assert body["player_slug"] == "test_euw"
    assert body["job"]["state"] == jobs.QUEUED
    assert body["job"]["min_games"] is None
    assert body["has_report"] is False

    duplicate = client.post(
        "/api/analyses", json={"riot_id": "Test", "tagline": "EUW", "region": "euw1"}
    ).json()
    assert duplicate["created"] is False
    assert duplicate["job"]["id"] == body["job"]["id"]


def test_submit_analysis_stores_min_games(client: TestClient) -> None:
    response = client.post(
        "/api/analyses",
        json={"riot_id": "Test#EUW", "region": "euw1", "min_games": 10},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["job"]["min_games"] == 10
    stored = client.job_store.get(int(body["job"]["id"]))
    assert stored is not None
    assert stored["min_games"] == 10


def test_submit_analysis_validates_input(client: TestClient) -> None:
    assert client.post("/api/analyses", json={"riot_id": "NoTagline"}).status_code == 422
    assert (
        client.post(
            "/api/analyses", json={"riot_id": "A#B", "region": "narnia"}
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/analyses",
            json={"riot_id": "A#B", "region": "euw1", "min_games": 7},
        ).status_code
        == 422
    )


def test_submit_rejects_unknown_riot_id(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(
        players: list[dict[str, str]], region: str, output_dir: Path
    ) -> None:
        raise ValueError(
            f"Player {players[0]['riot_id']}#{players[0]['tagline']} was not found "
            f"on {region}. Check the Riot ID, tagline and region."
        )

    monkeypatch.setattr(web_app, "_verify_players_exist", boom)
    response = client.post(
        "/api/analyses", json={"riot_id": "Fake#EUW", "region": "euw1"}
    )
    assert response.status_code == 422
    assert "Fake#EUW" in response.json()["detail"]
    assert "not found" in response.json()["detail"]
    assert client.job_store.list_active_jobs() == []


def test_submit_rejects_riot_api_outage(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from league_stats_common.infra.riot_api import RiotApiError

    def boom(
        players: list[dict[str, str]], region: str, output_dir: Path
    ) -> None:
        raise RiotApiError("GET failed: HTTP 503")

    monkeypatch.setattr(web_app, "_verify_players_exist", boom)
    response = client.post(
        "/api/analyses", json={"riot_id": "Test#EUW", "region": "euw1"}
    )
    assert response.status_code == 502
    assert "Could not verify" in response.json()["detail"]


def test_verify_players_exist_raises_for_404(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from league_stats_common.infra.riot_api import RiotApiError

    class FakeClient:
        def resolve_puuid(self, riot_id: str, tagline: str) -> str:
            if riot_id == "Missing":
                raise RiotApiError(
                    "GET https://europe.api.riotgames.com/riot/account/v1/"
                    f"accounts/by-riot-id/{riot_id}/{tagline} failed: HTTP 404 not found"
                )
            return "puuid-ok"

    monkeypatch.setattr(web_app, "_build_precheck_client", lambda region, output_dir: FakeClient())
    with pytest.raises(ValueError, match="Missing#EUW"):
        web_app._verify_players_exist(
            [
                {"riot_id": "Ok", "tagline": "EUW"},
                {"riot_id": "Missing", "tagline": "EUW"},
            ],
            "euw1",
            tmp_path,
        )


def test_verify_players_exist_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FakeClient:
        def resolve_puuid(self, riot_id: str, tagline: str) -> str:
            return f"puuid-{riot_id}"

    monkeypatch.setattr(web_app, "_build_precheck_client", lambda region, output_dir: FakeClient())
    web_app._verify_players_exist(
        [{"riot_id": "Alice", "tagline": "EUW"}],
        "euw1",
        tmp_path,
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


def test_cancel_queued_job(client: TestClient) -> None:
    job_id = client.post(
        "/api/analyses", json={"riot_id": "Test#EUW", "region": "euw1"}
    ).json()["job"]["id"]
    response = client.post(f"/api/jobs/{job_id}/cancel")
    assert response.status_code == 200
    body = response.json()
    assert body["job"]["state"] == jobs.CANCELLED
    assert body["job"]["stage_detail"] == "Cancelled by user"

    status = client.get("/api/players/test_euw").json()
    assert status["active_job"] is None

    # Terminal jobs cannot be cancelled again.
    assert client.post(f"/api/jobs/{job_id}/cancel").status_code == 409
    assert client.post("/api/jobs/999/cancel").status_code == 404


def test_cancel_keeps_existing_base_report(client: TestClient) -> None:
    _write_report(client.web_config.output_dir, "test_euw", "viktor_middle")
    job_id = client.post(
        "/api/analyses", json={"riot_id": "Test#EUW", "region": "euw1"}
    ).json()["job"]["id"]
    client.job_store.set_state(job_id, jobs.REPORT_READY, detail="Report ready")
    client.job_store.mark_player_base_complete("test_euw")

    response = client.post(f"/api/jobs/{job_id}/cancel")
    assert response.status_code == 200
    assert response.json()["job"]["state"] == jobs.CANCELLED

    report = client.web_config.output_dir / "reports/test_euw/viktor_middle/report.json"
    assert report.is_file()
    status = client.get("/api/players/test_euw").json()
    assert status["has_report"] is True
    assert status["active_job"] is None


def test_player_status_serves_existing_reports(client: TestClient) -> None:
    _write_report(client.web_config.output_dir, "test_euw", "viktor_middle")
    response = client.get("/api/players/test_euw")
    assert response.status_code == 200
    body = response.json()
    assert body["has_report"] is True
    assert body["builds"][0]["slug"] == "viktor_middle"
    assert body["builds"][0]["href"] == "/out/reports/test_euw/viktor_middle/report.json"
    assert body["builds"][0]["peers_ready"] is False
    assert body["active_job"] is None

    assert client.get("/api/players/unknown_player").status_code == 404


def test_player_status_includes_welcome_back_field(client: TestClient) -> None:
    _write_report(client.web_config.output_dir, "test_euw", "viktor_middle")

    # No cache entry: welcome_back should be null
    response = client.get("/api/players/test_euw")
    assert response.status_code == 200
    body = response.json()
    assert "welcome_back" in body
    assert body["welcome_back"] is None

    # Record something in the cache
    welcome_back_data = {
        "new_match_id": "match123",
        "match_summary": {
            "win": True,
            "kills": 5,
            "deaths": 2,
            "assists": 10,
            "kda": 7.5,
            "cs_per_min": 6.5,
            "damage_share": 0.35,
        },
        "detected_at_unix": 1692604800,
    }
    cache = client.app.state.welcome_back_cache  # type: ignore[attr-defined]
    cache.record("test_euw", welcome_back_data)

    # Cache entry exists: should be returned and consumed
    response = client.get("/api/players/test_euw")
    assert response.status_code == 200
    body = response.json()
    assert body["welcome_back"] == welcome_back_data

    # Consumed: next call should have None again
    response = client.get("/api/players/test_euw")
    assert response.status_code == 200
    body = response.json()
    assert body["welcome_back"] is None


def test_player_status_is_never_cached(client: TestClient) -> None:
    """The welcome-back field is consumed-on-read: a cached/coalesced response
    would silently eat a single delivery for a second reader (a duplicate tab,
    a prefetch, a caching proxy)."""
    _write_report(client.web_config.output_dir, "test_euw", "viktor_middle")
    response = client.get("/api/players/test_euw")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"


def test_get_build_payload_returns_report_json(client: TestClient) -> None:
    report_dir = _write_report(client.web_config.output_dir, "test_euw", "viktor_middle")
    (report_dir / "report.json").write_text(
        json.dumps(
            {
                "champion": "Viktor",
                "champion_icon": "../../../assets/champions/Viktor.png",
            }
        ),
        encoding="utf-8",
    )

    response = client.get("/api/players/test_euw/builds/viktor_middle")

    assert response.status_code == 200
    assert response.json() == {
        "champion": "Viktor",
        "champion_icon": "/out/assets/champions/Viktor.png",
    }


def test_get_build_payload_404_when_missing(client: TestClient) -> None:
    assert client.get("/api/players/test_euw/builds/nonexistent").status_code == 404


def test_get_build_payload_rejects_path_traversal(client: TestClient) -> None:
    response = client.get("/api/players/test_euw/builds/%2e%2e")
    assert response.status_code == 400


def test_player_builds_expose_per_build_peers_ready(client: TestClient) -> None:
    ready = _write_report(
        client.web_config.output_dir,
        "test_euw",
        "viktor_middle",
        has_peer_comparison=True,
    )
    _write_report(
        client.web_config.output_dir,
        "test_euw",
        "fiora_top",
        champion="Fiora",
        role="TOP",
        role_display="top",
        build_label="Fiora top",
        has_peer_comparison=False,
    )
    # Legacy report: no meta flag, but peer export on disk.
    legacy = _write_report(
        client.web_config.output_dir,
        "test_euw",
        "ahri_middle",
        champion="Ahri",
        role="MIDDLE",
        role_display="mid",
        build_label="Ahri mid",
    )
    meta = json.loads((legacy / "meta.json").read_text(encoding="utf-8"))
    meta.pop("has_peer_comparison", None)
    (legacy / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    (legacy / "rank_comparison.csv").write_text("metric,you,peer\n", encoding="utf-8")

    builds = {
        build["slug"]: build
        for build in client.get("/api/players/test_euw").json()["builds"]
    }
    assert builds["viktor_middle"]["peers_ready"] is True
    assert builds["fiora_top"]["peers_ready"] is False
    assert builds["ahri_middle"]["peers_ready"] is True
    assert (ready / "meta.json").is_file()


def test_report_served_statically(client: TestClient) -> None:
    _write_report(client.web_config.output_dir, "test_euw", "viktor_middle")
    response = client.get("/out/reports/test_euw/viktor_middle/report.json")
    assert response.status_code == 200
    assert response.json()["champion"] == "Viktor"


def test_refresh_recovers_identity_from_disk(client: TestClient) -> None:
    _write_report(client.web_config.output_dir, "test_euw", "viktor_middle")
    response = client.post("/api/players/test_euw/refresh")
    assert response.status_code == 200
    body = response.json()
    assert body["job"]["kind"] == jobs.JOB_KIND_REFRESH
    assert body["job"]["state"] == jobs.QUEUED
    assert body["job"]["filter_champion"] is None
    assert body["job"]["filter_role"] is None

    assert client.post("/api/players/nobody/refresh").status_code == 404


def test_refresh_scopes_to_single_build(client: TestClient) -> None:
    _write_report(client.web_config.output_dir, "test_euw", "viktor_middle")
    _write_report(
        client.web_config.output_dir,
        "test_euw",
        "fiora_top",
        champion="Fiora",
        role="TOP",
        role_display="top",
        build_label="Fiora top",
    )
    response = client.post(
        "/api/players/test_euw/refresh",
        json={"champion": "fiora", "role": "top"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["created"] is True
    assert body["job"]["kind"] == jobs.JOB_KIND_REFRESH
    assert body["job"]["filter_champion"] == "Fiora"
    assert body["job"]["filter_role"] == "TOP"
    job = client.job_store.get(int(body["job"]["id"]))
    assert job is not None
    assert job["filter_champion"] == "Fiora"
    assert job["filter_role"] == "TOP"


def test_refresh_single_build_rejects_unknown_champion(client: TestClient) -> None:
    _write_report(client.web_config.output_dir, "test_euw", "viktor_middle")
    response = client.post(
        "/api/players/test_euw/refresh",
        json={"champion": "Ahri", "role": "MIDDLE"},
    )
    assert response.status_code == 404
    assert "Ahri" in response.json()["detail"]


def test_refresh_single_build_requires_both_fields(client: TestClient) -> None:
    _write_report(client.web_config.output_dir, "test_euw", "viktor_middle")
    response = client.post(
        "/api/players/test_euw/refresh",
        json={"champion": "Viktor"},
    )
    assert response.status_code == 422


def test_regenerate_queues_from_disk(client: TestClient) -> None:
    _write_report(client.web_config.output_dir, "test_euw", "viktor_middle")
    response = client.post("/api/players/test_euw/regenerate")
    assert response.status_code == 200
    body = response.json()
    assert body["job"]["kind"] == jobs.JOB_KIND_REGENERATE
    assert body["job"]["state"] == jobs.QUEUED

    assert client.post("/api/players/nobody/regenerate").status_code == 404


def test_player_page_renders(client: TestClient) -> None:
    """The SPA's player page hydrates from /api/players/{slug}; the refresh/regenerate/
    cancel actions it renders are driven by the /api/players/{slug}/refresh,
    /api/players/{slug}/regenerate and /api/jobs/{id}/cancel endpoints exercised
    elsewhere in this file.
    """
    _write_report(client.web_config.output_dir, "test_euw", "viktor_middle")
    response = client.get("/api/players/test_euw")
    assert response.status_code == 200
    body = response.json()
    assert body["slug"] == "test_euw"
    assert body["has_report"] is True
    assert body["builds"][0]["slug"] == "viktor_middle"


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


def test_chat_uses_tab_scoped_context_when_provided(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_report(client.web_config.output_dir, "test_euw", "viktor_middle")

    captured: dict[str, Any] = {}

    def fake_reply(api_key: str, **kwargs: Any) -> str:
        captured["stats"] = kwargs["stats"]
        return "Focus on your Career goals."

    monkeypatch.setattr(web_app, "gemini_reply", fake_reply)
    response = client.post(
        "/api/chat",
        json={
            "report": "test_euw/viktor_middle",
            "history": [{"role": "user", "parts": [{"text": "What's my next goal?"}]}],
            "tab": "career",
            "context": {"player": "Test", "career": {"has_career": True, "blocks": []}},
        },
    )
    assert response.status_code == 200
    assert captured["stats"] == {"player": "Test", "career": {"has_career": True, "blocks": []}}


def test_chat_falls_back_to_full_summary_when_context_too_large(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_report(client.web_config.output_dir, "test_euw", "viktor_middle")

    captured: dict[str, Any] = {}

    def fake_reply(api_key: str, **kwargs: Any) -> str:
        captured["stats"] = kwargs["stats"]
        return "ok"

    monkeypatch.setattr(web_app, "gemini_reply", fake_reply)
    response = client.post(
        "/api/chat",
        json={
            "report": "test_euw/viktor_middle",
            "history": [{"role": "user", "parts": [{"text": "hi"}]}],
            "context": {"filler": "x" * 30000},
        },
    )
    assert response.status_code == 200
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


def test_regenerate_recovers_group_from_player_label(client: TestClient) -> None:
    """Legacy CLI group metas only stored a comma-separated player label."""
    _write_report(
        client.web_config.output_dir,
        "alice_euw__bob_euw",
        "viktor_middle",
        player="Alice#EUW, Bob#EUW",
        riot_id="Alice",
        tagline="EUW",
    )
    # Stale registry row from an earlier buggy recovery (primary only).
    client.job_store.upsert_player(
        slug="alice_euw__bob_euw",
        riot_id="Alice",
        tagline="EUW",
        region="euw1",
        players=[{"riot_id": "Alice", "tagline": "EUW"}],
    )
    response = client.post("/api/players/alice_euw__bob_euw/regenerate")
    assert response.status_code == 200
    job = client.job_store.get(int(response.json()["job"]["id"]))
    assert job is not None
    assert job["players"] == [
        {"riot_id": "Alice", "tagline": "EUW"},
        {"riot_id": "Bob", "tagline": "EUW"},
    ]
    saved = client.job_store.get_player("alice_euw__bob_euw")
    assert saved is not None
    assert saved["players"] == job["players"]

    status = client.get("/api/players/alice_euw__bob_euw").json()
    assert status["player_label"] == "Alice#EUW, Bob#EUW"
    assert [player["label"] for player in status["players"]] == [
        "Alice#EUW",
        "Bob#EUW",
    ]

    page = client.get("/api/players/alice_euw__bob_euw")
    assert page.status_code == 200
    member_labels = [player["label"] for player in page.json()["players"]]
    assert member_labels == ["Alice#EUW", "Bob#EUW"]


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


# ---------------------------------------------------------- watch_mode (Task 6)


class _FakeWatcher:
    """Stand-in for WatchPoller that records start()/stop() calls only."""

    instances: list["_FakeWatcher"] = []

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.started = False
        self.stopped = False
        _FakeWatcher.instances.append(self)

    def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True


class _FakeWorker:
    """Stand-in for AnalysisWorker: no real threads, just start()/stop() calls."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass


def test_watch_mode_in_process_starts_the_watcher_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """watch_mode defaults to "in_process": create_app's lifespan must still start
    the monolith's own WatchPoller exactly as before this task."""
    monkeypatch.setattr(web_app, "_verify_players_exist", lambda *args, **kwargs: None)
    _FakeWatcher.instances = []
    monkeypatch.setattr(web_app, "WatchPoller", _FakeWatcher)
    monkeypatch.setattr(web_app, "AnalysisWorker", _FakeWorker)

    config = WebConfig(app_db_path=tmp_path / "app.sqlite", output_dir=tmp_path / "output")
    assert config.watch_mode == "in_process"
    application = web_app.create_app(config, start_worker=True)
    with TestClient(application):
        watcher = _FakeWatcher.instances[0]
        assert watcher.started is True
        assert watcher.stopped is False
    assert watcher.stopped is True


def test_watch_mode_grpc_skips_starting_the_watcher(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """watch_mode="grpc" must NOT start the in-process WatchPoller: CRON-watch is
    expected to poll the same app.sqlite externally instead. Starting both would
    race over watch_seen_json (see league_stats.cron_watch.service's docstring)."""
    monkeypatch.setattr(web_app, "_verify_players_exist", lambda *args, **kwargs: None)
    _FakeWatcher.instances = []
    monkeypatch.setattr(web_app, "WatchPoller", _FakeWatcher)
    monkeypatch.setattr(web_app, "AnalysisWorker", _FakeWorker)

    config = WebConfig(
        app_db_path=tmp_path / "app.sqlite",
        output_dir=tmp_path / "output",
        watch_mode="grpc",
    )
    application = web_app.create_app(config, start_worker=True)
    with TestClient(application):
        watcher = _FakeWatcher.instances[0]
        assert watcher.started is False
    assert watcher.stopped is False


def test_watch_mode_grpc_does_not_change_worker_startup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """watch_mode only gates the watcher; the analysis worker still starts/stops
    normally regardless of watch_mode."""
    monkeypatch.setattr(web_app, "_verify_players_exist", lambda *args, **kwargs: None)
    started: list[bool] = []
    stopped: list[bool] = []

    class RecordingWorker(_FakeWorker):
        def start(self) -> None:
            started.append(True)

        def stop(self) -> None:
            stopped.append(True)

    monkeypatch.setattr(web_app, "WatchPoller", _FakeWatcher)
    monkeypatch.setattr(web_app, "AnalysisWorker", RecordingWorker)

    config = WebConfig(
        app_db_path=tmp_path / "app.sqlite",
        output_dir=tmp_path / "output",
        watch_mode="grpc",
    )
    application = web_app.create_app(config, start_worker=True)
    with TestClient(application):
        assert started == [True]
    assert stopped == [True]


def test_watch_mode_default_does_not_start_watcher_when_start_worker_false(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """start_worker=False (as every other test in this module uses) must keep
    skipping the watcher entirely, regardless of watch_mode -- this task must not
    change that existing test-mode behavior."""
    monkeypatch.setattr(web_app, "_verify_players_exist", lambda *args, **kwargs: None)
    _FakeWatcher.instances = []
    monkeypatch.setattr(web_app, "WatchPoller", _FakeWatcher)
    monkeypatch.setattr(web_app, "AnalysisWorker", _FakeWorker)

    config = WebConfig(app_db_path=tmp_path / "app.sqlite", output_dir=tmp_path / "output")
    application = web_app.create_app(config, start_worker=False)
    with TestClient(application):
        watcher = _FakeWatcher.instances[0]
        assert watcher.started is False
    assert watcher.stopped is False
