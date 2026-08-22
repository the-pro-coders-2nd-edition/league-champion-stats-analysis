"""Tests for the analysis worker: two-stage execution and failure handling."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import mongomock
import pytest

from league_stats_common.core.config import WebConfig
from league_stats_runner.ingest.parser import BuildPool
from league_stats_runner.pipeline.fetch import FetchResult
from league_stats_runner.pipeline.orchestrator import (
    BuildAnalysisResult,
    BuildBatch,
    NoEligibleBuildsError,
)
from league_stats_runner.pipeline.services import PlayerContext
import league_stats_common.infra.jobs as jobs
import league_stats_runner.worker as worker
from league_stats_common.infra.jobs import JobStore


def _fake_context(
    *,
    riot_id: str = "Test",
    tagline: str = "EUW",
    puuid: str = "puuid",
    profile_icon_id: int | None = 42,
) -> PlayerContext:
    return PlayerContext(
        riot_id=riot_id,
        tagline=tagline,
        puuid=puuid,
        profile_icon_id=profile_icon_id,
    )


@pytest.fixture()
def store(tmp_path: Path) -> JobStore:
    js = JobStore(mongomock.MongoClient())
    js.upsert_player(slug="test_euw", riot_id="Test", tagline="EUW", region="euw1")
    yield js
    js.close()


@pytest.fixture()
def web_config(tmp_path: Path) -> WebConfig:
    return WebConfig(
        output_dir=tmp_path / "output",
    )


def _claimed_job(store: JobStore) -> dict[str, Any]:
    store.enqueue(
        kind=jobs.JOB_KIND_ANALYZE,
        riot_id="Test",
        tagline="EUW",
        region="euw1",
        player_slug="test_euw",
    )
    job = store.claim_next()
    assert job is not None
    return job


def _fake_services() -> SimpleNamespace:
    return SimpleNamespace(
        config=SimpleNamespace(),
        store=SimpleNamespace(close=lambda: None),
        http_cache=SimpleNamespace(close=lambda: None),
    )


def _fake_batch() -> BuildBatch:
    return BuildBatch(
        pools=[BuildPool(champion="Viktor", role="MIDDLE", games=25)],
        records=[],
        manifest_builds=[],
        primary_puuid="puuid",
    )


def _patch_pipeline(monkeypatch: pytest.MonkeyPatch, **overrides: Any) -> list[str]:
    """Stub out every pipeline call the worker makes; record the call order."""
    calls: list[str] = []

    defaults: dict[str, Any] = {
        "_build_job_services": lambda job, cfg, reporter, **kwargs: _fake_services(),
        "fetch_matches": lambda services: calls.append("fetch")
        or FetchResult(contexts=[_fake_context()], new_match_ids=frozenset()),
        "resolve_player_contexts": lambda services: calls.append("resolve")
        or [_fake_context()],
        "prepare_builds": lambda services, contexts: calls.append("prepare") or _fake_batch(),
        "group_records": lambda records, champion, role: ["record"],
        "resolve_ranked": lambda services, batch, records: calls.append("ranked") or None,
        "should_skip_unchanged_build": lambda config, pool, records, new_ids: False,
        # Defaults to "already has peer data" so a test that skips stage A via
        # should_skip_unchanged_build also skips stage B without needing a real
        # AppConfig (report_needs_peer_comparison reads config.output_dir).
        "report_needs_peer_comparison": lambda config, pool: False,
        "analyze_build": (
            lambda services, batch, pool, *, ranked, peer_comparison, still_refining=False, full_frames=None, report_stats=None: calls.append(
                f"analyze(peer={peer_comparison is not None})"
            )
            or BuildAnalysisResult(path=Path("report.json"))
        ),
        # `_run_stage_b` always resolves peers over gRPC since Phase 9 deleted
        # `peers_mode` and its in-process `build_peer_for_pool` branch.
        "_build_peer_for_pool_via_grpc": (
            lambda services, batch, pool, ranked, web_config, **kwargs: calls.append("peer")
            or object()
        ),
    }
    defaults.update(overrides)
    for name, fn in defaults.items():
        monkeypatch.setattr(worker, name, fn)
    return calls


def _claimed_regenerate_job(store: JobStore) -> dict[str, Any]:
    store.enqueue(
        kind=jobs.JOB_KIND_REGENERATE,
        riot_id="Test",
        tagline="EUW",
        region="euw1",
        player_slug="test_euw",
    )
    job = store.claim_next()
    assert job is not None
    return job


def test_execute_job_two_stage_happy_path(
    store: JobStore, web_config: WebConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    job = _claimed_job(store)
    calls = _patch_pipeline(monkeypatch)

    worker.execute_job(job, store, web_config)

    final = store.get(int(job["id"]))
    assert final["state"] == jobs.DONE
    assert final["error"] == ""
    # Stage A renders without peer, stage B builds peer then re-renders.
    assert calls == ["fetch", "prepare", "ranked", "analyze(peer=False)", "peer", "analyze(peer=True)"]

    player = store.get_player("test_euw")
    assert player["base_completed_at"] is not None
    assert player["peer_completed_at"] is not None
    assert player["peer_failed"] == 0


def test_execute_job_skips_unchanged_builds(
    store: JobStore, web_config: WebConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Refresh with no new games keeps existing reports and skips peer work."""
    job = _claimed_job(store)
    calls = _patch_pipeline(
        monkeypatch,
        should_skip_unchanged_build=lambda *args, **kwargs: True,
    )

    worker.execute_job(job, store, web_config)

    final = store.get(int(job["id"]))
    assert final["state"] == jobs.DONE
    assert final["error"] == ""
    assert calls == ["fetch", "prepare"]
    assert "analyze(peer=False)" not in calls
    assert "peer" not in calls

    player = store.get_player("test_euw")
    assert player["base_completed_at"] is not None
    assert player["peer_completed_at"] is not None


def test_execute_scoped_refresh_still_skips_unchanged_build(
    store: JobStore, web_config: WebConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A champion-scoped refresh narrows the pool but does not bypass the
    unchanged-build check — an explicit single-build refresh with no new games
    must not force a full rank-comparison rebuild every time.
    """
    store.enqueue(
        kind=jobs.JOB_KIND_REFRESH,
        riot_id="Test",
        tagline="EUW",
        region="euw1",
        player_slug="test_euw",
        filter_champion="Viktor",
        filter_role="MIDDLE",
    )
    job = store.claim_next()
    assert job is not None
    assert job["filter_champion"] == "Viktor"

    skip_calls = {"n": 0}

    def always_skip(*args: Any, **kwargs: Any) -> bool:
        skip_calls["n"] += 1
        return True

    captured: dict[str, Any] = {}

    def capture_services(job_row: Any, cfg: Any, reporter: Any, **kwargs: Any) -> Any:
        services = _fake_services()
        services.config = SimpleNamespace(
            filter_champion=job_row.get("filter_champion"),
            filter_role=job_row.get("filter_role"),
        )
        captured["filter_champion"] = job_row.get("filter_champion")
        captured["filter_role"] = job_row.get("filter_role")
        return services

    calls = _patch_pipeline(
        monkeypatch,
        _build_job_services=capture_services,
        should_skip_unchanged_build=always_skip,
    )

    worker.execute_job(job, store, web_config)

    final = store.get(int(job["id"]))
    assert final["state"] == jobs.DONE
    assert captured["filter_champion"] == "Viktor"
    assert captured["filter_role"] == "MIDDLE"
    assert skip_calls["n"] > 0
    assert calls == ["fetch", "prepare"]


def test_execute_scoped_refresh_with_new_games_rebuilds(
    store: JobStore, web_config: WebConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A champion-scoped refresh still re-analyses when there are new games."""
    store.enqueue(
        kind=jobs.JOB_KIND_REFRESH,
        riot_id="Test",
        tagline="EUW",
        region="euw1",
        player_slug="test_euw",
        filter_champion="Viktor",
        filter_role="MIDDLE",
    )
    job = store.claim_next()
    assert job is not None

    calls = _patch_pipeline(
        monkeypatch,
        should_skip_unchanged_build=lambda *a, **k: False,
        report_needs_peer_comparison=lambda *a, **k: True,
    )

    worker.execute_job(job, store, web_config)

    final = store.get(int(job["id"]))
    assert final["state"] == jobs.DONE
    assert calls == ["fetch", "prepare", "ranked", "analyze(peer=False)", "peer", "analyze(peer=True)"]


def test_execute_regenerate_uses_cache_and_forces_reanalysis(
    store: JobStore, web_config: WebConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regenerate skips Riot download and re-analyses even with no new games."""
    job = _claimed_regenerate_job(store)
    seen_new_ids: list[Any] = []

    def track_skip(config: Any, pool: Any, records: Any, new_ids: Any) -> bool:
        seen_new_ids.append(new_ids)
        return False

    calls = _patch_pipeline(monkeypatch, should_skip_unchanged_build=track_skip)

    worker.execute_job(job, store, web_config)

    final = store.get(int(job["id"]))
    assert final["state"] == jobs.DONE
    assert final["error"] == ""
    assert calls == [
        "resolve",
        "prepare",
        "ranked",
        "analyze(peer=False)",
        "peer",
        "analyze(peer=True)",
    ]
    assert "fetch" not in calls
    assert seen_new_ids == [None, None]


def test_execute_job_peer_failure_is_soft(
    store: JobStore, web_config: WebConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    job = _claimed_job(store)

    def boom(services: Any, batch: Any, pool: Any, ranked: Any, web_config: Any, **kwargs: Any) -> Any:
        raise RuntimeError("riot exploded")

    _patch_pipeline(monkeypatch, _build_peer_for_pool_via_grpc=boom)

    worker.execute_job(job, store, web_config)

    final = store.get(int(job["id"]))
    assert final["state"] == jobs.DONE
    assert "Rank comparison failed" in final["error"]

    player = store.get_player("test_euw")
    assert player["base_completed_at"] is not None
    assert player["peer_completed_at"] is None
    assert player["peer_failed"] == 1


def test_execute_job_fetch_failure_marks_failed(
    store: JobStore, web_config: WebConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    job = _claimed_job(store)

    def boom(services: Any) -> Any:
        raise RuntimeError("network down")

    _patch_pipeline(monkeypatch, fetch_matches=boom)

    worker.execute_job(job, store, web_config)

    final = store.get(int(job["id"]))
    assert final["state"] == jobs.FAILED
    assert "network down" in final["error"]
    assert store.get_player("test_euw")["base_completed_at"] is None


def test_execute_job_passes_group_players_to_config(
    store: JobStore, web_config: WebConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    players = [
        {"riot_id": "Alice", "tagline": "EUW"},
        {"riot_id": "Bob", "tagline": "EUW"},
    ]
    store.upsert_player(
        slug="alice_euw__bob_euw",
        riot_id="Alice",
        tagline="EUW",
        region="euw1",
        players=players,
    )
    store.enqueue(
        kind=jobs.JOB_KIND_ANALYZE,
        riot_id="Alice",
        tagline="EUW",
        region="euw1",
        player_slug="alice_euw__bob_euw",
        players=players,
    )
    job = store.claim_next()
    assert job is not None
    captured: dict[str, Any] = {}

    def capture_services(
        claimed: dict[str, Any], cfg: WebConfig, reporter: Any, **kwargs: Any
    ) -> Any:
        captured["players"] = claimed.get("players")
        return _fake_services()

    group_contexts = [
        _fake_context(riot_id="Alice", tagline="EUW", puuid="alice", profile_icon_id=1),
        _fake_context(riot_id="Bob", tagline="EUW", puuid="bob", profile_icon_id=2),
    ]
    _patch_pipeline(
        monkeypatch,
        _build_job_services=capture_services,
        fetch_matches=lambda services: FetchResult(
            contexts=group_contexts, new_match_ids=frozenset()
        ),
    )
    worker.execute_job(job, store, web_config)
    assert captured["players"] == players
    assert store.get(int(job["id"]))["state"] == jobs.DONE
    saved = store.get_player("alice_euw__bob_euw")
    assert saved is not None
    assert saved["players"] == [
        {"riot_id": "Alice", "tagline": "EUW", "profile_icon_id": 1},
        {"riot_id": "Bob", "tagline": "EUW", "profile_icon_id": 2},
    ]


def test_execute_job_no_builds_marks_failed(
    store: JobStore, web_config: WebConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    job = _claimed_job(store)

    def no_builds(services: Any, contexts: Any) -> Any:
        raise NoEligibleBuildsError("No champion+lane reports with at least 20 ranked games found.")

    _patch_pipeline(monkeypatch, prepare_builds=no_builds)

    worker.execute_job(job, store, web_config)

    final = store.get(int(job["id"]))
    assert final["state"] == jobs.FAILED
    assert "20 ranked games" in final["error"]


def test_execute_job_honours_cancel_before_peer(
    store: JobStore, web_config: WebConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cancel after stage A keeps the job cancelled and skips peer work."""
    job = _claimed_job(store)
    job_id = int(job["id"])

    def cancel_after_analyze(
        services: Any, batch: Any, pool: Any, *, ranked: Any, peer_comparison: Any
    ) -> Path:
        store.cancel(job_id)
        return Path("report.json")

    calls = _patch_pipeline(monkeypatch, analyze_build=cancel_after_analyze)

    worker.execute_job(job, store, web_config)

    final = store.get(job_id)
    assert final["state"] == jobs.CANCELLED
    assert "peer" not in calls
    assert store.get_player("test_euw")["peer_completed_at"] is None


def test_execute_job_cancel_during_fetch_does_not_mark_failed(
    store: JobStore, web_config: WebConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    job = _claimed_job(store)
    job_id = int(job["id"])

    def cancel_mid_fetch(services: Any) -> Any:
        store.cancel(job_id)
        raise worker.JobCancelled()

    _patch_pipeline(monkeypatch, fetch_matches=cancel_mid_fetch)

    worker.execute_job(job, store, web_config)

    final = store.get(job_id)
    assert final["state"] == jobs.CANCELLED
    assert final["error"] == ""
    assert store.get_player("test_euw")["base_completed_at"] is None


def test_tracked_players_recovers_group_from_registry(store: JobStore) -> None:
    """Incomplete job players_json must not collapse a group slug to a solo path."""
    players = [
        {"riot_id": "Alice", "tagline": "EUW"},
        {"riot_id": "Bob", "tagline": "EUW"},
    ]
    store.upsert_player(
        slug="alice_euw__bob_euw",
        riot_id="Alice",
        tagline="EUW",
        region="euw1",
        players=players,
    )
    job = {
        "player_slug": "alice_euw__bob_euw",
        "riot_id": "Alice",
        "tagline": "EUW",
        # Only the primary — the bug that wrote solo reports under a group job.
        "players_json": '[{"riot_id":"Alice","tagline":"EUW"}]',
    }
    assert worker._tracked_players_for_job(job, store) == players


def test_build_job_services_rejects_slug_mismatch(
    store: JobStore, web_config: WebConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Never write a refresh into a different account folder than the job slug."""
    monkeypatch.setenv("RIOT_API_KEY", "RGAPI-test")
    job = {
        "id": 1,
        "player_slug": "alice_euw__bob_euw",
        "riot_id": "Alice",
        "tagline": "EUW",
        "region": "euw1",
        # Solo identity only — must not collapse the group report path.
        "players_json": '[{"riot_id":"Alice","tagline":"EUW"}]',
        "filter_champion": None,
        "filter_role": None,
    }
    reporter = SimpleNamespace(update=lambda *a, **k: None)

    with pytest.raises(ValueError, match="does not match resolved players"):
        worker._build_job_services(job, web_config, reporter, job_store=store)


def test_build_job_services_pins_output_slug(
    store: JobStore, web_config: WebConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Scoped refresh must rewrite only under the job's report folder."""
    monkeypatch.setenv("RIOT_API_KEY", "RGAPI-test")
    players = [
        {"riot_id": "Alice", "tagline": "EUW"},
        {"riot_id": "Bob", "tagline": "EUW"},
    ]
    store.upsert_player(
        slug="alice_euw__bob_euw",
        riot_id="Alice",
        tagline="EUW",
        region="euw1",
        players=players,
    )
    job = {
        "id": 1,
        "player_slug": "alice_euw__bob_euw",
        "riot_id": "Alice",
        "tagline": "EUW",
        "region": "euw1",
        "players_json": '[{"riot_id":"Alice","tagline":"EUW"}]',
        "filter_champion": "Viktor",
        "filter_role": "MIDDLE",
    }
    reporter = SimpleNamespace(update=lambda *a, **k: None)

    services = worker._build_job_services(
        job, web_config, reporter, job_store=store
    )
    try:
        assert services.config.reports_group_slug == "alice_euw__bob_euw"
        assert services.config.output_reports_slug == "alice_euw__bob_euw"
        assert services.config.filter_champion == "Viktor"
        assert services.config.filter_role == "MIDDLE"
        assert services.config.status_endpoint == "/api/players/alice_euw__bob_euw"
    finally:
        services.store.close()
        services.http_cache.close()


def test_build_job_services_applies_min_games(
    store: JobStore, web_config: WebConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RIOT_API_KEY", "RGAPI-test")
    store.upsert_player(slug="test_euw", riot_id="Test", tagline="EUW", region="euw1")
    job = {
        "id": 1,
        "player_slug": "test_euw",
        "riot_id": "Test",
        "tagline": "EUW",
        "region": "euw1",
        "players_json": '[{"riot_id":"Test","tagline":"EUW"}]',
        "filter_champion": None,
        "filter_role": None,
        "min_games": 10,
    }
    reporter = SimpleNamespace(update=lambda *a, **k: None)

    services = worker._build_job_services(
        job, web_config, reporter, job_store=store
    )
    try:
        assert services.config.min_games == 10
    finally:
        services.store.close()
        services.http_cache.close()


# ------------------------------------ RawMatchStore wiring (Phase 5 Task 1)


def test_build_job_services_uses_raw_match_store_in_mongo_mode(
    store: JobStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`_build_job_services` always constructs a `RawMatchStore` -- `MatchStore`
    (the local on-disk store this replaced) was deleted in Phase 8, Task 1, so
    this is unconditional. Uses the process-wide client seam
    `_build_mongo_client`, monkeypatched here to a mongomock client instead of
    dialing a real Mongo."""
    import mongomock

    from league_stats_runner.infra.raw_match_store import RawMatchStore

    monkeypatch.setenv("RIOT_API_KEY", "RGAPI-test")
    monkeypatch.chdir(tmp_path)
    mongo_client = mongomock.MongoClient()
    monkeypatch.setattr(worker, "_build_mongo_client", lambda uri: mongo_client)

    mongo_web_config = WebConfig(output_dir=tmp_path / "output")
    job = {
        "id": 1,
        "player_slug": "test_euw",
        "riot_id": "Test",
        "tagline": "EUW",
        "region": "euw1",
        "players_json": '[{"riot_id":"Test","tagline":"EUW"}]',
        "filter_champion": None,
        "filter_role": None,
    }
    reporter = SimpleNamespace(update=lambda *a, **k: None)

    services = worker._build_job_services(job, mongo_web_config, reporter, job_store=store)
    try:
        assert isinstance(services.store, RawMatchStore)
        # close() must be a genuine no-op that leaves the shared client usable.
        services.store.close()
        services.store.save_match("EUW1_1", "puuid-a", {"info": {}})
        assert services.store.load_match("EUW1_1") == {"info": {}}
    finally:
        services.store.close()
        services.http_cache.close()


def test_build_mongo_client_reuses_the_same_client_for_the_same_uri() -> None:
    """Mirrors `shared_rate_limiter`'s process-wide sharing pattern -- a second
    call with the same URI must not open a second connection pool."""
    first = worker._build_mongo_client("mongodb://localhost:27017/league_stats_shared_test")
    second = worker._build_mongo_client("mongodb://localhost:27017/league_stats_shared_test")
    assert first is second


# --------------------------------------------------------- runner delegation (Task 1, Phase 9)


def test_execute_job_delegates_to_runner_and_replays_progress(
    tmp_path: Path, store: JobStore, web_config: WebConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`AnalysisWorker`'s job-claim loop always delegates to RUNNER via
    `_execute_job_via_runner`, over a real (in-process) RunnerServicer over
    gRPC, and replays its StageResult stream into the real (local) JobStore --
    ending in the same terminal state/registry updates RUNNER's own
    `execute_job` produces for an equivalent run (see
    test_execute_job_two_stage_happy_path).

    Runs RUNNER's actual `execute_job` against offline fixture data (no real
    Riot API / Mongo -- `mongomock` stands in), reusing the exact pattern
    tests/test_runner_service.py's
    `test_enqueue_job_and_stream_progress_uses_raw_match_store_in_mongo_mode`
    already established for this: seed a mongomock-backed RawMatchStore,
    monkeypatch RiotApiClient's network calls and `_build_mongo_client`, and
    use kind=REGENERATE so `execute_job` never calls `fetch_matches`.
    """
    from concurrent import futures

    import grpc
    import mongomock

    from league_stats_common.infra.riot_api import RiotApiClient
    from league_stats_runner.infra.raw_match_store import RawMatchStore
    from league_stats_runner.service import RunnerServicer
    from league_stats_rpc.v1 import runner_pb2_grpc
    from tests.fixtures import MY_PUUID, make_player_match, make_timeline

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(RiotApiClient, "resolve_puuid", lambda self, riot_id, tagline: MY_PUUID)
    monkeypatch.setattr(RiotApiClient, "fetch_profile_icon_id", lambda self, puuid: None)
    monkeypatch.setattr(RiotApiClient, "fetch_solo_rank", lambda self, puuid: None)

    mongo_client = mongomock.MongoClient()
    monkeypatch.setattr(worker, "_build_mongo_client", lambda uri: mongo_client)

    match_store = RawMatchStore(mongo_client, db_name="league_stats")
    for index in range(6):
        match_id = f"EUW1_v{index}"
        match_store.save_match(
            match_id, MY_PUUID, make_player_match(match_id, champion="Viktor", position="MIDDLE")
        )
        match_store.save_timeline(match_id, make_timeline())

    runner_web_config = WebConfig(output_dir=tmp_path / "runner_output")
    servicer = RunnerServicer(web_config=runner_web_config)
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=4))
    runner_pb2_grpc.add_RunnerServiceServicer_to_server(servicer, server)
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()
    try:
        grpc_web_config = web_config.model_copy(
            update={"runner_grpc_target": f"127.0.0.1:{port}"}
        )
        job = _claimed_regenerate_job(store)
        job["min_games"] = 5

        worker._execute_job_via_runner(job, store, grpc_web_config)
    finally:
        server.stop(grace=None)

    final = store.get(int(job["id"]))
    assert final["state"] == jobs.DONE
    assert final["error"] == ""

    player = store.get_player("test_euw")
    assert player["base_completed_at"] is not None
    assert player["peer_completed_at"] is not None
    assert player["peer_failed"] == 0


def _run_scripted_grpc_job(
    store: JobStore, web_config: WebConfig, results: list[Any]
) -> dict[str, Any]:
    """Run `worker._execute_job_via_runner` against a scripted `StreamJobProgress`
    reply, to exercise its replay logic in isolation from a real pipeline run
    (used for the FAILED/soft-DONE inference branches, which only depend on
    the shape of the StageResult stream, not on real analysis).
    """
    from concurrent import futures

    import grpc

    from league_stats_rpc.v1 import runner_pb2, runner_pb2_grpc

    class _ScriptedRunnerServicer(runner_pb2_grpc.RunnerServiceServicer):
        def EnqueueJob(self, request, context):
            return runner_pb2.EnqueueJobResponse(job_id="scripted-1")

        def StreamJobProgress(self, request, context):
            yield from results

    server = grpc.server(futures.ThreadPoolExecutor(max_workers=2))
    runner_pb2_grpc.add_RunnerServiceServicer_to_server(_ScriptedRunnerServicer(), server)
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()
    try:
        job = _claimed_job(store)
        grpc_web_config = web_config.model_copy(
            update={"runner_grpc_target": f"127.0.0.1:{port}"}
        )
        worker._execute_job_via_runner(job, store, grpc_web_config)
    finally:
        server.stop(grace=None)
    return job


def test_execute_job_grpc_mode_unreachable_runner_marks_failed_not_hangs(
    store: JobStore, web_config: WebConfig
) -> None:
    """RUNNER unreachable (nothing listening on the target port) must not hang
    the worker thread or raise out of `_execute_job_via_runner` -- the job must
    reach a terminal FAILED state instead. Regression test for the gap where
    only a bare `finally: channel.close()` guarded the RPC calls, so a
    `grpc.RpcError` (RUNNER down, UNAVAILABLE, connection reset mid-stream)
    would propagate out of `_execute_job_via_runner` into `AnalysisWorker._loop`,
    which has no try/except of its own -- permanently killing the worker thread.
    """
    import socket

    # Grab a genuinely free port and never listen on it.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        free_port = probe.getsockname()[1]

    job = _claimed_job(store)
    grpc_web_config = web_config.model_copy(
        update={
            "runner_grpc_target": f"127.0.0.1:{free_port}",
        }
    )

    worker._execute_job_via_runner(job, store, grpc_web_config)

    final = store.get(int(job["id"]))
    assert final["state"] == jobs.FAILED
    assert final["error"]


def test_execute_job_grpc_mode_stage_a_failure_marks_failed(
    store: JobStore, web_config: WebConfig
) -> None:
    """A terminal error seen before RUNNER ever enters stage B is a hard failure
    (mirrors test_execute_job_fetch_failure_marks_failed's in-process assertions)."""
    from league_stats_rpc.v1 import common_pb2, runner_pb2

    results = [
        runner_pb2.StageResult(
            job_id="scripted-1",
            stage=common_pb2.STAGE_A,
            detail="Looking up match history…",
            current=0,
            total=0,
        ),
        runner_pb2.StageResult(
            job_id="scripted-1",
            stage=common_pb2.STAGE_A,
            error="network down",
            final=True,
        ),
    ]

    job = _run_scripted_grpc_job(store, web_config, results)

    final = store.get(int(job["id"]))
    assert final["state"] == jobs.FAILED
    assert "network down" in final["error"]
    assert store.get_player("test_euw")["base_completed_at"] is None


def test_execute_job_grpc_mode_soft_peer_failure_marks_done(
    store: JobStore, web_config: WebConfig
) -> None:
    """A terminal error seen after RUNNER has entered stage B is the same "soft"
    peer failure the in-process path produces (mirrors
    test_execute_job_peer_failure_is_soft's assertions): job still finishes
    DONE, error is set, and mark_player_peer_failed is reflected in the
    registry."""
    from league_stats_rpc.v1 import common_pb2, runner_pb2

    results = [
        runner_pb2.StageResult(
            job_id="scripted-1",
            stage=common_pb2.STAGE_A,
            detail="Analyzing Viktor Middle (1/1)",
            current=1,
            total=1,
        ),
        runner_pb2.StageResult(
            job_id="scripted-1",
            stage=common_pb2.STAGE_B,
            detail="Comparing you to players at your rank…",
        ),
        runner_pb2.StageResult(
            job_id="scripted-1",
            stage=common_pb2.STAGE_B,
            detail="Report complete",
            error="Rank comparison failed: riot exploded",
            final=True,
        ),
    ]

    job = _run_scripted_grpc_job(store, web_config, results)

    final = store.get(int(job["id"]))
    assert final["state"] == jobs.DONE
    assert "Rank comparison failed" in final["error"]

    player = store.get_player("test_euw")
    assert player["base_completed_at"] is not None
    assert player["peer_completed_at"] is None
    assert player["peer_failed"] == 1


def test_execute_job_grpc_mode_stage_b_dropped_still_registers_player_at_done() -> None:
    """Regression test for the production bug where a dropped Stage-B
    `StreamJobProgress` event (e.g. a `docker-compose` restart racing the
    stream between api-ui and RUNNER) permanently lost the player-registry
    write. RUNNER finishes the job and reaches a terminal `final` message on
    its own shared volume regardless of whether that one stream event
    survived; before `_execute_job_via_runner`'s DONE-time safety net, the
    only `store.upsert_player` call lived inside the `STAGE_B` branch, so a
    stream that never carries a `STAGE_B` `StageResult` (simulated here by
    simply never yielding one) reached `job_states.DONE` with no registry row
    at all: `can_watch` stayed `False` forever, and unwatch 404'd with
    "Unknown player" even though the report existed. Uses its own fresh
    `JobStore`/`WebConfig` (not the shared fixtures) specifically so "test_euw"
    starts out with *no* pre-existing registry row, proving the row is
    created from scratch by the safety net alone.
    """
    from league_stats_rpc.v1 import common_pb2, runner_pb2

    fresh_store = JobStore(mongomock.MongoClient())
    try:
        assert fresh_store.get_player("test_euw") is None

        results = [
            runner_pb2.StageResult(
                job_id="scripted-1",
                stage=common_pb2.STAGE_A,
                detail="Analyzing Viktor Middle (1/1)",
                current=1,
                total=1,
            ),
            # No STAGE_B StageResult at all -- the dropped-event simulation --
            # yet the stream still ends with a clean terminal message.
            runner_pb2.StageResult(
                job_id="scripted-1",
                stage=common_pb2.STAGE_A,
                detail="Report complete",
                final=True,
            ),
        ]

        fresh_web_config = WebConfig(output_dir=Path("/tmp/does-not-matter"))
        job = _run_scripted_grpc_job(fresh_store, fresh_web_config, results)

        final = fresh_store.get(int(job["id"]))
        assert final["state"] == jobs.DONE
        assert final["error"] == ""

        player = fresh_store.get_player("test_euw")
        assert player is not None
        assert player["riot_id"] == "Test"
        assert player["tagline"] == "EUW"
        assert player["region"] == "euw1"
        assert player["base_completed_at"] is not None
        assert player["peer_completed_at"] is not None
    finally:
        fresh_store.close()


def test_execute_job_grpc_mode_preserves_existing_enrichment_when_runner_sends_none(
    store: JobStore, web_config: WebConfig
) -> None:
    """RUNNER's payload_json is the only channel for resolved icon/rank data; when
    a run doesn't send one at all, a prior run's profile_icon_id/solo-rank data in
    the registry must survive. JobStore.upsert_player does a wholesale players_json
    overwrite on conflict, so skipping the merge step would silently wipe this out
    on every grpc-mode run that doesn't itself re-resolve rank data.
    """
    from league_stats_rpc.v1 import common_pb2, runner_pb2

    store.upsert_player(
        slug="test_euw",
        riot_id="Test",
        tagline="EUW",
        region="euw1",
        players=[
            {
                "riot_id": "Test",
                "tagline": "EUW",
                "profile_icon_id": 99,
                "solo_tier": "GOLD",
                "solo_rank": "II",
                "solo_lp": 40,
            }
        ],
    )

    results = [
        runner_pb2.StageResult(
            job_id="scripted-1",
            stage=common_pb2.STAGE_A,
            detail="Analyzing Viktor Middle (1/1)",
            current=1,
            total=1,
        ),
        runner_pb2.StageResult(
            job_id="scripted-1", stage=common_pb2.STAGE_B, detail="Comparing…"
        ),
        runner_pb2.StageResult(
            job_id="scripted-1", stage=common_pb2.STAGE_B, detail="Report complete", final=True
        ),
    ]

    job = _run_scripted_grpc_job(store, web_config, results)

    final = store.get(int(job["id"]))
    assert final["state"] == jobs.DONE

    player = store.get_player("test_euw")
    entry = next(p for p in player["players"] if p["riot_id"] == "Test")
    assert entry["profile_icon_id"] == 99
    assert entry["solo_tier"] == "GOLD"
    assert entry["solo_rank"] == "II"
    assert entry["solo_lp"] == 40


def test_execute_job_grpc_mode_round_trips_resolved_player_data_via_payload_json(
    store: JobStore, web_config: WebConfig
) -> None:
    """RunnerJobAdapter.upsert_player pushes RUNNER's freshly-resolved roster
    through StageResult.payload_json (an existing, previously-unused proto field);
    _execute_job_via_runner must read it back out and write it into the local
    registry, instead of relying only on the locally-resolved (icon/rank-less)
    fallback roster.
    """
    import json as _json

    from league_stats_rpc.v1 import common_pb2, runner_pb2

    resolved = [
        {
            "riot_id": "Test",
            "tagline": "EUW",
            "profile_icon_id": 4242,
            "solo_tier": "DIAMOND",
            "solo_rank": "III",
            "solo_lp": 77,
        }
    ]

    results = [
        runner_pb2.StageResult(
            job_id="scripted-1",
            stage=common_pb2.STAGE_A,
            payload_json=_json.dumps(resolved),
        ),
        runner_pb2.StageResult(
            job_id="scripted-1",
            stage=common_pb2.STAGE_A,
            detail="Analyzing Viktor Middle (1/1)",
            current=1,
            total=1,
        ),
        runner_pb2.StageResult(
            job_id="scripted-1", stage=common_pb2.STAGE_B, detail="Comparing…"
        ),
        runner_pb2.StageResult(
            job_id="scripted-1", stage=common_pb2.STAGE_B, detail="Report complete", final=True
        ),
    ]

    job = _run_scripted_grpc_job(store, web_config, results)

    final = store.get(int(job["id"]))
    assert final["state"] == jobs.DONE

    player = store.get_player("test_euw")
    entry = next(p for p in player["players"] if p["riot_id"] == "Test")
    assert entry["profile_icon_id"] == 4242
    assert entry["solo_tier"] == "DIAMOND"
    assert entry["solo_rank"] == "III"
    assert entry["solo_lp"] == 77


# --------------------------------------------------------- peers_mode removal (Task 3, Phase 9)


def test_execute_job_always_routes_stage_bs_peer_resolution_through_grpc(
    store: JobStore, web_config: WebConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Phase 9 deleted `peers_mode` and its in-process `build_peer_for_pool`
    branch: `_run_stage_b` now always resolves peers by calling
    `_build_peer_for_pool_via_grpc`, unconditionally, with no config flag
    left to check. Proves the grpc path actually runs and its result feeds
    the second, peer-aware render -- not merely that no in-process branch
    exists to accidentally hit (there is none left in the source to hit)."""
    job = _claimed_job(store)
    grpc_peer_calls = {"n": 0}

    def grpc_peer_stub(
        services: Any, batch: Any, pool: Any, ranked: Any, web_config: Any, **kwargs: Any
    ) -> Any:
        grpc_peer_calls["n"] += 1
        calls.append("peer")
        return object()

    calls = _patch_pipeline(monkeypatch, _build_peer_for_pool_via_grpc=grpc_peer_stub)

    worker.execute_job(job, store, web_config)

    final = store.get(int(job["id"]))
    assert final["state"] == jobs.DONE
    assert final["error"] == ""
    assert grpc_peer_calls["n"] == 1
    assert calls == ["fetch", "prepare", "ranked", "analyze(peer=False)", "peer", "analyze(peer=True)"]


# ------------------------------------------------- _build_peer_for_pool_via_grpc


class _FakeRecord:
    """Minimal stand-in for `MatchRecord`; `to_row()` is used by the grpc peer
    path (`matches_df = pd.DataFrame([r.to_row() for r in records])`), and
    `game_creation_ms`/`patch` are used by `current_patch` (`analysis/peer/
    comparison.py`), which `_build_peer_for_pool_via_grpc` calls to populate
    `RequestBaselineRequest.patch` (finding 1 of the final whole-branch review)."""

    def __init__(
        self, *, game_creation_ms: int = 0, patch: str = "14.1", **row: Any
    ) -> None:
        self._row = row
        self.game_creation_ms = game_creation_ms
        self.patch = patch

    def to_row(self) -> dict[str, Any]:
        return self._row


def _fake_services_for_grpc_peer(min_games: int = 1) -> Any:
    from league_stats_common.core.config import AppConfig

    config = AppConfig(
        riot_id="Test",
        tagline="EUW",
        api_key="RGAPI-test",
        min_games=min_games,
        platform="euw1",
    )
    # `_peer_comparison_from_baseline` calls `collect_user_history_peers(store, ...)`,
    # which iterates `store.iter_match_ids(exclude_puuid)` -- an empty iterator keeps
    # it a no-op (no history rows) without needing a real MatchStore in these tests.
    store = SimpleNamespace(iter_match_ids=lambda puuid: iter(()))
    return SimpleNamespace(config=config, store=store)


def _start_peers_server(servicer: Any) -> tuple[Any, int]:
    from concurrent import futures

    import grpc

    from league_stats_rpc.v1 import peers_pb2_grpc

    server = grpc.server(futures.ThreadPoolExecutor(max_workers=4))
    peers_pb2_grpc.add_PeersServiceServicer_to_server(servicer, server)
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()
    return server, port


def _peer_records() -> list[_FakeRecord]:
    return [
        _FakeRecord(win=1, kda=4.0, dpm=500.0, deaths=3, cspm=6.0)
        for _ in range(3)
    ]


def test_build_peer_for_pool_via_grpc_uses_cached_baseline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PEERS' fast path (`cached=True`) must resolve without waiting on any
    async callback."""
    import json as _json
    from dataclasses import asdict

    from league_stats_peers.analysis.peer.baseline import PeerBaseline
    from league_stats_common.core.models import RankedEntry
    from league_stats_rpc.v1 import peers_pb2, peers_pb2_grpc

    baseline = PeerBaseline(
        metrics={"kda": 3.0, "win": 0.5},
        games=60,
        players=10,
        source="peer store",
        confidence="high",
        fallback_level=0,
    )

    class _CachedPeersServicer(peers_pb2_grpc.PeersServiceServicer):
        def RequestBaseline(self, request, context):
            self.request = request
            return peers_pb2.RequestBaselineResponse(
                request_id="req-1", cached=True, baseline_json=_json.dumps(asdict(baseline))
            )

    servicer = _CachedPeersServicer()
    server, port = _start_peers_server(servicer)
    try:
        web_config = WebConfig(peers_grpc_target=f"127.0.0.1:{port}")
        services = _fake_services_for_grpc_peer()
        monkeypatch.setattr(worker, "group_records", lambda records, champ, role: _peer_records())
        batch = BuildBatch(
            pools=[], records=[], manifest_builds=[], primary_puuid="my-puuid"
        )
        pool = BuildPool(champion="Ahri", role="MIDDLE", games=25)
        ranked = RankedEntry(tier="GOLD", rank="II", league_points=45, wins=10, losses=10)

        result = worker._build_peer_for_pool_via_grpc(services, batch, pool, ranked, web_config)
    finally:
        server.stop(grace=None)

    assert result is not None
    assert servicer.request.champion == "Ahri"
    assert servicer.request.lane == "MIDDLE"
    assert servicer.request.platform == "euw1"
    assert servicer.request.exclude_puuid == "my-puuid"
    assert result.peer_games == 60
    assert result.peer_players == 10
    assert result.champion == "Ahri"
    assert result.role == "MIDDLE"


def test_build_peer_for_pool_via_grpc_waits_for_async_callback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PEERS' slow path (`cached=False`) must block until
    `resolve_peer_baseline_notification` (RUNNER's real
    `NotifyPeerBaselineReady` handler) delivers the result for the matching
    `request_id`."""
    import json as _json
    import threading as _threading
    import time as _time
    from dataclasses import asdict

    from league_stats_peers.analysis.peer.baseline import PeerBaseline
    from league_stats_common.core.models import RankedEntry
    from league_stats_rpc.v1 import peers_pb2, peers_pb2_grpc

    baseline = PeerBaseline(
        metrics={"kda": 5.0},
        games=80,
        players=15,
        source="live sample",
        confidence="medium",
        fallback_level=2,
    )

    class _SlowPeersServicer(peers_pb2_grpc.PeersServiceServicer):
        def RequestBaseline(self, request, context):
            return peers_pb2.RequestBaselineResponse(request_id="req-async-1", cached=False)

    server, port = _start_peers_server(_SlowPeersServicer())

    def _deliver_later() -> None:
        _time.sleep(0.2)
        worker.resolve_peer_baseline_notification(
            "req-async-1", baseline_json=_json.dumps(asdict(baseline)), error=""
        )

    _threading.Thread(target=_deliver_later, daemon=True).start()

    try:
        web_config = WebConfig(peers_grpc_target=f"127.0.0.1:{port}")
        services = _fake_services_for_grpc_peer()
        monkeypatch.setattr(worker, "group_records", lambda records, champ, role: _peer_records())
        batch = BuildBatch(pools=[], records=[], manifest_builds=[], primary_puuid="my-puuid")
        pool = BuildPool(champion="Ahri", role="MIDDLE", games=25)
        ranked = RankedEntry(tier="GOLD", rank="II", league_points=45, wins=10, losses=10)

        result = worker._build_peer_for_pool_via_grpc(services, batch, pool, ranked, web_config)
    finally:
        server.stop(grace=None)

    assert result is not None
    assert result.peer_games == 80
    assert result.fallback_level == 2


def test_build_peer_for_pool_via_grpc_times_out_if_peers_never_calls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A `cached=False` response with no matching `NotifyPeerBaselineReady`
    callback must not hang stage B forever -- it must give up and return
    `None` after `_PEERS_BASELINE_WAIT_TIMEOUT_S`, and must clean up its own
    waiter entry so it doesn't leak."""
    from league_stats_common.core.models import RankedEntry
    from league_stats_rpc.v1 import peers_pb2, peers_pb2_grpc

    class _NeverCallsBackServicer(peers_pb2_grpc.PeersServiceServicer):
        def RequestBaseline(self, request, context):
            return peers_pb2.RequestBaselineResponse(request_id="req-timeout-1", cached=False)

    server, port = _start_peers_server(_NeverCallsBackServicer())
    monkeypatch.setattr(worker, "_PEERS_BASELINE_WAIT_TIMEOUT_S", 0.2)

    try:
        web_config = WebConfig(peers_grpc_target=f"127.0.0.1:{port}")
        services = _fake_services_for_grpc_peer()
        monkeypatch.setattr(worker, "group_records", lambda records, champ, role: _peer_records())
        batch = BuildBatch(pools=[], records=[], manifest_builds=[], primary_puuid="my-puuid")
        pool = BuildPool(champion="Ahri", role="MIDDLE", games=25)
        ranked = RankedEntry(tier="GOLD", rank="II", league_points=45, wins=10, losses=10)

        result = worker._build_peer_for_pool_via_grpc(services, batch, pool, ranked, web_config)
    finally:
        server.stop(grace=None)

    assert result is None
    assert "req-timeout-1" not in worker._peer_baseline_waiters


def test_build_peer_for_pool_via_grpc_notices_cancellation_within_the_poll_interval(
    monkeypatch: pytest.MonkeyPatch, store: JobStore
) -> None:
    """A cancellation requested during the grpc-mode wait must be noticed
    within `_PEERS_BASELINE_POLL_INTERVAL_S`, not the full
    `_PEERS_BASELINE_WAIT_TIMEOUT_S` -- finding 2 of the final whole-branch
    review. Previously the wait was one long blocking
    `waiter.get(timeout=900)` call with no cancellation poll in between."""
    import time as _time

    from league_stats_common.core.models import RankedEntry
    from league_stats_runner.progress import JobCancelled
    from league_stats_rpc.v1 import peers_pb2, peers_pb2_grpc

    class _NeverCallsBackServicer(peers_pb2_grpc.PeersServiceServicer):
        def RequestBaseline(self, request, context):
            return peers_pb2.RequestBaselineResponse(request_id="req-cancel-1", cached=False)

    server, port = _start_peers_server(_NeverCallsBackServicer())
    # Long overall budget, short poll interval -- if cancellation weren't
    # polled for, this test would have to wait the full (long) timeout below
    # instead of noticing the cancellation almost immediately.
    monkeypatch.setattr(worker, "_PEERS_BASELINE_WAIT_TIMEOUT_S", 900.0)
    monkeypatch.setattr(worker, "_PEERS_BASELINE_POLL_INTERVAL_S", 0.1)

    job = _claimed_job(store)
    job_id = int(job["id"])

    def _cancel_shortly() -> None:
        _time.sleep(0.15)
        store.cancel(job_id)

    import threading as _threading

    _threading.Thread(target=_cancel_shortly, daemon=True).start()

    try:
        web_config = WebConfig(peers_grpc_target=f"127.0.0.1:{port}")
        services = _fake_services_for_grpc_peer()
        monkeypatch.setattr(worker, "group_records", lambda records, champ, role: _peer_records())
        batch = BuildBatch(pools=[], records=[], manifest_builds=[], primary_puuid="my-puuid")
        pool = BuildPool(champion="Ahri", role="MIDDLE", games=25)
        ranked = RankedEntry(tier="GOLD", rank="II", league_points=45, wins=10, losses=10)

        started = _time.monotonic()
        with pytest.raises(JobCancelled):
            worker._build_peer_for_pool_via_grpc(
                services, batch, pool, ranked, web_config, store=store, job_id=job_id
            )
        elapsed = _time.monotonic() - started
    finally:
        server.stop(grace=None)

    assert elapsed < 5.0, "cancellation should be noticed within a couple poll intervals"
    assert "req-cancel-1" not in worker._peer_baseline_waiters


def test_build_peer_for_pool_via_grpc_returns_none_when_peers_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RUNNER must not crash or hang stage B when PEERS is unreachable --
    a soft failure (None) for this one build, same as any other
    peer-resolution exception."""
    import socket

    from league_stats_common.core.models import RankedEntry

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        free_port = probe.getsockname()[1]

    monkeypatch.setattr(worker, "_PEERS_REQUEST_TIMEOUT_S", 1.0)
    web_config = WebConfig(peers_grpc_target=f"127.0.0.1:{free_port}")
    services = _fake_services_for_grpc_peer()
    monkeypatch.setattr(worker, "group_records", lambda records, champ, role: _peer_records())
    batch = BuildBatch(pools=[], records=[], manifest_builds=[], primary_puuid="my-puuid")
    pool = BuildPool(champion="Ahri", role="MIDDLE", games=25)
    ranked = RankedEntry(tier="GOLD", rank="II", league_points=45, wins=10, losses=10)

    result = worker._build_peer_for_pool_via_grpc(services, batch, pool, ranked, web_config)

    assert result is None


def test_build_peer_for_pool_via_grpc_survives_notification_arriving_before_waiter_registers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression test for fix round 1's Critical finding: the lost-wakeup race
    between PEERS' async callback and RUNNER's waiter registration.

    `concurrent.futures.Future.add_done_callback` (PEERS' own `_get_or_submit`/
    `RequestBaseline`, `peers/service.py`) fires SYNCHRONOUSLY, on the calling
    thread, if the future is already done when the callback is attached. That
    means PEERS can call back into `RunnerServicer.NotifyPeerBaselineReady`
    *before* its own `RequestBaseline` response (`cached=False` + `request_id`)
    has even been returned to RUNNER -- i.e. before
    `_build_peer_for_pool_via_grpc` has had any chance to call
    `_register_peer_baseline_waiter` for that `request_id`. Without the
    `_peer_baseline_orphans` buffer (`web/worker.py`), that notification would
    be silently dropped and this function would block the full
    `_PEERS_BASELINE_WAIT_TIMEOUT_S` for a baseline that had already arrived.

    This test forces that exact ordering deterministically -- not by relying
    on real scheduling/timeout timing (which would be flaky) -- via a fake
    PeersServicer whose `RequestBaseline` calls the real
    `RunnerServicer.NotifyPeerBaselineReady` itself, synchronously, BEFORE
    returning its own `cached=False` response. Everything downstream of that
    forced ordering is real: a real `RunnerServicer` instance behind a real
    gRPC server, a real gRPC call into it, and the actual
    `_build_peer_for_pool_via_grpc` function under test -- proving the orphan
    buffer closes the real, end-to-end race, not just a mocked-out piece of it.
    """
    import json as _json
    from concurrent import futures
    from dataclasses import asdict

    import grpc

    from league_stats_peers.analysis.peer.baseline import PeerBaseline
    from league_stats_common.core.models import RankedEntry
    from league_stats_runner.service import RunnerServicer
    from league_stats_rpc.v1 import peers_pb2, peers_pb2_grpc, runner_pb2, runner_pb2_grpc

    baseline = PeerBaseline(
        metrics={"kda": 6.0},
        games=100,
        players=20,
        source="live sample",
        confidence="high",
        fallback_level=2,
    )
    request_id = "req-race-1"

    runner_servicer = RunnerServicer(web_config=WebConfig())
    runner_server = grpc.server(futures.ThreadPoolExecutor(max_workers=4))
    runner_pb2_grpc.add_RunnerServiceServicer_to_server(runner_servicer, runner_server)
    runner_port = runner_server.add_insecure_port("127.0.0.1:0")
    runner_server.start()

    class _RaceyPeersServicer(peers_pb2_grpc.PeersServiceServicer):
        """Delivers PEERS' async callback BEFORE its own `RequestBaseline`
        response returns -- forcing the lost-wakeup ordering deterministically,
        exactly as `Future.add_done_callback` firing synchronously can in
        production (see this test's own docstring)."""

        def RequestBaseline(self, request, context):
            with grpc.insecure_channel(f"127.0.0.1:{runner_port}") as channel:
                stub = runner_pb2_grpc.RunnerServiceStub(channel)
                stub.NotifyPeerBaselineReady(
                    runner_pb2.PeerBaselineReadyRequest(
                        request_id=request_id,
                        champion=request.champion,
                        lane=request.lane,
                        rank=request.rank,
                        baseline_json=_json.dumps(asdict(baseline)),
                        error="",
                    )
                )
            return peers_pb2.RequestBaselineResponse(request_id=request_id, cached=False)

    peers_server, peers_port = _start_peers_server(_RaceyPeersServicer())
    try:
        web_config = WebConfig(peers_grpc_target=f"127.0.0.1:{peers_port}")
        services = _fake_services_for_grpc_peer()
        monkeypatch.setattr(worker, "group_records", lambda records, champ, role: _peer_records())
        batch = BuildBatch(pools=[], records=[], manifest_builds=[], primary_puuid="my-puuid")
        pool = BuildPool(champion="Ahri", role="MIDDLE", games=25)
        ranked = RankedEntry(tier="GOLD", rank="II", league_points=45, wins=10, losses=10)

        result = worker._build_peer_for_pool_via_grpc(services, batch, pool, ranked, web_config)
    finally:
        peers_server.stop(grace=None)
        runner_server.stop(grace=None)

    assert result is not None
    assert result.peer_games == 100
    assert result.fallback_level == 2
    # The orphan must have been claimed by _register_peer_baseline_waiter, not
    # left sitting in the buffer.
    assert request_id not in worker._peer_baseline_orphans
    assert request_id not in worker._peer_baseline_waiters


def test_build_peer_for_pool_via_grpc_returns_none_on_peers_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A `RequestBaselineResponse.error` (PEERS could not enqueue/resolve at
    all) must be a soft `None`, not an exception -- and must not register a
    waiter, since PEERS explicitly documents no callback ever follows this case."""
    from league_stats_common.core.models import RankedEntry
    from league_stats_rpc.v1 import peers_pb2, peers_pb2_grpc

    class _ErrorPeersServicer(peers_pb2_grpc.PeersServiceServicer):
        def RequestBaseline(self, request, context):
            return peers_pb2.RequestBaselineResponse(
                request_id="req-error-1", cached=True, error="no peer baseline available"
            )

    server, port = _start_peers_server(_ErrorPeersServicer())
    try:
        web_config = WebConfig(peers_grpc_target=f"127.0.0.1:{port}")
        services = _fake_services_for_grpc_peer()
        monkeypatch.setattr(worker, "group_records", lambda records, champ, role: _peer_records())
        batch = BuildBatch(pools=[], records=[], manifest_builds=[], primary_puuid="my-puuid")
        pool = BuildPool(champion="Ahri", role="MIDDLE", games=25)
        ranked = RankedEntry(tier="GOLD", rank="II", league_points=45, wins=10, losses=10)

        result = worker._build_peer_for_pool_via_grpc(services, batch, pool, ranked, web_config)
    finally:
        server.stop(grace=None)

    assert result is None
    assert "req-error-1" not in worker._peer_baseline_waiters
    assert "req-error-1" not in worker._peer_baseline_orphans


def test_build_peer_for_pool_via_grpc_returns_none_below_min_games(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same `min_games` gate as the in-process path -- must not even attempt
    a PEERS call when there aren't enough games."""
    from league_stats_common.core.models import RankedEntry

    web_config = WebConfig(peers_grpc_target="127.0.0.1:1")  # never dialed
    services = _fake_services_for_grpc_peer(min_games=99)
    monkeypatch.setattr(worker, "group_records", lambda records, champ, role: _peer_records())
    batch = BuildBatch(pools=[], records=[], manifest_builds=[], primary_puuid="my-puuid")
    pool = BuildPool(champion="Ahri", role="MIDDLE", games=25)
    ranked = RankedEntry(tier="GOLD", rank="II", league_points=45, wins=10, losses=10)

    result = worker._build_peer_for_pool_via_grpc(services, batch, pool, ranked, web_config)

    assert result is None


# -------------------------------- trace_id JobStore hand-off (Phase 6 final review, Finding 1)


@pytest.fixture(autouse=True)
def _reset_trace_id_for_worker_tests():
    """Every trace_id test starts from, and leaves, an unset ContextVar."""
    from league_stats_common.utils import set_trace_id

    set_trace_id("")
    yield
    set_trace_id("")


def test_enqueue_persists_trace_id_and_claim_next_returns_it(store: JobStore) -> None:
    """`JobStore.enqueue`'s new `trace_id` column round-trips through `claim_next`,
    the same column both a real HTTP request (`app.py`) and CronWatch's
    `WatchPoller._enqueue_refresh` now write."""
    job, created = store.enqueue(
        kind=jobs.JOB_KIND_ANALYZE,
        riot_id="Test",
        tagline="EUW",
        region="euw1",
        player_slug="test_euw",
        trace_id="enqueue-time-trace",
    )
    assert created
    assert job["trace_id"] == "enqueue-time-trace"

    claimed = store.claim_next()
    assert claimed is not None
    assert claimed["trace_id"] == "enqueue-time-trace"


def test_enqueue_without_trace_id_defaults_to_empty_string(store: JobStore) -> None:
    """A caller that never had a trace_id (e.g. a pre-migration test row) must
    not crash `enqueue`/`claim_next` -- the column defaults to ``''``."""
    job, _created = store.enqueue(
        kind=jobs.JOB_KIND_ANALYZE,
        riot_id="Test",
        tagline="EUW",
        region="euw1",
        player_slug="test_euw",
    )
    assert job["trace_id"] == ""


def test_execute_job_via_runner_restores_trace_id_from_job_before_delegation(
    store: JobStore, web_config: WebConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`_execute_job_via_runner` must call `set_trace_id` from the claimed
    job's `trace_id` column before doing anything else -- in particular
    before it opens a channel to RUNNER, so `TraceClientInterceptor` attaches
    the real originating trace_id instead of whatever this long-lived worker
    thread happened to have left over from a previous job. Verified by
    patching the gRPC stub call `_execute_job_via_runner` makes right after
    the restore, so no real network work is needed."""
    import grpc as grpc_module

    from league_stats_common.utils import current_trace_id, set_trace_id
    from league_stats_rpc.v1 import runner_pb2_grpc

    set_trace_id("stale-trace-from-a-previous-job")
    store.enqueue(
        kind=jobs.JOB_KIND_ANALYZE,
        riot_id="Test",
        tagline="EUW",
        region="euw1",
        player_slug="test_euw",
        trace_id="this-jobs-real-trace",
    )
    set_trace_id("stale-trace-from-a-previous-job")
    job = store.claim_next()
    assert job is not None

    observed: list[str] = []

    class _Boom(grpc_module.RpcError):
        pass

    real_init = runner_pb2_grpc.RunnerServiceStub.__init__

    def _patched_init(self, channel):
        real_init(self, channel)

        def _fake_enqueue_job(request, timeout=None):
            observed.append(current_trace_id())
            raise _Boom("stop before any real network work")

        self.EnqueueJob = _fake_enqueue_job

    monkeypatch.setattr(runner_pb2_grpc.RunnerServiceStub, "__init__", _patched_init)

    worker._execute_job_via_runner(job, store, web_config)

    assert observed == ["this-jobs-real-trace"]


def test_execute_job_via_runner_leaves_trace_id_untouched_when_job_has_none(
    store: JobStore, web_config: WebConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A job dict with no `trace_id` (e.g. one built without going through
    `JobStore.enqueue`) must not clobber whatever trace_id is already set on
    this thread."""
    import grpc as grpc_module

    from league_stats_common.utils import current_trace_id, set_trace_id
    from league_stats_rpc.v1 import runner_pb2_grpc

    set_trace_id("set-by-caller")
    job = _claimed_job(store)
    job["trace_id"] = ""

    observed: list[str] = []

    class _Boom(grpc_module.RpcError):
        pass

    real_init = runner_pb2_grpc.RunnerServiceStub.__init__

    def _patched_init(self, channel):
        real_init(self, channel)

        def _fake_enqueue_job(request, timeout=None):
            observed.append(current_trace_id())
            raise _Boom("stop before any real network work")

        self.EnqueueJob = _fake_enqueue_job

    monkeypatch.setattr(runner_pb2_grpc.RunnerServiceStub, "__init__", _patched_init)

    worker._execute_job_via_runner(job, store, web_config)

    assert observed == ["set-by-caller"]


def test_trace_id_survives_jobstore_handoff_through_a_real_runner_server(
    store: JobStore, web_config: WebConfig, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """End-to-end proof for Finding 1: a job enqueued under a known trace_id
    (mimicking `app.py`'s `current_trace_id()`-sourced enqueue) makes RUNNER's
    own `execute_job` run -- on the background thread `RunnerServicer.EnqueueJob`
    spawns -- observe that exact trace_id, via a real `grpc.server` with
    `TraceServerInterceptor` registered exactly like `runner/__main__.py` does,
    and a real `RunnerServicer.EnqueueJob`/`_run_job` thread hand-off (not a
    scripted/mocked servicer).
    """
    from concurrent import futures

    import grpc

    from league_stats_common.infra.trace_context import TraceServerInterceptor
    from league_stats_runner import service as runner_service
    from league_stats_common.utils import current_trace_id, set_trace_id
    from league_stats_rpc.v1 import runner_pb2_grpc

    observed: list[str] = []

    def _recording_execute_job(job: dict[str, Any], adapter: Any, cfg: Any) -> None:
        observed.append(current_trace_id())
        # Emit a terminal event ourselves (skipping the real pipeline) so
        # StreamJobProgress's consumer below doesn't block waiting for one.
        import league_stats_common.infra.jobs as job_states

        adapter.set_state(job["id"], job_states.DONE, detail="stub")

    monkeypatch.setattr(runner_service, "execute_job", _recording_execute_job)

    runner_web_config = web_config.model_copy(update={"output_dir": tmp_path / "runner_output"})
    servicer = runner_service.RunnerServicer(web_config=runner_web_config)
    server = grpc.server(
        futures.ThreadPoolExecutor(max_workers=4),
        interceptors=[TraceServerInterceptor()],
    )
    runner_pb2_grpc.add_RunnerServiceServicer_to_server(servicer, server)
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()
    try:
        # Enqueue under a known originating trace_id, exactly like app.py's
        # submit_analysis passing trace_id=current_trace_id().
        set_trace_id("originating-http-trace-xyz")
        store.enqueue(
            kind=jobs.JOB_KIND_ANALYZE,
            riot_id="Test",
            tagline="EUW",
            region="euw1",
            player_slug="test_euw",
            trace_id=current_trace_id(),
        )
        # Simulate the worker thread being idle/reused from an earlier, unrelated
        # job before claiming this one -- proves the value comes from the job
        # row, not merely from this test having never cleared the ContextVar.
        set_trace_id("")
        job = store.claim_next()
        assert job is not None

        grpc_web_config = web_config.model_copy(
            update={"runner_grpc_target": f"127.0.0.1:{port}"}
        )
        worker._execute_job_via_runner(job, store, grpc_web_config)
    finally:
        server.stop(grace=None)

    assert observed == ["originating-http-trace-xyz"]


# --------------------------- Phase 8, Task 1 fix regression (api-ui compose pin)


def test_via_grpc_peer_resolution_works_against_raw_match_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression test, reduced (Phase 9, Task 4): originally proved both
    halves of the Task 1 CRITICAL fix (see
    `.superpowers/sdd/2026-08-20-microservices-phase8/task-1-report.md`) --
    that `RawMatchStore` (the only store `_build_job_services` has constructed
    since Task 1 deleted `MatchStore`) does not implement `MatchStore`'s
    former peer-game methods, so the in-process peer path
    (`build_peer_for_pool`) crashed with `AttributeError` against it, while
    the gRPC path (`_build_peer_for_pool_via_grpc`) worked fine.

    Phase 9's final dead-code sweep deleted `build_peer_for_pool` itself: its
    apparent second caller, `orchestrator.run_all_builds`, was confirmed to
    have zero production callers of its own (the CLI shim that used to invoke
    it was deleted in commit `33bd81b`, predating this migration), so
    `build_peer_for_pool` had exactly one real call site all along --
    `_run_stage_b`'s branch, which Phase 9 already removed. There is no
    in-process peer path left anywhere to reproduce the original crash
    against, so only the "fix still works" half survives here: routing a real
    `RawMatchStore`-backed `Services` object through the gRPC path
    (`_build_peer_for_pool_via_grpc`, the only path `_run_stage_b` calls)
    against a real, in-process `PeersServicer` server resolves a peer
    comparison successfully -- `finish_peer_comparison`'s own store use
    (`collect_user_history_peers` -> `store.iter_match_ids`/`load_match`) is
    exactly the raw-match surface `RawMatchStore` *does* implement, so
    nothing here needs a mocked-out store.
    """
    from concurrent import futures
    from unittest.mock import MagicMock

    import grpc
    import mongomock

    from league_stats_common.core.config import AppConfig
    from league_stats_common.core.models import RankedEntry
    from league_stats_peers import service as peers_service
    from league_stats_peers.analysis.peer.ingest import ingest_match
    from league_stats_peers.infra.peer_sample_store import PeerSampleStore
    from league_stats_runner.infra.raw_match_store import RawMatchStore
    from league_stats_rpc.v1 import peers_pb2_grpc
    from tests.fixtures import make_match

    raw_match_store = RawMatchStore(mongomock.MongoClient(), db_name="league_stats_test")
    app_config = AppConfig(
        riot_id="Test", tagline="EUW", api_key="RGAPI-test", min_games=1, platform="euw1"
    )
    services = SimpleNamespace(config=app_config, store=raw_match_store, client=MagicMock())
    monkeypatch.setattr(worker, "group_records", lambda records, champ, role: _peer_records())
    batch = BuildBatch(pools=[], records=[], manifest_builds=[], primary_puuid="my-puuid")
    pool = BuildPool(champion="Ahri", role="MIDDLE", games=25)
    ranked = RankedEntry(tier="GOLD", rank="II", league_points=45, wins=10, losses=10)

    # The gRPC path, against a real PEERS gRPC server backed by a real
    # (mongomock) PeerSampleStore.
    peer_store = PeerSampleStore(mongomock.MongoClient(), db_name="league_stats_peer_test")
    for index in range(60):
        match = make_match()
        match["info"]["participants"][1]["puuid"] = f"peer-{index}"
        ingest_match(
            peers_service._PeerStoreAdapter(peer_store), f"EUW1_{index}", match, "euw1"
        )
        peer_store.set_puuid_rank(f"peer-{index}", "GOLD", "II")

    servicer = peers_service.PeersServicer(
        peer_store=peer_store,
        # `platform` must be a real string, not a bare `MagicMock()` (whose
        # `.platform` would itself default to an auto-generated child mock):
        # the batch scheduler's live-cache round trip (`SamplingTask.
        # build_snapshot`/`write_live_cache`) formats it into a cache key via
        # `.lower()`, and Ahri/MIDDLE has no store data here (the seeded rows
        # are all LeeSin/JUNGLE) so this test's request always falls through
        # to level 2.
        riot_client_factory=lambda platform: MagicMock(platform=platform),
        fast_path_timeout_s=3.0,
    )
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=4))
    peers_pb2_grpc.add_PeersServiceServicer_to_server(servicer, server)
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()
    try:
        grpc_web_config = WebConfig(peers_grpc_target=f"127.0.0.1:{port}")
        result = worker._build_peer_for_pool_via_grpc(
            services, batch, pool, ranked, grpc_web_config
        )
    finally:
        server.stop(grace=None)

    assert result is not None
    assert result.champion == "Ahri"
    assert result.role == "MIDDLE"


# ---------------------------------------------------------- new observability metrics


def _sample_value(metric_name: str, labels: dict[str, str] | None = None) -> float | None:
    from prometheus_client import generate_latest, REGISTRY
    from prometheus_client.parser import text_string_to_metric_families

    labels = labels or {}
    text = generate_latest(REGISTRY).decode("utf-8")
    for family in text_string_to_metric_families(text):
        for sample in family.samples:
            if sample.name == metric_name and all(
                sample.labels.get(k) == v for k, v in labels.items()
            ):
                return sample.value
    return None


def test_execute_job_records_stage_durations(
    store: JobStore, web_config: WebConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`RUNNER_STAGE_DURATION` must be observed once per stage (fetch/analyze/
    peer) on every job run, so a slow stage is distinguishable from the
    others -- previously all stages were folded into one unlabeled
    `runner_job_duration_seconds` histogram."""
    job = _claimed_job(store)
    _patch_pipeline(monkeypatch)

    before_fetch = _sample_value(
        "runner_stage_duration_seconds_count", {"stage": "fetch"}
    ) or 0.0
    before_analyze = _sample_value(
        "runner_stage_duration_seconds_count", {"stage": "analyze"}
    ) or 0.0
    before_peer = _sample_value(
        "runner_stage_duration_seconds_count", {"stage": "peer"}
    ) or 0.0

    worker.execute_job(job, store, web_config)

    assert _sample_value("runner_stage_duration_seconds_count", {"stage": "fetch"}) == before_fetch + 1
    assert _sample_value("runner_stage_duration_seconds_count", {"stage": "analyze"}) == before_analyze + 1
    assert _sample_value("runner_stage_duration_seconds_count", {"stage": "peer"}) == before_peer + 1


def test_build_peer_for_pool_via_grpc_records_cached_hit_request_duration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A `cached=True` `RequestBaseline` response must be observed under
    `RUNNER_PEERS_REQUEST_DURATION{outcome="cached_hit"}` and
    `api_ui_outbound_call_duration_seconds{target="peers",operation="RequestBaseline",outcome="ok"}`."""
    import json as _json
    from dataclasses import asdict

    from league_stats_peers.analysis.peer.baseline import PeerBaseline
    from league_stats_common.core.models import RankedEntry
    from league_stats_rpc.v1 import peers_pb2, peers_pb2_grpc

    baseline = PeerBaseline(
        metrics={"kda": 3.0}, games=60, players=10, source="peer store",
        confidence="high", fallback_level=0,
    )

    class _CachedPeersServicer(peers_pb2_grpc.PeersServiceServicer):
        def RequestBaseline(self, request, context):
            return peers_pb2.RequestBaselineResponse(
                request_id="req-metric-1", cached=True, baseline_json=_json.dumps(asdict(baseline))
            )

    server, port = _start_peers_server(_CachedPeersServicer())
    try:
        web_config = WebConfig(peers_grpc_target=f"127.0.0.1:{port}")
        services = _fake_services_for_grpc_peer()
        monkeypatch.setattr(worker, "group_records", lambda records, champ, role: _peer_records())
        batch = BuildBatch(pools=[], records=[], manifest_builds=[], primary_puuid="my-puuid")
        pool = BuildPool(champion="Ahri", role="MIDDLE", games=25)
        ranked = RankedEntry(tier="GOLD", rank="II", league_points=45, wins=10, losses=10)

        before = _sample_value(
            "runner_peers_request_duration_seconds_count", {"outcome": "cached_hit"}
        ) or 0.0
        before_outbound = _sample_value(
            "api_ui_outbound_call_duration_seconds_count",
            {"target": "peers", "operation": "RequestBaseline", "outcome": "ok"},
        ) or 0.0

        result = worker._build_peer_for_pool_via_grpc(services, batch, pool, ranked, web_config)
    finally:
        server.stop(grace=None)

    assert result is not None
    after = _sample_value(
        "runner_peers_request_duration_seconds_count", {"outcome": "cached_hit"}
    )
    after_outbound = _sample_value(
        "api_ui_outbound_call_duration_seconds_count",
        {"target": "peers", "operation": "RequestBaseline", "outcome": "ok"},
    )
    assert after == before + 1
    assert after_outbound == before_outbound + 1


def test_build_peer_for_pool_via_grpc_records_async_wait_delivered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A `cached=False` response followed by a real `NotifyPeerBaselineReady`
    delivery must be observed under `RUNNER_PEERS_ASYNC_WAIT_DURATION{outcome="delivered"}`."""
    import json as _json
    import threading as _threading
    import time as _time
    from dataclasses import asdict

    from league_stats_peers.analysis.peer.baseline import PeerBaseline
    from league_stats_common.core.models import RankedEntry
    from league_stats_rpc.v1 import peers_pb2, peers_pb2_grpc

    baseline = PeerBaseline(
        metrics={"kda": 5.0}, games=80, players=15, source="live sample",
        confidence="medium", fallback_level=2,
    )

    class _SlowPeersServicer(peers_pb2_grpc.PeersServiceServicer):
        def RequestBaseline(self, request, context):
            return peers_pb2.RequestBaselineResponse(request_id="req-metric-async-1", cached=False)

    server, port = _start_peers_server(_SlowPeersServicer())

    def _deliver_later() -> None:
        _time.sleep(0.1)
        worker.resolve_peer_baseline_notification(
            "req-metric-async-1", baseline_json=_json.dumps(asdict(baseline)), error=""
        )

    _threading.Thread(target=_deliver_later, daemon=True).start()

    try:
        web_config = WebConfig(peers_grpc_target=f"127.0.0.1:{port}")
        services = _fake_services_for_grpc_peer()
        monkeypatch.setattr(worker, "group_records", lambda records, champ, role: _peer_records())
        batch = BuildBatch(pools=[], records=[], manifest_builds=[], primary_puuid="my-puuid")
        pool = BuildPool(champion="Ahri", role="MIDDLE", games=25)
        ranked = RankedEntry(tier="GOLD", rank="II", league_points=45, wins=10, losses=10)

        before = _sample_value(
            "runner_peers_async_wait_duration_seconds_count", {"outcome": "delivered"}
        ) or 0.0

        result = worker._build_peer_for_pool_via_grpc(services, batch, pool, ranked, web_config)
    finally:
        server.stop(grace=None)

    assert result is not None
    after = _sample_value(
        "runner_peers_async_wait_duration_seconds_count", {"outcome": "delivered"}
    )
    assert after == before + 1


# ------------------- progressive peer-comparison updates (design doc §3.2) -------------------


def test_build_peer_for_pool_via_grpc_keeps_waiting_past_the_first_interim_callback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Design "Progressive peer-comparison updates during live sampling" §3.2:
    an interim (`still_refining=True`) `NotifyPeerBaselineReady` callback must
    NOT end the wait -- `_build_peer_for_pool_via_grpc` must keep waiting on
    the same `request_id` for a later, terminal callback, calling `on_update`
    once per delivery.

    Fails pre-fix: the old wait loop returned as soon as ANY notification
    arrived, regardless of a `still_refining` flag it didn't even read --
    the second (terminal) delivery below would never be observed at all.
    """
    import json as _json
    import threading as _threading
    import time as _time
    from dataclasses import asdict

    from league_stats_peers.analysis.peer.baseline import PeerBaseline
    from league_stats_common.core.models import RankedEntry
    from league_stats_rpc.v1 import peers_pb2, peers_pb2_grpc

    interim_baseline = PeerBaseline(
        metrics={"kda": 4.0}, games=6, players=6, source="live sample",
        confidence="low", fallback_level=2, still_refining=True,
    )
    terminal_baseline = PeerBaseline(
        metrics={"kda": 5.0}, games=50, players=40, source="live sample",
        confidence="full", fallback_level=2, still_refining=False,
    )

    class _SlowPeersServicer(peers_pb2_grpc.PeersServiceServicer):
        def RequestBaseline(self, request, context):
            return peers_pb2.RequestBaselineResponse(request_id="req-progressive-1", cached=False)

    server, port = _start_peers_server(_SlowPeersServicer())

    def _deliver_later() -> None:
        _time.sleep(0.1)
        worker.resolve_peer_baseline_notification(
            "req-progressive-1",
            baseline_json=_json.dumps(asdict(interim_baseline)),
            error="",
            still_refining=True,
        )
        _time.sleep(0.1)
        worker.resolve_peer_baseline_notification(
            "req-progressive-1",
            baseline_json=_json.dumps(asdict(terminal_baseline)),
            error="",
            still_refining=False,
        )

    _threading.Thread(target=_deliver_later, daemon=True).start()

    updates: list[tuple[int, bool]] = []

    try:
        web_config = WebConfig(peers_grpc_target=f"127.0.0.1:{port}")
        services = _fake_services_for_grpc_peer()
        monkeypatch.setattr(worker, "group_records", lambda records, champ, role: _peer_records())
        batch = BuildBatch(pools=[], records=[], manifest_builds=[], primary_puuid="my-puuid")
        pool = BuildPool(champion="Ahri", role="MIDDLE", games=25)
        ranked = RankedEntry(tier="GOLD", rank="II", league_points=45, wins=10, losses=10)

        result = worker._build_peer_for_pool_via_grpc(
            services,
            batch,
            pool,
            ranked,
            web_config,
            on_update=lambda peer, still_refining: updates.append(
                (peer.peer_games, still_refining)
            ),
        )
    finally:
        server.stop(grace=None)

    assert result is not None
    assert result.peer_games == 50
    assert updates == [(6, True), (50, False)]
    assert "req-progressive-1" not in worker._peer_baseline_waiters


def test_build_peer_for_pool_via_grpc_cached_response_always_reports_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A synchronous `cached=True` response is always reported to `on_update`
    as `still_refining=False` -- PEERS makes no further attempt for it
    regardless of what the underlying (possibly still-refining) snapshot's
    own flag says, since no `request_id`/waiter exists for RUNNER to receive
    a follow-up on. This is the "existing fast/common path... unchanged"
    case from the design doc's testing section."""
    import json as _json
    from dataclasses import asdict

    from league_stats_peers.analysis.peer.baseline import PeerBaseline
    from league_stats_common.core.models import RankedEntry
    from league_stats_rpc.v1 import peers_pb2, peers_pb2_grpc

    baseline = PeerBaseline(
        metrics={"kda": 3.0}, games=8, players=8, source="live cache",
        confidence="low", fallback_level=2, still_refining=True,
    )

    class _CachedPeersServicer(peers_pb2_grpc.PeersServiceServicer):
        def RequestBaseline(self, request, context):
            return peers_pb2.RequestBaselineResponse(
                request_id="req-cached-refining-1",
                cached=True,
                baseline_json=_json.dumps(asdict(baseline)),
            )

    server, port = _start_peers_server(_CachedPeersServicer())
    updates: list[tuple[int, bool]] = []
    try:
        web_config = WebConfig(peers_grpc_target=f"127.0.0.1:{port}")
        services = _fake_services_for_grpc_peer()
        monkeypatch.setattr(worker, "group_records", lambda records, champ, role: _peer_records())
        batch = BuildBatch(pools=[], records=[], manifest_builds=[], primary_puuid="my-puuid")
        pool = BuildPool(champion="Ahri", role="MIDDLE", games=25)
        ranked = RankedEntry(tier="GOLD", rank="II", league_points=45, wins=10, losses=10)

        result = worker._build_peer_for_pool_via_grpc(
            services,
            batch,
            pool,
            ranked,
            web_config,
            on_update=lambda peer, still_refining: updates.append(
                (peer.peer_games, still_refining)
            ),
        )
    finally:
        server.stop(grace=None)

    assert result is not None
    assert updates == [(8, False)]


# ------------------------- _run_stage_b: cheap patch vs. full render (design doc §3.2/§3.3)


def test_run_stage_b_patches_report_json_on_interim_and_computes_career_once_on_terminal(
    store: JobStore, web_config: WebConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Design "Progressive peer-comparison updates during live sampling"
    §3.2/§3.3: simulates PEERS delivering one interim (`still_refining=True`)
    callback followed by one terminal (`still_refining=False`) callback for
    the same pool. `report.json` must be patched cheaply
    (`patch_report_peer_comparison`) on the interim delivery, and the full
    `analyze_build` pass (the only place Career/`build_all_ranked_ladder`
    computes) must run exactly once, after the terminal delivery.

    Fails pre-fix: `_run_stage_b` had no concept of `still_refining` at all --
    every delivery (there was only ever one) went straight to `analyze_build`,
    so an interim result would have computed Career against still-refining
    data, and there was no `patch_report_peer_comparison` call path at all.
    """
    from league_stats_common.core.models import RankedEntry

    job = _claimed_job(store)
    ranked = RankedEntry(tier="GOLD", rank="II", league_points=45, wins=10, losses=10)
    batch = _fake_batch()
    services = _fake_services()

    interim_peer = SimpleNamespace(label="interim", peer_games=6)
    terminal_peer = SimpleNamespace(label="terminal")

    def _fake_build_peer_for_pool_via_grpc(
        services, batch, pool, ranked, web_config, *, store=None, job_id=None, on_update=None
    ):
        on_update(interim_peer, True)
        on_update(terminal_peer, False)
        return terminal_peer

    patch_calls: list[Any] = []
    analyze_calls: list[dict[str, Any]] = []

    def _fake_patch_report_peer_comparison(config, pool, peer_comparison):
        patch_calls.append(peer_comparison)
        return True

    def _fake_analyze_build(
        services, batch, pool, *, ranked, peer_comparison, still_refining=False,
        full_frames=None, report_stats=None,
    ):
        analyze_calls.append(
            {"peer_comparison": peer_comparison, "still_refining": still_refining}
        )
        return BuildAnalysisResult(path=Path("report.json"))

    monkeypatch.setattr(worker, "group_records", lambda records, champ, role: ["record"])
    monkeypatch.setattr(
        worker, "should_skip_unchanged_build", lambda *args, **kwargs: False
    )
    monkeypatch.setattr(worker, "report_needs_peer_comparison", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        worker, "_build_peer_for_pool_via_grpc", _fake_build_peer_for_pool_via_grpc
    )
    monkeypatch.setattr(
        worker, "patch_report_peer_comparison", _fake_patch_report_peer_comparison
    )
    monkeypatch.setattr(worker, "analyze_build", _fake_analyze_build)

    # §3.4: a successful interim patch must also push a `store.update_progress`
    # call -- the existing StreamJobProgress -> NotifyingJobStore -> JobEventBus
    # -> SSE path api-ui already uses for every other progress event, which is
    # what makes an open browser tab notice the patched report.json at all.
    progress_details: list[str] = []
    real_update_progress = store.update_progress

    def _spy_update_progress(job_id, **kwargs):
        progress_details.append(kwargs.get("detail", ""))
        return real_update_progress(job_id, **kwargs)

    monkeypatch.setattr(store, "update_progress", _spy_update_progress)

    worker._run_stage_b(services, store, job, batch, ranked, frozenset(), {}, web_config)

    assert patch_calls == [interim_peer]
    assert len(analyze_calls) == 1
    assert analyze_calls[0]["peer_comparison"] is terminal_peer
    assert analyze_calls[0]["still_refining"] is False
    assert any("improved" in detail for detail in progress_details), (
        "interim patch must publish a store.update_progress event so an open "
        "browser tab's SSE stream wakes and refetches"
    )


def test_run_stage_b_fast_path_still_computes_career_exactly_once(
    store: JobStore, web_config: WebConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The existing fast/common path (a single, immediately-terminal delivery
    -- no interim callbacks at all) must still work unchanged: no cheap-patch
    call, and `analyze_build` (Career) runs exactly once."""
    from league_stats_common.core.models import RankedEntry

    job = _claimed_job(store)
    ranked = RankedEntry(tier="GOLD", rank="II", league_points=45, wins=10, losses=10)
    batch = _fake_batch()
    services = _fake_services()
    resolved_peer = SimpleNamespace(label="resolved")

    def _fake_build_peer_for_pool_via_grpc(
        services, batch, pool, ranked, web_config, *, store=None, job_id=None, on_update=None
    ):
        on_update(resolved_peer, False)
        return resolved_peer

    patch_calls: list[Any] = []
    analyze_calls: list[Any] = []

    monkeypatch.setattr(worker, "group_records", lambda records, champ, role: ["record"])
    monkeypatch.setattr(
        worker, "should_skip_unchanged_build", lambda *args, **kwargs: False
    )
    monkeypatch.setattr(worker, "report_needs_peer_comparison", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        worker, "_build_peer_for_pool_via_grpc", _fake_build_peer_for_pool_via_grpc
    )
    monkeypatch.setattr(
        worker, "patch_report_peer_comparison", lambda *a, **k: patch_calls.append(1)
    )

    def _fake_analyze_build(
        services, batch, pool, *, ranked, peer_comparison, still_refining=False,
        full_frames=None, report_stats=None,
    ):
        analyze_calls.append(peer_comparison)
        return BuildAnalysisResult(path=Path("report.json"))

    monkeypatch.setattr(worker, "analyze_build", _fake_analyze_build)

    worker._run_stage_b(services, store, job, batch, ranked, frozenset(), {}, web_config)

    assert patch_calls == []
    assert analyze_calls == [resolved_peer]
