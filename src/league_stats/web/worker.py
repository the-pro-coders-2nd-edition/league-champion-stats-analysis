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
from typing import Any

import pandas as pd

from league_stats.analysis.peer import finish_peer_comparison
from league_stats.analysis.peer.baseline import PeerBaseline
from league_stats.core.champions import players_group_slug
from league_stats.core.config import PLATFORM_TO_REGION, PlayerIdentity, WebConfig, load_config
from league_stats.core.models import PeerComparisonResult, RankedEntry
from league_stats.infra.cache import HttpCache, MatchStore
from league_stats.infra.ddragon_assets import DDragonAssets
from league_stats.infra.riot_api import RiotApiClient, RiotApiError, shared_rate_limiter
from league_stats.ingest.parser import BuildPool
from league_stats.pipeline.fetch import fetch_matches, group_records, resolve_player_contexts
from league_stats.pipeline.orchestrator import (
    BuildAnalysisResult,
    BuildBatch,
    NoEligibleBuildsError,
    analyze_build,
    build_peer_for_pool,
    prepare_builds,
    report_needs_peer_comparison,
    resolve_ranked,
    should_skip_unchanged_build,
)
from league_stats.pipeline.services import PlayerContext, Services
from league_stats.presentation.report import discover_player_builds
from league_stats.web import jobs as job_states
from league_stats.web.jobs import JOB_KIND_REGENERATE, JobStore, decode_players
from league_stats.web.progress import JobCancelled, JobProgressReporter
from league_stats.utils import get_logger

CHAT_ENDPOINT = "/api/chat"

# Deadlines for the grpc runner_mode RPCs (see `_execute_job_via_runner`), so a
# RUNNER that's reachable but hung doesn't block the worker thread forever.
_RUNNER_ENQUEUE_TIMEOUT_S = 30.0
_RUNNER_STREAM_TIMEOUT_S = 1800.0

# Deadline for the peers_mode="grpc" `RequestBaseline` unary call itself (see
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
# on the *caller* side (`_RUNNER_STREAM_TIMEOUT_S`, 1800s, in the
# monolith-to-RUNNER gRPC client) -- since stage B can call this once per pool
# in `batch.pools`, several pools each waiting close to this timeout could in
# principle exceed that 1800s budget for a job with many builds; that
# per-job/per-pool interaction is a known, not-yet-solved limit of this design
# (see task-3-report.md), not something this constant alone can fix. A build
# whose baseline never arrives in time skips its peer comparison (soft
# failure, same as any other `build_peer_for_pool` exception) rather than
# hanging stage B forever.
_PEERS_BASELINE_WAIT_TIMEOUT_S = 900.0

# Keyed by PeersService's `request_id` (RequestBaselineResponse.request_id):
# registered by `_build_peer_for_pool_via_grpc` right after a `cached=False`
# response, consumed by `resolve_peer_baseline_notification` when RUNNER's
# `RunnerServicer.NotifyPeerBaselineReady` receives PEERS' callback for that
# request_id. Mirrors the `queue.SimpleQueue`-per-id shape
# `RunnerServicer`/`RunnerJobAdapter` already use for job progress -- see
# `runner/service.py`'s module docstring for why RUNNER's servicer is
# synchronous, not `grpc.aio`, and therefore needs a plain thread-safe handoff
# like this rather than an `asyncio.Event`.
_peer_baseline_waiters: dict[str, "queue.SimpleQueue[dict[str, str]]"] = {}
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
_peer_baseline_orphans: dict[str, tuple[float, dict[str, str]]] = {}
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


def _register_peer_baseline_waiter(request_id: str) -> "queue.SimpleQueue[dict[str, str]]":
    """Register a waiter for PEERS' async callback for `request_id`.

    Checks `_peer_baseline_orphans` first (see that dict's module comment for
    the exact lost-wakeup race this closes): if the notification already
    arrived before this call happened, the returned queue already has the
    result in it instead of blocking on a queue nothing will ever put
    anything into.
    """
    events: "queue.SimpleQueue[dict[str, str]]" = queue.SimpleQueue()
    with _peer_baseline_waiters_lock:
        orphan = _peer_baseline_orphans.pop(request_id, None)
        if orphan is not None:
            events.put(orphan[1])
            return events
        _peer_baseline_waiters[request_id] = events
    return events


def resolve_peer_baseline_notification(
    request_id: str, *, baseline_json: str, error: str
) -> bool:
    """Deliver RUNNER's real `NotifyPeerBaselineReady` callback to whichever
    stage-B thread is waiting on `request_id`, if any.

    Called by `RunnerServicer.NotifyPeerBaselineReady` (`runner/service.py`) --
    this is the real Phase 3 implementation of the coordination Phase 1's
    version of that method left as a logging-only stub.

    Returns ``False`` when no waiter is *currently* registered for
    `request_id` -- this covers two different cases the caller can't tell
    apart, and doesn't need to: (a) the stage-B thread already gave up after
    `_PEERS_BASELINE_WAIT_TIMEOUT_S` and moved on, or `request_id` never
    belonged to a request this process made -- nothing useful to do, the
    notification is simply logged and dropped; (b) the genuine lost-wakeup
    race documented on `_peer_baseline_orphans` above, where this notification
    arrived before `_register_peer_baseline_waiter` ran for the same
    `request_id` -- in that case the payload is NOT dropped, it's stashed in
    `_peer_baseline_orphans` for `_register_peer_baseline_waiter` to pick up
    immediately once it does run. Returning ``False`` here still accurately
    reports "not delivered to a live waiter"; the value living on
    unclaimed for a little while is what makes the race harmless either way.
    """
    with _peer_baseline_waiters_lock:
        events = _peer_baseline_waiters.pop(request_id, None)
        if events is None:
            _prune_expired_peer_baseline_orphans_locked()
            _peer_baseline_orphans[request_id] = (
                time.monotonic(),
                {"baseline_json": baseline_json, "error": error},
            )
    if events is None:
        return False
    events.put({"baseline_json": baseline_json, "error": error})
    return True


def _slug_for_players(players: list[dict[str, Any]]) -> str:
    """Filesystem group slug for a player list."""
    return players_group_slug(
        [(str(p["riot_id"]), str(p["tagline"])) for p in players]
    )


def _players_from_reports(output_dir: Path, job_slug: str) -> list[dict[str, Any]]:
    """Recover pooled identities from on-disk report metadata when the DB drifted."""
    from league_stats.core.models import solo_rank_fields

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
    store = MatchStore(config.db_path)
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
) -> PeerComparisonResult | None:
    """`peers_mode="grpc"` counterpart to `build_peer_for_pool`
    (`league_stats.pipeline.orchestrator`): resolves the peer baseline by
    calling PEERS' `RequestBaseline` over gRPC instead of running
    `resolve_peer_baseline` in this process.

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
    wait for the async callback) is a soft failure for this one build -- it is
    caught by `_run_stage_b`'s caller the same way `build_peer_for_pool`
    raising or returning `None` already is, and stage B moves on to the next
    build.
    """
    import grpc

    from league_stats_rpc.v1 import peers_pb2, peers_pb2_grpc

    log = get_logger("worker")
    records = group_records(batch.records, pool.champion, pool.role)
    if len(records) < services.config.min_games or ranked is None:
        return None
    matches_df = pd.DataFrame([r.to_row() for r in records])

    request = peers_pb2.RequestBaselineRequest(
        champion=pool.champion,
        lane=pool.role,
        rank=f"{ranked.tier} {ranked.rank}".strip(),
        platform=services.config.routing_platform,
        exclude_puuid=batch.primary_puuid,
    )

    channel = grpc.insecure_channel(web_config.peers_grpc_target)
    try:
        stub = peers_pb2_grpc.PeersServiceStub(channel)
        try:
            response = stub.RequestBaseline(request, timeout=_PEERS_REQUEST_TIMEOUT_S)
        except grpc.RpcError as exc:
            log.warning(
                "Skipping peer comparison for %s: could not reach PEERS: %s",
                pool.build_label,
                exc,
            )
            return None

        if response.error:
            log.warning(
                "Skipping peer comparison for %s: PEERS could not resolve a baseline: %s",
                pool.build_label,
                response.error,
            )
            return None

        if response.cached:
            baseline_json = response.baseline_json
        else:
            waiter = _register_peer_baseline_waiter(response.request_id)
            try:
                notification = waiter.get(timeout=_PEERS_BASELINE_WAIT_TIMEOUT_S)
            except queue.Empty:
                with _peer_baseline_waiters_lock:
                    _peer_baseline_waiters.pop(response.request_id, None)
                log.warning(
                    "Skipping peer comparison for %s: PEERS never called back within "
                    "%ss for request_id=%s",
                    pool.build_label,
                    _PEERS_BASELINE_WAIT_TIMEOUT_S,
                    response.request_id,
                )
                return None
            if notification["error"]:
                log.warning(
                    "Skipping peer comparison for %s: PEERS reported %s",
                    pool.build_label,
                    notification["error"],
                )
                return None
            baseline_json = notification["baseline_json"]
    finally:
        channel.close()

    if not baseline_json:
        return None
    try:
        baseline = PeerBaseline(**json.loads(baseline_json))
    except (TypeError, ValueError) as exc:
        log.warning(
            "Skipping peer comparison for %s: malformed baseline from PEERS: %s",
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
    )


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

    `web_config.peers_mode` (default "in_process") controls how each build's
    peer comparison is resolved: "in_process" calls `build_peer_for_pool`
    exactly as before (this branch's behavior is provably unchanged -- same
    call, same arguments); "grpc" calls PEERS instead via
    `_build_peer_for_pool_via_grpc`, added in Phase 3 of the microservices
    migration.
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
        if web_config.peers_mode == "grpc":
            peer = _build_peer_for_pool_via_grpc(services, batch, pool, ranked, web_config)
        else:
            peer = build_peer_for_pool(services, batch, pool, ranked)
        if peer is None:
            continue
        cached = analysed.get((pool.champion, pool.role))
        analyze_build(
            services,
            batch,
            pool,
            ranked=ranked,
            peer_comparison=peer,
            full_frames=cached.full_frames if cached else None,
            report_stats=cached.report_stats if cached else None,
        )


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


def _execute_job_via_runner(job: dict[str, Any], store: JobStore, web_config: WebConfig) -> None:
    """Delegate one claimed job to RUNNER over gRPC, replaying its progress into `store`.

    Opt-in counterpart to the in-process path above, used only when
    `web_config.runner_mode == "grpc"`. Opens a plain (synchronous) `grpc.Channel`
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
      `league_stats.core.config` for the authoritative tables -- deliberately
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
    import grpc

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

    channel = grpc.insecure_channel(web_config.runner_grpc_target)
    try:
        stub = runner_pb2_grpc.RunnerServiceStub(channel)
        try:
            response = stub.EnqueueJob(request, timeout=_RUNNER_ENQUEUE_TIMEOUT_S)
        except grpc.RpcError as exc:
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
        log.info("Job %d handed to RUNNER as %s", job_id, response.job_id)

        seen_stage_b = False
        resolved_players: list[dict[str, Any]] | None = None
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
                    elif result.error:
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
                            job_id, job_states.DONE, detail=result.detail or "Report complete"
                        )
                    log.info(
                        "Job %d (via RUNNER) finished: %s",
                        job_id,
                        "with error" if result.error else "clean",
                    )
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
            log.exception("Job %d: lost connection to RUNNER mid-stream", job_id)
            store.set_state(
                job_id, job_states.FAILED, error=f"Lost connection to RUNNER: {exc}"
            )
            return
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
    """Run one claimed job end to end, updating its state as stages complete."""
    if web_config.runner_mode == "grpc":
        _execute_job_via_runner(job, store, web_config)
        return

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

        _ensure_not_cancelled(store, job_id)
        store.upsert_player(
            slug=slug,
            riot_id=str(job["riot_id"]),
            tagline=str(job["tagline"]),
            region=str(job["region"]),
            players=[context.as_player_dict() for context in contexts],
        )

        store.set_state(job_id, job_states.ANALYZING, detail="Discovering reports…")
        batch = prepare_builds(services, contexts)
        ranked, available_any, analysed = _run_stage_a(
            services, store, job, batch, new_match_ids
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
    """Thread pool that drains the job queue."""

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
            execute_job(job, self._store, self._web_config)
