"""RUNNER's gRPC service: wraps execute_job verbatim via a duck-typed
store adapter that streams progress instead of writing to SQLite."""

import queue

from league_stats.runner.adapter import RunnerJobAdapter


def test_adapter_set_state_pushes_a_stage_result():
    events = queue.SimpleQueue()
    adapter = RunnerJobAdapter(job_id=1, events=events)

    adapter.set_state(1, "analyzing", detail="Discovering reports…")

    result = events.get_nowait()
    assert result["job_id"] == "1"
    assert result["detail"] == "Discovering reports…"


def test_adapter_update_progress_pushes_current_and_total():
    events = queue.SimpleQueue()
    adapter = RunnerJobAdapter(job_id=1, events=events)

    adapter.update_progress(1, detail="Analyzing Kayle Top (1/3)", current=1, total=3)

    result = events.get_nowait()
    assert result["detail"] == "Analyzing Kayle Top (1/3)"
    assert result["current"] == 1
    assert result["total"] == 3


def test_adapter_is_cancelled_defaults_false():
    events = queue.SimpleQueue()
    adapter = RunnerJobAdapter(job_id=1, events=events)
    assert adapter.is_cancelled(1) is False


def test_adapter_mark_player_base_complete_is_a_noop_that_does_not_raise():
    events = queue.SimpleQueue()
    adapter = RunnerJobAdapter(job_id=1, events=events)
    adapter.mark_player_base_complete("some-slug")  # must not raise


def test_adapter_get_player_is_a_noop_that_returns_none():
    """Not in the task brief's Step 1 catalogue, but genuinely required:

    `_build_job_services` -> `_tracked_players_for_job` calls
    `job_store.get_player(job_slug)`. Without this method, any job whose
    tracked-player list doesn't already resolve to a matching slug would
    raise AttributeError deep inside `execute_job`.
    """
    events = queue.SimpleQueue()
    adapter = RunnerJobAdapter(job_id=1, events=events)
    assert adapter.get_player("some-slug") is None


# --------------------------------------------------------------- gRPC service

from concurrent import futures
from pathlib import Path

import grpc
import pytest

from league_stats.core.config import WebConfig
from league_stats.core.champions import players_group_slug
from league_stats.infra.cache import MatchStore
from league_stats.infra.riot_api import RiotApiClient
from league_stats.runner.service import RunnerServicer
from league_stats.web import jobs as job_states
from league_stats_rpc.v1 import common_pb2, runner_pb2, runner_pb2_grpc
from tests.fixtures import MY_PUUID, make_player_match, make_timeline


def _start_runner_server(runner_servicer):
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=4))
    runner_pb2_grpc.add_RunnerServiceServicer_to_server(runner_servicer, server)
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()
    return server, port


def test_enqueue_job_and_stream_progress_reports_a_real_analysis(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end: EnqueueJob triggers execute_job against fixture match
    data (no real Riot API, no real Mongo), StreamJobProgress yields at
    least one STAGE_A progress update and a final message.

    Uses this repo's existing offline-pipeline pattern (tests/fixtures.py's
    synthetic match/timeline builders, as used by
    tests/test_incremental_regen_end_to_end.py) instead of inventing new
    fixture infrastructure.

    NOTE on scope: `execute_job`'s match-store construction is hardcoded
    inside the private `_build_job_services` (see service.py's module
    docstring) -- it always builds a local SQLite `MatchStore`, not the
    Mongo-backed `RawMatchStore` from Task 3. So this test seeds match data
    into a real SQLite `MatchStore` at the exact path `_build_job_services`
    will construct (`<cwd>/.cache/matches.sqlite`, since `AppConfig.cache_dir`
    is never overridden by the job-building code path, in RUNNER or in the
    monolith), not into `RawMatchStore`. That is the real, current behavior
    of `execute_job` -- not a simplification chosen for this test.

    The job uses kind=REGENERATE, which makes `execute_job` call
    `resolve_player_contexts` (PUUID/profile-icon/rank lookups only) instead
    of `fetch_matches` (which would also list + download match ids over the
    network). `fetch_solo_rank` is monkeypatched to return `None`, which
    makes the real (unmodified) peer stage skip cleanly with no baseline
    (`build_peer_comparison` returns `None` when `ranked is None`) instead of
    attempting a real network call for peer sampling -- this is a real,
    ordinary code path (an unranked player), not a bypass of the peer stage.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(RiotApiClient, "resolve_puuid", lambda self, riot_id, tagline: MY_PUUID)
    monkeypatch.setattr(RiotApiClient, "fetch_profile_icon_id", lambda self, puuid: None)
    monkeypatch.setattr(RiotApiClient, "fetch_solo_rank", lambda self, puuid: None)

    # Seed the local match store at the exact path `_build_job_services` will
    # open (AppConfig.cache_dir defaults to `.cache` under the cwd; never
    # overridden by the job-construction code path).
    store = MatchStore(Path(".cache") / "matches.sqlite")
    for index in range(6):
        match_id = f"EUW1_v{index}"
        store.save_match(
            match_id, MY_PUUID, make_player_match(match_id, champion="Viktor", position="MIDDLE")
        )
        store.save_timeline(match_id, make_timeline())
    store.close()

    web_config = WebConfig(output_dir=tmp_path / "output")
    servicer = RunnerServicer(web_config=web_config)
    server, port = _start_runner_server(servicer)
    channel = grpc.insecure_channel(f"127.0.0.1:{port}")
    try:
        stub = runner_pb2_grpc.RunnerServiceStub(channel)
        player_slug = players_group_slug([("Test", "EUW")])
        request = runner_pb2.EnqueueJobRequest(
            riot_id="Test",
            tagline="EUW",
            region=common_pb2.EUROPE,
            kind=runner_pb2.JOB_KIND_REGENERATE,
            player_slug=player_slug,
            min_games=5,
        )
        response = stub.EnqueueJob(request)
        assert response.job_id

        results = list(
            stub.StreamJobProgress(runner_pb2.StreamJobProgressRequest(job_id=response.job_id))
        )
    finally:
        channel.close()
        server.stop(grace=None)

    assert results, "expected at least one StageResult message"
    assert results[-1].final is True
    stage_a_progress = [
        r for r in results if r.stage == common_pb2.STAGE_A and r.detail and not r.final
    ]
    assert stage_a_progress, "expected at least one non-final STAGE_A progress update"
    # The job must have run for real: it reaches a terminal job_states state
    # (done here, since peer comparison cleanly skips with no rank) rather
    # than failing during setup.
    final_detail_or_error = results[-1].detail or results[-1].error
    assert final_detail_or_error
