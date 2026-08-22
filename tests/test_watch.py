"""Group watch: detection, dedup, budget, backoff, and the API surface."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import mongomock
import pytest
from fastapi.testclient import TestClient

from league_stats_common.core.config import RANKED_FLEX_QUEUE_ID, RANKED_SOLO_QUEUE_ID, WebConfig
from league_stats_api_ui.app import create_app
from league_stats_common.infra.jobs import JOB_KIND_REFRESH, JobStore
import league_stats_cron_watch.watch as watch_module
from league_stats_cron_watch.watch import _Budget, WatchPoller, _backoff_for
from tests.fixtures import make_player_match

SLUG = "hugros_euw"


class FakeClient:
    """Minimal stand-in for the Riot client the poller needs.

    ``newest`` maps ``puuid -> {queue_id: [match_ids]}`` so tests can control
    the solo and flex queues independently. ``matches`` maps ``match_id ->
    raw match-v5 document`` so tests can control what ``fetch_match`` returns
    for the welcome-back summary; unregistered ids fall back to a generic
    synthetic match (see ``tests/fixtures.py``) rather than raising, since
    most tests here don't care about summary content.
    """

    def __init__(
        self,
        newest: dict[str, dict[int, list[str]]] | None = None,
        matches: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        self.newest = newest or {}
        self.matches = matches or {}
        self.match_id_calls = 0
        self.use_cache_calls: list[bool] = []
        self.fetch_match_calls: list[str] = []
        self.fail = False

    def resolve_puuid(self, riot_id: str, tagline: str) -> str:
        return f"puuid-{riot_id.lower()}"

    def fetch_match_ids(
        self, puuid: str, count: int, *, queue_id: int, use_cache: bool = True
    ) -> list[str]:
        self.match_id_calls += 1
        self.use_cache_calls.append(use_cache)
        if self.fail:
            raise RuntimeError("riot is down")
        return self.newest.get(puuid, {}).get(queue_id, [])[:count]

    def fetch_match(self, match_id: str) -> dict[str, Any]:
        self.fetch_match_calls.append(match_id)
        return self.matches.get(match_id) or make_player_match(match_id)


@pytest.fixture()
def store(tmp_path: Path):
    handle = JobStore(mongomock.MongoClient())
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
    assert row["watch_interval_s"] == 60


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
    client = FakeClient({"puuid-hugros": {RANKED_SOLO_QUEUE_ID: ["EUW1_1"]}})
    clock = Clock()

    refreshed = _tick(_poller(store, client, clock))

    assert refreshed == [], "the first look must not look like a new game"
    row = store.get_player(SLUG)
    assert row["last_watch_at"] is not None
    assert store.list_active_jobs() == []


def test_a_new_match_id_enqueues_a_refresh(store: JobStore) -> None:
    store.set_watch(SLUG, enabled=True, interval_s=60)
    client = FakeClient({"puuid-hugros": {RANKED_SOLO_QUEUE_ID: ["EUW1_1"]}})
    clock = Clock()
    poller = _poller(store, client, clock)

    _tick(poller)  # baseline
    client.newest["puuid-hugros"] = {RANKED_SOLO_QUEUE_ID: ["EUW1_2"]}
    clock.advance(120)
    refreshed = _tick(poller)

    assert refreshed == [SLUG]
    jobs = store.list_active_jobs()
    assert len(jobs) == 1
    assert jobs[0]["kind"] == JOB_KIND_REFRESH


def test_a_new_match_id_mints_a_trace_id_for_the_enqueued_refresh(store: JobStore) -> None:
    """`WatchPoller` is self-driven (an internal asyncio.Task, not tied to any
    incoming request or RPC), so it never inherits a trace_id -- it must mint
    one at enqueue time (Phase 6 final review, Finding 1) rather than leaving
    the job's `trace_id` column empty, the same way the gRPC server
    interceptor mints one for a call with no upstream trace_id."""
    from league_stats_common.utils import set_trace_id

    set_trace_id("")
    store.set_watch(SLUG, enabled=True, interval_s=60)
    client = FakeClient({"puuid-hugros": {RANKED_SOLO_QUEUE_ID: ["EUW1_1"]}})
    clock = Clock()
    poller = _poller(store, client, clock)

    _tick(poller)  # baseline
    client.newest["puuid-hugros"] = {RANKED_SOLO_QUEUE_ID: ["EUW1_2"]}
    clock.advance(120)
    _tick(poller)

    jobs = store.list_active_jobs()
    assert len(jobs) == 1
    assert jobs[0]["trace_id"]
    assert len(jobs[0]["trace_id"]) == 32
    int(jobs[0]["trace_id"], 16)


def test_a_new_flex_match_id_enqueues_a_refresh(store: JobStore) -> None:
    """A player's solo queue is unchanged but their flex queue has a new game.

    Regression test: the merged ``fetch_ranked_match_ids`` list always sorted
    solo first, so comparing only its first element made flex games invisible
    to watch.
    """
    store.set_watch(SLUG, enabled=True, interval_s=60)
    client = FakeClient(
        {"puuid-hugros": {RANKED_SOLO_QUEUE_ID: ["EUW1_1"], RANKED_FLEX_QUEUE_ID: ["EUW1_F1"]}}
    )
    clock = Clock()
    poller = _poller(store, client, clock)

    _tick(poller)  # baseline
    client.newest["puuid-hugros"][RANKED_FLEX_QUEUE_ID] = ["EUW1_F2"]
    clock.advance(120)
    refreshed = _tick(poller)

    assert refreshed == [SLUG]
    jobs = store.list_active_jobs()
    assert len(jobs) == 1
    assert jobs[0]["kind"] == JOB_KIND_REFRESH


def test_watch_detection_bypasses_the_http_cache(store: JobStore) -> None:
    """Detection must read live data; a 15-minute HTTP cache would make the
    configured watch_interval_s a lie."""
    store.set_watch(SLUG, enabled=True, interval_s=60)
    client = FakeClient({"puuid-hugros": {RANKED_SOLO_QUEUE_ID: ["EUW1_1"]}})
    _tick(_poller(store, client, Clock()))

    assert client.use_cache_calls
    assert all(used is False for used in client.use_cache_calls)


def test_legacy_flat_watch_seen_is_migrated_without_crashing(store: JobStore) -> None:
    """A pre-per-queue-tracking ``watch_seen_json`` row (``{puuid: match_id}``)
    must not crash the poller; it is treated as an empty baseline."""
    store.set_watch(SLUG, enabled=True, interval_s=60)
    store.record_watch_tick(SLUG, seen={"puuid-hugros": "EUW1_LEGACY"}, at=1_000.0)
    client = FakeClient({"puuid-hugros": {RANKED_SOLO_QUEUE_ID: ["EUW1_1"]}})
    clock = Clock()

    refreshed = _tick(_poller(store, client, clock))

    assert refreshed == [], "re-establishing the baseline is not itself a new game"
    row = store.get_player(SLUG)
    seen = row["watch_seen_json"]
    assert "puuid-hugros" in seen


def test_no_new_match_id_does_nothing(store: JobStore) -> None:
    store.set_watch(SLUG, enabled=True, interval_s=60)
    client = FakeClient({"puuid-hugros": {RANKED_SOLO_QUEUE_ID: ["EUW1_1"]}})
    clock = Clock()
    poller = _poller(store, client, clock)

    _tick(poller)
    clock.advance(120)

    assert _tick(poller) == []
    assert store.list_active_jobs() == []


def test_an_active_job_blocks_enqueueing(store: JobStore) -> None:
    store.set_watch(SLUG, enabled=True, interval_s=60)
    client = FakeClient({"puuid-hugros": {RANKED_SOLO_QUEUE_ID: ["EUW1_1"]}})
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
    client.newest["puuid-hugros"] = {RANKED_SOLO_QUEUE_ID: ["EUW1_2"]}
    clock.advance(120)

    assert _tick(poller) == []
    assert client.match_id_calls == before, "should not even spend an API call"


def test_a_group_is_not_checked_before_its_interval(store: JobStore) -> None:
    store.set_watch(SLUG, enabled=True, interval_s=600)
    client = FakeClient({"puuid-hugros": {RANKED_SOLO_QUEUE_ID: ["EUW1_1"]}})
    clock = Clock()
    poller = _poller(store, client, clock)

    _tick(poller)
    calls = client.match_id_calls
    clock.advance(60)
    _tick(poller)

    assert client.match_id_calls == calls, "interval not elapsed"


def test_an_api_failure_backs_off_and_is_surfaced(store: JobStore) -> None:
    store.set_watch(SLUG, enabled=True, interval_s=60)
    client = FakeClient({"puuid-hugros": {RANKED_SOLO_QUEUE_ID: ["EUW1_1"]}})
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


def test_on_new_game_hook_fires_with_slug_job_id_and_match_id(store: JobStore) -> None:
    """The optional observer hook is called once a refresh is genuinely enqueued,
    carrying the real newly-detected match id, and not on the baseline tick
    where nothing looks new yet."""
    store.set_watch(SLUG, enabled=True, interval_s=60)
    client = FakeClient({"puuid-hugros": {RANKED_SOLO_QUEUE_ID: ["EUW1_1"]}})
    clock = Clock()
    calls: list[tuple[str, str, str, dict]] = []
    poller = WatchPoller(
        store,
        lambda region: client,
        now=clock,
        on_new_game=lambda slug, job_id, match_id, summary: calls.append(
            (slug, job_id, match_id, summary)
        ),
    )

    _tick(poller)  # baseline: must not fire the hook
    assert calls == []

    client.newest["puuid-hugros"] = {RANKED_SOLO_QUEUE_ID: ["EUW1_2"]}
    clock.advance(120)
    _tick(poller)

    assert len(calls) == 1
    fired_slug, fired_job_id, fired_match_id, _fired_summary = calls[0]
    assert fired_slug == SLUG
    assert fired_job_id == str(store.list_active_jobs()[0]["id"])
    assert fired_match_id == "EUW1_2"


def test_on_new_game_hook_carries_the_computed_welcome_back_summary(store: JobStore) -> None:
    """The hook's summary is computed from the newly-detected match alone,
    for the puuid whose queue actually changed."""
    store.set_watch(SLUG, enabled=True, interval_s=60)
    client = FakeClient({"puuid-hugros": {RANKED_SOLO_QUEUE_ID: ["EUW1_1"]}})
    client.matches["EUW1_2"] = make_player_match(
        "EUW1_2", puuid="puuid-hugros", duration_s=1200
    )
    clock = Clock()
    calls: list[tuple[str, str, str, dict]] = []
    poller = WatchPoller(
        store,
        lambda region: client,
        now=clock,
        on_new_game=lambda slug, job_id, match_id, summary: calls.append(
            (slug, job_id, match_id, summary)
        ),
    )

    _tick(poller)  # baseline
    client.newest["puuid-hugros"] = {RANKED_SOLO_QUEUE_ID: ["EUW1_2"]}
    clock.advance(120)
    _tick(poller)

    assert len(calls) == 1
    summary = calls[0][3]
    assert summary["win"] is True
    assert summary["kills"] == 7
    assert summary["deaths"] == 2
    assert summary["assists"] == 5
    assert summary["cs_per_min"] == 9.4
    assert client.fetch_match_calls == ["EUW1_2"]


def test_budget_exhaustion_skips_the_summary_but_still_enqueues(store: JobStore) -> None:
    """The extra `fetch_match` call is budgeted; running out must not block
    the refresh itself, only the summary that rides along with it."""
    store.set_watch(SLUG, enabled=True, interval_s=60)
    client = FakeClient({"puuid-hugros": {RANKED_SOLO_QUEUE_ID: ["EUW1_1"]}})
    clock = Clock()
    # Budget for exactly the two per-queue detection calls made on the second
    # tick (solo + flex); none left over for the summary's fetch_match call.
    budget = _Budget(limit=2)
    calls: list[dict] = []
    poller = WatchPoller(
        store,
        lambda region: client,
        now=clock,
        budget=budget,
        on_new_game=lambda slug, job_id, match_id, summary: calls.append(summary),
    )

    _tick(poller)  # baseline; consumes its own budget, window not yet advanced
    client.newest["puuid-hugros"] = {RANKED_SOLO_QUEUE_ID: ["EUW1_2"]}
    clock.advance(120)  # frees the window for exactly the 2 detection calls
    refreshed = _tick(poller)

    assert refreshed == [SLUG]
    assert len(calls) == 1
    assert calls[0] == {}
    assert client.fetch_match_calls == []


def test_on_new_game_hook_reports_the_flex_match_id_when_flex_is_what_changed(
    store: JobStore,
) -> None:
    """Regression guard for the tiebreak: when only the flex queue changed,
    the hook must report the flex match id, not a stale/empty solo one."""
    store.set_watch(SLUG, enabled=True, interval_s=60)
    client = FakeClient(
        {"puuid-hugros": {RANKED_SOLO_QUEUE_ID: ["EUW1_1"], RANKED_FLEX_QUEUE_ID: ["EUW1_F1"]}}
    )
    clock = Clock()
    calls: list[tuple[str, str, str, dict]] = []
    poller = WatchPoller(
        store,
        lambda region: client,
        now=clock,
        on_new_game=lambda slug, job_id, match_id, summary: calls.append(
            (slug, job_id, match_id, summary)
        ),
    )

    _tick(poller)  # baseline
    client.newest["puuid-hugros"][RANKED_FLEX_QUEUE_ID] = ["EUW1_F2"]
    clock.advance(120)
    _tick(poller)

    assert len(calls) == 1
    assert calls[0][2] == "EUW1_F2"


def test_on_new_game_hook_defaults_to_none_and_does_not_raise(store: JobStore) -> None:
    """Backward compatibility: omitting the hook must behave exactly as before."""
    store.set_watch(SLUG, enabled=True, interval_s=60)
    client = FakeClient({"puuid-hugros": {RANKED_SOLO_QUEUE_ID: ["EUW1_1"]}})
    clock = Clock()
    poller = _poller(store, client, clock)

    _tick(poller)  # baseline
    client.newest["puuid-hugros"] = {RANKED_SOLO_QUEUE_ID: ["EUW1_2"]}
    clock.advance(120)

    assert _tick(poller) == [SLUG]  # must not raise despite no hook configured


def test_unwatched_groups_are_never_polled(store: JobStore) -> None:
    client = FakeClient({"puuid-hugros": {RANKED_SOLO_QUEUE_ID: ["EUW1_1"]}})
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
    client = FakeClient({"puuid-hugros": {RANKED_SOLO_QUEUE_ID: ["EUW1_1"]}})
    poller = WatchPoller(
        store, lambda region: client, now=Clock(), budget=_Budget(limit=0)
    )

    assert _tick(poller) == []
    assert client.match_id_calls == 0


# ------------------------------------------------------------------------- API


@pytest.fixture()
def client(tmp_path: Path):
    config = WebConfig(
        output_dir=tmp_path / "out",
        assets_dir=tmp_path / "assets",
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


def test_watch_route_backfills_a_registry_row_missing_despite_a_real_report(
    tmp_path: Path,
) -> None:
    """Regression test for the "dropped Stage-B event" production bug: a slug
    with `has_report: true` on disk but no `store.get_player(slug)` row (the
    stuck state left behind when `docker-compose down` drops the gRPC stream
    at exactly the wrong moment -- see `worker.py`'s
    `_execute_job_via_runner`) must self-heal on a watch/unwatch click instead
    of 404ing with "Unknown player" forever.
    """
    import json

    slug = "orphan_euw"
    config = WebConfig(output_dir=tmp_path / "out", assets_dir=tmp_path / "assets")
    build_dir = config.reports_dir / slug / "viktor_middle"
    build_dir.mkdir(parents=True)
    (build_dir / "meta.json").write_text(
        json.dumps(
            {
                "champion": "Viktor",
                "role": "MIDDLE",
                "riot_id": "Orphan",
                "tagline": "EUW",
                "region": "europe",
            }
        ),
        encoding="utf-8",
    )
    (build_dir / "report.json").write_text("{}", encoding="utf-8")

    app = create_app(config, start_worker=False)
    with TestClient(app) as handle:
        assert handle.app.state.job_store.get_player(slug) is None

        response = handle.post(f"/api/players/{slug}/watch", json={"interval_s": 300})
        assert response.status_code == 200
        assert response.json()["watch_enabled"] is True

        row = handle.app.state.job_store.get_player(slug)
        assert row is not None
        assert row["riot_id"] == "Orphan"
        assert row["tagline"] == "EUW"


def test_career_banner_ack_route(tmp_path: Path) -> None:
    """A reader can retire a Career banner that watch rebuilds must not eat."""
    import json

    from league_stats_common.core.champions import player_slug
    from league_stats_common.infra.career_store import (
        build_key as career_build_key,
        open_career_store,
    )

    config = WebConfig(output_dir=tmp_path / "out", assets_dir=tmp_path / "assets")
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

    key = career_build_key(player_slug("Hugros", "EUW"), "Viktor", "MIDDLE")
    # `open_career_store()` (not a fresh `CareerStore(...)`) so this seed lands
    # in the same mongomock client the real ack route reaches via
    # `open_career_store()` -- the autouse `_career_store_uses_mongomock`
    # fixture gives every call in this test the same in-memory client.
    with open_career_store() as career:
        career.set_pending_congrats(key, "laning_income")

    app = create_app(config, start_worker=False)
    with TestClient(app) as handle:
        response = handle.post(f"/api/players/{SLUG}/builds/viktor_middle/career/ack")
        assert response.status_code == 200
        assert response.json() == {"acknowledged": True}

    with open_career_store() as career:
        assert career.peek_pending_congrats(key) == ""


def test_career_banner_ack_404s_on_unknown_build(tmp_path: Path) -> None:
    config = WebConfig(output_dir=tmp_path / "out", assets_dir=tmp_path / "assets")
    app = create_app(config, start_worker=False)
    with TestClient(app) as handle:
        assert (
            handle.post(f"/api/players/{SLUG}/builds/nope_mid/career/ack").status_code
            == 404
        )


def test_player_status_exposes_watch_state(client: TestClient) -> None:
    """The player hub toggle reads its state from here."""
    before = client.get(f"/api/players/{SLUG}").json()
    assert before["can_watch"] is True
    assert before["watch_enabled"] is False
    assert before["watch_interval_s"] == 60
    assert before["last_watch_error"] == ""

    client.post(f"/api/players/{SLUG}/watch", json={"interval_s": 600})
    after = client.get(f"/api/players/{SLUG}").json()
    assert after["watch_enabled"] is True
    assert after["watch_interval_s"] == 600


# --------------------------------------------------------- new observability metrics


def test_tick_sets_watched_groups_and_accounts_gauges(store: JobStore) -> None:
    store.set_watch(SLUG, enabled=True, interval_s=60)
    client = FakeClient({"puuid-hugros": {RANKED_SOLO_QUEUE_ID: ["EUW1_1"]}})
    clock = Clock()
    poller = _poller(store, client, clock)

    _tick(poller)

    assert watch_module.CRON_WATCH_WATCHED_GROUPS._value.get() == 1
    assert watch_module.CRON_WATCH_WATCHED_ACCOUNTS._value.get() == 1


def test_tick_sets_last_tick_timestamp_to_the_pollers_clock(store: JobStore) -> None:
    client = FakeClient()
    clock = Clock()
    poller = _poller(store, client, clock)

    _tick(poller)

    assert watch_module.CRON_WATCH_LAST_TICK_TIMESTAMP._value.get() == clock.t


def test_api_failure_records_match_fetch_check_failure(store: JobStore) -> None:
    store.set_watch(SLUG, enabled=True, interval_s=60)
    client = FakeClient({"puuid-hugros": {RANKED_SOLO_QUEUE_ID: ["EUW1_1"]}})
    clock = Clock()
    poller = _poller(store, client, clock)
    _tick(poller)

    before = watch_module.CRON_WATCH_CHECK_FAILURES_TOTAL.labels(
        stage="match_fetch"
    )._value.get()

    client.fail = True
    clock.advance(120)
    _tick(poller)

    after = watch_module.CRON_WATCH_CHECK_FAILURES_TOTAL.labels(stage="match_fetch")._value.get()
    assert after == before + 1


def test_client_factory_failure_records_client_unavailable_check_failure(store: JobStore) -> None:
    store.set_watch(SLUG, enabled=True, interval_s=60)
    clock = Clock()

    def _boom(region: str) -> FakeClient:
        raise RuntimeError("no client for region")

    poller = WatchPoller(store, _boom, now=clock)

    before = watch_module.CRON_WATCH_CHECK_FAILURES_TOTAL.labels(
        stage="client_unavailable"
    )._value.get()

    _tick(poller)

    after = watch_module.CRON_WATCH_CHECK_FAILURES_TOTAL.labels(
        stage="client_unavailable"
    )._value.get()
    assert after == before + 1


def test_check_group_increments_accounts_checked_total(store: JobStore) -> None:
    store.set_watch(SLUG, enabled=True, interval_s=60)
    client = FakeClient({"puuid-hugros": {RANKED_SOLO_QUEUE_ID: ["EUW1_1"]}})
    clock = Clock()
    poller = _poller(store, client, clock)

    before = watch_module.CRON_WATCH_ACCOUNTS_CHECKED_TOTAL._value.get()

    _tick(poller)

    after = watch_module.CRON_WATCH_ACCOUNTS_CHECKED_TOTAL._value.get()
    assert after == before + 1


def test_budget_exhaustion_increments_budget_exhausted_total(store: JobStore) -> None:
    store.set_watch(SLUG, enabled=True, interval_s=60)
    client = FakeClient({"puuid-hugros": {RANKED_SOLO_QUEUE_ID: ["EUW1_1"]}})
    clock = Clock()
    budget = _Budget(limit=0)
    poller = WatchPoller(store, lambda region: client, now=clock, budget=budget)

    before = watch_module.CRON_WATCH_BUDGET_EXHAUSTED_TOTAL._value.get()

    _tick(poller)

    after = watch_module.CRON_WATCH_BUDGET_EXHAUSTED_TOTAL._value.get()
    assert after > before
