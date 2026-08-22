"""Progress reporter that persists pipeline progress to the job store."""

from __future__ import annotations

import time

from league_stats_common.core.progress import ProgressReporter
from league_stats_common.infra.jobs import JobStore


# Minimum seconds between DB writes for high-frequency events.
_THROTTLE_S = 1.0


class JobCancelled(Exception):
    """Raised when a running job was cancelled by the user."""


class JobProgressReporter(ProgressReporter):
    """Forwards pipeline progress events into a job row (throttled)."""

    def __init__(self, store: JobStore, job_id: int) -> None:
        self._store = store
        self._job_id = job_id
        self._last_write = 0.0

    def update(
        self,
        stage: str,
        *,
        current: int | None = None,
        total: int | None = None,
        detail: str = "",
    ) -> None:
        if self._store.is_cancelled(self._job_id):
            raise JobCancelled()
        now = time.monotonic()
        is_final = current is not None and total is not None and current >= total
        if not is_final and now - self._last_write < _THROTTLE_S:
            return
        self._last_write = now
        self._store.update_progress(
            self._job_id, detail=detail, current=current, total=total
        )
