"""Background worker: claims queued jobs and runs the two-stage pipeline.

Stage A produces the base report (everything except peer benchmarks) and
flips the job to ``report_ready`` so the user can open it immediately.
Stage B builds the rank-peer comparison per build and re-renders each report
as its peer data lands. A stage-B failure is soft: the base report stays
served and the job still completes.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from league_stats.core.champions import players_group_slug
from league_stats.core.config import PLATFORM_TO_REGION, PlayerIdentity, WebConfig, load_config
from league_stats.core.models import RankedEntry
from league_stats.infra.cache import HttpCache, MatchStore
from league_stats.infra.ddragon_assets import DDragonAssets
from league_stats.infra.riot_api import RiotApiClient, RiotApiError, shared_rate_limiter
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


def _run_stage_b(
    services: Services,
    store: JobStore,
    job: dict[str, Any],
    batch: BuildBatch,
    ranked: RankedEntry | None,
    new_match_ids: frozenset[str] | set[str] | None,
    analysed: dict[tuple[str, str], BuildAnalysisResult],
) -> None:
    """Build peer comparisons and re-render each report as they land."""
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
      as that region group's *default* platform (`AppConfig.default_platform`)
      on RUNNER's side, not the job's original platform. For the 4 platforms
      that already are a region's default this round-trips exactly; for the
      other 13 platforms (e.g. "tr1"/"ru"/"me1" -> "euw1", "oc1" -> "na1",
      "jp1"/"vn2"/"tw2"/"sg2"/"ph2"/"th2" -> "kr") match resolution still
      targets the right regional routing host (match-v5/account-v1 only need
      the region group) but rank-v4/league-v4 calls -- which need the specific
      platform -- would resolve against the wrong platform's ranked ladder.
      Fixing this needs a platform-level field on `EnqueueJobRequest`, which
      `runner.proto` does not have; out of this task's scope.
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
        response = stub.EnqueueJob(request)
        log.info("Job %d handed to RUNNER as %s", job_id, response.job_id)

        seen_stage_b = False
        resolved_players: list[dict[str, Any]] | None = None
        for result in stub.StreamJobProgress(
            runner_pb2.StreamJobProgressRequest(job_id=response.job_id)
        ):
            if result.payload_json:
                # RunnerJobAdapter.upsert_player's resolved roster (see docstring) --
                # never final/error, so it's safe to just capture and move on.
                try:
                    parsed = json.loads(result.payload_json)
                except (TypeError, ValueError):
                    parsed = None
                if isinstance(parsed, list):
                    resolved_players = [entry for entry in parsed if isinstance(entry, dict)]
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
                services, store, job, batch, ranked, new_match_ids, analysed
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
