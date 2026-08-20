"""CronWatch's gRPC service: wraps `WatchPoller` behind `CronWatchService`.

Design note -- sync `grpc.server` vs `grpc.aio`:

Phase 0/1's established pattern is a plain synchronous `grpc.server(...)`
(see `league_stats.runner.service.RunnerServicer`), because `execute_job` is
100% synchronous -- there is no event loop anywhere near it, so bridging one
in would be pure overhead.

`WatchPoller` is the opposite: it is asyncio-native top to bottom
(`asyncio.Event`, `asyncio.create_task`, `await asyncio.to_thread(...)` for
every blocking Riot API call, an `async def tick()`/`_check_group()`). Two
shapes were considered for bridging it into a synchronous servicer:

1. Run `WatchPoller` on a dedicated background thread with its own event
   loop (`asyncio.new_event_loop()` + `loop.run_forever()`), and have each
   synchronous servicer method call `asyncio.run_coroutine_threadsafe(...)`
   and block on the resulting `concurrent.futures.Future`. This works, but
   for `WatchUpdates` (a *streaming* RPC) it would also need a second
   hand-off in the other direction: the event-loop thread (where
   `_enqueue_refresh` fires the new-game hook) would have to push into a
   thread-safe `queue.SimpleQueue` that the gRPC threadpool worker thread
   polls synchronously to `yield` -- i.e. two bridges, one per direction,
   plus a whole extra thread + loop to start and shut down cleanly.

2. Make `CronWatchServicer` itself an async servicer under `grpc.aio.server`.
   Since `WatchPoller`'s own methods are already `async def`, the servicer's
   `RegisterAccount`/`ForceRefresh` can just `await` straight into it, and
   `WatchUpdates` is a natural `async for` over an `asyncio.Queue` fed
   in-loop by the same callback -- no thread, no `run_coroutine_threadsafe`,
   no second queue. `cron_watch_pb2_grpc.py` (protoc's Python output) is not
   sync- or aio-specific -- the same generated `CronWatchServiceServicer`
   base class works with either `grpc.server()` (sync methods) or
   `grpc.aio.server()` (`async def` methods); the choice is made by which
   server class hosts it, not by regenerating anything.

Option 2 was chosen: it is less code, has fewer moving parts (no second
thread/loop to manage and shut down), and does not fight WatchPoller's grain.
The cost is that this service's *process* needs an asyncio entrypoint
(`grpc.aio.server()` + `await server.start()`) rather than
`grpc.server(futures.ThreadPoolExecutor(...))` -- a detail for whichever task
wires up CronWatch's `__main__`, not something that leaks into callers of
this class. A plain synchronous `grpc.insecure_channel` client (as used by
this module's tests, and by any other service) talks to a `grpc.aio` server
exactly the same way over the wire; only the server side is async.

Design note -- `RegisterAccountRequest`/`ForceRefreshRequest` only carry a
`puuid` (+ `region` for registration), but `JobStore`/`WatchPoller`'s actual
data model is keyed by a `slug` derived from riot_id/tagline, with no
puuid index at all (confirmed: nothing in `jobs.py` or `watch.py` looks
anything up by puuid). Resolving that mismatch properly (e.g. a puuid index
on `JobStore`, or extending the proto with riot_id/tagline) is out of this
task's scope -- it wraps the existing components, it does not redesign them.
The pragmatic mapping used here: the puuid itself is used as the `slug` (and
as a placeholder `riot_id`, with an empty `tagline`). This keeps the service
functional end to end (including a real detection cycle against a fake
`MatchIdSource` in tests), but means a real `WatchPoller` check against the
live Riot API would call `resolve_puuid(puuid, _PLACEHOLDER_TAGLINE)` instead
of a real Riot ID -- that call would fail against the real API. (The
placeholder tagline is not empty on purpose: `JobStore.decode_players` drops
any tracked player whose `tagline` is empty, so an empty string would make
`WatchPoller` see zero players for the account and never check it at all.)
Whoever wires this service up
against production Riot credentials needs to close that gap first (most
likely: extending `RegisterAccountRequest` with riot_id/tagline, since a
puuid does not resolve back to one through any API this codebase calls).

Similarly, `RegisterAccountRequest.region` is `league_stats_rpc.v1.Region`,
which is continent-grained (EUROPE/AMERICAS/ASIA/SEA), while `JobStore` rows
store a platform routing value (e.g. "euw1") -- the same granularity gap
`league_stats.core.config.AppConfig` already has a documented fallback for
(`REGION_DEFAULT_PLATFORM`). That existing fallback is reused here rather
than inventing a new one; it is a lossy, best-effort choice (e.g. all of
EUROPE maps to "euw1", never "eune1"/"tr1"/"ru"), not a fix.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, AsyncIterator, Callable

import grpc

from league_stats.core.config import REGION_DEFAULT_PLATFORM
from league_stats.utils import get_logger
from league_stats.web.jobs import JobStore
from league_stats.web.watch import MatchIdSource, WatchPoller
from league_stats_rpc.v1 import common_pb2, cron_watch_pb2, cron_watch_pb2_grpc

log = get_logger("cron_watch_service")

# Non-empty on purpose -- see the module docstring: JobStore.decode_players
# drops any tracked player with an empty tagline.
_PLACEHOLDER_TAGLINE = "UNKNOWN"

_REGION_TO_CONTINENT: dict[int, str] = {
    common_pb2.EUROPE: "europe",
    common_pb2.AMERICAS: "americas",
    common_pb2.ASIA: "asia",
    common_pb2.SEA: "sea",
}


def _platform_for(region: int) -> str:
    """Best-effort platform routing value for a continent-grained `Region`. See
    this module's docstring for why this is lossy and not a real fix."""
    continent = _REGION_TO_CONTINENT.get(region, "europe")
    return REGION_DEFAULT_PLATFORM.get(continent, "euw1")


class CronWatchServicer(cron_watch_pb2_grpc.CronWatchServiceServicer):
    """Implements `CronWatchService` on top of `WatchPoller` + `JobStore`.

    An `async` servicer, meant to be served by `grpc.aio.server()` -- see the
    module docstring for why this diverges from Phase 0/1's synchronous
    pattern.
    """

    def __init__(
        self,
        store: JobStore,
        client_factory: Callable[[str], MatchIdSource],
    ) -> None:
        self._store = store
        self._poller = WatchPoller(store, client_factory, on_new_game=self._on_new_game)
        # `None` is the wildcard key: a `WatchUpdates` call with no puuid filter
        # subscribes to every account's updates.
        self._subscribers: dict[str | None, list["asyncio.Queue[cron_watch_pb2.WelcomeBackUpdate]"]] = {}

    # ------------------------------------------------------------ lifecycle
    # For whoever wires up CronWatch's real entrypoint: these must be called
    # from inside the same running event loop `grpc.aio.server()` uses (e.g.
    # right after `await server.start()`), since `WatchPoller.start()` calls
    # `asyncio.create_task(...)`, which requires a running loop.

    async def start(self) -> None:
        """Start WatchPoller's background polling loop."""
        self._poller.start()

    async def stop(self) -> None:
        """Stop the background polling loop and wait for it to unwind."""
        await self._poller.stop()

    # -------------------------------------------------------------- helpers

    def _watched_row(self, slug: str) -> dict | None:
        for row in self._store.list_watched_players():
            if str(row.get("slug", "")) == slug:
                return row
        return None

    def _on_new_game(
        self, slug: str, job_id: str, new_match_id: str, summary: dict[str, Any]
    ) -> None:
        """`WatchPoller`'s new-game observer hook: fan out to `WatchUpdates` subscribers.

        `new_match_id` is the real match id `WatchPoller._check_group` detected
        (threaded through its `on_new_game` hook). `summary` is the lightweight
        welcome-back payload (win/loss, K/D/A, CS/min, damage share) `WatchPoller`
        already computed from that match alone -- an empty dict if the extra
        `fetch_match` call was budget-skipped or failed, in which case
        `match_summary_json` ships as `"{}"` rather than blocking the update.
        """
        update = cron_watch_pb2.WelcomeBackUpdate(
            puuid=slug,
            new_match_id=new_match_id,
            match_summary_json=json.dumps(summary),
            detected_at_unix=int(time.time()),
        )
        for key in (slug, None):
            for subscriber in self._subscribers.get(key, []):
                subscriber.put_nowait(update)

    # ------------------------------------------------------------------ RPC

    async def RegisterAccount(self, request, context):
        if not request.puuid:
            context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
            context.set_details("puuid is required")
            return common_pb2.Ack()

        slug = request.puuid
        platform = _platform_for(request.region)
        self._store.upsert_player(
            slug=slug, riot_id=request.puuid, tagline=_PLACEHOLDER_TAGLINE, region=platform
        )
        self._store.set_watch(slug, enabled=True)
        return common_pb2.Ack(ok=True, message=f"watching {slug}")

    async def ForceRefresh(self, request, context):
        if not request.puuid:
            context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
            context.set_details("puuid is required")
            return common_pb2.Ack()

        slug = request.puuid
        row = self._watched_row(slug)
        if row is None:
            context.set_code(grpc.StatusCode.NOT_FOUND)
            context.set_details(f"no watched account for puuid {slug!r}")
            return common_pb2.Ack()

        # `_check_group` (not `tick()`) deliberately: `tick()` would sweep every
        # due account, but "force" means checking this one account right now,
        # bypassing its due-interval gating too.
        found = await self._poller._check_group(row, slug)  # noqa: SLF001
        return common_pb2.Ack(ok=True, message="new game found" if found else "no new game")

    async def WatchUpdates(
        self, request, context
    ) -> AsyncIterator[cron_watch_pb2.WelcomeBackUpdate]:
        key = request.puuid or None
        subscriber: "asyncio.Queue[cron_watch_pb2.WelcomeBackUpdate]" = asyncio.Queue()
        self._subscribers.setdefault(key, []).append(subscriber)
        try:
            while True:
                yield await subscriber.get()
        finally:
            self._subscribers[key].remove(subscriber)
