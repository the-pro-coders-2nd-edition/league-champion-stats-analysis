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
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Protocol

from league_stats.core.config import RANKED_QUEUE_IDS
from league_stats.web.jobs import JOB_KIND_REFRESH, JobStore
from league_stats.web.welcome_back import compute_welcome_back_summary
from league_stats.utils import current_trace_id, get_logger, set_trace_id
from league_stats_common.watch_fields import watch_public_fields  # noqa: F401

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

    def fetch_match_ids(
        self, puuid: str, count: int, *, queue_id: int, use_cache: bool = True
    ) -> list[str]:
        """Newest match ids for a player in one ranked queue."""
        ...

    def resolve_puuid(self, riot_id: str, tagline: str) -> str:
        """PUUID for a Riot ID."""
        ...

    def fetch_match(self, match_id: str) -> dict[str, Any]:
        """Full Match-V5 document for one match id."""
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
        on_new_game: Callable[[str, str, str, dict[str, Any]], None] | None = None,
    ) -> None:
        self._store = store
        self._client_factory = client_factory
        self._now = now
        self._budget = budget or _Budget()
        # Optional observer: called with (slug, job_id, new_match_id, summary)
        # whenever a refresh is newly enqueued. `new_match_id` is whichever
        # queue's newest match id was detected as changed in this tick (see
        # `_check_group`'s tiebreak comment when more than one queue changes at
        # once). `summary` is the lightweight welcome-back payload (win/loss,
        # K/D/A, CS/min, damage share) computed from that match alone -- an
        # empty dict if the extra `fetch_match` call was skipped (budget
        # exhausted) or failed. Nothing in this module uses either value --
        # they exist so a consumer such as CronWatchServicer's WatchUpdates RPC
        # can push a notification the moment a new game is detected, without
        # WatchPoller itself knowing anything about gRPC.
        self._on_new_game = on_new_game
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
        """Look for a new game in either ranked queue; enqueue a refresh when one is found.

        Each account's newest match id is tracked per queue (``{puuid: {"420":
        id, "440": id}}``), because a player's newest solo game and newest flex
        game are independent -- collapsing them into one merged "newest" id
        would make whichever queue sorts first permanently shadow the other.
        A pre-existing ``watch_seen`` entry from before per-queue tracking is a
        flat ``{puuid: match_id}`` string value; it is treated as an empty
        baseline for that puuid rather than crashing, at the cost of one extra
        skipped detection cycle for it.
        """
        region = str(row.get("region") or "euw1")
        players = list(row.get("players") or [])
        seen: dict[str, dict[str, str]] = {}
        for puuid, value in dict(row.get("watch_seen") or {}).items():
            seen[puuid] = dict(value) if isinstance(value, dict) else {}
        try:
            client = self._client_factory(region)
        except Exception as exc:  # noqa: BLE001
            self._note_failure(slug, f"client unavailable: {exc}")
            return False

        found_new = False
        # The most recently detected new match id, kept for the on_new_game hook.
        # If both queues changed in the same tick (rare -- both a solo and a flex
        # game finished between two ticks), the flex one wins simply because
        # RANKED_QUEUE_IDS is checked in (solo, flex) order and this is
        # overwritten on every change; there's no ordering signal available to
        # prefer one over the other (both were merely "not seen before this
        # tick"), so "last one detected" is as good a tiebreak as any.
        new_match_id = ""
        # The puuid whose queue produced `new_match_id`, tracked alongside it so
        # the welcome-back summary is computed for the right participant --
        # `puuid` alone would be stale if a later player in `players` was
        # processed after the change was recorded.
        new_match_puuid = ""
        for player in players:
            label = f"{player.get('riot_id', '')}#{player.get('tagline', '')}"
            try:
                puuid = await self._puuid_for(client, label, player)
            except Exception as exc:  # noqa: BLE001 - any API failure backs off
                self._note_failure(slug, str(exc))
                return False
            queues_seen = seen.setdefault(puuid, {})
            for queue_id in RANKED_QUEUE_IDS:
                if not self._budget.take(self._now()):
                    self._log.debug("Watch budget spent; deferring %s", slug)
                    return False
                try:
                    newest = await asyncio.to_thread(
                        client.fetch_match_ids,
                        puuid,
                        1,
                        queue_id=queue_id,
                        use_cache=False,
                    )
                except Exception as exc:  # noqa: BLE001 - any API failure backs off
                    self._note_failure(slug, str(exc))
                    return False
                if not newest:
                    continue
                key = str(queue_id)
                if queues_seen.get(key) != newest[0]:
                    queues_seen[key] = newest[0]
                    found_new = True
                    new_match_id = newest[0]
                    new_match_puuid = puuid

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

        summary = await self._fetch_summary(client, new_match_id, new_match_puuid, slug)
        return self._enqueue_refresh(row, slug, new_match_id, summary)

    async def _fetch_summary(
        self, client: MatchIdSource, match_id: str, puuid: str, slug: str
    ) -> dict[str, Any]:
        """Fetch the new match and compute its welcome-back summary.

        This is an extra Riot call on top of the per-queue detection calls
        above, so it is budgeted against the same `_Budget` -- exhaustion here
        must not block the refresh from being enqueued, it just means the
        summary ships empty this time. Any fetch/parse failure is likewise
        swallowed to the same effect: the summary is a nice-to-have overlay on
        top of detection, not a gate on it.
        """
        if not match_id or not puuid:
            return {}
        if not self._budget.take(self._now()):
            self._log.debug("Watch budget spent; skipping welcome-back summary for %s", slug)
            return {}
        try:
            match = await asyncio.to_thread(client.fetch_match, match_id)
            return compute_welcome_back_summary(match, puuid)
        except Exception as exc:  # noqa: BLE001 - a summary failure must not block the refresh
            self._log.warning("Welcome-back summary failed for %s: %s", slug, exc)
            return {}

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

    def _enqueue_refresh(
        self,
        row: dict[str, Any],
        slug: str,
        new_match_id: str = "",
        summary: dict[str, Any] | None = None,
    ) -> bool:
        players = list(row.get("players") or [])
        primary = players[0] if players else {}
        # This detection loop is self-driven (an internal asyncio.Task, not tied
        # to any incoming request or RPC), so it never inherits a trace_id from
        # anywhere -- current_trace_id() would read the ContextVar's unset
        # default ("") here. Mint one, the same "originate if absent" rule the
        # HTTP middleware and gRPC server interceptor already use, so a
        # CronWatch-detected new game still gets a trace_id worth persisting
        # and propagating down through AnalysisWorker -> RUNNER.
        trace_id = current_trace_id() or uuid.uuid4().hex
        set_trace_id(trace_id)
        job, created = self._store.enqueue(
            kind=JOB_KIND_REFRESH,
            riot_id=str(primary.get("riot_id") or row.get("riot_id") or ""),
            tagline=str(primary.get("tagline") or row.get("tagline") or ""),
            region=str(row.get("region") or "euw1"),
            player_slug=slug,
            players=players or None,
            trace_id=trace_id,
        )
        if created:
            self._log.info("Watch found a new game for %s; queued a refresh", slug)
            if self._on_new_game is not None:
                self._on_new_game(slug, str(job.get("id", "")), new_match_id, summary or {})
        return created

    def _note_failure(self, slug: str, message: str) -> None:
        self._failures[slug] = self._failures.get(slug, 0) + 1
        self._log.warning("Watch check failed for %s: %s", slug, message)
        self._store.record_watch_tick(slug, error=message[:200], at=self._now())
