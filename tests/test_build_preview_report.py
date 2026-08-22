"""Tests for the Netlify preview build script (deploy/build_preview_report.py)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from league_stats_common.infra.report_store import open_report_store

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "deploy" / "build_preview_report.py"


def _load_build_preview_report():
    spec = importlib.util.spec_from_file_location("build_preview_report", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_build_preview_writes_index_and_player_reports(tmp_path: Path) -> None:
    module = _load_build_preview_report()

    player_slug = module.build_preview(tmp_path / "output")

    assert player_slug
    with open_report_store() as store:
        builds = store.list_builds(player_slug)
    assert len(builds) == len(module.PREVIEW_BUILDS)
    for build in builds:
        with open_report_store() as store:
            assert store.get_report(player_slug, build["build_slug"]) is not None


def test_build_preview_reports_cover_configured_champions(tmp_path: Path) -> None:
    module = _load_build_preview_report()

    player_slug = module.build_preview(tmp_path / "output")

    from league_stats_runner.presentation.report import discover_reports

    entries = [e for e in discover_reports() if e["player_slug"] == player_slug]
    champions = {entry["champion"] for entry in entries}
    assert champions == {build.champion for build in module.PREVIEW_BUILDS}


def test_preview_reports_can_switch_between_every_build(tmp_path: Path) -> None:
    module = _load_build_preview_report()
    output = tmp_path / "output"

    player_slug = module.build_preview(output)

    slugs = ["viktor_middle", "jinx_bottom", "thresh_utility"]
    for slug in slugs:
        with open_report_store() as store:
            payload = store.get_report(player_slug, slug)
        builds = payload["player_builds"]
        assert builds, f"{slug} has no champion switcher"
        hrefs = {build["href"] for build in builds}
        for target in slugs:
            assert f"../{target}/report.json" in hrefs, f"{slug} cannot reach {target}"
        selected = [build for build in builds if build["selected"]]
        assert len(selected) == 1
        assert selected[0]["href"] == f"../{slug}/report.json"


def test_preview_hub_defaults_to_the_most_played_build(tmp_path: Path) -> None:
    module = _load_build_preview_report()
    output = tmp_path / "output"

    player_slug = module.build_preview(output)

    with open_report_store() as store:
        builds = store.list_builds(player_slug)

    games = [build["games"] for build in builds]
    assert games == sorted(games, reverse=True), "hub should list most-played first"
    assert len(set(games)) == len(games), "distinct counts keep the default deterministic"
    default_href = builds[0]["href"]
    assert default_href == f"{builds[0]['build_slug']}/report.json"
