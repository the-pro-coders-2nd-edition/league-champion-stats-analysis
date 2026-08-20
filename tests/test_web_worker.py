"""Tests for the analysis worker: two-stage execution and failure handling."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from league_stats.core.config import WebConfig
from league_stats.ingest.parser import BuildPool
from league_stats.pipeline.fetch import FetchResult
from league_stats.pipeline.orchestrator import (
    BuildAnalysisResult,
    BuildBatch,
    NoEligibleBuildsError,
)
from league_stats.pipeline.services import PlayerContext
from league_stats.web import jobs, worker
from league_stats.web.jobs import JobStore


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
    js = JobStore(tmp_path / "app.sqlite")
    js.upsert_player(slug="test_euw", riot_id="Test", tagline="EUW", region="euw1")
    yield js
    js.close()


@pytest.fixture()
def web_config(tmp_path: Path) -> WebConfig:
    return WebConfig(
        app_db_path=tmp_path / "app.sqlite", output_dir=tmp_path / "output"
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
            lambda services, batch, pool, *, ranked, peer_comparison, full_frames=None, report_stats=None: calls.append(
                f"analyze(peer={peer_comparison is not None})"
            )
            or BuildAnalysisResult(path=Path("report.json"))
        ),
        "build_peer_for_pool": (
            lambda services, batch, pool, ranked: calls.append("peer") or object()
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

    def boom(services: Any, batch: Any, pool: Any, ranked: Any) -> Any:
        raise RuntimeError("riot exploded")

    _patch_pipeline(monkeypatch, build_peer_for_pool=boom)

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


# --------------------------------------------------------- runner_mode (Task 6)


def test_web_config_runner_mode_defaults_to_in_process() -> None:
    assert WebConfig().runner_mode == "in_process"


def test_web_config_runner_mode_can_be_set_to_grpc() -> None:
    assert WebConfig(runner_mode="grpc").runner_mode == "grpc"


def test_execute_job_in_process_mode_does_not_call_runner(
    store: JobStore, web_config: WebConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`runner_mode` defaults to "in_process" — execute_job must run exactly as
    before, with no gRPC call attempted."""
    assert web_config.runner_mode == "in_process"
    job = _claimed_job(store)
    _patch_pipeline(monkeypatch)
    called = {"n": 0}
    monkeypatch.setattr(
        worker, "_execute_job_via_runner", lambda *a, **k: called.__setitem__("n", called["n"] + 1)
    )

    worker.execute_job(job, store, web_config)

    assert called["n"] == 0
    final = store.get(int(job["id"]))
    assert final["state"] == jobs.DONE
    assert final["error"] == ""


def test_execute_job_grpc_mode_delegates_to_runner_and_replays_progress(
    tmp_path: Path, store: JobStore, web_config: WebConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When `runner_mode` is "grpc", execute_job delegates to a real (in-process)
    RunnerServicer over gRPC, and replays its StageResult stream into the real
    (local) JobStore -- ending in the same terminal state/registry updates the
    in_process path produces for an equivalent run (see
    test_execute_job_two_stage_happy_path).

    Runs RUNNER's actual `execute_job` against offline fixture data (no real
    Riot API / Mongo), reusing the exact pattern
    tests/test_runner_service.py's `test_enqueue_job_and_stream_progress_reports_a_real_analysis`
    already established for this: seed a local SQLite MatchStore at the path
    `_build_job_services` hardcodes, monkeypatch RiotApiClient's network calls,
    and use kind=REGENERATE so `execute_job` never calls `fetch_matches`.
    """
    from concurrent import futures

    import grpc

    from league_stats.infra.cache import MatchStore
    from league_stats.infra.riot_api import RiotApiClient
    from league_stats.runner.service import RunnerServicer
    from league_stats_rpc.v1 import runner_pb2_grpc
    from tests.fixtures import MY_PUUID, make_player_match, make_timeline

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(RiotApiClient, "resolve_puuid", lambda self, riot_id, tagline: MY_PUUID)
    monkeypatch.setattr(RiotApiClient, "fetch_profile_icon_id", lambda self, puuid: None)
    monkeypatch.setattr(RiotApiClient, "fetch_solo_rank", lambda self, puuid: None)

    match_store = MatchStore(Path(".cache") / "matches.sqlite")
    for index in range(6):
        match_id = f"EUW1_v{index}"
        match_store.save_match(
            match_id, MY_PUUID, make_player_match(match_id, champion="Viktor", position="MIDDLE")
        )
        match_store.save_timeline(match_id, make_timeline())
    match_store.close()

    runner_web_config = WebConfig(output_dir=tmp_path / "runner_output")
    servicer = RunnerServicer(web_config=runner_web_config)
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=4))
    runner_pb2_grpc.add_RunnerServiceServicer_to_server(servicer, server)
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()
    try:
        grpc_web_config = web_config.model_copy(
            update={"runner_mode": "grpc", "runner_grpc_target": f"127.0.0.1:{port}"}
        )
        job = _claimed_regenerate_job(store)
        job["min_games"] = 5

        worker.execute_job(job, store, grpc_web_config)
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
    """Run `worker.execute_job` in grpc mode against a scripted `StreamJobProgress`
    reply, to exercise `_execute_job_via_runner`'s replay logic in isolation from
    a real pipeline run (used for the FAILED/soft-DONE inference branches, which
    only depend on the shape of the StageResult stream, not on real analysis).
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
            update={"runner_mode": "grpc", "runner_grpc_target": f"127.0.0.1:{port}"}
        )
        worker.execute_job(job, store, grpc_web_config)
    finally:
        server.stop(grace=None)
    return job


def test_execute_job_grpc_mode_unreachable_runner_marks_failed_not_hangs(
    store: JobStore, web_config: WebConfig
) -> None:
    """RUNNER unreachable (nothing listening on the target port) must not hang
    the worker thread or raise out of `execute_job` -- the job must reach a
    terminal FAILED state instead. Regression test for the gap where only a
    bare `finally: channel.close()` guarded the RPC calls, so a `grpc.RpcError`
    (RUNNER down, UNAVAILABLE, connection reset mid-stream) would propagate out
    of `execute_job` into `AnalysisWorker._loop`, which has no try/except of
    its own -- permanently killing the worker thread.
    """
    import socket

    # Grab a genuinely free port and never listen on it.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        free_port = probe.getsockname()[1]

    job = _claimed_job(store)
    grpc_web_config = web_config.model_copy(
        update={
            "runner_mode": "grpc",
            "runner_grpc_target": f"127.0.0.1:{free_port}",
        }
    )

    worker.execute_job(job, store, grpc_web_config)

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


# --------------------------------------------------------- peers_mode (Task 3)


def test_web_config_peers_mode_defaults_to_in_process() -> None:
    assert WebConfig().peers_mode == "in_process"


def test_web_config_peers_mode_can_be_set_to_grpc() -> None:
    assert WebConfig(peers_mode="grpc").peers_mode == "grpc"


def test_execute_job_peers_in_process_mode_does_not_call_peers_grpc_path(
    store: JobStore, web_config: WebConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`peers_mode` defaults to "in_process" -- stage B must call
    `build_peer_for_pool` exactly as before Task 3, with `_build_peer_for_pool_via_grpc`
    never even attempted. This is the "provably unchanged" proof for the default path.
    """
    assert web_config.peers_mode == "in_process"
    job = _claimed_job(store)
    calls = _patch_pipeline(monkeypatch)

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("_build_peer_for_pool_via_grpc must not run in in_process mode")

    monkeypatch.setattr(worker, "_build_peer_for_pool_via_grpc", boom)

    worker.execute_job(job, store, web_config)

    final = store.get(int(job["id"]))
    assert final["state"] == jobs.DONE
    assert final["error"] == ""
    assert calls == ["fetch", "prepare", "ranked", "analyze(peer=False)", "peer", "analyze(peer=True)"]


def test_execute_job_peers_grpc_mode_calls_grpc_path_not_in_process(
    store: JobStore, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`peers_mode="grpc"` must route stage B's peer resolution through
    `_build_peer_for_pool_via_grpc` instead of `build_peer_for_pool`."""
    grpc_web_config = WebConfig(
        app_db_path=tmp_path / "app.sqlite",
        output_dir=tmp_path / "output",
        peers_mode="grpc",
    )
    job = _claimed_job(store)
    grpc_peer_calls = {"n": 0}

    def in_process_boom(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("build_peer_for_pool must not run in peers_mode=grpc")

    def grpc_peer_stub(services: Any, batch: Any, pool: Any, ranked: Any, web_config: Any) -> Any:
        grpc_peer_calls["n"] += 1
        return object()

    calls = _patch_pipeline(
        monkeypatch,
        build_peer_for_pool=in_process_boom,
        _build_peer_for_pool_via_grpc=grpc_peer_stub,
    )

    worker.execute_job(job, store, grpc_web_config)

    final = store.get(int(job["id"]))
    assert final["state"] == jobs.DONE
    assert grpc_peer_calls["n"] == 1
    assert "peer" not in calls


# ------------------------------------------------- _build_peer_for_pool_via_grpc


class _FakeRecord:
    """Minimal stand-in for `MatchRecord`; only `to_row()` is used by the
    grpc peer path (`matches_df = pd.DataFrame([r.to_row() for r in records])`)."""

    def __init__(self, **row: Any) -> None:
        self._row = row

    def to_row(self) -> dict[str, Any]:
        return self._row


def _fake_services_for_grpc_peer(min_games: int = 1) -> Any:
    from league_stats.core.config import AppConfig

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

    from league_stats.analysis.peer.baseline import PeerBaseline
    from league_stats.core.models import RankedEntry
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

    from league_stats.analysis.peer.baseline import PeerBaseline
    from league_stats.core.models import RankedEntry
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
    from league_stats.core.models import RankedEntry
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


def test_build_peer_for_pool_via_grpc_returns_none_when_peers_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RUNNER must not crash or hang stage B when PEERS is unreachable --
    a soft failure (None) for this one build, same as any other
    `build_peer_for_pool` exception."""
    import socket

    from league_stats.core.models import RankedEntry

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


def test_build_peer_for_pool_via_grpc_returns_none_on_peers_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A `RequestBaselineResponse.error` (PEERS could not enqueue/resolve at
    all) must be a soft `None`, not an exception -- and must not register a
    waiter, since PEERS explicitly documents no callback ever follows this case."""
    from league_stats.core.models import RankedEntry
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


def test_build_peer_for_pool_via_grpc_returns_none_below_min_games(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same `min_games` gate as the in-process path -- must not even attempt
    a PEERS call when there aren't enough games."""
    from league_stats.core.models import RankedEntry

    web_config = WebConfig(peers_grpc_target="127.0.0.1:1")  # never dialed
    services = _fake_services_for_grpc_peer(min_games=99)
    monkeypatch.setattr(worker, "group_records", lambda records, champ, role: _peer_records())
    batch = BuildBatch(pools=[], records=[], manifest_builds=[], primary_puuid="my-puuid")
    pool = BuildPool(champion="Ahri", role="MIDDLE", games=25)
    ranked = RankedEntry(tier="GOLD", rank="II", league_points=45, wins=10, losses=10)

    result = worker._build_peer_for_pool_via_grpc(services, batch, pool, ranked, web_config)

    assert result is None
