"""Tests for shared report stylesheet publishing and stale-copy refresh."""

from __future__ import annotations

from pathlib import Path

from league_stats.core.config import DEFAULT_TEMPLATE_DIR
from league_stats.presentation.report_static import (
    ensure_report_static_assets,
    report_static_version,
    report_stylesheet_hrefs,
    sync_report_static_dirs,
)


def test_ensure_publishes_shared_assets(tmp_path: Path) -> None:
    output = tmp_path / "output"
    result = ensure_report_static_assets(output, DEFAULT_TEMPLATE_DIR, sync_existing=False)
    shared = output / "assets" / "report-ui" / "report.css"
    assert shared.is_file()
    assert result["version"] == report_static_version(DEFAULT_TEMPLATE_DIR)
    assert len(result["version"]) == 12


def test_sync_refreshes_stale_per_build_css(tmp_path: Path) -> None:
    output = tmp_path / "output"
    build_static = output / "reports" / "player_euw" / "viktor_middle" / "static"
    build_static.mkdir(parents=True)
    (output / "reports" / "player_euw" / "viktor_middle" / "report.html").write_text(
        "<html></html>", encoding="utf-8"
    )
    stale = build_static / "report.css"
    stale.write_text("/* stale */", encoding="utf-8")

    updated = sync_report_static_dirs(output, DEFAULT_TEMPLATE_DIR)
    assert updated == 1
    fresh = (DEFAULT_TEMPLATE_DIR / "static" / "report.css").read_text(encoding="utf-8")
    assert stale.read_text(encoding="utf-8") == fresh
    assert "/* stale */" not in stale.read_text(encoding="utf-8")


def test_stylesheet_hrefs_use_shared_path_and_cache_buster(tmp_path: Path) -> None:
    output = tmp_path / "output"
    from_dir = output / "reports" / "player_euw" / "viktor_middle"
    from_dir.mkdir(parents=True)
    hrefs = report_stylesheet_hrefs(
        from_dir=from_dir,
        output_dir=output,
        template_dir=DEFAULT_TEMPLATE_DIR,
    )
    version = report_static_version(DEFAULT_TEMPLATE_DIR)
    assert hrefs["report_css_href"].startswith("../../../assets/report-ui/report.css?v=")
    assert hrefs["report_css_href"].endswith(version)
    assert hrefs["chatbot_css_href"].endswith(version)
