"""RUNNER's gRPC service: wraps `execute_job` verbatim.

Design note -- the RawMatchStore substitution the task brief called for is
NOT wired up here, and could not be without editing `worker.py` (out of
scope for this task). Details:

`execute_job` (`league_stats.web.worker`) does not take a `Services` object,
and does not accept an injectable match-store factory. Internally it always
calls the *private*, module-level `_build_job_services(job, web_config,
reporter, job_store=store)`, which itself always constructs
`MatchStore(config.db_path)` (a local SQLite cache) -- that construction is
hardcoded, not parameterised, and not overridable from outside the module
short of monkeypatching `league_stats.web.worker._build_job_services` at
runtime (fragile, and inappropriate for production code; not done here).

So in this phase, a job run through RUNNER uses the exact same on-disk
SQLite match cache the monolith's worker loop would (under
`web_config.output_dir`'s configured `AppConfig.cache_dir`), NOT
`RawMatchStore`/Mongo. This is consistent with "reuse, don't rewrite" for
this task -- `execute_job` runs completely unmodified -- but it means the
Mongo-backed raw match store Task 3 built is not yet reachable from RUNNER.
Wiring it up needs a follow-up task that gives `worker.py` an injectable
match-store seam (e.g. a `Services` factory parameter on `execute_job`
itself), which is out of this task's scope since it requires editing
`worker.py`.

Similarly, `RunnerServiceServicer` (generated from `runner.proto`) is a
**synchronous** servicer base class (`grpc.server(...)`, not
`grpc.aio.server(...)`) -- the task brief assumed `grpc.aio` and `async def`
methods, but the generated `runner_pb2_grpc.py` in this repo was produced
against plain `grpc`, and the brief's own Step 6 test harness
(`grpc.server(futures.ThreadPoolExecutor(...))`) is the synchronous API too.
`RunnerServicer`'s methods are therefore plain (non-async) methods, matching
what `RunnerServiceServicer` actually declares.
"""

from __future__ import annotations

import itertools
import queue
import threading
from typing import Any

import grpc

from league_stats.core.config import load_web_config
from league_stats.runner.adapter import RunnerJobAdapter
from league_stats.utils import get_logger
from league_stats.web import jobs as job_states
from league_stats.web.jobs import encode_players
from league_stats.web.worker import execute_job
from league_stats_rpc.v1 import common_pb2, runner_pb2, runner_pb2_grpc

log = get_logger("runner_service")

_REGION_TO_STR: dict[int, str] = {
    common_pb2.EUROPE: "europe",
    common_pb2.AMERICAS: "americas",
    common_pb2.ASIA: "asia",
    common_pb2.SEA: "sea",
}

_KIND_TO_STR: dict[int, str] = {
    runner_pb2.JOB_KIND_ANALYZE: job_states.JOB_KIND_ANALYZE,
    runner_pb2.JOB_KIND_REFRESH: job_states.JOB_KIND_REFRESH,
    runner_pb2.JOB_KIND_REGENERATE: job_states.JOB_KIND_REGENERATE,
}

_STAGE_BY_NAME: dict[str, int] = {
    "stage_a": common_pb2.STAGE_A,
    "stage_b": common_pb2.STAGE_B,
}


def _job_from_request(request: runner_pb2.EnqueueJobRequest, job_id: int) -> dict[str, Any]:
    """Build the plain job dict `execute_job` expects from an `EnqueueJobRequest`.

    Mirrors the shape a real `JobStore` row decodes to (see
    `league_stats.web.jobs.JobStore._get`): a `players_json` string plus a
    parallel decoded `players` list, so `_tracked_players_for_job`'s
    `decode_players(job.get("players_json"), ...)` call behaves exactly as
    it would against a real SQLite row.
    """
    players = [{"riot_id": p.riot_id, "tagline": p.tagline} for p in request.players]
    return {
        "id": job_id,
        "kind": _KIND_TO_STR.get(request.kind, job_states.JOB_KIND_ANALYZE),
        "riot_id": request.riot_id,
        "tagline": request.tagline,
        "region": _REGION_TO_STR.get(request.region, "europe"),
        "player_slug": request.player_slug,
        "players_json": encode_players(players) if players else "[]",
        "players": players,
        "filter_champion": request.filter_champion or None,
        "filter_role": request.filter_role or None,
        "min_games": request.min_games or None,
    }


def _to_stage_result(event: dict[str, Any]) -> runner_pb2.StageResult:
    """Translate one adapter event dict into a `StageResult` message."""
    return runner_pb2.StageResult(
        job_id=event["job_id"],
        stage=_STAGE_BY_NAME.get(event.get("stage", "stage_a"), common_pb2.STAGE_A),
        payload_json="",
        completed_at_unix=event.get("completed_at_unix", 0),
        error=event.get("error", ""),
        final=event.get("final", False),
        detail=event.get("detail", ""),
        current=event.get("current") or 0,
        total=event.get("total") or 0,
    )


class RunnerServicer(runner_pb2_grpc.RunnerServiceServicer):
    """Implements RunnerService by running `execute_job` on a worker thread per job."""

    def __init__(self, web_config: Any | None = None) -> None:
        self._web_config = web_config if web_config is not None else load_web_config()
        self._queues: dict[str, "queue.SimpleQueue[dict[str, Any]]"] = {}
        self._lock = threading.Lock()
        self._ids = itertools.count(1)

    def _allocate_job_id(self) -> int:
        with self._lock:
            return next(self._ids)

    def EnqueueJob(self, request, context):
        """Build a job dict, spawn a background thread running execute_job, return its id."""
        job_id = self._allocate_job_id()
        events: "queue.SimpleQueue[dict[str, Any]]" = queue.SimpleQueue()
        self._queues[str(job_id)] = events
        job = _job_from_request(request, job_id)
        adapter = RunnerJobAdapter(job_id=job_id, events=events)
        thread = threading.Thread(
            target=execute_job,
            args=(job, adapter, self._web_config),
            name=f"runner-job-{job_id}",
            daemon=True,
        )
        thread.start()
        return runner_pb2.EnqueueJobResponse(job_id=str(job_id))

    def StreamJobProgress(self, request, context):
        """Yield StageResult messages for one job until a final=True message lands."""
        events = self._queues.get(request.job_id)
        if events is None:
            context.set_code(grpc.StatusCode.NOT_FOUND)
            context.set_details(f"Unknown job_id {request.job_id!r}")
            return
        while True:
            event = events.get()
            yield _to_stage_result(event)
            if event.get("final"):
                return

    def NotifyPeerBaselineReady(self, request, context):
        """Minimal stub: real PEERS wiring is Phase 3's job, not this one."""
        log.info(
            "NotifyPeerBaselineReady received for request_id=%s (champion=%s, lane=%s, "
            "rank=%s) -- not yet wired to the peer stage (Phase 3)",
            request.request_id,
            request.champion,
            request.lane,
            request.rank,
        )
        return common_pb2.Ack(ok=True, message="received (peer stage wiring is Phase 3)")
