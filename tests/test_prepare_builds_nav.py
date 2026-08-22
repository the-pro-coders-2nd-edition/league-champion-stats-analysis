"""Scoped analysis keeps the full champion sidebar list."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from league_stats_common.infra.report_store import open_report_store
from league_stats_runner.ingest.parser import BuildPool
from league_stats_runner.pipeline import orchestrator
from league_stats_runner.pipeline.orchestrator import _merge_manifest_with_disk, prepare_builds
from league_stats_runner.pipeline.services import PlayerContext
from league_stats_runner.presentation.report import build_manifest_entry


def _seed_build(
    player_slug: str, build_slug: str, *, champion: str, role: str, games: int
) -> None:
    with open_report_store() as store:
        store.save_build(
            player_slug,
            build_slug,
            {
                "champion": champion,
                "role": role,
                "games": games,
                "winrate": 0.5,
                "build_label": f"{champion} {role.lower()}",
            },
        )


def test_merge_manifest_with_disk_keeps_existing_reports() -> None:
    player_slug = "test_euw"
    _seed_build(player_slug, "viktor_middle", champion="Viktor", role="MIDDLE", games=40)
    _seed_build(player_slug, "fiora_top", champion="Fiora", role="TOP", games=30)

    live = [
        build_manifest_entry(champion="Fiora", role="TOP", games=31, winrate=0.55),
    ]
    merged = _merge_manifest_with_disk(live, player_slug)
    slugs = {
        f"{entry['champion'].lower()}_{entry['role'].lower()}" for entry in merged
    }
    assert slugs == {"fiora_top", "viktor_middle"}
    fiora = next(entry for entry in merged if entry["champion"] == "Fiora")
    assert fiora["games"] == 31


def test_prepare_builds_scoped_keeps_full_manifest(monkeypatch: Any) -> None:
    player_slug = "test_euw"
    _seed_build(player_slug, "viktor_middle", champion="Viktor", role="MIDDLE", games=40)
    _seed_build(player_slug, "fiora_top", champion="Fiora", role="TOP", games=30)

    pools = [
        BuildPool(champion="Viktor", role="MIDDLE", games=40),
        BuildPool(champion="Fiora", role="TOP", games=30),
    ]
    monkeypatch.setattr(orchestrator, "discover_build_pools", lambda *a, **k: pools)
    monkeypatch.setattr(orchestrator, "load_all_records", lambda *a, **k: [])
    monkeypatch.setattr(
        orchestrator,
        "group_records",
        lambda records, champion, role: [
            SimpleNamespace(win=True)
        ]
        * (40 if champion == "Viktor" else 30),
    )

    config = SimpleNamespace(
        min_games=20,
        filter_champion="Fiora",
        filter_role="TOP",
        reports_group_slug=player_slug,
    )
    assets = SimpleNamespace(ensure_downloaded=lambda: None)
    services = SimpleNamespace(store=object(), config=config, assets=assets)
    contexts = [
        PlayerContext(riot_id="Test", tagline="EUW", puuid="puuid", profile_icon_id=1)
    ]

    batch = prepare_builds(services, contexts)

    assert [pool.build_label for pool in batch.pools] == ["Fiora top"]
    assert {entry["champion"] for entry in batch.manifest_builds} == {"Viktor", "Fiora"}


def test_prepare_builds_unscoped_keeps_on_disk_siblings(monkeypatch: Any) -> None:
    """Full-account refresh must not drop existing reports from the Champions nav."""
    player_slug = "test_euw"
    _seed_build(player_slug, "viktor_middle", champion="Viktor", role="MIDDLE", games=40)
    _seed_build(player_slug, "fiora_top", champion="Fiora", role="TOP", games=30)
    # Below the live min_games threshold, but still saved from an earlier run.
    _seed_build(player_slug, "bard_utility", champion="Bard", role="UTILITY", games=12)

    pools = [
        BuildPool(champion="Viktor", role="MIDDLE", games=40),
        BuildPool(champion="Fiora", role="TOP", games=30),
    ]
    monkeypatch.setattr(orchestrator, "discover_build_pools", lambda *a, **k: pools)
    monkeypatch.setattr(orchestrator, "load_all_records", lambda *a, **k: [])
    monkeypatch.setattr(
        orchestrator,
        "group_records",
        lambda records, champion, role: [
            SimpleNamespace(win=True)
        ]
        * (40 if champion == "Viktor" else 30),
    )

    config = SimpleNamespace(
        min_games=20,
        filter_champion=None,
        filter_role=None,
        reports_group_slug=player_slug,
    )
    assets = SimpleNamespace(ensure_downloaded=lambda: None)
    services = SimpleNamespace(store=object(), config=config, assets=assets)
    contexts = [
        PlayerContext(riot_id="Test", tagline="EUW", puuid="puuid", profile_icon_id=1)
    ]

    batch = prepare_builds(services, contexts)

    assert len(batch.pools) == 2
    assert {entry["champion"] for entry in batch.manifest_builds} == {
        "Viktor",
        "Fiora",
        "Bard",
    }
