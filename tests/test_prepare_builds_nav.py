"""Scoped analysis keeps the full champion sidebar list."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from league_stats_runner.ingest.parser import BuildPool
from league_stats_runner.pipeline import orchestrator
from league_stats_runner.pipeline.orchestrator import _merge_manifest_with_disk, prepare_builds
from league_stats_runner.pipeline.services import PlayerContext
from league_stats_runner.presentation.report import build_manifest_entry


def _write_meta(player_dir: Path, slug: str, *, champion: str, role: str, games: int) -> None:
    report_dir = player_dir / slug
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "report.json").write_text("{}", encoding="utf-8")
    (report_dir / "meta.json").write_text(
        json.dumps(
            {
                "champion": champion,
                "role": role,
                "games": games,
                "winrate": 0.5,
                "build_label": f"{champion} {role.lower()}",
            }
        ),
        encoding="utf-8",
    )


def test_merge_manifest_with_disk_keeps_existing_reports(tmp_path: Path) -> None:
    player_dir = tmp_path / "reports" / "test_euw"
    _write_meta(player_dir, "viktor_middle", champion="Viktor", role="MIDDLE", games=40)
    _write_meta(player_dir, "fiora_top", champion="Fiora", role="TOP", games=30)

    live = [
        build_manifest_entry(champion="Fiora", role="TOP", games=31, winrate=0.55),
    ]
    merged = _merge_manifest_with_disk(live, player_dir)
    slugs = {
        f"{entry['champion'].lower()}_{entry['role'].lower()}" for entry in merged
    }
    assert slugs == {"fiora_top", "viktor_middle"}
    fiora = next(entry for entry in merged if entry["champion"] == "Fiora")
    assert fiora["games"] == 31


def test_prepare_builds_scoped_keeps_full_manifest(
    tmp_path: Path, monkeypatch: Any
) -> None:
    player_dir = tmp_path / "reports" / "test_euw"
    _write_meta(player_dir, "viktor_middle", champion="Viktor", role="MIDDLE", games=40)
    _write_meta(player_dir, "fiora_top", champion="Fiora", role="TOP", games=30)

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
        player_reports_dir=player_dir,
    )
    assets = SimpleNamespace(ensure_downloaded=lambda: None)
    services = SimpleNamespace(store=object(), config=config, assets=assets)
    contexts = [
        PlayerContext(riot_id="Test", tagline="EUW", puuid="puuid", profile_icon_id=1)
    ]

    batch = prepare_builds(services, contexts)

    assert [pool.build_label for pool in batch.pools] == ["Fiora top"]
    assert {entry["champion"] for entry in batch.manifest_builds} == {"Viktor", "Fiora"}


def test_prepare_builds_unscoped_keeps_on_disk_siblings(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """Full-account refresh must not drop existing reports from the Champions nav."""
    player_dir = tmp_path / "reports" / "test_euw"
    _write_meta(player_dir, "viktor_middle", champion="Viktor", role="MIDDLE", games=40)
    _write_meta(player_dir, "fiora_top", champion="Fiora", role="TOP", games=30)
    # Below the live min_games threshold, but still on disk from an earlier run.
    _write_meta(player_dir, "bard_utility", champion="Bard", role="UTILITY", games=12)

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
        player_reports_dir=player_dir,
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
