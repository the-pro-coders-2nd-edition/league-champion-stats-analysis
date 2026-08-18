"""Group watch: detection, dedup, budget, backoff, and the API surface."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from league_stats.core.config import WebConfig
from league_stats.web.app import create_app
from league_stats.web.jobs import JOB_KIND_REFRESH, JobStore
from league_stats.web.watch import _Budget, WatchPoller, _backoff_for

SLUG = "hugros_euw"


class FakeClient:
    """Minimal stand-in for the Riot client the poller needs."""

    def __init__(self, newest: dict[str, list[str]] | None = None) -> None:
        self.newest = newest or {}
        self.match_id_calls = 0
        self.fail = False

    def resolve_puuid(self, riot_id: str, tagline: str) -> str:
        return f"puuid-{riot_id.lower()}"

    def fetch_ranked_match_ids(self, puuid: str, count: int) -> list[str]:
        self.match_id_calls += 1
        if self.fail:
            raise RuntimeError("riot is down")
        return self.newest.get(puuid, [])


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
    yield handle
    handle.close()


class Clock:
    def __init__(self) -> None:
        self.t = 1_000.0

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


def _poller(store: JobStore, client: FakeClient, clock: Clock) -> WatchPoller:
    return WatchPoller(store, lambda region: client, now=clock)


def _tick(poller: WatchPoller) -> list[str]:
    """Drive one poll synchronously (the repo has no pytest-asyncio)."""
    return asyncio.run(poller.tick())


# ------------------------------------------------------------------ store layer


def test_watch_defaults_to_off(store: JobStore) -> None:
    row = store.get_player(SLUG)
    assert row is not None
    assert not row["watch_enabled"]
    assert row["watch_interval_s"] == 180


def test_set_watch_toggles_and_floors_the_interval(store: JobStore) -> None:
    assert store.set_watch(SLUG, enabled=True, interval_s=10)
    row = store.get_player(SLUG)
    assert row["watch_enabled"] == 1
    assert row["watch_interval_s"] == 60

    assert store.set_watch(SLUG, enabled=False)
    assert store.get_player(SLUG)["watch_enabled"] == 0


def test_set_watch_on_unknown_slug(store: JobStore) -> None:
    assert store.set_watch("nobody", enabled=True) is False


def test_list_watched_only_returns_watched(store: JobStore) -> None:
    assert store.list_watched_players() == []
    store.set_watch(SLUG, enabled=True)
    assert [row["slug"] for row in store.list_watched_players()] == [SLUG]


# ---------------------------------------------------------------------- poller


def test_first_tick_only_records_a_baseline(store: JobStore) -> None:
    store.set_watch(SLUG, enabled=True)
    client = FakeClient({"puuid-hugros": ["EUW1_1"]})
    clock = Clock()

    refreshed = _tick(_poller(store, client, clock))

    assert refreshed == [], "the first look must not look like a new game"
    row = store.get_player(SLUG)
    assert row["last_watch_at"] is not None
    assert store.list_active_jobs() == []


def test_a_new_match_id_enqueues_a_refresh(store: JobStore) -> None:
    store.set_watch(SLUG, enabled=True, interval_s=60)
    client = FakeClient({"puuid-hugros": ["EUW1_1"]})
    clock = Clock()
    poller = _poller(store, client, clock)

    _tick(poller)  # baseline
    client.newest["puuid-hugros"] = ["EUW1_2"]
    clock.advance(120)
    refreshed = _tick(poller)

    assert refreshed == [SLUG]
    jobs = store.list_active_jobs()
    assert len(jobs) == 1
    assert jobs[0]["kind"] == JOB_KIND_REFRESH


def test_no_new_match_id_does_nothing(store: JobStore) -> None:
    store.set_watch(SLUG, enabled=True, interval_s=60)
    client = FakeClient({"puuid-hugros": ["EUW1_1"]})
    clock = Clock()
    poller = _poller(store, client, clock)

    _tick(poller)
    clock.advance(120)

    assert _tick(poller) == []
    assert store.list_active_jobs() == []


def test_an_active_job_blocks_enqueueing(store: JobStore) -> None:
    store.set_watch(SLUG, enabled=True, interval_s=60)
    client = FakeClient({"puuid-hugros": ["EUW1_1"]})
    clock = Clock()
    poller = _poller(store, client, clock)
    _tick(poller)

    store.enqueue(
        kind=JOB_KIND_REFRESH,
        riot_id="Hugros",
        tagline="EUW",
        region="euw1",
        player_slug=SLUG,
    )
    before = client.match_id_calls
    client.newest["puuid-hugros"] = ["EUW1_2"]
    clock.advance(120)

    assert _tick(poller) == []
    assert client.match_id_calls == before, "should not even spend an API call"


def test_a_group_is_not_checked_before_its_interval(store: JobStore) -> None:
    store.set_watch(SLUG, enabled=True, interval_s=600)
    client = FakeClient({"puuid-hugros": ["EUW1_1"]})
    clock = Clock()
    poller = _poller(store, client, clock)

    _tick(poller)
    calls = client.match_id_calls
    clock.advance(60)
    _tick(poller)

    assert client.match_id_calls == calls, "interval not elapsed"


def test_an_api_failure_backs_off_and_is_surfaced(store: JobStore) -> None:
    store.set_watch(SLUG, enabled=True, interval_s=60)
    client = FakeClient({"puuid-hugros": ["EUW1_1"]})
    clock = Clock()
    poller = _poller(store, client, clock)
    _tick(poller)

    client.fail = True
    clock.advance(120)
    _tick(poller)

    row = store.get_player(SLUG)
    assert "riot is down" in row["last_watch_error"]

    # Backoff: the next attempt is deferred beyond the plain interval.
    calls = client.match_id_calls
    clock.advance(61)
    _tick(poller)
    assert client.match_id_calls == calls


def test_unwatched_groups_are_never_polled(store: JobStore) -> None:
    client = FakeClient({"puuid-hugros": ["EUW1_1"]})
    assert _tick(_poller(store, client, Clock())) == []
    assert client.match_id_calls == 0


def test_budget_caps_calls_inside_the_window() -> None:
    budget = _Budget(window_s=120.0, limit=3)
    assert [budget.take(0.0) for _ in range(4)] == [True, True, True, False]
    # A later window frees the allowance again.
    assert budget.take(200.0) is True


def test_backoff_grows_then_caps() -> None:
    assert _backoff_for(0) == 0
    assert _backoff_for(1) < _backoff_for(2) < _backoff_for(3)
    assert _backoff_for(50) == _backoff_for(60)


def test_budget_exhaustion_defers_instead_of_failing(store: JobStore) -> None:
    store.set_watch(SLUG, enabled=True, interval_s=60)
    client = FakeClient({"puuid-hugros": ["EUW1_1"]})
    poller = WatchPoller(
        store, lambda region: client, now=Clock(), budget=_Budget(limit=0)
    )

    assert _tick(poller) == []
    assert client.match_id_calls == 0


# ------------------------------------------------------------------------- API


@pytest.fixture()
def client(tmp_path: Path):
    config = WebConfig(
        output_dir=tmp_path / "out", app_db_path=tmp_path / "app.sqlite"
    )
    app = create_app(config, start_worker=False)
    with TestClient(app) as handle:
        app.state.job_store.upsert_player(
            slug=SLUG,
            riot_id="Hugros",
            tagline="EUW",
            region="euw1",
            players=[{"riot_id": "Hugros", "tagline": "EUW"}],
        )
        yield handle


def test_watch_routes_toggle_state(client: TestClient) -> None:
    enabled = client.post(f"/api/players/{SLUG}/watch", json={"interval_s": 300})
    assert enabled.status_code == 200
    assert enabled.json()["watch_enabled"] is True
    assert enabled.json()["watch_interval_s"] == 300

    disabled = client.delete(f"/api/players/{SLUG}/watch")
    assert disabled.status_code == 200
    assert disabled.json()["watch_enabled"] is False


def test_watch_route_rejects_a_too_fast_interval(client: TestClient) -> None:
    response = client.post(f"/api/players/{SLUG}/watch", json={"interval_s": 5})
    assert response.status_code == 422


def test_watch_routes_404_on_unknown_player(client: TestClient) -> None:
    assert client.post("/api/players/nobody/watch").status_code == 404
    assert client.delete("/api/players/nobody/watch").status_code == 404


def test_career_banner_ack_route(tmp_path: Path) -> None:
    """A reader can retire a Career banner that watch rebuilds must not eat."""
    import json

    from league_stats.core.champions import player_slug
    from league_stats.core.config import load_config
    from league_stats.infra.career_store import CareerStore, build_key as career_build_key

    config = WebConfig(output_dir=tmp_path / "out", app_db_path=tmp_path / "app.sqlite")
    build_dir = config.reports_dir / SLUG / "viktor_middle"
    build_dir.mkdir(parents=True)
    (build_dir / "meta.json").write_text(
        json.dumps(
            {
                "champion": "Viktor",
                "role": "MIDDLE",
                "riot_id": "Hugros",
                "tagline": "EUW",
                "region": "europe",
            }
        ),
        encoding="utf-8",
    )

    app_config = load_config(
        riot_id="Hugros", tagline="EUW", region="europe", output_dir=config.output_dir
    )
    key = career_build_key(player_slug("Hugros", "EUW"), "Viktor", "MIDDLE")
    with CareerStore(app_config.career_db_path) as career:
        career.set_pending_congrats(key, "laning_income")

    app = create_app(config, start_worker=False)
    with TestClient(app) as handle:
        response = handle.post(f"/api/players/{SLUG}/builds/viktor_middle/career/ack")
        assert response.status_code == 200
        assert response.json() == {"acknowledged": True}

    with CareerStore(app_config.career_db_path) as career:
        assert career.peek_pending_congrats(key) == ""


def test_career_banner_ack_404s_on_unknown_build(tmp_path: Path) -> None:
    config = WebConfig(output_dir=tmp_path / "out", app_db_path=tmp_path / "app.sqlite")
    app = create_app(config, start_worker=False)
    with TestClient(app) as handle:
        assert (
            handle.post(f"/api/players/{SLUG}/builds/nope_mid/career/ack").status_code
            == 404
        )
