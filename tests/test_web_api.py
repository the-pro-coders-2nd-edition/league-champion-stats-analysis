"""Tests for the FastAPI endpoints (worker disabled, no network)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Iterator

import pytest
from fastapi.testclient import TestClient

from league_stats_common.core.config import WebConfig
from league_stats_common.infra.report_store import open_report_store
import league_stats_api_ui.app as web_app
import league_stats_common.infra.jobs as jobs


def _write_report(output_dir: Path, slug: str, build_slug: str, **meta: Any) -> Path:
    """Seed a minimal report (listing metadata + body) in the Mongo-backed
    ReportStore, mirroring the old on-disk meta.json/report.json/summary.json
    fixture. Still creates ``output_dir/reports/{slug}/{build_slug}`` so
    tests that layer disk-only artifacts (e.g. ``rank_comparison.csv``, the
    ``account_views`` cache) on top of it have somewhere to write.
    """
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
    with open_report_store() as store:
        store.save_build(slug, build_slug, payload)
        store.save_body(
            slug,
            build_slug,
            report=payload,
            summary={"player": "Test#EUW", "build_label": "Viktor mid", "games": 42},
        )
    return report_dir


def _override_report_body(slug: str, build_slug: str, report: dict[str, Any]) -> None:
    """Replace just the saved report body (old ``report.json`` overwrite),
    keeping any previously saved summary intact."""
    with open_report_store() as store:
        summary = store.get_summary(slug, build_slug) or {}
        store.save_body(slug, build_slug, report=report, summary=summary)


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    # Skip Riot account-v1 lookup; dedicated tests cover the precheck path.
    monkeypatch.setattr(web_app, "_verify_players_exist", lambda *args, **kwargs: None)
    config = WebConfig(
        output_dir=tmp_path / "output",
        assets_dir=tmp_path / "assets",
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
    previews = by_slug["test_euw"]["preview_builds"]
    assert len(previews) == 1
    assert previews[0]["slug"] == "viktor_middle"
    assert previews[0]["champion"]
    assert previews[0]["games"] == 42


def test_landing_page_preview_builds_are_most_recent(client: TestClient) -> None:
    _write_report(
        client.web_config.output_dir, "test_euw", "ahri_middle",
        champion="Ahri", games=10, winrate=0.4,
        last_game_at="2026-08-10T10:00:00Z",
    )
    _write_report(
        client.web_config.output_dir, "test_euw", "viktor_middle",
        champion="Viktor", games=42, winrate=0.55,
        last_game_at="2026-08-01T10:00:00Z",
    )
    groups = client.get("/api/groups").json()["groups"]
    group = next(group for group in groups if group["slug"] == "test_euw")
    slugs = [build["slug"] for build in group["preview_builds"]]
    assert slugs[0] == "ahri_middle"
    assert "viktor_middle" in slugs


def test_landing_page_shows_profile_icons(client: TestClient) -> None:
    icon_dir = client.web_config.assets_dir / "profile_icons"
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
    assert icons == {"/ddragon/profile_icons/456.png", "/ddragon/profile_icons/789.png"}
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
        players: list[dict[str, str]], region: str, output_dir: Path, web_config: WebConfig
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
        players: list[dict[str, str]], region: str, output_dir: Path, web_config: WebConfig
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

    monkeypatch.setattr(
        web_app, "_build_precheck_client", lambda region, output_dir, web_config: FakeClient()
    )
    with pytest.raises(ValueError, match="Missing#EUW"):
        web_app._verify_players_exist(
            [
                {"riot_id": "Ok", "tagline": "EUW"},
                {"riot_id": "Missing", "tagline": "EUW"},
            ],
            "euw1",
            tmp_path,
            WebConfig(),
        )


def test_verify_players_exist_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FakeClient:
        def resolve_puuid(self, riot_id: str, tagline: str) -> str:
            return f"puuid-{riot_id}"

    monkeypatch.setattr(
        web_app, "_build_precheck_client", lambda region, output_dir, web_config: FakeClient()
    )
    web_app._verify_players_exist(
        [{"riot_id": "Alice", "tagline": "EUW"}],
        "euw1",
        tmp_path,
        WebConfig(),
    )


def test_build_mongo_client_reuses_the_same_client_for_the_same_uri() -> None:
    """Regression test for Phase 8's whole-branch review finding: `app.py`'s
    `_build_mongo_client` previously opened a brand new, never-closed
    `pymongo.MongoClient` on every call -- an unbounded connection-pool leak
    reached on every `POST /api/analyses`, every in-process watch-poll tick,
    and every hit of the per-champion refresh route. Mirrors
    `test_web_worker.py::test_build_mongo_client_reuses_the_same_client_for_the_same_uri`
    and `career_store.py`/`derived.py`/`jobs.py`'s own caching tests."""
    first = web_app._build_mongo_client("mongodb://localhost:27017/league_stats_shared_test")
    second = web_app._build_mongo_client("mongodb://localhost:27017/league_stats_shared_test")
    assert first is second


def test_build_mongo_client_returns_a_different_client_for_a_different_uri() -> None:
    first = web_app._build_mongo_client("mongodb://localhost:27017/league_stats_client_a")
    second = web_app._build_mongo_client("mongodb://localhost:27017/league_stats_client_b")
    assert first is not second


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

    with open_report_store() as store:
        assert store.get_report("test_euw", "viktor_middle") is not None
    status = client.get("/api/players/test_euw").json()
    assert status["has_report"] is True
    assert status["active_job"] is None


def test_player_status_serves_existing_reports(client: TestClient) -> None:
    _write_report(
        client.web_config.output_dir,
        "test_euw",
        "viktor_middle",
        score=72,
        score_color="var(--tone-good-fg)",
        score_verdict_label="Strength",
        last_game_at="2026-08-01T09:00:00Z",
    )
    response = client.get("/api/players/test_euw")
    assert response.status_code == 200
    body = response.json()
    assert body["has_report"] is True
    build = body["builds"][0]
    assert build["slug"] == "viktor_middle"
    assert build["href"] == "/out/reports/test_euw/viktor_middle/report.json"
    assert build["peers_ready"] is False
    assert build["score"] == 72
    assert build["last_game_at"] == "2026-08-01T09:00:00Z"
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


def test_player_status_reads_score_from_report_json(client: TestClient) -> None:
    """Older meta.json files omit score; hub cards read it from report.json."""
    _write_report(client.web_config.output_dir, "test_euw", "viktor_middle")
    _override_report_body(
        "test_euw",
        "viktor_middle",
        {
            "score": 63.8,
            "score_color": "var(--tone-good-fg)",
            "score_verdict_label": "Solid",
        },
    )
    build = client.get("/api/players/test_euw").json()["builds"][0]
    assert build["score"] == 63.8
    assert build["score_verdict_label"] == "Solid"


def test_player_status_includes_flex_rank(client: TestClient) -> None:
    rank_dir = client.web_config.output_dir / "assets" / "ranks"
    rank_dir.mkdir(parents=True, exist_ok=True)
    (rank_dir / "GOLD.png").write_bytes(b"png")
    (rank_dir / "PLATINUM.png").write_bytes(b"png")
    _write_report(
        client.web_config.output_dir,
        "test_euw",
        "viktor_middle",
        players=[
            {
                "riot_id": "Test",
                "tagline": "EUW",
                "solo_tier": "GOLD",
                "solo_rank": "IV",
                "solo_lp": 42,
                "flex_tier": "PLATINUM",
                "flex_rank": "II",
                "flex_lp": 31,
            }
        ],
    )
    player = client.get("/api/players/test_euw").json()["players"][0]
    assert player["solo_rank_division"] == "Gold IV"
    assert player["flex_rank_division"] == "Platinum II"
    assert player["solo_lp"] == 42
    assert player["flex_lp"] == 31


def test_player_status_merges_flex_from_store_when_meta_is_solo_only(
    client: TestClient,
) -> None:
    rank_dir = client.web_config.output_dir / "assets" / "ranks"
    rank_dir.mkdir(parents=True, exist_ok=True)
    (rank_dir / "GOLD.png").write_bytes(b"png")
    (rank_dir / "PLATINUM.png").write_bytes(b"png")
    _write_report(
        client.web_config.output_dir,
        "test_euw",
        "viktor_middle",
        players=[
            {
                "riot_id": "Test",
                "tagline": "EUW",
                "solo_tier": "GOLD",
                "solo_rank": "II",
                "solo_lp": 68,
            }
        ],
    )
    client.job_store.upsert_player(
        slug="test_euw",
        riot_id="Test",
        tagline="EUW",
        region="euw1",
        players=[
            {
                "riot_id": "Test",
                "tagline": "EUW",
                "solo_tier": "GOLD",
                "solo_rank": "II",
                "solo_lp": 68,
                "flex_tier": "PLATINUM",
                "flex_rank": "IV",
                "flex_lp": 12,
            }
        ],
    )

    player = client.get("/api/players/test_euw").json()["players"][0]

    assert player["solo_rank_division"] == "Gold II"
    assert player["solo_lp"] == 68
    assert player["flex_rank_division"] == "Platinum IV"
    assert player["flex_lp"] == 12


def test_player_status_hydrates_missing_flex(client: TestClient, monkeypatch) -> None:
    from league_stats_common.core.models import RankedEntry

    rank_dir = client.web_config.output_dir / "assets" / "ranks"
    rank_dir.mkdir(parents=True, exist_ok=True)
    (rank_dir / "DIAMOND.png").write_bytes(b"png")
    (rank_dir / "EMERALD.png").write_bytes(b"png")
    _write_report(
        client.web_config.output_dir,
        "meojifo_moc",
        "viktor_middle",
        players=[
            {
                "riot_id": "meojifo",
                "tagline": "moc",
                "solo_tier": "DIAMOND",
                "solo_rank": "IV",
                "solo_lp": 1,
            }
        ],
    )
    client.job_store.upsert_player(
        slug="meojifo_moc",
        riot_id="meojifo",
        tagline="moc",
        region="euw1",
        players=[
            {
                "riot_id": "meojifo",
                "tagline": "moc",
                "solo_tier": "DIAMOND",
                "solo_rank": "IV",
                "solo_lp": 1,
            }
        ],
    )

    class FakeClient:
        def resolve_puuid(self, riot_id: str, tagline: str) -> str:
            return "puuid-1"

        def fetch_ranked_queues(self, puuid: str) -> dict[str, RankedEntry]:
            return {
                "flex": RankedEntry(
                    tier="EMERALD",
                    rank="IV",
                    league_points=79,
                    wins=10,
                    losses=8,
                )
            }

    monkeypatch.setattr(
        web_app, "_build_precheck_client", lambda region, output_dir, web_config: FakeClient()
    )

    player = client.get("/api/players/meojifo_moc").json()["players"][0]

    assert player["solo_rank_division"] == "Diamond IV"
    assert player["flex_rank_division"] == "Emerald IV"
    assert player["flex_lp"] == 79
    saved = client.job_store.get_player("meojifo_moc")
    assert saved is not None
    assert saved["players"][0]["flex_tier"] == "EMERALD"


def test_hydrate_tracked_ranks_fetches_missing_flex(client: TestClient, monkeypatch) -> None:
    from league_stats_common.core.models import RankedEntry

    class FakeClient:
        def resolve_puuid(self, riot_id: str, tagline: str) -> str:
            return "puuid-1"

        def fetch_ranked_queues(self, puuid: str) -> dict[str, RankedEntry]:
            return {
                "flex": RankedEntry(
                    tier="PLATINUM",
                    rank="III",
                    league_points=22,
                    wins=10,
                    losses=8,
                )
            }

    monkeypatch.setattr(
        web_app, "_build_precheck_client", lambda region, output_dir, web_config: FakeClient()
    )

    tracked, changed = web_app._hydrate_tracked_ranks(
        [
            {
                "riot_id": "Test",
                "tagline": "EUW",
                "solo_tier": "DIAMOND",
                "solo_rank": "IV",
                "solo_lp": 68,
            }
        ],
        region="euw1",
        output_dir=client.web_config.output_dir,
        web_config=client.web_config,
    )

    assert changed is True
    assert tracked[0]["flex_tier"] == "PLATINUM"
    assert tracked[0]["flex_rank"] == "III"
    assert tracked[0]["flex_lp"] == 22


def test_get_build_payload_returns_report_json(client: TestClient) -> None:
    _write_report(client.web_config.output_dir, "test_euw", "viktor_middle")
    _override_report_body(
        "test_euw",
        "viktor_middle",
        {
            "champion": "Viktor",
            "champion_icon": "../../../assets/champions/Viktor.png",
        },
    )

    response = client.get("/api/players/test_euw/builds/viktor_middle")

    assert response.status_code == 200
    assert response.json() == {
        "champion": "Viktor",
        "champion_icon": "/ddragon/champions/Viktor.png",
    }


def test_get_build_payload_404_when_missing(client: TestClient) -> None:
    assert client.get("/api/players/test_euw/builds/nonexistent").status_code == 404


def test_get_build_payload_rejects_path_traversal(client: TestClient) -> None:
    response = client.get("/api/players/test_euw/builds/%2e%2e")
    assert response.status_code == 400


def test_player_builds_expose_per_build_peers_ready(client: TestClient) -> None:
    _write_report(
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
    (legacy / "rank_comparison.csv").write_text("metric,you,peer\n", encoding="utf-8")

    builds = {
        build["slug"]: build
        for build in client.get("/api/players/test_euw").json()["builds"]
    }
    assert builds["viktor_middle"]["peers_ready"] is True
    assert builds["fiora_top"]["peers_ready"] is False
    assert builds["ahri_middle"]["peers_ready"] is True
    with open_report_store() as store:
        assert store.has_build("test_euw", "viktor_middle")


def test_report_served_statically(client: TestClient) -> None:
    """The `/out` static mount still serves any file placed under output_dir,
    though nothing report-shaped is written there anymore -- this exercises
    the mount itself, independent of the Mongo-backed report body."""
    report_dir = client.web_config.output_dir / "reports" / "test_euw" / "viktor_middle"
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "report.json").write_text(
        json.dumps({"champion": "Viktor"}), encoding="utf-8"
    )
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


# ------------------------------------------------------ start_worker gating


class _FakeWorker:
    """Stand-in for AnalysisWorker: no real threads, just start()/stop() calls."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass


def test_start_worker_controls_analysis_worker_startup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`start_worker` is now the only thing gating background-task startup in
    `create_app`'s lifespan: api-ui no longer runs its own watcher (CronWatch's
    always-on WatchPoller is the sole source of watch detection now), so
    `AnalysisWorker` is the only remaining thing to assert on here."""
    monkeypatch.setattr(web_app, "_verify_players_exist", lambda *args, **kwargs: None)
    started: list[bool] = []
    stopped: list[bool] = []

    class RecordingWorker(_FakeWorker):
        def start(self) -> None:
            started.append(True)

        def stop(self) -> None:
            stopped.append(True)

    monkeypatch.setattr(web_app, "AnalysisWorker", RecordingWorker)
    config = WebConfig(output_dir=tmp_path / "output", assets_dir=tmp_path / "assets")

    application = web_app.create_app(config, start_worker=True)
    with TestClient(application):
        assert started == [True]
    assert stopped == [True]

    started.clear()
    stopped.clear()
    application = web_app.create_app(config, start_worker=False)
    with TestClient(application):
        assert started == []
    assert stopped == []


# ---------------------------------------------------------------- SSE routes
#
# `TestClient.stream()` (this repo's pinned `httpx` 0.28.1) cannot exercise these
# routes: its `ASGITransport.handle_async_request` awaits the whole ASGI app call
# to completion *before returning anything at all* (verified directly by reading
# `httpx/_transports/asgi.py`) -- fine for a request/response body, but an SSE
# route's generator only ever ends on disconnect, so that `await` never resolves
# and the test process hangs forever (reproduced directly while writing these
# tests). `_ASGIStream`/`_asgi_request` below drive the app's ASGI callable
# directly instead, so a test can read `http.response.body` messages as they're
# sent and explicitly cancel the request task to simulate a client disconnect --
# each test also drives its own `lifespan_context` on one asyncio loop (rather
# than using the module's `client` fixture, which runs its `TestClient` on a
# separate thread/loop -- `job_event_bus.bind_loop()` must bind the *same* loop
# these tests await on, or `publish()`'s `call_soon_threadsafe` would target the
# wrong loop).


async def _asgi_request(
    app: Any, method: str, path: str, *, json_body: dict[str, Any] | None = None
) -> tuple[int, Any]:
    """One buffered (non-streaming) ASGI request/response round trip."""
    body_bytes = b"" if json_body is None else json.dumps(json_body).encode()
    headers = [(b"content-type", b"application/json")] if json_body is not None else []
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": headers,
        "scheme": "http",
        "server": ("testserver", 80),
        "client": ("testclient", 123),
        "root_path": "",
    }
    sent = False

    async def receive() -> dict[str, Any]:
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": body_bytes, "more_body": False}

    result: dict[str, Any] = {}
    chunks: list[bytes] = []

    async def send(message: dict[str, Any]) -> None:
        if message["type"] == "http.response.start":
            result["status"] = message["status"]
        elif message["type"] == "http.response.body":
            chunks.append(message.get("body", b""))

    await app(scope, receive, send)
    raw = b"".join(chunks)
    return result["status"], (json.loads(raw) if raw else None)


class _ASGIStream:
    """Drives one SSE ASGI request against `app`, exposing `data:` events live."""

    def __init__(self, app: Any, path: str) -> None:
        self._app = app
        self._path = path
        self.queue: asyncio.Queue[bytes] = asyncio.Queue()
        self.status: int | None = None
        self._task: asyncio.Task[None] | None = None
        self._headers_ready = asyncio.Event()

    async def __aenter__(self) -> "_ASGIStream":
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "path": self._path,
            "raw_path": self._path.encode(),
            "query_string": b"",
            "headers": [],
            "scheme": "http",
            "server": ("testserver", 80),
            "client": ("testclient", 123),
            "root_path": "",
        }

        async def receive() -> dict[str, Any]:
            # A real disconnect is simulated by cancelling `self._task` instead
            # (see `__aexit__`) -- this just needs to never resolve on its own.
            await asyncio.sleep(3600)
            return {"type": "http.disconnect"}  # pragma: no cover

        async def send(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                self.status = message["status"]
                self._headers_ready.set()
            elif message["type"] == "http.response.body":
                body = message.get("body", b"")
                if body:
                    await self.queue.put(body)

        self._task = asyncio.create_task(self._app(scope, receive, send))
        headers_wait = asyncio.ensure_future(self._headers_ready.wait())
        await asyncio.wait({self._task, headers_wait}, return_when=asyncio.FIRST_COMPLETED)
        if self._task.done():
            headers_wait.cancel()
            await self._task  # surfaces a short-circuit path's exception, if any
        return self

    async def next_event(self, timeout: float = 2.0) -> dict[str, Any]:
        chunk = await asyncio.wait_for(self.queue.get(), timeout=timeout)
        for line in chunk.decode().splitlines():
            if line.startswith("data:"):
                return json.loads(line[len("data:") :].strip())
        raise AssertionError(f"no data: line in chunk {chunk!r}")

    async def __aexit__(self, *exc_info: object) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass


def _run_app_scenario(app: Any, scenario: Any) -> Any:
    """Run `scenario(app)` inside the app's lifespan, on one fresh asyncio loop."""

    async def wrapper() -> Any:
        async with app.router.lifespan_context(app):
            return await scenario(app)

    return asyncio.run(wrapper())


def _sse_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **config_kwargs: Any) -> Any:
    monkeypatch.setattr(web_app, "_verify_players_exist", lambda *args, **kwargs: None)
    config = WebConfig(output_dir=tmp_path / "output", gemini_api_key="fake-key", **config_kwargs)
    application = web_app.create_app(config, start_worker=False)
    application.state.web_config = config  # mirrors the `client` fixture's own attribute
    return application


def test_player_status_events_sends_initial_snapshot_immediately(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _sse_app(tmp_path, monkeypatch)
    _write_report(app.state.web_config.output_dir, "test_euw", "viktor_middle")

    async def scenario(app: Any) -> None:
        _status, expected = await _asgi_request(app, "GET", "/api/players/test_euw")
        async with _ASGIStream(app, "/api/players/test_euw/events") as stream:
            assert stream.status == 200
            first = await stream.next_event()
        assert first == expected

    _run_app_scenario(app, scenario)


def test_player_status_events_404s_for_an_unknown_player(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _sse_app(tmp_path, monkeypatch)

    async def scenario(app: Any) -> None:
        async with _ASGIStream(app, "/api/players/unknown_player/events") as stream:
            assert stream.status == 404

    _run_app_scenario(app, scenario)


def test_player_status_events_pushes_a_fresh_snapshot_on_state_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _sse_app(tmp_path, monkeypatch)

    async def scenario(app: Any) -> None:
        _status, submitted = await _asgi_request(
            app, "POST", "/api/analyses", json_body={"riot_id": "Test#EUW", "region": "euw1"}
        )
        job_id = submitted["job"]["id"]

        async with _ASGIStream(app, "/api/players/test_euw/events") as stream:
            first = await stream.next_event()
            assert first["active_job"]["state"] == jobs.QUEUED

            app.state.job_store.set_state(job_id, jobs.ANALYZING, detail="Downloading matches")

            second = await stream.next_event()
            assert second["active_job"]["state"] == jobs.ANALYZING
            assert second["active_job"]["stage_detail"] == "Downloading matches"

    _run_app_scenario(app, scenario)


def test_player_status_events_cleans_up_subscriber_on_disconnect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _sse_app(tmp_path, monkeypatch)
    _write_report(app.state.web_config.output_dir, "test_euw", "viktor_middle")

    async def scenario(app: Any) -> None:
        bus = app.state.job_event_bus
        assert bus.subscriber_count("test_euw") == 0
        async with _ASGIStream(app, "/api/players/test_euw/events") as stream:
            await stream.next_event()
            assert bus.subscriber_count("test_euw") == 1
        # Cleanup runs in the cancelled task's `finally:` block, awaited by
        # `_ASGIStream.__aexit__` -- so it has already happened by this point.
        assert bus.subscriber_count("test_euw") == 0

    _run_app_scenario(app, scenario)


def test_player_status_events_single_flight_avoids_double_consuming_welcome_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two tabs open on the same slug must both see one welcome-back delivery.

    `WelcomeBackCache.get` is consume-on-read -- without the single-flight
    wrapper around `_player_status_payload` in the SSE code path, the second
    subscriber to independently recompute its own payload for the same publish
    would get `welcome_back: None` where the first got the real payload.
    """
    app = _sse_app(tmp_path, monkeypatch)
    _write_report(app.state.web_config.output_dir, "test_euw", "viktor_middle")

    async def scenario(app: Any) -> None:
        cache = app.state.welcome_back_cache
        bus = app.state.job_event_bus

        async with _ASGIStream(app, "/api/players/test_euw/events") as first_stream:
            async with _ASGIStream(app, "/api/players/test_euw/events") as second_stream:
                await first_stream.next_event()
                await second_stream.next_event()

                welcome_back_data = {
                    "new_match_id": "EUW1_42",
                    "match_summary": {"win": True},
                    "detected_at_unix": 1_700_000_000,
                }
                cache.record("test_euw", welcome_back_data)
                bus.publish("test_euw")

                first_payload = await first_stream.next_event()
                second_payload = await second_stream.next_event()

        assert first_payload["welcome_back"] == welcome_back_data
        assert second_payload["welcome_back"] == welcome_back_data

    _run_app_scenario(app, scenario)


def test_activity_events_sends_initial_snapshot_immediately(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _sse_app(tmp_path, monkeypatch)

    async def scenario(app: Any) -> None:
        _status, submitted = await _asgi_request(
            app, "POST", "/api/analyses", json_body={"riot_id": "New#EUW", "region": "euw1"}
        )
        assert submitted["created"] is True
        _status, expected = await _asgi_request(app, "GET", "/api/activity")

        async with _ASGIStream(app, "/api/activity/events") as stream:
            assert stream.status == 200
            first = await stream.next_event()
        assert first == expected

    _run_app_scenario(app, scenario)


def test_activity_events_pushes_on_a_newly_submitted_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _sse_app(tmp_path, monkeypatch)

    async def scenario(app: Any) -> None:
        async with _ASGIStream(app, "/api/activity/events") as stream:
            first = await stream.next_event()
            assert first["items"] == []

            await _asgi_request(
                app, "POST", "/api/analyses", json_body={"riot_id": "New#EUW", "region": "euw1"}
            )

            second = await stream.next_event()
            assert any(item["slug"] == "new_euw" for item in second["items"])

    _run_app_scenario(app, scenario)


def test_objectid_is_json_serializable_via_fastapi_encoders() -> None:
    """Defensive: any future route that accidentally forwards a raw Mongo
    document (containing a real ObjectId `_id`, post-migration) must not
    500 on JSON serialization. FastAPI's jsonable_encoder has no built-in
    support for ObjectId; app.py must register one explicitly."""
    from bson import ObjectId
    from fastapi.encoders import jsonable_encoder

    encoded = jsonable_encoder({"_id": ObjectId(), "name": "test"})
    assert isinstance(encoded["_id"], str)


# ------------------------- lazy peer-comparison refresh on report read


def _stale_peer_comparison(**overrides: Any) -> dict[str, Any]:
    """A `fallback_level >= 2` peer comparison -- resolved via PEERS' live
    cache/SamplingTask path, so it's eligible for the lazy refresh check
    (see design "peers-scheduling-and-cleanup" RFC, lazy-refresh section)."""
    peer = {
        "rank_label": "Emerald III",
        "tier": "EMERALD",
        "rank_badge": "III",
        "champion": "Aatrox",
        "role": "TOP",
        "build_label": "Aatrox top",
        "source": "live sample",
        "peer_games": 20,
        "peer_players": 15,
        "confidence": "low",
        "fallback_level": 2,
        "comparisons": [],
        "strengths": [],
        "weaknesses": [],
        "platform": "euw1",
        "patch": "16.16",
    }
    peer.update(overrides)
    return peer


def test_report_read_patches_a_stale_peer_comparison_from_peek_baseline(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A report whose stored peer comparison is not already maximally
    confident (`fallback_level >= 2`) must be refreshed from PEEK-ed
    live-cache data on read, if PEERS reports something better is now
    available."""
    monkeypatch.setattr(web_app, "_last_peek_at", {})
    _write_report(client.web_config.output_dir, "test_euw", "viktor_middle")
    _override_report_body(
        "test_euw",
        "viktor_middle",
        {
            "champion": "Viktor",
            "peer_comparison": _stale_peer_comparison(),
        },
    )

    class FakeStub:
        def PeekBaseline(self, request, timeout=None):
            return web_app.peers_pb2.PeekBaselineResponse(
                found=True,
                baseline_json=json.dumps(_stale_peer_comparison(peer_games=70, peer_players=40, source="improved")),
                still_refining=True,
            )

    monkeypatch.setattr(web_app, "_peers_stub", lambda: FakeStub())

    response = client.get("/api/players/test_euw/builds/viktor_middle")

    assert response.status_code == 200
    body = response.json()
    assert body["peer_comparison"]["peer_games"] == 70
    assert body["peer_comparison"]["source"] == "improved"

    with open_report_store() as store:
        stored = store.get_report("test_euw", "viktor_middle")
    assert stored["peer_comparison"]["peer_games"] == 70


def test_report_read_skips_peek_for_already_high_confidence_peer_comparison(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A report whose peer comparison already has `fallback_level < 2` must
    never call PeekBaseline at all -- levels 0/1 come from the persistent
    peer_games store, not a SamplingTask/live cache, and cannot improve via
    this mechanism."""
    monkeypatch.setattr(web_app, "_last_peek_at", {})
    _write_report(client.web_config.output_dir, "test_euw", "viktor_middle")
    _override_report_body(
        "test_euw",
        "viktor_middle",
        {
            "champion": "Viktor",
            "peer_comparison": _stale_peer_comparison(confidence="high", fallback_level=0),
        },
    )

    called: list[Any] = []

    class FakeStub:
        def PeekBaseline(self, request, timeout=None):
            called.append(request)
            raise AssertionError("PeekBaseline must not be called for a fallback_level < 2 report")

    monkeypatch.setattr(web_app, "_peers_stub", lambda: FakeStub())

    response = client.get("/api/players/test_euw/builds/viktor_middle")

    assert response.status_code == 200
    assert called == []


def test_report_read_rate_limits_repeated_peeks_for_the_same_build(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A second read of the same stale-peer-comparison report within
    `_PEEK_RATE_LIMIT_S` must not fire a second PeekBaseline RPC -- protects
    against an RPC storm on a popular report that gets viewed/polled
    frequently."""
    monkeypatch.setattr(web_app, "_last_peek_at", {})
    _write_report(client.web_config.output_dir, "test_euw", "viktor_middle")
    _override_report_body(
        "test_euw",
        "viktor_middle",
        {
            "champion": "Viktor",
            "peer_comparison": _stale_peer_comparison(),
        },
    )

    calls: list[Any] = []

    class FakeStub:
        def PeekBaseline(self, request, timeout=None):
            calls.append(request)
            return web_app.peers_pb2.PeekBaselineResponse(found=False)

    monkeypatch.setattr(web_app, "_peers_stub", lambda: FakeStub())

    first = client.get("/api/players/test_euw/builds/viktor_middle")
    second = client.get("/api/players/test_euw/builds/viktor_middle")

    assert first.status_code == 200
    assert second.status_code == 200
    assert len(calls) == 1
