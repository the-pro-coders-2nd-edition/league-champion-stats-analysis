"""In-process pub/sub that wakes SSE subscribers when job/player state changes.

See the SSE migration design doc for the full picture. The short version:
`AnalysisWorker` (plain `threading.Thread`s, see `league_stats_runner/worker.py`)
and `WelcomeBackSubscriber` (an `asyncio.Task` on the event loop,
`welcome_back_cache.py`) are the two producers that need to reach the same
place -- a per-slug fan-out SSE handlers subscribe to -- despite running on
different threading models. `JobEventBus` is that fan-out point.

Deliberately in-process, in-memory, no persistence, no cross-process fan-out:
correct and sufficient because `api-ui` is confirmed single-replica. If that
ever changes, this is the one thing that would need to move to a shared
broker (Redis pub/sub, etc.) -- nothing else in this design assumes
single-process.

The bus only signals *that* something changed for a slug, never *what*: each
SSE connection recomputes its own full payload on wake-up (see `app.py`),
mirroring today's polling semantics (no diffs/deltas).
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import AsyncIterator


class JobEventBus:
    """Per-slug (plus a wildcard topic) wake-up fan-out for SSE handlers."""

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._subscribers: dict[str | None, list[asyncio.Queue[int]]] = defaultdict(list)
        # Per-slug wake-up counter, bumped once per `publish(slug)` call. Exposed via
        # `generation()` so a caller can memoize a computed payload across sibling
        # subscribers woken by the very same publish (see `app.py`'s single-flight
        # wrapper around `_player_status_payload`, needed because
        # `WelcomeBackCache.get` is consume-on-read).
        self._generation: dict[str, int] = defaultdict(int)

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Record the event loop `publish()` should deliver wake-ups onto.

        Called once from `create_app`'s lifespan startup, where the running
        loop is already available.
        """
        self._loop = loop

    def publish(self, slug: str) -> None:
        """Wake every subscriber for `slug` and every wildcard subscriber.

        Thread-safe: callable from any thread, including `AnalysisWorker`'s
        plain `threading.Thread`s, via `call_soon_threadsafe`. A no-op before
        `bind_loop()` has run (no subscribers can exist yet at that point).
        """
        loop = self._loop
        if loop is None:
            return
        loop.call_soon_threadsafe(self._deliver, slug)

    def _deliver(self, slug: str) -> None:
        """Runs on the bound event loop. Pushes a wake-up into every relevant queue."""
        self._generation[slug] += 1
        generation = self._generation[slug]
        for queue in self._subscribers.get(slug, ()):
            queue.put_nowait(generation)
        for queue in self._subscribers.get(None, ()):
            queue.put_nowait(generation)

    def generation(self, slug: str) -> int:
        """Current wake-up counter for `slug` (0 if it has never been published)."""
        return self._generation.get(slug, 0)

    def subscriber_count(self, slug: str | None) -> int:
        """Number of live subscribers for `slug` (or the wildcard topic, `None`).

        A test seam: proves `subscribe()`'s cleanup ran after a client disconnects.
        """
        return len(self._subscribers.get(slug, ()))

    async def subscribe(self, slug: str | None) -> AsyncIterator[int]:
        """Register for wake-ups on `slug` (or every publish, if `slug` is `None`).

        Registration happens synchronously within this coroutine (before it
        returns), not lazily on first iteration -- so a caller that awaits
        this and only *then* computes an initial snapshot cannot miss a
        publish that lands in between (see `app.py`'s SSE routes: "subscribe,
        then snapshot" order, matching the design doc's connection lifecycle).

        The returned iterator yields the per-slug generation number for each
        wake-up (a bookkeeping value, not the changed data itself -- callers
        must recompute their own snapshot). Removes itself from the registry
        in a `finally:` block, so a client disconnect (which cancels the
        consuming task) cleans up without a separate disconnect-polling loop.
        """
        queue: asyncio.Queue[int] = asyncio.Queue()
        self._subscribers[slug].append(queue)

        async def _iterate() -> AsyncIterator[int]:
            try:
                while True:
                    yield await queue.get()
            finally:
                subscribers = self._subscribers.get(slug)
                if subscribers is not None and queue in subscribers:
                    subscribers.remove(queue)
                    if not subscribers:
                        del self._subscribers[slug]

        return _iterate()
