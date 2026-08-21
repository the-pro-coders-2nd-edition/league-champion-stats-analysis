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

from league_stats_peers.analysis.peer.sampling_task import SamplingTask, TaskKey
from league_stats_common.utils import get_logger

# How long an idle batch-worker sleeps between polls of an empty queue.
# Small enough that a newly-enqueued task starts within a batch or two of
# real API latency, large enough not to busy-loop a CPU core doing nothing.
_IDLE_POLL_INTERVAL_S: float = 0.05


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
        self._queue: "deque[SamplingTask]" = deque()
        self._tasks: dict[TaskKey, SamplingTask] = {}
        self._conditions: dict[TaskKey, threading.Condition] = {}
        self._num_workers = num_workers
        self._on_interim = on_interim
        self._on_finalize = on_finalize
        self._threads: list[threading.Thread] = []
        self._stopped = False
        self._log = get_logger("peer_sampling_scheduler")

    # -- task lifecycle -----------------------------------------------------

    def get_or_create(
        self, key: TaskKey, factory: "Callable[[], SamplingTask]"
    ) -> SamplingTask:
        """Return the active task for `key`, enqueuing a new one if none exists.

        Mirrors `PeersServicer._get_or_submit`'s existing dedup shape: a
        caller for a key already active attaches to that task instead of
        starting a redundant scan.
        """
        with self._lock:
            existing = self._tasks.get(key)
            if existing is not None:
                return existing
            task = factory()
            self._tasks[key] = task
            self._conditions[key] = threading.Condition()
            self._queue.append(task)
            self._log.info("Enqueued new sampling task for key=%s", key)
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
            if not self._queue:
                return False
            task = self._queue.popleft()
        self._run_one_batch(task)
        return True

    def _run_one_batch(self, task: SamplingTask) -> None:
        key = task.key
        cond = self._conditions.get(key)
        try:
            task.run_batch()
        except Exception:  # noqa: BLE001 -- a single bad batch must never wedge
            # this key forever. Without this, an exception here would leave
            # the task permanently in `self._tasks`/`self._conditions` (already
            # popped off `self._queue` by `step()`, never re-enqueued or
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

        if task.reached_target:
            self._finalize(task, "full")
        elif task.exhausted:
            self._finalize(task, "partial")
        else:
            if task.reached_interim and self._on_interim is not None:
                try:
                    self._on_interim(task)
                except Exception:  # noqa: BLE001 -- see `_finalize`'s matching guard.
                    self._log.exception("on_interim hook failed for key=%s", key)
            with self._lock:
                self._queue.append(task)

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
            "Sampling task finalized for key=%s: status=%s, games=%d, downloads=%d, batches=%d",
            key,
            status,
            task.games,
            task.downloads,
            task.batches_run,
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
