"""Background worker: claims queued jobs and runs the two-stage pipeline.

Stage A produces the base report (everything except peer benchmarks) and
flips the job to ``report_ready`` so the user can open it immediately.
Stage B builds the rank-peer comparison per build and re-renders each report
as its peer data lands. A stage-B failure is soft: the base report stays
served and the job still completes.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from league_stats.core.champions import players_group_slug
from league_stats.core.config import PlayerIdentity, WebConfig, load_config
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


def execute_job(job: dict[str, Any], store: JobStore, web_config: WebConfig) -> None:
    """Run one claimed job end to end, updating its state as stages complete."""
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
