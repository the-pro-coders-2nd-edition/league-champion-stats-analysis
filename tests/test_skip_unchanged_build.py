"""Tests for skipping re-analysis when a build has no newly fetched games."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from league_stats_common.core.champions import champion_slug
from league_stats_common.core.config import AppConfig
from league_stats_common.infra.report_store import open_report_store
from league_stats_runner.ingest.parser import BuildPool
from league_stats_runner.pipeline.orchestrator import (
    report_needs_peer_comparison,
    should_skip_unchanged_build,
)


def _config(tmp_path: Path) -> AppConfig:
    return AppConfig(
        riot_id="Test",
        tagline="EUW",
        region="europe",
        api_key="test-key",
        output_dir=tmp_path / "output",
    )


def _record(match_id: str) -> SimpleNamespace:
    return SimpleNamespace(match_id=match_id)


def _seed_build(config: AppConfig, pool: BuildPool, meta: dict | None = None) -> None:
    build_slug = champion_slug(pool.champion, pool.role)
    with open_report_store() as store:
        store.save_build(config.reports_group_slug, build_slug, meta or {})


def test_skip_when_report_exists_and_no_new_games(tmp_path: Path) -> None:
    config = _config(tmp_path)
    pool = BuildPool(champion="Viktor", role="MIDDLE", games=25)
    _seed_build(config, pool)

    assert should_skip_unchanged_build(
        config, pool, [_record("EUW1_old")], frozenset()
    )
    assert should_skip_unchanged_build(
        config, pool, [_record("EUW1_old")], frozenset({"EUW1_other"})
    )


def test_do_not_skip_when_build_has_new_game(tmp_path: Path) -> None:
    config = _config(tmp_path)
    pool = BuildPool(champion="Viktor", role="MIDDLE", games=25)
    _seed_build(config, pool)

    assert not should_skip_unchanged_build(
        config,
        pool,
        [_record("EUW1_old"), _record("EUW1_new")],
        frozenset({"EUW1_new"}),
    )


def test_do_not_skip_without_existing_report(tmp_path: Path) -> None:
    config = _config(tmp_path)
    pool = BuildPool(champion="Viktor", role="MIDDLE", games=25)
    # No build seeded in the store: this is the "never analysed" case.
    with open_report_store() as store:
        assert not store.has_build(config.reports_group_slug, champion_slug(pool.champion, pool.role))
    assert not should_skip_unchanged_build(
        config, pool, [_record("EUW1_1")], frozenset()
    )


def test_report_needs_peer_when_meta_flag_false(tmp_path: Path) -> None:
    config = _config(tmp_path)
    pool = BuildPool(champion="Viktor", role="MIDDLE", games=25)
    _seed_build(config, pool, {"has_peer_comparison": False})
    assert report_needs_peer_comparison(config, pool)


def test_report_does_not_need_peer_when_meta_flag_true(tmp_path: Path) -> None:
    config = _config(tmp_path)
    pool = BuildPool(champion="Viktor", role="MIDDLE", games=25)
    _seed_build(config, pool, {"has_peer_comparison": True})
    assert not report_needs_peer_comparison(config, pool)


def test_do_not_skip_when_new_match_ids_is_none(tmp_path: Path) -> None:
    """CLI report regeneration (no fetch) always re-analyses."""
    config = _config(tmp_path)
    pool = BuildPool(champion="Viktor", role="MIDDLE", games=25)
    _seed_build(config, pool)

    assert not should_skip_unchanged_build(
        config, pool, [_record("EUW1_old")], None
    )
