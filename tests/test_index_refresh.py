"""Tests that report hubs refresh as builds are written."""

from __future__ import annotations

from pathlib import Path

from league_stats_common.core.config import AppConfig
from league_stats_runner.pipeline.orchestrator import run_analysis
from league_stats_common.core.models import MatchRecord
from league_stats_runner.presentation.report import discover_reports, discover_player_builds, refresh_report_indexes
from tests.fixtures import FAKE_ITEMS, MY_PUUID, make_match, make_timeline
from league_stats_runner.ingest.parser import ItemCatalog, MatchParser


def _config(tmp_path: Path, *, champion: str = "Viktor", role: str = "MIDDLE") -> AppConfig:
    config = AppConfig(
        riot_id="Test",
        tagline="EUW",
        region="europe",
        api_key="RGAPI-test",
        champion=champion,
        role=role,
        output_dir=tmp_path / "output",
        assets_dir=tmp_path / "assets",
        cache_dir=tmp_path / "cache",
        template_dir=Path(__file__).resolve().parent.parent / "src/league_stats/presentation/templates",
    )
    config.ensure_directories()
    return config


def _records(n: int = 15) -> list[MatchRecord]:
    base = MatchParser(ItemCatalog(FAKE_ITEMS)).parse(make_match(), make_timeline(), MY_PUUID)
    return [
        base.model_copy(
            deep=True,
            update={
                "match_id": f"EUW1_{index}",
                "win": index % 2 == 0,
                "game_creation_ms": 1_700_000_000_000 + index * 3_600_000,
            },
        )
        for index in range(n)
    ]


def test_run_analysis_discovers_reports(tmp_path: Path) -> None:
    """Each completed report is discoverable immediately."""
    viktor_config = _config(tmp_path, champion="Viktor", role="MIDDLE")
    run_analysis(viktor_config, _records())

    assert not (viktor_config.output_dir / "index.html").exists()
    assert len(discover_reports()) == 1

    ahri_config = _config(tmp_path, champion="Ahri", role="MIDDLE")
    run_analysis(ahri_config, _records())

    reports = discover_reports()
    assert len(reports) == 2
    champions = {report["champion"] for report in reports}
    assert "Viktor" in champions
    assert "Ahri" in champions


def test_run_analysis_refreshes_player_hub(tmp_path: Path) -> None:
    """The player hub lists every build as soon as its report is saved."""
    run_analysis(_config(tmp_path, champion="Viktor"), _records())
    run_analysis(_config(tmp_path, champion="Ahri"), _records())

    player_slug = _config(tmp_path).reports_group_slug
    builds = discover_player_builds(player_slug)
    assert len(builds) == 2
    champions = {build["champion"] for build in builds}
    assert "Viktor" in champions
    assert "Ahri" in champions


def test_refresh_report_indexes_rebuilds_hub_from_disk(tmp_path: Path) -> None:
    """Manual refresh leaves builds discoverable and touches no index.html."""
    config = _config(tmp_path, champion="Zac", role="JUNGLE")
    run_analysis(config, _records())

    refresh_report_indexes(
        config.output_dir,
        config.template_dir,
        player_dir=config.player_reports_dir,
        player_label="Test#EUW",
    )
    builds = discover_player_builds(config.reports_group_slug)
    assert len(builds) == 1
    assert builds[0]["champion"] == "Zac"
    assert not (config.output_dir / "index.html").exists()
