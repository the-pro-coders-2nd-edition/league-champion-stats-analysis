"""`JobStore` subclass that publishes to a `JobEventBus` on every state change.

Lives in `league_stats_api_ui`, not in `league_stats_common.infra.jobs`:
`JobStore` is shared, unmodified, library code used as-is by `runner`,
`cron-watch`, and `peers` -- none of which run an event loop or serve SSE.
Keeping the notify behavior local to `api-ui` avoids adding an unused concept
to the other three services.

Every overridden method publishes unconditionally, with no attempt to filter
"does this specific field change actually matter to a subscriber." Given this
is a single-user/personal-scale tool, the cost of an occasional redundant
recompute is negligible next to the complexity of fine-grained per-field
diffing -- a deliberate YAGNI call (see the SSE migration design doc).
"""

from __future__ import annotations

from typing import Any

from league_stats_common.infra import jobs as _jobs
from league_stats_common.infra.jobs import JobStore
from league_stats_common.infra.mongo import db_name_from_uri

from league_stats_api_ui.job_events import JobEventBus


class NotifyingJobStore(JobStore):
    """`JobStore` that calls `bus.publish(slug)` after every mutating method."""

    def __init__(self, client: Any, db_name: str, bus: JobEventBus) -> None:
        super().__init__(client, db_name=db_name)
        self._bus = bus

    def set_state(
        self,
        job_id: int,
        state: str,
        *,
        detail: str | None = None,
        error: str | None = None,
    ) -> bool:
        changed = super().set_state(job_id, state, detail=detail, error=error)
        if changed:
            job = self.get(job_id)
            if job is not None:
                self._bus.publish(str(job["player_slug"]))
        return changed

    def update_progress(
        self,
        job_id: int,
        *,
        detail: str,
        current: int | None = None,
        total: int | None = None,
    ) -> None:
        super().update_progress(job_id, detail=detail, current=current, total=total)
        job = self.get(job_id)
        if job is not None:
            self._bus.publish(str(job["player_slug"]))

    def cancel(self, job_id: int) -> dict[str, Any] | None:
        job = super().cancel(job_id)
        if job is not None:
            self._bus.publish(str(job["player_slug"]))
        return job

    def upsert_player(
        self,
        *,
        slug: str,
        riot_id: str,
        tagline: str,
        region: str,
        players: list[dict[str, Any]] | None = None,
    ) -> None:
        super().upsert_player(
            slug=slug, riot_id=riot_id, tagline=tagline, region=region, players=players
        )
        self._bus.publish(slug)

    def set_watch(
        self, slug: str, *, enabled: bool, interval_s: int | None = None
    ) -> bool:
        changed = super().set_watch(slug, enabled=enabled, interval_s=interval_s)
        if changed:
            self._bus.publish(slug)
        return changed

    def record_watch_tick(
        self,
        slug: str,
        *,
        seen: dict[str, str] | None = None,
        error: str = "",
        at: float | None = None,
    ) -> None:
        super().record_watch_tick(slug, seen=seen, error=error, at=at)
        self._bus.publish(slug)

    def mark_player_base_complete(self, slug: str) -> None:
        super().mark_player_base_complete(slug)
        self._bus.publish(slug)

    def mark_player_peer_complete(self, slug: str) -> None:
        super().mark_player_peer_complete(slug)
        self._bus.publish(slug)

    def mark_player_peer_failed(self, slug: str) -> None:
        super().mark_player_peer_failed(slug)
        self._bus.publish(slug)


def open_notifying_jobs_store(bus: JobEventBus) -> NotifyingJobStore:
    """Open a `NotifyingJobStore` against the process-wide Mongo client.

    Mirrors `jobs.open_jobs_store()` exactly (same URI resolution, same
    shared-client seam, called through the `_jobs` module object rather than
    imported by name so tests monkeypatching `jobs._build_mongo_client`
    affect this too), just returning the notifying subclass instead.
    """
    uri = _jobs._resolve_mongo_uri()
    client = _jobs._build_mongo_client(uri)
    return NotifyingJobStore(client, db_name=db_name_from_uri(uri), bus=bus)
