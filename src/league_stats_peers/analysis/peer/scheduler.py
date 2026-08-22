"""Batched, round-robin scheduler for `SamplingTask`s.

RFC "Batched, Round-Robin Live Sampling for PEERS", §5.1: a small pool of
batch-worker threads repeatedly pop the next active `SamplingTask` off a
shared FIFO queue and run exactly one batch on it (`SamplingTask.run_batch`),
then either finalize it (target reached), finalize it as partial (ceiling/
stall reached below target), or re-enqueue it at the *back* of the queue.
Two tasks active at once therefore interleave batch-by-batch instead of one
running to exhaustion while the other queues behind a worker.

This scheduler intentionally does not add concurrency against the shared
Riot rate limiter (RFC §5.1.1): batch-workers still each call through the
same process-wide `RateLimiter` `SamplingTask`'s `RiotApiClient` shares with
every other caller. What changes is *interleaving*, not total throughput.

Deliberately generic and free of any Mongo/gRPC dependency, so scheduler
fairness, no-waste caching, and interim-serving can all be tested directly
against fake `SamplingTask`s (RFC §8) without spinning up PEERS' service
layer.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Callable

from prometheus_client import Counter, Gauge, Histogram

from league_stats_common.core.champions import VALID_ROLES
from league_stats_peers.analysis.peer.sampling_task import SamplingTask, TaskKey
from league_stats_common.utils import get_logger

# How long an idle batch-worker sleeps between polls of an empty queue.
# Small enough that a newly-enqueued task starts within a batch or two of
# real API latency, large enough not to busy-loop a CPU core doing nothing.
_IDLE_POLL_INTERVAL_S: float = 0.05

# Scheduler visibility (dashboard-observability follow-up to the RFC above --
# this scheduler shipped with zero metrics). `role` is `VALID_ROLES`, a fixed
# 5-value enum -- safe to label by. The queue's actual *content* (which
# (platform, tier, champion, role, patch) keys are active right now) is
# deliberately NOT a Prometheus label set here: that combination space is
# effectively unbounded once champion is involved (~170 values and growing
# with new releases), which would violate this project's cardinality rule.
# That "what's in the queue right now" visibility instead comes from the
# structured log lines in `get_or_create`/`_run_one_batch`/`_finalize` below,
# read via Grafana's Loki panel -- see `deploy/grafana/dashboards/peers.json`.
PEERS_SCHEDULER_QUEUED_TASKS = Gauge(
    "peers_scheduler_queued_tasks",
    "Sampling tasks currently waiting in the round-robin FIFO queue (not mid-batch).",
)
PEERS_SCHEDULER_ACTIVE_TASKS = Gauge(
    "peers_scheduler_active_tasks",
    "Sampling tasks tracked by the scheduler (queued + currently running a batch), "
    "by role. Every VALID_ROLES member is always set, including 0, so a role with "
    "no active tasks shows as an explicit zero rather than a missing series.",
    ["role"],
)
PEERS_SCHEDULER_BATCHES_TOTAL = Counter(
    "peers_scheduler_batches_total",
    "SamplingTask batches processed, by outcome.",
    ["outcome"],  # re_enqueued | finalized_full | finalized_partial
)

# RFC "PEERS priority scheduling...": relative rank of each of the three
# priority tiers, lower is higher-priority. Used by `get_or_create` to decide
# whether a caller's requested priority should promote an already-active
# task -- never used to demote one.
_PRIORITY_RANK: dict[str, int] = {"explicit": 0, "refining": 1, "background": 2}
PEERS_SCHEDULER_BATCH_DURATION = Histogram(
    "peers_scheduler_batch_duration_seconds",
    "Wall-clock time of one SamplingTask.run_batch() call.",
)


class SamplingScheduler:
    """Owns the shared FIFO queue of active `SamplingTask`s and their batch-workers.

    ``on_interim``/``on_finalize`` are the scheduler's only hooks into the
    outside world (e.g. writing to the Mongo-backed live cache in
    `analysis.peer.baseline`) -- this module has no cache/store dependency of
    its own, so it can be unit-tested with plain fakes.
    """

    def __init__(
        self,
        *,
        num_workers: int = 4,
        on_interim: "Callable[[SamplingTask], None] | None" = None,
        on_finalize: "Callable[[SamplingTask, str], None] | None" = None,
    ) -> None:
        self._lock = threading.RLock()
        self._explicit_queue: "deque[SamplingTask]" = deque()
        self._refining_queue: "deque[SamplingTask]" = deque()
        self._background_queue: "deque[SamplingTask]" = deque()
        self._tasks: dict[TaskKey, SamplingTask] = {}
        self._conditions: dict[TaskKey, threading.Condition] = {}
        self._num_workers = num_workers
        self._on_interim = on_interim
        self._on_finalize = on_finalize
        self._threads: list[threading.Thread] = []
        self._stopped = False
        self._log = get_logger("peer_sampling_scheduler")

    # -- metrics --------------------------------------------------------------

    def _update_task_gauges(self) -> None:
        """Recompute queue-depth/active-by-role gauges from current state.

        Called with `self._lock` held by every mutation site below -- cheap
        (a handful of dict entries at most) and always sets every
        `VALID_ROLES` member, so a role dropping to zero active tasks is
        reflected immediately instead of leaving a stale nonzero value.
        """
        counts = {role: 0 for role in VALID_ROLES}
        for key in self._tasks:
            role = key[3]
            if role in counts:
                counts[role] += 1
        for role, count in counts.items():
            PEERS_SCHEDULER_ACTIVE_TASKS.labels(role=role).set(count)
        PEERS_SCHEDULER_QUEUED_TASKS.set(
            len(self._explicit_queue) + len(self._refining_queue) + len(self._background_queue)
        )

    def _queue_for(self, priority: str) -> "deque[SamplingTask]":
        if priority == "explicit":
            return self._explicit_queue
        if priority == "refining":
            return self._refining_queue
        return self._background_queue

    @staticmethod
    def _log_fields(task: SamplingTask) -> str:
        """Structured ``key=value`` fields for one task, for Loki-side filtering.

        Deliberately a log line, not a Prometheus label set -- see the module
        docstring's cardinality note above.
        """
        platform, tier, champion, role, patch = task.key
        return (
            f"platform={platform} tier={tier} champion={champion} role={role} "
            f"patch={patch} games={task.games} downloads={task.downloads} "
            f"batches_run={task.batches_run}"
        )

    # -- task lifecycle -----------------------------------------------------

    def get_or_create(
        self,
        key: TaskKey,
        factory: "Callable[[], SamplingTask]",
        *,
        priority: str = "explicit",
    ) -> SamplingTask:
        """Return the active task for `key`, enqueuing a new one if none exists.

        Mirrors `PeersServicer._get_or_submit`'s existing dedup shape: a
        caller for a key already active attaches to that task instead of
        starting a redundant scan.

        `priority` only ever promotes an existing task (never demotes it) --
        RFC "PEERS priority scheduling...", §1.2 Case A/B: a caller asking
        for a higher-priority tier than the task currently holds moves it to
        the front of the relevant queue; a caller asking for a lower tier
        than the task already holds is a no-op on priority.
        """
        with self._lock:
            existing = self._tasks.get(key)
            if existing is not None:
                if _PRIORITY_RANK[priority] < _PRIORITY_RANK[existing.priority]:
                    old_queue = self._queue_for(existing.priority)
                    try:
                        old_queue.remove(existing)
                    except ValueError:
                        pass  # mid-batch, not sitting in a queue right now
                    else:
                        self._queue_for(priority).append(existing)
                    existing.priority = priority
                    self._update_task_gauges()
                return existing
            task = factory()
            task.priority = priority
            self._tasks[key] = task
            self._conditions[key] = threading.Condition()
            self._queue_for(priority).append(task)
            self._update_task_gauges()
            self._log.info(
                "sampling_task_enqueued key=%s %s", key, self._log_fields(task)
            )
            return task

    def is_active(self, key: TaskKey) -> bool:
        with self._lock:
            return key in self._tasks

    # -- batch execution ------------------------------------------------------

    def step(self) -> bool:
        """Pop the next queued task and run exactly one batch on it.

        Returns False when the queue was empty (nothing to do). Safe to call
        directly (deterministic, single-threaded) from tests, or in a loop
        from a background worker thread (`start()`).
        """
        with self._lock:
            if self._explicit_queue:
                task = self._explicit_queue.popleft()
            elif self._refining_queue:
                task = self._refining_queue.popleft()
            elif self._background_queue:
                task = self._background_queue.popleft()
            else:
                return False
            self._update_task_gauges()
        self._run_one_batch(task)
        return True

    def _run_one_batch(self, task: SamplingTask) -> None:
        key = task.key
        cond = self._conditions.get(key)
        try:
            with PEERS_SCHEDULER_BATCH_DURATION.time():
                task.run_batch()
        except Exception:  # noqa: BLE001 -- a single bad batch must never wedge
            # this key forever. Without this, an exception here would leave
            # the task permanently in `self._tasks`/`self._conditions` (already
            # popped off its priority queue by `step()`, never re-enqueued or
            # finalized) -- any caller blocked in `wait_for_signal` would hang
            # forever, since the production caller (`_try_live_baseline`)
            # passes no timeout. Finalizing as partial with whatever was
            # collected before the failure is strictly better than a wedged
            # scheduler and a permanently-hung caller.
            self._log.exception(
                "SamplingTask.run_batch failed for key=%s, finalizing as partial", key
            )
            self._finalize(task, "partial")
            return

        if task.exhausted:
            self._finalize(task, "full" if task.reached_target else "partial")
        else:
            if task.reached_target and task.priority != "background":
                task.priority = "refining"  # RFC §1.2 Case B: unconditional demotion
            if (task.reached_interim or task.reached_target) and self._on_interim is not None:
                try:
                    self._on_interim(task)
                except Exception:  # noqa: BLE001 -- see `_finalize`'s matching guard.
                    self._log.exception("on_interim hook failed for key=%s", key)
            with self._lock:
                self._queue_for(task.priority).append(task)
                self._update_task_gauges()
            PEERS_SCHEDULER_BATCHES_TOTAL.labels(outcome="re_enqueued").inc()
            self._log.info(
                "sampling_task_re_enqueued key=%s %s", key, self._log_fields(task)
            )

        if cond is not None:
            with cond:
                cond.notify_all()

    def _finalize(self, task: SamplingTask, status: str) -> None:
        # Remove the task/condition BEFORE calling the hook, not after: a
        # caller in `wait_for_signal` must be releasable (it re-checks
        # `self._tasks.get(key) is None`) even if `on_finalize` itself raises
        # below -- otherwise a broken hook (e.g. a cache write blowing up on
        # unexpected input) would leave the task in limbo exactly like an
        # unguarded `run_batch` failure would (see `_run_one_batch`).
        key = task.key
        with self._lock:
            self._tasks.pop(key, None)
            cond = self._conditions.pop(key, None)
            self._update_task_gauges()
        # `status` is always "full" or "partial" (see this method's two call
        # sites) -- a fixed 2-value enum, safe to fold into the outcome label.
        PEERS_SCHEDULER_BATCHES_TOTAL.labels(outcome=f"finalized_{status}").inc()
        if self._on_finalize is not None:
            try:
                self._on_finalize(task, status)
            except Exception:  # noqa: BLE001 -- best-effort; the task is already
                # considered finalized regardless of whether persisting its
                # result succeeded.
                self._log.exception(
                    "on_finalize hook failed for key=%s, status=%s", key, status
                )
        self._log.info(
            "sampling_task_finalized key=%s status=%s %s",
            key,
            status,
            self._log_fields(task),
        )
        if cond is not None:
            with cond:
                cond.notify_all()

    # -- waiting --------------------------------------------------------------

    def wait_for_signal(self, key: TaskKey, timeout: float | None = None) -> None:
        """Block until the task for `key` reaches its interim threshold, finalizes,
        or `timeout` elapses (whichever first).

        A key with no active task (already finalized, or never existed)
        returns immediately -- callers are expected to re-read whatever the
        task's finalize/interim hook wrote (e.g. the live cache) afterward.
        """
        with self._lock:
            cond = self._conditions.get(key)
        if cond is None:
            return

        deadline = None if timeout is None else time.monotonic() + timeout
        with cond:
            while True:
                task = self._tasks.get(key)
                if task is None:
                    return
                if task.reached_interim or task.done:
                    return
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    return
                cond.wait(remaining)

    # -- background worker pool (production use) -------------------------------

    def start(self) -> None:
        """Start `num_workers` background batch-worker threads (idempotent).

        Plain `daemon=True` threads, not a `ThreadPoolExecutor`: each worker
        runs `_worker_loop` forever (until `stop()`), as a single, never-
        returning submitted callable -- `ThreadPoolExecutor.shutdown()`
        cannot preempt an already-running work item, and its worker threads
        are registered for a `join()` at interpreter exit (`concurrent.futures`'
        own atexit hook), which would hang process/test-session shutdown
        since nothing would ever make that submitted callable return. Daemon
        threads are simply abandoned at interpreter exit instead.
        """
        with self._lock:
            if self._threads or self._stopped:
                return
            for i in range(self._num_workers):
                thread = threading.Thread(
                    target=self._worker_loop, name=f"peer-batch-worker-{i}", daemon=True
                )
                self._threads.append(thread)
                thread.start()

    def _worker_loop(self) -> None:
        while not self._stopped:
            if not self.step():
                time.sleep(_IDLE_POLL_INTERVAL_S)

    def stop(self) -> None:
        """Signal background workers to exit after their current batch (tests only)."""
        self._stopped = True
        with self._lock:
            self._threads = []
