"""End-to-end smoke test: parsed records -> exports + report.json."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from league_stats_peers.analysis.peer import build_comparisons
from league_stats_common.core.config import AppConfig
from league_stats_common.infra.report_store import open_report_store
from league_stats_runner.pipeline.orchestrator import run_analysis
from league_stats_common.core.models import MatchRecord, PeerComparisonResult, RankedEntry
from league_stats_runner.ingest.parser import ItemCatalog, MatchParser
from tests.fixtures import FAKE_ITEMS, MY_PUUID, make_match, make_timeline

OPPONENTS = ["Syndra", "Orianna", "Akali", "Ahri", "Zed"]

# The 6 report artifacts a build used to get under output_dir/reports/ before
# the Mongo migration (see league_stats_common.infra.report_store). None of
# them is written to disk anymore.
MIGRATED_REPORT_FILENAMES = (
    "report.json", "meta.json", "manifest.json",
    "summary.json", "progression.json", "progression.md",
)


def _report_ref_parts(report_ref: str) -> tuple[str, str]:
    player_slug, build_slug = report_ref.split("/", 1)
    return player_slug, build_slug


def _read_report(report_ref: str) -> dict:
    player_slug, build_slug = _report_ref_parts(report_ref)
    with open_report_store() as store:
        payload = store.get_report(player_slug, build_slug)
    assert payload is not None
    return payload


def _read_build_meta(report_ref: str) -> dict:
    player_slug, build_slug = _report_ref_parts(report_ref)
    with open_report_store() as store:
        meta = store.get_build(player_slug, build_slug)
    assert meta is not None
    return meta


def _make_records(n: int = 15) -> list[MatchRecord]:
    """Parse the fixture once and derive varied copies.

    Args:
        n: Number of records to produce.

    Returns:
        Records with varied ids, results and opponents.
    """
    base = MatchParser(ItemCatalog(FAKE_ITEMS)).parse(make_match(), make_timeline(), MY_PUUID)
    records: list[MatchRecord] = []
    for index in range(n):
        records.append(
            base.model_copy(
                deep=True,
                update={
                    "match_id": f"EUW1_{index}",
                    "win": index % 3 != 0,
                    "lane_opponent": OPPONENTS[index % len(OPPONENTS)],
                    "game_creation_ms": 1_700_000_000_000 + index * 3_600_000,
                },
            )
        )
    return records


def test_full_pipeline_generates_all_artifacts(tmp_path: Path) -> None:
    """The full analysis produces the report, every CSV and the summary."""
    config = AppConfig(
        riot_id="Test",
        tagline="EUW",
        region="europe",
        api_key="RGAPI-test",
        output_dir=tmp_path / "output",
        assets_dir=tmp_path / "assets",
        graphs_dir=tmp_path / "graphs",
        cache_dir=tmp_path / "cache",
        template_dir=Path(__file__).resolve().parent.parent / "src/league_stats/presentation/templates",
    )
    config.ensure_directories()
    ranked = RankedEntry(tier="GOLD", rank="II", league_points=45, wins=80, losses=75)
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
    peer = PeerComparisonResult(
        rank_label=ranked.label,
        tier=ranked.tier,
        source="test benchmark",
        peer_games=0,
        peer_players=0,
        comparisons=build_comparisons(
            pd.DataFrame([r.to_row() for r in _make_records()]).mean(numeric_only=True).to_dict(),
            peer_metrics,
        ),
    )
    report_ref = run_analysis(
        config, _make_records(), peer_comparison=peer, ranked=ranked
    )

    from league_stats_common.core.champions import champion_slug

    assert report_ref == f"{config.reports_group_slug}/{champion_slug(config.champion, config.role)}"
    payload = _read_report(report_ref)
    assert payload["score"] is not None
    assert payload["positive_recommendations"] or payload["negative_recommendations"]
    assert "form_available" in payload
    assert "progression_views" in payload
    assert payload["has_peer_comparison"] is True
    assert payload["peer_rows"]
    assert payload["player_page_href"] is None

    # File exports still on disk: CSVs and the coaching recommendations, plus
    # the peer CSV (out of scope for the Mongo migration -- see
    # orchestrator.report_needs_peer_comparison). The 6 migrated artifacts
    # (report.json/meta.json/manifest.json/summary.json/progression.json/.md)
    # are asserted absent below instead.
    expected = [
        "matches.csv", "deaths.csv", "timeline.csv", "matchups.csv",
        "vision.csv", "items.csv", "runes.csv", "objectives.csv", "teamfights.csv",
        "correlations.csv", "recommendations.md", "rank_comparison.csv",
    ]
    for name in expected:
        assert (config.report_dir / name).exists(), f"missing export: {name}"
    assert (config.run_graphs_dir / "death_heatmap.png").exists()

    assert not (config.output_dir / "index.html").exists()
    with open_report_store() as store:
        assert store.has_build(*_report_ref_parts(report_ref))

    for filename in MIGRATED_REPORT_FILENAMES:
        assert not list(config.output_dir.rglob(filename)), (
            f"{filename} must not be written to disk anymore"
        )


def test_report_embeds_chatbot_panel_and_stats(tmp_path: Path) -> None:
    """The rendered report embeds the chat panel, stats JSON and a security note."""
    config = AppConfig(
        riot_id="Test",
        tagline="EUW",
        region="europe",
        api_key="RGAPI-test",
        output_dir=tmp_path / "output",
        assets_dir=tmp_path / "assets",
        graphs_dir=tmp_path / "graphs",
        cache_dir=tmp_path / "cache",
        template_dir=Path(__file__).resolve().parent.parent / "src/league_stats/presentation/templates",
    )
    config.ensure_directories()

    report_ref = run_analysis(config, _make_records())
    payload = _read_report(report_ref)

    # CLI-rendered report (no chat_endpoint): the SPA's chat panel calls Gemini directly.
    assert payload["chat_endpoint"] is None
    assert payload["gemini_api_key"] is None

    embedded_stats = payload["chatbot_stats"]
    assert embedded_stats["build_label"] == config.build_label
    assert embedded_stats["games"] == 15


def test_web_stage_a_report_shows_peer_pending_placeholder(tmp_path: Path) -> None:
    """Web base reports keep a Peers section while peer comparison is still running."""
    config = AppConfig(
        riot_id="Test",
        tagline="EUW",
        region="europe",
        api_key="RGAPI-test",
        champion="Viktor",
        role="MIDDLE",
        output_dir=tmp_path / "output",
        assets_dir=tmp_path / "assets",
        graphs_dir=tmp_path / "graphs",
        cache_dir=tmp_path / "cache",
        template_dir=Path(__file__).resolve().parent.parent
        / "src/league_stats/presentation/templates",
        status_endpoint="/api/players/test_euw",
        chat_endpoint="/api/chat",
    )
    config.ensure_directories()

    report_ref = run_analysis(config, _make_records(), peer_comparison=None)
    payload = _read_report(report_ref)

    assert payload["has_peer_comparison"] is False
    assert "peer_comparison" not in payload
    assert payload["refresh_champion"] == "Viktor"
    assert payload["refresh_role"] == "MIDDLE"
    assert payload["status_endpoint"] == "/api/players/test_euw"
    meta = _read_build_meta(report_ref)
    assert meta["has_peer_comparison"] is False
    assert meta["last_game_at"]
    assert meta["score"] is not None
    assert not (config.report_dir / "rank_comparison.csv").exists()


def test_peer_report_marks_meta_ready_and_keeps_export(tmp_path: Path) -> None:
    """Peer stage writes has_peer_comparison into meta and the CSV export."""
    from league_stats_peers.analysis.peer import build_comparisons
    from league_stats_common.core.models import PeerComparisonResult, RankedEntry

    config = AppConfig(
        riot_id="Test",
        tagline="EUW",
        region="europe",
        api_key="RGAPI-test",
        champion="Viktor",
        role="MIDDLE",
        output_dir=tmp_path / "output",
        assets_dir=tmp_path / "assets",
        graphs_dir=tmp_path / "graphs",
        cache_dir=tmp_path / "cache",
        template_dir=Path(__file__).resolve().parent.parent
        / "src/league_stats/presentation/templates",
    )
    config.ensure_directories()
    records = _make_records()
    ranked = RankedEntry(tier="GOLD", rank="II", league_points=45, wins=80, losses=75)
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
    peer = PeerComparisonResult(
        rank_label=ranked.label,
        tier=ranked.tier,
        source="test benchmark",
        peer_games=10,
        peer_players=5,
        comparisons=build_comparisons(
            pd.DataFrame([r.to_row() for r in records]).mean(numeric_only=True).to_dict(),
            peer_metrics,
        ),
    )
    report_ref = run_analysis(
        config, records, peer_comparison=peer, ranked=ranked
    )
    meta = _read_build_meta(report_ref)
    assert meta["has_peer_comparison"] is True
    assert (config.report_dir / "rank_comparison.csv").is_file()

    # Re-running without peers must clear the stale peer export.
    run_analysis(config, records, peer_comparison=None, ranked=ranked)
    meta = _read_build_meta(report_ref)
    assert meta["has_peer_comparison"] is False
    assert not (config.report_dir / "rank_comparison.csv").exists()


def test_pipeline_saves_a_json_safe_report_body(tmp_path: Path) -> None:
    """run_analysis saves a JSON-safe report body to the report store."""
    config = AppConfig(
        riot_id="Test",
        tagline="EUW",
        region="europe",
        api_key="RGAPI-test",
        champion="Viktor",
        role="MIDDLE",
        output_dir=tmp_path / "output",
        assets_dir=tmp_path / "assets",
        graphs_dir=tmp_path / "graphs",
        cache_dir=tmp_path / "cache",
        template_dir=Path(__file__).resolve().parent.parent / "src/league_stats/presentation/templates",
    )
    config.ensure_directories()
    ranked = RankedEntry(tier="GOLD", rank="II", league_points=45, wins=80, losses=75)
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
    peer = PeerComparisonResult(
        rank_label=ranked.label,
        tier=ranked.tier,
        source="test benchmark",
        peer_games=0,
        peer_players=0,
        comparisons=build_comparisons(
            pd.DataFrame([r.to_row() for r in _make_records()]).mean(numeric_only=True).to_dict(),
            peer_metrics,
        ),
    )
    report_ref = run_analysis(
        config, _make_records(), peer_comparison=peer, ranked=ranked
    )

    payload = _read_report(report_ref)
    assert payload["champion"] == "Viktor"
    assert "report_views" in payload


def test_run_analysis_writes_none_of_the_six_migrated_report_files(tmp_path: Path) -> None:
    """Nothing report-shaped is written to disk anymore.

    Walks the whole output tree rather than just the expected build directory,
    so this fails if a future regression writes report.json/meta.json/etc.
    anywhere under output_dir/reports (e.g. a stray debug dump), not only at
    the one path this test happens to expect.
    """
    config = AppConfig(
        riot_id="Test",
        tagline="EUW",
        region="europe",
        api_key="RGAPI-test",
        champion="Viktor",
        role="MIDDLE",
        output_dir=tmp_path / "output",
        assets_dir=tmp_path / "assets",
        graphs_dir=tmp_path / "graphs",
        cache_dir=tmp_path / "cache",
        template_dir=Path(__file__).resolve().parent.parent / "src/league_stats/presentation/templates",
    )
    config.ensure_directories()
    ranked = RankedEntry(tier="GOLD", rank="II", league_points=45, wins=80, losses=75)
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
    peer = PeerComparisonResult(
        rank_label=ranked.label,
        tier=ranked.tier,
        source="test benchmark",
        peer_games=0,
        peer_players=0,
        comparisons=build_comparisons(
            pd.DataFrame([r.to_row() for r in _make_records()]).mean(numeric_only=True).to_dict(),
            peer_metrics,
        ),
    )
    report_ref = run_analysis(
        config, _make_records(), peer_comparison=peer, ranked=ranked
    )

    with open_report_store() as store:
        assert store.has_build(*_report_ref_parts(report_ref))

    reports_root = config.output_dir / "reports"
    assert reports_root.is_dir()
    for filename in MIGRATED_REPORT_FILENAMES:
        matches = list(reports_root.rglob(filename))
        assert matches == [], f"found unexpected {filename} on disk: {matches}"
