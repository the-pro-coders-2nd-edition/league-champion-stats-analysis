"""Standalone entrypoint for the CRON-watch gRPC service.

`CronWatchServicer` is a `grpc.aio` (async) servicer -- see
`cron_watch/service.py`'s module docstring for why (`WatchPoller` is
asyncio-native top to bottom, so its servicer is too). This entrypoint
therefore uses `grpc.aio.server()` / `await server.start()` /
`await server.wait_for_termination()`, NOT the plain synchronous
`grpc.server(futures.ThreadPoolExecutor(...))` pattern RUNNER's entrypoint
(`runner/__main__.py`) uses -- that pattern would not work here since
`CronWatchServicer`'s RPC methods are `async def`.

CRON-watch's design (`cron_watch/service.py`'s "Design note -- enqueue
target") points its own `JobStore` at the exact same `app.sqlite` file the
monolith uses, rather than calling RUNNER's `EnqueueJob`. In docker-compose
this is a volume shared between the `api-ui` and `cron-watch` services (see
`docker-compose.yml`). Per that module's "Handoff note for Task 5": this
entrypoint must NOT call `store.recover_orphans()` on startup the way
`web/app.py`'s `lifespan` does -- against a shared database, that would mark
the monolith's genuinely in-flight jobs as failed. Only the monolith calls
`recover_orphans()`.
"""

from __future__ import annotations

import asyncio
import os

import grpc
from prometheus_client import start_http_server

from league_stats_common.core.config import load_config, load_web_config
from league_stats_common.infra.cache import HttpCache, MatchStore
from league_stats_common.infra.jobs import JobStore
from league_stats_common.infra.riot_api import RiotApiClient, shared_rate_limiter
from league_stats_common.infra.trace_context import AsyncTraceServerInterceptor
from league_stats_common.utils import get_logger, setup_logging
from league_stats_cron_watch.service import CronWatchServicer
from league_stats_rpc.v1 import cron_watch_pb2_grpc

log = get_logger("cron_watch")


def _require_riot_api_key() -> str:
    """Fail loudly if `CRON_WATCH_RIOT_API_KEY` is unset.

    `_build_client` (below) is only invoked lazily inside
    `WatchPoller._check_group`, via the `client_factory` callable, whose
    broad `except Exception` (see `web/watch.py`) catches a missing-key
    `RuntimeError` and just logs it as a per-tick warning through
    `_note_failure`. That means a misconfigured deployment would start
    successfully, report healthy, and silently never detect a single new
    game -- forever. Calling this from `serve()` *before* `server.start()`
    turns that into an immediate, visible failure instead.
    """
    api_key = os.environ.get("CRON_WATCH_RIOT_API_KEY")
    if not api_key:
        raise RuntimeError(
            "Missing Riot API key: set CRON_WATCH_RIOT_API_KEY in the environment."
        )
    return api_key


def _build_client(region: str) -> RiotApiClient:
    """Build a Riot client for `WatchPoller`'s detection calls.

    Uses `CRON_WATCH_RIOT_API_KEY` -- this service's own key, per the spec's
    one-key-per-service-type design -- not the monolith's `RIOT_API_KEY`.
    Validity is already checked fail-fast in `serve()` via
    `_require_riot_api_key`; this call re-fetches it per region since
    `load_config` needs it, not because it might be newly missing here.
    """
    api_key = _require_riot_api_key()
    config = load_config(
        riot_id="cron-watch",
        tagline="CRW",
        region=region,
        api_key=api_key,
    )
    config.ensure_directories()
    return RiotApiClient(
        config,
        HttpCache(config.http_cache_dir),
        MatchStore(config.db_path),
        limiter=shared_rate_limiter(
            config.requests_per_second, config.requests_per_two_minutes
        ),
    )


async def serve() -> None:
    setup_logging(service="cron-watch", version=os.environ.get("GIT_COMMIT", "dev"))
    port = os.environ.get("CRON_WATCH_GRPC_PORT", "50052")
    metrics_port = int(os.environ.get("CRON_WATCH_METRICS_PORT", "9101"))
    # `load_web_config()` is what merges `.env` into `os.environ` (via
    # `core/config.py`'s `_load_env_file`), which is the documented way to
    # supply `CRON_WATCH_RIOT_API_KEY` for a local `python -m
    # league_stats.cron_watch` run (per `.env.example`). The fail-fast check
    # below must run AFTER this, or it would reject a validly configured
    # local run that only sets the key in `.env` rather than the shell
    # environment -- while still running well before `JobStore` construction,
    # `add_insecure_port`, and `server.start()`, so it's still "fail fast"
    # relative to the service actually coming up.
    web_config = load_web_config()
    _require_riot_api_key()
    # Shared file with the monolith's `api-ui` service -- a docker-compose volume
    # mount, not a per-service path. See this module's docstring.
    store = JobStore(web_config.app_db_path)
    servicer = CronWatchServicer(store, _build_client)

    server = grpc.aio.server(interceptors=[AsyncTraceServerInterceptor()])
    cron_watch_pb2_grpc.add_CronWatchServiceServicer_to_server(servicer, server)
    server.add_insecure_port(f"0.0.0.0:{port}")

    await server.start()
    # Minimal Prometheus /metrics HTTP surface -- same pattern as RUNNER
    # (cron_watch_tick_duration_seconds, cron_watch_new_games_detected_total,
    # see cron_watch/service.py). `start_http_server` is synchronous and spins
    # up its own background thread, so calling it here from inside `serve`'s
    # coroutine works the same as it would from a plain sync entrypoint.
    start_http_server(metrics_port)
    # Must run after `server.start()`: `WatchPoller.start()` calls
    # `asyncio.create_task(...)`, which requires a running event loop.
    await servicer.start()
    log.info(
        "CRON-watch gRPC service listening on :%s (metrics on :%s/metrics)", port, metrics_port
    )
    try:
        await server.wait_for_termination()
    finally:
        await servicer.stop()
        store.close()


def main() -> None:
    asyncio.run(serve())


if __name__ == "__main__":
    main()
