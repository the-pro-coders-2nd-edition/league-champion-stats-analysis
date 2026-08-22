"""Caches CronWatch's `WatchUpdates` push notifications for the polled REST API.

Design note -- per-puuid vs. broad subscription (Phase 4 Task 1's flagged question):

The task brief explicitly asked whether API-UI needs one `WatchUpdates` gRPC
stream per watched player, or can subscribe to every account at once.
Reading `CronWatchServicer` (`cron_watch/service.py`) answers this directly:
its `__init__` keeps `self._subscribers: dict[str | None, list[asyncio.Queue]]`
with a comment that `None` is "the wildcard key: a WatchUpdates call with no
puuid filter subscribes to every account's updates," and `WatchUpdates` itself
computes `key = request.puuid or None` before registering the caller's queue
under that key. `_on_new_game` (the hook that fans a new detection out to
subscribers) always pushes to both the exact-`slug` list AND the `None` list.
So a single `WatchUpdatesRequest()` with an empty `puuid` subscribes to every
account CronWatch is watching -- present and future -- over one long-lived
stream. API-UI does not need to open, track, or tear down one stream per
watched player as `store.list_watched_players()` changes; a single wildcard
subscription opened once at startup is sufficient and simpler. This also
means the `/api/players/{slug}/watch` POST endpoint (`web/app.py`) does not
need a new hook to open a per-player stream when a player starts being
watched -- the always-on wildcard stream already covers every future watch.

Design note -- `puuid` is really `slug` here: `CronWatchServicer._on_new_game`
builds `WelcomeBackUpdate(puuid=slug, ...)` (see that module's own docstring
on the puuid/slug mapping it uses as a placeholder), so `update.puuid` on the
wire is already the same `slug` the REST API and `JobStore` key everything by.
No extra puuid->slug resolution is needed on this side.
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any

from league_stats_common.utils import get_logger

if TYPE_CHECKING:
    from league_stats_api_ui.job_events import JobEventBus

log = get_logger("welcome_back_cache")

# Initial delay before reopening a `WatchUpdates` stream that ended or errored
# (CronWatch restarting, a network blip). Mirrors `web/watch.py`'s
# WatchPoller._loop shape: retry indefinitely rather than giving up, since this
# feature is best-effort. Backs off exponentially up to RECONNECT_MAX_DELAY_S
# (doubling each consecutive failure) and resets back to this value on a
# successful (re)connection, so a CronWatch outage produces a handful of
# warning lines rather than one every 5s (~17k/day) for as long as it's down.
RECONNECT_DELAY_S = 5.0
RECONNECT_MAX_DELAY_S = 60.0


class WelcomeBackCache:
    """Latest pending welcome-back payload per slug, consumed on read.

    A plain dict-backed cache -- no gRPC involved here at all, which is why
    this class is tested in isolation from the subscription plumbing below.
    """

    def __init__(self) -> None:
        self._data: dict[str, dict[str, Any]] = {}

    def record(self, slug: str, update: dict[str, Any]) -> None:
        """Store the latest welcome-back payload for `slug`, replacing any prior one."""
        self._data[slug] = update

    def get(self, slug: str) -> dict[str, Any] | None:
        """Return and clear the pending payload for `slug`, or `None` if there is none."""
        return self._data.pop(slug, None)


class WelcomeBackSubscriber:
    """Subscribes to CronWatch's `WatchUpdates` stream and feeds a `WelcomeBackCache`.

    Follows the exact start()/stop() background-task pattern `WatchPoller`
    (`web/watch.py`) already established: `start()` schedules `_loop()` as an
    asyncio task, `stop()` cancels it and awaits its unwind. Meant to be driven
    from `create_app`'s lifespan exactly like `WatchPoller`/`AnalysisWorker`.
    """

    def __init__(
        self,
        cache: WelcomeBackCache,
        grpc_target: str,
        bus: "JobEventBus | None" = None,
    ) -> None:
        self._cache = cache
        self._grpc_target = grpc_target
        self._bus = bus
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    def start(self) -> None:
        """Begin subscribing in the background."""
        if self._task is None:
            self._stop = asyncio.Event()
            self._task = asyncio.create_task(self._loop(), name="welcome-back-subscriber")

    async def stop(self) -> None:
        """Stop subscribing and wait for the loop to unwind."""
        self._stop.set()
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

    async def _loop(self) -> None:
        """Hold one wildcard `WatchUpdates` stream open, reconnecting on failure.

        A single empty-`puuid` request covers every watched account -- see this
        module's docstring. If the stream ends or errors (CronWatch restarts,
        a network blip) this reopens it after `RECONNECT_DELAY_S` rather than
        giving up permanently, until `stop()` is called.
        """
        import grpc

        from league_stats_common.infra.trace_context import AsyncTraceClientInterceptor
        from league_stats_rpc.v1 import cron_watch_pb2, cron_watch_pb2_grpc

        delay = RECONNECT_DELAY_S
        while not self._stop.is_set():
            try:
                async with grpc.aio.insecure_channel(
                    self._grpc_target, interceptors=[AsyncTraceClientInterceptor()]
                ) as channel:
                    stub = cron_watch_pb2_grpc.CronWatchServiceStub(channel)
                    stream = stub.WatchUpdates(cron_watch_pb2.WatchUpdatesRequest())
                    log.info("Connected to CronWatch WatchUpdates stream at %s", self._grpc_target)
                    delay = RECONNECT_DELAY_S
                    async for update in stream:
                        self._handle(update)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - a dropped stream must not kill the loop
                log.warning(
                    "WatchUpdates stream failed, reconnecting in %.0fs: %s", delay, exc
                )
            if self._stop.is_set():
                break
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=delay)
            except asyncio.TimeoutError:
                delay = min(RECONNECT_MAX_DELAY_S, delay * 2)
                continue

    def _handle(self, update: Any) -> None:
        """Decode one `WatchUpdate` and record it, unless the summary is empty.

        CronWatch's documented failure-degradation path (see
        `cron_watch/service.py`) ships `match_summary_json` as `"{}"` rather
        than blocking the update -- recording that verbatim would let a
        backend failure render as a fabricated "Defeat 0/0/0" toast on the
        frontend, so a decoded summary missing the real `"win"` field is
        dropped here rather than every downstream consumer having to guard
        against it. Malformed/invalid JSON is treated the same way.
        """
        try:
            summary = json.loads(update.match_summary_json) if update.match_summary_json else {}
        except (TypeError, ValueError):
            summary = {}
        if "win" not in summary:
            return
        self._cache.record(
            update.puuid,
            {
                "new_match_id": update.new_match_id,
                "match_summary": summary,
                "detected_at_unix": update.detected_at_unix,
            },
        )
        # `update.puuid` is already the slug (see this module's docstring). Runs
        # directly on the event loop already, so `publish()` can be called with no
        # thread-safety concern -- it works identically from the loop thread or a
        # worker thread. `None` when the bus is unavailable (e.g. an older caller
        # constructing this class without one) -- best-effort, not load-bearing.
        if self._bus is not None:
            self._bus.publish(update.puuid)
