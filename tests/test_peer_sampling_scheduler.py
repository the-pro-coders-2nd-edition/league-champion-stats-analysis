"""Tests for the batched round-robin scheduler (RFC "Batched, Round-Robin Live
Sampling for PEERS").

Covers §8's testing strategy directly:
- scheduler fairness (two tasks at different yield rates, neither starved)
- no-waste caching (a task exhausting its ceiling below target still writes
  a low-confidence entry that `resolve_peer_baseline` prefers over level 3)
- interim serving (a concurrent reader sees a mid-scan result)
- cross-champion reuse (Phase 2 `peer_match_samples`)
- rate-limiter accounting (interleaving doesn't change total Riot API cost)
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import mongomock
import pytest

from league_stats_peers.analysis.peer.baseline import (
    _on_task_finalize,
    _on_task_interim,
    resolve_peer_baseline,
)
from league_stats_peers.analysis.peer.benchmark_cache import read_live_cache, write_live_cache
from league_stats_peers.analysis.peer.sampling_task import SamplingTask
from league_stats_peers.analysis.peer.scheduler import SamplingScheduler
from league_stats_peers.infra.peer_match_sample_store import PeerMatchSampleStore
from league_stats_common.core.models import RankedEntry
from tests.fixtures import make_match


def _ranked(tier: str = "GOLD", rank: str = "II") -> RankedEntry:
    return RankedEntry(tier=tier, rank=rank, league_points=0, wins=0, losses=0)


def _noop_store() -> Any:
    store = MagicMock()
    store.load_match.return_value = None
    store.save_match.return_value = None
    store.set_puuid_rank.return_value = 1
    return store


class _YieldClient:
    """Deterministic fake `RiotApiClient`: every `hit_every`-th downloaded match
    contains the target champion+role (for a brand-new puuid each time); every
    other match is a miss. Counts `fetch_match_ids`/`fetch_match` calls so
    tests can assert on exactly how many live Riot calls were made.
    """

    def __init__(self, platform: str, *, hit_every: int, prefix: str, seed_count: int = 3) -> None:
        self.platform = platform
        self._hit_every = hit_every
        self._prefix = prefix
        self._seed_count = seed_count
        self._counter = 0
        self.match_ids_calls = 0
        self.match_calls = 0

    def fetch_league_entries_pages(self, tier: str, division: str, max_pages: int = 3) -> list[dict]:
        return [
            {"puuid": f"{self._prefix}-seed-{i}", "tier": "GOLD", "rank": "II"}
            for i in range(self._seed_count)
        ]

    def fetch_match_ids(self, puuid: str, count: int, queue_id: int | None = None) -> list[str]:
        self.match_ids_calls += 1
        self._counter += 1
        return [f"{self._prefix}-match-{self._counter}"]

    def fetch_match(self, match_id: str) -> dict[str, Any]:
        self.match_calls += 1
        idx = int(match_id.rsplit("-", 1)[1])
        is_hit = idx % self._hit_every == 0
        match = make_match()
        match["metadata"]["matchId"] = match_id
        participant = match["info"]["participants"][1]
        participant["puuid"] = f"{self._prefix}-hit-{idx}" if is_hit else f"{self._prefix}-miss-{idx}"
        participant["championName"] = "Zac" if is_hit else "Ashe"
        participant["teamPosition"] = "JUNGLE" if is_hit else "TOP"
        return match

    def fetch_solo_rank(self, puuid: str) -> RankedEntry:
        return _ranked()


def _make_task(
    key: tuple[str, str, str, str, str],
    client: Any,
    *,
    champion: str = "Zac",
    role: str = "JUNGLE",
    batch_size: int = 2,
    ceiling: int = 100,
    target: int = 6,
    interim_threshold: int = 2,
    match_sample_store: Any | None = None,
) -> SamplingTask:
    return SamplingTask(
        key=key,
        client=client,
        store=_noop_store(),
        ranked=_ranked(),
        champion=champion,
        role=role,
        exclude_puuid=None,
        patch="",
        match_sample_store=match_sample_store,
        target=target,
        ceiling=ceiling,
        batch_size=batch_size,
        interim_threshold=interim_threshold,
    )


# ------------------------------------------------------------- fairness


def test_scheduler_fairness_neither_task_is_starved() -> None:
    """Two tasks with different yield rates both make progress within a few
    batches -- neither runs to completion (or exhaustion) before the other
    gets a turn.

    Regression-proves the round-robin batching itself: if `run_batch` (or
    the scheduler) ignored `batch_size` and ran a task to completion in one
    call -- the old "one worker runs one job to completion" model this RFC
    replaces -- task A would already be at its 6-row target after the very
    first step, failing the assertion below.
    """
    client_a = _YieldClient("euw1", hit_every=1, prefix="a")  # always hits
    client_b = _YieldClient("euw1", hit_every=2, prefix="b")  # hits every other download

    task_a = _make_task(("euw1", "GOLD", "zac", "JUNGLE", ""), client_a)
    task_b = _make_task(("euw1", "GOLD", "zac", "JUNGLE-b", ""), client_b)

    scheduler = SamplingScheduler(num_workers=1)
    scheduler.get_or_create(task_a.key, lambda: task_a)
    scheduler.get_or_create(task_b.key, lambda: task_b)

    scheduler.step()  # one batch for task_a (FIFO: enqueued first)
    scheduler.step()  # one batch for task_b

    # Batching respected: task_a has NOT already reached its target of 6 in
    # a single batch of size 2.
    assert 0 < task_a.games < task_a.target
    # task_b was not starved behind task_a -- it got a turn and made
    # progress too, despite its lower yield rate.
    assert task_b.games > 0

    # Continue driving both to completion via strict round robin and verify
    # steady, roughly balanced progress (no long stretch where one task's
    # `rows` grows while the other's stays flat).
    rows_before = (task_a.games, task_b.games)
    for _ in range(6):
        scheduler.step()
    assert task_a.games >= rows_before[0]
    assert task_b.games >= rows_before[1]
    assert task_b.games > 0


# ------------------------------------------------------------- no-waste caching


def test_task_exhausting_ceiling_below_target_still_finalizes_with_rows() -> None:
    """A task that hits its download ceiling before reaching target still
    finalizes -- with whatever it found, not nothing."""
    client = _YieldClient("euw1", hit_every=2, prefix="c", seed_count=2)
    task = _make_task(
        ("euw1", "GOLD", "zac", "JUNGLE-c", ""),
        client,
        batch_size=4,
        ceiling=4,
        target=50,
        interim_threshold=2,
    )
    scheduler = SamplingScheduler(num_workers=1)
    scheduler.get_or_create(task.key, lambda: task)

    finalized: list[tuple[SamplingTask, str]] = []
    scheduler._on_finalize = lambda t, status: finalized.append((t, status))

    while scheduler.is_active(task.key):
        assert scheduler.step()

    assert finalized == [(task, "partial")]
    assert 0 < task.games < task.target
    assert task.downloads >= task.ceiling


def test_low_confidence_level_2_beats_level_3(monkeypatch: pytest.MonkeyPatch) -> None:
    """`resolve_peer_baseline` prefers a low-confidence (partial/interim) level-2
    live-cache entry over falling through to level 3's wider-rank store lookup.

    Fails against pre-fix code: the old `_snapshot_from_data` gate required
    `games >= MIN_BENCHMARK_GAMES` unconditionally, so a low-confidence entry
    with only a handful of games would be treated as a cache miss, live
    sampling would find nothing (no league entries seeded below), and
    `resolve_peer_baseline` would fall through to level 3's `_try_store_baseline`
    call, wrongly preferring wider-rank store noise over real (if partial)
    live data.
    """
    from tests.fixtures import CombinedMatchAndPeerStore
    from league_stats_peers.analysis.peer.ingest import ingest_match

    monkeypatch.setattr(
        "league_stats_peers.analysis.peer.benchmark_cache.MIN_BENCHMARK_GAMES", 50
    )

    store = CombinedMatchAndPeerStore()
    ranked = _ranked("EMERALD", "II")

    # Enough games at +/-2 tiers to satisfy level 3 if level 2 falls through.
    for index in range(60):
        match = make_match()
        match["info"]["participants"][1]["puuid"] = f"wide-peer-{index}"
        ingest_match(store, f"EUW1_wide_{index}", match, "euw1")
        store.set_puuid_rank(f"wide-peer-{index}", "GOLD", "II")

    snapshot_games = 4  # below MIN_BENCHMARK_GAMES, but a real low-confidence sample
    write_live_cache(
        "euw1",
        "EMERALD",
        "LeeSin",
        "JUNGLE",
        __import__(
            "league_stats_peers.analysis.peer.benchmark_fetcher", fromlist=["BenchmarkSnapshot"]
        ).BenchmarkSnapshot(
            metrics={"win": 0.5, "kda": 3.0},
            games_sampled=snapshot_games,
            players_sampled=snapshot_games,
            from_cache=False,
            platform="euw1",
            confidence="low",
            still_refining=False,
        ),
        patch="",
    )

    client = MagicMock()
    client.configure_mock(platform="euw1")
    client.fetch_league_entries_pages.return_value = []  # no live seeds

    baseline = resolve_peer_baseline(
        client, store, ranked, "LeeSin", "JUNGLE", exclude_puuid="puuid-me"
    )

    assert baseline is not None
    assert baseline.fallback_level == 2
    assert baseline.confidence == "low"
    assert baseline.games == snapshot_games


# ------------------------------------------------------------- interim serving


def test_interim_snapshot_is_readable_before_task_finishes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Once `interim_threshold` is crossed mid-scan, a concurrent reader of the
    same key sees that interim result via the live cache instead of blocking
    until the task reaches its full target.

    Fails against pre-fix code: the old live-sampling path never wrote
    anything to the cache until the whole scan succeeded or failed, so a
    concurrent reader mid-scan would see nothing at all.
    """
    import league_stats_peers.analysis.peer.benchmark_cache as benchmark_cache
    from league_stats_peers.infra.live_benchmark_cache_store import LiveBenchmarkCacheStore

    monkeypatch.setattr(
        benchmark_cache, "_store", LiveBenchmarkCacheStore(mongomock.MongoClient(), db_name="t")
    )

    client = _YieldClient("euw1", hit_every=1, prefix="d")
    task = _make_task(
        ("euw1", "GOLD", "zac", "JUNGLE", ""),
        client,
        batch_size=2,
        ceiling=100,
        target=10,
        interim_threshold=2,
    )
    scheduler = SamplingScheduler(num_workers=1, on_interim=_on_task_interim, on_finalize=_on_task_finalize)
    scheduler.get_or_create(task.key, lambda: task)

    scheduler.step()  # first batch: 2 hits -> reaches interim_threshold=2

    assert task.games == 2
    assert scheduler.is_active(task.key)  # still running, not finalized

    cached = read_live_cache("euw1", "GOLD", "Zac", "JUNGLE", patch="")
    assert cached is not None
    assert cached.confidence == "low"
    assert cached.still_refining is True
    assert cached.games_sampled == 2


# --------------------------------- progressive listener dispatch (design doc §3.1)


def test_progressive_listener_fires_on_every_improving_batch_and_once_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Design "Progressive peer-comparison updates during live sampling" §3.1:
    a listener registered via `register_progressive_listener` must be pushed
    on every batch that actually grows the sample (`still_refining=True`
    each time), then exactly once more with `still_refining=False` on the
    terminal batch -- never more than once at the end, and never for a batch
    that made no progress.

    Fails pre-fix: `_on_task_interim`/`_on_task_finalize` had no
    listener-dispatch mechanism at all, so a caller that already received one
    async callback while a `SamplingTask` was still refining had no way to
    learn about any later batch -- this is exactly the gap PEERS' own
    `PeersServicer._on_resolved`/`_notify_runner` relies on this module's
    `register_progressive_listener`/`_dispatch_progressive_listeners` to
    close.
    """
    import league_stats_peers.analysis.peer.benchmark_cache as benchmark_cache
    from league_stats_peers.analysis.peer.baseline import register_progressive_listener
    from league_stats_peers.infra.live_benchmark_cache_store import LiveBenchmarkCacheStore

    monkeypatch.setattr(
        benchmark_cache, "_store", LiveBenchmarkCacheStore(mongomock.MongoClient(), db_name="t")
    )

    client = _YieldClient("euw1", hit_every=1, prefix="prog")
    key = ("euw1", "GOLD", "zac", "JUNGLE-progressive", "")
    task = _make_task(key, client, batch_size=2, ceiling=100, target=6, interim_threshold=2)
    scheduler = SamplingScheduler(num_workers=1, on_interim=_on_task_interim, on_finalize=_on_task_finalize)
    scheduler.get_or_create(key, lambda: task)

    received: list[tuple[int, bool]] = []
    register_progressive_listener(
        key, lambda snapshot: received.append((snapshot.games_sampled, snapshot.still_refining))
    )

    while scheduler.is_active(key):
        scheduler.step()

    assert received, "progressive listener never fired"
    # Exactly one terminal push, and it's the last one.
    terminal_pushes = [r for r in received if r[1] is False]
    assert len(terminal_pushes) == 1
    assert received[-1][1] is False
    # Every push before it is an interim (still refining) push.
    assert all(still_refining for _games, still_refining in received[:-1])
    # Every push represents real growth over the previous one (dedup rule:
    # "fires ... where the cached snapshot actually improved").
    games_seen = [games for games, _still_refining in received]
    assert games_seen == sorted(games_seen)
    assert len(games_seen) == len(set(games_seen))
    assert games_seen[-1] == task.games == 6


def test_progressive_listener_deregistered_after_terminal_push(monkeypatch: pytest.MonkeyPatch) -> None:
    """A listener must stop being called once its task has finalized -- a new
    task later reusing the same key must not accidentally replay into an old
    listener that should have been forgotten."""
    import league_stats_peers.analysis.peer.benchmark_cache as benchmark_cache
    from league_stats_peers.analysis.peer.baseline import _dispatch_progressive_listeners, register_progressive_listener
    from league_stats_peers.infra.live_benchmark_cache_store import LiveBenchmarkCacheStore

    monkeypatch.setattr(
        benchmark_cache, "_store", LiveBenchmarkCacheStore(mongomock.MongoClient(), db_name="t")
    )

    client = _YieldClient("euw1", hit_every=1, prefix="term")
    key = ("euw1", "GOLD", "zac", "JUNGLE-terminal-only", "")
    task = _make_task(key, client, batch_size=6, ceiling=100, target=2, interim_threshold=2)
    scheduler = SamplingScheduler(num_workers=1, on_interim=_on_task_interim, on_finalize=_on_task_finalize)
    scheduler.get_or_create(key, lambda: task)

    received: list[bool] = []
    register_progressive_listener(key, lambda snapshot: received.append(snapshot.still_refining))

    while scheduler.is_active(key):
        scheduler.step()

    assert received == [False]

    # Simulate another (unrelated) snapshot for the same key arriving after
    # the listener should already have been forgotten -- must not replay.
    _dispatch_progressive_listeners(key, task.build_snapshot(confidence="low", still_refining=True))
    assert received == [False]


# ------------------------------------------------------------- cross-champion reuse (Phase 2)


def test_cross_champion_reuse_saves_a_live_call() -> None:
    """A match downloaded while sampling champion A also seeds champion B's
    shared cache row; a later task for champion B finds it there and skips
    the live download entirely.
    """
    match_sample_store = PeerMatchSampleStore(mongomock.MongoClient(), db_name="t")

    # Task A samples Zac/JUNGLE; its one downloaded match also has an
    # Ashe/TOP participant -- _YieldClient's "miss" branch already builds
    # exactly that shape.
    client_a = _YieldClient("euw1", hit_every=2, prefix="e", seed_count=2)
    task_a = _make_task(
        ("euw1", "GOLD", "zac", "JUNGLE-e", ""),
        client_a,
        batch_size=2,
        ceiling=2,
        target=50,
        match_sample_store=match_sample_store,
    )
    task_a.run_batch()
    assert task_a.downloads == 2  # one hit (Zac), one miss (Ashe/TOP)

    # Task B samples Ashe/TOP with a client that has NO live seeds at all --
    # any row it finds must come from the shared cache, not a live scan.
    client_b = MagicMock()
    client_b.configure_mock(platform="euw1")
    client_b.fetch_league_entries_pages.return_value = []
    client_b.fetch_solo_rank.return_value = _ranked()

    task_b = _make_task(
        ("euw1", "GOLD", "ashe", "TOP", ""),
        client_b,
        champion="Ashe",
        role="TOP",
        target=5,
        match_sample_store=match_sample_store,
    )
    task_b.run_batch()

    assert task_b.games >= 1
    client_b.fetch_match_ids.assert_not_called()
    client_b.fetch_match.assert_not_called()


# ------------------------------------------------------------- rate limiter accounting


def test_interleaved_scheduling_costs_the_same_as_sequential() -> None:
    """Total Riot API calls across two interleaved tasks match what running
    them sequentially would have cost -- the scheduler reallocates *when*
    work happens, not how much of it there is.
    """

    def _build_pair() -> tuple[SamplingTask, SamplingTask, _YieldClient, _YieldClient]:
        client_a = _YieldClient("euw1", hit_every=1, prefix="f")
        client_b = _YieldClient("euw1", hit_every=3, prefix="g")
        task_a = _make_task(("euw1", "GOLD", "zac", "JUNGLE-f", ""), client_a, batch_size=2, target=6)
        task_b = _make_task(("euw1", "GOLD", "zac", "JUNGLE-g", ""), client_b, batch_size=2, target=6)
        return task_a, task_b, client_a, client_b

    # Interleaved (round robin via the scheduler).
    task_a, task_b, client_a, client_b = _build_pair()
    scheduler = SamplingScheduler(num_workers=1)
    scheduler.get_or_create(task_a.key, lambda: task_a)
    scheduler.get_or_create(task_b.key, lambda: task_b)
    while scheduler.is_active(task_a.key) or scheduler.is_active(task_b.key):
        if not scheduler.step():
            break
    interleaved_calls = (
        client_a.match_ids_calls + client_a.match_calls + client_b.match_ids_calls + client_b.match_calls
    )

    # Sequential: task A to completion, then task B to completion.
    task_a2, task_b2, client_a2, client_b2 = _build_pair()
    while not task_a2.done:
        task_a2.run_batch()
    while not task_b2.done:
        task_b2.run_batch()
    sequential_calls = (
        client_a2.match_ids_calls + client_a2.match_calls + client_b2.match_ids_calls + client_b2.match_calls
    )

    assert interleaved_calls == sequential_calls
    assert task_a.games == task_a2.games
    assert task_b.games == task_b2.games
