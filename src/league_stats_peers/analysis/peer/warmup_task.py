"""`WarmupTask`: champion/role-blind idle-capacity pre-warming for one tier.

RFC "PEERS priority scheduling...", §3: a `SamplingTask` is keyed on
`(platform, tier, champion, role, patch)` and its stop condition is
champion+role-specific -- reusing it for pre-warm would mean one task per
(tier, champion, role) triple, up to 5 tiers x ~170 champions x 5 roles.
`WarmupTask` instead scans one tier's player pool once, downloading matches
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
`wait_for_signal` for a `WarmupTask` key, so it only ever runs when both the
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
        """Present for scheduler/log-line parity with `SamplingTask` -- a
        `WarmupTask`'s real "done" signal is the tier's store count, not this."""
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
        """Advance the scan by at most `batch_size` new match downloads.

        Deliberately only checks `exhausted` once, at the top of the method
        (mirroring `SamplingTask.run_batch`'s own pattern of bounding its
        inner loop purely by cheap, in-memory conditions) rather than on
        every mid-batch iteration, for two reasons: (1) `exhausted` calls
        `store.count_by_tier()`, a Mongo aggregate query -- re-running it on
        every single download inside a batch would be one aggregate query
        per download instead of one per batch; (2) checking it mid-loop right
        after popping the batch's last queued puuid (but before that puuid's
        match download has had a chance to enqueue new participants) reports
        a false "exhausted" the moment the queue is momentarily empty,
        aborting the batch before it does any work at all -- a real bug found
        while writing this task's own tests. A `WarmupTask` batch can
        therefore overshoot `target_games` by up to one batch's worth of
        downloads before the next batch's exhaustion check catches it --
        negligible against a `target_games` in the tens of thousands.
        """
        log = get_logger("warmup_task")
        self._ensure_seeded()
        self.batches_run += 1
        if self.exhausted:
            return

        batch_downloads = 0
        while self.queue and batch_downloads < self.batch_size:
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
                if batch_downloads >= self.batch_size:
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
