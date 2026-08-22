# PEERS priority scheduling, continued sampling, pre-warm, patch cleanup, division scope Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Real user requests never wait behind background work; a `SamplingTask` keeps improving toward `CEILING` instead of stopping dead at `TARGET_PEER_GAMES`; idle scheduler capacity proactively pre-warms common tiers; a patch changeover automatically drops stale peer data; peer matching can use a tighter division-level radius instead of whole-tier widening; and the PEERS Grafana dashboard shows the split between real-request and pre-warm Riot API usage.

**Architecture:** `SamplingScheduler` gains a third priority tier (`_explicit_queue` > `_refining_queue` > `_background_queue`) checked in that strict order every batch. A task demotes from explicit to refining the moment it hits `TARGET_PEER_GAMES`, and only real exhaustion (ceiling or dead snowball) removes it from the scheduler — not hitting target. A new `WarmupTask` type, keyed by a sentinel `(platform, tier, "__prewarm__", "__prewarm__", patch)` disjoint from every real `(champion, role)` key, downloads matches champion/role-blind and always runs at background priority; a small coordinator (`prewarm_tick`) round-robins it across tiers and also owns automatic patch-changeover detection. `rank_scope.py` gains an additive, opt-in division-level ordinal distance check.

**Tech Stack:** Python, `pymongo`, Prometheus client, Grafana dashboard JSON.

**Spec:** `~/.claude/docs/league-champion-stats-analysis/superpowers/specs/2026-08-22-peers-scheduling-and-cleanup-rfc.md`

## Global Constraints

- PEERS' Riot API budget is one shared `RateLimiter` (95 req/2min) across every caller — adding queues/workers never adds throughput, only changes interleaving. Every design here works within that, never around it.
- §2 (decouple confidence from finalization) and §3 (pre-warm) both depend on §1 (priority queues) already existing. Implement in the task order below — do not reorder.
- `WarmupTask` must never be reachable from `wait_for_signal` (nothing ever synchronously blocks on pre-warm) and must always enqueue at background priority, never explicit or refining.
- `division_radius=None` must preserve every existing `rank_matches` call site's behavior exactly — the division-level check is additive, opt-in, never a replacement of the existing tier-only scopes.
- Run the full test suite (`.venv/bin/python -m pytest -q`) after every task's own tests pass.

---

### Task 1: Three-tier priority queue in `SamplingScheduler`

**Files:**
- Modify: `src/league_stats_peers/analysis/peer/sampling_task.py`
- Modify: `src/league_stats_peers/analysis/peer/scheduler.py`
- Test: `tests/test_peer_sampling_scheduler.py`

**Interfaces:**
- Produces: `SamplingTask.priority: str` (`"explicit"` | `"refining"` | `"background"`, default `"explicit"`). `SamplingScheduler.step()` checks `_explicit_queue` → `_refining_queue` → `_background_queue` in that order. `get_or_create(key, factory, *, priority="explicit")` promotes an existing task's priority up (never down) when a caller asks for a higher tier than the task currently holds.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_peer_sampling_scheduler.py` (reuse this file's existing fake-`SamplingTask`/scheduler-construction helpers — read a handful of existing tests first for the exact fixture shapes):

```python
def test_step_services_explicit_queue_before_refining_and_background() -> None:
    """A task enqueued at each of the three priorities: step() must always
    run the explicit one first, then refining, then background, regardless
    of enqueue order."""
    scheduler = SamplingScheduler(num_workers=0)  # no auto-workers; call step() manually
    ran_order = []

    def make_task(name, priority):
        task = FakeTask(key=(..., name, ...))  # adapt to this file's existing FakeTask/fake-task-factory
        task.priority = priority
        return task

    # Enqueue background and refining first, explicit last -- step() must
    # still service explicit first.
    scheduler.get_or_create(("p", "t", "bg", "r", "16.16"), lambda: make_task("bg", "background"))
    scheduler.get_or_create(("p", "t", "ref", "r", "16.16"), lambda: make_task("ref", "refining"))
    scheduler.get_or_create(("p", "t", "exp", "r", "16.16"), lambda: make_task("exp", "explicit"))

    # adapt: run step() 3 times, recording which task ran each time via
    # whatever hook this file's FakeTask exposes (e.g. an on-run callback),
    # and assert the order is exp, ref, bg.


def test_reaching_target_demotes_explicit_to_refining_not_background() -> None:
    """Case B from the RFC: a task that started explicit and reaches target
    must land in the refining queue, never straight to background."""
    ...  # adapt to this file's conventions: a fake task whose reached_target
    # becomes True after one batch, priority starts "explicit"; after step(),
    # assert task.priority == "refining" and it's in _refining_queue, not
    # _background_queue or _explicit_queue.


def test_get_or_create_promotes_priority_never_demotes() -> None:
    """An explicit caller attaching to an already-refining task must promote
    it back to explicit; a background caller attaching to an explicit task
    must NOT demote it."""
    scheduler = SamplingScheduler(num_workers=0)
    key = ("p", "t", "c", "r", "16.16")
    task = scheduler.get_or_create(key, lambda: FakeTask(key=key), priority="refining")
    assert task.priority == "refining"
    promoted = scheduler.get_or_create(key, lambda: FakeTask(key=key), priority="explicit")
    assert promoted is task
    assert task.priority == "explicit"
    not_demoted = scheduler.get_or_create(key, lambda: FakeTask(key=key), priority="background")
    assert not_demoted is task
    assert task.priority == "explicit"  # unchanged -- background never demotes
```

Adapt every placeholder above to this file's real `FakeTask`/fixture conventions — read the file's existing tests first rather than guessing constructor signatures.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_peer_sampling_scheduler.py -k "priority or demotes or promotes" -v`
Expected: FAIL — `SamplingTask` has no `priority` field, `SamplingScheduler` has one queue, `get_or_create` has no `priority` kwarg.

- [ ] **Step 3: Write the implementation**

1. In `src/league_stats_peers/analysis/peer/sampling_task.py`, add to `SamplingTask` (after `patch: str = ""`):
   ```python
   # RFC "PEERS priority scheduling...": which of SamplingScheduler's three
   # queues this task belongs in. "explicit" (someone is synchronously
   # blocked in wait_for_signal for this exact task), "refining" (already
   # answered the confidence-full bar, still improving toward CEILING, but
   # nobody is blocked waiting), "background" (WarmupTask only -- see that
   # module). Never set directly except by SamplingScheduler.
   priority: str = "explicit"
   ```

2. In `src/league_stats_peers/analysis/peer/scheduler.py`:
   - Replace `self._queue: "deque[SamplingTask]" = deque()` with three queues:
     ```python
     self._explicit_queue: "deque[SamplingTask]" = deque()
     self._refining_queue: "deque[SamplingTask]" = deque()
     self._background_queue: "deque[SamplingTask]" = deque()
     ```
     Add a helper:
     ```python
     def _queue_for(self, priority: str) -> "deque[SamplingTask]":
         if priority == "explicit":
             return self._explicit_queue
         if priority == "refining":
             return self._refining_queue
         return self._background_queue
     ```
   - `get_or_create` gains a `priority: str = "explicit"` keyword-only parameter. New-task path: append to `self._queue_for(priority)` instead of `self._queue`, and set `task.priority = priority` before storing. Existing-task path (promotion, §1.2 Case A): if the requested `priority` outranks the task's current one (`explicit > refining > background`, e.g. via `_PRIORITY_RANK = {"explicit": 0, "refining": 1, "background": 2}` and comparing `_PRIORITY_RANK[priority] < _PRIORITY_RANK[existing.priority]`), move it: if it's still sitting in a queue (not mid-batch), remove it from its current queue (`O(n)` scan, bounded by the small number of distinct in-flight keys — not worth a fancier structure) and append it to the new, higher-priority queue; always update `existing.priority` regardless of whether the move happened (a mid-batch task's new priority takes effect on its next re-enqueue). Never demote on a lower-priority request.
   - `step()`:
     ```python
     def step(self) -> bool:
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
     ```
   - `_update_task_gauges`: change `PEERS_SCHEDULER_QUEUED_TASKS.set(len(self._queue))` to sum all three: `len(self._explicit_queue) + len(self._refining_queue) + len(self._background_queue)`. Iterate `self._tasks` for the role-gauge exactly as today (unchanged — `self._tasks` still tracks every active task regardless of which queue it's in).
   - `_run_one_batch`'s re-enqueue branch (the `else:` after `if task.reached_target / elif task.exhausted`): append to `self._queue_for(task.priority)` instead of `self._queue`. (Task 2 below changes the `reached_target`/`exhausted` branching itself — this task only changes which queue the re-enqueue lands in.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_peer_sampling_scheduler.py tests/test_peers_service.py -v`
Expected: all PASS. Fix any pre-existing test that directly inspected `scheduler._queue` (grep: `grep -rn "\._queue\b" tests/test_peer_sampling_scheduler.py`) to use the three new queue attributes or `is_active`/task-count helpers instead.

- [ ] **Step 5: Commit**

```bash
git add src/league_stats_peers/analysis/peer/sampling_task.py src/league_stats_peers/analysis/peer/scheduler.py tests/test_peer_sampling_scheduler.py
git commit -m "feat: three-tier priority scheduling (explicit > refining > background)"
```

---

### Task 2: Decouple "confidence: full" from "task finalized"

**Files:**
- Modify: `src/league_stats_peers/analysis/peer/scheduler.py`
- Modify: `src/league_stats_peers/analysis/peer/baseline.py`
- Test: `tests/test_peer_sampling_scheduler.py`, `tests/test_peer_baseline.py`

**Interfaces:**
- Consumes: `SamplingTask.priority` (Task 1).
- Produces: a task that reaches `TARGET_PEER_GAMES` is no longer removed from the scheduler; only `exhausted` finalizes it. `_on_task_interim` reports `confidence="full"` once target is reached, still `still_refining=True` until real exhaustion.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_peer_sampling_scheduler.py`:

```python
def test_reaching_target_does_not_remove_the_task_from_the_scheduler() -> None:
    """Only exhaustion finalizes a task now -- reaching target alone must
    leave it active (is_active still True, no on_finalize call)."""
    finalized = []
    scheduler = SamplingScheduler(
        num_workers=0, on_finalize=lambda task, status: finalized.append(status)
    )
    key = ("p", "t", "c", "r", "16.16")
    task = ...  # adapt: a FakeTask whose run_batch() flips reached_target=True
    # but exhausted stays False (games < ceiling, queue non-empty)
    scheduler.get_or_create(key, lambda: task)
    scheduler.step()
    assert scheduler.is_active(key) is True
    assert finalized == []


def test_task_finalizes_as_full_once_exhausted_after_reaching_target() -> None:
    """A task that reached target and later exhausts its ceiling must
    finalize with status='full', not 'partial' -- the terminal snapshot
    must say still_refining=False only once, correctly."""
    finalized = []
    scheduler = SamplingScheduler(
        num_workers=0, on_finalize=lambda task, status: finalized.append(status)
    )
    key = ("p", "t", "c", "r", "16.16")
    task = ...  # adapt: reached_target=True AND exhausted=True after run_batch()
    scheduler.get_or_create(key, lambda: task)
    scheduler.step()
    assert finalized == ["full"]
    assert scheduler.is_active(key) is False
```

Add to `tests/test_peer_baseline.py`:

```python
def test_on_task_interim_reports_full_confidence_once_target_reached() -> None:
    """_on_task_interim must switch confidence from 'low' to 'full' the
    moment task.reached_target flips True -- still_refining stays True
    (there's more sampling to come toward CEILING)."""
    from league_stats_peers.analysis.peer import baseline as peer_baseline_module

    task = ...  # adapt: a real/fake SamplingTask with reached_target=True
    dispatched = []
    monkeypatch.setattr(
        peer_baseline_module, "_dispatch_progressive_listeners",
        lambda key, snapshot: dispatched.append(snapshot),
    )
    monkeypatch.setattr(peer_baseline_module, "write_live_cache", lambda *a, **k: None)
    peer_baseline_module._on_task_interim(task)
    assert dispatched[0].confidence == "full"
    assert dispatched[0].still_refining is True
```

Adapt both to this codebase's real fixture conventions.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_peer_sampling_scheduler.py -k "finalize" tests/test_peer_baseline.py::test_on_task_interim_reports_full_confidence_once_target_reached -v`
Expected: FAIL against today's `reached_target` → immediate `_finalize(task, "full")` behavior, and today's hardcoded `confidence="low"` in `_on_task_interim`.

- [ ] **Step 3: Write the implementation**

In `src/league_stats_peers/analysis/peer/scheduler.py`, replace `_run_one_batch`'s branching:
```python
def _run_one_batch(self, task: SamplingTask) -> None:
    key = task.key
    cond = self._conditions.get(key)
    try:
        with PEERS_SCHEDULER_BATCH_DURATION.time():
            task.run_batch()
    except Exception:  # noqa: BLE001 -- unchanged from today
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
            except Exception:  # noqa: BLE001 -- unchanged from today
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
```
(`_finalize` itself needs no change — it already takes a `status` string and is only reached from this one call site now.)

In `src/league_stats_peers/analysis/peer/baseline.py`, in `_on_task_interim`:
```python
def _on_task_interim(task: SamplingTask) -> None:
    confidence = "full" if task.reached_target else "low"
    snapshot = task.build_snapshot(confidence=confidence, still_refining=True)
    write_live_cache(task.client.platform, task.ranked.tier, task.champion, task.role, snapshot, patch=task.patch)
    _dispatch_progressive_listeners(task.key, snapshot)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_peer_sampling_scheduler.py tests/test_peer_baseline.py tests/test_peers_service.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/league_stats_peers/analysis/peer/scheduler.py src/league_stats_peers/analysis/peer/baseline.py tests/test_peer_sampling_scheduler.py tests/test_peer_baseline.py
git commit -m "feat: only exhaustion finalizes a SamplingTask; reaching target demotes to refining"
```

---

### Task 3: `WarmupTask` + `prewarm_tick` round-robin

**Files:**
- Create: `src/league_stats_peers/analysis/peer/warmup_task.py`
- Modify: `src/league_stats_peers/analysis/peer/sampling_task.py` (extract the shared download/ingest helper)
- Modify: `src/league_stats_peers/service.py`
- Test: `tests/test_peer_warmup_task.py` (new file)

**Interfaces:**
- Produces: `WarmupTask` (a `SamplingScheduler`-compatible task: `key`, `priority`, `run_batch()`, `reached_target`/`exhausted`/`done`/`reached_interim` properties — same duck-typed surface `SamplingTask` provides, since the scheduler only calls those). `prewarm_tick(scheduler, store, client_factory, patch) -> None`.

- [ ] **Step 1: Write the failing tests**

First, extract the shared piece: in `sampling_task.py`'s `run_batch`, the inner block from `match = _load_or_fetch_match(...)` through the `match_sample_store.upsert_rows(...)` call (lines ~255-277 in the current file) is the part every download needs regardless of champion/role targeting — downloading (or reading from cache) and populating the shared cross-champion cache. Factor it into a module-level function:
```python
def _download_and_share(
    client: RiotApiClient,
    store: Any,
    match_id: str,
    puuid: str,
    *,
    patch: str,
    exclude_puuid: str | None,
    match_sample_store: Any | None,
) -> dict[str, Any] | None:
    """Download (or read cached) one match, populate the shared cross-champion
    cache, and return the raw match doc -- or None on a fetch failure.

    Shared by SamplingTask.run_batch and WarmupTask.run_batch: both need
    exactly this (download, ingest into peer_games via _load_or_fetch_match's
    own ingest_match call, populate peer_match_samples) regardless of whether
    the caller is targeting one champion+role or none at all.
    """
    match = _load_or_fetch_match(client, store, match_id, puuid)
    if match is None:
        return None
    if match_sample_store is not None:
        all_rows = extract_all_champion_role_rows(match, exclude_puuid=exclude_puuid or "")
        if all_rows:
            try:
                match_sample_store.upsert_rows(match_id, patch, client.platform, all_rows)
            except Exception as exc:  # noqa: BLE001 -- see run_batch's own matching comment
                get_logger("sampling_task").warning("Shared match cache write failed: %s", exc)
    return match
```
Update `SamplingTask.run_batch` to call this instead of its inline equivalent (behavior must be identical — this is a pure extraction, verify via the existing `test_peer_sampling_scheduler.py`/`test_peer_sampling_task.py` suite staying green with no changes needed to those tests).

Then create `tests/test_peer_warmup_task.py`:
```python
"""Tests for WarmupTask, the champion/role-blind pre-warm sampler."""

from __future__ import annotations

from unittest.mock import MagicMock

from league_stats_peers.analysis.peer.warmup_task import WarmupTask
from league_stats_common.core.models import RankedEntry
from tests.fixtures import CombinedMatchAndPeerStore, make_match


def _client() -> MagicMock:
    client = MagicMock()
    client.configure_mock(platform="euw1")
    return client


def test_warmup_task_key_uses_prewarm_sentinel() -> None:
    task = WarmupTask(
        key=("euw1", "GOLD", "__prewarm__", "__prewarm__", "16.16"),
        client=_client(), store=CombinedMatchAndPeerStore(), tier="GOLD",
        patch="16.16", target_games=100,
    )
    assert task.key == ("euw1", "GOLD", "__prewarm__", "__prewarm__", "16.16")


def test_warmup_task_done_once_store_reports_enough_games(monkeypatch) -> None:
    store = CombinedMatchAndPeerStore()
    monkeypatch.setattr(store, "count_by_tier", lambda: {"GOLD": 100})
    task = WarmupTask(
        key=("euw1", "GOLD", "__prewarm__", "__prewarm__", "16.16"),
        client=_client(), store=store, tier="GOLD", patch="16.16", target_games=100,
    )
    assert task.exhausted is True
    assert task.done is True


def test_warmup_task_run_batch_downloads_without_champion_role_filtering(monkeypatch) -> None:
    """A WarmupTask must never call _match_has_build/extract_champion_role_rows
    -- every downloaded match counts, regardless of champion/role."""
    store = CombinedMatchAndPeerStore()
    monkeypatch.setattr(store, "count_by_tier", lambda: {"GOLD": 0})
    client = _client()
    client.fetch_league_entries_pages.return_value = [
        {"puuid": "seed-1", "tier": "GOLD", "rank": "II"}
    ]
    client.fetch_match_ids.return_value = ["EUW1_1"]
    client.fetch_match.return_value = make_match()
    task = WarmupTask(
        key=("euw1", "GOLD", "__prewarm__", "__prewarm__", "16.16"),
        client=client, store=store, tier="GOLD", patch="16.16", target_games=100,
    )
    task.run_batch()
    assert task.downloads >= 1
```

Adapt fixture/mock details (`CombinedMatchAndPeerStore`'s exact `count_by_tier` availability, `make_match()`'s shape) to what's real in `tests/fixtures.py` — read it first.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_peer_warmup_task.py -v`
Expected: FAIL — `warmup_task.py` doesn't exist yet.

- [ ] **Step 3: Write the implementation**

Create `src/league_stats_peers/analysis/peer/warmup_task.py`:
```python
"""`WarmupTask`: champion/role-blind idle-capacity pre-warming for one tier.

RFC "PEERS priority scheduling...", §3: a SamplingTask is keyed on
`(platform, tier, champion, role, patch)` and its stop condition is
champion+role-specific -- reusing it for pre-warm would mean one task per
(tier, champion, role) triple, up to 5 tiers x ~170 champions x 5 roles.
WarmupTask instead scans one tier's player pool once, downloading matches
champion/role-blind: role and champion coverage falls out for free, since
every downloaded match's participants are ingested into `peer_games`
(all 10, all roles/champions present) regardless of what this task is
"looking for" -- see `sampling_task._download_and_share`, which this task
shares with `SamplingTask.run_batch` for exactly this reason.

Keyed `(platform, tier, "__prewarm__", "__prewarm__", patch)` -- a sentinel
champion/role pair no real Riot champion name or `VALID_ROLES` member can
collide with, so it reuses `SamplingScheduler`'s existing `TaskKey`/
`get_or_create` dedup machinery unchanged: only one `WarmupTask` per
`(platform, tier, patch)` active at a time.

Always enqueued at `priority="background"` -- nothing ever calls
`wait_for_signal` for a WarmupTask key, so it only ever runs when both the
explicit and refining queues are empty (see `SamplingScheduler.step`).
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any, Final

from league_stats_peers.analysis.peer.benchmark_fetcher import _gather_seeds, _participant_puuids
from league_stats_peers.analysis.peer.rank_scope import RankScope
from league_stats_peers.analysis.peer.sampling_task import BATCH_SIZE, MATCH_IDS_PER_PLAYER, _download_and_share
from league_stats_common.core.config import RANKED_SOLO_QUEUE_ID
from league_stats_common.core.models import RankedEntry
from league_stats_common.infra.riot_api import RiotApiClient, RiotApiError
from league_stats_common.utils import get_logger

PREWARM_CHAMPION_SENTINEL: Final[str] = "__prewarm__"
PREWARM_ROLE_SENTINEL: Final[str] = "__prewarm__"

TaskKey = tuple[str, str, str, str, str]


@dataclass
class WarmupTask:
    """One tier's idle-capacity pre-warm scan, advanced one batch at a time."""

    key: TaskKey
    client: RiotApiClient
    store: Any
    tier: str
    patch: str
    target_games: int
    exclude_puuid: str | None = None
    match_sample_store: Any | None = None
    batch_size: int = field(default_factory=lambda: BATCH_SIZE)

    priority: str = "background"

    seen_for_snowball: set[str] = field(default_factory=set)
    seen_matches: set[str] = field(default_factory=set)
    queue: "deque[str]" = field(default_factory=deque)
    downloads: int = 0
    batches_run: int = 0

    _seeded: bool = field(default=False, repr=False)

    @property
    def games(self) -> int:
        """Present for scheduler/log-line parity with SamplingTask -- a
        WarmupTask's real "done" signal is the tier's store count, not this."""
        return self.downloads

    @property
    def reached_interim(self) -> bool:
        return False  # WarmupTask has no interim progressive-listener consumer

    @property
    def reached_target(self) -> bool:
        return False  # WarmupTask is never "full confidence" -- only exhausted

    @property
    def exhausted(self) -> bool:
        current = self.store.count_by_tier().get(self.tier, 0)
        if current >= self.target_games:
            return True
        return self._seeded and not self.queue

    @property
    def done(self) -> bool:
        return self.exhausted

    def _ensure_seeded(self) -> None:
        if self._seeded:
            return
        scope = RankScope(target=RankedEntry(tier=self.tier, rank="", league_points=0, wins=0, losses=0), widened=False)
        seed_puuids, _ranks = _gather_seeds(self.client, scope, self.exclude_puuid)
        self.queue.extend(p for p in seed_puuids if p not in self.seen_for_snowball)
        self._seeded = True

    def run_batch(self) -> None:
        log = get_logger("warmup_task")
        self._ensure_seeded()
        self.batches_run += 1
        if self.exhausted:
            return

        batch_downloads = 0
        while self.queue and batch_downloads < self.batch_size and not self.exhausted:
            puuid = self.queue.popleft()
            if puuid in self.seen_for_snowball:
                continue
            self.seen_for_snowball.add(puuid)
            try:
                match_ids = self.client.fetch_match_ids(
                    puuid, MATCH_IDS_PER_PLAYER, queue_id=RANKED_SOLO_QUEUE_ID
                )
            except RiotApiError as exc:
                log.debug("Skipping %s...: %s", puuid[:12], exc)
                continue
            for match_id in match_ids:
                if batch_downloads >= self.batch_size or self.exhausted:
                    break
                if match_id in self.seen_matches:
                    continue
                self.seen_matches.add(match_id)
                match = _download_and_share(
                    self.client, self.store, match_id, puuid,
                    patch=self.patch, exclude_puuid=self.exclude_puuid,
                    match_sample_store=self.match_sample_store,
                )
                if match is None:
                    continue
                self.downloads += 1
                batch_downloads += 1
                for other_puuid in _participant_puuids(match):
                    if other_puuid and other_puuid not in self.seen_for_snowball:
                        self.queue.append(other_puuid)
```

Then in `src/league_stats_peers/service.py`, add the coordinator (near wherever `PeersServicer.__init__`/its background threads are set up -- read that section first to match its existing thread-lifecycle pattern, e.g. how `SamplingScheduler.start()`/`stop()` are already invoked):
```python
PEERS_PREWARM_TARGET_GAMES_PER_TIER: Final[int] = 20_000
PEERS_PREWARM_TIERS: Final[tuple[str, ...]] = ("GOLD", "PLATINUM", "EMERALD", "DIAMOND", "MASTER")


def prewarm_tick(scheduler: SamplingScheduler, store: Any, client_factory, patch: str, tier_cursor: list[int]) -> None:
    """Enqueue one tier's WarmupTask if it's not already warm, advancing the
    round-robin cursor by one tier per call regardless of outcome (so a
    permanently-skipped tier doesn't starve the rest of the ring)."""
    coverage = store.count_by_tier()
    tier = PEERS_PREWARM_TIERS[tier_cursor[0] % len(PEERS_PREWARM_TIERS)]
    tier_cursor[0] += 1
    if coverage.get(tier, 0) >= PEERS_PREWARM_TARGET_GAMES_PER_TIER:
        return
    key = ("euw1", tier, "__prewarm__", "__prewarm__", patch)  # platform: see Step 3 note below
    if scheduler.is_active(key):
        return
    client = client_factory(key[0])
    scheduler.get_or_create(
        key,
        lambda: WarmupTask(
            key=key, client=client, store=store, tier=tier, patch=patch,
            target_games=PEERS_PREWARM_TARGET_GAMES_PER_TIER,
        ),
        priority="background",
    )
```
Note on platform: `RequestBaseline`'s existing platform resolution (`requested_platform or env_platform or self._default_platform`) is per-request; `prewarm_tick` has no request to resolve from. Use `self._default_platform` (whatever `PeersServicer.__init__` already resolves as its default) rather than hardcoding `"euw1"` above -- adapt the sketch to read it from wherever the servicer already stores it, and pass it into `prewarm_tick` as a parameter alongside `patch`.

Wire `prewarm_tick` to run periodically -- piggyback on the scheduler's own idle-time signal rather than a new standalone timer thread: check `SamplingScheduler._worker_loop`'s existing `if not self.step(): time.sleep(_IDLE_POLL_INTERVAL_S)` branch (the "nothing to do" case) and add a hook there (e.g. an optional `on_idle: Callable[[], None] | None` constructor param invoked once per empty `step()`, rate-limited by the coordinator itself, not the scheduler, to avoid calling `prewarm_tick` every single 0.05s idle poll -- e.g. only fire it once per N idle polls or once every few seconds via a timestamp check inside the closure passed as `on_idle`). Wire this closure from `PeersServicer.__init__` where the scheduler is constructed, passing `self._peer_store`/`self._riot_client_for`/patch resolution as needed -- read how `PeersServicer` currently resolves "the current patch" for other purposes (if it does at all; if not, resolving a patch for `prewarm_tick` may need its own small helper -- note this as a decision point and pick something reasonable, e.g. the patch of the most recently ingested match, or Data Dragon's current version truncated to major.minor, whichever this codebase already has a seam for).

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_peer_warmup_task.py tests/test_peer_sampling_task.py tests/test_peer_sampling_scheduler.py tests/test_peers_service.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/league_stats_peers/analysis/peer/warmup_task.py src/league_stats_peers/analysis/peer/sampling_task.py src/league_stats_peers/service.py tests/test_peer_warmup_task.py
git commit -m "feat: add WarmupTask + prewarm_tick round-robin for idle scheduler capacity"
```

---

### Task 4: Automatic patch-changeover cleanup

**Files:**
- Modify: `src/league_stats_peers/service.py`
- Test: `tests/test_peers_service.py`

**Interfaces:**
- Produces: an automatic check, run on the same idle-time coordinator hook as Task 3's `prewarm_tick`, that drops `peer_games`/`peer_match_samples`/`live_benchmark_cache` when the current Data Dragon patch no longer matches the most recently stored `peer_games` row's patch.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_peers_service.py`:
```python
def test_patch_changeover_drops_stale_peer_collections(monkeypatch) -> None:
    """A patch mismatch between Data Dragon's current version and the last
    stored peer_games row must drop peer_games/peer_match_samples/
    live_benchmark_cache and nothing else."""
    # seed peer_games with a row at patch "16.16", live_benchmark_cache and
    # peer_match_samples with at least one document each (reuse this file's
    # existing store-construction fixtures)
    # monkeypatch whatever function resolves "current DDragon version" to
    # return "16.17.1"
    # call the patch-changeover check function directly
    # assert all three collections are now empty
```

Adapt to real fixture/store-construction conventions in this test file.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_peers_service.py::test_patch_changeover_drops_stale_peer_collections -v`
Expected: FAIL — the function doesn't exist yet.

- [ ] **Step 3: Write the implementation**

In `src/league_stats_peers/service.py`, add:
```python
def _normalize_patch(version: str) -> str:
    """Truncate a Data Dragon version (e.g. '14.3.1') to major.minor, matching
    the patch format stored on peer_games rows (parser.py's own
    `".".join(version.split(".")[:2])` truncation)."""
    return ".".join(version.split(".")[:2])


def _current_ddragon_patch() -> str:
    """Fetch Data Dragon's current version, truncated to major.minor.

    A lightweight, standalone HTTP call -- deliberately not routed through
    DDragonAssets (that class manages a whole icon-cache directory PEERS has
    no reason to touch); reuses the same `{DDRAGON_BASE}/api/versions.json`
    endpoint DDragonAssets._fetch_latest_version already calls.
    """
    import requests
    from league_stats_common.infra.ddragon_assets import DDRAGON_BASE

    response = requests.get(f"{DDRAGON_BASE}/api/versions.json", timeout=15)
    response.raise_for_status()
    return _normalize_patch(str(response.json()[0]))


def check_and_apply_patch_changeover(peer_store: Any, mongo_client: Any, db_name: str) -> bool:
    """Drop peer_games/peer_match_samples/live_benchmark_cache if the current
    patch no longer matches the last-stored peer_games row's patch.

    Returns True if a drop happened. Fail-soft on a Data-Dragon fetch error
    (network blip) -- a missed check just means the drop happens on the next
    periodic call instead, not a crash of the idle-time coordinator loop.
    """
    log = get_logger("patch_changeover")
    try:
        current = _current_ddragon_patch()
    except Exception as exc:  # noqa: BLE001 -- fail-soft, see docstring
        log.warning("Could not resolve current Data Dragon patch: %s", exc)
        return False

    last_doc = peer_store._peer_games.find_one(
        {}, {"patch": 1}, sort=[("ingested_at", -1)]
    )
    # Note: verify `peer_games` actually has an `ingested_at` field to sort
    # by (check `ingest_match`/`upsert_peer_game`'s stored fields first --
    # if no such field exists, use whatever the collection's own natural
    # "most recent" signal is, e.g. its own insertion order via `$natural`,
    # and adjust this sort accordingly rather than assuming the field name).
    last_patch = str(last_doc.get("patch", "")) if last_doc else ""
    if not last_patch or last_patch == current:
        return False

    log.info("Patch changeover detected: %s -> %s, dropping peer collections", last_patch, current)
    db = mongo_client[db_name]
    for name in ("peer_games", "peer_match_samples", "live_benchmark_cache"):
        db.drop_collection(name)
    return True
```
Wire a call to `check_and_apply_patch_changeover` into the same idle-time coordinator hook Task 3 wired `prewarm_tick` into, rate-limited independently (e.g. once every few minutes, not every idle poll -- this is a cheap check but no reason to hammer Data Dragon's API on every 0.05s idle tick).

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_peers_service.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/league_stats_peers/service.py tests/test_peers_service.py
git commit -m "feat: automatic patch-changeover cleanup for peer_games/peer_match_samples/live_benchmark_cache"
```

---

### Task 5: Division-level rank scope

**Files:**
- Modify: `src/league_stats_peers/analysis/peer/rank_scope.py`
- Modify: `src/league_stats_peers/analysis/peer/baseline.py` (wire `build_division_scope` into level 0)
- Test: `tests/test_rank_scope.py` (or wherever `rank_scope.py`'s existing tests live -- grep first)

**Interfaces:**
- Produces: `division_ordinal(tier: str, division: str) -> int`, `RankScope.division_radius: int | None` (new field, default `None`), `build_division_scope(ranked: RankedEntry, radius: int = PEER_DIVISION_SCOPE_RADIUS) -> RankScope`.

- [ ] **Step 1: Write the failing tests**

```python
def test_division_ordinal_orders_the_full_ladder() -> None:
    assert division_ordinal("IRON", "IV") == 0
    assert division_ordinal("IRON", "I") == 3
    assert division_ordinal("BRONZE", "IV") == 4
    assert division_ordinal("EMERALD", "III") < division_ordinal("EMERALD", "II")
    assert division_ordinal("DIAMOND", "I") < division_ordinal("MASTER", "")
    assert division_ordinal("MASTER", "") < division_ordinal("GRANDMASTER", "")
    assert division_ordinal("GRANDMASTER", "") < division_ordinal("CHALLENGER", "")


def test_division_ordinal_radius_3_from_emerald_iii() -> None:
    """Confirmed by the repo owner: ±3 divisions from Emerald III spans
    Platinum II through Diamond IV (not Diamond I -- that would be +6)."""
    target = division_ordinal("EMERALD", "III")
    assert division_ordinal("PLATINUM", "II") == target - 3
    assert division_ordinal("DIAMOND", "IV") == target + 3


def test_rank_matches_with_division_radius_excludes_out_of_window_peers() -> None:
    scope = build_division_scope(RankedEntry(tier="EMERALD", rank="III", league_points=0, wins=0, losses=0))
    assert rank_matches("PLATINUM", "II", scope) is True   # -3, inside
    assert rank_matches("PLATINUM", "I", scope) is False   # -2... wait: adapt --
    # verify the exact boundary against division_ordinal directly rather than
    # asserting a value that might be off-by-one; compute expected inclusion
    # via division_ordinal(...) - target <= radius in the test itself.
    assert rank_matches("DIAMOND", "III", scope) is False  # +4, outside


def test_rank_matches_without_division_radius_is_unchanged() -> None:
    """division_radius=None (every existing call site) must reproduce
    today's tier-only behavior exactly -- no regression."""
    scope = build_widened_scope(RankedEntry(tier="EMERALD", rank="III", league_points=0, wins=0, losses=0))
    assert scope.division_radius is None
    assert rank_matches("EMERALD", "I", scope) is True
    assert rank_matches("PLATINUM", "IV", scope) is True  # adjacent tier, any division
```

Fix the boundary assertion in the third test by computing expected values from `division_ordinal` directly rather than hand-picking divisions, to avoid an off-by-one mistake in the test itself.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_rank_scope.py -k "division" -v` (adjust path to wherever these tests actually land)
Expected: FAIL — none of `division_ordinal`/`build_division_scope`/`RankScope.division_radius` exist yet.

- [ ] **Step 3: Write the implementation**

In `src/league_stats_peers/analysis/peer/rank_scope.py`:
```python
from league_stats_peers.analysis.peer.benchmarks import TIER_ORDER

PEER_DIVISION_SCOPE_RADIUS: Final[int] = 3

_NON_MASTER_TIER_ORDER: Final[tuple[str, ...]] = tuple(
    tier for tier in TIER_ORDER if tier not in MASTER_PLUS
)
_DIVISION_INDEX: Final[dict[str, int]] = {"IV": 0, "III": 1, "II": 2, "I": 3}


def division_ordinal(tier: str, division: str) -> int:
    """Ordinal position of (tier, division) on the promotion ladder.

    Master+ (no real divisions) get one synthetic slot each, stacked
    directly above Diamond I -- Master, then Grandmaster, then Challenger.
    """
    upper_tier = tier.upper()
    if upper_tier in MASTER_PLUS:
        diamond_i = (len(_NON_MASTER_TIER_ORDER) - 1) * 4 + _DIVISION_INDEX["I"]
        master_plus_order = ("MASTER", "GRANDMASTER", "CHALLENGER")
        return diamond_i + 1 + master_plus_order.index(upper_tier)
    tier_index = _NON_MASTER_TIER_ORDER.index(upper_tier)
    div_index = _DIVISION_INDEX[division.upper()]
    return tier_index * 4 + div_index
```

Update `RankScope`:
```python
@dataclass(frozen=True)
class RankScope:
    target: RankedEntry
    widened: bool
    extra_tiers: frozenset[str] = field(default_factory=frozenset)
    division_radius: int | None = None

    @property
    def allowed_tiers(self) -> set[str]:
        ...  # unchanged
```

Add `build_division_scope`:
```python
def build_division_scope(ranked: RankedEntry, radius: int = PEER_DIVISION_SCOPE_RADIUS) -> RankScope:
    """Same tier plus neighbors within `radius` divisions -- tighter and more
    precise than `build_widened_scope`'s whole-tier ±1. Used at fallback
    level 0, the tightest/most-relevant rung, where match quality matters
    most."""
    return RankScope(target=ranked, widened=True, division_radius=radius)
```
(`widened=True` here is a simplification: `allowed_tiers` doesn't matter once `division_radius` is set, since `rank_matches` below skips straight to the ordinal check for that case -- but keep `allowed_tiers`' existing tier-scope check as the FIRST cheap filter regardless, per the plan's note below, so a target several tiers away is rejected before ever computing an ordinal.)

Update `rank_matches`:
```python
def rank_matches(peer_tier: str, peer_rank: str, scope: RankScope) -> bool:
    tier = peer_tier.upper()
    if tier not in scope.allowed_tiers:
        return False
    if scope.division_radius is None:
        target_tier = scope.target.tier.upper()
        if tier in MASTER_PLUS and target_tier in MASTER_PLUS:
            if scope.widened or tier in scope.extra_tiers:
                return True
            return tier == target_tier
        if tier == target_tier:
            return True
        return scope.widened or tier in scope.extra_tiers
    peer_ordinal = division_ordinal(tier, peer_rank)
    target_ordinal = division_ordinal(scope.target.tier, scope.target.rank)
    return abs(peer_ordinal - target_ordinal) <= scope.division_radius
```
Note: with `division_radius` set, `scope.allowed_tiers` (via `widened=True`) already includes ±1 whole tier -- for a radius of 3 that's enough to cover every division-level neighbor (since 3 divisions can cross at most one tier boundary in each direction), but verify this holds for `radius > 4` if that's ever configured differently; for the confirmed `radius=3` default this is safe. If `build_division_scope` is ever called with a larger radius, `allowed_tiers`' ±1-tier cheap filter would incorrectly reject valid ordinal-distance peers more than one tier away -- widen `RankScope.allowed_tiers` to account for `division_radius` when radius could plausibly exceed 4, but this is out of scope for the confirmed radius=3 case; leave a comment noting the constraint rather than over-engineering for an untested larger radius.

In `src/league_stats_peers/analysis/peer/baseline.py`, in `resolve_peer_baseline`'s level-0 call (`_try_store_baseline(..., scope=build_exact_scope(ranked), ...)`), change the scope to `build_division_scope(ranked)` instead of `build_exact_scope(ranked)`. Leave levels 1/3 (`build_widened_scope`/`build_wider_scope`) unchanged -- those stay coarse, progressively-wider fallback rungs, per the RFC.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_rank_scope.py tests/test_peer_baseline.py -v`
Expected: all PASS. If level 0's scope change causes any pre-existing `test_peer_baseline.py` test to now match/reject a different set of rows than before (a level-0 test that previously relied on whole-tier matching), update its fixture ranks to fall inside/outside the new ±3-division window as intended, and note this in your final report -- it should be rare, since real test fixtures mostly use exact or clearly-adjacent ranks, not boundary cases.

- [ ] **Step 5: Commit**

```bash
git add src/league_stats_peers/analysis/peer/rank_scope.py src/league_stats_peers/analysis/peer/baseline.py tests/test_rank_scope.py
git commit -m "feat: division-level (±3) rank scope for fallback level 0"
```

---

### Task 6: Dashboard — show explicit vs. refining vs. pre-warm Riot API usage

**Files:**
- Modify: `src/league_stats_peers/analysis/peer/scheduler.py` (add a `priority` label)
- Modify: `deploy/grafana/dashboards/peers.json`
- Test: none (dashboard JSON has no test suite in this repo) -- verify by hand per Step 3.

**Interfaces:**
- Produces: `PEERS_SCHEDULER_BATCHES_TOTAL` gains a `priority` label (`explicit`/`refining`/`background`) alongside its existing `outcome` label, so Grafana can split by it.

- [ ] **Step 1: Add the label**

In `src/league_stats_peers/analysis/peer/scheduler.py`:
```python
PEERS_SCHEDULER_BATCHES_TOTAL = Counter(
    "peers_scheduler_batches_total",
    "SamplingTask/WarmupTask batches processed, by outcome and priority tier.",
    ["outcome", "priority"],  # outcome: re_enqueued | finalized_full | finalized_partial
                              # priority: explicit | refining | background
)
```
Update both increment call sites in `_run_one_batch`/`_finalize` to pass `priority=task.priority` alongside the existing `outcome=...` label:
```python
PEERS_SCHEDULER_BATCHES_TOTAL.labels(outcome="re_enqueued", priority=task.priority).inc()
...
PEERS_SCHEDULER_BATCHES_TOTAL.labels(outcome=f"finalized_{status}", priority=task.priority).inc()
```
Run `grep -rn "PEERS_SCHEDULER_BATCHES_TOTAL" tests/` and update any test asserting on this metric's label set to include `priority=...` in its `.labels(...)` call.

- [ ] **Step 2: Add the dashboard panel**

In `deploy/grafana/dashboards/peers.json`, add a new panel (model its `gridPos`/`type`/`datasource` fields on the existing "Sampling batches by outcome" panel right next to it -- read that panel's exact JSON structure first and copy its shape, changing only `title`/`targets`/a fresh unique `id`):
```json
{
  "title": "Riot API batches by priority tier",
  "type": "timeseries",
  "targets": [
    {
      "expr": "sum(rate(peers_scheduler_batches_total[5m])) by (priority)"
    }
  ]
}
```
Place it adjacent to the existing "Sampling batches by outcome" panel (same row or immediately below), incrementing `gridPos.y`/`id` past whatever the last panel in the file currently uses -- read the file's existing panel `id`/`gridPos` values first to avoid a collision.

- [ ] **Step 3: Verify by hand**

This repo has no dashboard-JSON test suite. Verify: `python3 -c "import json; json.load(open('deploy/grafana/dashboards/peers.json'))"` to confirm the file is still valid JSON after editing. If a local Grafana instance is reachable in your environment, additionally confirm the dashboard loads without a provisioning error; if not reachable, note in your final report that only JSON-validity was checked, not a live Grafana render.

- [ ] **Step 4: Run tests to verify no regression**

Run: `.venv/bin/python -m pytest tests/test_peer_sampling_scheduler.py tests/test_peers_service.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/league_stats_peers/analysis/peer/scheduler.py deploy/grafana/dashboards/peers.json tests/
git commit -m "feat: label scheduler batches by priority tier, add dashboard panel"
```

---

### Task 7: Full-suite verification

**Files:** none (verification-only task).

- [ ] **Step 1: Run the full test suite**

Run: `.venv/bin/python -m pytest -q`
Expected: all tests pass, zero failures. (A pre-existing, unrelated flake in `tests/test_peers_service.py::test_resolve_peer_baseline_via_live_sampling_survives_the_noop_store_methods` under `pytest-xdist` parallel load is a known issue from earlier work this session, not something this plan introduces -- if it's the ONLY failure and a serial re-run of just that test passes, note it in your report rather than treating it as a regression from this plan.)

- [ ] **Step 2: Grep for any remaining single-queue assumption**

Run: `grep -rn "scheduler\._queue\b" src/ tests/` and confirm zero real hits (only the three new `_explicit_queue`/`_refining_queue`/`_background_queue` names should remain anywhere in the codebase).

- [ ] **Step 3: Report the rollout note**

No commit for this task. Report to the user that this ships as a normal code deploy — no new volumes, no schema migration (the new `WarmupTask`/patch-changeover behavior is all in-memory/existing-collection). `docker-compose up -d` (rebuild + restart `peers`) is sufficient once merged. Flag explicitly that `PEERS_PREWARM_TARGET_GAMES_PER_TIER = 20_000` is a starting point per the RFC's own magnitude analysis (~1.5 days theoretical floor, realistically several days to a week against a ~14-day patch cycle) -- worth watching real throughput after deploy, not treating as final.
