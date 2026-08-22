"""Tests for multi-report storage and the report index."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from league_stats_peers.analysis.peer import build_comparisons
from league_stats_common.core.champions import champion_slug, player_slug
from league_stats_common.core.config import AppConfig
from league_stats_common.infra.report_store import open_report_store
from league_stats_runner.pipeline.orchestrator import run_analysis
from league_stats_common.core.models import MatchRecord, PeerComparisonResult, RankedEntry
from league_stats_runner.presentation.report import discover_reports, refresh_report_indexes
from tests.fixtures import FAKE_ITEMS, MY_PUUID, make_match, make_timeline
from league_stats_runner.ingest.parser import ItemCatalog, MatchParser


def _make_records(n: int = 12) -> list[MatchRecord]:
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


def _peer(records: list[MatchRecord]) -> PeerComparisonResult:
    peer_metrics = {
        "win": 0.5,
        "kda": 2.4,
        "dpm": 640.0,
        "cspm": 7.0,
        "deaths": 5.0,
        "vspm": 1.0,
        "control_wards": 2.0,
        "kill_participation": 0.6,
        "damage_share": 0.2,
    }
    return PeerComparisonResult(
        rank_label="GOLD II",
        tier="GOLD",
        source="test benchmark",
        peer_games=0,
        peer_players=0,
        comparisons=build_comparisons(
            pd.DataFrame([r.to_row() for r in records]).mean(numeric_only=True).to_dict(),
            peer_metrics,
        ),
    )


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


def test_different_champions_create_separate_reports(tmp_path: Path) -> None:
    """Each player/champion/lane combo gets its own directory."""
    ranked = RankedEntry(tier="GOLD", rank="II", league_points=45, wins=80, losses=75)
    records = _make_records()
    peer = _peer(records)

    viktor_config = _config(tmp_path, champion="Viktor", role="MIDDLE")
    ahri_config = _config(tmp_path, champion="Ahri", role="MIDDLE")

    viktor_ref = run_analysis(viktor_config, records, peer_comparison=peer, ranked=ranked)
    ahri_ref = run_analysis(ahri_config, records, peer_comparison=peer, ranked=ranked)

    assert viktor_ref != ahri_ref
    assert viktor_ref == f"{viktor_config.reports_group_slug}/viktor_middle"
    assert ahri_ref == f"{ahri_config.reports_group_slug}/ahri_middle"

    with open_report_store() as store:
        assert store.get_report(viktor_config.reports_group_slug, "viktor_middle") is not None
        assert store.get_report(ahri_config.reports_group_slug, "ahri_middle") is not None

    entries = discover_reports()
    assert len(entries) == 2
    labels = {entry["build_label"] for entry in entries}
    assert labels == {"Viktor mid", "Ahri mid"}


def test_same_combo_overwrites_report(tmp_path: Path) -> None:
    """Re-running the same player/champion/lane replaces that report."""
    ranked = RankedEntry(tier="GOLD", rank="II", league_points=45, wins=80, losses=75)
    config = _config(tmp_path)
    peer = _peer(_make_records(10))

    first_ref = run_analysis(config, _make_records(10), peer_comparison=peer, ranked=ranked)

    second_ref = run_analysis(config, _make_records(20), peer_comparison=peer, ranked=ranked)
    build_slug = champion_slug(config.champion, config.role)
    with open_report_store() as store:
        build_meta = store.get_build(config.reports_group_slug, build_slug)

    assert first_ref == second_ref
    assert build_meta["games"] == 20
    assert len(discover_reports()) == 1


def test_player_slug_sanitizes_special_characters() -> None:
    """Riot IDs with spaces or symbols become safe directory names."""
    assert player_slug("Hide on Bush", "KR1") == "hide_on_bush_kr1"


def test_discover_reports_lists_all_builds(tmp_path: Path) -> None:
    """Saved builds remain discoverable after analysis."""
    ranked = RankedEntry(tier="GOLD", rank="II", league_points=45, wins=80, losses=75)
    records = _make_records()
    peer = _peer(records)

    run_analysis(_config(tmp_path, champion="Viktor"), records, peer_comparison=peer, ranked=ranked)
    run_analysis(_config(tmp_path, champion="Ahri"), records, peer_comparison=peer, ranked=ranked)

    config = _config(tmp_path)
    refresh_report_indexes(
        config.output_dir,
        config.template_dir,
        player_dir=config.player_reports_dir,
        player_label="Test#EUW",
    )
    assert not (config.output_dir / "index.html").exists()

    reports = discover_reports()
    champions = {report["champion"] for report in reports}
    assert "Viktor" in champions
    assert "Ahri" in champions
    assert all(report["build_slug"] for report in reports)


def test_improvement_score_has_a_resolved_tone_colour(tmp_path: Path) -> None:
    """score_color/score_verdict_label must reach report.json, never be empty."""
    ranked = RankedEntry(tier="GOLD", rank="II", league_points=45, wins=80, losses=75)
    records = _make_records()
    peer = _peer(records)

    report_ref = run_analysis(
        _config(tmp_path, champion="Viktor", role="MIDDLE"),
        records,
        peer_comparison=peer,
        ranked=ranked,
    )
    with open_report_store() as store:
        payload = store.get_report(*report_ref.split("/"))

    assert payload["score_color"]
    assert "var(--" in payload["score_color"]
    assert payload["score_verdict_label"] in {"Strength", "Solid", "Steady", "Watch", "Focus"}


def test_patch_report_peer_comparison_updates_peer_fields_and_generated_at(
    tmp_path: Path,
) -> None:
    """Design "Progressive peer-comparison updates during live sampling" §3.2:
    `patch_report_peer_comparison` rewrites an already-rendered `report.json`'s
    peer fields in place -- without re-running the analysis pipeline -- and
    bumps `generated_at` so the frontend's existing `generated_at`-triggers-
    refetch logic (§3.4) picks up the change. `career`/`overview`/other
    Stage-A-rendered fields must be left exactly as they were.

    Fails pre-fix: `patch_report_peer_comparison` did not exist at all.
    """
    import time

    from league_stats_runner.ingest.parser import BuildPool
    from league_stats_runner.pipeline.orchestrator import patch_report_peer_comparison

    config = _config(tmp_path, champion="Viktor", role="MIDDLE")
    records = _make_records()

    # Stage A: no peer comparison yet -- the awaiting_peers loading shape.
    report_path = run_analysis(config, records, peer_comparison=None)
    before = json.loads(report_path.read_text(encoding="utf-8"))
    assert before["has_peer_comparison"] is False
    assert before["career"]["awaiting_peers"] is True

    time.sleep(1.1)  # utc_now_iso() has second-level resolution
    pool = BuildPool(champion="Viktor", role="MIDDLE", games=len(records))
    interim_peer = _peer(records)

    patched = patch_report_peer_comparison(config, pool, interim_peer)

    assert patched is True
    after = json.loads(report_path.read_text(encoding="utf-8"))
    assert after["has_peer_comparison"] is True
    assert after["peer_comparison"]["rank_label"] == "GOLD II"
    assert after["peer_rows"]
    assert after["generated_at"] != before["generated_at"]
    # Career/overview/etc. are Stage-A-rendered and untouched by this cheap patch.
    assert after["career"] == before["career"]
    assert after["overview"] == before["overview"]

    meta_path = config.report_dir / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert meta["has_peer_comparison"] is True
    assert meta["generated_at"] == after["generated_at"]


def test_patch_report_peer_comparison_is_a_noop_without_an_existing_report(
    tmp_path: Path,
) -> None:
    """No `report.json` yet (e.g. Stage A never got far enough) -- the patch
    must report failure rather than crashing, so the caller can fall back to
    a full render."""
    from league_stats_runner.ingest.parser import BuildPool
    from league_stats_runner.pipeline.orchestrator import patch_report_peer_comparison

    config = _config(tmp_path, champion="Zed", role="MIDDLE")
    records = _make_records()
    pool = BuildPool(champion="Zed", role="MIDDLE", games=len(records))

    assert patch_report_peer_comparison(config, pool, _peer(records)) is False
