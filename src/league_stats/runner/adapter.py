"""Duck-typed stand-in for ``JobStore`` that lets RUNNER call ``execute_job`` verbatim.

``execute_job`` (``league_stats.web.worker``) never imports ``JobStore``
directly for the calls it makes on its ``store`` parameter -- it only relies
on a handful of methods, called positionally/by-keyword exactly the way
``JobStore`` implements them. That makes it possible to substitute an object
with the same surface that streams progress into an in-memory queue instead
of writing to SQLite.

The exact method list below was catalogued by reading ``execute_job`` and
everything it calls end to end (``_build_job_services``, ``_tracked_players_for_job``,
``_run_stage_a``, ``_run_stage_b``, ``_ensure_not_cancelled``), plus
``JobProgressReporter`` (``league_stats.web.progress``), which wraps ``store``
for pipeline-level progress events:

- ``is_cancelled(job_id)`` -- called directly by ``execute_job``/``_ensure_not_cancelled``,
  and by ``JobProgressReporter.update`` before every pipeline progress event.
- ``set_state(job_id, state, *, detail=None, error=None)`` -- called by
  ``execute_job`` at every stage transition.
- ``update_progress(job_id, *, detail, current=None, total=None)`` -- called
  by ``_run_stage_a``/``_run_stage_b`` directly, and by ``JobProgressReporter.update``
  (which drops the ``stage`` argument pipeline callers pass it -- ``store.update_progress``
  never learns which pipeline stage triggered the call).
- ``upsert_player(*, slug, riot_id, tagline, region, players=None)`` -- called
  once per job after contexts resolve.
- ``get_player(slug)`` -- **not listed in the task brief**, but genuinely
  required: ``_build_job_services`` calls ``_tracked_players_for_job(job, job_store, ...)``,
  which calls ``job_store.get_player(job_slug)`` whenever the job's own
  ``players_json``/``players`` don't already resolve to a slug-matching
  roster. Omitting this method would raise ``AttributeError`` on any job
  whose tracked-player list doesn't already match its `player_slug`.
- ``mark_player_base_complete(slug)`` / ``mark_player_peer_complete(slug)`` /
  ``mark_player_peer_failed(slug)`` -- called once each at the corresponding
  pipeline milestone.

RUNNER has no player registry or cancel button wiring in this phase, so the
registry-shaped methods (``upsert_player``, ``get_player``, ``mark_player_*``)
are no-ops and ``is_cancelled`` always returns ``False`` -- see the docstring
on that method for the resulting limitation.
"""

from __future__ import annotations

import queue
import time
from typing import Any


class RunnerJobAdapter:
    """Streams one job's progress into an in-memory queue instead of SQLite.

    Implements every method ``execute_job`` (and what it calls) invokes on
    its ``store``/``job_store`` parameter, so ``execute_job`` can run
    completely unmodified with this object in place of a real ``JobStore``.
    """

    def __init__(self, job_id: int, events: "queue.SimpleQueue[dict[str, Any]]") -> None:
        self._job_id = job_id
        self._events = events
        # Tracks which pipeline stage the most recent event belongs to, since
        # `store.update_progress` is never told which stage triggered it
        # (`JobProgressReporter.update` receives a `stage` argument but does
        # not forward it) -- entering PEER_RUNNING is the only signal we get.
        self._stage = "stage_a"

    def _push(self, **fields: Any) -> None:
        event: dict[str, Any] = {
            "job_id": str(self._job_id),
            "stage": self._stage,
            "detail": "",
            "error": "",
            "current": None,
            "total": None,
            "final": False,
            "completed_at_unix": int(time.time()),
        }
        event.update(fields)
        self._events.put(event)

    def is_cancelled(self, job_id: int) -> bool:
        """Always ``False`` in this phase.

        Known limitation: RUNNER does not yet expose a cancel path, so a job
        started through RUNNER cannot be cancelled mid-run. The monolith's
        existing cancel button still works today because Phase 1 leaves
        `mode=in_process` jobs going through the real `JobStore`; only jobs
        actually routed through RUNNER hit this no-op.
        """
        return False

    def set_state(
        self,
        job_id: int,
        state: str,
        *,
        detail: str | None = None,
        error: str | None = None,
    ) -> bool:
        """Push a state-transition event; mirrors ``JobStore.set_state``."""
        from league_stats.web import jobs as job_states

        if state == job_states.PEER_RUNNING:
            self._stage = "stage_b"
        self._push(
            state=state,
            detail=detail or "",
            error=error or "",
            final=state in job_states.TERMINAL_STATES,
        )
        return True

    def update_progress(
        self,
        job_id: int,
        *,
        detail: str,
        current: int | None = None,
        total: int | None = None,
    ) -> None:
        """Push a progress event; mirrors ``JobStore.update_progress``."""
        self._push(detail=detail, current=current, total=total)

    def upsert_player(
        self,
        *,
        slug: str,
        riot_id: str,
        tagline: str,
        region: str,
        players: list[dict[str, Any]] | None = None,
    ) -> None:
        """No-op: RUNNER has no player registry in this phase."""

    def get_player(self, slug: str) -> dict[str, Any] | None:
        """No-op: RUNNER has no player registry in this phase.

        ``_tracked_players_for_job`` treats a ``None`` return the same way
        ``JobStore.get_player`` treats an unknown slug -- it falls back to
        the job's own ``players``/``players_json``, then to on-disk report
        metadata.
        """
        return None

    def mark_player_base_complete(self, slug: str) -> None:
        """No-op: RUNNER has no player registry in this phase."""

    def mark_player_peer_complete(self, slug: str) -> None:
        """No-op: RUNNER has no player registry in this phase."""

    def mark_player_peer_failed(self, slug: str) -> None:
        """No-op: RUNNER has no player registry in this phase."""
