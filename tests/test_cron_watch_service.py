"""CronWatch's gRPC service: wraps `WatchPoller` behind a real in-process
gRPC server/client, following the pattern in `tests/test_rpc_contracts.py`
and `tests/test_runner_service.py`.

`CronWatchServicer` is an `async` servicer served by `grpc.aio.server()` (see
`service.py`'s module docstring for why), so the harness below runs the aio
server on a background thread with its own event loop -- the client side
stays a plain synchronous `grpc.insecure_channel`, since a sync channel talks
to a `grpc.aio` server exactly like it would any other gRPC server.
"""

from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path
from typing import Any

import grpc
import pytest
from fastapi.testclient import TestClient

from league_stats.core.config import RANKED_SOLO_QUEUE_ID, WebConfig
from league_stats.cron_watch.service import CronWatchServicer
from league_stats.web.app import create_app
from league_stats.web.jobs import JOB_KIND_REFRESH, JobStore
from league_stats_rpc.v1 import common_pb2, cron_watch_pb2, cron_watch_pb2_grpc
from tests.test_watch import FakeClient


class _AioServerThread:
    """Hosts a `grpc.aio.server()` on its own thread + event loop.

    Keeps its own loop reference (rather than `asyncio.run(...)`, which hides
    it) so `.stop()` can schedule `server.stop(...)` back onto that loop from
    the test thread via `run_coroutine_threadsafe`.
    """

    def __init__(self, servicer: CronWatchServicer) -> None:
        self._servicer = servicer
        self._ready = threading.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._server: Any = None
        self.port = 0
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout=5):
            raise RuntimeError("CronWatch test server failed to start")

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop

        async def _build() -> None:
            server = grpc.aio.server()
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


@pytest.fixture()
def store(tmp_path: Path):
    handle = JobStore(tmp_path / "app.sqlite")
    yield handle
    handle.close()


def _start(servicer: CronWatchServicer) -> _AioServerThread:
    return _AioServerThread(servicer)


def test_register_account_watches_a_new_puuid(store: JobStore) -> None:
    client = FakeClient()
    servicer = CronWatchServicer(store, lambda region: client)
    server = _start(servicer)
    try:
        with grpc.insecure_channel(f"127.0.0.1:{server.port}") as channel:
            stub = cron_watch_pb2_grpc.CronWatchServiceStub(channel)
            response = stub.RegisterAccount(
                cron_watch_pb2.RegisterAccountRequest(
                    puuid="hugros", region=common_pb2.EUROPE
                )
            )
        assert response.ok is True
        row = store.get_player("hugros")
        assert row is not None
        assert row["watch_enabled"] == 1
    finally:
        server.stop()


def test_register_account_rejects_empty_puuid(store: JobStore) -> None:
    servicer = CronWatchServicer(store, lambda region: FakeClient())
    server = _start(servicer)
    try:
        with grpc.insecure_channel(f"127.0.0.1:{server.port}") as channel:
            stub = cron_watch_pb2_grpc.CronWatchServiceStub(channel)
            with pytest.raises(grpc.RpcError) as exc_info:
                stub.RegisterAccount(cron_watch_pb2.RegisterAccountRequest())
            assert exc_info.value.code() == grpc.StatusCode.INVALID_ARGUMENT
    finally:
        server.stop()


def test_force_refresh_enqueues_a_job_when_a_new_match_id_appears(store: JobStore) -> None:
    """A tracked account with a genuinely new match id gets a refresh job
    enqueued through `ForceRefresh`, matching `test_watch.py`'s
    `test_a_new_match_id_enqueues_a_refresh` shape but driven over gRPC."""
    client = FakeClient({"puuid-hugros": {RANKED_SOLO_QUEUE_ID: ["EUW1_1"]}})
    servicer = CronWatchServicer(store, lambda region: client)
    server = _start(servicer)
    try:
        with grpc.insecure_channel(f"127.0.0.1:{server.port}") as channel:
            stub = cron_watch_pb2_grpc.CronWatchServiceStub(channel)
            stub.RegisterAccount(
                cron_watch_pb2.RegisterAccountRequest(
                    puuid="hugros", region=common_pb2.EUROPE
                )
            )

            baseline = stub.ForceRefresh(
                cron_watch_pb2.ForceRefreshRequest(puuid="hugros")
            )
            assert baseline.ok is True
            assert store.list_active_jobs() == []

            client.newest["puuid-hugros"] = {RANKED_SOLO_QUEUE_ID: ["EUW1_2"]}
            refreshed = stub.ForceRefresh(
                cron_watch_pb2.ForceRefreshRequest(puuid="hugros")
            )
        assert refreshed.ok is True
        jobs = store.list_active_jobs()
        assert len(jobs) == 1
        assert jobs[0]["kind"] == JOB_KIND_REFRESH
        assert jobs[0]["player_slug"] == "hugros"
    finally:
        server.stop()


def test_force_refresh_404s_on_an_unregistered_puuid(store: JobStore) -> None:
    servicer = CronWatchServicer(store, lambda region: FakeClient())
    server = _start(servicer)
    try:
        with grpc.insecure_channel(f"127.0.0.1:{server.port}") as channel:
            stub = cron_watch_pb2_grpc.CronWatchServiceStub(channel)
            with pytest.raises(grpc.RpcError) as exc_info:
                stub.ForceRefresh(cron_watch_pb2.ForceRefreshRequest(puuid="nobody"))
            assert exc_info.value.code() == grpc.StatusCode.NOT_FOUND
    finally:
        server.stop()


def test_watch_updates_streams_a_notification_when_force_refresh_finds_a_new_game(
    store: JobStore,
) -> None:
    """The strongest end-to-end proof: a fake `MatchIdSource` reports a new
    match id, `ForceRefresh` enqueues a job, and `WatchUpdates` -- already
    subscribed -- pushes a `WelcomeBackUpdate` for it without polling."""
    client = FakeClient({"puuid-hugros": {RANKED_SOLO_QUEUE_ID: ["EUW1_1"]}})
    servicer = CronWatchServicer(store, lambda region: client)
    server = _start(servicer)
    try:
        with grpc.insecure_channel(f"127.0.0.1:{server.port}") as channel:
            stub = cron_watch_pb2_grpc.CronWatchServiceStub(channel)
            stub.RegisterAccount(
                cron_watch_pb2.RegisterAccountRequest(
                    puuid="hugros", region=common_pb2.EUROPE
                )
            )
            stub.ForceRefresh(cron_watch_pb2.ForceRefreshRequest(puuid="hugros"))  # baseline

            updates = stub.WatchUpdates(
                cron_watch_pb2.WatchUpdatesRequest(puuid="hugros")
            )

            # The streaming call above starts iterating lazily; give the
            # subscription a moment to land before the next ForceRefresh call
            # fires the hook synchronously inside the server's event loop.
            time.sleep(0.1)
            client.newest["puuid-hugros"] = {RANKED_SOLO_QUEUE_ID: ["EUW1_2"]}
            stub.ForceRefresh(cron_watch_pb2.ForceRefreshRequest(puuid="hugros"))

            update = next(updates)
        assert update.puuid == "hugros"
        assert update.new_match_id == "EUW1_2"
        assert update.detected_at_unix > 0
    finally:
        server.stop()


def test_a_cron_watch_enqueued_job_surfaces_through_the_monolith_job_api(
    tmp_path: Path,
) -> None:
    """Task 4's decision, proven end to end: CRON-watch and the monolith open
    *separate* `JobStore` connections onto the *same* `app.sqlite` file (a
    stand-in for the docker-compose mounted volume Task 5 will wire up), and a
    job `CronWatchServicer` enqueues on its connection is immediately visible,
    with a real queue position and ETA, through the monolith's own
    `/api/players/{slug}` and `/api/jobs/{id}` endpoints -- exactly the
    surface `_job_public` in `web/app.py` serves to the frontend today. This
    is what option (a), routing through RUNNER's `EnqueueJob` instead, would
    NOT give for free: RUNNER assigns its own in-memory job ids
    (`itertools.count` local to one `RunnerServicer` instance) and never
    writes a row into `JobStore` at all.
    """
    db_path = tmp_path / "app.sqlite"

    # The monolith side: a real FastAPI app, worker disabled (no network),
    # backed by its own JobStore connection onto db_path.
    web_config = WebConfig(output_dir=tmp_path / "out", app_db_path=db_path)
    app = create_app(web_config, start_worker=False)

    # The CRON-watch side: a second, independent JobStore connection onto the
    # SAME file -- modeling two separate processes sharing a mounted volume,
    # not two handles inside one process.
    cron_store = JobStore(db_path)
    try:
        client = FakeClient({"puuid-hugros": {RANKED_SOLO_QUEUE_ID: ["EUW1_1"]}})
        servicer = CronWatchServicer(cron_store, lambda region: client)
        server = _start(servicer)
        try:
            with grpc.insecure_channel(f"127.0.0.1:{server.port}") as channel:
                stub = cron_watch_pb2_grpc.CronWatchServiceStub(channel)
                stub.RegisterAccount(
                    cron_watch_pb2.RegisterAccountRequest(
                        puuid="hugros", region=common_pb2.EUROPE
                    )
                )
                stub.ForceRefresh(cron_watch_pb2.ForceRefreshRequest(puuid="hugros"))  # baseline

                client.newest["puuid-hugros"] = {RANKED_SOLO_QUEUE_ID: ["EUW1_2"]}
                response = stub.ForceRefresh(cron_watch_pb2.ForceRefreshRequest(puuid="hugros"))
                assert response.ok is True
        finally:
            server.stop()

        with TestClient(app) as web_client:
            player_status = web_client.get("/api/players/hugros")
            assert player_status.status_code == 200
            active_job = player_status.json()["active_job"]
            assert active_job is not None
            assert active_job["kind"] == JOB_KIND_REFRESH
            assert active_job["player_slug"] == "hugros"
            assert active_job["queue_position"] == 0
            assert active_job["eta_s"] is not None

            job_status = web_client.get(f"/api/jobs/{active_job['id']}")
            assert job_status.status_code == 200
            assert job_status.json()["job"]["id"] == active_job["id"]
    finally:
        cron_store.close()
