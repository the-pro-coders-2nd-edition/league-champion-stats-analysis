"""trace_id gRPC propagation: client interceptors attach it, server interceptors
extract or mint it. Uses real in-process gRPC servers/clients (this migration's
established pattern -- see `tests/test_runner_service.py` and
`tests/test_cron_watch_service.py`), not mocked grpc internals.
"""

from __future__ import annotations

import asyncio
import threading
from concurrent import futures
from typing import Any

import grpc
import grpc.aio
import pytest

from league_stats.infra.trace_context import (
    AsyncTraceClientInterceptor,
    AsyncTraceServerInterceptor,
    TraceClientInterceptor,
    TraceServerInterceptor,
)
from league_stats.utils import current_trace_id, set_trace_id
from league_stats_rpc.v1 import common_pb2, cron_watch_pb2, cron_watch_pb2_grpc
from league_stats_rpc.v1 import runner_pb2, runner_pb2_grpc
from league_stats_rpc.v1 import peers_pb2, peers_pb2_grpc


@pytest.fixture(autouse=True)
def _reset_trace_id():
    """Every test starts from a clean (unset) trace_id context."""
    set_trace_id("")
    yield
    set_trace_id("")


# --------------------------------------------------------------------- sync


def _start_sync_server(add_servicer, servicer) -> tuple[grpc.Server, int]:
    server = grpc.server(
        futures.ThreadPoolExecutor(max_workers=4),
        interceptors=[TraceServerInterceptor()],
    )
    add_servicer(servicer, server)
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()
    return server, port


def _sync_channel(port: int) -> grpc.Channel:
    return grpc.intercept_channel(
        grpc.insecure_channel(f"127.0.0.1:{port}"), TraceClientInterceptor()
    )


class _RecordingPeersServicer(peers_pb2_grpc.PeersServiceServicer):
    """A real proto servicer (server B) that just records the trace_id it observed."""

    def __init__(self) -> None:
        self.observed_trace_id: str | None = None

    def RequestBaseline(self, request, context):  # noqa: N802 - grpc naming
        self.observed_trace_id = current_trace_id()
        return peers_pb2.RequestBaselineResponse(cached=True, baseline_json="{}")


class _ChainingRunnerServicer(runner_pb2_grpc.RunnerServiceServicer):
    """A real proto servicer (server A) that, upon receiving a call, itself acts as a
    gRPC client (through `TraceClientInterceptor`) calling server B -- proving the
    trace_id minted for A's inbound call genuinely propagates to B's inbound call.
    """

    def __init__(self, downstream_port: int) -> None:
        self.observed_trace_id: str | None = None
        self._downstream_port = downstream_port

    def EnqueueJob(self, request, context):  # noqa: N802 - grpc naming
        self.observed_trace_id = current_trace_id()
        with _sync_channel(self._downstream_port) as channel:
            stub = peers_pb2_grpc.PeersServiceStub(channel)
            stub.RequestBaseline(
                peers_pb2.RequestBaselineRequest(champion="Viktor", lane="MIDDLE")
            )
        return runner_pb2.EnqueueJobResponse(job_id="job-1")


def test_client_interceptor_attaches_current_trace_id_to_outgoing_metadata():
    set_trace_id("client-side-trace")
    servicer = _RecordingPeersServicer()
    server, port = _start_sync_server(
        peers_pb2_grpc.add_PeersServiceServicer_to_server, servicer
    )
    try:
        with _sync_channel(port) as channel:
            stub = peers_pb2_grpc.PeersServiceStub(channel)
            stub.RequestBaseline(peers_pb2.RequestBaselineRequest(champion="Viktor"))
        assert servicer.observed_trace_id == "client-side-trace"
    finally:
        server.stop(None)


def test_server_interceptor_mints_a_trace_id_when_none_is_present():
    servicer = _RecordingPeersServicer()
    server, port = _start_sync_server(
        peers_pb2_grpc.add_PeersServiceServicer_to_server, servicer
    )
    try:
        # A plain (non-intercepted) channel sends no trace-id metadata at all.
        with grpc.insecure_channel(f"127.0.0.1:{port}") as channel:
            stub = peers_pb2_grpc.PeersServiceStub(channel)
            stub.RequestBaseline(peers_pb2.RequestBaselineRequest(champion="Viktor"))
        assert servicer.observed_trace_id
        assert servicer.observed_trace_id != ""
        # uuid4().hex is 32 lowercase hex characters.
        assert len(servicer.observed_trace_id) == 32
        int(servicer.observed_trace_id, 16)
    finally:
        server.stop(None)


def test_server_interceptor_preserves_an_incoming_trace_id_verbatim():
    servicer = _RecordingPeersServicer()
    server, port = _start_sync_server(
        peers_pb2_grpc.add_PeersServiceServicer_to_server, servicer
    )
    try:
        with grpc.insecure_channel(f"127.0.0.1:{port}") as channel:
            stub = peers_pb2_grpc.PeersServiceStub(channel)
            stub.RequestBaseline(
                peers_pb2.RequestBaselineRequest(champion="Viktor"),
                metadata=(("trace-id", "upstream-trace-abc"),),
            )
        assert servicer.observed_trace_id == "upstream-trace-abc"
    finally:
        server.stop(None)


def test_two_real_servers_propagate_a_minted_trace_id_end_to_end():
    """Server A receives no trace_id, mints one; A calls server B, which must
    receive that EXACT trace_id -- genuine cross-server propagation, not just
    "the interceptor code exists."
    """
    servicer_b = _RecordingPeersServicer()
    server_b, port_b = _start_sync_server(
        peers_pb2_grpc.add_PeersServiceServicer_to_server, servicer_b
    )
    servicer_a = _ChainingRunnerServicer(downstream_port=port_b)
    server_a, port_a = _start_sync_server(
        runner_pb2_grpc.add_RunnerServiceServicer_to_server, servicer_a
    )
    try:
        # No trace_id set client-side and a plain channel: this call originates a new trace.
        with grpc.insecure_channel(f"127.0.0.1:{port_a}") as channel:
            stub = runner_pb2_grpc.RunnerServiceStub(channel)
            stub.EnqueueJob(runner_pb2.EnqueueJobRequest(riot_id="Test"))

        assert servicer_a.observed_trace_id
        assert servicer_b.observed_trace_id == servicer_a.observed_trace_id
    finally:
        server_a.stop(None)
        server_b.stop(None)


# -------------------------------------------------------------------- async


class _RecordingAsyncServicer(cron_watch_pb2_grpc.CronWatchServiceServicer):
    def __init__(self) -> None:
        self.observed_trace_id: str | None = None

    async def RegisterAccount(self, request, context):  # noqa: N802 - grpc naming
        self.observed_trace_id = current_trace_id()
        return common_pb2.Ack(ok=True, message="")


class _ChainingAsyncServicer(cron_watch_pb2_grpc.CronWatchServiceServicer):
    """Server A: on RegisterAccount, calls server B's RegisterAccount as a client."""

    def __init__(self, downstream_port: int) -> None:
        self.observed_trace_id: str | None = None
        self._downstream_port = downstream_port

    async def RegisterAccount(self, request, context):  # noqa: N802 - grpc naming
        self.observed_trace_id = current_trace_id()
        async with grpc.aio.insecure_channel(
            f"127.0.0.1:{self._downstream_port}",
            interceptors=[AsyncTraceClientInterceptor()],
        ) as channel:
            stub = cron_watch_pb2_grpc.CronWatchServiceStub(channel)
            await stub.RegisterAccount(cron_watch_pb2.RegisterAccountRequest(puuid="x"))
        return common_pb2.Ack(ok=True, message="")


class _AioServerThread:
    """Hosts a `grpc.aio.server()` with `AsyncTraceServerInterceptor` on its own
    thread + event loop -- mirrors `tests/test_cron_watch_service.py`'s harness.
    """

    def __init__(self, servicer: cron_watch_pb2_grpc.CronWatchServiceServicer) -> None:
        self._servicer = servicer
        self._ready = threading.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._server: Any = None
        self.port = 0
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout=5):
            raise RuntimeError("trace_context test aio server failed to start")

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop

        async def _build() -> None:
            server = grpc.aio.server(interceptors=[AsyncTraceServerInterceptor()])
            cron_watch_pb2_grpc.add_CronWatchServiceServicer_to_server(self._servicer, server)
            self.port = server.add_insecure_port("127.0.0.1:0")
            await server.start()
            self._server = server

        loop.run_until_complete(_build())
        self._ready.set()
        loop.run_forever()

    def stop(self) -> None:
        assert self._loop is not None and self._server is not None
        future = asyncio.run_coroutine_threadsafe(self._server.stop(None), self._loop)
        future.result(timeout=5)
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5)


def test_two_real_async_servers_propagate_a_minted_trace_id_end_to_end():
    servicer_b = _RecordingAsyncServicer()
    server_b = _AioServerThread(servicer_b)
    servicer_a = _ChainingAsyncServicer(downstream_port=server_b.port)
    server_a = _AioServerThread(servicer_a)
    try:
        with grpc.insecure_channel(f"127.0.0.1:{server_a.port}") as channel:
            stub = cron_watch_pb2_grpc.CronWatchServiceStub(channel)
            stub.RegisterAccount(cron_watch_pb2.RegisterAccountRequest(puuid="y"))

        assert servicer_a.observed_trace_id
        assert servicer_b.observed_trace_id == servicer_a.observed_trace_id
    finally:
        server_a.stop()
        server_b.stop()
