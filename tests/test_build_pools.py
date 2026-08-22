"""Tests for build pool discovery and batch grouping."""

from __future__ import annotations

import mongomock

from pathlib import Path
from typing import Any

import pytest

import league_stats_common.infra.jobs as jobs
import league_stats_runner.worker as worker
from league_stats_common.core.config import AppConfig
from league_stats_common.infra.jobs import JobStore
from league_stats_common.infra.report_store import open_report_store
from league_stats_common.infra.riot_api import RiotApiClient
from league_stats_runner.ingest.parser import ItemCatalog, MatchParser, discover_build_pools
from league_stats_runner.infra.raw_match_store import RawMatchStore
from league_stats_runner.pipeline.fetch import group_records
from league_stats_runner.pipeline.services import PlayerContext, Services
from league_stats_runner.presentation.report import discover_player_builds
from tests.fixtures import FAKE_ITEMS, MY_PUUID, make_player_match, make_timeline


def _job_store(slug: str) -> JobStore:
    js = JobStore(mongomock.MongoClient())
    js.upsert_player(slug=slug, riot_id="Test", tagline="EUW", region="euw1")
    return js


def _claimed_job(store: JobStore, slug: str) -> dict[str, Any]:
    store.enqueue(
        kind=jobs.JOB_KIND_ANALYZE,
        riot_id="Test",
        tagline="EUW",
        region="euw1",
        player_slug=slug,
    )
    job = store.claim_next()
    assert job is not None
    return job


def _config(tmp_path: Path) -> AppConfig:
    return AppConfig(
        riot_id="Test",
        tagline="EUW",
        region="europe",
        api_key="RGAPI-test",
        min_games=20,
        cache_dir=tmp_path / "cache",
        output_dir=tmp_path / "output",
        assets_dir=tmp_path / "assets",
        template_dir=Path(__file__).resolve().parent.parent / "src/league_stats/presentation/templates",
    )


def _seed_store(store: RawMatchStore, puuid: str, *, viktor: int, ahri: int) -> None:
    for index in range(viktor):
        match_id = f"EUW1_v{index}"
        store.save_match(match_id, puuid, make_player_match(match_id, champion="Viktor", position="MIDDLE"))
        store.save_timeline(match_id, make_timeline())
    for index in range(ahri):
        match_id = f"EUW1_a{index}"
        store.save_match(match_id, puuid, make_player_match(match_id, champion="Ahri", position="MIDDLE"))
        store.save_timeline(match_id, make_timeline())


def test_discover_build_pools_respects_min_games(tmp_path: Path) -> None:
    """Only champion+lane pairs with enough games are returned."""
    config = _config(tmp_path)
    store = RawMatchStore(mongomock.MongoClient(), db_name="league_stats")
    _seed_store(store, MY_PUUID, viktor=25, ahri=10)
    try:
        pools = discover_build_pools(store, MY_PUUID, config, min_games=20)
        assert len(pools) == 1
        assert pools[0].champion == "Viktor"
        assert pools[0].role == "MIDDLE"
        assert pools[0].games == 25
    finally:
        store.close()


def test_discover_build_pools_treats_lanes_separately(tmp_path: Path) -> None:
    """Same champion on different lanes counts as separate builds."""
    config = _config(tmp_path)
    store = RawMatchStore(mongomock.MongoClient(), db_name="league_stats")
    for index in range(20):
        match_id = f"EUW1_t{index}"
        store.save_match(
            match_id,
            MY_PUUID,
            make_player_match(match_id, champion="Akali", position="TOP"),
        )
        store.save_timeline(match_id, make_timeline())
    for index in range(20):
        match_id = f"EUW1_m{index}"
        store.save_match(
            match_id,
            MY_PUUID,
            make_player_match(match_id, champion="Akali", position="MIDDLE"),
        )
        store.save_timeline(match_id, make_timeline())
    try:
        pools = discover_build_pools(store, MY_PUUID, config, min_games=20)
        assert len(pools) == 2
        labels = {pool.build_label for pool in pools}
        assert labels == {"Akali top", "Akali mid"}
    finally:
        store.close()


def test_group_records_filters_by_champion_and_lane() -> None:
    """Grouped records match one build only."""
    parser = MatchParser(ItemCatalog(FAKE_ITEMS))
    viktor = parser.parse(
        make_player_match("EUW1_1", champion="Viktor", position="MIDDLE"),
        make_timeline(),
        MY_PUUID,
    )
    ahri = parser.parse(
        make_player_match("EUW1_2", champion="Ahri", position="MIDDLE"),
        make_timeline(),
        MY_PUUID,
    )
    grouped = group_records([viktor, ahri], "Viktor", "MIDDLE")
    assert len(grouped) == 1
    assert grouped[0].champion == "Viktor"


def test_run_all_builds_generates_player_hub(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Batch analysis (via RUNNER's real stage-A pipeline) writes every eligible
    report and a player hub.

    Drives this through `worker.prepare_builds` + `worker._run_stage_a` rather
    than `orchestrator.run_all_builds`, which Phase 9's dead-code sweep
    confirmed is orphaned (no production caller since commit `33bd81b`
    deleted the CLI shim that used to invoke it). The hub/manifest refresh
    tested here is a side effect of `analyze_build` itself, called by both.
    """
    from league_stats_common.infra.cache import HttpCache
    from league_stats_common.core.models import RankedEntry
    from league_stats_common.infra.ddragon_assets import DDragonAssets

    config = _config(tmp_path)
    config.ensure_directories()
    store = RawMatchStore(mongomock.MongoClient(), db_name="league_stats")
    http_cache = HttpCache(config.http_cache_dir)
    client = RiotApiClient(config, http_cache, store)
    _seed_store(store, MY_PUUID, viktor=20, ahri=20)

    monkeypatch.setattr(
        client,
        "fetch_solo_rank",
        lambda puuid: RankedEntry(tier="GOLD", rank="II", league_points=45, wins=80, losses=75),
    )
    monkeypatch.setattr(
        client,
        "fetch_item_catalog",
        lambda: FAKE_ITEMS,
    )
    monkeypatch.setattr(DDragonAssets, "ensure_downloaded", lambda self, force=False: "")

    services = Services(
        config=config,
        http_cache=http_cache,
        store=store,
        client=client,
        assets=DDragonAssets(config),
    )
    job_store = _job_store("test_euw")
    try:
        job = _claimed_job(job_store, "test_euw")
        contexts = [PlayerContext(riot_id="Test", tagline="EUW", puuid=MY_PUUID)]
        batch = worker.prepare_builds(services, contexts)
        worker._run_stage_a(services, job_store, job, batch, None)
    finally:
        store.close()
        http_cache.close()
        job_store.close()

    builds = discover_player_builds(config.reports_group_slug)
    champions = {build["champion"] for build in builds}
    assert "Viktor" in champions
    assert "Ahri" in champions
    with open_report_store() as store:
        assert store.has_build(config.reports_group_slug, "viktor_middle")
        assert store.has_build(config.reports_group_slug, "ahri_middle")
        report_json = store.get_report(config.reports_group_slug, "viktor_middle")

    assert report_json["player_builds"]
    assert any(build["champion"] == "Ahri" for build in report_json["player_builds"])
