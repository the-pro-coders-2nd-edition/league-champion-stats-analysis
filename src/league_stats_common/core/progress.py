"""Progress reporting seam between the pipeline and its callers.

The pipeline layers (fetch, orchestrator, peer sampling) emit coarse progress
events through a :class:`ProgressReporter`. The default implementation is a
no-op so CLI behavior is unchanged; the web worker installs a reporter that
persists progress to the job store so users can poll it.
"""

from __future__ import annotations

# Stage keys emitted by the pipeline.
STAGE_FETCHING = "fetching"
STAGE_PARSING = "parsing"
STAGE_ANALYZING = "analyzing"
STAGE_PEER = "peer"


class ProgressReporter:
    """Sink for pipeline progress events. Base implementation ignores them."""

    def update(
        self,
        stage: str,
        *,
        current: int | None = None,
        total: int | None = None,
        detail: str = "",
    ) -> None:
        """Record one progress event.

        Args:
            stage: One of the ``STAGE_*`` keys.
            current: Units completed within the stage, when known.
            total: Total units within the stage, when known.
            detail: Human-readable description of the current step.
        """


NULL_REPORTER = ProgressReporter()
