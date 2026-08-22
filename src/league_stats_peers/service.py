"""PEERS' gRPC service: wraps `resolve_peer_baseline` verbatim via a duck-typed
store adapter, following the same "reuse via adapter" design Phase 1's RUNNER
used for `execute_job` (see `league_stats.runner.adapter.RunnerJobAdapter`).

Sync vs async
-------------
`resolve_peer_baseline` (`analysis/peer/baseline.py`), everything it calls
(`analysis/peer/cache.py`, `analysis/peer/benchmark_fetcher.py`,
`analysis/peer/benchmark_cache.py`), `RiotApiClient` (`infra/riot_api.py`) and
`PeerSampleStore` (`infra/peer_sample_store.py`) are all plain synchronous
Python -- no `async def`/`await` anywhere in that call graph.
`peers_pb2_grpc.py` (Phase 0)
also declares a plain, non-`async` `PeersServiceServicer` base class. So
`PeersServicer` below is a plain `grpc.server(...)` servicer, exactly like
RUNNER's `RunnerServicer` -- NOT `grpc.aio`, which CRON-watch uses because it
polls on a genuinely async timer loop.

Known gap: PeerSampleStore does not cover everything `resolve_peer_baseline`
touches on its `store` parameter
------------------------------------------------------------------------
`resolve_peer_baseline`'s fallback ladder (levels 0/1/3) calls
`collect_peer_games_from_store` (`analysis/peer/cache.py`), which calls, on
its `store` argument: `count_peer_games`, `load_peer_games`,
`iter_unverified_puuids_for_build`/`iter_unverified_puuids` (via
`_backfill_ranks`), and `set_puuid_rank`. Level 2's live-sampling fallback
(`_try_live_baseline` -> `fetch_benchmark_from_api`,
`analysis/peer/benchmark_fetcher.py`) additionally calls, via
`_load_or_fetch_match`: `load_match` and `save_match`; and via
`_resolve_rank`: `set_puuid_rank` again. `ingest_match`
(`analysis/peer/ingest.py`), called from both of the above, calls
`upsert_peer_game`.

`PeerSampleStore` (Task 1 of this plan, `infra/peer_sample_store.py`)
deliberately mirrors ONLY the six peer-game methods
(`upsert_peer_game`, `load_peer_games`, `count_peer_games`,
`iter_unverified_puuids`, `iter_unverified_puuids_for_build`,
`set_puuid_rank` -- see its own module docstring). It does NOT implement
`iter_match_ids`, `load_match` or `save_match` -- the raw match/timeline
storage half of `MatchStore`'s surface (`infra/cache.py:184-303`).

Two of those three additionally get called by `collect_peer_games_from_store`
itself (`analysis/peer/cache.py`, lines ~181-186), *before* the six-method
surface above even runs: on the very first request for a champion+role+
platform combination (`store.count_peer_games(...) == 0`), it bootstraps by
calling `store.iter_match_ids(exclude_puuid)` then `store.load_match(match_id)`
for each id, to backfill peer rows from the tracked player's own already-
downloaded match history before doing anything else. Once at least one row
exists for that champion+role+platform, this bootstrap is skipped on every
later call (`count_peer_games(...) == 0` is false), so the gap only bites on
genuinely first-ever access to a build. `resolve_peer_baseline` on a bare
`PeerSampleStore` (with no adapter) would raise `AttributeError` on this
first call, and again on any call that falls through to level 2 (live
sampling), inside `_load_or_fetch_match`.

There IS an existing, tested Mongo store with the missing raw-match-storage
surface: `infra/raw_match_store.py`'s `RawMatchStore` (`has_match`,
`save_match`, `load_match`, `save_timeline`, `load_timeline`,
`claim_ownership`, `iter_all_match_ids`, `count`) -- built in an earlier
phase for RUNNER, currently unwired into any production code path (see
`runner/service.py`'s own module docstring), and it does NOT have a
`iter_match_ids(puuid)`-scoped method either (only the unscoped
`iter_all_match_ids()`), so wiring it in would not fully close this gap
either, and would add a second Mongo dependency to PEERS beyond what this
task's brief scopes (`PeerSampleStore` only). Review round 1 confirmed the
no-op adapter approach below is the right call for now (see
`test_resolve_peer_baseline_via_live_sampling_survives_the_noop_store_methods`
for direct proof the real fallback ladder survives it) -- wiring `RawMatchStore`
in remains a deliberate follow-up for the controller, not decided here.

`_PeerStoreAdapter` below therefore delegates the six real methods to a real
`PeerSampleStore`, and implements `iter_match_ids`/`load_match`/`save_match`
as documented no-ops (mirroring `RunnerJobAdapter`'s precedent of no-op
methods for capabilities its backing object doesn't have): the store
bootstrap step silently does nothing (falls through to "0 games", same as
an empty store), and `_load_or_fetch_match` always live-fetches from Riot
and never caches the raw match document (`ingest_match`'s
`upsert_peer_game` call still runs normally and persists the *extracted*
peer row for real, into the real `PeerSampleStore` -- only the raw match
JSON caching is skipped). This means live sampling can re-download the same
match from Riot more than once across separate requests instead of hitting
a local cache -- a real, known inefficiency, not a correctness bug.

Platform routing (review round 1, fix 1; corrected in round 2)
-----------------------------------------------------------------
`RequestBaselineRequest` originally had no `platform`/`exclude_puuid` fields.
Without a `platform` field, a hardcoded default riot-client builder fell back
to `AppConfig.routing_platform`'s silent default (`euw1`) whenever
`PEERS_PLATFORM` was unset -- a KR/NA request would silently get EUW peer
rows (or an empty store) with no error. The proto now carries both fields
(it had zero existing callers, so this was a free additive change).

Round 1's first attempt at routing was a `_PlatformScopedRiotClient` wrapper
overriding `.platform`/`.platform_base` and delegating everything else via
`__getattr__`. **That does not work and was removed in round 2.** Python
attribute reads *inside* a delegated method's own body (e.g.
`fetch_league_entries_pages` internally reading `self.platform_base`) resolve
against `self` as bound at method-lookup time -- i.e. the real
`RiotApiClient` the method was defined on, not the wrapper -- because
`__getattr__` only intercepts attribute lookups performed *from outside* the
object, not `self.xxx` reads inside a method body already bound to the real
object. So every real Riot HTTP call the wrapper "delegated" actually ran
with the *shared* client's own platform, silently ignoring the override
(confirmed empirically: `fetch_league_entries_pages` through the wrapper hit
`euw1.api.riotgames.com` even when the wrapper's own `.platform_base` read
`na1`). Worse, `client.platform` reads *from outside* a method (e.g.
`collect_peer_games_from_store`'s `platform=client.platform`) DID see the
override, so live-sampled peer rows fetched from the wrong (default)
platform got persisted into the store mislabeled under the *requested*
platform -- poisoning subsequent store-path (levels 0/1/3) lookups for that
platform with wrong-region data. The wrapper's docstring justification (no
public setter) was also simply wrong: `RiotApiClient.set_platform`
(`infra/riot_api.py`) is a real, validated public setter -- but mutating a
*shared* client via `set_platform` at request time would itself race under
concurrent different-platform requests (the executor runs up to
`max_workers` resolutions at once).

**Real fix**: a genuinely separate, fully-configured, immutable
`RiotApiClient` instance per platform, pooled and cached
(`PeersServicer._riot_client_for`/`self._riot_clients`, keyed by platform
string) rather than shared/wrapped/mutated. `_build_riot_client_for_platform`
builds one: both `.platform`/`.platform_base` (league-v4/summoner-v4) AND the
regional match-v5 base (`RiotApiClient.__init__`'s `_regional_base`, derived
from `AppConfig.region`) must be correct for a given platform --
`core.config.PLATFORM_TO_REGION` (already existed, previously unused here)
gives the right region per platform, rather than relying solely on a single
static `PEERS_REGION` env var that would route match-v5 calls to the wrong
regional cluster for any platform outside that one region. Each pooled
instance is built once and never mutated afterward (`set_platform` is never
called post-construction), so concurrent requests for different platforms
each get their own client and never race over shared state; `HttpCache`/API
key are shared across the pool (cheap to share, expensive-ish to rebuild per
platform -- disk-backed `diskcache` opens), while
`RateLimiter` instances come from `riot_api.shared_rate_limiter`, matching
this codebase's own existing process-wide-limiter convention.

`request.platform` is validated against `core.config.VALID_PLATFORMS`
(normalized to lowercase first) and rejected with `INVALID_ARGUMENT` when
unrecognized -- closes both the case-mismatch bug where `"NA1"` vs `"na1"`
would silently split store lookups into two different keys, and the
injection-adjacent risk of an arbitrary caller-supplied string being
interpolated into a URL host carrying PEERS' own Riot API key. Falls back to
`PEERS_PLATFORM` (also normalized), then `self._default_platform` (resolved
once at construction the same way), only when the request field is empty.

`request.exclude_puuid` is passed straight through to
`resolve_peer_baseline`'s `exclude_puuid` keyword, and likewise
`request.patch` to its `patch` keyword (added in the final whole-branch
review, finding 1 -- previously dropped entirely, so PEERS always resolved
with `patch=""`, which `select_by_patch` (`analysis/peer/cache.py`) treats as
"no filter", blending every patch ever ingested into one baseline).

For tests, `riot_client_factory: Callable[[str], Any] | None` overrides the
production pool entirely with an injectable per-platform factory -- e.g.
`lambda platform: some_fake` (ignoring the platform argument, for tests that
don't care about platform-specific behavior) or a factory that returns a
distinct, correctly-configured fake per platform (for tests that need to
prove platform wiring, e.g.
`test_request_baseline_uses_request_platform_and_exclude_puuid`). This
replaces round 1's single fixed `riot_client` override, which could not
represent "different behavior per platform" at all -- exactly the gap that
made the broken wrapper look like it worked in round 1's tests (which only
asserted the wrapper's own attributes, never a real HTTP call -- the "proves
acceptance, not use" gap round 2's review called out).

Concurrency (review round 1, fix 2)
------------------------------------
Two problems existed: (a) executor saturation -- a live sample can issue up
to `MAX_MATCH_DOWNLOADS` (400) rate-limited HTTP calls, taking minutes, while
the fast-path timeout is a few seconds; a request queued behind several such
samples would silently time out with no signal that it never even started
running; (b) no dedup -- two identical in-flight `(champion, role, platform,
tier)` requests each launched an independent, redundant live sample.

Fixed by `_get_or_submit`: in-flight resolutions are tracked in
`self._inflight`, keyed on `(champion, role, platform, tier, patch)` (division
is excluded from the key because `RankScope` -- see
`analysis/peer/rank_scope.py` -- only ever matches on tier, never division,
so two requests differing only by division would resolve identically; this
does mean two callers with different `exclude_puuid` values deduped onto the
same in-flight resolution share one result that only excludes the *first*
caller's puuid -- a known, accepted simplification matching the review's
specified dedup key). `patch` was added to the key in the final whole-branch
review (finding 1): without it, a stale in-flight resolution for a different
patch could be joined by a later request for the current patch, silently
handing that caller a wrong-patch baseline. A second caller for the same key
attaches its own `request_id`/callback to the *same* `Future` instead of
submitting a new one.

For observability, each in-flight resolution tracks a `threading.Event` that
is set only once a worker thread actually starts running it (not merely
submitted to the executor's queue). When `RequestBaseline`'s fast-path wait
times out, it logs -- and increments `PEERS_FAST_PATH_TIMEOUTS_TOTAL`, labeled
`started="true"/"false"` -- whether the resolution was genuinely running
(almost certainly live sampling) or still queued behind other in-flight work
(saturated executor), instead of returning an opaque `cached=False` with no
way to tell the two apart. `PEERS_INFLIGHT_BASELINES` gauges current
concurrency. `PEERS_MAX_CONCURRENT_BASELINES` (env var, default 4) bounds the
executor.
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable, Iterator

import grpc
import pymongo
import requests
from prometheus_client import Counter, Gauge, Histogram

from league_stats_peers.analysis.peer.baseline import (
    PeerBaseline,
    _baseline_from_snapshot,
    _get_default_scheduler,
    configure_scheduler_idle_hook,
    register_progressive_listener,
    resolve_peer_baseline,
    task_key_for,
)
from league_stats_peers.analysis.peer.benchmark_cache import read_live_cache
from league_stats_peers.analysis.peer.benchmark_fetcher import BenchmarkSnapshot
from league_stats_peers.analysis.peer.benchmarks import VALID_TIERS
from league_stats_peers.analysis.peer.scheduler import SamplingScheduler
from league_stats_peers.analysis.peer.warmup_task import (
    PREWARM_CHAMPION_SENTINEL,
    PREWARM_ROLE_SENTINEL,
    WarmupTask,
)
from league_stats_common.core.config import AppConfig, PLATFORM_TO_REGION, REGION_DEFAULT_PLATFORM, VALID_PLATFORMS
from league_stats_common.core.models import RankedEntry
from league_stats_common.infra.cache import HttpCache
from league_stats_common.infra.ddragon_assets import DDRAGON_BASE
from league_stats_common.infra.mongo import db_name_from_uri as _db_name_from_uri
from league_stats_peers.infra.peer_match_sample_store import PeerMatchSampleStore
from league_stats_peers.infra.peer_sample_store import PeerSampleStore
from league_stats_common.infra.riot_api import RiotApiClient, shared_rate_limiter
from league_stats_runner.infra.raw_match_store import RawMatchStore
from league_stats_common.infra.trace_context import TraceClientInterceptor
from league_stats_common.utils import get_logger
from league_stats_rpc.v1 import peers_pb2, peers_pb2_grpc, runner_pb2, runner_pb2_grpc

log = get_logger("peers_service")

# How long RequestBaseline blocks waiting for a same-thread resolution before
# giving up and treating it as a background (live-sampling) fetch. Store-only
# resolutions (levels 0/1/3) and static-JSON fallbacks (levels 4/5) are all
# local reads and comfortably finish well inside this window; level 2 (live
# Riot sampling, up to `MAX_MATCH_DOWNLOADS` real HTTP calls) does not.
FAST_PATH_TIMEOUT_S: float = 3.0

PEERS_INFLIGHT_BASELINES = Gauge(
    "peers_inflight_baselines",
    "resolve_peer_baseline calls currently running on PEERS' executor "
    "(does not count requests still queued behind them).",
)
PEERS_FAST_PATH_TIMEOUTS_TOTAL = Counter(
    "peers_fast_path_timeouts_total",
    "RequestBaseline calls that exceeded the fast-path timeout, labeled by "
    "whether the underlying resolution had actually started running yet "
    "('true' = likely live sampling, 'false' = still queued behind other work).",
    ["started"],
)
PEERS_DEDUPED_REQUESTS_TOTAL = Counter(
    "peers_deduped_requests_total",
    "RequestBaseline calls that attached to an already in-flight resolution "
    "for the same (champion, role, platform, tier) instead of starting a new one.",
)
# PEERS' first Prometheus metrics -- see peers/__main__.py's start_http_server call.
# Recorded once per completed resolve_peer_baseline call, in _get_or_submit's `_run`
# below, which is the single code path both RequestBaseline's fast (same-thread) wait
# and its backgrounded live-sampling continuation share -- so both are covered by one
# instrumentation point.
# Note: since the batched round-robin scheduler landed (RFC "Batched,
# Round-Robin Live Sampling for PEERS"), a level-2 resolution's duration here
# only covers the wait for interim/finalize on its SamplingTask, not the full
# live scan -- a concurrent metrics RFC is also touching this histogram
# (splitting it by source) in a separate worktree; that overlap is expected
# and resolved at merge time, not here.
PEERS_BASELINE_RESOLUTION_DURATION = Histogram(
    "peers_baseline_resolution_duration_seconds",
    "Time resolve_peer_baseline took to run one baseline resolution, from worker "
    "thread start to completion, labeled by source -- cached (levels 0/1/3/4/5, "
    "all local reads, sub-millisecond) vs. live_sample (level 2, live Riot "
    "sampling, seconds to minutes) vs. error, so a live-sampling latency "
    "regression is no longer averaged away by the fast local-read levels.",
    ["source"],  # cached | live_sample | error
)
PEERS_BASELINE_RESOLUTIONS_TOTAL = Counter(
    "peers_baseline_resolutions_total",
    "Completed resolve_peer_baseline calls, labeled by whether the result came from "
    "local data (cached: store hits or static benchmark fallback) or required a live "
    "Riot sampling fetch (live_sample: fallback_level 2).",
    ["source"],
)
# Queued (submitted but not yet running) baseline resolutions -- the
# complement to PEERS_INFLIGHT_BASELINES (which only counts work a worker
# thread has actually started). Without this, an operator watching
# PEERS_INFLIGHT_BASELINES during a timeout spike can't tell "all workers
# busy, N queued behind them" from "workers idle, something else is wrong."
PEERS_QUEUED_BASELINES = Gauge(
    "peers_queued_baselines",
    "In-flight resolutions submitted to the executor but not yet running "
    "(len(self._inflight) - running).",
)
# Denominator for PEERS_FAST_PATH_TIMEOUTS_TOTAL -- every RequestBaseline call
# that waits on the fast-path timeout at all (whether it times out or not),
# so the timeout counter can be turned into a real rate.
PEERS_FAST_PATH_ATTEMPTS_TOTAL = Counter(
    "peers_fast_path_attempts_total",
    "RequestBaseline calls that waited on the fast-path timeout (denominator "
    "for PEERS_FAST_PATH_TIMEOUTS_TOTAL).",
)
# Data-coverage visibility (dashboard-observability follow-up): how much
# verified peer-sample data exists per tier. Labeled by `tier` only, not
# champion -- `VALID_TIERS` is a fixed 10-value enum (safe), but champion is
# a Data Dragon-sourced, ~170-value-and-growing set with no fixed enum in
# code, so a (champion, tier) label pair would risk real cardinality growth
# every time a new champion ships. This is a coarser signal than per-champion
# coverage, by design -- see `refresh_match_sample_coverage`'s docstring.
PEERS_MATCH_SAMPLE_COVERAGE_GAMES = Gauge(
    "peers_match_sample_coverage_games",
    "Verified peer_games rows in PeerSampleStore, by tier -- refreshed "
    "periodically (see refresh_match_sample_coverage), not per-request.",
    ["tier"],
)
_COVERAGE_REFRESH_INTERVAL_S: float = 300.0


def refresh_match_sample_coverage(peer_store: PeerSampleStore) -> dict[str, int]:
    """Query current verified peer-game coverage by tier and publish it.

    Every member of `VALID_TIERS` is always set (0 when a tier has no
    coverage yet), so a tier never silently disappears from the dashboard --
    it shows an explicit zero instead of a missing series.
    """
    counts = peer_store.count_by_tier()
    for tier in VALID_TIERS:
        PEERS_MATCH_SAMPLE_COVERAGE_GAMES.labels(tier=tier).set(counts.get(tier, 0))
    return counts


def log_champion_coverage(peer_store: PeerSampleStore) -> dict[tuple[str, str], int]:
    """Log verified peer-game coverage per (champion, role) for Grafana's Loki side.

    Champion+role is the per-key granularity Brice actually wants ("games per
    champion"), but champion has no fixed enum (~170 Data Dragon values and
    growing every patch), so it can't be a Prometheus label -- see
    `PeerSampleStore.count_by_champion_role`'s docstring and
    `PEERS_MATCH_SAMPLE_COVERAGE_GAMES`'s tier-only rationale above. This
    mirrors how `analysis.peer.scheduler` surfaces its own high-cardinality
    (platform, tier, champion, role, patch) queue keys: a structured log
    line per key, read via a Grafana Loki panel
    (`deploy/grafana/dashboards/peers.json`), not a metric label.

    Only keys with at least one verified row are logged -- there is no fixed
    universe of (champion, role) pairs to log an explicit zero for, unlike
    `refresh_match_sample_coverage`'s `VALID_TIERS` loop.
    """
    counts = peer_store.count_by_champion_role()
    for (champion, role), games in counts.items():
        log.info("peer_sample_champion_coverage champion=%s role=%s games=%d", champion, role, games)
    return counts


def start_match_sample_coverage_refresher(
    peer_store: PeerSampleStore, interval_s: float = _COVERAGE_REFRESH_INTERVAL_S
) -> threading.Thread:
    """Start a daemon thread refreshing `PEERS_MATCH_SAMPLE_COVERAGE_GAMES` on an interval.

    Coverage is a slowly-changing signal (verified peer-game rows accumulate
    over hours/days), so a 5-minute default is more than fresh enough --
    deliberately not computed per-request, since `count_by_tier`'s
    aggregation is a full-collection scan-and-group. The same cadence also
    drives `log_champion_coverage`'s per-(champion, role) log lines, so both
    signals stay in sync.
    """

    def _loop() -> None:
        while True:
            try:
                refresh_match_sample_coverage(peer_store)
            except Exception:  # noqa: BLE001 -- a failed refresh must never
                # crash the process; the gauge simply keeps its last-known
                # values until the next successful cycle.
                log.exception("Failed to refresh peers_match_sample_coverage_games")
            try:
                log_champion_coverage(peer_store)
            except Exception:  # noqa: BLE001 -- same rationale as above.
                log.exception("Failed to log peer_sample_champion_coverage")
            time.sleep(interval_s)

    thread = threading.Thread(target=_loop, name="peers-coverage-refresher", daemon=True)
    thread.start()
    return thread


# RFC "PEERS priority scheduling, continued sampling, pre-warm, and patch
# cleanup" §3/§4: idle-capacity pre-warm + automatic patch-changeover
# cleanup, both piggybacking on SamplingScheduler's own idle-poll signal
# (`on_idle`, see scheduler.py) instead of a standalone timer thread.
#
# Deliberately OFF by default (opt-in via this env var), not on by
# construction: `PeersServicer.__init__` is exercised by a very large number
# of existing unit tests, many of which already call `SamplingScheduler.start()`
# indirectly (via a level-2 live-sampling path) with fake/mock Riot clients
# and stores that were never built to support this hook's real work -- a
# background WarmupTask silently erroring against a MagicMock is harmless
# (the scheduler's own per-batch exception guard catches it), but
# `check_and_apply_patch_changeover`'s outbound Data Dragon HTTP call is not:
# unconditionally wiring it in would make ordinary test runs make real
# network calls from background daemon threads, which is slow, flaky under a
# sandboxed/offline CI runner, and not something any of those tests asked
# for.
#
# Deliberately on-by-default in production (unlike a typical opt-in feature
# flag): an env var a real deploy has to remember to set is a silent no-op
# waiting to happen -- confirmed live, this exact flag shipped inverted once
# already and nobody noticed pre-warm was inert. Instead, *tests* opt out via
# `DISABLE_PREWARM_FOR_TESTS=true` (set unconditionally at the top of
# `tests/conftest.py`, before this module is ever imported -- see that file),
# so production gets the real behavior with zero action required, and test
# runs are the ones carrying the exception.
PEERS_ENABLE_PREWARM_COORDINATOR: bool = (
    os.environ.get("DISABLE_PREWARM_FOR_TESTS", "false").strip().lower() != "true"
)
PEERS_PREWARM_TARGET_GAMES_PER_TIER: int = 20_000
PEERS_PREWARM_TIERS: tuple[str, ...] = ("GOLD", "PLATINUM", "EMERALD", "DIAMOND", "MASTER")
# How often the idle hook actually does real work -- it's invoked on every
# empty `SamplingScheduler.step()` (as often as every `_IDLE_POLL_INTERVAL_S`
# = 0.05s), far too often to run a Mongo aggregate or an HTTP call on every
# firing. Pre-warm is cheap (one `count_by_tier()` aggregate) and benefits
# from a short interval so idle capacity gets used promptly; the patch/
# Data-Dragon check is a real outbound HTTP call and patches ship roughly
# every two weeks, so a coarse interval costs nothing in responsiveness.
_PREWARM_TICK_INTERVAL_S: float = 5.0
_PATCH_REFRESH_INTERVAL_S: float = 300.0


def prewarm_tick(
    scheduler: SamplingScheduler,
    store: Any,
    client_factory: "Callable[[str], Any]",
    patch: str,
    tier_cursor: list[int],
    platform: str,
) -> None:
    """Enqueue one tier's `WarmupTask` if it's not already warm, advancing the
    round-robin cursor by one tier per call regardless of outcome (so a
    permanently-skipped tier doesn't starve the rest of the ring).

    `tier_cursor` is a single-element list, not a plain int, so the caller
    (`_IdleCoordinator`) can hold a persistent, mutable rotation position
    across calls without this function needing to be a method/closure itself
    -- keeps it a plain, easily-unit-testable function.
    """
    coverage = store.count_by_tier()
    tier = PEERS_PREWARM_TIERS[tier_cursor[0] % len(PEERS_PREWARM_TIERS)]
    tier_cursor[0] += 1
    if coverage.get(tier, 0) >= PEERS_PREWARM_TARGET_GAMES_PER_TIER:
        return
    key = (platform, tier, PREWARM_CHAMPION_SENTINEL, PREWARM_ROLE_SENTINEL, patch)
    if scheduler.is_active(key):
        return
    client = client_factory(platform)
    scheduler.get_or_create(
        key,
        lambda: WarmupTask(
            key=key, client=client, store=store, tier=tier, patch=patch,
            target_games=PEERS_PREWARM_TARGET_GAMES_PER_TIER,
        ),
        priority="background",
    )


def _normalize_patch(version: str) -> str:
    """Truncate a Data Dragon version (e.g. '14.3.1') to major.minor, matching
    the patch format stored on peer_games rows (parser.py's own
    `".".join(version.split(".")[:2])` truncation)."""
    return ".".join(version.split(".")[:2])


def _current_ddragon_patch() -> str:
    """Fetch Data Dragon's current version, truncated to major.minor.

    A lightweight, standalone HTTP call -- deliberately not routed through
    `DDragonAssets` (that class manages a whole icon-cache directory PEERS
    has no reason to touch); reuses the same `{DDRAGON_BASE}/api/versions.json`
    endpoint `DDragonAssets._fetch_latest_version` already calls.
    """
    response = requests.get(f"{DDRAGON_BASE}/api/versions.json", timeout=15)
    response.raise_for_status()
    return _normalize_patch(str(response.json()[0]))


def check_and_apply_patch_changeover(peer_store: Any) -> bool:
    """Drop peer_games/peer_match_samples/live_benchmark_cache if the current
    patch no longer matches the last-stored peer_games row's patch.

    Returns True if a drop happened. Fail-soft on a Data-Dragon fetch error
    (network blip) -- a missed check just means the drop happens on the next
    periodic call instead, not a crash of the idle-time coordinator loop.

    Deviates slightly from earlier drafts of this check: takes only
    `peer_store`, not a separate `mongo_client`/`db_name` pair -- `PeerSampleStore`
    already owns a `pymongo`/`mongomock` collection handle
    (`peer_store._peer_games`), and every `pymongo`/`mongomock` `Collection`
    exposes its parent `Database` via `.database`, so the three collections
    to drop can be reached from that one handle without PEERS having to also
    thread a raw Mongo client/db-name pair through every caller just for this.
    """
    try:
        current = _current_ddragon_patch()
    except Exception as exc:  # noqa: BLE001 -- fail-soft, see docstring
        log.warning("Could not resolve current Data Dragon patch: %s", exc)
        return False

    last_doc = peer_store._peer_games.find_one(
        {}, {"patch": 1}, sort=[("ingested_at", -1)]
    )
    last_patch = str(last_doc.get("patch", "")) if last_doc else ""
    if not last_patch or last_patch == current:
        return False

    log.info("Patch changeover detected: %s -> %s, dropping peer collections", last_patch, current)
    db = peer_store._peer_games.database
    for name in ("peer_games", "peer_match_samples", "live_benchmark_cache"):
        db.drop_collection(name)
    return True


class _IdleCoordinator:
    """Rate-limited idle-time hook wired into `SamplingScheduler.set_on_idle`.

    Owns both `prewarm_tick`'s round-robin cursor and the patch-changeover
    check's cadence (RFC §3.3/§4: "one small background loop can own both
    checks"). `on_idle` fires on every empty `step()` call -- as often as
    every `_IDLE_POLL_INTERVAL_S` (0.05s) -- so everything here is
    self-rate-limited by wall-clock timestamps; the scheduler itself applies
    no rate limiting of its own (see `SamplingScheduler.set_on_idle`).

    "Current patch" for `prewarm_tick` is resolved from Data Dragon (the same
    source `check_and_apply_patch_changeover` already uses) rather than from
    `peer_store`'s own most-recently-ingested row: the latter would be empty
    or stale immediately after a patch-changeover drop (there is nothing to
    read until pre-warm/real traffic re-populates it), creating a circular
    dependency for the very thing pre-warm is trying to bootstrap. Cached
    for `_PATCH_REFRESH_INTERVAL_S` between refreshes so `prewarm_tick`'s own
    (much shorter) cadence doesn't also refetch it on every tick.
    """

    def __init__(
        self,
        *,
        scheduler: SamplingScheduler,
        peer_store: Any,
        riot_client_for: "Callable[[str], Any]",
        default_platform: str,
    ) -> None:
        self._scheduler = scheduler
        self._peer_store = peer_store
        self._riot_client_for = riot_client_for
        self._default_platform = default_platform
        self._tier_cursor = [0]
        self._lock = threading.Lock()
        self._last_prewarm_tick = 0.0
        self._last_patch_refresh = 0.0
        self._current_patch = ""

    def __call__(self) -> None:
        now = time.monotonic()
        with self._lock:
            do_patch_refresh = now - self._last_patch_refresh >= _PATCH_REFRESH_INTERVAL_S
            if do_patch_refresh:
                self._last_patch_refresh = now
            do_prewarm = now - self._last_prewarm_tick >= _PREWARM_TICK_INTERVAL_S
            if do_prewarm:
                self._last_prewarm_tick = now
            current_patch = self._current_patch

        if do_patch_refresh:
            current_patch = self._refresh_patch_and_check_changeover()

        if do_prewarm and current_patch:
            prewarm_tick(
                self._scheduler,
                self._peer_store,
                self._riot_client_for,
                current_patch,
                self._tier_cursor,
                self._default_platform,
            )

    def _refresh_patch_and_check_changeover(self) -> str:
        try:
            current = _current_ddragon_patch()
        except Exception as exc:  # noqa: BLE001 -- fail-soft; keep the
            # previously-cached patch (if any) rather than blocking pre-warm
            # entirely on a transient Data Dragon outage.
            log.warning("Idle coordinator could not resolve current Data Dragon patch: %s", exc)
            with self._lock:
                return self._current_patch
        with self._lock:
            self._current_patch = current
        try:
            check_and_apply_patch_changeover(self._peer_store)
        except Exception:  # noqa: BLE001 -- must never take down a batch-worker thread.
            log.exception("check_and_apply_patch_changeover failed")
        return current


class _PeerStoreAdapter:
    """`MatchStore`-shaped adapter around `PeerSampleStore` for `resolve_peer_baseline`.

    See this module's docstring for the exact method inventory and the
    documented gap (`iter_match_ids`/`load_match`/`save_match` are no-ops).
    """

    def __init__(self, store: PeerSampleStore) -> None:
        self._store = store

    def upsert_peer_game(self, row: dict[str, Any]) -> bool:
        return self._store.upsert_peer_game(row)

    def load_peer_games(self, *, champion: str, role: str, platform: str) -> list[dict[str, Any]]:
        return self._store.load_peer_games(champion=champion, role=role, platform=platform)

    def count_peer_games(self, *, champion: str, role: str, platform: str) -> int:
        return self._store.count_peer_games(champion=champion, role=role, platform=platform)

    def iter_unverified_puuids(self, limit: int = 100) -> list[str]:
        return self._store.iter_unverified_puuids(limit)

    def iter_unverified_puuids_for_build(
        self, champion: str, role: str, platform: str, limit: int = 200
    ) -> list[str]:
        return self._store.iter_unverified_puuids_for_build(champion, role, platform, limit)

    def set_puuid_rank(self, puuid: str, tier: str, rank: str) -> int:
        return self._store.set_puuid_rank(puuid, tier, rank)

    def count_by_tier(self) -> dict[str, int]:
        """Delegates to `PeerSampleStore.count_by_tier` -- `WarmupTask`'s own
        "how close is this tier to its pre-warm target" check (RFC "PEERS
        priority scheduling...", §3)."""
        return self._store.count_by_tier()

    def iter_match_ids(self, puuid: str) -> Iterator[str]:
        """No-op: PEERS keeps no per-player raw match history (see module docstring)."""
        return iter(())

    def load_match(self, match_id: str) -> dict[str, Any] | None:
        """No-op: PEERS keeps no raw match cache (see module docstring)."""
        return None

    def save_match(self, match_id: str, puuid: str, match: dict[str, Any]) -> None:
        """No-op: PEERS persists only extracted peer rows, not raw match JSON."""
        return None


def _parse_rank(raw: str) -> tuple[str, str]:
    """Split a rank string (``"GOLD II"``, ``"GOLD_II"``, or bare ``"CHALLENGER"``)
    into ``(tier, division)``. ``RequestBaselineRequest.rank`` (Phase 0's proto)
    is a single free-form string, not separate tier/division fields, so this
    is PEERS' own parsing convention -- there is no established format to
    match yet, since no real caller exists before this task. Division is
    ``""`` for apex tiers (MASTER/GRANDMASTER/CHALLENGER) or an unparseable
    input.
    """
    parts = raw.replace("_", " ").split()
    if not parts:
        return "", ""
    tier = parts[0].upper()
    rank = parts[1].upper() if len(parts) > 1 else ""
    return tier, rank


def _encode_baseline(baseline: PeerBaseline | None) -> str:
    if baseline is None:
        return ""
    return json.dumps(asdict(baseline))


def _build_default_mongo_client() -> pymongo.MongoClient:
    mongo_uri = os.environ.get("MONGO_URI", "mongodb://localhost:27017/league_stats")
    # No short serverSelectionTimeoutMS here (unlike benchmark_cache.py's
    # best-effort live cache): this client backs the peer-baseline sample
    # pipeline itself (PeerSampleStore) and, since Phase 8 Task 1, PEERS' own
    # raw-match cache (RawMatchStore) -- neither is an optional cache layer,
    # so a slow/starting-up Mongo should be waited out (pymongo's ~30s
    # default) rather than treated as an immediate fallback.
    return pymongo.MongoClient(mongo_uri)


def _build_default_peer_store(
    client: pymongo.MongoClient | None = None,
) -> PeerSampleStore:
    mongo_uri = os.environ.get("MONGO_URI", "mongodb://localhost:27017/league_stats")
    client = client or _build_default_mongo_client()
    return PeerSampleStore(client, db_name=_db_name_from_uri(mongo_uri))


def _build_default_match_sample_store(
    client: pymongo.MongoClient | None = None,
) -> PeerMatchSampleStore:
    """Build the Phase 2 shared match cache store (`peer_match_samples`).

    TTL mirrors `analysis.peer.benchmark_cache`'s existing pattern (RFC §5.2):
    the same total window (its `CACHE_TTL_S`, patch-based staleness) plus the
    same margin, kept as a pure housekeeping backstop against unbounded
    growth rather than the actual staleness check (that's the caller's
    `patch` filter on `find_candidates`).

    Builds its own client with a short `serverSelectionTimeoutMS` (matching
    `analysis.peer.benchmark_cache._get_store`'s convention) when none is
    given -- unlike `_build_default_peer_store`/`_build_default_riot_client_factory`,
    this is reached from inside a live-sampling batch (`SamplingTask.
    _check_shared_cache`, via `_LazyMatchSampleStore`), a best-effort cache
    lookup that must fail fast, not stall for pymongo's ~30s default, on an
    unreachable Mongo. `_check_shared_cache`/`run_batch` also wrap every call
    on this store in a broad `except Exception` regardless, so this is
    defense in depth, not the only guard.
    """
    from league_stats_peers.analysis.peer.benchmark_cache import (
        CACHE_TTL_S,
        _SERVER_SELECTION_TIMEOUT_MS,
        _TTL_INDEX_MARGIN_S,
    )

    mongo_uri = os.environ.get("MONGO_URI", "mongodb://localhost:27017/league_stats")
    client = client or pymongo.MongoClient(
        mongo_uri, serverSelectionTimeoutMS=_SERVER_SELECTION_TIMEOUT_MS
    )
    return PeerMatchSampleStore(
        client, db_name=_db_name_from_uri(mongo_uri), ttl_seconds=CACHE_TTL_S + _TTL_INDEX_MARGIN_S
    )


def _resolve_default_platform() -> str:
    """The platform used when a request carries no `platform` and `PEERS_PLATFORM`
    is unset. Normalized lowercase, always a member of `VALID_PLATFORMS`."""
    explicit = os.environ.get("PEERS_PLATFORM", "").strip().lower()
    if explicit in VALID_PLATFORMS:
        return explicit
    region = os.environ.get("PEERS_REGION", "europe").strip().lower()
    return REGION_DEFAULT_PLATFORM.get(region, "euw1")


def _build_riot_client_for_platform(
    platform: str,
    *,
    api_key: str,
    http_cache: HttpCache,
    match_store: RawMatchStore,
    session: requests.Session | None = None,
) -> RiotApiClient:
    """Build a `RiotApiClient` fully and correctly scoped to one platform.

    Both `.platform`/`.platform_base` (league-v4/summoner-v4 routing) AND the
    regional match-v5 base (`RiotApiClient`'s `_regional_base`, derived from
    `AppConfig.region`) must be correct for `platform` -- using
    `PLATFORM_TO_REGION` here (rather than a single static `PEERS_REGION`) is
    what makes match-v5 calls hit the right regional cluster for any
    platform, not just whichever one region happens to be configured. The
    returned client is never mutated after this call (`set_platform` is not
    used post-construction) -- callers should build one instance per platform
    and reuse it, never call this per request, and never share one instance
    across code that might mutate it.

    The `RawMatchStore` passed here (Phase 8, Task 1 -- was `MatchStore`) is
    required by `RiotApiClient.__init__`'s type hint, but none of the methods
    `resolve_peer_baseline`/`fetch_benchmark_from_api` call on a
    `RiotApiClient` (`fetch_league_entries_pages`, `fetch_match_ids`,
    `fetch_match`, `fetch_solo_rank`) ever touch `self._store` -- only
    `download_matches` does, which is not part of this call graph.
    """
    region = PLATFORM_TO_REGION.get(platform, "europe")
    config = AppConfig(riot_id="peers", tagline="peers", api_key=api_key, region=region, platform=platform)
    limiter = shared_rate_limiter(config.requests_per_second, config.requests_per_two_minutes)
    return RiotApiClient(config, http_cache, match_store, session=session, limiter=limiter)


class _LazyMatchSampleStore:
    """Defers building the real `PeerMatchSampleStore` until first genuine use.

    `PeerMatchSampleStore.__init__` calls `create_index` -- a real Mongo
    round-trip -- so building it eagerly in `PeersServicer.__init__` would
    touch Mongo on every servicer construction, even for requests that never
    reach level 2 (most don't) and for tests that inject fakes for
    everything else but don't care about Phase 2's shared match cache. This
    proxy is passed to `resolve_peer_baseline` in its place and only
    triggers the real construction the first time a `SamplingTask` actually
    calls `find_candidates`/`upsert_rows` on it (i.e. only once live
    sampling genuinely starts).
    """

    def __init__(self, factory: "Callable[[], Any]") -> None:
        self._factory = factory
        self._store: Any = None
        self._lock = threading.Lock()

    def _get(self) -> Any:
        if self._store is None:
            with self._lock:
                if self._store is None:
                    self._store = self._factory()
        return self._store

    def find_candidates(self, **kwargs: Any) -> list[dict[str, Any]]:
        return self._get().find_candidates(**kwargs)

    def upsert_rows(self, *args: Any, **kwargs: Any) -> None:
        self._get().upsert_rows(*args, **kwargs)


class _InFlightResolution:
    """One shared `resolve_peer_baseline` call, possibly awaited by several callers."""

    __slots__ = ("future", "started")

    def __init__(self, future: "Future[PeerBaseline | None]", started: threading.Event) -> None:
        self.future = future
        self.started = started


class PeersServicer(peers_pb2_grpc.PeersServiceServicer):
    """Implements PeersService by running `resolve_peer_baseline` unmodified.

    A same-thread call is attempted first (`ThreadPoolExecutor.submit` +
    bounded `Future.result(timeout=...)`); when it finishes inside
    `fast_path_timeout_s` (the store/static-fallback levels 0/1/3/4/5, which
    are all local reads), the response carries the resolved baseline directly
    (`cached=True`). When it doesn't -- almost certainly because it fell
    through to level 2's live Riot sampling, or is queued behind other
    in-flight work -- `RequestBaseline` returns immediately with `cached=False`
    and a `request_id`, and lets the same future keep running in the
    background; a completion callback then calls back into
    `RunnerServiceStub.NotifyPeerBaselineReady` with the result. Identical
    concurrent requests (same champion/role/platform/tier) share one
    in-flight resolution instead of each launching their own (see module
    docstring, "Concurrency").
    """

    def __init__(
        self,
        peer_store: PeerSampleStore | None = None,
        riot_client_factory: "Callable[[str], Any] | None" = None,
        runner_target: str | None = None,
        fast_path_timeout_s: float = FAST_PATH_TIMEOUT_S,
        executor: ThreadPoolExecutor | None = None,
        default_platform: str | None = None,
        match_sample_store: "PeerMatchSampleStore | None" = None,
    ) -> None:
        # Built once and reused for both defaults below (Phase 8, Task 1) --
        # avoids opening a second Mongo client purely for the raw-match store
        # when both `peer_store` and `riot_client_factory` fall back to their
        # production defaults.
        default_mongo_client = (
            _build_default_mongo_client()
            if peer_store is None or riot_client_factory is None
            else None
        )
        self._peer_store = (
            peer_store
            if peer_store is not None
            else _build_default_peer_store(default_mongo_client)
        )
        # Phase 2 shared cross-champion/cross-tier match cache (RFC "Batched,
        # Round-Robin Live Sampling for PEERS", §5.2) -- passed through to
        # `resolve_peer_baseline` so every `SamplingTask` can both read from it
        # (before live-scanning its own key) and write to it (every downloaded
        # match's other participants, regardless of what champion the task
        # was actually sampling). Wrapped in `_LazyMatchSampleStore` when not
        # explicitly injected so building it (a real Mongo `create_index`
        # round-trip) only happens on first genuine live-sampling use, not on
        # every servicer construction -- see that class's docstring.
        self._match_sample_store = (
            match_sample_store
            if match_sample_store is not None
            else _LazyMatchSampleStore(_build_default_match_sample_store)
        )
        self._default_platform = (default_platform or _resolve_default_platform()).strip().lower()
        self._riot_client_factory: "Callable[[str], Any]" = (
            riot_client_factory
            or self._build_default_riot_client_factory(default_mongo_client)
        )
        # Pool of per-platform clients, built lazily and never mutated after
        # creation -- see module docstring, "Platform routing".
        self._riot_clients: dict[str, Any] = {}
        self._riot_clients_lock = threading.Lock()
        self._runner_target = runner_target or os.environ.get("RUNNER_GRPC_TARGET", "localhost:50051")
        self._fast_path_timeout_s = fast_path_timeout_s
        self._executor = executor or ThreadPoolExecutor(
            max_workers=int(os.environ.get("PEERS_MAX_CONCURRENT_BASELINES", "4")),
            thread_name_prefix="peers-baseline",
        )
        self._inflight: dict[tuple[str, str, str, str, str], _InFlightResolution] = {}
        # RLock, not Lock: _get_or_submit calls future.add_done_callback(_cleanup)
        # while still holding this lock. If the resolution is fast enough to
        # already be done by that point (store hits, static fallback -- both
        # near-instant), concurrent.futures invokes the callback SYNCHRONOUSLY,
        # on this same thread, before add_done_callback returns -- and _cleanup
        # itself acquires this lock. A plain Lock deadlocks on that reentrant
        # acquisition; a real, timing-dependent deadlock, not test flakiness --
        # it only shows up when the executor thread wins the race often enough
        # (reliably reproduced under pytest-xdist's parallel workers, rare but
        # possible in production under load too).
        self._inflight_lock = threading.RLock()

        # RFC "PEERS priority scheduling...", §3/§4: wire the idle-time
        # pre-warm + patch-changeover coordinator onto whichever
        # SamplingScheduler `resolve_peer_baseline` actually uses by default
        # -- see `configure_scheduler_idle_hook`'s docstring for why this
        # goes through that seam rather than PeersServicer owning its own
        # scheduler instance (it doesn't; the scheduler is a process-wide
        # singleton lazily built inside `analysis.peer.baseline`). Gated by
        # `PEERS_ENABLE_PREWARM_COORDINATOR` -- on by default in production;
        # see that flag's own docstring for why tests are the ones that opt
        # out, not production that opts in.
        self._idle_coordinator: "_IdleCoordinator | None" = None
        if PEERS_ENABLE_PREWARM_COORDINATOR:
            scheduler = _get_default_scheduler()
            self._idle_coordinator = _IdleCoordinator(
                scheduler=scheduler,
                peer_store=self._peer_store,
                riot_client_for=self._riot_client_for,
                default_platform=self._default_platform,
            )
            configure_scheduler_idle_hook(self._idle_coordinator)
            # `SamplingScheduler.start()` is otherwise only called lazily, by
            # `_enqueue_sampling_task` the first time a real request falls
            # through to live sampling (`baseline.py:345`) -- without this
            # explicit call, a freshly started/restarted PEERS process with
            # no real traffic yet never spins up the scheduler's background
            # worker threads at all, so `_worker_loop` never runs, `on_idle`
            # never fires, and pre-warm silently never does anything until
            # the first real request happens to trigger it organically --
            # confirmed live: a redeployed instance sat completely idle,
            # logging nothing, because of exactly this. Pre-warm's whole
            # point is to warm the cache BEFORE a real request needs it, so
            # the workers must start at process startup, not on first use.
            scheduler.start()

    @property
    def peer_store(self) -> PeerSampleStore:
        """The `PeerSampleStore` this servicer resolves baselines against.

        Exposed so `__main__.serve()` can start
        `start_match_sample_coverage_refresher` against the same store
        without opening a second Mongo connection.
        """
        return self._peer_store

    @staticmethod
    def _build_default_riot_client_factory(
        mongo_client: "pymongo.MongoClient | None" = None,
    ) -> "Callable[[str], RiotApiClient]":
        """Build the production per-platform client factory.

        Raises eagerly (before any request) when `PEERS_RIOT_API_KEY` is unset,
        with a message naming the correct env var -- `AppConfig`'s own
        validator would raise here too, but its message names `RIOT_API_KEY`,
        the wrong variable for PEERS. The API-key check runs BEFORE touching
        `mongo_client`/building one, so a zero-arg call (as
        `PeersServicer.__init__` makes when `riot_client_factory` isn't
        overridden, and as this method's own dedicated test calls it) still
        fails fast on a missing key with no Mongo connection attempted.

        `mongo_client`: reuse `PeersServicer`'s existing Mongo client (built
        for `PeerSampleStore`, see `__init__`) rather than opening a second
        one -- Phase 8, Task 1. Falls back to building its own when called
        standalone (e.g. the dedicated unit test above).
        """
        api_key = os.environ.get("PEERS_RIOT_API_KEY", "")
        if not api_key:
            raise RuntimeError(
                "Missing Riot API key for PEERS. Set PEERS_RIOT_API_KEY in the "
                "environment or a .env file (get one at https://developer.riotgames.com)."
            )
        cache_dir = Path(os.environ.get("PEERS_CACHE_DIR", ".cache/peers"))
        http_cache = HttpCache(cache_dir / "http")
        mongo_uri = os.environ.get("MONGO_URI", "mongodb://localhost:27017/league_stats")
        client = mongo_client or pymongo.MongoClient(mongo_uri)
        # Shared across the whole platform pool -- see _build_riot_client_for_platform.
        match_store = RawMatchStore(client, db_name=_db_name_from_uri(mongo_uri))

        def factory(platform: str) -> RiotApiClient:
            return _build_riot_client_for_platform(
                platform, api_key=api_key, http_cache=http_cache, match_store=match_store
            )

        return factory

    def _riot_client_for(self, platform: str) -> Any:
        """Return the (lazily built, cached) client for `platform`."""
        with self._riot_clients_lock:
            client = self._riot_clients.get(platform)
            if client is None:
                client = self._riot_client_factory(platform)
                self._riot_clients[platform] = client
            return client

    def _get_or_submit(
        self,
        key: tuple[str, str, str, str, str],
        riot_client: Any,
        adapter: _PeerStoreAdapter,
        ranked: RankedEntry,
        champion: str,
        role: str,
        exclude_puuid: str | None,
        patch: str,
    ) -> _InFlightResolution:
        """Return the in-flight resolution for `key`, submitting a new one if needed.

        `key` includes `patch` (see the field's tuple position) so a stale
        in-flight request for a different patch is never joined -- see
        finding 1 of the final whole-branch review.
        """
        with self._inflight_lock:
            existing = self._inflight.get(key)
            if existing is not None and not existing.future.done():
                PEERS_DEDUPED_REQUESTS_TOTAL.inc()
                log.info("RequestBaseline deduped onto in-flight resolution for key=%s", key)
                return existing

            log.info("RequestBaseline starting new resolution for key=%s", key)
            started = threading.Event()

            def _run() -> PeerBaseline | None:
                started.set()
                self._update_queued_gauge()
                PEERS_INFLIGHT_BASELINES.inc()
                resolution_start = time.perf_counter()
                # Both the success and failure paths must record the duration/counter
                # metrics -- previously only the success path did, so failed
                # resolutions were invisible in metrics (finding 5 of the final
                # whole-branch review).
                try:
                    baseline = resolve_peer_baseline(
                        riot_client,
                        adapter,
                        ranked,
                        champion,
                        role,
                        exclude_puuid=exclude_puuid,
                        patch=patch,
                        match_sample_store=self._match_sample_store,
                    )
                except Exception:
                    duration = time.perf_counter() - resolution_start
                    PEERS_BASELINE_RESOLUTION_DURATION.labels(source="error").observe(duration)
                    PEERS_BASELINE_RESOLUTIONS_TOTAL.labels(source="error").inc()
                    log.exception(
                        "RequestBaseline resolution failed for key=%s after %.1fs", key, duration
                    )
                    raise
                finally:
                    PEERS_INFLIGHT_BASELINES.dec()
                duration = time.perf_counter() - resolution_start
                source = "live_sample" if baseline is not None and baseline.fallback_level == 2 else "cached"
                PEERS_BASELINE_RESOLUTION_DURATION.labels(source=source).observe(duration)
                PEERS_BASELINE_RESOLUTIONS_TOTAL.labels(source=source).inc()
                log.info(
                    "RequestBaseline resolution completed for key=%s: source=%s, took=%.1fs",
                    key,
                    source,
                    duration,
                )
                return baseline

            future = self._executor.submit(_run)
            record = _InFlightResolution(future=future, started=started)
            self._inflight[key] = record
            self._update_queued_gauge()

            def _cleanup(_future: "Future[PeerBaseline | None]") -> None:
                with self._inflight_lock:
                    if self._inflight.get(key) is record:
                        del self._inflight[key]
                self._update_queued_gauge()

            future.add_done_callback(_cleanup)
            return record

    def _update_queued_gauge(self) -> None:
        """Recompute `PEERS_QUEUED_BASELINES` from the current `_inflight` snapshot.

        Called (under `_inflight_lock`, reentrant since it's an `RLock`) right
        after `_inflight` gains or loses an entry, and right after a worker
        thread actually starts running one (`started.set()` in `_run` above) --
        the three moments that can change how many entries are queued vs.
        running.
        """
        with self._inflight_lock:
            queued = sum(1 for record in self._inflight.values() if not record.started.is_set())
            PEERS_QUEUED_BASELINES.set(queued)

    def RequestBaseline(self, request, context):
        champion = request.champion
        role = request.lane
        tier, division = _parse_rank(request.rank)
        if not champion or not role or not tier:
            context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
            context.set_details("champion, lane and a parseable rank are required")
            return peers_pb2.RequestBaselineResponse()

        # Request field wins when present, must be a real platform code --
        # rejected outright otherwise (closes both the "NA1" vs "na1" store-key
        # mismatch and the risk of an arbitrary string reaching a URL host that
        # carries PEERS' own Riot API key). PEERS_PLATFORM is a last-resort
        # default (e.g. for a caller that hasn't been updated yet), then
        # `self._default_platform`, resolved once at construction the same way.
        requested_platform = request.platform.strip().lower() if request.platform else ""
        if requested_platform and requested_platform not in VALID_PLATFORMS:
            context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
            context.set_details(
                f"unknown platform {request.platform!r}; must be one of {sorted(VALID_PLATFORMS)}"
            )
            return peers_pb2.RequestBaselineResponse()

        env_platform = os.environ.get("PEERS_PLATFORM", "").strip().lower()
        platform = requested_platform or env_platform or self._default_platform
        exclude_puuid = request.exclude_puuid or None

        # RankedEntry.league_points/wins/losses are never read anywhere in
        # resolve_peer_baseline's call graph (only `.tier`/`.label`/`.rank`
        # are) -- see analysis/peer/rank_scope.py.
        ranked = RankedEntry(tier=tier, rank=division, league_points=0, wins=0, losses=0)
        adapter = _PeerStoreAdapter(self._peer_store)
        client = self._riot_client_for(platform)
        request_id = str(uuid.uuid4())
        patch = request.patch

        # patch is part of the dedup key: a stale in-flight request for a
        # different patch must never be joined (finding 1 of the final
        # whole-branch review) -- see `_get_or_submit`'s docstring.
        dedup_key = (champion.lower(), role.upper(), platform, tier.upper(), patch)
        record = self._get_or_submit(
            dedup_key, client, adapter, ranked, champion, role, exclude_puuid, patch
        )

        PEERS_FAST_PATH_ATTEMPTS_TOTAL.inc()
        try:
            baseline = record.future.result(timeout=self._fast_path_timeout_s)
        except FutureTimeoutError:
            started = record.started.is_set()
            PEERS_FAST_PATH_TIMEOUTS_TOTAL.labels(started=str(started)).inc()
            log.info(
                "RequestBaseline fast path timed out for %s %s (%s, tier=%s), request_id=%s: %s",
                champion,
                role,
                platform,
                tier,
                request_id,
                "already running (likely live sampling)"
                if started
                else "still queued behind other in-flight work",
            )
            task_key = task_key_for(platform, tier, champion, role, patch)
            record.future.add_done_callback(
                lambda f: self._on_resolved(f, request_id, champion, role, request.rank, task_key)
            )
            return peers_pb2.RequestBaselineResponse(request_id=request_id, cached=False)
        except Exception as exc:  # noqa: BLE001 -- report as a request-level failure
            log.exception("resolve_peer_baseline failed for %s %s", champion, role)
            return peers_pb2.RequestBaselineResponse(request_id=request_id, error=str(exc))

        if baseline is None:
            return peers_pb2.RequestBaselineResponse(
                request_id=request_id,
                cached=True,
                error=f"no peer baseline available for {champion} {role} at {request.rank!r}",
            )
        return peers_pb2.RequestBaselineResponse(
            request_id=request_id,
            cached=True,
            baseline_json=_encode_baseline(baseline),
        )

    def PeekBaseline(self, request, context):
        """Read-only: returns whatever is currently cached for this key, or
        found=false. Never calls `resolve_peer_baseline` (the only path to a
        `SamplingTask`) -- a genuine cache miss is reported as-is, it never
        falls through to live sampling. See the `.proto`'s own docstring for
        the intended caller (api-ui's lazy peer-comparison refresh on report
        read, design "peers-scheduling-and-cleanup" RFC, lazy-refresh
        section)."""
        champion = request.champion
        role = request.lane
        tier, _division = _parse_rank(request.rank)
        if not champion or not role or not tier:
            context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
            context.set_details("champion, lane and a parseable rank are required")
            return peers_pb2.PeekBaselineResponse()

        requested_platform = request.platform.strip().lower() if request.platform else ""
        if requested_platform and requested_platform not in VALID_PLATFORMS:
            context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
            context.set_details(
                f"unknown platform {request.platform!r}; must be one of {sorted(VALID_PLATFORMS)}"
            )
            return peers_pb2.PeekBaselineResponse()
        env_platform = os.environ.get("PEERS_PLATFORM", "").strip().lower()
        platform = requested_platform or env_platform or self._default_platform

        snapshot = read_live_cache(platform, tier, champion, role, patch=request.patch)
        if snapshot is None:
            return peers_pb2.PeekBaselineResponse(found=False)
        baseline = _baseline_from_snapshot(snapshot, champion, role, level=2)
        return peers_pb2.PeekBaselineResponse(
            found=True,
            baseline_json=_encode_baseline(baseline),
            still_refining=snapshot.still_refining,
        )

    def _on_resolved(
        self,
        future: "Future[PeerBaseline | None]",
        request_id: str,
        champion: str,
        role: str,
        rank: str,
        task_key: "tuple[str, str, str, str, str]",
    ) -> None:
        """Runs once a backgrounded resolution finishes (possibly shared by several callers).

        Design "Progressive peer-comparison updates during live sampling"
        §3.1: when the delivered result is itself still `still_refining`
        (an interim snapshot -- the underlying `SamplingTask` is still
        running in the background), this also registers a progressive
        listener on `task_key` so every later interim/finalize snapshot for
        the same task keeps pushing further `NotifyPeerBaselineReady`
        callbacks for THIS `request_id`, instead of RUNNER only ever hearing
        about the one snapshot that happened to exist when this future
        completed.
        """
        try:
            baseline = future.result()
        except Exception as exc:  # noqa: BLE001 -- must still notify RUNNER
            log.exception("Background peer baseline resolution failed for %s %s", champion, role)
            self._notify_runner(
                request_id, champion, role, rank, baseline_json="", error=str(exc), still_refining=False
            )
            return
        if baseline is None:
            self._notify_runner(
                request_id,
                champion,
                role,
                rank,
                baseline_json="",
                error=f"no peer baseline available for {champion} {role} at {rank!r}",
                still_refining=False,
            )
            return
        self._notify_runner(
            request_id,
            champion,
            role,
            rank,
            baseline_json=_encode_baseline(baseline),
            error="",
            still_refining=baseline.still_refining,
        )
        if baseline.still_refining:

            def _on_progress(snapshot: BenchmarkSnapshot) -> None:
                updated = _baseline_from_snapshot(snapshot, champion, role, level=2)
                self._notify_runner(
                    request_id,
                    champion,
                    role,
                    rank,
                    baseline_json=_encode_baseline(updated),
                    error="",
                    still_refining=snapshot.still_refining,
                )

            register_progressive_listener(task_key, _on_progress)

    def _notify_runner(
        self,
        request_id: str,
        champion: str,
        role: str,
        rank: str,
        *,
        baseline_json: str,
        error: str,
        still_refining: bool = False,
    ) -> None:
        try:
            with grpc.intercept_channel(
                grpc.insecure_channel(self._runner_target), TraceClientInterceptor()
            ) as channel:
                stub = runner_pb2_grpc.RunnerServiceStub(channel)
                stub.NotifyPeerBaselineReady(
                    runner_pb2.PeerBaselineReadyRequest(
                        request_id=request_id,
                        champion=champion,
                        lane=role,
                        rank=rank,
                        baseline_json=baseline_json,
                        error=error,
                        still_refining=still_refining,
                    )
                )
        except Exception as exc:  # noqa: BLE001 -- a done-callback must never raise silently
            log.error("Failed to notify RUNNER for request_id=%s: %s", request_id, exc)
