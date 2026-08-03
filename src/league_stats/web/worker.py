"""Background worker: claims queued jobs and runs the two-stage pipeline.

Stage A produces the base report (everything except peer benchmarks) and
flips the job to ``report_ready`` so the user can open it immediately.
Stage B builds the rank-peer comparison per build and re-renders each report
as its peer data lands. A stage-B failure is soft: the base report stays
served and the job still completes.
"""

from __future__ import annotations

import threading
from typing import Any

from league_stats.core.config import PlayerIdentity, WebConfig, load_config
from league_stats.core.models import RankedEntry
from league_stats.infra.cache import HttpCache, MatchStore
from league_stats.infra.ddragon_assets import DDragonAssets
from league_stats.infra.riot_api import RiotApiClient, RiotApiError, shared_rate_limiter
from league_stats.pipeline.fetch import fetch_matches, group_records
from league_stats.pipeline.orchestrator import (
    BuildBatch,
    NoEligibleBuildsError,
    analyze_build,
    build_peer_for_pool,
    prepare_builds,
    resolve_ranked,
    should_skip_unchanged_build,
)
from league_stats.pipeline.services import Services
from league_stats.web import jobs as job_states
from league_stats.web.jobs import JobStore, decode_players
from league_stats.web.progress import JobProgressReporter
from league_stats.utils import get_logger

CHAT_ENDPOINT = "/api/chat"


def _build_job_services(
    job: dict[str, Any], web_config: WebConfig, reporter: JobProgressReporter
) -> Services:
    """Wire pipeline services for one job (shared rate limiter, DB reporter)."""
    tracked = decode_players(
        job.get("players_json"),
        riot_id=str(job.get("riot_id", "")),
        tagline=str(job.get("tagline", "")),
    )
    if not tracked and job.get("players"):
        tracked = list(job["players"])
    players = [
        PlayerIdentity(riot_id=entry["riot_id"], tagline=entry["tagline"])
        for entry in tracked
    ] or None
    config = load_config(
        riot_id=job["riot_id"],
        tagline=job["tagline"],
        region=job["region"],
        output_dir=web_config.output_dir,
        chat_endpoint=CHAT_ENDPOINT if web_config.gemini_api_key else None,
        status_endpoint=f"/api/players/{job['player_slug']}",
        players=players,
    )
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


def _run_stage_a(
    services: Services,
    store: JobStore,
    job: dict[str, Any],
    batch: BuildBatch,
    new_match_ids: frozenset[str] | set[str] | None,
) -> tuple[RankedEntry | None, bool]:
    """Render every eligible build without peer data.

    Builds with an existing report and no newly fetched games are skipped.

    Returns:
        The player's ranked entry (fetched once) and whether any report is
        available (newly rendered or already on disk).
    """
    log = get_logger("worker")
    job_id = int(job["id"])
    ranked: RankedEntry | None = None
    ranked_resolved = False
    available_any = False
    total = len(batch.pools)
    for index, pool in enumerate(batch.pools, start=1):
        records = group_records(batch.records, pool.champion, pool.role)
        if should_skip_unchanged_build(services.config, pool, records, new_match_ids):
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
        report = analyze_build(
            services, batch, pool, ranked=ranked, peer_comparison=None
        )
        if report is not None:
            available_any = True
    return ranked, available_any


def _run_stage_b(
    services: Services,
    store: JobStore,
    job: dict[str, Any],
    batch: BuildBatch,
    ranked: RankedEntry | None,
    new_match_ids: frozenset[str] | set[str] | None,
) -> None:
    """Build peer comparisons and re-render each report as they land."""
    log = get_logger("worker")
    job_id = int(job["id"])
    total = len(batch.pools)
    for index, pool in enumerate(batch.pools, start=1):
        records = group_records(batch.records, pool.champion, pool.role)
        if should_skip_unchanged_build(services.config, pool, records, new_match_ids):
            log.info(
                "Skipping peer for %s: no new games since last report", pool.build_label
            )
            store.update_progress(
                job_id,
                detail=f"Skipping peer {pool.build_label} — no new games ({index}/{total})",
                current=index,
                total=total,
            )
            continue
        store.update_progress(
            job_id,
            detail=f"Peer analysis: {pool.build_label} ({index}/{total})",
            current=index,
            total=total,
        )
        peer = build_peer_for_pool(services, batch, pool, ranked)
        if peer is None:
            continue
        analyze_build(services, batch, pool, ranked=ranked, peer_comparison=peer)


def execute_job(job: dict[str, Any], store: JobStore, web_config: WebConfig) -> None:
    """Run one claimed job end to end, updating its state as stages complete."""
    log = get_logger("worker")
    job_id = int(job["id"])
    slug = str(job["player_slug"])
    reporter = JobProgressReporter(store, job_id)
    log.info("Job %d started: %s (%s)", job_id, slug, job["kind"])

    try:
        services = _build_job_services(job, web_config, reporter)
    except Exception as exc:
        store.set_state(job_id, job_states.FAILED, error=str(exc))
        log.exception("Job %d failed during setup", job_id)
        return

    try:
        store.set_state(job_id, job_states.FETCHING, detail="Looking up match history…")
        fetch_result = fetch_matches(services)

        store.set_state(job_id, job_states.ANALYZING, detail="Discovering builds…")
        batch = prepare_builds(services, fetch_result.contexts)
        ranked, available_any = _run_stage_a(
            services, store, job, batch, fetch_result.new_match_ids
        )
        if not available_any:
            raise NoEligibleBuildsError("No builds could be analysed.")

        store.mark_player_base_complete(slug)
        store.set_state(
            job_id,
            job_states.REPORT_READY,
            detail="Report ready — running peer analysis…",
        )

        peer_error: str | None = None
        try:
            store.set_state(job_id, job_states.PEER_RUNNING, detail="Peer analysis starting…")
            _run_stage_b(
                services, store, job, batch, ranked, fetch_result.new_match_ids
            )
        except Exception as exc:
            peer_error = f"Peer analysis failed: {exc}"
            store.mark_player_peer_failed(slug)
            log.exception("Job %d: peer stage failed (base report kept)", job_id)
        if peer_error is None:
            store.mark_player_peer_complete(slug)

        store.set_state(
            job_id, job_states.DONE, detail="Report complete", error=peer_error or ""
        )
        log.info("Job %d done (%s)", job_id, "with peer errors" if peer_error else "clean")
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
