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
this is a volume shared between the `app` and `cron-watch` services (see
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

from league_stats.core.config import load_config, load_web_config
from league_stats.cron_watch.service import CronWatchServicer
from league_stats.infra.cache import HttpCache, MatchStore
from league_stats.infra.riot_api import RiotApiClient, shared_rate_limiter
from league_stats.utils import get_logger
from league_stats.web.jobs import JobStore
from league_stats_rpc.v1 import cron_watch_pb2_grpc

log = get_logger("cron_watch")


def _build_client(region: str) -> RiotApiClient:
    """Build a Riot client for `WatchPoller`'s detection calls.

    Uses `CRON_WATCH_RIOT_API_KEY` -- this service's own key, per the spec's
    one-key-per-service-type design -- not the monolith's `RIOT_API_KEY`.
    """
    api_key = os.environ.get("CRON_WATCH_RIOT_API_KEY")
    if not api_key:
        raise RuntimeError(
            "Missing Riot API key: set CRON_WATCH_RIOT_API_KEY in the environment."
        )
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
    port = os.environ.get("CRON_WATCH_GRPC_PORT", "50052")
    web_config = load_web_config()
    # Shared file with the monolith's `app` service -- a docker-compose volume
    # mount, not a per-service path. See this module's docstring.
    store = JobStore(web_config.app_db_path)
    servicer = CronWatchServicer(store, _build_client)

    server = grpc.aio.server()
    cron_watch_pb2_grpc.add_CronWatchServiceServicer_to_server(servicer, server)
    server.add_insecure_port(f"0.0.0.0:{port}")

    await server.start()
    # Must run after `server.start()`: `WatchPoller.start()` calls
    # `asyncio.create_task(...)`, which requires a running event loop.
    await servicer.start()
    log.info("CRON-watch gRPC service listening on :%s", port)
    try:
        await server.wait_for_termination()
    finally:
        await servicer.stop()
        store.close()


def main() -> None:
    asyncio.run(serve())


if __name__ == "__main__":
    main()
