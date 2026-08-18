"""Tests for the Netlify preview build script (deploy/build_preview_report.py)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "deploy" / "build_preview_report.py"


def _load_build_preview_report():
    spec = importlib.util.spec_from_file_location("build_preview_report", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_build_preview_writes_index_and_player_reports(tmp_path: Path) -> None:
    module = _load_build_preview_report()

    hub_path = module.build_preview(tmp_path / "output")

    assert hub_path.exists()
    assert hub_path.read_text(encoding="utf-8")

    report_files = list((tmp_path / "output" / "reports").rglob("report.html"))
    assert len(report_files) == len(module.PREVIEW_BUILDS)


def test_build_preview_reports_cover_configured_champions(tmp_path: Path) -> None:
    module = _load_build_preview_report()

    module.build_preview(tmp_path / "output")

    from league_stats.presentation.report import discover_reports

    entries = discover_reports(tmp_path / "output")
    champions = {entry["champion"] for entry in entries}
    assert champions == {build.champion for build in module.PREVIEW_BUILDS}


def test_preview_reports_can_switch_between_every_build(tmp_path: Path) -> None:
    module = _load_build_preview_report()
    output = tmp_path / "output"

    module.build_preview(output)

    player_dir = output / "reports" / "preview_euw"
    slugs = ["viktor_middle", "jinx_bottom", "thresh_utility"]
    for slug in slugs:
        html = (player_dir / slug / "report.html").read_text(encoding="utf-8")
        assert "nav-builds" in html, f"{slug} has no champion switcher"
        for target in slugs:
            assert f'href="../{target}/report.html"' in html, f"{slug} cannot reach {target}"
        assert f'build-card is-default" href="../{slug}/report.html"' in html


def test_preview_hub_defaults_to_the_most_played_build(tmp_path: Path) -> None:
    import json

    module = _load_build_preview_report()
    output = tmp_path / "output"

    module.build_preview(output)

    manifest = json.loads(
        (output / "reports" / "preview_euw" / "manifest.json").read_text(encoding="utf-8")
    )
    games = [build["games"] for build in manifest["builds"]]
    assert games == sorted(games, reverse=True), "hub should list most-played first"
    assert len(set(games)) == len(games), "distinct counts keep the default deterministic"
    assert manifest["default_href"] == manifest["builds"][0]["href"]
