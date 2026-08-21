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
import mongomock
import pytest
from fastapi.testclient import TestClient

from league_stats_common.core.config import RANKED_SOLO_QUEUE_ID, WebConfig
from league_stats_cron_watch.service import CronWatchServicer
from league_stats_api_ui.app import create_app
from league_stats_common.infra.jobs import JOB_KIND_REFRESH, JobStore, open_jobs_store
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
    handle = JobStore(mongomock.MongoClient())
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


def test_require_riot_api_key_raises_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """Extracted validation from `cron_watch/__main__.py`'s `serve()` must
    fail loudly when `CRON_WATCH_RIOT_API_KEY` is unset."""
    from league_stats_cron_watch.__main__ import _require_riot_api_key

    monkeypatch.delenv("CRON_WATCH_RIOT_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="CRON_WATCH_RIOT_API_KEY"):
        _require_riot_api_key()


def test_serve_fails_fast_when_the_riot_api_key_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`serve()` must raise before ever calling `server.start()` when the key
    is missing, instead of starting successfully and silently never detecting
    a new game (the finding's exact failure mode).

    `_load_env_file()` (called by `serve()` via `load_web_config()`) is
    monkeypatched to a no-op, not just `delenv`'d around: it merges a `.env`
    into `os.environ` with `override=False`, so it only fills in what
    `delenv` just cleared. Its search list includes
    `PACKAGE_ROOT.parent.parent / ".env"` -- this repo's own project-root
    `.env`, resolved from the installed package's location, independent of
    the test's cwd -- so a plain `monkeypatch.chdir` cannot hide it. If that
    real `.env` happens to define `CRON_WATCH_RIOT_API_KEY` (e.g. for
    local/VPS deployment, as this repo's does), this test's "key is missing"
    precondition becomes false, `_require_riot_api_key()` no longer raises,
    and `serve()` runs all the way to a real
    `await server.wait_for_termination()` -- hanging this test (and the
    whole suite) forever instead of failing fast."""
    import league_stats_common.core.config as config_module
    from league_stats_cron_watch.__main__ import serve

    monkeypatch.delenv("CRON_WATCH_RIOT_API_KEY", raising=False)
    monkeypatch.setattr(config_module, "_load_env_file", lambda: None)
    with pytest.raises(RuntimeError, match="CRON_WATCH_RIOT_API_KEY"):
        asyncio.run(serve())


def test_serve_accepts_a_riot_api_key_supplied_only_via_dotenv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Round-2 regression test: `_require_riot_api_key()` must run AFTER
    `load_web_config()` (the only thing in `serve()` that merges `.env` into
    `os.environ`, via `core/config.py`'s `_load_env_file`), not before. A
    prior version of this fix checked the key first and wrongly rejected a
    validly configured local `python -m league_stats.cron_watch` run that
    only sets `CRON_WATCH_RIOT_API_KEY` in `.env` (the documented
    `.env.example` path), not the shell environment.

    Drives the real `serve()` with the key present ONLY in a temp-dir `.env`
    file and nothing in `os.environ`. Rather than letting `serve()` actually
    bind a gRPC server / metrics HTTP server / run `wait_for_termination()`
    forever (heavy, and one more thing that could hang a test), `open_jobs_store`
    (Phase 8, Task 4 -- previously `JobStore` constructed directly) -- the
    very next thing `serve()` calls after the key check -- is monkeypatched
    to raise a distinctive sentinel exception. Reaching that sentinel (rather
    than `_require_riot_api_key`'s `RuntimeError` about the missing key)
    proves `serve()` got past both `load_web_config()` and the key check in
    the right order.
    """
    import league_stats_cron_watch.__main__ as main_module

    monkeypatch.delenv("CRON_WATCH_RIOT_API_KEY", raising=False)
    (tmp_path / ".env").write_text(
        "CRON_WATCH_RIOT_API_KEY=dotenv-only-key\n", encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)

    sentinel = RuntimeError("stopped deliberately before starting the real server")

    def _fake_open_jobs_store() -> Any:
        raise sentinel

    monkeypatch.setattr(main_module, "open_jobs_store", _fake_open_jobs_store)

    with pytest.raises(RuntimeError) as exc_info:
        asyncio.run(main_module.serve())
    assert exc_info.value is sentinel


def test_check_group_for_the_same_slug_never_runs_concurrently(store: JobStore) -> None:
    """Proves `CronWatchServicer._instrument_check_group`'s per-slug lock
    actually serializes the two call sites the finding names: `ForceRefresh`
    calling `WatchPoller._check_group` directly, and the background `_loop`'s
    own `tick()` -> `_check_group` call, for the SAME slug at the same time.

    Constructing a `FakeClient` that deterministically reproduces the exact
    `watch_seen`-dropping outcome is impractical here: `_check_group` always
    re-derives `watch_seen` from a *live* Riot read for every queue it checks
    in a given call, so whichever call commits last still writes a
    self-consistent result in most orderings. What the lock is actually for
    -- and what genuinely matters for the finding's "last write wins" concern
    -- is that the two calls' bodies (read row -> await Riot calls -> write
    row) never *interleave*; this test proves that directly instead, per the
    finding's own fallback: "a simpler test that directly demonstrates the
    lock is acquired/released around both call sites".

    `fetch_match_ids` is invoked through `await asyncio.to_thread(...)`, i.e.
    on a real thread-pool thread, so a `threading.Lock` (not an `asyncio`
    primitive) guards the shared concurrency counter honestly.
    """
    counter_lock = threading.Lock()
    state = {"active": 0, "max_active": 0}

    class BlockingClient:
        def resolve_puuid(self, riot_id: str, tagline: str) -> str:
            return "puuid-hugros"

        def fetch_match_ids(
            self, puuid: str, count: int, *, queue_id: int, use_cache: bool = True
        ) -> list[str]:
            with counter_lock:
                state["active"] += 1
                state["max_active"] = max(state["max_active"], state["active"])
            time.sleep(0.05)
            with counter_lock:
                state["active"] -= 1
            return []

        def fetch_match(self, match_id: str) -> dict[str, Any]:
            return {}

    client = BlockingClient()
    servicer = CronWatchServicer(store, lambda region: client)
    store.upsert_player(slug="hugros", riot_id="Hugros", tagline="EUW", region="euw1")
    store.set_watch("hugros", enabled=True)

    async def _race() -> None:
        row_a = servicer._watched_row("hugros")  # noqa: SLF001
        row_b = servicer._watched_row("hugros")  # noqa: SLF001
        assert row_a is not None and row_b is not None
        await asyncio.gather(
            servicer._poller._check_group(row_a, "hugros"),  # noqa: SLF001 -- ForceRefresh path
            servicer._poller._check_group(row_b, "hugros"),  # noqa: SLF001 -- background tick path
        )

    asyncio.run(_race())

    assert state["max_active"] == 1, "the two calls must not run inside _check_group at once"


def test_a_cron_watch_enqueued_job_surfaces_through_the_monolith_job_api(
    tmp_path: Path,
) -> None:
    """Task 4's decision, proven end to end: CRON-watch and the monolith open
    *separate* `JobStore` connections onto the *same* Mongo database (Phase 8,
    Task 4 -- previously the same on-disk file via a docker-compose
    mounted volume, now the same `RUNNER_MONGO_URI`/`MONGO_URI`), and a job
    `CronWatchServicer` enqueues on its connection is immediately visible,
    with a real queue position and ETA, through the monolith's own
    `/api/players/{slug}` and `/api/jobs/{id}` endpoints -- exactly the
    surface `_job_public` in `web/app.py` serves to the frontend today. This
    is what option (a), routing through RUNNER's `EnqueueJob` instead, would
    NOT give for free: RUNNER assigns its own in-memory job ids
    (`itertools.count` local to one `RunnerServicer` instance) and never
    writes a row into `JobStore` at all.

    Both sides call `open_jobs_store()` (not a fresh `JobStore(...)`), so
    this test's "two processes" share the same conftest-patched mongomock
    client, the same way `test_watch.py::test_career_banner_ack_route`
    shares one via `open_career_store()` for Task 3's equivalent store.
    """
    # The monolith side: a real FastAPI app, worker disabled (no network),
    # backed by its own JobStore connection onto the shared Mongo database.
    web_config = WebConfig(output_dir=tmp_path / "out", assets_dir=tmp_path / "assets")
    app = create_app(web_config, start_worker=False)

    # The CRON-watch side: a second, independent JobStore connection onto the
    # SAME database -- modeling two separate processes sharing Mongo, not two
    # handles inside one process.
    cron_store = open_jobs_store()
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
