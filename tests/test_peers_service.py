"""PEERS' gRPC service: wraps `resolve_peer_baseline` verbatim via a duck-typed
store adapter around `PeerSampleStore` (Task 1), following the same pattern
RUNNER's `RunnerJobAdapter` established for `execute_job`.
"""

from __future__ import annotations

import json
import threading
import time
from concurrent import futures
from unittest.mock import MagicMock

import grpc
import mongomock
import pytest

from league_stats_peers.analysis.peer.baseline import PeerBaseline
from league_stats_peers.analysis.peer.ingest import ingest_match
from league_stats_common.core.config import VALID_PLATFORMS
from league_stats_common.core.models import RankedEntry
from league_stats_common.infra.cache import HttpCache
from league_stats_peers.infra.peer_sample_store import PeerSampleStore
from league_stats_runner.infra.raw_match_store import RawMatchStore
from league_stats_peers import service as peers_service
from league_stats_peers.service import (
    PeersServicer,
    _build_riot_client_for_platform,
    _db_name_from_uri,
    _parse_rank,
    _PeerStoreAdapter,
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


# --------------------------------------------------- _build_riot_client_for_platform


class _FakeSession:
    """Records every URL `RiotApiClient._get` actually requests, so a test can
    assert on the real host contacted -- proving routing, not just that some
    wrapper's own attributes reflect the request (see service.py's module
    docstring, "Platform routing", on why the round 1 wrapper's tests missed
    exactly this)."""

    def __init__(self) -> None:
        self.requested_urls: list[str] = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.requested_urls.append(url)

        class _Response:
            status_code = 200

            @staticmethod
            def json():
                # league-v4 non-apex entries expect a list; everything else
                # (match-v5 documents) expects a dict -- either is a fine,
                # minimal stand-in since only the requested URL is asserted on.
                return [] if "league/v4" in url else {}

        return _Response()


def _riot_client_test_deps(tmp_path):
    http_cache = HttpCache(tmp_path / "http")
    match_store = RawMatchStore(mongomock.MongoClient(), db_name="league_stats")
    return http_cache, match_store


def test_build_riot_client_for_platform_routes_league_v4_to_the_requested_platform(tmp_path):
    http_cache, match_store = _riot_client_test_deps(tmp_path)
    session = _FakeSession()
    client = _build_riot_client_for_platform(
        "na1", api_key="fake-key", http_cache=http_cache, match_store=match_store, session=session
    )

    client.fetch_league_entries_pages("GOLD", "II")

    assert session.requested_urls, "expected at least one real HTTP call"
    assert all("na1.api.riotgames.com" in url for url in session.requested_urls)
    assert not any("euw1.api.riotgames.com" in url for url in session.requested_urls)


def test_build_riot_client_for_platform_routes_match_v5_to_the_derived_region(tmp_path):
    """The regional (match-v5) host must be derived from PLATFORM_TO_REGION for
    the requested platform, not left at whatever region a single static env
    var configured -- the exact bug the round 1 wrapper had (it never touched
    `_regional_base` at all)."""
    http_cache, match_store = _riot_client_test_deps(tmp_path)

    na1_session = _FakeSession()
    na1_client = _build_riot_client_for_platform(
        "na1", api_key="fake-key", http_cache=http_cache, match_store=match_store, session=na1_session
    )
    na1_client.fetch_match("NA1_123")
    assert any("americas.api.riotgames.com" in url for url in na1_session.requested_urls)

    kr_session = _FakeSession()
    kr_client = _build_riot_client_for_platform(
        "kr", api_key="fake-key", http_cache=http_cache, match_store=match_store, session=kr_session
    )
    kr_client.fetch_match("KR_123")
    assert any("asia.api.riotgames.com" in url for url in kr_session.requested_urls)

    euw_session = _FakeSession()
    euw_client = _build_riot_client_for_platform(
        "euw1", api_key="fake-key", http_cache=http_cache, match_store=match_store, session=euw_session
    )
    euw_client.fetch_match("EUW1_123")
    assert any("europe.api.riotgames.com" in url for url in euw_session.requested_urls)


def test_build_riot_client_for_platform_never_mutated_after_construction(tmp_path):
    """Each platform's client is a distinct, independently-configured instance --
    building one for "na1" must not affect one already built for "euw1"."""
    http_cache, match_store = _riot_client_test_deps(tmp_path)
    euw_client = _build_riot_client_for_platform(
        "euw1", api_key="fake-key", http_cache=http_cache, match_store=match_store
    )
    na_client = _build_riot_client_for_platform(
        "na1", api_key="fake-key", http_cache=http_cache, match_store=match_store
    )

    assert euw_client.platform == "euw1"
    assert na_client.platform == "na1"


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


def _fake_riot_client(platform: str = "euw1") -> MagicMock:
    client = MagicMock()
    client.configure_mock(platform=platform)
    return client


def _fixed_riot_client_factory(client):
    """A `riot_client_factory` that returns the same fake regardless of the
    requested platform -- for tests where platform-specific behavior isn't
    under test."""
    return lambda platform: client


def test_request_baseline_rejects_missing_fields(fake_runner):
    _, runner_target = fake_runner
    client = mongomock.MongoClient()
    peer_store = PeerSampleStore(client, db_name="league_stats_test")
    servicer = PeersServicer(
        peer_store=peer_store, riot_client_factory=_fixed_riot_client_factory(_fake_riot_client()), runner_target=runner_target
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
    import league_stats_peers.analysis.peer.baseline as peer_baseline

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
        riot_client_factory=_fixed_riot_client_factory(_fake_riot_client()),
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
        riot_client_factory=_fixed_riot_client_factory(client),
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
        riot_client_factory=_fixed_riot_client_factory(_fake_riot_client()),
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
        riot_client_factory=_fixed_riot_client_factory(_fake_riot_client()),
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
    accepted -- regression test for review round 2's fix 1. The factory below
    mirrors the production pool: a distinct, correctly-`.platform`-configured
    fake per requested platform (never a single fixed client, which could not
    represent per-platform behavior at all -- the gap that let round 1's
    broken `_PlatformScopedRiotClient` look correct). This request asks for
    "na1"; it should resolve using na1-platform rows only, with the given
    puuid excluded from the peer average."""
    import league_stats_peers.analysis.peer.baseline as peer_baseline

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
        # Distinct, correctly-configured fake per platform -- see docstring above.
        riot_client_factory=lambda platform: _fake_riot_client(platform=platform),
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
        riot_client_factory=_fixed_riot_client_factory(_fake_riot_client()),
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


def test_request_baseline_passes_patch_to_resolve_peer_baseline(
    monkeypatch: pytest.MonkeyPatch, fake_runner
):
    """`request.patch` must actually reach `resolve_peer_baseline` -- regression
    test for finding 1 of the final whole-branch review. Previously the proto
    had no `patch` field at all, so PEERS always resolved with `patch=""`,
    which `select_by_patch` (`analysis/peer/cache.py`) treats as "no filter"
    and blends every patch ever ingested into one baseline."""
    _, runner_target = fake_runner
    seen_patches: list[str] = []

    def _spy_resolve(client, store, ranked, champion, role, **kwargs):
        seen_patches.append(kwargs.get("patch", "<missing>"))
        return PeerBaseline(
            metrics={"kda": 4.0},
            games=60,
            players=12,
            source="live sample",
            confidence="medium",
            fallback_level=2,
        )

    monkeypatch.setattr(peers_service, "resolve_peer_baseline", _spy_resolve)

    mongo_client = mongomock.MongoClient()
    peer_store = PeerSampleStore(mongo_client, db_name="league_stats_test")
    servicer = PeersServicer(
        peer_store=peer_store,
        riot_client_factory=_fixed_riot_client_factory(_fake_riot_client()),
        runner_target=runner_target,
        fast_path_timeout_s=3.0,
    )
    server, port = _start_peers_server(servicer)
    channel = grpc.insecure_channel(f"127.0.0.1:{port}")
    try:
        stub = peers_pb2_grpc.PeersServiceStub(channel)
        response = stub.RequestBaseline(
            peers_pb2.RequestBaselineRequest(
                champion="Ahri", lane="MIDDLE", rank="GOLD II", patch="14.3"
            )
        )
    finally:
        channel.close()
        server.stop(grace=None)

    assert response.cached is True
    assert seen_patches == ["14.3"]


def test_request_baseline_different_patches_do_not_share_dedup_slot(
    monkeypatch: pytest.MonkeyPatch, fake_runner
):
    """Two in-flight requests for the same champion/role/platform/tier but
    different patches must each run their own `resolve_peer_baseline` call --
    a stale in-flight resolution for a different patch must never be joined
    (finding 1 of the final whole-branch review)."""
    fake_runner_servicer, runner_target = fake_runner
    call_patches: list[str] = []
    release = threading.Event()

    def _slow_resolve(client, store, ranked, champion, role, **kwargs):
        call_patches.append(kwargs.get("patch", "<missing>"))
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
        riot_client_factory=_fixed_riot_client_factory(_fake_riot_client()),
        runner_target=runner_target,
        fast_path_timeout_s=0.05,
    )
    server, port = _start_peers_server(servicer)
    channel = grpc.insecure_channel(f"127.0.0.1:{port}")
    try:
        stub = peers_pb2_grpc.PeersServiceStub(channel)
        request_a = peers_pb2.RequestBaselineRequest(
            champion="Ahri", lane="MIDDLE", rank="GOLD II", patch="14.3"
        )
        request_b = peers_pb2.RequestBaselineRequest(
            champion="Ahri", lane="MIDDLE", rank="GOLD II", patch="14.4"
        )

        responses = [stub.RequestBaseline(request_a), stub.RequestBaseline(request_b)]
        time.sleep(0.2)
        release.set()

        for response in responses:
            assert response.cached is False

        deadline = time.monotonic() + 5.0
        while len(fake_runner_servicer.received) < 2 and time.monotonic() < deadline:
            time.sleep(0.05)
    finally:
        channel.close()
        server.stop(grace=None)

    assert sorted(call_patches) == ["14.3", "14.4"], (
        "expected two independent resolve_peer_baseline calls, one per patch"
    )


def test_get_or_submit_records_error_metric_on_failure(monkeypatch: pytest.MonkeyPatch, fake_runner):
    """A failed `resolve_peer_baseline` call must still be observed in
    `PEERS_BASELINE_RESOLUTION_DURATION`/`PEERS_BASELINE_RESOLUTIONS_TOTAL`
    (labeled `source="error"`), not just the success path -- finding 5 of the
    final whole-branch review."""
    _, runner_target = fake_runner

    def _boom(client, store, ranked, champion, role, **kwargs):
        raise RuntimeError("riot api on fire")

    monkeypatch.setattr(peers_service, "resolve_peer_baseline", _boom)

    mongo_client = mongomock.MongoClient()
    peer_store = PeerSampleStore(mongo_client, db_name="league_stats_test")
    servicer = PeersServicer(
        peer_store=peer_store,
        riot_client_factory=_fixed_riot_client_factory(_fake_riot_client()),
        runner_target=runner_target,
        fast_path_timeout_s=3.0,
    )

    before_count = peers_service.PEERS_BASELINE_RESOLUTIONS_TOTAL.labels(source="error")._value.get()
    before_observations = (
        peers_service.PEERS_BASELINE_RESOLUTION_DURATION.labels(source="error")._sum.get()
    )

    record = servicer._get_or_submit(
        ("ahri", "MIDDLE", "euw1", "GOLD", "14.3"),
        _fake_riot_client(),
        _PeerStoreAdapter(peer_store),
        RankedEntry(tier="GOLD", rank="II", league_points=0, wins=0, losses=0),
        "Ahri",
        "MIDDLE",
        None,
        "14.3",
    )
    with pytest.raises(RuntimeError, match="riot api on fire"):
        record.future.result(timeout=5.0)

    after_count = peers_service.PEERS_BASELINE_RESOLUTIONS_TOTAL.labels(source="error")._value.get()
    after_observations = peers_service.PEERS_BASELINE_RESOLUTION_DURATION.labels(source="error")._sum.get()
    assert after_count == before_count + 1
    assert after_observations > before_observations


def test_get_or_submit_updates_queued_gauge_while_a_second_key_waits_behind_the_first(
    monkeypatch: pytest.MonkeyPatch, fake_runner
):
    """`PEERS_QUEUED_BASELINES` must reflect submitted-but-not-yet-running
    resolutions -- the complement `PEERS_INFLIGHT_BASELINES` (running only)
    can't express. A single-worker executor forces a second, distinct key to
    queue behind the first."""
    _, runner_target = fake_runner
    release = threading.Event()
    started_first = threading.Event()

    def _slow(client, store, ranked, champion, role, **kwargs):
        started_first.set()
        release.wait(timeout=5.0)
        return None

    monkeypatch.setattr(peers_service, "resolve_peer_baseline", _slow)

    mongo_client = mongomock.MongoClient()
    peer_store = PeerSampleStore(mongo_client, db_name="league_stats_test")
    executor = futures.ThreadPoolExecutor(max_workers=1)
    servicer = PeersServicer(
        peer_store=peer_store,
        riot_client_factory=_fixed_riot_client_factory(_fake_riot_client()),
        runner_target=runner_target,
        fast_path_timeout_s=0.1,
        executor=executor,
    )
    ranked = RankedEntry(tier="GOLD", rank="II", league_points=0, wins=0, losses=0)

    try:
        record1 = servicer._get_or_submit(
            ("ahri", "MIDDLE", "euw1", "GOLD", "14.3"),
            _fake_riot_client(),
            _PeerStoreAdapter(peer_store),
            ranked,
            "Ahri",
            "MIDDLE",
            None,
            "14.3",
        )
        assert started_first.wait(timeout=5.0), "first resolution never started"

        record2 = servicer._get_or_submit(
            ("zed", "MIDDLE", "euw1", "GOLD", "14.3"),
            _fake_riot_client(),
            _PeerStoreAdapter(peer_store),
            ranked,
            "Zed",
            "MIDDLE",
            None,
            "14.3",
        )

        assert peers_service.PEERS_QUEUED_BASELINES._value.get() >= 1

        release.set()
        record1.future.result(timeout=5.0)
        record2.future.result(timeout=5.0)
    finally:
        release.set()
        executor.shutdown(wait=True)

    assert peers_service.PEERS_QUEUED_BASELINES._value.get() == 0


def test_request_baseline_records_fast_path_attempt(
    monkeypatch: pytest.MonkeyPatch, fake_runner
):
    """`PEERS_FAST_PATH_ATTEMPTS_TOTAL` must be incremented for every
    `RequestBaseline` call that waits on the fast-path timeout -- the
    denominator `PEERS_FAST_PATH_TIMEOUTS_TOTAL` needs to compute a real
    timeout rate rather than just a raw count."""
    _, runner_target = fake_runner
    mongo_client = mongomock.MongoClient()
    peer_store = PeerSampleStore(mongo_client, db_name="league_stats_test")
    servicer = PeersServicer(
        peer_store=peer_store,
        riot_client_factory=_fixed_riot_client_factory(_fake_riot_client()),
        runner_target=runner_target,
        fast_path_timeout_s=3.0,
    )

    before = peers_service.PEERS_FAST_PATH_ATTEMPTS_TOTAL._value.get()

    request = peers_pb2.RequestBaselineRequest(champion="Ahri", lane="MIDDLE", rank="GOLD II")
    servicer.RequestBaseline(request, MagicMock())

    after = peers_service.PEERS_FAST_PATH_ATTEMPTS_TOTAL._value.get()
    assert after == before + 1


def test_notify_runner_swallows_non_grpc_exceptions(monkeypatch: pytest.MonkeyPatch, fake_runner):
    """Any exception inside `_notify_runner` (not just `grpc.RpcError`) must be
    caught and logged, not raised out of a done-callback -- otherwise RUNNER
    would wait forever with no diagnostic (review round 1, fix 6)."""
    _, runner_target = fake_runner
    mongo_client = mongomock.MongoClient()
    peer_store = PeerSampleStore(mongo_client, db_name="league_stats_test")
    servicer = PeersServicer(
        peer_store=peer_store,
        riot_client_factory=_fixed_riot_client_factory(_fake_riot_client()),
        runner_target=runner_target,
    )

    def _boom(*args, **kwargs):
        raise ValueError("channel construction exploded")

    monkeypatch.setattr(peers_service.grpc, "insecure_channel", _boom)

    servicer._notify_runner(
        "req-1", "Ahri", "MIDDLE", "GOLD II", baseline_json="{}", error=""
    )  # must not raise


def test_build_default_riot_client_factory_error_names_the_correct_env_var(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.delenv("PEERS_RIOT_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="PEERS_RIOT_API_KEY"):
        PeersServicer._build_default_riot_client_factory()


def test_request_baseline_rejects_unknown_platform(fake_runner):
    """An unrecognized platform string must be rejected outright -- both the
    "NA1" vs "na1" store-key mismatch and the risk of an arbitrary string
    reaching a URL host carrying PEERS' own Riot API key (review round 2,
    fix 1's "also fix the minor")."""
    _, runner_target = fake_runner
    mongo_client = mongomock.MongoClient()
    peer_store = PeerSampleStore(mongo_client, db_name="league_stats_test")
    servicer = PeersServicer(
        peer_store=peer_store,
        riot_client_factory=_fixed_riot_client_factory(_fake_riot_client()),
        runner_target=runner_target,
    )
    server, port = _start_peers_server(servicer)
    channel = grpc.insecure_channel(f"127.0.0.1:{port}")
    try:
        stub = peers_pb2_grpc.PeersServiceStub(channel)
        with pytest.raises(grpc.RpcError) as exc_info:
            stub.RequestBaseline(
                peers_pb2.RequestBaselineRequest(
                    champion="Ahri", lane="MIDDLE", rank="GOLD II", platform="not-a-real-platform"
                )
            )
        assert exc_info.value.code() == grpc.StatusCode.INVALID_ARGUMENT
    finally:
        channel.close()
        server.stop(grace=None)


def test_request_baseline_normalizes_platform_case(fake_runner):
    """"NA1" and "na1" must resolve to the same platform, not silently split
    store lookups into two different keys."""
    _, runner_target = fake_runner
    mongo_client = mongomock.MongoClient()
    peer_store = PeerSampleStore(mongo_client, db_name="league_stats_test")
    seen_platforms: list[str] = []

    def factory(platform):
        seen_platforms.append(platform)
        return _fake_riot_client(platform=platform)

    servicer = PeersServicer(
        peer_store=peer_store, riot_client_factory=factory, runner_target=runner_target
    )
    server, port = _start_peers_server(servicer)
    channel = grpc.insecure_channel(f"127.0.0.1:{port}")
    try:
        stub = peers_pb2_grpc.PeersServiceStub(channel)
        stub.RequestBaseline(
            peers_pb2.RequestBaselineRequest(
                champion="Ahri", lane="MIDDLE", rank="GOLD II", platform="NA1"
            )
        )
    finally:
        channel.close()
        server.stop(grace=None)

    assert seen_platforms == ["na1"]


def test_request_baseline_accepts_every_valid_platform(fake_runner):
    _, runner_target = fake_runner
    mongo_client = mongomock.MongoClient()
    peer_store = PeerSampleStore(mongo_client, db_name="league_stats_test")
    client = _fake_riot_client()
    client.fetch_league_entries_pages.return_value = []
    servicer = PeersServicer(
        peer_store=peer_store,
        riot_client_factory=_fixed_riot_client_factory(client),
        runner_target=runner_target,
        fast_path_timeout_s=3.0,
    )
    server, port = _start_peers_server(servicer)
    channel = grpc.insecure_channel(f"127.0.0.1:{port}")
    try:
        stub = peers_pb2_grpc.PeersServiceStub(channel)
        for platform in sorted(VALID_PLATFORMS):
            response = stub.RequestBaseline(
                peers_pb2.RequestBaselineRequest(
                    champion="Ahri", lane="MIDDLE", rank="GOLD II", platform=platform
                )
            )
            assert response.cached is True
            assert "unknown platform" not in response.error
    finally:
        channel.close()
        server.stop(grace=None)


def test_riot_client_for_caches_one_client_per_platform(fake_runner):
    _, runner_target = fake_runner
    mongo_client = mongomock.MongoClient()
    peer_store = PeerSampleStore(mongo_client, db_name="league_stats_test")
    build_count = {"n": 0}

    def factory(platform):
        build_count["n"] += 1
        return _fake_riot_client(platform=platform)

    servicer = PeersServicer(
        peer_store=peer_store, riot_client_factory=factory, runner_target=runner_target
    )

    first = servicer._riot_client_for("na1")
    second = servicer._riot_client_for("na1")
    third = servicer._riot_client_for("kr")

    assert first is second
    assert first is not third
    assert build_count["n"] == 2


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
    import league_stats_peers.analysis.peer.baseline as peer_baseline

    monkeypatch.setattr(
        "league_stats_peers.analysis.peer.benchmark_fetcher.MIN_BENCHMARK_GAMES", 3
    )
    monkeypatch.setattr("league_stats_peers.analysis.peer.benchmark_fetcher.TARGET_PEER_GAMES", 3)
    monkeypatch.setattr("league_stats_peers.analysis.peer.benchmark_fetcher.MAX_MATCH_DOWNLOADS", 10)
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
