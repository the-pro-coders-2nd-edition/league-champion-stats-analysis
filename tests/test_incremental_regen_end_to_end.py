"""End-to-end proof that a refresh with one new game leaves other builds untouched.

`should_skip_unchanged_build` (orchestrator.py) is exercised in isolation by
tests/test_skip_unchanged_build.py, and the worker's handling of a skip
decision is exercised (with the skip function monkeypatched) by
tests/test_web_worker.py. Neither proves that, given a real multi-build
player and a real `new_match_ids` set touching only one build, the *other*
build's on-disk report is actually left alone. This closes that gap.

Drives this through `worker.prepare_builds` + `worker._run_stage_a` directly
(RUNNER's own real stage-A pipeline, reached via `execute_job`) rather than
through `orchestrator.run_all_builds`, which Phase 9's dead-code sweep
confirmed has been orphaned (no production caller) since commit `33bd81b`
deleted the CLI shim that used to invoke it.
"""

from __future__ import annotations

import mongomock

import json
from pathlib import Path
from typing import Any

import pytest

import league_stats_common.infra.jobs as jobs
import league_stats_runner.worker as worker
from league_stats_common.core.config import AppConfig
from league_stats_common.core.models import RankedEntry
from league_stats_common.infra.cache import HttpCache
from league_stats_common.infra.ddragon_assets import DDragonAssets
from league_stats_common.infra.jobs import JobStore
from league_stats_common.infra.riot_api import RiotApiClient
from league_stats_runner.infra.raw_match_store import RawMatchStore
from league_stats_runner.pipeline.services import PlayerContext, Services
from tests.fixtures import FAKE_ITEMS, MY_PUUID, make_player_match, make_timeline


def _config(tmp_path: Path) -> AppConfig:
    config = AppConfig(
        riot_id="Test",
        tagline="EUW",
        region="europe",
        api_key="RGAPI-test",
        min_games=20,
        cache_dir=tmp_path / "cache",
        output_dir=tmp_path / "output",
        assets_dir=tmp_path / "assets",
        template_dir=Path(__file__).resolve().parent.parent
        / "src/league_stats/presentation/templates",
    )
    config.ensure_directories()
    return config


def _seed(store: RawMatchStore, *, viktor: int, ahri: int) -> None:
    for index in range(viktor):
        match_id = f"EUW1_v{index}"
        store.save_match(
            match_id, MY_PUUID, make_player_match(match_id, champion="Viktor", position="MIDDLE")
        )
        store.save_timeline(match_id, make_timeline())
    for index in range(ahri):
        match_id = f"EUW1_a{index}"
        store.save_match(
            match_id, MY_PUUID, make_player_match(match_id, champion="Ahri", position="MIDDLE")
        )
        store.save_timeline(match_id, make_timeline())


def _services(config: AppConfig, store: RawMatchStore, monkeypatch: pytest.MonkeyPatch) -> Services:
    http_cache = HttpCache(config.http_cache_dir)
    client = RiotApiClient(config, http_cache, store)
    monkeypatch.setattr(
        client,
        "fetch_solo_rank",
        lambda puuid: RankedEntry(tier="GOLD", rank="II", league_points=45, wins=80, losses=75),
    )
    monkeypatch.setattr(client, "fetch_item_catalog", lambda: FAKE_ITEMS)
    return Services(
        config=config,
        http_cache=http_cache,
        store=store,
        client=client,
        assets=DDragonAssets(config),
    )


def _job_store() -> JobStore:
    js = JobStore(mongomock.MongoClient())
    js.upsert_player(slug="test_euw", riot_id="Test", tagline="EUW", region="euw1")
    return js


def _claimed_job(store: JobStore) -> dict[str, Any]:
    store.enqueue(
        kind=jobs.JOB_KIND_ANALYZE,
        riot_id="Test",
        tagline="EUW",
        region="euw1",
        player_slug="test_euw",
    )
    job = store.claim_next()
    assert job is not None
    return job


def _run_stage_a(
    services: Services, job_store: JobStore, contexts: list[PlayerContext], new_match_ids: frozenset[str] | None
) -> None:
    """Drive RUNNER's real stage-A pipeline (`execute_job`'s own building blocks)."""
    job = _claimed_job(job_store)
    batch = worker.prepare_builds(services, contexts)
    worker._run_stage_a(services, job_store, job, batch, new_match_ids)
    # `enqueue` dedupes against an active job for this player; finish this one
    # so the next `_run_stage_a` call's `enqueue` creates a fresh job to claim.
    job_store.set_state(int(job["id"]), jobs.DONE)


def test_refresh_with_one_new_game_only_touches_the_affected_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    store = RawMatchStore(mongomock.MongoClient(), db_name="league_stats")
    _seed(store, viktor=25, ahri=25)
    services = _services(config, store, monkeypatch)
    contexts = [PlayerContext(riot_id="Test", tagline="EUW", puuid=MY_PUUID)]
    job_store = _job_store()

    try:
        # First pass: no new_match_ids -> should_skip_unchanged_build never
        # skips (mirrors a fresh `analyze`), so both builds get a real report.
        _run_stage_a(services, job_store, contexts, None)

        viktor_path = config.player_reports_dir / "viktor_middle" / "report.json"
        ahri_path = config.player_reports_dir / "ahri_middle" / "report.json"
        assert viktor_path.is_file()
        assert ahri_path.is_file()
        ahri_before = ahri_path.read_bytes()
        viktor_before = viktor_path.read_bytes()

        # One new Viktor game arrives; nothing changed for Ahri.
        new_match_id = "EUW1_v25"
        store.save_match(
            new_match_id,
            MY_PUUID,
            make_player_match(new_match_id, champion="Viktor", position="MIDDLE"),
        )
        store.save_timeline(new_match_id, make_timeline())

        _run_stage_a(services, job_store, contexts, frozenset({new_match_id}))

        ahri_after = ahri_path.read_bytes()
        viktor_after = viktor_path.read_bytes()
    finally:
        store.close()
        services.http_cache.close()
        job_store.close()

    assert ahri_after == ahri_before, (
        "Ahri build has no new games; its report.json must be byte-identical"
    )
    assert viktor_after != viktor_before, (
        "Viktor build has a new game; its report.json must have been re-rendered"
    )
    assert json.loads(viktor_after)["total_games"] == 26
    assert json.loads(ahri_after)["total_games"] == 25
