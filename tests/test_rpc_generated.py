"""Generated gRPC stubs exist and expose the expected names."""


def test_common_pb2_exposes_enums_and_ack():
    from league_stats_rpc.v1 import common_pb2

    assert common_pb2.Region.Name(common_pb2.EUROPE) == "EUROPE"
    assert common_pb2.Stage.Name(common_pb2.SUMMARY) == "SUMMARY"
    ack = common_pb2.Ack(ok=True, message="fine")
    assert ack.ok is True


def test_cron_watch_pb2_grpc_exposes_stub_and_servicer():
    from league_stats_rpc.v1 import cron_watch_pb2, cron_watch_pb2_grpc

    assert hasattr(cron_watch_pb2_grpc, "CronWatchServiceStub")
    assert hasattr(cron_watch_pb2_grpc, "CronWatchServiceServicer")
    req = cron_watch_pb2.RegisterAccountRequest(puuid="abc")
    assert req.puuid == "abc"


def test_runner_pb2_grpc_exposes_stub_and_servicer():
    from league_stats_rpc.v1 import runner_pb2, runner_pb2_grpc

    assert hasattr(runner_pb2_grpc, "RunnerServiceStub")
    assert hasattr(runner_pb2_grpc, "RunnerServiceServicer")
    req = runner_pb2.EnqueueJobRequest(puuid="abc", match_id="EUW1_1")
    assert req.match_id == "EUW1_1"


def test_peers_pb2_grpc_exposes_stub_and_servicer():
    from league_stats_rpc.v1 import peers_pb2, peers_pb2_grpc

    assert hasattr(peers_pb2_grpc, "PeersServiceStub")
    assert hasattr(peers_pb2_grpc, "PeersServiceServicer")
    req = peers_pb2.RequestBaselineRequest(champion="Kayle", lane="TOP", rank="EMERALD")
    assert req.champion == "Kayle"
