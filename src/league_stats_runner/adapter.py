"""Duck-typed stand-in for ``JobStore`` that lets RUNNER call ``execute_job`` verbatim.

``execute_job`` (``league_stats.web.worker``) never imports ``JobStore``
directly for the calls it makes on its ``store`` parameter -- it only relies
on a handful of methods, called positionally/by-keyword exactly the way
``JobStore`` implements them. That makes it possible to substitute an object
with the same surface that streams progress into an in-memory queue instead
of writing to the shared job store.

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
  once per job after contexts resolve, carrying the fully-resolved
  ``players`` list (``PlayerContext.as_player_dict()``: riot_id/tagline plus
  optional ``profile_icon_id``/``solo_tier``/``solo_rank``/``solo_lp`` -- see
  ``league_stats_runner.pipeline.services.PlayerContext``). RUNNER has no player
  registry of its own, but this data is genuinely useful to the caller's
  registry, so it is pushed out as a progress event's ``payload_json``
  instead of being dropped -- see this method's own docstring.
- ``get_player(slug)`` -- **not listed in the task brief**, but genuinely
  required: ``_build_job_services`` calls ``_tracked_players_for_job(job, job_store, ...)``,
  which calls ``job_store.get_player(job_slug)`` whenever the job's own
  ``players_json``/``players`` don't already resolve to a slug-matching
  roster. Omitting this method would raise ``AttributeError`` on any job
  whose tracked-player list doesn't already match its `player_slug`.
- ``mark_player_base_complete(slug)`` / ``mark_player_peer_complete(slug)`` /
  ``mark_player_peer_failed(slug)`` -- called once each at the corresponding
  pipeline milestone.

RUNNER has no player registry or cancel button wiring in this phase, so
``get_player`` and the ``mark_player_*`` methods are no-ops and
``is_cancelled`` always returns ``False`` -- see the docstring on that method
for the resulting limitation. ``upsert_player`` is *not* a no-op: it forwards
its resolved ``players`` payload to the caller over the wire (see its own
docstring) -- RUNNER itself still keeps no registry of this data.
"""

from __future__ import annotations

import json
import queue
import time
from typing import Any


class RunnerJobAdapter:
    """Streams one job's progress into an in-memory queue instead of the shared job store.

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
        }
        event.update(fields)
        # Only a terminal event is actually a "completed at" timestamp; stamping
        # every in-progress event would misleadingly look like a completion time.
        if event["final"]:
            event["completed_at_unix"] = int(time.time())
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
        from league_stats_common.infra import jobs as job_states

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
        """Push the resolved ``players`` list out as a ``payload_json`` event.

        RUNNER keeps no player registry of its own in this phase, but ``players``
        here is the fully-resolved roster ``execute_job`` just computed (riot_id/
        tagline plus optional ``profile_icon_id``/``solo_tier``/``solo_rank``/
        ``solo_lp`` -- see ``PlayerContext.as_player_dict``; note ``puuid`` is
        never part of this dict, so there is nothing puuid-related to lose here).
        Silently discarding it would mean the caller's own registry can never
        learn a player's icon/rank from a job routed through RUNNER. Instead,
        this is pushed through ``payload_json`` -- otherwise unused by every
        other event this adapter pushes -- so ``RunnerServicer._to_stage_result``
        forwards it as-is and the monolith's ``_execute_job_via_runner`` can
        read it back out and fold it into its own registry write.
        """
        self._push(payload_json=json.dumps(players or []))

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
