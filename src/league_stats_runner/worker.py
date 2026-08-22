"""Background worker: claims queued jobs and runs the two-stage pipeline.

Stage A produces the base report (everything except peer benchmarks) and
flips the job to ``report_ready`` so the user can open it immediately.
Stage B builds the rank-peer comparison per build and re-renders each report
as its peer data lands. A stage-B failure is soft: the base report stays
served and the job still completes.
"""

from __future__ import annotations

import json
import queue
import threading
import time
from pathlib import Path
from typing import Any, Callable

import pandas as pd
import pymongo
from prometheus_client import Histogram

from league_stats_api_ui.chat import OUTBOUND_RPC_DURATION
from league_stats_peers.analysis.peer import current_patch, finish_peer_comparison
from league_stats_peers.analysis.peer.baseline import PeerBaseline
from league_stats_common.core.champions import champion_slug, players_group_slug
from league_stats_common.core.config import PLATFORM_TO_REGION, PlayerIdentity, WebConfig, load_config
from league_stats_common.core.models import PeerComparisonResult, RankedEntry
from league_stats_common.infra.cache import HttpCache
from league_stats_common.infra.ddragon_assets import DDragonAssets
from league_stats_common.infra.mongo import db_name_from_uri as _db_name_from_uri
from league_stats_runner.infra.raw_match_store import RawMatchStore
from league_stats_common.infra.riot_api import RiotApiClient, RiotApiError, shared_rate_limiter
from league_stats_runner.ingest.parser import BuildPool
from league_stats_runner.pipeline.fetch import fetch_matches, group_records, resolve_player_contexts
from league_stats_runner.pipeline.orchestrator import (
    BuildAnalysisResult,
    BuildBatch,
    NoEligibleBuildsError,
    analyze_build,
    patch_report_peer_comparison,
    prepare_builds,
    report_needs_peer_comparison,
    resolve_ranked,
    should_skip_unchanged_build,
)
from league_stats_runner.pipeline.services import PlayerContext, Services
from league_stats_runner.presentation.report import discover_player_builds
from league_stats_common.infra import jobs as job_states
from league_stats_common.infra.jobs import JOB_KIND_REGENERATE, JobStore, decode_players
from league_stats_runner.progress import JobCancelled, JobProgressReporter
from league_stats_common.utils import get_logger, set_trace_id

CHAT_ENDPOINT = "/api/chat"

# RUNNER's own pipeline-stage/PEERS-call metrics. Module-level, following the
# same pattern `runner/service.py`'s `RUNNER_JOB_DURATION`/`RUNNER_JOBS_TOTAL`
# already established, so re-importing this module (as every test importing
# `league_stats_runner.worker` does) never re-registers the same collector.
RUNNER_STAGE_DURATION = Histogram(
    "runner_stage_duration_seconds",
    "Time one pipeline stage took within execute_job, labeled by stage.",
    ["stage"],  # fetch | analyze | peer
)
RUNNER_PEERS_REQUEST_DURATION = Histogram(
    "runner_peers_request_duration_seconds",
    "Time RUNNER's synchronous RequestBaseline call to PEERS took to return.",
    ["outcome"],  # cached_hit | cached_miss | rpc_error | peers_error
)
RUNNER_PEERS_ASYNC_WAIT_DURATION = Histogram(
    "runner_peers_async_wait_duration_seconds",
    "Time RUNNER waited on PEERS' async NotifyPeerBaselineReady callback after "
    "a cached=False RequestBaseline response, per pool.",
    ["outcome"],  # delivered | timed_out | cancelled
    buckets=(0, .5, 1, 2, 5, 10, 30, 60, 120, 300, 600, 900),
)

# Deadlines for the gRPC RPCs to RUNNER (see `_execute_job_via_runner`), so a
# RUNNER that's reachable but hung doesn't block the worker thread forever.
_RUNNER_ENQUEUE_TIMEOUT_S = 30.0
# Raised from 1800s (30min) to 5400s (90min): a multi-account tracked group
# (several riot ids analyzed together) can need well over 30 minutes of
# Riot-API-rate-limited match downloads across all its accounts before RUNNER
# ever reports progress back over this stream. When this fired, api-ui gave
# up and marked the job FAILED while RUNNER kept executing it in the
# background, orphaned from any job/state tracking -- confirmed live in
# production against a 3-account group needing ~1300 total matches.
_RUNNER_STREAM_TIMEOUT_S = 5400.0

# Deadline for the `RequestBaseline` unary call itself (see
# `_build_peer_for_pool_via_grpc`). PEERS' own `RequestBaseline` blocks
# internally for up to its `FAST_PATH_TIMEOUT_S` (3s, `peers/service.py`)
# before answering with `cached=False`, so this deadline only needs to cover
# that plus network/scheduling slack -- it is deliberately NOT the deadline
# for the whole baseline resolution (see `_PEERS_BASELINE_WAIT_TIMEOUT_S` below
# for that).
_PEERS_REQUEST_TIMEOUT_S = 10.0
# How long a stage-B thread waits for PEERS' async `NotifyPeerBaselineReady`
# callback (via `RunnerServicer`, replayed here through
# `_peer_baseline_waiters`) after a `cached=False` response, before giving up
# on THIS ONE POOL's peer comparison and moving on to the next pool in stage
# B's loop -- this is a per-pool budget, not a per-job one.
#
# There is no existing precedent in this codebase for this specific
# wait-for-an-async-callback-inside-a-sync-stage shape, so this value is a
# judgment call, not a derived constant. Sized against PEERS' own live-sampling
# cost, not against RUNNER's job-level budget: PEERS' module docstring notes a
# live sample can issue up to `MAX_MATCH_DOWNLOADS` (400) rate-limited Riot
# HTTP calls -- at PEERS' default `requests_per_two_minutes=95`, downloading
# 400 matches alone takes roughly 400/95*120s =~ 505s, before league-v4 seed
# lookups and per-puuid rank verification calls on top, and before accounting
# for PEERS' own `_PeerStoreAdapter.save_match` being a documented no-op (no
# cross-request raw-match cache on PEERS' side, so a request that falls
# through to live sampling cannot benefit from a previous request's downloads).
# 900s leaves real headroom above that ~505s+ floor. The actual outer
# constraint this must stay under is RUNNER's own `StreamJobProgress` deadline
# on the *caller* side (`_RUNNER_STREAM_TIMEOUT_S`, 5400s, in the
# monolith-to-RUNNER gRPC client) -- since stage B can call this once per pool
# in `batch.pools`, several pools each waiting close to this timeout could in
# principle exceed that 5400s budget for a job with many builds; that
# per-job/per-pool interaction is a known, not-yet-solved limit of this design
# (see task-3-report.md), not something this constant alone can fix. A build
# whose baseline never arrives in time skips its peer comparison (soft
# failure, same as any other peer-resolution exception) rather than hanging
# stage B forever.
_PEERS_BASELINE_WAIT_TIMEOUT_S = 900.0

# How often the grpc-mode wait below re-checks for cancellation instead of
# blocking in one long `waiter.get(timeout=900)` call (finding 2 of the final
# whole-branch review). A single long blocking wait meant a cancelled job
# kept burning up to the full budget PER REMAINING POOL before it noticed the
# cancellation, since `_ensure_not_cancelled` in `_run_stage_b` only runs
# between pools. Short enough to notice a cancellation promptly, long enough
# not to spam `JobStore` with polling overhead.
_PEERS_BASELINE_POLL_INTERVAL_S = 5.0

# Keyed by PeersService's `request_id` (RequestBaselineResponse.request_id):
# registered by `_build_peer_for_pool_via_grpc` right after a `cached=False`
# response, consumed by `resolve_peer_baseline_notification` when RUNNER's
# `RunnerServicer.NotifyPeerBaselineReady` receives PEERS' callback for that
# request_id. Mirrors the `queue.SimpleQueue`-per-id shape
# `RunnerServicer`/`RunnerJobAdapter` already use for job progress -- see
# `runner/service.py`'s module docstring for why RUNNER's servicer is
# synchronous, not `grpc.aio`, and therefore needs a plain thread-safe handoff
# like this rather than an `asyncio.Event`.
_peer_baseline_waiters: dict[str, "queue.SimpleQueue[dict[str, Any]]"] = {}
_peer_baseline_waiters_lock = threading.Lock()

# Lost-wakeup guard (fix round 1): `concurrent.futures.Future.add_done_callback`
# (PEERS' own `_get_or_submit`/`RequestBaseline`, `peers/service.py`) fires
# SYNCHRONOUSLY, on the calling thread, if the future is already done at the
# moment the callback is attached. That callback is what eventually calls
# `NotifyPeerBaselineReady` -- and PEERS attaches it (and can therefore fire
# it) *before* `RequestBaseline`'s `cached=False` response has even been
# returned to RUNNER, let alone before `_build_peer_for_pool_via_grpc` has
# called `_register_peer_baseline_waiter` for that `request_id`. So a
# notification can genuinely arrive here before any waiter exists for it --
# not a rare edge case, but the single most likely timing for a resolution
# that finishes "just barely too slow" for PEERS' own fast-path window.
# Without this buffer, `resolve_peer_baseline_notification` would find no
# waiter, report `ok=False`, and the already-arrived result would be silently
# dropped -- `_build_peer_for_pool_via_grpc` would then block the full
# `_PEERS_BASELINE_WAIT_TIMEOUT_S` for a baseline that had already landed.
# Keyed the same way as `_peer_baseline_waiters`; values are
# `(stored_at_monotonic, {"baseline_json":..., "error":...})`.
_peer_baseline_orphans: dict[str, tuple[float, dict[str, Any]]] = {}
# The real race window above is normally milliseconds (the gap between PEERS
# returning `cached=False` and this process calling
# `_register_peer_baseline_waiter`), so this TTL only needs to survive
# scheduling jitter, not model any real waiting period -- an orphan nobody
# claims within it was never going to be claimed at all, since each
# `RequestBaseline` call mints a fresh `request_id` (a given id is only ever
# waited on once). This bounds the dict's size against a `request_id` that
# NotifyPeerBaselineReady is called with directly (bypassing a real PEERS
# instance, e.g. a stray/duplicate/malicious callback) and is never claimed.
_PEER_BASELINE_ORPHAN_TTL_S = 120.0


def _prune_expired_peer_baseline_orphans_locked() -> None:
    """Drop orphaned notifications older than `_PEER_BASELINE_ORPHAN_TTL_S`.

    Must be called while holding `_peer_baseline_waiters_lock`.
    """
    now = time.monotonic()
    expired = [
        request_id
        for request_id, (stored_at, _payload) in _peer_baseline_orphans.items()
        if now - stored_at > _PEER_BASELINE_ORPHAN_TTL_S
    ]
    for request_id in expired:
        del _peer_baseline_orphans[request_id]


def _register_peer_baseline_waiter(request_id: str) -> "queue.SimpleQueue[dict[str, Any]]":
    """Register a waiter for PEERS' async callback for `request_id`.

    Checks `_peer_baseline_orphans` first (see that dict's module comment for
    the exact lost-wakeup race this closes): if the notification already
    arrived before this call happened, the returned queue already has the
    result in it instead of blocking on a queue nothing will ever put
    anything into.
    """
    events: "queue.SimpleQueue[dict[str, Any]]" = queue.SimpleQueue()
    with _peer_baseline_waiters_lock:
        orphan = _peer_baseline_orphans.pop(request_id, None)
        if orphan is not None:
            events.put(orphan[1])
            return events
        _peer_baseline_waiters[request_id] = events
    return events


def resolve_peer_baseline_notification(
    request_id: str, *, baseline_json: str, error: str, still_refining: bool = False
) -> bool:
    """Deliver RUNNER's real `NotifyPeerBaselineReady` callback to whichever
    stage-B thread is waiting on `request_id`, if any.

    Called by `RunnerServicer.NotifyPeerBaselineReady` (`runner/service.py`) --
    this is the real Phase 3 implementation of the coordination Phase 1's
    version of that method left as a logging-only stub.

    Design "Progressive peer-comparison updates during live sampling" §3.1/
    §3.2: unlike the original one-shot version, this does NOT remove the
    waiter from `_peer_baseline_waiters` on delivery -- PEERS can (and now
    does) call `NotifyPeerBaselineReady` more than once for the same
    `request_id` while its `SamplingTask` is still `still_refining`, and each
    push must reach the same stage-B thread's wait loop. The waiter is only
    ever deregistered by the consumer itself (`_build_peer_for_pool_via_grpc`'s
    wait loop below), once it decides to stop waiting -- a terminal
    (`still_refining=False`) delivery, an error, a timeout, or cancellation.

    Returns ``False`` when no waiter is *currently* registered for
    `request_id` -- this covers two different cases the caller can't tell
    apart, and doesn't need to: (a) the stage-B thread already gave up after
    `_PEERS_BASELINE_WAIT_TIMEOUT_S` (or a prior terminal delivery) and moved
    on, or `request_id` never belonged to a request this process made --
    nothing useful to do, the notification is simply logged and dropped;
    (b) the genuine lost-wakeup race documented on `_peer_baseline_orphans`
    above, where this notification arrived before
    `_register_peer_baseline_waiter` ran for the same `request_id` -- in that
    case the payload is NOT dropped, it's stashed in `_peer_baseline_orphans`
    for `_register_peer_baseline_waiter` to pick up immediately once it does
    run. Returning ``False`` here still accurately reports "not delivered to
    a live waiter"; the value living on unclaimed for a little while is what
    makes the race harmless either way.
    """
    with _peer_baseline_waiters_lock:
        events = _peer_baseline_waiters.get(request_id)
        if events is None:
            _prune_expired_peer_baseline_orphans_locked()
            _peer_baseline_orphans[request_id] = (
                time.monotonic(),
                {"baseline_json": baseline_json, "error": error, "still_refining": still_refining},
            )
    if events is None:
        return False
    events.put({"baseline_json": baseline_json, "error": error, "still_refining": still_refining})
    return True


def _slug_for_players(players: list[dict[str, Any]]) -> str:
    """Filesystem group slug for a player list."""
    return players_group_slug(
        [(str(p["riot_id"]), str(p["tagline"])) for p in players]
    )


def _players_from_reports(output_dir: Path, job_slug: str) -> list[dict[str, Any]]:
    """Recover pooled identities from on-disk report metadata when the DB drifted."""
    from league_stats_common.core.models import solo_rank_fields

    builds = discover_player_builds(output_dir / "reports" / job_slug)
    for build in builds:
        raw = build.get("players")
        if not isinstance(raw, list) or not raw:
            continue
        players: list[dict[str, Any]] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            name = str(item.get("riot_id", "")).strip()
            tag = str(item.get("tagline", "")).strip()
            if not name or not tag:
                continue
            entry: dict[str, Any] = {"riot_id": name, "tagline": tag}
            raw_icon = item.get("profile_icon_id")
            if raw_icon is not None:
                try:
                    entry["profile_icon_id"] = int(raw_icon)
                except (TypeError, ValueError):
                    pass
            entry.update(solo_rank_fields(item))
            players.append(entry)
        if players and _slug_for_players(players) == job_slug:
            return players
    return []


def _tracked_players_for_job(
    job: dict[str, Any],
    store: JobStore | None = None,
    *,
    output_dir: Path | None = None,
) -> list[dict[str, Any]]:
    """Resolve identities for a job, recovering from the registry/disk if needed.

    Report output paths are derived from the player list. If ``players_json`` is
    incomplete relative to ``player_slug``, prefer the registry row (or on-disk
    group metadata) so solo and pooled group directories stay aligned with the job.
    """
    tracked = decode_players(
        job.get("players_json"),
        riot_id=str(job.get("riot_id", "")),
        tagline=str(job.get("tagline", "")),
    )
    if not tracked and job.get("players"):
        tracked = list(job["players"])
    job_slug = str(job.get("player_slug", ""))
    if tracked and job_slug and _slug_for_players(tracked) == job_slug:
        return tracked
    if store is not None and job_slug:
        row = store.get_player(job_slug)
        registered = list(row.get("players") or []) if row else []
        if registered and _slug_for_players(registered) == job_slug:
            return registered
    if output_dir is not None and job_slug:
        from_disk = _players_from_reports(output_dir, job_slug)
        if from_disk:
            return from_disk
    return tracked or [
        {
            "riot_id": str(job.get("riot_id", "")),
            "tagline": str(job.get("tagline", "")),
        }
    ]


# Process-wide Mongo clients keyed by URI, mirroring `shared_rate_limiter`
# above: reused across jobs so RUNNER's `RawMatchStore` doesn't open a
# fresh connection pool per job. `RawMatchStore.close()` is deliberately a
# no-op for the same reason -- nothing here ever closes this client either.
_SHARED_MONGO_CLIENTS: dict[str, pymongo.MongoClient] = {}
_SHARED_MONGO_CLIENTS_LOCK = threading.Lock()


def _build_mongo_client(mongo_uri: str) -> pymongo.MongoClient:
    """Return the process-wide Mongo client for this URI, creating it once.

    A separate seam (rather than calling `pymongo.MongoClient` directly from
    `_build_job_services`) so tests can monkeypatch this one function to
    return a `mongomock.MongoClient` instead of dialing a real Mongo --
    matching the pattern this module already uses for stubbing gRPC calls
    (e.g. `_build_peer_for_pool_via_grpc`).

    No short `serverSelectionTimeoutMS` here (unlike `analysis.peer.
    benchmark_cache`'s best-effort live cache): `RawMatchStore` is the
    required match persistence for the job pipeline, not an optional cache
    -- there is no fallback path if this client fails fast, so waiting out
    pymongo's ~30s default on a slow/starting-up Mongo is the right
    tradeoff.
    """
    with _SHARED_MONGO_CLIENTS_LOCK:
        client = _SHARED_MONGO_CLIENTS.get(mongo_uri)
        if client is None:
            client = pymongo.MongoClient(mongo_uri)
            _SHARED_MONGO_CLIENTS[mongo_uri] = client
        return client


def _build_job_services(
    job: dict[str, Any],
    web_config: WebConfig,
    reporter: JobProgressReporter,
    *,
    job_store: JobStore | None = None,
) -> Services:
    """Wire pipeline services for one job (shared rate limiter, DB reporter)."""
    tracked = _tracked_players_for_job(
        job, job_store, output_dir=web_config.output_dir
    )
    job_slug = str(job["player_slug"]).strip()
    if job_slug and tracked:
        resolved_slug = _slug_for_players(tracked)
        if resolved_slug != job_slug:
            raise ValueError(
                f"Report folder {job_slug!r} does not match resolved players "
                f"{resolved_slug!r}. Re-submit this player from the home page."
            )
    players = [
        PlayerIdentity(riot_id=entry["riot_id"], tagline=entry["tagline"])
        for entry in tracked
        if entry.get("riot_id") and entry.get("tagline")
    ] or None
    filter_champion = str(job.get("filter_champion") or "").strip() or None
    filter_role = str(job.get("filter_role") or "").strip() or None
    raw_min_games = job.get("min_games")
    min_games: int | None = None
    if raw_min_games is not None:
        try:
            min_games = int(raw_min_games)
        except (TypeError, ValueError):
            min_games = None
    config = load_config(
        riot_id=job["riot_id"],
        tagline=job["tagline"],
        region=job["region"],
        output_dir=web_config.output_dir,
        assets_dir=web_config.assets_dir,
        chat_endpoint=CHAT_ENDPOINT if web_config.gemini_api_key else None,
        players=players,
        filter_champion=filter_champion,
        filter_role=filter_role,
        min_games=min_games,
        # Always write under the URL the user refreshed — never a parallel folder.
        output_reports_slug=job_slug or None,
    )
    # Poll URL must match the report directory slug. Mismatched polls compare
    # timestamps across solo vs group folders and infinite-reload the page.
    config.status_endpoint = f"/api/players/{config.reports_group_slug}"
    config.ensure_directories()
    http_cache = HttpCache(config.http_cache_dir)
    # `MatchStore` (the local on-disk store this replaced) was deleted in
    # Phase 8, Task 1 -- `RawMatchStore` is now the only backing
    # implementation. It never implemented the peer-game methods the
    # now-deleted in-process peer path (`build_peer_for_pool`, removed Phase
    # 9 Task 4) used to need -- moot now since `_run_stage_b` always resolves
    # peers via PEERS over gRPC (`_build_peer_for_pool_via_grpc`), which
    # never touches those methods (see `core/config.py`'s `WebConfig`
    # docstring).
    mongo_client = _build_mongo_client(web_config.runner_mongo_uri)
    store: RawMatchStore = RawMatchStore(
        mongo_client, db_name=_db_name_from_uri(web_config.runner_mongo_uri)
    )
    client = RiotApiClient(
        config,
        http_cache,
        store,
        limiter=shared_rate_limiter(
            config.requests_per_second, config.requests_per_two_minutes
        ),
        progress=reporter,
    )
    assets = DDragonAssets(config)
    return Services(
        config=config,
        http_cache=http_cache,
        store=store,
        client=client,
        assets=assets,
        progress=reporter,
    )


def _friendly_error(exc: Exception, job: dict[str, Any]) -> str:
    """Translate common pipeline failures into user-facing messages."""
    message = str(exc)
    if isinstance(exc, RiotApiError) and "404" in message and "by-riot-id" in message:
        return (
            f"Player {job['riot_id']}#{job['tagline']} was not found on "
            f"{job['region']}. Check the Riot ID, tagline and region."
        )
    return message


def _ensure_not_cancelled(store: JobStore, job_id: int) -> None:
    """Raise :class:`JobCancelled` when the user has cancelled this job."""
    if store.is_cancelled(job_id):
        raise JobCancelled()


def _run_stage_a(
    services: Services,
    store: JobStore,
    job: dict[str, Any],
    batch: BuildBatch,
    new_match_ids: frozenset[str] | set[str] | None,
) -> tuple[RankedEntry | None, bool, dict[tuple[str, str], BuildAnalysisResult]]:
    """Render every eligible build without peer data.

    Builds with an existing report and no newly fetched games are skipped.
    A regenerate job (``new_match_ids is None``) always re-analyses, since
    ``should_skip_unchanged_build`` never skips in that case. Being scoped
    to a single build (an explicit champion refresh) narrows which pools
    are considered — it never bypasses the unchanged-build check by itself,
    since that would force a full rank-comparison rebuild on every refresh
    even when nothing changed.

    Returns:
        The player's ranked entry (fetched once), whether any report is
        available (newly rendered or already on disk), and each analysed
        pool's frames/stats keyed by (champion, role) -- stage B reuses these
        for the same records instead of rebuilding them from scratch, since
        neither depends on peer comparison.
    """
    log = get_logger("worker")
    job_id = int(job["id"])
    ranked: RankedEntry | None = None
    ranked_resolved = False
    available_any = False
    total = len(batch.pools)
    analysed: dict[tuple[str, str], BuildAnalysisResult] = {}
    for index, pool in enumerate(batch.pools, start=1):
        _ensure_not_cancelled(store, job_id)
        records = group_records(batch.records, pool.champion, pool.role)
        if should_skip_unchanged_build(
            services.config, pool, records, new_match_ids
        ):
            log.info("Skipping %s: no new games since last report", pool.build_label)
            store.update_progress(
                job_id,
                detail=f"Skipping {pool.build_label} — no new games ({index}/{total})",
                current=index,
                total=total,
            )
            available_any = True
            continue
        store.update_progress(
            job_id,
            detail=f"Analyzing {pool.build_label} ({index}/{total})",
            current=index,
            total=total,
        )
        if not ranked_resolved:
            if records:
                ranked = resolve_ranked(services, batch, records)
                ranked_resolved = True
        result = analyze_build(
            services, batch, pool, ranked=ranked, peer_comparison=None
        )
        if result.path is not None:
            available_any = True
            analysed[(pool.champion, pool.role)] = result
    return ranked, available_any, analysed


def _build_peer_for_pool_via_grpc(
    services: Services,
    batch: BuildBatch,
    pool: BuildPool,
    ranked: RankedEntry | None,
    web_config: WebConfig,
    *,
    store: JobStore | None = None,
    job_id: int | None = None,
    on_update: "Callable[[PeerComparisonResult, bool], None] | None" = None,
) -> PeerComparisonResult | None:
    """`_run_stage_b`'s sole peer-comparison path (since Phase 9 removed the
    `peers_mode` flag and its in-process fallback): resolves the peer
    baseline by calling PEERS' `RequestBaseline` over gRPC instead of running
    `resolve_peer_baseline` in this process, the way the now-deleted
    `build_peer_for_pool` (`league_stats_runner.pipeline.orchestrator`) used
    to. `build_peer_for_pool` was itself deleted in this same phase's final
    dead-code sweep: its apparent second caller, `orchestrator.run_all_builds`,
    was confirmed to have zero production callers of its own (the CLI shim
    that used to invoke it was deleted in commit `33bd81b`, predating this
    migration), so `build_peer_for_pool` had exactly one real call site all
    along -- this function's predecessor in `_run_stage_b`.

    HARD PRECONDITION (relocated here from `WebConfig`'s removed
    `_warn_on_peers_grpc_topology_precondition` validator, Phase 9): this
    only works when THIS PROCESS is itself running as RUNNER
    (`runner/__main__.py`, i.e. hosting a `RunnerServiceServicer`), because
    PEERS' slow (live-sampling) path calls back into
    `RunnerService.NotifyPeerBaselineReady` (`runner/service.py`) in
    whichever process issued the original `RequestBaseline` call, and that
    callback is only ever routed to this module's own, in-memory
    `_peer_baseline_waiters` registry -- there is no cross-process delivery
    mechanism. Calling this from a process that is NOT RUNNER (e.g. the
    monolith's own web app, running `execute_job` in-process with no
    `RunnerServiceServicer` anywhere in it) does not raise or fail fast:
    every peer comparison that falls through to PEERS' slow path silently
    blocks for the full `_PEERS_BASELINE_WAIT_TIMEOUT_S` waiting for a
    callback that can never arrive, then skips that build's peer comparison
    with only a warning log. Nothing here can verify "is this process
    RUNNER" -- that is a deployment-topology fact, not something derivable
    from `web_config` alone -- so this is a real, silent-failure-shaped risk
    for any caller outside RUNNER's own dispatch, not merely a hypothetical.

    Contract with `PeersServicer.RequestBaseline` (`peers/service.py`):
    a synchronous response either carries `cached=True` plus `baseline_json`
    (the store/static-fallback levels, all local reads PEERS can finish inside
    its own fast-path timeout), or `cached=False` plus a `request_id` when
    resolution fell through to live Riot sampling (or is queued behind other
    in-flight work) -- in which case this function blocks on
    `_peer_baseline_waiters` until RUNNER's own `RunnerServicer.NotifyPeerBaselineReady`
    (called back by PEERS on the same `request_id`) delivers the result, or
    until `_PEERS_BASELINE_WAIT_TIMEOUT_S` elapses. `response.error` (either
    case) means PEERS could not resolve a baseline at all; no callback will
    ever follow for that `request_id`.

    A `None` return (unreachable PEERS, a PEERS-side error, or a timed-out
    wait with no callback ever delivered) is a soft failure for this one
    build -- it is caught by `_run_stage_b`'s caller the same way the
    now-deleted `build_peer_for_pool` raising or returning `None` used to be,
    and stage B moves on to the next build.

    Design "Progressive peer-comparison updates during live sampling" §3.2:
    a `cached=False` response no longer waits for exactly one callback and
    returns -- PEERS can (and now does) call `NotifyPeerBaselineReady` more
    than once for the same `request_id` while its `SamplingTask` is still
    `still_refining` (see `resolve_peer_baseline_notification`'s docstring).
    This function keeps polling `_peer_baseline_waiters` for further
    deliveries until either a terminal (`still_refining=False`) one arrives,
    or the original `_PEERS_BASELINE_WAIT_TIMEOUT_S` deadline elapses (NOT
    reset per notification -- it is a budget for the whole wait, not per
    callback). `on_update`, when given, is called once per delivered result
    -- `(peer_comparison, still_refining)` -- including the single delivery
    a fast-path `cached=True` response produces (always reported as
    `still_refining=False`: PEERS makes no further attempt for a synchronous
    response regardless of what its own internal snapshot says, so from this
    function's caller's perspective it is always the final answer for this
    stage-B pass). The return value is always the LAST delivered result (or
    `None` if none ever arrived) -- callers that only care about "did we get
    anything" can ignore `on_update` entirely, exactly as before this design.

    Cancellation and progress (finding 2 of the final whole-branch review):
    in-process, `services.progress` (a `JobProgressReporter`) is threaded into
    `resolve_peer_baseline`, and its `update` call is what raises
    `JobCancelled` to interrupt a long peer sample. The grpc path has no
    equivalent hook into PEERS' own resolution -- it can only poll. So instead
    of one long blocking `waiter.get(timeout=900)` call (which would not
    notice a cancellation until the full budget elapsed), the wait below is
    broken into `_PEERS_BASELINE_POLL_INTERVAL_S`-sized polls; between polls,
    when `store`/`job_id` are given (as `_run_stage_b` does), it calls the
    same `_ensure_not_cancelled` check `_run_stage_b` itself uses between
    pools, and refreshes job progress so the UI doesn't look frozen for the
    whole wait. `store`/`job_id` are optional (default `None`, checks skipped)
    so direct unit tests of this function don't need a real `JobStore`.
    """
    import grpc

    from league_stats_common.infra.trace_context import TraceClientInterceptor
    from league_stats_rpc.v1 import peers_pb2, peers_pb2_grpc

    log = get_logger("worker")
    records = group_records(batch.records, pool.champion, pool.role)
    if len(records) < services.config.min_games or ranked is None:
        return None
    matches_df = pd.DataFrame([r.to_row() for r in records])

    def _to_peer_comparison(baseline_json: str) -> PeerComparisonResult | None:
        if not baseline_json:
            return None
        try:
            baseline = PeerBaseline(**json.loads(baseline_json))
        except (TypeError, ValueError) as exc:
            log.warning(
                "Skipping peer comparison update for %s: malformed baseline from PEERS: %s",
                pool.build_label,
                exc,
            )
            return None
        return finish_peer_comparison(
            baseline,
            matches_df=matches_df,
            records=records,
            store=services.store,
            user_puuid=batch.primary_puuid,
            ranked=ranked,
            champion=pool.champion,
            role=pool.role,
            platform=services.config.routing_platform,
        )

    request = peers_pb2.RequestBaselineRequest(
        champion=pool.champion,
        lane=pool.role,
        rank=f"{ranked.tier} {ranked.rank}".strip(),
        platform=services.config.routing_platform,
        exclude_puuid=batch.primary_puuid,
        patch=current_patch(records),
    )

    channel = grpc.intercept_channel(
        grpc.insecure_channel(web_config.peers_grpc_target), TraceClientInterceptor()
    )
    try:
        stub = peers_pb2_grpc.PeersServiceStub(channel)
        request_start = time.perf_counter()
        try:
            response = stub.RequestBaseline(request, timeout=_PEERS_REQUEST_TIMEOUT_S)
        except grpc.RpcError as exc:
            request_elapsed = time.perf_counter() - request_start
            RUNNER_PEERS_REQUEST_DURATION.labels(outcome="rpc_error").observe(request_elapsed)
            OUTBOUND_RPC_DURATION.labels(
                target="peers", operation="RequestBaseline", outcome="error"
            ).observe(request_elapsed)
            log.warning(
                "Skipping peer comparison for %s: could not reach PEERS: %s",
                pool.build_label,
                exc,
            )
            return None
        request_elapsed = time.perf_counter() - request_start

        if response.error:
            RUNNER_PEERS_REQUEST_DURATION.labels(outcome="peers_error").observe(request_elapsed)
            OUTBOUND_RPC_DURATION.labels(
                target="peers", operation="RequestBaseline", outcome="error"
            ).observe(request_elapsed)
            log.warning(
                "Skipping peer comparison for %s: PEERS could not resolve a baseline: %s",
                pool.build_label,
                response.error,
            )
            return None

        if response.cached:
            RUNNER_PEERS_REQUEST_DURATION.labels(outcome="cached_hit").observe(request_elapsed)
            OUTBOUND_RPC_DURATION.labels(
                target="peers", operation="RequestBaseline", outcome="ok"
            ).observe(request_elapsed)
            # Always reported as terminal (`still_refining=False`): PEERS
            # makes no further attempt for a synchronous response regardless
            # of what its own internal snapshot says -- see this function's
            # docstring, "Design ... §3.2".
            result = _to_peer_comparison(response.baseline_json)
            if result is not None and on_update is not None:
                on_update(result, False)
            return result

        RUNNER_PEERS_REQUEST_DURATION.labels(outcome="cached_miss").observe(request_elapsed)
        OUTBOUND_RPC_DURATION.labels(
            target="peers", operation="RequestBaseline", outcome="ok"
        ).observe(request_elapsed)
        wait_start = time.perf_counter()
        waiter = _register_peer_baseline_waiter(response.request_id)
        deadline = time.monotonic() + _PEERS_BASELINE_WAIT_TIMEOUT_S
        last_result: PeerComparisonResult | None = None
        terminal_reached = False
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            poll_timeout = min(_PEERS_BASELINE_POLL_INTERVAL_S, remaining)
            try:
                notification = waiter.get(timeout=poll_timeout)
            except queue.Empty:
                if store is not None and job_id is not None:
                    try:
                        _ensure_not_cancelled(store, job_id)
                    except JobCancelled:
                        with _peer_baseline_waiters_lock:
                            _peer_baseline_waiters.pop(response.request_id, None)
                        RUNNER_PEERS_ASYNC_WAIT_DURATION.labels(outcome="cancelled").observe(
                            time.perf_counter() - wait_start
                        )
                        log.info(
                            "Job %d cancelled while waiting on PEERS for %s "
                            "(request_id=%s)",
                            job_id,
                            pool.build_label,
                            response.request_id,
                        )
                        raise
                    store.update_progress(
                        job_id,
                        detail=(
                            f"Comparing {pool.build_label} to players at your "
                            "rank — waiting on PEERS…"
                        ),
                    )
                continue

            if notification["error"]:
                log.warning(
                    "Skipping peer comparison for %s: PEERS reported %s",
                    pool.build_label,
                    notification["error"],
                )
                terminal_reached = True
                break

            still_refining = bool(notification.get("still_refining", False))
            result = _to_peer_comparison(notification["baseline_json"])
            if result is not None:
                last_result = result
                if on_update is not None:
                    on_update(result, still_refining)
            if not still_refining:
                terminal_reached = True
                break
            # Interim delivery: keep waiting on the SAME waiter for further
            # batches (design §3.1/§3.2) -- the deadline above is a budget
            # for the whole wait, not reset per notification.

        with _peer_baseline_waiters_lock:
            _peer_baseline_waiters.pop(response.request_id, None)
        RUNNER_PEERS_ASYNC_WAIT_DURATION.labels(
            outcome="delivered" if last_result is not None else "timed_out"
        ).observe(time.perf_counter() - wait_start)
        if not terminal_reached:
            log.warning(
                "PEERS never sent a terminal callback for %s within %ss for "
                "request_id=%s%s",
                pool.build_label,
                _PEERS_BASELINE_WAIT_TIMEOUT_S,
                response.request_id,
                "; using the last delivered (still refining) peer comparison"
                if last_result is not None
                else "",
            )
        return last_result
    finally:
        channel.close()


def _run_stage_b(
    services: Services,
    store: JobStore,
    job: dict[str, Any],
    batch: BuildBatch,
    ranked: RankedEntry | None,
    new_match_ids: frozenset[str] | set[str] | None,
    analysed: dict[tuple[str, str], BuildAnalysisResult],
    web_config: WebConfig,
) -> None:
    """Build peer comparisons and re-render each report as they land.

    Each build's peer comparison is always resolved by calling PEERS over
    gRPC (`_build_peer_for_pool_via_grpc`, added in Phase 3 of the
    microservices migration). Phase 9 removed the `peers_mode` flag that
    used to let this fall back to resolving the baseline in-process
    (`build_peer_for_pool`, itself deleted as fully dead code in Phase 9's
    final sweep) -- see `_build_peer_for_pool_via_grpc`'s own docstring for
    the topology precondition this gRPC-only path carries.

    Design "Progressive peer-comparison updates during live sampling" §3.2/
    §3.3: `_build_peer_for_pool_via_grpc` may now call `on_update` more than
    once per pool while PEERS' `SamplingTask` is still refining. An interim
    (`still_refining=True`) update is patched into `report.json` cheaply
    (`patch_report_peer_comparison`, no pipeline re-run, no Career). Only a
    terminal (`still_refining=False`) update runs the full `analyze_build`
    pass, which is also the only place Career computes -- and a per-pool
    `career_computed` boolean (same shape as `_execute_job_via_runner`'s
    `seen_stage_b` guard) ensures that happens exactly once per pool even if
    a terminal update is somehow delivered more than once (e.g. both PEERS'
    own one-shot `_on_resolved` and its progressive-listener path deliver the
    same final snapshot -- see `peers/service.py`).
    """
    log = get_logger("worker")
    job_id = int(job["id"])
    total = len(batch.pools)
    for index, pool in enumerate(batch.pools, start=1):
        _ensure_not_cancelled(store, job_id)
        records = group_records(batch.records, pool.champion, pool.role)
        if should_skip_unchanged_build(
            services.config, pool, records, new_match_ids
        ) and not report_needs_peer_comparison(services.config, pool):
            log.info(
                "Skipping peer for %s: no new games since last report", pool.build_label
            )
            store.update_progress(
                job_id,
                detail=f"Skipping {pool.build_label} — no new games ({index}/{total})",
                current=index,
                total=total,
            )
            continue
        store.update_progress(
            job_id,
            detail=f"Comparing {pool.build_label} to players at your rank ({index}/{total})",
            current=index,
            total=total,
        )
        cached = analysed.get((pool.champion, pool.role))
        career_computed = False

        def _run_terminal_analysis(peer_comparison: PeerComparisonResult) -> None:
            nonlocal career_computed
            if career_computed:
                return
            career_computed = True
            analyze_build(
                services,
                batch,
                pool,
                ranked=ranked,
                peer_comparison=peer_comparison,
                still_refining=False,
                full_frames=cached.full_frames if cached else None,
                report_stats=cached.report_stats if cached else None,
            )

        def _on_peer_update(peer_comparison: PeerComparisonResult, still_refining: bool) -> None:
            if not still_refining:
                _run_terminal_analysis(peer_comparison)
                return
            patched = patch_report_peer_comparison(
                services.config.reports_group_slug, champion_slug(pool.champion, pool.role), peer_comparison
            )
            if not patched:
                # No report.json exists yet for this pool (e.g. Stage A never
                # got far enough) -- fall back to a full render so the
                # interim result is still visible, without computing Career
                # against a still-refining result (still_refining=True keeps
                # `build_report_views`'s Career gate closed, §3.3).
                analyze_build(
                    services,
                    batch,
                    pool,
                    ranked=ranked,
                    peer_comparison=peer_comparison,
                    still_refining=True,
                    full_frames=cached.full_frames if cached else None,
                    report_stats=cached.report_stats if cached else None,
                )
                return
            # Design "Progressive peer-comparison updates during live sampling"
            # §3.4: a cheap `report.json` patch on disk has NO effect on its
            # own for a browser tab already open on this report -- it is
            # RUNNER's own local process state, invisible to api-ui's
            # `NotifyingJobStore`/`JobEventBus` (a separate process) unless
            # something rides the existing `StreamJobProgress` ->
            # `_execute_job_via_runner` replay -> local `store.update_progress`
            # -> `bus.publish(slug)` -> SSE path those already use for every
            # other progress event. `store.update_progress` here is exactly
            # that "something": `RunnerJobAdapter.update_progress` (RUNNER
            # side) turns it into a `StageResult`, and
            # `_execute_job_via_runner`'s replay (api-ui side, `worker.py`
            # line ~1307) calls the same method on its own local,
            # notification-wrapped store -- reusing plumbing every other
            # progress update already goes through, not adding any new one.
            # `Report.svelte`'s `applyStatusMessage` then sees this build's
            # `generated_at` changed and refetches (`reloadBuild()`).
            store.update_progress(
                job_id,
                detail=(
                    f"Comparison for {pool.build_label} improved — "
                    f"{peer_comparison.peer_games} peer games so far…"
                ),
            )

        peer = _build_peer_for_pool_via_grpc(
            services,
            batch,
            pool,
            ranked,
            web_config,
            store=store,
            job_id=job_id,
            on_update=_on_peer_update,
        )
        if peer is None:
            continue
        # `on_update` already ran the terminal `analyze_build` pass above for
        # every code path that can return a non-None `peer` -- this is a
        # defensive backstop, not the normal path, so `_run_stage_b`'s
        # postcondition ("peer is not None implies analyze_build ran") holds
        # even if a future change to `_build_peer_for_pool_via_grpc` ever
        # returns a result without having called `on_update` terminally.
        _run_terminal_analysis(peer)


_PLAYER_ENRICHMENT_FIELDS = ("profile_icon_id", "solo_tier", "solo_rank", "solo_lp")


def _merge_player_enrichment(
    base: list[dict[str, Any]], enrichment: list[dict[str, Any]] | None
) -> list[dict[str, Any]]:
    """Fill missing profile_icon_id/solo-rank fields in `base` from `enrichment`.

    Entries are matched by (riot_id, tagline). Only fills a field `base`
    doesn't already have -- never overwrites one it does -- so merging in
    older or emptier data can only add information, never erase what's
    already known. Used by `_execute_job_via_runner` to keep a grpc-mode
    job's `store.upsert_player` call from wiping out previously-resolved
    registry data (`JobStore.upsert_player` does a wholesale `players_json`
    overwrite on conflict).
    """
    if not enrichment:
        return base
    lookup = {
        (str(entry.get("riot_id", "")), str(entry.get("tagline", ""))): entry
        for entry in enrichment
    }
    merged: list[dict[str, Any]] = []
    for entry in base:
        key = (str(entry.get("riot_id", "")), str(entry.get("tagline", "")))
        source = lookup.get(key)
        result = dict(entry)
        if source:
            for field in _PLAYER_ENRICHMENT_FIELDS:
                if result.get(field) is None and source.get(field) is not None:
                    result[field] = source[field]
        merged.append(result)
    return merged


def _upsert_player_registry(
    *,
    slug: str,
    job: dict[str, Any],
    store: JobStore,
    resolved_players: list[dict[str, Any]] | None,
    tracked: list[dict[str, Any]],
) -> None:
    """Idempotently write this job's player/group row into the registry.

    Shared by `_execute_job_via_runner`'s two call sites: the normal
    Stage-B-triggered fast path, and the DONE-time safety net below it. Both
    must merge onto the existing row the same way (see
    `_merge_player_enrichment`'s docstring) so neither call can wipe out
    profile_icon_id/solo-rank data the other already wrote.
    """
    existing = store.get_player(slug)
    existing_players = (existing or {}).get("players") or []
    merged_players = _merge_player_enrichment(
        resolved_players if resolved_players is not None else tracked,
        existing_players,
    )
    store.upsert_player(
        slug=slug,
        riot_id=str(job.get("riot_id", "")),
        tagline=str(job.get("tagline", "")),
        region=str(job.get("region", "")),
        players=merged_players or None,
    )


def _execute_job_via_runner(job: dict[str, Any], store: JobStore, web_config: WebConfig) -> None:
    """Delegate one claimed job to RUNNER over gRPC, replaying its progress into `store`.

    This is the only path `AnalysisWorker._loop` (api-ui's/cron-watch's own
    job-claim loop) ever takes -- those processes never run a job themselves,
    they only ever hand it to RUNNER. `execute_job` below is RUNNER's own
    in-process executor, reached only from `RunnerServicer._run_job`, once a
    job has already been delegated here and RUNNER has received it over
    `EnqueueJob`. Restores the originating trace_id (`job.get("trace_id")`,
    `JobStore`'s `trace_id` column persisted at enqueue time) onto this
    thread's ContextVar before opening a channel to RUNNER, so
    `TraceClientInterceptor` attaches the real originating trace_id rather
    than whatever this long-lived worker thread happened to have left over
    from a previous job. Opens a plain (synchronous) `grpc.Channel`
    to RUNNER at `web_config.runner_grpc_target`, calls `EnqueueJob`, then consumes
    `StreamJobProgress` and replays each `StageResult` into the real `store` using
    the same `set_state`/`update_progress`/`mark_player_*` calls the in-process path
    would have made.

    Fidelity limits (inherent to `runner.proto`, not fixable from this module):
    `StageResult` carries only `stage` (STAGE_A/STAGE_B), `detail`/`current`/`total`,
    `error` and `final` -- it has no field for the exact `job_states.*` value
    `execute_job` would have set. So this reconstructs, rather than transmits,
    today's behavior:

    - FETCHING/ANALYZING/REPORT_READY all collapse into a single "stage A" bucket
      of `update_progress` calls, since the wire format can't tell them apart.
    - The transition into stage B (the first `StageResult` with `stage=STAGE_B`) is
      treated as the REPORT_READY -> PEER_RUNNING moment, and is also when
      `mark_player_base_complete` is called (RUNNER's `RunnerJobAdapter` never
      forwards that call itself -- see `league_stats.runner.adapter` -- so it must
      be inferred here, locally, from the stage transition).
    - A terminal (`final=True`) message with `error` set is ambiguous on the wire
      between "hard failure" (`job_states.FAILED`) and "soft peer failure"
      (`job_states.DONE` with `error` set, `mark_player_peer_failed` called) --
      both map to a terminal state with a non-empty `error` in
      `RunnerJobAdapter.set_state`. This is disambiguated using whether stage B
      was ever entered: a real peer-stage failure can only happen after stage A
      has already succeeded (i.e. after `mark_player_base_complete`), so an error
      seen before stage B started is treated as `FAILED`, and one seen after is
      treated as a soft `DONE` peer failure.
    - RUNNER cannot be cancelled in this phase (`RunnerJobAdapter.is_cancelled`
      always returns `False`), so unlike the in-process path, cancellation is not
      handled here.
    - The job's specific platform routing value (e.g. "kr", "oc1", "tr1") is not
      transmitted -- `EnqueueJobRequest.region` is `runner.proto`'s 4-value
      `Region` enum (europe/americas/asia/sea region groups only), so a job's
      platform collapses to its region group on the way out and is reconstructed
      as that region group's *default* platform (`REGION_DEFAULT_PLATFORM`) on
      RUNNER's side, not the job's original platform. Concretely: 13 of the 17
      platforms in `PLATFORM_TO_REGION` collapse to their region's default
      platform this way; only the 4 platforms that already *are* their region's
      default (`euw1`, `na1`, `kr`, `oc1`) round-trip exactly. For the other 13,
      match resolution still targets the right regional routing host
      (match-v5/account-v1 only need the region group) but rank-v4/league-v4
      calls -- which need the specific platform -- would resolve against the
      wrong platform's ranked ladder. Fixing this needs a platform-level field
      on `EnqueueJobRequest`, which `runner.proto` does not have; out of this
      task's scope. (See `REGION_DEFAULT_PLATFORM` and `PLATFORM_TO_REGION` in
      `league_stats_common.core.config` for the authoritative tables -- deliberately
      not enumerated per-platform here, to avoid this list drifting out of sync
      with those tables again.)
    - The fine-grained stage-A progression is not transmitted either:
      `job_states.FETCHING`, `ANALYZING` and `REPORT_READY` are three distinct
      states in the in-process path, but `runner.proto`'s `Stage` enum only
      distinguishes `STAGE_A` from `STAGE_B` -- there is no wire signal for
      "now analyzing" vs. "now fetching" within stage A, so `ANALYZING` never
      appears as a distinct replayed state here; both collapse into
      `update_progress` calls under the inferred `FETCHING`/(nothing) bucket
      described above. Fixing this needs a richer stage/state field on
      `StageResult`, which `runner.proto` does not have; out of this task's
      scope.
    - `store.upsert_player`'s registry write IS replayed, using RUNNER's actual
      resolved data where available: `execute_job` (running unmodified inside
      RUNNER) calls `RunnerJobAdapter.upsert_player` with the fully-resolved
      roster (`PlayerContext.as_player_dict()`: riot_id/tagline plus optional
      `profile_icon_id`/`solo_tier`/`solo_rank`/`solo_lp` -- note there is no
      puuid in this dict anywhere; `JobStore.upsert_player`/`as_player_dict`
      never carry one, so there is nothing puuid-related to lose here).
      `RunnerJobAdapter.upsert_player` pushes that roster out as a
      JSON-encoded `StageResult.payload_json` (an existing, previously-unused
      field on `runner.proto`'s `StageResult` -- no proto change was needed),
      and this function parses it back out below and uses it as the
      resolved-player data for its own local `store.upsert_player` call.
    - Timing divergence from the in-process path: the local `store.upsert_player`
      call above only fires on the first STAGE_B `StageResult` (i.e. once
      `seen_stage_b` flips), whereas the in-process `execute_job` calls
      `store.upsert_player` during stage A, right after `fetch_matches`/
      `resolve_player_contexts` and before analysis even starts. A grpc-mode
      job that fails during stage A therefore never writes the registry row
      that the in-process path would already have written by that point. This
      is a real behavioral difference, not yet fixed -- fixing it needs
      `runner.proto`/`RunnerJobAdapter` to surface the resolved roster before
      stage A completes, which is out of this task's scope.
    - Safety net for a lost Stage-B event: if the gRPC stream drops (or a
      `docker-compose` restart of api-ui/RUNNER races) at exactly the moment
      the one-shot Stage-B `StageResult` would have been sent/received, the
      `store.upsert_player` call above never fires -- but RUNNER still
      finishes independently (the report is saved straight to the shared
      Mongo `ReportStore`, no gRPC dependency), so the job can still reach a terminal `final`
      message. Without a fallback, that job would land in `job_states.DONE`
      with `has_report: true` but no registry row at all: `can_watch` stays
      false forever, and unwatch 404s with "Unknown player". So the `final`
      handling below unconditionally calls `_upsert_player_registry` (using
      the same locally-resolved `tracked`/`resolved_players` data) whenever a
      job is about to reach `DONE` and `seen_stage_b` never flipped -- purely
      additive redundancy on top of the Stage-B fast path above, safe because
      `JobStore.upsert_player` is an idempotent upsert.
    - This function also resolves the job's roster *locally* before ever
      contacting RUNNER, via the same `_tracked_players_for_job` recovery
      `execute_job` itself relies on (registry -> job's own
      `players`/`players_json` -> on-disk report metadata, using this
      monolith's own real `store` and `web_config.output_dir` -- not RUNNER's,
      which live in a separate container with no shared volume for `output/`
      in this repo's `docker-compose.yml`), and sends that already-resolved
      roster to RUNNER as `EnqueueJobRequest.players`. This fixes the
      roster-recovery problem RUNNER's own `RunnerJobAdapter.get_player`/disk
      fallback cannot solve (see below), and is also the fallback used for the
      local `store.upsert_player` call above if RUNNER's `payload_json` never
      arrives for some reason (e.g. an older RUNNER build).
    - The local `store.upsert_player` call never erases previously-known
      `profile_icon_id`/solo-rank data: before writing, it reads the existing
      registry row (`store.get_player(slug)`) and fills any field the new
      data (RUNNER's `payload_json`, or the local fallback roster) doesn't
      have from the existing row (see `_merge_player_enrichment`) -- so a run
      that itself resolves nothing new (e.g. `resolve_player_contexts`
      returning no rank) can only add information, never overwrite existing
      data with blanks. `JobStore.upsert_player` itself does a wholesale
      `players_json` overwrite on conflict, so skipping this merge would
      silently wipe out previously-resolved icon/rank data on every grpc-mode
      run that doesn't independently re-resolve it.
    - Why the roster-recovery fallback matters at all: `_tracked_players_for_job`
      (used by RUNNER's own unmodified `execute_job` run, via `RunnerJobAdapter`)
      falls back to `job_store.get_player(slug)` and then to on-disk report
      metadata when a job's own `players_json` doesn't already resolve to its
      `player_slug` (e.g. a group job with a stale/partial roster). On RUNNER,
      `RunnerJobAdapter.get_player` is a hardcoded no-op returning `None`
      (RUNNER keeps no player registry in this phase -- see
      `league_stats.runner.adapter`), and RUNNER's own `output_dir` is a
      separate filesystem from the monolith's in `docker-compose.yml` (no
      shared volume is declared for either service's `output/`/`data/`
      directories -- only `mongo-data` is shared), so neither fallback could
      ever succeed inside RUNNER even before this fix. Resolving the roster on
      the monolith side up front (where the real registry and real
      `output_dir` are) and sending the resolved list over the wire sidesteps
      that gap entirely for the roster itself; it does not, and cannot, give
      RUNNER access to the monolith's on-disk report metadata for anything
      else RUNNER's own `execute_job` run might independently need it for.
    """
    trace_id = job.get("trace_id")
    if trace_id:
        set_trace_id(trace_id)

    import grpc

    from league_stats_common.infra.trace_context import TraceClientInterceptor
    from league_stats_rpc.v1 import common_pb2, runner_pb2, runner_pb2_grpc

    log = get_logger("worker")
    job_id = int(job["id"])
    slug = str(job["player_slug"])

    region_key = str(job.get("region", "")).strip().lower()
    region_key = PLATFORM_TO_REGION.get(region_key, region_key)
    region_enum = {
        "europe": common_pb2.EUROPE,
        "americas": common_pb2.AMERICAS,
        "asia": common_pb2.ASIA,
        "sea": common_pb2.SEA,
    }.get(region_key, common_pb2.REGION_UNSPECIFIED)

    kind_enum = {
        job_states.JOB_KIND_ANALYZE: runner_pb2.JOB_KIND_ANALYZE,
        job_states.JOB_KIND_REFRESH: runner_pb2.JOB_KIND_REFRESH,
        JOB_KIND_REGENERATE: runner_pb2.JOB_KIND_REGENERATE,
    }.get(str(job.get("kind", "")), runner_pb2.JOB_KIND_UNSPECIFIED)

    # Resolve the roster locally (real registry + real output_dir) rather than
    # trusting the job's own players_json verbatim -- see the docstring above.
    tracked = _tracked_players_for_job(job, store, output_dir=web_config.output_dir)
    players = [
        runner_pb2.JobPlayer(riot_id=str(entry["riot_id"]), tagline=str(entry["tagline"]))
        for entry in tracked
        if entry.get("riot_id") and entry.get("tagline")
    ]

    min_games_raw = job.get("min_games")
    try:
        min_games = int(min_games_raw) if min_games_raw else 0
    except (TypeError, ValueError):
        min_games = 0

    request = runner_pb2.EnqueueJobRequest(
        region=region_enum,
        kind=kind_enum,
        riot_id=str(job.get("riot_id", "")),
        tagline=str(job.get("tagline", "")),
        player_slug=slug,
        players=players,
        filter_champion=str(job.get("filter_champion") or ""),
        filter_role=str(job.get("filter_role") or ""),
        min_games=min_games,
    )

    channel = grpc.intercept_channel(
        grpc.insecure_channel(web_config.runner_grpc_target), TraceClientInterceptor()
    )
    try:
        stub = runner_pb2_grpc.RunnerServiceStub(channel)
        enqueue_start = time.perf_counter()
        try:
            response = stub.EnqueueJob(request, timeout=_RUNNER_ENQUEUE_TIMEOUT_S)
        except grpc.RpcError as exc:
            OUTBOUND_RPC_DURATION.labels(
                target="runner", operation="EnqueueJob", outcome="error"
            ).observe(time.perf_counter() - enqueue_start)
            # RUNNER down, connection refused/reset, or the deadline above hit
            # (a reachable-but-hung RUNNER). Without this, the exception would
            # propagate out of execute_job into AnalysisWorker._loop, which has
            # no try/except of its own -- killing the worker thread permanently
            # and leaving this job stuck in a non-terminal state forever.
            log.exception("Job %d: EnqueueJob to RUNNER failed", job_id)
            store.set_state(
                job_id, job_states.FAILED, error=f"Could not reach RUNNER: {exc}"
            )
            return
        OUTBOUND_RPC_DURATION.labels(
            target="runner", operation="EnqueueJob", outcome="ok"
        ).observe(time.perf_counter() - enqueue_start)
        log.info("Job %d handed to RUNNER as %s", job_id, response.job_id)

        seen_stage_b = False
        resolved_players: list[dict[str, Any]] | None = None
        stream_start = time.perf_counter()
        stream_outcome = "error"
        try:
            for result in stub.StreamJobProgress(
                runner_pb2.StreamJobProgressRequest(job_id=response.job_id),
                timeout=_RUNNER_STREAM_TIMEOUT_S,
            ):
                if result.payload_json:
                    # RunnerJobAdapter.upsert_player's resolved roster (see docstring) --
                    # never final/error, so it's safe to just capture and move on.
                    try:
                        parsed = json.loads(result.payload_json)
                    except (TypeError, ValueError):
                        parsed = None
                    if isinstance(parsed, list):
                        resolved_players = [
                            entry for entry in parsed if isinstance(entry, dict)
                        ]
                    continue
                if result.stage == common_pb2.STAGE_B and not seen_stage_b:
                    seen_stage_b = True
                    # Registry refresh: prefer RUNNER's freshly-resolved roster (from
                    # payload_json above) over the locally-resolved fallback, then fill
                    # any still-missing profile_icon_id/solo-rank fields from what the
                    # registry already had -- never erase existing data (see docstring).
                    _upsert_player_registry(
                        slug=slug,
                        job=job,
                        store=store,
                        resolved_players=resolved_players,
                        tracked=tracked,
                    )
                    store.mark_player_base_complete(slug)
                    store.set_state(
                        job_id,
                        job_states.REPORT_READY,
                        detail="Report ready — comparing you to players at your rank…",
                    )
                    store.set_state(job_id, job_states.PEER_RUNNING, detail=result.detail)
                    if not result.final:
                        continue
                if result.final:
                    if result.error and not seen_stage_b:
                        store.set_state(job_id, job_states.FAILED, error=result.error)
                    else:
                        if not seen_stage_b:
                            # Safety net: the Stage-B StreamJobProgress event that
                            # normally triggers this write can be lost if the gRPC
                            # stream drops at exactly the wrong moment (e.g. a
                            # docker-compose restart mid-job) -- see
                            # `_upsert_player_registry`. RUNNER still finishes the
                            # job independently of this stream (the report is
                            # saved straight to the shared Mongo `ReportStore`),
                            # so without this, a job
                            # can reach DONE with no registry row at all:
                            # can_watch stays false forever and unwatch 404s with
                            # "Unknown player". Must not depend on ever having
                            # seen RUNNER's Stage-B payload.
                            _upsert_player_registry(
                                slug=slug,
                                job=job,
                                store=store,
                                resolved_players=resolved_players,
                                tracked=tracked,
                            )
                            store.mark_player_base_complete(slug)
                        if result.error:
                            store.mark_player_peer_failed(slug)
                            store.set_state(
                                job_id,
                                job_states.DONE,
                                detail=result.detail or "Report complete",
                                error=result.error,
                            )
                        else:
                            store.mark_player_peer_complete(slug)
                            store.set_state(
                                job_id,
                                job_states.DONE,
                                detail=result.detail or "Report complete",
                            )
                    log.info(
                        "Job %d (via RUNNER) finished: %s",
                        job_id,
                        "with error" if result.error else "clean",
                    )
                    stream_outcome = "ok"
                    return
                store.update_progress(
                    job_id,
                    detail=result.detail,
                    current=result.current or None,
                    total=result.total or None,
                )
        except grpc.RpcError as exc:
            # RUNNER went unreachable mid-stream (crashed, connection reset,
            # UNAVAILABLE) or the deadline above hit (a reachable-but-hung
            # RUNNER). Without this, the exception would propagate out of
            # execute_job into AnalysisWorker._loop, which has no try/except of
            # its own -- killing the worker thread permanently and, with
            # worker_concurrency=1, silently stopping the whole queue from
            # draining, while this job stays stuck in a non-terminal state
            # forever.
            stream_outcome = (
                "timeout" if exc.code() == grpc.StatusCode.DEADLINE_EXCEEDED else "error"
            )
            log.exception("Job %d: lost connection to RUNNER mid-stream", job_id)
            store.set_state(
                job_id, job_states.FAILED, error=f"Lost connection to RUNNER: {exc}"
            )
            return
        finally:
            OUTBOUND_RPC_DURATION.labels(
                target="runner", operation="StreamJobProgress", outcome=stream_outcome
            ).observe(time.perf_counter() - stream_start)
        # RunnerServicer guarantees a final=True message even when execute_job
        # crashes outside its own try/finally (see RunnerServicer._run_job) --
        # but if the stream still ends without one (e.g. a dropped connection),
        # don't leave the job stuck mid-flight forever.
        store.set_state(
            job_id,
            job_states.FAILED,
            error="Lost connection to RUNNER before the job completed",
        )
    finally:
        channel.close()


def execute_job(job: dict[str, Any], store: JobStore, web_config: WebConfig) -> None:
    """Run one claimed job end to end, updating its state as stages complete.

    This is RUNNER's own in-process executor, reached only from
    `RunnerServicer._run_job` once a job has been delegated to RUNNER over
    gRPC (see `_execute_job_via_runner`, the delegator counterpart used by
    api-ui's/cron-watch's own `AnalysisWorker._loop`). RUNNER never calls
    `_execute_job_via_runner` from here -- there is no branch left to choose
    it -- so a RUNNER process can never gRPC-delegate to itself.

    Restores the originating trace_id (`JobStore`'s `trace_id` column,
    persisted at enqueue time by `app.py`'s HTTP handlers and `WatchPoller`'s
    self-originated detection) onto this thread's ContextVar *before* doing
    anything else. `job.get("trace_id")` is falsy for RUNNER's own internal
    job dicts (built from `EnqueueJobRequest`, which carries no trace_id
    field) -- in that case this leaves whatever the caller already set
    (`RunnerServicer._run_job` sets it from the gRPC call it received)
    untouched, rather than clobbering it with an empty string.
    """
    trace_id = job.get("trace_id")
    if trace_id:
        set_trace_id(trace_id)

    log = get_logger("worker")
    job_id = int(job["id"])
    slug = str(job["player_slug"])
    reporter = JobProgressReporter(store, job_id)
    log.info("Job %d started: %s (%s)", job_id, slug, job["kind"])

    if store.is_cancelled(job_id):
        log.info("Job %d already cancelled before start", job_id)
        return

    try:
        services = _build_job_services(
            job, web_config, reporter, job_store=store
        )
    except JobCancelled:
        log.info("Job %d cancelled during setup", job_id)
        return
    except Exception as exc:
        store.set_state(job_id, job_states.FAILED, error=str(exc))
        log.exception("Job %d failed during setup", job_id)
        return

    try:
        contexts: list[PlayerContext]
        new_match_ids: frozenset[str] | None
        _ensure_not_cancelled(store, job_id)
        fetch_start = time.perf_counter()
        try:
            if job["kind"] == JOB_KIND_REGENERATE:
                # Re-analyse from the local match store; do not download newer games.
                store.set_state(
                    job_id, job_states.FETCHING, detail="Loading cached matches…"
                )
                contexts = resolve_player_contexts(services)
                new_match_ids = None
            else:
                store.set_state(
                    job_id, job_states.FETCHING, detail="Looking up match history…"
                )
                fetch_result = fetch_matches(services)
                contexts = fetch_result.contexts
                new_match_ids = fetch_result.new_match_ids
        finally:
            RUNNER_STAGE_DURATION.labels(stage="fetch").observe(time.perf_counter() - fetch_start)

        _ensure_not_cancelled(store, job_id)
        store.upsert_player(
            slug=slug,
            riot_id=str(job["riot_id"]),
            tagline=str(job["tagline"]),
            region=str(job["region"]),
            players=[context.as_player_dict() for context in contexts],
        )

        store.set_state(job_id, job_states.ANALYZING, detail="Discovering reports…")
        analyze_start = time.perf_counter()
        try:
            batch = prepare_builds(services, contexts)
            ranked, available_any, analysed = _run_stage_a(
                services, store, job, batch, new_match_ids
            )
        finally:
            RUNNER_STAGE_DURATION.labels(stage="analyze").observe(
                time.perf_counter() - analyze_start
            )
        _ensure_not_cancelled(store, job_id)
        if not available_any:
            raise NoEligibleBuildsError("No reports could be analysed.")

        store.mark_player_base_complete(slug)
        store.set_state(
            job_id,
            job_states.REPORT_READY,
            detail="Report ready — comparing you to players at your rank…",
        )

        peer_error: str | None = None
        peer_start = time.perf_counter()
        try:
            _ensure_not_cancelled(store, job_id)
            store.set_state(
                job_id,
                job_states.PEER_RUNNING,
                detail="Comparing you to players at your rank…",
            )
            _run_stage_b(
                services, store, job, batch, ranked, new_match_ids, analysed, web_config
            )
        except JobCancelled:
            raise
        except Exception as exc:
            peer_error = f"Rank comparison failed: {exc}"
            store.mark_player_peer_failed(slug)
            log.exception("Job %d: peer stage failed (base report kept)", job_id)
        finally:
            RUNNER_STAGE_DURATION.labels(stage="peer").observe(time.perf_counter() - peer_start)
        if peer_error is None:
            store.mark_player_peer_complete(slug)

        store.set_state(
            job_id, job_states.DONE, detail="Report complete", error=peer_error or ""
        )
        log.info("Job %d done (%s)", job_id, "with peer errors" if peer_error else "clean")
    except JobCancelled:
        # Leave any base reports on disk; cancel never deletes output/.
        log.info("Job %d cancelled (reports kept if already written)", job_id)
    except Exception as exc:
        store.set_state(job_id, job_states.FAILED, error=_friendly_error(exc, job))
        log.exception("Job %d failed", job_id)
    finally:
        services.store.close()
        services.http_cache.close()


class AnalysisWorker:
    """Thread pool that drains the job queue by delegating each job to RUNNER over gRPC."""

    def __init__(self, store: JobStore, web_config: WebConfig) -> None:
        self._store = store
        self._web_config = web_config
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

    def start(self) -> None:
        """Start the worker thread(s)."""
        for index in range(self._web_config.worker_concurrency):
            thread = threading.Thread(
                target=self._loop, name=f"analysis-worker-{index}", daemon=True
            )
            thread.start()
            self._threads.append(thread)

    def stop(self, timeout_s: float = 5.0) -> None:
        """Signal the worker loops to exit and wait briefly."""
        self._stop.set()
        for thread in self._threads:
            thread.join(timeout=timeout_s)

    def _loop(self) -> None:
        while not self._stop.is_set():
            job = self._store.claim_next()
            if job is None:
                self._stop.wait(self._web_config.worker_poll_interval_s)
                continue
            _execute_job_via_runner(job, self._store, self._web_config)
