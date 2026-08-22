"""Round-trip smoke tests proving the generated gRPC contracts work over
a real server/client pair, using trivial stub implementations. No
business logic is exercised here — later phases replace these servicers
with the real CronWatch/Runner/Peers implementations."""

from concurrent import futures

import grpc

from league_stats_rpc.v1 import common_pb2
from league_stats_rpc.v1 import cron_watch_pb2, cron_watch_pb2_grpc
from league_stats_rpc.v1 import peers_pb2, peers_pb2_grpc
from league_stats_rpc.v1 import runner_pb2, runner_pb2_grpc


def _start_server(add_servicer_fn):
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=2))
    add_servicer_fn(server)
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()
    return server, port


def test_cron_watch_register_account_round_trip():
    class FakeCronWatch(cron_watch_pb2_grpc.CronWatchServiceServicer):
        def RegisterAccount(self, request, context):
            return common_pb2.Ack(ok=True, message=f"registered {request.puuid}")

    server, port = _start_server(
        lambda s: cron_watch_pb2_grpc.add_CronWatchServiceServicer_to_server(FakeCronWatch(), s)
    )
    try:
        with grpc.insecure_channel(f"127.0.0.1:{port}") as channel:
            stub = cron_watch_pb2_grpc.CronWatchServiceStub(channel)
            response = stub.RegisterAccount(
                cron_watch_pb2.RegisterAccountRequest(puuid="puuid-123", region=common_pb2.EUROPE)
            )
        assert response.ok is True
        assert response.message == "registered puuid-123"
    finally:
        server.stop(None)


def test_runner_enqueue_job_and_stream_progress():
    class FakeRunner(runner_pb2_grpc.RunnerServiceServicer):
        def EnqueueJob(self, request, context):
            return runner_pb2.EnqueueJobResponse(job_id="job-1")

        def StreamJobProgress(self, request, context):
            yield runner_pb2.StageResult(
                job_id=request.job_id,
                stage=common_pb2.STAGE_A,
                payload_json="{}",
                detail="Analyzing Kayle Top (1/1)",
                current=1,
                total=1,
            )
            yield runner_pb2.StageResult(
                job_id=request.job_id,
                stage=common_pb2.STAGE_B,
                payload_json="{}",
                detail="Comparing Kayle Top to players at your rank (1/1)",
                current=1,
                total=1,
            )

    server, port = _start_server(
        lambda s: runner_pb2_grpc.add_RunnerServiceServicer_to_server(FakeRunner(), s)
    )
    try:
        with grpc.insecure_channel(f"127.0.0.1:{port}") as channel:
            stub = runner_pb2_grpc.RunnerServiceStub(channel)
            enqueue_response = stub.EnqueueJob(
                runner_pb2.EnqueueJobRequest(puuid="puuid-123", match_id="EUW1_1")
            )
            assert enqueue_response.job_id == "job-1"

            stages = [
                result.stage
                for result in stub.StreamJobProgress(
                    runner_pb2.StreamJobProgressRequest(job_id=enqueue_response.job_id)
                )
            ]
        assert stages == [common_pb2.STAGE_A, common_pb2.STAGE_B]
    finally:
        server.stop(None)


def test_runner_stream_job_progress_reports_failure_as_final():
    class FakeRunner(runner_pb2_grpc.RunnerServiceServicer):
        def StreamJobProgress(self, request, context):
            yield runner_pb2.StageResult(
                job_id=request.job_id, stage=common_pb2.STAGE_A, payload_json="{}"
            )
            yield runner_pb2.StageResult(
                job_id=request.job_id,
                stage=common_pb2.STAGE_UNSPECIFIED,
                error="riot api timed out",
                final=True,
            )

    server, port = _start_server(
        lambda s: runner_pb2_grpc.add_RunnerServiceServicer_to_server(FakeRunner(), s)
    )
    try:
        with grpc.insecure_channel(f"127.0.0.1:{port}") as channel:
            stub = runner_pb2_grpc.RunnerServiceStub(channel)
            results = list(
                stub.StreamJobProgress(runner_pb2.StreamJobProgressRequest(job_id="job-1"))
            )
        assert [r.final for r in results] == [False, True]
        assert results[-1].error == "riot api timed out"
    finally:
        server.stop(None)


def test_peers_notify_baseline_ready_reports_failure():
    class FakeRunner(runner_pb2_grpc.RunnerServiceServicer):
        def NotifyPeerBaselineReady(self, request, context):
            assert request.error == "peers fetch failed"
            return common_pb2.Ack(ok=True, message=f"received {request.request_id}")

    server, port = _start_server(
        lambda s: runner_pb2_grpc.add_RunnerServiceServicer_to_server(FakeRunner(), s)
    )
    try:
        with grpc.insecure_channel(f"127.0.0.1:{port}") as channel:
            stub = runner_pb2_grpc.RunnerServiceStub(channel)
            ack = stub.NotifyPeerBaselineReady(
                runner_pb2.PeerBaselineReadyRequest(
                    request_id="req-2",
                    champion="Kayle",
                    lane="TOP",
                    rank="EMERALD",
                    error="peers fetch failed",
                )
            )
        assert ack.ok is True
        assert ack.message == "received req-2"
    finally:
        server.stop(None)


def test_runner_enqueue_job_carries_kind_and_players():
    class FakeRunner(runner_pb2_grpc.RunnerServiceServicer):
        def EnqueueJob(self, request, context):
            assert request.kind == runner_pb2.JOB_KIND_REGENERATE
            assert request.riot_id == "Faker"
            assert request.tagline == "KR1"
            assert request.player_slug == "faker-kr1"
            assert [p.riot_id for p in request.players] == ["Faker", "Duo"]
            assert request.filter_champion == "Ahri"
            assert request.min_games == 20
            return runner_pb2.EnqueueJobResponse(job_id="job-2")

    server, port = _start_server(
        lambda s: runner_pb2_grpc.add_RunnerServiceServicer_to_server(FakeRunner(), s)
    )
    try:
        with grpc.insecure_channel(f"127.0.0.1:{port}") as channel:
            stub = runner_pb2_grpc.RunnerServiceStub(channel)
            response = stub.EnqueueJob(
                runner_pb2.EnqueueJobRequest(
                    puuid="puuid-123",
                    match_id="EUW1_1",
                    kind=runner_pb2.JOB_KIND_REGENERATE,
                    riot_id="Faker",
                    tagline="KR1",
                    player_slug="faker-kr1",
                    players=[
                        runner_pb2.JobPlayer(riot_id="Faker", tagline="KR1"),
                        runner_pb2.JobPlayer(riot_id="Duo", tagline="KR1"),
                    ],
                    filter_champion="Ahri",
                    min_games=20,
                )
            )
        assert response.job_id == "job-2"
    finally:
        server.stop(None)


def test_peers_request_baseline_then_runner_notified():
    class FakePeers(peers_pb2_grpc.PeersServiceServicer):
        def RequestBaseline(self, request, context):
            return peers_pb2.RequestBaselineResponse(request_id="req-1", cached=False)

    class FakeRunner(runner_pb2_grpc.RunnerServiceServicer):
        def NotifyPeerBaselineReady(self, request, context):
            return common_pb2.Ack(ok=True, message=f"received {request.request_id}")

    peers_server, peers_port = _start_server(
        lambda s: peers_pb2_grpc.add_PeersServiceServicer_to_server(FakePeers(), s)
    )
    runner_server, runner_port = _start_server(
        lambda s: runner_pb2_grpc.add_RunnerServiceServicer_to_server(FakeRunner(), s)
    )
    try:
        with grpc.insecure_channel(f"127.0.0.1:{peers_port}") as peers_channel:
            peers_stub = peers_pb2_grpc.PeersServiceStub(peers_channel)
            baseline_response = peers_stub.RequestBaseline(
                peers_pb2.RequestBaselineRequest(champion="Kayle", lane="TOP", rank="EMERALD")
            )
        assert baseline_response.cached is False

        with grpc.insecure_channel(f"127.0.0.1:{runner_port}") as runner_channel:
            runner_stub = runner_pb2_grpc.RunnerServiceStub(runner_channel)
            ack = runner_stub.NotifyPeerBaselineReady(
                runner_pb2.PeerBaselineReadyRequest(
                    request_id=baseline_response.request_id,
                    champion="Kayle",
                    lane="TOP",
                    rank="EMERALD",
                    baseline_json="{}",
                )
            )
        assert ack.ok is True
        assert ack.message == "received req-1"
    finally:
        peers_server.stop(None)
        runner_server.stop(None)
