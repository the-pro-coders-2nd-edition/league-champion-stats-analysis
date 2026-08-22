"""Resolve peer baselines from store, live sampling, and static fallbacks."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Final

from prometheus_client import Counter

from league_stats_peers.analysis.peer.benchmark_cache import read_live_cache, write_live_cache
from league_stats_peers.analysis.peer.benchmark_fetcher import BenchmarkSnapshot
from league_stats_peers.analysis.peer.benchmarks import try_role_benchmark, try_static_benchmark
from league_stats_peers.analysis.peer.cache import (
    PeerSample,
    aggregate_peer_metrics,
    collect_peer_games_from_store,
    peer_metric_quantiles,
)
from league_stats_peers.analysis.peer.rank_scope import RankScope, build_exact_scope, build_wider_scope, build_widened_scope
from league_stats_peers.analysis.peer.sampling_task import SamplingTask, TaskKey
from league_stats_peers.analysis.peer.scheduler import SamplingScheduler
from league_stats_common.core.champions import build_label
from league_stats_common.core.models import RankedEntry
from league_stats_common.core.progress import NULL_REPORTER, ProgressReporter
from league_stats_common.infra.riot_api import RiotApiClient, RiotApiError
from league_stats_common.utils import get_logger

TARGET_PEER_GAMES: Final[int] = 50
MIN_EXACT_GAMES: Final[int] = 50
MIN_WIDENED_GAMES: Final[int] = 50
MIN_LIVE_GAMES: Final[int] = 50
# Exact-rank store confidence is "high" once we have this many games.
HIGH_CONFIDENCE_GAMES: Final[int] = 100
# Defense-in-depth ceiling on how long a worker thread waits on a SamplingTask
# to reach its interim threshold or finalize -- see `_try_live_baseline`.
LIVE_SAMPLING_WAIT_TIMEOUT_S: Final[float] = 30.0


def task_key_for(platform: str, tier: str, champion: str, role: str, patch: str) -> TaskKey:
    """Public alias of `_task_key` -- used by `peers/service.py` to key its own
    progressive-notification registry against the exact same `SamplingTask`
    key the scheduler uses (see `register_progressive_listener`).
    """
    return _task_key(platform, tier, champion, role, patch)


# Design "Progressive peer-comparison updates during live sampling" §3.1: a
# request that fell through to live sampling and outlived PEERS' own
# fast-path timeout gets ONE `NotifyPeerBaselineReady` callback today, fired
# once by `PeersServicer._on_resolved` when its own `resolve_peer_baseline`
# call returns (after waiting at most `LIVE_SAMPLING_WAIT_TIMEOUT_S` for the
# first interim/finalize signal) -- even though the underlying `SamplingTask`
# keeps improving in the background across many more scheduler batches.
# `register_progressive_listener` lets `service.py` attach a callback that
# instead keeps firing for every later interim/finalize snapshot of the same
# `SamplingTask`, for as long as it stays `still_refining`. Deliberately a
# plain callback registry here (not gRPC-aware) so this module stays free of
# any gRPC/proto dependency -- `service.py` supplies the closure that turns a
# `BenchmarkSnapshot` into a real `NotifyPeerBaselineReady` call.
_progressive_listeners: dict[TaskKey, list["Callable[[BenchmarkSnapshot], None]"]] = {}
_progressive_listeners_lock = threading.Lock()
# Last (games, confidence) a listener set was actually notified with, per key
# -- lets `_dispatch_progressive_listeners` skip a re-enqueued batch that made
# no progress (RFC §3.1: "fires ... where the cached snapshot actually
# improved (games increased, or confidence changed)", not on every batch
# unconditionally). Cleared together with the listener list on finalize.
_last_notified_state: dict[TaskKey, tuple[int, str]] = {}


def register_progressive_listener(
    key: TaskKey, callback: "Callable[[BenchmarkSnapshot], None]"
) -> None:
    """Run `callback` on every future interim/finalize snapshot for `key`.

    Automatically forgotten once a terminal (`still_refining=False`) snapshot
    for `key` is dispatched -- a finalized `SamplingTask` never produces
    another snapshot, so there is nothing left to listen for.
    """
    with _progressive_listeners_lock:
        _progressive_listeners.setdefault(key, []).append(callback)


def _dispatch_progressive_listeners(key: TaskKey, snapshot: BenchmarkSnapshot) -> None:
    """Notify any listeners registered for `key`, honoring the "only on real
    improvement" rule for interim snapshots (terminal snapshots always fire,
    even if the sample didn't grow, so a caller still learns refinement has
    stopped)."""
    terminal = not snapshot.still_refining
    with _progressive_listeners_lock:
        if terminal:
            callbacks = list(_progressive_listeners.pop(key, ()))
            _last_notified_state.pop(key, None)
        else:
            state = (snapshot.games_sampled, snapshot.confidence)
            if _last_notified_state.get(key) == state:
                return
            _last_notified_state[key] = state
            callbacks = list(_progressive_listeners.get(key, ()))
    log = get_logger("peer_baseline")
    for callback in callbacks:
        try:
            callback(snapshot)
        except Exception:  # noqa: BLE001 -- one broken listener must never
            # break the scheduler's on_interim/on_finalize hook for the other
            # listeners, or for the cache write that already happened above.
            log.exception("Progressive peer-baseline listener failed for key=%s", key)


def _on_task_interim(task: SamplingTask) -> None:
    """Scheduler hook: write the task's current aggregate as an interim cache entry.

    RFC §5.1.2: called once per batch once `interim_threshold` is crossed, so
    the entry keeps improving for whoever reads it next, without a second
    notification to the original caller. Design "Progressive peer-comparison
    updates during live sampling" §3.1 adds `_dispatch_progressive_listeners`
    on top: any request that already got one async callback while this task
    was still refining gets another push here too, whenever the sample
    actually grew.
    """
    snapshot = task.build_snapshot(confidence="low", still_refining=True)
    write_live_cache(task.client.platform, task.ranked.tier, task.champion, task.role, snapshot, patch=task.patch)
    _dispatch_progressive_listeners(task.key, snapshot)


def _on_task_finalize(task: SamplingTask, status: str) -> None:
    """Scheduler hook: write the task's final result -- full or partial (RFC §5.1.3).

    Always writes, even below `target` -- the whole point of "no-waste
    caching" is that a task exhausting its ceiling short of 50 games still
    persists whatever it found instead of discarding it. Also dispatches the
    terminal (`still_refining=False`) progressive-listener push -- see
    `_dispatch_progressive_listeners`.
    """
    confidence = "full" if status == "full" else "low"
    snapshot = task.build_snapshot(confidence=confidence, still_refining=False)
    write_live_cache(task.client.platform, task.ranked.tier, task.champion, task.role, snapshot, patch=task.patch)
    _dispatch_progressive_listeners(task.key, snapshot)


# Process-wide singleton (RFC §5.1: "single in-process queue -- safe given the
# confirmed single-replica deployment"). Built lazily so importing this module
# never spins up worker threads, and tests can inject their own scheduler via
# `resolve_peer_baseline`'s `scheduler` keyword instead of touching this.
_default_scheduler: SamplingScheduler | None = None


def _get_default_scheduler() -> SamplingScheduler:
    global _default_scheduler
    if _default_scheduler is None:
        import os

        num_workers = int(os.environ.get("PEERS_MAX_CONCURRENT_BASELINES", "4"))
        _default_scheduler = SamplingScheduler(
            num_workers=num_workers, on_interim=_on_task_interim, on_finalize=_on_task_finalize
        )
    return _default_scheduler

# Resolution mix visibility: which rung of the fallback ladder (0/1/3/4/5 all
# local reads, cost-equivalent; 2 is live Riot sampling -- see `source` on
# PEERS_BASELINE_RESOLUTION_DURATION for that timing-relevant split instead)
# actually answered each request. A fixed, closed 6-value enum
# (`fallback_level`, see `resolve_peer_baseline`'s docstring) -- safe to label
# by, and lets a store-coverage regression (more requests falling through to
# higher levels) show up before it becomes a user-visible latency spike.
PEERS_BASELINE_RESOLUTIONS_BY_LEVEL_TOTAL = Counter(
    "peers_baseline_resolutions_by_level_total",
    "Resolved peer baselines, labeled by which fallback_level answered the request.",
    ["fallback_level"],
)


@dataclass(frozen=True)
class PeerBaseline:
    """Resolved peer baseline for rank comparison."""

    metrics: dict[str, float]
    games: int
    players: int
    source: str
    confidence: str
    fallback_level: int
    metrics_p50: dict[str, float] = field(default_factory=dict)
    metrics_p75: dict[str, float] = field(default_factory=dict)
    # Mirrors `BenchmarkSnapshot.still_refining` (design "Progressive
    # peer-comparison updates during live sampling" §3.1): True only for an
    # interim level-2 result whose `SamplingTask` is still running in the
    # background. Defaults False for every other level (store/static levels
    # resolve once, synchronously, with no further updates ever coming).
    still_refining: bool = False


def _baseline_from_sample(sample: PeerSample, *, level: int, confidence: str) -> PeerBaseline:
    """Build a baseline object from collected peer rows."""
    metrics = aggregate_peer_metrics(sample.rows)
    if "win" not in metrics and "winrate" in metrics:
        metrics = {**metrics, "win": float(metrics["winrate"])}
    return PeerBaseline(
        metrics=metrics,
        games=sample.games,
        players=sample.players,
        source=sample.source,
        confidence=confidence,
        fallback_level=level,
        metrics_p50=peer_metric_quantiles(sample.rows, 0.5),
        metrics_p75=peer_metric_quantiles(sample.rows, 0.75),
    )


def _try_store_baseline(
    store: Any,
    client: RiotApiClient,
    ranked: RankedEntry,
    champion: str,
    role: str,
    *,
    scope: RankScope,
    exclude_puuid: str,
    min_games: int,
    level: int,
    confidence: str,
    source_label: str,
    patch: str = "",
    high_confidence_threshold: int = 0,
) -> PeerBaseline | None:
    """Return a store-backed baseline when enough games exist.

    When ``high_confidence_threshold`` is set, the confidence is upgraded to
    ``"high"`` once the sample reaches that size (graduated confidence).
    """
    sample = collect_peer_games_from_store(
        store,
        champion=champion,
        role=role,
        platform=client.platform,
        scope=scope,
        exclude_puuid=exclude_puuid,
        client=client,
        patch=patch,
        min_games=min_games,
    )
    if sample.games < min_games:
        return None
    effective_confidence = confidence
    if high_confidence_threshold and sample.games >= high_confidence_threshold:
        effective_confidence = "high"
    sample = PeerSample(
        rows=sample.rows,
        games=sample.games,
        players=sample.players,
        source=source_label,
    )
    return _baseline_from_sample(sample, level=level, confidence=effective_confidence)


def _baseline_from_snapshot(
    snapshot: BenchmarkSnapshot, champion: str, role: str, *, level: int
) -> PeerBaseline:
    """Wrap a BenchmarkSnapshot in a PeerBaseline.

    A ``confidence="low"`` snapshot (an interim or ceiling-exhausted-partial
    result, RFC §5.1.3) is surfaced as ``confidence="low"`` on the
    `PeerBaseline` too, and its source string says so -- callers should not
    mistake a partial 11-game sample for the usual 50+-game "medium"
    confidence live result.
    """
    metrics = snapshot.metrics
    if "win" not in metrics and "winrate" in metrics:
        metrics = {**metrics, "win": float(metrics["winrate"])}
    is_low = snapshot.confidence == "low"
    qualifier = ""
    if is_low:
        qualifier = " (still refining)" if snapshot.still_refining else " (partial -- below target)"
    return PeerBaseline(
        metrics=metrics,
        games=snapshot.games_sampled,
        players=snapshot.players_sampled,
        source=(
            f"{'Cached' if snapshot.from_cache else 'Live API'} sample: "
            f"{snapshot.games_sampled} ranked solo games "
            f"from {snapshot.players_sampled} players on {build_label(champion, role)}{qualifier}."
        ),
        confidence="low" if is_low else "medium",
        fallback_level=level,
        metrics_p50=dict(snapshot.metrics_p50),
        metrics_p75=dict(snapshot.metrics_p75),
        still_refining=snapshot.still_refining,
    )


def _task_key(platform: str, tier: str, champion: str, role: str, patch: str) -> TaskKey:
    return (platform.lower(), tier.upper(), champion.lower(), role.upper(), patch)


def _enqueue_sampling_task(
    scheduler: SamplingScheduler,
    client: RiotApiClient,
    store: Any,
    ranked: RankedEntry,
    champion: str,
    role: str,
    *,
    exclude_puuid: str | None,
    patch: str,
    match_sample_store: Any | None,
) -> TaskKey:
    """Get-or-create the `SamplingTask` for this key on `scheduler` and start it running."""
    key = _task_key(client.platform, ranked.tier, champion, role, patch)

    def _factory() -> SamplingTask:
        return SamplingTask(
            key=key,
            client=client,
            store=store,
            ranked=ranked,
            champion=champion,
            role=role,
            exclude_puuid=exclude_puuid,
            patch=patch,
            match_sample_store=match_sample_store,
        )

    scheduler.get_or_create(key, _factory)
    scheduler.start()
    return key


def _try_live_baseline(
    client: RiotApiClient,
    store: Any,
    ranked: RankedEntry,
    champion: str,
    role: str,
    *,
    exclude_puuid: str | None,
    patch: str = "",
    progress: ProgressReporter = NULL_REPORTER,
    match_sample_store: Any | None = None,
    scheduler: SamplingScheduler | None = None,
) -> PeerBaseline | None:
    """Return a peer baseline from the Mongo-backed live cache, or a batched live scan.

    RFC "Batched, Round-Robin Live Sampling for PEERS": live sampling no
    longer runs a whole scan to completion on this call. Instead it attaches
    to (or creates) a `SamplingTask` on the shared `SamplingScheduler`, whose
    batch-workers advance it (and every other active task) round-robin, and
    blocks only until that task reaches its interim threshold or finalizes
    (RFC §5.1.2) -- both of which write the result to the live cache, which
    is then re-read here. A cache hit already marked ``still_refining`` still
    has a `SamplingTask` (re-)enqueued in the background to keep improving it
    for the *next* reader (RFC §6), without making this caller wait for that.
    """
    log = get_logger("peer_baseline")
    active_scheduler = scheduler or _get_default_scheduler()

    cached = read_live_cache(client.platform, ranked.tier, champion, role, patch=patch)
    if cached is not None:
        log.info(
            "Live cache hit for %s %s (platform=%s, tier=%s): %d games, confidence=%s, "
            "still_refining=%s",
            champion,
            role,
            client.platform,
            ranked.tier,
            cached.games_sampled,
            cached.confidence,
            cached.still_refining,
        )
        # RFC §6: "a low-confidence hit could itself enqueue a SamplingTask to
        # keep improving." Deliberately scoped to `still_refining` (an interim
        # snapshot, mid-scan) rather than every `confidence="low"` hit: an
        # already ceiling-exhausted partial result finalized because a full
        # scan already spent its 1000-download budget without reaching
        # target, so re-attaching a fresh task on every subsequent cache read
        # within the 3-day TTL would repeatedly re-spend that same budget for
        # a build that's already shown itself to be low-yield, worsening the
        # exact "one job hogs the shared rate limit" problem this RFC exists
        # to fix. It still gets cheaper over time via Phase 2's shared cache
        # (RFC §7) the next time this key's cache entry goes stale and a new
        # request re-triggers a task from scratch -- just not on every read.
        if cached.still_refining:
            _enqueue_sampling_task(
                active_scheduler,
                client,
                store,
                ranked,
                champion,
                role,
                exclude_puuid=exclude_puuid,
                patch=patch,
                match_sample_store=match_sample_store,
            )
        return _baseline_from_snapshot(cached, champion, role, level=2)

    key = _enqueue_sampling_task(
        active_scheduler,
        client,
        store,
        ranked,
        champion,
        role,
        exclude_puuid=exclude_puuid,
        patch=patch,
        match_sample_store=match_sample_store,
    )
    # Defense in depth, not the primary bound: `SamplingTask`/`SamplingScheduler`
    # are designed to always finalize a key eventually (target, ceiling, or a
    # stalled/empty snowball queue -- see `SamplingTask.exhausted`), and the
    # scheduler itself guarantees a waiter is released even if a batch or a
    # cache-write hook raises unexpectedly. This ceiling exists purely so an
    # entirely unanticipated future bug degrades to "falls through the rest
    # of the fallback ladder" within a bounded time instead of hanging this
    # worker thread (and the PEERS executor slot it occupies) forever.
    active_scheduler.wait_for_signal(key, timeout=LIVE_SAMPLING_WAIT_TIMEOUT_S)

    cached = read_live_cache(client.platform, ranked.tier, champion, role, patch=patch)
    if cached is None:
        return None
    return _baseline_from_snapshot(cached, champion, role, level=2)


def resolve_peer_baseline(
    client: RiotApiClient,
    store: Any,
    ranked: RankedEntry,
    champion: str,
    role: str,
    *,
    exclude_puuid: str | None = None,
    patch: str = "",
    progress: ProgressReporter = NULL_REPORTER,
    match_sample_store: Any | None = None,
    scheduler: SamplingScheduler | None = None,
) -> PeerBaseline | None:
    """Resolve the best available peer baseline using the fallback ladder.

    Levels:
    0 — Peer store, exact rank, ≥50 games (high confidence at ≥100)
    1 — Peer store, ±1 widened rank, ≥50 games
    2 — Mongo-backed live cache (full or low-confidence/interim), or a batched
        live snowball scan via `SamplingScheduler` (cache reused only on the
        same patch and tier, and within its TTL). A low-confidence level-2
        result (an interim or ceiling-exhausted-partial sample, RFC "Batched,
        Round-Robin Live Sampling for PEERS" §5.1.3) is still preferred over
        falling through to level 3 -- real partial data beats a wider-rank
        guess.
    3 — Peer store, ±2 wider rank, ≥50 games (only reached when level 2 has
        zero live data at all)
    4 — Static champion JSON
    5 — Static role JSON

    ``match_sample_store``/``scheduler`` are optional injection points for the
    Phase 2 shared cross-champion match cache and the batch scheduler,
    respectively -- both default to production singletons when omitted (see
    `PeersServicer` for how they're wired in `service.py`).
    """
    import time

    log = get_logger("peer_baseline")
    label = build_label(champion, role)
    exclude = exclude_puuid or ""
    t0 = time.monotonic()

    baseline = _try_store_baseline(
        store,
        client,
        ranked,
        champion,
        role,
        scope=build_exact_scope(ranked),
        exclude_puuid=exclude,
        min_games=MIN_EXACT_GAMES,
        level=0,
        confidence="medium",
        patch=patch,
        high_confidence_threshold=HIGH_CONFIDENCE_GAMES,
        source_label=(
            f"Peer store: {label} at {ranked.label} "
            f"({TARGET_PEER_GAMES}+ game target)."
        ),
    )
    if baseline is not None:
        PEERS_BASELINE_RESOLUTIONS_BY_LEVEL_TOTAL.labels(fallback_level="0").inc()
        log.info(
            "Resolved peer baseline for %s on %s at %s: level=0, games=%d, source=store, took=%.1fs",
            label,
            client.platform,
            ranked.label,
            baseline.games,
            time.monotonic() - t0,
        )
        return replace(
            baseline,
            source=(
                f"Peer store: {baseline.games} {label} games at {ranked.label} "
                f"from {baseline.players} players."
            ),
        )

    baseline = _try_store_baseline(
        store,
        client,
        ranked,
        champion,
        role,
        scope=build_widened_scope(ranked),
        exclude_puuid=exclude,
        min_games=MIN_WIDENED_GAMES,
        level=1,
        confidence="medium",
        patch=patch,
        source_label=f"Peer store (widened rank): {label}.",
    )
    if baseline is not None:
        PEERS_BASELINE_RESOLUTIONS_BY_LEVEL_TOTAL.labels(fallback_level="1").inc()
        log.info(
            "Resolved peer baseline for %s on %s at %s: level=1, games=%d, source=store (widened), "
            "took=%.1fs",
            label,
            client.platform,
            ranked.label,
            baseline.games,
            time.monotonic() - t0,
        )
        return replace(
            baseline,
            source=(
                f"Peer store (widened rank): {baseline.games} {label} games "
                f"from {baseline.players} players near {ranked.label}."
            ),
        )

    log.info(
        "Falling through to live sampling (level=2) for %s on %s at %s: no store baseline yet",
        label,
        client.platform,
        ranked.label,
    )
    try:
        baseline = _try_live_baseline(
            client,
            store,
            ranked,
            champion,
            role,
            exclude_puuid=exclude_puuid,
            patch=patch,
            progress=progress,
            match_sample_store=match_sample_store,
            scheduler=scheduler,
        )
    except RiotApiError as exc:
        log.warning("Live peer sampling failed: %s", exc)
        baseline = None
    if baseline is not None:
        PEERS_BASELINE_RESOLUTIONS_BY_LEVEL_TOTAL.labels(fallback_level="2").inc()
        log.info(
            "Resolved peer baseline for %s on %s at %s: level=2, games=%d, source=live/cache, "
            "took=%.1fs",
            label,
            client.platform,
            ranked.label,
            baseline.games,
            time.monotonic() - t0,
        )
        return baseline

    # After the live attempt the store may have been populated; try ±2 tiers
    # before falling back to static benchmarks (still requires 50 games).
    baseline = _try_store_baseline(
        store,
        client,
        ranked,
        champion,
        role,
        scope=build_wider_scope(ranked),
        exclude_puuid=exclude,
        min_games=MIN_WIDENED_GAMES,
        level=3,
        confidence="medium",
        patch=patch,
        source_label=f"Peer store (wider rank ±2 tiers): {label}.",
    )
    if baseline is not None:
        PEERS_BASELINE_RESOLUTIONS_BY_LEVEL_TOTAL.labels(fallback_level="3").inc()
        log.info(
            "Resolved peer baseline for %s on %s at %s: level=3, games=%d, source=store (wider "
            "±2), took=%.1fs",
            label,
            client.platform,
            ranked.label,
            baseline.games,
            time.monotonic() - t0,
        )
        return replace(
            baseline,
            source=(
                f"Peer store (±2 tier range): {baseline.games} {label} games "
                f"from {baseline.players} players near {ranked.label}."
            ),
        )

    static = try_static_benchmark(ranked.tier, champion, role)
    if static is not None:
        PEERS_BASELINE_RESOLUTIONS_BY_LEVEL_TOTAL.labels(fallback_level="4").inc()
        log.info(
            "Resolved peer baseline for %s on %s at %s: level=4, source=static champion JSON, "
            "took=%.1fs",
            label,
            client.platform,
            ranked.label,
            time.monotonic() - t0,
        )
        return PeerBaseline(
            metrics=static,
            games=0,
            players=0,
            source=f"Static champion benchmark for {label} at {ranked.label}.",
            confidence="low",
            fallback_level=4,
        )

    role_static = try_role_benchmark(ranked.tier, role)
    if role_static is not None:
        PEERS_BASELINE_RESOLUTIONS_BY_LEVEL_TOTAL.labels(fallback_level="5").inc()
        log.info(
            "Resolved peer baseline for %s on %s at %s: level=5, source=static role JSON, "
            "took=%.1fs",
            label,
            client.platform,
            ranked.label,
            time.monotonic() - t0,
        )
        return PeerBaseline(
            metrics=role_static,
            games=0,
            players=0,
            source=f"Static role benchmark for {label} lane at {ranked.label}.",
            confidence="low",
            fallback_level=5,
        )

    log.warning(
        "No peer baseline available for %s on %s at %s (took %.1fs)",
        label,
        client.platform,
        ranked.label,
        time.monotonic() - t0,
    )
    return None
