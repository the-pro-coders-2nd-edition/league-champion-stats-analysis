"""Group watch: detect new games and hand off to the analysis queue.

Detection is cheap and IO-bound -- one match-id call per tracked account -- so it
runs on the event loop. Re-analysis is expensive and long, so it stays in the
existing thread worker: the poller only ever enqueues a job. Mixing the two would
either block the event loop or starve the queue.

The Riot client is synchronous, so each call is dispatched through
``asyncio.to_thread``.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Callable, Protocol

from league_stats.web.jobs import JOB_KIND_REFRESH, JobStore
from league_stats.utils import get_logger

# Detection must not crowd out the analysis jobs it triggers: both share one
# process-wide rate limiter. A dev key allows 100 requests per 2 minutes, so this
# caps watching at 15 of them and defers the rest to the next tick.
RATE_WINDOW_S: float = 120.0
RATE_WINDOW_BUDGET: int = 15

# How often the loop wakes up. Individual groups are still gated by their own
# ``watch_interval_s``, so a short tick just means better staggering.
TICK_INTERVAL_S: float = 20.0

# Consecutive-failure backoff, capped so a recovered API is picked up promptly.
BACKOFF_BASE_S: float = 60.0
BACKOFF_MAX_S: float = 1800.0


class MatchIdSource(Protocol):
    """The one Riot capability watching needs."""

    def fetch_ranked_match_ids(self, puuid: str, count: int) -> list[str]:
        """Newest ranked match ids for a player."""
        ...

    def resolve_puuid(self, riot_id: str, tagline: str) -> str:
        """PUUID for a Riot ID."""
        ...


@dataclass
class _Budget:
    """Sliding-window allowance for detection calls."""

    window_s: float = RATE_WINDOW_S
    limit: int = RATE_WINDOW_BUDGET
    spent: list[float] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.spent is None:
            self.spent = []

    def take(self, now: float) -> bool:
        """Consume one call if the window allows it."""
        cutoff = now - self.window_s
        self.spent = [stamp for stamp in self.spent if stamp > cutoff]
        if len(self.spent) >= self.limit:
            return False
        self.spent.append(now)
        return True


def _backoff_for(failures: int) -> float:
    if failures <= 0:
        return 0.0
    return min(BACKOFF_MAX_S, BACKOFF_BASE_S * (2 ** (failures - 1)))


class WatchPoller:
    """Checks watched groups for new games and enqueues refreshes."""

    def __init__(
        self,
        store: JobStore,
        client_factory: Callable[[str], MatchIdSource],
        *,
        now: Callable[[], float] = time.time,
        budget: _Budget | None = None,
    ) -> None:
        self._store = store
        self._client_factory = client_factory
        self._now = now
        self._budget = budget or _Budget()
        self._failures: dict[str, int] = {}
        self._puuids: dict[str, str] = {}
        self._log = get_logger("watch")
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    # ------------------------------------------------------------------ lifecycle

    def start(self) -> None:
        """Begin polling in the background."""
        if self._task is None:
            self._stop = asyncio.Event()
            self._task = asyncio.create_task(self._loop(), name="watch-poller")

    async def stop(self) -> None:
        """Stop polling and wait for the loop to unwind."""
        self._stop.set()
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                await self.tick()
            except Exception as exc:  # noqa: BLE001 - a bad tick must not kill the loop
                self._log.warning("Watch tick failed: %s", exc)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=TICK_INTERVAL_S)
            except asyncio.TimeoutError:
                continue

    # ---------------------------------------------------------------------- work

    async def tick(self) -> list[str]:
        """Check every group that is due, returning the slugs that were refreshed."""
        refreshed: list[str] = []
        for row in self._store.list_watched_players():
            if self._stop.is_set():
                break
            slug = str(row.get("slug", ""))
            if not slug or not self._is_due(row, slug):
                continue
            if self._has_active_job(slug):
                # A refresh is already running; enqueueing again would queue
                # duplicates faster than the worker can drain them.
                continue
            if await self._check_group(row, slug):
                refreshed.append(slug)
        return refreshed

    def _is_due(self, row: dict[str, Any], slug: str) -> bool:
        last = row.get("last_watch_at")
        if last is None:
            return True
        interval = float(row.get("watch_interval_s") or TICK_INTERVAL_S)
        wait = interval + _backoff_for(self._failures.get(slug, 0))
        return (self._now() - float(last)) >= wait

    def _has_active_job(self, slug: str) -> bool:
        return any(
            str(job.get("player_slug", "")) == slug
            for job in self._store.list_active_jobs()
        )

    async def _check_group(self, row: dict[str, Any], slug: str) -> bool:
        """Look for a new game; enqueue a refresh when one is found."""
        region = str(row.get("region") or "euw1")
        players = list(row.get("players") or [])
        seen: dict[str, str] = dict(row.get("watch_seen") or {})
        try:
            client = self._client_factory(region)
        except Exception as exc:  # noqa: BLE001
            self._note_failure(slug, f"client unavailable: {exc}")
            return False

        found_new = False
        for player in players:
            if not self._budget.take(self._now()):
                self._log.debug("Watch budget spent; deferring %s", slug)
                return False
            label = f"{player.get('riot_id', '')}#{player.get('tagline', '')}"
            try:
                puuid = await self._puuid_for(client, label, player)
                newest = await asyncio.to_thread(client.fetch_ranked_match_ids, puuid, 1)
            except Exception as exc:  # noqa: BLE001 - any API failure backs off
                self._note_failure(slug, str(exc))
                return False
            if not newest:
                continue
            if seen.get(puuid) != newest[0]:
                seen[puuid] = newest[0]
                found_new = True

        self._failures.pop(slug, None)
        first_look = row.get("last_watch_at") is None
        self._store.record_watch_tick(slug, seen=seen, at=self._now())

        if not found_new:
            return False
        if first_look:
            # The first check only establishes a baseline: everything looks new
            # because nothing was recorded yet.
            self._log.info("Watch baseline recorded for %s", slug)
            return False

        return self._enqueue_refresh(row, slug)

    async def _puuid_for(
        self, client: MatchIdSource, label: str, player: dict[str, Any]
    ) -> str:
        """Resolve and memoise a PUUID for one account."""
        cached = self._puuids.get(label)
        if cached:
            return cached
        puuid = await asyncio.to_thread(
            client.resolve_puuid,
            str(player.get("riot_id", "")),
            str(player.get("tagline", "")),
        )
        self._puuids[label] = puuid
        return puuid

    def _enqueue_refresh(self, row: dict[str, Any], slug: str) -> bool:
        players = list(row.get("players") or [])
        primary = players[0] if players else {}
        _job, created = self._store.enqueue(
            kind=JOB_KIND_REFRESH,
            riot_id=str(primary.get("riot_id") or row.get("riot_id") or ""),
            tagline=str(primary.get("tagline") or row.get("tagline") or ""),
            region=str(row.get("region") or "euw1"),
            player_slug=slug,
            players=players or None,
        )
        if created:
            self._log.info("Watch found a new game for %s; queued a refresh", slug)
        return created

    def _note_failure(self, slug: str, message: str) -> None:
        self._failures[slug] = self._failures.get(slug, 0) + 1
        self._log.warning("Watch check failed for %s: %s", slug, message)
        self._store.record_watch_tick(slug, error=message[:200], at=self._now())


def watch_public_fields(row: dict[str, Any]) -> dict[str, Any]:
    """Watch state for API responses."""
    return {
        "watch_enabled": bool(row.get("watch_enabled")),
        "watch_interval_s": int(row.get("watch_interval_s") or 0),
        "last_watch_at": row.get("last_watch_at"),
        "last_watch_error": str(row.get("last_watch_error") or ""),
    }
