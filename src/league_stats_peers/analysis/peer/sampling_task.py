"""`SamplingTask`: in-memory, resumable state for one live peer-sampling scan.

RFC "Batched, Round-Robin Live Sampling for PEERS", §5.1: replaces the old
"one worker runs `fetch_benchmark_from_api` to completion" model
(`benchmark_fetcher.py`) with a task object that carries its snowball-scan
state (queue, seen-sets, rank cache, rows collected so far) *between* calls,
so a scheduler (`analysis.peer.scheduler.SamplingScheduler`) can run it in
small batches interleaved with other active tasks instead of monopolizing a
worker thread for the whole scan.

One `SamplingTask` exists per active ``(platform, tier, champion, role,
patch)`` key -- the same key shape `PeersServicer._get_or_submit` already
dedupes concurrent `RequestBaseline` callers on. State is in-memory only, by
design (RFC §9.5): a PEERS restart loses in-flight progress, exactly like
today's crash behavior, and re-triggers from scratch on the next request for
that key.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any, Final

from league_stats_peers.analysis.peer.benchmark_fetcher import (
    BenchmarkSnapshot,
    _aggregate_rows,
    _gather_seeds,
    _load_or_fetch_match,
    _match_has_build,
    _participant_puuids,
    _quantile_metrics,
    _resolve_rank,
)
from league_stats_peers.analysis.peer.metrics import extract_all_champion_role_rows, extract_champion_role_rows
from league_stats_peers.analysis.peer.rank_scope import build_widened_scope, rank_matches
from league_stats_common.core.config import RANKED_SOLO_QUEUE_ID
from league_stats_common.core.models import RankedEntry
from league_stats_common.infra.riot_api import RiotApiClient, RiotApiError
from league_stats_common.utils import get_logger

# RFC §9: all confirmed by Brice, not open questions.
TARGET_PEER_GAMES: Final[int] = 50
CEILING: Final[int] = 1000
BATCH_SIZE: Final[int] = 50
INTERIM_THRESHOLD: Final[int] = 5
MATCH_IDS_PER_PLAYER: Final[int] = 30

TaskKey = tuple[str, str, str, str, str]  # (platform, tier, champion, role, patch)


@dataclass
class SamplingTask:
    """Accumulated state for one live-sampling scan, advanced one batch at a time.

    ``run_batch()`` is the scheduler's unit of work (RFC §5.1 step 2): it
    continues the snowball scan for up to ``batch_size`` new match downloads,
    or until the snowball queue is exhausted, whichever comes first, then
    returns -- it never runs to completion by itself.
    """

    key: TaskKey
    client: RiotApiClient
    store: Any
    ranked: RankedEntry
    champion: str
    role: str
    exclude_puuid: str | None = None
    patch: str = ""
    # RFC "PEERS priority scheduling...": which of SamplingScheduler's three
    # queues this task belongs in. "explicit" (someone is synchronously
    # blocked in wait_for_signal for this exact task), "refining" (already
    # answered the confidence-full bar, still improving toward CEILING, but
    # nobody is blocked waiting), "background" (WarmupTask only -- see that
    # module). Never set directly except by SamplingScheduler.
    priority: str = "explicit"
    # Phase 2 (RFC §5.2): shared cross-champion/cross-tier match cache. None
    # disables the pre-check entirely (falls back to a pure live scan) --
    # used by tests that don't care about Phase 2 wiring.
    match_sample_store: Any | None = None

    # `default_factory`, not a plain default: the plain-default form bakes in
    # the module constant's value at class-body execution time (import time),
    # which would make `monkeypatch.setattr(sampling_task, "TARGET_PEER_GAMES",
    # ...)` -- this codebase's usual way of shrinking targets for fast tests --
    # silently no-op. Reading the module global at instantiation time instead
    # keeps that convention working here too.
    target: int = field(default_factory=lambda: TARGET_PEER_GAMES)
    ceiling: int = field(default_factory=lambda: CEILING)
    batch_size: int = field(default_factory=lambda: BATCH_SIZE)
    interim_threshold: int = field(default_factory=lambda: INTERIM_THRESHOLD)

    rows: list[dict[str, Any]] = field(default_factory=list)
    seen_matches: set[str] = field(default_factory=set)
    seen_for_snowball: set[str] = field(default_factory=set)
    queue: "deque[str]" = field(default_factory=deque)
    rank_cache: dict[str, tuple[str, str]] = field(default_factory=dict)
    downloads: int = 0
    players_used: set[str] = field(default_factory=set)
    batches_run: int = 0

    _seeded: bool = field(default=False, repr=False)
    _cache_checked: bool = field(default=False, repr=False)

    @property
    def games(self) -> int:
        return len(self.rows)

    @property
    def reached_interim(self) -> bool:
        return self.games >= self.interim_threshold

    @property
    def reached_target(self) -> bool:
        return self.games >= self.target

    @property
    def exhausted(self) -> bool:
        """No more useful work remains at all -- ceiling spent, or the
        snowball queue has genuinely run dry after seeding.

        RFC "PEERS priority scheduling...", §2: deliberately independent of
        `reached_target` now. Reaching target used to make this method
        return False unconditionally (finalization happened at target,
        `exhausted` was never even consulted for such a task) -- now that a
        task keeps sampling toward `CEILING` after reaching target (RFC §1.2
        Case B/§2), `exhausted` has to be able to say "yes, genuinely done"
        purely from ceiling/queue state, independent of whether target was
        ever reached. Without this, a task that already reached target could
        never finalize: it would sit in `_refining_queue` forever, re-run
        every batch for no further progress once its queue drains.

        True once the download ceiling is hit, or once the snowball queue
        has genuinely run dry after seeding (e.g. no league entries at all,
        or every reachable player has already been visited) -- without this
        second condition a task with no candidates left would sit in the
        scheduler's queue forever, re-enqueued every batch for no progress.
        """
        if self.downloads >= self.ceiling:
            return True
        return self._seeded and not self.queue

    @property
    def done(self) -> bool:
        return self.reached_target or self.exhausted

    def build_snapshot(self, *, confidence: str, still_refining: bool) -> BenchmarkSnapshot:
        """Aggregate rows collected so far into a servable `BenchmarkSnapshot`.

        Called by the scheduler both for interim writes (``confidence="low"``,
        ``still_refining=True``) and for finalization, full (``confidence="full"``)
        or partial (``confidence="low"``, ``still_refining=False``) -- RFC §5.1.3.
        """
        return BenchmarkSnapshot(
            metrics=_aggregate_rows(self.rows),
            games_sampled=self.games,
            players_sampled=len(self.players_used),
            from_cache=False,
            platform=self.client.platform,
            metrics_p50=_quantile_metrics(self.rows, 0.5),
            metrics_p75=_quantile_metrics(self.rows, 0.75),
            confidence=confidence,
            still_refining=still_refining,
        )

    def _check_shared_cache(self) -> None:
        """RFC §5.2: pull free rows from `peer_match_samples` before live-scanning.

        Queries the shared collection for this task's own ``(platform, patch,
        champion, role)`` key -- not scoped by tier, since rows are stored
        tier-agnostic -- then resolves rank only for the candidates that come
        back and keeps only the ones inside this task's own tier scope. Any
        hit counts toward ``target`` for zero new match downloads.
        """
        self._cache_checked = True
        if self.match_sample_store is None:
            return
        log = get_logger("sampling_task")
        scope = build_widened_scope(self.ranked)
        try:
            candidates = self.match_sample_store.find_candidates(
                platform=self.client.platform,
                patch=self.patch,
                champion=self.champion,
                role=self.role,
            )
        except Exception as exc:  # noqa: BLE001 -- an unreachable/broken shared
            # cache must degrade to "nothing found", exactly like a genuine
            # miss (see `analysis.peer.benchmark_cache`'s own fail-soft
            # convention) -- never leave a batch mid-way / crash the
            # scheduler's worker thread, which would leave a caller blocked
            # on `SamplingScheduler.wait_for_signal` forever (no timeout).
            log.warning("Shared match cache lookup failed, scanning live only: %s", exc)
            return
        exclude = self.exclude_puuid or ""
        for candidate in candidates:
            if self.reached_target:
                break
            match_id = str(candidate["match_id"])
            puuid = str(candidate["puuid"])
            if not puuid or puuid == exclude or match_id in self.seen_matches:
                continue
            resolved = _resolve_rank(puuid, self.rank_cache, self.client, self.store)
            if resolved is None:
                continue
            tier, rank_str = resolved
            if not rank_matches(tier, rank_str, scope):
                continue
            row = dict(candidate["row"])
            row["match_id"] = match_id
            self.rows.append(row)
            self.players_used.add(puuid)
            self.seen_matches.add(match_id)

    def _ensure_seeded(self) -> None:
        if self._seeded:
            return
        if not self._cache_checked:
            self._check_shared_cache()
        if not self.reached_target:
            scope = build_widened_scope(self.ranked)
            seed_puuids, seed_ranks = _gather_seeds(self.client, scope, self.exclude_puuid)
            for puuid, rank in seed_ranks.items():
                self.rank_cache.setdefault(puuid, rank)
            self.queue.extend(p for p in seed_puuids if p not in self.seen_for_snowball)
        self._seeded = True

    def run_batch(self) -> None:
        """Advance the scan by at most `batch_size` new match downloads.

        Mirrors `benchmark_fetcher._collect_sample_rows`'s snowball loop, but
        stops after one batch instead of running until ceiling -- the
        scheduler decides what happens next (finalize, finalize-partial, or
        re-enqueue), not this method.

        RFC "PEERS priority scheduling...", §2: no longer stops early once
        `reached_target` -- a task keeps collecting toward `ceiling` after
        target (the scheduler demotes it to "refining" priority instead of
        finalizing it, see `SamplingScheduler._run_one_batch`). Only
        `self.exhausted` (ceiling spent, or the snowball queue genuinely dry)
        stops this method from doing further work.
        """
        log = get_logger("sampling_task")
        self._ensure_seeded()
        self.batches_run += 1
        if self.exhausted:
            return

        scope = build_widened_scope(self.ranked)
        batch_downloads = 0

        while self.queue and batch_downloads < self.batch_size and self.downloads < self.ceiling:
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
                if batch_downloads >= self.batch_size or self.downloads >= self.ceiling:
                    break
                if match_id in self.seen_matches:
                    continue
                self.seen_matches.add(match_id)

                match = _load_or_fetch_match(self.client, self.store, match_id, puuid)
                if match is None:
                    continue
                self.downloads += 1
                batch_downloads += 1

                # Phase 2 (RFC §5.2): every downloaded match pays for itself
                # once -- extract rows for ALL champion+role pairs present,
                # not just this task's own target, and share them.
                if self.match_sample_store is not None:
                    all_rows = extract_all_champion_role_rows(
                        match, exclude_puuid=self.exclude_puuid or ""
                    )
                    if all_rows:
                        try:
                            self.match_sample_store.upsert_rows(
                                match_id, self.patch, self.client.platform, all_rows
                            )
                        except Exception as exc:  # noqa: BLE001 -- best-effort,
                            # mirrors `write_live_cache`'s fail-soft convention:
                            # a failed shared-cache write must never break this
                            # task's own live sample.
                            log.warning("Shared match cache write failed: %s", exc)

                if not _match_has_build(match, self.champion, self.role):
                    continue

                for other_puuid in _participant_puuids(match):
                    if other_puuid and other_puuid not in self.seen_for_snowball:
                        self.queue.append(other_puuid)

                match_rows = extract_champion_role_rows(
                    match,
                    exclude_puuid=self.exclude_puuid or "",
                    champion=self.champion,
                    role=self.role,
                )
                for row in match_rows:
                    p_puuid = str(row.get("puuid", ""))
                    if not p_puuid:
                        continue
                    resolved = _resolve_rank(p_puuid, self.rank_cache, self.client, self.store)
                    if resolved is None:
                        continue
                    tier, rank_str = resolved
                    if not rank_matches(tier, rank_str, scope):
                        continue
                    row["match_id"] = match_id
                    self.rows.append(row)
                    self.players_used.add(p_puuid)
