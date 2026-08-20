"""PEERS' gRPC service: wraps `resolve_peer_baseline` verbatim via a duck-typed
store adapter around `PeerSampleStore` (Task 1), following the same pattern
RUNNER's `RunnerJobAdapter` established for `execute_job`.
"""

from __future__ import annotations

import json
import threading
import time
from concurrent import futures
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock

import grpc
import mongomock
import pytest

from league_stats.analysis.peer.baseline import PeerBaseline
from league_stats.analysis.peer.ingest import ingest_match
from league_stats.core.models import RankedEntry
from league_stats.infra.peer_sample_store import PeerSampleStore
from league_stats.peers import service as peers_service
from league_stats.peers.service import (
    PeersServicer,
    _db_name_from_uri,
    _parse_rank,
    _PeerStoreAdapter,
    _PlatformScopedRiotClient,
)
from league_stats_rpc.v1 import peers_pb2, peers_pb2_grpc, runner_pb2, runner_pb2_grpc
from tests.fixtures import make_match

# --------------------------------------------------------------- _parse_rank


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("GOLD II", ("GOLD", "II")),
        ("gold ii", ("GOLD", "II")),
        ("GOLD_II", ("GOLD", "II")),
        ("CHALLENGER", ("CHALLENGER", "")),
        ("", ("", "")),
    ],
)
def test_parse_rank(raw, expected):
    assert _parse_rank(raw) == expected


# --------------------------------------------------------------- _PeerStoreAdapter


@pytest.fixture
def peer_store():
    client = mongomock.MongoClient()
    return PeerSampleStore(client, db_name="league_stats_test")


def test_adapter_delegates_peer_game_methods_to_the_real_store(peer_store):
    adapter = _PeerStoreAdapter(peer_store)
    row = {
        "match_id": "EUW1_1",
        "puuid": "puuid-a",
        "champion": "Ahri",
        "role": "MIDDLE",
        "platform": "euw1",
        "queue_id": 420,
        "metrics": {"kda": 3.5},
        "ingested_at": 1000.0,
    }

    assert adapter.upsert_peer_game(row) is True
    assert adapter.count_peer_games(champion="Ahri", role="MIDDLE", platform="euw1") == 1
    assert len(adapter.load_peer_games(champion="Ahri", role="MIDDLE", platform="euw1")) == 1
    assert adapter.iter_unverified_puuids() == ["puuid-a"]
    assert adapter.iter_unverified_puuids_for_build("Ahri", "MIDDLE", "euw1") == ["puuid-a"]
    assert adapter.set_puuid_rank("puuid-a", "gold", "ii") == 1


def test_adapter_iter_match_ids_is_a_documented_noop(peer_store):
    """PeerSampleStore has no raw match history -- see service.py's module docstring."""
    adapter = _PeerStoreAdapter(peer_store)
    assert list(adapter.iter_match_ids("any-puuid")) == []


def test_adapter_load_match_is_a_documented_noop(peer_store):
    adapter = _PeerStoreAdapter(peer_store)
    assert adapter.load_match("EUW1_1") is None


def test_adapter_save_match_is_a_documented_noop_that_does_not_raise(peer_store):
    adapter = _PeerStoreAdapter(peer_store)
    adapter.save_match("EUW1_1", "puuid-a", {"info": {}})  # must not raise


# --------------------------------------------------------------- _db_name_from_uri


@pytest.mark.parametrize(
    "uri, expected",
    [
        ("mongodb://host:27017/mydb", "mydb"),
        ("mongodb://host:27017/mydb?retryWrites=true&w=majority", "mydb"),
        ("mongodb://host:27017/", "league_stats"),
        ("mongodb://host:27017", "league_stats"),
    ],
)
def test_db_name_from_uri(uri, expected):
    assert _db_name_from_uri(uri) == expected


# --------------------------------------------------------------- _PlatformScopedRiotClient


def test_platform_scoped_riot_client_overrides_platform_and_platform_base():
    base = _fake_riot_client()
    scoped = _PlatformScopedRiotClient(base, "kr")

    assert scoped.platform == "kr"
    assert scoped.platform_base == "https://kr.api.riotgames.com"
    assert base.platform == "euw1"  # the shared client is untouched


def test_platform_scoped_riot_client_delegates_other_attributes():
    base = _fake_riot_client()
    base.fetch_solo_rank.return_value = "some-rank"
    scoped = _PlatformScopedRiotClient(base, "kr")

    assert scoped.fetch_solo_rank("puuid-a") == "some-rank"
    base.fetch_solo_rank.assert_called_once_with("puuid-a")


# --------------------------------------------------------------- gRPC service


def _start_peers_server(servicer):
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=4))
    peers_pb2_grpc.add_PeersServiceServicer_to_server(servicer, server)
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()
    return server, port


class _FakeRunnerServicer(runner_pb2_grpc.RunnerServiceServicer):
    """Captures NotifyPeerBaselineReady calls for assertions."""

    def __init__(self) -> None:
        self.received: list[runner_pb2.PeerBaselineReadyRequest] = []
        self._event = threading.Event()

    def NotifyPeerBaselineReady(self, request, context):
        from league_stats_rpc.v1 import common_pb2

        self.received.append(request)
        self._event.set()
        return common_pb2.Ack(ok=True)

    def wait(self, timeout: float = 5.0) -> bool:
        return self._event.wait(timeout)


@pytest.fixture
def fake_runner():
    servicer = _FakeRunnerServicer()
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=2))
    runner_pb2_grpc.add_RunnerServiceServicer_to_server(servicer, server)
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()
    yield servicer, f"127.0.0.1:{port}"
    server.stop(grace=None)


def _fake_riot_client() -> MagicMock:
    client = MagicMock()
    client.configure_mock(platform="euw1")
    return client


def test_request_baseline_rejects_missing_fields(fake_runner):
    _, runner_target = fake_runner
    client = mongomock.MongoClient()
    peer_store = PeerSampleStore(client, db_name="league_stats_test")
    servicer = PeersServicer(
        peer_store=peer_store, riot_client=_fake_riot_client(), runner_target=runner_target
    )
    server, port = _start_peers_server(servicer)
    channel = grpc.insecure_channel(f"127.0.0.1:{port}")
    try:
        stub = peers_pb2_grpc.PeersServiceStub(channel)
        with pytest.raises(grpc.RpcError) as exc_info:
            stub.RequestBaseline(peers_pb2.RequestBaselineRequest(champion="Ahri"))
        assert exc_info.value.code() == grpc.StatusCode.INVALID_ARGUMENT
    finally:
        channel.close()
        server.stop(grace=None)


def test_request_baseline_resolves_synchronously_from_the_peer_store(
    monkeypatch: pytest.MonkeyPatch, fake_runner
):
    """Enough exact-rank store games (level 0) resolves well inside the fast-path
    timeout, so the response carries the baseline directly (cached=True)."""
    import league_stats.analysis.peer.baseline as peer_baseline

    monkeypatch.setattr(peer_baseline, "MIN_EXACT_GAMES", 2)

    _, runner_target = fake_runner
    mongo_client = mongomock.MongoClient()
    peer_store = PeerSampleStore(mongo_client, db_name="league_stats_test")

    for index in range(2):
        match = make_match()
        match["info"]["participants"][1]["puuid"] = f"peer-{index}"
        ingest_match(_PeerStoreAdapter(peer_store), f"EUW1_{index}", match, "euw1")
        peer_store.set_puuid_rank(f"peer-{index}", "EMERALD", "II")

    servicer = PeersServicer(
        peer_store=peer_store,
        riot_client=_fake_riot_client(),
        runner_target=runner_target,
        fast_path_timeout_s=3.0,
    )
    server, port = _start_peers_server(servicer)
    channel = grpc.insecure_channel(f"127.0.0.1:{port}")
    try:
        stub = peers_pb2_grpc.PeersServiceStub(channel)
        response = stub.RequestBaseline(
            peers_pb2.RequestBaselineRequest(champion="LeeSin", lane="JUNGLE", rank="EMERALD II")
        )
    finally:
        channel.close()
        server.stop(grace=None)

    assert response.cached is True
    assert response.error == ""
    assert response.request_id
    payload = json.loads(response.baseline_json)
    assert payload["fallback_level"] == 0
    assert payload["games"] >= 2


def test_request_baseline_falls_back_to_static_benchmark_synchronously(fake_runner):
    """An empty store + no live sampling still resolves fast via static JSON
    (level 4/5), so this is also a cached=True response."""
    _, runner_target = fake_runner
    mongo_client = mongomock.MongoClient()
    peer_store = PeerSampleStore(mongo_client, db_name="league_stats_test")
    client = _fake_riot_client()
    client.fetch_league_entries_pages.return_value = []

    servicer = PeersServicer(
        peer_store=peer_store,
        riot_client=client,
        runner_target=runner_target,
        fast_path_timeout_s=3.0,
    )
    server, port = _start_peers_server(servicer)
    channel = grpc.insecure_channel(f"127.0.0.1:{port}")
    try:
        stub = peers_pb2_grpc.PeersServiceStub(channel)
        response = stub.RequestBaseline(
            peers_pb2.RequestBaselineRequest(champion="Ornn", lane="TOP", rank="EMERALD II")
        )
    finally:
        channel.close()
        server.stop(grace=None)

    assert response.cached is True
    assert response.error == ""
    payload = json.loads(response.baseline_json)
    assert payload["fallback_level"] in (4, 5)


def test_request_baseline_falls_back_to_background_thread_and_notifies_runner(
    monkeypatch: pytest.MonkeyPatch, fake_runner
):
    """A resolution that outlives the fast-path timeout returns cached=False
    immediately and later calls RunnerServiceStub.NotifyPeerBaselineReady."""
    fake_runner_servicer, runner_target = fake_runner

    def _slow_resolve(client, store, ranked, champion, role, **kwargs):
        time.sleep(0.3)
        return PeerBaseline(
            metrics={"kda": 4.0},
            games=60,
            players=12,
            source="live sample",
            confidence="medium",
            fallback_level=2,
        )

    monkeypatch.setattr(peers_service, "resolve_peer_baseline", _slow_resolve)

    mongo_client = mongomock.MongoClient()
    peer_store = PeerSampleStore(mongo_client, db_name="league_stats_test")
    servicer = PeersServicer(
        peer_store=peer_store,
        riot_client=_fake_riot_client(),
        runner_target=runner_target,
        fast_path_timeout_s=0.05,
    )
    server, port = _start_peers_server(servicer)
    channel = grpc.insecure_channel(f"127.0.0.1:{port}")
    try:
        stub = peers_pb2_grpc.PeersServiceStub(channel)
        response = stub.RequestBaseline(
            peers_pb2.RequestBaselineRequest(champion="Ahri", lane="MIDDLE", rank="GOLD II")
        )
        assert response.cached is False
        assert response.request_id

        assert fake_runner_servicer.wait(timeout=5.0), "RUNNER was never notified"
    finally:
        channel.close()
        server.stop(grace=None)

    assert len(fake_runner_servicer.received) == 1
    notified = fake_runner_servicer.received[0]
    assert notified.request_id == response.request_id
    assert notified.champion == "Ahri"
    assert notified.lane == "MIDDLE"
    assert notified.error == ""
    payload = json.loads(notified.baseline_json)
    assert payload["fallback_level"] == 2
    assert payload["games"] == 60


def test_request_baseline_background_failure_notifies_runner_with_error(
    monkeypatch: pytest.MonkeyPatch, fake_runner
):
    fake_runner_servicer, runner_target = fake_runner

    def _slow_boom(client, store, ranked, champion, role, **kwargs):
        time.sleep(0.3)
        raise RuntimeError("riot api on fire")

    monkeypatch.setattr(peers_service, "resolve_peer_baseline", _slow_boom)

    mongo_client = mongomock.MongoClient()
    peer_store = PeerSampleStore(mongo_client, db_name="league_stats_test")
    servicer = PeersServicer(
        peer_store=peer_store,
        riot_client=_fake_riot_client(),
        runner_target=runner_target,
        fast_path_timeout_s=0.05,
    )
    server, port = _start_peers_server(servicer)
    channel = grpc.insecure_channel(f"127.0.0.1:{port}")
    try:
        stub = peers_pb2_grpc.PeersServiceStub(channel)
        response = stub.RequestBaseline(
            peers_pb2.RequestBaselineRequest(champion="Ahri", lane="MIDDLE", rank="GOLD II")
        )
        assert response.cached is False

        assert fake_runner_servicer.wait(timeout=5.0), "RUNNER was never notified"
    finally:
        channel.close()
        server.stop(grace=None)

    notified = fake_runner_servicer.received[0]
    assert notified.baseline_json == ""
    assert "riot api on fire" in notified.error


def test_request_baseline_uses_request_platform_and_exclude_puuid(
    monkeypatch: pytest.MonkeyPatch, fake_runner
):
    """`request.platform`/`request.exclude_puuid` must actually be used, not just
    accepted -- regression test for review round 1's fix 1. The riot client's
    own default platform is "euw1" (see `_fake_riot_client`); this request asks
    for "na1" instead, and should resolve using na1-platform rows only, with
    the given puuid excluded from the peer average."""
    import league_stats.analysis.peer.baseline as peer_baseline

    monkeypatch.setattr(peer_baseline, "MIN_EXACT_GAMES", 2)

    _, runner_target = fake_runner
    mongo_client = mongomock.MongoClient()
    peer_store = PeerSampleStore(mongo_client, db_name="league_stats_test")

    # 3 na1 rows, one of which belongs to the excluded puuid -- only 2 should count.
    for index in range(3):
        match = make_match()
        match["info"]["participants"][1]["puuid"] = f"na-peer-{index}"
        ingest_match(_PeerStoreAdapter(peer_store), f"NA1_{index}", match, "na1")
        peer_store.set_puuid_rank(f"na-peer-{index}", "EMERALD", "II")

    # 5 euw1 rows for the same build -- must NOT be picked up (wrong platform,
    # i.e. the riot client's own default, which the request should override).
    for index in range(5):
        match = make_match()
        match["info"]["participants"][1]["puuid"] = f"euw-peer-{index}"
        ingest_match(_PeerStoreAdapter(peer_store), f"EUW1_{index}", match, "euw1")
        peer_store.set_puuid_rank(f"euw-peer-{index}", "EMERALD", "II")

    servicer = PeersServicer(
        peer_store=peer_store,
        riot_client=_fake_riot_client(),  # default platform "euw1"
        runner_target=runner_target,
        fast_path_timeout_s=3.0,
    )
    server, port = _start_peers_server(servicer)
    channel = grpc.insecure_channel(f"127.0.0.1:{port}")
    try:
        stub = peers_pb2_grpc.PeersServiceStub(channel)
        response = stub.RequestBaseline(
            peers_pb2.RequestBaselineRequest(
                champion="LeeSin",
                lane="JUNGLE",
                rank="EMERALD II",
                platform="na1",
                exclude_puuid="na-peer-0",
            )
        )
    finally:
        channel.close()
        server.stop(grace=None)

    assert response.cached is True
    assert response.error == ""
    payload = json.loads(response.baseline_json)
    assert payload["fallback_level"] == 0
    # 3 na1 rows minus the 1 excluded puuid = 2. If platform routing or
    # exclude_puuid weren't actually wired, this would read 3 (no exclusion)
    # or 5 (wrong, euw1-default platform) instead.
    assert payload["games"] == 2


def test_request_baseline_dedups_identical_inflight_requests(
    monkeypatch: pytest.MonkeyPatch, fake_runner
):
    """Two concurrent identical (champion, role, platform, rank) requests must
    share one underlying resolve_peer_baseline call instead of each launching
    an independent live sample."""
    fake_runner_servicer, runner_target = fake_runner
    call_count = {"n": 0}
    release = threading.Event()

    def _slow_resolve(client, store, ranked, champion, role, **kwargs):
        call_count["n"] += 1
        release.wait(timeout=5.0)
        return PeerBaseline(
            metrics={"kda": 4.0},
            games=60,
            players=12,
            source="live sample",
            confidence="medium",
            fallback_level=2,
        )

    monkeypatch.setattr(peers_service, "resolve_peer_baseline", _slow_resolve)

    mongo_client = mongomock.MongoClient()
    peer_store = PeerSampleStore(mongo_client, db_name="league_stats_test")
    servicer = PeersServicer(
        peer_store=peer_store,
        riot_client=_fake_riot_client(),
        runner_target=runner_target,
        fast_path_timeout_s=0.05,
    )
    server, port = _start_peers_server(servicer)
    channel = grpc.insecure_channel(f"127.0.0.1:{port}")
    try:
        stub = peers_pb2_grpc.PeersServiceStub(channel)
        request = peers_pb2.RequestBaselineRequest(champion="Ahri", lane="MIDDLE", rank="GOLD II")

        responses = [stub.RequestBaseline(request), stub.RequestBaseline(request)]
        # Give both calls time to reach _get_or_submit before releasing the shared future.
        time.sleep(0.2)
        release.set()

        for response in responses:
            assert response.cached is False

        # Let both callbacks reach the (still-live) fake RUNNER before tearing
        # it down, purely to avoid a harmless but noisy "connection refused"
        # log if the background callback fires after the server stops.
        deadline = time.monotonic() + 5.0
        while len(fake_runner_servicer.received) < 2 and time.monotonic() < deadline:
            time.sleep(0.05)
    finally:
        channel.close()
        server.stop(grace=None)

    assert call_count["n"] == 1, "expected exactly one underlying resolve_peer_baseline call"
    assert peers_service.PEERS_DEDUPED_REQUESTS_TOTAL._value.get() >= 1


def test_notify_runner_swallows_non_grpc_exceptions(monkeypatch: pytest.MonkeyPatch, fake_runner):
    """Any exception inside `_notify_runner` (not just `grpc.RpcError`) must be
    caught and logged, not raised out of a done-callback -- otherwise RUNNER
    would wait forever with no diagnostic (review round 1, fix 6)."""
    _, runner_target = fake_runner
    mongo_client = mongomock.MongoClient()
    peer_store = PeerSampleStore(mongo_client, db_name="league_stats_test")
    servicer = PeersServicer(
        peer_store=peer_store,
        riot_client=_fake_riot_client(),
        runner_target=runner_target,
    )

    def _boom(*args, **kwargs):
        raise ValueError("channel construction exploded")

    monkeypatch.setattr(peers_service.grpc, "insecure_channel", _boom)

    servicer._notify_runner(
        "req-1", "Ahri", "MIDDLE", "GOLD II", baseline_json="{}", error=""
    )  # must not raise


def test_build_default_riot_client_error_names_the_correct_env_var(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.delenv("PEERS_RIOT_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="PEERS_RIOT_API_KEY"):
        peers_service._build_default_riot_client()


# ----------------------------------------- real level-2 ladder, no-op adapter


def _league_entry(puuid: str) -> dict[str, str]:
    return {"puuid": puuid, "tier": "GOLD", "rank": "II"}


def _match_for(puuid: str, champion: str = "Zac", role: str = "JUNGLE") -> dict:
    match = make_match()
    participant = match["info"]["participants"][1]
    participant["puuid"] = puuid
    participant["championName"] = champion
    participant["teamPosition"] = role
    return match


def test_resolve_peer_baseline_via_live_sampling_survives_the_noop_store_methods(
    monkeypatch: pytest.MonkeyPatch, peer_store
):
    """Direct proof (review round 1, fix 7) that the real, unmodified
    `resolve_peer_baseline` ladder -- specifically level 2's live sampling,
    which calls `load_match`/`save_match` via `_load_or_fetch_match`, and the
    store bootstrap in `collect_peer_games_from_store`, which calls
    `iter_match_ids`/`load_match` -- survives running against `_PeerStoreAdapter`'s
    no-op stubs for those three methods, exactly as it would in production."""
    import league_stats.analysis.peer.baseline as peer_baseline

    monkeypatch.setattr(
        "league_stats.analysis.peer.benchmark_fetcher.MIN_BENCHMARK_GAMES", 3
    )
    monkeypatch.setattr("league_stats.analysis.peer.benchmark_fetcher.TARGET_PEER_GAMES", 3)
    monkeypatch.setattr("league_stats.analysis.peer.benchmark_fetcher.MAX_MATCH_DOWNLOADS", 10)
    # Keep this test off the real on-disk live-sample file cache.
    monkeypatch.setattr(peer_baseline, "read_live_cache", lambda *a, **k: None)
    monkeypatch.setattr(peer_baseline, "write_live_cache", lambda *a, **k: None)

    adapter = _PeerStoreAdapter(peer_store)
    client = MagicMock()
    client.configure_mock(platform="euw1")
    client.fetch_league_entries_pages.return_value = [
        _league_entry(f"peer-{index}") for index in range(5)
    ]
    client.fetch_match_ids.side_effect = lambda puuid, count, queue_id=None: [f"EUW1_{puuid}"]
    client.fetch_match.side_effect = lambda match_id: _match_for(match_id.removeprefix("EUW1_"))
    client.fetch_solo_rank.return_value = RankedEntry(
        tier="GOLD", rank="II", league_points=45, wins=10, losses=10
    )

    ranked = RankedEntry(tier="GOLD", rank="II", league_points=45, wins=10, losses=10)
    baseline = peer_baseline.resolve_peer_baseline(
        client, adapter, ranked, "Zac", "JUNGLE", exclude_puuid="puuid-me"
    )

    assert baseline is not None
    assert baseline.fallback_level == 2
    assert baseline.games >= 3
    # The extracted peer rows were still persisted for real via ingest_match's
    # upsert_peer_game, even though the raw match documents were never cached.
    assert peer_store.count_peer_games(champion="Zac", role="JUNGLE", platform="euw1") >= 3
