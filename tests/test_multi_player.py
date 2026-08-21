"""Tests for multi-player pooling."""

from __future__ import annotations

import mongomock

import json
from pathlib import Path

import pytest

import league_stats_runner.worker as worker
from league_stats_common.core.config import PlayerIdentity, load_config
from league_stats_common.infra.ddragon_assets import DDragonAssets
from league_stats_runner.ingest.parser import ItemCatalog, MatchParser, discover_build_pools
from league_stats_runner.infra.raw_match_store import RawMatchStore
from league_stats_runner.pipeline.fetch import group_records
from league_stats_runner.pipeline.services import PlayerContext, Services
from tests.fixtures import FAKE_ITEMS, MY_PUUID, make_player_match, make_timeline
from tests.test_build_pools import _claimed_job, _config, _job_store, _seed_store

ALT_PUUID = "alt-puuid-22222222-2222-2222-2222-222222222222"


def test_discover_build_pools_pools_multiple_players(tmp_path: Path) -> None:
    """Champion+lane counts combine games from every tracked player."""
    config = _config(tmp_path)
    store = RawMatchStore(mongomock.MongoClient(), db_name="league_stats")
    _seed_store(store, MY_PUUID, viktor=10, ahri=0)
    for index in range(15):
        match_id = f"EUW1_alt_v{index}"
        store.save_match(
            match_id,
            ALT_PUUID,
            make_player_match(
                match_id, champion="Viktor", position="MIDDLE", puuid=ALT_PUUID
            ),
        )
        store.save_timeline(match_id, make_timeline())
    try:
        pools = discover_build_pools(store, [MY_PUUID, ALT_PUUID], config, min_games=20)
        assert len(pools) == 1
        assert pools[0].champion == "Viktor"
        assert pools[0].games == 25
    finally:
        store.close()


def test_group_records_pools_same_build_across_players() -> None:
    """Grouped records include every player's games for one build."""
    parser = MatchParser(ItemCatalog(FAKE_ITEMS))
    mine = parser.parse(
        make_player_match("EUW1_1", champion="Viktor", position="MIDDLE"),
        make_timeline(),
        MY_PUUID,
    )
    alt_match = make_player_match(
        "EUW1_2", champion="Viktor", position="MIDDLE", puuid=ALT_PUUID
    )
    theirs = parser.parse(alt_match, make_timeline(), ALT_PUUID)
    grouped = group_records([mine, theirs], "Viktor", "MIDDLE")
    assert len(grouped) == 2


def test_multi_player_config_uses_group_slug(tmp_path: Path) -> None:
    """Multiple players share one report directory slug."""
    config = load_config(
        api_key="RGAPI-test",
        riot_id="Alice",
        tagline="EUW",
        players=[
            PlayerIdentity(riot_id="Alice", tagline="EUW"),
            PlayerIdentity(riot_id="Bob", tagline="NA1"),
        ],
        output_dir=tmp_path / "output",
    )
    assert config.players_label == "Alice#EUW, Bob#NA1"
    assert config.reports_group_slug == "alice_euw__bob_na1"


def test_run_all_builds_pools_multi_player_reports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Batch analysis (via RUNNER's real stage-A pipeline) pools qualifying
    games from multiple players.

    Drives this through `worker.prepare_builds` + `worker._run_stage_a`
    rather than `orchestrator.run_all_builds`, which Phase 9's dead-code
    sweep confirmed is orphaned (no production caller since commit
    `33bd81b` deleted the CLI shim that used to invoke it).
    """
    from league_stats_common.infra.cache import HttpCache
    from league_stats_common.core.models import RankedEntry
    from league_stats_common.infra.riot_api import RiotApiClient

    config = load_config(
        api_key="RGAPI-test",
        riot_id="Alice",
        tagline="EUW",
        players=[
            PlayerIdentity(riot_id="Alice", tagline="EUW"),
            PlayerIdentity(riot_id="Bob", tagline="NA1"),
        ],
        min_games=20,
        cache_dir=tmp_path / "cache",
        output_dir=tmp_path / "output",
        template_dir=Path(__file__).resolve().parent.parent / "src/league_stats/presentation/templates",
    )
    config.ensure_directories()
    store = RawMatchStore(mongomock.MongoClient(), db_name="league_stats")
    http_cache = HttpCache(config.http_cache_dir)
    client = RiotApiClient(config, http_cache, store)
    _seed_store(store, MY_PUUID, viktor=10, ahri=0)
    for index in range(15):
        match_id = f"EUW1_alt_v{index}"
        store.save_match(
            match_id,
            ALT_PUUID,
            make_player_match(
                match_id, champion="Viktor", position="MIDDLE", puuid=ALT_PUUID
            ),
        )
        store.save_timeline(match_id, make_timeline())

    monkeypatch.setattr(
        client,
        "fetch_solo_rank",
        lambda puuid: RankedEntry(tier="GOLD", rank="II", league_points=45, wins=80, losses=75),
    )
    monkeypatch.setattr(client, "fetch_item_catalog", lambda: FAKE_ITEMS)

    services = Services(
        config=config,
        http_cache=http_cache,
        store=store,
        client=client,
        assets=DDragonAssets(config),
    )
    contexts = [
        PlayerContext(riot_id="Alice", tagline="EUW", puuid=MY_PUUID),
        PlayerContext(riot_id="Bob", tagline="NA1", puuid=ALT_PUUID),
    ]
    job_store = _job_store("alice_euw__bob_na1")
    try:
        job = _claimed_job(job_store, "alice_euw__bob_na1")
        batch = worker.prepare_builds(services, contexts)
        worker._run_stage_a(services, job_store, job, batch, None)
    finally:
        store.close()
        http_cache.close()
        job_store.close()

    hub_path = config.player_reports_dir / "manifest.json"
    report_payload = json.loads(
        (config.player_reports_dir / "viktor_middle" / "report.json").read_text(encoding="utf-8")
    )
    assert hub_path.exists()
    assert report_payload["player_name"] == "Alice#EUW, Bob#NA1"
    assert report_payload["total_games"] == 25

    account_filter = report_payload["account_filter"]
    assert account_filter["enabled"] is True
    # Subsets are computed on demand now, so nothing is precomputed into the payload;
    # the members list is all the checkbox bar needs in order to render.
    assert account_filter["full_combinations"] is False
    assert account_filter["views"] == {}
    assert account_filter["members"]
    members = {member["key"]: member for member in account_filter["members"]}
    assert members["Alice#EUW"]["games"] == 10
    assert members["Bob#NA1"]["games"] == 15
    # Subset views are no longer precomputed into the payload. That a subset is
    # correctly scoped to one account's games is covered against the endpoint that
    # now builds them, in tests/test_web_account_filter.py.
