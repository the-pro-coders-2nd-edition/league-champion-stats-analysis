"""RUNNER's gRPC service: wraps execute_job verbatim via a duck-typed
store adapter that streams progress instead of writing to the shared job store."""

import queue

from league_stats_runner.adapter import RunnerJobAdapter


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

from league_stats_common.core.config import WebConfig
from league_stats_common.core.champions import players_group_slug
from league_stats_common.infra.riot_api import RiotApiClient
from league_stats_runner import service as runner_service
from league_stats_runner.service import RunnerServicer
import league_stats_common.infra.jobs as job_states
from league_stats_rpc.v1 import common_pb2, runner_pb2, runner_pb2_grpc
from tests.fixtures import MY_PUUID, make_player_match, make_timeline


def _start_runner_server(runner_servicer):
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=4))
    runner_pb2_grpc.add_RunnerServiceServicer_to_server(runner_servicer, server)
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()
    return server, port


def test_enqueue_job_and_stream_progress_uses_raw_match_store_in_mongo_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end: EnqueueJob triggers execute_job against fixture match
    data (no real Riot API, no real Mongo -- `mongomock` stands in),
    StreamJobProgress yields at least one STAGE_A progress update and a
    final message, and the real report artifact lands on disk.

    `MatchStore` (the local on-disk store this replaced) was deleted in
    Phase 8, Task 1 -- `_build_job_services` now unconditionally constructs
    `RawMatchStore`. Uses this repo's existing offline-pipeline pattern
    (tests/fixtures.py's synthetic match/timeline builders, as used by
    tests/test_incremental_regen_end_to_end.py) instead of inventing new
    fixture infrastructure.

    The job uses kind=REGENERATE, which makes `execute_job` call
    `resolve_player_contexts` (PUUID/profile-icon/rank lookups only) instead
    of `fetch_matches` (which would also list + download match ids over the
    network). `fetch_solo_rank` is monkeypatched to return `None`, which
    makes the real (unmodified) peer stage skip cleanly with no baseline
    (`build_peer_comparison` returns `None` when `ranked is None`) instead of
    attempting a real network call for peer sampling -- this is a real,
    ordinary code path (an unranked player), not a bypass of the peer stage.
    """
    import mongomock

    from league_stats_runner.infra.raw_match_store import RawMatchStore
    import league_stats_runner.worker as web_worker

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(RiotApiClient, "resolve_puuid", lambda self, riot_id, tagline: MY_PUUID)
    monkeypatch.setattr(RiotApiClient, "fetch_profile_icon_id", lambda self, puuid: None)
    monkeypatch.setattr(RiotApiClient, "fetch_solo_rank", lambda self, puuid: None)

    mongo_client = mongomock.MongoClient()
    monkeypatch.setattr(web_worker, "_build_mongo_client", lambda uri: mongo_client)

    # Seed the RawMatchStore directly -- `MatchStore` no longer exists, so
    # there's no local on-disk match cache to seed instead.
    # db_name matches WebConfig.runner_mongo_uri's default database
    # ("league_stats"), the same one `_build_job_services` will resolve via
    # `_db_name_from_uri`.
    raw_store = RawMatchStore(mongo_client, db_name="league_stats")
    for index in range(6):
        match_id = f"EUW1_v{index}"
        raw_store.save_match(
            match_id, MY_PUUID, make_player_match(match_id, champion="Viktor", position="MIDDLE")
        )
        raw_store.save_timeline(match_id, make_timeline())

    web_config = WebConfig(
        output_dir=tmp_path / "output",
    )
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
    assert results[-1].error == "", f"job failed: {results[-1].error!r}"
    stage_a_progress = [
        r for r in results if r.stage == common_pb2.STAGE_A and r.detail and not r.final
    ]
    assert stage_a_progress, "expected at least one non-final STAGE_A progress update"
    report_path = (
        tmp_path / "output" / "reports" / player_slug / "viktor_middle" / "report.json"
    )
    assert report_path.exists(), f"expected report artifact at {report_path}"


def test_runner_servicer_never_delegates_even_with_stale_runner_mode_env_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RUNNER must never dial another RUNNER.

    Phase 9 removed `runner_mode`/`ANALYZER_RUNNER_MODE` entirely: there is no
    longer a flag `RunnerServicer` could pick up (from `env_file: .env` or an
    explicitly-passed `web_config`) that would make its internal job dispatch
    delegate back to RUNNER itself (unbounded recursive job fan-out). This is
    now structural rather than flag-enforced: `_run_job` calls `execute_job`
    directly, and `execute_job` has no gRPC-delegation branch at all -- that's
    `_execute_job_via_runner`, which only api-ui's/cron-watch's own
    `AnalysisWorker._loop` ever calls (see `league_stats_runner.worker`).

    Proves this two ways: (1) an unrecognised `ANALYZER_RUNNER_MODE=grpc` env
    var (pydantic's `extra="ignore"` means a leftover env-map entry pointing
    at a deleted field would silently no-op even if config.py's env map still
    referenced it) does not survive onto `WebConfig` at all -- there is no
    `runner_mode` attribute left to carry a wrong value; and (2) actually
    running a job through the real servicer calls `execute_job`, never
    `_execute_job_via_runner`.
    """
    import league_stats_runner.worker as web_worker

    monkeypatch.setenv("ANALYZER_RUNNER_MODE", "grpc")
    servicer = RunnerServicer()
    assert not hasattr(servicer._web_config, "runner_mode")

    def _boom_if_delegated(*_args, **_kwargs):
        raise AssertionError("RunnerServicer must never call _execute_job_via_runner")

    monkeypatch.setattr(web_worker, "_execute_job_via_runner", _boom_if_delegated)

    observed: list[bool] = []

    def _recording_execute_job(job, store, web_config):
        observed.append(True)
        store.set_state(job["id"], job_states.DONE, detail="stub")

    monkeypatch.setattr(runner_service, "execute_job", _recording_execute_job)

    server, port = _start_runner_server(servicer)
    channel = grpc.insecure_channel(f"127.0.0.1:{port}")
    try:
        stub = runner_pb2_grpc.RunnerServiceStub(channel)
        request = runner_pb2.EnqueueJobRequest(
            riot_id="Test",
            tagline="EUW",
            region=common_pb2.EUROPE,
            kind=runner_pb2.JOB_KIND_REGENERATE,
            player_slug="test_euw",
        )
        response = stub.EnqueueJob(request)
        list(stub.StreamJobProgress(runner_pb2.StreamJobProgressRequest(job_id=response.job_id)))
    finally:
        channel.close()
        server.stop(grace=None)

    assert observed == [True]


def test_enqueue_job_rejects_unspecified_kind() -> None:
    """JOB_KIND_UNSPECIFIED must be rejected, not silently defaulted to 'analyze'."""
    servicer = RunnerServicer(web_config=WebConfig())
    server, port = _start_runner_server(servicer)
    channel = grpc.insecure_channel(f"127.0.0.1:{port}")
    try:
        stub = runner_pb2_grpc.RunnerServiceStub(channel)
        with pytest.raises(grpc.RpcError) as exc_info:
            stub.EnqueueJob(runner_pb2.EnqueueJobRequest(riot_id="Test", tagline="EUW"))
        assert exc_info.value.code() == grpc.StatusCode.INVALID_ARGUMENT
    finally:
        channel.close()
        server.stop(grace=None)


def test_run_job_wraps_execute_job_so_a_crash_still_yields_a_final_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A crash outside execute_job's own try/finally must not hang StreamJobProgress.

    Regression test for the unwrapped `threading.Thread(target=execute_job, ...)`
    gap: without `_run_job`'s wrapper, a crash here would leave the queue
    empty forever and `StreamJobProgress`'s blocking `events.get()` would
    hang indefinitely.
    """

    def _boom(job, store, web_config):
        raise RuntimeError("boom")

    monkeypatch.setattr(runner_service, "execute_job", _boom)
    servicer = RunnerServicer(web_config=WebConfig())
    server, port = _start_runner_server(servicer)
    channel = grpc.insecure_channel(f"127.0.0.1:{port}")
    try:
        stub = runner_pb2_grpc.RunnerServiceStub(channel)
        request = runner_pb2.EnqueueJobRequest(
            riot_id="Test",
            tagline="EUW",
            region=common_pb2.EUROPE,
            kind=runner_pb2.JOB_KIND_REGENERATE,
            player_slug="test_euw",
        )
        response = stub.EnqueueJob(request)
        results = list(
            stub.StreamJobProgress(runner_pb2.StreamJobProgressRequest(job_id=response.job_id))
        )
    finally:
        channel.close()
        server.stop(grace=None)

    assert results, "expected a terminal event even though execute_job crashed"
    assert results[-1].final is True
    assert "boom" in results[-1].error


def test_enqueue_job_threads_the_callers_trace_id_into_the_spawned_job_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Phase 6 final review, Finding 1: `EnqueueJob`'s own handler runs on the
    thread `TraceServerInterceptor` set the incoming trace_id on, but the
    actual job runs on a brand-new `threading.Thread` (`_run_job`) with its
    own, unrelated `contextvars.Context`. Without explicitly threading the
    captured trace_id through, it would be silently lost the moment
    `_run_job`'s thread starts, even though the RPC itself carried it
    correctly.
    """
    from league_stats_common.infra.trace_context import TraceServerInterceptor
    from league_stats_common.utils import current_trace_id

    observed: list[str] = []

    def _recording_execute_job(job, store, web_config):
        observed.append(current_trace_id())
        # Emit a terminal event ourselves (skipping the real pipeline) so
        # StreamJobProgress's consumer below doesn't block waiting for one.
        store.set_state(job["id"], job_states.DONE, detail="stub")

    monkeypatch.setattr(runner_service, "execute_job", _recording_execute_job)
    servicer = RunnerServicer(web_config=WebConfig())
    server = grpc.server(
        futures.ThreadPoolExecutor(max_workers=4), interceptors=[TraceServerInterceptor()]
    )
    runner_pb2_grpc.add_RunnerServiceServicer_to_server(servicer, server)
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()
    channel = grpc.insecure_channel(f"127.0.0.1:{port}")
    try:
        stub = runner_pb2_grpc.RunnerServiceStub(channel)
        request = runner_pb2.EnqueueJobRequest(
            riot_id="Test",
            tagline="EUW",
            region=common_pb2.EUROPE,
            kind=runner_pb2.JOB_KIND_REGENERATE,
            player_slug="test_euw",
        )
        response = stub.EnqueueJob(request, metadata=(("trace-id", "upstream-trace-123"),))
        list(
            stub.StreamJobProgress(runner_pb2.StreamJobProgressRequest(job_id=response.job_id))
        )
    finally:
        channel.close()
        server.stop(grace=None)

    assert observed == ["upstream-trace-123"]


# ------------------------------------------------------- NotifyPeerBaselineReady


def test_notify_peer_baseline_ready_delivers_to_a_registered_waiter() -> None:
    """The real (Phase 3) implementation must forward to
    `league_stats.web.worker.resolve_peer_baseline_notification`, which
    `_build_peer_for_pool_via_grpc` blocks on -- this is what replaced Phase 1's
    logging-only stub."""
    import league_stats_runner.worker as web_worker

    events = web_worker._register_peer_baseline_waiter("req-notify-1")
    servicer = RunnerServicer(web_config=WebConfig())

    request = runner_pb2.PeerBaselineReadyRequest(
        request_id="req-notify-1",
        champion="Ahri",
        lane="MIDDLE",
        rank="GOLD II",
        baseline_json='{"games": 42}',
        error="",
    )
    ack = servicer.NotifyPeerBaselineReady(request, context=None)

    assert ack.ok is True
    delivered = events.get_nowait()
    assert delivered == {
        "baseline_json": '{"games": 42}',
        "error": "",
        "still_refining": False,
    }


def test_notify_peer_baseline_ready_reports_ok_false_when_no_waiter() -> None:
    """A callback for a `request_id` nobody is (yet) waiting on must not raise
    -- it's reported via `ok=False`, not an RPC error, mirroring PEERS' own
    `_notify_runner`, which only logs a failed delivery and never retries.

    `ok=False` reports "not delivered to a live waiter" -- it does NOT mean
    the notification was dropped: fix round 1 (the lost-wakeup race) made
    `resolve_peer_baseline_notification` stash it in `_peer_baseline_orphans`
    for a still-to-come `_register_peer_baseline_waiter` call to claim
    instead. See `test_build_peer_for_pool_via_grpc_survives_notification_arriving_before_waiter_registers`
    (`test_web_worker.py`) for the real end-to-end proof of that path.
    """
    import league_stats_runner.worker as web_worker

    servicer = RunnerServicer(web_config=WebConfig())

    request = runner_pb2.PeerBaselineReadyRequest(
        request_id="req-nobody-waiting",
        champion="Ahri",
        lane="MIDDLE",
        rank="GOLD II",
        baseline_json="",
        error="some failure",
    )
    ack = servicer.NotifyPeerBaselineReady(request, context=None)

    assert ack.ok is False
    assert "req-nobody-waiting" in web_worker._peer_baseline_orphans
    stored_at, payload = web_worker._peer_baseline_orphans.pop("req-nobody-waiting")
    assert payload == {
        "baseline_json": "",
        "error": "some failure",
        "still_refining": False,
    }


def test_stream_job_progress_reconnect_after_terminal_returns_immediately(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A second call for an already-drained job_id must not block on an empty queue."""

    def _fake_execute_job(job, store, web_config):
        store.set_state(job["id"], job_states.DONE, detail="ok")

    monkeypatch.setattr(runner_service, "execute_job", _fake_execute_job)
    servicer = RunnerServicer(web_config=WebConfig())
    server, port = _start_runner_server(servicer)
    channel = grpc.insecure_channel(f"127.0.0.1:{port}")
    try:
        stub = runner_pb2_grpc.RunnerServiceStub(channel)
        request = runner_pb2.EnqueueJobRequest(
            riot_id="Test",
            tagline="EUW",
            region=common_pb2.EUROPE,
            kind=runner_pb2.JOB_KIND_REGENERATE,
            player_slug="test_euw",
        )
        response = stub.EnqueueJob(request)

        first = list(
            stub.StreamJobProgress(runner_pb2.StreamJobProgressRequest(job_id=response.job_id))
        )
        assert first[-1].final is True

        second = list(
            stub.StreamJobProgress(runner_pb2.StreamJobProgressRequest(job_id=response.job_id))
        )
    finally:
        channel.close()
        server.stop(grace=None)

    assert len(second) == 1
    assert second[0].final is True
